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

# Structure warnings on completed conversions
STRUCTURE_WARNINGS = Counter(
    "ingestion_structure_warnings_total",
    "Structure warnings raised on completed conversions",
    ["format", "warning"],
)

# Duration histogram
DURATION = Histogram(
    "ingestion_duration_seconds",
    "Document conversion duration",
    ["format"],
    # Conversions take seconds to tens of minutes (a 492-page PDF takes ~13.5 minutes)
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800),
)

# Document size histogram
DOC_SIZE = Histogram(
    "ingestion_document_size_bytes",
    "Document size in bytes",
    buckets=(1e3, 1e4, 1e5, 1e6, 1e7, 5e7, 1e8),
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
