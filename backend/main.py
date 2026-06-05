"""
backend/main.py  — Phase 7: Real Reusable Voice Cloning
────────────────────────────────────────────────────────
IndicVoice AI API: TTS + voice cloning + voice profile library.

What's new in Phase 7
─────────────────────
CLONE-1  Embedding extraction at voice-create time
         POST /voices now triggers a background task that:
           • calls extract_and_store_embedding() from openvoice_provider
           • saves the .npy to embeddings/voice_{id}.npy
           • updates VoiceProfile: cloning_ready=True / embedding_key / embedding_error

CLONE-2  Stored embedding used at generate time
         GET /voices/{id} → load embedding_key → pass Path to OpenVoiceProvider
         cloning_applied=True / fallback_used=False only if real cloning ran

CLONE-3  Full API exposure
         POST /generate response: cloning_available, provider_name
         GET  /job/{id}  response: cloning_applied, fallback_used, cloning_error
         GET  /voices    response: cloning_ready, embedding_key, embedding_error
         GET  /health    response: cloning_available

CLONE-4  No fake results
         cloning_applied=True ONLY when ToneColorConverter.convert() succeeded.
         cloning_error contains exact exception text on failure.

All Phase 6 changes retained
────────────────────────────
VOICE-1  Voice Profile CRUD  (POST/GET/DELETE /voices)
VOICE-2  Engine abstraction  (make_provider)
UI-1     Text limit 2000 chars
UI-2/3   fallback_used + provider_name in job response
FIX-2a   Background TTL cleanup
FIX-2b   /health extended metrics
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
    OpenVoiceSynthesisResult,
    SynthesisRequest,
    TTSNoAudioError,
    TTSProvider,
    TTSProviderError,
    TTSUnavailableError,
    extract_and_store_embedding,
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
BASE_DIR       = Path(__file__).parent
STORAGE_DIR    = BASE_DIR
UPLOADS_DIR    = BASE_DIR / "uploads"
OUTPUTS_DIR    = BASE_DIR / "outputs"
EMBEDDINGS_DIR = BASE_DIR / "embeddings"   # CLONE-1: .npy speaker embeddings
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)
EMBEDDINGS_DIR.mkdir(exist_ok=True)

# ── Feature flags ─────────────────────────────────────────────────────────
OPENVOICE_ENABLED: bool = os.getenv("OPENVOICE_ENABLED", "false").lower() == "true"

# ── TTL cleanup constants ──────────────────────────────────────────────────
JOB_TTL_SECONDS:    float = float(os.getenv("JOB_TTL_SECONDS",    str(8 * 3600)))
CLEANUP_INTERVAL_S: float = float(os.getenv("CLEANUP_INTERVAL_S", str(30 * 60)))

# ── Singletons ─────────────────────────────────────────────────────────────
job_manager  = LocalJobManager()
storage      = LocalStorageBackend(base_dir=STORAGE_DIR, serve_prefix="/files")
voice_store  = VoiceStore(base_dir=BASE_DIR)
_edge_tts    = EdgeTTSProvider()


# ── CLONE-1: Background embedding extraction ───────────────────────────────
async def _extract_embedding_background(
    voice_id:    str,
    audio_bytes: bytes,
) -> None:
    """
    Background task: extract speaker embedding and update VoiceProfile.
    Runs after POST /voices returns to avoid blocking the HTTP response.
    Sets cloning_ready=True and embedding_key on success;
    sets embedding_error with exact exception text on failure.
    """
    logger.info("Embedding extraction START  voice_id=%s", voice_id)
    dest_npy = EMBEDDINGS_DIR / f"voice_{voice_id}.npy"
    embedding_key = f"embeddings/voice_{voice_id}.npy"

    if not OPENVOICE_ENABLED:
        # Record that we cannot extract — not an error, just disabled
        await voice_store.update_voice(
            voice_id=voice_id,
            cloning_ready=False,
            embedding_error=(
                "OpenVoice is disabled (OPENVOICE_ENABLED=false). "
                "Set OPENVOICE_ENABLED=true and install requirements-clone.txt, "
                "then re-upload this voice to extract the embedding."
            ),
        )
        logger.info(
            "Embedding extraction SKIPPED (OpenVoice disabled)  voice_id=%s", voice_id
        )
        return

    ok, err = await extract_and_store_embedding(
        audio_bytes=audio_bytes,
        dest_npy_path=dest_npy,
    )

    if ok:
        await voice_store.update_voice(
            voice_id=voice_id,
            cloning_ready=True,
            embedding_key=embedding_key,
            embedding_error=None,
        )
        logger.info(
            "Embedding extraction DONE  voice_id=%s  path=%s", voice_id, dest_npy
        )
    else:
        await voice_store.update_voice(
            voice_id=voice_id,
            cloning_ready=False,
            embedding_error=err,
        )
        logger.warning(
            "Embedding extraction FAILED  voice_id=%s  error=%s", voice_id, err
        )


# ── VOICE-2: Engine abstraction ────────────────────────────────────────────
def make_provider(
    mode:                  str,
    reference_audio:       Optional[bytes] = None,
    stored_embedding_path: Optional[Path]  = None,
) -> TTSProvider:
    """
    Single dispatch point for provider selection.

    mode='standard'  → EdgeTTSProvider (always available)
    mode='clone'     → OpenVoiceProvider if OPENVOICE_ENABLED, else EdgeTTS fallback

    CLONE-2: stored_embedding_path is passed through so OpenVoiceProvider
    can load the pre-extracted .npy instead of re-extracting every time.
    """
    if mode == "clone":
        return make_openvoice_provider(
            reference_audio=reference_audio,
            stored_embedding_path=stored_embedding_path,
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
        "IndicVoice AI API starting  version=7.0.0  openvoice_enabled=%s",
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
        "Backend API for IndicVoice AI — Phase 7 Real Voice Cloning.\n\n"
        "**POST /voices** — save a named voice profile; triggers background embedding extraction.\n"
        "**GET  /voices** — list saved profiles with cloning_ready status.\n"
        "**POST /generate** — run TTS / real voice-cloning job (async).\n"
        "Poll **GET /job/{job_id}** for status, cloning_applied, fallback_used, cloning_error."
    ),
    version="7.0.0",
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
SUPPORTED_LANGUAGES: dict = {
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
    background_tasks: BackgroundTasks,
    audio:       UploadFile = File(...,  description="Reference audio — WAV/MP3/OGG/WEBM/FLAC, max 50 MB"),
    name:        str        = Form(...,  description="Display name for this voice"),
    language:    str        = Form("en", description="Language code: te / ta / hi / en"),
    description: str        = Form("",   description="Optional free-text notes"),
    tags:        str        = Form("",   description="Comma-separated tags (optional)"),
):
    """
    Upload a voice sample and save it as a named profile.
    Returns the created VoiceProfile (cloning_ready=false initially).

    CLONE-1: Immediately after saving, triggers a background task that:
      1. Extracts the speaker embedding using OpenVoice se_extractor.
      2. Stores it as embeddings/voice_{id}.npy.
      3. Updates the profile: cloning_ready=true (or embedding_error on failure).

    Poll GET /voices/{id} to check cloning_ready status.
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

    # Create profile record (cloning_ready=False initially)
    profile = await voice_store.create_voice(
        name=name,
        language=language,
        sample_key=sample_key,
        sample_size=len(content),
        description=description.strip(),
        tags=tag_list,
    )

    logger.info(
        "Voice profile created  id=%s  name=%r  lang=%s  size=%d  cloning_enabled=%s",
        profile.id, name, language, len(content), OPENVOICE_ENABLED,
    )

    # CLONE-1: trigger background embedding extraction
    background_tasks.add_task(
        _extract_embedding_background,
        voice_id=profile.id,
        audio_bytes=content,
    )

    # Return profile with cloning_available so frontend knows to poll
    result = profile.to_dict()
    result["cloning_available"] = OPENVOICE_ENABLED
    result["message"] = (
        "Voice profile created. Speaker embedding extraction started in background. "
        "Poll GET /voices/{id} for cloning_ready status."
        if OPENVOICE_ENABLED
        else
        "Voice profile created. OpenVoice is disabled — cloning_ready will remain false."
    )
    return result


@app.get(
    "/voices",
    tags=["Voices"],
    summary="List all saved voice profiles",
)
async def list_voices():
    """Return all saved voice profiles, newest-first, with cloning_ready status."""
    profiles = await voice_store.list_voices()
    return {
        "count": len(profiles),
        "cloning_available": OPENVOICE_ENABLED,
        "voices": [p.to_dict() for p in profiles],
    }


@app.get(
    "/voices/{voice_id}",
    tags=["Voices"],
    summary="Get a single voice profile",
)
async def get_voice(voice_id: str):
    """Return one voice profile by id, including cloning_ready / embedding_error."""
    profile = await voice_store.get_voice(voice_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Voice profile '{voice_id}' not found.")
    result = profile.to_dict()
    result["cloning_available"] = OPENVOICE_ENABLED
    return result


@app.delete(
    "/voices/{voice_id}",
    tags=["Voices"],
    summary="Delete a voice profile and its sample",
)
async def delete_voice(voice_id: str):
    """Delete a voice profile, its audio sample, and its stored embedding."""
    profile = await voice_store.get_voice(voice_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Voice profile '{voice_id}' not found.")

    # Remove sample file
    try:
        await storage.delete(profile.sample_key)
    except Exception as exc:
        logger.warning("Could not delete sample for voice %s: %s", voice_id, exc)

    # Remove embedding .npy
    if profile.embedding_key:
        emb_path = BASE_DIR / profile.embedding_key
        try:
            emb_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Could not delete embedding for voice %s: %s", voice_id, exc)

    deleted = await voice_store.delete_voice(voice_id)
    logger.info("Voice profile deleted  id=%s", voice_id)
    return {"deleted": deleted, "voice_id": voice_id}


# ═══════════════════════════════════════════════════════════════════════════
# TTS / Generation endpoints
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

    # CLONE-2: read cloning flags from OpenVoiceSynthesisResult
    cloning_applied: bool         = getattr(result, "cloning_applied", False)
    fallback_used:   bool         = getattr(result, "fallback_used",   mode == "clone" and not cloning_applied)
    cloning_error:   Optional[str] = getattr(result, "cloning_error",   None)
    provider_name:   str          = getattr(result, "provider_name",   provider.name)

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

    # Persist flags to job meta
    job = await job_manager.get_job(job_id)
    if job and job.meta:
        job.meta.cloning_applied = cloning_applied
        job.meta.fallback_used   = fallback_used
        job.meta.provider_name   = provider_name
        job.meta.cloning_error   = cloning_error

    await job_manager.update_status(
        job_id, JobStatus.COMPLETED,
        output_key=output_key,
        output_url=output_url,
    )
    logger.info(
        "Worker DONE  job_id=%s  provider=%s  cloning_applied=%s  "
        "fallback_used=%s  cloning_error=%s  wav=%d bytes",
        job_id, provider_name, cloning_applied, fallback_used,
        cloning_error, len(result.wav_bytes),
    )


# ── System ─────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"], include_in_schema=False)
async def root():
    return {
        "service":           "IndicVoice AI API",
        "version":           "7.0.0",
        "phase":             "7 — Real Voice Cloning",
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
    ready_voices = sum(1 for v in all_voices if v.cloning_ready)
    return {
        "status":              "ok",
        "service":             "IndicVoice AI API",
        "version":             "7.0.0",
        "phase":               "7 — Real Voice Cloning",
        "tts_engine":          _edge_tts.name,
        "cloning_available":   OPENVOICE_ENABLED,
        "supported_modes":     sorted(SUPPORTED_MODES),
        "supported_languages": {
            code: {"name": cfg["name"], "voice": cfg["voice"]}
            for code, cfg in SUPPORTED_LANGUAGES.items()
        },
        "max_text_length":     MAX_TEXT_LENGTH,
        "max_upload_mb":       MAX_UPLOAD_BYTES // (1024 * 1024),
        "accepted_formats":    sorted(ACCEPTED_EXTENSIONS),
        "output_format":       "WAV (PCM 16-bit, 22050 Hz, mono)",
        "voice_profiles":      len(all_voices),
        "voices_clone_ready": ready_voices,
        "active_jobs":         active_jobs,
        "total_jobs":          len(all_jobs),
        "outputs_dir_mb":      _outputs_dir_mb(),
        "job_ttl_hours":       round(JOB_TTL_SECONDS / 3600, 1),
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

    CLONE-2: When voice_id is given and mode=clone:
      - Loads the VoiceProfile and checks cloning_ready.
      - If cloning_ready=True: loads stored embedding_key (.npy) and passes it
        to OpenVoiceProvider → real cloning runs → cloning_applied=True.
      - If cloning_ready=False: still runs but cloning falls back to EdgeTTS
        with exact reason (embedding not yet extracted / extraction failed).

    Returns HTTP 202 immediately; poll /job/{job_id} for completion.
    Response includes: cloning_available, cloning_ready, provider_name.
    Job result includes: cloning_applied, fallback_used, cloning_error.
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

    # Resolve audio bytes + stored embedding — from saved profile OR direct upload
    content:               Optional[bytes] = None
    upload_key:            str             = ""
    stored_embedding_path: Optional[Path]  = None  # CLONE-2: pre-extracted .npy
    profile_cloning_ready: bool            = False

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
        upload_key  = profile.sample_key
        language    = profile.language   # honour profile language

        # CLONE-2: resolve stored embedding path
        if profile.embedding_key:
            stored_embedding_path = BASE_DIR / profile.embedding_key
            if not stored_embedding_path.exists():
                logger.warning(
                    "embedding_key set but file missing: %s", stored_embedding_path
                )
                stored_embedding_path = None
        profile_cloning_ready = profile.cloning_ready

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
        # No pre-extracted embedding for direct uploads — live extraction will run
        profile_cloning_ready = False
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'voice_id' (saved profile) or 'audio' (direct upload).",
        )

    # CLONE-2: Select provider with stored embedding path
    provider = make_provider(
        mode=mode,
        reference_audio=content,
        stored_embedding_path=stored_embedding_path,
    )

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
        "Job enqueued  job_id=%s  lang=%s  mode=%s  voice_id=%s  "
        "cloning_ready=%s  embedding=%s  text_len=%d",
        job.id, language, mode, voice_id or "(upload)",
        profile_cloning_ready,
        stored_embedding_path is not None,
        len(text),
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id":           job.id,
            "status":           job.status.value,
            "mode":             mode,
            "cloning_available": OPENVOICE_ENABLED and mode == "clone",
            "cloning_ready":    profile_cloning_ready and mode == "clone",
            "provider_name":    provider.name,
            "poll_url":         f"/job/{job.id}",
            "message":          "Job accepted. Poll poll_url for status.",
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
