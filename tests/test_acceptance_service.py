"""Acceptance: concurrent coalescing and fault-tolerant degradation."""
import asyncio
import time

import pytest

from app.config import ServiceConfig
from app.errors import LanguageNotSupportedError
from app.languages.python_lang import PythonPlugin
from app.languages.registry import default_registry
from app.models import Position, TextEdit
from tests.conftest import PY_SAMPLE, make_service


# ---------------------------------------------------------- coalescing
@pytest.mark.parametrize("concurrency", [1, 2, 8, 32, 64])
def test_identical_concurrent_requests_compute_once(concurrency):
    async def run():
        service = make_service()
        data = PY_SAMPLE.encode()
        results = await asyncio.gather(
            *[service.analyze("python", data) for _ in range(concurrency)]
        )
        assert service.compute_count == 1
        # every waiter sees the very same result object
        assert all(r is results[0] for r in results)
        sig = [
            [(h.range.start, h.range.end, h.category) for h in r.highlights]
            for r in results
        ]
        assert all(s == sig[0] for s in sig)

    asyncio.run(run())


def test_results_consistent_under_mixed_order_and_concurrency():
    async def run():
        service = make_service()
        contents = [f"x = {i}\ny = 'v{i}'\n" for i in range(6)]

        async def burst():
            coros = [
                service.analyze("python", c.encode()) for c in contents
            ] * 4
            res = await asyncio.gather(*coros)
            by_content = {}
            for c, r in zip(contents * 4, res):
                by_content.setdefault(c, r)
                assert r is by_content[c]
            return by_content

        first = await burst()
        second = await burst()  # different scheduling order / second wave
        for c in contents:
            assert first[c] is second[c]
            assert first[c].formatted == c

    asyncio.run(run())


def test_waiter_cancellation_never_poisons_shared_result():
    class SlowPlugin(PythonPlugin):
        name = "slow"

        def analyze(self, source):
            time.sleep(0.25)
            return super().analyze(source)

    async def run():
        registry = default_registry()
        registry.register(SlowPlugin())
        service = CodeService(ServiceConfig(), registry)
        data = PY_SAMPLE.encode()

        cancelled = asyncio.create_task(service.analyze("slow", data))
        survivor = asyncio.create_task(service.analyze("slow", data))
        await asyncio.sleep(0.05)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        # the other waiter must still receive the complete result
        result = await survivor
        assert not result.degraded and result.highlights and result.outline
        assert service.compute_count == 1
        # the clean result was cached by the shared computation
        again = await service.analyze("slow", data)
        assert again is result
        # no leftover in-flight state
        assert not service._inflight

    from app.service import CodeService

    asyncio.run(run())


def test_all_waiters_cancelled_leaves_no_state():
    class SlowPlugin(PythonPlugin):
        name = "slow2"

        def analyze(self, source):
            time.sleep(0.25)
            return super().analyze(source)

    async def run():
        from app.service import CodeService

        registry = default_registry()
        registry.register(SlowPlugin())
        service = CodeService(ServiceConfig(), registry)
        data = PY_SAMPLE.encode()
        tasks = [
            asyncio.create_task(service.analyze("slow2", data)) for _ in range(3)
        ]
        await asyncio.sleep(0.02)
        for t in tasks:
            t.cancel()
        for t in tasks:
            with pytest.raises(asyncio.CancelledError):
                await t
        await asyncio.sleep(0.4)  # let the shielded computation finish
        # no partial result ever leaked (waiters all got CancelledError);
        # in-flight bookkeeping cleaned itself. The shared computation was
        # allowed to finish and, being clean, cached its COMPLETE result --
        # never a half-built intermediate.
        assert not service._inflight
        cached = list(service._cache.values())
        assert len(cached) == 1
        assert not cached[0].degraded and cached[0].highlights
        # a later request simply receives the complete cached result
        result = await service.analyze("slow2", data)
        assert not result.degraded
        assert result is cached[0]

    asyncio.run(run())


# --------------------------------------------------- degraded no caching
def test_syntax_error_degradation_is_not_cached_and_recoverable():
    async def run():
        service = make_service()
        bad = b"def broken(:\n  pass\n"
        r1 = await service.analyze("python", bad)
        assert r1.degraded
        diag = [d for d in r1.diagnostics if d.code == "syntax-error"][0]
        assert diag.position is not None and diag.position.line >= 0
        assert not service._cache, "degraded results must not be cached"
        # subsequent correct input unaffected
        good = await service.analyze("python", PY_SAMPLE.encode())
        assert not good.degraded and service._cache

    asyncio.run(run())


def test_invalid_encoding_degrades_with_precise_position_not_cached():
    async def run():
        service = make_service()
        data = "x = 1\ny = '坏'\n".encode("gbk")  # bytes invalid as utf-8
        result = await service.analyze("python", data)
        assert result.degraded
        diag = [d for d in result.diagnostics if d.code == "invalid-encoding"]
        assert diag and diag[0].position is not None
        assert diag[0].position.line == 1
        assert "�" in result.formatted
        assert not service._cache
        # correct input afterwards still works
        good = await service.analyze("python", b"x = 1\n")
        assert not good.degraded

    asyncio.run(run())


def test_oversize_input_degrades_not_cached():
    async def run():
        service = make_service(max_file_size=16)
        result = await service.analyze("python", b"x" * 100)
        assert result.degraded
        assert [d.code for d in result.diagnostics] == ["file-too-large"]
        assert result.highlights == [] and result.outline == []
        assert result.formatted == "x" * 100
        assert not service._cache

    asyncio.run(run())


def test_incremental_timeout_degrades_and_does_not_cache():
    class SlowPlugin(PythonPlugin):
        name = "slow"

        def analyze(self, source):
            time.sleep(0.3)
            return super().analyze(source)

    async def run():
        from app.service import CodeService

        registry = default_registry()
        registry.register(SlowPlugin())
        service = CodeService(ServiceConfig(timeout_seconds=0.05), registry)
        old = b"x = 1\n"
        new = b"x = 2\n"
        inc = await service.apply_edit(
            "slow", old, TextEdit(Position(0, 4), Position(0, 5), "2"), new
        )
        assert inc.result.degraded
        assert any(d.code == "timeout" for d in inc.result.diagnostics)
        assert inc.result.formatted == "x = 2\n"
        assert not service._cache

    asyncio.run(run())


# ------------------------------------------------------- language gating
def test_disabled_language_errors_not_silent_degrade():
    async def run():
        service = make_service(languages=frozenset({"python"}))
        with pytest.raises(LanguageNotSupportedError) as exc:
            await service.analyze("json", b'{"a":1}')
        assert exc.value.code == "language-not-supported"
        # the enabled language still works
        result = await service.analyze("python", b"x = 1\n")
        assert not result.degraded

    asyncio.run(run())


def test_unknown_language_errors_explicitly():
    async def run():
        service = make_service()
        with pytest.raises(LanguageNotSupportedError) as exc:
            await service.analyze("cobol", b"DIVISION.")
        assert exc.value.code == "language-not-supported"

    asyncio.run(run())


# ------------------------------------------- isolation across languages
def test_languages_are_isolated():
    async def run():
        service = make_service()
        py_before = await service.analyze("python", PY_SAMPLE.encode())
        js_result = await service.analyze("json", b'{"a": "true", "b": 1e3}')
        # touching json does not change python's cached answer
        py_after = await service.analyze("python", PY_SAMPLE.encode())
        assert py_after is py_before
        # and json independently gets the semantic-preserving result
        assert '"true"' in js_result.formatted and "1e3" in js_result.formatted

    asyncio.run(run())
