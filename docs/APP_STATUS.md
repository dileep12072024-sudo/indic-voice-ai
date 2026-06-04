# IndicVoice AI — App Status

> **Method**: static code inspection. No app execution was performed here.
> Items marked "requires local testing" must be validated on a real machine.
> Last updated: 2026-06-04

---

## Core question: can a real user use this app?

**Yes — for Standard TTS, after applying the proxy fix in this commit.**
**No — for real Voice Cloning, without enabling OpenVoice + checkpoints.**

---

## Component status

### ✅ Backend API — REAL and complete

| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /health` | ✅ Real | Returns version, cloning_available, languages, modes |
| `POST /generate` | ✅ Real | Validates all inputs; returns 202 + poll_url |
| `GET /job/{id}` | ✅ Real | Returns full job state including mode + cloning_applied |
| `DELETE /job/{id}` | ✅ Real | GDPR erasure of record + files |
| `GET /jobs` | ✅ Real | Debug list of in-memory jobs |

Input validation covers: language (te/ta/hi/en), mode (standard/clone),
text length (≤500 chars), file extension, empty file, 50 MB size limit.

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
| `OPENVOICE_ENABLED=false` (default) | Falls back to EdgeTTS, `cloning_applied=false` |
| `OPENVOICE_ENABLED=true`, no checkpoints | Falls back to EdgeTTS, `cloning_applied=false` |
| `OPENVOICE_ENABLED=true`, checkpoints present, PyTorch installed | Real ToneColorConverter cloning, `cloning_applied=true` |

`requirements.txt` intentionally does **not** include PyTorch or the
`openvoice` package. Real cloning requires manual setup (see `RUN_LOCALLY.md`).

### ✅ Frontend Generate flow — REAL (proxy bug fixed in this commit)

1. Validate file (type, size) and text
2. `POST /generate` with multipart form (audio, text, language, mode)
3. Poll `GET /job/{id}` every 2 s, timeout 120 s
4. On `completed`: fetch `output_url`, create blob, feed AudioPlayer
5. Error states: backend down, 4xx/5xx, timeout, empty blob — all handled

**Fixed in this commit**: `vite.config.js` now proxies `/generate`, `/job`,
`/health`, and `/files` to the backend. Previously only `/api` was proxied,
causing the Generate button to fail on a fresh clone.

### ✅ Clone transparency — FIXED in this commit

`GET /job/{id}` now returns `mode` and `cloning_applied` in the response.
`cloning_applied=true` only when OpenVoice v2 actually ran inference.
Previously, the job record had no way to tell real cloning from fallback.

---

## What still requires real local testing

- [ ] End-to-end Standard TTS for te / ta / hi / en (internet required)
- [ ] Browser audio playback and WAV download
- [ ] Confirm WAV files appear in `backend/outputs/`
- [ ] Clone mode fallback: graceful, no crash, `cloning_applied=false`
- [ ] Clone mode real: enable + install checkpoints; verify tone transfer
- [ ] RAM / CPU measurements under actual synthesis
- [ ] 2-minute polling timeout behaviour when backend is slow

---

## Known non-blockers (by inspection)

- `App.jsx` shows a static "API Online" badge regardless of backend state.
  This is cosmetic and was not changed (no UI redesign rule).
- `_pcm_to_wav()` is duplicated in `edge_tts_provider.py` and
  `openvoice_provider.py`. This is intentional provider decoupling, not
  dead code. Not removed.
- `GET /jobs` is an unauthenticated debug route. Not changed (no auth work rule).
