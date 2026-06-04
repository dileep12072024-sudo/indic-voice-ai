#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# start-backend.sh — Start the IndicVoice AI FastAPI backend
# Usage: bash scripts/start-backend.sh
# Runs at: http://localhost:8000
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"

cd "$BACKEND_DIR"
echo "[start-backend] Working in: $(pwd)"

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
  echo "[start-backend] Creating Python virtual environment..."
  python3 -m venv .venv
fi

# Activate
# shellcheck disable=SC1091
source .venv/bin/activate
echo "[start-backend] Virtual environment active: $(which python)"

# Install / upgrade dependencies
echo "[start-backend] Installing dependencies from requirements.txt..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo ""
echo "[start-backend] ✓ Backend starting at http://localhost:8000"
echo "[start-backend]   API docs at  http://localhost:8000/docs"
echo "[start-backend]   Health at    http://localhost:8000/health"
echo ""

exec uvicorn main:app --host 0.0.0.0 --port 8000 --reload
