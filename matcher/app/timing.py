"""
Per-request phase timings (thread-local) for the /match log line and response.

    timing.start()
    with timing.phase("detect"):
        ...
    timing.add("embed_wait", seconds)
    timing.take()  -> {"detect": 120, "embed_wait": 0, ...} in ms

Outside a started request (background backfill, extension) everything is a no-op.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

_local = threading.local()


def start() -> None:
    _local.acc = {}


def add(name: str, seconds: float) -> None:
    acc = getattr(_local, "acc", None)
    if acc is not None:
        acc[name] = acc.get(name, 0.0) + seconds


@contextmanager
def phase(name: str) -> Iterator[None]:
    t = time.perf_counter()
    try:
        yield
    finally:
        add(name, time.perf_counter() - t)


def take() -> dict[str, int]:
    acc = getattr(_local, "acc", None) or {}
    _local.acc = None
    return {k: round(v * 1000) for k, v in acc.items()}
