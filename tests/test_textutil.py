"""Tests for decoding, positions and span tiling."""

import pytest

from codeview.textutil import (
    build_line_index,
    decode_source,
    normalize_newlines,
    position_at,
    tile_spans,
)


def test_decode_valid_utf8():
    text, reason = decode_source("hello 世界".encode("utf-8"))
    assert text == "hello 世界"
    assert reason is None


def test_decode_rejects_invalid_utf8():
    text, reason = decode_source(b"hello\xff\xfeworld")
    assert text is None
    assert reason == "illegal_encoding"


def test_decode_rejects_bom():
    text, reason = decode_source(b"\xef\xbb\xbfx = 1\n")
    assert reason == "illegal_encoding"
    assert text is None


def test_positions():
    text = "abc\ndefgh\n"
    idx = build_line_index(text)
    assert (position_at(text, 0, idx).line, position_at(text, 0, idx).column) == (1, 0)
    p4 = position_at(text, 4, idx); assert (p4.line, p4.column) == (2, 0)
    p6 = position_at(text, 6, idx); assert (p6.line, p6.column) == (2, 2)
    # offset right after final newline -> line 3, column 0
    pe = position_at(text, len(text), idx); assert (pe.line, pe.column) == (3, 0)


def test_tile_spans_covers_and_sorts():
    segs = [(5, 7, "keyword"), (2, 4, "number")]
    spans = tile_spans(segs, 10)
    assert [(s.start, s.end, s.category) for s in spans] == [
        (0, 2, "text"),
        (2, 4, "number"),
        (4, 5, "text"),
        (5, 7, "keyword"),
        (7, 10, "text"),
    ]
    # reconstruct
    text = "0123456789"
    assert "".join(text[s.start:s.end] for s in spans) == text


def test_tile_spans_empty_document():
    assert tile_spans([], 0) == ()


def test_tile_spans_overlap_rejected():
    with pytest.raises(ValueError):
        tile_spans([(0, 5, "a"), (3, 8, "b")], 10)


def test_tile_spans_adjacent():
    spans = tile_spans([(0, 2, "a"), (2, 4, "b")], 4)
    assert [s.category for s in spans] == ["a", "b"]


def test_normalize_newlines():
    assert normalize_newlines("a\r\nb\rc") == "a\nb\nc"
