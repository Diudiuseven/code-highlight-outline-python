import ast
import json

from tests.conftest import JSON_SAMPLE, PY_SAMPLE


def test_python_format_idempotent(py):
    messy = "import   os\r\n\r\n\r\n\r\n\tx = 1   \r\n"
    once = py.analyze(messy).formatted
    twice = py.analyze(once).formatted
    assert once == twice
    assert "\r" not in once
    assert once.endswith("\n") and not once.endswith("\n\n")
    assert "\t" not in once.split("\n")[4]


def test_python_format_preserves_semantics(py):
    formatted = py.analyze(PY_SAMPLE).formatted
    assert ast.dump(ast.parse(formatted)) == ast.dump(ast.parse(PY_SAMPLE))


def test_python_format_keeps_multiline_strings(py):
    src = 's = """keep  trailing   \n\n\n\ninside"""\nx = 1\n'
    formatted = py.analyze(src).formatted
    assert 'keep  trailing   \n\n\n\ninside' in formatted
    assert ast.dump(ast.parse(formatted)) == ast.dump(ast.parse(src))


def test_json_format_idempotent_and_equal(js):
    once = js.analyze(JSON_SAMPLE).formatted
    twice = js.analyze(once).formatted
    assert once == twice
    assert json.loads(once) == json.loads(JSON_SAMPLE)


def test_json_format_preserves_duplicate_keys(js):
    formatted = js.analyze('{"a":1,"a":2}').formatted
    assert formatted.count('"a"') == 2
