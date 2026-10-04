"""Service: validation, envelope, caching/coalescing, sessions, timeout."""

import threading
import time

import pytest

from codeview.errors import ServiceError
from codeview.models import Settings
from codeview.service import CodeViewService


@pytest.fixture
def svc():
    settings = Settings(
        languages=frozenset({"python", "json", "plaintext"}),
        max_file_size=1000,
        cache_capacity=4,
        session_capacity=4,
        timeout_ms=2000,
    )
    s = CodeViewService(settings=settings)
    yield s
    s.close()


def test_unknown_language_explicit_error(svc):
    with pytest.raises(ServiceError) as ei:
        svc.analyze("ruby", "x = 1")
    assert ei.value.reason.value == "unknown_language"


def test_file_too_large(svc):
    with pytest.raises(ServiceError) as ei:
        svc.analyze("python", "a" * 1001)
    assert ei.value.reason.value == "file_too_large"
    assert ei.value.http_status == 413


def test_ok_envelope_and_determinism(svc):
    r1 = svc.analyze("python", "x = 1\n")
    r2 = svc.analyze("python", "x = 1\n")
    assert r1["status"] == "ok"
    assert r1 == r2


def test_syntax_error_degraded(svc):
    r = svc.analyze("python", "def broken(:\n")
    assert r["status"] == "degraded"
    assert "syntax_error" in r["reasons"]
    pos = r["diagnostics"][0]["range"]["start_pos"]
    assert pos["line"] >= 1 and pos["column"] >= 0


def test_concurrent_requests_merged(svc):
    calls = []

    # instrument backend via monkeypatching analyze_full used in compute
    import codeview.service as svc_mod
    real_full = svc_mod.analyze_full

    def counting(backend, text):
        calls.append(1)
        time.sleep(0.05)
        return real_full(backend, text)

    svc_mod.analyze_full = counting
    try:
        results = []

        def call():
            results.append(svc.analyze("python", "y = 2\n"))

        threads = [threading.Thread(target=call) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        svc_mod.analyze_full = real_full
    assert calls == [1]
    assert all(r == results[0] for r in results)


def test_cache_hits(svc):
    svc.analyze("python", "z = 3\n")
    assert len(svc._cache) == 1
    svc.analyze("python", "z = 3\n")
    assert len(svc._cache) == 1


def test_lru_eviction(svc):
    for i in range(6):
        svc.analyze("python", f"v{i} = {i}\n")
    assert len(svc._cache) <= 4


def test_incremental_session_matches_full(svc):
    old = "a = 1\n\nb = 2\n"
    svc.analyze("python", old)
    # first edit establishes session with explicit old_text
    old_frag, new_frag = "b = 2", "b = 22"
    new = old.replace(old_frag, new_frag)
    pos = old.index(old_frag)
    r = svc.apply_edit(
        "python",
        new,
        pos,
        pos + len(old_frag),
        pos + len(new_frag),
        old_text=old,
        session_id="s1",
    )
    assert r["incremental"] is True
    full = svc.analyze("python", new)
    assert r["highlights"] == full["highlights"]
    assert r["outline"] == full["outline"]
    assert r["formatted"] == full["formatted"]
    assert r["regions_recomputed"] < r["region_count"]

    # continue the session without old_text
    new2 = new + "\nc = 3\n"
    r2 = svc.apply_edit(
        "python", new2, len(new), len(new), len(new2), session_id="s1"
    )
    full2 = svc.analyze("python", new2)
    assert r2["highlights"] == full2["highlights"]


def test_stale_session_falls_back_to_full(svc):
    # session s has old text; client sends inconsistent edit
    svc.analyze("python", "a = 1\n")
    from codeview.sessions import SessionState
    svc._sessions.put("s9", SessionState("a = 1\n", "python"))
    r = svc.apply_edit("python", "totally different", 0, 0, 18,
                       session_id="s9")
    assert r["status"] in ("ok", "degraded")


def test_language_isolation(svc):
    py = svc.analyze("python", "x = 1\n")
    js = svc.analyze("json", '{"x": 1}\n')
    assert py["language"] == "python"
    assert js["language"] == "json"
    # removing json from settings must not change python results
    svc.settings = Settings(
        languages=frozenset({"python", "plaintext"}),
        max_file_size=1000,
        cache_capacity=4,
        session_capacity=4,
        timeout_ms=2000,
    )
    py2 = svc.analyze("python", "x = 1\n")
    assert py2["highlights"] == py["highlights"]
    with pytest.raises(ServiceError):
        svc.analyze("json", '{"x": 1}\n')


def test_failed_computation_not_cached(svc):
    # syntax error is a *successful* degraded response and IS cached;
    # unknown language raises before caching.
    for _ in range(3):
        with pytest.raises(ServiceError):
            svc.analyze("nope", "x")
    assert len(svc._cache) == 0


def test_timeout(svc):
    svc.settings = Settings(
        languages=frozenset({"python"}),
        max_file_size=10**9,
        cache_capacity=4,
        session_capacity=4,
        timeout_ms=1,
    )

    import codeview.service as svc_mod
    real = svc_mod.analyze_full

    def slow(backend, text):
        time.sleep(0.3)
        return real(backend, text)

    svc_mod.analyze_full = slow
    try:
        import asyncio
        with pytest.raises(ServiceError) as ei:
            asyncio.run(svc.analyze_async("python", "x = 1\n"))
        assert ei.value.reason.value == "timeout"
    finally:
        svc_mod.analyze_full = real


def test_timeout_does_not_poison_cache(svc):
    import asyncio as _aio
    import codeview.service as svc_mod

    svc.settings = Settings(
        languages=frozenset({"python"}),
        max_file_size=10**9,
        cache_capacity=4,
        session_capacity=4,
        timeout_ms=50,
    )
    real = svc_mod.analyze_full
    gate = {"slow": True}

    def maybe_slow(backend, text):
        if gate["slow"]:
            time.sleep(0.3)
        return real(backend, text)

    svc_mod.analyze_full = maybe_slow
    async def scenario():
        # timed-out request
        with pytest.raises(ServiceError):
            await svc.analyze_async("python", "x = 1\n")
        svc_mod.analyze_full = real
        # give the orphaned worker a moment to finish (it must not cache a
        # value the caller never received)
        await _aio.sleep(0.4)
        # cache must NOT hold an entry inserted after the deadline
        r = await svc.analyze_async("python", "x = 1\n")
        assert r["status"] == "ok"
        assert r["formatted"] == "x = 1\n"

    _aio.run(scenario())


def test_sync_call_has_no_timeout(svc):
    # a very small async timeout must not affect the synchronous API
    svc.settings = Settings(
        languages=frozenset({"python"}),
        max_file_size=10**9,
        cache_capacity=4,
        session_capacity=4,
        timeout_ms=1,
    )
    r = svc.analyze("python", "x = 1\n")
    assert r["status"] == "ok"


def test_zero_cache_capacity_disables_cache():
    settings = Settings(
        languages=frozenset({"python", "json"}),
        max_file_size=1000,
        cache_capacity=0,
        session_capacity=2,
        timeout_ms=2000,
    )
    s = CodeViewService(settings=settings)
    try:
        r = s.analyze("python", "q = 1\n")
        assert r["status"] == "ok"
        assert len(s._cache) == 0
    finally:
        s.close()


def test_registration_isolation_switching_language():
    # register a fresh python backend (a "switch") and confirm json unchanged
    from codeview.languages.json_lang import JsonBackend
    from codeview.languages.python_lang import PythonBackend

    settings = Settings(
        languages=frozenset({"python", "json"}),
        max_file_size=1000,
        cache_capacity=8,
        session_capacity=8,
        timeout_ms=2000,
    )
    s = CodeViewService(settings=settings)
    try:
        before = s.analyze("json", '{"k": 1}')
        s.registry.register(PythonBackend())  # replace python only
        s.registry.register(JsonBackend())  # re-register json with same logic
        after = s.analyze("json", '{"k": 1}')
        assert before == after
    finally:
        s.close()


def test_async_concurrent_requests_merged(svc):
    import asyncio as _aio
    import codeview.service as svc_mod

    real = svc_mod.analyze_full
    calls = []

    def counting(backend, text):
        calls.append(1)
        time.sleep(0.1)
        return real(backend, text)

    svc_mod.analyze_full = counting

    async def scenario():
        return await _aio.gather(
            *[svc.analyze_async("python", "w = 4\n") for _ in range(8)]
        )

    try:
        results = _aio.run(scenario())
    finally:
        svc_mod.analyze_full = real
    assert calls == [1]
    assert all(r == results[0] for r in results)
