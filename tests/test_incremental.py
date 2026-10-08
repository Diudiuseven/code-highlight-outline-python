import random

from app.incremental import affected_range, analyze_edit
from app.models import Position, TextEdit
from tests.conftest import PY_SAMPLE


def _sig(result):
    return (
        [(h.range.start, h.range.end, h.category) for h in result.highlights],
        [s.symbol_id for s in result.outline],
        result.formatted,
    )


def _apply_edit(text, edit):
    lines = text.split("\n")
    start_off = sum(len(l) + 1 for l in lines[: edit.start.line]) + edit.start.col
    end_off = sum(len(l) + 1 for l in lines[: edit.end.line]) + edit.end.col
    return text[:start_off] + edit.new_text + text[end_off:]


def _check(plugin, old, edit):
    new = _apply_edit(old, edit)
    old_result = plugin.analyze(old)
    result, used, affected = analyze_edit(plugin, old, new, old_result, edit)
    full = plugin.analyze(new)
    assert _sig(result) == _sig(full), "incremental result must equal full recompute"
    return "spliced" if used else "fallback"


def test_incremental_single_line_edit(py):
    edit = TextEdit(Position(6, 0), Position(6, 0), "# comment\n")
    assert _check(py, PY_SAMPLE, edit) == "spliced"


def test_incremental_multiline_insert(py):
    edit = TextEdit(Position(0, 0), Position(0, 0), "import sys\nimport re\n")
    assert _check(py, PY_SAMPLE, edit) == "spliced"


def test_incremental_delete_lines(py):
    edit = TextEdit(Position(1, 0), Position(3, 0), "")
    assert _check(py, PY_SAMPLE, edit) == "spliced"


def test_incremental_edit_inside_string(py):
    line = PY_SAMPLE.split("\n").index('    Multiline docstring with trailing spaces:   ')
    edit = TextEdit(Position(line, 4), Position(line, 10), "XXXXXX")
    assert _check(py, PY_SAMPLE, edit) == "spliced"


def test_incremental_syntax_break_falls_back(py):
    src = 'x = "abc"\ny = 2\n'
    edit = TextEdit(Position(0, 4), Position(0, 5), "")  # unterminate string
    assert _check(py, src, edit) == "fallback"


def test_incremental_fuzz_matches_full(py):
    rng = random.Random(20241004)
    lines = PY_SAMPLE.split("\n")
    stats = {"spliced": 0, "fallback": 0}
    for _ in range(200):
        line = rng.randrange(len(lines))
        col = rng.randrange(len(lines[line]) + 1)
        end_line = min(line + rng.randrange(3), len(lines) - 1)
        end_col = rng.randrange(len(lines[end_line]) + 1)
        if (end_line, end_col) < (line, col):
            end_line, end_col = line, col
        piece = rng.choice(["x", "1", " ", "\n", "def g():\n    pass\n", '"', ")", "\t"])
        edit = TextEdit(Position(line, col), Position(end_line, end_col), piece)
        stats[_check(py, PY_SAMPLE, edit)] += 1
    # aggressive random edits often break syntax (-> full recompute), but a
    # substantial share must still be served incrementally
    assert stats["spliced"] > 50


def test_incremental_fuzz_realistic_typing(py):
    """Benign, typing-like edits should almost always splice."""
    rng = random.Random(99)
    lines = PY_SAMPLE.split("\n")
    stats = {"spliced": 0, "fallback": 0}
    for _ in range(200):
        line = rng.randrange(len(lines))
        col = rng.randrange(len(lines[line]) + 1)
        piece = rng.choice(["x", "1", " ", "\n", "y = 2\n", "_"])
        edit = TextEdit(Position(line, col), Position(line, col), piece)
        stats[_check(py, PY_SAMPLE, edit)] += 1
    assert stats["spliced"] >= 100, stats


def test_incremental_fuzz_json(js):
    rng = random.Random(7)
    src = '{\n  "a": 1,\n  "b": [1, 2, 3],\n  "c": {"d": "e"}\n}\n'
    lines = src.split("\n")
    for _ in range(100):
        line = rng.randrange(len(lines))
        col = rng.randrange(len(lines[line]) + 1)
        piece = rng.choice(["5", "x", " ", "\n", "{", '"'])
        edit = TextEdit(Position(line, col), Position(line, col), piece)
        _check(js, src, edit)


def test_affected_range_reported(py):
    old = PY_SAMPLE
    line_no = old.split("\n").index("CONST = 42  # answer")
    edit = TextEdit(Position(line_no, 0), Position(line_no, 0), "import sys\n")
    new = old.replace("CONST", "import sys\nCONST", 1)
    rng = affected_range(old, edit, new)
    assert rng.start.line == line_no and rng.start.col == 0
    assert rng.end.line == line_no + 1
