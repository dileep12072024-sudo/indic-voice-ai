"""
backend/storage/backend.py
──────────────────────────
Abstract StorageBackend interface + LocalStorageBackend implementation.

Swap LocalStorageBackend for CloudflareR2StorageBackend (or S3-compatible)
when deploying to production without changing any caller code.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger("indicvoice.storage")


# ── Abstract interface ─────────────────────────────────────────────────────

class StorageBackend(ABC):
    """
    Storage-agnostic interface for reading and writing binary blobs.

    All keys are forward-slash-delimited logical paths, e.g.:
      uploads/abc123_sample.wav
      outputs/abc123_output.wav
      embeddings/speaker_abc.npy

    Production implementations:
      - CloudflareR2StorageBackend  → calls R2 via Workers binding or S3 API
      - S3StorageBackend            → boto3 / aiobotocore
    """

    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """
        Write *data* at *key*.
        Returns the key (may differ from input for backends that normalise keys).
        """

    @abstractmethod
    async def get(self, key: str) -> Optional[bytes]:
        """Return the blob at *key*, or None if it does not exist."""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete *key*. Returns True if it existed."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if *key* exists."""

    @abstractmethod
    async def presign_url(self, key: str, ttl_seconds: int = 3600) -> str:
        """
        Return a URL that lets the client download *key* directly.

        Local:       returns a path-based URL like /outputs/{filename}
                     (served by the FastAPI static-files mount).
        Cloudflare:  returns a pre-signed R2 URL with *ttl_seconds* TTL.
        S3:          returns a pre-signed S3 URL.
        """

    @abstractmethod
    async def list_keys(self, prefix: str = "") -> list[str]:
        """Return all keys that start with *prefix* (shallow, no recursion guarantee)."""


# ── Local (filesystem) implementation ─────────────────────────────────────

class LocalStorageBackend(StorageBackend):
    """
    Filesystem-backed storage for local development.

    Directory layout mirrors the logical key namespace:
      {base_dir}/uploads/abc123_sample.wav
      {base_dir}/outputs/abc123_output.wav

    presign_url() returns a server-relative URL so the FastAPI app can
    serve files via a StaticFiles mount at /files → base_dir.

    Cloudflare equivalent:
      Replace with CloudflareR2StorageBackend that calls:
        await env.STORAGE.put(key, data)
        await env.STORAGE.get(key)
        Generate pre-signed URL via R2's S3-compatible presignedUrl API.
    """

    def __init__(self, base_dir: Path, serve_prefix: str = "/files") -> None:
        """
        Parameters
        ----------
        base_dir:
            Root directory on disk.  Sub-directories are created on demand.
        serve_prefix:
            URL prefix under which the FastAPI StaticFiles mount serves *base_dir*.
            Used by presign_url() to build client-accessible URLs.
        """
        self.base_dir     = Path(base_dir)
        self.serve_prefix = serve_prefix.rstrip("/")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """Resolve a logical key to an absolute filesystem path."""
        # Prevent path traversal: strip leading slashes and collapse ".."
        safe = Path(key.lstrip("/")).resolve().relative_to(Path("/").resolve()) if key.startswith("/") else Path(key)
        return self.base_dir / safe

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.debug("Storage PUT  key=%s  size=%d  path=%s", key, len(data), path)
        return key

    async def get(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        if not path.exists():
            logger.debug("Storage GET miss  key=%s", key)
            return None
        return path.read_bytes()

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            logger.debug("Storage DEL  key=%s", key)
            return True
        return False

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def presign_url(self, key: str, ttl_seconds: int = 3600) -> str:
        # Local dev: just return a static-files URL — no expiry enforced.
        # TTL is ignored here; real expiry is handled by the CF R2 implementation.
        clean_key = key.lstrip("/")
        url = f"{self.serve_prefix}/{clean_key}"
        logger.debug("Storage PRESIGN  key=%s  url=%s  (ttl=%ds, local=no-expiry)", key, url, ttl_seconds)
        return url

    async def list_keys(self, prefix: str = "") -> list[str]:
        root = self._path(prefix) if prefix else self.base_dir
        if not root.exists():
            return []
        keys: list[str] = []
        for entry in root.rglob("*"):
            if entry.is_file():
                keys.append(str(entry.relative_to(self.base_dir)))
        return sorted(keys)
