# IndicVoice AI — App Status

> Last updated: Phase 6 — Voice Profile Library

---

## Phase 6 Feature Summary

| Area | Feature | Status |
|------|---------|--------|
| **Backend** | `POST /voices` — save named voice profile | ✅ Implemented |
| **Backend** | `GET /voices` — list all profiles (newest-first) | ✅ Implemented |
| **Backend** | `GET /voices/{id}` — fetch one profile | ✅ Implemented |
| **Backend** | `DELETE /voices/{id}` — delete profile + sample file | ✅ Implemented |
| **Backend** | JSON file storage (`voices.json`) with atomic writes | ✅ Implemented |
| **Backend** | `make_provider()` engine abstraction (VOICE-2) | ✅ Implemented |
| **Backend** | `/generate` accepts `voice_id` OR direct `audio` upload | ✅ Implemented |
| **Frontend** | React Router v6 multi-page SaaS layout | ✅ Implemented |
| **Frontend** | `/` — Voice Library page (card grid, delete, empty state) | ✅ Implemented |
| **Frontend** | `/create` — Add Voice page (drag-drop + form) | ✅ Implemented |
| **Frontend** | `/generate` — Generate Speech page (voice picker + text) | ✅ Implemented |
| **Frontend** | Persistent Navbar with active-link indicator | ✅ Implemented |
| **Frontend** | Clean SaaS design — Inter font, indigo palette, no neon | ✅ Implemented |
| **Frontend** | All scan-line / cyberpunk elements removed | ✅ Implemented |
| **Frontend** | AudioPlayer: indigo theme, seek bar, volume | ✅ Implemented |
| **Frontend** | Download WAV button on completed jobs | ✅ Implemented |

---

## Phase 5 features retained

| Feature | Status |
|---------|--------|
| Text limit 2000 chars (backend + frontend char counter) | ✅ |
| `fallback_used` + `provider_name` in `/generate` + `/job/{id}` | ✅ |
| Background TTL job cleanup (8 h TTL, 30 min sweep) | ✅ |
| `/health` extended metrics (active_jobs, outputs_dir_mb, voice_profiles) | ✅ |
| Standard TTS (EdgeTTS) — 4 Indic languages | ✅ |
| Clone mode (defaults to EdgeTTS fallback until OpenVoice installed) | ✅ |

---

## Voice Cloning Readiness

Cloning mode is guarded by `OPENVOICE_ENABLED=true` env var.
Without it, clone requests fall back to EdgeTTS transparently.

**To enable real cloning:**
```bash
pip install -r backend/requirements-clone.txt   # installs torch, openvoice
export OPENVOICE_ENABLED=true
./scripts/start-backend.sh
```

---

## Running Locally

```bash
# Terminal 1 — backend (port 8000)
./scripts/start-backend.sh        # macOS/Linux
scripts\start-backend.bat         # Windows

# Terminal 2 — frontend (port 5173)
./scripts/start-frontend.sh       # macOS/Linux
scripts\start-frontend.bat        # Windows

# Open http://localhost:5173
```

---

## API Quick Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service status + voice count + job metrics |
| POST | `/voices` | Save a named voice profile |
| GET | `/voices` | List all voice profiles |
| GET | `/voices/{id}` | Get one profile |
| DELETE | `/voices/{id}` | Delete profile + sample |
| POST | `/generate` | Submit TTS/clone job (async, returns job_id) |
| GET | `/job/{id}` | Poll job status + output URL |
| DELETE | `/job/{id}` | Delete a job |
| GET | `/docs` | Interactive Swagger UI |

---

## Architecture Overview

```
frontend/src/
├── main.jsx              # BrowserRouter root
├── App.jsx               # Route table (/, /create, /generate)
├── index.css             # SaaS design system (Inter, indigo, no cyberpunk)
├── components/
│   ├── Navbar.jsx        # Fixed top nav, active NavLink
│   └── AudioPlayer.jsx   # Seek bar, volume, download
└── pages/
    ├── VoiceLibrary.jsx  # Card grid + delete
    ├── CreateVoice.jsx   # Upload form → POST /voices
    └── GenerateSpeech.jsx # Voice picker + text → POST /generate

backend/
├── main.py               # FastAPI v6.0.0 — all routes
├── voices/
│   ├── __init__.py
│   └── models.py         # VoiceProfile + VoiceStore (JSON storage)
├── jobs/                 # Job management (unchanged)
├── providers/            # EdgeTTS + OpenVoice (unchanged)
└── storage/              # LocalStorageBackend (unchanged)
```
