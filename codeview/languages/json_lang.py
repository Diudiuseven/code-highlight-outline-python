"""JSON language backend.

The document is a single region (JSON has no boundary that permits local
re-parse), so every edit triggers a full recompute. A custom lexer classifies
characters even for invalid input, while :func:`json.loads` decides validity
and drives the outline.
"""

from __future__ import annotations

import json

from codeview.incremental import analyze_full
from codeview.models import Analysis, Diagnostic, Region, Symbol
from codeview.textutil import make_range, tile_spans

ANONYMOUS = "<anonymous>"


class JsonBackend:
    language_id = "json"

    def analyze(self, text: str) -> Analysis:
        return analyze_full(self, text)

    def split_regions(self, text: str) -> tuple[Region, ...]:
        return (Region(0, len(text)),)

    def analyze_region(self, text: str, is_first: bool, is_last: bool) -> Analysis:
        diagnostics: list[Diagnostic] = []
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            diagnostics.append(_diagnostic(text, exc))

        spans = _lex(text)
        symbols = _outline(text, data) if not diagnostics else ()
        formatted = (
            json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            if not diagnostics
            else _fallback_text(text)
        )
        return Analysis(
            language=self.language_id,
            spans=spans,
            symbols=symbols,
            formatted=formatted,
            diagnostics=tuple(diagnostics),
        )


def _diagnostic(text: str, exc: json.JSONDecodeError) -> Diagnostic:
    # json error positions: lineno 1-based, colno 0-based
    line_index = _line_starts(text)
    start = min(line_index[exc.lineno - 1] + exc.colno, len(text))
    end = min(start + 1, len(text))
    return Diagnostic(
        reason="syntax_error",
        message=exc.msg,
        range=make_range(text, start, end),
    )


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


# ---------------------------------------------------------------------------
# Lexing (tolerant): produces a full tiling for any input.
# ---------------------------------------------------------------------------
def _lex(text: str) -> tuple:
    """Classify characters tolerantly.

    Object keys (a string followed, ignoring whitespace, by ``:``) get the
    ``property`` category; all other strings get ``string``. Invalid input
    is still fully tiled with ``text``.
    """
    segments: list[tuple[int, int, str]] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = _skip_string(text, i)
            k = j
            while k < n and text[k] in " \t\r\n":
                k += 1
            category = "property" if k < n and text[k] == ":" else "string"
            segments.append((i, j, category))
            i = j
            continue
        if ch in "{}[]:,":
            segments.append((i, i + 1, "punctuation"))
            i += 1
            continue
        if ch in " \t\r\n":
            i += 1
            continue
        # number / true / false / null / unknown word
        j = i
        while j < n and text[j] not in ' \t\r\n{}[],:"':
            j += 1
        word = text[i:j]
        if word in ("true", "false", "null"):
            segments.append((i, j, "keyword"))
        elif _looks_like_number(word):
            segments.append((i, j, "number"))
        else:
            segments.append((i, j, "text"))
        i = j
    return tile_spans(segments, n)


def _skip_string(text: str, i: int) -> int:
    j = i + 1
    n = len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == '"':
            return j + 1
        j += 1
    return j


def _looks_like_number(word: str) -> bool:
    try:
        float(word)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Outline: object members and array elements with ranges.
# ---------------------------------------------------------------------------
def _outline(text: str, data) -> tuple[Symbol, ...]:
    # Re-parse with raw_decode to obtain member ranges via a position-aware
    # scan; json gives no positions, so we re-scan deterministically.
    scanner = _Scanner(text)
    try:
        nodes, rng = scanner.parse_value(0)
    except _ScanError:
        return ()
    return tuple(nodes)


class _ScanError(Exception):
    pass


class _Scanner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)

    def skip_ws(self, i: int) -> int:
        while i < self.n and self.text[i] in " \t\r\n":
            i += 1
        return i

    def parse_value(self, i: int, name: str | None = None, kind_hint: str | None = None):
        i = self.skip_ws(i)
        if i >= self.n:
            raise _ScanError
        ch = self.text[i]
        if ch == "{":
            sym, end = self.parse_object(i)
            return sym, (i, end, "object")
        if ch == "[":
            sym, end = self.parse_array(i)
            return sym, (i, end, "array")
        if ch == '"':
            j = self.skip_string(i)
            return [], (i, j, "string")
        j = i
        while j < self.n and self.text[j] not in " \t\r\n{}[],":
            j += 1
        word = self.text[i:j]
        kind = "number" if _looks_like_number(word) else "boolean" if word in ("true", "false") else "null"
        return [], (i, j, kind)

    def skip_string(self, i: int) -> int:
        assert self.text[i] == '"'
        j = i + 1
        while j < self.n:
            if self.text[j] == "\\":
                j += 2
                continue
            if self.text[j] == '"':
                return j + 1
            j += 1
        raise _ScanError

    def parse_object(self, i: int):
        start = i
        i += 1  # {
        children = []
        i = self.skip_ws(i)
        if i < self.n and self.text[i] == "}":
            i += 1
            sym = self._symbol(ANONYMOUS, "object", start, i)
            return [sym], i
        while True:
            i = self.skip_ws(i)
            if i >= self.n or self.text[i] != '"':
                raise _ScanError
            key_start = i
            key_end = self.skip_string(i)
            key_name = json.loads(self.text[key_start:key_end])
            i = self.skip_ws(key_end)
            if i >= self.n or self.text[i] != ":":
                raise _ScanError
            i += 1
            child_nodes, (vs, ve, vkind) = self.parse_value(i)
            i = ve
            # Flatten the one anonymous container symbol: its members become
            # direct children of the property, so the outline mirrors the
            # JSON nesting without a redundant "<anonymous>" level.
            if vkind in ("object", "array") and child_nodes:
                prop_children = child_nodes[0].children
            else:
                prop_children = ()
            sym = self._symbol(key_name, "property", key_start, key_end, vs, ve, prop_children)
            children.append(sym)
            i = self.skip_ws(i)
            if i < self.n and self.text[i] == ",":
                i += 1
                continue
            if i < self.n and self.text[i] == "}":
                i += 1
                break
            raise _ScanError
        sym = self._symbol(ANONYMOUS, "object", start, i, children=tuple(children))
        return [sym], i

    def parse_array(self, i: int):
        start = i
        i += 1  # [
        children = []
        i = self.skip_ws(i)
        if i < self.n and self.text[i] == "]":
            i += 1
            return [self._symbol(ANONYMOUS, "array", start, i)], i
        idx = 0
        while True:
            child_nodes, (vs, ve, kind) = self.parse_value(i)
            i = ve
            if kind in ("object", "array") and child_nodes:
                elem_children = child_nodes[0].children
            else:
                elem_children = ()
            children.append(
                self._symbol(str(idx), kind, vs, ve, children=elem_children)
            )
            idx += 1
            i = self.skip_ws(i)
            if i < self.n and self.text[i] == ",":
                i += 1
                continue
            if i < self.n and self.text[i] == "]":
                i += 1
                break
            raise _ScanError
        return [self._symbol(ANONYMOUS, "array", start, i, children=tuple(children))], i

    def _symbol(self, name, kind, start, end, sel_start=None, sel_end=None, children=()):
        return Symbol(
            name=name,
            kind=kind,
            range=make_range(self.text, start, end),
            selection_range=make_range(
                self.text,
                start if sel_start is None else sel_start,
                start + 1 if sel_end is None else sel_end,
            ),
            children=children,
        )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def _fallback_text(text: str) -> str:
    """Invalid JSON: normalize newlines only, never fabricate content."""
    return text.replace("\r\n", "\n").replace("\r", "\n")
