from tests.conftest import JSON_SAMPLE, PY_SAMPLE


def _flat(symbols):
    out = []
    for s in symbols:
        out.append(s)
        out.extend(_flat(s.children))
    return out


def test_outline_nesting(py):
    outline = py.analyze(PY_SAMPLE).outline
    by_id = {s.symbol_id: s for s in _flat(outline)}
    animal = by_id["module/Animal"]
    assert animal.kind.value == "class"
    child_ids = [c.symbol_id for c in animal.children]
    assert "module/Animal/speak" in child_ids
    assert "module/Animal/legs" in child_ids
    dog = by_id["module/Dog"]
    assert dog.children[0].kind.value == "method"


def test_outline_sorted_by_position(py):
    outline = py.analyze(PY_SAMPLE).outline
    starts = [(s.range.start.line, s.range.start.col) for s in outline]
    assert starts == sorted(starts)


def test_duplicate_names_get_stable_ids(py):
    outline = py.analyze(PY_SAMPLE).outline
    dups = [s for s in outline if s.name == "dup"]
    assert [s.symbol_id for s in dups] == ["module/dup", "module/dup#2"]
    # ranges differ, ids deterministic across runs
    again = py.analyze(PY_SAMPLE).outline
    assert [s.symbol_id for s in again if s.name == "dup"] == ["module/dup", "module/dup#2"]


def test_anonymous_lambda(py):
    outline = py.analyze(PY_SAMPLE).outline
    flat = _flat(outline)
    lambdas = [s for s in flat if s.name == "<lambda>"]
    assert len(lambdas) == 1
    assert lambdas[0].symbol_id.startswith("module/make_animal/<lambda>@")


def test_json_outline_and_duplicate_keys(js):
    result = js.analyze('{"a": 1, "a": 2, "b": {"c": [true, {"d": 1}]}}')
    flat = _flat(result.outline)
    ids = [s.symbol_id for s in flat]
    assert ids == ["root/a", "root/a#2", "root/b", "root/b/c", "root/b/c/d"]
    assert all(s.kind.value == "key" for s in flat)


def test_json_arrays_transparent(js):
    result = js.analyze(JSON_SAMPLE)
    names = [s.name for s in _flat(result.outline)]
    assert "nested" in names and "x" in names
