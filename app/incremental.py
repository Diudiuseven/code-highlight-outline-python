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

from app.languages.tiling import tile_highlights
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


def full_range(text: str) -> Range:
    """Range covering the whole text."""
    if not text:
        return Range(Position(0, 0), Position(0, 0))
    lines = text.split("\n")
    return Range(Position(0, 0), Position(len(lines) - 1, len(lines[-1])))


def _clip_highlight(
    text: str, h: Highlight, lo: int, hi: int
) -> Highlight | None:
    """Intersect a highlight with the byte range [lo, hi)."""
    hs = offset_at(text, h.range.start)
    he = offset_at(text, h.range.end)
    a, b = max(hs, lo), min(he, hi)
    if b <= a:
        return None
    return Highlight(
        Range(position_at(text, a), position_at(text, b)), h.category
    )


def incremental_highlights(
    old_text: str,
    new_text: str,
    old_highlights: list[Highlight],
    edit: TextEdit,
    tokenize_region: TokenizeRegion,
) -> tuple[list[Highlight], Range] | None:
    """Splice highlights for an edited document.

    Returns ``(new_highlights, affected)`` tiled over the whole new text, or
    None when the edit cannot be handled incrementally (boundary not in a
    clean lexer state, ...), in which case the caller must recompute fully.
    """
    old_lines = old_text.split("\n")
    new_lines = new_text.split("\n")
    if edit.start.line >= len(old_lines) or edit.end.line >= len(old_lines):
        return None

    start_line = edit.start.line
    end_line_old = edit.end.line  # old line index where replaced text ends

    def _old_region_offsets(sl: int, el: int) -> tuple[int, int]:
        start = offset_at(old_text, Position(sl, 0))
        if el + 1 < len(old_lines):
            end = offset_at(old_text, Position(el + 1, 0))
        else:
            end = len(old_text)
        return start, end

    # Expand the region to cover EVERY old token intersecting it. Expansion
    # runs to a fixed point: widening the region can pull in further
    # multi-line tokens (e.g. strings spanning several lines).
    while True:
        sl, el = start_line, end_line_old
        region_start, region_end = _old_region_offsets(sl, el)
        for h in old_highlights:
            hs = offset_at(old_text, h.range.start)
            he = offset_at(old_text, h.range.end)
            # Strict intersection only: a token starting exactly at the
            # region's first column, or ending exactly at its boundary,
            # does not need to be re-tokenized.
            if he <= region_start or hs >= region_end:
                continue
            start_line = min(start_line, h.range.start.line)
            end_line_old = max(end_line_old, h.range.end.line)
        if (sl, el) == (start_line, end_line_old):
            break

    line_delta = len(new_lines) - len(old_lines)
    end_line_new = end_line_old + line_delta
    if not (0 <= start_line <= end_line_new < len(new_lines)):
        return None

    # Cut the old tiled token stream by ABSOLUTE OFFSETS. This is essential
    # because filler ("text") tokens routinely span line boundaries; line-
    # based cuts would otherwise duplicate or drop characters. Tokens
    # crossing a cut point are clipped to it (and later re-tiled).
    cut_start, cut_end = _old_region_offsets(start_line, end_line_old)
    prefix_parts: list[Highlight] = []
    suffix_parts: list[Highlight] = []
    for h in old_highlights:
        hs = offset_at(old_text, h.range.start)
        he = offset_at(old_text, h.range.end)
        if he <= cut_start:
            prefix_parts.append(h)
        elif hs >= cut_end:
            suffix_parts.append(h)
        else:
            # intersects the region; keep whichever sides spill out so no
            # character is lost when a token spans both cut points
            left = _clip_highlight(old_text, h, 0, cut_start)
            if left is not None:
                prefix_parts.append(left)
            right = _clip_highlight(old_text, h, cut_end, len(old_text))
            if right is not None:
                suffix_parts.append(right)

    snippet_lines = new_lines[start_line : end_line_new + 1]
    snippet = "\n".join(snippet_lines)
    # Lexers expect a trailing newline except when the region genuinely ends
    # the document without one; fabricating one there would emit a phantom
    # token past the end of the source.
    needs_trailing_nl = (
        end_line_new < len(new_lines) - 1
    ) or new_text.endswith("\n")
    token_input = snippet + ("\n" if needs_trailing_nl and not snippet.endswith("\n") else "")
    region = tokenize_region(token_input, start_line)
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
        for h in suffix_parts
    ]

    combined = prefix_parts + region + shifted_suffix
    combined.sort(key=lambda h: (h.range.start, h.range.end))
    # Re-tile against the NEW text: clips any boundary artifact of the
    # isolated tokenization and fills every gap, so the splice has the same
    # full-coverage, non-overlapping property as a fresh analysis.
    result = tile_highlights(new_text, combined)
    affected = Range(
        Position(start_line, 0),
        Position(end_line_new, len(new_lines[end_line_new])),
    )
    return result, affected


def analyze_edit(plugin, old_text: str, new_text: str, old_result, edit: TextEdit):
    """Incremental analysis with a guaranteed-consistent fallback.

    Returns ``(AnalysisResult, used_incremental, affected)``. The spliced
    result is only trusted when the previous analysis was clean AND the new
    text parses without syntax errors; otherwise a full recompute is
    returned, so the output is always identical to a full recomputation.
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
            new_highlights, affected = spliced
            result = plugin.rederive(new_text, new_highlights)
            if not any(d.code == "syntax-error" for d in result.diagnostics):
                return result, True, affected
    return plugin.analyze(new_text), False, full_range(new_text)
