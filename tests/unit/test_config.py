import pytest
from pydantic import ValidationError


def test_defaults():
    from crosscodex_ingestion.config import IngestionSettings

    settings = IngestionSettings()
    assert settings.grpc_port == 8080
    assert settings.log_level == "info"
    assert settings.execution_mode == "embedded"
    assert settings.max_document_size == 52_428_800
    assert settings.attestation_enabled is True
    assert settings.storage_backend == "filesystem"


def test_allowed_formats_set():
    from crosscodex_ingestion.config import IngestionSettings

    settings = IngestionSettings()
    assert settings.allowed_formats_set == {"pdf", "docx", "html", "txt"}


def test_allowed_formats_custom(monkeypatch):
    from crosscodex_ingestion.config import IngestionSettings

    monkeypatch.setenv("ALLOWED_FORMATS", " PDF , Html ")
    settings = IngestionSettings()
    assert settings.allowed_formats_set == {"pdf", "html"}


def test_env_override(monkeypatch):
    from crosscodex_ingestion.config import IngestionSettings

    monkeypatch.setenv("GRPC_PORT", "9999")
    settings = IngestionSettings()
    assert settings.grpc_port == 9999


def test_invalid_execution_mode(monkeypatch):
    from crosscodex_ingestion.config import IngestionSettings

    monkeypatch.setenv("EXECUTION_MODE", "invalid")
    with pytest.raises(ValidationError):
        IngestionSettings()


def test_rlimit_zero_unlimited():
    from crosscodex_ingestion.config import IngestionSettings

    settings = IngestionSettings(rlimit_as_mb=0)
    assert settings.rlimit_as_mb == 0


def test_rlimit_as_default_fits_torch_virtual_address_space():
    """Docling/torch need tens of GB of virtual address space (measured ~40 GB VmPeak for a 32-page PDF)."""
    from crosscodex_ingestion.config import IngestionSettings

    assert IngestionSettings().rlimit_as_mb == 65536


def test_time_defaults_allow_large_documents():
    """NIST SP 800-53r5 (492 pages) needs >12 min wall and >23 CPU-minutes (RLIMIT_CPU counts all threads)."""
    from crosscodex_ingestion.config import IngestionSettings

    settings = IngestionSettings()
    assert settings.max_processing_time == 3600
    assert settings.rlimit_cpu == 21600


def test_shutdown_grace_seconds_defaults_below_kubernetes_default():
    from crosscodex_ingestion.config import IngestionSettings

    assert IngestionSettings().shutdown_grace_seconds == 25


def test_shutdown_grace_seconds_from_env(monkeypatch):
    from crosscodex_ingestion.config import IngestionSettings

    monkeypatch.setenv("SHUTDOWN_GRACE_SECONDS", "8.5")
    assert IngestionSettings().shutdown_grace_seconds == 8.5


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_shutdown_grace_seconds_rejects_invalid(monkeypatch, value):
    from crosscodex_ingestion.config import IngestionSettings

    monkeypatch.setenv("SHUTDOWN_GRACE_SECONDS", value)
    with pytest.raises(ValidationError):
        IngestionSettings()


def test_shutdown_grace_seconds_zero_is_allowed(monkeypatch):
    from crosscodex_ingestion.config import IngestionSettings

    monkeypatch.setenv("SHUTDOWN_GRACE_SECONDS", "0")
    assert IngestionSettings().shutdown_grace_seconds == 0
