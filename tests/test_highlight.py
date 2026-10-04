import asyncio

from tests.conftest import JSON_SAMPLE, PY_SAMPLE, make_service


def _slice(source, rng):
    lines = source.split("\n")
    if rng.start.line == rng.end.line:
        return lines[rng.start.line][rng.start.col : rng.end.col]
    parts = [lines[rng.start.line][rng.start.col :]]
    parts.extend(lines[rng.start.line + 1 : rng.end.line])
    parts.append(lines[rng.end.line][: rng.end.col])
    return "\n".join(parts)


def test_highlights_non_overlapping_and_ordered(py):
    result = py.analyze(PY_SAMPLE)
    prev_end = None
    for h in result.highlights:
        assert h.range.start < h.range.end
        if prev_end is not None:
            assert prev_end <= h.range.start, "highlights must not overlap"
        prev_end = h.range.end


def test_highlights_reconstruct_source(py, js):
    for plugin, sample in ((py, PY_SAMPLE), (js, JSON_SAMPLE)):
        result = plugin.analyze(sample)
        for h in result.highlights:
            text = _slice(sample, h.range)
            assert text, "highlight range must map back to non-empty source"
            # keyword tokens must slice to real keywords etc.
            if h.category == "number":
                assert any(ch.isdigit() for ch in text)


def test_declaration_categories(py):
    result = py.analyze(PY_SAMPLE)
    decls = {
        _slice(PY_SAMPLE, h.range): h.category
        for h in result.highlights
        if h.category.endswith(".declaration")
    }
    assert decls.get("Animal") == "class.declaration"
    assert decls.get("speak") == "function.declaration"
    assert decls.get("make_animal") == "function.declaration"


def test_deterministic_repeated_runs(py):
    first = py.analyze(PY_SAMPLE)
    for _ in range(5):
        again = py.analyze(PY_SAMPLE)
        assert [(h.range, h.category) for h in again.highlights] == [
            (h.range, h.category) for h in first.highlights
        ]


def test_concurrent_requests_identical():
    async def run():
        service = make_service()
        data = PY_SAMPLE.encode()
        results = await asyncio.gather(
            *[service.analyze("python", data) for _ in range(50)]
        )
        assert service.compute_count == 1, "concurrent requests must coalesce"
        sig = [[(h.range.start, h.range.end, h.category) for h in r.highlights] for r in results]
        assert all(s == sig[0] for s in sig)

    asyncio.run(run())
