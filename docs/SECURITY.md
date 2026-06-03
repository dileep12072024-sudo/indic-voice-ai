# Indic Voice AI — Security Reference

> **Phase 7** — Cloudflare Workers security hardening  
> Applies to: `workers/index.ts` v7.0.0 · `workers/wrangler.toml` Phase 7

---

## Table of Contents

1. [Threat model](#1-threat-model)
2. [API key authentication](#2-api-key-authentication)
3. [Per-IP rate limiting](#3-per-ip-rate-limiting)
4. [Security response headers](#4-security-response-headers)
5. [CORS policy](#5-cors-policy)
6. [Request tracing (X-Request-Id)](#6-request-tracing-x-request-id)
7. [Secret management](#7-secret-management)
8. [Cloudflare Access (zero-trust overlay)](#8-cloudflare-access-zero-trust-overlay)
9. [Frontend integration](#9-frontend-integration)
10. [Local development](#10-local-development)
11. [Operational runbook](#11-operational-runbook)
12. [Error reference](#12-error-reference)

---

## 1. Threat model

| Threat | Mitigation |
|--------|-----------|
| Unauthenticated TTS abuse | `WORKER_API_KEY` header check (§2) |
| Brute-force / credential stuffing | Constant-time key comparison (§2) |
| DDoS / high-volume scraping | Per-IP rate limiting via KV (§3) |
| XSS / clickjacking via API response | `CSP`, `X-Frame-Options`, `X-Content-Type-Options` (§4) |
| CORS wildcard abuse | Origin allowlist via `ALLOWED_ORIGINS` (§5) |
| Replay / log correlation | `X-Request-Id` on every response (§6) |
| Secret leakage via env vars | Wrangler secrets (encrypted at rest, never in toml) (§7) |
| Admin endpoint exposure | `/health` is auth-exempt; all mutations require auth |
| Backend credential exposure | Worker proxies — frontend never contacts FastAPI directly |
| GDPR data retention | `DELETE /job/:id` erases KV + R2 + backend atomically |

### Out of scope (Phase 7)

- Per-user quotas (requires auth identity store)
- JWT / OAuth (Phase 8+)
- WAF / Bot Management rules (configure in CF dashboard)
- DDoS L3/L4 (handled transparently by Cloudflare network)

---

## 2. API key authentication

### How it works

Every request to `/generate`, `/job/:id` (GET), and `/job/:id` (DELETE) must carry a valid API key.  
`GET /health` and `OPTIONS *` are **exempt** — they must be reachable by uptime monitors and browsers without credentials.

```
Client → Worker
  Request headers:
    X-Api-Key: <your-key>
    Content-Type: application/json
```

Alternatively, the key may be sent as a Bearer token:

```
Authorization: Bearer <your-key>
```

The Worker reads `X-Api-Key` first; falls back to stripping the `Bearer ` prefix from `Authorization`.

### Validation

Key comparison uses a **constant-time XOR loop** — prevents timing-oracle attacks that would allow an attacker to infer the correct key one character at a time.

```typescript
function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}
```

### Error responses

| Scenario | Status | Code |
|----------|--------|------|
| Key header missing | 401 | `MISSING_API_KEY` |
| Key present but wrong | 403 | `INVALID_API_KEY` |

### Setting the key

```bash
# Development
wrangler secret put WORKER_API_KEY
# Enter: any strong random string (32+ hex chars recommended)

# Production
wrangler secret put WORKER_API_KEY --env production
```

Generate a strong key locally:

```bash
openssl rand -hex 32
# Example output: a3f8c2...  (use this as your key)
```

### Auth bypass (local dev only)

If `WORKER_API_KEY` is **not set**, the Worker logs a warning and bypasses auth:

```
[Worker][req_...] WORKER_API_KEY not set — auth is DISABLED.
```

This allows `wrangler dev` without any key configuration, but will be **visible in CF Tail logs** if accidentally deployed to production.

---

## 3. Per-IP rate limiting

### Algorithm

Fixed-width sliding window keyed on `(client_ip, window_index)`.

```
window_index = floor(now_ms / window_ms)
kv_key       = "rl:{sanitised_ip}:{window_index}"
```

Each KV entry stores `{ count, window_start }` and expires automatically after `window_seconds + 10 s`.

### Default limits

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_REQUESTS` | `20` | Max requests per IP per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Window size in seconds |

At defaults: **20 req / 60 s per IP** (≈ 1200 req/hr).

### Response headers

Every authenticated response includes:

```
X-RateLimit-Limit: 20
X-RateLimit-Remaining: 17
X-RateLimit-Reset: 1717401660   ← Unix timestamp (seconds)
X-Request-Id: req_lz4abc_xy9z
```

When the limit is exceeded:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 43
X-RateLimit-Remaining: 0
```

### Tuning

Override in `workers/wrangler.toml` `[vars]` or the CF dashboard:

```toml
[vars]
RATE_LIMIT_REQUESTS       = "50"   # more generous for authenticated SaaS
RATE_LIMIT_WINDOW_SECONDS = "60"
```

For stricter per-route limits (e.g., `/generate` only), extend `checkRateLimit()` to accept a route prefix and use separate KV key namespaces per route.

### KV namespace setup

```bash
# Create rate-limit namespace (separate from job store)
wrangler kv namespace create indic-voice-ratelimit
wrangler kv namespace create indic-voice-ratelimit --preview

# Paste the returned IDs into workers/wrangler.toml:
# [[kv_namespaces]]
# binding    = "RATE_LIMITER"
# id         = "<prod-id>"
# preview_id = "<preview-id>"
```

### Disabling rate limiting

If `RATE_LIMITER` KV is not bound, the Worker logs a warning and allows all traffic. Use this during initial local dev.

---

## 4. Security response headers

Every non-preflight response (including 4xx / 5xx errors) includes:

| Header | Value | Purpose |
|--------|-------|---------|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` | Force HTTPS for 2 years; eligible for HSTS preload list |
| `X-Content-Type-Options` | `nosniff` | Prevent MIME-sniffing attacks |
| `X-Frame-Options` | `DENY` | Block clickjacking via iframe embedding |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limit referrer leakage |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` | Deny powerful browser APIs |
| `Content-Security-Policy` | `default-src 'none'; script-src 'none'; frame-ancestors 'none'` | API-only: no scripts, no embedding |
| `Cache-Control` | `no-store` | Prevent caching of API responses (may contain job data) |

### Preflight (OPTIONS)

CORS preflight responses do **not** include security headers (unnecessary for 204 no-body responses and avoids header conflicts with browser preflight handling).

---

## 5. CORS policy

### Configuration

Set `ALLOWED_ORIGINS` in `wrangler.toml` `[vars]`:

```toml
# Development — allow all origins
ALLOWED_ORIGINS = "*"

# Production — restrict to your CF Pages domain
ALLOWED_ORIGINS = "https://indic-voice.pages.dev"

# Multiple origins
ALLOWED_ORIGINS = "https://indic-voice.pages.dev,https://your-custom-domain.com"
```

### How it works

The Worker reflects the **exact request `Origin`** back in `Access-Control-Allow-Origin` (rather than echoing `*`), which:

1. Enables `credentials: 'include'` if needed in the future
2. Prevents other origins from being reflected
3. Adds `Vary: Origin` so CDN caches are keyed per-origin

If the request origin is **not in the allowlist**, no `Access-Control-Allow-Origin` header is sent — browsers block the response automatically.

### Exposed headers

```
Access-Control-Expose-Headers: X-Request-Id, X-RateLimit-Remaining
```

These are safe to expose to browser scripts for request correlation and UI throttle warnings.

### Tightening for production

```bash
# Override via wrangler.toml [env.production.vars] — already set to Pages domain
# Or set as a dashboard variable for runtime changes without redeploy
```

---

## 6. Request tracing (X-Request-Id)

Every response carries a unique `X-Request-Id`:

```
X-Request-Id: req_lz4abc_xy9z
```

Format: `req_{timestamp_base36}_{6_random_chars}`

### Uses

- **Log correlation**: CF Workers Tail logs print the request ID at each handler step
- **Client retry logic**: store the request ID with failed requests to aid support debugging
- **Backend propagation**: the Worker forwards `X-Request-Id` to FastAPI as a request header

### Viewing logs

```bash
# Stream live Worker logs
wrangler tail indic-voice-workers

# Filter by request ID (once you have one)
wrangler tail indic-voice-workers --format pretty | grep "req_lz4abc_xy9z"
```

---

## 7. Secret management

### Secrets vs vars

| Type | Storage | Visibility | Use for |
|------|---------|-----------|---------|
| `[vars]` in toml | Plaintext in repo | Readable in dashboard | Non-sensitive config: version, rate limits, CORS origins |
| `wrangler secret put` | Encrypted at rest | Never exposed in dashboard or logs | API keys, backend URLs, tokens |

### All secrets for this project

| Secret | Set via | Purpose |
|--------|---------|---------|
| `BACKEND_URL` | `wrangler secret put BACKEND_URL` | FastAPI backend base URL |
| `WORKER_API_KEY` | `wrangler secret put WORKER_API_KEY` | Gateway authentication key |

### Secret rotation procedure

1. Generate a new key: `openssl rand -hex 32`
2. Update the Worker secret: `wrangler secret put WORKER_API_KEY`
3. Update the frontend env var (`VITE_WORKER_API_KEY`) and redeploy CF Pages
4. Verify `/health` still returns `auth_enabled: true`
5. Test one authenticated request with the new key
6. Decommission the old key (it is replaced immediately on `wrangler secret put`)

### Never do

- Commit real secret values to `workers/wrangler.toml` or `.env`
- Log the API key in `console.log` statements
- Return the key in any API response
- Use the same key for development and production

---

## 8. Cloudflare Access (zero-trust overlay)

Cloudflare Access can add a **second layer of authentication** in front of the Worker — useful for admin dashboards, staging environments, or team-internal tools.

### When to use

- Protecting staging/preview deployments
- Adding SSO (Google, GitHub, Okta) without writing auth code
- Audit-log trail for all requests (Access logs in CF dashboard)

### Setup steps

1. **Dashboard** → Zero Trust → Access → Applications → Add an application
2. Choose **Self-hosted**
3. Set domain: `indic-voice-workers.your-subdomain.workers.dev` (or your custom domain)
4. Configure an identity provider (GitHub, Google, etc.)
5. Create a policy (e.g., allow emails matching `@your-org.com`)
6. Save — Access will now intercept requests and issue a signed JWT

### Verifying the CF Access JWT in the Worker (optional)

If you need the Worker to verify the identity from Access:

```typescript
// Pseudocode — add to Env and checkApiKey
const cfJwt = request.headers.get("Cf-Access-Jwt-Assertion");
// Verify against CF Access public keys (JWKS endpoint)
// See: https://developers.cloudflare.com/cloudflare-one/identity/authorization-cookie/validating-json/
```

For most use cases, Access handles auth entirely at the edge — the Worker sees only valid, pre-authenticated requests.

---

## 9. Frontend integration

### Sending the API key from the browser

In `frontend/src/components/VoiceCloner.jsx`:

```javascript
const WORKERS_URL = import.meta.env.VITE_WORKERS_URL;
const API_KEY     = import.meta.env.VITE_WORKER_API_KEY;

const response = await fetch(`${WORKERS_URL}/generate`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-Api-Key': API_KEY,        // ← Phase 7
  },
  body: JSON.stringify({ text, language, voice, mode }),
});
```

### Environment variables

In your **Cloudflare Pages** dashboard → Settings → Environment variables:

| Variable | Value | Environment |
|----------|-------|-------------|
| `VITE_WORKERS_URL` | `https://indic-voice-workers.your-subdomain.workers.dev` | Production |
| `VITE_WORKER_API_KEY` | `<your-key>` | Production |

> ⚠️ **Security note**: `VITE_*` variables are embedded in the built JS bundle — they are visible to anyone who inspects the page source. This is acceptable for a shared API key (anyone using the app would need it), but **do not use** this pattern for per-user secrets or admin credentials. Use backend sessions or CF Access for that.

### Handling 429 in the UI

```javascript
if (response.status === 429) {
  const retryAfter = response.headers.get('Retry-After') ?? '60';
  showError(`Rate limit reached. Please wait ${retryAfter} seconds.`);
  return;
}
```

---

## 10. Local development

### Without API key (simplest)

```bash
cd workers
npm install
wrangler dev          # WORKER_API_KEY not set → auth bypassed, warning in logs
```

```bash
# Test health (no key needed)
curl http://localhost:8787/health

# Test generate (no key needed when bypassed)
curl -X POST http://localhost:8787/generate \
  -H "Content-Type: application/json" \
  -d '{"text":"నమస్కారం","language":"te"}'
```

### With API key (mirrors production)

```bash
# Set a local dev key
wrangler secret put WORKER_API_KEY    # Enter: dev-only-key-not-for-prod

wrangler dev
```

```bash
# All authenticated requests now require the key
curl -X POST http://localhost:8787/generate \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: dev-only-key-not-for-prod" \
  -d '{"text":"நமஸ்காரம்","language":"ta"}'
```

### Full local stack

```bash
# Terminal 1 — FastAPI backend
cd backend && uvicorn main:app --reload --port 8000

# Terminal 2 — Cloudflare Worker (proxies to localhost:8000)
cd workers
echo "http://localhost:8000" | wrangler secret put BACKEND_URL
wrangler dev --port 8787

# Terminal 3 — Vite frontend
cd frontend
VITE_WORKERS_URL=http://localhost:8787 \
VITE_WORKER_API_KEY=dev-only-key-not-for-prod \
npm run dev
```

---

## 11. Operational runbook

### Deploy to production

```bash
cd workers

# 1. Ensure secrets are set
wrangler secret list --env production
# Should show: BACKEND_URL, WORKER_API_KEY

# 2. Ensure KV namespace IDs are filled in wrangler.toml
#    JOB_STORE id, RATE_LIMITER id

# 3. Type-check
npm run type-check   # must exit 0

# 4. Deploy
npm run deploy:prod

# 5. Smoke test
curl https://indic-voice-workers.your-subdomain.workers.dev/health
# Expected: { "status": "ok", "bindings": { "auth_enabled": true, ... } }

# 6. Authenticated test
MYKEY="<your-key>"
curl -X POST https://indic-voice-workers.your-subdomain.workers.dev/generate \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: $MYKEY" \
  -d '{"text":"Hello","language":"en"}'
```

### Monitor for abuse

```bash
# Stream Worker logs
wrangler tail indic-voice-workers --env production

# Watch for rate limit warnings
wrangler tail indic-voice-workers --env production --format pretty \
  | grep "Rate limit exceeded"

# Watch for invalid key attempts
wrangler tail indic-voice-workers --env production --format pretty \
  | grep "Invalid API key"
```

### Inspect rate-limit counters (debugging)

```bash
# List all rate-limit KV keys for a specific IP
wrangler kv key list --binding RATE_LIMITER --env production \
  | grep "rl:1.2.3.4"

# Read a specific counter
wrangler kv key get "rl:1.2.3.4:28623810" --binding RATE_LIMITER --env production
```

### Temporarily lift rate limit

Increase `RATE_LIMIT_REQUESTS` in the CF dashboard vars temporarily. For a permanent exemption, add an IP allowlist check before `checkRateLimit()` in `index.ts`.

### Rotate the API key (zero-downtime)

```bash
# 1. Generate new key
NEW_KEY=$(openssl rand -hex 32)
echo "New key: $NEW_KEY"

# 2. Update Worker secret (takes effect on next request)
echo "$NEW_KEY" | wrangler secret put WORKER_API_KEY --env production

# 3. Update frontend Pages env var in CF dashboard
#    Settings → Environment Variables → VITE_WORKER_API_KEY → Edit

# 4. Trigger a Pages deployment to pick up the new var
#    Push a trivial commit or use the "Retry deploy" button

# 5. Verify
curl https://...workers.dev/health | jq .bindings.auth_enabled
# → true
```

---

## 12. Error reference

| HTTP | Code | Cause | Client action |
|------|------|-------|--------------|
| 401 | `MISSING_API_KEY` | No `X-Api-Key` or `Authorization` header | Add the header |
| 403 | `INVALID_API_KEY` | Key present but wrong | Check key value; rotate if compromised |
| 404 | `NOT_FOUND` | Route does not exist | Check path spelling |
| 404 | `JOB_NOT_FOUND` | Job ID not in KV or backend | Job may have expired (8 h TTL) |
| 405 | `METHOD_NOT_ALLOWED` | Wrong HTTP method for route | Check `Allow` response header |
| 422 | `MISSING_TEXT` | `text` field empty or missing | Provide non-empty text |
| 422 | `MISSING_LANGUAGE` | `language` field missing | Provide `te`, `ta`, `hi`, or `en` |
| 422 | `UNSUPPORTED_LANGUAGE` | Language code not recognised | Use a supported code |
| 422 | `TEXT_TOO_LONG` | `text` > 5000 chars | Truncate input |
| 422 | `INVALID_JSON` | Request body is not valid JSON | Fix request body |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests from this IP | Wait `Retry-After` seconds |
| 500 | `INTERNAL_ERROR` | Unhandled Worker exception | Report with `X-Request-Id` |
| 500 | `KV_PARSE_ERROR` | Corrupt record in KV | Delete job and retry |
| 502 | `BACKEND_PARSE_ERROR` | Backend returned non-JSON | Check backend health |
| 503 | `BACKEND_UNREACHABLE` | Network error reaching FastAPI | Check backend is running |
| 503 | `BACKEND_NOT_CONFIGURED` | `BACKEND_URL` secret not set | Run `wrangler secret put BACKEND_URL` |
| 503 | `BACKEND_ERROR` | Backend returned 5xx | Check FastAPI logs |

---

## See also

- [`docs/CLOUDFLARE_WORKERS.md`](./CLOUDFLARE_WORKERS.md) — Worker setup, deploy, R2/KV reference
- [`docs/DEPLOYMENT.md`](./DEPLOYMENT.md) — End-to-end deploy guide (Pages + Workers + backend)
- [`workers/wrangler.toml`](../workers/wrangler.toml) — Binding config + env vars
- [`workers/index.ts`](../workers/index.ts) — Worker source (v7.0.0)
- [Cloudflare Workers Docs](https://developers.cloudflare.com/workers/)
- [Cloudflare Access Docs](https://developers.cloudflare.com/cloudflare-one/policies/access/)
