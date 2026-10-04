"""Core data models shared by all components."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True, order=True)
class Position:
    """Zero-based line/column position."""
    line: int
    col: int


@dataclass(frozen=True)
class Range:
    """Half-open source range [start, end)."""
    start: Position
    end: Position

    def contains(self, pos: Position) -> bool:
        return self.start <= pos < self.end


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    """A non-fatal issue; `code` is machine-readable, position precise to line/col."""
    code: str
    message: str
    severity: Severity
    position: Position | None = None


@dataclass(frozen=True)
class Highlight:
    """A semantic highlight token. Ranges never overlap and can reconstruct the source."""
    range: Range
    category: str


class SymbolKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    KEY = "key"


@dataclass
class Symbol:
    """An outline symbol. `symbol_id` is unique & deterministic; children are nested."""
    name: str
    kind: SymbolKind
    range: Range
    symbol_id: str
    children: list[Symbol] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """Full analysis output. `degraded` is True when fault-tolerance kicked in."""
    language: str
    highlights: list[Highlight]
    outline: list[Symbol]
    formatted: str
    diagnostics: list[Diagnostic]
    degraded: bool = False


@dataclass(frozen=True)
class TextEdit:
    """Replace range [start, end) of the old text with `new_text`."""
    start: Position
    end: Position
    new_text: str


@dataclass
class IncrementalResult:
    """Incremental analysis: same payload as full analysis plus the affected range."""
    result: AnalysisResult
    affected: Range
