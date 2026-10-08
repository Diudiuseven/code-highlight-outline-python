"""Highlights must be an exact partition of the submitted source.

Slicing every highlighted range (in start order) and concatenating the
pieces must reproduce the source verbatim -- including whitespace, line
breaks and indentation -- with neither overlap nor gaps.
"""
from tests.conftest import (
    JSON_SAMPLE,
    PY_SAMPLE,
    assert_partitions,
    reconstruct,
)

SOURCES = [
    "",
    " ",
    "\n",
    "  \n\t\n",
    "\n\n\n",
    "x = 1\n",
    "x = 1",  # no trailing newline
    "\tx=1\r\ny = 2  \n",
    "# only a comment\n",
    "a = [1,\n     2,\n     3]\n",
    's = """multi\nline\nstring"""\n',
    'f = f"{name!r:>10} = {x:.2f}"\n',
    'f2 = f"""a {x}\nb {y}"""\n',
    "def g(\n    a,\n    b,\n):\n    return a + b\n",
    '   indented   \n\n\n    x=1   \n',
]

JSON_SOURCES = [
    JSON_SAMPLE,
    "{}",
    "[]",
    "{\n}",
    ' {\n  "a": 1,\n\n  "b": [true, null, false]\n}\n',
    '[1,\n 2,\n 3]',  # no trailing newline
    '{"中文": "值", "tab": "a\\tb"}\n',
]


def test_python_highlights_partition_every_source(py):
    for src in [PY_SAMPLE, *SOURCES]:
        result = py.analyze(src)
        assert_partitions(src, result.highlights)
        assert reconstruct(src, result.highlights) == src


def test_json_highlights_partition_every_source(js):
    for src in JSON_SOURCES:
        result = js.analyze(src)
        assert_partitions(src, result.highlights)
        assert reconstruct(src, result.highlights) == src


def test_python_degraded_input_still_partitioned(py):
    for src in ["def broken(:\n    pass\n", "x = '\n", ")\n"]:
        result = py.analyze(src)
        assert result.degraded
        assert_partitions(src, result.highlights)
