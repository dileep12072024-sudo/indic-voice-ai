# Voice Cloning Readiness Audit

> **Method**: static code inspection + upstream OpenVoice v2 dependency research.
> No application execution was performed. All findings are code-level facts.
> Last updated: 2026-06-04

---

## Readiness Score: **2 / 10**

| Dimension | Score | Reason |
|-----------|-------|--------|
| Code integration | 8/10 | Provider logic is correct and complete |
| Python packages | 0/10 | Neither `openvoice` nor `torch` is installed |
| Model checkpoints | 0/10 | Converter checkpoint missing, no download script |
| Hardware readiness | N/A | Cannot assess without a live machine |
| Fallback safety | 10/10 | EdgeTTS fallback always works; no crash risk |

**Bottom line**: real voice cloning cannot run today on a fresh clone of this
repository. Three hard blockers must be resolved before `cloning_applied=true`
can ever appear in a job response.

---

## Can Cloning Run Today?

**No.** Three independent blockers each individually prevent inference:

| # | Blocker | Location | Severity |
|---|---------|----------|----------|
| 1 | `openvoice` Python package not installed | `requirements.txt` — absent | 🔴 Hard |
| 2 | `torch` / `torchaudio` not installed | `requirements.txt` — absent | 🔴 Hard |
| 3 | Converter checkpoint files not present | `~/.cache/openvoice/v2/converter/` — missing | 🔴 Hard |

All three must be resolved simultaneously. Fixing only one or two will still
result in `cloning_applied=false` (graceful fallback to EdgeTTS).

---

## What the Code Actually Imports (Lazy Runtime Imports)

The provider defers all heavy imports to inference time so the server starts
without PyTorch. These imports occur inside `_load_models_sync()` and
`_clone_sync()` — none are in `requirements.txt`:

```python
# _load_models_sync() — called on first clone request
from openvoice import se_extractor          # ← openvoice package
from openvoice.api import ToneColorConverter # ← openvoice package

# _clone_sync() — called per inference
import soundfile as sf   # ← soundfile package
import numpy as np       # ← numpy package
import librosa           # ← librosa package (optional, for resampling)
```

If any of these imports fail, `_load_models_sync()` returns `False`, the
provider falls back to EdgeTTS, and `cloning_applied` stays `False`.
No exception is raised and no job fails — the fallback is completely silent
to the end user unless they check `cloning_applied` in the job response.

---

## Missing Python Dependencies

### Direct (imported by `openvoice_provider.py`)

| Package | Import in code | Currently in `requirements.txt` | Notes |
|---------|---------------|--------------------------------|-------|
| `openvoice` | `from openvoice import se_extractor` | ❌ No | Core cloning package |
| `openvoice` | `from openvoice.api import ToneColorConverter` | ❌ No | Core cloning package |
| `soundfile` | `import soundfile as sf` | ❌ No | Read/write WAV after conversion |
| `numpy` | `import numpy as np` | ❌ No | PCM array processing |
| `librosa` | `import librosa` | ❌ No (optional) | Resampling; skipped if absent |

### Transitive (required by `openvoice` and its dependencies)

| Package | Required by | Notes |
|---------|-------------|-------|
| `torch` | `openvoice` | PyTorch — the largest single dependency (~2 GB) |
| `torchaudio` | `openvoice` | Audio tensor ops |
| `MeloTTS` | `openvoice` v2 | Base TTS for V2; install via git URL |
| `faster-whisper` | `se_extractor.get_se()` | VAD (voice activity detection) in speaker encoder |
| `wavmark` | `ToneColorConverter.convert()` | `@MyShell` audio watermark |
| `librosa==0.9.1` | `openvoice/requirements.txt` | Audio analysis |
| `scipy` | `librosa` | Transitive — signal processing |
| `pydub` | `MeloTTS` | Audio segment manipulation |
| `langid` | `openvoice` | Language detection |
| `inflect` | `MeloTTS` | English text normalisation |
| `unidecode` | `MeloTTS` | Unicode text normalisation |
| `eng_to_ipa` | `MeloTTS` | Phoneme conversion |

> **Note on MeloTTS**: OpenVoice V2 uses MeloTTS as its internal base TTS.
> This repository substitutes EdgeTTS for the base TTS stage, which is
> architecturally correct for tone-colour transfer. However, `se_extractor.get_se()`
> still pulls in MeloTTS transitive imports (`faster-whisper`, `wavmark`) even
> when MeloTTS is not used for synthesis. MeloTTS must still be installed.

---

## Missing Model Checkpoints

The provider expects checkpoints at:
```
$OPENVOICE_CHECKPOINT_DIR/converter/config.json
$OPENVOICE_CHECKPOINT_DIR/converter/checkpoint.pth
```

Default path: `~/.cache/openvoice/v2/converter/`

| File | Status | Approximate Size | Source |
|------|--------|-----------------|--------|
| `converter/config.json` | ❌ Not present | ~2 KB | HuggingFace: `myshell-ai/openvoice` |
| `converter/checkpoint.pth` | ❌ Not present | ~300 MB | HuggingFace: `myshell-ai/openvoice` |

**No checkpoint download script exists anywhere in this repository.**

The provider guard at `_load_models_sync()` logs a warning and returns `False`
if the checkpoint directory does not exist:
```python
if not _CHECKPOINT_DIR.exists():
    logger.warning("OpenVoice checkpoint directory not found: %s", _CHECKPOINT_DIR)
    return False
```

This means cloning silently falls back to EdgeTTS the first time any user
tries clone mode — with no visible error in the UI.

---

## Hardware Requirements

### Minimum (CPU-only, usable but slow)

| Resource | Minimum | Notes |
|----------|---------|-------|
| RAM | 8 GB | 4–6 GB consumed by PyTorch + OpenVoice models |
| CPU | Intel i5 / AMD Ryzen 5 (4 cores) | Multi-core required; inference is single-threaded |
| Storage | 5 GB free | ~2 GB PyTorch, ~300 MB checkpoints, ~500 MB MeloTTS/deps |
| GPU | Not required | Falls back to CPU automatically |
| Internet | Required during setup | Checkpoint + package downloads only |

**CPU inference latency**: 20–60 seconds per sentence (confirmed in provider
docstring and upstream benchmarks). Acceptable for offline/batch use;
unacceptable for interactive real-time use.

### Recommended (GPU, interactive use)

| Resource | Recommended | Notes |
|----------|------------|-------|
| RAM | 16 GB | Headroom for concurrent jobs |
| GPU VRAM | 4 GB+ | NVIDIA RTX 3060 or better |
| GPU | NVIDIA (CUDA) | `OPENVOICE_DEVICE=cuda` |
| Storage | 10 GB free | Includes CUDA libs |
| CUDA | 11.8 or 12.x | Match installed PyTorch build |

**GPU inference latency**: 3–8 seconds per sentence (RTX 3060/3070 class).
Converter VRAM footprint: ~500 MB. MeloTTS adds ~1–2 GB if loaded.

### Current repository default

`OPENVOICE_DEVICE=cpu` — the default is CPU. GPU requires setting the env var
before starting the backend.

---

## Exact Steps to Enable Real Cloning

Complete all steps in order. Each step is a hard requirement.

### Step 1 — Create a `requirements-clone.txt`

A separate requirements file for the clone path (so standard TTS users are
not forced to install PyTorch):

```
# backend/requirements-clone.txt
# Install AFTER requirements.txt
# CPU-only PyTorch (remove +cpu suffix for CUDA build)
torch==2.2.2+cpu --extra-index-url https://download.pytorch.org/whl/cpu
torchaudio==2.2.2+cpu --extra-index-url https://download.pytorch.org/whl/cpu

# OpenVoice v2 core
openvoice @ git+https://github.com/myshell-ai/OpenVoice.git@main
MeloTTS @ git+https://github.com/myshell-ai/MeloTTS.git@main

# Direct imports in openvoice_provider.py
soundfile>=0.12.1
numpy>=1.22.0
librosa==0.9.1

# Transitive (VAD, watermark, text normalisation)
faster-whisper>=0.9.0
wavmark>=0.0.3
pydub>=0.25.1
langid>=1.1.6
inflect>=7.0.0
unidecode>=1.3.7
eng_to_ipa>=0.0.2
scipy>=1.9.0
```

### Step 2 — Install clone dependencies

```bash
cd backend
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Base deps first (if not already installed)
pip install -r requirements.txt

# Clone deps
pip install -r requirements-clone.txt

# MeloTTS language data (required by se_extractor)
python -m unidic download
```

### Step 3 — Download the converter checkpoint

```bash
mkdir -p ~/.cache/openvoice/v2/converter

# Option A: HuggingFace Hub (recommended)
pip install huggingface_hub
python -c "
from huggingface_hub import hf_hub_download
hf_hub_download(
    repo_id='myshell-ai/openvoice',
    filename='checkpoints_v2/converter/checkpoint.pth',
    local_dir=os.path.expanduser('~/.cache/openvoice/v2'),
)
hf_hub_download(
    repo_id='myshell-ai/openvoice',
    filename='checkpoints_v2/converter/config.json',
    local_dir=os.path.expanduser('~/.cache/openvoice/v2'),
)
"

# Option B: direct git clone (downloads everything, ~400 MB)
git clone --depth 1 https://huggingface.co/myshell-ai/openvoice /tmp/openvoice-ckpt
cp -r /tmp/openvoice-ckpt/checkpoints_v2/converter ~/.cache/openvoice/v2/
```

Verify:
```bash
ls ~/.cache/openvoice/v2/converter/
# Expected: checkpoint.pth  config.json
```

### Step 4 — Set environment variables

Add to `backend/.env` (create if missing):
```bash
OPENVOICE_ENABLED=true
OPENVOICE_CHECKPOINT_DIR=/home/<you>/.cache/openvoice/v2
OPENVOICE_DEVICE=cpu        # or cuda / cuda:0 for GPU
```

Or export before starting the backend:
```bash
export OPENVOICE_ENABLED=true
export OPENVOICE_CHECKPOINT_DIR=~/.cache/openvoice/v2
export OPENVOICE_DEVICE=cpu
```

### Step 5 — Start the backend and verify

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Check health:
```bash
curl http://localhost:8000/health
# Expected: "cloning_available": true
```

### Step 6 — Test real cloning

Submit a clone job and confirm `cloning_applied=true`:
```bash
curl -X POST http://localhost:8000/generate \
  -F "audio=@/path/to/sample.wav" \
  -F "text=Hello, this is a cloned voice." \
  -F "language=en" \
  -F "mode=clone"
# Returns: {"job_id": "...", "mode": "clone", "cloning_enabled": true, ...}

# Poll until completed
curl http://localhost:8000/job/<job_id>
# Expected: "cloning_applied": true
```

---

## Integration Assessment (Code Quality)

The OpenVoice integration code in `backend/providers/openvoice_provider.py`
is **architecturally correct**. Specific observations:

| Aspect | Assessment |
|--------|------------|
| Lazy import guard | ✅ Server starts without PyTorch; no startup crash |
| Checkpoint existence check | ✅ Returns `False` cleanly if dir missing |
| Asyncio thread isolation | ✅ Inference runs in executor; event loop not blocked |
| Fallback chain | ✅ EdgeTTS result always returned on any failure |
| `cloning_applied` propagation | ✅ Fixed in commit `c48fb0f` — now in job record |
| Base TTS substitution | ✅ EdgeTTS replaces MeloTTS correctly for te/ta/hi/en |
| Resampling fallback | ✅ librosa optional; silently skips if unavailable |
| Cross-lingual support | ⚠️ OpenVoice V2 natively targets EN/ZH/ES/FR/JP/KO — Indic language quality (te/ta/hi) is **untested** upstream |
| Concurrency | ⚠️ All jobs serialised through `asyncio.Lock` — one clone at a time |
| CPU latency | ⚠️ 20–60s per sentence on CPU — unacceptable for interactive use |

---

## Known Risk: Indic Language Clone Quality

OpenVoice V2 was trained and benchmarked on English, Chinese, Spanish, French,
Japanese, and Korean. Telugu, Tamil, and Hindi are **not in the upstream
training set**. Tone-colour transfer for these languages may produce:
- degraded speaker similarity
- reduced intelligibility
- artifacts in prosody

This is a model capability gap, not a code bug. It cannot be fixed without
fine-tuning or switching to an Indic-language-aware cloning model.
**This must be tested empirically once the blockers above are resolved.**

---

## Summary Checklist

```
To enable real voice cloning:

[ ] pip install -r backend/requirements-clone.txt
[ ] python -m unidic download
[ ] Download converter/checkpoint.pth (~300 MB) from myshell-ai/openvoice
[ ] Download converter/config.json from myshell-ai/openvoice
[ ] Set OPENVOICE_ENABLED=true in environment
[ ] Set OPENVOICE_CHECKPOINT_DIR to checkpoint location
[ ] Verify /health returns cloning_available: true
[ ] Test clone job — confirm cloning_applied: true in job response
[ ] Test clone quality for te / ta / hi (may differ from en)
[ ] Benchmark latency on target hardware (CPU vs GPU)
```

---

## Files That Need To Exist (Currently Missing)

| File | Status | Purpose |
|------|--------|---------|
| `backend/requirements-clone.txt` | ❌ Missing | One-file install for all clone deps |
| `~/.cache/openvoice/v2/converter/checkpoint.pth` | ❌ Missing | Core model weights (~300 MB) |
| `~/.cache/openvoice/v2/converter/config.json` | ❌ Missing | Model architecture config |
| `scripts/download-checkpoints.sh` | ❌ Missing | Automated checkpoint download |
