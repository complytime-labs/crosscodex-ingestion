"""Unit tests for worker module."""

import asyncio
import json
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from prometheus_client import REGISTRY as PROMETHEUS

from crosscodex_ingestion.audit import AuditUnavailableError
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import ConversionError, ConversionTimeout, DocumentLimitError
from crosscodex_ingestion.html_profile import load_html_profiles
from crosscodex_ingestion.mapping import load_registry
from crosscodex_ingestion.output_schema import SCHEMA_VERSION
from crosscodex_ingestion.state import DocumentRecord, MemoryStateBackend
from crosscodex_ingestion.storage import FilesystemStorage
from crosscodex_ingestion.structured import StructuredSourceError
from crosscodex_ingestion.worker import (
    ERROR_CODE_UNAVAILABLE,
    JOB_STATUS_FAILED,
    SHUTDOWN_ERROR,
    record_failure,
    run_conversion,
)
from tests.unit.output_samples import conforming_output, structured_output

REGISTRY = load_registry("")
PROFILES = load_html_profiles("")
ASVS = REGISTRY.get("owasp-asvs-5.0.0-json")


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
    mock_result = conforming_output()

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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
    mock_result = conforming_output()

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
                mappings=REGISTRY,
                html_profiles=PROFILES,
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
            mappings=REGISTRY,
            html_profiles=PROFILES,
        )

    # Verify state is FAILED with error code 1 (INVALID_ARGUMENT)
    final_record = await state_backend.get(tenant_id, document_id)
    assert final_record is not None
    assert final_record.status == 4  # JOB_STATUS_FAILED
    assert final_record.error is not None
    assert final_record.error["code"] == 1
    assert "Unknown format" in final_record.error["message"]


@pytest.mark.asyncio
async def test_unknown_format_error_keeps_input_bytes_out_of_state_audit_and_logs(
    settings, state_backend, storage_backend, audit_publisher, caplog
):
    """The unknown-format failure reports a classification and size, never a prefix of the document."""
    document_id = "doc-prefix"
    tenant_id = "tenant-prefix"
    content = b"SECRET-TENANT-PREFIX more document text"
    await state_backend.create(DocumentRecord(document_id=document_id, tenant_id=tenant_id, status=1, metadata={}))

    with caplog.at_level("DEBUG"):
        await run_conversion(
            document_id=document_id,
            tenant_id=tenant_id,
            content=content,
            source_uri=None,
            metadata={},
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
            mappings=REGISTRY,
            html_profiles=PROFILES,
        )

    final_record = await state_backend.get(tenant_id, document_id)
    assert final_record is not None
    assert final_record.status == 4  # JOB_STATUS_FAILED
    assert "Cannot determine file type" in final_record.error["message"]
    audit_event = audit_publisher.publish.call_args.kwargs["event"]
    assert "Cannot determine file type" in json.dumps(audit_event)
    for surface in (json.dumps(final_record.error), json.dumps(audit_event), caplog.text):
        assert "SECRET" not in surface


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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
    mock_result = conforming_output()

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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
    mock_result = conforming_output()

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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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

    mock_result = conforming_output()

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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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

    mock_result = conforming_output()

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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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

    mock_result = conforming_output()

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
            mappings=REGISTRY,
            html_profiles=PROFILES,
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
        mappings=REGISTRY,
        html_profiles=PROFILES,
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
        return conforming_output()

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
        patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()),
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
        patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()),
        patch("pathlib.Path.read_bytes", return_value=b"%PDF-1.4 test"),
    ):
        mock_fetch.return_value.__enter__ = Mock(return_value=("/tmp/x.pdf", "pdf"))
        mock_fetch.return_value.__exit__ = Mock(return_value=False)
        await _run_pdf_conversion(
            settings, state_backend, storage_backend, audit_publisher, source_uri="https://example.com/a.pdf"
        )

    mock_fetch.assert_called_once_with(
        "https://example.com/a.pdf", max_bytes=1234, timeout=7, max_redirects=2, expected_format=None
    )


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
        patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()),
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


@pytest.mark.asyncio
async def test_record_failure_with_nats_down_keeps_failed_state_and_warns(state_backend, audit_publisher, caplog):
    """NATS dropped mid-job: the FAILED state is what callers see, so a lost audit event must not raise."""
    await state_backend.create(DocumentRecord(document_id="doc-t", tenant_id="tenant-t", status=1, metadata={}))
    audit_publisher.publish.side_effect = AuditUnavailableError("NATS at nats://nats:4222 is unavailable")

    with caplog.at_level("WARNING"):
        await record_failure(state_backend, audit_publisher, "tenant-t", "doc-t", {}, SHUTDOWN_ERROR, "shutdown", "pdf")

    record = await state_backend.get("tenant-t", "doc-t")
    assert record.status == JOB_STATUS_FAILED
    assert record.error == SHUTDOWN_ERROR
    warnings = [r for r in caplog.records if "doc-t" in r.getMessage()]
    assert [r.levelname for r in warnings] == ["WARNING"]
    assert warnings[0].exc_info is None
    assert "audit event was not recorded" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_record_failure_propagates_other_audit_errors(state_backend, audit_publisher):
    """Only the known NATS-down case is downgraded to a warning."""
    await state_backend.create(DocumentRecord(document_id="doc-t", tenant_id="tenant-t", status=1, metadata={}))
    audit_publisher.publish.side_effect = RuntimeError("unexpected")

    with pytest.raises(RuntimeError, match="unexpected"):
        await record_failure(state_backend, audit_publisher, "tenant-t", "doc-t", {}, SHUTDOWN_ERROR, "shutdown", "pdf")


@pytest.mark.asyncio
async def test_conversion_with_nats_down_ends_failed_without_raising(
    settings, state_backend, storage_backend, audit_publisher
):
    """Every audit publish fails: the job is FAILED and run_conversion returns, so no task exception is left."""
    audit_publisher.publish.side_effect = AuditUnavailableError("NATS at nats://nats:4222 is unavailable")

    with patch("crosscodex_ingestion.worker.convert_document", side_effect=ConversionError("boom")):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    record = await state_backend.get("tenant-t", "doc-t")
    assert record.status == JOB_STATUS_FAILED


@pytest.mark.asyncio
async def test_non_conforming_output_fails_with_the_offending_element_and_stores_nothing(
    settings, state_backend, storage_backend, audit_publisher, tmp_path
):
    """An output that breaks the schema is never stored; the client learns which element broke which rule."""
    output = conforming_output()
    output["elements"][0]["content"] = " "

    with patch("crosscodex_ingestion.worker.convert_document", return_value=output):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    record = await state_backend.get("tenant-t", "doc-t")
    assert record.status == JOB_STATUS_FAILED
    assert not record.structured_json_uri
    assert record.error["code"] == 6  # INTERNAL: the service's own output is wrong, not the request
    assert "elements[0] (element_id 'e-001').content" in record.error["message"]
    assert "doc-t" in record.error["message"]
    assert record.error["details"] == {
        "schema_version": SCHEMA_VERSION,
        "location": "elements[0] (element_id 'e-001').content",
        "element_index": "0",
        "element_id": "e-001",
    }
    assert audit_publisher.publish.call_args.kwargs["event"]["error"] == record.error
    assert not list((tmp_path / "storage").rglob("*"))


@pytest.mark.asyncio
async def test_non_conforming_output_is_never_attested(
    settings, state_backend, storage_backend, audit_publisher, tmp_path
):
    """With attestation enabled, a rejected output gets no signed link either."""
    output = conforming_output()
    output["elements"][1]["parent_id"] = "e-999"
    attestation = Mock()
    await state_backend.create(DocumentRecord(document_id="doc-t", tenant_id="tenant-t", status=1, metadata={}))

    with patch("crosscodex_ingestion.worker.convert_document", return_value=output):
        await run_conversion(
            document_id="doc-t",
            tenant_id="tenant-t",
            content=b"%PDF-1.4 test",
            source_uri=None,
            metadata={},
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=attestation,
            audit=audit_publisher,
            mappings=REGISTRY,
            html_profiles=PROFILES,
        )

    assert (await state_backend.get("tenant-t", "doc-t")).status == JOB_STATUS_FAILED
    attestation.create_link.assert_not_called()
    assert not list((tmp_path / "storage").rglob("*"))


@pytest.mark.asyncio
async def test_violation_outside_an_element_has_no_element_details(
    settings, state_backend, storage_backend, audit_publisher
):
    output = conforming_output()
    del output["metadata"]["dropped"]

    with patch("crosscodex_ingestion.worker.convert_document", return_value=output):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    error = (await state_backend.get("tenant-t", "doc-t")).error
    assert error["details"] == {"schema_version": SCHEMA_VERSION, "location": "metadata"}
    assert "'dropped' is a required property" in error["message"]


@pytest.mark.asyncio
async def test_dropped_content_is_logged_and_kept_in_the_stored_output(
    settings, state_backend, storage_backend, audit_publisher, caplog
):
    output = conforming_output()
    output["metadata"]["dropped"] = [{"type": "table", "page": 4, "reason": "table exported no content"}]

    with (
        patch("crosscodex_ingestion.worker.convert_document", return_value=output),
        caplog.at_level("WARNING"),
    ):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    record = await state_backend.get("tenant-t", "doc-t")
    assert record.status == 3
    stored = json.loads(Path(record.structured_json_uri.removeprefix("file://")).read_text())
    assert stored["metadata"]["dropped"] == output["metadata"]["dropped"]
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("doc-t" in m and "table" in m and "page 4" in m and "exported no content" in m for m in warnings)


def _structure_warning_count(fmt, warning):
    return PROMETHEUS.get_sample_value("ingestion_structure_warnings_total", {"format": fmt, "warning": warning}) or 0


async def _stored_output(state_backend, tenant, document):
    record = await state_backend.get(tenant, document)
    assert record.status == 3
    return json.loads(Path(record.structured_json_uri.removeprefix("file://")).read_text())


def _flat_output():
    output = conforming_output()
    output["elements"] = [
        {
            "element_id": f"e-{i:03d}",
            "type": "paragraph",
            "content": f"secret paragraph text {i}",
            "page": 1,
            "level": None,
            "parent_id": None,
            "style": {},
        }
        for i in range(60)
    ]
    output["metadata"]["element_count"] = 60
    return output


@pytest.mark.asyncio
async def test_flat_result_counts_and_logs_no_headings(
    settings, state_backend, storage_backend, audit_publisher, caplog
):
    output = _flat_output()
    before = _structure_warning_count("pdf", "no_headings")

    with (
        patch("crosscodex_ingestion.worker.convert_document", return_value=output),
        caplog.at_level("WARNING"),
    ):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    stored = await _stored_output(state_backend, "tenant-t", "doc-t")
    assert stored["metadata"]["structure"]["warnings"] == ["no_headings"]
    assert _structure_warning_count("pdf", "no_headings") == before + 1
    assert "structure warning no_headings (60 elements)" in caplog.text
    assert "secret paragraph text" not in caplog.text


@pytest.mark.asyncio
async def test_structured_result_has_no_warnings(settings, state_backend, storage_backend, audit_publisher, caplog):
    before = _structure_warning_count("pdf", "no_headings")

    with (
        patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()),
        caplog.at_level("WARNING"),
    ):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    stored = await _stored_output(state_backend, "tenant-t", "doc-t")
    assert stored["metadata"]["structure"]["warnings"] == []
    assert _structure_warning_count("pdf", "no_headings") == before
    assert "structure warning" not in caplog.text


@pytest.mark.asyncio
async def test_conversion_warnings_are_merged_counted_and_logged(
    settings, state_backend, storage_backend, audit_publisher, caplog
):
    output = conforming_output()
    output["metadata"]["detected_format"] = "html"
    output["conversion_warnings"] = ["html_profile_ambiguous"]
    before = _structure_warning_count("html", "html_profile_ambiguous")
    await state_backend.create(DocumentRecord(document_id="doc-h", tenant_id="tenant-h", status=1, metadata={}))

    with (
        patch("crosscodex_ingestion.worker.convert_document", return_value=output),
        caplog.at_level("WARNING"),
    ):
        await run_conversion(
            document_id="doc-h",
            tenant_id="tenant-h",
            content=b"<!DOCTYPE html><html><body><p>x</p></body></html>",
            source_uri=None,
            metadata={},
            settings=settings,
            state=state_backend,
            storage=storage_backend,
            attestation=None,
            audit=audit_publisher,
            mappings=REGISTRY,
            html_profiles=PROFILES,
        )

    stored = await _stored_output(state_backend, "tenant-h", "doc-h")
    assert "conversion_warnings" not in stored
    assert stored["metadata"]["structure"]["warnings"] == ["html_profile_ambiguous"]
    assert _structure_warning_count("html", "html_profile_ambiguous") == before + 1
    assert "structure warning html_profile_ambiguous (2 elements)" in caplog.text


@pytest.mark.asyncio
async def test_conversion_warnings_follow_measured_warnings(settings, state_backend, storage_backend, audit_publisher):
    output = _flat_output()
    output["conversion_warnings"] = ["html_profile_ambiguous"]

    with patch("crosscodex_ingestion.worker.convert_document", return_value=output):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    stored = await _stored_output(state_backend, "tenant-t", "doc-t")
    assert stored["metadata"]["structure"]["warnings"] == ["no_headings", "html_profile_ambiguous"]


@pytest.mark.asyncio
async def test_result_missing_elements_fails_as_invalid_output(
    settings, state_backend, storage_backend, audit_publisher, tmp_path
):
    output = conforming_output()
    del output["elements"]

    with patch("crosscodex_ingestion.worker.convert_document", return_value=output):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    record = await state_backend.get("tenant-t", "doc-t")
    assert record.status == JOB_STATUS_FAILED
    assert record.error["code"] == 6
    assert record.error["details"]["location"] == "output"
    assert not list((tmp_path / "storage").rglob("*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [["Bad Code"], [3], ["ok\n"], "no_headings", ["made_up_code"]])
async def test_malformed_conversion_warnings_fail_as_invalid_output(
    bad, settings, state_backend, storage_backend, audit_publisher, tmp_path
):
    output = conforming_output()
    output["conversion_warnings"] = bad

    with patch("crosscodex_ingestion.worker.convert_document", return_value=output):
        await _run_pdf_conversion(settings, state_backend, storage_backend, audit_publisher)

    record = await state_backend.get("tenant-t", "doc-t")
    assert record.status == JOB_STATUS_FAILED
    assert record.error["code"] == 6
    assert record.error["details"]["location"].startswith("conversion_warnings")
    assert not list((tmp_path / "storage").rglob("*"))


ASVS_JSON = b'{"Requirements": [{"Shortcode": "V1", "Description": "Verify."}]}'


async def _run_structured(
    state_backend,
    storage_backend,
    audit_publisher,
    *,
    content=ASVS_JSON,
    metadata=None,
    settings=None,
    attestation=None,
    source_uri=None,
):
    """Submit a PENDING record and run the pipeline; by default for ASVS JSON with the ASVS mapping."""
    if metadata is None:
        metadata = {"mapping_id": ASVS.id, "mapping_sha256": ASVS.sha256}
    await state_backend.create(DocumentRecord(document_id="doc-s", tenant_id="tenant-s", status=1, metadata=metadata))
    await run_conversion(
        document_id="doc-s",
        tenant_id="tenant-s",
        content=None if source_uri else content,
        source_uri=source_uri,
        metadata=metadata,
        settings=settings or IngestionSettings(),
        state=state_backend,
        storage=storage_backend,
        attestation=attestation,
        audit=audit_publisher,
        mappings=REGISTRY,
        html_profiles=PROFILES,
    )
    return await state_backend.get("tenant-s", "doc-s")


class TestStructuredConversion:
    async def test_structure_present_on_structured_path(self, state_backend, storage_backend, audit_publisher):
        with patch("crosscodex_ingestion.worker.convert_structured", return_value=structured_output()):
            await _run_structured(state_backend, storage_backend, audit_publisher)

        stored = await _stored_output(state_backend, "tenant-s", "doc-s")
        assert stored["metadata"]["structure"]["heading_count"] == 1

    async def test_mapping_routes_to_the_structured_worker_and_is_attested(
        self, state_backend, storage_backend, audit_publisher
    ):
        attestation = Mock()
        attestation.create_link.return_value = {"signed": {}, "signatures": []}

        with (
            patch("crosscodex_ingestion.worker.convert_structured", return_value=structured_output()) as structured,
            patch("crosscodex_ingestion.worker.convert_document") as document,
        ):
            record = await _run_structured(state_backend, storage_backend, audit_publisher, attestation=attestation)

        assert record.status == 3
        assert structured.call_args.args[1] is ASVS
        document.assert_not_called()
        byproducts = attestation.create_link.call_args.kwargs["byproducts"]
        assert (byproducts["format"], byproducts["mapping_id"], byproducts["mapping_sha256"]) == (
            "json",
            ASVS.id,
            ASVS.sha256,
        )

    async def test_link_byproducts_record_who_submitted_the_job(self, state_backend, storage_backend, audit_publisher):
        attestation = Mock()
        attestation.create_link.return_value = {"signed": {}, "signatures": []}
        metadata = {"mapping_id": ASVS.id, "mapping_sha256": ASVS.sha256}
        await state_backend.create(
            DocumentRecord(
                document_id="doc-s", tenant_id="tenant-s", status=1, metadata=metadata, audit={"created_by": "gateway"}
            )
        )

        with patch("crosscodex_ingestion.worker.convert_structured", return_value=structured_output()):
            await run_conversion(
                document_id="doc-s",
                tenant_id="tenant-s",
                content=ASVS_JSON,
                source_uri=None,
                metadata=metadata,
                settings=IngestionSettings(),
                state=state_backend,
                storage=storage_backend,
                attestation=attestation,
                audit=audit_publisher,
                mappings=REGISTRY,
                html_profiles=PROFILES,
            )

        assert attestation.create_link.call_args.kwargs["byproducts"]["created_by"] == "gateway"

    async def test_attestation_uri_is_stored_on_the_record(self, state_backend, storage_backend, audit_publisher):
        attestation = Mock()
        attestation.create_link.return_value = {"signed": {}, "signatures": []}

        with patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()):
            record = await _run_structured(
                state_backend,
                storage_backend,
                audit_publisher,
                content=b"%PDF-1.4",
                metadata={},
                attestation=attestation,
            )

        assert record.attestation_uri
        assert record.attestation_uri.endswith("attestation.link")

    async def test_attestation_uri_is_empty_when_attestation_is_disabled(
        self, state_backend, storage_backend, audit_publisher
    ):
        with patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()):
            record = await _run_structured(
                state_backend, storage_backend, audit_publisher, content=b"%PDF-1.4", metadata={}
            )

        assert record.status == 3
        assert record.attestation_uri == ""

    async def test_documents_are_attested_without_mapping_fields(self, state_backend, storage_backend, audit_publisher):
        attestation = Mock()
        attestation.create_link.return_value = {"signed": {}, "signatures": []}

        with patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()):
            await _run_structured(
                state_backend,
                storage_backend,
                audit_publisher,
                content=b"%PDF-1.4",
                metadata={},
                attestation=attestation,
            )

        assert "mapping_id" not in attestation.create_link.call_args.kwargs["byproducts"]

    @pytest.mark.parametrize(
        "metadata",
        [
            {"mapping_id": ASVS.id, "mapping_sha256": "0" * 64},
            {"mapping_id": "retired-mapping", "mapping_sha256": "0" * 64},
        ],
    )
    async def test_mapping_this_worker_lacks_or_hashes_differently_fails_retryable(
        self, state_backend, storage_backend, audit_publisher, metadata
    ):
        with patch("crosscodex_ingestion.worker.convert_structured") as structured:
            record = await _run_structured(state_backend, storage_backend, audit_publisher, metadata=metadata)

        structured.assert_not_called()
        assert record.error["code"] == ERROR_CODE_UNAVAILABLE
        assert metadata["mapping_id"] in record.error["message"]
        assert "resubmit" in record.error["message"]

    async def test_structured_input_without_mapping_fails_listing_the_mappings(
        self, state_backend, storage_backend, audit_publisher
    ):
        with patch("crosscodex_ingestion.worker.convert_document") as document:
            record = await _run_structured(
                state_backend, storage_backend, audit_publisher, metadata={"mime_type": "application/json"}
            )

        document.assert_not_called()
        assert record.error["code"] == 1
        assert record.error["message"] == (
            "The input is json, which is read through a mapping: set ConvertDocumentRequest.mapping to the ID of a "
            "registered mapping. Registered mappings: mitre-attack-enterprise-17.1-techniques-xlsx, "
            "nist-csf-2.0-core-xlsx, nist-sp800-53r5-catalog-xlsx, owasp-asvs-5.0.0-csv, owasp-asvs-5.0.0-json."
        )

    async def test_input_of_another_format_than_the_mapping_reads_fails(
        self, state_backend, storage_backend, audit_publisher
    ):
        attack = REGISTRY.get("mitre-attack-enterprise-17.1-techniques-xlsx")
        metadata = {"mapping_id": attack.id, "mapping_sha256": attack.sha256}

        record = await _run_structured(state_backend, storage_backend, audit_publisher, metadata=metadata)

        assert record.error == {"code": 1, "message": f"Mapping '{attack.id}' reads xlsx, but the input is json."}

    async def test_structured_format_not_allowed_fails(self, state_backend, storage_backend, audit_publisher):
        record = await _run_structured(
            state_backend, storage_backend, audit_publisher, settings=IngestionSettings(allowed_formats="pdf")
        )

        assert record.error["code"] == 1
        assert "'json' is not in allowed formats" in record.error["message"]

    async def test_source_problems_reach_the_client_with_details(self, state_backend, storage_backend, audit_publisher):
        error = StructuredSourceError(ASVS.id, ["Node at Requirements[0] has no ID."], ["Requirements[0]"], 1)

        with patch("crosscodex_ingestion.worker.convert_structured", side_effect=error):
            record = await _run_structured(state_backend, storage_backend, audit_publisher)

        assert record.error == {
            "code": 1,
            "message": str(error),
            "details": {
                "mapping": ASVS.id,
                "count": "1",
                "problem.1": "Node at Requirements[0] has no ID.",
                "location.1": "Requirements[0]",
            },
        }

    async def test_audit_and_logs_get_the_problems_without_source_values(
        self, state_backend, storage_backend, audit_publisher, caplog
    ):
        """Problems quote the caller's data: only the caller (the state record) gets them in full."""
        error = StructuredSourceError(
            ASVS.id, ["Duplicate ID 'tenant-secret' at Requirements[0] and Requirements[1]."], ["Requirements[1]"], 1
        )

        with (
            patch("crosscodex_ingestion.worker.convert_structured", side_effect=error),
            caplog.at_level("DEBUG"),
        ):
            record = await _run_structured(state_backend, storage_backend, audit_publisher)

        assert "tenant-secret" in record.error["message"]
        audit_error = audit_publisher.publish.call_args.kwargs["event"]["error"]
        message, details = error.redacted()
        assert audit_error == {"code": 1, "message": message, "details": details}
        assert "tenant-secret" not in str(audit_error)
        assert not any("tenant-secret" in r.getMessage() or r.exc_info for r in caplog.records)
        assert any("doc-s" in r.getMessage() and "Requirements[1]" in r.getMessage() for r in caplog.records)

    async def test_limit_error_values_stay_out_of_audit_and_logs(
        self, state_backend, storage_backend, audit_publisher, caplog
    ):
        error = DocumentLimitError(
            "ZIP entry 'tenant-secret' expands 9 bytes from 1, more than MAX_XLSX_COMPRESSION_RATIO (2) times.",
            redacted_message="A ZIP entry expands 9 bytes from 1, more than MAX_XLSX_COMPRESSION_RATIO (2) times.",
        )

        with (
            patch("crosscodex_ingestion.worker.convert_structured", side_effect=error),
            caplog.at_level("DEBUG"),
        ):
            record = await _run_structured(state_backend, storage_backend, audit_publisher)

        assert record.error == {"code": 5, "message": str(error)}
        audit_error = audit_publisher.publish.call_args.kwargs["event"]["error"]
        assert audit_error == {"code": 5, "message": error.redacted_message}
        assert not any("tenant-secret" in r.getMessage() or r.exc_info for r in caplog.records)
        assert any("doc-s" in r.getMessage() and "MAX_XLSX_COMPRESSION_RATIO" in r.getMessage() for r in caplog.records)

    async def test_format_checks_run_off_the_event_loop(self, state_backend, storage_backend, audit_publisher):
        """They read untrusted bytes in the service process; the event loop must keep serving RPCs."""
        threads = []

        def confirm(*args):
            threads.append(threading.current_thread())
            return "json"

        with (
            patch("crosscodex_ingestion.worker.confirm_structured_format", side_effect=confirm),
            patch("crosscodex_ingestion.worker.convert_structured", return_value=structured_output()),
        ):
            await _run_structured(state_backend, storage_backend, audit_publisher)

        assert threads
        assert threads[0] is not threading.main_thread()

    async def test_document_format_checks_run_off_the_event_loop(self, state_backend, storage_backend, audit_publisher):
        threads = []

        def validate(*args):
            threads.append(threading.current_thread())
            return "pdf"

        with (
            patch("crosscodex_ingestion.worker.validate_format", side_effect=validate),
            patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()),
        ):
            await _run_structured(state_backend, storage_backend, audit_publisher, content=b"%PDF-1.4", metadata={})

        assert threads[0] is not threading.main_thread()

    async def test_url_source_is_fetched_as_the_mapping_format(self, state_backend, storage_backend, audit_publisher):
        with (
            patch("crosscodex_ingestion.worker.validate_url"),
            patch("crosscodex_ingestion.worker.fetch_url") as mock_fetch,
            patch("crosscodex_ingestion.worker.convert_structured", return_value=structured_output()),
            patch("pathlib.Path.read_bytes", return_value=ASVS_JSON),
        ):
            mock_fetch.return_value.__enter__ = Mock(return_value=("/tmp/x.json", "json"))
            mock_fetch.return_value.__exit__ = Mock(return_value=False)
            record = await _run_structured(
                state_backend, storage_backend, audit_publisher, source_uri="https://example.org/download/1"
            )

        assert record.status == 3
        assert mock_fetch.call_args.kwargs["expected_format"] == "json"


async def test_run_conversion_passes_the_html_profiles_to_the_converter(tmp_path):
    state, storage, audit = MemoryStateBackend(), FilesystemStorage(str(tmp_path / "storage")), AsyncMock()
    metadata = {"mime_type": "text/html"}
    await state.create(DocumentRecord(document_id="doc-p", tenant_id="t", status=1, metadata=metadata))

    with patch("crosscodex_ingestion.worker.convert_document", return_value=conforming_output()) as document:
        await run_conversion(
            document_id="doc-p",
            tenant_id="t",
            content=b"<p>x</p>",
            source_uri=None,
            metadata=metadata,
            settings=IngestionSettings(storage_path=str(tmp_path / "storage")),
            state=state,
            storage=storage,
            attestation=None,
            audit=audit,
            mappings=REGISTRY,
            html_profiles=PROFILES,
        )

    assert document.call_args.args[-1] is PROFILES
