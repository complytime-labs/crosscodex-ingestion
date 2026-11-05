import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

import redis.asyncio as redis

from crosscodex_ingestion.config import IngestionSettings


@dataclass
class DocumentRecord:
    document_id: str
    tenant_id: str
    status: int
    metadata: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
    structured_json_uri: str = ""
    error: dict | None = None
    created_at: str = ""
    updated_at: str = ""


class StateBackend(ABC):
    @abstractmethod
    async def create(self, record: DocumentRecord) -> None:
        """Create a new document record."""

    @abstractmethod
    async def get(self, tenant_id: str, document_id: str) -> DocumentRecord | None:
        """Get a document record by tenant_id and document_id."""

    @abstractmethod
    async def update(self, tenant_id: str, document_id: str, **fields: Any) -> None:
        """Update specific fields of a document record."""

    @abstractmethod
    async def list_documents(
        self, tenant_id: str, page_size: int = 50, page_token: str = ""
    ) -> tuple[list[DocumentRecord], str]:
        """List documents for a tenant with pagination.

        Returns (records, next_page_token). Empty next_page_token means no more pages.
        """


class MemoryStateBackend(StateBackend):
    """In-memory dict keyed by (tenant_id, document_id).

    Async only to satisfy the interface: no method awaits, so each call runs to completion
    without yielding to the event loop and is atomic with respect to other coroutines. It must
    be used from the event loop thread only; never hand it to asyncio.to_thread.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], DocumentRecord] = {}

    async def create(self, record: DocumentRecord) -> None:
        key = (record.tenant_id, record.document_id)
        self._store[key] = record

    async def get(self, tenant_id: str, document_id: str) -> DocumentRecord | None:
        key = (tenant_id, document_id)
        return self._store.get(key)

    async def update(self, tenant_id: str, document_id: str, **fields: Any) -> None:
        key = (tenant_id, document_id)
        if key not in self._store:
            raise KeyError(f"Document not found: {tenant_id}/{document_id}")

        record = self._store[key]
        for field_name, value in fields.items():
            setattr(record, field_name, value)

    async def list_documents(
        self, tenant_id: str, page_size: int = 50, page_token: str = ""
    ) -> tuple[list[DocumentRecord], str]:
        tenant_records = [record for (tid, _), record in self._store.items() if tid == tenant_id]

        tenant_records.sort(key=lambda r: r.document_id)

        offset = int(page_token) if page_token else 0

        page_records = tenant_records[offset : offset + page_size]

        next_offset = offset + page_size
        next_token = str(next_offset) if next_offset < len(tenant_records) else ""

        return page_records, next_token


class RedisStateBackend(StateBackend):
    """Redis-backed. Uses hash key 'doc:<tenant_id>:<document_id>' with JSON-serialized DocumentRecord."""

    def __init__(self, redis_url: str):
        self._redis = redis.from_url(redis_url)

    def _make_key(self, tenant_id: str, document_id: str) -> str:
        return f"doc:{tenant_id}:{document_id}"

    async def create(self, record: DocumentRecord) -> None:
        key = self._make_key(record.tenant_id, record.document_id)
        data = json.dumps(asdict(record))
        await self._redis.hset(key, "data", data)

    async def get(self, tenant_id: str, document_id: str) -> DocumentRecord | None:
        key = self._make_key(tenant_id, document_id)
        data = await self._redis.hget(key, "data")
        if data is None:
            return None

        record_dict = json.loads(data)
        return DocumentRecord(**record_dict)

    async def update(self, tenant_id: str, document_id: str, **fields: Any) -> None:
        record = await self.get(tenant_id, document_id)
        if record is None:
            raise KeyError(f"Document not found: {tenant_id}/{document_id}")

        for field_name, value in fields.items():
            setattr(record, field_name, value)

        await self.create(record)

    async def list_documents(
        self, tenant_id: str, page_size: int = 50, page_token: str = ""
    ) -> tuple[list[DocumentRecord], str]:
        pattern = f"doc:{tenant_id}:*"
        records = []
        async for key in self._redis.scan_iter(match=pattern):
            data = await self._redis.hget(key, "data")
            if data:
                record_dict = json.loads(data)
                records.append(DocumentRecord(**record_dict))

        records.sort(key=lambda r: r.document_id)

        offset = int(page_token) if page_token else 0

        page_records = records[offset : offset + page_size]

        next_offset = offset + page_size
        next_token = str(next_offset) if next_offset < len(records) else ""

        return page_records, next_token


def create_state_backend(settings: IngestionSettings) -> StateBackend:
    """Factory: returns MemoryStateBackend or RedisStateBackend."""
    if settings.state_backend == "memory":
        return MemoryStateBackend()
    if settings.state_backend == "redis":
        return RedisStateBackend(settings.redis_url)
    raise ValueError(f"Unknown state backend: {settings.state_backend}")
