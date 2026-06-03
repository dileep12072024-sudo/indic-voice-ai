# Deployment Guide — Indic Voice AI

> **Phase 5 — Cloudflare Pages + FastAPI backend**

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  User's Browser                     │
└────────────────────────┬────────────────────────────┘
                         │  HTTPS
          ┌──────────────▼──────────────┐
          │     Cloudflare Pages CDN    │
          │  frontend/dist  (React SPA) │
          │  _headers  (security rules) │
          │  _redirects (SPA fallback)  │
          └──────────────┬──────────────┘
                         │  /api/* → proxy
          ┌──────────────▼──────────────┐
          │   FastAPI Backend Server    │
          │  (Railway / Fly.io / VPS)   │
          └─────────────────────────────┘
```

### Key Components

| Layer | Technology | Hosted On |
|-------|-----------|----------|
| Frontend SPA | React 18 + Vite | Cloudflare Pages |
| API Gateway | Cloudflare Pages Functions / proxy | Cloudflare |
| Backend API | FastAPI (Python 3.11) | Railway / Fly.io / self-hosted |
| Model Inference | Hugging Face Transformers | Backend host (GPU optional) |

---

## Prerequisites

- Node.js ≥ 18 and npm ≥ 9
- Python ≥ 3.11 and pip
- A [Cloudflare account](https://dash.cloudflare.com/) (free tier works)
- Git

---

## Local Development

### 1. Clone & install

```bash
git clone https://github.com/dileep12072024-sudo/indic-voice-ai.git
cd indic-voice-ai

# Frontend
cd frontend
npm install

# Backend
cd ../backend        # adjust path if different
pip install -r requirements.txt
```

### 2. Configure environment

```bash
# In the repo root
cp .env.example .env
# Edit .env — set VITE_DEV_BACKEND if your backend runs on a port other than 8000
```

### 3. Start services

```bash
# Terminal 1 — backend
uvicorn main:app --reload --port 8000

# Terminal 2 — frontend (Vite dev server with proxy)
cd frontend
npm run dev
```

The Vite dev server proxies `/api/*` → `http://localhost:8000` (or `VITE_DEV_BACKEND`).
Open <http://localhost:5173> in your browser.

---

## Production Build

```bash
cd frontend
npm run build        # outputs to frontend/dist/
```

Verify the build locally:

```bash
npm run preview      # serves dist/ at http://localhost:4173
```

---

## Deploying to Cloudflare Pages

### Option A — GitHub integration (recommended)

1. Go to [Cloudflare Dashboard → Pages](https://dash.cloudflare.com/?to=/:account/pages).
2. Click **Create a project → Connect to Git**.
3. Select the `indic-voice-ai` repository.
4. Set build settings:

   | Setting | Value |
   |---------|-------|
   | **Framework preset** | `Vite` |
   | **Build command** | `cd frontend && npm install && npm run build` |
   | **Build output directory** | `frontend/dist` |
   | **Root directory** | `/` (repo root) |

5. Under **Environment variables**, add:

   | Variable | Value | Environment |
   |----------|-------|-------------|
   | `VITE_API_URL` | `https://your-backend.example.com` | Production |
   | `VITE_DEV_BACKEND` | `http://localhost:8000` | Preview |

6. Click **Save and Deploy**.

Subsequent pushes to `main` trigger automatic re-deployments.

### Option B — Wrangler CLI

```bash
npm install -g wrangler
wrangler pages deploy frontend/dist --project-name indic-voice-ai
```

---

## Security Headers (`frontend/public/_headers`)

Cloudflare Pages automatically serves the `_headers` file from `public/`.
The current ruleset applies to all routes (`/*`):

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `X-XSS-Protection` | `1; mode=block` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(self), geolocation=()` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'unsafe-inline'; ...` |

To modify headers, edit `frontend/public/_headers` and redeploy.

---

## SPA Routing (`frontend/public/_redirects`)

```
/* /index.html 200
```

This tells Cloudflare Pages to serve `index.html` for every path, enabling
client-side routing (React Router, etc.) to work correctly on direct URL loads
and browser refreshes.

---

## Backend Deployment

### Railway (quick start)

```bash
npm install -g @railway/cli
railway login
cd backend
railway init
railway up
```

Set the `PORT` environment variable in the Railway dashboard to match your FastAPI bind port.

### Fly.io

```bash
curl -L https://fly.io/install.sh | sh
cd backend
fly launch      # follow prompts
fly deploy
```

### Self-hosted (systemd)

```ini
# /etc/systemd/system/indic-voice-ai.service
[Unit]
Description=Indic Voice AI FastAPI Backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/indic-voice-ai/backend
ExecStart=/opt/indic-voice-ai/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now indic-voice-ai
```

---

## Connecting Frontend ↔ Backend

In the Cloudflare Pages dashboard, set:

```
VITE_API_URL=https://your-backend.railway.app
```

Your `VoiceCloner.jsx` (and any other component) already reads:

```js
const API_BASE = import.meta.env.VITE_API_URL ?? ''
```

so no code changes are needed — just the env variable.

---

## CI/CD Overview

```
Push to main
    │
    ├─► GitHub Actions (if configured)
    │       └─► Run tests, lint
    │
    └─► Cloudflare Pages (auto-deploy)
            └─► Build → Deploy → Purge CDN cache
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Blank page on direct URL load | Missing `_redirects` | Confirm `frontend/public/_redirects` exists |
| API calls return 404 in dev | Backend not running | Start `uvicorn main:app --reload` |
| CORS errors in production | Backend missing CORS headers | Add `fastapi.middleware.cors.CORSMiddleware` |
| Build fails on Cloudflare | Node version mismatch | Set `NODE_VERSION=18` env var in Pages dashboard |
| Security header not applied | `_headers` path wrong | File must be in `frontend/public/`, not `frontend/` |
| Large bundle warning | No code splitting | `vite.config.js` `manualChunks` splits vendor bundle |

---

## Useful Links

- [Cloudflare Pages Docs](https://developers.cloudflare.com/pages/)
- [Vite Environment Variables](https://vitejs.dev/guide/env-and-mode.html)
- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)
- [Wrangler CLI](https://developers.cloudflare.com/workers/wrangler/)
