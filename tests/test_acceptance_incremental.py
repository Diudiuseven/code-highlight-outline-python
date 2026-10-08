"""Acceptance: after ANY random edit, incremental analysis equals a full
recomputation entry by entry (highlights, outline, formatting), the
incremental highlights still tile the new source verbatim, and the reported
affected range explains the edit.
"""
import random

import pytest

from app.incremental import analyze_edit
from app.models import Position, TextEdit


def apply_edit(text, edit):
    lines = text.split("\n")
    so = sum(len(l) + 1 for l in lines[: edit.start.line]) + edit.start.col
    eo = sum(len(l) + 1 for l in lines[: edit.end.line]) + edit.end.col
    return text[:so] + edit.new_text + text[eo:]


def slice_of(source, rng):
    lines = source.split("\n")
    if rng.start.line == rng.end.line:
        return lines[rng.start.line][rng.start.col : rng.end.col]
    parts = [lines[rng.start.line][rng.start.col :]]
    parts.extend(lines[rng.start.line + 1 : rng.end.line])
    parts.append(lines[rng.end.line][: rng.end.col])
    return "\n".join(parts)


def signature(result):
    return (
        [(h.range.start, h.range.end, h.category) for h in result.highlights],
        [
            (s.name, str(s.kind), s.symbol_id, s.range.start, s.range.end)
            for s in result.outline
        ],
        result.formatted,
    )


def assert_tiles(source, highlights):
    hs = sorted(highlights, key=lambda h: (h.range.start, h.range.end))
    for prev, nxt in zip(hs, hs[1:]):
        assert prev.range.end <= nxt.range.start
    assert "".join(slice_of(source, h.range) for h in hs) == source


PY_SOURCE = '''import os


CONST = 42  # answer


class Animal:
    """multi
    line
    docstring"""

    legs = 4

    def speak(self):
        raise NotImplementedError


def make(kind):
    f = lambda: Animal()
    return f
'''

JSON_SOURCE = (
    '{\n  "name": "svc",\n  "nums": [1, 2, 3],\n'
    '  "nested": {"x": 1.5e2, "flag": true, "nil": null}\n}\n'
)


def run_fuzz(plugin, source, seed, iterations, pieces, multi_line):
    rng = random.Random(seed)
    lines = source.split("\n")
    stats = {"spliced": 0, "fallback": 0}
    for i in range(iterations):
        line = rng.randrange(len(lines))
        col = rng.randrange(len(lines[line]) + 1)
        end_line, end_col = line, col
        if multi_line:
            end_line = min(line + rng.randrange(4), len(lines) - 1)
            end_col = rng.randrange(len(lines[end_line]) + 1)
            if (end_line, end_col) < (line, col):
                end_line, end_col = line, col
        piece = rng.choice(pieces)
        edit = TextEdit(Position(line, col), Position(end_line, end_col), piece)
        new = apply_edit(source, edit)

        old_result = plugin.analyze(source)
        inc, used, affected = analyze_edit(plugin, source, new, old_result, edit)
        full = plugin.analyze(new)

        assert signature(inc) == signature(full), (
            f"incremental != full (seed={seed}, iter={i}, used={used})\n"
            f"new source: {new!r}"
        )
        # tiling must survive the incremental path as well
        assert_tiles(new, inc.highlights)
        # the affected range must explain the edit (new-text coordinates):
        # it starts no later than the edit and stays inside the document,
        # and covers the inserted text's line span
        new_lines = new.split("\n")
        assert affected.start.line <= edit.start.line
        assert affected.start.col == 0
        assert 0 <= affected.end.line < len(new_lines)
        inserted_lines = piece.split("\n")
        expected_last = edit.start.line + len(inserted_lines) - 1
        if used:
            assert affected.start.line <= expected_last
            assert affected.end.line >= min(expected_last, len(new_lines) - 1)
        stats["spliced" if used else "fallback"] += 1
    return stats


def test_python_random_edits_match_full_recompute(py):
    stats = run_fuzz(
        py,
        PY_SOURCE,
        seed=20261008,
        iterations=400,
        pieces=[
            "x", "1", " ", "\n", "\t", "  ", "# note\n",
            "def g():\n    return 0\n", '"', ")", "y = 2\n", "_",
        ],
        multi_line=True,
    )
    assert stats["spliced"] > 50, stats


def test_json_random_edits_match_full_recompute(js):
    stats = run_fuzz(
        js,
        JSON_SOURCE,
        seed=4242,
        iterations=400,
        pieces=[
            "5", "x", " ", "\n", "{", '"', ":", ",", "[", "]", "true",
            "null", '"lookalike"', '"123"',
        ],
        multi_line=True,
    )
    assert stats["spliced"] > 20, stats


def test_json_no_trailing_newline_edits(js):
    run_fuzz(
        js,
        '{"a":1,"b":[2,3]}',
        seed=77,
        iterations=200,
        pieces=["5", " ", "\n", '"', "{", "]", "true", "null"],
        multi_line=False,
    )


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_python_typing_edits_are_stable(py, seed):
    # a substantial share of benign single-point edits must splice, and
    # every spliced OR fallback result equals the full recomputation
    stats = run_fuzz(
        py,
        PY_SOURCE,
        seed=seed,
        iterations=100,
        pieces=["x", "1", " ", "_", "\n", "z = 9\n"],
        multi_line=False,
    )
    assert stats["spliced"] >= 50, stats
