import os
import uuid
import struct
import math
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

# ── Directories ────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="IndicVoice AI API",
    description="AI Voice Cloning platform for Indic languages",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Supported languages ────────────────────────────────────────────────────
SUPPORTED_LANGUAGES = {
    "te": "Telugu",
    "ta": "Tamil",
    "hi": "Hindi",
    "en": "English",
}

# ── Helpers ────────────────────────────────────────────────────────────────
def _generate_placeholder_wav(output_path: Path, duration_s: float = 3.0, sample_rate: int = 22050) -> None:
    """Generate a valid WAV file with a soft sine-wave tone as placeholder output."""
    freq        = 440.0          # A4 tone
    n_samples   = int(sample_rate * duration_s)
    amplitude   = 16000

    # Build PCM samples
    samples = []
    for i in range(n_samples):
        t   = i / sample_rate
        val = int(amplitude * math.sin(2 * math.pi * freq * t))
        # Apply simple fade-in / fade-out envelope
        fade = min(1.0, min(i / (sample_rate * 0.1), (n_samples - i) / (sample_rate * 0.1)))
        samples.append(int(val * fade))

    # WAV header
    data_size   = n_samples * 2          # 16-bit mono
    file_size   = 36 + data_size

    with open(output_path, "wb") as f:
        # RIFF chunk
        f.write(b"RIFF")
        f.write(struct.pack("<I", file_size))
        f.write(b"WAVE")
        # fmt sub-chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))           # chunk size
        f.write(struct.pack("<H", 1))            # PCM
        f.write(struct.pack("<H", 1))            # mono
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * 2))  # byte rate
        f.write(struct.pack("<H", 2))            # block align
        f.write(struct.pack("<H", 16))           # bits per sample
        # data sub-chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        for s in samples:
            f.write(struct.pack("<h", max(-32768, min(32767, s))))


# ── Routes ─────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health():
    """Health-check endpoint."""
    return {
        "status": "ok",
        "service": "IndicVoice AI API",
        "version": "1.0.0",
        "supported_languages": SUPPORTED_LANGUAGES,
        "uploads_dir": str(UPLOADS_DIR),
        "outputs_dir": str(OUTPUTS_DIR),
    }


@app.post("/generate", tags=["Voice"])
async def generate(
    audio: UploadFile = File(..., description="Voice sample audio file (WAV/MP3/OGG/FLAC)"),
    text: str         = Form(..., description="Text to synthesize"),
    language: str     = Form("en", description="Target language code: te/ta/hi/en"),
):
    """
    Generate a cloned voice audio file.

    - Accepts a voice sample upload
    - Accepts text and language parameters
    - Returns a WAV file of the synthesized voice
    """
    # Validate language
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language '{language}'. Choose from: {list(SUPPORTED_LANGUAGES.keys())}",
        )

    # Validate text
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    if len(text) > 500:
        raise HTTPException(status_code=400, detail="Text must be ≤ 500 characters.")

    # Validate audio content type
    allowed_types = {"audio/wav", "audio/mpeg", "audio/mp3", "audio/ogg", "audio/flac", "audio/webm", "audio/x-wav"}
    if audio.content_type and audio.content_type not in allowed_types:
        # Allow through if content_type is ambiguous (some browsers send application/octet-stream)
        pass

    # Save uploaded sample
    job_id     = uuid.uuid4().hex
    ext        = Path(audio.filename).suffix if audio.filename else ".wav"
    upload_path = UPLOADS_DIR / f"{job_id}_sample{ext}"
    content    = await audio.read()

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")

    with open(upload_path, "wb") as f:
        f.write(content)

    # Generate placeholder output WAV
    # In production: replace this block with real TTS/voice-cloning inference
    output_path = OUTPUTS_DIR / f"{job_id}_output.wav"
    duration    = min(3.0 + len(text) * 0.05, 10.0)   # rough duration estimate
    _generate_placeholder_wav(output_path, duration_s=duration)

    return FileResponse(
        path=str(output_path),
        media_type="audio/wav",
        filename=f"indicvoice_{language}_{job_id[:8]}.wav",
        headers={
            "X-Job-Id":   job_id,
            "X-Language": SUPPORTED_LANGUAGES[language],
            "X-Text-Len": str(len(text)),
        },
    )


@app.get("/", tags=["System"])
async def root():
    return {"message": "IndicVoice AI API is running. Visit /docs for the API reference."}
