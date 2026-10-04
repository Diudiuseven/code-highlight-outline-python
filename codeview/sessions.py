"""Editing sessions for incremental requests (bounded LRU)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionState:
    """Last accepted document state for a session."""

    text: str
    language: str


class SessionStore:
    """Thread-safe bounded LRU store of previous document text."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._data: "OrderedDict[str, SessionState]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionState | None:
        with self._lock:
            if session_id not in self._data:
                return None
            self._data.move_to_end(session_id)
            return self._data[session_id]

    def put(self, session_id: str, state: SessionState) -> None:
        with self._lock:
            if self._capacity == 0:
                return
            if session_id in self._data:
                self._data.move_to_end(session_id)
            self._data[session_id] = state
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
