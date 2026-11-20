"""gRPC server lifecycle and initialization."""

import asyncio
import logging
import math
import signal
import socket
from collections.abc import Callable
from wsgiref.simple_server import WSGIServer

import grpc
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, ParentBased, Sampler, TraceIdRatioBased
from prometheus_client import start_http_server
from pythonjsonlogger.json import JsonFormatter

from crosscodex_ingestion import __version__
from crosscodex_ingestion.attestation import create_attestation_manager
from crosscodex_ingestion.audit import AuditPublisher
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.converter import validate_artifacts_path
from crosscodex_ingestion.dispatch import create_dispatch
from crosscodex_ingestion.html_profile import HtmlProfileError, load_html_profiles
from crosscodex_ingestion.mapping import MappingError, load_registry
from crosscodex_ingestion.nats_connection import NatsConnection
from crosscodex_ingestion.proto.crosscodex.v1 import ingestion_pb2_grpc
from crosscodex_ingestion.service import IngestionServiceServicer
from crosscodex_ingestion.state import create_state_backend
from crosscodex_ingestion.storage import create_storage
from crosscodex_ingestion.tls import ClientAllowlistInterceptor, build_server_credentials

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

    # JSON logging comes first so every later line, warnings from the checks below included, is JSON
    _configure_logging(settings)

    # Check everything before opening anything, and report every problem at once: fixing one,
    # restarting and meeting the next made a first start a loop. Each check fails closed.
    listen_addr = f"[::]:{settings.grpc_port}"
    errors: list[str] = []
    _attempt(errors, lambda: validate_artifacts_path(settings.docling_artifacts_path), ValueError)
    # Every mapping is validated now: a request can only name one that was valid at startup.
    mappings = _attempt(errors, lambda: load_registry(settings.mappings_path), ValueError, MappingError)
    # Same for HTML profiles: the workers are only ever sent profiles that were valid at startup.
    html_profiles = _attempt(
        errors, lambda: load_html_profiles(settings.html_profiles_path), ValueError, HtmlProfileError
    )
    # Unverifiable attestations and unauthenticated callers are refused here
    attestation_manager = _attempt(errors, lambda: create_attestation_manager(settings), ValueError, OSError)
    # Builds the NATS TLS context now; connecting waits until after telemetry is set up
    nats_connection = _attempt(
        errors, lambda: NatsConnection(settings) if settings.nats_url else None, ValueError, OSError
    )
    if settings.execution_mode == "distributed" and not settings.nats_url:
        errors.append("EXECUTION_MODE=distributed publishes jobs to NATS, so NATS_URL must be set")
    grpc_server = _attempt(errors, lambda: create_grpc_server(settings, listen_addr), ValueError, OSError)
    sampler = _attempt(
        errors,
        lambda: _build_sampler(settings.otel_traces_sampler, settings.otel_traces_sampler_arg),
        ValueError,
    )
    # Bound last, and only to prove the port is free: a failed start must not leave it held
    metrics_server = _attempt(errors, lambda: _start_metrics_server(settings.metrics_port), ValueError)
    # mappings and the rest are None only when a check above failed, so errors is non-empty then
    if errors or mappings is None or html_profiles is None or sampler is None or grpc_server is None:
        if metrics_server is not None:
            metrics_server.shutdown()
            metrics_server.server_close()
        raise SystemExit(
            f"Configuration error: {errors[0]}"
            if len(errors) == 1
            else "Configuration errors:\n" + "\n".join(f"  - {error}" for error in errors)
        )
    server, _ = grpc_server

    _init_tracing(settings, sampler)
    logger.info("Prometheus metrics server started on port %s", settings.metrics_port)

    logger.info(
        "Starting CrossCodex Ingestion Service",
        extra={
            "grpc_port": settings.grpc_port,
            "execution_mode": settings.execution_mode,
            "storage_backend": settings.storage_backend,
            "state_backend": settings.state_backend,
            "mappings": mappings.ids(),
            "html_profiles": html_profiles.ids(),
        },
    )

    # Create backends
    state_backend = create_state_backend(settings)
    storage_backend = create_storage(settings)

    # Connect to NATS in the background: the port opens now, and ConvertDocument returns
    # UNAVAILABLE until NATS is connected
    if nats_connection is not None:
        await nats_connection.start()

    # Create audit publisher
    audit_publisher = AuditPublisher(settings, nats_connection)

    # Create dispatch backend
    dispatch_backend = create_dispatch(
        settings=settings,
        state=state_backend,
        storage=storage_backend,
        attestation=attestation_manager,
        audit=audit_publisher,
        mappings=mappings,
        html_profiles=html_profiles,
        nats=nats_connection,
    )

    # Create gRPC servicer
    servicer = IngestionServiceServicer(
        settings=settings,
        state_backend=state_backend,
        dispatch_backend=dispatch_backend,
        mappings=mappings,
        nats=nats_connection,
    )

    ingestion_pb2_grpc.add_IngestionServiceServicer_to_server(servicer, server)

    # Set up graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig: signal.Signals) -> None:
        """Handle shutdown signals."""
        logger.info("Received signal %s, initiating graceful shutdown", sig.value)
        shutdown_event.set()

    # Register through the loop: a plain signal.signal handler runs, but setting the
    # event from it does not wake a loop blocked in gRPC's poller, so SIGTERM never
    # ended the process (the runtime had to SIGKILL it). Registered before the
    # server starts so a signal at any point after "listening" is handled.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler, sig)

    # Start server
    await server.start()
    logger.info("gRPC server listening on %s", listen_addr)

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown
    logger.info("Shutting down server...")

    # One grace budget covers stopping the RPCs and draining conversions, so the total stays
    # under the runtime's stop timeout.
    shutdown_deadline = loop.time() + settings.shutdown_grace_seconds

    # Stop accepting new requests
    await server.stop(grace=settings.shutdown_grace_seconds)

    # Shutdown dispatch backend: let in-flight jobs finish, then cancel them (retryable FAILED)
    await dispatch_backend.shutdown(max(0.0, shutdown_deadline - loop.time()))

    # Close the NATS connection last: the dispatch shutdown above publishes audit events
    if nats_connection is not None:
        await nats_connection.close()

    logger.info("Server shutdown complete")


# Room in a ConvertDocument message for everything but the document bytes:
# tenant context, metadata and protobuf framing.
_GRPC_REQUEST_OVERHEAD = 1024 * 1024


def create_grpc_server(settings: IngestionSettings, listen_addr: str) -> tuple[grpc.aio.Server, int]:
    """Create the gRPC server, bound to listen_addr, with transport security per settings.

    Returns the (not yet started) server and the bound port.

    The receive limit fits max_document_size: gRPC's default 4 MB limit rejected real
    documents long before max_document_size (the 6 MB NIST SP 800-53r5 PDF failed with
    "Received message larger than max"). Allowing max_document_size plus request overhead
    lets the servicer enforce max_document_size with its own actionable error.

    SO_REUSEPORT is off: gRPC enables it by default, so a second process could
    bind the same port without error and receive a share of the connections.

    With GRPC_TLS_MODE=mutual the port requires a CA-signed client certificate, and
    GRPC_ALLOWED_CLIENTS (if set) is enforced per RPC.

    Raises:
        ValueError: If the TLS configuration is incomplete or unreadable.
    """
    credentials = build_server_credentials(settings)
    allowed_clients = settings.grpc_allowed_clients_set
    server = grpc.aio.server(
        interceptors=[ClientAllowlistInterceptor(allowed_clients)] if allowed_clients else None,
        options=[
            ("grpc.max_receive_message_length", settings.max_document_size + _GRPC_REQUEST_OVERHEAD),
            ("grpc.so_reuseport", 0),
        ],
    )
    if credentials is None:
        return server, server.add_insecure_port(listen_addr)
    return server, server.add_secure_port(listen_addr, credentials)


def _attempt[T](errors: list[str], check: Callable[[], T], *failures: type[Exception]) -> T | None:
    """Run a startup check; a failure it raises is recorded in *errors* and yields None."""
    try:
        return check()
    except failures as exc:
        errors.append(str(exc))
        return None


# OTEL_TRACES_SAMPLER names (OpenTelemetry spec) to a sampler built from the OTEL_TRACES_SAMPLER_ARG ratio.
_SAMPLERS: dict[str, Callable[[float], Sampler]] = {
    "always_on": lambda _ratio: ALWAYS_ON,
    "always_off": lambda _ratio: ALWAYS_OFF,
    "traceidratio": TraceIdRatioBased,
    "parentbased_always_on": lambda _ratio: ParentBased(ALWAYS_ON),
    "parentbased_always_off": lambda _ratio: ParentBased(ALWAYS_OFF),
    "parentbased_traceidratio": lambda ratio: ParentBased(TraceIdRatioBased(ratio)),
}


def _build_sampler(name: str, arg: str) -> Sampler:
    """The OpenTelemetry sampler for OTEL_TRACES_SAMPLER and OTEL_TRACES_SAMPLER_ARG.

    The argument is a ratio in [0, 1] (default 1.0) used by the traceidratio variants and ignored
    by the rest.

    Raises:
        ValueError: If the name is not a standard sampler or the argument is not a ratio in [0, 1].
    """
    factory = _SAMPLERS.get(name.strip().lower())
    if factory is None:
        raise ValueError(f"OTEL_TRACES_SAMPLER {name!r} is not one of: {', '.join(_SAMPLERS)}")
    if not arg.strip():
        return factory(1.0)
    try:
        ratio = float(arg)
    except ValueError:
        ratio = math.nan
    if not 0.0 <= ratio <= 1.0:
        raise ValueError(f"OTEL_TRACES_SAMPLER_ARG {arg!r} is not a number from 0 to 1")
    return factory(ratio)


def _start_metrics_server(port: int) -> WSGIServer:
    """Serve Prometheus metrics on *port*.

    Raises:
        ValueError: If the port cannot be bound; a service without metrics is not left running.
    """
    try:
        httpd, _ = start_http_server(port)
    except OSError as exc:
        raise ValueError(f"Cannot bind the Prometheus metrics port {port} (METRICS_PORT): {exc}") from exc
    return httpd


def _configure_logging(settings: IngestionSettings) -> None:
    """Send every log line to stderr as JSON, at the configured level."""
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    level = level_map.get(settings.log_level.lower(), logging.INFO)

    # Create JSON formatter with trace correlation
    json_formatter = JsonFormatter(
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


def _init_tracing(settings: IngestionSettings, sampler: Sampler) -> None:
    """Initialize OpenTelemetry tracing when an OTLP endpoint is configured.

    Args:
        settings: Configuration settings.
        sampler: The sampler built from OTEL_TRACES_SAMPLER.
    """
    if not settings.otel_exporter_otlp_endpoint:
        # Use NoOp tracer provider (default behavior when not configured)
        logger.info("OpenTelemetry tracing not configured (no OTLP endpoint)")
        return

    # Create resource with service metadata
    resource = Resource.create(
        attributes={
            "service.name": settings.otel_service_name,
            "service.version": __version__,
            "host.name": socket.gethostname(),
        }
    )

    # Create OTLP exporter
    otlp_exporter = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        insecure=True,  # plaintext OTLP; TLS is not configurable yet
    )

    # Create and configure TracerProvider
    provider = TracerProvider(
        resource=resource,
        sampler=sampler,
    )
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Set global tracer provider
    trace.set_tracer_provider(provider)

    logger.info(
        "OpenTelemetry tracing initialized",
        extra={
            "endpoint": settings.otel_exporter_otlp_endpoint,
            "service_name": settings.otel_service_name,
            "sampler": sampler.get_description(),
        },
    )
