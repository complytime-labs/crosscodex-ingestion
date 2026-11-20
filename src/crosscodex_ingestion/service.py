"""gRPC service implementation for document ingestion."""

import logging
import uuid
from datetime import UTC, datetime

import grpc

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.dispatch import DispatchBackend
from crosscodex_ingestion.listing import ListQueryError, parse_list_options
from crosscodex_ingestion.mapping import MappingRegistry
from crosscodex_ingestion.metrics import DOC_SIZE, ERRORS_TOTAL, REQUESTS_TOTAL
from crosscodex_ingestion.nats_connection import NatsConnection, NatsUnavailableError
from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc
from crosscodex_ingestion.security import validate_document_id, validate_tenant_id
from crosscodex_ingestion.state import DocumentRecord, StateBackend
from crosscodex_ingestion.tls import client_identity

logger = logging.getLogger(__name__)

# Sent to clients while NATS is down. CWE-209: the broker's address goes to the log only.
NATS_UNAVAILABLE_MESSAGE = "NATS is unavailable; audit events cannot be recorded. Retry later."


class IngestionServiceServicer(ingestion_pb2_grpc.IngestionServiceServicer):
    """gRPC servicer for IngestionService.

    Implements ConvertDocument, GetDocument, and ListDocuments RPCs.
    """

    def __init__(
        self,
        settings: IngestionSettings,
        state_backend: StateBackend,
        dispatch_backend: DispatchBackend,
        mappings: MappingRegistry,
        nats: NatsConnection | None = None,
    ):
        """Initialize the servicer.

        Args:
            settings: Configuration settings.
            state_backend: State management backend.
            dispatch_backend: Job dispatch backend.
            mappings: Structured-source mappings a request may name.
            nats: The shared NATS connection, or None when NATS_URL is unset.
        """
        self._settings = settings
        self._state = state_backend
        self._dispatch = dispatch_backend
        self._mappings = mappings
        self._nats = nats

    async def ConvertDocument(
        self, request: ingestion_pb2.ConvertDocumentRequest, context: grpc.aio.ServicerContext
    ) -> ingestion_pb2.ConvertDocumentResponse:
        """Convert a document to structured JSON.

        Args:
            request: ConvertDocumentRequest containing tenant context and source.
            context: gRPC servicer context.

        Returns:
            ConvertDocumentResponse with document_id and PENDING status.

        Raises:
            grpc.RpcError: On validation failures (INVALID_ARGUMENT, RESOURCE_EXHAUSTED), or
                UNAVAILABLE while NATS is configured but not connected.
        """
        await self._require_valid_tenant(request.tenant_context.tenant_id, context)

        # Every job ends in an audit event; refuse work whose outcome could not be recorded.
        if self._nats is not None and not self._nats.available:
            logger.warning("Rejected ConvertDocument: NATS at %s is unavailable", self._nats.url)
            await context.abort(grpc.StatusCode.UNAVAILABLE, NATS_UNAVAILABLE_MESSAGE)

        # Validate that either content or source_uri is provided
        has_content = request.WhichOneof("source") == "content"
        has_source_uri = request.WhichOneof("source") == "source_uri"

        if not has_content and not has_source_uri:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Either content or source_uri must be provided",
            )

        # Validate document size if content is provided
        if has_content:
            content_size = len(request.content)
            DOC_SIZE.observe(content_size)
            if content_size > self._settings.max_document_size:
                ERRORS_TOTAL.labels(error_type="resource_exhausted").inc()
                await context.abort(
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    f"Document size {content_size} exceeds maximum {self._settings.max_document_size}",
                )

        # Assign UUID for the document
        document_id = str(uuid.uuid4())

        # Create metadata dict from proto message
        metadata = {}
        if request.metadata.mime_type:
            metadata["mime_type"] = request.metadata.mime_type
        if request.metadata.sha256:
            metadata["sha256"] = request.metadata.sha256
        if request.metadata.size_bytes:
            metadata["size_bytes"] = request.metadata.size_bytes
        if request.metadata.format_version:
            metadata["format_version"] = request.metadata.format_version
        if request.metadata.source_uri:
            metadata["source_uri"] = request.metadata.source_uri
        if request.mapping:
            mapping = self._mappings.get(request.mapping)
            if mapping is None:
                registered = self._mappings.ids()
                known = (
                    f"Registered mappings: {', '.join(registered)}."
                    if registered
                    else "No mappings are registered on this server."
                )
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, f"Unknown mapping {request.mapping[:64]!r}. {known}"
                )
                raise RuntimeError("unreachable")
            # The worker re-checks the hash: in distributed mode it may run another deployment's mappings.
            metadata["mapping_id"] = mapping.id
            metadata["mapping_sha256"] = mapping.sha256

        # Create audit dict
        now = datetime.now(UTC).isoformat()
        audit = {
            "created_at": now,
            "created_by": client_identity(context, self._settings.grpc_allowed_clients_set) or "unauthenticated",
        }

        # Create DocumentRecord in state backend
        record = DocumentRecord(
            document_id=document_id,
            tenant_id=request.tenant_context.tenant_id,
            status=common_pb2.JOB_STATUS_PENDING,
            metadata=metadata,
            audit=audit,
            structured_json_uri="",
            error=None,
            created_at=now,
            updated_at=now,
        )
        await self._state.create(record)

        # Dispatch to worker
        try:
            await self._dispatch.dispatch(
                document_id=document_id,
                tenant_id=request.tenant_context.tenant_id,
                content=request.content if has_content else None,
                source_uri=request.source_uri if has_source_uri else None,
                metadata=metadata,
            )
        except NatsUnavailableError as exc:
            # NATS dropped after the check above. Fail the record so it does not stay PENDING forever.
            logger.warning("Rejected ConvertDocument for document %s: %s", document_id, exc)
            await self._state.update(
                request.tenant_context.tenant_id,
                document_id,
                status=common_pb2.JOB_STATUS_FAILED,
                error={"code": common_pb2.ERROR_CODE_UNAVAILABLE, "message": NATS_UNAVAILABLE_MESSAGE},
            )
            await context.abort(grpc.StatusCode.UNAVAILABLE, NATS_UNAVAILABLE_MESSAGE)

        # Record metrics
        format_label = metadata.get("format_version", "unknown")
        REQUESTS_TOTAL.labels(format=format_label, status="pending").inc()

        logger.info(
            "Document conversion submitted",
            extra={
                "document_id": document_id,
                "tenant_id": request.tenant_context.tenant_id,
                "has_content": has_content,
                "has_source_uri": has_source_uri,
            },
        )

        return ingestion_pb2.ConvertDocumentResponse(
            document_id=document_id,
            status=common_pb2.JOB_STATUS_PENDING,
        )

    async def GetDocument(
        self, request: ingestion_pb2.GetDocumentRequest, context: grpc.aio.ServicerContext
    ) -> ingestion_pb2.GetDocumentResponse:
        """Get document conversion status and metadata.

        Args:
            request: GetDocumentRequest containing tenant context and document_id.
            context: gRPC servicer context.

        Returns:
            GetDocumentResponse with document details.

        Raises:
            grpc.RpcError: On validation failures or if document not found.
        """
        await self._require_valid_tenant(request.tenant_context.tenant_id, context)

        try:
            validate_document_id(request.document_id)
        except ValueError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

        # Look up document in state backend
        record = await self._state.get(request.tenant_context.tenant_id, request.document_id)

        if record is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Document {request.document_id} not found",
            )
            # abort() always raises; grpc has no type stubs, so mypy cannot see that
            raise RuntimeError("unreachable")

        # Map DocumentRecord to Document proto message
        document = self._record_to_proto(record)

        return ingestion_pb2.GetDocumentResponse(document=document)

    async def ListDocuments(
        self, request: ingestion_pb2.ListDocumentsRequest, context: grpc.aio.ServicerContext
    ) -> ingestion_pb2.ListDocumentsResponse:
        """List documents for a tenant with filters, sort and keyset pagination.

        Args:
            request: ListDocumentsRequest containing tenant context and list options.
            context: gRPC servicer context.

        Returns:
            ListDocumentsResponse with documents and page info.

        Raises:
            grpc.RpcError: On validation failures.
        """
        await self._require_valid_tenant(request.tenant_context.tenant_id, context)

        try:
            query = parse_list_options(request.options)
        except ListQueryError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

        records, next_token = await self._state.list_documents(request.tenant_context.tenant_id, query)

        # Map records to proto messages
        documents = [self._record_to_proto(record) for record in records]

        # Build page info
        page_info = common_pb2.PageInfo(next_page_token=next_token)

        return ingestion_pb2.ListDocumentsResponse(
            documents=documents,
            page_info=page_info,
        )

    @staticmethod
    async def _require_valid_tenant(tenant_id: str, context: grpc.aio.ServicerContext) -> None:
        """Abort with INVALID_ARGUMENT unless tenant_id passes the platform rule."""
        try:
            validate_tenant_id(tenant_id)
        except ValueError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))

    def _record_to_proto(self, record: DocumentRecord) -> ingestion_pb2.Document:
        """Convert DocumentRecord to Document proto message.

        Args:
            record: DocumentRecord from state backend.

        Returns:
            Document proto message.
        """
        # Build ContentMetadata
        metadata = common_pb2.ContentMetadata()
        if "mime_type" in record.metadata:
            metadata.mime_type = record.metadata["mime_type"]
        if "sha256" in record.metadata:
            metadata.sha256 = record.metadata["sha256"]
        if "size_bytes" in record.metadata:
            metadata.size_bytes = record.metadata["size_bytes"]
        if "format_version" in record.metadata:
            metadata.format_version = record.metadata["format_version"]
        if "source_uri" in record.metadata:
            metadata.source_uri = record.metadata["source_uri"]

        # Build AuditMetadata
        audit = common_pb2.AuditMetadata()
        if "created_by" in record.audit:
            audit.created_by = record.audit["created_by"]
        if "created_at" in record.audit:
            # Convert ISO string to timestamp

            created_dt = datetime.fromisoformat(record.audit["created_at"])
            audit.created_at.FromDatetime(created_dt)

        # Build Error if present
        error = None
        if record.error:
            error = common_pb2.Error(
                code=record.error.get("code", common_pb2.ERROR_CODE_INTERNAL),
                message=record.error.get("message", ""),
            )
            if "details" in record.error:
                error.details.update(record.error["details"])

        # Build Document
        document = ingestion_pb2.Document(
            document_id=record.document_id,
            tenant_context=common_pb2.TenantContext(tenant_id=record.tenant_id),
            metadata=metadata,
            audit=audit,
            conversion_status=record.status,
            structured_json_uri=record.structured_json_uri,
            attestation_uri=record.attestation_uri,
        )

        if error:
            document.error.CopyFrom(error)

        return document
