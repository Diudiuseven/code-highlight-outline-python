"""Content-addressed LRU cache with concurrent request coalescing."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_SENTINEL = object()


class LRUCache:
    """Thread-safe, bounded LRU cache.

    Only successfully produced values are ever stored (the service calls
    ``put`` only after the computation succeeds). A producer that raises or
    is cancelled therefore leaves no entry and no stale/intermediate state
    is observable.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 0:
            raise ValueError("capacity must be >= 0")
        self._capacity = capacity
        self._data: "OrderedDict[Any, Any]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            if key not in self._data:
                return _SENTINEL
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            if self._capacity == 0:
                return
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_MISSING = object()


@dataclass
class _FlightState:
    event: threading.Event = field(default_factory=threading.Event)
    waiters: int = 0
    done: bool = False
    result: Any = _MISSING
    error: BaseException | None = None


class SingleFlight:
    """Merge concurrent calls with the same key into one computation.

    All waiters receive the same result or the same exception. Abandoning a
    waiting caller (e.g. an asyncio timeout) never aborts the shared
    computation: the follower simply stops waiting while the leader runs to
    completion and the other waiters still receive the shared outcome.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[Any, _FlightState] = {}

    def run(self, key: Any, fn: Callable[[], Any]) -> Any:
        # Join an in-flight (not finished) computation, or start one.
        while True:
            with self._lock:
                state = self._flights.get(key)
                if state is not None and state.done:
                    # Finished flight briefly retained for remaining
                    # waiters: consume its outcome and reap if last.
                    state.waiters -= 1
                    result = state.result
                    error = state.error
                    if state.waiters == 0:
                        self._flights.pop(key, None)
                    if error is not None:
                        raise error
                    return result
                if state is None:
                    state = _FlightState()
                    self._flights[key] = state
                    is_leader = True
                else:
                    state.waiters += 1
                    is_leader = False
                event = state.event
                break

        if is_leader:
            # Compute WITHOUT holding the lock, so followers can join.
            return self._lead(key, state, fn)

        # Follower: block until the leader finishes. Local interruption
        # must not disturb the shared flight.
        while not event.wait(timeout=0.05):
            pass
        return self._collect(key, state)

    def _lead(self, key: Any, state: _FlightState, fn: Callable[[], Any]) -> Any:
        try:
            value = fn()
        except BaseException as exc:
            with self._lock:
                state.error = exc
                state.done = True
                event = state.event
                no_waiters = state.waiters == 0
                if no_waiters:
                    self._flights.pop(key, None)
            event.set()
            raise
        with self._lock:
            state.result = value
            state.done = True
            event = state.event
            no_waiters = state.waiters == 0
            if no_waiters:
                self._flights.pop(key, None)
        event.set()
        return value

    def _collect(self, key: Any, state: _FlightState) -> Any:
        with self._lock:
            state.waiters -= 1
            result = state.result
            error = state.error
            last = state.waiters == 0
            if last:
                self._flights.pop(key, None)
        if error is not None:
            raise error
        return result
