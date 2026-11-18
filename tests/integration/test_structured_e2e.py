"""Integration tests: structured sources through the real gRPC server (mutual TLS, attestation on).

The structured worker imports no Docling, so these need no models. They submit the publishers'
trimmed releases with the registered mappings and check the stored output against the golden
files, the attestation, and the errors a client sees.
"""

import asyncio
import io
import json
import zipfile
from pathlib import Path

import grpc
import pytest

from crosscodex_ingestion.mapping import load_registry
from crosscodex_ingestion.output_schema import SCHEMA_VERSION, validate_output
from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures"
REGISTRY = load_registry("")
TENANT = "tenant-structured"
GOLDEN = [
    ("owasp-asvs-5.0.0", "input.json", "owasp-asvs-5.0.0-json"),
    ("owasp-asvs-5.0.0-csv", "input.csv", "owasp-asvs-5.0.0-csv"),
    ("mitre-attack-enterprise-17.1", "input.xlsx", "mitre-attack-enterprise-17.1-techniques-xlsx"),
    ("nist-sp800-53r5", "input.xlsx", "nist-sp800-53r5-catalog-xlsx"),
    ("nist-csf-2.0", "input.xlsx", "nist-csf-2.0-core-xlsx"),
]


async def _convert(
    channel: grpc.aio.Channel, content: bytes, mapping: str = "", mime_type: str = ""
) -> ingestion_pb2.Document:
    """Submit *content* and poll until the conversion is COMPLETED or FAILED."""
    stub = ingestion_pb2_grpc.IngestionServiceStub(channel)
    response = await stub.ConvertDocument(
        ingestion_pb2.ConvertDocumentRequest(
            tenant_context=common_pb2.TenantContext(tenant_id=TENANT),
            content=content,
            metadata=common_pb2.ContentMetadata(mime_type=mime_type),
            mapping=mapping,
        )
    )
    request = ingestion_pb2.GetDocumentRequest(
        tenant_context=common_pb2.TenantContext(tenant_id=TENANT), document_id=response.document_id
    )
    for _ in range(240):
        document = (await stub.GetDocument(request)).document
        if document.conversion_status in (common_pb2.JOB_STATUS_COMPLETED, common_pb2.JOB_STATUS_FAILED):
            return document
        await asyncio.sleep(0.25)
    raise TimeoutError(f"document {response.document_id} did not finish")


@pytest.mark.parametrize(("directory", "name", "mapping_id"), GOLDEN)
async def test_release_converts_to_the_golden_output_and_is_attested(
    grpc_server_and_channel, directory, name, mapping_id
):
    _, channel = grpc_server_and_channel
    mapping = REGISTRY.get(mapping_id)

    document = await _convert(channel, (FIXTURES / "structured" / directory / name).read_bytes(), mapping_id)

    assert document.conversion_status == common_pb2.JOB_STATUS_COMPLETED, document.error.message
    stored_path = Path(document.structured_json_uri.removeprefix("file://"))
    stored = json.loads(stored_path.read_text())
    validate_output(stored)
    expected = json.loads((FIXTURES / "structured" / directory / "expected.json").read_text())
    assert stored["schema_version"] == SCHEMA_VERSION
    assert stored["elements"] == expected["elements"]
    del stored["metadata"]["processing_time_ms"]
    assert stored["metadata"] == expected["metadata"]

    link = json.loads((stored_path.parent / "attestation.link").read_text())
    byproducts = link["signed"]["byproducts"]
    assert (byproducts["mapping_id"], byproducts["mapping_sha256"]) == (mapping.id, mapping.sha256)


async def test_source_problems_reach_the_client(grpc_server_and_channel):
    _, channel = grpc_server_and_channel
    source = b'{"Requirements": [{"Shortcode": "V1", "Name": "C", "Items": [{"Description": "x"}]}]}'

    document = await _convert(channel, source, "owasp-asvs-5.0.0-json")

    assert document.conversion_status == common_pb2.JOB_STATUS_FAILED
    assert document.error.code == common_pb2.ERROR_CODE_INVALID_ARGUMENT
    problem = "Node at Requirements[0].Items[0] has no ID: field 'Shortcode' is missing or blank."
    assert document.error.message == f"Problem in source (mapping owasp-asvs-5.0.0-json): {problem}"
    assert dict(document.error.details) == {
        "mapping": "owasp-asvs-5.0.0-json",
        "count": "1",
        "problem.1": problem,
        "location.1": "Requirements[0].Items[0]",
    }


async def test_unknown_mapping_is_rejected_at_submission(grpc_server_and_channel):
    _, channel = grpc_server_and_channel

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await _convert(channel, b"{}", "owasp-asvs-4.0.3-json")

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Registered mappings: " in exc_info.value.details()


async def test_json_without_a_mapping_is_refused_with_the_registered_ids(grpc_server_and_channel):
    _, channel = grpc_server_and_channel

    document = await _convert(channel, b'{"Requirements": []}', mime_type="application/json")

    assert document.error.code == common_pb2.ERROR_CODE_INVALID_ARGUMENT
    assert "set ConvertDocumentRequest.mapping" in document.error.message
    assert "owasp-asvs-5.0.0-json" in document.error.message


async def test_docx_renamed_xlsx_is_refused(grpc_server_and_channel, sample_docx):
    _, channel = grpc_server_and_channel
    mapping_id = "mitre-attack-enterprise-17.1-techniques-xlsx"

    document = await _convert(channel, sample_docx, mapping_id)

    assert document.error.code == common_pb2.ERROR_CODE_INVALID_ARGUMENT
    assert document.error.message == f"Mapping '{mapping_id}' reads xlsx, but the input is docx."


async def test_zip_bomb_is_refused_by_the_worker(grpc_server_and_channel):
    """Built at test time, never committed: 50 MB of zeros deflates to about 50 KB."""
    _, channel = grpc_server_and_channel
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", b"<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", b"\0" * 50_000_000)

    document = await _convert(channel, buffer.getvalue(), "mitre-attack-enterprise-17.1-techniques-xlsx")

    assert document.error.code == common_pb2.ERROR_CODE_RESOURCE_EXHAUSTED
    assert "MAX_XLSX_COMPRESSION_RATIO" in document.error.message
