"""Cross-backend robustness: weird input never crashes, always tiles & idempotent."""

import pytest

from codeview.languages.json_lang import JsonBackend
from codeview.languages.plaintext import PlaintextBackend
from codeview.languages.python_lang import PythonBackend

BACKENDS = [PythonBackend(), JsonBackend(), PlaintextBackend()]
WEIRD = [
    "",
    "\x00",
    "￾",
    "def ",
    "{",
    "[1,",
    "'",
    "'''",
    "\n" * 5,
    "lambda",
    "a" * 5000,
    "\t\t\n  \r\n",
    "def f(:\n  pass\n",
    '{"a": 1, "b":}',
    "\r\n\r\n",
]


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.language_id)
@pytest.mark.parametrize("text", WEIRD)
def test_never_crashes_tiles_and_idempotent(backend, text):
    result = backend.analyze(text)
    # exact reconstruction
    assert "".join(text[s.start:s.end] for s in result.spans) == text
    # adjacent, ordered tiling
    for prev, cur in zip(result.spans, result.spans[1:]):
        assert prev.end == cur.start
        assert prev.start < prev.end
    # formatting idempotent
    once = result.formatted
    assert backend.analyze(once).formatted == once
