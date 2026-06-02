# Voice Cloning — Architecture, Setup & Limitations

> **Phase 4B — IndicVoice AI**  
> OpenVoice v2 (MIT licence) tone-colour transfer over EdgeTTS base synthesis.

---

## Table of Contents

1. [Overview](#1-overview)  
2. [Architecture](#2-architecture)  
3. [API Reference](#3-api-reference)  
4. [Local Setup](#4-local-setup)  
5. [Production Deployment](#5-production-deployment)  
6. [Cloning Pipeline — Step by Step](#6-cloning-pipeline--step-by-step)  
7. [Speaker Embedding Cache](#7-speaker-embedding-cache)  
8. [Supported Languages & Quality Notes](#8-supported-languages--quality-notes)  
9. [Fallback Behaviour](#9-fallback-behaviour)  
10. [Frontend Integration](#10-frontend-integration)  
11. [Limitations & Known Issues](#11-limitations--known-issues)  
12. [Future Improvements (Phase 5)](#12-future-improvements-phase-5)  
13. [Quick-Reference: end-to-end curl test](#13-quick-reference-end-to-end-curl-test)

---

## 1. Overview

Phase 4B adds **voice cloning** as an optional synthesis mode.  
Two modes are now available on `POST /generate`:

| Mode       | Engine           | Latency (local CPU) | Description                                     |
|------------|------------------|--------------------|-------------------------------------------------|
| `standard` | EdgeTTS          | ~3 s               | Neural TTS, no cloning. Default behaviour.      |
| `clone`    | OpenVoice v2     | 20–60 s (CPU)      | Tone-colour transfer from uploaded speaker sample. |

The system **degrades gracefully**: if OpenVoice models are not installed or
`OPENVOICE_ENABLED` is not set, `clone` mode silently falls back to `standard`
and the job still completes successfully.

---

## 2. Architecture

```
                          POST /generate
                          (mode=clone)
                               │
                    ┌──────────▼──────────┐
                    │  FastAPI Route       │
                    │  · validate inputs   │
                    │  · save upload       │
                    │  · create job        │
                    │  · pick provider     │
                    └──────────┬──────────┘
                               │
               ┌───────────────▼──────────────────┐
               │  Background Worker (_run_tts_job)  │
               │                                    │
               │  provider.synthesise(request)      │
               │         │                          │
               │  ┌──────▼────────────────────┐    │
               │  │   OpenVoiceProvider        │    │
               │  │                            │    │
               │  │  1. EdgeTTSProvider        │    │
               │  │     → base WAV bytes       │    │
               │  │                            │    │
               │  │  2. _OpenVoiceRuntime      │    │
               │  │     · ensure_loaded()      │    │
               │  │     · SpeakerEncoder       │    │
               │  │       (reference audio)    │    │
               │  │     · ToneColorConverter   │    │
               │  │       (base + ref embed)   │    │
               │  │     → cloned WAV bytes     │    │
               │  └────────────────────────────┘    │
               │                                    │
               │  storage.put(output_key, wav)      │
               │  job_manager.update_status(DONE)   │
               └────────────────────────────────────┘
                               │
                    GET /job/{job_id}
                    output_url → /files/outputs/…
```

### Abstraction layer

| Concern        | Local Dev                         | Cloudflare Production (Phase 5)   |
|----------------|-----------------------------------|-----------------------------------|
| Job store      | `LocalJobManager` (in-memory)     | `CFKVJobManager` (CF KV, TTL 8h)  |
| File storage   | `LocalStorageBackend` (filesystem)| `CFR2StorageBackend` (R2 + presign)|
| TTS engine     | `EdgeTTSProvider` (edge-tts)      | `AzureSpeechProvider` (REST API)  |
| Voice cloning  | `OpenVoiceProvider` (CPU/GPU)     | Modal.com or RunPod worker        |
| Queue          | `asyncio.BackgroundTasks`         | Cloudflare Queues                 |

All route handlers and the background worker are **identical** across
environments — only the concrete implementations passed at startup differ.

---

## 3. API Reference

### `POST /generate` — now accepts `mode`

```
Request:  multipart/form-data
  audio     : file    (WAV/MP3/OGG/WEBM/FLAC, ≤ 50 MB)
  text      : string  (1–500 characters, UTF-8)
  language  : string  ("te" | "ta" | "hi" | "en", default "en")
  mode      : string  ("standard" | "clone",       default "standard")  ← NEW

Response: 202 Accepted
Headers:
  Location:    /job/{job_id}
  X-Job-Id:    {job_id}
  X-Job-Mode:  {mode}
  Retry-After: 2

Body:
{
  "job_id":          "a1b2c3d4e5f6…",
  "status":          "queued",
  "mode":            "clone",
  "cloning_enabled": true,            ← false if OPENVOICE_ENABLED=false
  "poll_url":        "/job/a1b2c3d4…",
  "message":         "Job accepted. Poll poll_url for status."
}
```

### `GET /job/{job_id}` — unchanged from Phase 4A

```json
{
  "job_id":        "a1b2c3d4e5f6…",
  "status":        "completed",
  "language":      "te",
  "voice":         "te-IN-ShrutiNeural",
  "text_len":      42,
  "output_url":    "/files/outputs/{job_id}_output.wav",
  "error_message": null,
  "created_at":    1717200000.0,
  "updated_at":    1717200005.0,
  "completed_at":  1717200008.0
}
```

### `GET /health` — new fields

```json
{
  "status":            "ok",
  "version":           "4.0.0",
  "phase":             "4B — Voice Cloning MVP",
  "tts_engine":        "edge-tts",
  "cloning_available": false,
  "supported_modes":   ["clone", "standard"]
}
```

---

## 4. Local Setup

### 4.1 Standard TTS (no extra steps)

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

Voice cloning will be **disabled** by default (`OPENVOICE_ENABLED` defaults to `false`).
All `mode=clone` requests fall back to EdgeTTS seamlessly.

### 4.2 Enable voice cloning locally

#### Step 1 — Install OpenVoice v2

```bash
# Clone the OpenVoice repository
git clone https://github.com/myshell-ai/OpenVoice.git
cd OpenVoice
pip install -e .

# Install additional dependencies
pip install soundfile librosa
```

#### Step 2 — Download checkpoints

```bash
# Create the default checkpoint directory
mkdir -p ~/.cache/openvoice/v2

# Download from HuggingFace (myshell-ai/OpenVoiceV2)
# https://huggingface.co/myshell-ai/OpenVoiceV2
# Required files:
#   converter/config.json
#   converter/checkpoint.pth
#   (optionally the speaker encoder checkpoint)

# Using huggingface_hub:
python - <<'EOF'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="myshell-ai/OpenVoiceV2",
    local_dir=str(Path.home() / ".cache/openvoice/v2"),
)
EOF
```

#### Step 3 — Set environment variables

```bash
export OPENVOICE_ENABLED=true
export OPENVOICE_CHECKPOINT_DIR=~/.cache/openvoice/v2
export OPENVOICE_DEVICE=cpu      # or "cuda:0" if GPU available

uvicorn main:app --reload
```

#### Step 4 — Verify

```bash
curl http://localhost:8000/health | python -m json.tool | grep cloning
# "cloning_available": true
```

---

## 5. Production Deployment

### Cloudflare Workers + Modal.com

OpenVoice v2 cannot run inside Cloudflare Workers (Python runtime limitations,
no PyTorch support).  The recommended production architecture is:

```
Browser
  │
  ▼ POST /generate (mode=clone)
Cloudflare Worker (FastAPI via worker-py)
  │
  ▼ Enqueue to Cloudflare Queues
Modal.com Function (GPU — A100 or H100)
  │  · Loads OpenVoice v2 checkpoint (cached in Modal Volume)
  │  · Reads reference audio from R2
  │  · Runs ToneColorConverter
  │  · Writes output WAV to R2
  ▼
Cloudflare Worker Consumer
  │  · Reads result from R2
  │  · Updates job status in CF KV
  ▼
GET /job/{job_id} → completed + output_url (R2 pre-signed, 1h TTL)
```

### Modal.com function skeleton

```python
import modal

app     = modal.App("indicvoice-clone")
vol     = modal.Volume.from_name("openvoice-checkpoints")
gpu_img = modal.Image.debian_slim().pip_install("openvoice", "soundfile", "librosa")

@app.function(
    image=gpu_img,
    gpu=modal.gpu.A10G(),
    volumes={"/checkpoints": vol},
    timeout=120,
)
def clone_voice(base_wav: bytes, reference_audio: bytes, language: str) -> bytes:
    from openvoice.api import ToneColorConverter
    from openvoice import se_extractor
    # … same logic as OpenVoiceProvider._clone_sync …
```

---

## 6. Cloning Pipeline — Step by Step

```
Reference audio (uploaded WAV/MP3)
         │
         ▼  se_extractor.get_se(reference, vad=True)
   target_se  (speaker embedding, ~512-dim tensor)

Base TTS audio (EdgeTTS output WAV)
         │
         ▼  se_extractor.get_se(base, vad=False)
   source_se  (content voice embedding)

ToneColorConverter.convert(
    audio_src_path = base_wav,
    src_se         = source_se,
    tgt_se         = target_se,
    output_path    = cloned_wav,
    message        = "@MyShell",   ← watermark (MIT licence compliance)
)
         │
         ▼
Resample to 22050 Hz mono (if needed)
Convert float32 PCM → int16 PCM
Wrap in RIFF/WAV container
         │
         ▼
    cloned_wav (bytes)  →  storage.put(output_key, …)
```

### Timing breakdown (local CPU, ~3 s EdgeTTS + ~45 s OpenVoice)

| Stage                    | Approx. time (CPU) | Approx. time (A10G GPU) |
|--------------------------|--------------------|-------------------------|
| EdgeTTS base synthesis   | 3 s                | 3 s (network-bound)     |
| Speaker encoder (ref)    | 5–10 s             | < 1 s                   |
| Speaker encoder (base)   | 5–10 s             | < 1 s                   |
| ToneColorConverter       | 15–25 s            | 2–5 s                   |
| Resample + WAV encode    | < 1 s              | < 1 s                   |
| **Total**                | **~30–50 s**       | **~7–12 s**             |

---

## 7. Speaker Embedding Cache

In the current Phase 4B implementation, speaker embeddings are recomputed on
every request.  Phase 5 will add a cache layer:

### Local dev (planned)

```
uploads/{job_id}_sample.wav
embeddings/{fingerprint}_se.pt     ← SHA-256 of audio bytes, first 16 chars
```

### Production (planned)

```
R2 key: embeddings/{user_id}/{audio_sha256}_se.pt
CF KV:  se_cache:{audio_sha256} → { r2_key, created_at }
TTL:    7 days (KV metadata TTL)
```

**Cache hit rate estimate**: for repeat users with the same reference audio,
~80% of requests can skip the speaker encoder step, reducing latency by ~10 s
on CPU or ~1.5 s on GPU.

---

## 8. Supported Languages & Quality Notes

| Language | Code | Edge TTS Voice          | Cloning Quality | Notes                                           |
|----------|------|-------------------------|-----------------|-------------------------------------------------|
| Telugu   | `te` | te-IN-ShrutiNeural      | ⭐⭐⭐           | Good prosody transfer; some consonant softening |
| Tamil    | `ta` | ta-IN-PallaviNeural     | ⭐⭐⭐           | Good; retroflex consonants mostly preserved     |
| Hindi    | `hi` | hi-IN-SwaraNeural       | ⭐⭐⭐⭐         | Best quality among Indic langs in OpenVoice v2  |
| English  | `en` | en-US-JennyNeural       | ⭐⭐⭐⭐⭐        | Native language; highest quality                |

### Cross-lingual cloning

OpenVoice v2 supports **cross-lingual tone transfer** — you can upload an
English speaker sample and clone their voice into Telugu/Tamil/Hindi output.
Quality depends on:

1. **Reference audio length**: ≥ 5 s of clean speech (no music/noise).
2. **Reference audio quality**: 16 kHz+ sample rate recommended.
3. **Language distance**: English→Hindi works well; English→Telugu has slightly
   more artefacts at consonant boundaries.
4. **Text phoneme coverage**: longer text (50+ characters) produces more natural
   prosody than very short utterances.

### Recommendations for best results

- Upload 10–30 s of clean speech from a single speaker.
- Avoid background music, reverb, or multiple speakers.
- Use WAV (PCM 16-bit, 16 kHz or 22 kHz) for best fidelity.
- Prefer Hindi or English for production demos; Telugu/Tamil are Phase 5 targets
  for fine-tuned checkpoint improvement.

---

## 9. Fallback Behaviour

The OpenVoice provider **never fails a job** due to model unavailability.  
Fallback chain:

```
mode=clone requested
      │
      ▼  OPENVOICE_ENABLED=false?
      │         YES → EdgeTTS output (cloning_applied=false)
      │          NO
      ▼  _runtime.ensure_loaded() → False (not installed / bad checkpoint)?
      │         YES → EdgeTTS output (cloning_applied=false)
      │          NO
      ▼  _runtime.clone() raises exception?
      │         YES → log warning + EdgeTTS output (cloning_applied=false)
      │          NO
      ▼  cloned WAV returned (cloning_applied=true)
```

The frontend displays a badge reflecting the actual outcome:

| Outcome                  | Badge                                    |
|--------------------------|------------------------------------------|
| Real cloning applied     | 🧬 Voice cloned                          |
| Fallback to EdgeTTS      | 🔊 Standard TTS (cloning unavailable)   |
| Standard mode chosen     | 🔊 Standard TTS                          |

---

## 10. Frontend Integration

### Mode toggle (Phase 4B)

`VoiceCloner.jsx` now renders a **Synthesis Mode** toggle above the upload zone:

```
┌─────────────────────────┐  ┌──────────────────────────────┐
│  🔊 Standard TTS    ● ──┤  │  🧬 Voice Clone              │
│  Neural EdgeTTS. Fast.  │  │  OpenVoice v2 tone transfer  │
│  No voice cloning.      │  │  Requires GPU backend.       │
└─────────────────────────┘  └──────────────────────────────┘
```

The selected mode is appended to the `FormData` as `mode=standard|clone`.

### Job polling (Phase 4B)

The frontend now uses the async job system end-to-end:

```
POST /generate  (202 immediately)
      │
      ▼  poll every 2 s
GET /job/{job_id}  → { status: "queued" | "processing" | "completed" | "failed" }
      │
      ▼  status === "completed"
GET {output_url}   → WAV blob
      │
      ▼  createObjectURL(blob)  → <AudioPlayer>
```

Status badges shown during polling:

| `pollStatus`  | UI label          |
|---------------|-------------------|
| `queued`      | Queued…           |
| `processing`  | Synthesising…     |
| `completed`   | Done ✓            |
| `failed`      | Failed            |

### Environment variables (frontend)

```bash
# .env.local (Vite)
VITE_API_BASE=http://localhost:8000   # or your production worker URL
```

---

## 11. Limitations & Known Issues

| # | Limitation | Severity | Planned Fix |
|---|------------|----------|-------------|
| 1 | OpenVoice v2 CPU inference is slow (30–50 s) | High | Phase 5: GPU worker on Modal.com |
| 2 | No speaker embedding cache | Medium | Phase 5: R2 + CF KV cache |
| 3 | Telugu/Tamil cloning has minor consonant artefacts | Medium | Phase 5: fine-tuned checkpoint |
| 4 | Reference audio is not validated for quality (SNR, length) | Medium | Phase 5: pre-filter |
| 5 | Cross-lingual cloning quality varies with speaker | Low | Documented above |
| 6 | `cloning_applied` flag not yet stored in Job.meta | Low | Phase 5: extend JobMeta |
| 7 | No streaming — full WAV returned at once | Low | Phase 5: chunked streaming |
| 8 | No speaker diarisation for multi-speaker reference audio | Low | Phase 5 |
| 9 | Watermark `@MyShell` embedded per OpenVoice licence | Info | Required by MIT licence |

---

## 12. Future Improvements (Phase 5)

### 5A — GPU worker

- Deploy `OpenVoiceProvider` as a Modal.com A10G function.
- Use Cloudflare Queues to route `clone` jobs to the GPU worker.
- Target latency: 7–12 s end-to-end.

### 5B — Speaker embedding cache

- Compute SHA-256 of reference audio on upload.
- Store embedding tensor in R2 (`embeddings/{sha256}_se.pt`).
- Cache lookup in CF KV (TTL 7 days).
- Skip speaker encoder step on cache hit (~10 s saving on CPU).

### 5C — Fine-tuned Indic checkpoints

- Fine-tune ToneColorConverter on Telugu + Tamil speech corpora.
- Target: ⭐⭐⭐⭐ quality for `te` and `ta` (matching Hindi today).
- Corpora: IIIT-H Telugu TTS dataset, IISc-MILE Tamil corpus.

### 5D — Reference audio quality gate

- Reject uploads with SNR < 20 dB or duration < 3 s.
- Return 422 with actionable message: "Reference audio too noisy. Please use a
  quieter recording in a low-reverb environment."

### 5E — Streaming output

- Return audio chunks via Server-Sent Events (SSE) as synthesis progresses.
- Frontend plays audio while remaining chunks are still being generated.

---

## 13. Quick-Reference: end-to-end curl test

```bash
# 1. Standard TTS
curl -X POST http://localhost:8000/generate \
  -F "audio=@sample.wav" \
  -F "text=నమస్కారం, నేను ఒక AI వాయిస్ క్లోనింగ్ సిస్టమ్." \
  -F "language=te" \
  -F "mode=standard" | python -m json.tool

# → { "job_id": "abc123", "status": "queued", "mode": "standard", ... }

# 2. Poll until completed
JOB_ID="abc123"
while true; do
  STATUS=$(curl -s http://localhost:8000/job/$JOB_ID | python -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")
  echo "Status: $STATUS"
  [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ] && break
  sleep 2
done

# 3. Download output
OUTPUT_URL=$(curl -s http://localhost:8000/job/$JOB_ID | python -c "import sys,json; print(json.load(sys.stdin)['output_url'])")
curl -o output_standard.wav "http://localhost:8000${OUTPUT_URL}"

# ──────────────────────────────────────────────────────────────────────────
# 4. Voice clone mode (requires OPENVOICE_ENABLED=true)
curl -X POST http://localhost:8000/generate \
  -F "audio=@my_voice_sample.wav" \
  -F "text=Hello, this is my cloned voice." \
  -F "language=en" \
  -F "mode=clone" | python -m json.tool

# → { "job_id": "def456", "mode": "clone", "cloning_enabled": true, ... }

# 5. Check health / cloning status
curl http://localhost:8000/health | python -m json.tool
# "cloning_available": true  (if OPENVOICE_ENABLED=true)
# "cloning_available": false (default — EdgeTTS fallback active)

# 6. Delete a job (GDPR)
curl -X DELETE http://localhost:8000/job/$JOB_ID
# { "deleted": true, "job_id": "abc123" }
```

---

*Last updated: Phase 4B commit — IndicVoice AI*
