# Run IndicVoice AI Locally

Complete local setup guide. No Cloudflare account or deployment needed.

---

## 1. Get the code

**Option A — Download ZIP (no git required)**
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

## 2. Prerequisites

| Requirement | Minimum version | Notes |
|-------------|----------------|-------|
| Python | 3.10+ | `python3 --version` |
| pip | 22+ | bundled with Python |
| Node.js | 18+ | `node --version` |
| npm | 9+ | bundled with Node |
| Internet | — | Standard TTS calls Microsoft EdgeTTS online |

---

## 3. Start the backend

### One-command (recommended)
```bash
# macOS / Linux
bash scripts/start-backend.sh

# Windows
scripts\start-backend.bat
```

The script creates a virtual environment, installs all dependencies, and starts
the FastAPI server on **http://localhost:8000**.

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

**Verify**: open <http://localhost:8000/health> — you should see:
```json
{"status": "ok", "cloning_available": false, ...}
```

---

## 4. Start the frontend

### One-command (recommended)
```bash
# macOS / Linux
bash scripts/start-frontend.sh

# Windows
scripts\start-frontend.bat
```

The script auto-creates `frontend/.env` pointing at `localhost:8000` if it does
not exist, installs npm dependencies, and starts Vite on **http://localhost:5173**.

### Manual steps
```bash
# 1. Create the .env file (REQUIRED — without this Generate fails)
echo "VITE_API_BASE=http://localhost:8000" > frontend/.env
echo "VITE_DEV_BACKEND=http://localhost:8000" >> frontend/.env

# 2. Install and run
cd frontend
npm install
npm run dev
```

> **⚠️ Critical**: `frontend/.env` must exist before starting the frontend.
> Without it the Generate button cannot reach the backend (it calls port 5173
> instead of 8000). This is the most common first-run failure.

Open **http://localhost:5173** in your browser.

---

## 5. Expected ports

| Service | URL | Notes |
|---------|-----|-------|
| Backend (FastAPI) | <http://localhost:8000> | API + static file serving |
| Backend docs | <http://localhost:8000/docs> | Swagger UI |
| Backend health | <http://localhost:8000/health> | Quick sanity check |
| Frontend (Vite) | <http://localhost:5173> | Main app |

---

## 6. Where audio output is saved

| Type | Path | Served at |
|------|------|-----------|
| Uploaded sample | `backend/uploads/<job_id>_sample.<ext>` | (not exposed) |
| Generated WAV | `backend/outputs/<job_id>_output.wav` | `http://localhost:8000/files/outputs/<job_id>_output.wav` |

The browser player uses an in-memory blob URL. Use the **Download WAV** button
in the player to save the file locally.

---

## 7. Using the app

1. Open <http://localhost:5173>
2. Choose **Standard TTS** or **Voice Clone** mode
3. Upload a voice sample (WAV / MP3 / OGG / WEBM / FLAC, max 50 MB)
4. Select a language (Telugu / Tamil / Hindi / English)
5. Enter text to synthesise (max 500 characters)
6. Click **Generate**
7. Wait for the progress indicator (Standard TTS ~3–10 s)
8. Play or download the output WAV

---

## 8. Voice Clone mode (advanced)

By default, **clone mode falls back to Standard TTS** (EdgeTTS). The output is
still generated — it just uses the Microsoft Neural voice instead of your
reference speaker's tone colour. The `cloning_applied` field in the job response
will be `false`.

To enable real OpenVoice v2 cloning:
```bash
# 1. Download OpenVoice v2 checkpoints (myshell-ai/OpenVoice on HuggingFace)
# 2. Install additional deps (PyTorch, soundfile, etc.)
pip install torch torchaudio openvoice soundfile

# 3. Set environment variables before starting the backend
export OPENVOICE_ENABLED=true
export OPENVOICE_CHECKPOINT_DIR=/path/to/openvoice/v2/checkpoints
export OPENVOICE_DEVICE=cuda   # or cpu (much slower)

bash scripts/start-backend.sh
```

See `docs/VOICE_CLONING.md` for full checkpoint setup instructions.

---

## 9. Common errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Cannot reach the backend" | `frontend/.env` missing or backend not running | Create `.env`, start backend |
| Generate times out after 2 min | EdgeTTS can't reach `speech.platform.bing.com` | Check internet / firewall |
| HTTP 422 Unsupported language | Invalid language code | Use `te`, `ta`, `hi`, or `en` |
| HTTP 422 Unsupported mode | Invalid mode value | Use `standard` or `clone` |
| HTTP 413 File too large | Sample exceeds 50 MB | Use a smaller file |
| Clone sounds like default voice | OpenVoice disabled (expected default) | See Voice Clone section above |
| Port 8000 already in use | Another process on 8000 | `lsof -i :8000` and kill it |
| `ModuleNotFoundError: edge_tts` | Dependencies not installed | Run `pip install -r requirements.txt` |

---

## 10. Exact run commands (quick reference)

```bash
# Terminal 1 — Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — Frontend
echo "VITE_API_BASE=http://localhost:8000" > frontend/.env
echo "VITE_DEV_BACKEND=http://localhost:8000" >> frontend/.env
cd frontend && npm install && npm run dev

# Then open: http://localhost:5173
```
