"""Integration tests for full document conversion pipeline.

These tests exercise the complete gRPC service with real Docling conversion.
They may be slow. When Docling is unusable the tests skip, except that they fail when
DOCLING_ARTIFACTS_PATH is set (Docling is then expected to work).
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


def _stored_nodes(final: ingestion_pb2.GetDocumentResponse) -> tuple[dict, list[dict]]:
    """Return the stored output and its structure nodes (the elements carrying an id)."""
    stored = json.loads(Path(final.document.structured_json_uri.removeprefix("file://")).read_text())
    return stored, [e for e in stored["elements"] if e.get("id") is not None]


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

    stored, nodes = _stored_nodes(final_response)
    assert stored["metadata"]["structure_method"] == "heading"
    assert [(n["id"], n["role"]) for n in nodes] == [("sec-1", "control")]


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
    stored, nodes = _stored_nodes(final_response)
    assert stored["metadata"]["structure_method"] == "pattern"
    assert [(n["id"], n["role"], n["page"]) for n in nodes] == [
        ("Section 1", "control", None),
        ("Section 2", "control", None),
        ("Section 3", "control", None),
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prose_only_text_fails_with_no_structure(grpc_server_and_channel):
    """CSV-looking text/plain is paragraphs (not a table), so the fallback structurer finds no controls."""
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

    assert final.document.conversion_status == common_pb2.JOB_STATUS_FAILED
    assert final.document.error.code == common_pb2.ERROR_CODE_INVALID_ARGUMENT
    assert final.document.error.message == (
        "No controls found in this txt document: it has no headings, tables or numbered lines the fallback "
        "structurer recognises. Convert it with a document mapping for txt; registered: none yet."
    )
    assert not final.document.structured_json_uri


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
async def test_convert_pdf_end_to_end(grpc_server_and_channel):
    """Convert a PDF with headings: COMPLETED, heading-tier structure, every node carrying its page.

    sample.pdf is prose, so it has no structure to convert; the NIST excerpt has real headings.
    """
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-pdf"),
        content=(Path(__file__).parent.parent / "fixtures" / "documents" / "nist-sp800-171r3-excerpt.pdf").read_bytes(),
        metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
    )

    convert_response = await stub.ConvertDocument(request)
    assert convert_response.document_id

    # PDF conversion may be slow; allow more time
    final_response = await poll_until_complete(stub, "tenant-pdf", convert_response.document_id, timeout_seconds=180)

    assert final_response.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED, (
        final_response.document.error.message
    )
    stored, nodes = _stored_nodes(final_response)
    assert stored["metadata"]["structure_method"] == "heading"
    assert nodes
    assert {n["role"] for n in nodes} <= {"control", "section"}
    assert all(isinstance(n["page"], int) and n["page"] >= 1 for n in nodes)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prose_only_pdf_fails_with_no_structure(grpc_server_and_channel, sample_pdf):
    """sample.pdf has no headings, tables or numbered lines: the conversion is refused, nothing is stored."""
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    convert_response = await stub.ConvertDocument(
        ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-prose-pdf"),
            content=sample_pdf,
            metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
        )
    )
    final = await poll_until_complete(stub, "tenant-prose-pdf", convert_response.document_id, timeout_seconds=180)

    assert final.document.conversion_status == common_pb2.JOB_STATUS_FAILED
    assert final.document.error.code == common_pb2.ERROR_CODE_INVALID_ARGUMENT
    assert final.document.error.message.startswith("No controls found in this pdf document: ")
    assert not final.document.structured_json_uri


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

    assert final_response.document.conversion_status == common_pb2.JOB_STATUS_COMPLETED, (
        final_response.document.error.message
    )
    stored, nodes = _stored_nodes(final_response)
    assert stored["metadata"]["structure_method"] == "heading"
    assert [(n["id"], n["role"]) for n in nodes] == [("sec-1", "control"), ("sec-2", "control")]


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
