# IndicVoice AI — App Status

> **Method**: static code inspection. No app execution was performed here.
> Items marked "requires local testing" must be validated on a real machine.
> Last updated: 2026-06-04

---

## Core question: can a real user use this app?

**Yes — for Standard TTS, after applying the proxy fix in commit `c48fb0f`.**
**No — for real Voice Cloning, without enabling OpenVoice + checkpoints.**

---

## Component status

### ✅ Backend API — REAL and complete (Phase 5)

| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /health` | ✅ Real | Returns version, cloning_available, languages, modes, **max_text_length** |
| `POST /generate` | ✅ Real | Validates all inputs; returns 202 + job_id + **provider_name** |
| `GET /job/{id}` | ✅ Real | Returns full job state including mode, cloning_applied, **fallback_used**, **provider_name** |
| `DELETE /job/{id}` | ✅ Real | GDPR erasure of record + files |
| `GET /jobs` | ✅ Real | Debug list of in-memory jobs |

Input validation covers: language (te/ta/hi/en), mode (standard/clone),
text length (**≤2000 chars** — raised from 500 in Phase 5), file extension, empty file, 50 MB size limit.

### ✅ Async job lifecycle — REAL

`QUEUED → PROCESSING → COMPLETED / FAILED` via FastAPI `BackgroundTasks`
+ `LocalJobManager` (in-memory, 8 h TTL, asyncio-safe).
Output WAV written to `backend/outputs/` and served via `/files`.

### ✅ Standard TTS (EdgeTTS) — REAL, internet-dependent

- Engine: `edge-tts` (Microsoft Neural voices) + `miniaudio` decode
- Output: 16-bit PCM WAV, 22050 Hz, mono
- Languages: Telugu (`te-IN-ShrutiNeural`), Tamil (`ta-IN-PallaviNeural`),
  Hindi (`hi-IN-SwaraNeural`), English (`en-US-JennyNeural`)
- **Requires outbound internet access to `speech.platform.bing.com`**
- Status: code complete; **requires live run to confirm end-to-end**

### ⚠️ Voice Clone (OpenVoice v2) — FALLBACK by default

| Condition | Result |
|-----------|--------|
| `OPENVOICE_ENABLED=false` (default) | Falls back to EdgeTTS, `cloning_applied=false`, `fallback_used=true` |
| `OPENVOICE_ENABLED=true`, no checkpoints | Falls back to EdgeTTS, `cloning_applied=false`, `fallback_used=true` |
| `OPENVOICE_ENABLED=true`, checkpoints present, PyTorch installed | Real ToneColorConverter cloning, `cloning_applied=true`, `fallback_used=false` |

`requirements.txt` intentionally does **not** include PyTorch or the
`openvoice` package. Real cloning requires manual setup (see `RUN_LOCALLY.md`).

### ✅ Frontend — Premium UI (Phase 5 upgrade)

**New in Phase 5:**

| Feature | Detail |
|---------|--------|
| Card-based section layout | Voice Sample · Text · Language · Mode · Status · Output |
| Mobile-first responsive grid | Stacked on mobile (≤767 px), side-by-side on tablet/desktop (≥768 px) |
| Drag-and-drop upload | Drop zone with live feedback; click-to-browse retained |
| Character counter | Real-time 0/2000 display with colour-coded fill bar (cyan→lime→amber→red) |
| 3-step progress indicator | Submitting → Processing → Completed, with checkmarks |
| Clone status badge | **Real Clone** (green) / **Fallback** (orange) / **Standard TTS** (cyan) |
| Provider name display | Shows `provider_name` from job response under audio player |
| Actionable error messages | Copy-ready `start-backend.sh` / `.bat` commands in error text |
| Blob URL revocation | Prevents memory leaks on repeated generations (FIX-4 retained) |
| 50 MB client-side guard | Instant feedback before upload attempt (FIX-3 retained) |

**Generate flow (retained from Phase 4B, extended):**
1. Validate file (type, size) and text (1–2000 chars)
2. `POST /generate` with multipart form (audio, text, language, mode)
3. Poll `GET /job/{id}` every 2 s, timeout 120 s — status badge updates live
4. On `completed`: read `cloning_applied`, `fallback_used`, `provider_name` from response
5. Fetch `output_url`, create blob URL, feed to AudioPlayer
6. Error states: backend down, 4xx/5xx, timeout, empty blob — all handled with actionable messages

---

## New response fields (Phase 5)

All three locations (`/generate` response, `/job/{id}` poll, `Job.to_dict()`):

| Field | Type | Meaning |
|-------|------|---------|
| `fallback_used` | `bool` | `true` when clone mode ran EdgeTTS fallback instead of OpenVoice |
| `provider_name` | `str` | Name of the TTS provider that actually produced the audio |

Existing fields `mode` and `cloning_applied` are unchanged and still present.

---

## Text limit (Phase 5)

| Location | Before | After |
|----------|--------|-------|
| `backend/main.py` validation guard | `> 500` | `> 2000` |
| `backend/main.py` Form description | `1–500 characters` | `1–2000 characters` |
| `backend/main.py` MAX_TEXT_LENGTH constant | `500` | `2000` |
| `backend/jobs/models.py` — no direct limit stored | n/a | n/a |
| `frontend/src/components/VoiceCloner.jsx` | `500` | `2000` |
| `GET /health` response | not exposed | `max_text_length: 2000` |
| `scripts/test-standard-tts.py` docstring | `1–500` | `1–2000` |

---

## What still requires real local testing

- [ ] End-to-end Standard TTS for te / ta / hi / en (internet required)
- [ ] Browser audio playback and WAV download
- [ ] Confirm WAV files appear in `backend/outputs/`
- [ ] Clone mode fallback: graceful, no crash, `cloning_applied=false`, `fallback_used=true`
- [ ] Clone mode real: enable + install checkpoints; verify tone transfer + `cloning_applied=true`
- [ ] RAM / CPU measurements under actual synthesis
- [ ] 2-minute polling timeout behaviour when backend is slow
- [ ] Mobile layout at 320 px, 375 px, 768 px viewports
- [ ] Drag-and-drop upload on Chrome, Safari, Firefox

---

## Known non-blockers (by inspection)

- `App.jsx` shows a static "API Online" badge regardless of backend state.
  Cosmetic — not changed (nav bar change not requested).
- `_pcm_to_wav()` is duplicated in `edge_tts_provider.py` and
  `openvoice_provider.py`. Intentional provider decoupling, not dead code.
- `GET /jobs` is an unauthenticated debug route. Not changed (no auth work rule).
- `fallback_used` defaults to `mode == "clone" and not cloning_applied` in the
  worker as a safe fallback if the provider result object does not expose the field.
