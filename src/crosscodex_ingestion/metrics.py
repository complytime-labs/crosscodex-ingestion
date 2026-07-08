"""Prometheus metrics for the ingestion service."""

from prometheus_client import Counter, Gauge, Histogram

# Request counters
REQUESTS_TOTAL = Counter(
    "ingestion_requests_total",
    "Total ingestion requests",
    ["format", "status"],
)

# Error counter
ERRORS_TOTAL = Counter(
    "ingestion_errors_total",
    "Total ingestion errors",
    ["error_type"],
)

# Duration histogram
DURATION = Histogram(
    "ingestion_duration_seconds",
    "Document conversion duration",
    ["format"],
)

# Document size histogram
DOC_SIZE = Histogram(
    "ingestion_document_size_bytes",
    "Document size in bytes",
)

# Active conversions gauge
ACTIVE_CONVERSIONS = Gauge(
    "ingestion_active_conversions",
    "Currently active conversions",
)

# Queue depth gauge
QUEUE_DEPTH = Gauge(
    "ingestion_queue_depth",
    "Ingestion queue depth",
)
