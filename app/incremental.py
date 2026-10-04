"""Incremental recomputation.

The highlight pass is genuinely incremental: only the edited line region
(expanded to cover any multi-line tokens it touches) is re-tokenized via
the language plugin, and the surrounding tokens are reused from the
previous analysis after a position shift. The splice is only applied when
both boundaries are in a "clean" lexer state (no token spans the cut
points); otherwise the caller falls back to a full recompute, so an
incremental result is always identical to a full recomputation.
"""
from __future__ import annotations

from typing import Callable

from app.models import Highlight, Position, Range, TextEdit

TokenizeRegion = Callable[[str, int], "list[Highlight] | None"]


def offset_at(text: str, pos: Position) -> int:
    """Absolute offset of a (line, col) position."""
    lines = text.split("\n")
    return sum(len(line) + 1 for line in lines[: pos.line]) + pos.col


def position_at(text: str, offset: int) -> Position:
    line = text.count("\n", 0, offset)
    col = offset - (text.rfind("\n", 0, offset) + 1)
    return Position(line, col)


def affected_range(old_text: str, edit: TextEdit, new_text: str) -> Range:
    """Line-aligned range, in NEW-text coordinates, invalidated by `edit`."""
    inserted = edit.new_text.split("\n")
    if len(inserted) == 1:
        end = Position(edit.start.line, edit.start.col + len(inserted[0]))
    else:
        end = Position(edit.start.line + len(inserted) - 1, len(inserted[-1]))
    new_lines = new_text.split("\n")
    end_line = min(end.line, len(new_lines) - 1)
    return Range(
        Position(edit.start.line, 0),
        Position(end_line, len(new_lines[end_line])),
    )


def incremental_highlights(
    old_text: str,
    new_text: str,
    old_highlights: list[Highlight],
    edit: TextEdit,
    tokenize_region: TokenizeRegion,
) -> list[Highlight] | None:
    """Splice highlights for an edited document.

    Returns the new highlight list, or None when the edit cannot be handled
    incrementally (boundary not in a clean lexer state, inconsistent
    indentation, ...), in which case the caller must recompute fully.
    """
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    if edit.start.line >= len(old_lines) or edit.end.line >= len(old_lines):
        return None

    start_line = edit.start.line
    end_line_old = edit.end.line  # old line index where replaced text ends
    # expand the region to cover every old token intersecting the edit
    edit_start = Position(edit.start.line, edit.start.col)
    edit_end = Position(edit.end.line, edit.end.col)
    for h in old_highlights:
        if h.range.end <= edit_start or h.range.start >= edit_end:
            continue
        start_line = min(start_line, h.range.start.line)
        end_line_old = max(end_line_old, h.range.end.line)

    line_delta = len(new_lines) - len(old_lines)
    end_line_new = end_line_old + line_delta
    if end_line_new >= len(new_lines):
        return None

    # prefix: old tokens ending at or before the region's first line
    prefix = [
        h
        for h in old_highlights
        if h.range.end.line < start_line
        or (h.range.end.line == start_line and h.range.end.col == 0)
    ]
    # suffix: old tokens starting after the region's last old line
    suffix = [h for h in old_highlights if h.range.start.line > end_line_old]

    snippet = "\n".join(new_lines[start_line : end_line_new + 1])
    if snippet and not snippet.endswith("\n"):
        snippet += "\n"
    region = tokenize_region(snippet, start_line)
    if region is None:
        return None

    # shift suffix tokens into new-text coordinates (columns unchanged
    # because the edit only touched lines inside the region)
    shifted_suffix = [
        Highlight(
            Range(
                Position(h.range.start.line + line_delta, h.range.start.col),
                Position(h.range.end.line + line_delta, h.range.end.col),
            ),
            h.category,
        )
        for h in suffix
    ]

    result = prefix + region + shifted_suffix
    result.sort(key=lambda h: (h.range.start, h.range.end))
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
