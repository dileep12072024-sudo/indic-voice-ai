import io
import logging
import struct
import uuid
from pathlib import Path

import asyncio
import edge_tts
import miniaudio

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger("indicvoice")

# ── Directories ────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="IndicVoice AI API",
    description=(
        "Backend API for IndicVoice AI — an AI Voice Synthesis platform "
        "supporting Telugu, Tamil, Hindi, and English.\n\n"
        "**Phase 3A** uses Microsoft Neural TTS (edge-tts) for real speech "
        "synthesis across all four supported languages."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── TTS configuration ──────────────────────────────────────────────────────
# Microsoft Neural TTS voices via edge-tts.
# Voices confirmed working as of 2024 — see DEVELOPMENT.md for alternatives.
LANGUAGE_CONFIG: dict[str, dict] = {
    "te": {
        "name":    "Telugu",
        "voice":   "te-IN-ShrutiNeural",
        "gender":  "Female",
        "locale":  "te-IN",
    },
    "ta": {
        "name":    "Tamil",
        "voice":   "ta-IN-PallaviNeural",
        "gender":  "Female",
        "locale":  "ta-IN",
    },
    "hi": {
        "name":    "Hindi",
        "voice":   "hi-IN-SwaraNeural",
        "gender":  "Female",
        "locale":  "hi-IN",
    },
    "en": {
        "name":    "English",
        "voice":   "en-US-JennyNeural",
        "gender":  "Female",
        "locale":  "en-US",
    },
}

# Output WAV parameters — fixed for consistent downstream playback
OUTPUT_SAMPLE_RATE = 22050
OUTPUT_CHANNELS    = 1          # mono
OUTPUT_SAMPLE_WIDTH = 2         # 16-bit

# Upload limits
MAX_UPLOAD_BYTES   = 50 * 1024 * 1024   # 50 MB
ACCEPTED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".webm", ".flac"}


# ── TTS helpers ────────────────────────────────────────────────────────────

def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int, channels: int) -> bytes:
    """Wrap raw 16-bit signed PCM bytes into a valid RIFF/WAV container."""
    data_len = len(pcm_bytes)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_len))
    buf.write(b"WAVE")
    # fmt chunk
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))                        # chunk size
    buf.write(struct.pack("<H", 1))                         # PCM = 1
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * channels * OUTPUT_SAMPLE_WIDTH))
    buf.write(struct.pack("<H", channels * OUTPUT_SAMPLE_WIDTH))
    buf.write(struct.pack("<H", OUTPUT_SAMPLE_WIDTH * 8))   # bits per sample
    # data chunk
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm_bytes)
    return buf.getvalue()


async def _synthesize_speech(text: str, language: str) -> bytes:
    """
    Synthesise *text* using the Microsoft Neural TTS voice for *language*.

    Returns the audio as a WAV (RIFF/PCM) byte string.
    Raises HTTPException(503) when the TTS service is unreachable.
    Raises HTTPException(502) on any other synthesis failure.
    """
    config = LANGUAGE_CONFIG[language]
    voice  = config["voice"]
    logger.info("TTS request: lang=%s voice=%s text_len=%d", language, voice, len(text))

    # ── Stream audio chunks from edge-tts ─────────────────────────────────
    try:
        communicate = edge_tts.Communicate(text, voice)
        audio_chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
    except edge_tts.exceptions.NoAudioReceived:
        logger.error("edge-tts returned no audio for lang=%s", language)
        raise HTTPException(
            status_code=502,
            detail=(
                f"TTS returned no audio for language '{language}'. "
                "The text may be too short or contain unsupported characters."
            ),
        )
    except Exception as exc:
        logger.error("edge-tts error: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "TTS service is temporarily unavailable. "
                "edge-tts requires an internet connection to the Microsoft Speech API. "
                f"Error: {exc}"
            ),
        )

    if not audio_chunks:
        raise HTTPException(
            status_code=502,
            detail="TTS returned empty audio. Please try again.",
        )

    raw_audio = b"".join(audio_chunks)
    logger.info("Received %d bytes of raw TTS audio", len(raw_audio))

    # ── Decode MP3/WebM → raw PCM → WAV ───────────────────────────────────
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
        logger.error("Audio decode/conversion error: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert TTS audio to WAV: {exc}",
        )

    duration = len(pcm_bytes) / (decoded.sample_rate * decoded.nchannels * OUTPUT_SAMPLE_WIDTH)
    logger.info(
        "WAV ready: %d bytes, %.2fs, %dHz %dch",
        len(wav_bytes), duration, decoded.sample_rate, decoded.nchannels,
    )
    return wav_bytes


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"], include_in_schema=False)
async def root():
    return {
        "service": "IndicVoice AI API",
        "version": "2.0.0",
        "tts_engine": "edge-tts (Microsoft Neural TTS)",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["System"], summary="Health check")
async def health():
    """Returns service status, TTS engine info, and supported languages."""
    return {
        "status": "ok",
        "service": "IndicVoice AI API",
        "version": "2.0.0",
        "tts_engine": "edge-tts (Microsoft Neural TTS)",
        "supported_languages": {
            code: {
                "name":   cfg["name"],
                "voice":  cfg["voice"],
                "gender": cfg["gender"],
                "locale": cfg["locale"],
            }
            for code, cfg in LANGUAGE_CONFIG.items()
        },
        "max_upload_mb":     MAX_UPLOAD_BYTES // (1024 * 1024),
        "accepted_formats":  sorted(ACCEPTED_EXTENSIONS),
        "output_format":     "WAV (PCM 16-bit, 22050 Hz, mono)",
    }


@app.post("/generate", tags=["Voice"], summary="Generate speech audio")
async def generate(
    audio:    UploadFile = File(..., description="Voice sample (WAV/MP3/OGG/WEBM/FLAC, max 50 MB)"),
    text:     str        = Form(..., description="Text to synthesise (1–500 characters)"),
    language: str        = Form("en", description="Language code: te / ta / hi / en"),
):
    """
    Synthesise speech from *text* using the Neural TTS voice for *language*.

    - **audio** – reference voice sample (accepted but not used for cloning in Phase 3A)
    - **text**  – the text to speak (1–500 characters)
    - **language** – `te` Telugu · `ta` Tamil · `hi` Hindi · `en` English

    Returns a `audio/wav` attachment ready for direct browser playback.
    """
    # ── Validate language ──────────────────────────────────────────────────
    if language not in LANGUAGE_CONFIG:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported language '{language}'. "
                f"Accepted values: {list(LANGUAGE_CONFIG.keys())}"
            ),
        )

    # ── Validate text ──────────────────────────────────────────────────────
    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="'text' must not be empty.")
    if len(text) > 500:
        raise HTTPException(
            status_code=422,
            detail=f"'text' must be ≤ 500 characters (got {len(text)}).",
        )

    # ── Validate audio upload ──────────────────────────────────────────────
    filename = audio.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in ACCEPTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file extension '{ext}'. Accepted: {sorted(ACCEPTED_EXTENSIONS)}",
        )

    content = await audio.read()
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Uploaded audio file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        mb = len(content) / 1024 / 1024
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({mb:.1f} MB). "
                f"Maximum allowed: {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            ),
        )

    # ── Save uploaded sample ───────────────────────────────────────────────
    job_id      = uuid.uuid4().hex
    upload_path = UPLOADS_DIR / f"{job_id}_sample{ext}"
    with open(upload_path, "wb") as f:
        f.write(content)
    logger.info("Saved upload: %s (%d bytes)", upload_path.name, len(content))

    # ── Synthesise speech ──────────────────────────────────────────────────
    wav_bytes = await _synthesize_speech(text, language)

    # ── Persist output ─────────────────────────────────────────────────────
    output_path = OUTPUTS_DIR / f"{job_id}_output.wav"
    with open(output_path, "wb") as f:
        f.write(wav_bytes)
    logger.info("Saved output: %s (%d bytes)", output_path.name, len(wav_bytes))

    # ── Return WAV ─────────────────────────────────────────────────────────
    cfg = LANGUAGE_CONFIG[language]
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "Content-Disposition":            f'attachment; filename="indicvoice_{language}_{job_id[:8]}.wav"',
            "X-Job-Id":                       job_id,
            "X-Language":                     cfg["name"],
            "X-Voice":                        cfg["voice"],
            "X-Text-Len":                     str(len(text)),
            "Access-Control-Expose-Headers":  "X-Job-Id, X-Language, X-Voice, X-Text-Len",
        },
    )
