"""Text helpers: decoding, position mapping and span tiling."""

from __future__ import annotations

import bisect

from codeview.models import Position, Range, Span


def decode_source(raw: bytes) -> tuple[str | None, str | None]:
    """Decode raw bytes as UTF-8.

    Returns ``(text, reason)``: on success ``(text, None)``; on failure
    ``(None, "illegal_encoding")``. BOM is rejected to keep offsets
    unambiguous (it is never valid source content for our languages).
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return None, "illegal_encoding"
    try:
        return raw.decode("utf-8", errors="strict"), None
    except UnicodeDecodeError:
        return None, "illegal_encoding"


def build_line_index(text: str) -> list[int]:
    """Return the start offset of every line (index 0 == line 1).

    Line 1 always starts at 0. A trailing newline starts a final empty
    line, matching common editor conventions.
    """
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def position_at(text: str, offset: int, line_index: list[int] | None = None) -> Position:
    """Map an absolute character offset to a 1-based line / 0-based column."""
    n = len(text)
    if offset < 0:
        offset = 0
    if offset > n:
        offset = n
    if line_index is None:
        line_index = build_line_index(text)
    # Number of line starts <= offset == line number (1-based).
    line_no = bisect.bisect_right(line_index, offset)
    line_start = line_index[line_no - 1]
    return Position(line=line_no, column=offset - line_start)


from collections import OrderedDict as _OrderedDict

_INDEX_CACHE_MAX = 64
_WEAK_INDEX: "_OrderedDict[str, tuple[int, list[int]]]" = _OrderedDict()


def line_index_for(text: str) -> list[int]:
    """Return a line index for ``text`` via a small bounded LRU cache.

    Backends create thousands of ranges over the same immutable text while
    analysing one document; rebuilding the index each time was quadratic.
    The text itself is the key, so there are no id-reuse hazards, and the
    cache is bounded to avoid holding large documents indefinitely.
    """
    cached = _WEAK_INDEX.get(text)
    if cached is not None:
        length, idx = cached
        if length == len(text):
            _WEAK_INDEX.move_to_end(text)
            return idx
    idx = build_line_index(text)
    _WEAK_INDEX[text] = (len(text), idx)
    _WEAK_INDEX.move_to_end(text)
    while len(_WEAK_INDEX) > _INDEX_CACHE_MAX:
        _WEAK_INDEX.popitem(last=False)
    return idx


def make_range(text: str, start: int, end: int, line_index: list[int] | None = None) -> Range:
    """Build a :class:`Range` with precise line/column positions."""
    idx = line_index if line_index is not None else line_index_for(text)
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    return Range(
        start=start,
        end=end,
        start_pos=position_at(text, start, idx),
        end_pos=position_at(text, end, idx),
    )


# Higher number wins when two segments cover the exact same range
# (e.g. an AST-derived "parameter" overrides the generic "identifier").
DEFAULT_PRIORITY = {
    "text": 0,
    "operator": 1,
    "punctuation": 1,
    "identifier": 2,
    "builtin": 3,
    "class": 4,
    "function": 4,
    "decorator": 4,
    "parameter": 5,
    "constant": 5,
    "keyword": 6,
    "number": 6,
    "string": 6,
    "comment": 7,
    "property": 5,
}


def tile_spans(
    segments: list[tuple[int, int, str]],
    length: int,
    priority: dict[str, int] | None = None,
) -> tuple[Span, ...]:
    """Turn classified segments into a full, sorted, non-overlapping tiling.

    Every uncovered character gets the ``"text"`` category. Segments are
    clipped to ``[0, length)`` and zero-length ones dropped. Segments with
    the exact same range are resolved by category priority; any remaining
    overlap raises ``ValueError`` (it is a backend bug).
    """
    prio = DEFAULT_PRIORITY if priority is None else priority
    cleaned: list[tuple[int, int, str]] = []
    for raw_start, raw_end, category in segments:
        s = max(0, min(raw_start, length))
        e = max(0, min(raw_end, length))
        if s < e:
            cleaned.append((s, e, category))
    cleaned.sort(key=lambda seg: (seg[0], seg[1], -prio.get(seg[2], 0)))

    # Drop same-range duplicates keeping the highest priority category.
    deduped: list[tuple[int, int, str]] = []
    for seg in cleaned:
        if deduped and seg[0] == deduped[-1][0] and seg[1] == deduped[-1][1]:
            if prio.get(seg[2], 0) > prio.get(deduped[-1][2], 0):
                deduped[-1] = seg
            continue
        deduped.append(seg)
    cleaned = deduped

    spans: list[Span] = []
    cursor = 0
    for s, e, category in cleaned:
        if s > length or e > length or s < 0:
            raise ValueError("segment outside document")
        if s < cursor:
            raise ValueError(f"overlapping highlight segments at {s} < {cursor}")
        if s > cursor:
            spans.append(Span(cursor, s, "text"))
        spans.append(Span(s, e, category))
        cursor = e
    if cursor < length:
        spans.append(Span(cursor, length, "text"))
    return tuple(spans)


def normalize_newlines(text: str) -> str:
    """Normalize CRLF/CR to LF without touching anything else."""
    return text.replace("\r\n", "\n").replace("\r", "\n")
