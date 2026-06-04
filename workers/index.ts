/**
 * Indic Voice AI — Cloudflare Workers API Gateway
 * Phase 8: Bug-fix release — restore all template literal interpolations
 *
 * Phase 7 feature set (unchanged):
 *   - API key authentication via X-Api-Key header (WORKER_API_KEY secret)
 *   - Per-IP sliding-window rate limiting via KV (RATE_LIMITER namespace)
 *   - Security response headers (CSP, HSTS, X-Frame-Options, etc.)
 *   - Unique request ID on every response (X-Request-Id)
 *   - CORS tightened to ALLOWED_ORIGINS allowlist
 *   - Structured console logging with request context
 *
 * Routes:
 *   GET    /health       → service status (no auth required)
 *   POST   /generate     → proxy TTS job to FastAPI backend (auth required)
 *   GET    /job/:id      → poll job status from KV + R2 (auth required)
 *   DELETE /job/:id      → GDPR erasure (auth required)
 *   OPTIONS *            → CORS preflight (no auth required)
 */

import type {
  R2Bucket,
  KVNamespace,
  ExecutionContext,
  ExportedHandler,
} from "@cloudflare/workers-types";

// ─── Environment bindings ────────────────────────────────────────────────────

export interface Env {
  /** R2 bucket for audio uploads + outputs */
  AUDIO_BUCKET: R2Bucket;
  /** KV namespace for job state */
  JOB_STORE: KVNamespace;
  /** KV namespace for rate-limit counters (separate namespace for clean TTL management) */
  RATE_LIMITER: KVNamespace;
  /** FastAPI backend base URL (set as encrypted secret via wrangler secret put BACKEND_URL) */
  BACKEND_URL: string;
  /** Worker version string */
  WORKER_VERSION: string;
  /**
   * Shared API key for gateway authentication (set as encrypted secret).
   * Clients must send: X-Api-Key: <value>
   * If empty/unset, auth is BYPASSED — set this before going to production.
   */
  WORKER_API_KEY: string;
  /**
   * Comma-separated list of allowed CORS origins.
   * Example: "https://indic-voice.pages.dev,https://your-domain.com"
   * Use "*" to allow all origins (development only).
   */
  ALLOWED_ORIGINS: string;
  /**
   * Maximum requests per IP per window (default: 20).
   * Set as a plain var in wrangler.toml [vars] or CF dashboard.
   */
  RATE_LIMIT_REQUESTS: string;
  /**
   * Sliding window duration in seconds (default: 60).
   * Set as a plain var in wrangler.toml [vars] or CF dashboard.
   */
  RATE_LIMIT_WINDOW_SECONDS: string;
}

// ─── Domain types ─────────────────────────────────────────────────────────────

export interface JobRecord {
  id: string;
  status: "queued" | "processing" | "completed" | "failed";
  language: string;
  text: string;
  voice: string;
  mode: "standard" | "clone";
  output_key: string | null;
  output_url: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  ttl_seconds: number;
}

export interface GenerateRequest {
  text: string;
  language: string;
  voice?: string;
  mode?: "standard" | "clone";
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  timestamp: string;
  bindings: {
    r2: boolean;
    kv: boolean;
    rate_limiter: boolean;
    backend: boolean;
    auth_enabled: boolean;
  };
  backend_url: string;
}

/** Sliding-window rate-limit record stored in KV */
interface RateLimitRecord {
  count: number;
  window_start: number; // Unix ms
}

// ─── Security helpers ─────────────────────────────────────────────────────────

/** Security response headers added to every non-preflight response */
function securityHeaders(): Record<string, string> {
  return {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy":
      "default-src 'none'; script-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
  };
}

/** Generate a compact unique request ID */
function newRequestId(): string {
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 8);
  return `req_${ts}_${rand}`;
}

// ─── CORS helpers ─────────────────────────────────────────────────────────────

/**
 * Returns CORS headers for a given request Origin.
 * Reflects the origin only if it is in the ALLOWED_ORIGINS list (or list is "*").
 */
function corsHeaders(
  requestOrigin: string | null,
  allowedOrigins: string
): Record<string, string> {
  const origins = allowedOrigins
    .split(",")
    .map((o) => o.trim())
    .filter(Boolean);

  const isWildcard = origins.includes("*");
  const originAllowed =
    isWildcard ||
    (requestOrigin !== null && origins.includes(requestOrigin));

  const allowOrigin =
    originAllowed && requestOrigin
      ? requestOrigin // reflect exact origin (enables credentials support)
      : isWildcard
      ? "*"
      : ""; // disallowed: no ACAO header → browser blocks

  const headers: Record<string, string> = {
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers":
      "Content-Type, Authorization, X-Api-Key, X-Request-Id",
    "Access-Control-Expose-Headers": "X-Request-Id, X-RateLimit-Remaining",
    "Access-Control-Max-Age": "86400",
  };

  if (allowOrigin) {
    headers["Access-Control-Allow-Origin"] = allowOrigin;
    if (!isWildcard) {
      headers["Vary"] = "Origin";
    }
  }

  return headers;
}

// ─── Response helpers ─────────────────────────────────────────────────────────

function jsonResponse<T>(
  data: T,
  status = 200,
  extra: Record<string, string> = {}
): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...securityHeaders(),
      ...extra,
    },
  });
}

function errorResponse(
  message: string,
  code: string,
  status: number,
  extra: Record<string, string> = {}
): Response {
  return jsonResponse(
    {
      error: message,
      code,
      status,
      timestamp: new Date().toISOString(),
    },
    status,
    extra
  );
}

// ─── Middleware: API key authentication ───────────────────────────────────────

/**
 * Validates the X-Api-Key header against the WORKER_API_KEY secret.
 * Returns null if valid (or auth is disabled), or a 401/403 Response.
 *
 * Auth is disabled when WORKER_API_KEY is empty — this allows local dev
 * without any key, but logs a warning so it is never silently skipped in prod.
 */
function checkApiKey(
  request: Request,
  env: Env,
  requestId: string
): Response | null {
  const expectedKey = env.WORKER_API_KEY?.trim();

  if (!expectedKey) {
    console.warn(
      `[Worker][${requestId}] WORKER_API_KEY not set — auth is DISABLED. Set this secret before production.`
    );
    return null; // bypass
  }

  const providedKey =
    request.headers.get("X-Api-Key") ??
    request.headers.get("Authorization")?.replace(/^Bearer\s+/i, "") ??
    "";

  if (!providedKey) {
    return errorResponse(
      "Missing API key. Provide X-Api-Key header.",
      "MISSING_API_KEY",
      401,
      {
        "WWW-Authenticate": 'ApiKey realm="indic-voice-ai"',
      }
    );
  }

  // Constant-time comparison to prevent timing attacks
  if (!constantTimeEqual(providedKey, expectedKey)) {
    console.warn(`[Worker][${requestId}] Invalid API key attempt.`);
    return errorResponse("Invalid API key.", "INVALID_API_KEY", 403);
  }

  return null; // auth passed
}

/**
 * Constant-time string comparison — prevents timing side-channel attacks
 * on the API key check.
 */
function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}

// ─── Middleware: Per-IP rate limiting ─────────────────────────────────────────

interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  resetAt: number; // Unix ms
  limit: number;
}

/**
 * Sliding-window rate limiter backed by KV.
 *
 * Algorithm:
 *   1. Derive a stable key from the client IP + current window index.
 *   2. Read the counter from KV (TTL = window duration).
 *   3. If count < limit → increment and allow.
 *   4. If count >= limit → reject with 429.
 *
 * The window slides by using floor(now / windowMs) as the window index,
 * so each window is fixed-width and aligned to clock time (not per-client).
 * This is simpler than a true sliding log but sufficient for abuse prevention.
 */
async function checkRateLimit(
  request: Request,
  env: Env,
  requestId: string
): Promise<RateLimitResult> {
  const limit = parseInt(env.RATE_LIMIT_REQUESTS ?? "20", 10);
  const windowSeconds = parseInt(env.RATE_LIMIT_WINDOW_SECONDS ?? "60", 10);
  const windowMs = windowSeconds * 1000;

  const clientIp =
    request.headers.get("CF-Connecting-IP") ??
    request.headers.get("X-Forwarded-For")?.split(",")[0]?.trim() ??
    "unknown";

  const now = Date.now();
  const windowIndex = Math.floor(now / windowMs);
  const windowStart = windowIndex * windowMs;
  const resetAt = windowStart + windowMs;

  // Sanitise IP for use as a KV key
  const safeIp = clientIp.replace(/[^a-zA-Z0-9.:_-]/g, "_").slice(0, 64);
  const kvKey = `rl:${safeIp}:${windowIndex}`;

  if (!env.RATE_LIMITER) {
    console.warn(
      `[Worker][${requestId}] RATE_LIMITER KV not bound — rate limiting disabled.`
    );
    return { allowed: true, remaining: limit, resetAt, limit };
  }

  const raw = await env.RATE_LIMITER.get(kvKey);
  let record: RateLimitRecord = raw
    ? (JSON.parse(raw) as RateLimitRecord)
    : { count: 0, window_start: windowStart };

  // Guard against stale records from a previous window
  if (record.window_start !== windowStart) {
    record = { count: 0, window_start: windowStart };
  }

  if (record.count >= limit) {
    return { allowed: false, remaining: 0, resetAt, limit };
  }

  record.count += 1;
  const remaining = limit - record.count;

  // Non-blocking write — TTL set to window + 10 s buffer for auto-cleanup
  const writePromise = env.RATE_LIMITER.put(kvKey, JSON.stringify(record), {
    expirationTtl: windowSeconds + 10,
  });
  void writePromise;

  return { allowed: true, remaining, resetAt, limit };
}

/** Build rate-limit response headers */
function rateLimitHeaders(
  result: RateLimitResult,
  requestId: string
): Record<string, string> {
  return {
    "X-RateLimit-Limit": String(result.limit),
    "X-RateLimit-Remaining": String(result.remaining),
    "X-RateLimit-Reset": String(Math.ceil(result.resetAt / 1000)),
    "X-Request-Id": requestId,
  };
}

// ─── Route handlers ───────────────────────────────────────────────────────────

/**
 * GET /health
 * Returns service liveness + binding diagnostics.
 * Auth NOT required — allows uptime monitors without API keys.
 */
async function handleHealth(env: Env, requestId: string): Promise<Response> {
  const body: HealthResponse = {
    status: "ok",
    version: env.WORKER_VERSION ?? "8.0.0",
    timestamp: new Date().toISOString(),
    bindings: {
      r2: typeof env.AUDIO_BUCKET !== "undefined",
      kv: typeof env.JOB_STORE !== "undefined",
      rate_limiter: typeof env.RATE_LIMITER !== "undefined",
      backend: Boolean(env.BACKEND_URL),
      auth_enabled: Boolean(env.WORKER_API_KEY?.trim()),
    },
    backend_url: env.BACKEND_URL ?? "(not set)",
  };
  return jsonResponse(body, 200, { "X-Request-Id": requestId });
}

/**
 * POST /generate
 * Accepts multipart/form-data { audio, text, language, mode? }
 * Forwards to FastAPI backend and returns job_id immediately (202 Accepted).
 */
async function handleGenerate(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  requestId: string
): Promise<Response> {
  // Accept both JSON and multipart/form-data (frontend sends FormData)
  const contentType = request.headers.get("Content-Type") ?? "";
  let text: string | null = null;
  let language: string | null = null;
  let voice: string | null = null;
  let mode: string = "standard";
  let bodyToForward: BodyInit;
  let forwardContentType: string | null = null;

  if (contentType.includes("multipart/form-data")) {
    // Forward the raw FormData to FastAPI
    const formData = await request.formData();
    text = formData.get("text") as string | null;
    language = formData.get("language") as string | null;
    voice = formData.get("voice") as string | null;
    mode = (formData.get("mode") as string) ?? "standard";
    bodyToForward = formData;
    // Let fetch set the correct boundary automatically (no explicit Content-Type)
  } else {
    // Assume JSON
    let body: GenerateRequest;
    try {
      body = (await request.json()) as GenerateRequest;
    } catch {
      return errorResponse("Invalid JSON body", "INVALID_JSON", 400, {
        "X-Request-Id": requestId,
      });
    }
    text = body.text ?? null;
    language = body.language ?? null;
    voice = body.voice ?? null;
    mode = body.mode ?? "standard";
    bodyToForward = JSON.stringify({ text, language, voice, mode });
    forwardContentType = "application/json";
  }

  if (!text || text.trim().length === 0) {
    return errorResponse(
      "'text' is required and must be a non-empty string",
      "MISSING_TEXT",
      422,
      { "X-Request-Id": requestId }
    );
  }

  if (!language || typeof language !== "string") {
    return errorResponse(
      "'language' is required (te | ta | hi | en)",
      "MISSING_LANGUAGE",
      422,
      { "X-Request-Id": requestId }
    );
  }

  const SUPPORTED_LANGUAGES = ["te", "ta", "hi", "en"];
  if (!SUPPORTED_LANGUAGES.includes(language)) {
    return errorResponse(
      `Unsupported language '${language}'. Supported: ${SUPPORTED_LANGUAGES.join(", ")}`,
      "UNSUPPORTED_LANGUAGE",
      422,
      { "X-Request-Id": requestId }
    );
  }

  if (text.length > 5000) {
    return errorResponse(
      "'text' must be 5000 characters or fewer",
      "TEXT_TOO_LONG",
      422,
      { "X-Request-Id": requestId }
    );
  }

  const backendUrl = env.BACKEND_URL?.replace(/\/+$/, "");
  if (!backendUrl) {
    return errorResponse(
      "Backend URL not configured",
      "BACKEND_NOT_CONFIGURED",
      503,
      { "X-Request-Id": requestId }
    );
  }

  const forwardHeaders: Record<string, string> = {
    "X-Forwarded-By": "cf-worker",
    "X-Request-Id": requestId,
  };
  if (forwardContentType) {
    forwardHeaders["Content-Type"] = forwardContentType;
  }

  let backendResponse: Response;
  try {
    backendResponse = await fetch(`${backendUrl}/generate`, {
      method: "POST",
      headers: forwardHeaders,
      body: bodyToForward,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown network error";
    console.error(`[Worker][${requestId}] Backend fetch error:`, msg);
    return errorResponse(`Backend unreachable: ${msg}`, "BACKEND_UNREACHABLE", 503, {
      "X-Request-Id": requestId,
    });
  }

  if (!backendResponse.ok) {
    let detail = "Backend returned an error";
    try {
      const errBody = (await backendResponse.json()) as { detail?: string };
      detail = errBody.detail ?? detail;
    } catch {
      /* ignore parse errors */
    }
    return errorResponse(detail, "BACKEND_ERROR", backendResponse.status, {
      "X-Request-Id": requestId,
    });
  }

  let jobData: Record<string, unknown>;
  try {
    jobData = (await backendResponse.json()) as Record<string, unknown>;
  } catch {
    return errorResponse(
      "Backend returned non-JSON response",
      "BACKEND_PARSE_ERROR",
      502,
      { "X-Request-Id": requestId }
    );
  }

  const jobId = jobData["job_id"] as string | undefined;

  // Store initial KV record for Workers-native polling
  if (jobId && env.JOB_STORE) {
    const record: JobRecord = {
      id: jobId,
      status: "queued",
      language,
      text: text.trim().slice(0, 200),
      voice: (voice as string) ?? "",
      mode: mode as "standard" | "clone",
      output_key: null,
      output_url: null,
      error_message: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      ttl_seconds: 28800,
    };
    ctx.waitUntil(
      env.JOB_STORE.put(jobId, JSON.stringify(record), {
        expirationTtl: record.ttl_seconds,
      })
    );
  }

  console.log(
    `[Worker][${requestId}] Job created: job_id=${jobId} lang=${language} mode=${mode}`
  );

  return jsonResponse(
    {
      ...jobData,
      worker_version: env.WORKER_VERSION ?? "8.0.0",
      gateway: "cloudflare-workers",
      request_id: requestId,
    },
    202,
    { "X-Request-Id": requestId }
  );
}

/**
 * GET /job/:id
 * KV-first lookup; falls back to FastAPI backend.
 */
async function handleGetJob(
  jobId: string,
  env: Env,
  requestId: string
): Promise<Response> {
  if (!jobId || jobId.trim().length === 0) {
    return errorResponse("Job ID is required", "MISSING_JOB_ID", 400, {
      "X-Request-Id": requestId,
    });
  }

  if (env.JOB_STORE) {
    const raw = await env.JOB_STORE.get(jobId);
    if (raw) {
      let record: JobRecord;
      try {
        record = JSON.parse(raw) as JobRecord;
      } catch {
        return errorResponse("Corrupted job record", "KV_PARSE_ERROR", 500, {
          "X-Request-Id": requestId,
        });
      }

      // Inject R2 output URL if completed and url not yet set
      if (
        record.status === "completed" &&
        record.output_key &&
        env.AUDIO_BUCKET &&
        !record.output_url
      ) {
        try {
          const obj = await env.AUDIO_BUCKET.head(record.output_key);
          if (obj) {
            record.output_url = `/files/${record.output_key}`;
          }
        } catch {
          /* non-fatal */
        }
      }

      return jsonResponse(record, 200, { "X-Request-Id": requestId });
    }
  }

  // Fallback to FastAPI
  const backendUrl = env.BACKEND_URL?.replace(/\/+$/, "");
  if (!backendUrl) {
    return errorResponse(
      "Job not found and backend not configured",
      "NOT_FOUND",
      404,
      { "X-Request-Id": requestId }
    );
  }

  let backendResponse: Response;
  try {
    backendResponse = await fetch(`${backendUrl}/job/${jobId}`, {
      headers: {
        "X-Forwarded-By": "cf-worker",
        "X-Request-Id": requestId,
      },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(`Backend unreachable: ${msg}`, "BACKEND_UNREACHABLE", 503, {
      "X-Request-Id": requestId,
    });
  }

  if (backendResponse.status === 404) {
    return errorResponse(`Job '${jobId}' not found`, "JOB_NOT_FOUND", 404, {
      "X-Request-Id": requestId,
    });
  }
  if (!backendResponse.ok) {
    return errorResponse(
      "Backend returned an error",
      "BACKEND_ERROR",
      backendResponse.status,
      { "X-Request-Id": requestId }
    );
  }

  const jobData = await backendResponse.json();
  return jsonResponse(jobData, 200, { "X-Request-Id": requestId });
}

/**
 * DELETE /job/:id
 * GDPR erasure: removes R2 blobs + KV record + backend copy.
 */
async function handleDeleteJob(
  jobId: string,
  env: Env,
  ctx: ExecutionContext,
  requestId: string
): Promise<Response> {
  if (!jobId || jobId.trim().length === 0) {
    return errorResponse("Job ID is required", "MISSING_JOB_ID", 400, {
      "X-Request-Id": requestId,
    });
  }

  const deletionTasks: Promise<unknown>[] = [];
  let outputKey: string | null = null;

  if (env.JOB_STORE) {
    const raw = await env.JOB_STORE.get(jobId);
    if (raw) {
      try {
        outputKey = (JSON.parse(raw) as JobRecord).output_key;
      } catch {
        /* proceed */
      }
      deletionTasks.push(env.JOB_STORE.delete(jobId));
    }
  }

  if (outputKey && env.AUDIO_BUCKET) {
    deletionTasks.push(env.AUDIO_BUCKET.delete(outputKey));
  }
  if (env.AUDIO_BUCKET) {
    deletionTasks.push(
      env.AUDIO_BUCKET.delete(`uploads/${jobId}_upload.wav`)
    );
  }

  const backendUrl = env.BACKEND_URL?.replace(/\/+$/, "");
  if (backendUrl) {
    deletionTasks.push(
      fetch(`${backendUrl}/job/${jobId}`, {
        method: "DELETE",
        headers: {
          "X-Forwarded-By": "cf-worker",
          "X-Request-Id": requestId,
        },
      }).catch(() => {
        /* non-fatal */
      })
    );
  }

  ctx.waitUntil(Promise.allSettled(deletionTasks));

  console.log(`[Worker][${requestId}] GDPR delete scheduled for job: ${jobId}`);

  return jsonResponse(
    {
      deleted: true,
      job_id: jobId,
      message:
        "Job and associated audio files have been scheduled for deletion.",
      timestamp: new Date().toISOString(),
      request_id: requestId,
    },
    200,
    { "X-Request-Id": requestId }
  );
}

// ─── Router ───────────────────────────────────────────────────────────────────

export default {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext
  ): Promise<Response> {
    const url = new URL(request.url);
    const { pathname } = url;
    const method = request.method;
    const requestId = newRequestId();

    const requestOrigin = request.headers.get("Origin");
    const allowedOrigins = env.ALLOWED_ORIGINS ?? "*";
    const cors = corsHeaders(requestOrigin, allowedOrigins);

    // ── CORS preflight — no auth, no rate limit ───────────────────────────────
    if (method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: { ...cors, "X-Request-Id": requestId },
      });
    }

    console.log(`[Worker][${requestId}] ${method} ${pathname}`);

    try {
      // ── /health is auth-exempt ────────────────────────────────────────────
      if (pathname === "/health" && method === "GET") {
        const res = await handleHealth(env, requestId);
        const headers = new Headers(res.headers);
        for (const [k, v] of Object.entries(cors)) headers.set(k, v);
        return new Response(res.body, { status: res.status, headers });
      }

      // ── Auth middleware ───────────────────────────────────────────────────
      const authError = checkApiKey(request, env, requestId);
      if (authError) {
        const headers = new Headers(authError.headers);
        for (const [k, v] of Object.entries(cors)) headers.set(k, v);
        return new Response(authError.body, {
          status: authError.status,
          headers,
        });
      }

      // ── Rate-limit middleware ─────────────────────────────────────────────
      const rlResult = await checkRateLimit(request, env, requestId);
      const rlHeaders = rateLimitHeaders(rlResult, requestId);

      if (!rlResult.allowed) {
        const retryAfter = Math.ceil((rlResult.resetAt - Date.now()) / 1000);
        console.warn(
          `[Worker][${requestId}] Rate limit exceeded. Reset in ${retryAfter}s`
        );
        const res = errorResponse(
          `Rate limit exceeded. Try again in ${retryAfter} seconds.`,
          "RATE_LIMIT_EXCEEDED",
          429,
          { ...rlHeaders, "Retry-After": String(retryAfter) }
        );
        const headers = new Headers(res.headers);
        for (const [k, v] of Object.entries(cors)) headers.set(k, v);
        return new Response(res.body, { status: 429, headers });
      }

      // ── Route dispatch ────────────────────────────────────────────────────
      let res: Response;

      if (pathname === "/generate" && method === "POST") {
        res = await handleGenerate(request, env, ctx, requestId);
      } else {
        const jobMatch = pathname.match(/^\/job\/([^/]+)$/);
        if (jobMatch) {
          const jobId = jobMatch[1];
          if (method === "GET") {
            res = await handleGetJob(jobId, env, requestId);
          } else if (method === "DELETE") {
            res = await handleDeleteJob(jobId, env, ctx, requestId);
          } else {
            res = errorResponse(
              `Method ${method} not allowed on /job/:id`,
              "METHOD_NOT_ALLOWED",
              405,
              { Allow: "GET, DELETE", "X-Request-Id": requestId }
            );
          }
        } else {
          res = errorResponse(
            `Route not found: ${method} ${pathname}`,
            "NOT_FOUND",
            404,
            { "X-Request-Id": requestId }
          );
        }
      }

      // Attach CORS + rate-limit headers to every authenticated response
      const headers = new Headers(res.headers);
      for (const [k, v] of Object.entries(cors)) headers.set(k, v);
      for (const [k, v] of Object.entries(rlHeaders)) headers.set(k, v);
      return new Response(res.body, { status: res.status, headers });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Unexpected internal error";
      console.error(
        `[Worker][${requestId}] Unhandled error on ${method} ${pathname}:`,
        err
      );
      const res = errorResponse(message, "INTERNAL_ERROR", 500, {
        "X-Request-Id": requestId,
      });
      const headers = new Headers(res.headers);
      for (const [k, v] of Object.entries(cors)) headers.set(k, v);
      return new Response(res.body, { status: 500, headers });
    }
  },
} satisfies ExportedHandler<Env>;
