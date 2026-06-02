"""
backend/providers/base.py
─────────────────────────
Abstract TTSProvider interface.

All TTS engines (edge-tts, Piper, Azure Speech, Workers AI, etc.)
implement this interface.  The job worker only calls synthesise() —
it never imports a concrete provider directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SynthesisRequest:
    """Everything a TTS provider needs to produce an audio clip."""
    text:     str           # text to speak (UTF-8, may contain Indic script)
    language: str           # "te" | "ta" | "hi" | "en"
    voice:    str           # provider-specific voice identifier
    # Future fields for voice cloning (Phase 4):
    # speaker_embedding: Optional[bytes] = None
    # reference_audio_key: Optional[str] = None


@dataclass(frozen=True)
class SynthesisResult:
    """
    Output from a successful synthesis.
    Always contains raw WAV bytes (RIFF/PCM 16-bit).
    """
    wav_bytes:   bytes   # complete WAV file ready to write to storage
    sample_rate: int     # Hz (e.g. 22050)
    channels:    int     # 1 = mono
    duration_s:  float   # approximate duration in seconds


class TTSProvider(ABC):
    """
    Storage-agnostic, stateless TTS interface.

    Each provider is responsible for:
      1. Accepting a SynthesisRequest
      2. Producing a SynthesisResult (WAV bytes + metadata)
      3. Raising an appropriate exception on failure

    Providers must NOT write to disk or talk to the job manager —
    that is the job worker's responsibility.

    Concrete implementations:
      - EdgeTTSProvider          → edge-tts (local dev, all 4 Indic langs)
      - AzureSpeechProvider      → Azure Cognitive Services REST (production)
      - PiperTTSProvider         → offline ONNX (en + hi, no internet needed)
      - WorkersAITTSProvider     → Cloudflare Workers AI binding (future)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name, e.g. 'edge-tts' or 'azure-speech'."""

    @property
    @abstractmethod
    def supported_languages(self) -> set[str]:
        """Language codes this provider can handle, e.g. {'te', 'ta', 'hi', 'en'}."""

    def supports(self, language: str) -> bool:
        """Convenience: True if *language* is in supported_languages."""
        return language in self.supported_languages

    @abstractmethod
    async def synthesise(self, request: SynthesisRequest) -> SynthesisResult:
        """
        Perform TTS synthesis and return a SynthesisResult.

        Raises:
            TTSUnavailableError   – provider endpoint unreachable (HTTP 503 upstream)
            TTSNoAudioError       – provider returned empty / no audio
            TTSProviderError      – any other provider-level failure
        """


# ── Provider-specific exceptions ──────────────────────────────────────────

class TTSProviderError(Exception):
    """Base class for all TTS provider errors."""

class TTSUnavailableError(TTSProviderError):
    """TTS service endpoint is unreachable (network / quota / offline)."""

class TTSNoAudioError(TTSProviderError):
    """Provider responded successfully but returned no audio data."""
