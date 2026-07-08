from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from crosscodex_ingestion.proto.crosscodex.v1.common_pb2 import JobStatus

from crosscodex_ingestion.config import IngestionSettings
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
    def test_create_and_get(self, sample_record: DocumentRecord):
        backend = MemoryStateBackend()
        backend.create(sample_record)

        retrieved = backend.get(sample_record.tenant_id, sample_record.document_id)

        assert retrieved is not None
        assert retrieved.document_id == sample_record.document_id
        assert retrieved.tenant_id == sample_record.tenant_id
        assert retrieved.status == sample_record.status

    def test_get_returns_none_for_missing(self):
        backend = MemoryStateBackend()

        result = backend.get("tenant-a", "nonexistent")

        assert result is None

    def test_update_changes_fields(self, sample_record: DocumentRecord):
        backend = MemoryStateBackend()
        backend.create(sample_record)

        backend.update(
            sample_record.tenant_id,
            sample_record.document_id,
            status=JobStatus.JOB_STATUS_COMPLETED,
            structured_json_uri="s3://bucket/doc-123.json",
        )

        retrieved = backend.get(sample_record.tenant_id, sample_record.document_id)
        assert retrieved is not None
        assert retrieved.status == JobStatus.JOB_STATUS_COMPLETED
        assert retrieved.structured_json_uri == "s3://bucket/doc-123.json"

    def test_list_documents_returns_only_tenant_records(self):
        backend = MemoryStateBackend()

        now = datetime.now(UTC).isoformat()
        for tenant in ["tenant-a", "tenant-b"]:
            for i in range(3):
                backend.create(
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

        records, next_token = backend.list_documents("tenant-a", page_size=50)

        assert len(records) == 3
        assert all(r.tenant_id == "tenant-a" for r in records)
        assert next_token == ""

    def test_list_documents_pagination(self):
        backend = MemoryStateBackend()

        now = datetime.now(UTC).isoformat()
        for i in range(5):
            backend.create(
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

        page1, token1 = backend.list_documents("tenant-a", page_size=2)
        assert len(page1) == 2
        assert token1 != ""

        page2, token2 = backend.list_documents("tenant-a", page_size=2, page_token=token1)
        assert len(page2) == 2
        assert token2 != ""

        page3, token3 = backend.list_documents("tenant-a", page_size=2, page_token=token2)
        assert len(page3) == 1
        assert token3 == ""

    def test_tenant_isolation(self):
        backend = MemoryStateBackend()

        now = datetime.now(UTC).isoformat()
        backend.create(
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
        backend.create(
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

        result_a = backend.get("tenant-a", "doc-b")
        result_b = backend.get("tenant-b", "doc-a")

        assert result_a is None
        assert result_b is None


class TestRedisStateBackend:
    @patch("crosscodex_ingestion.state.redis.from_url")
    def test_create_calls_hset(self, mock_redis_from_url: MagicMock, sample_record: DocumentRecord):
        mock_redis = MagicMock()
        mock_redis_from_url.return_value = mock_redis

        backend = RedisStateBackend("redis://localhost:6379")
        backend.create(sample_record)

        expected_key = f"doc:{sample_record.tenant_id}:{sample_record.document_id}"
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == expected_key

    @patch("crosscodex_ingestion.state.redis.from_url")
    def test_get_calls_hget(self, mock_redis_from_url: MagicMock, sample_record: DocumentRecord):
        mock_redis = MagicMock()
        mock_redis_from_url.return_value = mock_redis

        now = datetime.now(UTC).isoformat()
        mock_redis.hget.return_value = (
            f'{{"document_id": "{sample_record.document_id}", '
            f'"tenant_id": "{sample_record.tenant_id}", '
            f'"status": {sample_record.status}, "metadata": {{}}, "audit": {{}}, '
            f'"structured_json_uri": "", "error": null, '
            f'"created_at": "{now}", "updated_at": "{now}"}}'
        )

        backend = RedisStateBackend("redis://localhost:6379")
        result = backend.get(sample_record.tenant_id, sample_record.document_id)

        expected_key = f"doc:{sample_record.tenant_id}:{sample_record.document_id}"
        mock_redis.hget.assert_called_once_with(expected_key, "data")
        assert result is not None
        assert result.document_id == sample_record.document_id


class TestCreateStateBackend:
    def test_returns_memory_backend(self):
        settings = IngestionSettings(state_backend="memory")
        backend = create_state_backend(settings)

        assert isinstance(backend, MemoryStateBackend)

    def test_returns_redis_backend(self):
        settings = IngestionSettings(state_backend="redis", redis_url="redis://localhost:6379")
        backend = create_state_backend(settings)

        assert isinstance(backend, RedisStateBackend)
