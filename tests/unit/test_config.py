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
