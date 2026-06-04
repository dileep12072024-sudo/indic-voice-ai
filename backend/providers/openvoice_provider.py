"""
backend/providers/openvoice_provider.py
────────────────────────────────────────
OpenVoiceProvider — Phase 4B Voice Cloning MVP.

Architecture
────────────
OpenVoice v2 (MIT licence, myshell-ai/OpenVoice) performs cross-lingual
tone-colour transfer:

  Reference audio (speaker sample)
          │
          ▼
  ┌───────────────────┐        ┌──────────────────────────────┐
  │  Speaker Encoder  │──────▶ │  ToneColorConverter (VITS)   │
  └───────────────────┘        │  + EdgeTTS base synthesis     │
                                └──────────────────────────────┘
                                          │
                                          ▼
                                  output WAV (cloned voice)

Local-dev strategy
──────────────────
OpenVoice v2 requires PyTorch + GPU for real-time inference (20–60 s on CPU).
In local/CI environments (no GPU, no OpenVoice installed) this provider falls
back to EdgeTTS synthesis automatically — the job still completes successfully.
The response includes a "cloning_applied" flag (in both SynthesisResult and the
Job record) so the frontend can show the correct status badge.

Production strategy
───────────────────
Set OPENVOICE_ENABLED=true and ensure PyTorch + the OpenVoice checkpoint are
available (see docs/VOICE_CLONING.md for setup).  The provider will then use
the real ToneColorConverter.

Environment variables
─────────────────────
OPENVOICE_ENABLED        "true" | "false" (default "false")
OPENVOICE_CHECKPOINT_DIR Path to downloaded OpenVoice v2 checkpoints
                         (default "~/.cache/openvoice/v2")
OPENVOICE_DEVICE         "cpu" | "cuda" | "cuda:0" … (default "cpu")

Thread safety
─────────────
Speaker encoder + ToneColorConverter are loaded once at first use (lazy init,
protected by asyncio.Lock).  Multiple concurrent jobs are serialised through
the lock; parallelism is left for the GPU-backed Modal/RunPod production path.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import (
    SynthesisRequest,
    SynthesisResult,
    TTSNoAudioError,
    TTSProvider,
    TTSProviderError,
    TTSUnavailableError,
)
from .edge_tts_provider import EdgeTTSProvider, VOICE_MAP

logger = logging.getLogger("indicvoice.providers.openvoice")

# ── Environment flags ──────────────────────────────────────────────────────────
_ENABLED_ENV      = os.getenv("OPENVOICE_ENABLED", "false").lower() == "true"
_CHECKPOINT_DIR   = Path(
    os.getenv("OPENVOICE_CHECKPOINT_DIR", Path.home() / ".cache" / "openvoice" / "v2")
)
_DEVICE           = os.getenv("OPENVOICE_DEVICE", "cpu")

# ── WAV helpers (same constants as EdgeTTSProvider) ───────────────────────────
_SAMPLE_RATE  = 22050
_CHANNELS     = 1
_SAMPLE_WIDTH = 2       # 16-bit PCM

def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = _SAMPLE_RATE,
                channels: int = _CHANNELS) -> bytes:
    """Wrap raw 16-bit signed PCM into a RIFF/WAV container."""
    data_len = len(pcm_bytes)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", data_len + 36))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))           # PCM
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * channels * _SAMPLE_WIDTH))
    buf.write(struct.pack("<H", channels * _SAMPLE_WIDTH))
    buf.write(struct.pack("<H", 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm_bytes)
    return buf.getvalue()


def _wav_to_duration(wav_bytes: bytes) -> float:
    """Parse WAV header to compute approximate duration in seconds."""
    try:
        # data chunk starts at byte 44 in a standard PCM WAV
        data_size   = struct.unpack_from("<I", wav_bytes, 40)[0]
        sample_rate = struct.unpack_from("<I", wav_bytes, 24)[0]
        channels    = struct.unpack_from("<H", wav_bytes, 22)[0]
        return data_size / (sample_rate * channels * _SAMPLE_WIDTH)
    except Exception:
        return 0.0


# ── BUG-2 FIX: Extended SynthesisResult with cloning_applied flag ─────────────
@dataclass(frozen=True)
class OpenVoiceSynthesisResult(SynthesisResult):
    """
    SynthesisResult extended with cloning_applied.
    Returned by OpenVoiceProvider.synthesise() so the worker can
    write the flag back to the job meta record.
    """
    cloning_applied: bool = False


# ── OpenVoice runtime (lazy-loaded singleton) ──────────────────────────────────

class _OpenVoiceRuntime:
    """Manages lazy loading and thread-safe inference for OpenVoice v2 models."""

    def __init__(self) -> None:
        self._lock    = asyncio.Lock()
        self._loaded  = False
        self._tone_conv    = None  # ToneColorConverter
        self._se_extractor = None  # SpeakerEncoder / se_extractor

    async def ensure_loaded(self) -> bool:
        """
        Try to load OpenVoice models.
        Returns True if models loaded, False if unavailable.
        Does not raise — caller decides whether to fall back.
        """
        async with self._lock:
            if self._loaded:
                return True
            return await asyncio.get_event_loop().run_in_executor(
                None, self._load_models_sync
            )

    def _load_models_sync(self) -> bool:
        """Synchronous model loading — runs in a thread-pool executor."""
        try:
            # Guard: only try if the checkpoint directory exists
            if not _CHECKPOINT_DIR.exists():
                logger.warning(
                    "OpenVoice checkpoint directory not found: %s. "
                    "Clone myshell-ai/OpenVoice and set OPENVOICE_CHECKPOINT_DIR.",
                    _CHECKPOINT_DIR,
                )
                return False

            # Lazy import so the server starts even without PyTorch installed
            from openvoice import se_extractor as _se_mod  # noqa: F401
            from openvoice.api import ToneColorConverter    # noqa: F401

            ckpt_converter = _CHECKPOINT_DIR / "converter"
            self._tone_conv = ToneColorConverter(
                str(ckpt_converter / "config.json"), device=_DEVICE
            )
            self._tone_conv.load_ckpt(str(ckpt_converter / "checkpoint.pth"))
            self._se_extractor = _se_mod
            self._loaded = True
            logger.info(
                "OpenVoice v2 models loaded device=%s checkpoint=%s",
                _DEVICE, _CHECKPOINT_DIR,
            )
            return True

        except ImportError:
            logger.warning(
                "OpenVoice package not installed. "
                "Run: pip install openvoice (or see docs/VOICE_CLONING.md)."
            )
            return False
        except Exception as exc:
            logger.warning("OpenVoice model load failed: %s", exc)
            return False

    async def clone(
        self,
        base_wav: bytes,
        reference_audio_bytes: bytes,
        language: str,
    ) -> bytes:
        """
        Apply tone-colour transfer from *reference_audio_bytes* onto *base_wav*.
        """
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._clone_sync, base_wav, reference_audio_bytes, language,
            )

    def _clone_sync(
        self,
        base_wav: bytes,
        reference_audio_bytes: bytes,
        language: str,
    ) -> bytes:
        """Synchronous ToneColorConverter inference — runs in a thread-pool."""
        import soundfile as sf  # type: ignore
        import numpy as np      # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_path = tmp_path / "base.wav"
            ref_path  = tmp_path / "reference.wav"
            out_path  = tmp_path / "output.wav"

            base_path.write_bytes(base_wav)
            ref_path.write_bytes(reference_audio_bytes)

            target_se, _ = self._se_extractor.get_se(
                str(ref_path), self._tone_conv,
                target_dir=str(tmp_path), vad=True,
            )
            source_se, _ = self._se_extractor.get_se(
                str(base_path), self._tone_conv,
                target_dir=str(tmp_path), vad=False,
            )

            encode_message = "@MyShell"
            self._tone_conv.convert(
                audio_src_path=str(base_path),
                src_se=source_se,
                tgt_se=target_se,
                output_path=str(out_path),
                message=encode_message,
            )

            data, sr = sf.read(str(out_path), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)

            if sr != _SAMPLE_RATE:
                try:
                    import librosa  # type: ignore
                    data = librosa.resample(data, orig_sr=sr, target_sr=_SAMPLE_RATE)
                except ImportError:
                    pass

            pcm = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            return _pcm_to_wav(pcm, _SAMPLE_RATE, _CHANNELS)


# Module-level singleton
_runtime = _OpenVoiceRuntime()


# ── OpenVoiceProvider ───────────────────────────────────────────────────────────

class OpenVoiceProvider(TTSProvider):
    """
    Voice-cloning TTS provider — Phase 4B.

    Pipeline
    ────────
    1. Generate base speech with EdgeTTS (same voices as standard mode).
    2. If OpenVoice models are available and OPENVOICE_ENABLED=true:
       a. Extract speaker embedding from the uploaded reference audio.
       b. Apply ToneColorConverter to transfer the reference speaker's tone colour.
    3. If OpenVoice is unavailable, fall back to EdgeTTS output gracefully.

    The SynthesisResult is always an OpenVoiceSynthesisResult with
    `cloning_applied=True/False` so the job worker can persist that flag.
    """

    def __init__(
        self,
        reference_audio: Optional[bytes] = None,
        enabled: bool = _ENABLED_ENV,
    ) -> None:
        self._reference_audio = reference_audio
        self._enabled = enabled
        self._edge_tts = EdgeTTSProvider()

    @property
    def name(self) -> str:
        return "openvoice-v2"

    @property
    def supported_languages(self) -> set[str]:
        return set(VOICE_MAP.keys())

    async def synthesise(self, request: SynthesisRequest) -> OpenVoiceSynthesisResult:
        """
        Full voice-cloning pipeline (or graceful EdgeTTS fallback).
        Always returns an OpenVoiceSynthesisResult with cloning_applied set.
        """
        logger.info(
            "OpenVoice synthesise lang=%s text_len=%d enabled=%s ref=%s",
            request.language, len(request.text), self._enabled,
            "yes" if self._reference_audio else "no",
        )

        # Step 1: generate base speech with EdgeTTS
        base_result = await self._edge_tts.synthesise(request)

        # Step 2: apply voice cloning (if available)
        cloning_applied = False
        if self._enabled and self._reference_audio:
            models_ok = await _runtime.ensure_loaded()
            if models_ok:
                try:
                    cloned_wav = await _runtime.clone(
                        base_wav=base_result.wav_bytes,
                        reference_audio_bytes=self._reference_audio,
                        language=request.language,
                    )
                    duration_s = _wav_to_duration(cloned_wav)
                    logger.info(
                        "OpenVoice cloning done cloning_applied=True wav=%d bytes duration=%.2fs",
                        len(cloned_wav), duration_s,
                    )
                    # BUG-2 FIX: return OpenVoiceSynthesisResult with cloning_applied=True
                    return OpenVoiceSynthesisResult(
                        wav_bytes=cloned_wav,
                        sample_rate=_SAMPLE_RATE,
                        channels=_CHANNELS,
                        duration_s=duration_s,
                        cloning_applied=True,
                    )
                except Exception as exc:
                    logger.warning(
                        "OpenVoice cloning failed, falling back to EdgeTTS: %s", exc
                    )
            else:
                logger.info(
                    "OpenVoice models unavailable (not installed / no checkpoint). "
                    "Returning EdgeTTS output with cloning_applied=False."
                )
        else:
            reason = "disabled" if not self._enabled else "no reference audio"
            logger.info("OpenVoice cloning skipped (%s) — using EdgeTTS output.", reason)

        # Fallback: wrap EdgeTTS result as OpenVoiceSynthesisResult with cloning_applied=False
        logger.info(
            "OpenVoice result cloning_applied=False wav=%d bytes duration=%.2fs",
            len(base_result.wav_bytes), base_result.duration_s,
        )
        return OpenVoiceSynthesisResult(
            wav_bytes=base_result.wav_bytes,
            sample_rate=base_result.sample_rate,
            channels=base_result.channels,
            duration_s=base_result.duration_s,
            cloning_applied=False,
        )


# ── Convenience factory ─────────────────────────────────────────────────────────

def make_openvoice_provider(
    reference_audio: Optional[bytes] = None,
    enabled: Optional[bool] = None,
) -> OpenVoiceProvider:
    """
    Factory: create an OpenVoiceProvider, optionally overriding env defaults.
    """
    if enabled is None:
        enabled = _ENABLED_ENV
    return OpenVoiceProvider(reference_audio=reference_audio, enabled=enabled)
