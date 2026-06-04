"""
backend/main.py  — Phase 6
────────────────────────────────────────────────
IndicVoice AI API: TTS + voice cloning + voice profile library.

What's new in Phase 6
─────────────────────
VOICE-1  Voice Profile CRUD
         POST   /voices          — upload sample, name it, save profile
         GET    /voices          — list all saved profiles
         GET    /voices/{id}     — fetch one profile
         DELETE /voices/{id}     — delete profile + sample file

VOICE-2  Engine abstraction
         EngineAdapter protocol decouples provider selection from
         route logic.  OpenVoice and EdgeTTS both implement it.
         make_provider() is the single dispatch point.

All Phase 5 changes retained
────────────────────────────
UI-1  Text limit 2000 chars
UI-2  /generate → fallback_used + provider_name
UI-3  /job/{id} → same fields via Job.to_dict()
FIX-2a  Background TTL cleanup
FIX-2b  /health extended metrics
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
from voices import VoiceProfile, VoiceStore

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

# ── Feature flags ─────────────────────────────────────────────────────────
OPENVOICE_ENABLED: bool = os.getenv("OPENVOICE_ENABLED", "false").lower() == "true"

# ── TTL cleanup constants ──────────────────────────────────────────────────
JOB_TTL_SECONDS:    float = float(os.getenv("JOB_TTL_SECONDS",    str(8 * 3600)))
CLEANUP_INTERVAL_S: float = float(os.getenv("CLEANUP_INTERVAL_S", str(30 * 60)))

# ── Singletons ─────────────────────────────────────────────────────────────
job_manager  = LocalJobManager()
storage      = LocalStorageBackend(base_dir=STORAGE_DIR, serve_prefix="/files")
voice_store  = VoiceStore(base_dir=BASE_DIR)          # VOICE-1
_edge_tts    = EdgeTTSProvider()


# ── VOICE-2: Engine abstraction ────────────────────────────────────────────
def make_provider(
    mode:             str,
    reference_audio:  Optional[bytes] = None,
) -> TTSProvider:
    """
    Single dispatch point for provider selection.

    mode='standard'  → EdgeTTSProvider (always available)
    mode='clone'     → OpenVoiceProvider if OPENVOICE_ENABLED, else EdgeTTS fallback
    """
    if mode == "clone":
        return make_openvoice_provider(
            reference_audio=reference_audio,
            enabled=OPENVOICE_ENABLED,
        )
    return _edge_tts


# ── FIX-2a: background TTL cleanup ────────────────────────────────────────
async def _cleanup_expired_jobs() -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_S)
        try:
            now   = time.time()
            jobs  = await job_manager.list_jobs()
            evicted = 0
            for job in jobs:
                if now - job.created_at >= JOB_TTL_SECONDS:
                    if job.output_key:
                        try:
                            await storage.delete(job.output_key)
                        except Exception:
                            pass
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
    try:
        total = sum(f.stat().st_size for f in OUTPUTS_DIR.rglob("*") if f.is_file())
        return round(total / (1024 * 1024), 2)
    except Exception:
        return 0.0


# ── Lifespan ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "IndicVoice AI API starting  version=6.0.0  openvoice_enabled=%s",
        OPENVOICE_ENABLED,
    )
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
        "Backend API for IndicVoice AI — Phase 6 Voice Library.\n\n"
        "**POST /voices** — save a named voice profile from an audio sample.\n"
        "**GET  /voices** — list saved profiles.\n"
        "**POST /generate** — run TTS / voice-cloning job (async).\n"
        "Poll **GET /job/{job_id}** for status and output URL."
    ),
    version="6.0.0",
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

# ── Static files ───────────────────────────────────────────────────────────
app.mount("/files", StaticFiles(directory=str(STORAGE_DIR)), name="files")

# ── Constants ──────────────────────────────────────────────────────────────
SUPPORTED_LANGUAGES: dict[str, dict] = {
    "te": {"name": "Telugu",  "voice": "te-IN-ShrutiNeural"},
    "ta": {"name": "Tamil",   "voice": "ta-IN-PallaviNeural"},
    "hi": {"name": "Hindi",   "voice": "hi-IN-SwaraNeural"},
    "en": {"name": "English", "voice": "en-US-JennyNeural"},
}
SUPPORTED_MODES      = {"standard", "clone"}
MAX_TEXT_LENGTH      = 2000
MAX_UPLOAD_BYTES     = 50 * 1024 * 1024
ACCEPTED_EXTENSIONS  = {".wav", ".mp3", ".ogg", ".webm", ".flac"}


# ═══════════════════════════════════════════════════════════════════════════
# VOICE-1 — Voice Profile endpoints
# ═══════════════════════════════════════════════════════════════════════════

@app.post(
    "/voices",
    status_code=201,
    tags=["Voices"],
    summary="Save a new voice profile",
)
async def create_voice(
    audio:       UploadFile = File(...,  description="Reference audio — WAV/MP3/OGG/WEBM/FLAC, max 50 MB"),
    name:        str        = Form(...,  description="Display name for this voice"),
    language:    str        = Form("en", description="Language code: te / ta / hi / en"),
    description: str        = Form("",   description="Optional free-text notes"),
    tags:        str        = Form("",   description="Comma-separated tags (optional)"),
):
    """
    Upload a voice sample and save it as a named profile.
    Returns the created VoiceProfile.
    The profile can then be referenced by id when calling /generate.
    """
    # Validate language
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported language '{language}'. Accepted: {list(SUPPORTED_LANGUAGES)}",
        )

    # Validate name
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="'name' must not be empty.")
    if len(name) > 100:
        raise HTTPException(status_code=422, detail="'name' must be ≤ 100 characters.")

    # Validate file
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

    # Persist sample to disk
    sample_id  = uuid.uuid4().hex
    sample_key = f"uploads/voice_{sample_id}{ext}"
    await storage.put(sample_key, content)

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # Create profile record
    profile = await voice_store.create_voice(
        name=name,
        language=language,
        sample_key=sample_key,
        sample_size=len(content),
        description=description.strip(),
        tags=tag_list,
    )

    logger.info(
        "Voice profile created  id=%s  name=%r  lang=%s  size=%d",
        profile.id, name, language, len(content),
    )
    return profile.to_dict()


@app.get(
    "/voices",
    tags=["Voices"],
    summary="List all saved voice profiles",
)
async def list_voices():
    """Return all saved voice profiles, newest-first."""
    profiles = await voice_store.list_voices()
    return {"count": len(profiles), "voices": [p.to_dict() for p in profiles]}


@app.get(
    "/voices/{voice_id}",
    tags=["Voices"],
    summary="Get a single voice profile",
)
async def get_voice(voice_id: str):
    """Return one voice profile by id."""
    profile = await voice_store.get_voice(voice_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Voice profile '{voice_id}' not found.")
    return profile.to_dict()


@app.delete(
    "/voices/{voice_id}",
    tags=["Voices"],
    summary="Delete a voice profile and its sample",
)
async def delete_voice(voice_id: str):
    """Delete a voice profile and remove its stored audio sample."""
    profile = await voice_store.get_voice(voice_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Voice profile '{voice_id}' not found.")

    # Remove sample file
    try:
        await storage.delete(profile.sample_key)
    except Exception as exc:
        logger.warning("Could not delete sample for voice %s: %s", voice_id, exc)

    deleted = await voice_store.delete_voice(voice_id)
    logger.info("Voice profile deleted  id=%s", voice_id)
    return {"deleted": deleted, "voice_id": voice_id}


# ═══════════════════════════════════════════════════════════════════════════
# TTS / Generation endpoints (Phase 5 + VOICE-2 engine abstraction)
# ═══════════════════════════════════════════════════════════════════════════

async def _run_tts_job(
    job_id:   str,
    text:     str,
    language: str,
    voice:    str,
    provider: TTSProvider,
    mode:     str,
) -> None:
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
        await job_manager.update_status(
            job_id, JobStatus.FAILED,
            error_message=f"TTS service unavailable: {exc}",
        )
        return
    except TTSNoAudioError as exc:
        await job_manager.update_status(
            job_id, JobStatus.FAILED,
            error_message=f"TTS returned no audio: {exc}",
        )
        return
    except TTSProviderError as exc:
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
    fallback_used:   bool = getattr(result, "fallback_used",   mode == "clone" and not cloning_applied)
    provider_name:   str  = getattr(result, "provider_name",   provider.name)

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
        job.meta.fallback_used   = fallback_used
        job.meta.provider_name   = provider_name

    await job_manager.update_status(
        job_id, JobStatus.COMPLETED,
        output_key=output_key,
        output_url=output_url,
    )
    logger.info(
        "Worker DONE  job_id=%s  provider=%s  cloning_applied=%s  fallback_used=%s  wav=%d bytes",
        job_id, provider_name, cloning_applied, fallback_used, len(result.wav_bytes),
    )


# ── System ─────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"], include_in_schema=False)
async def root():
    return {
        "service":           "IndicVoice AI API",
        "version":           "6.0.0",
        "phase":             "6 — Voice Library",
        "openvoice_enabled": OPENVOICE_ENABLED,
        "docs":              "/docs",
        "health":            "/health",
    }


@app.get("/health", tags=["System"], summary="Health check")
async def health():
    all_jobs    = await job_manager.list_jobs()
    all_voices  = await voice_store.list_voices()
    active_jobs = sum(
        1 for j in all_jobs
        if j.status in (JobStatus.QUEUED, JobStatus.PROCESSING)
    )
    return {
        "status":             "ok",
        "service":            "IndicVoice AI API",
        "version":            "6.0.0",
        "phase":              "6 — Voice Library",
        "tts_engine":         _edge_tts.name,
        "cloning_available":  OPENVOICE_ENABLED,
        "supported_modes":    sorted(SUPPORTED_MODES),
        "supported_languages": {
            code: {"name": cfg["name"], "voice": cfg["voice"]}
            for code, cfg in SUPPORTED_LANGUAGES.items()
        },
        "max_text_length":    MAX_TEXT_LENGTH,
        "max_upload_mb":      MAX_UPLOAD_BYTES // (1024 * 1024),
        "accepted_formats":   sorted(ACCEPTED_EXTENSIONS),
        "output_format":      "WAV (PCM 16-bit, 22050 Hz, mono)",
        "voice_profiles":     len(all_voices),
        "active_jobs":        active_jobs,
        "total_jobs":         len(all_jobs),
        "outputs_dir_mb":     _outputs_dir_mb(),
        "job_ttl_hours":      round(JOB_TTL_SECONDS / 3600, 1),
    }


@app.post(
    "/generate",
    status_code=202,
    tags=["Generate"],
    summary="Submit a TTS / voice-cloning job (async)",
)
async def generate(
    background_tasks: BackgroundTasks,
    audio:     UploadFile       = File(None,       description="Voice sample — required unless voice_id is given"),
    voice_id:  Optional[str]    = Form(None,       description="Saved voice profile id (alternative to uploading audio)"),
    text:      str              = Form(...,        description="Text to synthesise, 1–2000 characters"),
    language:  str              = Form("en",       description="Language code: te / ta / hi / en"),
    mode:      str              = Form("standard", description="Mode: 'standard' | 'clone'"),
):
    """
    Async TTS / voice-cloning job submission.

    Accepts either:
    - a direct audio upload (``audio`` field), or
    - a saved voice profile id (``voice_id`` field).

    Returns HTTP 202 immediately; poll /job/{job_id} for completion.
    """
    # Validate language
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported language '{language}'. Accepted: {list(SUPPORTED_LANGUAGES)}",
        )

    # Validate mode
    mode = mode.strip().lower()
    if mode not in SUPPORTED_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported mode '{mode}'. Accepted: {sorted(SUPPORTED_MODES)}",
        )

    # Validate text
    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="'text' must not be empty.")
    if len(text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"'text' must be ≤ {MAX_TEXT_LENGTH} characters (got {len(text)}).",
        )

    # Resolve audio bytes — from saved profile OR direct upload
    content:    Optional[bytes] = None
    upload_key: str             = ""

    if voice_id:
        # Load sample from saved voice profile
        profile = await voice_store.get_voice(voice_id)
        if profile is None:
            raise HTTPException(
                status_code=404,
                detail=f"Voice profile '{voice_id}' not found.",
            )
        try:
            content = await storage.get(profile.sample_key)
        except Exception:
            raise HTTPException(
                status_code=500,
                detail=f"Could not read sample for voice profile '{voice_id}'.",
            )
        upload_key = profile.sample_key
        language   = profile.language   # honour profile language
    elif audio is not None:
        # Direct upload path (legacy / standalone use)
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
        job_id_tmp = uuid.uuid4().hex
        upload_key = f"uploads/{job_id_tmp}_sample{ext}"
        await storage.put(upload_key, content)
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'voice_id' (saved profile) or 'audio' (direct upload).",
        )

    # Select provider — VOICE-2 abstraction
    provider = make_provider(mode=mode, reference_audio=content)

    # Create job
    cfg  = SUPPORTED_LANGUAGES[language]
    meta = JobMeta(
        language=language,
        text=text,
        voice=cfg["voice"],
        upload_key=upload_key,
        sample_size_b=len(content) if content else 0,
        mode=mode,
        cloning_applied=False,
        fallback_used=False,
        provider_name=provider.name,
    )
    job = await job_manager.create_job(meta)

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
        "Job enqueued  job_id=%s  lang=%s  mode=%s  voice_id=%s  text_len=%d",
        job.id, language, mode, voice_id or "(upload)", len(text),
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id":          job.id,
            "status":          job.status.value,
            "mode":            mode,
            "cloning_enabled": OPENVOICE_ENABLED and mode == "clone",
            "provider_name":   provider.name,
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


@app.get("/job/{job_id}", tags=["Generate"], summary="Get job status")
async def get_job(job_id: str):
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{job_id}' not found. It may have expired (TTL {JOB_TTL_SECONDS/3600:.0f}h).",
        )
    return job.to_dict()


@app.delete("/job/{job_id}", tags=["Generate"], summary="Delete a job and its output")
async def delete_job(job_id: str):
    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job.output_key:
        await storage.delete(job.output_key)
    if job.meta and job.meta.upload_key:
        await storage.delete(job.meta.upload_key)
    await job_manager.delete_job(job_id)
    return {"deleted": True, "job_id": job_id}


@app.get("/jobs", tags=["Generate"], summary="List recent jobs (dev/debug)")
async def list_jobs():
    jobs = await job_manager.list_jobs()
    return {"count": len(jobs), "jobs": [j.to_dict() for j in jobs]}
