"""Dispatch module — embedded and distributed job execution backends.

This module provides two dispatch backends for submitting conversion jobs:
1. EmbeddedDispatch: Runs jobs in the current process using asyncio.create_task()
   with a semaphore to limit concurrency.
2. NatsDispatch: Publishes jobs to a NATS JetStream work queue for distributed processing.

The create_dispatch factory returns the appropriate backend based on execution_mode.
"""

import asyncio
import base64
import json
import logging
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import nats

from crosscodex_ingestion.attestation import AttestationManager
from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.state import StateBackend
from crosscodex_ingestion.storage import StorageBackend
from crosscodex_ingestion.worker import SHUTDOWN_ERROR, record_failure, run_conversion

if TYPE_CHECKING:
    from nats.aio.client import Client

logger = logging.getLogger(__name__)

# After cancelling over-grace conversions, how long to wait for them to kill their Docling
# subprocess and record FAILED. Kept small so shutdown stays near the configured grace period.
CANCEL_CLEANUP_SECONDS = 2.0


@dataclass(frozen=True)
class _QueuedJob:
    """What is needed to fail a job that is still waiting for a concurrency slot."""

    document_id: str
    tenant_id: str
    metadata: dict


class DispatchBackend(ABC):
    """Abstract base class for dispatch backends."""

    @abstractmethod
    async def dispatch(
        self,
        document_id: str,
        tenant_id: str,
        content: bytes | None,
        source_uri: str | None,
        metadata: dict,
    ) -> None:
        """Submit a conversion job."""

    @abstractmethod
    async def shutdown(self, grace_seconds: float) -> None:
        """Stop work and clean up resources, taking about *grace_seconds* at most.

        Implementations give in-flight work up to *grace_seconds* to finish, then abandon
        it so that another attempt can be made.
        """


class EmbeddedDispatch(DispatchBackend):
    """Embedded asyncio dispatch backend.

    Runs conversion jobs in the current process using asyncio.create_task().
    A semaphore limits concurrency to max_concurrent_conversions.
    """

    def __init__(
        self,
        settings: IngestionSettings,
        state: StateBackend,
        storage: StorageBackend,
        attestation: AttestationManager | None,
        audit: AuditPublisher,
    ):
        """Initialize the embedded dispatch backend.

        Args:
            settings: Configuration settings.
            state: State management backend.
            storage: Storage backend.
            attestation: Optional attestation manager.
            audit: Audit event publisher.
        """
        self._settings = settings
        self._state = state
        self._storage = storage
        self._attestation = attestation
        self._audit = audit

        # Create semaphore to limit concurrent conversions
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_conversions)

        # Track in-flight tasks for shutdown
        self._tasks: set[asyncio.Task] = set()
        # Jobs still waiting on the semaphore. An entry is removed in the same event-loop step
        # in which the job acquires its slot, so membership means "has not started converting".
        self._queued: dict[asyncio.Task, _QueuedJob] = {}

    async def dispatch(
        self,
        document_id: str,
        tenant_id: str,
        content: bytes | None,
        source_uri: str | None,
        metadata: dict,
    ) -> None:
        """Submit a conversion job by creating an asyncio task.

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant identifier.
            content: Optional raw document bytes.
            source_uri: Optional URL to fetch document from.
            metadata: Document metadata.
        """
        # Create task wrapping run_conversion
        task = asyncio.create_task(
            self._run_with_semaphore(
                document_id=document_id,
                tenant_id=tenant_id,
                content=content,
                source_uri=source_uri,
                metadata=metadata,
            )
        )

        # Track task; it counts as queued until it acquires a concurrency slot
        self._tasks.add(task)
        self._queued[task] = _QueuedJob(document_id, tenant_id, metadata)

        # Remove from tracking when done
        task.add_done_callback(self._tasks.discard)

    async def _run_with_semaphore(
        self,
        document_id: str,
        tenant_id: str,
        content: bytes | None,
        source_uri: str | None,
        metadata: dict,
    ) -> None:
        """Run conversion with semaphore-limited concurrency.

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant identifier.
            content: Optional raw document bytes.
            source_uri: Optional URL to fetch document from.
            metadata: Document metadata.
        """
        async with self._semaphore:
            del self._queued[asyncio.current_task()]  # type: ignore[arg-type]
            await run_conversion(
                document_id=document_id,
                tenant_id=tenant_id,
                content=content,
                source_uri=source_uri,
                metadata=metadata,
                settings=self._settings,
                state=self._state,
                storage=self._storage,
                attestation=self._attestation,
                audit=self._audit,
            )

    async def shutdown(self, grace_seconds: float) -> None:
        """Bounded shutdown: fail queued jobs, let running ones finish, then cancel the rest.

        1. Jobs waiting for a slot are failed at once (retryable); starting minutes-long
           conversions that would be cut off is wasted work.
        2. Running conversions get *grace_seconds* to finish.
        3. Any still running are cancelled. run_conversion kills the Docling subprocess and
           marks the document FAILED (retryable), then cancellation completes, bounded by
           CANCEL_CLEANUP_SECONDS.
        """
        if not self._tasks:
            return

        queued = dict(self._queued)
        for task in queued:
            task.cancel()
        results = await asyncio.gather(
            *(
                record_failure(
                    self._state,
                    self._audit,
                    job.tenant_id,
                    job.document_id,
                    job.metadata,
                    SHUTDOWN_ERROR,
                    "shutdown",
                    "unknown",
                )
                for job in queued.values()
            ),
            return_exceptions=True,
        )
        for job, result in zip(queued.values(), results, strict=True):
            if isinstance(result, BaseException):
                logger.error("Could not record shutdown failure for queued document %s: %r", job.document_id, result)
        if queued:
            logger.info("Failed %d queued conversions (retryable)", len(queued))

        running = self._tasks - queued.keys()
        if not running:
            return
        logger.info("Waiting up to %.1fs for %d in-flight conversions", grace_seconds, len(running))
        _, unfinished = await asyncio.wait(running, timeout=grace_seconds)
        if not unfinished:
            logger.info("All conversion tasks completed")
            return

        logger.warning("Grace period expired; cancelling %d conversions", len(unfinished))
        for task in unfinished:
            task.cancel()
        await asyncio.wait(unfinished, timeout=CANCEL_CLEANUP_SECONDS)
        for task in unfinished:
            if not task.done():
                logger.error("Conversion task did not stop within %.1fs of cancellation", CANCEL_CLEANUP_SECONDS)
            elif not task.cancelled() and task.exception() is not None:
                logger.error("Conversion task failed while cancelling: %r", task.exception())


class NatsDispatch(DispatchBackend):
    """NATS JetStream dispatch backend.

    Publishes conversion jobs to a NATS JetStream work queue for distributed processing.
    Jobs are serialized as JSON and published to the 'ingestion.jobs' subject.
    """

    def __init__(self, settings: IngestionSettings):
        """Initialize the NATS dispatch backend.

        Args:
            settings: Configuration settings with NATS connection details.
        """
        self._settings = settings
        self._nc: Client | None = None
        self._js: Any = None

    async def _connect(self) -> None:
        """Establish connection to NATS server."""
        if self._nc:
            return

        # Build TLS context if certificates are provided
        tls_context: ssl.SSLContext | None = None
        if self._settings.nats_tls_cert and self._settings.nats_tls_key:
            tls_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            tls_context.load_cert_chain(
                certfile=self._settings.nats_tls_cert,
                keyfile=self._settings.nats_tls_key,
            )
            if self._settings.nats_tls_ca:
                tls_context.load_verify_locations(cafile=self._settings.nats_tls_ca)

        # Connect to NATS
        logger.info("Connecting to NATS at %s", self._settings.nats_url)
        self._nc = await nats.connect(
            servers=[self._settings.nats_url],
            tls=tls_context,
        )

        # Get JetStream context (synchronous call)
        self._js = self._nc.jetstream()
        logger.info("Connected to NATS JetStream")

    async def dispatch(
        self,
        document_id: str,
        tenant_id: str,
        content: bytes | None,
        source_uri: str | None,
        metadata: dict,
    ) -> None:
        """Submit a conversion job by publishing to NATS.

        Args:
            document_id: Unique document identifier.
            tenant_id: Tenant identifier.
            content: Optional raw document bytes (base64-encoded in payload).
            source_uri: Optional URL to fetch document from.
            metadata: Document metadata.
        """
        # Ensure connected
        await self._connect()

        # Serialize job payload
        job = {
            "document_id": document_id,
            "tenant_id": tenant_id,
            "content": base64.b64encode(content).decode("utf-8") if content else None,
            "source_uri": source_uri,
            "metadata": metadata,
        }

        payload = json.dumps(job, separators=(",", ":")).encode("utf-8")

        # Publish to ingestion.jobs subject
        logger.debug("Publishing job to NATS: document_id=%s", document_id)
        await self._js.publish(
            subject="ingestion.jobs",
            payload=payload,
        )

    async def shutdown(self, grace_seconds: float) -> None:
        """Flush pending publishes and close the NATS connection.

        This backend only publishes jobs; consuming them is done by a separate worker that
        is not part of this repository, so there are no in-flight messages to negatively
        acknowledge here. *grace_seconds* is unused: drain() is bounded by the NATS client.
        """
        if self._nc:
            logger.info("Closing NATS connection")
            await self._nc.drain()
            await self._nc.close()
            self._nc = None
            self._js = None


def create_dispatch(
    settings: IngestionSettings,
    state: StateBackend,
    storage: StorageBackend,
    attestation: AttestationManager | None,
    audit: AuditPublisher,
) -> DispatchBackend:
    """Factory function to create the appropriate dispatch backend.

    Args:
        settings: Configuration settings.
        state: State management backend.
        storage: Storage backend.
        attestation: Optional attestation manager.
        audit: Audit event publisher.

    Returns:
        DispatchBackend instance based on execution_mode.

    Raises:
        ValueError: If execution_mode is not recognized.
    """
    if settings.execution_mode == "embedded":
        logger.info("Creating embedded dispatch backend")
        return EmbeddedDispatch(
            settings=settings,
            state=state,
            storage=storage,
            attestation=attestation,
            audit=audit,
        )
    if settings.execution_mode == "distributed":
        logger.info("Creating NATS dispatch backend")
        return NatsDispatch(settings=settings)
    raise ValueError(f"Unknown execution_mode: {settings.execution_mode}")
