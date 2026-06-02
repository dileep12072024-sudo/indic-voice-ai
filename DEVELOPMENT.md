# DEVELOPMENT.md — IndicVoice AI Local Setup Guide

## Prerequisites

| Tool | Version |
|------|---------|
| Node.js | ≥ 18 |
| npm | ≥ 9 |
| Python | ≥ 3.10 |
| pip | ≥ 22 |

---

## Repository Structure

```
indic-voice-ai/
├── frontend/          # React + Vite + Tailwind CSS
│   ├── src/
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   ├── index.css
│   │   └── components/
│   │       ├── Hero.jsx
│   │       ├── VoiceCloner.jsx   ← drag-drop upload, validation, generate, player
│   │       └── AudioPlayer.jsx   ← playback, seek, volume, download
│   ├── index.html
│   ├── vite.config.js            ← /api proxy → localhost:8000
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   └── package.json
│
├── backend/           # FastAPI
│   ├── main.py        ← /health, /generate endpoints
│   ├── requirements.txt
│   ├── uploads/       ← incoming voice samples (auto-created)
│   └── outputs/       ← generated WAV files (auto-created)
│
├── .gitignore
├── DEVELOPMENT.md     ← this file
└── README.md
```

---

## Quick Start (two terminals)

### Terminal 1 — Backend

```bash
cd backend

# Create and activate a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Backend will be available at:
- API root:    http://localhost:8000
- Health:      http://localhost:8000/health
- Swagger UI:  http://localhost:8000/docs
- ReDoc:       http://localhost:8000/redoc

### Terminal 2 — Frontend

```bash
cd frontend

# Install dependencies
npm install

# (Optional) copy env example
cp .env.example .env.local

# Start the dev server
npm run dev
```

Frontend will be available at: http://localhost:5173

> The Vite dev server proxies all `/generate` and `/health` requests to `http://localhost:8000` automatically — no manual CORS setup needed during development.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_BASE` | `""` (empty) | API base URL. Leave empty in dev (Vite proxy handles routing). Set to the deployed backend URL in production. |

Create `frontend/.env.local` from the example:

```bash
cp frontend/.env.example frontend/.env.local
```

---

## Testing the Endpoints

### Health check

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "IndicVoice AI API",
  "version": "1.0.0",
  "supported_languages": {
    "te": "Telugu",
    "ta": "Tamil",
    "hi": "Hindi",
    "en": "English"
  },
  "max_upload_mb": 50,
  "accepted_formats": [".flac", ".mp3", ".ogg", ".wav", ".webm"]
}
```

### Generate endpoint

```bash
# Requires a real audio file — generate a 3-second silent WAV for testing:
python3 -c "
import struct, math
sr=22050; dur=3; amp=100; n=sr*dur
samples=[int(amp*math.sin(2*math.pi*440*i/sr)) for i in range(n)]
with open('/tmp/test.wav','wb') as f:
    f.write(b'RIFF'); f.write(struct.pack('<I',36+n*2)); f.write(b'WAVE')
    f.write(b'fmt '); f.write(struct.pack('<IHHIIHH',16,1,1,sr,sr*2,2,16))
    f.write(b'data'); f.write(struct.pack('<I',n*2))
    [f.write(struct.pack('<h',max(-32768,min(32767,s)))) for s in samples]
print('Created /tmp/test.wav')
"

curl -X POST http://localhost:8000/generate \
  -F "audio=@/tmp/test.wav" \
  -F "text=Hello from IndicVoice AI" \
  -F "language=en" \
  --output /tmp/generated.wav

# Play the result (macOS)
afplay /tmp/generated.wav

# Play the result (Linux)
aplay /tmp/generated.wav
```

---

## Production Build

```bash
# Build the frontend
cd frontend
npm run build
# Output is in frontend/dist/

# Preview the production build locally
npm run preview
```

---

## Validation Rules

### Audio upload
| Rule | Limit |
|------|-------|
| Max file size | 50 MB |
| Accepted formats | WAV, MP3, OGG, WEBM, FLAC |
| Min recommended duration | 10 s (30 s ideal) |

### Text input
| Rule | Limit |
|------|-------|
| Max length | 500 characters |
| Min length | 1 character (non-whitespace) |

---

## Known Limitations (Phase 1)

- The `/generate` endpoint returns a **placeholder sine-wave WAV** — real voice cloning inference is not yet wired up.
- Uploaded files are persisted in `backend/uploads/`. Add a cleanup job in production.
- Output files are persisted in `backend/outputs/`. Add a cleanup job in production.

---

## Roadmap

| Phase | Goal |
|-------|------|
| ✅ Phase 1 | Full-stack scaffold: React UI + FastAPI + placeholder audio |
| 🔄 Phase 2 | Stability, validation, end-to-end pipeline (this release) |
| ⬜ Phase 3 | Real TTS/voice-cloning model integration |
| ⬜ Phase 4 | Authentication, rate limiting, cloud storage |
