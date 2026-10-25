"""Per-run session log at ``.book-gen/logs/session-<timestamp>.log``.

Records what the user typed, what the agent said, and every tool call
with its result, so a session that went wrong can be inspected after
the fact instead of reconstructed from a pasted terminal. Secrets never
reach it: API keys are read through ``read_secret``, which isn't logged.

Nothing is written until ``start`` attaches the file handler, so tests
and library use stay file-free.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime
from pathlib import Path

LOGGER = logging.getLogger("littlepress")
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False

#: How many session logs to keep; older ones are deleted on start.
KEEP = 20


def start(session_root: Path) -> Path:
    """Open a fresh log file for this run and return its path."""
    logs = Path(session_root) / ".book-gen" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for old in sorted(logs.glob("session-*.log"))[: -(KEEP - 1)]:
        with suppress(OSError):
            old.unlink()
    path = logs / f"session-{datetime.now():%Y%m%d-%H%M%S}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    for old_handler in list(LOGGER.handlers):
        old_handler.close()
        LOGGER.removeHandler(old_handler)
    LOGGER.addHandler(handler)
    return path
