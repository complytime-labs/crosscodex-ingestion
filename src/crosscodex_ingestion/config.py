from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


class IngestionSettings(BaseSettings):
    model_config = {"env_prefix": "", "case_sensitive": False}

    grpc_port: int = 8080
    metrics_port: int = 9090
    log_level: str = "info"
    execution_mode: Literal["embedded", "distributed"] = "embedded"
    max_concurrent_conversions: int = 4

    max_document_size: int = 52_428_800
    # Wall-clock limit per conversion. A 492-page PDF (NIST SP 800-53r5) takes >12 minutes.
    max_processing_time: int = 3600
    max_pages: int = 1000
    max_extraction_length: int = 10_485_760
    allowed_formats: str = "pdf,docx,html,txt"

    # Directory holding pre-downloaded Docling models (layout, table, OCR). The sandboxed
    # worker runs offline, so the server refuses to start if this is unset or not a directory.
    # Fetch models with: docling-tools models download -o <dir>
    docling_artifacts_path: str = ""

    # RLIMIT_AS bounds virtual address space only, not resident memory. Importing torch alone
    # needs more than 4 GB of address space, and a 32-page PDF peaks near 40 GB VmPeak while
    # using ~2.7 GB RSS. The real memory bound is the container memory limit.
    rlimit_as_mb: int = 65536
    # CPU-seconds limit; RLIMIT_CPU sums all threads and Docling uses ~6x wall time in CPU
    # (1 hour wall -> 6 hours CPU).
    rlimit_cpu: int = 21600
    rlimit_fsize_mb: int = 100

    max_url_bytes: int = 104_857_600
    url_timeout: int = 30
    max_redirects: int = 5

    storage_backend: Literal["filesystem", "s3"] = "filesystem"
    storage_path: str = "/data/documents"
    s3_endpoint: str = ""
    s3_bucket: str = "crosscodex-documents"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    attestation_enabled: bool = True
    attestation_private_key_path: str = ""
    attestation_public_key_path: str = ""
    fips_mode: bool = False

    nats_url: str = ""
    nats_tls_cert: str = ""
    nats_tls_key: str = ""
    nats_tls_ca: str = ""

    state_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = ""

    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_protocol: str = "grpc"
    otel_service_name: str = "crosscodex-ingestion"
    otel_traces_sampler: str = "parentbased_always_on"

    @property
    def allowed_formats_set(self) -> set[str]:
        return {f.strip().lower() for f in self.allowed_formats.split(",") if f.strip()}

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, v: str) -> str:
        return v.lower()
