"""Data models shared across the service.

All data is immutable and order is deterministic: spans and symbols are
returned in the order they appear in the source (sorted by start offset).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final


class Status(str, Enum):
    """Overall result status.

    - ``ok``: analysis completed with full fidelity.
    - ``degraded``: the request did not fail, but one or more parts were
      produced by a fallback strategy (see ``reasons`` / ``diagnostics``).
    - ``error``: the request could not be served at all (e.g. unknown
      language). No partial data is returned.
    """

    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class Reason(str, Enum):
    """Machine-readable reason codes for degradation / errors."""

    SYNTAX_ERROR = "syntax_error"
    UNKNOWN_LANGUAGE = "unknown_language"
    ILLEGAL_ENCODING = "illegal_encoding"
    FILE_TOO_LARGE = "file_too_large"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class Position:
    """1-based line / 0-based column position in the source."""

    line: int
    column: int

    def to_dict(self) -> dict:
        return {"line": self.line, "column": self.column}


@dataclass(frozen=True, slots=True)
class Range:
    """A half-open byte/character range ``[start, end)`` with positions."""

    start: int
    end: int
    start_pos: Position
    end_pos: Position

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "start_pos": self.start_pos.to_dict(),
            "end_pos": self.end_pos.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Span:
    """A highlight span.

    ``[start, end)`` is a half-open character offset range. Spans tile the
    whole document (every character belongs to exactly one span) and are
    sorted by ``start``, so the original text can always be reconstructed by
    concatenating ``text[start:end]``.
    """

    start: int
    end: int
    category: str

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "category": self.category}


@dataclass(frozen=True, slots=True)
class Symbol:
    """An outline symbol.

    - ``name``: declared name; anonymous symbols use the fixed token
      ``"<anonymous>"`` (see LanguageBackend docs). Symbols with identical
      names are all kept and distinguished by their ``range``.
    - ``kind``: backend-specific kind string, e.g. ``function`` / ``class``.
    - ``range``: full lexical range of the construct including its body.
    - ``selection_range``: range of the name/identifier for editors.
    - ``children``: nested symbols, ordered by source position.
    """

    name: str
    kind: str
    range: Range
    selection_range: Range
    children: tuple["Symbol", ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "range": self.range.to_dict(),
            "selection_range": self.selection_range.to_dict(),
            "children": [c.to_dict() for c in self.children],
        }


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A parse problem located precisely by line/column."""

    reason: str
    message: str
    range: Range

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "message": self.message,
            "range": self.range.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Analysis:
    """Result of analysing one source document in one language."""

    language: str
    spans: tuple[Span, ...]
    symbols: tuple[Symbol, ...]
    formatted: str
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "highlights": [s.to_dict() for s in self.spans],
            "outline": [s.to_dict() for s in self.symbols],
            "formatted": self.formatted,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class Region:
    """An independently re-parseable region of a document.

    Regions are half-open ranges that partition the whole document
    (``[0, n0) [n0, n1) ... [nk, len(text))``) and are separated by region
    boundaries (blank lines for Python). Editing one region never changes
    analysis offsets/results of other regions, so incremental updates only
    need to re-analyse the regions intersecting the edit.
    """

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Settings:
    """Service configuration.

    - ``languages``: enabled language ids. A request for any other language
      is rejected with ``unknown_language`` (never silently downgraded).
    - ``max_file_size``: maximum accepted decoded source size in characters.
    - ``cache_capacity``: maximum number of cached analysis entries.
    - ``session_capacity``: maximum number of remembered editing sessions.
    - ``timeout_ms``: per-computation timeout in milliseconds.
    """

    languages: frozenset[str]
    max_file_size: int
    cache_capacity: int
    session_capacity: int
    timeout_ms: int

    @staticmethod
    def default() -> "Settings":
        from codeview.config import load_settings

        return load_settings()


ANONYMOUS_NAME: Final[str] = "<anonymous>"
