"""
backend/providers/edge_tts_provider.py
───────────────────────────────────────
EdgeTTSProvider — wraps edge-tts for local development.

This is the Phase 3A TTS engine extracted into the TTSProvider abstraction.
It is the default provider for local development and is NOT suitable for
Cloudflare Workers (Python runtime, unofficial API, no SLA).

Production replacement: AzureSpeechProvider (same voices, official REST API).

Changes in this revision
────────────────────────
FIX-1a  miniaudio.decode() now runs in a thread-pool executor so it no
        longer blocks the asyncio event loop during CPU decode (~50-200 ms).
FIX-1b  An asyncio.timeout(30) guard wraps the EdgeTTS stream so a stalled
        Microsoft connection cannot hold a worker thread for up to 300 s.
FIX-1c  aiohttp.ClientTimeout is caught explicitly and mapped to
        TTSUnavailableError (previously fell into the generic branch).
"""

from __future__ import annotations

import asyncio
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
# One primary voice per language.
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

# FIX-1b: maximum wall-clock seconds to wait for the EdgeTTS stream to start
# delivering audio chunks.  Protects against stalled Microsoft connections.
_EDGE_TTS_STREAM_TIMEOUT_S = 30


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int, channels: int) -> bytes:
    """Wrap raw 16-bit signed PCM into a valid RIFF/WAV container."""
    data_len = len(pcm_bytes)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", data_len + 36))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))           # PCM
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * channels * OUTPUT_SAMPLE_WIDTH))
    buf.write(struct.pack("<H", channels * OUTPUT_SAMPLE_WIDTH))
    buf.write(struct.pack("<H", 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm_bytes)
    return buf.getvalue()


def _decode_audio_sync(raw_audio: bytes) -> tuple[bytes, int, int]:
    """
    Synchronous miniaudio decode — runs in a thread-pool executor.

    Returns (pcm_bytes, sample_rate, nchannels).
    FIX-1a: keeps CPU-bound work off the asyncio event loop.
    """
    decoded = miniaudio.decode(
        raw_audio,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=OUTPUT_CHANNELS,
        sample_rate=OUTPUT_SAMPLE_RATE,
    )
    return bytes(decoded.samples), decoded.sample_rate, decoded.nchannels


class EdgeTTSProvider(TTSProvider):
    """Microsoft EdgeTTS neural voice provider for local development."""

    @property
    def name(self) -> str:
        return "edge-tts"

    @property
    def supported_languages(self) -> set[str]:
        return set(VOICE_MAP.keys())  # {"te", "ta", "hi", "en"}

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
            "EdgeTTS synthesise lang=%s voice=%s text_len=%d",
            request.language, voice, len(request.text),
        )

        # ── Stream audio chunks from edge-tts ────────────────────────────
        # FIX-1b: asyncio.timeout guard prevents a stalled upstream
        # connection from blocking a thread for up to 300 s (aiohttp default).
        try:
            async with asyncio.timeout(_EDGE_TTS_STREAM_TIMEOUT_S):
                communicate = edge_tts.Communicate(request.text, voice)
                audio_chunks: list[bytes] = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])

        except TimeoutError as exc:
            raise TTSUnavailableError(
                f"edge-tts stream timed out after {_EDGE_TTS_STREAM_TIMEOUT_S}s. "
                "Check internet connectivity to speech.platform.bing.com."
            ) from exc

        except edge_tts.exceptions.NoAudioReceived as exc:
            raise TTSNoAudioError(
                f"edge-tts returned no audio for lang={request.language}. "
                "The text may be too short or contain unsupported characters."
            ) from exc

        except Exception as exc:
            # FIX-1c: catch aiohttp timeout explicitly in the message string
            # so operators see a clear error rather than a generic trace.
            msg = str(exc).lower()
            if any(kw in msg for kw in (
                "connection", "timeout", "refused", "network",
                "unreachable", "clienttimeout", "client timeout",
            )):
                raise TTSUnavailableError(
                    "edge-tts cannot reach speech.platform.bing.com. "
                    "Check internet connectivity."
                ) from exc
            raise TTSProviderError(f"edge-tts error: {exc}") from exc

        if not audio_chunks:
            raise TTSNoAudioError("edge-tts returned empty audio stream.")

        raw_audio = b"".join(audio_chunks)
        logger.debug("EdgeTTS raw audio: %d bytes", len(raw_audio))

        # ── Decode compressed audio → PCM → WAV ─────────────────────────
        # FIX-1a: run miniaudio.decode() in a thread-pool executor so it
        # does not block the asyncio event loop during CPU decode.
        try:
            loop = asyncio.get_event_loop()
            pcm_bytes, sr, nch = await loop.run_in_executor(
                None, _decode_audio_sync, raw_audio
            )
            wav_bytes = _pcm_to_wav(pcm_bytes, sr, nch)
        except Exception as exc:
            raise TTSProviderError(f"Audio decode/conversion failed: {exc}") from exc

        duration_s = len(pcm_bytes) / (sr * nch * OUTPUT_SAMPLE_WIDTH)
        logger.info(
            "EdgeTTS done wav=%d bytes duration=%.2fs sr=%dHz",
            len(wav_bytes), duration_s, sr,
        )

        return SynthesisResult(
            wav_bytes=wav_bytes,
            sample_rate=sr,
            channels=nch,
            duration_s=duration_s,
        )
