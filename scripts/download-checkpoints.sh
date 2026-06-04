#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# download-checkpoints.sh
# Download OpenVoice v2 converter checkpoints for real voice cloning.
#
# Usage:
#   bash scripts/download-checkpoints.sh
#   bash scripts/download-checkpoints.sh --dir /custom/path
#
# Downloads to: ~/.cache/openvoice/v2/converter/ (default)
# Requires:     python3, pip, internet access
# Size:         ~300 MB
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CHECKPOINT_DIR="${OPENVOICE_CHECKPOINT_DIR:-${HOME}/.cache/openvoice/v2}"

# Allow --dir override
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) CHECKPOINT_DIR="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

CONVERTER_DIR="${CHECKPOINT_DIR}/converter"

echo "[download-checkpoints] Target directory: ${CONVERTER_DIR}"
mkdir -p "${CONVERTER_DIR}"

# Check if already downloaded
if [ -f "${CONVERTER_DIR}/checkpoint.pth" ] && [ -f "${CONVERTER_DIR}/config.json" ]; then
  echo "[download-checkpoints] Checkpoints already present — nothing to do."
  echo "  ${CONVERTER_DIR}/checkpoint.pth"
  echo "  ${CONVERTER_DIR}/config.json"
  exit 0
fi

# Download via huggingface_hub
echo "[download-checkpoints] Installing huggingface_hub..."
pip install --quiet huggingface_hub

echo "[download-checkpoints] Downloading checkpoint.pth (~300 MB)..."
python3 - <<PYEOF
import os
from huggingface_hub import hf_hub_download

converter_dir = "${CONVERTER_DIR}"
os.makedirs(converter_dir, exist_ok=True)

for filename in ["checkpoints_v2/converter/checkpoint.pth",
                 "checkpoints_v2/converter/config.json"]:
    local_name = os.path.basename(filename)
    dest = os.path.join(converter_dir, local_name)
    if os.path.exists(dest):
        print(f"  Already exists: {dest}")
        continue
    print(f"  Downloading {filename} ...")
    path = hf_hub_download(
        repo_id="myshell-ai/openvoice",
        filename=filename,
        local_dir="/tmp/openvoice_dl",
    )
    import shutil
    shutil.copy(path, dest)
    print(f"  Saved to: {dest}")

print("Done.")
PYEOF

echo ""
echo "[download-checkpoints] ✓ Checkpoints ready:"
ls -lh "${CONVERTER_DIR}/"
echo ""
echo "[download-checkpoints] Next steps:"
echo "  1. pip install -r backend/requirements-clone.txt"
echo "  2. python -m unidic download"
echo "  3. export OPENVOICE_ENABLED=true"
echo "  4. export OPENVOICE_CHECKPOINT_DIR=${CHECKPOINT_DIR}"
echo "  5. bash scripts/start-backend.sh"
echo "  6. curl http://localhost:8000/health  # expect cloning_available: true"
