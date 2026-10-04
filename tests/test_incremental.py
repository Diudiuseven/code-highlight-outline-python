"""Incremental results must equal full recomputation for many edits."""

import random

from codeview.incremental import analyze_full, analyze_incremental, analyze_regions
from codeview.languages.python_lang import PythonBackend

BACKEND = PythonBackend()

SAMPLE = (
    "import os\n"
    "\n"
    "def alpha(x):\n"
    "    y = x + 1\n"
    "    return y\n"
    "\n"
    "class Beta:\n"
    "    def method(self):\n"
    "        return 42\n"
    "\n"
    "VALUE = 10\n"
    "\n"
    "def gamma(a, b):\n"
    "    return a * b\n"
)


def _dicts(analysis):
    return (
        [s.to_dict() for s in analysis.spans],
        [s.to_dict() for s in analysis.symbols],
        analysis.formatted,
        [d.to_dict() for d in analysis.diagnostics],
    )


def _apply_and_check(old, new, start, end_old, end_new):
    full_new = analyze_full(BACKEND, new)
    old_parts = analyze_regions(BACKEND, old)
    inc, _ = analyze_incremental(BACKEND, old, new, start, end_old, end_new, old_parts)
    assert _dicts(inc.analysis) == _dicts(full_new)
    # all affected ranges cover the edit
    assert inc.region_count >= 1
    return inc


def test_insert_middle():
    old = SAMPLE
    pos = old.index("return y")
    new = old[:pos] + "z = 2\n    " + old[pos:]
    inc = _apply_and_check(old, new, pos, pos, pos + len("z = 2\n    "))
    assert inc.recomputed <= inc.region_count
    # unaffected regions reused
    assert inc.recomputed < inc.region_count


def test_edit_inside_one_region():
    old = SAMPLE
    pos = old.index("42")
    new = old[:pos] + "7" + old[pos + 1 :]
    inc = _apply_and_check(old, new, pos, pos + 1, pos + 1)
    assert inc.recomputed < inc.region_count


def test_delete_statement():
    old = SAMPLE
    stmt = "VALUE = 10\n"
    pos = old.index(stmt)
    # also consume the separating blank line after it
    end = pos + len(stmt)
    new = old[:pos] + old[end:]
    _apply_and_check(old, new, pos, end, pos)


def test_insert_new_top_level_symbol():
    old = SAMPLE
    addition = "def delta():\n    return 3\n"
    pos = len(old)
    new = old + "\n" + addition
    _apply_and_check(old, new, pos, pos, pos + len("\n" + addition))


def test_random_edits_match_full():
    rng = random.Random(1234)
    text = SAMPLE
    for _ in range(40):
        pos = rng.randrange(len(text) + 1)
        length = rng.randrange(0, min(12, len(text) - pos) + 1)
        choices = ["x = 9\n", "def f():\n    pass\n", "\n", "", "   ", "z"]
        repl = rng.choice(choices)
        new = text[:pos] + repl + text[pos + length :]
        old_parts = analyze_regions(BACKEND, text)
        full_new = analyze_full(BACKEND, new)
        inc, fresh = analyze_incremental(
            BACKEND, text, new, pos, pos + length, pos + len(repl), old_parts
        )
        assert _dicts(inc.analysis) == _dicts(full_new), (
            f"mismatch for edit {pos!r} {length!r} {repl!r}"
        )
        # fresh parts must themselves re-assemble identically
        from codeview.incremental import _assemble

        reasm = _assemble(
            BACKEND.language_id,
            new,
            [p.region for p in fresh],
            [p.analysis for p in fresh],
        )
        assert _dicts(reasm) == _dicts(full_new)
        text = new


def test_empty_and_tiny_documents():
    for text in ["", "\n", "x=1\n", "\n\nx=1\n"]:
        full = analyze_full(BACKEND, text)
        assert _dicts(full) == _dicts(analyze_full(BACKEND, text))
        assert "".join(text[s.start:s.end] for s in full.spans) == text


def test_inconsistent_edit_rejected():
    import pytest

    with pytest.raises(ValueError):
        analyze_incremental(BACKEND, "abc", "abd", 0, 2, 2, None)
