"""Structured JSON tracing with one conversation id per research run.

Every event is one JSON line carrying `conversation_id`, so a whole run (every agent
step, model call and tool call) can be filtered with a single id. Lines go to stderr
and, when a run directory is set, to runs/<conversation_id>.jsonl.

On Cloud Run, JSON lines on stdout/stderr are parsed by Cloud Logging: `severity` and
`message` become first-class fields and the rest lands in jsonPayload, so
`jsonPayload.conversation_id="..."` finds a run end to end.
"""

import json
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_conversation_id: ContextVar[str | None] = ContextVar("conversation_id", default=None)
_run_file: ContextVar[Path | None] = ContextVar("run_file", default=None)
_echo: ContextVar[bool] = ContextVar("echo", default=True)
_extra: ContextVar[dict] = ContextVar("extra", default={})


def new_conversation_id() -> str:
    return uuid.uuid4().hex[:16]


def current_conversation_id() -> str | None:
    return _conversation_id.get()


@contextmanager
def conversation(conversation_id: str | None = None, run_dir: Path | None = None,
                 echo: bool = True, extra: dict | None = None) -> Iterator[str]:
    """Bind a conversation id (and optional per-run log file) for everything inside.

    extra: fields added to every event, e.g. Cloud Logging's trace field so all of a
    request's log lines group under its Cloud Trace span.
    """
    cid = conversation_id or new_conversation_id()
    run_file = None
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        run_file = run_dir / f"{cid}.jsonl"
    tokens = (_conversation_id.set(cid), _run_file.set(run_file), _echo.set(echo),
              _extra.set(dict(extra or {})))
    try:
        yield cid
    finally:
        _conversation_id.reset(tokens[0])
        _run_file.reset(tokens[1])
        _echo.reset(tokens[2])
        _extra.reset(tokens[3])


def log_event(event: str, severity: str = "INFO", **fields: Any) -> dict:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "message": event,
        "event": event,
        "conversation_id": _conversation_id.get(),
        **_extra.get(),
        **fields,
    }
    line = json.dumps(record, default=str)
    if _echo.get():
        print(line, file=sys.stderr, flush=True)
    run_file = _run_file.get()
    if run_file is not None:
        with run_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    return record


@contextmanager
def timed(event: str, **fields: Any) -> Iterator[dict]:
    """Log `event` with elapsed seconds; callers can add result fields to the yielded dict."""
    extra: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        yield extra
    except Exception as e:
        log_event(event, severity="ERROR", seconds=round(time.perf_counter() - start, 3),
                  error=f"{type(e).__name__}: {e}", **fields, **extra)
        raise
    log_event(event, seconds=round(time.perf_counter() - start, 3), **fields, **extra)
