# IndicVoice AI — Repository Audit

> **Method**: complete static code inspection of all backend and frontend source files.
> No application execution was performed. Findings are code-level facts.
> Last updated: 2026-06-04

---

## Health score: **7.5 / 10**

| Area | Score | Reason |
|------|-------|--------|
| Backend architecture | 9/10 | Clean ABCs, proper error hierarchy, solid async job system |
| Backend routes | 9/10 | Complete, validated, consistent |
| Standard TTS pipeline | 9/10 | Real, wired, internet-dependent |
| Frontend logic | 8/10 | Correct flow; one proxy bug fixed |
| Clone implementation | 7/10 | Real code present; fallback-only by default |
| Clone transparency | 6/10 → 8/10 | Fixed in this commit (mode+cloning_applied in job record) |
| Local setup UX | 4/10 → 9/10 | Fixed in this commit (proxy, .env, startup scripts, docs) |
| Dead code | 10/10 | None found |

---

## Files audited

| File | Lines | Assessment |
|------|-------|------------|
| `backend/main.py` | ~290 | Complete, well-structured. Fixed: mode+cloning_applied in JobMeta |
| `backend/providers/base.py` | ~70 | Clean ABC. No issues |
| `backend/providers/edge_tts_provider.py` | ~110 | Real EdgeTTS pipeline. No issues |
| `backend/providers/openvoice_provider.py` | ~260 | Real OpenVoice code. Fixed: cloning_applied propagation |
| `backend/jobs/models.py` | ~80 | Fixed: added mode + cloning_applied to JobMeta + to_dict() |
| `backend/jobs/manager.py` | ~120 | Correct asyncio-safe in-memory manager. No issues |
| `backend/storage/backend.py` | ~130 | Clean filesystem storage. No issues |
| `backend/requirements.txt` | 10 | Minimal correct deps for Standard TTS. No issues |
| `frontend/src/App.jsx` | ~70 | Static shell. Static "Online" badge (cosmetic, not touched) |
| `frontend/src/components/VoiceCloner.jsx` | ~340 | Correct Generate flow. Works after proxy fix |
| `frontend/src/components/AudioPlayer.jsx` | ~180 | Full-featured player. No issues |
| `frontend/vite.config.js` | ~35 | **FIXED**: added /generate /job /health /files proxy routes |
| `frontend/package.json` | 20 | Correct deps (react 18, axios, vite 5). No issues |
| `frontend/.env.example` | 10 | **UPDATED**: added VITE_API_BASE local dev entry |

---

## Bugs found and fixed in this commit

### BUG-1 — BLOCKER: frontend proxy mis-wiring

**File**: `frontend/vite.config.js`

**Problem**: The Vite dev server only proxied the `/api` prefix to the
backend. The Generate button POSTs to `/generate` and polls `/job/:id`,
both of which went to the Vite origin (port 5173) instead of FastAPI (port
8000). A fresh clone with no `.env` could not generate any audio.

**Fix**: Added proxy entries for `/generate`, `/job`, `/jobs`, `/health`,
and `/files`, all pointing to `VITE_DEV_BACKEND` (default `localhost:8000`).

**Impact**: Generate button now works on a fresh clone without any manual
config beyond creating `frontend/.env`.

---

### BUG-2 — Clone mode transparency gap

**Files**: `backend/jobs/models.py`, `backend/main.py`,
`backend/providers/openvoice_provider.py`

**Problem**:
- `JobMeta` had no `mode` field, so `GET /job/{id}` could not report which
  mode was used.
- `cloning_applied` existed only as a local variable inside the provider
  and a log line. It was never stored in the job record.
- The UI had no reliable way to distinguish real OpenVoice cloning from
  EdgeTTS fallback after the job completed.

**Fix**:
1. Added `mode: str = "standard"` and `cloning_applied: bool = False` to
   `JobMeta` dataclass.
2. Added `OpenVoiceSynthesisResult` (subclass of `SynthesisResult`) in
   `openvoice_provider.py` that carries `cloning_applied=True/False`.
3. Worker reads `result.cloning_applied` and writes it back to `job.meta`.
4. `Job.to_dict()` now returns `mode` and `cloning_applied` in every response.

**Impact**: `GET /job/{id}` now exposes `"cloning_applied": true/false`,
giving the frontend accurate real-vs-fallback information.

---

## Dead code audit

| Item | Decision | Reason |
|------|----------|--------|
| `_pcm_to_wav()` in both provider files | **Kept** | Intentional decoupling; safe removal requires runtime test |
| `GET /jobs` debug route | **Kept** | Functional debug utility; no auth rule |
| `AzureSpeechProvider` mention in docstring | **Kept** | Documentation only, no code |

**Result**: No safe deletions found. Nothing removed.

---

## Integration flow (verified by inspection)

```
User (browser :5173)
  │
  │  POST /generate  (multipart: audio, text, language, mode)
  ▼
Vite proxy → FastAPI :8000
  │
  │  1. Validate inputs
  │  2. storage.put(upload_key, audio_bytes)
  │  3. job_manager.create_job(meta{mode, language, text, voice})
  │  4. background_tasks.add_task(_run_tts_job, ..., mode=mode)
  │  5. Return 202 {job_id, poll_url, mode, cloning_enabled}
  ▼
BackgroundTask: _run_tts_job
  │
  │  provider.synthesise(request)
  │    → EdgeTTSProvider: edge-tts stream → miniaudio decode → WAV
  │    → OpenVoiceProvider: EdgeTTS base → ToneColorConverter (if enabled)
  │        Returns OpenVoiceSynthesisResult{wav_bytes, cloning_applied}
  │  storage.put(output_key, wav_bytes)
  │  job.meta.cloning_applied = result.cloning_applied
  │  job_manager.update_status(COMPLETED, output_url)
  ▼
Client polls GET /job/{id} every 2s
  │  → {status, output_url, mode, cloning_applied, ...}
  ▼
On completed: fetch output_url → blob → AudioPlayer
  │  WAV served from /files/outputs/<job_id>_output.wav
  ▼
User plays + downloads output WAV
```

---

## What requires real local testing (not provable by inspection)

1. EdgeTTS reaches `speech.platform.bing.com` and returns audio
2. `miniaudio` decodes the compressed stream correctly
3. WAV is written to `backend/outputs/` and served correctly
4. Browser blob URL plays in AudioPlayer and downloads correctly
5. 120-second poll timeout behaves gracefully
6. Clone fallback completes without crash when `OPENVOICE_ENABLED=false`
7. Real clone inference with OpenVoice + checkpoints installed
8. RAM / CPU usage under Standard TTS synthesis
9. Concurrent request handling

---

## Recommendation

**Continue local development.**

The application is structurally sound. The two code-level blockers preventing
real local usage have been fixed in this commit. The next step is to run the
backend and frontend locally and execute the test checklist in `APP_STATUS.md`
to convert inspection findings into verified runtime results.

Do **not** deploy until Standard TTS is verified end-to-end locally.
