"""Highlight tiling: guarantee a source is fully covered by highlights.

The lexers only emit *semantic* tokens; every byte not claimed by one is
assigned a filler ``text`` token so that, after sorting, the highlight list
partitions the source into non-overlapping, gap-free half-open ranges.
Slicing each range in order and concatenating reproduces the source verbatim.
"""
from __future__ import annotations

from app.models import Highlight, Position, Range

FILLER_CATEGORY = "text"


def line_offsets(text: str) -> list[int]:
    """Absolute offset of the first character of every line."""
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def position_at(text: str, offset: int, line_starts: list[int] | None = None) -> Position:
    starts = line_starts if line_starts is not None else line_offsets(text)
    # binary-ish search via bisect would also work; files are small here
    import bisect

    line = bisect.bisect_right(starts, offset) - 1
    return Position(line, offset - starts[line])


def _range_bounds(text: str, rng: Range, line_starts: list[int]) -> tuple[int, int]:
    start = line_starts[rng.start.line] + rng.start.col if rng.start.line < len(line_starts) else len(text)
    end = line_starts[rng.end.line] + rng.end.col if rng.end.line < len(line_starts) else len(text)
    return start, end


def tile_highlights(source: str, highlights: list[Highlight]) -> list[Highlight]:
    """Return sorted, non-overlapping highlights covering ``source`` fully.

    - zero-length/out-of-range tokens are dropped;
    - overlapping tokens are resolved in favour of the earlier one;
    - every uncovered run of characters becomes a ``text`` filler token.
    """
    n = len(source)
    starts = line_offsets(source)

    bounded: list[tuple[int, int, str]] = []
    for h in highlights:
        a, b = _range_bounds(source, h.range, starts)
        a = max(0, min(a, n))
        b = max(0, min(b, n))
        if b <= a:
            continue
        bounded.append((a, b, h.category))
    bounded.sort(key=lambda t: (a, b))

    merged: list[tuple[int, int, str]] = []
    for a, b, cat in bounded:
        if merged and a < merged[-1][1]:
            # overlap: never extend inside an already claimed range
            a = merged[-1][1]
            if a >= b:
                continue
        merged.append((a, b, cat))

    out: list[Highlight] = []
    cursor = 0
    for a, b, cat in merged:
        if a > cursor:
            out.append(
                Highlight(
                    Range(position_at(source, cursor, starts), position_at(source, a, starts)),
                    FILLER_CATEGORY,
                )
            )
        out.append(
            Highlight(
                Range(position_at(source, a, starts), position_at(source, b, starts)),
                cat,
            )
        )
        cursor = b
    if cursor < n:
        out.append(
            Highlight(
                Range(position_at(source, cursor, starts), position_at(source, n, starts)),
                FILLER_CATEGORY,
            )
        )

    # Canonical segmentation: adjacent ranges of the SAME category (most
    # commonly filler ``text`` runs split at a line boundary) are a single
    # run. Applied on both the full and incremental paths, it guarantees the
    # splice produces the exact token list a fresh analysis would.
    canonical: list[Highlight] = []
    for h in out:
        if (
            canonical
            and canonical[-1].range.end == h.range.start
            and canonical[-1].category == h.category
        ):
            canonical[-1] = Highlight(
                Range(canonical[-1].range.start, h.range.end), h.category
            )
        else:
            canonical.append(h)
    return canonical
