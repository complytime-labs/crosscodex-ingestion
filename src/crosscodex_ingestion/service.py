"""gRPC service implementation for document ingestion."""

import logging
import uuid
from datetime import UTC, datetime

import grpc

from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.dispatch import DispatchBackend
from crosscodex_ingestion.metrics import DOC_SIZE, ERRORS_TOTAL, REQUESTS_TOTAL
from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2, ingestion_pb2, ingestion_pb2_grpc
from crosscodex_ingestion.state import DocumentRecord, StateBackend

logger = logging.getLogger(__name__)


class IngestionServiceServicer(ingestion_pb2_grpc.IngestionServiceServicer):
    """gRPC servicer for IngestionService.

    Implements ConvertDocument, GetDocument, and ListDocuments RPCs.
    """

    def __init__(
        self,
        settings: IngestionSettings,
        state_backend: StateBackend,
        dispatch_backend: DispatchBackend,
    ):
        """Initialize the servicer.

        Args:
            settings: Configuration settings.
            state_backend: State management backend.
            dispatch_backend: Job dispatch backend.
        """
        self._settings = settings
        self._state = state_backend
        self._dispatch = dispatch_backend

    async def ConvertDocument(
        self, request: ingestion_pb2.ConvertDocumentRequest, context: grpc.ServicerContext
    ) -> ingestion_pb2.ConvertDocumentResponse:
        """Convert a document to structured JSON.

        Args:
            request: ConvertDocumentRequest containing tenant context and source.
            context: gRPC servicer context.

        Returns:
            ConvertDocumentResponse with document_id and PENDING status.

        Raises:
            grpc.RpcError: On validation failures (INVALID_ARGUMENT, RESOURCE_EXHAUSTED).
        """
        # Validate tenant_id
        if not request.tenant_context.tenant_id:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "tenant_id is required in tenant_context",
            )

        # Validate that either content or source_uri is provided
        has_content = request.WhichOneof("source") == "content"
        has_source_uri = request.WhichOneof("source") == "source_uri"

        if not has_content and not has_source_uri:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Either content or source_uri must be provided",
            )

        # Validate document size if content is provided
        if has_content:
            content_size = len(request.content)
            DOC_SIZE.observe(content_size)
            if content_size > self._settings.max_document_size:
                ERRORS_TOTAL.labels(error_type="resource_exhausted").inc()
                context.abort(
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

        # Create audit dict
        now = datetime.now(UTC).isoformat()
        audit = {
            "created_at": now,
            "created_by": "system",  # TODO: Extract from mTLS or auth context
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
        self._state.create(record)

        # Dispatch to worker
        await self._dispatch.dispatch(
            document_id=document_id,
            tenant_id=request.tenant_context.tenant_id,
            content=request.content if has_content else None,
            source_uri=request.source_uri if has_source_uri else None,
            metadata=metadata,
        )

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
        self, request: ingestion_pb2.GetDocumentRequest, context: grpc.ServicerContext
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
        # Validate tenant_id
        if not request.tenant_context.tenant_id:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "tenant_id is required in tenant_context",
            )

        # Look up document in state backend
        record = self._state.get(request.tenant_context.tenant_id, request.document_id)

        if record is None:
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Document {request.document_id} not found",
            )
            # Unreachable, but mypy doesn't know abort raises
            raise RuntimeError("unreachable")

        # Map DocumentRecord to Document proto message
        document = self._record_to_proto(record)

        return ingestion_pb2.GetDocumentResponse(document=document)

    async def ListDocuments(
        self, request: ingestion_pb2.ListDocumentsRequest, context: grpc.ServicerContext
    ) -> ingestion_pb2.ListDocumentsResponse:
        """List documents for a tenant with pagination.

        Args:
            request: ListDocumentsRequest containing tenant context and pagination options.
            context: gRPC servicer context.

        Returns:
            ListDocumentsResponse with documents and page info.

        Raises:
            grpc.RpcError: On validation failures.
        """
        # Validate tenant_id
        if not request.tenant_context.tenant_id:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "tenant_id is required in tenant_context",
            )

        # Extract pagination parameters
        page_size = 50  # Default
        page_token = ""

        if request.options and request.options.pagination:
            if request.options.pagination.page_size:
                page_size = request.options.pagination.page_size
            if request.options.pagination.page_token:
                page_token = request.options.pagination.page_token

        # Query state backend
        records, next_token = self._state.list_documents(
            tenant_id=request.tenant_context.tenant_id,
            page_size=page_size,
            page_token=page_token,
        )

        # Map records to proto messages
        documents = [self._record_to_proto(record) for record in records]

        # Build page info
        page_info = common_pb2.PageInfo(next_page_token=next_token)

        return ingestion_pb2.ListDocumentsResponse(
            documents=documents,
            page_info=page_info,
        )

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
        )

        if error:
            document.error.CopyFrom(error)

        return document
