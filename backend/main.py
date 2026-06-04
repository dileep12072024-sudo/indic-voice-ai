"""
backend/main.py  — Phase 4B
────────────────────────────────────────────────
IndicVoice AI API with voice cloning feature flag.

Changes in this revision
────────────────────────
FIX-2a  _cleanup_expired_jobs() background coroutine runs every
        CLEANUP_INTERVAL_S (30 min) and sweeps jobs older than
        JOB_TTL_SECONDS (8 h). Prevents unbounded RAM and disk growth.
FIX-2b  /health now includes active_jobs, total_jobs, outputs_dir_mb
        so operators can see queue depth and disk usage at a glance.
"""

from __future__ import annotations

import asyncio
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
OPENVOICE_ENABLED: bool = os.getenv("OPENVOICE_ENABLED", "false").lower() == "true"

# ── FIX-2a: TTL cleanup constants ─────────────────────────────────────────
JOB_TTL_SECONDS:      float = float(os.getenv("JOB_TTL_SECONDS",      str(8 * 3600)))
CLEANUP_INTERVAL_S:   float = float(os.getenv("CLEANUP_INTERVAL_S",   str(30 * 60)))

# ── Singletons ─────────────────────────────────────────────────────────────
job_manager  = LocalJobManager()
storage      = LocalStorageBackend(base_dir=STORAGE_DIR, serve_prefix="/files")
_edge_tts    = EdgeTTSProvider()


# ── FIX-2a: background TTL cleanup ────────────────────────────────────────
async def _cleanup_expired_jobs() -> None:
    """
    Periodic background coroutine.
    Sweeps all jobs older than JOB_TTL_SECONDS, deletes their storage
    objects, and removes them from the job manager.

    Runs every CLEANUP_INTERVAL_S seconds (default: 30 minutes).
    Prevents unbounded RAM accumulation and disk fill on long-running instances.
    """
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_S)
        try:
            now = time.time()
            jobs = await job_manager.list_jobs()
            evicted = 0
            for job in jobs:
                age = now - job.created_at
                if age >= JOB_TTL_SECONDS:
                    # Delete storage objects
                    if job.output_key:
                        try:
                            await storage.delete(job.output_key)
                        except Exception:
                            pass  # already deleted or never written
                    if job.meta and job.meta.upload_key:
                        try:
                            await storage.delete(job.meta.upload_key)
                        except Exception:
                            pass
                    await job_manager.delete_job(job.id)
                    evicted += 1
            if evicted:
                logger.info(
                    "TTL cleanup: evicted %d expired jobs (ttl=%.0fh)",
                    evicted, JOB_TTL_SECONDS / 3600,
                )
        except Exception as exc:
            logger.warning("TTL cleanup error (non-fatal): %s", exc)


def _outputs_dir_mb() -> float:
    """Return approximate size of outputs/ directory in MB."""
    try:
        total = sum(f.stat().st_size for f in OUTPUTS_DIR.rglob("*") if f.is_file())
        return round(total / (1024 * 1024), 2)
    except Exception:
        return 0.0


# ── Lifespan ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "IndicVoice AI API starting  version=4.0.0  openvoice_enabled=%s",
        OPENVOICE_ENABLED,
    )
    # FIX-2a: start background TTL cleanup loop
    cleanup_task = asyncio.create_task(_cleanup_expired_jobs())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("IndicVoice AI API shutting down")


# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="IndicVoice AI API",
    description=(
        "Backend API for IndicVoice AI — Phase 4B voice cloning MVP.\n\n"
        "**POST /generate** accepts a voice sample + text + language + mode and "
        "returns a `job_id` immediately (HTTP 202). "
        "Poll **GET /job/{job_id}** for status and the output URL."
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
    mode:     str,
) -> None:
    """
    Background task: runs TTS/voice-cloning inference and writes output.
    State: QUEUED → PROCESSING → COMPLETED / FAILED
    """
    logger.info("Worker START  job_id=%s  provider=%s  mode=%s", job_id, provider.name, mode)

    try:
        await job_manager.update_status(job_id, JobStatus.PROCESSING)
    except KeyError:
        logger.error("Worker cannot find job_id=%s (already evicted?)", job_id)
        return

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

    cloning_applied: bool = getattr(result, "cloning_applied", False)

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

    job = await job_manager.get_job(job_id)
    if job and job.meta:
        job.meta.cloning_applied = cloning_applied

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
    """
    Returns service status, TTS engine, cloning availability,
    and supported languages.

    FIX-2b: also returns active_jobs, total_jobs, outputs_dir_mb
    for operator observability.
    """
    all_jobs    = await job_manager.list_jobs()
    active_jobs = sum(
        1 for j in all_jobs
        if j.status in (JobStatus.QUEUED, JobStatus.PROCESSING)
    )
    return {
        "status":            "ok",
        "service":           "IndicVoice AI API",
        "version":           "4.0.0",
        "phase":             "4B — Voice Cloning MVP",
        "tts_engine":        _edge_tts.name,
        "cloning_available": OPENVOICE_ENABLED,
        "supported_modes":   sorted(SUPPORTED_MODES),
        "supported_languages": {
            code: {"name": cfg["name"], "voice": cfg["voice"]}
            for code, cfg in SUPPORTED_LANGUAGES.items()
        },
        "max_upload_mb":    MAX_UPLOAD_BYTES // (1024 * 1024),
        "accepted_formats": sorted(ACCEPTED_EXTENSIONS),
        "output_format":    "WAV (PCM 16-bit, 22050 Hz, mono)",
        "job_system":       "LocalJobManager (in-memory)",
        "storage_backend":  "LocalStorageBackend (filesystem)",
        # FIX-2b: operator observability
        "active_jobs":      active_jobs,
        "total_jobs":       len(all_jobs),
        "outputs_dir_mb":   _outputs_dir_mb(),
        "job_ttl_hours":    round(JOB_TTL_SECONDS / 3600, 1),
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
    Asynchronous TTS / voice-cloning job submission.
    Returns HTTP 202 immediately; poll /job/{job_id} for status.
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
    if mode == "clone":
        provider: TTSProvider = make_openvoice_provider(
            reference_audio=content,
            enabled=OPENVOICE_ENABLED,
        )
    else:
        provider = _edge_tts

    # ── Create job ─────────────────────────────────────────────────────────
    cfg  = SUPPORTED_LANGUAGES[language]
    meta = JobMeta(
        language=language,
        text=text,
        voice=cfg["voice"],
        upload_key=upload_key,
        sample_size_b=len(content),
        mode=mode,
        cloning_applied=False,
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
        mode=mode,
    )

    logger.info(
        "Job enqueued  job_id=%s  lang=%s  mode=%s  text_len=%d",
        job.id, language, mode, len(text),
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id":          job.id,
            "status":          job.status.value,
            "mode":            mode,
            "cloning_enabled": OPENVOICE_ENABLED and mode == "clone",
            "poll_url":        f"/job/{job.id}",
            "message":         "Job accepted. Poll poll_url for status.",
        },
        headers={
            "Location":    f"/job/{job.id}",
            "X-Job-Id":    job.id,
            "X-Job-Mode":  mode,
            "Retry-After": "2",
        },
    )


@app.get("/job/{job_id}", tags=["Voice"], summary="Get job status")
async def get_job(job_id: str):
    """
    Return the current status of a TTS / voice-cloning job.
    `cloning_applied` is true only when OpenVoice v2 actually ran.
    """
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found. It may have expired (TTL {JOB_TTL_SECONDS/3600:.0f}h) or never existed.",
        )
    return job.to_dict()


@app.delete("/job/{job_id}", tags=["Voice"], summary="Delete a job and its output")
async def delete_job(job_id: str):
    """Remove a job record and its associated storage objects."""
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job.output_key:
        await storage.delete(job.output_key)
    if job.meta and job.meta.upload_key:
        await storage.delete(job.meta.upload_key)

    await job_manager.delete_job(job_id)
    logger.info("Job deleted  job_id=%s", job_id)
    return {"deleted": True, "job_id": job_id}


@app.get("/jobs", tags=["Voice"], summary="List recent jobs (dev/debug)")
async def list_jobs():
    """Return all in-memory jobs. Auth-gate before exposing publicly."""
    jobs = await job_manager.list_jobs()
    return {"count": len(jobs), "jobs": [j.to_dict() for j in jobs]}
