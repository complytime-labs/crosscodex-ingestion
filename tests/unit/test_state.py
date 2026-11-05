import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.proto.crosscodex.v1.common_pb2 import JobStatus
from crosscodex_ingestion.state import (
    DocumentRecord,
    MemoryStateBackend,
    RedisStateBackend,
    create_state_backend,
)


@pytest.fixture
def sample_record() -> DocumentRecord:
    now = datetime.now(UTC).isoformat()
    return DocumentRecord(
        document_id="doc-123",
        tenant_id="tenant-a",
        status=JobStatus.JOB_STATUS_PENDING,
        metadata={"mime_type": "application/pdf", "size_bytes": 1024},
        audit={"created_by": "user-1"},
        structured_json_uri="",
        error=None,
        created_at=now,
        updated_at=now,
    )


class TestMemoryStateBackend:
    async def test_create_and_get(self, sample_record: DocumentRecord):
        backend = MemoryStateBackend()
        await backend.create(sample_record)

        retrieved = await backend.get(sample_record.tenant_id, sample_record.document_id)

        assert retrieved is not None
        assert retrieved.document_id == sample_record.document_id
        assert retrieved.tenant_id == sample_record.tenant_id
        assert retrieved.status == sample_record.status

    async def test_get_returns_none_for_missing(self):
        backend = MemoryStateBackend()

        result = await backend.get("tenant-a", "nonexistent")

        assert result is None

    async def test_update_changes_fields(self, sample_record: DocumentRecord):
        backend = MemoryStateBackend()
        await backend.create(sample_record)

        await backend.update(
            sample_record.tenant_id,
            sample_record.document_id,
            status=JobStatus.JOB_STATUS_COMPLETED,
            structured_json_uri="s3://bucket/doc-123.json",
        )

        retrieved = await backend.get(sample_record.tenant_id, sample_record.document_id)
        assert retrieved is not None
        assert retrieved.status == JobStatus.JOB_STATUS_COMPLETED
        assert retrieved.structured_json_uri == "s3://bucket/doc-123.json"

    async def test_list_documents_returns_only_tenant_records(self):
        backend = MemoryStateBackend()

        now = datetime.now(UTC).isoformat()
        for tenant in ["tenant-a", "tenant-b"]:
            for i in range(3):
                await backend.create(
                    DocumentRecord(
                        document_id=f"doc-{tenant}-{i}",
                        tenant_id=tenant,
                        status=JobStatus.JOB_STATUS_PENDING,
                        metadata={},
                        audit={},
                        created_at=now,
                        updated_at=now,
                    )
                )

        records, next_token = await backend.list_documents("tenant-a", page_size=50)

        assert len(records) == 3
        assert all(r.tenant_id == "tenant-a" for r in records)
        assert next_token == ""

    async def test_list_documents_pagination(self):
        backend = MemoryStateBackend()

        now = datetime.now(UTC).isoformat()
        for i in range(5):
            await backend.create(
                DocumentRecord(
                    document_id=f"doc-{i}",
                    tenant_id="tenant-a",
                    status=JobStatus.JOB_STATUS_PENDING,
                    metadata={},
                    audit={},
                    created_at=now,
                    updated_at=now,
                )
            )

        page1, token1 = await backend.list_documents("tenant-a", page_size=2)
        assert len(page1) == 2
        assert token1 != ""

        page2, token2 = await backend.list_documents("tenant-a", page_size=2, page_token=token1)
        assert len(page2) == 2
        assert token2 != ""

        page3, token3 = await backend.list_documents("tenant-a", page_size=2, page_token=token2)
        assert len(page3) == 1
        assert token3 == ""

    async def test_tenant_isolation(self):
        backend = MemoryStateBackend()

        now = datetime.now(UTC).isoformat()
        await backend.create(
            DocumentRecord(
                document_id="doc-a",
                tenant_id="tenant-a",
                status=JobStatus.JOB_STATUS_PENDING,
                metadata={},
                audit={},
                created_at=now,
                updated_at=now,
            )
        )
        await backend.create(
            DocumentRecord(
                document_id="doc-b",
                tenant_id="tenant-b",
                status=JobStatus.JOB_STATUS_PENDING,
                metadata={},
                audit={},
                created_at=now,
                updated_at=now,
            )
        )

        result_a = await backend.get("tenant-a", "doc-b")
        result_b = await backend.get("tenant-b", "doc-a")

        assert result_a is None
        assert result_b is None


class TestMemoryStateBackendConcurrency:
    async def test_concurrent_writers_and_listers_stay_consistent(self):
        backend = MemoryStateBackend()

        async def writer(i: int) -> None:
            await backend.create(DocumentRecord(document_id=f"doc-{i:03d}", tenant_id="tenant-a", status=1))
            await asyncio.sleep(0)
            await backend.update("tenant-a", f"doc-{i:03d}", status=3)

        async def lister() -> None:
            for _ in range(20):
                records, _ = await backend.list_documents("tenant-a", page_size=1000)
                ids = [r.document_id for r in records]
                assert ids == sorted(ids)
                await asyncio.sleep(0)

        await asyncio.gather(*(writer(i) for i in range(100)), *(lister() for _ in range(5)))

        records, token = await backend.list_documents("tenant-a", page_size=1000)
        assert len(records) == 100
        assert token == ""
        assert all(r.status == 3 for r in records)

    async def test_update_missing_raises_keyerror(self):
        with pytest.raises(KeyError):
            await MemoryStateBackend().update("tenant-a", "absent", status=1)


def _record_json(record: DocumentRecord) -> str:
    return json.dumps(asdict(record))


class TestRedisStateBackend:
    @patch("crosscodex_ingestion.state.redis.from_url")
    async def test_create_calls_hset(self, mock_redis_from_url: MagicMock, sample_record: DocumentRecord):
        mock_redis = AsyncMock()
        mock_redis_from_url.return_value = mock_redis

        backend = RedisStateBackend("redis://localhost:6379")
        await backend.create(sample_record)

        expected_key = f"doc:{sample_record.tenant_id}:{sample_record.document_id}"
        mock_redis.hset.assert_awaited_once()
        assert mock_redis.hset.call_args[0][0] == expected_key

    @patch("crosscodex_ingestion.state.redis.from_url")
    async def test_get_calls_hget(self, mock_redis_from_url: MagicMock, sample_record: DocumentRecord):
        mock_redis = AsyncMock()
        mock_redis_from_url.return_value = mock_redis
        mock_redis.hget.return_value = _record_json(sample_record)

        backend = RedisStateBackend("redis://localhost:6379")
        result = await backend.get(sample_record.tenant_id, sample_record.document_id)

        expected_key = f"doc:{sample_record.tenant_id}:{sample_record.document_id}"
        mock_redis.hget.assert_awaited_once_with(expected_key, "data")
        assert result == sample_record

    @patch("crosscodex_ingestion.state.redis.from_url")
    async def test_get_returns_none_for_missing(self, mock_redis_from_url: MagicMock):
        mock_redis = AsyncMock()
        mock_redis_from_url.return_value = mock_redis
        mock_redis.hget.return_value = None

        backend = RedisStateBackend("redis://localhost:6379")

        assert await backend.get("tenant-a", "absent") is None

    @patch("crosscodex_ingestion.state.redis.from_url")
    async def test_update_writes_merged_record(self, mock_redis_from_url: MagicMock, sample_record: DocumentRecord):
        mock_redis = AsyncMock()
        mock_redis_from_url.return_value = mock_redis
        mock_redis.hget.return_value = _record_json(sample_record)

        backend = RedisStateBackend("redis://localhost:6379")
        await backend.update("tenant-a", "doc-123", status=JobStatus.JOB_STATUS_COMPLETED)

        written = json.loads(mock_redis.hset.call_args[0][2])
        assert written["status"] == JobStatus.JOB_STATUS_COMPLETED
        assert written["document_id"] == "doc-123"

    @patch("crosscodex_ingestion.state.redis.from_url")
    async def test_update_missing_raises_keyerror_without_writing(self, mock_redis_from_url: MagicMock):
        mock_redis = AsyncMock()
        mock_redis_from_url.return_value = mock_redis
        mock_redis.hget.return_value = None

        backend = RedisStateBackend("redis://localhost:6379")

        with pytest.raises(KeyError):
            await backend.update("tenant-a", "absent", status=1)
        mock_redis.hset.assert_not_awaited()

    @patch("crosscodex_ingestion.state.redis.from_url")
    async def test_list_documents_scans_sorts_and_paginates(self, mock_redis_from_url: MagicMock):
        records = {
            f"doc:tenant-a:doc-{i}": DocumentRecord(document_id=f"doc-{i}", tenant_id="tenant-a", status=1)
            for i in (2, 0, 1)
        }

        async def scan_iter(match: str):
            assert match == "doc:tenant-a:*"
            for key in records:
                yield key

        mock_redis = AsyncMock()
        mock_redis.scan_iter = scan_iter
        mock_redis.hget.side_effect = lambda key, _field: _record_json(records[key])
        mock_redis_from_url.return_value = mock_redis

        backend = RedisStateBackend("redis://localhost:6379")
        page, next_page = await backend.list_documents("tenant-a", page_size=2)

        assert [r.document_id for r in page] == ["doc-0", "doc-1"]
        assert next_page == "2"

    @patch("crosscodex_ingestion.state.redis.from_url")
    async def test_slow_redis_call_does_not_block_event_loop(self, mock_redis_from_url: MagicMock):
        """While a Redis round trip is pending, other coroutines must keep running."""
        release = asyncio.Event()

        async def slow_hget(*_args):
            await release.wait()

        mock_redis = AsyncMock()
        mock_redis.hget.side_effect = slow_hget
        mock_redis_from_url.return_value = mock_redis
        backend = RedisStateBackend("redis://localhost:6379")

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0)

        get_task = asyncio.create_task(backend.get("tenant-a", "doc-1"))
        ticker_task = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)

        assert not get_task.done()
        assert ticks > 10
        ticker_task.cancel()
        release.set()
        assert await get_task is None


class TestCreateStateBackend:
    async def test_returns_memory_backend(self):
        settings = IngestionSettings(state_backend="memory")
        backend = create_state_backend(settings)

        assert isinstance(backend, MemoryStateBackend)

    async def test_returns_redis_backend(self):
        settings = IngestionSettings(state_backend="redis", redis_url="redis://localhost:6379")
        backend = create_state_backend(settings)

        assert isinstance(backend, RedisStateBackend)
