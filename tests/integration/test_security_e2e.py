"""Integration tests for security validation and rejection paths."""

import grpc
import pytest

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_tenant_id_is_rejected(grpc_server_and_channel):
    """ConvertDocument with an empty tenant_id is refused before anything is stored."""
    server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id=""),
        content=b"valid content",
        metadata=common_pb2.ContentMetadata(mime_type="text/plain"),
    )

    with pytest.raises(grpc.aio.AioRpcError) as exc:
        await stub.ConvertDocument(request)
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "tenant_id" in exc.value.details()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_larger_than_grpc_default_message_size_is_accepted(grpc_server_and_channel):
    """Real documents exceed gRPC's 4 MB default (NIST SP 800-53r5 is a 6 MB PDF)."""
    server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
        # A type the worker rejects at once, so teardown does not wait on a real conversion.
        content=b"x" * (6 * 1024 * 1024),
        metadata=common_pb2.ContentMetadata(mime_type="application/octet-stream"),
    )

    response = await stub.ConvertDocument(request)
    assert response.status == common_pb2.JOB_STATUS_PENDING


@pytest.mark.integration
@pytest.mark.asyncio
async def test_oversized_document_returns_resource_exhausted(grpc_server_and_channel):
    """Content over max_document_size reaches the service and gets its actionable error."""
    server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)
    max_size = IngestionSettings().max_document_size

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-a"),
        content=b"x" * (max_size + 1),
        metadata=common_pb2.ContentMetadata(mime_type="text/plain"),
    )

    with pytest.raises(grpc.aio.AioRpcError) as exc:
        await stub.ConvertDocument(request)
    assert exc.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert f"exceeds maximum {max_size}" in exc.value.details()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unsupported_format_results_in_failed_status(grpc_server_and_channel):
    """ConvertDocument with unsupported MIME type results in FAILED status.

    Format validation happens in the worker, so the request is accepted
    but the document reaches FAILED status with an error message.
    """
    server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-unsupported"),
        content=b"fake executable",
        metadata=common_pb2.ContentMetadata(mime_type="application/x-executable"),
    )

    # The request is accepted
    response = await stub.ConvertDocument(request)
    assert response.document_id
    assert response.status == common_pb2.JOB_STATUS_PENDING

    # Poll until it reaches FAILED status
    import asyncio

    for _ in range(20):
        get_request = ingestion_pb2.GetDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id="tenant-unsupported"), document_id=response.document_id
        )
        get_response = await stub.GetDocument(get_request)

        if get_response.document.conversion_status == common_pb2.JOB_STATUS_FAILED:
            assert (
                "cannot determine file type" in get_response.document.error.message.lower()
                or "unsupported" in get_response.document.error.message.lower()
            )
            return

        await asyncio.sleep(0.5)

    pytest.fail("Document did not reach FAILED status within timeout")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_content_and_source_uri_is_rejected(grpc_server_and_channel):
    """ConvertDocument with neither content nor source_uri is refused at the RPC."""
    server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    request = ingestion_pb2.ConvertDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id="tenant-missing-content"),
        metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
    )

    with pytest.raises(grpc.aio.AioRpcError) as exc:
        await stub.ConvertDocument(request)
    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
