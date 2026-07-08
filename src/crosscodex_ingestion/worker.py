"""Worker module — orchestrates the document conversion pipeline.

This module provides the core run_conversion function that coordinates:
1. Input resolution (fetch URL or use provided bytes)
2. Format validation
3. Content hash verification
4. Document conversion via Docling
5. Storage of structured output
6. Cryptographic attestation
7. Audit event publishing
8. State management (PENDING → RUNNING → COMPLETED/FAILED)
"""

import hashlib
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from opentelemetry import trace

from crosscodex_ingestion.attestation import AttestationManager
from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import ConversionError, ConversionTimeout, convert_document
from crosscodex_ingestion.metrics import ACTIVE_CONVERSIONS, DURATION, ERRORS_TOTAL, REQUESTS_TOTAL
from crosscodex_ingestion.security import (
    fetch_url,
    sniff_format,
    validate_content_hash,
    validate_format,
    validate_url,
)
from crosscodex_ingestion.state import StateBackend
from crosscodex_ingestion.storage import StorageBackend

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
) -> None:
    """Run the document conversion pipeline.

    This function orchestrates the entire conversion workflow:
    1. Updates state to RUNNING
    2. Resolves input (fetch URL or use provided bytes)
    3. Validates format against allowed formats
    4. Validates content hash (if provided in metadata)
    5. Writes bytes to temp file
    6. Calls convert_document (subprocess isolation)
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
        state.update(tenant_id, document_id, status=JOB_STATUS_RUNNING)

        # Step 2: Resolve input
        input_bytes: bytes
        detected_format: str

        if source_uri:
            with tracer.start_as_current_span("ingestion.fetch_url") as span:
                span.set_attribute("document.id", document_id)
                span.set_attribute("tenant.id", tenant_id)
                span.set_attribute("source.uri", source_uri)

                # Validate URL first
                validate_url(source_uri)

                # Fetch from URL
                logger.info("Fetching document from URL: %s", source_uri)
                with fetch_url(
                    source_uri,
                    max_bytes=settings.max_url_bytes,
                    timeout=settings.url_timeout,
                ) as (file_path, file_format):
                    # Read the fetched file
                    input_bytes = Path(file_path).read_bytes()
                    detected_format = file_format
                    span.set_attribute("document.size_bytes", len(input_bytes))
                    span.set_attribute("document.format", file_format)

        elif content:
            # Use provided bytes
            input_bytes = content

            # Sniff format from bytes
            mime_type = metadata.get("mime_type", "")
            source_uri_fallback = metadata.get("source_uri", "")
            detected_format = sniff_format(input_bytes, mime_type, source_uri_fallback)

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

            validated_format = validate_format(
                input_bytes,
                detected_format,
                allowed_formats_set,
                mime_type,
                source_uri_for_validation,
            )
            span.set_attribute("document.validated_format", validated_format)

        # Step 4: Validate content hash (if provided)
        if "sha256" in metadata and metadata["sha256"]:
            logger.info("Validating content hash for document %s", document_id)
            validate_content_hash(input_bytes, metadata["sha256"])

        # Step 5: Write bytes to temp file
        logger.info("Writing input to temp file for document %s", document_id)
        temp_file = tempfile.NamedTemporaryFile(
            suffix=f".{validated_format}",
            delete=False,
            prefix=f"crosscodex_{document_id}_",
        )
        temp_file_path = Path(temp_file.name)
        temp_file.write(input_bytes)
        temp_file.close()

        # Step 6: Convert document
        with tracer.start_as_current_span("ingestion.convert") as span:
            span.set_attribute("document.id", document_id)
            span.set_attribute("tenant.id", tenant_id)
            span.set_attribute("document.format", validated_format)
            span.set_attribute("document.size_bytes", len(input_bytes))

            logger.info("Converting document %s (format: %s)", document_id, validated_format)
            conversion_result: dict[str, Any] = convert_document(
                str(temp_file_path),
                validated_format,
                settings,
            )

        # Step 7: Upload structured JSON to storage
        with tracer.start_as_current_span("ingestion.upload") as span:
            span.set_attribute("document.id", document_id)
            span.set_attribute("tenant.id", tenant_id)

            logger.info("Uploading structured JSON for document %s", document_id)
            structured_json = json.dumps(conversion_result, separators=(",", ":"))
            structured_json_bytes = structured_json.encode("utf-8")
            span.set_attribute("output.size_bytes", len(structured_json_bytes))

            structured_json_uri = storage.upload(
                tenant_id,
                document_id,
                "structured.json",
                structured_json_bytes,
            )

        # Step 8: Create attestation link (if enabled)
        attestation_uri = None
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
                byproducts = {
                    "document_id": document_id,
                    "tenant_id": tenant_id,
                    "format": validated_format,
                }

                link = attestation.create_link(
                    step_name="convert-document",
                    materials=materials,
                    products=products,
                    byproducts=byproducts,
                )

                # Upload attestation link to storage
                logger.info("Uploading attestation link for document %s", document_id)
                link_bytes = json.dumps(link, separators=(",", ":")).encode("utf-8")
                attestation_uri = storage.upload(
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
        state.update(
            tenant_id,
            document_id,
            status=JOB_STATUS_COMPLETED,
            structured_json_uri=structured_json_uri,
        )

        # Record metrics for successful conversion
        duration = time.time() - start_time
        DURATION.labels(format=validated_format).observe(duration)
        REQUESTS_TOTAL.labels(format=validated_format, status="completed").inc()

    except Exception as exc:
        # Handle all errors by updating state to FAILED
        logger.exception("Conversion failed for document %s: %s", document_id, exc)

        # Determine error code based on exception type
        error_code = ERROR_CODE_INTERNAL
        error_message = str(exc)
        error_type = "internal"

        if isinstance(exc, ValueError):
            # Format validation, SSRF, hash mismatch
            error_code = ERROR_CODE_INVALID_ARGUMENT
            error_type = "invalid_argument"
        elif isinstance(exc, ConversionTimeout):
            # Timeout/resource exhaustion
            error_code = ERROR_CODE_RESOURCE_EXHAUSTED
            error_type = "timeout"
        elif isinstance(exc, ConversionError):
            # Conversion failure
            error_code = ERROR_CODE_INTERNAL
            error_type = "conversion_error"

        error_dict = {
            "code": error_code,
            "message": error_message,
        }

        # Record error metrics
        ERRORS_TOTAL.labels(error_type=error_type).inc()
        REQUESTS_TOTAL.labels(format=validated_format, status="failed").inc()

        # Update state to FAILED
        state.update(
            tenant_id,
            document_id,
            status=JOB_STATUS_FAILED,
            error=error_dict,
        )

        # Publish audit event for failure
        await audit.publish(
            tenant_id=tenant_id,
            event={
                "document_id": document_id,
                "tenant_id": tenant_id,
                "status": "failed",
                "error": error_dict,
            },
            trace_id=metadata.get("correlation_id", ""),
            span_id="",
        )

    finally:
        # Decrement active conversions
        ACTIVE_CONVERSIONS.dec()

        # Step 11: Clean up temp files
        if temp_file_path and temp_file_path.exists():
            logger.debug("Cleaning up temp file: %s", temp_file_path)
            temp_file_path.unlink(missing_ok=True)
