"""
backend/providers/openvoice_provider.py  — Phase 7: Real Voice Cloning
───────────────────────────────────────────────────────────────────────
OpenVoiceProvider — complete speaker-embedding pipeline.

Architecture
────────────
  Reference audio (speaker sample)
          │
          ▼
  ┌──────────────────────┐
  │  extract_embedding() │  ← called ONCE at voice-create time, result stored as .npy
  └──────────────────────┘
          │  numpy array (1, D)
          ▼
  ┌────────────────────────────────────────────────────────┐
  │  synthesise()                                          │
  │    1. EdgeTTS → base WAV                               │
  │    2. load stored .npy OR extract live (no-embed path) │
  │    3. ToneColorConverter.convert()                     │
  │    4. return cloned WAV, cloning_applied=True          │
  └────────────────────────────────────────────────────────┘

Key design rules (Phase 7)
──────────────────────────
• cloning_applied=True ONLY when ToneColorConverter.convert() succeeded.
• fallback_used=True when cloning was requested but OpenVoice could not run.
• Exact exception text is always surfaced — nothing silenced.
• Stored embedding (.npy) loaded from disk if embedding_path is provided;
  live extraction used only when no stored embedding is available.
• checkpoint_manager.ensure_checkpoints() called lazily on first use.

Environment variables
─────────────────────
OPENVOICE_ENABLED        "true" | "false" (default "false")
OPENVOICE_CHECKPOINT_DIR Path to downloaded OpenVoice v2 checkpoints
                         (default ~/.cache/openvoice/v2)
OPENVOICE_DEVICE         "cpu" | "cuda" | "cuda:0" … (default "cpu")
OPENVOICE_AUTO_DOWNLOAD  "true" | "false" (default "true")
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
_ENABLED_ENV    = os.getenv("OPENVOICE_ENABLED", "false").lower() == "true"
_CHECKPOINT_DIR = Path(
    os.getenv("OPENVOICE_CHECKPOINT_DIR", Path.home() / ".cache" / "openvoice" / "v2")
)
_DEVICE         = os.getenv("OPENVOICE_DEVICE", "cpu")

# ── WAV helpers ────────────────────────────────────────────────────────────────
_SAMPLE_RATE  = 22050
_CHANNELS     = 1
_SAMPLE_WIDTH = 2       # 16-bit PCM


def _pcm_to_wav(
    pcm_bytes: bytes,
    sample_rate: int = _SAMPLE_RATE,
    channels: int = _CHANNELS,
) -> bytes:
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
        data_size   = struct.unpack_from("<I", wav_bytes, 40)[0]
        sample_rate = struct.unpack_from("<I", wav_bytes, 24)[0]
        channels    = struct.unpack_from("<H", wav_bytes, 22)[0]
        return data_size / (sample_rate * channels * _SAMPLE_WIDTH)
    except Exception:
        return 0.0


# ── Extended SynthesisResult ───────────────────────────────────────────────────

@dataclass(frozen=True)
class OpenVoiceSynthesisResult(SynthesisResult):
    """
    SynthesisResult extended with cloning_applied and fallback_used flags.
    Returned by OpenVoiceProvider.synthesise() so the worker can
    write both flags back to the job meta record.
    """
    cloning_applied: bool = False
    fallback_used:   bool = False
    cloning_error:   Optional[str] = None    # exact error reason if cloning failed


# ── OpenVoice runtime (lazy-loaded singleton) ──────────────────────────────────

class _OpenVoiceRuntime:
    """Manages lazy loading and thread-safe inference for OpenVoice v2 models."""

    def __init__(self) -> None:
        self._lock         = asyncio.Lock()
        self._loaded       = False
        self._load_error: Optional[str] = None
        self._tone_conv    = None  # ToneColorConverter
        self._se_extractor = None  # se_extractor module

    async def ensure_loaded(self) -> tuple:
        """
        Try to load OpenVoice models + verify/download checkpoints.
        Returns (ok: bool, error: str | None).
        Never raises — callers decide whether to fall back.
        """
        async with self._lock:
            if self._loaded:
                return True, None
            if self._load_error is not None:
                return False, self._load_error
            ok, err = await asyncio.get_event_loop().run_in_executor(
                None, self._load_models_sync
            )
            if not ok:
                self._load_error = err
            return ok, err

    def _load_models_sync(self) -> tuple:
        """Synchronous model loading — runs in a thread-pool executor."""
        # Step 1: checkpoint management
        try:
            from checkpoint_manager import ensure_checkpoints
            ckpt_ok, ckpt_err = ensure_checkpoints(_CHECKPOINT_DIR)
            if not ckpt_ok:
                return False, f"Checkpoint unavailable: {ckpt_err}"
        except Exception as exc:
            return False, f"checkpoint_manager import/run failed: {exc}"

        # Step 2: import PyTorch + OpenVoice
        try:
            import torch  # noqa: F401
        except ImportError:
            return False, (
                "PyTorch not installed. "
                "Run: pip install -r requirements-clone.txt"
            )

        try:
            from openvoice import se_extractor as _se_mod
            from openvoice.api import ToneColorConverter
        except ImportError as exc:
            return False, (
                f"OpenVoice package not installed ({exc}). "
                "Run: pip install openvoice @ git+https://github.com/myshell-ai/OpenVoice.git@main"
            )

        # Step 3: load ToneColorConverter
        try:
            ckpt_converter = _CHECKPOINT_DIR / "converter"
            config_path = ckpt_converter / "config.json"
            ckpt_path   = ckpt_converter / "checkpoint.pth"

            if not config_path.exists():
                return False, f"converter/config.json not found at {config_path}"
            if not ckpt_path.exists():
                return False, f"converter/checkpoint.pth not found at {ckpt_path}"

            tone_conv = ToneColorConverter(str(config_path), device=_DEVICE)
            tone_conv.load_ckpt(str(ckpt_path))
            self._tone_conv    = tone_conv
            self._se_extractor = _se_mod
            self._loaded       = True
            logger.info(
                "OpenVoice v2 models loaded  device=%s  checkpoint=%s",
                _DEVICE, _CHECKPOINT_DIR,
            )
            return True, None

        except Exception as exc:
            return False, f"OpenVoice model load failed: {exc}"

    # ── Embedding extraction ───────────────────────────────────────────────────

    async def extract_embedding(
        self,
        audio_bytes: bytes,
        dest_npy_path: Path,
    ) -> tuple:
        """
        Extract speaker embedding from audio_bytes and save as .npy to dest_npy_path.
        Returns (ok: bool, error: str | None).
        """
        ok, err = await self.ensure_loaded()
        if not ok:
            return False, f"Cannot extract embedding — models not loaded: {err}"
        return await asyncio.get_event_loop().run_in_executor(
            None, self._extract_embedding_sync, audio_bytes, dest_npy_path
        )

    def _extract_embedding_sync(
        self,
        audio_bytes: bytes,
        dest_npy_path: Path,
    ) -> tuple:
        """Synchronous embedding extraction — runs in thread-pool."""
        import numpy as np
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path  = Path(tmp)
                audio_path = tmp_path / "reference.wav"
                audio_path.write_bytes(audio_bytes)

                se, _ = self._se_extractor.get_se(
                    str(audio_path),
                    self._tone_conv,
                    target_dir=str(tmp_path),
                    vad=True,
                )
                # se is a torch.Tensor — convert to numpy and save
                dest_npy_path.parent.mkdir(parents=True, exist_ok=True)
                np.save(str(dest_npy_path), se.cpu().numpy())
                logger.info(
                    "Embedding extracted  shape=%s  saved=%s",
                    tuple(se.shape), dest_npy_path,
                )
                return True, None
        except Exception as exc:
            return False, f"Embedding extraction failed: {exc}"

    # ── Voice cloning (ToneColorConverter) ────────────────────────────────────

    async def clone(
        self,
        base_wav:              bytes,
        reference_audio_bytes: Optional[bytes],
        language:              str,
        stored_embedding_path: Optional[Path] = None,
    ) -> tuple:
        """
        Apply tone-colour transfer.
        Uses stored_embedding_path (.npy) when available;
        falls back to live extraction from reference_audio_bytes.
        Returns (cloned_wav_bytes: bytes | None, error: str | None).
        """
        ok, err = await self.ensure_loaded()
        if not ok:
            return None, err
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._clone_sync,
            base_wav,
            reference_audio_bytes,
            language,
            stored_embedding_path,
        )

    def _clone_sync(
        self,
        base_wav:              bytes,
        reference_audio_bytes: Optional[bytes],
        language:              str,
        stored_embedding_path: Optional[Path],
    ) -> tuple:
        """Synchronous ToneColorConverter inference — runs in thread-pool."""
        import numpy as np
        import soundfile as sf

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path  = Path(tmp)
                base_path = tmp_path / "base.wav"
                out_path  = tmp_path / "output.wav"

                base_path.write_bytes(base_wav)

                # ── Load or extract target speaker embedding ────────────────
                if stored_embedding_path and stored_embedding_path.exists():
                    # Fast path: load pre-extracted embedding
                    import torch
                    se_np = np.load(str(stored_embedding_path))
                    target_se = torch.from_numpy(se_np).to(_DEVICE)
                    logger.info(
                        "Loaded stored embedding  path=%s  shape=%s",
                        stored_embedding_path, tuple(target_se.shape),
                    )
                elif reference_audio_bytes:
                    # Slow path: extract live from reference audio
                    ref_path = tmp_path / "reference.wav"
                    ref_path.write_bytes(reference_audio_bytes)
                    target_se, _ = self._se_extractor.get_se(
                        str(ref_path), self._tone_conv,
                        target_dir=str(tmp_path), vad=True,
                    )
                    logger.info(
                        "Live embedding extracted  shape=%s",
                        tuple(target_se.shape),
                    )
                else:
                    return None, (
                        "No speaker embedding available: stored embedding missing "
                        "and no reference audio provided for live extraction."
                    )

                # ── Extract source (base) embedding ────────────────────────
                source_se, _ = self._se_extractor.get_se(
                    str(base_path), self._tone_conv,
                    target_dir=str(tmp_path), vad=False,
                )

                # ── ToneColorConverter ─────────────────────────────────────
                self._tone_conv.convert(
                    audio_src_path=str(base_path),
                    src_se=source_se,
                    tgt_se=target_se,
                    output_path=str(out_path),
                    message="@MyShell",
                )

                # ── Read output and convert to WAV ─────────────────────────
                data, sr = sf.read(str(out_path), dtype="float32")
                if data.ndim > 1:
                    data = data.mean(axis=1)

                if sr != _SAMPLE_RATE:
                    try:
                        import librosa
                        data = librosa.resample(data, orig_sr=sr, target_sr=_SAMPLE_RATE)
                    except ImportError:
                        logger.warning("librosa not available for resampling; keeping sr=%d", sr)

                pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("int16").tobytes()
                return _pcm_to_wav(pcm, _SAMPLE_RATE, _CHANNELS), None

        except Exception as exc:
            return None, f"ToneColorConverter.convert() failed: {exc}"


# Module-level singleton
_runtime = _OpenVoiceRuntime()


# ── Public extraction helper (called from main.py background task) ─────────────

async def extract_and_store_embedding(
    audio_bytes:    bytes,
    dest_npy_path:  Path,
) -> tuple:
    """
    Extract speaker embedding from audio and save as .npy.
    Returns (ok: bool, error: str | None).
    Exposed at module level so main.py can import it directly.
    """
    return await _runtime.extract_embedding(audio_bytes, dest_npy_path)


# ── OpenVoiceProvider ──────────────────────────────────────────────────────────

class OpenVoiceProvider(TTSProvider):
    """
    Voice-cloning TTS provider — Phase 7.

    Pipeline
    ────────
    1. Generate base speech with EdgeTTS.
    2. If OPENVOICE_ENABLED=true and a speaker embedding is available:
       a. Load stored .npy embedding (fast path, pre-extracted at voice-create time)
          OR extract live from reference_audio (slow path, fallback)
       b. Apply ToneColorConverter to transfer tone colour.
       → returns cloning_applied=True, fallback_used=False
    3. If OpenVoice unavailable / embedding missing:
       → returns cloning_applied=False, fallback_used=True, cloning_error=<exact reason>

    cloning_applied=True ONLY when ToneColorConverter.convert() succeeded.
    """

    def __init__(
        self,
        reference_audio:       Optional[bytes] = None,
        stored_embedding_path: Optional[Path]  = None,
        enabled:               bool = _ENABLED_ENV,
    ) -> None:
        self._reference_audio       = reference_audio
        self._stored_embedding_path = stored_embedding_path
        self._enabled               = enabled
        self._edge_tts              = EdgeTTSProvider()

    @property
    def name(self) -> str:
        return "openvoice-v2"

    @property
    def supported_languages(self) -> set:
        return set(VOICE_MAP.keys())

    async def synthesise(self, request: SynthesisRequest) -> OpenVoiceSynthesisResult:
        """
        Full voice-cloning pipeline (or graceful EdgeTTS fallback with exact error).
        Always returns OpenVoiceSynthesisResult with cloning_applied and fallback_used set.
        """
        logger.info(
            "OpenVoice synthesise  lang=%s  text_len=%d  enabled=%s  "
            "stored_emb=%s  ref_audio=%s",
            request.language, len(request.text), self._enabled,
            self._stored_embedding_path is not None,
            self._reference_audio is not None,
        )

        # Step 1: generate base speech with EdgeTTS (always)
        base_result = await self._edge_tts.synthesise(request)

        # Step 2: attempt voice cloning
        if self._enabled:
            has_embedding = (
                (self._stored_embedding_path and self._stored_embedding_path.exists())
                or self._reference_audio is not None
            )
            if not has_embedding:
                cloning_error = (
                    "No speaker embedding available: voice profile has not been "
                    "processed yet (cloning_ready=false) and no reference audio was "
                    "uploaded. Create the voice profile first and wait for processing."
                )
                logger.warning("OpenVoice skipped: %s", cloning_error)
                return OpenVoiceSynthesisResult(
                    wav_bytes=base_result.wav_bytes,
                    sample_rate=base_result.sample_rate,
                    channels=base_result.channels,
                    duration_s=base_result.duration_s,
                    cloning_applied=False,
                    fallback_used=True,
                    cloning_error=cloning_error,
                )

            cloned_wav, clone_error = await _runtime.clone(
                base_wav=base_result.wav_bytes,
                reference_audio_bytes=self._reference_audio,
                language=request.language,
                stored_embedding_path=self._stored_embedding_path,
            )

            if cloned_wav is not None and clone_error is None:
                duration_s = _wav_to_duration(cloned_wav)
                logger.info(
                    "OpenVoice cloning done  cloning_applied=True  "
                    "wav=%d bytes  duration=%.2fs",
                    len(cloned_wav), duration_s,
                )
                return OpenVoiceSynthesisResult(
                    wav_bytes=cloned_wav,
                    sample_rate=_SAMPLE_RATE,
                    channels=_CHANNELS,
                    duration_s=duration_s,
                    cloning_applied=True,
                    fallback_used=False,
                    cloning_error=None,
                )
            else:
                # Cloning attempted but failed — exact error surfaced
                logger.warning(
                    "OpenVoice cloning failed — falling back to EdgeTTS: %s",
                    clone_error,
                )
                return OpenVoiceSynthesisResult(
                    wav_bytes=base_result.wav_bytes,
                    sample_rate=base_result.sample_rate,
                    channels=base_result.channels,
                    duration_s=base_result.duration_s,
                    cloning_applied=False,
                    fallback_used=True,
                    cloning_error=clone_error,
                )
        else:
            # OpenVoice disabled via env flag
            reason = (
                "OpenVoice cloning is disabled. "
                "Set OPENVOICE_ENABLED=true and install requirements-clone.txt to enable."
            )
            logger.info("OpenVoice disabled — using EdgeTTS output. Reason: %s", reason)
            return OpenVoiceSynthesisResult(
                wav_bytes=base_result.wav_bytes,
                sample_rate=base_result.sample_rate,
                channels=base_result.channels,
                duration_s=base_result.duration_s,
                cloning_applied=False,
                fallback_used=True,
                cloning_error=reason,
            )


# ── Convenience factory ────────────────────────────────────────────────────────

def make_openvoice_provider(
    reference_audio:       Optional[bytes] = None,
    stored_embedding_path: Optional[Path]  = None,
    enabled:               Optional[bool]  = None,
) -> OpenVoiceProvider:
    """
    Factory: create an OpenVoiceProvider with optional stored embedding path.
    stored_embedding_path should be the .npy file saved during voice creation.
    """
    if enabled is None:
        enabled = _ENABLED_ENV
    return OpenVoiceProvider(
        reference_audio=reference_audio,
        stored_embedding_path=stored_embedding_path,
        enabled=enabled,
    )
