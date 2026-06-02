"""
backend/providers/edge_tts_provider.py
───────────────────────────────────────
EdgeTTSProvider — wraps edge-tts for local development.

This is the Phase 3A TTS engine extracted into the TTSProvider abstraction.
It is the default provider for local development and is NOT suitable for
Cloudflare Workers (Python runtime, unofficial API, no SLA).

Production replacement: AzureSpeechProvider (same voices, official REST API).
"""

from __future__ import annotations

import io
import logging
import struct
from typing import Optional

import edge_tts
import miniaudio

from .base import (
    SynthesisRequest,
    SynthesisResult,
    TTSNoAudioError,
    TTSProvider,
    TTSProviderError,
    TTSUnavailableError,
)

logger = logging.getLogger("indicvoice.providers.edge_tts")

# ── Voice map ──────────────────────────────────────────────────────────────
# One primary + one fallback voice per language.
# All voices confirmed available as of 2024-06.
VOICE_MAP: dict[str, str] = {
    "te": "te-IN-ShrutiNeural",
    "ta": "ta-IN-PallaviNeural",
    "hi": "hi-IN-SwaraNeural",
    "en": "en-US-JennyNeural",
}

# ── WAV constants ──────────────────────────────────────────────────────────
OUTPUT_SAMPLE_RATE  = 22050
OUTPUT_CHANNELS     = 1      # mono
OUTPUT_SAMPLE_WIDTH = 2      # 16-bit signed PCM


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int, channels: int) -> bytes:
    """Wrap raw 16-bit signed PCM into a valid RIFF/WAV container."""
    data_len = len(pcm_bytes)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_len))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))                                   # PCM
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * channels * OUTPUT_SAMPLE_WIDTH))
    buf.write(struct.pack("<H", channels * OUTPUT_SAMPLE_WIDTH))
    buf.write(struct.pack("<H", OUTPUT_SAMPLE_WIDTH * 8))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm_bytes)
    return buf.getvalue()


class EdgeTTSProvider(TTSProvider):
    """
    TTS provider backed by edge-tts (Microsoft Neural TTS, unofficial API).

    Suitable for:  local development, CI smoke tests
    Not suitable for: Cloudflare Workers, production (no SLA, unofficial API)

    Thread safety: edge_tts.Communicate is stateless; multiple concurrent
    calls are safe — each creates its own asyncio websocket session.
    """

    @property
    def name(self) -> str:
        return "edge-tts"

    @property
    def supported_languages(self) -> set[str]:
        return set(VOICE_MAP.keys())   # {"te", "ta", "hi", "en"}

    def resolve_voice(self, language: str) -> str:
        """Return the canonical voice ID for *language*."""
        if language not in VOICE_MAP:
            raise TTSProviderError(
                f"EdgeTTSProvider does not support language '{language}'. "
                f"Supported: {sorted(VOICE_MAP.keys())}"
            )
        return VOICE_MAP[language]

    async def synthesise(self, request: SynthesisRequest) -> SynthesisResult:
        voice = request.voice or self.resolve_voice(request.language)
        logger.info(
            "EdgeTTS synthesise  lang=%s  voice=%s  text_len=%d",
            request.language, voice, len(request.text),
        )

        # ── Stream audio chunks from edge-tts ─────────────────────────────
        try:
            communicate    = edge_tts.Communicate(request.text, voice)
            audio_chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
        except edge_tts.exceptions.NoAudioReceived as exc:
            raise TTSNoAudioError(
                f"edge-tts returned no audio for lang={request.language}. "
                "The text may be too short or contain unsupported characters."
            ) from exc
        except Exception as exc:
            # Treat connection errors as service unavailable
            msg = str(exc).lower()
            if any(kw in msg for kw in ("connection", "timeout", "refused", "network", "unreachable")):
                raise TTSUnavailableError(
                    "edge-tts cannot reach speech.platform.bing.com. "
                    "Check internet connectivity."
                ) from exc
            raise TTSProviderError(f"edge-tts error: {exc}") from exc

        if not audio_chunks:
            raise TTSNoAudioError("edge-tts returned empty audio stream.")

        raw_audio = b"".join(audio_chunks)
        logger.debug("EdgeTTS raw audio: %d bytes", len(raw_audio))

        # ── Decode compressed audio → PCM → WAV ───────────────────────────
        try:
            decoded = miniaudio.decode(
                raw_audio,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=OUTPUT_CHANNELS,
                sample_rate=OUTPUT_SAMPLE_RATE,
            )
            pcm_bytes = bytes(decoded.samples)
            wav_bytes = _pcm_to_wav(pcm_bytes, decoded.sample_rate, decoded.nchannels)
        except Exception as exc:
            raise TTSProviderError(f"Audio decode/conversion failed: {exc}") from exc

        duration_s = len(pcm_bytes) / (decoded.sample_rate * decoded.nchannels * OUTPUT_SAMPLE_WIDTH)
        logger.info(
            "EdgeTTS done  wav=%d bytes  duration=%.2fs  sr=%dHz",
            len(wav_bytes), duration_s, decoded.sample_rate,
        )
        return SynthesisResult(
            wav_bytes=wav_bytes,
            sample_rate=decoded.sample_rate,
            channels=decoded.nchannels,
            duration_s=duration_s,
        )
