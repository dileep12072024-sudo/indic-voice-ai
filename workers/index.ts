/**
 * Indic Voice AI — Cloudflare Workers API Gateway
 * Phase 6: TypeScript Worker with R2 + KV bindings
 *
 * Routes:
 *   GET    /health       → service status
 *   POST   /generate     → proxy TTS job to FastAPI backend
 *   GET    /job/:id      → poll job status from KV + R2
 *   DELETE /job/:id      → GDPR erasure (R2 blobs + KV record)
 *   OPTIONS *            → CORS preflight
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
  /** FastAPI backend base URL (set in wrangler.toml vars or dashboard) */
  BACKEND_URL: string;
  /** Worker version string */
  WORKER_VERSION: string;
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
    backend: boolean;
  };
  backend_url: string;
}

// ─── CORS helpers ─────────────────────────────────────────────────────────────

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Api-Key",
  "Access-Control-Max-Age": "86400",
};

function corsHeaders(): Record<string, string> {
  return { ...CORS_HEADERS };
}

// ─── Response helpers ─────────────────────────────────────────────────────────

function jsonResponse<T>(data: T, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...corsHeaders(),
    },
  });
}

function errorResponse(
  message: string,
  code: string,
  status: number
): Response {
  return jsonResponse(
    {
      error: message,
      code,
      status,
      timestamp: new Date().toISOString(),
    },
    status
  );
}

// ─── Route handlers ───────────────────────────────────────────────────────────

/**
 * GET /health
 * Returns service liveness + binding diagnostics.
 */
async function handleHealth(env: Env): Promise<Response> {
  const body: HealthResponse = {
    status: "ok",
    version: env.WORKER_VERSION ?? "6.0.0",
    timestamp: new Date().toISOString(),
    bindings: {
      r2: typeof env.AUDIO_BUCKET !== "undefined",
      kv: typeof env.JOB_STORE !== "undefined",
      backend: Boolean(env.BACKEND_URL),
    },
    backend_url: env.BACKEND_URL ?? "(not set)",
  };
  return jsonResponse(body, 200);
}

/**
 * POST /generate
 * Accepts JSON { text, language, voice?, mode? }
 * Forwards to FastAPI backend and returns job_id immediately (202 Accepted).
 */
async function handleGenerate(
  request: Request,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  let body: GenerateRequest;
  try {
    body = (await request.json()) as GenerateRequest;
  } catch {
    return errorResponse("Invalid JSON body", "INVALID_JSON", 400);
  }

  const { text, language, voice, mode = "standard" } = body;

  if (!text || typeof text !== "string" || text.trim().length === 0) {
    return errorResponse(
      "'text' is required and must be a non-empty string",
      "MISSING_TEXT",
      422
    );
  }

  if (!language || typeof language !== "string") {
    return errorResponse(
      "'language' is required (te | ta | hi | en)",
      "MISSING_LANGUAGE",
      422
    );
  }

  const SUPPORTED_LANGUAGES = ["te", "ta", "hi", "en"];
  if (!SUPPORTED_LANGUAGES.includes(language)) {
    return errorResponse(
      `Unsupported language '${language}'. Supported: ${SUPPORTED_LANGUAGES.join(", ")}`,
      "UNSUPPORTED_LANGUAGE",
      422
    );
  }

  if (text.length > 5000) {
    return errorResponse(
      "'text' must be 5000 characters or fewer",
      "TEXT_TOO_LONG",
      422
    );
  }

  const backendUrl = env.BACKEND_URL?.replace(/\/+$/, "");
  if (!backendUrl) {
    return errorResponse(
      "Backend URL not configured",
      "BACKEND_NOT_CONFIGURED",
      503
    );
  }

  let backendResponse: Response;
  try {
    backendResponse = await fetch(`${backendUrl}/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Forwarded-By": "cf-worker",
      },
      body: JSON.stringify({
        text: text.trim(),
        language,
        voice: voice ?? null,
        mode,
      }),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown network error";
    return errorResponse(
      `Backend unreachable: ${msg}`,
      "BACKEND_UNREACHABLE",
      503
    );
  }

  if (!backendResponse.ok) {
    let detail = "Backend returned an error";
    try {
      const errBody = (await backendResponse.json()) as { detail?: string };
      detail = errBody.detail ?? detail;
    } catch {
      /* ignore parse errors */
    }
    return errorResponse(detail, "BACKEND_ERROR", backendResponse.status);
  }

  let jobData: Record<string, unknown>;
  try {
    jobData = (await backendResponse.json()) as Record<string, unknown>;
  } catch {
    return errorResponse(
      "Backend returned non-JSON response",
      "BACKEND_PARSE_ERROR",
      502
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
      mode,
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

  return jsonResponse(
    {
      ...jobData,
      worker_version: env.WORKER_VERSION ?? "6.0.0",
      gateway: "cloudflare-workers",
    },
    202
  );
}

/**
 * GET /job/:id
 * KV-first lookup; falls back to FastAPI backend.
 */
async function handleGetJob(jobId: string, env: Env): Promise<Response> {
  if (!jobId || jobId.trim().length === 0) {
    return errorResponse("Job ID is required", "MISSING_JOB_ID", 400);
  }

  if (env.JOB_STORE) {
    const raw = await env.JOB_STORE.get(jobId);
    if (raw) {
      let record: JobRecord;
      try {
        record = JSON.parse(raw) as JobRecord;
      } catch {
        return errorResponse("Corrupted job record", "KV_PARSE_ERROR", 500);
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

      return jsonResponse(record, 200);
    }
  }

  // Fallback to FastAPI
  const backendUrl = env.BACKEND_URL?.replace(/\/+$/, "");
  if (!backendUrl) {
    return errorResponse(
      "Job not found and backend not configured",
      "NOT_FOUND",
      404
    );
  }

  let backendResponse: Response;
  try {
    backendResponse = await fetch(
      `${backendUrl}/job/${encodeURIComponent(jobId)}`,
      { headers: { "X-Forwarded-By": "cf-worker" } }
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(
      `Backend unreachable: ${msg}`,
      "BACKEND_UNREACHABLE",
      503
    );
  }

  if (backendResponse.status === 404) {
    return errorResponse(`Job '${jobId}' not found`, "JOB_NOT_FOUND", 404);
  }
  if (!backendResponse.ok) {
    return errorResponse(
      "Backend returned an error",
      "BACKEND_ERROR",
      backendResponse.status
    );
  }

  const jobData = await backendResponse.json();
  return jsonResponse(jobData, 200);
}

/**
 * DELETE /job/:id
 * GDPR erasure: removes R2 blobs + KV record + backend copy.
 */
async function handleDeleteJob(
  jobId: string,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  if (!jobId || jobId.trim().length === 0) {
    return errorResponse("Job ID is required", "MISSING_JOB_ID", 400);
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
      fetch(`${backendUrl}/job/${encodeURIComponent(jobId)}`, {
        method: "DELETE",
        headers: { "X-Forwarded-By": "cf-worker" },
      }).catch(() => {
        /* non-fatal */
      })
    );
  }

  ctx.waitUntil(Promise.allSettled(deletionTasks));

  return jsonResponse(
    {
      deleted: true,
      job_id: jobId,
      message:
        "Job and associated audio files have been scheduled for deletion.",
      timestamp: new Date().toISOString(),
    },
    200
  );
}

// ─── Router ───────────────────────────────────────────────────────────────────

export default {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext
  ): Promise<Response> {
    const { pathname } = new URL(request.url);
    const method = request.method;

    if (method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    try {
      if (pathname === "/health" && method === "GET") {
        return await handleHealth(env);
      }

      if (pathname === "/generate" && method === "POST") {
        return await handleGenerate(request, env, ctx);
      }

      const jobMatch = pathname.match(/^\/job\/([^/]+)$/);
      if (jobMatch) {
        const jobId = jobMatch[1];
        if (method === "GET") return await handleGetJob(jobId, env);
        if (method === "DELETE") return await handleDeleteJob(jobId, env, ctx);
      }

      return errorResponse(
        `Route not found: ${method} ${pathname}`,
        "NOT_FOUND",
        404
      );
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Unexpected internal error";
      console.error(`[Worker] Unhandled error on ${method} ${pathname}:`, err);
      return errorResponse(message, "INTERNAL_ERROR", 500);
    }
  },
} satisfies ExportedHandler<Env>;
