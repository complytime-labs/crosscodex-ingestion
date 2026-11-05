"""Bounded graceful shutdown of EmbeddedDispatch, driven through the real converter subprocess."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from crosscodex_ingestion import converter
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.dispatch import CANCEL_CLEANUP_SECONDS, EmbeddedDispatch
from crosscodex_ingestion.state import DocumentRecord, MemoryStateBackend
from crosscodex_ingestion.storage import FilesystemStorage
from crosscodex_ingestion.worker import (
    ERROR_CODE_UNAVAILABLE,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
)
from tests.unit.process_helpers import is_running, wait_for_pids

TENANT = "tenant-t"
EPSILON = 1.0  # scheduling slack on top of the documented shutdown bound

# Stand-in worker that finishes successfully after a short delay.
_SLOW_SUCCESS_SCRIPT = """
import sys, time
time.sleep(0.5)
open(sys.argv[2], "w").write('{"elements":[],"metadata":{}}')
"""


@pytest.fixture
def state():
    return MemoryStateBackend()


@pytest.fixture
def audit():
    return AsyncMock()


@pytest.fixture
def make_dispatch(tmp_path, state, audit):
    def _make(max_concurrent: int = 4) -> EmbeddedDispatch:
        settings = IngestionSettings(
            max_concurrent_conversions=max_concurrent,
            storage_path=str(tmp_path / "storage"),
            docling_artifacts_path=str(tmp_path),
        )
        return EmbeddedDispatch(
            settings=settings,
            state=state,
            storage=FilesystemStorage(str(tmp_path / "storage")),
            attestation=None,
            audit=audit,
        )

    return _make


async def _submit(dispatcher: EmbeddedDispatch, state: MemoryStateBackend, document_id: str) -> None:
    await state.create(DocumentRecord(document_id=document_id, tenant_id=TENANT, status=1))
    await dispatcher.dispatch(document_id, TENANT, b"%PDF-1.4 test", None, {})


async def test_conversion_finishing_within_grace_completes(make_dispatch, state, monkeypatch):
    monkeypatch.setattr(converter, "_WORKER_SCRIPT", _SLOW_SUCCESS_SCRIPT)
    dispatcher = make_dispatch()
    await _submit(dispatcher, state, "doc-1")
    await asyncio.sleep(0.1)

    await dispatcher.shutdown(grace_seconds=10)

    record = await state.get(TENANT, "doc-1")
    assert record.status == JOB_STATUS_COMPLETED
    assert record.error is None


async def test_conversion_exceeding_grace_is_killed_and_failed_retryable(make_dispatch, state, audit, sleeper_worker):
    _, pidfile = sleeper_worker
    dispatcher = make_dispatch()
    await _submit(dispatcher, state, "doc-1")
    pids = await wait_for_pids(pidfile)
    assert all(is_running(p) for p in pids)

    grace = 0.5
    started = time.monotonic()
    await dispatcher.shutdown(grace_seconds=grace)
    elapsed = time.monotonic() - started

    assert elapsed < grace + CANCEL_CLEANUP_SECONDS + EPSILON
    assert not any(is_running(p) for p in pids)
    record = await state.get(TENANT, "doc-1")
    assert record.status == JOB_STATUS_FAILED
    assert record.error["code"] == ERROR_CODE_UNAVAILABLE
    assert "resubmit" in record.error["message"]
    event = audit.publish.call_args.kwargs["event"]
    assert event["document_id"] == "doc-1"
    assert event["status"] == "failed"
    assert event["error"]["code"] == ERROR_CODE_UNAVAILABLE


async def test_queued_jobs_fail_immediately_without_waiting_for_grace(make_dispatch, state, audit, sleeper_worker):
    _, pidfile = sleeper_worker
    dispatcher = make_dispatch(max_concurrent=1)
    await _submit(dispatcher, state, "running")
    await _submit(dispatcher, state, "queued")
    await wait_for_pids(pidfile)

    shutdown = asyncio.create_task(dispatcher.shutdown(grace_seconds=30))
    try:
        await asyncio.sleep(0.3)

        queued = await state.get(TENANT, "queued")
        assert queued.status == JOB_STATUS_FAILED
        assert queued.error["code"] == ERROR_CODE_UNAVAILABLE
        assert (await state.get(TENANT, "running")).status == JOB_STATUS_RUNNING
        assert not shutdown.done()
        assert [c.kwargs["event"]["document_id"] for c in audit.publish.call_args_list] == ["queued"]
    finally:
        shutdown.cancel()
        for task in list(dispatcher._tasks):
            task.cancel()
        await asyncio.gather(*dispatcher._tasks, shutdown, return_exceptions=True)


async def test_queued_job_never_starts_a_worker_process(make_dispatch, state, sleeper_worker):
    """A job failed while queued must not later run once a slot frees up."""
    _, pidfile = sleeper_worker
    dispatcher = make_dispatch(max_concurrent=1)
    await _submit(dispatcher, state, "running")
    await _submit(dispatcher, state, "queued")
    await wait_for_pids(pidfile)
    pidfile.unlink()

    await dispatcher.shutdown(grace_seconds=0.2)

    assert not pidfile.exists()
    assert (await state.get(TENANT, "queued")).status == JOB_STATUS_FAILED


async def test_job_dispatched_but_not_yet_started_is_failed(make_dispatch, state):
    dispatcher = make_dispatch()
    await _submit(dispatcher, state, "doc-1")

    await dispatcher.shutdown(grace_seconds=1)  # no event-loop turn since dispatch: task never ran

    record = await state.get(TENANT, "doc-1")
    assert record.status == JOB_STATUS_FAILED
    assert record.error["code"] == ERROR_CODE_UNAVAILABLE


async def test_shutdown_with_nothing_in_flight_returns_immediately(make_dispatch):
    started = time.monotonic()
    await make_dispatch().shutdown(grace_seconds=30)
    assert time.monotonic() - started < EPSILON


async def test_zero_grace_cancels_running_conversion_at_once(make_dispatch, state, sleeper_worker):
    _, pidfile = sleeper_worker
    dispatcher = make_dispatch()
    await _submit(dispatcher, state, "doc-1")
    pids = await wait_for_pids(pidfile)

    await dispatcher.shutdown(grace_seconds=0)

    assert not any(is_running(p) for p in pids)
    assert (await state.get(TENANT, "doc-1")).status == JOB_STATUS_FAILED
