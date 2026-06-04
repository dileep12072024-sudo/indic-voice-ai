@echo off
REM ─────────────────────────────────────────────────────────────
REM start-backend.bat — Start the IndicVoice AI FastAPI backend
REM Usage: scripts\start-backend.bat
REM Runs at: http://localhost:8000
REM ─────────────────────────────────────────────────────────────
setlocal

cd /d "%~dp0..\backend"
echo [start-backend] Working in: %CD%

if not exist ".venv" (
  echo [start-backend] Creating Python virtual environment...
  python -m venv .venv
)

call .venv\Scripts\activate.bat
echo [start-backend] Virtual environment active.

echo [start-backend] Installing dependencies from requirements.txt...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo.
echo [start-backend] Backend starting at http://localhost:8000
echo [start-backend]   API docs at  http://localhost:8000/docs
echo [start-backend]   Health at    http://localhost:8000/health
echo.

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
