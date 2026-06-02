# docs/JOBS.md — IndicVoice AI Job System

> **Phase 4A** — Async job architecture reference.
> Covers local development flow, Cloudflare production flow,
> queue architecture, and the future voice cloning pipeline.

---

## Table of Contents

1. [Overview](#1-overview)
2. [API Contract](#2-api-contract)
3. [Local Development Flow](#3-local-development-flow)
4. [Cloudflare Production Flow](#4-cloudflare-production-flow)
5. [Queue Architecture](#5-queue-architecture)
6. [Abstraction Layer Design](#6-abstraction-layer-design)
7. [Future Voice Cloning Flow](#7-future-voice-cloning-flow)
8. [Error Handling](#8-error-handling)
9. [Quick Reference](#9-quick-reference)

---

## 1. Overview

Phase 4A replaces the synchronous `POST /generate → WAV bytes` contract
with an **asynchronous job system**:

```
Before (Phase 3A):
  POST /generate  →  wait 2–8s  →  200 + WAV bytes

After (Phase 4A):
  POST /generate  →  202 + { job_id }  (immediate)
  GET  /job/{id}  →  { status: "queued" | "processing" | "completed" | "failed" }
  GET  {output_url} →  WAV bytes  (when completed)
```

**Why async?**

| Reason | Detail |
|--------|--------|
| Cloudflare Workers timeout | Workers CPU limit is 30 s; TTS can take 3–8 s + network |
| Voice cloning latency | XTTS-v2 / OpenVoice can take 20–60 s on GPU |
| User experience | UI shows progress indicator instead of a frozen button |
| Retryability | Jobs can be retried if the worker crashes |
| Observability | Every job has a record with timestamps and error messages |

---

## 2. API Contract

### POST /generate

**Request**

```
POST /generate
Content-Type: multipart/form-data

audio     : File   — voice sample (WAV/MP3/OGG/WEBM/FLAC, max 50 MB)
text      : string — text to synthesise (1–500 chars, UTF-8)
language  : string — "te" | "ta" | "hi" | "en"
```

**Response — 202 Accepted**

```json
{
  "job_id":   "a1b2c3d4e5f6...",
  "status":   "queued",
  "poll_url": "/job/a1b2c3d4e5f6...",
  "message":  "Job accepted. Poll poll_url for status."
}
```

**Response headers**

```
Location:    /job/{job_id}
X-Job-Id:    {job_id}
Retry-After: 2
```

**Error responses (synchronous — returned before job is created)**

| HTTP | Condition |
|------|-----------|
| 422  | Invalid language, empty text, text > 500 chars, unsupported file type |
| 413  | Audio file > 50 MB |
| 422  | Empty audio file |

---

### GET /job/{job_id}

**Response — 200 OK**

```json
{
  "job_id":        "a1b2c3d4e5f6...",
  "status":        "queued | processing | completed | failed",
  "language":      "te",
  "voice":         "te-IN-ShrutiNeural",
  "text_len":      42,
  "output_url":    "/files/outputs/a1b2c3d4e5f6_output.wav",
  "error_message": null,
  "created_at":    1717200000.0,
  "updated_at":    1717200005.0,
  "completed_at":  1717200008.0
}
```

- `output_url` is `null` until `status == "completed"`.
- `error_message` is `null` unless `status == "failed"`.
- `completed_at` is `null` until the job reaches a terminal state.

**Response — 404 Not Found**

```json
{ "detail": "Job 'xyz' not found. It may have expired (TTL 8h) or never existed." }
```

---

### DELETE /job/{job_id}

Deletes the job record and its storage objects (upload + output WAV).
Used for GDPR right-to-erasure requests.

```json
{ "deleted": true, "job_id": "a1b2c3d4e5f6..." }
```

---

### GET /jobs  *(dev/debug only)*

Returns the most recent N jobs. **Remove or auth-gate before production.**

```json
{
  "count": 3,
  "jobs":  [ { ...job... }, { ...job... }, { ...job... } ]
}
```

---

## 3. Local Development Flow

```
Browser / curl client
        │
        │  POST /generate  (multipart)
        ▼
┌───────────────────────────────────────────────────────────┐
│  FastAPI  (uvicorn, localhost:8000)                        │
│                                                           │
│  1. Validate: language, text, file ext, file size         │
│  2. Write audio → LocalStorageBackend                     │
│        backend/uploads/{job_id}_sample.wav                │
│  3. Create job → LocalJobManager (in-memory dict)         │
│        { id, status:"queued", meta: {lang, text, voice} } │
│  4. Schedule: BackgroundTasks.add_task(_run_tts_job)      │
│  5. Return 202 { job_id, poll_url }           ←── NOW     │
│                                                           │
│  [Background coroutine — runs concurrently]               │
│  6. update_status → PROCESSING                            │
│  7. EdgeTTSProvider.synthesise()                          │
│        edge_tts.Communicate(text, voice).stream()         │
│        miniaudio.decode(mp3) → PCM → WAV                  │
│  8. Write WAV → LocalStorageBackend                       │
│        backend/outputs/{job_id}_output.wav                │
│  9. update_status → COMPLETED                             │
│        output_url = /files/outputs/{job_id}_output.wav    │
└───────────────────────────────────────────────────────────┘
        │
        │  GET /job/{job_id}  (every 2 s)
        ▼
  { status: "queued" | "processing" | "completed" }
        │
        │  when completed:
        │  GET /files/outputs/{job_id}_output.wav
        ▼
  WAV bytes → AudioPlayer plays + Download button
```

### Storage layout (local)

```
backend/
  uploads/
    {job_id}_sample.wav       ← voice sample (input)
  outputs/
    {job_id}_output.wav       ← synthesised speech (output)
```

### Job lifecycle (local)

```
Time  0ms:   POST /generate received
Time  1ms:   Validation passes
Time  2ms:   Upload saved to disk
Time  3ms:   Job created (status=queued)
Time  4ms:   BackgroundTask scheduled
Time  5ms:   202 Accepted returned to client ← client polls from here

[Background]
Time 10ms:   status → processing
Time 3000ms: EdgeTTS synthesis complete
Time 3010ms: WAV written to disk
Time 3011ms: status → completed, output_url set

Time 6000ms: client polls /job/{id} → completed → plays audio
```

---

## 4. Cloudflare Production Flow

> **Note:** CF deployment is Phase 3C. This section documents the target architecture.

```
Browser
   │
   │  POST /generate  (multipart, max 100 MB body)
   ▼
┌──────────────────────────────────────────────────────────────┐
│  Cloudflare Worker — API Gateway (TypeScript, V8)            │
│                                                              │
│  1. Auth: KV lookup → api_key → { user_id, plan }           │
│  2. Rate limit: KV INCR rl:{api_key} (TTL 60s)              │
│  3. Validate: language, text, file ext, file size            │
│  4. Upload audio → R2 bucket                                 │
│        uploads/{job_id}_sample.ext                           │
│  5. Create job → KV                                          │
│        key=job_id  value=JSON  TTL=8h                        │
│  6. Enqueue → Cloudflare Queue "indicvoice-jobs"             │
│        { job_id, language, text, voice, upload_key }         │
│  7. Return 202 { job_id, poll_url }           ←── IMMEDIATE  │
└──────────────────────────────────────────────────────────────┘
   │  Queue message (async)
   ▼
┌──────────────────────────────────────────────────────────────┐
│  Cloudflare Queue Consumer Worker (TypeScript, V8)           │
│  (runs separately from the API Gateway, triggered by Queue)  │
│                                                              │
│  1. Dequeue message                                          │
│  2. Update KV: status → processing                           │
│  3. Read upload ← R2 uploads/{job_id}_sample.ext             │
│  4. HTTP POST → External Inference Service                   │
│        Azure Speech REST API  (te/ta/hi/en)                  │
│        OR Modal OpenVoice     (voice cloning, Phase 4)       │
│  5. Receive WAV bytes                                        │
│  6. Write → R2 outputs/{job_id}_output.wav                   │
│  7. Generate pre-signed R2 URL (TTL 1h)                      │
│  8. Update KV: status → completed, output_url = R2_URL       │
│     OR        status → failed,    error_message = ...        │
└──────────────────────────────────────────────────────────────┘
   │
   │  GET /job/{job_id}  (client polling)
   ▼
┌──────────────────────────────────────────────────────────────┐
│  Cloudflare Worker — Status Check                             │
│  KV GET job_id → JSON → return to client                     │
└──────────────────────────────────────────────────────────────┘
   │  when completed:
   │  GET {pre-signed R2 URL}
   ▼
  WAV bytes → AudioPlayer + Download button
```

### CF vs Local comparison

| Concern | Local Dev | Cloudflare Production |
|---------|-----------|----------------------|
| Job store | Python dict (in-memory) | CF KV (global, TTL 8h) |
| Queue | asyncio BackgroundTasks | CF Queues |
| Storage | Local filesystem | CF R2 |
| TTS | edge-tts (Python, unofficial) | Azure Speech REST (official) |
| File serving | FastAPI StaticFiles | R2 pre-signed URL (1h TTL) |
| Auth | None | API key via KV |
| Rate limiting | None | KV sliding window |
| Cleanup | Server restart | R2 lifecycle rules |
| Runtime | Python + uvicorn | TypeScript + V8 Workers |

---

## 5. Queue Architecture

### Why a queue?

```
Without a queue (Phase 3A — synchronous):
  Client ──────────────── waits 3–60s ────────────────▶ WAV
                          (blocks HTTP connection)
                          (fails if > CF Worker timeout)
                          (no retry on crash)

With a queue (Phase 4A+):
  Client ── 202 (5ms) ──▶ polls every 2s ──▶ plays WAV
                │
                └── job queued ──▶ worker ──▶ TTS ──▶ output ──▶ job done
                    (decoupled, retriable, observable)
```

### Local queue: FastAPI BackgroundTasks

```python
# In the /generate route handler:
background_tasks.add_task(_run_tts_job, job_id=job.id, ...)
return 202  # returned immediately; _run_tts_job runs concurrently

# _run_tts_job runs in the same uvicorn event loop:
async def _run_tts_job(job_id, text, language, voice):
    await job_manager.update_status(job_id, PROCESSING)
    result = await tts.synthesise(...)          # edge-tts I/O
    await storage.put(output_key, result.wav_bytes)
    await job_manager.update_status(job_id, COMPLETED, output_url=...)
```

**Limitations of BackgroundTasks (local only):**
- Task is lost if the server process restarts while running.
- No retry on failure (one attempt only).
- No concurrency control (all jobs run in the same event loop).
- Suitable for development; not suitable for production scale.

### Cloudflare queue: CF Queues

```
Producer (API Gateway Worker):
  await env.JOB_QUEUE.send({
    job_id, language, text, voice, upload_key
  })
  // fire-and-forget; CF guarantees at-least-once delivery

Consumer (Queue Consumer Worker):
  // triggered by CF when queue has messages
  export default {
    async queue(batch, env) {
      for (const msg of batch.messages) {
        await processJob(msg.body, env)
        msg.ack()   // or msg.retry() on failure
      }
    }
  }
```

**CF Queues guarantees:**
- At-least-once delivery (consumer must be idempotent).
- Automatic retry with exponential back-off on consumer failure.
- Dead-letter queue (DLQ) for persistently failing messages.
- Max batch size: 100 messages; max batch timeout: 30 s.
- Max message size: 128 KB (text + metadata only; audio goes to R2).

### Job state machine

```
                  ┌──────────────────────────────────┐
                  │                                  │
  POST /generate  │                                  ▼
  ────────────────▶  QUEUED ──────▶ PROCESSING ──▶ COMPLETED
                  │                      │
                  │                      └──────────▶ FAILED
                  │
                  └── 404 after TTL expiry (8 hours)
```

| Transition | Trigger | Who |
|-----------|---------|-----|
| → QUEUED | Job created by /generate | API handler |
| QUEUED → PROCESSING | Worker picks up job | Background worker |
| PROCESSING → COMPLETED | TTS done, WAV stored | Background worker |
| PROCESSING → FAILED | TTS error / storage error | Background worker |
| Any → 404 | TTL expired (8h) | Job store eviction |

### Polling recommendations

```
Client polling strategy:
  interval  = 2s
  max_polls = 60        (2 min timeout)
  backoff   = 1.5×      (optional exponential backoff after 10 polls)

Pseudo-code:
  const { job_id } = await POST /generate(...)
  for (let i = 0; i < max_polls; i++) {
    await sleep(interval * Math.min(1.5**i, 8))   // backoff
    const job = await GET /job/{job_id}
    if (job.status === "completed") return play(job.output_url)
    if (job.status === "failed")    return showError(job.error_message)
  }
  showError("Generation timed out. Please try again.")
```

---

## 6. Abstraction Layer Design

### Why abstractions?

The three core dependencies — **job store**, **file storage**, and **TTS engine** —
are implementation details that differ between environments:

```
Environment         Job Store        Storage          TTS Engine
──────────────────────────────────────────────────────────────────
Local dev           LocalJobManager  LocalStorage     EdgeTTSProvider
CF production       CFKVJobManager   CFR2Storage      AzureSpeechProvider
Testing             MemoryJobManager MockStorage      MockTTSProvider
Future (GPU)        RedisJobManager  S3Storage        OpenVoiceProvider
```

Every environment uses the **same route handlers and worker code** — only the
concrete implementations passed at startup differ.

### JobManager

```python
class JobManager(ABC):
    async def create_job(meta: JobMeta) -> Job
    async def get_job(job_id: str) -> Optional[Job]
    async def update_status(job_id, status, *, output_key, output_url, error_message) -> Job
    async def delete_job(job_id: str) -> bool
    async def list_jobs(limit: int) -> list[Job]

# Local dev implementation:
class LocalJobManager(JobManager):
    # asyncio.Lock + dict[str, Job]
    # Auto-evicts jobs older than 8 hours
```

### StorageBackend

```python
class StorageBackend(ABC):
    async def put(key, data, content_type) -> str
    async def get(key) -> Optional[bytes]
    async def delete(key) -> bool
    async def exists(key) -> bool
    async def presign_url(key, ttl_seconds) -> str
    async def list_keys(prefix) -> list[str]

# Local dev: filesystem under backend/
# Production: CF R2 via S3-compatible API
```

### TTSProvider

```python
class TTSProvider(ABC):
    name: str                        # "edge-tts", "azure-speech", ...
    supported_languages: set[str]    # {"te","ta","hi","en"}
    async def synthesise(request: SynthesisRequest) -> SynthesisResult

# SynthesisRequest: { text, language, voice }
# SynthesisResult:  { wav_bytes, sample_rate, channels, duration_s }

# Local dev:   EdgeTTSProvider  (edge-tts + miniaudio)
# Production:  AzureSpeechProvider (REST API, CF-compatible)
# Phase 4:     OpenVoiceProvider   (GPU inference via Modal)
```

### File layout

```
backend/
  jobs/
    __init__.py
    models.py      ← Job, JobMeta, JobStatus dataclasses
    manager.py     ← JobManager (ABC) + LocalJobManager
  storage/
    __init__.py
    backend.py     ← StorageBackend (ABC) + LocalStorageBackend
  providers/
    __init__.py
    base.py        ← TTSProvider (ABC), SynthesisRequest/Result, exceptions
    edge_tts_provider.py  ← EdgeTTSProvider (local dev)
    # azure_speech_provider.py   (Phase 3C — CF production)
    # openvoice_provider.py      (Phase 4  — voice cloning)
  main.py          ← FastAPI app, routes, background worker
```

---

## 7. Future Voice Cloning Flow

> Not implemented in Phase 4A. Documented here for Phase 4B planning.

### Target pipeline

```
Client: POST /generate
  { audio: voice_sample.wav, text: "Hello", language: "te" }
        │
        ▼
API Gateway Worker
  1. Validate inputs
  2. Compute speaker_id = SHA256(audio_bytes)[0:16]
  3. Check storage: embeddings/{speaker_id}.npy  → HIT or MISS
  4. Save audio → uploads/{job_id}_sample.wav
  5. Create job with { speaker_id, embedding_cached: bool, ... }
  6. Enqueue job
  7. Return 202 { job_id }
        │
        ▼
Queue Consumer Worker
  IF embedding_cached:
    Load embedding ← storage: embeddings/{speaker_id}.npy
    Skip speaker encoding step
  ELSE:
    Load audio ← storage: uploads/{job_id}_sample.wav
    Call inference service: /encode  → speaker embedding (512-dim)
    Save embedding → storage: embeddings/{speaker_id}.npy

  Call inference service: /synthesise
    { text, language, speaker_embedding }
    → WAV bytes

  Save WAV → outputs/{job_id}_output.wav
  Update job: COMPLETED, output_url
        │
        ▼
Client: GET /job/{job_id}  → completed → plays cloned voice
```

### Inference service API (OpenVoice v2 on Modal)

```
POST /encode
  body: { audio_bytes: base64 }
  returns: { embedding: base64(float32[512]) }

POST /synthesise
  body: {
    text:       "text to speak",
    language:   "te",
    embedding:  base64(float32[512]),
    speed:      1.0
  }
  returns: audio/wav bytes
```

### Provider implementation (Phase 4B)

```python
class OpenVoiceProvider(TTSProvider):
    """
    Voice cloning via OpenVoice v2 on Modal.com serverless GPU.
    Extends SynthesisRequest with speaker_embedding field.
    """
    name = "openvoice-v2"
    supported_languages = {"te", "ta", "hi", "en"}

    async def synthesise(self, request: SynthesisRequest) -> SynthesisResult:
        # 1. POST embedding to Modal /synthesise endpoint
        # 2. Receive WAV bytes
        # 3. Return SynthesisResult
        ...
```

---

## 8. Error Handling

### Error taxonomy

```
HTTP 422  ← synchronous validation errors (before job is created)
  - invalid language code
  - empty or too-long text
  - unsupported file extension
  - empty file

HTTP 413  ← synchronous file size error
  - audio > 50 MB

HTTP 404  ← job not found
  - job_id never existed
  - job expired (TTL 8h)

Job status: "failed"  ← async errors (after job is created)
  - TTS service unavailable (no internet / Azure quota)
  - TTS returned no audio
  - Audio decode error
  - Storage write error
  - Unexpected exception in worker
```

### Failed job response

```json
{
  "job_id":        "a1b2c3d4e5f6...",
  "status":        "failed",
  "error_message": "TTS service unavailable: edge-tts cannot reach speech.platform.bing.com. Check internet connectivity.",
  "output_url":    null,
  "completed_at":  1717200008.0
}
```

### Client retry strategy

```
On status=="failed":
  if error_message contains "unavailable" or "network":
    → Show: "Service temporarily unavailable. Please try again."
    → Offer retry button (creates a new job)
  else:
    → Show error_message directly
    → Offer "Start over" (reset form)

On timeout (max_polls exceeded):
    → Show: "Generation is taking longer than expected."
    → Offer: "Check status" button (resumes polling same job_id)
```

---

## 9. Quick Reference

### Start local server

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Test the async flow (curl)

```bash
# 1. Generate test sample WAV
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

# 2. Submit job (returns immediately with job_id)
JOB=$(curl -s -X POST http://localhost:8000/generate \
  -F "audio=@/tmp/test.wav" \
  -F "text=Hello from IndicVoice AI phase four A" \
  -F "language=en")
echo $JOB
JOB_ID=$(echo $JOB | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

# 3. Poll until completed
while true; do
  STATUS=$(curl -s http://localhost:8000/job/$JOB_ID)
  echo $STATUS
  STATE=$(echo $STATUS | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  [ "$STATE" = "completed" ] && break
  [ "$STATE" = "failed" ]    && break
  sleep 2
done

# 4. Download WAV
OUTPUT_URL=$(echo $STATUS | python3 -c "import sys,json; print(json.load(sys.stdin)['output_url'])")
curl -s "http://localhost:8000$OUTPUT_URL" -o /tmp/output.wav
echo "Downloaded: $(wc -c < /tmp/output.wav) bytes"

# 5. Play (macOS: afplay /tmp/output.wav  |  Linux: aplay /tmp/output.wav)
```

### Job status values

| Value | Meaning | output_url | error_message |
|-------|---------|------------|---------------|
| `queued` | Created, waiting | null | null |
| `processing` | TTS running | null | null |
| `completed` | Ready to play | ✅ set | null |
| `failed` | Error occurred | null | ✅ set |
