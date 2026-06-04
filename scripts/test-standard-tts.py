#!/usr/bin/env python3
"""
scripts/test-standard-tts.py
────────────────────────────
Standard TTS end-to-end validation script.

What it does
────────────
1. Calls GET /health  — asserts status == "ok"
2. Submits Standard TTS jobs for: English (en), Telugu (te), Tamil (ta), Hindi (hi)
3. Polls GET /job/{id} every 2 s (up to 120 s) per job
4. Downloads the output WAV from the backend
5. Saves each WAV to validation_outputs/<lang>_output.wav
6. Prints a PASS / FAIL line per language and an overall summary

Requirements
────────────
  Python 3.8+
  pip install requests        (only non-stdlib dependency)

Usage
─────
  # From the repo root, with the backend running on localhost:8000:
  python scripts/test-standard-tts.py

  # Custom backend URL:
  python scripts/test-standard-tts.py --url http://localhost:8000

  # Use your own audio sample instead of the generated silence:
  python scripts/test-standard-tts.py --sample /path/to/sample.wav

  # Save outputs to a different directory:
  #   Windows : python scripts/test-standard-tts.py --out %TEMP%\\tts_outputs
  #   macOS/Linux: python scripts/test-standard-tts.py --out /tmp/tts_outputs

Do NOT run with OPENVOICE_ENABLED=true — this script tests Standard TTS only.
It always submits mode=standard; OpenVoice is never touched.

NOTE: This script does NOT claim any test passed.
Results are only valid if you run it yourself against a live backend.

Text limit
──────────
The backend accepts 1–2000 characters per request (raised from 500 in Phase 5).
The test sentences used here are 50–80 characters each — well within both limits.

Windows notes
─────────────
This script explicitly reconfigures stdout/stderr to UTF-8 on startup so that
box-drawing characters (═ ─) and check/cross marks (✓ ✗) render correctly in
Windows Terminal, PowerShell 7+, and modern cmd.exe.  On older consoles that
cannot accept UTF-8 the output automatically falls back to plain ASCII (= - PASS FAIL).
Run the script from Windows Terminal or PowerShell for the best experience.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Optional


# ── Windows-safe Unicode output ───────────────────────────────────────────────

def _configure_output() -> bool:
    """
    On Windows, try to reconfigure stdout/stderr to UTF-8 so that box-drawing
    characters and check/cross marks do not raise UnicodeEncodeError.

    Returns True if UTF-8 output is available, False if we fell back to ASCII.
    This lets callers pick the right separator/mark constants.
    """
    if sys.platform != "win32":
        return True  # POSIX — assume UTF-8 terminal

    # Python 3.7+ on Windows: reconfigure both streams
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        # Quick probe — write a box-drawing char to check it doesn't raise
        sys.stdout.write("\u2550" * 0)  # zero-width write, just validates encoding
        sys.stdout.flush()
        return True
    except Exception:
        return False


# Initialise Unicode support as early as possible
_UTF8_OK: bool = _configure_output()

# Output constants — fall back to plain ASCII when UTF-8 is unavailable
if _UTF8_OK:
    _SEP_HEAVY = "\u2550"   # ═
    _SEP_LIGHT = "\u2500"   # ─
    _PASS      = "\u2713 PASS"   # ✓ PASS
    _FAIL      = "\u2717 FAIL"   # ✗ FAIL
else:
    _SEP_HEAVY = "="
    _SEP_LIGHT = "-"
    _PASS      = "PASS"
    _FAIL      = "FAIL"


# ── Dependency check ──────────────────────────────────────────────────────────
try:
    import requests
except ImportError:
    print(
        "[ERROR] 'requests' is not installed.\n"
        "Fix: pip install requests\n"
        "Then re-run: python scripts/test-standard-tts.py"
    )
    sys.stdout.flush()
    sys.exit(1)


# ── Test sentences per language ───────────────────────────────────────────────
# All sentences are 50–80 characters — well within the 2000-character limit.
TEST_CASES: list[dict] = [
    {
        "language": "en",
        "name":     "English",
        "text":     "Hello, this is a Standard TTS validation test for English.",
    },
    {
        "language": "te",
        "name":     "Telugu",
        "text":     "\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02, \u0c07\u0c26\u0c3f \u0c24\u0c46\u0c32\u0c41\u0c17\u0c41 \u0c2d\u0c3e\u0c37\u0c32\u0c4b \u0c2a\u0c4d\u0c30\u0c3e\u0c2e\u0c3e\u0c23\u0c3f\u0c15 \u0c35\u0c3e\u0c2f\u0c3f\u0c38\u0c4d \u0c2a\u0c30\u0c40\u0c15\u0c4d\u0c37.",
    },
    {
        "language": "ta",
        "name":     "Tamil",
        "text":     "\u0bb5\u0ba3\u0b95\u0bcd\u0b95\u0bae\u0bcd, \u0b87\u0ba4\u0bc1 \u0ba4\u0bae\u0bbf\u0bb4\u0bcd \u0bae\u0bca\u0bb4\u0bbf\u0baf\u0bbf\u0bb2\u0bcd \u0ba8\u0bbf\u0bb2\u0bc8\u0baf\u0bbe\u0ba9 \u0b95\u0bc1\u0bb0\u0bb2\u0bcd \u0b9a\u0bcb\u0ba4\u0ba9\u0bc8.",
    },
    {
        "language": "hi",
        "name":     "Hindi",
        "text":     "\u0928\u092e\u0938\u094d\u0924\u0947, \u092f\u0939 \u0939\u093f\u0902\u0926\u0940 \u092d\u093e\u0937\u093e \u092e\u0947\u0902 \u092e\u093e\u0928\u0915 \u0935\u0949\u092f\u0938 \u092a\u0930\u0940\u0915\u094d\u0937\u0923 \u0939\u0948\u0964",
    },
]


# ── WAV generator (stdlib only — no audio file required) ──────────────────────

def _make_silence_wav(
    duration_s: float = 1.0,
    sample_rate: int = 22050,
    channels: int = 1,
) -> bytes:
    """
    Generate a minimal valid RIFF/WAV file containing silence (all zeros).
    This satisfies the backend's audio upload requirement without needing
    a real voice sample. Standard TTS ignores the reference audio entirely.
    """
    sample_width = 2  # 16-bit PCM
    n_samples = int(duration_s * sample_rate * channels)
    pcm_bytes = b"\x00" * (n_samples * sample_width)
    data_len = len(pcm_bytes)
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width

    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", data_len + 36))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))
    buf.write(struct.pack("<H", channels))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", byte_rate))
    buf.write(struct.pack("<H", block_align))
    buf.write(struct.pack("<H", 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm_bytes)
    return buf.getvalue()


# ── Core helpers ──────────────────────────────────────────────────────────────

def _print_separator(char: str = _SEP_LIGHT, width: int = 60) -> None:
    print(char * width)


def check_health(base_url: str, timeout: int = 10) -> bool:
    """Call GET /health. Print result. Return True if status == 'ok'."""
    url = f"{base_url.rstrip('/')}/health"
    print(f"\n[HEALTH] GET {url}")
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        status  = data.get("status", "?")
        engine  = data.get("tts_engine", "?")
        cloning = data.get("cloning_available", False)
        langs   = list(data.get("supported_languages", {}).keys())
        max_len = data.get("max_text_length", "?")
        print(f"  status             : {status}")
        print(f"  tts_engine         : {engine}")
        print(f"  cloning_available  : {cloning}")
        print(f"  supported_languages: {langs}")
        print(f"  max_text_length    : {max_len}")
        if status == "ok":
            print(f"[HEALTH] {_PASS}")
            return True
        else:
            print(f"[HEALTH] {_FAIL} -- unexpected status: {status!r}")
            return False
    except requests.exceptions.ConnectionError:
        print(
            f"[HEALTH] {_FAIL} -- cannot connect to backend.\n"
            f"  Is the backend running at {base_url}?\n"
            "\n"
            "  Start the backend:\n"
            "    Windows (Command Prompt or PowerShell):\n"
            "      scripts\\start-backend.bat\n"
            "    macOS / Linux:\n"
            "      bash scripts/start-backend.sh\n"
            "\n"
            "  Tip (Windows): open Command Prompt or PowerShell, cd to the repo\n"
            "  root, then run the .bat file above."
        )
        return False
    except Exception as exc:
        print(f"[HEALTH] {_FAIL} -- {exc}")
        return False


def submit_job(
    base_url: str,
    language: str,
    text: str,
    audio_bytes: bytes,
    audio_filename: str = "sample.wav",
    timeout: int = 15,
) -> Optional[str]:
    """POST /generate. Return job_id on success, None on failure."""
    url = f"{base_url.rstrip('/')}/generate"
    try:
        r = requests.post(
            url,
            data={
                "text":     text,
                "language": language,
                "mode":     "standard",   # always standard — never touches OpenVoice
            },
            files={
                "audio": (audio_filename, io.BytesIO(audio_bytes), "audio/wav"),
            },
            timeout=timeout,
        )
        if r.status_code == 202:
            job_id = r.json().get("job_id")
            return job_id
        else:
            print(f"    submit failed HTTP {r.status_code}: {r.text[:200]}")
            return None
    except Exception as exc:
        print(f"    submit error: {exc}")
        return None


def poll_job(
    base_url: str,
    job_id: str,
    poll_interval: float = 2.0,
    timeout: float = 120.0,
) -> Optional[dict]:
    """
    Poll GET /job/{job_id} every poll_interval seconds until
    status is 'completed' or 'failed', or timeout elapses.
    Returns the final job dict, or None on timeout/error.
    """
    url = f"{base_url.rstrip('/')}/job/{job_id}"
    deadline = time.monotonic() + timeout
    dots = 0
    while time.monotonic() < deadline:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 404:
                print(f"\n    job not found (404)")
                return None
            r.raise_for_status()
            data   = r.json()
            status = data.get("status", "?")
            if status in ("completed", "failed"):
                print()  # newline after dots
                return data
            print(".", end="", flush=True)
            dots += 1
            time.sleep(poll_interval)
        except requests.exceptions.Timeout:
            print("\n    poll request timed out")
            return None
        except Exception as exc:
            print(f"\n    poll error: {exc}")
            return None
    print(f"\n    timed out after {timeout:.0f}s")
    return None


def download_wav(
    base_url: str,
    output_url: str,
    dest_path: Path,
    timeout: int = 30,
) -> bool:
    """
    Download the WAV from output_url (which may be a relative path like
    /files/outputs/...) and save to dest_path.
    Returns True on success.
    """
    if output_url.startswith("/"):
        full_url = f"{base_url.rstrip('/')}{output_url}"
    else:
        full_url = output_url

    try:
        r = requests.get(full_url, timeout=timeout, stream=True)
        r.raise_for_status()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        size_kb = dest_path.stat().st_size // 1024
        print(f"    saved: {dest_path}  ({size_kb} KB)")
        return True
    except Exception as exc:
        print(f"    download error: {exc}")
        return False


# ── Main validation loop ───────────────────────────────────────────────────────

def run_validation(
    base_url: str,
    out_dir: Path,
    sample_bytes: bytes,
    poll_timeout: float = 120.0,
) -> dict[str, bool]:
    """
    Run the full validation suite. Returns {language: passed} dict.
    """
    results: dict[str, bool] = {}

    for case in TEST_CASES:
        lang = case["language"]
        name = case["name"]
        text = case["text"]

        _print_separator()
        print(f"[{lang.upper()}] {name}")
        print(f"  text : {text[:60]}{'...' if len(text) > 60 else ''}")
        print(f"  mode : standard")

        # 1. Submit
        print("  [1/3] submitting job ...", end=" ")
        job_id = submit_job(base_url, lang, text, sample_bytes)
        if not job_id:
            print(f"\n[{lang.upper()}] {_FAIL} -- job submission failed")
            results[lang] = False
            continue
        print(f"job_id={job_id}")

        # 2. Poll
        print(f"  [2/3] polling (up to {poll_timeout:.0f}s) ", end="", flush=True)
        job = poll_job(base_url, job_id, timeout=poll_timeout)
        if job is None:
            print(f"[{lang.upper()}] {_FAIL} -- polling timed out or errored")
            results[lang] = False
            continue

        status = job.get("status")
        if status == "failed":
            err = job.get("error_message", "unknown error")
            print(f"  job status : failed")
            print(f"  error      : {err}")
            print(f"[{lang.upper()}] {_FAIL} -- job failed: {err}")
            results[lang] = False
            continue

        if status != "completed":
            print(f"[{lang.upper()}] {_FAIL} -- unexpected status: {status!r}")
            results[lang] = False
            continue

        output_url    = job.get("output_url")
        provider_name = job.get("provider_name", "?")
        fallback_used = job.get("fallback_used", False)

        if not output_url:
            print(f"[{lang.upper()}] {_FAIL} -- completed but output_url is empty")
            results[lang] = False
            continue

        duration = job.get("completed_at", 0) - job.get("created_at", 0)
        print(f"  job status    : completed  (job duration ~{duration:.1f}s)")
        print(f"  provider_name : {provider_name}")
        print(f"  fallback_used : {fallback_used}")
        print(f"  output_url    : {output_url}")

        # 3. Download
        dest = out_dir / f"{lang}_output.wav"
        print(f"  [3/3] downloading WAV ...")
        ok = download_wav(base_url, output_url, dest)
        if not ok:
            print(f"[{lang.upper()}] {_FAIL} -- WAV download failed")
            results[lang] = False
            continue

        # Sanity: WAV must be > 1 KB
        size = dest.stat().st_size
        if size < 1024:
            print(f"[{lang.upper()}] {_FAIL} -- WAV too small ({size} bytes), likely empty")
            results[lang] = False
            continue

        print(f"[{lang.upper()}] {_PASS}")
        results[lang] = True

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standard TTS end-to-end validation for IndicVoice AI."
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Backend base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--sample",
        default=None,
        help="Path to a real audio sample (WAV/MP3). "
             "If omitted, a 1-second silence WAV is generated automatically.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory for WAV files (default: validation_outputs/ "
             "next to this script's repo root).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-job poll timeout in seconds (default: 120)",
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    # Resolve output directory
    if args.out:
        out_dir = Path(args.out)
    else:
        repo_root = Path(__file__).resolve().parent.parent
        out_dir   = repo_root / "validation_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load or generate audio sample
    if args.sample:
        sample_path = Path(args.sample)
        if not sample_path.exists():
            print(f"[ERROR] Sample file not found: {sample_path}")
            sys.stdout.flush()
            sys.exit(1)
        sample_bytes    = sample_path.read_bytes()
        sample_filename = sample_path.name
        print(f"[INFO] Using audio sample: {sample_path} ({len(sample_bytes)//1024} KB)")
    else:
        sample_bytes    = _make_silence_wav(duration_s=1.0)
        sample_filename = "silence_sample.wav"
        print("[INFO] Using auto-generated 1-second silence WAV as sample.")
        print("       Standard TTS ignores the reference audio -- this is correct.")

    print(f"[INFO] Backend  : {base_url}")
    print(f"[INFO] Output   : {out_dir}")
    print(f"[INFO] Timeout  : {args.timeout}s per job")

    # ── Step 1: Health check ──────────────────────────────────────────────────
    _print_separator(_SEP_HEAVY)
    print("STEP 1 -- Health check")
    _print_separator(_SEP_HEAVY)
    if not check_health(base_url):
        print(
            "\n[ABORT] Backend is not reachable or not healthy.\n"
            "Start the backend first:\n"
            "\n"
            "  Windows (Command Prompt or PowerShell):\n"
            "    scripts\\start-backend.bat\n"
            "\n"
            "  macOS / Linux:\n"
            "    bash scripts/start-backend.sh\n"
            "\n"
            "  Tip (Windows): right-click the Start menu -> 'Windows Terminal'\n"
            "  or search for 'cmd' / 'PowerShell', then cd to the repo root.\n"
            "\n"
            "Then re-run this script."
        )
        sys.stdout.flush()
        sys.exit(1)

    # ── Step 2: Run TTS validation for all 4 languages ────────────────────────
    _print_separator(_SEP_HEAVY)
    print("STEP 2 -- Standard TTS jobs (en / te / ta / hi)")
    _print_separator(_SEP_HEAVY)
    results = run_validation(
        base_url=base_url,
        out_dir=out_dir,
        sample_bytes=sample_bytes,
        poll_timeout=args.timeout,
    )

    # ── Step 3: Summary ───────────────────────────────────────────────────────
    _print_separator(_SEP_HEAVY)
    print("SUMMARY")
    _print_separator(_SEP_HEAVY)
    passed = [lang for lang, ok in results.items() if ok]
    failed = [lang for lang, ok in results.items() if not ok]

    for lang, ok in results.items():
        name = next(c["name"] for c in TEST_CASES if c["language"] == lang)
        mark = _PASS if ok else _FAIL
        print(f"  {lang}  ({name:8s})  {mark}")

    _print_separator()
    total  = len(results)
    n_pass = len(passed)
    n_fail = len(failed)
    print(f"  Total: {total}   Passed: {n_pass}   Failed: {n_fail}")

    if n_fail == 0:
        print(f"\n  {_PASS.replace('PASS', 'ALL TESTS PASSED')}")
        print(f"  Output WAV files are in: {out_dir}\\")
        print()
        for case in TEST_CASES:
            lang = case["language"]
            wav  = out_dir / f"{lang}_output.wav"
            size = f"{wav.stat().st_size // 1024} KB" if wav.exists() else "missing"
            print(f"    {wav.name}  ({size})")
    else:
        print(f"\n  {_FAIL.replace('FAIL', str(n_fail) + ' LANGUAGE(S) FAILED')}: {', '.join(failed)}")
        print("  Check backend logs for details.")
        print("  Common causes:")
        print("    - No internet (EdgeTTS requires speech.platform.bing.com)")
        print("    - Backend not started or crashed")
        print("    - Firewall blocking outbound port 443")
        print("    - Windows Defender / corporate firewall blocking edge-tts")

    print()
    sys.stdout.flush()
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
