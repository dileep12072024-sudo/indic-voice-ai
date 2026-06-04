# Standard TTS Production Readiness Audit

> **Method**: complete static code inspection of all backend and frontend
> source files across the Standard TTS pipeline.
> No application execution was performed. All findings are code-level facts.
> Last updated: 2026-06-04

---

## Readiness Score: **6.5 / 10**

| Dimension | Before audit | After this commit | Notes |
|-----------|-------------|-------------------|-------|
| Correctness (event loop) | 5/10 | **9/10** | miniaudio now off event loop |
| Reliability | 6/10 | **7/10** | Timeout guard added; no retry yet |
| Frontend UX | 6/10 | **7/10** | Client-side size check added |
| Memory safety | 5/10 | **7/10** | Blob URL leak fixed; TTL cleanup added |
| Scalability (concurrency) | 4/10 | 4/10 | BackgroundTasks still not a real queue |
| Observability | 4/10 | **7/10** | /health now reports job counts + disk |
| Solo / dev use | 9/10 | 9/10 | Excellent for single-user local use |
| Multi-user production | 3/10 | 4/10 | Still needs a real queue + persistent store |

**Summary**: Standard TTS is production-ready for single-user / small-team
local use after the fixes in this commit. It is **not** ready for multi-user
concurrent public traffic without a real job queue and persistent job store.

---

## Architecture Summary

```
Browser (React/Vite :5173)
       │
       │  POST /generate  multipart(audio, text, language, mode)
       ▼
  Vite dev proxy  ──────────────▶  FastAPI :8000
                                       │
                                  Validate inputs
                                  storage.put(upload_key)
                                  job_manager.create_job()
                                  background_tasks.add_task()
                                  return 202 {job_id, poll_url}
                                       │
                                       ▼
                              BackgroundTask: _run_tts_job
                                       │
                              EdgeTTSProvider.synthesise()
                                       │
                              ┌────────┴──────────────┐
                              │                       │
                     edge-tts stream           asyncio.timeout(30s)
                     (Microsoft Neural)         [FIX-1b: stall guard]
                              │
                     miniaudio.decode()   ← run_in_executor [FIX-1a]
                              │
                     _pcm_to_wav() → WAV bytes
                              │
                     storage.put(output_key)
                     job_manager.update_status(COMPLETED)
                              │
       ┌──────────────────────┘
       │  GET /job/{id}  (every 2s, up to 120s)
       ▼
  {status: completed, output_url: /files/outputs/...}
       │
       │  GET /files/outputs/<job_id>_output.wav
       ▼
  URL.createObjectURL(blob)  →  AudioPlayer
       │
  [FIX-4: previous blob URL revoked]
```

---

## Request Flow (step by step)

| Step | Component | Action | Notes |
|------|-----------|--------|-------|
| 1 | Browser | User selects file, enters text, picks language | FIX-3: client-side 50MB guard |
| 2 | Browser | `POST /generate` multipart | Vite proxy forwards to :8000 |
| 3 | FastAPI | Validate language, mode, text (≤500), extension, size (≤50MB) | Returns 422/413 on error |
| 4 | FastAPI | `await audio.read()` → full upload in RAM | ⚠ ISSUE-B2: no streaming |
| 5 | FastAPI | `storage.put(upload_key, content)` → writes to `uploads/` | Sync via aiofiles |
| 6 | FastAPI | `job_manager.create_job(meta)` → QUEUED | In-memory dict |
| 7 | FastAPI | `background_tasks.add_task(_run_tts_job)` | BackgroundTasks (not a real queue) |
| 8 | FastAPI | Return 202 `{job_id, poll_url}` | Client begins polling |
| 9 | Worker | `update_status(PROCESSING)` | |
| 10 | Worker | `edge_tts.Communicate(text, voice).stream()` | Online call to Microsoft |
| 11 | Worker | `asyncio.timeout(30s)` guard | FIX-1b |
| 12 | Worker | `miniaudio.decode()` in executor | FIX-1a |
| 13 | Worker | `_pcm_to_wav()` → 16-bit PCM WAV at 22050 Hz | |
| 14 | Worker | `storage.put(output_key, wav_bytes)` → `outputs/` | |
| 15 | Worker | `update_status(COMPLETED, output_url)` | |
| 16 | Browser | Poll returns `{status: completed, output_url}` | Every 2s up to 120s |
| 17 | Browser | `fetch(output_url)` → blob | |
| 18 | Browser | `URL.createObjectURL(blob)` → AudioPlayer | FIX-4: previous URL revoked |
| 19 | Backend | `_cleanup_expired_jobs()` every 30 min | FIX-2a: TTL eviction |

---

## Strengths

- **Clean provider abstraction**: `TTSProvider` ABC makes swapping
  EdgeTTS for Azure/Piper/Workers AI a one-file change.
- **Typed error hierarchy**: `TTSUnavailableError`, `TTSNoAudioError`,
  `TTSProviderError` give the worker precise failure categorisation.
- **Async-first**: FastAPI + BackgroundTasks keeps the HTTP layer
  non-blocking for I/O.
- **Solid input validation**: language, mode, text length, extension,
  empty-file, and 50 MB cap all enforced at the API layer.
- **Graceful clone fallback**: clone mode never crashes; it always returns
  an EdgeTTS result if OpenVoice is unavailable.
- **Self-contained local setup**: 6 Python packages, one `pip install`,
  automatic directory creation.
- **Explicit error messages**: every failure path returns a human-readable
  string, not a raw traceback.

---

## Issues Found (by code inspection)

### 🔴 Critical

| ID | Location | Issue | Fixed in this commit? |
|----|----------|-------|-----------------------|
| B1 | `main.py` | `BackgroundTasks` is not a real queue. No concurrency limit, no queue depth, no back-pressure. Under concurrent load all jobs compete for the same thread pool. | ❌ Documented only |
| B2 | `main.py` | `await audio.read()` reads entire upload into RAM (up to 50 MB per concurrent request). No streaming write to disk. | ❌ Documented only |
| E2 | `edge_tts_provider.py` | `miniaudio.decode()` was synchronous in an async method — blocked the event loop for 50–200 ms per request. | ✅ **FIX-1a: run_in_executor** |

### 🟠 High

| ID | Location | Issue | Fixed? |
|----|----------|-------|--------|
| B4/B7 | `main.py` | No TTL eviction — jobs and files accumulate indefinitely. RAM and disk grow without bound on long-running instances. | ✅ **FIX-2a: cleanup task** |
| B8 | `edge_tts_provider.py` | No timeout on EdgeTTS stream. A stalled Microsoft connection could hold a thread for up to 300 s (aiohttp default). | ✅ **FIX-1b: asyncio.timeout(30)** |
| E1 | `edge_tts_provider.py` | `aiohttp.ClientTimeout` not explicitly caught — fell into generic `TTSProviderError` instead of `TTSUnavailableError`. | ✅ **FIX-1c: explicit catch** |
| F2 | `VoiceCloner.jsx` | No client-side file size check. User waited 30 s+ for upload to complete before getting a server 413. | ✅ **FIX-3: instant client check** |
| F6 | `VoiceCloner.jsx` | `URL.createObjectURL()` blob never revoked on re-generation or component unmount → memory leak accumulates per use. | ✅ **FIX-4: revokeObjectURL** |

### 🟡 Medium

| ID | Location | Issue | Fixed? |
|----|----------|-------|--------|
| B3 | `main.py` | `LocalJobManager` is in-memory only. Server restart loses all job state; polling clients get 404 with no explanation. | ❌ Documented |
| B5 | `main.py` | No rate limiting on EdgeTTS upstream calls. Concurrent requests multiply Microsoft API connections. | ❌ Documented |
| B6 | `edge_tts_provider.py` | No retry logic for transient EdgeTTS failures. One network hiccup permanently fails the job. | ❌ Documented |
| B10 | `main.py` | `/health` had no job count or disk usage — no operator observability. | ✅ **FIX-2b: active_jobs + outputs_dir_mb** |
| M2 | `jobs/manager.py` | TTL mentioned in docstring but no eviction loop. | ✅ **FIX-2a: cleanup task** |
| F8 | `AudioPlayer.jsx` | No cleanup of `<audio>` element on component unmount. | ❌ Documented |

### 🟢 Low

| ID | Location | Issue | Fixed? |
|----|----------|-------|--------|
| B9 | `main.py` | If server shuts down mid-job, job stays `QUEUED` forever after restart (but in-memory store doesn't survive restart anyway). | ❌ Documented |
| F4 | `VoiceCloner.jsx` | Poll timeout hardcoded at 120 s with no user-visible countdown. | ❌ Documented |
| F5 | `VoiceCloner.jsx` | No retry button — user must reload page after error. | ❌ Documented |
| F7 | `VoiceCloner.jsx` | No cancel button during active job. | ❌ Documented |
| F9 | `AudioPlayer.jsx` | Volume/seek state resets on each new generation. | ❌ Documented |

---

## Bottlenecks

### 1. BackgroundTasks concurrency (CRITICAL for multi-user)
FastAPI `BackgroundTasks` runs jobs in the same process thread pool as all
other async I/O. Under load:
- Multiple EdgeTTS HTTP streams compete for aiohttp connection slots.
- Multiple `miniaudio.decode()` calls compete for CPU threads.
- Multiple file writes compete for disk I/O.

No back-pressure mechanism exists. The 100th concurrent job will block or fail
silently without any queue-depth signal to the user.

**Mitigation** (not in scope for this commit): replace `BackgroundTasks` with
`asyncio.Queue` + a bounded worker pool, or an external queue (Celery/ARQ/RQ).

### 2. Upload reads entirely into RAM
`await audio.read()` materialises the full upload in memory before writing
to disk. At 50 MB max × N concurrent requests = O(N × 50 MB) peak RAM
with no streaming safety valve.

**Mitigation**: stream-write the upload to disk with `aiofiles` in chunks.

### 3. Microsoft EdgeTTS is a third-party dependency
All Standard TTS depends on `speech.platform.bing.com` being reachable.
No offline fallback exists. Network latency directly affects job latency.

---

## Recommended Improvements

In priority order for production readiness:

| Priority | Improvement | Effort | Impact |
|----------|-------------|--------|--------|
| P1 | Replace `BackgroundTasks` with `asyncio.Queue` + bounded worker pool | Medium | Critical for concurrency |
| P2 | Stream upload to disk instead of `await audio.read()` | Small | Eliminates RAM spike |
| P3 | Add 1-retry with 2s backoff for EdgeTTS `TTSUnavailableError` | Small | Resilience |
| P4 | Persist job store to SQLite/Redis (survive restarts) | Medium | Reliability |
| P5 | Add `AudioPlayer.jsx` cleanup (`useEffect` return) | Tiny | Memory |
| P6 | Add poll countdown timer to UI | Tiny | UX |
| P7 | Add retry button to error state | Tiny | UX |
| P8 | Add upload progress indicator | Small | UX |

---

## What Was Fixed in This Commit

| Fix | File | Change |
|-----|------|--------|
| FIX-1a | `edge_tts_provider.py` | `miniaudio.decode()` → `run_in_executor` (unblocks event loop) |
| FIX-1b | `edge_tts_provider.py` | `asyncio.timeout(30)` around EdgeTTS stream |
| FIX-1c | `edge_tts_provider.py` | `aiohttp.ClientTimeout` caught → `TTSUnavailableError` |
| FIX-2a | `main.py` | TTL cleanup coroutine runs every 30 min; sweeps expired jobs+files |
| FIX-2b | `main.py` | `/health` adds `active_jobs`, `total_jobs`, `outputs_dir_mb` |
| FIX-3  | `VoiceCloner.jsx` | Client-side 50 MB guard before upload |
| FIX-4  | `VoiceCloner.jsx` | Blob URL revoked on re-generation and unmount |

---

## What Still Requires Real Local Testing

- [ ] End-to-end Standard TTS for te / ta / hi / en (internet required)
- [ ] Confirm 30s timeout fires correctly on a stalled network
- [ ] Confirm TTL cleanup evicts correct jobs after 8h
- [ ] Confirm `/health` returns non-zero `active_jobs` during inference
- [ ] Client-side 50MB guard shown before upload attempt
- [ ] Blob URL revocation confirmed via browser memory profiler
- [ ] Concurrent request behaviour under load
