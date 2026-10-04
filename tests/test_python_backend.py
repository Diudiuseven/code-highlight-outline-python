"""Python backend: tiling, outline, formatting determinism."""

import threading

from codeview.languages.python_lang import PythonBackend

BACKEND = PythonBackend()


def test_spans_tile_and_reconstruct():
    text = "def foo():\n    return 1\n"
    a = BACKEND.analyze(text)
    assert "".join(text[s.start:s.end] for s in a.spans) == text
    # sorted and non-overlapping
    for prev, cur in zip(a.spans, a.spans[1:]):
        assert prev.end == cur.start
    cats = {s.category for s in a.spans}
    assert "keyword" in cats
    assert "function" in cats
    assert "number" in cats


def test_outline_basic():
    text = (
        "import os\n"
        "\n"
        "class C:\n"
        "    def m(self):\n"
        "        return 1\n"
        "\n"
        "def top(x=1):\n"
        "    return x\n"
    )
    a = BACKEND.analyze(text)
    kinds = [(s.name, s.kind) for s in a.symbols]
    assert ("C", "class") in kinds
    assert ("top", "function") in kinds
    cls = next(s for s in a.symbols if s.name == "C")
    assert [(c.name, c.kind) for c in cls.children] == [("m", "function")]
    # selection range covers the identifier exactly
    assert text[cls.selection_range.start:cls.selection_range.end] == "C"
    m = cls.children[0]
    assert text[m.selection_range.start:m.selection_range.end] == "m"


def test_duplicate_names_kept():
    text = "def f():\n    return 1\n\ndef f():\n    return 2\n"
    a = BACKEND.analyze(text)
    fs = [s for s in a.symbols if s.name == "f"]
    assert len(fs) == 2
    assert fs[0].range.start < fs[1].range.start


def test_format_idempotent_and_stable():
    text = "import  os\r\n\r\n\r\n\r\ndef foo( ) :\r\n    x=1   \r\n    return  x\r\n"
    a = BACKEND.analyze(text)
    once = a.formatted
    twice = BACKEND.analyze(once).formatted
    assert once == twice
    # CRLF normalized
    assert "\r" not in once
    # blank run collapsed to one blank line
    assert "\n\n\n" not in once
    # trailing whitespace removed
    assert all(not ln.rstrip(" \t").endswith(" ") for ln in once.split("\n"))


def test_format_preserves_multiline_string_blank_lines():
    text = 's = """a\n\n\nb"""\n'
    a = BACKEND.analyze(text)
    # blank lines inside the triple-quoted string are untouched
    assert 'a\n\n\nb' in a.formatted
    assert BACKEND.analyze(a.formatted).formatted == a.formatted


def test_syntax_error_does_not_fail():
    text = "def broken(:\n    pass\n"
    a = BACKEND.analyze(text)
    reasons = {d.reason for d in a.diagnostics}
    assert "syntax_error" in reasons
    # spans still tile the document
    assert "".join(text[s.start:s.end] for s in a.spans) == text
    diag = a.diagnostics[0]
    assert diag.range.start_pos.line >= 1


def test_concurrent_determinism():
    text = "x = 1\n\ndef f():\n    return x\n\nclass A:\n    pass\n"
    expected = BACKEND.analyze(text)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(BACKEND.analyze(text)))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for r in results:
        assert [s.to_dict() for s in r.spans] == [s.to_dict() for s in expected.spans]
        assert [s.to_dict() for s in r.symbols] == [s.to_dict() for s in expected.symbols]
        assert r.formatted == expected.formatted


def test_regions_partition_document():
    text = "a = 1\n\nb = 2\n\nc = 3\n"
    regions = BACKEND.split_regions(text)
    assert regions[0].start == 0
    assert regions[-1].end == len(text)
    for p, n in zip(regions, regions[1:]):
        assert p.end == n.start
    assert len(regions) >= 2


def test_blank_inside_function_is_not_a_boundary():
    text = "def f():\n    x = 1\n\n    return x\n"
    regions = BACKEND.split_regions(text)
    assert len(regions) == 1


def test_decorated_function_name_selection():
    text = "@dec\ndef f():\n    pass\n"
    a = BACKEND.analyze(text)
    f = next(s for s in a.symbols if s.name == "f")
    assert f.range.start_pos.line == 1  # covers decorator
    assert text[f.selection_range.start:f.selection_range.end] == "f"


def test_format_preserves_ast_semantics():
    import ast

    def dump(t):
        return ast.dump(ast.parse(t))

    samples = [
        "x=1\r\n\r\ny = 2  \n\n\n\nz = 'multi'\n",
        "import os,sys\n\ndef f( a ,b=1 ):\n    return a+b\n",
        "class A:\n    x=1\n\n\n\n    def m(self):\n        return self.x\n",
        "v = [i for i in range(3)]\n\n(s := 4)\n",
        "s = 'a\\rb'\n\nt = 1 # comment   \n",
    ]
    for src in samples:
        out = BACKEND.analyze(src).formatted
        assert dump(src) == dump(out), (src, out)
    big = "\n\n".join(
        f"def f{i}(a, b=2, *args, c=3):\n    x=a+b\n    return x if x else c"
        for i in range(100)
    )
    assert dump(big) == dump(BACKEND.analyze(big).formatted)


def test_json_format_preserves_value():
    import json

    from codeview.languages.json_lang import JsonBackend

    jb = JsonBackend()
    for text in ['{"a": 1, "b": [1, 2, {"c": 3}]}', "[true,false,null,1.5,-2,\"s\"]"]:
        out = jb.analyze(text).formatted
        assert json.loads(out) == json.loads(text)
