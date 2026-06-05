"""
backend/jobs/models.py
──────────────────────
Job data model and status enum.
Shared by all JobManager implementations (local, Cloudflare KV, Redis, etc.).

Changes in Phase 7 (Real Voice Cloning)
────────────────────────────────────────
CLONE-3  JobMeta gains a new field:
            cloning_error  — exact error string when cloning failed
          Job.to_dict() exposes it so GET /job/{id} clients can
          display the exact failure reason (never silenced).

Changes in Phase 5
──────────────────
UI-3  JobMeta gains two fields:
        fallback_used  — True when clone mode fell back to EdgeTTS
        provider_name  — name string of the provider that actually ran
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    """Lifecycle states for a TTS generation job."""
    QUEUED     = "queued"      # created, waiting for a worker to pick it up
    PROCESSING = "processing"  # worker has started TTS inference
    COMPLETED  = "completed"   # output WAV is ready in storage
    FAILED     = "failed"      # unrecoverable error; see error_message


@dataclass
class JobMeta:
    """
    Immutable payload that describes what this job should do.
    Stored alongside the job record so the worker can act without
    re-reading the original HTTP request.
    """
    language:        str                    # "te" | "ta" | "hi" | "en"
    text:            str                    # text to synthesise
    voice:           str                    # provider voice ID
    upload_key:      str                    # storage key for the input audio sample
    mode:            str  = "standard"      # "standard" | "clone"
    cloning_applied: bool = False           # True only when OpenVoice ran successfully
    fallback_used:   bool = False           # True when clone fell back to EdgeTTS
    provider_name:   Optional[str] = None   # name of provider that ran
    cloning_error:   Optional[str] = None   # CLONE-3: exact error from failed cloning
    text_len:        int  = 0
    sample_size_b:   int  = 0               # uploaded file size in bytes

    def __post_init__(self) -> None:
        self.text_len = len(self.text)


@dataclass
class Job:
    """
    Full job record.  This is what gets stored in the job store and
    returned by GET /job/{job_id}.
    """
    id:            str        = field(default_factory=lambda: uuid.uuid4().hex)
    status:        JobStatus  = JobStatus.QUEUED
    meta:          Optional[JobMeta] = None

    # Populated when the job finishes (successfully or not)
    output_key:    Optional[str] = None   # storage key for the output WAV
    output_url:    Optional[str] = None   # pre-signed / local URL for the client
    error_message: Optional[str] = None

    # Unix timestamps (float)
    created_at:    float = field(default_factory=time.time)
    updated_at:    float = field(default_factory=time.time)
    completed_at:  Optional[float] = None

    def touch(self) -> None:
        """Update the updated_at timestamp in place."""
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON responses."""
        return {
            "job_id":          self.id,
            "status":          self.status.value,
            "language":        self.meta.language          if self.meta else None,
            "voice":           self.meta.voice             if self.meta else None,
            "text_len":        self.meta.text_len          if self.meta else None,
            "mode":            self.meta.mode              if self.meta else "standard",
            "cloning_applied": self.meta.cloning_applied   if self.meta else False,
            "fallback_used":   self.meta.fallback_used     if self.meta else False,
            "provider_name":   self.meta.provider_name     if self.meta else None,
            # CLONE-3: exact error reason — never silenced
            "cloning_error":   self.meta.cloning_error     if self.meta else None,
            "output_url":      self.output_url,
            "error_message":   self.error_message,
            "created_at":      self.created_at,
            "updated_at":      self.updated_at,
            "completed_at":    self.completed_at,
        }
