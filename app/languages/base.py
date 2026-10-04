"""Language plugin interface."""
from __future__ import annotations

from typing import Protocol

from app.models import AnalysisResult, Diagnostic, Highlight


class LanguagePlugin(Protocol):
    """A language plugin computes highlights, outline and formatting.

    All methods must be deterministic and must never raise on malformed
    input; problems are reported as diagnostics instead.
    """

    name: str

    def analyze(self, source: str) -> AnalysisResult:
        """Full analysis of a source text."""
        ...

    def tokenize_region(self, snippet: str, line_offset: int) -> list[Highlight] | None:
        """Tokenize an isolated line region for incremental updates.

        Returns None when the region cannot be tokenized in isolation
        (unclean lexer state); the caller then falls back to full recompute.
        """
        ...

    def lexical(self, highlights: list[Highlight]) -> list[Highlight]:
        """Strip derived (non-lexer) categories, e.g. ``*.declaration``,
        back to their lexical base so a splice never leaks stale derived
        state into a new analysis."""
        ...

    def rederive(
        self,
        source: str,
        highlights: list[Highlight],
        extra_diagnostics: list[Diagnostic] | None = None,
    ) -> AnalysisResult:
        """Compute outline + formatting around an existing highlight list."""
        ...
