"""Unit tests for gRPC service layer."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import grpc
import pytest
from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.service import IngestionServiceServicer
from crosscodex_ingestion.state import DocumentRecord, MemoryStateBackend


class MockServicerContext:
    """Mock gRPC servicer context that captures abort() calls."""

    def __init__(self):
        self.aborted = False
        self.abort_code = None
        self.abort_details = None

    def abort(self, code: grpc.StatusCode, details: str):
        """Capture abort call."""
        self.aborted = True
        self.abort_code = code
        self.abort_details = details
        raise grpc.RpcError(f"{code}: {details}")


@pytest.fixture
def settings():
    """Create test settings."""
    return IngestionSettings(
        max_document_size=1024 * 1024,  # 1MB
        allowed_formats="pdf,docx,html",
    )


@pytest.fixture
def state_backend():
    """Create in-memory state backend."""
    return MemoryStateBackend()


@pytest.fixture
def mock_dispatch():
    """Create mock dispatch backend."""
    dispatch = AsyncMock()
    dispatch.dispatch = AsyncMock()
    return dispatch


@pytest.fixture
def servicer(settings, state_backend, mock_dispatch):
    """Create servicer with mocked dependencies."""
    return IngestionServiceServicer(
        settings=settings,
        state_backend=state_backend,
        dispatch_backend=mock_dispatch,
    )


class TestConvertDocument:
    """Tests for ConvertDocument RPC."""

    @pytest.mark.asyncio
    async def test_convert_with_valid_bytes_returns_document_id_and_pending(
        self, servicer, mock_dispatch, state_backend
    ):
        """ConvertDocument with valid bytes returns document_id and PENDING status."""
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            content=b"fake PDF content",
            metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
        )
        context = MockServicerContext()

        response = await servicer.ConvertDocument(request, context)

        assert response.document_id
        assert response.status == common_pb2.JOB_STATUS_PENDING

        # Verify dispatch was called
        mock_dispatch.dispatch.assert_called_once()
        call_args = mock_dispatch.dispatch.call_args[1]
        assert call_args["tenant_id"] == "tenant-a"
        assert call_args["content"] == b"fake PDF content"
        assert call_args["source_uri"] is None

        # Verify record was created in state
        record = state_backend.get("tenant-a", response.document_id)
        assert record is not None
        assert record.status == common_pb2.JOB_STATUS_PENDING

    @pytest.mark.asyncio
    async def test_convert_with_valid_url_returns_document_id_and_pending(self, servicer, mock_dispatch, state_backend):
        """ConvertDocument with valid URL returns document_id and PENDING status."""
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-b"),
            source_uri="https://example.com/document.pdf",
            metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
        )
        context = MockServicerContext()

        response = await servicer.ConvertDocument(request, context)

        assert response.document_id
        assert response.status == common_pb2.JOB_STATUS_PENDING

        # Verify dispatch was called
        mock_dispatch.dispatch.assert_called_once()
        call_args = mock_dispatch.dispatch.call_args[1]
        assert call_args["tenant_id"] == "tenant-b"
        assert call_args["content"] is None
        assert call_args["source_uri"] == "https://example.com/document.pdf"

    @pytest.mark.asyncio
    async def test_convert_missing_tenant_id_returns_invalid_argument(self, servicer):
        """ConvertDocument missing tenant_id calls abort(INVALID_ARGUMENT)."""
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=""),
            content=b"fake PDF content",
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.aborted
        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT
        assert "tenant_id" in context.abort_details.lower()

    @pytest.mark.asyncio
    async def test_convert_missing_content_and_source_uri_returns_invalid_argument(self, servicer):
        """ConvertDocument missing both content and source_uri calls abort(INVALID_ARGUMENT)."""
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.aborted
        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT
        assert "content" in context.abort_details.lower() or "source" in context.abort_details.lower()

    @pytest.mark.asyncio
    async def test_convert_oversized_content_returns_resource_exhausted(self, servicer):
        """ConvertDocument with oversized content calls abort(RESOURCE_EXHAUSTED)."""
        # Create content larger than max_document_size (1MB)
        large_content = b"x" * (1024 * 1024 + 1)

        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            content=large_content,
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.aborted
        assert context.abort_code == grpc.StatusCode.RESOURCE_EXHAUSTED
        assert "size" in context.abort_details.lower() or "large" in context.abort_details.lower()


class TestGetDocument:
    """Tests for GetDocument RPC."""

    @pytest.mark.asyncio
    async def test_get_document_returns_completed_document(self, servicer, state_backend):
        """GetDocument returns completed document with structured_json_uri."""
        now = datetime.now(UTC).isoformat()
        record = DocumentRecord(
            document_id="doc-123",
            tenant_id="tenant-a",
            status=common_pb2.JOB_STATUS_COMPLETED,
            metadata={"mime_type": "application/pdf"},
            audit={"created_by": "user-1"},
            structured_json_uri="s3://bucket/doc-123.json",
            error=None,
            created_at=now,
            updated_at=now,
        )
        state_backend.create(record)

        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            document_id="doc-123",
        )
        context = MockServicerContext()

        response = await servicer.GetDocument(request, context)

        assert response.document.document_id == "doc-123"
        assert response.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED
        assert response.document.structured_json_uri == "s3://bucket/doc-123.json"
        assert response.document.tenant_context.tenant_id == "tenant-a"

    @pytest.mark.asyncio
    async def test_get_document_missing_returns_not_found(self, servicer):
        """GetDocument for missing document calls abort(NOT_FOUND)."""
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            document_id="nonexistent",
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.GetDocument(request, context)

        assert context.aborted
        assert context.abort_code == grpc.StatusCode.NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_document_tenant_isolation(self, servicer, state_backend):
        """GetDocument tenant isolation: different tenant gets NOT_FOUND."""
        now = datetime.now(UTC).isoformat()
        record = DocumentRecord(
            document_id="doc-123",
            tenant_id="tenant-a",
            status=common_pb2.JOB_STATUS_COMPLETED,
            metadata={},
            audit={},
            structured_json_uri="s3://bucket/doc-123.json",
            error=None,
            created_at=now,
            updated_at=now,
        )
        state_backend.create(record)

        # Try to access with different tenant
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-b"),
            document_id="doc-123",
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.GetDocument(request, context)

        assert context.aborted
        assert context.abort_code == grpc.StatusCode.NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_document_missing_tenant_id_returns_invalid_argument(self, servicer):
        """GetDocument missing tenant_id calls abort(INVALID_ARGUMENT)."""
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=""),
            document_id="doc-123",
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.GetDocument(request, context)

        assert context.aborted
        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT


class TestListDocuments:
    """Tests for ListDocuments RPC."""

    @pytest.mark.asyncio
    async def test_list_documents_returns_only_tenant_docs(self, servicer, state_backend):
        """ListDocuments returns only requesting tenant's documents."""
        now = datetime.now(UTC).isoformat()

        # Create docs for tenant-a
        for i in range(3):
            state_backend.create(
                DocumentRecord(
                    document_id=f"doc-a-{i}",
                    tenant_id="tenant-a",
                    status=common_pb2.JOB_STATUS_PENDING,
                    metadata={},
                    audit={},
                    created_at=now,
                    updated_at=now,
                )
            )

        # Create docs for tenant-b
        for i in range(2):
            state_backend.create(
                DocumentRecord(
                    document_id=f"doc-b-{i}",
                    tenant_id="tenant-b",
                    status=common_pb2.JOB_STATUS_PENDING,
                    metadata={},
                    audit={},
                    created_at=now,
                    updated_at=now,
                )
            )

        request = ingestion_pb2.ListDocumentsRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
        )
        context = MockServicerContext()

        response = await servicer.ListDocuments(request, context)

        assert len(response.documents) == 3
        for doc in response.documents:
            assert doc.tenant_context.tenant_id == "tenant-a"
            assert doc.document_id.startswith("doc-a-")

    @pytest.mark.asyncio
    async def test_list_documents_pagination_works(self, servicer, state_backend):
        """ListDocuments pagination works correctly."""
        now = datetime.now(UTC).isoformat()

        # Create 5 documents
        for i in range(5):
            state_backend.create(
                DocumentRecord(
                    document_id=f"doc-{i:02d}",
                    tenant_id="tenant-a",
                    status=common_pb2.JOB_STATUS_PENDING,
                    metadata={},
                    audit={},
                    created_at=now,
                    updated_at=now,
                )
            )

        # Request first page with page_size=2
        request = ingestion_pb2.ListDocumentsRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            options=common_pb2.ListOptions(pagination=common_pb2.Pagination(page_size=2)),
        )
        context = MockServicerContext()

        response = await servicer.ListDocuments(request, context)

        assert len(response.documents) == 2
        assert response.page_info.next_page_token != ""

        # Request second page
        request2 = ingestion_pb2.ListDocumentsRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            options=common_pb2.ListOptions(
                pagination=common_pb2.Pagination(page_size=2, page_token=response.page_info.next_page_token)
            ),
        )
        context2 = MockServicerContext()

        response2 = await servicer.ListDocuments(request2, context2)

        assert len(response2.documents) == 2
        assert response2.page_info.next_page_token != ""

        # Verify no duplicate documents
        first_page_ids = {doc.document_id for doc in response.documents}
        second_page_ids = {doc.document_id for doc in response2.documents}
        assert len(first_page_ids & second_page_ids) == 0

    @pytest.mark.asyncio
    async def test_list_documents_missing_tenant_id_returns_invalid_argument(self, servicer):
        """ListDocuments missing tenant_id calls abort(INVALID_ARGUMENT)."""
        request = ingestion_pb2.ListDocumentsRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=""),
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ListDocuments(request, context)

        assert context.aborted
        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT
