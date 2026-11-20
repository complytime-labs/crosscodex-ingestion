"""Unit tests for gRPC service layer."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from prometheus_client import REGISTRY as PROMETHEUS_REGISTRY

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.listing import parse_list_options
from crosscodex_ingestion.mapping import MappingRegistry, load_registry
from crosscodex_ingestion.nats_connection import NatsConnection, NatsUnavailableError
from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2
from crosscodex_ingestion.service import IngestionServiceServicer
from crosscodex_ingestion.state import DocumentRecord, MemoryStateBackend

DOC_ID = "6f1c1c1e-3b2a-4c55-9d0e-0a1b2c3d4e5f"
MISSING_ID = "00000000-0000-4000-8000-000000000000"
REGISTRY = load_registry("")

_ALL = parse_list_options(common_pb2.ListOptions())


class MockServicerContext:
    """Mock grpc.aio servicer context: abort() is a coroutine that raises only when awaited."""

    def __init__(self, auth: dict[str, list[bytes]] | None = None):
        self.aborted = False
        self.abort_code = None
        self.abort_details = None
        self._auth = auth or {}

    def auth_context(self) -> dict[str, list[bytes]]:
        """Return the gRPC auth context; empty, as on a plaintext port, unless a test supplies one."""
        return self._auth

    async def abort(self, code: grpc.StatusCode, details: str):
        """Capture abort call and stop processing, as grpc.aio does."""
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
        mappings=REGISTRY,
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
        record = await state_backend.get("tenant-a", response.document_id)
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


class TestCreatedBy:
    """created_by records the authenticated caller."""

    @staticmethod
    def _request():
        return ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            content=b"fake PDF content",
            metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
        )

    @pytest.mark.asyncio
    async def test_created_by_is_the_client_certificate_identity(self, servicer, state_backend):
        context = MockServicerContext({"transport_security_type": [b"ssl"], "x509_common_name": [b"gateway"]})

        response = await servicer.ConvertDocument(self._request(), context)

        record = await state_backend.get("tenant-a", response.document_id)
        assert record.audit["created_by"] == "gateway"

    @pytest.mark.asyncio
    async def test_created_by_uses_san_when_certificate_has_no_common_name(self, servicer, state_backend):
        auth = {"transport_security_type": [b"ssl"], "x509_subject_alternative_name": [b"gateway.internal"]}

        response = await servicer.ConvertDocument(self._request(), MockServicerContext(auth))

        record = await state_backend.get("tenant-a", response.document_id)
        assert record.audit["created_by"] == "gateway.internal"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("auth", "expected"),
        [
            ({"x509_common_name": [b"foo"], "x509_subject_alternative_name": [b"gateway"]}, "gateway"),
            (
                {"x509_common_name": [b"foo"], "x509_subject_alternative_name": [b"other", b"gateway"]},
                "gateway",
            ),
        ],
    )
    async def test_created_by_is_the_allowlisted_identity(self, state_backend, mock_dispatch, auth, expected):
        servicer = IngestionServiceServicer(
            settings=IngestionSettings(grpc_allowed_clients="gateway"),
            state_backend=state_backend,
            dispatch_backend=mock_dispatch,
            mappings=REGISTRY,
        )
        context = MockServicerContext({"transport_security_type": [b"ssl"], **auth})

        response = await servicer.ConvertDocument(self._request(), context)

        record = await state_backend.get("tenant-a", response.document_id)
        assert record.audit["created_by"] == expected

    @pytest.mark.asyncio
    async def test_created_by_is_unauthenticated_without_tls(self, servicer, state_backend):
        response = await servicer.ConvertDocument(self._request(), MockServicerContext())

        record = await state_backend.get("tenant-a", response.document_id)
        assert record.audit["created_by"] == "unauthenticated"


class TestConvertWithMapping:
    """request.mapping names a registered mapping; it is checked before anything is stored or dispatched."""

    @pytest.mark.asyncio
    async def test_registered_mapping_is_recorded_with_its_hash_and_dispatched(
        self, servicer, mock_dispatch, state_backend
    ):
        asvs = REGISTRY.get("owasp-asvs-5.0.0-json")
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            content=b'{"Requirements": []}',
            mapping=asvs.id,
        )

        response = await servicer.ConvertDocument(request, MockServicerContext())

        record = await state_backend.get("tenant-a", response.document_id)
        assert record.metadata["mapping_id"] == asvs.id
        assert record.metadata["mapping_sha256"] == asvs.sha256
        dispatched = mock_dispatch.dispatch.call_args.kwargs["metadata"]
        assert (dispatched["mapping_id"], dispatched["mapping_sha256"]) == (asvs.id, asvs.sha256)

    @pytest.mark.asyncio
    async def test_without_mapping_nothing_is_recorded(self, servicer, mock_dispatch, state_backend):
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"), content=b"%PDF-1.4"
        )

        response = await servicer.ConvertDocument(request, MockServicerContext())

        record = await state_backend.get("tenant-a", response.document_id)
        assert "mapping_id" not in record.metadata

    @pytest.mark.asyncio
    async def test_unknown_mapping_is_rejected_listing_the_registered_ones(
        self, servicer, mock_dispatch, state_backend
    ):
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"), content=b"{}", mapping="owasp-asvs-4.0"
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT
        assert context.abort_details == (
            "Unknown mapping 'owasp-asvs-4.0'. Registered mappings: mitre-attack-enterprise-17.1-techniques-xlsx, "
            "nist-csf-2.0-core-xlsx, nist-sp800-53r5-catalog-xlsx, owasp-asvs-5.0.0-csv, owasp-asvs-5.0.0-json."
        )
        mock_dispatch.dispatch.assert_not_called()
        assert (await state_backend.list_documents("tenant-a", _ALL))[0] == []

    @pytest.mark.asyncio
    async def test_unknown_mapping_is_echoed_truncated(self, servicer):
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"), content=b"{}", mapping="x" * 500
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert "x" * 65 not in context.abort_details

    @pytest.mark.asyncio
    async def test_unknown_mapping_on_a_server_without_mappings(self, settings, state_backend, mock_dispatch):
        servicer = IngestionServiceServicer(
            settings=settings, state_backend=state_backend, dispatch_backend=mock_dispatch, mappings=MappingRegistry({})
        )
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"), content=b"{}", mapping="any"
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.abort_details == "Unknown mapping 'any'. No mappings are registered on this server."


class TestGetDocument:
    """Tests for GetDocument RPC."""

    @pytest.mark.asyncio
    async def test_get_document_returns_completed_document(self, servicer, state_backend):
        """GetDocument returns completed document with structured_json_uri."""
        now = datetime.now(UTC).isoformat()
        record = DocumentRecord(
            document_id=DOC_ID,
            tenant_id="tenant-a",
            status=common_pb2.JOB_STATUS_COMPLETED,
            metadata={"mime_type": "application/pdf"},
            audit={"created_by": "user-1"},
            structured_json_uri=f"s3://bucket/{DOC_ID}.json",
            error=None,
            created_at=now,
            updated_at=now,
        )
        await state_backend.create(record)

        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            document_id=DOC_ID,
        )
        context = MockServicerContext()

        response = await servicer.GetDocument(request, context)

        assert response.document.document_id == DOC_ID
        assert response.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED
        assert response.document.structured_json_uri == f"s3://bucket/{DOC_ID}.json"
        assert response.document.tenant_context.tenant_id == "tenant-a"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("uri", ["s3://bucket/t/d/attestation.link", ""])
    async def test_get_document_returns_attestation_uri(self, servicer, state_backend, uri):
        """attestation_uri is the stored link location, or empty when the job was not attested."""
        now = datetime.now(UTC).isoformat()
        await state_backend.create(
            DocumentRecord(
                document_id=DOC_ID,
                tenant_id="tenant-a",
                status=common_pb2.JOB_STATUS_COMPLETED,
                attestation_uri=uri,
                created_at=now,
                updated_at=now,
            )
        )
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"), document_id=DOC_ID
        )

        response = await servicer.GetDocument(request, MockServicerContext())

        assert response.document.attestation_uri == uri

    @pytest.mark.asyncio
    async def test_get_document_returns_failure_code_message_and_details(self, servicer, state_backend):
        """A schema violation's element index and id reach the client as Error.details."""
        error = {
            "code": common_pb2.ERROR_CODE_INTERNAL,
            "message": "Structured output does not conform to schema 1.0: elements[0] ...",
            "details": {"location": "elements[0] (element_id 'e-001').content", "element_id": "e-001"},
        }
        await state_backend.create(
            DocumentRecord(
                document_id=DOC_ID,
                tenant_id="tenant-a",
                status=common_pb2.JOB_STATUS_FAILED,
                metadata={},
                error=error,
            )
        )
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            document_id=DOC_ID,
        )

        response = await servicer.GetDocument(request, MockServicerContext())

        assert response.document.error.code == common_pb2.ERROR_CODE_INTERNAL
        assert response.document.error.message == error["message"]
        assert dict(response.document.error.details) == error["details"]

    @pytest.mark.asyncio
    async def test_get_document_missing_returns_not_found(self, servicer):
        """GetDocument for missing document calls abort(NOT_FOUND)."""
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            document_id=MISSING_ID,
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
            document_id=DOC_ID,
            tenant_id="tenant-a",
            status=common_pb2.JOB_STATUS_COMPLETED,
            metadata={},
            audit={},
            structured_json_uri=f"s3://bucket/{DOC_ID}.json",
            error=None,
            created_at=now,
            updated_at=now,
        )
        await state_backend.create(record)

        # Try to access with different tenant
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-b"),
            document_id=DOC_ID,
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
            document_id=DOC_ID,
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
            await state_backend.create(
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
            await state_backend.create(
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
            await state_backend.create(
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

    @pytest.mark.asyncio
    async def test_list_documents_filters_and_sorts(self, servicer, state_backend):
        for i, status in enumerate(
            [common_pb2.JOB_STATUS_FAILED, common_pb2.JOB_STATUS_COMPLETED, common_pb2.JOB_STATUS_FAILED]
        ):
            await state_backend.create(
                DocumentRecord(
                    document_id=f"doc-{i}",
                    tenant_id="tenant-a",
                    status=status,
                    created_at=datetime(2026, 1, 1, i, tzinfo=UTC).isoformat(),
                )
            )
        request = ingestion_pb2.ListDocumentsRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            options=common_pb2.ListOptions(
                filters=[common_pb2.FieldFilter(field="status", operator="eq", value="JOB_STATUS_FAILED")],
                sort=[common_pb2.SortOrder(field="created_at", descending=True)],
            ),
        )

        response = await servicer.ListDocuments(request, MockServicerContext())

        assert [d.document_id for d in response.documents] == ["doc-2", "doc-0"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("options", "message"),
        [
            (
                common_pb2.ListOptions(pagination=common_pb2.Pagination(page_token="garbage")),
                "page_token is invalid or belongs to a different query",
            ),
            (
                common_pb2.ListOptions(filters=[common_pb2.FieldFilter(field="framework", operator="eq", value="x")]),
                "unsupported filter field 'framework'. Supported filters: status",
            ),
            (
                common_pb2.ListOptions(pagination=common_pb2.Pagination(page_size=1001)),
                "page_size must be between 0 and 1000 (0 means 50)",
            ),
        ],
    )
    async def test_list_documents_bad_options_return_invalid_argument(self, servicer, options, message):
        request = ingestion_pb2.ListDocumentsRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"), options=options
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ListDocuments(request, context)

        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT
        assert context.abort_details.startswith(message)


class TestAbortStopsProcessing:
    """A failed validation must stop the RPC before any state or dispatch side effect."""

    @pytest.mark.asyncio
    async def test_convert_empty_tenant_creates_no_state_and_dispatches_nothing(
        self, servicer, mock_dispatch, state_backend
    ):
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=""),
            content=b"fake PDF content",
        )

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, MockServicerContext())

        mock_dispatch.dispatch.assert_not_called()
        records, _ = await state_backend.list_documents("", _ALL)
        assert records == []

    @pytest.mark.asyncio
    async def test_convert_without_source_dispatches_nothing(self, servicer, mock_dispatch):
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
        )

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, MockServicerContext())

        mock_dispatch.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_empty_tenant_does_not_query_state(self, servicer):
        servicer._state = MagicMock()
        request = ingestion_pb2.ListDocumentsRequest(tenant_context=common_pb2.TenantContext(tenant_id=""))

        with pytest.raises(grpc.RpcError):
            await servicer.ListDocuments(request, MockServicerContext())

        servicer._state.list_documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_missing_document_aborts_not_found_not_runtime_error(self, servicer):
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            document_id=MISSING_ID,
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.GetDocument(request, context)

        assert context.abort_code == grpc.StatusCode.NOT_FOUND


def _nats(available: bool) -> MagicMock:
    nats = MagicMock(spec=NatsConnection)
    nats.available = available
    nats.url = "nats://nats.internal:4222"
    return nats


class TestNatsAvailability:
    """While NATS is down, audit events cannot be recorded, so submissions are refused up front."""

    @pytest.mark.asyncio
    async def test_convert_while_nats_is_unavailable_aborts_unavailable_and_writes_nothing(
        self, settings, state_backend, mock_dispatch, caplog
    ):
        servicer = IngestionServiceServicer(
            settings=settings,
            state_backend=state_backend,
            dispatch_backend=mock_dispatch,
            mappings=REGISTRY,
            nats=_nats(available=False),
        )
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            content=b"fake PDF content",
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.abort_code == grpc.StatusCode.UNAVAILABLE
        # CWE-209: the broker's address is logged, never sent to the client
        assert context.abort_details == "NATS is unavailable; audit events cannot be recorded. Retry later."
        assert "nats.internal:4222" in caplog.text
        mock_dispatch.dispatch.assert_not_called()
        records, _ = await state_backend.list_documents("tenant-a", _ALL)
        assert records == []

    @pytest.mark.asyncio
    async def test_nats_dropping_before_the_dispatch_publish_fails_the_record_and_aborts_unavailable(
        self, settings, state_backend, mock_dispatch, caplog
    ):
        """The availability check passed, then the job publish found NATS gone."""
        mock_dispatch.dispatch.side_effect = NatsUnavailableError(
            "NATS at nats://nats.internal:4222 is unavailable; job for document x was not published"
        )
        servicer = IngestionServiceServicer(
            settings=settings,
            state_backend=state_backend,
            dispatch_backend=mock_dispatch,
            mappings=REGISTRY,
            nats=_nats(available=True),
        )
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            content=b"fake PDF content",
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.abort_code == grpc.StatusCode.UNAVAILABLE
        assert context.abort_details == "NATS is unavailable; audit events cannot be recorded. Retry later."
        assert "nats.internal:4222" in caplog.text
        # No PENDING record is left behind that no worker will ever pick up
        records, _ = await state_backend.list_documents("tenant-a", _ALL)
        assert len(records) == 1
        assert records[0].status == common_pb2.JOB_STATUS_FAILED
        assert records[0].error == {
            "code": common_pb2.ERROR_CODE_UNAVAILABLE,
            "message": "NATS is unavailable; audit events cannot be recorded. Retry later.",
        }

    @pytest.mark.asyncio
    async def test_convert_validates_the_tenant_before_checking_nats(self, settings, state_backend, mock_dispatch):
        servicer = IngestionServiceServicer(
            settings=settings,
            state_backend=state_backend,
            dispatch_backend=mock_dispatch,
            mappings=REGISTRY,
            nats=_nats(available=False),
        )
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="../../x"),
            content=b"fake PDF content",
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT

    @pytest.mark.asyncio
    async def test_convert_while_nats_is_available_is_accepted(self, settings, state_backend, mock_dispatch):
        servicer = IngestionServiceServicer(
            settings=settings,
            state_backend=state_backend,
            dispatch_backend=mock_dispatch,
            mappings=REGISTRY,
            nats=_nats(available=True),
        )
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            content=b"fake PDF content",
        )

        response = await servicer.ConvertDocument(request, MockServicerContext())

        assert response.status == common_pb2.JOB_STATUS_PENDING
        mock_dispatch.dispatch.assert_called_once()
        assert await state_backend.get("tenant-a", response.document_id) is not None

    @pytest.mark.asyncio
    async def test_convert_without_nats_configured_is_accepted(self, servicer, mock_dispatch):
        """nats=None (NATS_URL unset): audit events go to the log, so there is nothing to wait for."""
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            content=b"fake PDF content",
        )

        response = await servicer.ConvertDocument(request, MockServicerContext())

        assert response.status == common_pb2.JOB_STATUS_PENDING
        mock_dispatch.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_reads_are_served_while_nats_is_unavailable(self, settings, state_backend, mock_dispatch):
        servicer = IngestionServiceServicer(
            settings=settings,
            state_backend=state_backend,
            dispatch_backend=mock_dispatch,
            mappings=REGISTRY,
            nats=_nats(available=False),
        )
        request = ingestion_pb2.ListDocumentsRequest(tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"))

        response = await servicer.ListDocuments(request, MockServicerContext())

        assert list(response.documents) == []


BAD_TENANTS = [
    "../../x",
    "a.>",
    "tenant.*",
    "Acme",
    "ab",
    "a" * 53,
    "-acme",
    "acme-",
    "1acme",
    "ac me",
    "acme\n",
    "acm_e",
]


class TestTenantIdValidation:
    """tenant_id feeds filesystem paths and NATS subjects, so it is validated at the boundary."""

    @pytest.mark.parametrize("tenant", BAD_TENANTS)
    @pytest.mark.asyncio
    async def test_convert_rejects_invalid_tenant(self, servicer, mock_dispatch, state_backend, tenant):
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=tenant),
            content=b"fake PDF content",
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ConvertDocument(request, context)

        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT
        assert "^[a-z][a-z0-9-]{1,50}[a-z0-9]$" in context.abort_details
        mock_dispatch.dispatch.assert_not_called()

    @pytest.mark.parametrize("tenant", BAD_TENANTS)
    @pytest.mark.asyncio
    async def test_get_rejects_invalid_tenant(self, servicer, tenant):
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=tenant),
            document_id=DOC_ID,
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.GetDocument(request, context)

        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT

    @pytest.mark.parametrize("tenant", BAD_TENANTS)
    @pytest.mark.asyncio
    async def test_list_rejects_invalid_tenant(self, servicer, tenant):
        request = ingestion_pb2.ListDocumentsRequest(tenant_context=common_pb2.TenantContext(tenant_id=tenant))
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.ListDocuments(request, context)

        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT

    @pytest.mark.parametrize("tenant", ["acme", "tenant-a", "a1b", "a" + "b" * 50 + "c"])
    @pytest.mark.asyncio
    async def test_convert_accepts_valid_tenant(self, servicer, mock_dispatch, tenant):
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=tenant),
            content=b"fake PDF content",
        )

        await servicer.ConvertDocument(request, MockServicerContext())

        mock_dispatch.dispatch.assert_called_once()

    @pytest.mark.parametrize(
        "document_id", ["doc-123", "../../etc/passwd", "", "6F1C1C1E-3B2A-4C55-9D0E-0A1B2C3D4E5F "]
    )
    @pytest.mark.asyncio
    async def test_get_rejects_non_uuid_document_id(self, servicer, document_id):
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
            document_id=document_id,
        )
        context = MockServicerContext()

        with pytest.raises(grpc.RpcError):
            await servicer.GetDocument(request, context)

        assert context.abort_code == grpc.StatusCode.INVALID_ARGUMENT
        assert "document_id" in context.abort_details


def _requests_total(format_label: str) -> float:
    return (
        PROMETHEUS_REGISTRY.get_sample_value("ingestion_requests_total", {"format": format_label, "status": "pending"})
        or 0.0
    )


class TestRequestMetricLabels:
    """ingestion_requests_total{format} takes a bounded value, never text the caller chose."""

    async def _convert(self, servicer, **fields) -> None:
        request = ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"), content=b"x", **fields
        )
        await servicer.ConvertDocument(request, MockServicerContext())

    @pytest.mark.asyncio
    async def test_free_text_format_version_never_becomes_a_label(self, servicer):
        before = _requests_total("unknown")

        await self._convert(servicer, metadata=common_pb2.ContentMetadata(format_version="zz-1795120790608977744"))

        assert _requests_total("zz-1795120790608977744") == 0.0
        assert _requests_total("unknown") == before + 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mime_type", "label"),
        [
            ("application/pdf", "pdf"),
            ("Text/HTML; charset=utf-8", "html"),
            ("application/x-attacker-1234", "unknown"),
            ("", "unknown"),
        ],
    )
    async def test_declared_mime_type_maps_to_a_known_format_or_unknown(self, servicer, mime_type, label):
        before = _requests_total(label)

        await self._convert(servicer, metadata=common_pb2.ContentMetadata(mime_type=mime_type))

        assert _requests_total(label) == before + 1

    @pytest.mark.asyncio
    async def test_a_named_mapping_is_labelled_mapping(self, servicer):
        before = _requests_total("mapping")

        await self._convert(
            servicer,
            mapping="owasp-asvs-5.0.0-json",
            metadata=common_pb2.ContentMetadata(mime_type="application/json"),
        )

        assert _requests_total("mapping") == before + 1


def test_histogram_buckets_fit_document_sizes_and_conversion_times():
    from crosscodex_ingestion.metrics import DOC_SIZE, DURATION

    assert DOC_SIZE._upper_bounds == [1e3, 1e4, 1e5, 1e6, 1e7, 5e7, 1e8, float("inf")]
    assert DURATION._upper_bounds == [1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, float("inf")]
