"""Integration tests: the two real sources convert through the service to the Go structurer's golden output.

The goldens (tests/fixtures/fallback) were captured from the Go fallback structurer on the layout of
these same files. Real sources carry `page`; the goldens do not, so nodes are compared without it.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc
from tests.unit.fallback_golden import expected_nodes, load_case

ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH", "")
DOCUMENTS = Path(__file__).parent.parent / "fixtures" / "documents"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ARTIFACTS or not Path(ARTIFACTS).is_dir(), reason="DOCLING_ARTIFACTS_PATH models directory not available"
    ),
]


def _nodes(output: dict[str, Any]) -> list[dict[str, Any]]:
    """The structure nodes of converted output, shaped like `expected_nodes`: the elements carrying an id.

    A node's text is also emitted as an id-less paragraph child, which is not a node. A node's parent is
    the id of the node its element's parent_id names.
    """
    id_of = {e["element_id"]: e.get("id") for e in output["elements"]}
    return [
        {
            "id": e["id"],
            "title": e["title"],
            "text": e["text"],
            "parent": id_of[e["parent_id"]] if e["parent_id"] else None,
            "role": e["role"],
            "properties": e["properties"],
        }
        for e in output["elements"]
        if e.get("id") is not None
    ]


@pytest.mark.parametrize(
    ("case", "fixture", "mime_type", "method"),
    [
        ("nist-sp800-171r3-excerpt", "nist-sp800-171r3-excerpt.pdf", "application/pdf", "heading"),
        ("eurlex-nis2-32022L2555", "eurlex-nis2-32022L2555.html", "text/html", "heading"),
    ],
)
async def test_real_source_converts_to_the_go_golden_nodes(grpc_server_and_channel, case, fixture, mime_type, method):
    _server, channel = grpc_server_and_channel
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)
    tenant = common_pb2.TenantContext(tenant_id="tenant-parity")

    submitted = await stub.ConvertDocument(
        ingestion_pb2.ConvertDocumentRequest(
            tenant_context=tenant,
            content=(DOCUMENTS / fixture).read_bytes(),
            metadata=common_pb2.ContentMetadata(mime_type=mime_type),
        )
    )
    document = None
    for _ in range(480):
        document = (
            await stub.GetDocument(
                ingestion_pb2.GetDocumentRequest(tenant_context=tenant, document_id=submitted.document_id)
            )
        ).document
        if document.conversion_status in (common_pb2.JOB_STATUS_COMPLETED, common_pb2.JOB_STATUS_FAILED):
            break
        await asyncio.sleep(0.5)

    assert document is not None
    assert document.conversion_status == common_pb2.JOB_STATUS_COMPLETED, document.error.message
    output = json.loads(Path(document.structured_json_uri.removeprefix("file://")).read_text())
    _elements, golden = load_case(case)
    assert output["metadata"]["structure_method"] == method
    assert _nodes(output) == expected_nodes(golden)
