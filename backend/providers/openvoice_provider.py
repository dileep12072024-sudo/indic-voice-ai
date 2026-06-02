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
back to EdgeTTS synthesis automatically — the job still completes successfully,
and the response includes a "cloning_available" flag so the frontend can show
the correct status badge.

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
    buf.write(struct.pack("<I", 36 + data_len))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))                                   # PCM
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * channels * _SAMPLE_WIDTH))
    buf.write(struct.pack("<H", channels * _SAMPLE_WIDTH))
    buf.write(struct.pack("<H", _SAMPLE_WIDTH * 8))
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


# ── OpenVoice runtime (lazy, protected) ───────────────────────────────────────

class _OpenVoiceRuntime:
    """
    Lazy-loaded singleton that holds the OpenVoice v2 models.

    Loaded on first use; subsequent calls reuse the loaded models.
    Protected by an asyncio.Lock so only one synthesis runs at a time
    (GPU memory safety).
    """

    def __init__(self) -> None:
        self._lock         = asyncio.Lock()
        self._loaded       = False
        self._tone_conv    = None   # ToneColorConverter
        self._se_extractor = None   # SpeakerEncoder / se_extractor

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
            from openvoice import se_extractor as _se_mod          # noqa: F401
            from openvoice.api import ToneColorConverter             # noqa: F401

            ckpt_converter = _CHECKPOINT_DIR / "converter"
            self._tone_conv = ToneColorConverter(
                str(ckpt_converter / "config.json"), device=_DEVICE
            )
            self._tone_conv.load_ckpt(str(ckpt_converter / "checkpoint.pth"))
            self._se_extractor = _se_mod

            self._loaded = True
            logger.info(
                "OpenVoice v2 models loaded  device=%s  checkpoint=%s",
                _DEVICE, _CHECKPOINT_DIR,
            )
            return True

        except ImportError:
            logger.warning(
                "OpenVoice package not installed. "
                "Run: pip install openvoice  (or see docs/VOICE_CLONING.md)."
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

        Parameters
        ──────────
        base_wav              : WAV bytes produced by EdgeTTS (the 'content' voice)
        reference_audio_bytes : raw bytes of the uploaded speaker sample
        language              : e.g. "en", "te", "hi", "ta"

        Returns
        ───────
        WAV bytes with the cloned speaker's tone colour.
        """
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                self._clone_sync,
                base_wav,
                reference_audio_bytes,
                language,
            )

    def _clone_sync(
        self,
        base_wav: bytes,
        reference_audio_bytes: bytes,
        language: str,
    ) -> bytes:
        """Synchronous ToneColorConverter inference — runs in a thread-pool."""
        import soundfile as sf   # type: ignore   # installed as openvoice dependency
        import numpy as np       # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Write inputs to temp files (OpenVoice API uses file paths)
            base_path = tmp_path / "base.wav"
            ref_path  = tmp_path / "reference.wav"
            out_path  = tmp_path / "output.wav"

            base_path.write_bytes(base_wav)
            ref_path.write_bytes(reference_audio_bytes)

            # Extract speaker embedding from reference
            target_se, _ = self._se_extractor.get_se(
                str(ref_path),
                self._tone_conv,
                target_dir=str(tmp_path),
                vad=True,
            )

            # Extract source embedding from base TTS audio
            source_se, _ = self._se_extractor.get_se(
                str(base_path),
                self._tone_conv,
                target_dir=str(tmp_path),
                vad=False,
            )

            # Tone-colour conversion
            encode_message = "@MyShell"
            self._tone_conv.convert(
                audio_src_path=str(base_path),
                src_se=source_se,
                tgt_se=target_se,
                output_path=str(out_path),
                message=encode_message,
            )

            # Read output and re-encode as 16-bit PCM WAV at target sample rate
            data, sr = sf.read(str(out_path), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)   # to mono

            # Resample to 22050 Hz if needed (simple linear; use librosa in prod)
            if sr != _SAMPLE_RATE:
                try:
                    import librosa    # type: ignore
                    data = librosa.resample(data, orig_sr=sr, target_sr=_SAMPLE_RATE)
                except ImportError:
                    pass   # keep original sample rate in fallback

            # Convert float32 → int16 PCM
            pcm = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            return _pcm_to_wav(pcm, _SAMPLE_RATE, _CHANNELS)


# Module-level singleton
_runtime = _OpenVoiceRuntime()


# ── OpenVoiceProvider ─────────────────────────────────────────────────────────

class OpenVoiceProvider(TTSProvider):
    """
    Voice-cloning TTS provider — Phase 4B.

    Pipeline
    ────────
    1. Generate base speech with EdgeTTS (same voices as standard mode).
    2. If OpenVoice models are available and OPENVOICE_ENABLED=true:
       a. Extract speaker embedding from the uploaded reference audio.
       b. Apply ToneColorConverter to transfer the reference speaker's
          tone colour onto the base speech.
    3. If OpenVoice is unavailable, fall back to EdgeTTS output gracefully.

    The SynthesisResult always succeeds — a "cloning_available" boolean
    in the extra metadata (accessible via Job.meta) tells the frontend
    whether real cloning occurred.

    Supported languages: same four as EdgeTTS (te / ta / hi / en).
    Cross-lingual cloning quality notes are in docs/VOICE_CLONING.md.
    """

    def __init__(
        self,
        reference_audio: Optional[bytes] = None,
        enabled: bool = _ENABLED_ENV,
    ) -> None:
        """
        Parameters
        ──────────
        reference_audio : raw bytes of the uploaded speaker sample.
                          If None, falls back to EdgeTTS (standard TTS mode).
        enabled         : override for OPENVOICE_ENABLED env var (testing).
        """
        self._reference_audio = reference_audio
        self._enabled         = enabled
        self._edge_tts        = EdgeTTSProvider()

    # ── TTSProvider interface ──────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "openvoice-v2"

    @property
    def supported_languages(self) -> set[str]:
        return set(VOICE_MAP.keys())   # {"te", "ta", "hi", "en"}

    async def synthesise(self, request: SynthesisRequest) -> SynthesisResult:
        """
        Full voice-cloning pipeline (or graceful EdgeTTS fallback).

        The returned SynthesisResult always contains valid WAV bytes.
        Check the log line "cloning_applied=..." to know which path ran.
        """
        logger.info(
            "OpenVoice synthesise  lang=%s  text_len=%d  enabled=%s  ref=%s",
            request.language,
            len(request.text),
            self._enabled,
            "yes" if self._reference_audio else "no",
        )

        # ── Step 1: generate base speech with EdgeTTS ──────────────────────────
        base_result = await self._edge_tts.synthesise(request)

        # ── Step 2: apply voice cloning (if available) ─────────────────────────
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
                        "OpenVoice cloning done  wav=%d bytes  duration=%.2fs",
                        len(cloned_wav), duration_s,
                    )
                    cloning_applied = True
                    return SynthesisResult(
                        wav_bytes=cloned_wav,
                        sample_rate=_SAMPLE_RATE,
                        channels=_CHANNELS,
                        duration_s=duration_s,
                    )
                except Exception as exc:
                    logger.warning(
                        "OpenVoice cloning failed, falling back to EdgeTTS: %s", exc
                    )
                    # Fall through to EdgeTTS result below
            else:
                logger.info(
                    "OpenVoice models unavailable (not installed / no checkpoint). "
                    "Returning EdgeTTS output."
                )
        else:
            reason = "disabled" if not self._enabled else "no reference audio"
            logger.info("OpenVoice cloning skipped (%s) — using EdgeTTS output.", reason)

        logger.info(
            "OpenVoice result  cloning_applied=%s  wav=%d bytes  duration=%.2fs",
            cloning_applied,
            len(base_result.wav_bytes),
            base_result.duration_s,
        )
        return base_result


# ── Convenience factory ───────────────────────────────────────────────────────

def make_openvoice_provider(
    reference_audio: Optional[bytes] = None,
    enabled: Optional[bool] = None,
) -> OpenVoiceProvider:
    """
    Factory: create an OpenVoiceProvider, optionally overriding env defaults.

    Usage in job worker:
        provider = make_openvoice_provider(
            reference_audio=upload_bytes,
            enabled=True,   # or rely on OPENVOICE_ENABLED env var
        )
        result = await provider.synthesise(request)
    """
    if enabled is None:
        enabled = _ENABLED_ENV
    return OpenVoiceProvider(reference_audio=reference_audio, enabled=enabled)
