"""
In-process cache for small, rarely-changing reference lists
(departments, appointment types) -- Phase 12 hardening (master spec
audit gap #7's "caching" sub-item). Scoped narrowly, per that gap's
own scoping decision: only data an admin edits "once in a while" and
every other screen re-reads constantly via a dropdown, never anything
live/transactional (queue status, appointment status, payment status)
-- nothing else in this app is cached, and nothing else should be.

In-process only, with no cross-instance invalidation: correct for how
this app actually runs today (a single uvicorn process; see this
codebase's own "still single-tenant in every behavior except the
schema" framing elsewhere for the same single-instance reality). If
this is ever deployed as multiple worker processes, this cache would
need to move to a shared store (Redis) instead -- flagged here, not
silently assumed away.

Two invalidation paths, deliberately redundant: every write endpoint
that touches a cached list calls invalidate() itself right after its
own transaction commits, so a cache entry is normally never stale for
longer than that one request. The TTL below is a self-healing
backstop for the one thing invalidate() can't reach -- a write made
directly against the database (a migration backfill, a manual fix)
that never goes through this app's own endpoints at all.
"""

import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_TTL_SECONDS = 300

_lock = threading.Lock()
_store: dict[str, tuple[float, object]] = {}


def get_or_set(key: str, compute: Callable[[], T]) -> T:
    with _lock:
        cached = _store.get(key)
        if cached is not None and time.monotonic() - cached[0] < _TTL_SECONDS:
            return cached[1]  # type: ignore[return-value]

    value = compute()

    with _lock:
        _store[key] = (time.monotonic(), value)

    return value


def invalidate(key: str) -> None:
    with _lock:
        _store.pop(key, None)


def clear_all() -> None:
    """Wipe every cached entry, regardless of key. Not called from any
    application code path -- application writes always go through
    invalidate() for the one key they actually changed. This exists
    for tests/conftest.py's _clean_tables fixture, which truncates
    every table directly (bypassing this app's own API and therefore
    every invalidate() call) before each test: without this, a cached
    department/appointment-type list from one test would leak into the
    next one, which starts from an empty table."""
    with _lock:
        _store.clear()
