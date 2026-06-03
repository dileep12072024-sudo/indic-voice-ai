# Cloudflare Workers API Gateway

> **Phase 6** — TypeScript Worker + R2 + KV bindings  
> File: `workers/index.ts` · Config: `workers/wrangler.toml`

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Environment Bindings](#2-environment-bindings)
3. [Route Reference](#3-route-reference)
4. [Request / Response Shapes](#4-request--response-shapes)
5. [Local Development](#5-local-development)
6. [Deploying to Cloudflare](#6-deploying-to-cloudflare)
7. [R2 + KV Setup](#7-r2--kv-setup)
8. [CORS Policy](#8-cors-policy)
9. [Error Reference](#9-error-reference)
10. [Upgrade from Phase 5](#10-upgrade-from-phase-5)
11. [Security Hardening (Phase 7)](#11-security-hardening-phase-7)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        User Browser                                  │
│              React SPA (Cloudflare Pages)                            │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  HTTPS (fetch)
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│            Cloudflare Workers API Gateway (Phase 6)                  │
│                                                                      │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────────┐ │
│  │ GET /health  │  │ POST /generate │  │ GET|DELETE /job/:id      │ │
│  └──────────────┘  └───────┬────────┘  └──────────┬───────────────┘ │
│                            │                       │                 │
│  ┌─────────────────────────▼───────────────────────▼──────────────┐ │
│  │                   R2 (AUDIO_BUCKET)  KV (JOB_STORE)            │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                            │                                         │
└────────────────────────────┼─────────────────────────────────────────┘
                             │  HTTPS (fetch)
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   FastAPI Backend                                     │
│         (Railway / Render / Modal / localhost:8000)                  │
│                                                                      │
│  POST /generate → edge-tts → WAV → LocalStorageBackend              │
│  GET  /job/:id  → LocalJobManager                                    │
│  DELETE /job/:id → evict job + audio                                 │
└──────────────────────────────────────────────────────────────────────┘
```

### Data flow — POST /generate

```
Browser  →  Worker POST /generate
                │
                ├─ Validate JSON body (text, language, mode)
                ├─ Forward to FastAPI backend POST /generate
                ├─ Backend creates job (queued → processing → completed)
                ├─ Worker writes initial KV record (status=queued, TTL=8h)
                └─ Return 202 { job_id, status, poll_url } to browser

Browser polls GET /job/:id every 2 seconds
        Worker: KV lookup → if completed, attach output_url → return
        Worker: KV miss → fallback to FastAPI GET /job/:id
```

---

## 2. Environment Bindings

| Binding | Type | Purpose | How to set |
|---------|------|---------|------------|
| `AUDIO_BUCKET` | R2Bucket | Audio uploads + synthesised WAV storage | `wrangler r2 bucket create indic-voice-audio` |
| `JOB_STORE` | KVNamespace | Job state (status, metadata, TTL) | `wrangler kv namespace create indic-voice-jobs` |
| `BACKEND_URL` | `string` var | FastAPI backend base URL | `wrangler secret put BACKEND_URL` |
| `WORKER_VERSION` | `string` var | Semver string for health endpoint | `wrangler.toml` `[vars]` or CF dashboard |

### Setting secrets (never commit to git)

```bash
# Set BACKEND_URL as an encrypted secret
wrangler secret put BACKEND_URL
# Paste: https://your-backend.railway.app
```

---

## 3. Route Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Service liveness + binding diagnostics |
| `POST` | `/generate` | Optional API key (Phase 7) | Submit TTS job |
| `GET` | `/job/:id` | None | Poll job status |
| `DELETE` | `/job/:id` | None | GDPR erasure |
| `OPTIONS` | `*` | None | CORS preflight (204 No Content) |

---

## 4. Request / Response Shapes

### GET /health

**Response 200**
```json
{
  "status": "ok",
  "version": "6.0.0",
  "timestamp": "2026-06-03T12:00:00.000Z",
  "bindings": {
    "r2": true,
    "kv": true,
    "backend": true
  },
  "backend_url": "https://your-backend.railway.app"
}
```

```bash
curl https://indic-voice-workers.your-account.workers.dev/health
```

---

### POST /generate

**Request body** (JSON)
```json
{
  "text": "నమస్కారం, ఇది ఒక పరీక్ష.",
  "language": "te",
  "voice": "te-IN-ShrutiNeural",
  "mode": "standard"
}
```

| Field | Type | Required | Values |
|-------|------|----------|--------|
| `text` | string | ✅ | 1–5000 chars |
| `language` | string | ✅ | `te` \| `ta` \| `hi` \| `en` |
| `voice` | string | ❌ | Neural voice name (backend picks default if omitted) |
| `mode` | string | ❌ | `standard` (default) \| `clone` |

**Response 202**
```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "queued",
  "poll_url": "/job/3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message": "Job accepted. Poll the poll_url for status.",
  "worker_version": "6.0.0",
  "gateway": "cloudflare-workers"
}
```

**Validation error 422**
```json
{
  "error": "Unsupported language 'fr'. Supported: te, ta, hi, en",
  "code": "UNSUPPORTED_LANGUAGE",
  "status": 422,
  "timestamp": "2026-06-03T12:00:00.000Z"
}
```

```bash
curl -X POST https://indic-voice-workers.your-account.workers.dev/generate \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello world","language":"en"}'
```

---

### GET /job/:id

**Response 200 — queued / processing**
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "processing",
  "language": "en",
  "text": "Hello world",
  "mode": "standard",
  "output_key": null,
  "output_url": null,
  "error_message": null,
  "created_at": "2026-06-03T12:00:00.000Z",
  "updated_at": "2026-06-03T12:00:02.000Z",
  "ttl_seconds": 28800
}
```

**Response 200 — completed**
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "completed",
  "output_url": "/files/outputs/3fa85f64_output.wav",
  "output_key": "outputs/3fa85f64_output.wav",
  "error_message": null,
  "created_at": "2026-06-03T12:00:00.000Z",
  "updated_at": "2026-06-03T12:00:05.000Z",
  "ttl_seconds": 28800
}
```

**Response 404**
```json
{
  "error": "Job '3fa85f64' not found",
  "code": "JOB_NOT_FOUND",
  "status": 404,
  "timestamp": "2026-06-03T12:00:00.000Z"
}
```

```bash
curl https://indic-voice-workers.your-account.workers.dev/job/3fa85f64-5717-4562-b3fc-2c963f66afa6
```

---

### DELETE /job/:id

**Response 200**
```json
{
  "deleted": true,
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "message": "Job and associated audio files have been scheduled for deletion.",
  "timestamp": "2026-06-03T12:00:00.000Z"
}
```

The deletion is **async** (`ctx.waitUntil`): the 200 response is returned immediately, while R2 + KV + backend deletions run in the background.

```bash
curl -X DELETE https://indic-voice-workers.your-account.workers.dev/job/3fa85f64-5717-4562-b3fc-2c963f66afa6
```

---

## 5. Local Development

### Prerequisites

```bash
node --version   # >= 18.0.0
npm --version    # >= 9
```

### Install and run

```bash
# From repo root
cd workers

# Install dev dependencies
npm install

# Type-check (zero errors expected)
npm run type-check

# Start local Worker dev server on http://localhost:8787
npm run dev
```

The local dev server:
- Hot-reloads on file save
- Uses in-memory KV and R2 stubs (no real CF resources needed locally)
- Forwards requests to your local FastAPI on port 8000 (set via `BACKEND_URL`)

### Test locally

```bash
# Health check
curl http://localhost:8787/health

# Submit a TTS job
curl -X POST http://localhost:8787/generate \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello, this is a test.","language":"en"}'

# Poll job status (replace JOB_ID)
curl http://localhost:8787/job/JOB_ID

# Delete job (GDPR)
curl -X DELETE http://localhost:8787/job/JOB_ID
```

### Point to a local FastAPI backend

Add to your `.env` or prefix the command:

```bash
# In workers/.env (created by you, not committed)
BACKEND_URL=http://localhost:8000
```

Or override inline:

```bash
BACKEND_URL=http://localhost:8000 npm run dev
```

---

## 6. Deploying to Cloudflare

### Step-by-step checklist

```bash
# 1. Authenticate with Cloudflare
wrangler login

# 2. Create R2 bucket
wrangler r2 bucket create indic-voice-audio

# 3. Create KV namespaces
wrangler kv namespace create indic-voice-jobs
# → Copy the id into wrangler.toml → [[kv_namespaces]] id = "..."

wrangler kv namespace create indic-voice-jobs --preview
# → Copy the preview_id into wrangler.toml → [[kv_namespaces]] preview_id = "..."

# 4. Set backend URL as an encrypted secret (never in wrangler.toml)
wrangler secret put BACKEND_URL
# Paste your backend URL when prompted, e.g. https://your-app.railway.app

# 5. Run type-check one final time
npm run type-check

# 6. Deploy
npm run deploy
# → Output: https://indic-voice-workers.<account>.workers.dev
```

### Production deploy (custom domain / route)

```bash
# 1. Edit wrangler.toml → [env.production] → uncomment and fill in the route
# 2. Set BACKEND_URL for production environment
wrangler secret put BACKEND_URL --env production

# 3. Deploy to production
npm run deploy:prod
```

---

## 7. R2 + KV Setup

### R2 Bucket layout

```
indic-voice-audio/
├── uploads/
│   └── {job_id}_upload.wav       ← user reference audio (voice clone mode)
└── outputs/
    └── {job_id}_output.wav       ← synthesised audio output
```

**Lifecycle rule** (set in CF dashboard → R2 → indic-voice-audio → Settings → Lifecycle):
- Delete objects after **24 hours** to cap storage costs.

### KV Record schema

```typescript
interface JobRecord {
  id: string;                         // UUID
  status: "queued" | "processing" | "completed" | "failed";
  language: string;                   // te | ta | hi | en
  text: string;                       // truncated to 200 chars for KV storage
  voice: string;                      // neural voice name
  mode: "standard" | "clone";
  output_key: string | null;          // R2 object key
  output_url: string | null;          // URL for client audio download
  error_message: string | null;
  created_at: string;                 // ISO 8601
  updated_at: string;                 // ISO 8601
  ttl_seconds: number;                // default: 28800 (8 hours)
}
```

KV records expire automatically after `ttl_seconds`. No manual cleanup needed.

---

## 8. CORS Policy

All route responses include:

```
Access-Control-Allow-Origin:  *
Access-Control-Allow-Methods: GET, POST, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization, X-Api-Key
Access-Control-Max-Age:       86400
```

The `OPTIONS` preflight is handled before any route logic and returns `204 No Content`.

**Tighten for production** — replace `*` with your Pages domain in `workers/index.ts`:

```typescript
// In corsHeaders(), change:
"Access-Control-Allow-Origin": "https://your-app.pages.dev",
```

---

## 9. Error Reference

### Error response shape

```json
{
  "error": "Human-readable message",
  "code": "MACHINE_READABLE_CODE",
  "status": 422,
  "timestamp": "2026-06-03T12:00:00.000Z"
}
```

### Error codes

| Code | HTTP | Cause |
|------|------|-------|
| `INVALID_JSON` | 400 | Request body is not valid JSON |
| `MISSING_TEXT` | 422 | `text` field absent or empty |
| `MISSING_LANGUAGE` | 422 | `language` field absent |
| `UNSUPPORTED_LANGUAGE` | 422 | Language not in te / ta / hi / en |
| `TEXT_TOO_LONG` | 422 | `text` exceeds 5000 characters |
| `MISSING_JOB_ID` | 400 | Job ID path segment is empty |
| `JOB_NOT_FOUND` | 404 | Job not in KV or backend |
| `BACKEND_NOT_CONFIGURED` | 503 | `BACKEND_URL` env var not set |
| `BACKEND_UNREACHABLE` | 503 | Network error reaching backend |
| `BACKEND_ERROR` | 4xx/5xx | Backend returned error status |
| `BACKEND_PARSE_ERROR` | 502 | Backend returned non-JSON body |
| `KV_PARSE_ERROR` | 500 | Corrupted KV record |
| `NOT_FOUND` | 404 | Route path not matched |
| `INTERNAL_ERROR` | 500 | Unhandled Worker exception |

---

## 10. Upgrade from Phase 5

Phase 5 deployed the React SPA to Cloudflare Pages with `_headers` and `_redirects`. Phase 6 adds the Worker in `workers/`.

### What changes in the frontend

Update the frontend env (`.env` or CF Pages dashboard → Environment variables):

```bash
# Before (Phase 5 — direct to FastAPI)
VITE_API_URL=https://your-backend.railway.app

# After (Phase 6 — through Workers gateway)
VITE_API_URL=https://indic-voice-workers.your-account.workers.dev
VITE_WORKERS_URL=https://indic-voice-workers.your-account.workers.dev
```

All `fetch('/generate')` and `fetch('/job/...')` calls from `VoiceCloner.jsx` work without code changes. The Vite dev-server proxy already forwards those paths.

### Abstraction swap table

| Component | Phase 5 (local) | Phase 6 (Workers) |
|-----------|----------------|-------------------|
| Job storage | `LocalJobManager` (Python dict) | `JOB_STORE` KV Namespace |
| Audio storage | `LocalStorageBackend` (filesystem) | `AUDIO_BUCKET` R2 Bucket |
| API gateway | Vite proxy → FastAPI direct | CF Worker → FastAPI |
| CORS | FastAPI `CORSMiddleware` | Worker `corsHeaders()` helper |
| Routing | FastAPI `@app.post` | Worker `pathname.match()` |

---

## 11. Security Hardening (Phase 7)

Phase 6 ships with no authentication on routes. Phase 7 will add:

### API key gate

```typescript
// Add to fetch() before route dispatch:
const apiKey = request.headers.get("X-Api-Key");
if (apiKey !== env.API_SECRET_KEY) {
  return errorResponse("Unauthorized", "UNAUTHORIZED", 401);
}
```

Set the secret:
```bash
wrangler secret put API_SECRET_KEY
```

### KV-based rate limiting

```typescript
const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
const key = `ratelimit:${ip}`;
const count = parseInt((await env.JOB_STORE.get(key)) ?? "0");
if (count >= 10) {
  return errorResponse("Rate limit exceeded", "RATE_LIMITED", 429);
}
await env.JOB_STORE.put(key, String(count + 1), { expirationTtl: 60 });
```

### Cloudflare Access

For admin routes, add a [Cloudflare Access application](https://one.dash.cloudflare.com/) in front of the Worker route.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `wrangler dev` fails: `KV namespace not found` | `preview_id` not set in `wrangler.toml` | Run `wrangler kv namespace create indic-voice-jobs --preview` and paste the ID |
| `503 BACKEND_NOT_CONFIGURED` | `BACKEND_URL` var empty | Set via `wrangler secret put BACKEND_URL` or add to `[vars]` |
| `503 BACKEND_UNREACHABLE` | FastAPI not running or wrong URL | Start FastAPI: `uvicorn backend.main:app --reload` |
| `422 UNSUPPORTED_LANGUAGE` | Frontend sending unsupported lang code | Supported: `te`, `ta`, `hi`, `en` |
| `tsc --noEmit` error | TypeScript strict mode violation | Check `workers/index.ts` for unused vars or type mismatches |
| CORS error in browser | Missing `Access-Control-Allow-Origin` header | Verify all responses go through `jsonResponse()` / `errorResponse()` |
| `wrangler deploy` fails: R2 bucket not found | Bucket not created yet | Run `wrangler r2 bucket create indic-voice-audio` |
| Audio 404 after job completes | `output_url` points to backend `/files/*` but Worker isn't proxying it | Confirm FastAPI is reachable at `BACKEND_URL` for file serving |

---

*Phase 6 implementation — 2026-06-03*
