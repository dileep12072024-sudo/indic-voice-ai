"""
backend/jobs/manager.py
───────────────────────
Abstract JobManager interface + LocalJobManager implementation.

Swap LocalJobManager for CloudflareKVJobManager (or a Redis-backed one)
when deploying to production without changing any caller code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from .models import Job, JobMeta, JobStatus

logger = logging.getLogger("indicvoice.jobs")


# ── Abstract interface ─────────────────────────────────────────────────────

class JobManager(ABC):
    """
    Storage-agnostic interface for creating and tracking TTS jobs.

    Production implementations:
      - CloudflareKVJobManager  → stores Job JSON in CF KV (TTL 8h)
      - RedisJobManager         → stores Job JSON in Redis with expiry
    """

    @abstractmethod
    async def create_job(self, meta: JobMeta) -> Job:
        """
        Persist a new job with status=QUEUED and return it.
        The caller immediately returns the job_id to the HTTP client.
        """

    @abstractmethod
    async def get_job(self, job_id: str) -> Optional[Job]:
        """Return the Job record, or None if not found / expired."""

    @abstractmethod
    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        output_key: Optional[str] = None,
        output_url: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Job:
        """
        Transition a job to a new status and optionally attach result data.
        Raises KeyError if job_id is not found.
        """

    @abstractmethod
    async def delete_job(self, job_id: str) -> bool:
        """
        Remove a job record (GDPR erasure, manual cleanup).
        Returns True if the record existed and was deleted.
        """

    @abstractmethod
    async def list_jobs(self, limit: int = 50) -> list[Job]:
        """Return the most recent *limit* jobs (newest first)."""


# ── Local (in-memory) implementation ──────────────────────────────────────

class LocalJobManager(JobManager):
    """
    Thread-safe in-memory job store for local development.

    - All state is lost when the server restarts (intentional for dev).
    - Uses asyncio.Lock to prevent races between the background worker
      and concurrent HTTP status-poll requests.
    - Automatically evicts jobs older than `ttl_seconds` (default 8 h)
      during list/get operations so memory does not grow unboundedly.

    Cloudflare equivalent:
      Replace with CloudflareKVJobManager that calls:
        env.JOBS.put(job_id, json.dumps(job.to_dict()), { expirationTtl: 28800 })
        env.JOBS.get(job_id)
    """

    TTL_SECONDS: int = 8 * 3600   # 8 hours — matches CF KV design

    def __init__(self) -> None:
        self._store: dict[str, Job] = {}
        self._lock  = asyncio.Lock()

    # ── helpers ──────────────────────────────────────────────────────────

    def _is_expired(self, job: Job) -> bool:
        return (time.time() - job.created_at) > self.TTL_SECONDS

    async def _evict_stale(self) -> None:
        """Remove expired jobs while holding the lock."""
        expired = [jid for jid, j in self._store.items() if self._is_expired(j)]
        for jid in expired:
            del self._store[jid]
            logger.debug("Evicted expired job %s", jid)

    # ── JobManager interface ──────────────────────────────────────────────

    async def create_job(self, meta: JobMeta) -> Job:
        job = Job(meta=meta)
        async with self._lock:
            self._store[job.id] = job
        logger.info("Job created  id=%s lang=%s text_len=%d", job.id, meta.language, meta.text_len)
        return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            await self._evict_stale()
            job = self._store.get(job_id)
        if job is None:
            logger.debug("get_job miss id=%s", job_id)
        return job

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        output_key: Optional[str] = None,
        output_url: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Job:
        async with self._lock:
            job = self._store.get(job_id)
            if job is None:
                raise KeyError(f"Job not found: {job_id}")
            job.status        = status
            job.output_key    = output_key    or job.output_key
            job.output_url    = output_url    or job.output_url
            job.error_message = error_message or job.error_message
            job.touch()
            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                job.completed_at = time.time()

        logger.info(
            "Job updated  id=%s status=%s output_key=%s error=%s",
            job_id, status.value, output_key, error_message,
        )
        return job

    async def delete_job(self, job_id: str) -> bool:
        async with self._lock:
            existed = job_id in self._store
            self._store.pop(job_id, None)
        logger.info("Job deleted  id=%s existed=%s", job_id, existed)
        return existed

    async def list_jobs(self, limit: int = 50) -> list[Job]:
        async with self._lock:
            await self._evict_stale()
            jobs = sorted(self._store.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]
