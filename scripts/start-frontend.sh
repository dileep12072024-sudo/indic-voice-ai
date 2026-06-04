#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# start-frontend.sh — Start the IndicVoice AI React/Vite frontend
# Usage: bash scripts/start-frontend.sh
# Runs at: http://localhost:5173
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/../frontend"

cd "$FRONTEND_DIR"
echo "[start-frontend] Working in: $(pwd)"

# Auto-create .env pointing at local backend if missing
if [ ! -f ".env" ]; then
  echo "[start-frontend] No .env found — creating one for local dev."
  printf 'VITE_API_BASE=http://localhost:8000\nVITE_DEV_BACKEND=http://localhost:8000\n' > .env
  echo "[start-frontend] Created frontend/.env"
fi

# Install npm dependencies if node_modules is missing
if [ ! -d "node_modules" ]; then
  echo "[start-frontend] Installing npm dependencies..."
  npm install
fi

echo ""
echo "[start-frontend] ✓ Frontend starting at http://localhost:5173"
echo ""

exec npm run dev
