"""Full-coverage highlight construction.

Language scanners emit *semantic* tokens only; whitespace, line breaks and
other unclassified characters are not covered by them. This module fills
every uncovered source offset with a ``text`` token so that the resulting
highlight list is an exact partition of the source: sorted by range,
non-overlapping, gap-free, and concatenating ``source[r]`` over the ranges
reproduces the source byte-for-byte (in str coordinates).
"""
from __future__ import annotations

from bisect import bisect_right

from app.models import Highlight, Position, Range


def _line_starts(text: str) -> list[int]:
    """Offset of the first character of every line."""
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def cover(
    source: str,
    tokens: list[Highlight],
    line_offset: int = 0,
) -> list[Highlight]:
    """Return a full-coverage highlight list for ``source``.

    ``tokens`` use coordinates relative to ``source`` plus ``line_offset``
    (i.e. token line 0 corresponds to source line ``line_offset``). Gaps
    between/around tokens are filled with category ``text``. Tokens are
    assumed non-overlapping; defensive clamping handles the odd malformed
    span without ever dropping source characters.
    """
    n = len(source)
    if n == 0:
        return []
    starts = _line_starts(source)

    def to_off(line: int, col: int) -> int:
        idx = line - line_offset
        if idx < 0:
            idx = 0
        if idx >= len(starts):
            return n
        return min(starts[idx] + col, n)

    spans: list[tuple[int, int, str]] = []
    for h in tokens:
        s = to_off(h.range.start.line, h.range.start.col)
        e = to_off(h.range.end.line, h.range.end.col)
        if e > s:
            spans.append((s, e, h.category))
    spans.sort(key=lambda x: (x[0], x[1]))

    merged: list[tuple[int, int, str]] = []
    cur = 0
    for s, e, cat in spans:
        if e <= cur:
            continue  # fully inside an earlier (defensive: overlapping) span
        if s > cur:
            merged.append((cur, s, "text"))
        merged.append((max(s, cur), e, cat))
        cur = e
    if cur < n:
        merged.append((cur, n, "text"))

    def to_pos(off: int) -> Position:
        line = bisect_right(starts, off) - 1
        if line < 0:
            line = 0
        return Position(line + line_offset, off - starts[line])

    return [
        Highlight(Range(to_pos(s), to_pos(e)), cat)
        for s, e, cat in merged
    ]
