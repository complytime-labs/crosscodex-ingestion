"""Integration tests: the layout-repair excerpts convert live to the pinned rejoin, drop and warning results.

The expected values are those of the captured layouts (tests/fixtures/layout, see its PROVENANCE.md), shared
through `expectations`. Live 1.5 output carries no layout tables, so only what it carries is compared.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc
from tests.fixtures.layout.expectations import CASES, DROPPED, TABLE_COUNT, TABLE_REJOIN_COUNT, WARNINGS

ARTIFACTS = os.environ.get("DOCLING_ARTIFACTS_PATH", "")
DOCUMENTS = Path(__file__).parent.parent / "fixtures" / "documents"
DRIFT = "Docling output drifted; re-capture tests/fixtures/layout with provenance"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ARTIFACTS or not Path(ARTIFACTS).is_dir(), reason="DOCLING_ARTIFACTS_PATH models directory not available"
    ),
]


async def _convert(channel: Any, case: str) -> dict[str, Any]:
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)
    tenant = common_pb2.TenantContext(tenant_id="tenant-layout-repair")
    submitted = await stub.ConvertDocument(
        ingestion_pb2.ConvertDocumentRequest(
            tenant_context=tenant,
            content=(DOCUMENTS / f"{case}.pdf").read_bytes(),
            metadata=common_pb2.ContentMetadata(mime_type="application/pdf"),
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
    output: dict[str, Any] = json.loads(Path(document.structured_json_uri.removeprefix("file://")).read_text())
    return output


@pytest.mark.parametrize("case", CASES)
async def test_excerpt_converts_to_the_pinned_layout_repair_results(grpc_server_and_channel, case):
    _server, channel = grpc_server_and_channel
    output = await _convert(channel, case)

    assert output["schema_version"] == "1.5"
    assert output["metadata"]["dropped"] == DROPPED[case], DRIFT
    structure = output["metadata"]["structure"]
    if case in TABLE_COUNT:  # pinned for the cases whose table count the fixtures record
        assert structure["table_count"] == TABLE_COUNT[case], DRIFT
    assert structure["table_rejoin_count"] == TABLE_REJOIN_COUNT[case], DRIFT
    assert structure["warnings"] == WARNINGS[case], DRIFT
