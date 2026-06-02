# IndicVoice AI — Production Architecture (Phase 3B)

> **Document purpose:** Design reference for migrating IndicVoice AI from a local FastAPI monolith
> to a Cloudflare Pages + Workers production deployment.
>
> **Scope:** Architecture analysis, TTS/VC model evaluation, deployment design, storage, API gateway,
> security, and cost estimation. No code is changed in this phase.

---

## Table of Contents

1. [Current Architecture](#1-current-architecture)
2. [Cloudflare Deployment Architecture](#2-cloudflare-deployment-architecture)
3. [TTS Strategy](#3-tts-strategy)
4. [Voice Cloning Strategy](#4-voice-cloning-strategy)
5. [Storage Strategy](#5-storage-strategy)
6. [API Gateway Design](#6-api-gateway-design)
7. [Security Design](#7-security-design)
8. [Cost Estimation](#8-cost-estimation)
9. [Recommendation Matrix](#9-recommendation-matrix)
10. [Migration Roadmap](#10-migration-roadmap)

---

## 1. Current Architecture

### 1.1 Stack Overview

```
┌─────────────────────────────────────────────────────────┐
│                   LOCAL DEVELOPMENT                      │
│                                                         │
│  ┌──────────────────┐        ┌──────────────────────┐  │
│  │   FRONTEND       │        │   BACKEND            │  │
│  │                  │        │                      │  │
│  │  React 18        │        │  FastAPI 0.111       │  │
│  │  Vite 5          │──────▶ │  uvicorn             │  │
│  │  Tailwind CSS 3  │  HTTP  │  edge-tts            │  │
│  │                  │  POST  │  miniaudio           │  │
│  │  localhost:5173  │        │  localhost:8000      │  │
│  └──────────────────┘        └──────────┬───────────┘  │
│                                         │               │
│                               ┌─────────▼──────────┐   │
│                               │   LOCAL FILESYSTEM  │   │
│                               │  uploads/  (WAV/MP3)│   │
│                               │  outputs/  (WAV)    │   │
│                               └────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 1.2 Request Flow (Current)

```
Browser
  │
  ├─ GET  /           → React SPA (Vite dev server)
  ├─ GET  /health     → FastAPI /health (Vite proxy → :8000)
  └─ POST /generate   → FastAPI /generate (Vite proxy → :8000)
                            │
                            ├─ validates: language, text, file
                            ├─ saves:  uploads/{job_id}_sample.ext
                            ├─ calls:  edge-tts → Microsoft Speech API
                            ├─ decodes: MP3/WebM → PCM (miniaudio)
                            ├─ wraps:  PCM → RIFF/WAV
                            └─ saves + returns: outputs/{job_id}_output.wav
```

### 1.3 Current Dependency Map

| Component | Technology | Runtime | Notes |
|-----------|-----------|---------|-------|
| Frontend | React 18 + Vite 5 + Tailwind CSS 3 | Browser | Static assets after build |
| API server | FastAPI 0.111 + uvicorn | Python 3.10+, Linux/macOS/Windows | Long-running process |
| TTS engine | edge-tts 6.1.9 | Async Python + internet | Calls `speech.platform.bing.com` |
| Audio decode | miniaudio 1.71 | Pure Python, CPU | No ffmpeg dependency |
| File storage | Local filesystem | OS filesystem | No cloud storage |
| Dependency transport | pip + venv | Python package manager | ~150 MB installed |

### 1.4 Current Architecture Limitations

| Limitation | Impact | Cloudflare Blocker? |
|-----------|--------|---------------------|
| Long-running uvicorn process | Cannot run in serverless | ✅ Yes — Workers have no persistent processes |
| Local filesystem (`uploads/`, `outputs/`) | No shared state across instances | ✅ Yes — Workers have no filesystem |
| edge-tts runtime (Python asyncio) | Python not supported in Workers | ✅ Yes — Workers are V8 JavaScript only |
| miniaudio (C extension) | No native extensions in Workers | ✅ Yes |
| No authentication | Public API, abuse risk | ⚠️ Security gap |
| No job queue | Synchronous request blocks until TTS completes | ⚠️ Performance/scale gap |
| No CDN | Static assets served from dev server only | ⚠️ Production gap |

---

## 2. Cloudflare Deployment Architecture

### 2.1 Platform Constraints

```
┌─────────────────────────────────────────────────────────────┐
│              CLOUDFLARE WORKERS CONSTRAINTS                  │
├─────────────────┬───────────────────────────────────────────┤
│ CPU time        │ 50 ms (free) / 30 s (Paid)                │
│ Memory          │ 128 MB per request                        │
│ Runtime         │ V8 JavaScript / TypeScript (no Python)    │
│ Filesystem      │ ❌ None (read-only /tmp in some bindings) │
│ GPU             │ ❌ None                                    │
│ Persistent proc │ ❌ None (stateless per request)           │
│ Max body size   │ 100 MB (request), 500 MB (R2 upload)      │
│ Execution model │ Request/response, no long polling default │
└─────────────────┴───────────────────────────────────────────┘
```

### 2.2 Target Production Architecture

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                    CLOUDFLARE PRODUCTION DEPLOYMENT                        ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║  ┌──────────────────────────────────────────────────────────────────┐    ║
║  │                  CLOUDFLARE PAGES                                 │    ║
║  │  (CDN-served static React SPA — globally distributed)            │    ║
║  │                                                                   │    ║
║  │   indicvoice-ai.pages.dev  (or custom domain)                    │    ║
║  │   ├── index.html  (entrypoint)                                   │    ║
║  │   ├── assets/    (Vite-compiled JS/CSS bundles)                  │    ║
║  │   └── _headers   (CSP, cache-control, HSTS)                      │    ║
║  └──────────────────────┬────────────────────────────────────────┘    ║
║                          │  API calls  /generate, /health              ║
║                          ▼                                              ║
║  ┌─────────────────────────────────────────────────────────────────┐   ║
║  │               CLOUDFLARE WORKERS — API GATEWAY                   │   ║
║  │  (TypeScript, stateless, globally distributed edge nodes)        │   ║
║  │                                                                   │   ║
║  │  POST /generate                                                   │   ║
║  │   ├── 1. Auth: verify API key or session token (KV lookup)       │   ║
║  │   ├── 2. Rate limit: check KV counter per IP/key                 │   ║
║  │   ├── 3. Validate: language, text len, file ext, file size       │   ║
║  │   ├── 4. Upload sample → R2 bucket (uploads/)                    │   ║
║  │   ├── 5. Create job record in KV  {status: "queued"}             │   ║
║  │   └── 6. Enqueue job → Cloudflare Queue                          │   ║
║  │                                                                   │   ║
║  │  GET /job/{id}   → KV lookup → return status/output URL          │   ║
║  │  GET /health     → static JSON (no external calls)               │   ║
║  └──────────────────────┬──────────────────────────────────────────┘   ║
║                          │  Queue message                              ║
║                          ▼                                              ║
║  ┌─────────────────────────────────────────────────────────────────┐   ║
║  │            CLOUDFLARE QUEUE CONSUMER WORKER                      │   ║
║  │  (processes TTS jobs asynchronously)                             │   ║
║  │                                                                   │   ║
║  │   ├── Dequeue job                                                │   ║
║  │   ├── Read voice sample ← R2 uploads/                            │   ║
║  │   ├── Call TTS inference service (HTTP)                          │   ║
║  │   ├── Receive WAV bytes                                          │   ║
║  │   ├── Write output WAV → R2 outputs/                             │   ║
║  │   └── Update KV job status → {status: "done", url: "R2_URL"}    │   ║
║  └──────────────────────┬──────────────────────────────────────────┘   ║
║                          │  HTTPS inference request                    ║
║                          ▼                                              ║
║  ┌─────────────────────────────────────────────────────────────────┐   ║
║  │            EXTERNAL INFERENCE TIER  (see §3)                     │   ║
║  │                                                                   │   ║
║  │  Option A: Microsoft Speech API (edge-tts compatible, REST)      │   ║
║  │  Option B: Replicate.com (Coqui XTTS-v2 / OpenVoice)            │   ║
║  │  Option C: Modal.com / RunPod / Beam (custom FastAPI container)  │   ║
║  │  Option D: Workers AI (if/when Indic TTS model is added)         │   ║
║  └──────────────────────┬──────────────────────────────────────────┘   ║
║                          │                                              ║
║  ┌───────────────────────▼────────────────────────────────────────┐    ║
║  │                  CLOUDFLARE DATA SERVICES                        │    ║
║  │                                                                   │    ║
║  │  R2 Bucket "indicvoice-storage"                                   │    ║
║  │   ├── uploads/{job_id}_sample.{ext}  (voice sample input)        │    ║
║  │   └── outputs/{job_id}_output.wav    (synthesised speech output) │    ║
║  │                                                                   │    ║
║  │  KV Namespace "indicvoice-jobs"                                   │    ║
║  │   └── {job_id} → {status, lang, text_len, created_at, url}       │    ║
║  │                                                                   │    ║
║  │  D1 Database "indicvoice-db"  (future: users, billing, usage)    │    ║
║  └───────────────────────────────────────────────────────────────────┘    ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### 2.3 Async Job Flow (Production)

```
Client (Browser)
    │
    ├─ POST /generate  multipart(audio, text, language)
    │       │
    │       │  Worker (API Gateway)
    │       ├─ auth + validate
    │       ├─ upload audio → R2
    │       ├─ write KV: job_id → {status:"queued"}
    │       ├─ enqueue → CF Queue
    │       └─ 202 Accepted  { job_id, poll_url }
    │
    ├─ GET /job/{job_id}  (poll every 2s)
    │       │
    │       │  Worker (status check)
    │       └─ KV get job_id → { status:"queued"|"processing"|"done"|"error" }
    │                               │
    │             (when "done") ────┴── return { wav_url, expires_in }
    │
    └─ GET {wav_url}  (pre-signed R2 URL, TTL 1 hour)
           │
           └─ Audio player plays / download button saves
```

### 2.4 Synchronous Fallback (Small Text / Low Latency)

For short text (≤ 100 chars), a direct synchronous path is viable if TTS responds in < 25s:

```
Client → POST /generate
           │
           Worker calls TTS API inline (awaited)
           │
           ├── TTS responds within 25s → stream WAV bytes directly
           └── TTS timeout → fall back to async path
```

---

## 3. TTS Strategy

### 3.1 Model Evaluation

#### 3.1.1 edge-tts (Microsoft Neural TTS)

```
┌───────────────────────────────────────────────────────────────┐
│  edge-tts                                                      │
├──────────────────┬────────────────────────────────────────────┤
│ Type             │ Online Neural TTS (REST streaming)          │
│ Provider         │ Microsoft Azure Speech (free tier)          │
│ Runtime          │ Python asyncio client                       │
│ Model size       │ N/A (cloud model)                           │
│ GPU required     │ No                                          │
│ Internet         │ Yes (mandatory)                             │
│ Indic support    │ te, ta, hi, en-IN — full native voices      │
│ Voice quality    │ ★★★★☆  Neural quality, natural prosody     │
│ Latency          │ 1–3 s per request                           │
│ Cost             │ Free (unofficial API, no SLA)               │
│ Workers compat   │ ❌ Python runtime, unofficial API           │
│ Rate limits      │ Unknown — unofficial access, may throttle   │
│ Production risk  │ HIGH — unofficial, can be blocked anytime   │
└──────────────────┴────────────────────────────────────────────┘

Verdict:
  ✅ LOCAL DEVELOPMENT ONLY
  ❌ Production: unofficial API, Python-only client, no SLA, throttle risk
  🔄 Production alternative: Azure Cognitive Services Speech SDK (official,
     REST API, same voices, ~$4/1M chars, Workers-compatible via HTTP)
```

#### 3.1.2 Piper TTS

```
┌───────────────────────────────────────────────────────────────┐
│  Piper TTS  (rhasspy/piper)                                    │
├──────────────────┬────────────────────────────────────────────┤
│ Type             │ Offline Neural TTS (ONNX)                   │
│ Provider         │ Open-source (Apache 2.0)                    │
│ Runtime          │ Python + onnxruntime, or C++ binary         │
│ Model sizes      │ Low: ~25 MB, Medium: ~63 MB, High: ~130 MB  │
│ GPU required     │ No (CPU ONNX inference)                     │
│ Internet         │ No (fully offline after model download)     │
│ Indic support    │ hi_IN only (medium quality)                 │
│                  │ ❌ No Telugu, No Tamil (as of 2024)         │
│ Voice quality    │ ★★★☆☆  Good for en/hi, limited Indic       │
│ Latency          │ 0.3–1.5 s on CPU (model-dependent)          │
│ Cost             │ Free (open-source)                          │
│ Workers compat   │ ❌ Python + ONNX, 63 MB model > 128 MB mem  │
│ Container compat │ ✅ Excellent (Docker, pip install)          │
│ Production risk  │ LOW (open-source, stable releases)          │
└──────────────────┴────────────────────────────────────────────┘

Verdict:
  ✅ LOCAL DEVELOPMENT — English + Hindi only
  ✅ PRODUCTION INFERENCE — run in Docker container on Modal/RunPod
  ❌ Cloudflare Workers — memory + Python constraints
  ❌ Telugu / Tamil support missing
  📌 Best fit: English/Hindi offline fallback when internet is unavailable
```

#### 3.1.3 XTTS-v2 (Coqui TTS)

```
┌───────────────────────────────────────────────────────────────┐
│  XTTS-v2  (coqui-ai/TTS)                                      │
├──────────────────┬────────────────────────────────────────────┤
│ Type             │ Zero-shot voice cloning TTS                 │
│ Provider         │ Coqui (CPML licence — non-commercial free)  │
│ Runtime          │ Python, PyTorch, CUDA preferred             │
│ Model size       │ ~1.9 GB (full checkpoint)                   │
│ GPU required     │ Strongly recommended (A10G / T4 minimum)    │
│ Internet         │ No after download                           │
│ Indic support    │ hi only (16 languages total, no te/ta)      │
│ Voice quality    │ ★★★★★  State-of-the-art cloning quality   │
│ Latency          │ 3–8 s GPU / 20–60 s CPU                    │
│ Cost (GPU)       │ ~$0.002–$0.008 per req (Replicate/Modal)   │
│ Workers compat   │ ❌ Completely incompatible                  │
│ Container compat │ ✅ Docker + NVIDIA runtime                  │
│ Licence          │ ⚠️  CPML — commercial use requires licence │
│ Production risk  │ MEDIUM (licence risk, GPU cost)             │
└──────────────────┴────────────────────────────────────────────┘

Verdict:
  ✅ LOCAL DEVELOPMENT — best quality for en/hi cloning tests
  ✅ EXTERNAL INFERENCE SERVICE — Replicate API (pay-per-second)
  ❌ Cloudflare Workers — incompatible in every dimension
  ❌ Telugu/Tamil — not supported natively
  📌 Phase 3B target: use for en/hi voice cloning via Replicate
  ⚠️  Licence: verify CPML terms before commercial launch
```

#### 3.1.4 OpenVoice (MyShell)

```
┌───────────────────────────────────────────────────────────────┐
│  OpenVoice v2  (myshell-ai/OpenVoice)                         │
├──────────────────┬────────────────────────────────────────────┤
│ Type             │ Instant voice cloning (tone-colour transfer)│
│ Provider         │ MyShell AI (MIT licence)                    │
│ Runtime          │ Python, PyTorch, CUDA preferred             │
│ Model size       │ ~300 MB (base) + ~200 MB (converter)        │
│ GPU required     │ Recommended (T4 sufficient)                 │
│ Internet         │ No after download                           │
│ Indic support    │ Cross-lingual — can clone voice into any    │
│                  │ supported lang; Indic quality varies         │
│ Voice quality    │ ★★★★☆  Excellent cross-lingual cloning     │
│ Latency          │ 2–5 s GPU / 15–30 s CPU                    │
│ Cost (GPU)       │ ~$0.001–$0.005 per req (Modal/RunPod)      │
│ Workers compat   │ ❌ Completely incompatible                  │
│ Container compat │ ✅ Docker + NVIDIA runtime                  │
│ Licence          │ ✅ MIT (commercial use permitted)           │
│ Production risk  │ LOW–MEDIUM (MIT, active maintenance)        │
└──────────────────┴────────────────────────────────────────────┘

Verdict:
  ✅ LOCAL DEVELOPMENT — excellent cloning tests across languages
  ✅ EXTERNAL INFERENCE SERVICE — Modal / RunPod serverless GPU
  ❌ Cloudflare Workers — completely incompatible
  📌 RECOMMENDED for Phase 3B voice cloning (MIT licence, lower GPU cost)
  📌 Strategy: use edge-tts/Piper as TTS base, OpenVoice for tone transfer
```

#### 3.1.5 Fish Speech (fishaudio/fish-speech)

```
┌───────────────────────────────────────────────────────────────┐
│  Fish Speech S2 Pro  (fishaudio/fish-speech)                  │
├──────────────────┬────────────────────────────────────────────┤
│ Type             │ Multilingual TTS + voice cloning            │
│ Provider         │ Fish Audio (CC-BY-NC-SA 4.0 base weights)  │
│ Runtime          │ Python, PyTorch, CUDA (A10G recommended)    │
│ Model size       │ ~2.5 GB                                     │
│ GPU required     │ Yes (VRAM ≥ 12 GB recommended)              │
│ Internet         │ No after download                           │
│ Indic support    │ 80+ languages incl. hi; te/ta: limited      │
│ Voice quality    │ ★★★★★  State-of-the-art (2024 benchmark)  │
│ Latency          │ 1–4 s GPU (A10G)                           │
│ Cost (API)       │ Fish Audio API: ~$0.015/1000 chars          │
│ Workers compat   │ ❌ Completely incompatible                  │
│ Container compat │ ✅ Docker + NVIDIA (requires ≥A10G)        │
│ Licence          │ ⚠️  CC-BY-NC-SA (non-commercial only)      │
│ Production risk  │ HIGH (licence restricts commercial use)     │
└──────────────────┴────────────────────────────────────────────┘

Verdict:
  ✅ LOCAL DEVELOPMENT — best quality multilingual experimentation
  ✅ EXTERNAL INFERENCE SERVICE — Fish Audio API (official, REST, affordable)
  ❌ Cloudflare Workers — completely incompatible
  ⚠️  Licence: CC-BY-NC-SA blocks commercial use with base weights
  📌 Use Fish Audio hosted API for production (avoids licence issue)
  📌 Long-term: consider for te/ta if quality improves
```

### 3.2 TTS Compatibility Matrix — Cloudflare

```
┌────────────────┬──────────┬──────────┬──────────┬──────────────┬──────────────┐
│ Model          │ te       │ ta       │ hi       │ en           │ CF Workers   │
├────────────────┼──────────┼──────────┼──────────┼──────────────┼──────────────┤
│ edge-tts       │ ✅ Neural│ ✅ Neural│ ✅ Neural│ ✅ Neural    │ ❌ Python    │
│ Piper TTS      │ ❌ None  │ ❌ None  │ ✅ ONNX  │ ✅ ONNX     │ ❌ Python    │
│ XTTS-v2        │ ❌ None  │ ❌ None  │ ✅ Clone │ ✅ Clone     │ ❌ PyTorch   │
│ OpenVoice v2   │ ⚠️ Vary  │ ⚠️ Vary  │ ✅ Clone │ ✅ Clone     │ ❌ PyTorch   │
│ Fish Speech    │ ⚠️ Ltd   │ ⚠️ Ltd   │ ✅ Multi │ ✅ Multi     │ ❌ PyTorch   │
│ Azure Speech   │ ✅ REST  │ ✅ REST  │ ✅ REST  │ ✅ REST      │ ✅ HTTP API  │
│ Workers AI     │ ❌       │ ❌       │ ❌       │ ✅ (en only) │ ✅ Native    │
└────────────────┴──────────┴──────────┴──────────┴──────────────┴──────────────┘
```

### 3.3 Production TTS Strategy Decision

```
PRODUCTION TTS PIPELINE
═══════════════════════

Phase 3A (current — local only):
  All languages → edge-tts → Microsoft unofficial API

Phase 3B (Cloudflare production):
  ┌─ te / ta (Telugu, Tamil) ────────────────────────────────────┐
  │  PRIMARY:   Azure Cognitive Services Speech REST API          │
  │             te-IN-ShrutiNeural / ta-IN-PallaviNeural          │
  │             ~$4.00 / 1M characters (standard tier)            │
  │  FALLBACK:  Fish Audio API (if Azure cost is prohibitive)     │
  └──────────────────────────────────────────────────────────────┘
  ┌─ hi (Hindi) ─────────────────────────────────────────────────┐
  │  PRIMARY:   Azure Cognitive Services Speech REST API          │
  │             hi-IN-SwaraNeural                                  │
  │  FALLBACK:  Piper TTS container (hi_IN-hindi-medium, offline) │
  └──────────────────────────────────────────────────────────────┘
  ┌─ en (English) ───────────────────────────────────────────────┐
  │  PRIMARY:   Workers AI @cf/meta/llama + TTS (if available)    │
  │             OR Azure Speech en-US-JennyNeural                 │
  │  FALLBACK:  Piper TTS container (en_US-lessac-medium)         │
  └──────────────────────────────────────────────────────────────┘
```

---

## 4. Voice Cloning Strategy

> **Note:** Voice cloning is not implemented in Phase 3A/3B. This section defines the
> architecture for Phase 4 implementation.

### 4.1 Cloning Pipeline Design

```
VOICE CLONING PIPELINE (Phase 4 target)
════════════════════════════════════════

  [Voice Sample Upload]
         │  WAV/MP3, ≥10s recommended
         ▼
  [R2: uploads/ ]
         │
         ▼
  [Speaker Encoder]  — extract speaker embedding (d-vector / x-vector)
         │  ~512-dim float vector
         ▼
  [R2: embeddings/{speaker_id}.npy ]  ← cached per user
         │
         ▼
  [TTS Synthesis with Speaker Conditioning]
    ┌────┴──────────────────────────────────┐
    │  Option A: XTTS-v2                     │
    │    - Pass reference wav as conditioning │
    │    - Native zero-shot cloning          │
    │    - Supports: en, hi                  │
    │                                        │
    │  Option B: OpenVoice v2                │
    │    - Run base TTS (edge/Piper)         │
    │    - Apply tone-colour transfer        │
    │    - Supports: cross-lingual          │
    │    - MIT licence ✅                    │
    │                                        │
    │  Option C: Fish Speech API             │
    │    - Upload reference sample           │
    │    - API handles cloning               │
    │    - Supports: 80+ languages           │
    └───────────────────────────────────────┘
         │  WAV output
         ▼
  [R2: outputs/{job_id}_output.wav]
         │
         ▼
  [Pre-signed URL returned to client]
```

### 4.2 Cloning Model Decision Matrix

```
┌──────────────┬────────────┬──────────┬──────────┬───────────┬──────────────┐
│ Model        │ te clone   │ ta clone │ hi clone │ en clone  │ Recommended  │
├──────────────┼────────────┼──────────┼──────────┼───────────┼──────────────┤
│ XTTS-v2      │ ❌ No      │ ❌ No    │ ✅ Yes   │ ✅ Yes    │ en + hi only │
│ OpenVoice v2 │ ✅ X-ling  │ ✅ X-ling│ ✅ Yes   │ ✅ Yes    │ ✅ All langs │
│ Fish Speech  │ ⚠️ Ltd     │ ⚠️ Ltd   │ ✅ Yes   │ ✅ Yes    │ Via API only │
│ Piper TTS    │ ❌ No      │ ❌ No    │ ✅ Fixed │ ✅ Fixed   │ No cloning   │
└──────────────┴────────────┴──────────┴──────────┴───────────┴──────────────┘

RECOMMENDATION FOR PHASE 4:
  Primary cloning engine: OpenVoice v2
    - MIT licence (commercial safe)
    - Cross-lingual transfer covers te/ta via tone transfer
    - Lower GPU memory than XTTS-v2
    - Active maintained repo

  Deployment target: Modal.com serverless GPU (A10G, ~$0.001–$0.005/req)
  Inference API: FastAPI container on Modal, called by CF Queue Consumer Worker
```

### 4.3 Speaker Embedding Cache Strategy

```
[First request with new voice sample]
  POST /generate  (audio + text + language)
      │
      Worker: compute speaker_id = SHA256(audio_bytes)[0:16]
      │
      ├─ Check R2: embeddings/{speaker_id}.npy  → MISS
      │
      └─ Send to inference service WITH full audio bytes
              │
              Inference: extract embedding + synthesise
              │
              ├─ Store embedding → R2: embeddings/{speaker_id}.npy
              └─ Return WAV

[Subsequent requests from same speaker]
  POST /generate  (audio + text + language)
      │
      Worker: compute speaker_id = SHA256(audio_bytes)[0:16]
      │
      ├─ Check R2: embeddings/{speaker_id}.npy  → HIT
      │
      └─ Send to inference service WITH embedding bytes only
              │  (faster — no re-encoding)
              Inference: synthesise using cached embedding
              └─ Return WAV
```

---

## 5. Storage Strategy

### 5.1 Cloudflare R2 Design

```
R2 BUCKET: indicvoice-storage
══════════════════════════════

  uploads/
    {job_id}_sample.{ext}        ← uploaded voice sample (raw)
    TTL: 24 hours (lifecycle rule)

  outputs/
    {job_id}_output.wav           ← synthesised WAV output
    TTL: 7 days (lifecycle rule)
    Access: pre-signed URL, 1 hour expiry

  embeddings/
    {speaker_id}.npy              ← cached speaker embedding (NumPy)
    TTL: 90 days (or user-managed)
    Access: private (Worker-only)

  models/ (future — if self-hosting Piper)
    piper/en_US-lessac-medium.onnx
    piper/en_US-lessac-medium.onnx.json
    piper/hi_IN-hindi-medium.onnx
    piper/hi_IN-hindi-medium.onnx.json
    TTL: permanent

BUCKET POLICY:
  - CORS: allow GET from indicvoice-ai.pages.dev only
  - Pre-signed URLs: generated by Worker, 1-hour TTL
  - Public access: disabled (all access via pre-signed URLs)
  - Egress: R2 → Workers is free; R2 → public incurs egress cost
```

### 5.2 KV Namespace Design

```
KV NAMESPACE: indicvoice-jobs
══════════════════════════════

  Key:   {job_id}                    (UUID v4 hex)
  Value: JSON string:
  {
    "job_id":     "abc123...",
    "status":     "queued" | "processing" | "done" | "error",
    "language":   "te" | "ta" | "hi" | "en",
    "voice":      "te-IN-ShrutiNeural",
    "text_len":   42,
    "created_at": 1717200000,
    "updated_at": 1717200005,
    "output_key": "outputs/abc123_output.wav",  // set when done
    "error":      null | "error message string"
  }
  TTL:   8 hours (auto-expiry)

KV NAMESPACE: indicvoice-ratelimits
  Key:   "rl:{ip}"  or  "rl:{api_key}"
  Value: request count integer (string)
  TTL:   60 seconds (sliding window)
```

### 5.3 D1 Database Schema (future — Phase 4)

```sql
-- indicvoice-db (Cloudflare D1 / SQLite)

CREATE TABLE users (
    id          TEXT PRIMARY KEY,   -- UUID
    email       TEXT UNIQUE,
    api_key     TEXT UNIQUE,
    plan        TEXT DEFAULT 'free', -- free | pro | enterprise
    created_at  INTEGER,
    updated_at  INTEGER
);

CREATE TABLE jobs (
    id          TEXT PRIMARY KEY,   -- job_id
    user_id     TEXT REFERENCES users(id),
    language    TEXT NOT NULL,
    text_len    INTEGER,
    status      TEXT DEFAULT 'queued',
    voice       TEXT,
    duration_s  REAL,
    bytes_out   INTEGER,
    created_at  INTEGER,
    completed_at INTEGER
);

CREATE TABLE usage (
    user_id     TEXT REFERENCES users(id),
    month       TEXT,               -- YYYY-MM
    requests    INTEGER DEFAULT 0,
    chars_total INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, month)
);
```

### 5.4 Storage Cost Summary

```
┌─────────────────────┬───────────────┬──────────────────────────────┐
│ Service             │ Free Tier     │ Paid (per unit)               │
├─────────────────────┼───────────────┼──────────────────────────────┤
│ R2 Storage          │ 10 GB/month   │ $0.015 / GB-month             │
│ R2 Class A ops      │ 1M/month      │ $4.50 / 1M ops (PUT/POST)     │
│ R2 Class B ops      │ 10M/month     │ $0.36 / 1M ops (GET)          │
│ R2 Egress           │ Free          │ $0.00 (R2 → internet free)    │
│ KV reads            │ 10M/month     │ $0.50 / 1M reads              │
│ KV writes           │ 1M/month      │ $5.00 / 1M writes             │
│ D1 rows read        │ 25M/month     │ $0.001 / 1M rows              │
│ D1 rows written     │ 50K/month     │ $1.00 / 1M rows               │
└─────────────────────┴───────────────┴──────────────────────────────┘
```

---

## 6. API Gateway Design

### 6.1 Worker Routes

```
CLOUDFLARE WORKER — Route Table
════════════════════════════════

  GET  /health
    → Return: { status, version, tts_engine, supported_languages }
    → No external calls. Pure static response.
    → Cache: stale-while-revalidate 60s

  POST /generate
    → Accept: multipart/form-data { audio, text, language }
    → Sync mode  (text ≤ 100 chars): return WAV bytes directly
    → Async mode (text > 100 chars): return 202 + job_id + poll_url
    → Auth: API key header X-API-Key (KV lookup)
    → Rate limit: 10 req/min per IP (free), 100 req/min per API key (pro)

  GET  /job/{job_id}
    → Return: { status, output_url?, error? }
    → output_url = pre-signed R2 URL (1h TTL, only when status=done)
    → Auth: same API key that created the job

  GET  /voices
    → Return: list of available voices per language
    → Static JSON, no external calls
    → Cache: 1 hour

  DELETE /job/{job_id}
    → Delete R2 upload + output + KV entry
    → Auth: job creator only
    → For GDPR compliance

  POST /feedback  (future)
    → Accept: { job_id, rating, comment }
    → Write to D1 feedback table
```

### 6.2 Worker Architecture (TypeScript)

```
src/
  index.ts          ← main Worker entrypoint (router)
  handlers/
    health.ts       ← GET /health
    generate.ts     ← POST /generate (sync + async dispatch)
    job.ts          ← GET /job/:id, DELETE /job/:id
    voices.ts       ← GET /voices
  lib/
    auth.ts         ← API key validation via KV
    ratelimit.ts    ← sliding window counter via KV
    validation.ts   ← input validation (language, text, file)
    r2.ts           ← R2 upload/download/presign helpers
    queue.ts        ← enqueue job to CF Queue
    tts.ts          ← HTTP client to inference service
  types.ts          ← shared TypeScript interfaces
  wrangler.toml     ← CF Worker config (routes, bindings, env)
```

### 6.3 Wrangler Configuration (skeleton)

```toml
# wrangler.toml
name = "indicvoice-api"
main = "src/index.ts"
compatibility_date = "2024-06-01"

[[r2_buckets]]
binding = "STORAGE"
bucket_name = "indicvoice-storage"

[[kv_namespaces]]
binding = "JOBS"
id = "<kv-namespace-id>"

[[kv_namespaces]]
binding = "RATELIMITS"
id = "<kv-ratelimit-namespace-id>"

[[queues.producers]]
binding = "JOB_QUEUE"
queue = "indicvoice-jobs"

[[queues.consumers]]
queue = "indicvoice-jobs"
max_batch_size = 5
max_batch_timeout = 30

[[d1_databases]]
binding = "DB"
database_name = "indicvoice-db"
database_id = "<d1-db-id>"

[vars]
TTS_SERVICE_URL = "https://indicvoice-inference.modal.run"
AZURE_SPEECH_REGION = "eastus"

[secrets]
# Set via: wrangler secret put AZURE_SPEECH_KEY
# Set via: wrangler secret put TTS_SERVICE_SECRET
```

### 6.4 CORS and Headers Policy

```
Allowed Origins:
  https://indicvoice-ai.pages.dev
  https://indicvoice.ai  (custom domain)
  http://localhost:5173   (dev only, removed in production)

Allowed Methods:  GET, POST, DELETE, OPTIONS
Allowed Headers:  Content-Type, X-API-Key, X-Request-ID
Exposed Headers:  X-Job-Id, X-Language, X-Voice, X-Request-ID

Cache-Control:
  /health   → max-age=60, stale-while-revalidate=300
  /voices   → max-age=3600
  /generate → no-store
  /job/:id  → no-store
```

---

## 7. Security Design

### 7.1 Threat Model

```
┌──────────────────────────────────────────────────────────────────┐
│                    THREAT SURFACE                                  │
├────────────────────┬─────────────────────────────────────────────┤
│ Threat             │ Mitigation                                    │
├────────────────────┼─────────────────────────────────────────────┤
│ Unauthenticated    │ API key required (X-API-Key header)           │
│ abuse / scraping   │ Keys stored in KV, rotatable                  │
│                    │ Rate limit: 10/min free, 100/min pro          │
├────────────────────┼─────────────────────────────────────────────┤
│ DDoS / volumetric  │ Cloudflare DDoS protection (built-in)         │
│ attack             │ Workers rate limiting at edge                 │
│                    │ Cloudflare WAF rules                          │
├────────────────────┼─────────────────────────────────────────────┤
│ Malicious file     │ File extension whitelist (.wav .mp3 .ogg …)  │
│ upload             │ File size cap (50 MB)                         │
│                    │ No execution — stored to R2, read-only        │
│                    │ Malware scan: ClamAV on inference container   │
├────────────────────┼─────────────────────────────────────────────┤
│ Prompt injection   │ Text length cap (500 chars)                   │
│ / content abuse    │ Unicode normalisation before TTS              │
│                    │ Output format: audio only (no text returned)  │
├────────────────────┼─────────────────────────────────────────────┤
│ Data exfiltration  │ R2 pre-signed URLs only (1h TTL)              │
│ of audio output    │ No public bucket access                       │
│                    │ Job access restricted to creator API key      │
├────────────────────┼─────────────────────────────────────────────┤
│ Secret leakage     │ Secrets via wrangler secret (encrypted KV)    │
│                    │ No secrets in source code                     │
│                    │ .env.local in .gitignore                      │
├────────────────────┼─────────────────────────────────────────────┤
│ Inference service  │ HMAC-signed requests between Worker → Modal  │
│ endpoint abuse     │ Modal endpoint auth + IP allowlist            │
├────────────────────┼─────────────────────────────────────────────┤
│ Cross-site attacks │ CORS restricted to allowed origins            │
│ (XSS, CSRF)        │ CSP headers on Pages (_headers file)          │
│                    │ SameSite=Strict on any cookies                │
├────────────────────┼─────────────────────────────────────────────┤
│ Stored voice data  │ User consent required before upload           │
│ privacy (GDPR)     │ DELETE /job/:id endpoint for erasure          │
│                    │ R2 TTL: uploads 24h, outputs 7d               │
│                    │ No voice data used for training               │
└────────────────────┴─────────────────────────────────────────────┘
```

### 7.2 Authentication Flow

```
API Key Authentication
══════════════════════

  Client request:
    POST /generate
    Headers: { "X-API-Key": "ivai_live_abc123..." }

  Worker:
    1. Extract X-API-Key header
    2. If missing → 401 Unauthorized
    3. KV GET: keys/{sha256(api_key)} → {user_id, plan, active}
    4. If not found → 401 Unauthorized
    5. If active=false → 403 Forbidden
    6. Rate limit check: KV GET rl:{api_key} → count
    7. If count > plan limit → 429 Too Many Requests
       (with Retry-After header)
    8. KV INCR rl:{api_key} (TTL 60s)
    9. Proceed with request

  Key format: ivai_{env}_{32-char-hex}
    env: live | test
    Example: ivai_live_a1b2c3d4e5f6...

  Key rotation:
    POST /keys/rotate (future auth endpoint)
    → Generate new key, deprecate old key (24h grace)
```

### 7.3 Content Security Policy (_headers)

```
# frontend/public/_headers
https://indicvoice-ai.pages.dev/*
  Content-Security-Policy: default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; media-src 'self' blob:; connect-src 'self' https://indicvoice-api.workers.dev https://*.indicvoice.ai; img-src 'self' data:; frame-ancestors 'none'
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: microphone=(), camera=(), geolocation=()
  Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
```

---

## 8. Cost Estimation

### 8.1 Cloudflare Platform Costs

```
SCENARIO: 10,000 requests/month (small production)
══════════════════════════════════════════════════

  Cloudflare Workers (Paid plan required for Queues + D1)
  ──────────────────────────────────────────────────────
  Workers Paid plan                     $5.00 / month
  Worker requests (10K)                 Included in paid plan
    (Free: 100K/day; Paid: 10M/month)
  Queue messages (10K)                  $0.00
    (Free: 1M/month)

  R2 Storage
  ──────────
  Uploads (50MB × 10K × 1 day TTL)     ~16 GB·days → ~0.5 GB avg  $0.01
  Outputs (300KB × 10K × 7 day TTL)    ~20 GB·days → ~0.67 GB avg  $0.01
  R2 PUT ops (10K uploads + 10K outputs) 20K ops                    $0.09
  R2 GET ops (10K downloads × 2)        20K ops                    $0.01

  KV
  ──
  KV writes (10K job creates + 10K updates) 20K writes              $0.00 (free tier)
  KV reads  (10K status polls × avg 3)      30K reads               $0.00 (free tier)

  D1 (minimal usage)
  ──
  Row writes ~30K (jobs + usage)            ~30K writes              $0.00 (free tier)
  Row reads  ~50K                           ~50K reads               $0.00 (free tier)

  CLOUDFLARE SUBTOTAL:                                              ~$5.12 / month
```

```
SCENARIO: 100,000 requests/month (growth stage)
════════════════════════════════════════════════

  Workers Paid plan                     $5.00
  Worker requests (100K)                Included
  R2 Storage (scaled)                   $0.50
  R2 ops (200K)                         $1.00
  KV writes (200K)                      $1.00
  D1                                    $0.10

  CLOUDFLARE SUBTOTAL:                 ~$7.60 / month
```

### 8.2 TTS Inference Costs

```
SCENARIO: 10,000 requests/month, avg 100 chars/request
═══════════════════════════════════════════════════════

  Option A: Azure Cognitive Services Speech (Official REST)
  ─────────────────────────────────────────────────────────
  Standard Neural TTS
    1M char free/month → covers 10,000 × 100 = 1M chars → FREE
  Beyond free: $4.00 / 1M chars (Neural Standard)
              $16.00 / 1M chars (Neural Custom)

  Cost at 10K req/month (avg 100 chars): $0.00 (within free tier)
  Cost at 100K req/month (avg 100 chars): $0.00 (1M chars free, then $4/1M)
  Cost at 1M req/month (avg 100 chars): $400.00 (100M chars × $4/1M)

  Option B: Modal.com (OpenVoice / XTTS-v2 container)
  ─────────────────────────────────────────────────────
  A10G GPU: $0.000583 / second
  Avg inference time: ~3s (OpenVoice) → $0.00175 / request
  10K requests:  ~$17.50 / month
  100K requests: ~$175.00 / month
  Cold start: ~5s (billed) — mitigate with keep_warm=1

  Option C: Replicate.com (XTTS-v2)
  ──────────────────────────────────
  coqui/xtts-v2: ~$0.0023/prediction (avg ~4s on A40)
  10K requests:  ~$23.00 / month
  100K requests: ~$230.00 / month

  Option D: Fish Audio API
  ─────────────────────────
  $0.015 / 1,000 chars (~$0.0015/request at 100 chars)
  10K requests:  ~$15.00 / month
  100K requests: ~$150.00 / month

  Option E: Workers AI (English only)
  ─────────────────────────────────────
  @cf/myshell-ai/melotts (or equivalent, if available)
  Free: 10K neurons/day → ~333 req/day
  Paid: $0.011 / 1K neurons (varies by model)
  10K requests: ~$0.00–$2.00 / month
```

### 8.3 Recommended Cost-Optimal Stack per Tier

```
┌──────────────────┬──────────────────────────────────────────────────────┐
│ Tier             │ Stack                                         $/month │
├──────────────────┼──────────────────────────────────────────────────────┤
│ Free / Dev       │ edge-tts (local) + CF Pages free + R2 free    $0.00  │
├──────────────────┼──────────────────────────────────────────────────────┤
│ Small Prod       │ Azure Speech (1M char free)                           │
│ ≤10K req/month   │ + CF Workers Paid + R2                        $5.12  │
├──────────────────┼──────────────────────────────────────────────────────┤
│ Growth           │ Azure Speech (~$4/1M chars)                           │
│ ≤100K req/month  │ + CF Workers Paid + R2                       ~$12    │
├──────────────────┼──────────────────────────────────────────────────────┤
│ Voice Cloning    │ Azure TTS (base) + Modal OpenVoice (GPU)              │
│ (Phase 4)        │ + CF Workers Paid + R2                       ~$30+   │
│ ≤10K clones/mo   │   (scales linearly with GPU requests)                │
├──────────────────┼──────────────────────────────────────────────────────┤
│ Scale            │ Azure Speech + Modal (auto-scale)                     │
│ ≤1M req/month    │ + CF Workers Paid + R2 + D1                 ~$450    │
└──────────────────┴──────────────────────────────────────────────────────┘
```

---

## 9. Recommendation Matrix

### 9.1 Model Deployment Classification

```
┌──────────────────┬───────────────────┬──────────────────┬────────────────────┐
│ Model            │ Local Dev         │ Production       │ External Inference │
├──────────────────┼───────────────────┼──────────────────┼────────────────────┤
│ edge-tts         │ ✅ PRIMARY        │ ❌ Unofficial    │ ❌ (Python only)   │
│                  │  (all 4 langs)    │    No SLA        │                    │
├──────────────────┼───────────────────┼──────────────────┼────────────────────┤
│ Piper TTS        │ ✅ SECONDARY      │ ⚠️  en+hi only   │ ✅ Docker on       │
│                  │  (en, hi offline) │    Container     │   Modal/RunPod     │
├──────────────────┼───────────────────┼──────────────────┼────────────────────┤
│ Azure Speech     │ ✅ optional       │ ✅ PRIMARY       │ ✅ REST API        │
│ (official)       │  (requires key)   │   all 4 langs    │   CF Worker calls  │
├──────────────────┼───────────────────┼──────────────────┼────────────────────┤
│ XTTS-v2          │ ✅ CLONING DEV    │ ⚠️  Licence risk │ ✅ Replicate.com   │
│                  │  (en, hi)         │   GPU required   │   (pay-per-pred)   │
├──────────────────┼───────────────────┼──────────────────┼────────────────────┤
│ OpenVoice v2     │ ✅ CLONING DEV    │ ✅ RECOMMENDED   │ ✅ Modal.com       │
│                  │  (all langs)      │   MIT licence    │   serverless GPU   │
├──────────────────┼───────────────────┼──────────────────┼────────────────────┤
│ Fish Speech      │ ✅ RESEARCH ONLY  │ ⚠️  NC licence   │ ✅ Fish Audio API  │
│                  │  (quality eval)   │   via hosted API │   (avoid self-host)│
├──────────────────┼───────────────────┼──────────────────┼────────────────────┤
│ Workers AI TTS   │ ❌ Not useful     │ ✅ en only       │ N/A (native CF)    │
│ (if available)   │                   │   zero cost      │                    │
└──────────────────┴───────────────────┴──────────────────┴────────────────────┘
```

### 9.2 Phase Implementation Recommendation

```
PHASE 3A ✅ DONE — edge-tts local
  └─ All 4 languages: te/ta/hi/en via Microsoft Neural TTS (unofficial)

PHASE 3B (this phase) — Cloudflare architecture design
  └─ No code changes; this document defines the target

PHASE 3C (next) — Cloudflare Workers API Gateway
  ├─ Migrate FastAPI routes to Cloudflare Workers (TypeScript)
  ├─ Deploy React to Cloudflare Pages
  ├─ R2 for file storage
  ├─ KV for job tracking
  └─ Azure Speech API for TTS (official, REST, CF-compatible)

PHASE 4 — Voice Cloning
  ├─ OpenVoice v2 inference container on Modal.com
  ├─ Speaker embedding cache in R2
  ├─ Async job queue (CF Queues → Modal webhook)
  └─ D1 for user management + usage tracking
```

---

## 10. Migration Roadmap

```
WEEK 1 — Cloudflare Pages deployment
  ┌──────────────────────────────────────────────────────────────┐
  │  1. npm run build → upload frontend/dist to CF Pages         │
  │  2. Set custom domain indicvoice.ai on CF Pages              │
  │  3. Add _headers file (CSP, HSTS, X-Frame-Options)           │
  │  4. Verify SPA routing with _redirects file                  │
  └──────────────────────────────────────────────────────────────┘

WEEK 2 — Cloudflare Worker API Gateway
  ┌──────────────────────────────────────────────────────────────┐
  │  1. Scaffold Worker project (wrangler init)                  │
  │  2. Port /health route (pure static, no external calls)      │
  │  3. Port /generate route (validation + R2 upload + async)    │
  │  4. Implement KV job tracking                                │
  │  5. Add GET /job/:id polling endpoint                        │
  │  6. Update frontend: replace fetch('/generate') with async   │
  │     polling pattern (POST → 202 → poll /job/:id → play)      │
  └──────────────────────────────────────────────────────────────┘

WEEK 3 — Azure Speech TTS integration
  ┌──────────────────────────────────────────────────────────────┐
  │  1. Create Azure Cognitive Services resource (F0 free tier)  │
  │  2. Implement TTS HTTP client in Worker (REST + SSML)        │
  │  3. Decode audio in Worker or inference service              │
  │  4. Store output WAV to R2                                   │
  │  5. Generate pre-signed R2 URL for client                    │
  │  6. End-to-end test: browser → Pages → Worker → Azure → R2  │
  └──────────────────────────────────────────────────────────────┘

WEEK 4 — Security hardening + monitoring
  ┌──────────────────────────────────────────────────────────────┐
  │  1. API key authentication (KV-backed)                       │
  │  2. Rate limiting (KV sliding window)                        │
  │  3. Cloudflare WAF rules                                     │
  │  4. Worker Analytics + Logpush                               │
  │  5. Uptime monitoring (CF Healthcheck)                       │
  │  6. D1 usage tracking schema                                 │
  └──────────────────────────────────────────────────────────────┘

PHASE 4 (future) — Voice Cloning
  ┌──────────────────────────────────────────────────────────────┐
  │  1. OpenVoice v2 Docker container → deploy to Modal.com      │
  │  2. CF Queue → Queue Consumer Worker → Modal webhook         │
  │  3. Speaker embedding cache in R2                            │
  │  4. User auth + D1 usage tracking                            │
  └──────────────────────────────────────────────────────────────┘
```

---

*Document version 1.0 — Phase 3B — 2024*
*Next review: after Phase 3C (Cloudflare Workers migration) is complete*
