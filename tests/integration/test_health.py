"""Integration tests for gRPC server health and connectivity."""

import pytest
from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grpc_server_is_reachable(grpc_server_and_channel):
    """Verify that the gRPC server starts and accepts connections."""
    server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)

    # Verify we can make a simple RPC call
    # ListDocuments with no documents should return empty list
    request = ingestion_pb2.ListDocumentsRequest(tenant_context=common_pb2.TenantContext(tenant_id="health-check"))
    response = await stub.ListDocuments(request)

    assert response is not None
    assert response.documents == []
