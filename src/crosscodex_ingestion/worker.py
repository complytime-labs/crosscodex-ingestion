"""Worker module — orchestrates the document conversion pipeline.

This module provides the core run_conversion function that coordinates:
1. Input resolution (fetch URL or use provided bytes)
2. Format validation (structured sources: confirmed against the request's mapping)
3. Content hash verification
4. Conversion via Docling, or via the structured worker for a structured source
5. Storage of structured output
6. Cryptographic attestation
7. Audit event publishing
8. State management (PENDING → RUNNING → COMPLETED/FAILED)
"""

import asyncio
import hashlib
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from opentelemetry import trace

from crosscodex_ingestion.attestation import AttestationManager
from crosscodex_ingestion.audit import AuditPublisher, AuditUnavailableError
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import (
    ConversionError,
    ConversionTimeout,
    DocumentLimitError,
    convert_document,
    convert_structured,
)
from crosscodex_ingestion.html_profile import HtmlProfileRegistry
from crosscodex_ingestion.mapping import Mapping, MappingRegistry
from crosscodex_ingestion.metrics import (
    ACTIVE_CONVERSIONS,
    DURATION,
    ERRORS_TOTAL,
    REQUESTS_TOTAL,
    STRUCTURE_WARNINGS,
)
from crosscodex_ingestion.output_schema import SCHEMA_VERSION, OutputSchemaError, Violation, validate_output
from crosscodex_ingestion.quality import measure_structure
from crosscodex_ingestion.security import (
    STRUCTURED_FORMATS,
    confirm_structured_format,
    fetch_url,
    sniff_format,
    validate_content_hash,
    validate_format,
    validate_url,
)
from crosscodex_ingestion.state import StateBackend
from crosscodex_ingestion.storage import StorageBackend
from crosscodex_ingestion.structured import StructuredSourceError

logger = logging.getLogger(__name__)

# Get tracer for this module
tracer = trace.get_tracer("crosscodex-ingestion")

# Job status constants (from proto/crosscodex/v1/common.proto)
JOB_STATUS_PENDING = 1
JOB_STATUS_RUNNING = 2
JOB_STATUS_COMPLETED = 3
JOB_STATUS_FAILED = 4

# Error code constants (from proto/crosscodex/v1/common.proto)
ERROR_CODE_INVALID_ARGUMENT = 1
ERROR_CODE_RESOURCE_EXHAUSTED = 5
ERROR_CODE_INTERNAL = 6
ERROR_CODE_UNAVAILABLE = 7

# Retryable failure recorded for documents interrupted by service shutdown.
SHUTDOWN_ERROR = {
    "code": ERROR_CODE_UNAVAILABLE,
    "message": "The service shut down during conversion; resubmit the document.",
}


class MappingSkewError(Exception):
    """The request's mapping is not registered on this worker with the hash the service validated.

    Only possible when the service and the worker are different deployments (distributed mode, or a
    rollout in progress); the conversion fails as retryable rather than using another mapping.
    """


# Codes the converter may report in conversion_warnings. The converter subprocess is untrusted and the code becomes
# a Prometheus label, so anything else is rejected rather than counted.
CONVERSION_WARNING_CODES = frozenset({"html_profile_ambiguous"})


def _check_conversion_warnings(warnings: object) -> None:
    """Raise OutputSchemaError unless *warnings* is a list of known warning codes; values are never quoted."""
    if not isinstance(warnings, list):
        raise OutputSchemaError([Violation("conversion_warnings", "is not a list")])
    violations = [
        Violation(f"conversion_warnings[{index}]", "is not a known warning code")
        for index, code in enumerate(warnings)
        if not (isinstance(code, str) and code in CONVERSION_WARNING_CODES)
    ]
    if violations:
        raise OutputSchemaError(violations)


def _fetch_source(source_uri: str, settings: IngestionSettings, expected_format: str | None) -> tuple[bytes, str]:
    """Validate and download *source_uri*; return (bytes, detected_format).

    *expected_format* is a mapping's format: the download is confirmed against it afterwards.
    Blocking (DNS, HTTP); call via asyncio.to_thread.
    """
    validate_url(source_uri)
    with fetch_url(
        source_uri,
        max_bytes=settings.max_url_bytes,
        timeout=settings.url_timeout,
        max_redirects=settings.max_redirects,
        expected_format=expected_format,
    ) as (file_path, file_format):
        return Path(file_path).read_bytes(), file_format


def _request_mapping(metadata: dict, mappings: MappingRegistry) -> Mapping | None:
    """The mapping the request named (metadata mapping_id), checked against the hash the service recorded."""
    mapping_id = metadata.get("mapping_id")
    if not mapping_id:
        return None
    mapping = mappings.get(mapping_id)
    if mapping is None or mapping.sha256 != metadata.get("mapping_sha256"):
        found = "is not registered" if mapping is None else "has different content"
        raise MappingSkewError(
            f"Mapping {mapping_id!r} {found} on the worker that ran this conversion (the service and the worker run "
            "different mapping sets, e.g. during a deployment); resubmit the document."
        )
    return mapping


def _check_format(
    content: bytes,
    mapping: Mapping | None,
    detected_format: str,
    allowed_formats: set[str],
    mime_type: str,
    source_uri: str,
    mappings: MappingRegistry,
) -> str:
    """The validated format: the mapping's, confirmed by the bytes; or the sniffed one, which must not need a mapping.

    Blocking (reads the whole input); call via asyncio.to_thread.
    """
    if mapping:
        validated = confirm_structured_format(content, mapping.id, mapping.format)
        if validated not in allowed_formats:
            raise ValueError(f"Detected format {validated!r} is not in allowed formats: {sorted(allowed_formats)}")
        return validated
    validated = validate_format(content, detected_format, allowed_formats, mime_type, source_uri)
    if validated in STRUCTURED_FORMATS:
        raise _missing_mapping(validated, mappings)
    return validated


def _missing_mapping(detected_format: str, mappings: MappingRegistry) -> ValueError:
    registered = mappings.ids()
    known = (
        f"Registered mappings: {', '.join(registered)}."
        if registered
        else 'No mappings are registered on this server; an operator must add one (README "Structured sources").'
    )
    return ValueError(
        f"The input is {detected_format}, which is read through a mapping: set ConvertDocumentRequest.mapping to the "
        f"ID of a registered mapping. {known}"
    )


async def record_failure(
    state: StateBackend,
    audit: AuditPublisher,
    tenant_id: str,
    document_id: str,
    metadata: dict,
    error: dict,
    error_type: str,
    document_format: str,
    audit_error: dict | None = None,
) -> None:
    """Mark a document FAILED with *error*, count it, and publish the audit event.

    *audit_error*, when given, is published instead of *error*: the same failure without the
    caller's data, which only the caller gets (through GetDocument).
    """
    ERRORS_TOTAL.labels(error_type=error_type).inc()
    REQUESTS_TOTAL.labels(format=document_format, status="failed").inc()

    await state.update(
        tenant_id,
        document_id,
        status=JOB_STATUS_FAILED,
        error=error,
    )

    try:
        await audit.publish(
            tenant_id=tenant_id,
            event={
                "document_id": document_id,
                "tenant_id": tenant_id,
                "status": "failed",
                "error": audit_error or error,
            },
            trace_id=metadata.get("correlation_id", ""),
            span_id="",
        )
    except AuditUnavailableError as exc:
        # The FAILED state above is what callers see; a lost audit event must not also fail the task.
        logger.warning("Document %s marked FAILED, but its audit event was not recorded: %s", document_id, exc)


async def run_conversion(
    document_id: str,
    tenant_id: str,
    content: bytes | None,
    source_uri: str | None,
    metadata: dict,
    settings: IngestionSettings,
    state: StateBackend,
    storage: StorageBackend,
    attestation: AttestationManager | None,
    audit: AuditPublisher,
    mappings: MappingRegistry,
    html_profiles: HtmlProfileRegistry,
) -> None:
    """Run the document conversion pipeline.

    This function orchestrates the entire conversion workflow:
    1. Updates state to RUNNING
    2. Resolves input (fetch URL or use provided bytes)
    3. Validates format against allowed formats (with a mapping: confirms the mapping's format)
    4. Validates content hash (if provided in metadata)
    5. Writes bytes to temp file
    6. Calls convert_document, or convert_structured with the request's mapping (subprocess isolation),
       and validates its output against the schema
    7. Uploads structured JSON to storage
    8. Creates cryptographic attestation link (if enabled)
    9. Uploads attestation to storage (if enabled)
    10. Publishes audit event
    11. Updates state to COMPLETED

    On any error, updates state to FAILED with error details.

    Parameters
    ----------
    document_id : str
        Unique document identifier.
    tenant_id : str
        Tenant identifier for isolation.
    content : bytes | None
        Raw document bytes (if provided directly).
    source_uri : str | None
        URL to fetch document from (if not provided as bytes).
    metadata : dict
        Document metadata (mime_type, sha256, etc.).
    settings : IngestionSettings
        Configuration settings.
    state : StateBackend
        State management backend.
    storage : StorageBackend
        Object storage backend.
    attestation : AttestationManager | None
        Attestation manager for cryptographic signing (optional).
    audit : AuditPublisher
        Audit event publisher.
    mappings : MappingRegistry
        Mappings registered on this worker; metadata["mapping_id"] and ["mapping_sha256"] name the
        request's mapping, as validated by the service.
    html_profiles : HtmlProfileRegistry
        HTML profiles registered on this worker; the Docling worker applies the one an HTML document matches.

    Returns
    -------
    None
        Updates state directly; does not return a value.
    """
    temp_file_path: Path | None = None
    start_time = time.time()
    validated_format = "unknown"

    # Track active conversions
    ACTIVE_CONVERSIONS.inc()

    try:
        # Step 1: Update state to RUNNING
        logger.info("Starting conversion for document %s (tenant %s)", document_id, tenant_id)
        await state.update(tenant_id, document_id, status=JOB_STATUS_RUNNING)

        mapping = _request_mapping(metadata, mappings)

        # Step 2: Resolve input
        input_bytes: bytes
        detected_format: str

        if source_uri:
            with tracer.start_as_current_span("ingestion.fetch_url") as span:
                span.set_attribute("document.id", document_id)
                span.set_attribute("tenant.id", tenant_id)
                span.set_attribute("source.uri", source_uri)

                logger.info("Fetching document from URL: %s", source_uri)
                input_bytes, detected_format = await asyncio.to_thread(
                    _fetch_source, source_uri, settings, mapping.format if mapping else None
                )
                span.set_attribute("document.size_bytes", len(input_bytes))
                span.set_attribute("document.format", detected_format)

        elif content:
            # Use provided bytes
            input_bytes = content

            # Sniff format from bytes; a mapping's format is confirmed below instead.
            mime_type = metadata.get("mime_type", "")
            source_uri_fallback = metadata.get("source_uri", "")
            detected_format = (
                mapping.format
                if mapping
                else await asyncio.to_thread(sniff_format, input_bytes, mime_type, source_uri_fallback)
            )

        else:
            raise ValueError("Either content or source_uri must be provided")

        # Step 3: Validate format
        with tracer.start_as_current_span("ingestion.validate") as span:
            span.set_attribute("document.id", document_id)
            span.set_attribute("tenant.id", tenant_id)
            span.set_attribute("document.format", detected_format)
            span.set_attribute("document.size_bytes", len(input_bytes))

            logger.info("Validating format for document %s", document_id)
            allowed_formats_set = set(settings.allowed_formats_set)
            mime_type = metadata.get("mime_type", "")
            source_uri_for_validation = source_uri or metadata.get("source_uri", "")

            # Reads the untrusted bytes in this (unsandboxed) process: keep it off the event loop.
            validated_format = await asyncio.to_thread(
                _check_format,
                input_bytes,
                mapping,
                detected_format,
                allowed_formats_set,
                mime_type,
                source_uri_for_validation,
                mappings,
            )
            span.set_attribute("document.validated_format", validated_format)

        # Step 4: Validate content hash (if provided)
        if metadata.get("sha256"):
            logger.info("Validating content hash for document %s", document_id)
            validate_content_hash(input_bytes, metadata["sha256"])

        # Step 5: Write bytes to temp file
        logger.info("Writing input to temp file for document %s", document_id)
        with tempfile.NamedTemporaryFile(
            suffix=f".{validated_format}",
            delete=False,
            prefix=f"crosscodex_{document_id}_",
        ) as temp_file:
            temp_file_path = Path(temp_file.name)
            temp_file.write(input_bytes)

        # Step 6: Convert document
        with tracer.start_as_current_span("ingestion.convert") as span:
            span.set_attribute("document.id", document_id)
            span.set_attribute("tenant.id", tenant_id)
            span.set_attribute("document.format", validated_format)
            span.set_attribute("document.size_bytes", len(input_bytes))

            logger.info("Converting document %s (format: %s)", document_id, validated_format)
            # Awaits the worker subprocess; cancelling this task kills the subprocess.
            conversion_result: dict[str, Any]
            if mapping:
                span.set_attribute("mapping.id", mapping.id)
                conversion_result = await convert_structured(str(temp_file_path), mapping, settings)
            else:
                conversion_result = await convert_document(
                    str(temp_file_path), validated_format, settings, html_profiles
                )

        # conversion_warnings is not part of the output schema; it is checked here and folded into metadata.structure.
        conversion_warnings = conversion_result.pop("conversion_warnings", [])
        # The output schema is the contract with consumers: nothing that breaks it is stored.
        # The result is untrusted, so it is validated before anything walks it.
        validate_output(conversion_result)
        _check_conversion_warnings(conversion_warnings)
        structure = measure_structure(conversion_result["elements"])
        structure["warnings"].extend(conversion_warnings)
        conversion_result["metadata"]["structure"] = structure
        for dropped in conversion_result["metadata"]["dropped"]:
            logger.warning(
                "Document %s: dropped a %s on page %s: %s",
                document_id,
                dropped["type"],
                dropped["page"],
                dropped["reason"],
            )
        for code in structure["warnings"]:
            logger.warning(
                "Document %s: structure warning %s (%d elements)",
                document_id,
                code,
                len(conversion_result["elements"]),
            )
            STRUCTURE_WARNINGS.labels(format=validated_format, warning=code).inc()

        # Step 7: Upload structured JSON to storage
        with tracer.start_as_current_span("ingestion.upload") as span:
            span.set_attribute("document.id", document_id)
            span.set_attribute("tenant.id", tenant_id)

            logger.info("Uploading structured JSON for document %s", document_id)
            structured_json = json.dumps(conversion_result, separators=(",", ":"))
            structured_json_bytes = structured_json.encode("utf-8")
            span.set_attribute("output.size_bytes", len(structured_json_bytes))

            # Network I/O for S3; keep it off the event loop.
            structured_json_uri = await asyncio.to_thread(
                storage.upload,
                tenant_id,
                document_id,
                "structured.json",
                structured_json_bytes,
            )

        # Step 8: Create attestation link (if enabled)
        attestation_uri = ""
        if attestation:
            with tracer.start_as_current_span("ingestion.attest") as span:
                span.set_attribute("document.id", document_id)
                span.set_attribute("tenant.id", tenant_id)

                logger.info("Creating attestation link for document %s", document_id)

                # Compute hashes
                input_hash = hashlib.sha256(input_bytes).hexdigest()
                output_hash = hashlib.sha256(structured_json_bytes).hexdigest()

                # Create link
                materials = {
                    "input": {
                        "sha256": input_hash,
                    }
                }
                products = {
                    "structured_json": {
                        "sha256": output_hash,
                    }
                }
                # The submitting caller is recorded on the state record at submission (also in
                # distributed mode, where the record lives in the shared state backend).
                submitted = await state.get(tenant_id, document_id)
                byproducts = {
                    "document_id": document_id,
                    "tenant_id": tenant_id,
                    "format": validated_format,
                    "created_by": submitted.audit.get("created_by", "unknown") if submitted else "unknown",
                }
                if mapping:
                    byproducts["mapping_id"] = mapping.id
                    byproducts["mapping_sha256"] = mapping.sha256

                link = attestation.create_link(
                    step_name="convert-document",
                    materials=materials,
                    products=products,
                    byproducts=byproducts,
                )

                # Upload attestation link to storage
                logger.info("Uploading attestation link for document %s", document_id)
                link_bytes = json.dumps(link, separators=(",", ":")).encode("utf-8")
                attestation_uri = await asyncio.to_thread(
                    storage.upload,
                    tenant_id,
                    document_id,
                    "attestation.link",
                    link_bytes,
                )

        # Step 9: Publish audit event
        with tracer.start_as_current_span("ingestion.audit") as span:
            span.set_attribute("document.id", document_id)
            span.set_attribute("tenant.id", tenant_id)

            logger.info("Publishing audit event for document %s", document_id)
            audit_event = {
                "document_id": document_id,
                "tenant_id": tenant_id,
                "status": "completed",
                "structured_json_uri": structured_json_uri,
            }
            if attestation_uri:
                audit_event["attestation_uri"] = attestation_uri

            await audit.publish(
                tenant_id=tenant_id,
                event=audit_event,
                trace_id=metadata.get("correlation_id", ""),
                span_id="",
            )

        # Step 10: Update state to COMPLETED
        logger.info("Marking document %s as completed", document_id)
        await state.update(
            tenant_id,
            document_id,
            status=JOB_STATUS_COMPLETED,
            structured_json_uri=structured_json_uri,
            attestation_uri=attestation_uri,
        )

        # Record metrics for successful conversion
        duration = time.time() - start_time
        DURATION.labels(format=validated_format).observe(duration)
        REQUESTS_TOTAL.labels(format=validated_format, status="completed").inc()

    except Exception as exc:
        # Handle all errors by updating state to FAILED. Problems and some limit messages of a
        # structured source quote the caller's data, so the log (like the audit event below) gets
        # them without source values.
        if isinstance(exc, StructuredSourceError):
            logger.warning("Conversion failed for document %s: %s", document_id, exc.redacted()[0])
        elif isinstance(exc, DocumentLimitError):
            logger.warning("Conversion failed for document %s: %s", document_id, exc.redacted_message)
        else:
            logger.exception("Conversion failed for document %s", document_id)

        # Client-visible messages: specific only for failures the caller caused and can fix
        # (ValueError family, our own timeout text); generic for everything else so internals
        # (paths, tracebacks, hostnames) never leak (CWE-209). Full detail is logged above.
        error_code = ERROR_CODE_INTERNAL
        error_message = (
            f"Document conversion failed due to an internal error (document ID {document_id}); contact the operator."
        )
        error_type = "internal"

        if isinstance(exc, DocumentLimitError):
            error_code = ERROR_CODE_RESOURCE_EXHAUSTED
            error_message = str(exc)
            error_type = "limit_exceeded"
        elif isinstance(exc, ValueError):
            # Format validation, SSRF, hash mismatch
            error_code = ERROR_CODE_INVALID_ARGUMENT
            error_message = str(exc)
            error_type = "invalid_argument"
        elif isinstance(exc, MappingSkewError):
            error_code = ERROR_CODE_UNAVAILABLE
            error_message = str(exc)
            error_type = "mapping_skew"
        elif isinstance(exc, ConversionTimeout):
            error_code = ERROR_CODE_RESOURCE_EXHAUSTED
            error_message = str(exc)
            error_type = "timeout"
        elif isinstance(exc, ConversionError):
            error_type = "conversion_error"
        elif isinstance(exc, OutputSchemaError):
            # Still INTERNAL (the service's output is wrong, not the request), but the message names
            # locations and rules only, never document text, so it is safe to return in full.
            error_message = f"{exc} (document ID {document_id}); contact the operator."
            error_type = "invalid_output"

        error_dict: dict[str, Any] = {
            "code": error_code,
            "message": error_message,
        }
        if isinstance(exc, OutputSchemaError):
            first = exc.violations[0]
            details = {"schema_version": SCHEMA_VERSION, "location": first.location}
            if first.element_index is not None:
                details["element_index"] = str(first.element_index)
            if first.element_id is not None:
                details["element_id"] = first.element_id
            error_dict["details"] = details
        audit_error = None
        if isinstance(exc, StructuredSourceError):
            error_dict["details"] = exc.details
            redacted_message, redacted_details = exc.redacted()
            audit_error = {"code": error_code, "message": redacted_message, "details": redacted_details}
        elif isinstance(exc, DocumentLimitError):
            audit_error = {"code": error_code, "message": exc.redacted_message}

        await record_failure(
            state, audit, tenant_id, document_id, metadata, error_dict, error_type, validated_format, audit_error
        )

    except asyncio.CancelledError:
        # Only dispatch shutdown cancels conversions (after its grace period expires).
        logger.warning("Conversion of document %s cancelled by shutdown", document_id)
        await record_failure(
            state, audit, tenant_id, document_id, metadata, SHUTDOWN_ERROR, "shutdown", validated_format
        )
        raise

    finally:
        # Decrement active conversions
        ACTIVE_CONVERSIONS.dec()

        # Step 11: Clean up temp files
        if temp_file_path and temp_file_path.exists():
            logger.debug("Cleaning up temp file: %s", temp_file_path)
            temp_file_path.unlink(missing_ok=True)
