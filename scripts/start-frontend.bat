@echo off
REM ─────────────────────────────────────────────────────────────
REM start-frontend.bat — Start the IndicVoice AI React/Vite frontend
REM Usage: scripts\start-frontend.bat
REM Runs at: http://localhost:5173
REM ─────────────────────────────────────────────────────────────
setlocal

cd /d "%~dp0..\frontend"
echo [start-frontend] Working in: %CD%

if not exist ".env" (
  echo [start-frontend] No .env found - creating one for local dev.
  (
    echo VITE_API_BASE=http://localhost:8000
    echo VITE_DEV_BACKEND=http://localhost:8000
  ) > .env
  echo [start-frontend] Created frontend\.env
)

if not exist "node_modules" (
  echo [start-frontend] Installing npm dependencies...
  call npm install
)

echo.
echo [start-frontend] Frontend starting at http://localhost:5173
echo.

call npm run dev
