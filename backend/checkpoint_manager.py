"""
backend/checkpoint_manager.py  — Phase 7
─────────────────────────────────────────
OpenVoice v2 Checkpoint Auto-Manager.

Responsibilities
────────────────
1. Detect whether the checkpoint directory contains the required files.
2. If missing, attempt to download from the official HuggingFace Hub release.
3. Verify file integrity (size > 0).
4. Return (ok: bool, error: str | None) so callers can surface exact reasons.

Checkpoint layout expected at OPENVOICE_CHECKPOINT_DIR (default ~/.cache/openvoice/v2):
    converter/
        config.json
        checkpoint.pth

Download source: HuggingFace Hub  myshell-ai/OpenVoice  (MIT licence)
Fallback source: direct GitHub release asset URL.

Environment variables
─────────────────────
OPENVOICE_CHECKPOINT_DIR   Path to checkpoint directory (default ~/.cache/openvoice/v2)
OPENVOICE_CHECKPOINT_URL   Override HuggingFace download URL (optional)
OPENVOICE_AUTO_DOWNLOAD    "true" | "false" (default "true")
"""

from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("indicvoice.checkpoint_manager")

# ── Constants ──────────────────────────────────────────────────────────────
_CHECKPOINT_DIR = Path(
    os.getenv("OPENVOICE_CHECKPOINT_DIR", Path.home() / ".cache" / "openvoice" / "v2")
)
_AUTO_DOWNLOAD = os.getenv("OPENVOICE_AUTO_DOWNLOAD", "true").lower() == "true"

# Official HuggingFace Hub download URLs for OpenVoice v2 converter checkpoint
_HF_BASE = "https://huggingface.co/myshell-ai/OpenVoiceV2/resolve/main/checkpoints_v2/converter"
_GITHUB_BASE = (
    "https://github.com/myshell-ai/OpenVoice/releases/download/v0.2.0"
)

_REQUIRED_FILES: dict = {
    "converter/config.json": [
        f"{_HF_BASE}/config.json",
    ],
    "converter/checkpoint.pth": [
        f"{_HF_BASE}/checkpoint.pth",
    ],
}


def _download_file(url: str, dest: Path, timeout: int = 120) -> None:
    """Download a file from URL to dest, creating parent directories."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    logger.info("Downloading %s → %s", url, dest)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "indicvoice-ai/7.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
        tmp.replace(dest)
        logger.info("Downloaded %s (%d bytes)", dest.name, dest.stat().st_size)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def check_checkpoints(checkpoint_dir: Optional[Path] = None) -> Tuple[bool, Optional[str]]:
    """
    Verify that all required checkpoint files exist and are non-empty.

    Returns:
        (True, None)          — all files present
        (False, error_str)    — missing or corrupt, error_str explains why
    """
    ckpt_dir = checkpoint_dir or _CHECKPOINT_DIR
    missing = []
    for rel_path in _REQUIRED_FILES:
        full = ckpt_dir / rel_path
        if not full.exists() or full.stat().st_size == 0:
            missing.append(rel_path)
    if missing:
        return False, (
            f"Missing checkpoint files in {ckpt_dir}: {missing}. "
            f"Set OPENVOICE_CHECKPOINT_DIR or run: python -m checkpoint_manager download"
        )
    return True, None


def ensure_checkpoints(
    checkpoint_dir: Optional[Path] = None,
    auto_download: Optional[bool] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Check checkpoints; if missing and auto-download is enabled, download them.

    Returns:
        (True, None)         — checkpoints available (either already present or just downloaded)
        (False, error_str)   — unavailable with exact reason
    """
    ckpt_dir = checkpoint_dir or _CHECKPOINT_DIR
    do_download = auto_download if auto_download is not None else _AUTO_DOWNLOAD

    ok, err = check_checkpoints(ckpt_dir)
    if ok:
        logger.info("OpenVoice v2 checkpoints verified at %s", ckpt_dir)
        return True, None

    logger.warning("Checkpoint check failed: %s", err)

    if not do_download:
        return False, (
            f"{err}  (auto-download disabled — set OPENVOICE_AUTO_DOWNLOAD=true to enable)"
        )

    # Attempt download for each missing file
    logger.info("Auto-downloading OpenVoice v2 checkpoints to %s …", ckpt_dir)
    download_errors: list = []
    for rel_path, urls in _REQUIRED_FILES.items():
        dest = ckpt_dir / rel_path
        if dest.exists() and dest.stat().st_size > 0:
            continue
        downloaded = False
        last_exc: Optional[Exception] = None
        for url in urls:
            try:
                _download_file(url, dest)
                downloaded = True
                break
            except Exception as exc:
                last_exc = exc
                logger.warning("Download attempt failed (%s): %s", url, exc)
        if not downloaded:
            download_errors.append(f"{rel_path}: {last_exc}")

    if download_errors:
        reason = "; ".join(download_errors)
        return False, (
            f"OpenVoice checkpoint download failed: {reason}. "
            "Download manually from https://huggingface.co/myshell-ai/OpenVoiceV2 "
            "and set OPENVOICE_CHECKPOINT_DIR."
        )

    # Final verification
    ok2, err2 = check_checkpoints(ckpt_dir)
    if ok2:
        logger.info("OpenVoice v2 checkpoints downloaded and verified at %s", ckpt_dir)
        return True, None
    return False, f"Post-download verification failed: {err2}"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "download":
        ok, err = ensure_checkpoints(auto_download=True)
        if ok:
            print("✓ Checkpoints ready.")
        else:
            print(f"✗ {err}")
            sys.exit(1)
    else:
        ok, err = check_checkpoints()
        print(f"Checkpoints OK: {ok}" if ok else f"Missing: {err}")
