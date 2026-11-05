import pytest

from crosscodex_ingestion import converter
from tests.unit.process_helpers import SLEEPER_SCRIPT


@pytest.fixture
def sleeper_worker(tmp_path, monkeypatch):
    """Replace the Docling worker with a script that hangs; return (input_path, pidfile)."""
    pidfile = tmp_path / "pids"
    monkeypatch.setattr(converter, "_WORKER_SCRIPT", SLEEPER_SCRIPT.replace("@PIDFILE@", str(pidfile)))
    input_path = tmp_path / "doc.pdf"
    input_path.write_bytes(b"%PDF-1.4")
    return input_path, pidfile
