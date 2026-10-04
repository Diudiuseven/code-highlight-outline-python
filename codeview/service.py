"""High-level service: validation, caching, coalescing, incremental."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import threading

from codeview.backend import BackendRegistry
from codeview.cache import LRUCache, SingleFlight, _SENTINEL
from codeview.config import load_settings
from codeview.errors import ServiceError
from codeview.incremental import (
    analyze_incremental,
    analyze_full,
    analyze_regions,
)
from codeview.languages.json_lang import JsonBackend
from codeview.languages.plaintext import PlaintextBackend
from codeview.languages.python_lang import PythonBackend
from codeview.models import Reason, Settings
from codeview.sessions import SessionStore, SessionState


def _cache_key(language: str, text: str) -> tuple[str, str]:
    return language, hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(PythonBackend())
    registry.register(JsonBackend())
    registry.register(PlaintextBackend())
    return registry


class CodeViewService:
    """Synchronous core plus async wrappers used by the HTTP layer."""

    def __init__(
        self,
        settings: Settings | None = None,
        registry: BackendRegistry | None = None,
        executor: concurrent.futures.ThreadPoolExecutor | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.registry = registry or default_registry()
        self._cache = LRUCache(self.settings.cache_capacity)
        self._sessions = SessionStore(self.settings.session_capacity)
        self._flight = SingleFlight()
        self._inc_flight = SingleFlight()
        self._owns_executor = executor is None
        self._executor = executor or concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="codeview"
        )
        # session id -> previous per-region analyses
        self._session_parts: dict[str, list] = {}
        self._parts_lock = threading.Lock()
        # One optional deadline per coalesced computation. The async
        # dispatcher sets a worker-local timeout before calling the sync API;
        # the flight leader (whichever thread that is) records its absolute
        # deadline here. A finished flight whose deadline passed is not
        # cached, so a timeout never poisons the cache or sessions.
        self._deadlines: dict[object, float] = {}
        self._deadline_lock = threading.Lock()
        self._local_timeout = threading.local()

    def close(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=False)

    def _expired(self, key) -> bool:
        import time as _time

        with self._deadline_lock:
            deadline = self._deadlines.get(key)
        return deadline is not None and _time.monotonic() > deadline

    def _arm(self, key) -> None:
        """Arm a deadline for ``key`` if this worker has a timeout context."""
        import time as _time

        timeout_s = getattr(self._local_timeout, "seconds", None)
        if timeout_s is None:
            return
        with self._deadline_lock:
            self._deadlines.setdefault(key, _time.monotonic() + timeout_s)

    def _store_if_fresh(self, key, payload) -> None:
        if not self._expired(key):
            self._cache.put(key, payload)

    # ---- validation -------------------------------------------------------
    def _validate(self, language: str, text: str) -> None:
        if language not in self.settings.languages:
            raise ServiceError(
                Reason.UNKNOWN_LANGUAGE,
                f"language '{language}' is not enabled; enabled: "
                f"{sorted(self.settings.languages)}",
                http_status=400,
            )
        backend = self.registry.get(language)
        if backend is None:
            # Configuration inconsistency: language enabled but no backend.
            # Never silently degrade - report explicitly.
            raise ServiceError(
                Reason.UNKNOWN_LANGUAGE,
                f"no backend registered for enabled language '{language}'",
                http_status=500,
            )
        if len(text) > self.settings.max_file_size:
            raise ServiceError(
                Reason.FILE_TOO_LARGE,
                f"source size {len(text)} exceeds limit "
                f"{self.settings.max_file_size}",
                http_status=413,
            )

    # ---- synchronous API --------------------------------------------------
    def analyze(self, language: str, text: str) -> dict:
        self._validate(language, text)
        backend = self.registry.get(language)
        key = _cache_key(language, text)

        def compute() -> dict:
            self._arm(key)
            cached = self._cache.get(key)
            if cached is not _SENTINEL:
                return cached
            result = analyze_full(backend, text)
            payload = self._envelope(result, incremental=False, affected=(),
                                     regions=1, recomputed=1, cached=False)
            self._store_if_fresh(key, payload)
            return payload

        return self._flight.run(key, compute)

    def apply_edit(
        self,
        language: str,
        new_text: str,
        edit_start: int,
        edit_end_old: int,
        edit_end_new: int,
        old_text: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        self._validate(language, new_text)
        backend = self.registry.get(language)

        old_parts, resolved_old = self._resolve_old(
            backend, language, new_text, edit_start, edit_end_old, edit_end_new,
            old_text, session_id,
        )
        if resolved_old is None:
            return self.analyze(language, new_text)

        inc_key = (
            session_id or "",
            language,
            hashlib.sha256(resolved_old.encode()).hexdigest(),
            edit_start,
            edit_end_old,
            edit_end_new,
            hashlib.sha256(new_text.encode()).hexdigest(),
        )

        def compute() -> dict:
            self._arm(inc_key)
            inc, fresh_parts = analyze_incremental(
                backend,
                resolved_old,
                new_text,
                edit_start,
                edit_end_old,
                edit_end_new,
                old_parts,
            )
            payload = self._envelope(
                inc.analysis,
                incremental=True,
                affected=inc.affected_ranges,
                regions=inc.region_count,
                recomputed=inc.recomputed,
                cached=False,
            )
            if not self._expired(inc_key):
                if session_id is not None:
                    with self._parts_lock:
                        self._session_parts[session_id] = fresh_parts
                        self._sessions.put(session_id, SessionState(new_text, language))
                self._cache.put(_cache_key(language, new_text), payload)
            return payload

        return self._inc_flight.run(inc_key, compute)

    def _resolve_old(
        self, backend, language, new_text, start, end_old, end_new, old_text, session_id
    ):
        with self._parts_lock:
            state = self._sessions.get(session_id) if session_id else None
            parts = self._session_parts.get(session_id) if session_id else None
        if old_text is None and state is None:
            return None, None
        resolved_old = old_text if old_text is not None else state.text
        # Verify the edit is consistent with the new document; on any
        # mismatch (e.g. stale session) fall back to full recompute.
        if start < 0 or end_old < 0 or end_new < 0:
            return None, None
        if start > len(resolved_old) or end_old > len(resolved_old):
            return None, None
        if end_new > len(new_text) or start > len(new_text):
            return None, None
        if resolved_old[:start] != new_text[:start]:
            return None, None
        if resolved_old[end_old:] != new_text[end_new:]:
            return None, None
        if parts is None:
            # Explicit old_text without a session: build the prior region
            # analyses once so unaffected regions can still be reused.
            parts = analyze_regions(backend, resolved_old)
            if session_id is not None:
                with self._parts_lock:
                    self._session_parts[session_id] = parts
        return parts, resolved_old

    # ---- envelope ---------------------------------------------------------
    def _envelope(self, analysis, *, incremental, affected, regions,
                  recomputed, cached) -> dict:
        has_errors = bool(analysis.diagnostics)
        status = "degraded" if has_errors else "ok"
        reasons = sorted({d.reason for d in analysis.diagnostics})
        payload = analysis.to_dict()
        payload.update(
            {
                "status": status,
                "reasons": reasons,
                "incremental": incremental,
                "affected_ranges": [
                    {"start": s, "end": e} for s, e in affected
                ],
                "region_count": regions,
                "regions_recomputed": recomputed,
                "cached": cached,
            }
        )
        return payload

    # ---- async wrappers ---------------------------------------------------
    async def analyze_async(self, language: str, text: str) -> dict:
        self._validate(language, text)
        return await self._submit(_cache_key(language, text),
                                  self.analyze, language, text)

    async def apply_edit_async(self, language: str, new_text: str, edit_start: int,
                               edit_end_old: int, edit_end_new: int,
                               old_text: str | None = None,
                               session_id: str | None = None) -> dict:
        self._validate(language, new_text)
        return await self._submit(
            None,
            self.apply_edit,
            language,
            new_text,
            edit_start,
            edit_end_old,
            edit_end_new,
            old_text,
            session_id,
        )

    async def _submit(self, deadline_key, fn, *args) -> dict:
        loop = asyncio.get_running_loop()
        timeout_s = self.settings.timeout_ms / 1000

        def worker():
            # Publish the timeout for this worker only; the flight leader's
            # compute arms the shared deadline from it.
            self._local_timeout.seconds = timeout_s
            try:
                return fn(*args)
            finally:
                self._local_timeout.seconds = None

        fut = loop.run_in_executor(self._executor, worker)
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except TimeoutError:
            # The worker may keep running, but the shared deadline makes the
            # leader drop its result instead of caching it; a fresh retry
            # starts a new flight (SingleFlight removes the completed one).
            raise ServiceError(
                Reason.TIMEOUT,
                f"analysis exceeded {self.settings.timeout_ms} ms",
                http_status=504,
            )
