"""Acceptance: highlight ranges tile the WHOLE source.

Sorting the ranges by start, slicing the original text of each range in
order and concatenating reproduces the submitted source verbatim, including
every space, tab, newline and indentation character. Ranges never overlap
and never leave a gap.
"""
import pytest

from tests.conftest import JSON_SAMPLE, PY_SAMPLE


def slice_of(source, rng):
    lines = source.split("\n")
    if rng.start.line == rng.end.line:
        return lines[rng.start.line][rng.start.col : rng.end.col]
    parts = [lines[rng.start.line][rng.start.col :]]
    parts.extend(lines[rng.start.line + 1 : rng.end.line])
    parts.append(lines[rng.end.line][: rng.end.col])
    return "\n".join(parts)


def assert_tiles(source, highlights):
    hs = sorted(highlights, key=lambda h: (h.range.start, h.range.end))
    for h in hs:
        assert h.range.start < h.range.end, "ranges must be non-empty"
    for prev, nxt in zip(hs, hs[1:]):
        assert prev.range.end <= nxt.range.start, "ranges must not overlap"
    reconstructed = "".join(slice_of(source, h.range) for h in hs)
    assert reconstructed == source, (
        "concatenated ranges must reproduce the source byte for byte\n"
        f"--- reconstructed ---\n{reconstructed!r}\n--- source ---\n{source!r}"
    )


def test_python_sample_tiles_completely(py):
    result = py.analyze(PY_SAMPLE)
    assert_tiles(PY_SAMPLE, result.highlights)


def test_json_sample_tiles_completely(js):
    result = js.analyze(JSON_SAMPLE)
    assert_tiles(JSON_SAMPLE, result.highlights)


@pytest.mark.parametrize(
    "source",
    [
        "x = 1\n",
        "    leading spaces and trailing    \n",
        "\t\n  \n\n",  # blank / whitespace-only lines
        "no_newline_at_end = True",
        "a = 'one'\nb\t=\t2  \r\n",  # tabs + CR (source kept as-is in ranges)
        's = """multi\nline\n   string with blanks   """\nx=1\n',
        "def broken(:\n  pass\n",  # syntax error still covers the whole text
        "# only a comment\n",
    ],
)
def test_python_highlights_reconstruct_source_verbatim(py, source):
    result = py.analyze(source)
    assert_tiles(source, result.highlights)


@pytest.mark.parametrize(
    "source",
    [
        '  {"a" : 1 , "b": [ 2, 3 ]}\n  ',
        '{"a":1}\n',
        '{ broken',  # malformed: available text still fully covered
        '{\n  "a": ,\n}',
        "true false null",
    ],
)
def test_json_highlights_reconstruct_source_verbatim(js, source):
    result = js.analyze(source)
    assert_tiles(source, result.highlights)


def test_empty_source_has_no_ranges_and_reconstructs(py, js):
    for plugin in (py, js):
        result = plugin.analyze("")
        assert result.highlights == []
