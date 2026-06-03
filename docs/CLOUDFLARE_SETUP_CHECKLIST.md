# Cloudflare Setup Checklist — Indic Voice AI

> **Scope:** Phase 6 (Workers + R2 + KV gateway) and Phase 7 (API key auth + per-IP rate limiting).  
> Work through every section **in order**. Tick each box as you complete it.  
> No application code changes are needed — this is purely infrastructure setup.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Cloudflare Account & CLI Authentication](#2-cloudflare-account--cli-authentication)
3. [R2 Bucket — `indic-voice-audio`](#3-r2-bucket--indic-voice-audio)
4. [KV Namespaces](#4-kv-namespaces)
5. [Wire KV IDs into `wrangler.toml`](#5-wire-kv-ids-into-wranglertoml)
6. [Encrypted Secrets](#6-encrypted-secrets)
7. [Local Development Smoke Test](#7-local-development-smoke-test)
8. [Deploy to Cloudflare (Staging / Default)](#8-deploy-to-cloudflare-staging--default)
9. [Production Environment](#9-production-environment)
10. [Cloudflare Pages — Frontend Environment Variables](#10-cloudflare-pages--frontend-environment-variables)
11. [CORS Lockdown (Phase 7)](#11-cors-lockdown-phase-7)
12. [R2 Lifecycle Rule](#12-r2-lifecycle-rule)
13. [Post-Deploy Verification](#13-post-deploy-verification)
14. [Rollback Procedure](#14-rollback-procedure)
15. [Quick-Reference: All Commands](#15-quick-reference-all-commands)

---

## 1. Prerequisites

| Requirement | Command to verify | Expected output |
|-------------|-------------------|-----------------|
| Node.js ≥ 18 | `node --version` | `v18.x.x` or higher |
| npm ≥ 9 | `npm --version` | `9.x.x` or higher |
| Wrangler CLI ≥ 3 | `wrangler --version` | `⛅️ wrangler 3.x.x` |
| Cloudflare account | — | Active account at [dash.cloudflare.com](https://dash.cloudflare.com) |
| Backend deployed | `curl $BACKEND_URL/health` | `200 OK` JSON |

Install / upgrade Wrangler if needed:

```bash
npm install -g wrangler@latest
```

- [ ] Node ≥ 18 confirmed
- [ ] npm ≥ 9 confirmed
- [ ] Wrangler ≥ 3 installed
- [ ] Cloudflare account active
- [ ] Backend URL known and reachable

---

## 2. Cloudflare Account & CLI Authentication

```bash
# Opens a browser tab — log in and authorise Wrangler
wrangler login

# Verify you are authenticated and note your Account ID
wrangler whoami
```

Copy the **Account ID** shown by `wrangler whoami`. You will need it when configuring Cloudflare Pages environment variables and the CF dashboard.

- [ ] `wrangler login` completed without errors
- [ ] Account ID noted: `______________________________`

---

## 3. R2 Bucket — `indic-voice-audio`

```bash
wrangler r2 bucket create indic-voice-audio
```

Expected output:

```
Created bucket 'indic-voice-audio'
```

> **Note:** If the bucket already exists you will see an error — that is fine, continue.

- [ ] Bucket `indic-voice-audio` created (or already existed)

### Verify bucket is visible

```bash
wrangler r2 bucket list
```

Confirm `indic-voice-audio` appears in the output.

- [ ] Bucket visible in `wrangler r2 bucket list`

---

## 4. KV Namespaces

Two separate KV namespaces are required — one for job state, one for rate-limit counters.

### 4a. Job Store (`JOB_STORE`)

```bash
# Production namespace
wrangler kv namespace create indic-voice-jobs

# Preview namespace (used by `wrangler dev`)
wrangler kv namespace create indic-voice-jobs --preview
```

Each command prints output like:

```
{ binding = "JOB_STORE", id = "abc123..." }
```

Record both IDs:

| Namespace | ID |
|-----------|-----|
| `indic-voice-jobs` (production) | `______________________________` |
| `indic-voice-jobs` (preview) | `______________________________` |

- [ ] `indic-voice-jobs` production namespace created — ID recorded
- [ ] `indic-voice-jobs` preview namespace created — ID recorded

### 4b. Rate-Limit Store (`RATE_LIMITER`)

```bash
# Production namespace
wrangler kv namespace create indic-voice-ratelimit

# Preview namespace
wrangler kv namespace create indic-voice-ratelimit --preview
```

Record both IDs:

| Namespace | ID |
|-----------|-----|
| `indic-voice-ratelimit` (production) | `______________________________` |
| `indic-voice-ratelimit` (preview) | `______________________________` |

- [ ] `indic-voice-ratelimit` production namespace created — ID recorded
- [ ] `indic-voice-ratelimit` preview namespace created — ID recorded

---

## 5. Wire KV IDs into `wrangler.toml`

Open `workers/wrangler.toml`. Replace the four placeholder strings with the real IDs collected in Step 4.

```toml
# Job Store
[[kv_namespaces]]
binding    = "JOB_STORE"
id         = "REPLACE_WITH_KV_JOB_STORE_ID"          ← paste production ID here
preview_id = "REPLACE_WITH_KV_JOB_STORE_PREVIEW_ID"  ← paste preview ID here

# Rate-Limit Store
[[kv_namespaces]]
binding    = "RATE_LIMITER"
id         = "REPLACE_WITH_KV_RATELIMIT_ID"           ← paste production ID here
preview_id = "REPLACE_WITH_KV_RATELIMIT_PREVIEW_ID"   ← paste preview ID here
```

**Commit this change** — the IDs are not secret:

```bash
git add workers/wrangler.toml
git commit -m "chore: wire real KV namespace IDs into wrangler.toml"
git push
```

- [ ] All four `REPLACE_WITH_*` placeholders replaced with real IDs
- [ ] `wrangler.toml` committed and pushed

---

## 6. Encrypted Secrets

Secrets are set via the Wrangler CLI and stored encrypted in Cloudflare. **Never** put them in `wrangler.toml` or commit them to git.

### 6a. `BACKEND_URL`

```bash
wrangler secret put BACKEND_URL
# Paste when prompted, e.g.:
#   https://your-app.railway.app
```

- [ ] `BACKEND_URL` secret set (staging / default environment)

### 6b. `WORKER_API_KEY`

Generate a strong random key first:

```bash
openssl rand -hex 32
```

Then set it as a Worker secret:

```bash
wrangler secret put WORKER_API_KEY
# Paste the hex string when prompted
```

Store the same value somewhere secure (e.g. your password manager) — you will need it in Step 10 for the Pages frontend environment variable `VITE_WORKER_API_KEY`.

- [ ] `WORKER_API_KEY` generated (32-byte hex)
- [ ] `WORKER_API_KEY` secret set on Worker
- [ ] `WORKER_API_KEY` value saved securely

### 6c. Verify secrets are registered

```bash
wrangler secret list
```

Both `BACKEND_URL` and `WORKER_API_KEY` should appear in the output.

- [ ] Both secrets confirmed visible in `wrangler secret list`

---

## 7. Local Development Smoke Test

```bash
cd workers
npm install          # install dev deps (first time only)
npm run type-check   # must exit 0 — zero TypeScript errors
npm run dev          # starts on http://localhost:8787
```

In a separate terminal, run the following checks:

```bash
# Health check — must show bindings.r2, bindings.kv, bindings.backend all true
curl http://localhost:8787/health | jq .

# Submit a TTS job
curl -X POST http://localhost:8787/generate \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: <your WORKER_API_KEY>' \
  -d '{"text":"Hello, this is a test.","language":"en"}' | jq .

# Poll job status (replace <JOB_ID> with the id returned above)
curl http://localhost:8787/job/<JOB_ID> | jq .

# CORS preflight
curl -X OPTIONS http://localhost:8787/generate \
  -H 'Origin: http://localhost:5173' \
  -H 'Access-Control-Request-Method: POST' \
  -v 2>&1 | grep -E "< HTTP|Access-Control"
```

Expected results:

| Check | Expected |
|-------|----------|
| `/health` | `"status":"ok"`, all bindings `true` |
| `POST /generate` | HTTP 202, `job_id` in body |
| `GET /job/:id` | HTTP 200, status field present |
| OPTIONS preflight | HTTP 204, CORS headers present |

- [ ] `npm run type-check` exits 0
- [ ] `/health` returns all bindings `true`
- [ ] `POST /generate` returns 202 with `job_id`
- [ ] `GET /job/:id` returns 200
- [ ] OPTIONS preflight returns 204 with CORS headers

---

## 8. Deploy to Cloudflare (Staging / Default)

```bash
cd workers

# Final type-check before deploy
npm run type-check

# Deploy
npm run deploy
```

Expected output ends with:

```
✨  Built successfully
✨  Successfully published your Worker

https://indic-voice-workers.<your-account>.workers.dev
```

Record the deployed Worker URL: `______________________________`

- [ ] `npm run deploy` succeeded
- [ ] Worker URL noted

### Smoke-test the deployed Worker

```bash
export WORKER_URL="https://indic-voice-workers.<your-account>.workers.dev"

curl $WORKER_URL/health | jq .

curl -X POST $WORKER_URL/generate \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: <your WORKER_API_KEY>' \
  -d '{"text":"Hello cloud.","language":"en"}' | jq .
```

- [ ] Deployed `/health` returns `"status":"ok"`
- [ ] Deployed `POST /generate` returns 202

---

## 9. Production Environment

Only needed if you have a custom domain or want an isolated production config.

```bash
# Set secrets for the production environment
wrangler secret put BACKEND_URL     --env production
wrangler secret put WORKER_API_KEY  --env production

# Deploy to production
npm run deploy:prod
```

If using a custom domain, uncomment and fill in the `route` line in `workers/wrangler.toml`:

```toml
[env.production]
# route = { pattern = "api.your-domain.com/*", zone_name = "your-domain.com" }
```

- [ ] Production secrets set (if applicable)
- [ ] Production `route` configured in `wrangler.toml` (if custom domain)
- [ ] `npm run deploy:prod` succeeded (if applicable)

---

## 10. Cloudflare Pages — Frontend Environment Variables

Go to **Cloudflare Dashboard → Pages → indic-voice (your Pages project) → Settings → Environment Variables**.

Add the following variables for the **Production** environment (repeat for Preview if needed):

| Variable | Value | Notes |
|----------|-------|-------|
| `VITE_API_URL` | `https://indic-voice-workers.<account>.workers.dev` | Points frontend at the Worker gateway (not FastAPI directly) |
| `VITE_WORKERS_URL` | `https://indic-voice-workers.<account>.workers.dev` | Same URL — used by VoiceCloner.jsx |
| `VITE_WORKER_API_KEY` | `<your 32-byte hex key from Step 6b>` | **Mark as Secret / Encrypted** |

> ⚠️ `VITE_WORKER_API_KEY` is embedded in the compiled JS bundle. Mark it **encrypted** in the dashboard and rotate it if it ever leaks. For tighter security, proxy the key server-side instead of exposing it in the client.

After saving, **trigger a new Pages deployment** (push a commit or click "Retry deployment") so the new env vars are baked into the Vite build.

- [ ] `VITE_API_URL` set in Pages dashboard (Production)
- [ ] `VITE_WORKERS_URL` set in Pages dashboard (Production)
- [ ] `VITE_WORKER_API_KEY` set as encrypted secret in Pages dashboard (Production)
- [ ] New Pages deployment triggered and successful

---

## 11. CORS Lockdown (Phase 7)

After the Pages deployment succeeds and you have the final Pages URL, restrict CORS to that domain only.

### Option A — `wrangler.toml` (recommended, version-controlled)

In `workers/wrangler.toml`, under `[env.production.vars]`:

```toml
[env.production.vars]
ALLOWED_ORIGINS = "https://indic-voice.pages.dev"
# If using a custom domain, add it comma-separated:
# ALLOWED_ORIGINS = "https://indic-voice.pages.dev,https://your-domain.com"
```

Redeploy:

```bash
npm run deploy:prod
```

### Option B — Cloudflare Dashboard

Go to **Workers → indic-voice-workers → Settings → Variables** and set `ALLOWED_ORIGINS` to your Pages domain.

> Keep `ALLOWED_ORIGINS = "*"` **only** in local dev / staging. Never in production.

- [ ] `ALLOWED_ORIGINS` restricted to Pages domain in production
- [ ] Redeployed after changing `ALLOWED_ORIGINS`

---

## 12. R2 Lifecycle Rule

Audio files are auto-deleted after 24 hours to control storage costs.

1. Go to **Cloudflare Dashboard → R2 → indic-voice-audio → Settings → Lifecycle rules**
2. Click **Add rule**
3. Configure:
   - **Rule name:** `expire-audio-24h`
   - **Prefix filter:** *(leave empty — applies to all objects)*
   - **Action:** Delete objects
   - **Days after object creation:** `1`
4. Save the rule

- [ ] R2 lifecycle rule `expire-audio-24h` created (delete after 1 day)

### Verify rule is active

Go to **R2 → indic-voice-audio → Settings → Lifecycle rules** and confirm the rule appears with status **Active**.

- [ ] Lifecycle rule confirmed Active in dashboard

---

## 13. Post-Deploy Verification

Run this full suite against the **production** Worker URL after all steps above are complete.

```bash
export WORKER_URL="https://indic-voice-workers.<your-account>.workers.dev"
export API_KEY="<your WORKER_API_KEY>"

echo "=== 1. Health check ==="
curl -s $WORKER_URL/health | jq '{status,version,bindings}'

echo "=== 2. Submit TTS job (Telugu) ==="
JOB=$(curl -s -X POST $WORKER_URL/generate \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: $API_KEY" \
  -d '{"text":"నమస్కారం","language":"te"}')
echo $JOB | jq .
JOB_ID=$(echo $JOB | jq -r '.job_id')

echo "=== 3. Poll job status ==="
sleep 3
curl -s $WORKER_URL/job/$JOB_ID | jq '{id,status,output_url}'

echo "=== 4. Rate-limit check (20 rapid requests) ==="
for i in $(seq 1 21); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" $WORKER_URL/health)
  echo "Request $i: HTTP $STATUS"
done
# Request 21 should return 429

echo "=== 5. Unauthorized request (missing API key) ==="
curl -s -X POST $WORKER_URL/generate \
  -H "Content-Type: application/json" \
  -d '{"text":"test","language":"en"}' | jq '{error,code,status}'
# Expect: 401 UNAUTHORIZED

echo "=== 6. GDPR delete ==="
curl -s -X DELETE $WORKER_URL/job/$JOB_ID | jq .

echo "=== 7. CORS header present ==="
curl -s -I $WORKER_URL/health | grep -i "access-control"
```

Expected outcomes:

| Check | Pass condition |
|-------|---------------|
| Health | `"status":"ok"`, all bindings `true` |
| Submit job | HTTP 202, `job_id` present |
| Poll status | HTTP 200, `status` field in `queued\|processing\|completed\|failed` |
| Rate limit | Request 21 returns HTTP 429 |
| No API key | HTTP 401 with `"code":"UNAUTHORIZED"` |
| GDPR delete | HTTP 200, `"deleted":true` |
| CORS header | `Access-Control-Allow-Origin` header present |

- [ ] Health check passes
- [ ] TTS job submitted successfully
- [ ] Job polling works
- [ ] Rate limiting triggers on request 21
- [ ] Unauthenticated request rejected with 401
- [ ] GDPR delete returns 200
- [ ] CORS header present on responses

---

## 14. Rollback Procedure

If a deployment breaks production:

```bash
# List recent deployments
wrangler deployments list

# Roll back to the previous known-good deployment
wrangler rollback <deployment-id>
```

To find the deployment ID of the last good state, check the Cloudflare Dashboard under **Workers → indic-voice-workers → Deployments**.

- [ ] Rollback procedure understood and tested in staging (recommended)

---

## 15. Quick-Reference: All Commands

```bash
# ── Auth ───────────────────────────────────────────────────────────────────
wrangler login
wrangler whoami

# ── R2 ─────────────────────────────────────────────────────────────────────
wrangler r2 bucket create indic-voice-audio
wrangler r2 bucket list

# ── KV ─────────────────────────────────────────────────────────────────────
wrangler kv namespace create indic-voice-jobs
wrangler kv namespace create indic-voice-jobs --preview
wrangler kv namespace create indic-voice-ratelimit
wrangler kv namespace create indic-voice-ratelimit --preview
wrangler kv namespace list

# ── Secrets ─────────────────────────────────────────────────────────────────
openssl rand -hex 32                          # generate WORKER_API_KEY
wrangler secret put BACKEND_URL
wrangler secret put WORKER_API_KEY
wrangler secret put BACKEND_URL    --env production
wrangler secret put WORKER_API_KEY --env production
wrangler secret list

# ── Local dev ────────────────────────────────────────────────────────────────
cd workers && npm install
npm run type-check
npm run dev                                   # http://localhost:8787

# ── Deploy ───────────────────────────────────────────────────────────────────
npm run deploy                                # staging / default
npm run deploy:prod                           # production env

# ── Rollback ─────────────────────────────────────────────────────────────────
wrangler deployments list
wrangler rollback <deployment-id>
```

---

## Completion Sign-Off

| Step | Completed by | Date |
|------|-------------|------|
| Prerequisites | | |
| CF Auth | | |
| R2 Bucket | | |
| KV Namespaces | | |
| wrangler.toml IDs | | |
| Encrypted Secrets | | |
| Local smoke test | | |
| Staging deploy | | |
| Production deploy | | |
| Pages env vars | | |
| CORS lockdown | | |
| R2 lifecycle rule | | |
| Post-deploy verification | | |

---

*Phase 6 + 7 — Generated 2026-06-03 · See also: [`docs/CLOUDFLARE_WORKERS.md`](./CLOUDFLARE_WORKERS.md) · [`docs/SECURITY.md`](./SECURITY.md) · [`docs/DEPLOYMENT.md`](./DEPLOYMENT.md)*
