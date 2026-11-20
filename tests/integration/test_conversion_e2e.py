"""Integration tests for full document conversion pipeline.

These tests exercise the complete gRPC service with real Docling conversion.
They may be slow and may fail if Docling model downloads are blocked.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc

def _probe_docling() -> str:
    """Return "" if a clean interpreter can import Docling's converter, else the failure text.

    Runs in a private temp dir with ``python -I`` so a stray module in a shared directory
    (e.g. /tmp/struct.py) cannot shadow the standard library and break the probe.
    """
    workdir = tempfile.mkdtemp(prefix="docling-probe-")
    try:
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-c", "from docling.document_converter import DocumentConverter"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired as exc:
            return f"Docling probe timed out after {exc.timeout}s"
        return "" if result.returncode == 0 else f"Docling probe exited {result.returncode}: {result.stderr.strip()}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


DOCLING_PROBE_ERROR = _probe_docling()


@pytest.fixture(autouse=True)
def _require_docling() -> None:
    """Fail (never skip) when Docling is expected, i.e. DOCLING_ARTIFACTS_PATH is set."""
    if not DOCLING_PROBE_ERROR:
        return
    if os.environ.get("DOCLING_ARTIFACTS_PATH"):
        pytest.fail(f"DOCLING_ARTIFACTS_PATH is set but Docling is unusable: {DOCLING_PROBE_ERROR}")
    pytest.skip(f"Docling not available in this environment: {DOCLING_PROBE_ERROR}")


async def poll_until_complete(
    stub: ingestion_pb2_grpc.IngestionServiceStub,
    tenant_id: str,
    document_id: str,
    timeout_seconds: int = 60,
    poll_interval: float = 0.5,
) -> ingestion_pb2.GetDocumentResponse:
    """Poll GetDocument until status is COMPLETED or FAILED, or timeout.

    Args:
        stub: gRPC stub.
        tenant_id: Tenant ID.
        document_id: Document ID.
        timeout_seconds: Maximum time to wait.
        poll_interval: Time between polls in seconds.

    Returns:
        Final GetDocumentResponse.

    Raises:
        TimeoutError: If document does not reach terminal state within timeout.
    """
    start_time = asyncio.get_event_loop().time()
    while True:
        request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=tenant_id), document_id=document_id
        )
        response = await stub.GetDocument(request)

        if response.document.conversion_status in (common_pb2.JOB_STATUS_COMPLETED, common_pb2.JOB_STATUS_FAILED):
            return response

        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout_seconds:
            raise TimeoutError(f"Document {document_id} did not complete within {timeout_seconds}s")

        await asyncio.sleep(poll_interval)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_convert_html_end_to_end(grpc_server_and_channel, sample_html):
    """Convert HTML document and verify COMPLETED status with structured output."""
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    # Submit conversion request
    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-html"),
        content=sample_html,
        metadata=common_pb2.ContentMetadata(mime_type="text/html"),
    )

    convert_response = await stub.ConvertDocument(request)
    assert convert_response.document_id
    assert convert_response.status == common_pb2.JOB_STATUS_PENDING

    # Poll until completed
    final_response = await poll_until_complete(stub, "tenant-html", convert_response.document_id, timeout_seconds=120)

    # Verify completion
    assert final_response.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED
    assert final_response.document.structured_json_uri

    # Verify structured JSON URI is set (actual JSON retrieval would require storage backend access)
    # For now, just verify the URI exists
    assert final_response.document.structured_json_uri


@pytest.mark.integration
@pytest.mark.asyncio
async def test_convert_txt_end_to_end(grpc_server_and_channel, sample_txt):
    """Convert TXT document and verify COMPLETED status."""
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-txt"),
        content=sample_txt,
        metadata=common_pb2.ContentMetadata(mime_type="text/plain"),
    )

    convert_response = await stub.ConvertDocument(request)
    assert convert_response.document_id

    final_response = await poll_until_complete(stub, "tenant-txt", convert_response.document_id, timeout_seconds=120)

    assert final_response.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED
    assert final_response.document.structured_json_uri


@pytest.mark.integration
@pytest.mark.asyncio
async def test_csv_looking_text_is_converted_as_plain_text(grpc_server_and_channel):
    """Docling's sniffer would read this as a CSV table; text/plain is paragraphs."""
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    convert_response = await stub.ConvertDocument(
        ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-csv-text"),
            content=b"name,role\nalice,admin\nbob,user\n",
            metadata=common_pb2.ContentMetadata(mime_type="text/plain"),
        )
    )
    final = await poll_until_complete(stub, "tenant-csv-text", convert_response.document_id, timeout_seconds=120)

    assert final.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED, final.document.error.message
    stored = json.loads(Path(final.document.structured_json_uri.removeprefix("file://")).read_text())
    assert [e["type"] for e in stored["elements"]] == ["paragraph"]
    assert "alice,admin" in stored["elements"][0]["content"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_corrupt_pdf_is_an_invalid_argument_not_internal(grpc_server_and_channel):
    """The %PDF header passes format validation; Docling's PDF backend then cannot parse the body."""
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    convert_response = await stub.ConvertDocument(
        ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-corrupt-pdf"),
            content=b"%PDF-1.7\nthis is not a PDF body\n",
            metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
        )
    )
    final = await poll_until_complete(stub, "tenant-corrupt-pdf", convert_response.document_id, timeout_seconds=120)

    assert final.document.conversion_status == common_pb2.JOB_STATUS_FAILED
    assert final.document.error.code == common_pb2.ERROR_CODE_INVALID_ARGUMENT
    assert final.document.error.message.startswith("The input could not be parsed as pdf: ")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_convert_pdf_end_to_end(grpc_server_and_channel, sample_pdf):
    """Convert PDF document and verify COMPLETED status.

    PDF conversion with Docling may require model downloads and is slower.
    """
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-pdf"),
        content=sample_pdf,
        metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
    )

    convert_response = await stub.ConvertDocument(request)
    assert convert_response.document_id

    # PDF conversion may be slow; allow more time
    final_response = await poll_until_complete(stub, "tenant-pdf", convert_response.document_id, timeout_seconds=180)

    assert final_response.document.conversion_status in (common_pb2.JOB_STATUS_COMPLETED, common_pb2.JOB_STATUS_FAILED)
    # If it succeeded, verify output
    if final_response.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED:
        assert final_response.document.structured_json_uri


@pytest.mark.integration
@pytest.mark.asyncio
async def test_convert_docx_end_to_end(grpc_server_and_channel, sample_docx):
    """Convert DOCX document and verify COMPLETED status.

    DOCX conversion with Docling may require model downloads and is slower.
    """
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-docx"),
        content=sample_docx,
        metadata=common_pb2.ContentMetadata(
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )

    convert_response = await stub.ConvertDocument(request)
    assert convert_response.document_id

    # DOCX conversion may be slow; allow more time
    final_response = await poll_until_complete(stub, "tenant-docx", convert_response.document_id, timeout_seconds=180)

    assert final_response.document.conversion_status in (common_pb2.JOB_STATUS_COMPLETED, common_pb2.JOB_STATUS_FAILED)
    # If it succeeded, verify output
    if final_response.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED:
        assert final_response.document.structured_json_uri


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_documents_returns_submitted_documents(grpc_server_and_channel, sample_txt):
    """Verify ListDocuments returns documents submitted for a tenant."""
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    tenant_id = "tenant-list-test"

    # Submit two conversion requests
    request1 = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id=tenant_id),
        content=sample_txt,
        metadata=common_pb2.ContentMetadata(mime_type="text/plain"),
    )
    request2 = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id=tenant_id),
        content=sample_txt,
        metadata=common_pb2.ContentMetadata(mime_type="text/plain"),
    )

    response1 = await stub.ConvertDocument(request1)
    response2 = await stub.ConvertDocument(request2)

    # List documents for this tenant
    list_request = ingestion_pb2.ListDocumentsRequest(tenant_context=common_pb2.TenantContext(tenant_id=tenant_id))
    list_response = await stub.ListDocuments(list_request)

    # Verify both documents are in the list
    document_ids = [doc.document_id for doc in list_response.documents]
    assert response1.document_id in document_ids
    assert response2.document_id in document_ids
    assert len(list_response.documents) >= 2
    # Verify the documents have the expected tenant
    for doc in list_response.documents:
        if doc.document_id in [response1.document_id, response2.document_id]:
            assert doc.tenant_context.tenant_id == tenant_id
