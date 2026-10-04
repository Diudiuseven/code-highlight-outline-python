"""Core service: caching, request coalescing, timeouts, degradation.

Guarantees:
- identical (language, content) requests are coalesced into ONE computation,
  at any concurrency level, and every waiter observes the same result;
- the cache is content-addressed (sha256), so it can never return stale data;
- timeouts/cancellations never populate the cache nor leave in-flight state;
- oversized input, invalid encoding, syntax errors and timeouts degrade to a
  partial result with machine-readable diagnostics instead of failing.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict

from app.config import ServiceConfig
from app.errors import LanguageNotSupportedError
from app.incremental import affected_range, analyze_edit
from app.languages.registry import LanguageRegistry
from app.models import (
    AnalysisResult,
    Diagnostic,
    IncrementalResult,
    Position,
    Severity,
    TextEdit,
)


def _decode(data: bytes) -> tuple[str, list[Diagnostic]]:
    """Decode UTF-8; on failure decode with replacement chars and report a
    precise line/col diagnostic for the first invalid byte."""
    try:
        return data.decode("utf-8"), []
    except UnicodeDecodeError as exc:
        text = data.decode("utf-8", errors="replace")
        prefix = data[: exc.start].decode("utf-8", errors="replace")
        line = prefix.count("\n")
        col = len(prefix) - (prefix.rfind("\n") + 1)
        return text, [
            Diagnostic(
                code="invalid-encoding",
                message=f"invalid UTF-8 byte at offset {exc.start}; "
                "replaced with U+FFFD",
                severity=Severity.ERROR,
                position=Position(line, col),
            )
        ]


class CodeService:
    def __init__(self, config: ServiceConfig, registry: LanguageRegistry) -> None:
        self._config = config
        self._registry = registry
        self._cache: OrderedDict[tuple[str, str], AnalysisResult] = OrderedDict()
        self._inflight: dict[tuple[str, str], asyncio.Task[AnalysisResult]] = {}
        self.compute_count = 0  # observable for tests / metrics

    # ------------------------------------------------------------- helpers
    def _plugin(self, language: str):
        if (
            self._config.languages is not None
            and language not in self._config.languages
        ):
            raise LanguageNotSupportedError(
                f"language {language!r} is disabled by configuration"
            )
        return self._registry.get(language)

    def _cache_get(self, key: tuple[str, str]) -> AnalysisResult | None:
        result = self._cache.get(key)
        if result is not None:
            self._cache.move_to_end(key)
        return result

    def _cache_put(self, key: tuple[str, str], result: AnalysisResult) -> None:
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > self._config.cache_capacity:
            self._cache.popitem(last=False)

    async def _compute(
        self, language: str, text: str, base_diagnostics: list[Diagnostic]
    ) -> AnalysisResult:
        plugin = self._plugin(language)
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, plugin.analyze, text),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return AnalysisResult(
                language=language,
                highlights=[],
                outline=[],
                formatted=text,
                diagnostics=base_diagnostics
                + [
                    Diagnostic(
                        code="timeout",
                        message="analysis exceeded "
                        f"{self._config.timeout_seconds}s; returned unprocessed",
                        severity=Severity.ERROR,
                    )
                ],
                degraded=True,
            )
        if base_diagnostics:
            result.diagnostics = base_diagnostics + result.diagnostics
            result.degraded = True
        return result

    # ----------------------------------------------------------------- API
    async def analyze(self, language: str, data: bytes) -> AnalysisResult:
        self._plugin(language)  # raises LanguageNotSupportedError early
        text, decode_diags = _decode(data)
        if len(data) > self._config.max_file_size:
            return AnalysisResult(
                language=language,
                highlights=[],
                outline=[],
                formatted=text,
                diagnostics=decode_diags
                + [
                    Diagnostic(
                        code="file-too-large",
                        message=f"{len(data)} bytes exceeds limit "
                        f"{self._config.max_file_size}; returned unprocessed",
                        severity=Severity.ERROR,
                    )
                ],
                degraded=True,
            )
        key = (language, hashlib.sha256(data).hexdigest())
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(
                self._compute(language, text, decode_diags)
            )
            self._inflight[key] = task
            self.compute_count += 1

            def _cleanup(_t: asyncio.Task, k: tuple[str, str] = key) -> None:
                self._inflight.pop(k, None)

            task.add_done_callback(_cleanup)
        # shield: one caller's cancellation must not cancel the shared
        # computation nor prevent others from receiving the result
        result = await asyncio.shield(task)
        if not any(d.code == "timeout" for d in result.diagnostics):
            self._cache_put(key, result)
        return result

    async def apply_edit(
        self, language: str, old: bytes, edit: TextEdit, new: bytes
    ) -> IncrementalResult:
        plugin = self._plugin(language)
        old_text, _ = _decode(old)
        new_text, new_diags = _decode(new)
        affected = affected_range(old_text, edit, new_text)

        old_result = await self.analyze(language, old)
        if new_diags:
            result = await self.analyze(language, new)
            return IncrementalResult(result=result, affected=affected)

        loop = asyncio.get_running_loop()
        try:
            result, _used = await asyncio.wait_for(
                loop.run_in_executor(
                    None, analyze_edit, plugin, old_text, new_text,
                    old_result, edit,
                ),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = AnalysisResult(
                language=language,
                highlights=[],
                outline=[],
                formatted=new_text,
                diagnostics=[
                    Diagnostic(
                        code="timeout",
                        message="analysis exceeded "
                        f"{self._config.timeout_seconds}s; returned unprocessed",
                        severity=Severity.ERROR,
                    )
                ],
                degraded=True,
            )
            return IncrementalResult(result=result, affected=affected)
        key = (language, hashlib.sha256(new).hexdigest())
        self._cache_put(key, result)
        return IncrementalResult(result=result, affected=affected)
