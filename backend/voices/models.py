"""
backend/voices/models.py  — Phase 7: Real Voice Cloning
──────────────────────────────────────────────────────────
VoiceProfile dataclass and VoiceStore:
  • JSON-file backed persistence  (voices.json in BASE_DIR)
  • Async-safe via asyncio.Lock
  • Full CRUD: create / get / list / delete / update
  • cloning_ready flag: True only when speaker embedding extracted successfully
  • embedding_key: storage key for the .npy speaker embedding tensor
  • embedding_error: exact error string from failed extraction (never silenced)

Design notes
────────────
- No external dependencies beyond the stdlib.
- JSON schema is intentionally flat so it survives future SQLite migration.
- Sample audio bytes are stored on disk (uploads/) referenced by sample_key.
- Speaker embeddings stored as .npy files referenced by embedding_key.
- cloning_ready=False by default; set to True only after successful extraction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("indicvoice.voices")


# ── Data model ─────────────────────────────────────────────────────────────

@dataclass
class VoiceProfile:
    """A saved voice profile created from an uploaded audio sample."""

    id:              str
    name:            str            # user-visible display name
    language:        str            # te / ta / hi / en
    sample_key:      str            # storage key for the reference audio file
    sample_size:     int            # bytes
    created_at:      float          # unix timestamp
    description:     str  = ""      # optional free-text notes
    tags:            list  = field(default_factory=list)

    # ── Cloning readiness (Phase 7) ────────────────────────────────────────
    cloning_ready:   bool           = False   # True only after successful embedding extraction
    embedding_key:   Optional[str]  = None    # storage key for the .npy speaker embedding
    embedding_error: Optional[str]  = None    # exact error from last failed extraction

    # ── Serialisation ──────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "VoiceProfile":
        return cls(
            id=d["id"],
            name=d["name"],
            language=d["language"],
            sample_key=d["sample_key"],
            sample_size=d.get("sample_size", 0),
            created_at=d.get("created_at", time.time()),
            description=d.get("description", ""),
            tags=d.get("tags", []),
            cloning_ready=d.get("cloning_ready", False),
            embedding_key=d.get("embedding_key", None),
            embedding_error=d.get("embedding_error", None),
        )


# ── Store ──────────────────────────────────────────────────────────────────

class VoiceStore:
    """
    Async, file-backed voice profile store.

    All mutations hold ``_lock`` to make concurrent FastAPI requests safe.
    The JSON file is written atomically (temp-file + rename) to avoid
    corruption on power-loss or SIGKILL.
    """

    _DB_FILENAME = "voices.json"

    def __init__(self, base_dir: Path) -> None:
        self._path  = base_dir / self._DB_FILENAME
        self._lock  = asyncio.Lock()
        self._cache: Optional[dict] = None   # lazy load

    # ── Internal helpers ───────────────────────────────────────────────────

    def _load_sync(self) -> dict:
        """Read the JSON file and return an id-keyed dict.  No lock."""
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {v["id"]: VoiceProfile.from_dict(v) for v in raw.get("voices", [])}
        except Exception as exc:
            logger.warning("VoiceStore: failed to load %s (%s) — starting empty", self._path, exc)
            return {}

    def _save_sync(self, profiles: dict) -> None:
        """Atomically write profiles to disk.  No lock."""
        tmp = self._path.with_suffix(".tmp")
        payload = {"voices": [p.to_dict() for p in profiles.values()]}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    async def _profiles(self) -> dict:
        """Return the cache, loading from disk once if needed.  Caller holds lock."""
        if self._cache is None:
            self._cache = self._load_sync()
        return self._cache

    # ── Public API ─────────────────────────────────────────────────────────

    async def list_voices(self) -> list:
        """Return all profiles sorted newest-first."""
        async with self._lock:
            profiles = await self._profiles()
            return sorted(profiles.values(), key=lambda p: p.created_at, reverse=True)

    async def get_voice(self, voice_id: str) -> Optional[VoiceProfile]:
        """Return a single profile by id, or None."""
        async with self._lock:
            profiles = await self._profiles()
            return profiles.get(voice_id)

    async def create_voice(
        self,
        name:        str,
        language:    str,
        sample_key:  str,
        sample_size: int,
        description: str = "",
        tags:        Optional[list] = None,
    ) -> VoiceProfile:
        """Persist a new voice profile and return it."""
        async with self._lock:
            profiles = await self._profiles()
            profile = VoiceProfile(
                id=uuid.uuid4().hex,
                name=name,
                language=language,
                sample_key=sample_key,
                sample_size=sample_size,
                created_at=time.time(),
                description=description,
                tags=tags or [],
                cloning_ready=False,
                embedding_key=None,
                embedding_error=None,
            )
            profiles[profile.id] = profile
            self._save_sync(profiles)
            logger.info("VoiceStore: created id=%s name=%r lang=%s", profile.id, name, language)
            return profile

    async def update_voice(
        self,
        voice_id:        str,
        cloning_ready:   Optional[bool] = None,
        embedding_key:   Optional[str]  = None,
        embedding_error: Optional[str]  = None,
    ) -> Optional[VoiceProfile]:
        """
        Patch cloning metadata on an existing profile.
        Returns updated profile, or None if not found.
        Thread-safe: holds lock during read-modify-write.
        """
        async with self._lock:
            profiles = await self._profiles()
            profile = profiles.get(voice_id)
            if profile is None:
                logger.warning("VoiceStore.update_voice: id=%s not found", voice_id)
                return None
            if cloning_ready is not None:
                profile.cloning_ready = cloning_ready
            if embedding_key is not None:
                profile.embedding_key = embedding_key
            if embedding_error is not None:
                profile.embedding_error = embedding_error
            self._save_sync(profiles)
            logger.info(
                "VoiceStore: updated id=%s cloning_ready=%s embedding_key=%s",
                voice_id, profile.cloning_ready, profile.embedding_key,
            )
            return profile

    async def delete_voice(self, voice_id: str) -> bool:
        """
        Remove a profile by id.
        Returns True if it existed, False if not found.
        """
        async with self._lock:
            profiles = await self._profiles()
            if voice_id not in profiles:
                return False
            del profiles[voice_id]
            self._save_sync(profiles)
            logger.info("VoiceStore: deleted id=%s", voice_id)
            return True
