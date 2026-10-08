"""Incremental results must match a full recompute for every random edit,
for both languages, including exact highlight reconstruction and outline.
"""
import random

from app.incremental import analyze_edit
from app.models import Position, TextEdit
from tests.conftest import assert_partitions

PY_BASE = (
    "import os\n\nCONST = 42  # answer\n\n\n"
    "class Animal:\n"
    '    """doc\n    across lines"""\n'
    "    legs = 4\n\n"
    "    def speak(self):\n"
    "        return f'{self.legs} legs'\n\n\n"
    "def make(kind='x'):\n"
    "    if kind == 'x':\n"
    "        return Animal()\n"
    "    return None\n"
)

JSON_BASE = '{\n  "name": "svc",\n  "replicas": 3,\n  "tags": ["a", "b"],\n' \
            '  "nested": {"x": 1.5e2, "ok": true, "z": null}\n}\n'


def _apply(text, edit):
    lines = text.split("\n")
    so = sum(len(l) + 1 for l in lines[: edit.start.line]) + edit.start.col
    eo = sum(len(l) + 1 for l in lines[: edit.end.line]) + edit.end.col
    return text[:so] + edit.new_text + text[eo:]


def _sig(result):
    return (
        [
            (h.range.start.line, h.range.start.col,
             h.range.end.line, h.range.end.col, h.category)
            for h in result.highlights
        ],
        [
            (s.symbol_id, s.name, s.kind.value,
             (s.range.start.line, s.range.start.col))
            for s in result.outline
        ],
        result.formatted,
        [(d.code, (d.position.line, d.position.col) if d.position else None)
         for d in result.diagnostics],
        result.degraded,
    )


def _fuzz(plugin, base, seed, pieces, iterations=250):
    rng = random.Random(seed)
    spliced = 0
    for _ in range(iterations):
        lines = base.split("\n")
        line = rng.randrange(len(lines))
        col = rng.randrange(len(lines[line]) + 1)
        end_line = min(line + rng.randrange(3), len(lines) - 1)
        end_col = rng.randrange(len(lines[end_line]) + 1)
        if (end_line, end_col) < (line, col):
            end_line, end_col = line, col
        edit = TextEdit(
            Position(line, col), Position(end_line, end_col),
            rng.choice(pieces),
        )
        new = _apply(base, edit)
        old_result = plugin.analyze(base)
        inc, used = analyze_edit(plugin, base, new, old_result, edit)
        full = plugin.analyze(new)
        assert _sig(inc) == _sig(full), (edit, base[:40])
        # highlights always partition the new text, even when degraded
        assert_partitions(new, inc.highlights)
        assert_partitions(new, full.highlights)
        spliced += used
    return spliced


def test_python_random_edits_match_full(py):
    pieces = ["x", "1", " ", "\n", "def g():\n    pass\n", '"', ")",
              "\t", "_", "# c\n", "f'", "2.0", "=", "]", "'''"]
    n = _fuzz(py, PY_BASE, seed=20261008, pieces=pieces)
    assert n > 50, f"expected many splices, got {n}"


def test_json_random_edits_match_full(js):
    pieces = ["5", "x", " ", "\n", "{", '"', ":", ",", "]", "}",
              "true", "null", '"v"', "."]
    n = _fuzz(js, JSON_BASE, seed=4242, pieces=pieces)
    assert n > 10, f"expected some splices, got {n}"


def test_json_benign_edits_splice(js):
    """Typing whitespace/values into a valid JSON doc should mostly splice."""
    n = _fuzz(js, JSON_BASE, seed=99,
              pieces=[" ", "\n", "7", "e", "9"], iterations=150)
    assert n >= 25, f"benign edits should splice often, got {n}"


def test_incremental_chain_of_edits_python(py):
    """A sequence of edits, each based on the previous result, stays exact."""
    rng = random.Random(7)
    text = PY_BASE
    for _ in range(60):
        lines = text.split("\n")
        line = rng.randrange(len(lines))
        col = rng.randrange(len(lines[line]) + 1)
        edit = TextEdit(Position(line, col), Position(line, col),
                        rng.choice(["x", " ", "\n", "_", "1"]))
        new = _apply(text, edit)
        old_result = py.analyze(text)
        inc, _ = analyze_edit(py, text, new, old_result, edit)
        assert _sig(inc) == _sig(py.analyze(new))
        assert_partitions(new, inc.highlights)
        text = new
