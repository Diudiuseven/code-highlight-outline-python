"""JSON backend: tiling, outline with keys/indexes, tolerant errors."""

from codeview.languages.json_lang import JsonBackend, ANONYMOUS

BACKEND = JsonBackend()


def test_valid_json_highlights_and_reconstruct():
    text = '{"name": "bob", "age": 3, "ok": true, "tags": [1, 2]}\n'
    a = BACKEND.analyze(text)
    assert "".join(text[s.start:s.end] for s in a.spans) == text
    cats = {s.category for s in a.spans}
    assert {"property", "string", "number", "keyword", "punctuation"} <= cats


def test_outline_keys_and_array_indexes():
    text = '{"a": {"b": 1}, "list": [10, 20]}'
    a = BACKEND.analyze(text)
    root = a.symbols[0]
    assert root.name == ANONYMOUS
    assert root.kind == "object"
    keys = {c.name: c.kind for c in root.children}
    assert keys == {"a": "property", "list": "property"}
    nested = root.children[0]
    assert nested.children[0].name == "b"
    arr = root.children[1]
    assert arr.children[0].kind == "number"
    assert [c.name for c in arr.children] == ["0", "1"]


def test_format_idempotent_and_preserves_semantics():
    import json

    text = '{"b":1,"a":[1 ,2] , "u":"\\u4e2d\\u6587"}'
    a = BACKEND.analyze(text)
    once = a.formatted
    twice = BACKEND.analyze(once).formatted
    assert once == twice
    assert json.loads(once) == json.loads(text)
    # unicode is preserved, not escaped
    assert "中文" in once


def test_invalid_json_degrades_with_diagnostic():
    text = '{"a": 1, "b": }\n'
    a = BACKEND.analyze(text)
    assert len(a.diagnostics) == 1
    d = a.diagnostics[0]
    assert d.reason == "syntax_error"
    assert d.range.start_pos.line == 1
    # still tiled, still returned, no crash
    assert "".join(text[s.start:s.end] for s in a.spans) == text
    # formatting falls back to newline-normalized original
    assert a.formatted == text


def test_crlf_invalid_fallback():
    text = 'not json\r\nx'
    a = BACKEND.analyze(text)
    assert a.diagnostics
    assert a.formatted == "not json\nx"


def test_empty_document():
    a = BACKEND.analyze("")
    assert a.spans == ()
    assert a.symbols == ()
    assert a.formatted == ""


def test_regions_single():
    text = '{"a": 1}'
    regions = BACKEND.split_regions(text)
    assert len(regions) == 1
