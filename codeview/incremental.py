"""Region-based incremental analysis driver.

Full and incremental analyses share one code path:

* full  -> split the document into regions, analyse each with
  ``backend.analyze_region(text, is_first, is_last)`` and assemble;
* edit  -> reuse the cached analysis of regions untouched by the edit and
  recompute exactly the regions intersecting it (plus the last region when
  the document tail/partition changed), then assemble identically.

Therefore the assembled spans/symbols/formatted text are guaranteed equal
to a from-scratch recomputation.
"""

from __future__ import annotations

from dataclasses import dataclass

from codeview.models import Analysis, Diagnostic, Region, Symbol


@dataclass(frozen=True, slots=True)
class RegionAnalysis:
    region: Region
    text: str
    analysis: Analysis
    is_first: bool
    is_last: bool


@dataclass(frozen=True, slots=True)
class IncrementalResult:
    analysis: Analysis
    affected_ranges: tuple[tuple[int, int], ...]
    region_count: int
    recomputed: int


# ---------------------------------------------------------------------------
# Full / incremental analysis
# ---------------------------------------------------------------------------
def analyze_full(backend, text: str) -> Analysis:
    """Analyse a whole document by partitioning it into regions."""
    regions = list(backend.split_regions(text))
    parts = [
        backend.analyze_region(
            text[r.start : r.end],
            is_first=(i == 0),
            is_last=(i == len(regions) - 1),
        )
        for i, r in enumerate(regions)
    ]
    return _assemble(backend.language_id, text, regions, parts)


def analyze_regions(backend, text: str) -> list[RegionAnalysis]:
    """Split and analyse every region (used to prime session cache)."""
    regions = list(backend.split_regions(text))
    out: list[RegionAnalysis] = []
    for i, region in enumerate(regions):
        part = backend.analyze_region(
            text[region.start : region.end],
            is_first=(i == 0),
            is_last=(i == len(regions) - 1),
        )
        out.append(RegionAnalysis(region, text[region.start : region.end], part, i == 0, i == len(regions) - 1))
    return out


def _assemble(language_id: str, text: str, regions: list[Region], parts: list) -> Analysis:
    from codeview.textutil import tile_spans

    segments: list[tuple[int, int, str]] = []
    symbols: list[Symbol] = []
    diagnostics: list[Diagnostic] = []
    formatted_parts: list[str] = []

    for region, part in zip(regions, parts):
        base = region.start
        for span in part.spans:
            segments.append((base + span.start, base + span.end, span.category))
        for sym in part.symbols:
            symbols.append(_shift_symbol_tree(sym, base, text))
        for diag in part.diagnostics:
            diagnostics.append(_shift_diagnostic(diag, base, text))
        formatted_parts.append(part.formatted)

    spans = tile_spans(segments, len(text))
    symbols.sort(key=lambda s: (s.range.start, s.range.end, s.kind, s.name))
    formatted = "".join(formatted_parts)
    return Analysis(
        language=language_id,
        spans=spans,
        symbols=tuple(symbols),
        formatted=formatted,
        diagnostics=tuple(diagnostics),
    )


def _shift_symbol_tree(symbol: Symbol, delta: int, text: str) -> Symbol:
    from codeview.textutil import make_range

    def shift(rng):
        return make_range(text, rng.start + delta, rng.end + delta)

    children = tuple(_shift_symbol_tree(c, delta, text) for c in symbol.children)
    return Symbol(
        name=symbol.name,
        kind=symbol.kind,
        range=shift(symbol.range),
        selection_range=shift(symbol.selection_range),
        children=children,
    )


def _shift_diagnostic(diag: Diagnostic, delta: int, text: str) -> Diagnostic:
    from codeview.textutil import make_range

    return Diagnostic(
        reason=diag.reason,
        message=diag.message,
        range=make_range(text, diag.range.start + delta, diag.range.end + delta),
    )


def analyze_incremental(
    backend,
    old_text: str,
    new_text: str,
    edit_start: int,
    edit_end_old: int,
    edit_end_new: int,
    old_parts: list[RegionAnalysis] | None = None,
) -> tuple[IncrementalResult, list[RegionAnalysis]]:
    """Re-analyse only regions touched by an edit.

    ``old_text[edit_start:edit_end_old]`` was replaced by
    ``new_text[edit_start:edit_end_new]``. Returns the assembled result,
    affected ranges and the new per-region analyses (for session caching).
    """
    if old_text[:edit_start] != new_text[:edit_start]:
        raise ValueError("prefix before edit_start must match")
    if old_text[edit_end_old:] != new_text[edit_end_new:]:
        raise ValueError("suffix after edit must match")

    new_regions = list(backend.split_regions(new_text))

    # Determine which new regions may have changed.
    prefix_end = edit_start
    suffix_start_new = edit_end_new
    affected_idx: set[int] = set()
    reused_count = 0
    old_by_content: dict[str, RegionAnalysis] = (
        {p.text: p for p in old_parts} if old_parts is not None else {}
    )

    new_parts: list[Analysis | None] = [None] * len(new_regions)
    affected_ranges: list[tuple[int, int]] = []

    for i, region in enumerate(new_regions):
        intersects = region.start < suffix_start_new and region.end > prefix_end
        tail_risk = region.end > suffix_start_new and i == len(new_regions) - 1
        local = new_text[region.start : region.end]
        is_first = i == 0
        is_last = i == len(new_regions) - 1

        if not intersects and not tail_risk:
            prior = old_by_content.get(local)
            if prior is not None and prior.is_first == is_first and prior.is_last == is_last:
                new_parts[i] = prior.analysis
                reused_count += 1
                continue

        # Must recompute.
        affected_idx.add(i)
        affected_ranges.append((region.start, region.end))
        new_parts[i] = backend.analyze_region(local, is_first=is_first, is_last=is_last)

    analysis = _assemble(
        backend.language_id, new_text, new_regions, new_parts  # type: ignore[arg-type]
    )

    fresh: list[RegionAnalysis] = []
    for i, region in enumerate(new_regions):
        fresh.append(
            RegionAnalysis(
                region,
                new_text[region.start : region.end],
                new_parts[i],  # type: ignore[arg-type]
                i == 0,
                i == len(new_regions) - 1,
            )
        )

    result = IncrementalResult(
        analysis=analysis,
        affected_ranges=tuple(affected_ranges),
        region_count=len(new_regions),
        recomputed=len(affected_idx),
    )
    return result, fresh
