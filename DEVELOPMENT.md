# DEVELOPMENT.md — IndicVoice AI Local Setup Guide

## Prerequisites

| Tool | Version |
|------|---------|
| Node.js | ≥ 18 |
| npm | ≥ 9 |
| Python | ≥ 3.10 |
| pip | ≥ 22 |
| Internet access | Required for TTS (edge-tts calls Microsoft Speech API) |

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
│   ├── .env.example
│   └── package.json
│
├── backend/           # FastAPI + edge-tts
│   ├── main.py        ← /health, /generate endpoints + real TTS
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

> The Vite dev server proxies all requests to `/generate` and `/health` → `http://localhost:8000` automatically.

---

## TTS Engine — Phase 3A

### Engine: Microsoft Neural TTS via `edge-tts`

| Property | Value |
|----------|-------|
| Package | `edge-tts >= 6.1.9` |
| Type | Online Neural TTS (no model download) |
| Latency | ~1–3 s per request |
| Internet required | **Yes** — calls `speech.platform.bing.com` |
| Audio output | MP3/WebM (converted to WAV by `miniaudio`) |
| WAV spec | 22050 Hz · mono · 16-bit PCM |

### Voices per language

| Code | Language | Voice | Gender |
|------|----------|-------|--------|
| `te` | Telugu | `te-IN-ShrutiNeural` | Female |
| `ta` | Tamil | `ta-IN-PallaviNeural` | Female |
| `hi` | Hindi | `hi-IN-SwaraNeural` | Female |
| `en` | English | `en-US-JennyNeural` | Female |

### No model download required

`edge-tts` sends text to Microsoft's cloud Neural TTS API and streams back compressed audio. There are no model files to download or GPU requirements.

### Offline / no-internet fallback

If the server cannot reach `speech.platform.bing.com`, the `/generate` endpoint returns:
```json
{ "detail": "TTS service is temporarily unavailable. edge-tts requires an internet connection..." }
```
with HTTP 503.

### Alternative voices (optional)

To change a voice, edit `LANGUAGE_CONFIG` in `backend/main.py`:

```python
"en": {
    "voice": "en-US-AriaNeural",   # change this line
    ...
}
```

Full voice list:
```bash
python3 -c "import asyncio, edge_tts; asyncio.run(edge_tts.list_voices())" | python3 -m json.tool | grep ShortName
```
Or browse: https://speech.microsoft.com/portal/voicegallery

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_BASE` | `""` | API base URL. Empty = use Vite proxy in dev. Set to deployed backend URL in production. |

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
  "version": "2.0.0",
  "tts_engine": "edge-tts (Microsoft Neural TTS)",
  "supported_languages": {
    "te": { "name": "Telugu",  "voice": "te-IN-ShrutiNeural",  "gender": "Female", "locale": "te-IN" },
    "ta": { "name": "Tamil",   "voice": "ta-IN-PallaviNeural", "gender": "Female", "locale": "ta-IN" },
    "hi": { "name": "Hindi",   "voice": "hi-IN-SwaraNeural",   "gender": "Female", "locale": "hi-IN" },
    "en": { "name": "English", "voice": "en-US-JennyNeural",   "gender": "Female", "locale": "en-US" }
  },
  "max_upload_mb": 50,
  "accepted_formats": [".flac", ".mp3", ".ogg", ".wav", ".webm"],
  "output_format": "WAV (PCM 16-bit, 22050 Hz, mono)"
}
```

### Generate endpoint — English

```bash
# Create a minimal test WAV sample
python3 -c "
import struct, math
sr=22050; n=sr*2; amp=8000
s=[int(amp*math.sin(2*math.pi*440*i/sr)) for i in range(n)]
with open('/tmp/test.wav','wb') as f:
    f.write(b'RIFF'); f.write(struct.pack('<I',36+n*2)); f.write(b'WAVE')
    f.write(b'fmt '); f.write(struct.pack('<IHHIIHH',16,1,1,sr,sr*2,2,16))
    f.write(b'data'); f.write(struct.pack('<I',n*2))
    [f.write(struct.pack('<h',max(-32768,min(32767,v)))) for v in s]
"

curl -X POST http://localhost:8000/generate \
  -F "audio=@/tmp/test.wav" \
  -F "text=Hello from IndicVoice AI" \
  -F "language=en" \
  --output /tmp/out_en.wav
```

### Generate endpoint — Telugu

```bash
curl -X POST http://localhost:8000/generate \
  -F "audio=@/tmp/test.wav" \
  -F "text=నమస్కారం, ఇది IndicVoice AI నుండి తెలుగు వాయిస్." \
  -F "language=te" \
  --output /tmp/out_te.wav
```

### Generate endpoint — Hindi

```bash
curl -X POST http://localhost:8000/generate \
  -F "audio=@/tmp/test.wav" \
  -F "text=नमस्ते, यह IndicVoice AI से हिंदी आवाज़ है।" \
  -F "language=hi" \
  --output /tmp/out_hi.wav
```

### Play the output

```bash
# macOS
afplay /tmp/out_en.wav

# Linux (ALSA)
aplay /tmp/out_en.wav

# Linux (PulseAudio)
paplay /tmp/out_en.wav
```

---

## Validation Rules

### Audio upload
| Rule | Limit |
|------|-------|
| Max file size | 50 MB |
| Accepted formats | WAV, MP3, OGG, WEBM, FLAC |
| Purpose (Phase 3A) | Accepted and stored; not used for cloning yet |

### Text input
| Rule | Limit |
|------|-------|
| Max length | 500 characters |
| Min length | 1 character (non-whitespace) |
| Encoding | UTF-8 (full Unicode, including Indic scripts) |

---

## Production Build

```bash
# Build the frontend
cd frontend
npm run build
# Output: frontend/dist/

# Preview the production build
npm run preview
# Set VITE_API_BASE to your deployed backend URL in .env.local before building
```

---

## Known Limitations (Phase 3A)

- **Not voice cloning** — Phase 3A uses a fixed Neural TTS voice per language. The uploaded voice sample is saved but not used for speaker adaptation yet.
- **Internet required** — `edge-tts` calls the Microsoft Speech API. Offline use returns HTTP 503.
- **File persistence** — uploads and outputs accumulate in `backend/uploads/` and `backend/outputs/`. Add a scheduled cleanup job in production.
- **Rate limits** — Microsoft's TTS API is accessed via `edge-tts` without authentication. High traffic may be throttled.

---

## Roadmap

| Phase | Goal | Status |
|-------|------|--------|
| Phase 1 | Full-stack scaffold: React UI + FastAPI + placeholder audio | ✅ Done |
| Phase 2 | Stability, validation, frontend↔backend pipeline | ✅ Done |
| Phase 3A | Real Neural TTS (edge-tts, all 4 languages) | ✅ Done |
| Phase 3B | Voice cloning: speaker adaptation / XTTS-v2 integration | ⬜ Planned |
| Phase 4 | Auth, rate limiting, cloud storage, deployment | ⬜ Planned |
