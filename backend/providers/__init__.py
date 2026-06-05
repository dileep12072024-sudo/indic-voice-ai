"""backend/providers/__init__.py"""
from .base import (
    SynthesisRequest,
    SynthesisResult,
    TTSProvider,
    TTSProviderError,
    TTSUnavailableError,
    TTSNoAudioError,
)
from .edge_tts_provider import EdgeTTSProvider
from .openvoice_provider import (
    OpenVoiceProvider,
    OpenVoiceSynthesisResult,
    make_openvoice_provider,
    extract_and_store_embedding,
)

__all__ = [
    "SynthesisRequest", "SynthesisResult",
    "TTSProvider", "TTSProviderError", "TTSUnavailableError", "TTSNoAudioError",
    "EdgeTTSProvider",
    "OpenVoiceProvider", "OpenVoiceSynthesisResult",
    "make_openvoice_provider", "extract_and_store_embedding",
]
