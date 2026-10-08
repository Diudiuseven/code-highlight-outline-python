"""Incremental recomputation.

The highlight pass is genuinely incremental. Highlight tokens form a
gap-free, non-overlapping partition of the source (see ``app.coverage``),
so splicing can be done in *absolute offsets*:

1. expand the edited region until no old token crosses either boundary;
2. cut the old document at line-aligned boundaries that are clean lexer
   states (the expansion above guarantees this);
3. re-tokenize the corresponding line region of the new document;
4. reuse old tokens strictly outside the region, shifting the suffix by
   the edit's offset delta.

The splice is only trusted when the region re-tokenizes cleanly *and* the
result provably reconstructs the whole new source; otherwise the caller
falls back to a full recompute, so an incremental result is always
identical to a full recomputation.
"""
from __future__ import annotations

from typing import Callable

from app.models import Highlight, Position, Range, TextEdit

TokenizeRegion = Callable[[str, int], "list[Highlight] | None"]


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def offset_at(text: str, pos: Position) -> int:
    """Absolute offset of a (line, col) position."""
    starts = _line_starts(text)
    idx = min(pos.line, len(starts) - 1)
    line_start = starts[idx]
    if idx == len(starts) - 1:
        return min(line_start + pos.col, len(text))
    return line_start + pos.col


def position_at(text: str, offset: int) -> Position:
    starts = _line_starts(text)
    lo, hi = 0, len(starts)
    while lo < hi:
        mid = (lo + hi) // 2
        if starts[mid] <= offset:
            lo = mid + 1
        else:
            hi = mid
    line = lo - 1
    return Position(line, offset - starts[line])


def affected_range(
    old_text: str,
    edit: TextEdit,
    new_text: str,
    old_highlights: list[Highlight] | None = None,
) -> Range:
    """Line-aligned range, in NEW-text coordinates, invalidated by `edit`.

    When the old lexical highlights are given, the range is expanded to
    cover every token the edit intersects (e.g. a multi-line string).
    The range spans whole lines: it starts at the first affected line's
    column 0 and ends at the last affected line's end column.
    """
    start_line = edit.start.line
    end_line_old = edit.end.line
    if old_highlights is not None:
        edit_start = Position(edit.start.line, edit.start.col)
        edit_end = Position(edit.end.line, edit.end.col)
        for h in old_highlights:
            if h.range.end <= edit_start or h.range.start >= edit_end:
                continue
            start_line = min(start_line, h.range.start.line)
            end_line_old = max(end_line_old, h.range.end.line)

    inserted = edit.new_text.split("\n")
    last_inserted_line = edit.start.line + len(inserted) - 1
    line_delta = len(new_text.split("\n")) - len(old_text.split("\n"))
    end_line_new = max(end_line_old + line_delta, last_inserted_line)

    new_lines = new_text.split("\n")
    start_line = max(0, min(start_line, len(new_lines) - 1))
    end_line_new = max(start_line, min(end_line_new, len(new_lines) - 1))
    return Range(
        Position(start_line, 0),
        Position(end_line_new, len(new_lines[end_line_new])),
    )


def _reconstruct(text: str, highlights: list[Highlight]) -> bool:
    """Verify highlights are a sorted, exact partition of text."""
    cursor = 0
    prev_end: Position | None = None
    for h in highlights:
        if prev_end is not None and h.range.start < prev_end:
            return False
        s = offset_at(text, h.range.start)
        e = offset_at(text, h.range.end)
        if e < s or s != cursor:
            return False
        cursor = e
        prev_end = h.range.end
    return cursor == len(text)


def incremental_highlights(
    old_text: str,
    new_text: str,
    old_highlights: list[Highlight],
    edit: TextEdit,
    tokenize_region: TokenizeRegion,
) -> list[Highlight] | None:
    """Splice highlights for an edited document.

    Returns the new highlight list, or None when the edit cannot be handled
    incrementally (no clean lexer state at a boundary, region fails to
    tokenize, or the splice does not reconstruct the source); the caller
    must then recompute fully.
    """
    old_starts = _line_starts(old_text)
    new_starts = _line_starts(new_text)
    n_old_lines = len(old_starts)
    if edit.start.line >= n_old_lines or edit.end.line >= n_old_lines:
        return None

    # Expand until no old token crosses either cut. Both cuts are line
    # aligned, so a token must be wholly in the prefix, wholly in the
    # suffix, or wholly inside the re-tokenized region.
    start_line = edit.start.line
    end_line_old = edit.end.line

    def crosses(left_line: int, right_line: int) -> Highlight | None:
        """A token that starts before ``left_line`` and reaches it, or
        reaches past ``right_line``."""
        for h in old_highlights:
            if h.range.start.line < left_line and h.range.end.line >= left_line:
                return h
            if h.range.start.line <= right_line and h.range.end.line > right_line:
                return h
        return None

    # also pull in tokens merely intersecting the edited span
    edit_start = Position(edit.start.line, edit.start.col)
    edit_end = Position(edit.end.line, edit.end.col)
    for h in old_highlights:
        if h.range.end <= edit_start or h.range.start >= edit_end:
            continue
        start_line = min(start_line, h.range.start.line)
        end_line_old = max(end_line_old, h.range.end.line)

    while True:
        h = crosses(start_line, end_line_old)
        if h is None:
            break
        start_line = min(start_line, h.range.start.line)
        end_line_old = max(end_line_old, h.range.end.line)

    n_new_lines = len(new_starts)
    line_delta = n_new_lines - n_old_lines
    end_line_new = end_line_old + line_delta
    if not (0 <= start_line <= end_line_new < n_new_lines):
        return None

    snippet = new_text[
        new_starts[start_line] : (
            new_starts[end_line_new + 1]
            if end_line_new + 1 < n_new_lines
            else len(new_text)
        )
    ]
    region = tokenize_region(snippet, start_line)
    if region is None:
        return None

    cut_left = old_starts[start_line]
    cut_right = (
        old_starts[end_line_old + 1]
        if end_line_old + 1 < n_old_lines
        else len(old_text)
    )

    prefix = [
        h
        for h in old_highlights
        if offset_at(old_text, h.range.end) <= cut_left
    ]
    suffix = [
        Highlight(
            Range(
                Position(h.range.start.line + line_delta, h.range.start.col),
                Position(h.range.end.line + line_delta, h.range.end.col),
            ),
            h.category,
        )
        for h in old_highlights
        if offset_at(old_text, h.range.start) >= cut_right
    ]

    result = prefix + region + suffix
    result.sort(key=lambda h: (h.range.start, h.range.end))
    if not _reconstruct(new_text, result):
        return None
    return result


def analyze_edit(plugin, old_text: str, new_text: str, old_result, edit: TextEdit):
    """Incremental analysis with a guaranteed-consistent fallback.

    Returns (AnalysisResult, used_incremental). The spliced result is only
    trusted when the previous analysis was clean AND the new text parses
    without syntax errors; otherwise a full recompute is returned, so the
    output is always identical to a full recomputation.
    """
    if not old_result.degraded:
        spliced = incremental_highlights(
            old_text,
            new_text,
            plugin.lexical(old_result.highlights),
            edit,
            plugin.tokenize_region,
        )
        if spliced is not None:
            result = plugin.rederive(new_text, spliced)
            if not any(d.code == "syntax-error" for d in result.diagnostics):
                return result, True
    return plugin.analyze(new_text), False
