import os
import uuid
import struct
import math
import logging
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exception_handlers import http_exception_handler

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
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
        "Backend API for IndicVoice AI — an AI Voice Cloning platform "
        "supporting Telugu, Tamil, Hindi, and English.\n\n"
        "**Phase 1** uses a placeholder sine-wave WAV generator. "
        "Real TTS/voice-cloning inference will be wired in Phase 2."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ───────────────────────────────────────────────────────────────────
# Allow the Vite dev server and any localhost origin during development.
# In production, restrict to your actual frontend domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite default
        "http://localhost:4173",   # Vite preview
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Constants ──────────────────────────────────────────────────────────────
SUPPORTED_LANGUAGES: dict[str, str] = {
    "te": "Telugu",
    "ta": "Tamil",
    "hi": "Hindi",
    "en": "English",
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 50 MB
ACCEPTED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".webm", ".flac"}


# ── Helpers ────────────────────────────────────────────────────────────────
def _generate_placeholder_wav(output_path: Path, duration_s: float = 3.0, sample_rate: int = 22050) -> None:
    """
    Write a valid 16-bit mono PCM WAV file containing a sine-wave tone.
    This is a placeholder; replace with real TTS inference in a later phase.
    """
    freq      = 440.0
    n_samples = int(sample_rate * duration_s)
    amplitude = 16000
    fade_len  = int(sample_rate * 0.1)      # 100 ms fade in/out

    samples = []
    for i in range(n_samples):
        t    = i / sample_rate
        raw  = amplitude * math.sin(2 * math.pi * freq * t)
        fade = min(1.0, i / fade_len) if i < fade_len else min(1.0, (n_samples - i) / fade_len)
        samples.append(int(raw * fade))

    data_size = n_samples * 2          # 16-bit = 2 bytes per sample

    with open(output_path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))            # chunk size
        f.write(struct.pack("<H", 1))             # PCM
        f.write(struct.pack("<H", 1))             # mono
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * 2))
        f.write(struct.pack("<H", 2))             # block align
        f.write(struct.pack("<H", 16))            # bits per sample
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        for s in samples:
            f.write(struct.pack("<h", max(-32768, min(32767, s))))

    logger.info("Generated placeholder WAV: %s (%.1f s, %d samples)", output_path.name, duration_s, n_samples)


# ── Routes ─────────────────────────────────────────────────────────────────
@app.get("/", tags=["System"], include_in_schema=False)
async def root():
    return {
        "service": "IndicVoice AI API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["System"], summary="Health check")
async def health():
    """Returns service status, version, and supported languages."""
    return {
        "status": "ok",
        "service": "IndicVoice AI API",
        "version": "1.0.0",
        "supported_languages": SUPPORTED_LANGUAGES,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "accepted_formats": sorted(ACCEPTED_EXTENSIONS),
        "directories": {
            "uploads": str(UPLOADS_DIR),
            "outputs": str(OUTPUTS_DIR),
        },
    }


@app.post("/generate", tags=["Voice"], summary="Generate cloned voice audio")
async def generate(
    audio: UploadFile = File(..., description="Voice sample (WAV / MP3 / OGG / WEBM / FLAC, max 50 MB)"),
    text:  str        = Form(..., description="Text to synthesize (1–500 characters)"),
    language: str     = Form("en", description="Language code: te / ta / hi / en"),
):
    """
    Accept a voice sample and text, return a synthesized WAV file.

    - **audio**: the reference voice sample
    - **text**: the text to synthesize in the target speaker's voice
    - **language**: one of `te` (Telugu), `ta` (Tamil), `hi` (Hindi), `en` (English)

    Returns a `audio/wav` file as an attachment.
    """
    # ── Validate language ──────────────────────────────────────────────────
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported language '{language}'. Accepted values: {list(SUPPORTED_LANGUAGES.keys())}",
        )

    # ── Validate text ──────────────────────────────────────────────────────
    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="'text' must not be empty.")
    if len(text) > 500:
        raise HTTPException(status_code=422, detail=f"'text' must be ≤ 500 characters (got {len(text)}).")

    # ── Validate file extension ────────────────────────────────────────────
    filename = audio.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in ACCEPTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file extension '{ext}'. Accepted: {sorted(ACCEPTED_EXTENSIONS)}",
        )

    # ── Read + validate file size ──────────────────────────────────────────
    content = await audio.read()
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Uploaded audio file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        mb = len(content) / 1024 / 1024
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({mb:.1f} MB). Maximum allowed size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )

    # ── Save upload ────────────────────────────────────────────────────────
    job_id      = uuid.uuid4().hex
    upload_path = UPLOADS_DIR / f"{job_id}_sample{ext}"
    with open(upload_path, "wb") as f:
        f.write(content)
    logger.info("Saved upload: %s (%d bytes)", upload_path.name, len(content))

    # ── Generate output WAV (placeholder) ─────────────────────────────────
    output_path = OUTPUTS_DIR / f"{job_id}_output.wav"
    duration    = min(2.0 + len(text) * 0.06, 12.0)   # rough proportional duration
    _generate_placeholder_wav(output_path, duration_s=duration)

    # ── Return file ────────────────────────────────────────────────────────
    return FileResponse(
        path=str(output_path),
        media_type="audio/wav",
        filename=f"indicvoice_{language}_{job_id[:8]}.wav",
        headers={
            "X-Job-Id":   job_id,
            "X-Language": SUPPORTED_LANGUAGES[language],
            "X-Text-Len": str(len(text)),
            "Access-Control-Expose-Headers": "X-Job-Id, X-Language, X-Text-Len",
        },
    )
