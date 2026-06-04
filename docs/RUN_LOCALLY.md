# Run IndicVoice AI Locally

Local-first setup: FastAPI backend + React/Vite frontend.
No Cloudflare account, no OpenVoice, no GPU required for Standard TTS.

---

## Quick answer: what do I need for Standard TTS?

| Requirement | Details |
|-------------|--------|
| Python 3.10+ | `python3 --version` |
| Node.js 18+ | `node --version` |
| Internet | EdgeTTS calls `speech.platform.bing.com` |
| GPU / PyTorch | ❌ Not needed for Standard TTS |
| OpenVoice | ❌ Not needed for Standard TTS |

Standard TTS installs **6 Python packages** (`fastapi`, `uvicorn`,
`python-multipart`, `aiofiles`, `edge-tts`, `miniaudio`) and nothing else.

---

## 1. Get the code

**Option A — Download ZIP** (no git required)
1. Open <https://github.com/dileep12072024-sudo/indic-voice-ai>
2. Click the green **Code** button → **Download ZIP**
3. Extract the ZIP to a folder on your machine
4. Open a terminal in the extracted `indic-voice-ai/` folder

**Option B — git clone**
```bash
git clone https://github.com/dileep12072024-sudo/indic-voice-ai.git
cd indic-voice-ai
```

---

## 2. Start the backend (Standard TTS only)

### One-command helper (recommended)
```bash
# macOS / Linux
bash scripts/start-backend.sh

# Windows
scripts\start-backend.bat
```

The script creates a virtual environment, installs all 6 Standard TTS
dependencies from `backend/requirements.txt`, and starts the FastAPI
server on **http://localhost:8000**.

### Manual steps
```bash
cd backend
python3 -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

> **Do NOT** install `requirements-clone.txt` if you only want Standard TTS.
> That file adds PyTorch + OpenVoice and is only needed for voice cloning.

**Verify the backend is healthy:**
```bash
curl http://localhost:8000/health
```
Expected response:
```json
{
  "status": "ok",
  "tts_engine": "edge-tts",
  "cloning_available": false,
  ...
}
```

---

## 3. Start the frontend

### One-command helper (recommended)
```bash
# macOS / Linux
bash scripts/start-frontend.sh

# Windows
scripts\start-frontend.bat
```

The script auto-creates `frontend/.env` pointing at `localhost:8000` if it
does not exist, installs npm dependencies, and starts Vite on
**http://localhost:5173**.

### Manual steps
```bash
# 1. Create the .env file (REQUIRED — without this Generate fails silently)
echo "VITE_API_BASE=http://localhost:8000" > frontend/.env
echo "VITE_DEV_BACKEND=http://localhost:8000" >> frontend/.env

# 2. Install and run
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

> **⚠️ Critical**: `frontend/.env` must exist before starting the frontend.
> Without it the Generate button targets port 5173 instead of 8000
> and cannot reach the backend. This is the most common first-run failure.

---

## 4. Using the app (Standard TTS)

1. Open <http://localhost:5173>
2. Choose **Standard TTS** mode
3. Upload any audio file (WAV / MP3 / OGG / WEBM / FLAC, max 50 MB)
   — the file is used as a placeholder; Standard TTS does not clone it
4. Select a language: **Telugu / Tamil / Hindi / English**
5. Enter text to synthesise (max 500 characters)
6. Click **Generate**
7. Wait ~3–10 seconds for the EdgeTTS neural voice
8. Play or **Download WAV** from the player

---

## 5. Expected ports

| Service | URL | Notes |
|---------|-----|-------|
| Backend (FastAPI) | <http://localhost:8000> | API + static file serving |
| Backend Swagger UI | <http://localhost:8000/docs> | Interactive API docs |
| Backend health | <http://localhost:8000/health> | Quick sanity check |
| Frontend (Vite) | <http://localhost:5173> | Main app |

---

## 6. Where audio output is saved

| Type | Path on disk | Served at |
|------|-------------|-----------|
| Uploaded sample | `backend/uploads/<job_id>_sample.<ext>` | (not publicly exposed) |
| Generated WAV | `backend/outputs/<job_id>_output.wav` | `http://localhost:8000/files/outputs/<job_id>_output.wav` |

The browser player uses an in-memory blob URL. Use the **Download WAV**
button to save the file locally.

---

## 7. Validate Standard TTS with the test script

The validation script tests all four languages end-to-end without needing
a browser. It is the fastest way to confirm Standard TTS is fully working.

### Prerequisites
```bash
# Activate the backend venv first
cd backend && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install requests (only extra dep the script needs)
pip install requests
```

### Run the script
```bash
# From the repo root, with the backend already running on localhost:8000:
python scripts/test-standard-tts.py
```

### Optional arguments
```bash
# Custom backend URL
python scripts/test-standard-tts.py --url http://localhost:8000

# Use your own audio sample instead of the generated silence
python scripts/test-standard-tts.py --sample /path/to/your/sample.wav

# Save output WAVs to a different directory
python scripts/test-standard-tts.py --out /tmp/my_outputs

# Change per-job timeout (seconds, default 120)
python scripts/test-standard-tts.py --timeout 60
```

### What the script does

1. **Health check** — calls `GET /health`, asserts `status == "ok"`;
   aborts immediately if the backend is unreachable.
2. **Four TTS jobs** — submits `mode=standard` jobs for `en`, `te`, `ta`, `hi`
   with short sentences in each language.
3. **Polls** each job every 2 s up to 120 s.
4. **Downloads** each output WAV to `validation_outputs/`.
5. **Prints** a per-language PASS/FAIL + overall summary.

The script generates a 1-second silence WAV automatically — you do not need
to provide a real audio file. Standard TTS ignores the reference audio.

### Expected output

Console (all passing):
```
[INFO] Using auto-generated 1-second silence WAV as sample.
[INFO] Backend  : http://localhost:8000
[INFO] Output   : /path/to/repo/validation_outputs
[INFO] Timeout  : 120.0s per job
════════════════════════════════════════════════════════════
STEP 1 — Health check
════════════════════════════════════════════════════════════
[HEALTH] GET http://localhost:8000/health
  status            : ok
  tts_engine        : edge-tts
  cloning_available : False
  supported_languages: ['en', 'hi', 'ta', 'te']
[HEALTH] ✓ PASS
════════════════════════════════════════════════════════════
STEP 2 — Standard TTS jobs (en / te / ta / hi)
════════════════════════════════════════════════════════════
────────────────────────────────────────────────────────────
[EN] English
  text : Hello, this is a Standard TTS validation test for English.
  mode : standard
  [1/3] submitting job ...  job_id=<hex>
  [2/3] polling (up to 120s) ..
  job status : completed  (job duration ~4.2s)
  output_url : /files/outputs/<job_id>_output.wav
  [3/3] downloading WAV ...
    saved: validation_outputs/en_output.wav  (142 KB)
[EN] ✓ PASS
... (te, ta, hi follow same pattern)
════════════════════════════════════════════════════════════
SUMMARY
════════════════════════════════════════════════════════════
  en  (English )  ✓ PASS
  te  (Telugu  )  ✓ PASS
  ta  (Tamil   )  ✓ PASS
  hi  (Hindi   )  ✓ PASS
────────────────────────────────────────────────────────────
  Total: 4   Passed: 4   Failed: 0

  ✓ ALL TESTS PASSED
  Output WAV files are in: validation_outputs/

    en_output.wav  (142 KB)
    te_output.wav  (138 KB)
    ta_output.wav  (145 KB)
    hi_output.wav  (151 KB)
```

> **Important**: The expected output above is illustrative.
> Actual file sizes and durations depend on your machine and network speed.
> The script only reports what it actually observed — nothing is pre-claimed.

### Expected output files

After a successful run:
```
validation_outputs/
├── en_output.wav    # English  — en-US-JennyNeural
├── te_output.wav    # Telugu   — te-IN-ShrutiNeural
├── ta_output.wav    # Tamil    — ta-IN-PallaviNeural
└── hi_output.wav    # Hindi    — hi-IN-SwaraNeural
```

Each file is a 16-bit PCM WAV at 22050 Hz, mono. Typical size: 100–200 KB.

---

## 8. Common errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Cannot connect to backend` | Backend not started | Run `bash scripts/start-backend.sh` first |
| `[HEALTH] ✗ FAIL — cannot connect` | Backend not running on port 8000 | Start backend, re-run script |
| Job `failed` with `TTS service unavailable` | No internet / firewall blocking port 443 | Check internet; allow outbound HTTPS |
| Job `failed` with `TTS returned no audio` | EdgeTTS got empty stream from Microsoft | Text may be empty or only punctuation |
| Generate times out after 2 min | EdgeTTS can't reach `speech.platform.bing.com` | Check firewall / network |
| `frontend/.env` missing | First-run config step skipped | Create `.env` with `VITE_API_BASE=http://localhost:8000` |
| HTTP 422 Unsupported language | Invalid language code | Use `te`, `ta`, `hi`, or `en` |
| HTTP 422 Unsupported mode | Invalid mode value | Use `standard` or `clone` |
| HTTP 413 File too large | Sample exceeds 50 MB | Use a smaller file |
| Clone sounds like default voice | OpenVoice disabled (expected) | See docs/VOICE_CLONING_READINESS.md |
| Port 8000 already in use | Another process on 8000 | `lsof -i :8000` then kill it |
| `ModuleNotFoundError: edge_tts` | Deps not installed | `pip install -r requirements.txt` |
| `ModuleNotFoundError: requests` | Script dep missing | `pip install requests` |

---

## 9. Voice Clone mode

By default, **clone mode falls back to Standard TTS** (EdgeTTS). The job
still completes; `cloning_applied` in the response will be `false`.

Real cloning requires:
- `OPENVOICE_ENABLED=true`
- OpenVoice v2 checkpoints downloaded
- PyTorch + OpenVoice installed

See **docs/VOICE_CLONING_READINESS.md** for the complete setup guide.

---

## 10. Exact run commands (quick reference)

```bash
# ── Terminal 1: Backend ──────────────────────────────────────────────
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # 6 packages, ~30 seconds
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# → Running on http://localhost:8000

# ── Terminal 2: Frontend ─────────────────────────────────────────────
echo "VITE_API_BASE=http://localhost:8000" > frontend/.env
echo "VITE_DEV_BACKEND=http://localhost:8000" >> frontend/.env
cd frontend && npm install && npm run dev
# → Running on http://localhost:5173

# ── Terminal 3: Validate Standard TTS ───────────────────────────────
# (backend must be running; activate the same venv)
cd backend && source .venv/bin/activate
pip install requests
cd ..    # back to repo root
python scripts/test-standard-tts.py
# → validation_outputs/ contains en/te/ta/hi WAV files
```
