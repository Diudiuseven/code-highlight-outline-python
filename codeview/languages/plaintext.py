"""Plaintext backend: no symbols, single "text" span, newline normalization."""

from __future__ import annotations

from codeview.incremental import analyze_full
from codeview.models import Analysis, Region
from codeview.textutil import normalize_newlines, tile_spans


class PlaintextBackend:
    language_id = "plaintext"

    def analyze(self, text: str) -> Analysis:
        return analyze_full(self, text)

    def split_regions(self, text: str) -> tuple[Region, ...]:
        # Plaintext is one region: every edit is a full recompute, which is
        # both cheap and trivially consistent.
        return (Region(0, len(text)),)

    def analyze_region(self, text: str, is_first: bool, is_last: bool) -> Analysis:
        normalized = normalize_newlines(text)
        if is_last and normalized and not normalized.endswith("\n"):
            normalized += "\n"
        spans = tile_spans([], len(text))
        return Analysis(
            language=self.language_id,
            spans=spans,
            symbols=(),
            formatted=normalized,
            diagnostics=(),
        )
