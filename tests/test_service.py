import asyncio

import pytest

from app.errors import LanguageNotSupportedError
from app.models import Position, TextEdit
from tests.conftest import PY_SAMPLE, make_service


def test_concurrent_coalescing_single_compute():
    async def run():
        service = make_service()
        data = PY_SAMPLE.encode()
        results = await asyncio.gather(
            *[service.analyze("python", data) for _ in range(30)]
        )
        assert service.compute_count == 1
        assert all(r is results[0] for r in results)

    asyncio.run(run())


def test_cache_no_stale_after_edit():
    async def run():
        service = make_service()
        old = b"x = 1\n"
        new = b"x = 2\n"
        r1 = await service.analyze("python", old)
        inc = await service.apply_edit(
            "python", old, TextEdit(Position(0, 4), Position(0, 5), "2"), new
        )
        r2 = await service.analyze("python", new)
        assert r1.formatted != r2.formatted
        assert inc.result.formatted == r2.formatted == "x = 2\n"

    asyncio.run(run())


def test_cache_capacity_eviction():
    async def run():
        service = make_service(cache_capacity=4)
        for i in range(10):
            await service.analyze("python", f"x = {i}\n".encode())
        assert len(service._cache) <= 4

    asyncio.run(run())


def test_cancellation_does_not_pollute_cache():
    async def run():
        service = make_service()
        data = PY_SAMPLE.encode()
        task = asyncio.create_task(service.analyze("python", data))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # a later request still computes and caches a clean result
        result = await service.analyze("python", data)
        assert result.highlights
        assert service._cache

    asyncio.run(run())


def test_timeout_degrades_and_not_cached():
    import time

    from app.config import ServiceConfig
    from app.languages.python_lang import PythonPlugin
    from app.languages.registry import default_registry
    from app.service import CodeService

    class SlowPlugin(PythonPlugin):
        name = "slow"

        def analyze(self, source):
            time.sleep(0.3)
            return super().analyze(source)

    async def run():
        registry = default_registry()
        registry.register(SlowPlugin())
        service = CodeService(ServiceConfig(timeout_seconds=0.05), registry)
        result = await service.analyze("slow", PY_SAMPLE.encode())
        assert result.degraded
        assert any(d.code == "timeout" for d in result.diagnostics)
        assert not service._cache, "timeout results must not be cached"

    asyncio.run(run())


def test_language_switching_isolated():
    async def run():
        service = make_service()
        py_result = await service.analyze("python", PY_SAMPLE.encode())
        js_result = await service.analyze("json", b'{"a": 1}')
        assert py_result.language == "python" and js_result.language == "json"
        # switching back returns the cached, unchanged python result
        again = await service.analyze("python", PY_SAMPLE.encode())
        assert again is py_result

    asyncio.run(run())


def test_disabled_language_rejected():
    async def run():
        service = make_service(languages=frozenset({"json"}))
        with pytest.raises(LanguageNotSupportedError):
            await service.analyze("python", b"x = 1\n")
        result = await service.analyze("json", b"{}")
        assert not result.degraded

    asyncio.run(run())


def test_new_language_registration_does_not_affect_others(registry):
    async def run():
        service = make_service()
        before = await service.analyze("python", PY_SAMPLE.encode())

        class Dummy:
            name = "dummy"

            def analyze(self, source):
                raise AssertionError("not used")

            def tokenize_region(self, snippet, line_offset):
                return None

            def rederive(self, source, highlights, extra_diagnostics=None):
                raise AssertionError("not used")

        service._registry.register(Dummy())
        after = await service.analyze("python", PY_SAMPLE.encode())
        assert after is before
        assert "dummy" in service._registry.names()

    asyncio.run(run())
