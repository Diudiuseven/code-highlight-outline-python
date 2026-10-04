"""Language backend interface and registry.

A backend implements three pure, deterministic operations for one language:

1. ``analyze(text)`` -> :class:`Analysis` (highlights + outline + format).
2. ``split_regions(text)`` -> partition used for incremental updates.
3. ``region_is_boundary`` semantics are encoded in ``split_regions``.

Determinism requirements for every backend:

- Given the same input string the output is byte-for-byte identical, at any
  concurrency and in any request order. Backends must keep no per-call
  mutable state.
- Highlight spans are sorted, non-overlapping and cover the whole document.
- Symbols are sorted by (range.start, range.end, kind, name); nesting is
  decided solely by lexical containment. Symbols with duplicate names are
  all preserved and distinguished by their range. Constructs without a
  declarable name (e.g. an unnamed JSON array) use ``"<anonymous>"`` and a
  synthetic selection range at the construct's first character.

Error policy: syntax errors must *not* raise. Backends return whatever
partial result they can guarantee plus at least one ``syntax_error``
diagnostic with a precise line/column range; in the worst case they return
an empty outline, a single ``text`` span over the whole document and the
unchanged input as ``formatted``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from codeview.models import Analysis, Region


@runtime_checkable
class LanguageBackend(Protocol):
    language_id: str

    def analyze(self, text: str) -> Analysis: ...

    def split_regions(self, text: str) -> tuple[Region, ...]: ...


class BackendRegistry:
    """Holds the enabled backends. Backends never share mutable state."""

    def __init__(self) -> None:
        self._backends: dict[str, LanguageBackend] = {}

    def register(self, backend: LanguageBackend) -> None:
        if not getattr(backend, "language_id", None):
            raise ValueError("backend must define a non-empty language_id")
        # Replacing a language only affects that one id; other backends are
        # untouched, so switching a language cannot alter other languages.
        self._backends[backend.language_id] = backend

    def get(self, language_id: str) -> LanguageBackend | None:
        return self._backends.get(language_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))
