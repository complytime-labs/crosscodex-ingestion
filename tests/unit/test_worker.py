"""Unit tests for worker module."""

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import ConversionError, ConversionTimeout, DocumentLimitError
from crosscodex_ingestion.state import DocumentRecord, MemoryStateBackend
from crosscodex_ingestion.storage import FilesystemStorage
from crosscodex_ingestion.worker import (
    ERROR_CODE_UNAVAILABLE,
    JOB_STATUS_FAILED,
    SHUTDOWN_ERROR,
    run_conversion,
)


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
    await state_backend.create(record)

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
    final_record = await state_backend.get(tenant_id, document_id)
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
    await state_backend.create(record)

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
    final_record = await state_backend.get(tenant_id, document_id)
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
    await state_backend.create(record)

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
    final_record = await state_backend.get(tenant_id, document_id)
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
    await state_backend.create(record)

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
    final_record = await state_backend.get(tenant_id, document_id)
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
    await state_backend.create(record)

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
    final_record = await state_backend.get(tenant_id, document_id)
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
    await state_backend.create(record)

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
    final_record = await state_backend.get(tenant_id, document_id)
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
    await state_backend.create(record)

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
    await state_backend.create(record)

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
    final_record = await state_backend.get(tenant_id, document_id)
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
    await state_backend.create(record)

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
    final_record = await state_backend.get(tenant_id, document_id)
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
    await state_backend.create(record)

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


async def _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher, source_uri=None):
    """Submit a PENDING record and run the pipeline for a PDF given as bytes (or by URL)."""
    await state_backend.create(DocumentRecord(document_id="doc-t", tenant_id="tenant-t", status=1, metadata={}))
    await run_conversion(
        document_id="doc-t",
        tenant_id="tenant-t",
        content=None if source_uri else b"%PDF-1.4 test",
        source_uri=source_uri,
        metadata={},
        settings=settings,
        state=state_backend,
        storage=storage_backend,
        attestation=None,
        audit=audit_publisher,
    )


async def _count_ticks_while(coro):
    """Run *coro* while a ticker coroutine counts event-loop turns; return the tick count."""
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker_task = asyncio.create_task(ticker())
    try:
        await coro
    finally:
        ticker_task.cancel()
    return ticks


@pytest.mark.asyncio
async def test_slow_conversion_does_not_block_event_loop(settings, state_backend, storage_backend, audit_publisher):
    """convert_document awaits the subprocess, so other coroutines (e.g. GetDocument) keep running."""

    async def slow_convert(*args, **kwargs):
        await asyncio.sleep(0.5)
        return {"elements": [], "metadata": {}}

    with patch("crosscodex_ingestion.worker.convert_document", side_effect=slow_convert):
        ticks = await _count_ticks_while(_run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher))

    assert ticks >= 20  # ~50 expected; a blocked loop yields ~0
    assert (await state_backend.get("tenant-t", "doc-t")).status == 3


@pytest.mark.asyncio
async def test_slow_url_fetch_does_not_block_event_loop(settings, state_backend, storage_backend, audit_publisher):
    """DNS resolution and the HTTP download run in a thread too."""

    def slow_validate(url):
        time.sleep(0.5)

    with (
        patch("crosscodex_ingestion.worker.validate_url", side_effect=slow_validate),
        patch("crosscodex_ingestion.worker.fetch_url") as mock_fetch,
        patch("crosscodex_ingestion.worker.convert_document", return_value={"elements": [], "metadata": {}}),
        patch("pathlib.Path.read_bytes", return_value=b"%PDF-1.4 test"),
    ):
        mock_fetch.return_value.__enter__ = Mock(return_value=("/tmp/x.pdf", "pdf"))
        mock_fetch.return_value.__exit__ = Mock(return_value=False)
        ticks = await _count_ticks_while(
            _run_pdf_conversion(
                settings, state_backend, storage_backend, audit_publisher, source_uri="https://example.com/a.pdf"
            )
        )

    assert ticks >= 20


@pytest.mark.asyncio
async def test_max_redirects_setting_is_passed_to_fetch_url(state_backend, storage_backend, audit_publisher):
    settings = IngestionSettings(max_redirects=2, max_url_bytes=1234, url_timeout=7)

    with (
        patch("crosscodex_ingestion.worker.validate_url"),
        patch("crosscodex_ingestion.worker.fetch_url") as mock_fetch,
        patch("crosscodex_ingestion.worker.convert_document", return_value={"elements": [], "metadata": {}}),
        patch("pathlib.Path.read_bytes", return_value=b"%PDF-1.4 test"),
    ):
        mock_fetch.return_value.__enter__ = Mock(return_value=("/tmp/x.pdf", "pdf"))
        mock_fetch.return_value.__exit__ = Mock(return_value=False)
        await _run_pdf_conversion(
            settings, state_backend, storage_backend, audit_publisher, source_uri="https://example.com/a.pdf"
        )

    mock_fetch.assert_called_once_with("https://example.com/a.pdf", max_bytes=1234, timeout=7, max_redirects=2)


async def _failed_error_with(convert_side_effect, settings, state_backend, storage_backend, audit_publisher):
    """Run a conversion whose convert_document raises; return (client error dict, audit event)."""
    with patch("crosscodex_ingestion.worker.convert_document", side_effect=convert_side_effect):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)
    record = await state_backend.get("tenant-t", "doc-t")
    assert record.status == 4
    return record.error, audit_publisher.publish.call_args.kwargs["event"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        ConversionError("exit code 1. stderr: Traceback (most recent call last): File '/srv/app/secret.py'"),
        RuntimeError("connect to redis://user:pw@10.0.0.5 failed"),
        OSError("[Errno 13] Permission denied: '/data/crosscodex/tenant/x'"),
    ],
)
async def test_internal_failures_return_generic_message_and_log_detail(
    exc, settings, state_backend, storage_backend, audit_publisher, caplog
):
    """CWE-209: internal exception text never reaches the client or the audit event."""
    with caplog.at_level("ERROR"):
        error, event = await _failed_error_with(exc, settings, state_backend, storage_backend, audit_publisher)

    assert error["code"] == 6  # INTERNAL
    assert "doc-t" in error["message"]
    for leaked in ("Traceback", "/srv", "redis", "10.0.0.5", "/data"):
        assert leaked not in error["message"]
    assert event["error"] == error
    # Full detail is logged server-side exactly once, tagged with the document id.
    failure_logs = [r for r in caplog.records if r.levelname == "ERROR" and "doc-t" in r.getMessage()]
    assert len(failure_logs) == 1
    assert str(exc) in failure_logs[0].exc_text or str(exc) in failure_logs[0].getMessage()


@pytest.mark.asyncio
async def test_user_caused_value_error_keeps_specific_message(
    settings, state_backend, storage_backend, audit_publisher
):
    error, _ = await _failed_error_with(
        ValueError("SHA-256 mismatch: expected 'aa', got 'bb'"),
        settings,
        state_backend,
        storage_backend,
        audit_publisher,
    )

    assert error["code"] == 1  # INVALID_ARGUMENT
    assert error["message"] == "SHA-256 mismatch: expected 'aa', got 'bb'"


@pytest.mark.asyncio
async def test_document_limit_error_is_resource_exhausted_with_specific_message(
    settings, state_backend, storage_backend, audit_publisher
):
    error, _ = await _failed_error_with(
        DocumentLimitError("Document exceeds the maximum of 10 pages"),
        settings,
        state_backend,
        storage_backend,
        audit_publisher,
    )

    assert error["code"] == 5  # RESOURCE_EXHAUSTED
    assert error["message"] == "Document exceeds the maximum of 10 pages"


@pytest.mark.asyncio
async def test_timeout_message_is_generic_but_actionable(settings, state_backend, storage_backend, audit_publisher):
    error, _ = await _failed_error_with(
        ConversionTimeout("Conversion timed out after 3600 seconds"),
        settings,
        state_backend,
        storage_backend,
        audit_publisher,
    )

    assert error["code"] == 5
    assert "timed out" in error["message"].lower()


@pytest.mark.asyncio
async def test_slow_storage_upload_does_not_block_event_loop(settings, state_backend, storage_backend, audit_publisher):
    """S3 uploads of structured output and attestation are network I/O; they run in a thread."""
    real_upload = storage_backend.upload

    def slow_upload(*args, **kwargs):
        time.sleep(0.25)
        return real_upload(*args, **kwargs)

    with (
        patch.object(storage_backend, "upload", side_effect=slow_upload),
        patch("crosscodex_ingestion.worker.convert_document", return_value={"elements": [], "metadata": {}}),
    ):
        ticks = await _count_ticks_while(_run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher))

    assert ticks >= 10
    assert (await state_backend.get("tenant-t", "doc-t")).status == 3


@pytest.mark.asyncio
async def test_cancelled_conversion_is_failed_with_retryable_error_and_audited(
    settings, state_backend, storage_backend, audit_publisher
):
    """Shutdown cancels the task: the document must end FAILED/UNAVAILABLE, be audited, and cancellation propagates."""
    started = asyncio.Event()

    async def hang(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    with patch("crosscodex_ingestion.worker.convert_document", side_effect=hang):
        task = asyncio.create_task(_run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    record = await state_backend.get("tenant-t", "doc-t")
    assert record.status == JOB_STATUS_FAILED
    assert record.error == SHUTDOWN_ERROR
    assert record.error["code"] == ERROR_CODE_UNAVAILABLE
    assert "resubmit" in record.error["message"]
    event = audit_publisher.publish.call_args.kwargs["event"]
    assert event["status"] == "failed"
    assert event["error"] == SHUTDOWN_ERROR
