"""Helpers for tests that run a real, hanging stand-in for the Docling worker process."""

import asyncio
from pathlib import Path

# Stand-in worker: records its own and a grandchild's PID, then sleeps until killed.
SLEEPER_SCRIPT = """
import os, subprocess, time
grandchild = subprocess.Popen(["sleep", "600"])
with open("@PIDFILE@.tmp", "w") as f:
    f.write(f"{os.getpid()} {grandchild.pid}")
os.rename("@PIDFILE@.tmp", "@PIDFILE@")
time.sleep(600)
"""


def is_running(pid: int) -> bool:
    """True unless the process is gone or a zombie awaiting reaping by an init that may not reap."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


async def wait_for_pids(pidfile: Path) -> list[int]:
    for _ in range(200):
        if pidfile.exists():
            return [int(p) for p in pidfile.read_text().split()]
        await asyncio.sleep(0.05)
    raise AssertionError("stand-in worker did not start")
