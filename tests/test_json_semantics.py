"""Formatting must preserve JSON semantics.

Strings that merely *look like* numbers, booleans or null stay strings
after formatting; number literal spellings, key order and duplicate keys
are preserved; repeated formatting is stable.
"""
import json

import pytest

from app.languages.json_lang import RawLiteral

# (source, key) pairs where the value is a STRING that looks like something
# else; formatting must keep it a quoted string.
STRING_LOOKALIKES = [
    ('{"v": "123"}', "v"),
    ('{"v": "-42"}', "v"),
    ('{"v": "1.5"}', "v"),
    ('{"v": "1e3"}', "v"),
    ('{"v": "1E-4"}', "v"),
    ('{"v": "0"}', "v"),
    ('{"v": "true"}', "v"),
    ('{"v": "false"}', "v"),
    ('{"v": "null"}', "v"),
    ('{"v": "NaN"}', "v"),
    ('["42", "true", "null", "1e3"]', None),
]


@pytest.mark.parametrize("src,key", STRING_LOOKALIKES)
def test_string_lookalikes_stay_strings(js, src, key):
    once = js.analyze(src).formatted
    orig = json.loads(src)
    out = json.loads(once)
    if key is not None:
        assert isinstance(orig[key], str)
        assert isinstance(out[key], str), f"{src} became {out[key]!r}"
        assert out[key] == orig[key]
    else:
        for a, b in zip(orig, out):
            assert isinstance(a, str) and isinstance(b, str) and a == b
    # the string must still appear quoted in the formatted text
    assert json.dumps(orig[key] if key is not None else orig[0]) in once


@pytest.mark.parametrize(
    "literal",
    ["123", "-42", "1000", "1e3", "1.50", "1.5e2", "-0.0", "0.001", "2.9999999999999998"],
)
def test_number_literal_spelling_preserved(js, literal):
    src = f'{{"n": {literal}}}'
    once = js.analyze(src).formatted
    assert f'"n": {literal}' in once, once
    # value still parses numerically to the same number
    assert json.loads(once)["n"] == json.loads(src)["n"]


def test_bool_and_null_real_values_unquoted(js):
    src = '{"t": true, "f": false, "n": null, "s": "true"}'
    once = js.analyze(src).formatted
    out = json.loads(once)
    assert out["t"] is True and out["f"] is False and out["n"] is None
    assert isinstance(out["s"], str) and out["s"] == "true"


def test_key_order_preserved(js):
    src = '{"z": 1, "a": 2, "m": 3, "b": 4}'
    once = js.analyze(src).formatted
    keys = [p.split('"')[1] for p in once.splitlines() if ":" in p]
    assert keys == ["z", "a", "m", "b"]
    # outline order too
    names = [s.name for s in js.analyze(src).outline]
    assert names == ["z", "a", "m", "b"]


def test_duplicate_keys_preserved_with_values(js):
    src = '{"a": 1, "a": "1", "a": true}'
    data = json.loads(src, object_pairs_hook=list)
    assert [type(v) for _k, v in data] == [int, str, bool]
    out = js.analyze(src).formatted
    assert out.count('"a"') == 3
    assert " 1" in out and '"1"' in out and " true" in out
    # each duplicate value keeps its type after a round trip
    reparsed = json.loads(out, object_pairs_hook=list)
    assert [type(v) for _k, v in reparsed] == [int, str, bool]
    # duplicate keys map to distinct outline ids
    ids = [s.symbol_id for s in js.analyze(src).outline]
    assert ids == ["root/a", "root/a#2", "root/a#3"]


def test_format_idempotent_many_times(js):
    src = '{"z":[1,{"a":"true","b":"null"}],"n":1e3,"dup":1,"dup":"2"}\n'
    cur = src
    for _ in range(5):
        cur = js.analyze(cur).formatted
    assert js.analyze(cur).formatted == cur
    # final document semantically equal to original
    assert json.loads(cur) == json.loads(src)


def test_raw_literal_is_distinct_from_plain_string():
    assert isinstance(RawLiteral("1"), str)
    assert RawLiteral("1") == "1"
    assert type(RawLiteral("1")) is not str
