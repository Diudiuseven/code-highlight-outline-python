"""Concurrency coalescing and fault-tolerant degradation guarantees."""
import asyncio
import threading

import pytest

from app.config import ServiceConfig
from app.errors import LanguageNotSupportedError
from app.models import Position, TextEdit
from tests.conftest import PY_SAMPLE, make_service


def test_same_content_any_concurrency_is_one_compute():
    async def run():
        for conc in (1, 2, 8, 32, 64):
            service = make_service()
            data = PY_SAMPLE.encode()
            results = await asyncio.gather(
                *[service.analyze("python", data) for _ in range(conc)]
            )
            assert service.compute_count == 1
            assert all(r is results[0] for r in results)
            assert service._cache

    asyncio.run(run())


def test_results_identical_under_mixed_order():
    async def run():
        service = make_service()
        payloads = [f"x = {i}\n".encode() for i in range(8)]

        async def burst():
            order = payloads * 4
            await asyncio.gather(
                *[service.analyze("python", p) for p in order]
            )

        await burst()
        # every distinct input maps to one stable result, regardless of order
        for p in payloads:
            r1 = await service.analyze("python", p)
            r2 = await service.analyze("python", p)
            assert r1 is r2

    asyncio.run(run())


def test_cancellation_leaves_no_intermediate_state_and_others_complete():
    class Gate:
        def __init__(self):
            self.enter = threading.Event()
            self.release = threading.Event()

    from app.languages.python_lang import PythonPlugin
    from app.languages.registry import default_registry
    from app.service import CodeService

    gate = Gate()

    class GatedPlugin(PythonPlugin):
        name = "gated"

        def analyze(self, source):
            gate.enter.set()
            gate.release.wait(5)
            return super().analyze(source)

    async def run():
        registry = default_registry()
        registry.register(GatedPlugin())
        service = CodeService(ServiceConfig(timeout_seconds=10), registry)
        data = PY_SAMPLE.encode()

        t1 = asyncio.create_task(service.analyze("gated", data))
        t2 = asyncio.create_task(service.analyze("gated", data))
        await asyncio.to_thread(gate.enter.wait, 5)
        # cancel ONE waiter while the shared computation is in flight
        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1
        gate.release.set()
        # the other waiter still receives the complete, clean result
        r2 = await t2
        assert r2.highlights and not r2.degraded
        assert service.compute_count == 1
        assert service._inflight == {}
        # result was cached by the shared task despite one cancellation
        cached = await service.analyze("gated", data)
        assert cached is r2 and service.compute_count == 1

    asyncio.run(run())


def test_degraded_result_is_not_cached():
    async def run():
        service = make_service()
        bad = b"def broken(:\n"
        r = await service.analyze("python", bad)
        assert r.degraded
        assert not service._cache, "degraded results must not be cached"
        # but a later valid input is cached normally
        good = await service.analyze("python", PY_SAMPLE.encode())
        assert not good.degraded and service._cache

    asyncio.run(run())


def test_invalid_encoding_result_not_cached():
    async def run():
        service = make_service()
        r = await service.analyze("python", b"x = '\xff'\n")
        assert any(d.code == "invalid-encoding" for d in r.diagnostics)
        assert not service._cache

    asyncio.run(run())


def test_oversize_result_not_cached_and_does_not_poison_later():
    async def run():
        service = make_service(max_file_size=16)
        big = await service.analyze("python", b"x" * 100)
        assert any(d.code == "file-too-large" for d in big.diagnostics)
        assert not service._cache
        # a subsequent, correctly sized request works and is cached
        good = await service.analyze("python", b"x = 1\n")
        assert not good.degraded and service._cache

    asyncio.run(run())


def test_incremental_degraded_not_cached_but_full_equivalent():
    import hashlib

    async def run():
        service = make_service()
        old = b'x = "ab"\n'
        new = b'x = "a\n'
        inc = await service.apply_edit(
            "python", old,
            TextEdit(Position(0, 8), Position(0, 9), ""),
            new,
        )
        assert inc.result.degraded
        assert any(d.code == "syntax-error" for d in inc.result.diagnostics)
        # the degraded NEW document must not be cached (old clean doc may be)
        new_key = ("python", hashlib.sha256(new).hexdigest())
        assert new_key not in service._cache
        # affected range explains the edit (line 0 is affected)
        assert inc.affected.start.line == 0

    asyncio.run(run())


def test_unknown_language_explicit_error_not_silent_degrade():
    async def run():
        service = make_service()
        with pytest.raises(LanguageNotSupportedError) as exc:
            await service.analyze("cobol", b"x")
        assert exc.value.code == "language-not-supported"

    asyncio.run(run())


def test_enabled_set_isolates_languages():
    async def run():
        service = make_service(languages=frozenset({"json"}))
        with pytest.raises(LanguageNotSupportedError):
            await service.analyze("python", b"x = 1\n")
        ok = await service.analyze("json", b'{"a":1}')
        assert not ok.degraded

    asyncio.run(run())
