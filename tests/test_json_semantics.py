"""Acceptance: JSON formatting never changes document semantics.

Strings that merely look like numbers/booleans/null stay strings; number
literal spelling, key order and duplicate keys are preserved; formatting is
idempotent.
"""
import json


def parse_pairs(text):
    return json.loads(text, object_pairs_hook=list)


def test_string_that_looks_like_number_stays_string(js):
    src = '{"a": "123", "b": "007", "c": "1.50", "d": "-3", "e": "1e3"}'
    out = js.analyze(src).formatted
    data = dict(parse_pairs(out))
    for key in ("a", "b", "c", "d", "e"):
        assert type(data[key]) is str, (key, data[key])
    assert data == {"a": "123", "b": "007", "c": "1.50", "d": "-3", "e": "1e3"}
    # the quotes proving "stringness" must be present in the output
    assert '"123"' in out and '"1e3"' in out


def test_string_that_looks_like_bool_or_null_stays_string(js):
    src = '{"t": "true", "f": "false", "n": "null"}'
    out = js.analyze(src).formatted
    data = dict(parse_pairs(out))
    assert data == {"t": "true", "f": "false", "n": "null"}
    assert all(type(v) is str for v in data.values())
    assert '"true"' in out and '"null"' in out


def test_special_constant_lookalike_strings_stay_strings(js):
    src = '{"a": "NaN", "b": "Infinity", "c": "-Infinity"}'
    out = js.analyze(src).formatted
    data = dict(parse_pairs(out))
    assert all(type(v) is str for v in data.values())
    assert '"NaN"' in out and '"Infinity"' in out


def test_real_numbers_and_constants_keep_their_type(js):
    src = '{"i": 42, "flt": 1.5, "e": 1e3, "neg": -7, "t": true, "f": false, "n": null}'
    out = js.analyze(src).formatted
    data = json.loads(out)
    assert data["i"] == 42 and type(data["i"]) is int
    assert data["flt"] == 1.5 and type(data["flt"]) is float
    assert data["e"] == 1000.0
    assert data["neg"] == -7
    assert data["t"] is True and data["f"] is False and data["n"] is None


def test_number_literal_spelling_preserved(js):
    src = '{"a": 100.0, "b": 1e3, "c": 1.00, "d": 0.0001}'
    out = js.analyze(src).formatted
    assert "100.0" in out
    assert "1e3" in out  # not rewritten to 1000 / 1000.0
    assert "1.00" in out
    assert "0.0001" in out
    assert json.loads(out) == json.loads(src)


def test_key_order_and_duplicate_keys_preserved(js):
    src = '{"z": 1, "a": "true", "z": 2, "m": "null", "a": 3}'
    out = js.analyze(src).formatted
    pairs = parse_pairs(out)
    assert [k for k, _ in pairs] == ["z", "a", "z", "m", "a"]
    assert pairs == [("z", 1), ("a", "true"), ("z", 2), ("m", "null"), ("a", 3)]
    assert out.count('"z"') == 2 and out.count('"a"') == 2


def test_formatting_is_idempotent_on_lookalike_values(js):
    src = '{"a": "123", "b": "true", "c": "null", "d": 1e3, "e": [true, "false", "5"]}'
    once = js.analyze(src).formatted
    twice = js.analyze(once).formatted
    thrice = js.analyze(twice).formatted
    assert once == twice == thrice
    # values in the array also keep their types
    data = json.loads(once)
    assert data["e"] == [True, "false", "5"]
    assert type(data["e"][1]) is str and type(data["e"][2]) is str
