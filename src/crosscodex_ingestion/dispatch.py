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
from typing import Any

import nats
from nats.aio.client import Client as NATS

from crosscodex_ingestion.attestation import AttestationManager
from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.state import StateBackend
from crosscodex_ingestion.storage import StorageBackend
from crosscodex_ingestion.worker import run_conversion

logger = logging.getLogger(__name__)


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
    async def shutdown(self) -> None:
        """Wait for in-flight jobs and clean up resources."""


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

        # Track task
        self._tasks.add(task)

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

    async def shutdown(self) -> None:
        """Wait for all in-flight tasks to complete."""
        if self._tasks:
            logger.info("Waiting for %d in-flight conversion tasks", len(self._tasks))
            await asyncio.gather(*self._tasks, return_exceptions=True)
            logger.info("All conversion tasks completed")


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
        self._nc: NATS | None = None
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

    async def shutdown(self) -> None:
        """Close the NATS connection."""
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
    elif settings.execution_mode == "distributed":
        logger.info("Creating NATS dispatch backend")
        return NatsDispatch(settings=settings)
    else:
        raise ValueError(f"Unknown execution_mode: {settings.execution_mode}")
