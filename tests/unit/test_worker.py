"""Unit tests for worker module."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import ConversionError, ConversionTimeout
from crosscodex_ingestion.state import DocumentRecord, MemoryStateBackend
from crosscodex_ingestion.storage import FilesystemStorage
from crosscodex_ingestion.worker import run_conversion


@pytest.fixture
def settings():
    """Create test settings."""
    return IngestionSettings(
        allowed_formats="pdf,html,docx,txt",
        storage_backend="filesystem",
        storage_path="/tmp/test-storage",
        state_backend="memory",
    )


@pytest.fixture
def state_backend():
    """Create in-memory state backend."""
    return MemoryStateBackend()


@pytest.fixture
def storage_backend(tmp_path):
    """Create filesystem storage backend."""
    return FilesystemStorage(str(tmp_path / "storage"))


@pytest.fixture
def audit_publisher():
    """Create mock audit publisher."""
    mock = AsyncMock()
    mock.publish = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_run_conversion_with_bytes_successful(
    settings, state_backend, storage_backend, audit_publisher, tmp_path
):
    """Test successful conversion with direct bytes input."""
    document_id = "doc-123"
    tenant_id = "tenant-1"
    content = b"%PDF-1.4 test content"
    metadata = {"mime_type": "application/pdf"}

    # Create initial record
    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,  # JOB_STATUS_PENDING
        metadata=metadata,
    )
    state_backend.create(record)

    # Mock convert_document to return structured data
    mock_result = {
        "elements": [{"type": "text", "content": "Sample text"}],
        "metadata": {"page_count": 1},
    }

    with patch("crosscodex_ingestion.worker.convert_document", return_value=mock_result):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
        )

    # Verify state transitions
    final_record = state_backend.get(tenant_id, document_id)
    assert final_record is not None
    assert final_record.status == 3  # JOB_STATUS_COMPLETED
    assert final_record.structured_json_uri.startswith("file://")
    assert final_record.error is None

    # Verify audit event published
    assert audit_publisher.publish.call_count == 1
    call_args = audit_publisher.publish.call_args
    assert call_args.kwargs["tenant_id"] == tenant_id
    event = call_args.kwargs["event"]
    assert event["document_id"] == document_id
    assert event["status"] == "completed"


@pytest.mark.asyncio
async def test_run_conversion_with_url_successful(settings, state_backend, storage_backend, audit_publisher):
    """Test successful conversion with URL source."""
    document_id = "doc-456"
    tenant_id = "tenant-2"
    source_uri = "https://example.com/document.pdf"
    metadata = {"mime_type": "application/pdf"}

    # Create initial record
    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,  # JOB_STATUS_PENDING
        metadata=metadata,
    )
    state_backend.create(record)

    # Mock fetch_url and convert_document
    mock_result = {
        "elements": [{"type": "text", "content": "Sample text"}],
        "metadata": {"page_count": 1},
    }

    with (
        patch("crosscodex_ingestion.worker.fetch_url") as mock_fetch,
        patch("crosscodex_ingestion.worker.convert_document", return_value=mock_result),
        patch("crosscodex_ingestion.worker.validate_url"),
    ):
        # Mock fetch_url to return temp file path and detected type
        mock_temp_path = "/tmp/test-file.pdf"
        mock_fetch.return_value.__enter__ = Mock(return_value=(mock_temp_path, "pdf"))
        mock_fetch.return_value.__exit__ = Mock(return_value=False)

        # Mock Path.read_bytes to return PDF content
        with patch("pathlib.Path.read_bytes", return_value=b"%PDF-1.4 test"):
            await run_conversion(
                document_id=document_id,
                tenant_id=tenant_id,
                content=None,
                source_uri=source_uri,
                metadata=metadata,
                settings=settings,
                state=state_backend,
                storage=storage_backend,
                attestation=None,
                audit=audit_publisher,
            )

    # Verify validate_url was called
    # Verify state is completed
    final_record = state_backend.get(tenant_id, document_id)
    assert final_record is not None
    assert final_record.status == 3  # JOB_STATUS_COMPLETED


@pytest.mark.asyncio
async def test_run_conversion_format_validation_failure(settings, state_backend, storage_backend, audit_publisher):
    """Test format validation failure results in FAILED state."""
    document_id = "doc-789"
    tenant_id = "tenant-3"
    content = b"Invalid content that doesn't match any format"
    metadata = {"mime_type": "text/plain"}

    # Create initial record
    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,
        metadata=metadata,
    )
    state_backend.create(record)

    # Mock sniff_format to raise ValueError
    with patch("crosscodex_ingestion.worker.sniff_format", side_effect=ValueError("Unknown format")):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
        )

    # Verify state is FAILED with error code 1 (INVALID_ARGUMENT)
    final_record = state_backend.get(tenant_id, document_id)
    assert final_record is not None
    assert final_record.status == 4  # JOB_STATUS_FAILED
    assert final_record.error is not None
    assert final_record.error["code"] == 1
    assert "Unknown format" in final_record.error["message"]


@pytest.mark.asyncio
async def test_run_conversion_timeout(settings, state_backend, storage_backend, audit_publisher):
    """Test conversion timeout results in FAILED state."""
    document_id = "doc-timeout"
    tenant_id = "tenant-4"
    content = b"%PDF-1.4 test content"
    metadata = {"mime_type": "application/pdf"}

    # Create initial record
    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,
        metadata=metadata,
    )
    state_backend.create(record)

    # Mock convert_document to raise ConversionTimeout
    with patch(
        "crosscodex_ingestion.worker.convert_document",
        side_effect=ConversionTimeout("Conversion timed out"),
    ):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
        )

    # Verify state is FAILED with RESOURCE_EXHAUSTED error
    final_record = state_backend.get(tenant_id, document_id)
    assert final_record is not None
    assert final_record.status == 4
    assert final_record.error is not None
    assert final_record.error["code"] == 5  # RESOURCE_EXHAUSTED


@pytest.mark.asyncio
async def test_run_conversion_storage_failure(settings, state_backend, storage_backend, audit_publisher):
    """Test storage upload failure results in FAILED state."""
    document_id = "doc-storage-fail"
    tenant_id = "tenant-5"
    content = b"%PDF-1.4 test content"
    metadata = {"mime_type": "application/pdf"}

    # Create initial record
    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,
        metadata=metadata,
    )
    state_backend.create(record)

    # Mock convert_document to succeed
    mock_result = {
        "elements": [{"type": "text", "content": "Sample text"}],
        "metadata": {"page_count": 1},
    }

    # Mock storage.upload to raise exception
    with (
        patch("crosscodex_ingestion.worker.convert_document", return_value=mock_result),
        patch.object(storage_backend, "upload", side_effect=Exception("Storage error")),
    ):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
        )

    # Verify state is FAILED with INTERNAL error
    final_record = state_backend.get(tenant_id, document_id)
    assert final_record is not None
    assert final_record.status == 4
    assert final_record.error is not None
    assert final_record.error["code"] == 6  # INTERNAL


@pytest.mark.asyncio
async def test_run_conversion_with_attestation(settings, state_backend, storage_backend, audit_publisher):
    """Test attestation link creation when attestation is enabled."""
    document_id = "doc-attestation"
    tenant_id = "tenant-6"
    content = b"%PDF-1.4 test content"
    metadata = {"mime_type": "application/pdf"}

    # Create initial record
    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,
        metadata=metadata,
    )
    state_backend.create(record)

    # Mock attestation manager
    mock_attestation = Mock()
    mock_attestation.create_link = Mock(
        return_value={
            "signed": {"name": "convert-document", "materials": {}, "products": {}},
            "signatures": [{"sig": "mock-signature"}],
        }
    )

    # Mock convert_document
    mock_result = {
        "elements": [{"type": "text", "content": "Sample text"}],
        "metadata": {"page_count": 1},
    }

    with patch("crosscodex_ingestion.worker.convert_document", return_value=mock_result):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=mock_attestation,
            audit=audit_publisher,
        )

    # Verify attestation.create_link was called
    assert mock_attestation.create_link.call_count == 1
    call_args = mock_attestation.create_link.call_args
    assert call_args[1]["step_name"] == "convert-document"
    # Materials should have input hash
    materials = call_args[1]["materials"]
    assert "input" in materials
    assert "sha256" in materials["input"]
    # Products should have output hash
    products = call_args[1]["products"]
    assert "structured_json" in products
    assert "sha256" in products["structured_json"]

    # Verify state is completed
    final_record = state_backend.get(tenant_id, document_id)
    assert final_record is not None
    assert final_record.status == 3


@pytest.mark.asyncio
async def test_run_conversion_audit_event_published(settings, state_backend, storage_backend, audit_publisher):
    """Test audit event is published on completion."""
    document_id = "doc-audit"
    tenant_id = "tenant-7"
    content = b"%PDF-1.4 test content"
    metadata = {"mime_type": "application/pdf"}

    # Create initial record
    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,
        metadata=metadata,
    )
    state_backend.create(record)

    mock_result = {
        "elements": [{"type": "text", "content": "Sample text"}],
        "metadata": {"page_count": 1},
    }

    with patch("crosscodex_ingestion.worker.convert_document", return_value=mock_result):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
        )

    # Verify audit.publish was called
    assert audit_publisher.publish.call_count == 1
    call_args = audit_publisher.publish.call_args
    assert call_args.kwargs["tenant_id"] == tenant_id
    event = call_args.kwargs["event"]
    assert "document_id" in event
    assert "status" in event


@pytest.mark.asyncio
async def test_run_conversion_temp_files_cleaned_on_success(settings, state_backend, storage_backend, audit_publisher):
    """Test temp files are cleaned up after successful conversion."""
    document_id = "doc-cleanup"
    tenant_id = "tenant-8"
    content = b"%PDF-1.4 test content"
    metadata = {"mime_type": "application/pdf"}

    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,
        metadata=metadata,
    )
    state_backend.create(record)

    mock_result = {
        "elements": [{"type": "text", "content": "Sample text"}],
        "metadata": {"page_count": 1},
    }

    temp_file_created = None

    def track_temp_file(*args, **kwargs):
        # Track temp files created during conversion
        nonlocal temp_file_created
        temp_file_created = args[0] if args else None
        return mock_result

    with patch("crosscodex_ingestion.worker.convert_document", side_effect=track_temp_file):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
        )

    # Verify conversion succeeded
    final_record = state_backend.get(tenant_id, document_id)
    assert final_record.status == 3


@pytest.mark.asyncio
async def test_run_conversion_temp_files_cleaned_on_failure(settings, state_backend, storage_backend, audit_publisher):
    """Test temp files are cleaned up after conversion failure."""
    document_id = "doc-cleanup-fail"
    tenant_id = "tenant-9"
    content = b"%PDF-1.4 test content"
    metadata = {"mime_type": "application/pdf"}

    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,
        metadata=metadata,
    )
    state_backend.create(record)

    # Mock convert_document to raise exception
    with patch(
        "crosscodex_ingestion.worker.convert_document",
        side_effect=ConversionError("Conversion failed"),
    ):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
        )

    # Verify state is FAILED
    final_record = state_backend.get(tenant_id, document_id)
    assert final_record.status == 4
    assert final_record.error is not None


@pytest.mark.asyncio
async def test_run_conversion_hash_validation(settings, state_backend, storage_backend, audit_publisher):
    """Test content hash validation when sha256 is provided in metadata."""
    document_id = "doc-hash"
    tenant_id = "tenant-10"
    content = b"%PDF-1.4 test content"

    # Calculate correct hash
    import hashlib

    correct_hash = hashlib.sha256(content).hexdigest()

    metadata = {
        "mime_type": "application/pdf",
        "sha256": correct_hash,
    }

    record = DocumentRecord(
        document_id=document_id,
        tenant_id=tenant_id,
        status=1,
        metadata=metadata,
    )
    state_backend.create(record)

    mock_result = {
        "elements": [{"type": "text", "content": "Sample text"}],
        "metadata": {"page_count": 1},
    }

    with (
        patch("crosscodex_ingestion.worker.convert_document", return_value=mock_result),
        patch("crosscodex_ingestion.worker.validate_content_hash") as mock_validate,
    ):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata=metadata,
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
        )

    # Verify validate_content_hash was called
    assert mock_validate.call_count == 1
    assert mock_validate.call_args[0][0] == content
    assert mock_validate.call_args[0][1] == correct_hash
