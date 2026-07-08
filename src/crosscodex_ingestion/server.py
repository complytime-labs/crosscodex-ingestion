"""gRPC server lifecycle and initialization."""

import asyncio
import logging
import signal
import socket
from typing import Any

import grpc
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
from prometheus_client import start_http_server
from pythonjsonlogger import jsonlogger

from crosscodex_ingestion.attestation import create_attestation_manager
from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.dispatch import create_dispatch
from crosscodex_ingestion.proto.crosscodex.v1 import ingestion_pb2_grpc
from crosscodex_ingestion.service import IngestionServiceServicer
from crosscodex_ingestion.state import create_state_backend
from crosscodex_ingestion.storage import create_storage

logger = logging.getLogger(__name__)


async def serve() -> None:
    """Initialize and run the gRPC server.

    This function:
    1. Loads configuration from environment
    2. Initializes structured logging
    3. Creates backend services (state, storage, attestation, audit, dispatch)
    4. Starts the gRPC async server
    5. Handles graceful shutdown on SIGTERM/SIGINT

    The server runs until interrupted by a signal.
    """
    # Load settings from environment
    settings = IngestionSettings()

    # Initialize telemetry (OTel + metrics + structured logging)
    _init_telemetry(settings)

    logger.info(
        "Starting CrossCodex Ingestion Service",
        extra={
            "grpc_port": settings.grpc_port,
            "execution_mode": settings.execution_mode,
            "storage_backend": settings.storage_backend,
            "state_backend": settings.state_backend,
        },
    )

    # Create backends
    state_backend = create_state_backend(settings)
    storage_backend = create_storage(settings)
    attestation_manager = create_attestation_manager(settings)

    # Create audit publisher
    audit_publisher = AuditPublisher(settings)
    await audit_publisher.connect()

    # Create dispatch backend
    dispatch_backend = create_dispatch(
        settings=settings,
        state=state_backend,
        storage=storage_backend,
        attestation=attestation_manager,
        audit=audit_publisher,
    )

    # Create gRPC servicer
    servicer = IngestionServiceServicer(
        settings=settings,
        state_backend=state_backend,
        dispatch_backend=dispatch_backend,
    )

    # Create gRPC server
    server = grpc.aio.server()
    ingestion_pb2_grpc.add_IngestionServiceServicer_to_server(servicer, server)

    # Bind to port
    listen_addr = f"[::]:{settings.grpc_port}"
    server.add_insecure_port(listen_addr)

    # Start server
    await server.start()
    logger.info(f"gRPC server listening on {listen_addr}")

    # Set up graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig: int, frame: Any) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {sig}, initiating graceful shutdown")
        shutdown_event.set()

    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown
    logger.info("Shutting down server...")

    # Stop accepting new requests
    await server.stop(grace=30)

    # Shutdown dispatch backend (wait for in-flight jobs)
    await dispatch_backend.shutdown()

    # Close audit publisher connection
    await audit_publisher.close()

    logger.info("Server shutdown complete")


def _init_telemetry(settings: IngestionSettings) -> None:
    """Initialize OpenTelemetry tracing, Prometheus metrics, and structured logging.

    Args:
        settings: Configuration settings.
    """
    # Step 1: Initialize OpenTelemetry tracing
    if settings.otel_exporter_otlp_endpoint:
        # Create resource with service metadata
        resource = Resource.create(
            attributes={
                "service.name": settings.otel_service_name,
                "service.version": "0.1.0",  # TODO: Extract from package metadata
                "host.name": socket.gethostname(),
            }
        )

        # Create sampler
        sampler_map = {
            "always_on": ParentBasedTraceIdRatio(1.0),
            "parentbased_always_on": ParentBasedTraceIdRatio(1.0),
        }
        sampler = sampler_map.get(
            settings.otel_traces_sampler.lower(),
            ParentBasedTraceIdRatio(1.0),
        )

        # Create OTLP exporter
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=True,  # TODO: Support TLS configuration
        )

        # Create and configure TracerProvider
        provider = TracerProvider(
            resource=resource,
            sampler=sampler,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        # Set global tracer provider
        trace.set_tracer_provider(provider)

        logger.info(
            "OpenTelemetry tracing initialized",
            extra={
                "endpoint": settings.otel_exporter_otlp_endpoint,
                "service_name": settings.otel_service_name,
            },
        )
    else:
        # Use NoOp tracer provider (default behavior when not configured)
        logger.info("OpenTelemetry tracing not configured (no OTLP endpoint)")

    # Step 2: Start Prometheus metrics HTTP server
    try:
        start_http_server(settings.metrics_port)
        logger.info(f"Prometheus metrics server started on port {settings.metrics_port}")
    except OSError as e:
        logger.warning(f"Failed to start Prometheus metrics server: {e}")

    # Step 3: Configure structured JSON logging
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    level = level_map.get(settings.log_level.lower(), logging.INFO)

    # Create JSON formatter with trace correlation
    json_formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        timestamp=True,
    )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers and add JSON handler
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    json_handler = logging.StreamHandler()
    json_handler.setFormatter(json_formatter)
    root_logger.addHandler(json_handler)

    # Set application logger
    app_logger = logging.getLogger("crosscodex_ingestion")
    app_logger.setLevel(level)

    # Suppress noisy third-party loggers
    logging.getLogger("grpc").setLevel(logging.WARNING)
    logging.getLogger("nats").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
