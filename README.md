# IndicVoice AI

<p align="center">
  <img src="https://img.shields.io/badge/Telugu-తెలుగు-blue?style=flat-square" alt="Telugu" />
  <img src="https://img.shields.io/badge/Tamil-தமிழ்-blue?style=flat-square" alt="Tamil" />
  <img src="https://img.shields.io/badge/Hindi-हिन्दी-blue?style=flat-square" alt="Hindi" />
  <img src="https://img.shields.io/badge/English-supported-blue?style=flat-square" alt="English" />
  <img src="https://img.shields.io/badge/React-18-61dafb?style=flat-square&logo=react" alt="React 18" />
  <img src="https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Cloudflare_Workers-deployed-f38020?style=flat-square&logo=cloudflare" alt="CF Workers" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT" />
</p>

<p align="center">
  A production-ready AI voice cloning platform for Indic languages.<br/>
  Neural TTS · Voice cloning · Async job queue · Cloudflare edge delivery.
</p>

---

## Features

| Feature | Detail |
|---------|--------|
| 🗣️ **Neural TTS** | Microsoft Neural voices via edge-tts — no model download, pure Python |
| 🧬 **Voice Cloning** | OpenVoice v2 tone-colour transfer (GPU backend, with EdgeTTS fallback) |
| 🌐 **4 Languages** | Telugu · Tamil · Hindi · English (extensible) |
| ⚡ **Async Jobs** | 202 Accepted → polling → WAV download; 2-min timeout with progress badges |
| 🔒 **Secure Gateway** | Cloudflare Workers API gateway with API-key auth + per-IP rate limiting |
| 🛡️ **Security Headers** | HSTS · CSP · X-Frame-Options · Referrer-Policy on every response |
| 🌍 **Edge Delivery** | CF Pages (frontend) + CF Workers (API) + R2 (audio) + KV (job state) |
| 📱 **Mobile-first UI** | React + Vite + Tailwind CSS — drag-and-drop upload, responsive grid |

---

## Architecture

```
Browser (CF Pages)
      │  HTTPS  X-Api-Key
      ▼
CF Worker  (workers/index.ts)
  ├─ Auth middleware    ← WORKER_API_KEY secret
  ├─ Rate limiter       ← RATE_LIMITER KV  (20 req/60 s per IP)
  ├─ Security headers   ← HSTS, CSP, X-Frame-Options …
  ├─ POST /generate ──► FastAPI backend  (Railway / Modal / Fly)
  ├─ GET  /job/:id  ──► KV-first, R2 URL injection, FastAPI fallback
  └─ DELETE /job/:id──► KV + R2 + backend GDPR erasure

FastAPI  (backend/main.py)
  ├─ Background TTS worker  (edge-tts → miniaudio → WAV)
  ├─ JobManager ABC         (LocalJobManager / CF KV impl)
  ├─ StorageBackend ABC     (LocalStorage / CF R2 impl)
  └─ TTSProvider ABC        (EdgeTTSProvider / OpenVoiceProvider)

Cloudflare Storage
  ├─ KV JOB_STORE    — job metadata (8 h TTL)
  ├─ KV RATE_LIMITER — sliding-window counters
  └─ R2 AUDIO_BUCKET — uploads/ + outputs/
```

---

## Project Layout

```
indic-voice-ai/
├── frontend/               # React + Vite + Tailwind
│   ├── src/
│   │   ├── App.jsx
│   │   └── components/
│   │       ├── VoiceCloner.jsx   # Main UI — upload, TTS, polling, player
│   │       ├── AudioPlayer.jsx   # Custom audio player with seek bar
│   │       └── Hero.jsx          # Landing hero section
│   └── public/
│       └── _headers              # CF Pages security headers
├── backend/                # FastAPI
│   ├── main.py             # Routes: /generate /job/:id /health
│   ├── jobs/               # JobManager ABC + LocalJobManager
│   ├── providers/          # TTSProvider ABC + EdgeTTS + OpenVoice
│   ├── storage/            # StorageBackend ABC + LocalStorage
│   └── requirements.txt
├── workers/                # Cloudflare Workers API gateway (TypeScript)
│   ├── index.ts            # Router + auth + rate-limit + CORS + proxy
│   ├── wrangler.toml       # KV/R2 bindings + secrets + env config
│   ├── package.json
│   └── tsconfig.json
├── docs/
│   ├── ARCHITECTURE.md     # Full Cloudflare architecture design (63 KB)
│   ├── CLOUDFLARE_WORKERS.md
│   ├── DEPLOYMENT.md
│   ├── JOBS.md             # Async job system design
│   ├── SECURITY.md         # API key, rate-limit, CORS, headers runbook
│   └── VOICE_CLONING.md    # OpenVoice v2 pipeline + speaker embedding cache
├── .env.example
├── ARCHITECTURE.md
└── DEVELOPMENT.md
```

---

## Quick Start

### 1 — Frontend

```bash
cd frontend
npm install
cp ../.env.example .env          # set VITE_API_BASE=http://localhost:8000
npm run dev                      # http://localhost:5173
```

### 2 — Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload        # http://localhost:8000
```

### 3 — Workers (optional local dev)

```bash
cd workers
npm install
npx wrangler dev                 # http://localhost:8787
```

Open `http://localhost:5173` — upload any WAV/MP3, pick a language, hit **Generate**.

---

## Environment Variables

### Frontend (`frontend/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_WORKERS_URL` | Prod | CF Workers gateway URL (e.g. `https://indic-voice.your-subdomain.workers.dev`) |
| `VITE_WORKER_API_KEY` | Prod | Must match `WORKER_API_KEY` secret on the Worker |
| `VITE_API_BASE` | Dev | Direct FastAPI URL for local dev without Workers |

### Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENVOICE_ENABLED` | `false` | Enable OpenVoice v2 GPU cloning pipeline |
| `OPENVOICE_CHECKPOINT_DIR` | — | Path to OpenVoice v2 checkpoint weights |
| `OPENVOICE_DEVICE` | `cpu` | `cpu` or `cuda` |

### Workers (`wrangler secret put` / CF dashboard)

| Secret / Var | Type | Description |
|-------------|------|-------------|
| `BACKEND_URL` | Secret | FastAPI backend base URL |
| `WORKER_API_KEY` | Secret | Shared API key — generate with `openssl rand -hex 32` |
| `ALLOWED_ORIGINS` | Var | Comma-separated CORS origins (e.g. `https://indic-voice.pages.dev`) |
| `RATE_LIMIT_REQUESTS` | Var | Max requests per IP per window (default `20`) |
| `RATE_LIMIT_WINDOW_SECONDS` | Var | Window size in seconds (default `60`) |

---

## Production Deployment

### Step 1 — Create Cloudflare resources

```bash
# KV namespaces
wrangler kv namespace create indic-voice-jobs
wrangler kv namespace create indic-voice-jobs --preview
wrangler kv namespace create indic-voice-ratelimit
wrangler kv namespace create indic-voice-ratelimit --preview

# R2 bucket
wrangler r2 bucket create indic-voice-audio

# Paste the namespace IDs into workers/wrangler.toml
```

### Step 2 — Set secrets

```bash
wrangler secret put BACKEND_URL      --env production   # https://your-fastapi.railway.app
wrangler secret put WORKER_API_KEY   --env production   # openssl rand -hex 32
```

### Step 3 — Deploy Worker

```bash
cd workers
npm install
npm run deploy:prod
```

### Step 4 — Deploy Frontend (CF Pages)

Connect the repo to Cloudflare Pages:
- **Framework preset**: Vite
- **Build command**: `npm run build`
- **Build output**: `dist`
- **Root directory**: `frontend`
- **Environment variables**:
  - `VITE_WORKERS_URL` = `https://indic-voice-ai.your-account.workers.dev`
  - `VITE_WORKER_API_KEY` = *(same value as WORKER_API_KEY secret)*

### Step 5 — Verify

```bash
# Check all bindings are wired
curl https://indic-voice-ai.your-account.workers.dev/health | jq .bindings
# → { r2: true, kv: true, rate_limiter: true, backend: true, auth_enabled: true }

# Test authenticated generate
curl -X POST https://indic-voice-ai.your-account.workers.dev/generate \
  -H "X-Api-Key: $WORKER_API_KEY" \
  -F "text=నమస్కారం" \
  -F "language=te" \
  -F "audio=@sample.wav"
# → HTTP 202 { job_id, status: "queued", poll_url }
```

---

## Deploy Backend

The FastAPI backend can be deployed on any Python-capable host. Recommended options:

| Platform | Command |
|----------|---------|
| **Railway** | Connect repo → select `backend/` → add `uvicorn main:app --host 0.0.0.0 --port $PORT` start command |
| **Modal** | See `docs/VOICE_CLONING.md` for the Modal deployment skeleton with GPU support |
| **Fly.io** | `fly launch` in `backend/` — add `OPENVOICE_ENABLED=true` for cloning |
| **Render** | Web service → `pip install -r requirements.txt && uvicorn main:app` |

---

## Documentation

| Document | Contents |
|----------|---------|
| [`docs/SECURITY.md`](docs/SECURITY.md) | API key auth, rate limiting, CORS, secrets, CF Access, error codes |
| [`docs/CLOUDFLARE_WORKERS.md`](docs/CLOUDFLARE_WORKERS.md) | Workers setup, R2/KV, route reference, troubleshooting |
| [`docs/JOBS.md`](docs/JOBS.md) | Async job system design, polling contract, GDPR erasure |
| [`docs/VOICE_CLONING.md`](docs/VOICE_CLONING.md) | OpenVoice v2 pipeline, speaker embedding cache, limitations |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | CF Pages + Workers deploy checklist |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Full Cloudflare production architecture design (63 KB) |
| [`DEVELOPMENT.md`](DEVELOPMENT.md) | Local dev setup, TTS engine docs, voice table, test commands |

---

## License

MIT © 2026 [dileep12072024-sudo](https://github.com/dileep12072024-sudo)
