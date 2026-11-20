import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.listing import parse_list_options
from crosscodex_ingestion.proto.crosscodex.v1.common_pb2 import (
    FieldFilter,
    JobStatus,
    ListOptions,
    Pagination,
)
from crosscodex_ingestion.state import (
    DocumentRecord,
    MemoryStateBackend,
    RedisStateBackend,
    StateBackend,
    create_state_backend,
)


def _query(page_size: int = 0, page_token: str = "", filters: tuple[FieldFilter, ...] = ()):
    return parse_list_options(
        ListOptions(pagination=Pagination(page_size=page_size, page_token=page_token), filters=filters)
    )


class FakeRedis:
    """Dict-backed stand-in for the redis.asyncio calls RedisStateBackend makes."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def hset(self, key: str, field_name: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field_name] = value

    async def hget(self, key: str, field_name: str) -> str | None:
        return self.hashes.get(key, {}).get(field_name)

    async def scan_iter(self, match: str):
        for key in list(self.hashes):
            if fnmatchcase(key, match):
                yield key


@pytest.fixture(params=["memory", "redis"])
def backend(request: pytest.FixtureRequest) -> StateBackend:
    if request.param == "memory":
        return MemoryStateBackend()
    with patch("crosscodex_ingestion.state.redis.from_url", return_value=FakeRedis()):
        return RedisStateBackend("redis://localhost:6379")


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


class TestStateBackendContract:
    """Behavior every backend must share; runs against memory and a fake Redis."""

    async def test_create_and_get(self, backend: StateBackend, sample_record: DocumentRecord):
        await backend.create(sample_record)

        retrieved = await backend.get(sample_record.tenant_id, sample_record.document_id)

        assert retrieved is not None
        assert retrieved.document_id == sample_record.document_id
        assert retrieved.tenant_id == sample_record.tenant_id
        assert retrieved.status == sample_record.status

    async def test_update_missing_raises_keyerror(self, backend: StateBackend):
        with pytest.raises(KeyError):
            await backend.update("tenant-a", "absent", status=1)

    async def test_get_returns_none_for_missing(self, backend: StateBackend):
        result = await backend.get("tenant-a", "nonexistent")

        assert result is None

    async def test_update_changes_fields(self, backend: StateBackend, sample_record: DocumentRecord):
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

    async def test_list_documents_returns_only_tenant_records(self, backend: StateBackend):
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

        records, next_token = await backend.list_documents("tenant-a", _query(page_size=50))

        assert len(records) == 3
        assert all(r.tenant_id == "tenant-a" for r in records)
        assert next_token == ""

    async def test_list_documents_pagination(self, backend: StateBackend):
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

        page1, token1 = await backend.list_documents("tenant-a", _query(page_size=2))
        assert len(page1) == 2
        assert token1 != ""

        page2, token2 = await backend.list_documents("tenant-a", _query(page_size=2, page_token=token1))
        assert len(page2) == 2
        assert token2 != ""

        page3, token3 = await backend.list_documents("tenant-a", _query(page_size=2, page_token=token2))
        assert len(page3) == 1
        assert token3 == ""

    async def test_list_documents_filters_by_status(self, backend: StateBackend):
        await backend.create(
            DocumentRecord(document_id="ok", tenant_id="tenant-a", status=JobStatus.JOB_STATUS_COMPLETED)
        )
        await backend.create(
            DocumentRecord(document_id="bad", tenant_id="tenant-a", status=JobStatus.JOB_STATUS_FAILED)
        )
        failed = FieldFilter(field="status", operator="eq", value="JOB_STATUS_FAILED")

        records, _ = await backend.list_documents("tenant-a", _query(filters=(failed,)))

        assert [r.document_id for r in records] == ["bad"]

    async def test_tenant_isolation(self, backend: StateBackend):
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
    # Memory only: these cover in-process atomicity of a dict store; Redis is a separate process.
    async def test_concurrent_writers_and_listers_stay_consistent(self):
        backend = MemoryStateBackend()

        async def writer(i: int) -> None:
            await backend.create(DocumentRecord(document_id=f"doc-{i:03d}", tenant_id="tenant-a", status=1))
            await asyncio.sleep(0)
            await backend.update("tenant-a", f"doc-{i:03d}", status=3)

        async def lister() -> None:
            for _ in range(20):
                records, _ = await backend.list_documents("tenant-a", _query(page_size=1000))
                ids = [r.document_id for r in records]
                assert ids == sorted(ids)
                await asyncio.sleep(0)

        await asyncio.gather(*(writer(i) for i in range(100)), *(lister() for _ in range(5)))

        records, token = await backend.list_documents("tenant-a", _query(page_size=1000))
        assert len(records) == 100
        assert token == ""
        assert all(r.status == 3 for r in records)


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
    async def test_list_documents_scans_only_the_tenant_prefix(self, mock_redis_from_url: MagicMock):
        patterns: list[str] = []

        async def scan_iter(match: str):
            patterns.append(match)
            return
            yield

        mock_redis = AsyncMock()
        mock_redis.scan_iter = scan_iter
        mock_redis_from_url.return_value = mock_redis

        page, token = await RedisStateBackend("redis://localhost:6379").list_documents("tenant-a", _query())

        assert (patterns, page, token) == (["doc:tenant-a:*"], [], "")

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
