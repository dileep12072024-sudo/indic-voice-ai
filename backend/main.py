"""
backend/main.py  — Phase 4B
────────────────────────────
IndicVoice AI API with voice cloning feature flag.

New in Phase 4B:
  POST /generate now accepts an optional `mode` field:
    "standard"  (default) — EdgeTTS neural TTS, no cloning
    "clone"                — OpenVoice v2 tone-colour transfer
                             Falls back to EdgeTTS if OpenVoice
                             models are not installed / enabled.

  GET  /health   → now reports cloning_available flag.
  GET  /job/{id} → job.meta carries the mode that was used
                   and cloning_applied (True only if OpenVoice ran).

Architecture:
  All TTS logic is isolated behind the TTSProvider interface.
  The job worker receives a pre-constructed provider (either
  EdgeTTSProvider or OpenVoiceProvider) — it never imports a
  concrete provider directly.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from jobs import Job, JobMeta, JobStatus, LocalJobManager
from providers import (
    EdgeTTSProvider,
    OpenVoiceProvider,
    SynthesisRequest,
    TTSNoAudioError,
    TTSProvider,
    TTSProviderError,
    TTSUnavailableError,
    make_openvoice_provider,
)
from storage import LocalStorageBackend

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger("indicvoice")

# ── Directories ────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
STORAGE_DIR = BASE_DIR
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# ── Feature flag: voice cloning ────────────────────────────────────────────
# Set OPENVOICE_ENABLED=true in the environment to activate real cloning.
# When false the "clone" mode still works but uses EdgeTTS internally.
OPENVOICE_ENABLED: bool = os.getenv("OPENVOICE_ENABLED", "false").lower() == "true"

# ── Singletons (created once at startup) ──────────────────────────────────
job_manager  = LocalJobManager()
storage      = LocalStorageBackend(base_dir=STORAGE_DIR, serve_prefix="/files")
_edge_tts    = EdgeTTSProvider()   # default / fallback provider

# ── Lifespan ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "IndicVoice AI API starting  version=4.0.0  "
        "openvoice_enabled=%s",
        OPENVOICE_ENABLED,
    )
    yield
    logger.info("IndicVoice AI API shutting down")

# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="IndicVoice AI API",
    description=(
        "Backend API for IndicVoice AI — Phase 4B voice cloning MVP.\n\n"
        "**POST /generate** accepts a voice sample + text + language + mode and "
        "returns a `job_id` immediately (HTTP 202). "
        "Poll **GET /job/{job_id}** for status and the output URL.\n\n"
        "Set `mode=clone` to enable OpenVoice v2 tone-colour transfer "
        "(requires `OPENVOICE_ENABLED=true` + checkpoints installed)."
    ),
    version="4.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
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

# ── Static-file serving (local dev) ───────────────────────────────────────
app.mount("/files", StaticFiles(directory=str(STORAGE_DIR)), name="files")

# ── Validation constants ───────────────────────────────────────────────────
SUPPORTED_LANGUAGES: dict[str, dict] = {
    "te": {"name": "Telugu",  "voice": "te-IN-ShrutiNeural"},
    "ta": {"name": "Tamil",   "voice": "ta-IN-PallaviNeural"},
    "hi": {"name": "Hindi",   "voice": "hi-IN-SwaraNeural"},
    "en": {"name": "English", "voice": "en-US-JennyNeural"},
}
SUPPORTED_MODES     = {"standard", "clone"}
MAX_UPLOAD_BYTES    = 50 * 1024 * 1024
ACCEPTED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".webm", ".flac"}

# ── Background worker ──────────────────────────────────────────────────────

async def _run_tts_job(
    job_id:   str,
    text:     str,
    language: str,
    voice:    str,
    provider: TTSProvider,
    mode:     str,           # BUG-2 FIX: passed in so we can write it back to meta
) -> None:
    """
    Background task: runs TTS/voice-cloning inference and writes output.

    State machine:
      QUEUED → PROCESSING → COMPLETED
                          → FAILED

    The *provider* argument is a fully-constructed TTSProvider instance
    (either EdgeTTSProvider or OpenVoiceProvider).  The worker never
    imports a concrete provider — it only calls provider.synthesise().
    """
    logger.info("Worker START  job_id=%s  provider=%s  mode=%s", job_id, provider.name, mode)

    # Transition: QUEUED → PROCESSING
    try:
        await job_manager.update_status(job_id, JobStatus.PROCESSING)
    except KeyError:
        logger.error("Worker cannot find job_id=%s (already evicted?)", job_id)
        return

    # Synthesise
    try:
        result = await provider.synthesise(
            SynthesisRequest(text=text, language=language, voice=voice)
        )
    except TTSUnavailableError as exc:
        logger.error("Worker TTS unavailable  job_id=%s  err=%s", job_id, exc)
        await job_manager.update_status(
            job_id, JobStatus.FAILED,
            error_message=f"TTS service unavailable: {exc}",
        )
        return
    except TTSNoAudioError as exc:
        logger.error("Worker TTS no audio  job_id=%s  err=%s", job_id, exc)
        await job_manager.update_status(
            job_id, JobStatus.FAILED,
            error_message=f"TTS returned no audio: {exc}",
        )
        return
    except TTSProviderError as exc:
        logger.error("Worker TTS error  job_id=%s  err=%s", job_id, exc)
        await job_manager.update_status(
            job_id, JobStatus.FAILED,
            error_message=f"TTS provider error: {exc}",
        )
        return
    except Exception as exc:
        logger.exception("Worker unexpected error  job_id=%s", job_id)
        await job_manager.update_status(
            job_id, JobStatus.FAILED,
            error_message=f"Unexpected error: {exc}",
        )
        return

    # BUG-2 FIX: determine whether real cloning was applied.
    # OpenVoiceProvider sets result.cloning_applied when it runs the real
    # ToneColorConverter; for all other providers it stays False.
    cloning_applied: bool = getattr(result, "cloning_applied", False)

    # Persist output
    output_key = f"outputs/{job_id}_output.wav"
    try:
        await storage.put(output_key, result.wav_bytes, content_type="audio/wav")
        output_url = await storage.presign_url(output_key, ttl_seconds=3600)
    except Exception as exc:
        logger.exception("Worker storage error  job_id=%s", job_id)
        await job_manager.update_status(
            job_id, JobStatus.FAILED,
            error_message=f"Storage write failed: {exc}",
        )
        return

    # BUG-2 FIX: write cloning_applied back to the job's meta so
    # GET /job/{id} exposes it and the frontend can show real-vs-fallback.
    job = await job_manager.get_job(job_id)
    if job and job.meta:
        job.meta.cloning_applied = cloning_applied

    # Transition: PROCESSING → COMPLETED
    await job_manager.update_status(
        job_id, JobStatus.COMPLETED,
        output_key=output_key,
        output_url=output_url,
    )
    logger.info(
        "Worker DONE  job_id=%s  provider=%s  mode=%s  cloning_applied=%s  "
        "wav=%d bytes  duration=%.2fs",
        job_id, provider.name, mode, cloning_applied,
        len(result.wav_bytes), result.duration_s,
    )

# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"], include_in_schema=False)
async def root():
    return {
        "service":           "IndicVoice AI API",
        "version":           "4.0.0",
        "phase":             "4B — Voice Cloning MVP",
        "openvoice_enabled": OPENVOICE_ENABLED,
        "docs":              "/docs",
        "health":            "/health",
    }

@app.get("/health", tags=["System"], summary="Health check")
async def health():
    """Returns service status, TTS engine, cloning availability, and supported languages."""
    return {
        "status":            "ok",
        "service":           "IndicVoice AI API",
        "version":           "4.0.0",
        "phase":             "4B — Voice Cloning MVP",
        "tts_engine":        _edge_tts.name,
        "cloning_available": OPENVOICE_ENABLED,
        "supported_modes":   sorted(SUPPORTED_MODES),
        "supported_languages": {
            code: {
                "name":  cfg["name"],
                "voice": cfg["voice"],
            }
            for code, cfg in SUPPORTED_LANGUAGES.items()
        },
        "max_upload_mb":    MAX_UPLOAD_BYTES // (1024 * 1024),
        "accepted_formats": sorted(ACCEPTED_EXTENSIONS),
        "output_format":    "WAV (PCM 16-bit, 22050 Hz, mono)",
        "job_system":       "LocalJobManager (in-memory)",
        "storage_backend":  "LocalStorageBackend (filesystem)",
    }

@app.post(
    "/generate",
    status_code=202,
    tags=["Voice"],
    summary="Submit a TTS / voice-cloning job (async)",
    response_description="Job accepted — poll /job/{job_id} for status",
)
async def generate(
    background_tasks: BackgroundTasks,
    audio:    UploadFile = File(...,        description="Voice sample — WAV/MP3/OGG/WEBM/FLAC, max 50 MB"),
    text:     str        = Form(...,        description="Text to synthesise, 1–500 characters"),
    language: str        = Form("en",       description="Language code: te / ta / hi / en"),
    mode:     str        = Form("standard", description="Mode: 'standard' (EdgeTTS) | 'clone' (OpenVoice v2)"),
):
    """
    **Asynchronous** TTS / voice-cloning job submission.

    Modes
    ─────
    * **standard** — EdgeTTS neural TTS (fast, ~3 s, no cloning)
    * **clone**    — OpenVoice v2 tone-colour transfer
      * Requires `OPENVOICE_ENABLED=true` + checkpoints installed.
      * When OpenVoice is unavailable the job still completes using
        EdgeTTS; `cloning_applied` in the job record will be `false`.

    Flow
    ────
    1. Validates all inputs.
    2. Saves the uploaded voice sample to storage.
    3. Creates a job record (status = `queued`).
    4. Starts a background worker with the appropriate provider.
    5. Returns **HTTP 202** with `{ job_id, mode, poll_url }` immediately.

    Poll **GET /job/{job_id}** until `status` is `completed` or `failed`.
    When `completed`, the response contains `output_url` and `cloning_applied`.
    """
    # ── Validate language ──────────────────────────────────────────────────
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported language '{language}'. Accepted: {list(SUPPORTED_LANGUAGES)}",
        )

    # ── Validate mode ──────────────────────────────────────────────────────
    mode = mode.strip().lower()
    if mode not in SUPPORTED_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported mode '{mode}'. Accepted: {sorted(SUPPORTED_MODES)}",
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
    ext      = Path(filename).suffix.lower()
    if ext not in ACCEPTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{ext}'. Accepted: {sorted(ACCEPTED_EXTENSIONS)}",
        )

    content = await audio.read()
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded audio file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        mb = len(content) / 1024 / 1024
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({mb:.1f} MB). Max: {MAX_UPLOAD_BYTES//(1024*1024)} MB.",
        )

    # ── Persist upload ─────────────────────────────────────────────────────
    job_id     = uuid.uuid4().hex
    upload_key = f"uploads/{job_id}_sample{ext}"
    await storage.put(upload_key, content)
    logger.info("Upload saved  key=%s  size=%d  mode=%s", upload_key, len(content), mode)

    # ── Select provider ────────────────────────────────────────────────────
    # "standard" → EdgeTTSProvider (stateless singleton)
    # "clone"    → OpenVoiceProvider (per-request, carries reference audio)
    if mode == "clone":
        provider: TTSProvider = make_openvoice_provider(
            reference_audio=content,
            enabled=OPENVOICE_ENABLED,
        )
    else:
        provider = _edge_tts

    # ── Create job ─────────────────────────────────────────────────────────
    cfg  = SUPPORTED_LANGUAGES[language]
    # BUG-2 FIX: store mode in JobMeta so GET /job/:id can report it
    meta = JobMeta(
        language=language,
        text=text,
        voice=cfg["voice"],
        upload_key=upload_key,
        sample_size_b=len(content),
        mode=mode,                  # NEW
        cloning_applied=False,      # NEW — updated by worker after inference
    )
    job = await job_manager.create_job(meta)

    # ── Enqueue background worker ──────────────────────────────────────────
    background_tasks.add_task(
        _run_tts_job,
        job_id=job.id,
        text=text,
        language=language,
        voice=cfg["voice"],
        provider=provider,
        mode=mode,          # BUG-2 FIX: pass mode to worker
    )

    logger.info(
        "Job enqueued  job_id=%s  lang=%s  mode=%s  text_len=%d",
        job.id, language, mode, len(text),
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id":            job.id,
            "status":            job.status.value,
            "mode":              mode,
            "cloning_enabled":   OPENVOICE_ENABLED and mode == "clone",
            "poll_url":          f"/job/{job.id}",
            "message":           "Job accepted. Poll poll_url for status.",
        },
        headers={
            "Location":    f"/job/{job.id}",
            "X-Job-Id":    job.id,
            "X-Job-Mode":  mode,
            "Retry-After": "2",
        },
    )

@app.get(
    "/job/{job_id}",
    tags=["Voice"],
    summary="Get job status",
)
async def get_job(job_id: str):
    """
    Return the current status of a TTS / voice-cloning job.

    | status       | meaning                                           |
    |------------- |---------------------------------------------------|
    | `queued`     | Job created, waiting for a worker                 |
    | `processing` | TTS inference is running                          |
    | `completed`  | WAV is ready — `output_url` is populated          |
    | `failed`     | Unrecoverable error — `error_message` explains why|

    When `status == completed`, fetch or play the audio at `output_url`.
    The `cloning_applied` field is `true` only when OpenVoice v2 actually ran
    (i.e. `OPENVOICE_ENABLED=true` + models loaded + inference succeeded).
    """
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found. It may have expired (TTL 8h) or never existed.",
        )
    return job.to_dict()

@app.delete(
    "/job/{job_id}",
    tags=["Voice"],
    summary="Delete a job and its output (GDPR erasure)",
)
async def delete_job(job_id: str):
    """
    Remove a job record and its associated storage objects.
    Useful for GDPR right-to-erasure requests and manual cleanup.
    Returns 404 if the job does not exist.
    """
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job.output_key:
        await storage.delete(job.output_key)
    if job.meta and job.meta.upload_key:
        await storage.delete(job.meta.upload_key)

    await job_manager.delete_job(job_id)

    logger.info("Job deleted (GDPR)  job_id=%s", job_id)
    return {"deleted": True, "job_id": job_id}

@app.get(
    "/jobs",
    tags=["Voice"],
    summary="List recent jobs (dev/debug)",
)
async def list_jobs():
    """
    Return all in-memory jobs (dev / debug only).
    Auth-gate this endpoint before exposing to the public internet.
    """
    jobs = await job_manager.list_jobs()
    return {
        "count": len(jobs),
        "jobs":  [j.to_dict() for j in jobs],
    }
