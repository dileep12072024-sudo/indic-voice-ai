# Python Version Compatibility

## TL;DR

| Use case | Minimum Python | Maximum Python | Notes |
|---|---|---|---|
| Standard TTS (Edge-TTS, no cloning) | 3.9 | **3.14** ✅ | Works on any modern Python |
| Voice Cloning (OpenVoice v2) | **3.10** | **3.11** ⚠️ | Hard ceiling — see below |

---

## Why Python 3.14 Fails for Voice Cloning

The error `No matching distribution found for torch==2.2.2+cpu` was the **first** failure,
but fixing the torch pin alone is **not enough**.

OpenVoice v2's own `setup.py` hard-pins several packages that have **no Python ≥3.12
binary wheels on PyPI**. These are upstream pins in `myshell-ai/OpenVoice` that we
cannot override without forking the project:

| Pinned package | Pin | Why it blocks 3.12+ |
|---|---|---|
| `numpy` | `==1.22.0` | No `cp312`/`cp313`/`cp314` wheel on PyPI; source build fails on Python 3.12+ |
| `faster-whisper` | `==0.9.0` | Requires `ctranslate2<3.17`; that ctranslate2 range has no 3.12+ wheels |
| `librosa` | `==0.9.1` | Depends on `numba`; numba had no 3.12+ wheel at that version |
| `whisper-timestamped` | `==1.14.2` | Pulls numba transitively |
| `wavmark` | `==0.0.3` | Depends on the older numpy range |

### Community confirmation

- GitHub issue [myshell-ai/OpenVoice#62](https://github.com/myshell-ai/OpenVoice/issues/62):
  > *"I have used Python 3.10.11 and I was able to install everything on Windows 11
  > and Windows 10. 3.11 and 3.12 did not work for me."*
- Reddit thread (2025):
  > *"I updated Python to 3.13 and still doesn't work while trying to install
  > the dependencies that OpenVoice v2 needs."*

### What about torch?

PyTorch ≥2.9.0 **does** support Python 3.14 (confirmed in the
[pytorch/pytorch 2.9.0 release notes](https://github.com/pytorch/pytorch/releases)).
The pinned `torch==2.2.2+cpu` was a secondary problem (no 3.14 wheel exists for that
version). The requirements file has been updated to `torch>=2.0.0,<3.0.0` — but that
only helps after the OpenVoice dep-chain problem is solved, which requires Python 3.10/3.11.

---

## Required Python Version for Cloning

**Python 3.10** is the recommended version.  
**Python 3.11** may work but has been reported as unstable on some Windows configurations.  
Python 3.12, 3.13, and 3.14 are **not compatible** with the current OpenVoice release.

---

## Windows 11 — Installing Python 3.10 Alongside Python 3.14

You do **not** need to uninstall Python 3.14. The Windows Python Launcher (`py`) lets
you run multiple Python versions side by side.

### Step 1 — Download Python 3.10

Download the latest **Python 3.10.x** Windows installer from the official site:

```
https://www.python.org/downloads/release/python-31016/
```

- Choose **"Windows installer (64-bit)"**
- During installation, check **"Add Python to PATH"** only if 3.10 should be your
  system default. Otherwise leave it unchecked — the `py` launcher will still find it.
- Verify after install:

```powershell
py -3.10 --version
# Python 3.10.16

py -3.14 --version
# Python 3.14.x   ← your existing install is untouched
```

### Step 2 — Create a Dedicated Virtual Environment

```powershell
# From the repo root
cd indic-voice-ai

# Create a venv using Python 3.10 specifically
py -3.10 -m venv .venv-clone

# Activate it
.venv-clone\Scripts\Activate.ps1

# Verify the active Python version
python --version
# Python 3.10.16   ← must show 3.10.x
```

> **Tip:** If PowerShell blocks script execution, run once:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### Step 3 — Install Standard Backend Dependencies

```powershell
# Still inside .venv-clone
cd backend
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4 — Install Voice Cloning Dependencies

The CPU torch wheels live on the PyTorch index. Pass `--extra-index-url` so pip
can find them:

```powershell
pip install -r requirements-clone.txt `
    --extra-index-url https://download.pytorch.org/whl/cpu
```

> **CUDA users:** Replace the index URL:
> - CUDA 11.8: `https://download.pytorch.org/whl/cu118`
> - CUDA 12.1: `https://download.pytorch.org/whl/cu121`
> - CUDA 12.4: `https://download.pytorch.org/whl/cu124`

### Step 5 — Download MeloTTS Language Data

```powershell
python -m unidic download
```

### Step 6 — Download OpenVoice v2 Checkpoints

Checkpoints (~300 MB) are auto-downloaded on first synthesis when
`OPENVOICE_AUTO_DOWNLOAD=true` (the default). To pre-download:

```powershell
python checkpoint_manager.py download
```

### Step 7 — Start the Backend

```powershell
$env:OPENVOICE_ENABLED       = "true"
$env:OPENVOICE_DEVICE        = "cpu"
$env:OPENVOICE_AUTO_DOWNLOAD = "true"
uvicorn main:app --reload --port 8000
```

---

## Verification Checklist

```powershell
# 1. Confirm Python version in the venv
python --version
# ✅ Must show Python 3.10.x or 3.11.x

# 2. Confirm torch is installed and importable
python -c "import torch; print(torch.__version__)"
# ✅ Prints e.g. 2.9.0+cpu

# 3. Confirm OpenVoice is importable
python -c "from openvoice import se_extractor; print('OpenVoice OK')"
# ✅ Prints: OpenVoice OK

# 4. Health check
curl http://localhost:8000/health
# ✅ "cloning_available": true

# 5. Create voice profile
curl -X POST http://localhost:8000/voices `
  -F "audio=@C:\path\to\sample.wav" `
  -F "name=TestVoice" `
  -F "language=en"
# ✅ Returns { "id": "<VOICE_ID>", "cloning_ready": false }
# ✅ Background task starts embedding extraction

# 6. Poll until embedding ready (repeat until cloning_ready=true)
curl http://localhost:8000/voices/<VOICE_ID>
# ✅ { "cloning_ready": true, "embedding_key": "embeddings/voice_....npy" }

# 7. Generate cloned speech
curl -X POST http://localhost:8000/generate `
  -F "voice_id=<VOICE_ID>" `
  -F "text=Hello, this is my cloned voice speaking." `
  -F "language=en" `
  -F "mode=clone"
# ✅ Returns { "job_id": "<JOB_ID>" }

# 8. Confirm cloning result
curl http://localhost:8000/job/<JOB_ID>
# ✅ "cloning_applied": true
# ✅ "fallback_used": false
# ✅ "status": "completed"
```

---

## Standard TTS on Python 3.14 (No Cloning)

If you only need Edge-TTS speech generation (no voice cloning), your existing
Python 3.14 installation works fine:

```powershell
# Using your existing Python 3.14 install
py -3.14 -m venv .venv-standard
.venv-standard\Scripts\Activate.ps1

cd backend
pip install -r requirements.txt   # no requirements-clone.txt needed

# Start backend WITHOUT cloning
# (OPENVOICE_ENABLED defaults to false — no extra env var needed)
uvicorn main:app --reload --port 8000
```

The `/health` endpoint will return `"cloning_available": false` and all speech
generation will use Edge-TTS. The Generate Speech page, Voice Library, and all
other features work normally.

---

## Summary Table — Exact Error → Fix

| Error message | Python | Fix |
|---|---|---|
| `No matching distribution found for torch==2.2.2+cpu` | 3.14 | Torch was hard-pinned to 2.2.2 which has no 3.14 wheel. **Fixed** — torch is now unpinned (`>=2.0.0`). **But** OpenVoice itself still requires Python 3.10/3.11. |
| `ERROR: Could not find a version that satisfies the requirement numpy==1.22.0` | 3.12–3.14 | numpy 1.22.0 has no cp312+ wheel. **Fix:** use Python 3.10/3.11. |
| `ERROR: Failed building wheel for numba` | 3.12–3.14 | numba has no 3.12+ wheel at librosa 0.9.1's required range. **Fix:** use Python 3.10/3.11. |
| `ERROR: ctranslate2 ... has no matching distribution` | 3.12–3.14 | faster-whisper 0.9.0 pins ctranslate2<3.17 which has no 3.12+ wheel. **Fix:** use Python 3.10/3.11. |
| `No matching distribution found for torch` (after Python fix) | 3.10 | torch wheels for 3.10 are on the PyTorch index. **Fix:** add `--extra-index-url https://download.pytorch.org/whl/cpu` to pip install. |
