"""JSON language plugin.

Highlighting uses a hand-rolled, deterministic scanner (tolerant: it stops
at the first lexical error and reports it with line/col). Outline maps
object keys to symbols; arrays are transparent (no symbol). Formatting is a
deterministic pretty-printer that preserves key order *and* duplicate keys,
and keeps number literals in their original textual form, so it never
changes document semantics and is idempotent.
"""
from __future__ import annotations

import json
import re

from app.languages.tiling import tile_highlights
from app.models import (
    AnalysisResult,
    Diagnostic,
    Highlight,
    Position,
    Range,
    Severity,
    Symbol,
    SymbolKind,
)

_NUMBER_RE = re.compile(r"-?\d+(\.\d+)?([eE][+-]?\d+)?")
_LITERALS = {"true", "false", "null", "Infinity", "NaN", "-Infinity"}
_PUNCT = "{}[]:,"


class _Raw(str):
    """A raw JSON token (number/constant) captured verbatim from the source.

    Distinct from a plain ``str`` value, which always represents a JSON
    *string* and must be re-emitted quoted even when it looks numeric
    (``"123"``) or boolean/null-like (``"true"``).
    """


class _Object(list):
    """Ordered list of (key, value) pairs; preserves duplicate keys."""


def _pos_of(text: str, offset: int) -> Position:
    line = text.count("\n", 0, offset)
    col = offset - (text.rfind("\n", 0, offset) + 1)
    return Position(line, col)


class JsonPlugin:
    name = "json"

    # -------------------------------------------------- plugin interface
    def analyze(self, source: str) -> AnalysisResult:
        diagnostics: list[Diagnostic] = []
        highlights, _keys = self._scan(source, diagnostics, line_offset=0)
        highlights = tile_highlights(source, highlights)
        return self.rederive(source, highlights, extra_diagnostics=diagnostics)

    def tokenize_region(self, snippet: str, line_offset: int) -> list[Highlight] | None:
        """Re-scan an isolated line region. JSON strings never span lines,
        so a region aligned to line boundaries scans cleanly; any lexical
        error inside the region forces a full-recompute fallback (None)."""
        diagnostics: list[Diagnostic] = []
        highlights, _keys = self._scan(snippet, diagnostics, line_offset)
        if diagnostics:
            return None
        return highlights

    def lexical(self, highlights: list[Highlight]) -> list[Highlight]:
        return highlights  # all JSON categories are lexical

    def rederive(
        self,
        source: str,
        highlights: list[Highlight],
        extra_diagnostics: list[Diagnostic] | None = None,
    ) -> AnalysisResult:
        diagnostics: list[Diagnostic] = list(extra_diagnostics or [])
        keys = [
            (_key_value(source, h.range), h.range)
            for h in highlights
            if h.category == "key"
        ]
        data = self._parse(source, diagnostics)
        outline = self._outline(data, keys) if data is not None else []
        if data is not None:
            formatted = self._render(data, 0) + "\n"
        else:
            formatted = self._conservative_format(source)
        return AnalysisResult(
            language=self.name,
            highlights=highlights,
            outline=outline,
            formatted=formatted,
            diagnostics=diagnostics,
            degraded=bool(diagnostics),
        )

    # -------------------------------------------------------------- scanner
    def _scan(
        self, source: str, diagnostics: list[Diagnostic], line_offset: int = 0
    ) -> tuple[list[Highlight], list[tuple[str, Range]]]:
        """Return (highlight tokens, key tokens in document order)."""
        tokens: list[Highlight] = []
        keys: list[tuple[str, Range]] = []
        i, n = 0, len(source)

        def rng(a: int, b: int) -> Range:
            st, en = _pos_of(source, a), _pos_of(source, b)
            return Range(
                Position(st.line + line_offset, st.col),
                Position(en.line + line_offset, en.col),
            )

        while i < n:
            ch = source[i]
            if ch in " \t\r\n":
                i += 1
                continue
            if ch in _PUNCT:
                tokens.append(Highlight(rng(i, i + 1), "punctuation"))
                i += 1
                continue
            if ch == '"':
                j = i + 1
                ok = False
                while j < n:
                    if source[j] == "\\":
                        j += 2
                        continue
                    if source[j] == '"':
                        ok = True
                        j += 1
                        break
                    if source[j] == "\n":
                        break
                    j += 1
                if not ok:
                    diagnostics.append(
                        Diagnostic(
                            code="syntax-error",
                            message="unterminated string literal",
                            severity=Severity.ERROR,
                            position=rng(i, i).start,
                        )
                    )
                    tokens.append(Highlight(rng(i, n), "unknown"))
                    break
                k = j
                while k < n and source[k] in " \t\r\n":
                    k += 1
                category = "key" if k < n and source[k] == ":" else "string"
                r = rng(i, j)
                tokens.append(Highlight(r, category))
                if category == "key":
                    try:
                        keys.append((json.loads(source[i:j]), r))
                    except ValueError:
                        keys.append((source[i + 1 : j - 1], r))
                i = j
                continue
            m = _NUMBER_RE.match(source, i)
            if m and (ch.isdigit() or ch == "-"):
                tokens.append(Highlight(rng(i, m.end()), "number"))
                i = m.end()
                continue
            if ch.isalpha():
                j = i
                while j < n and source[j].isalpha():
                    j += 1
                word = source[i:j]
                cat = "literal" if word in _LITERALS else "unknown"
                if cat == "unknown":
                    diagnostics.append(
                        Diagnostic(
                            code="syntax-error",
                            message=f"unexpected token {word!r}",
                            severity=Severity.ERROR,
                            position=rng(i, i).start,
                        )
                    )
                tokens.append(Highlight(rng(i, j), cat))
                i = j
                continue
            diagnostics.append(
                Diagnostic(
                    code="syntax-error",
                    message=f"unexpected character {ch!r}",
                    severity=Severity.ERROR,
                    position=rng(i, i).start,
                )
            )
            tokens.append(Highlight(rng(i, i + 1), "unknown"))
            i += 1
        return tokens, keys

    # --------------------------------------------------------------- parser
    def _parse(self, source: str, diagnostics: list[Diagnostic]):
        try:
            return json.loads(
                source,
                object_pairs_hook=_Object,
                parse_int=_Raw,
                parse_float=_Raw,
                parse_constant=_Raw,
            )
        except json.JSONDecodeError as exc:
            diagnostics.append(
                Diagnostic(
                    code="syntax-error",
                    message=exc.msg,
                    severity=Severity.ERROR,
                    position=Position(exc.lineno - 1, exc.colno - 1),
                )
            )
            return None
        except (RecursionError, MemoryError) as exc:
            diagnostics.append(
                Diagnostic(
                    code="syntax-error",
                    message=f"{type(exc).__name__}: {exc}",
                    severity=Severity.ERROR,
                )
            )
            return None

    # -------------------------------------------------------------- outline
    def _outline(self, data, keys: list[tuple[str, Range]]) -> list[Symbol]:
        cursor = iter(keys)

        def build(value, path: str) -> list[Symbol]:
            if isinstance(value, _Object):
                found: list[tuple[str, object, Range]] = []
                for key, child in value:
                    rng = next(cursor, (key, None))[1]
                    if rng is None:
                        return []  # positions exhausted; bail out safely
                    found.append((key, child, rng))
                seen: dict[str, int] = {}
                symbols: list[Symbol] = []
                for key, child, rng in found:
                    seen[key] = seen.get(key, 0) + 1
                    suffix = "" if seen[key] == 1 else f"#{seen[key]}"
                    sym_id = f"{path}/{key}{suffix}"
                    symbols.append(
                        Symbol(
                            name=key,
                            kind=SymbolKind.KEY,
                            range=rng,
                            symbol_id=sym_id,
                            children=build(child, sym_id),
                        )
                    )
                return symbols
            if isinstance(value, list):
                symbols = []
                for item in value:
                    symbols.extend(build(item, path))
                return symbols
            return []

        return build(data, "root")

    # -------------------------------------------------------------- format
    def _render(self, value, depth: int) -> str:
        pad = "  " * depth
        child_pad = "  " * (depth + 1)
        if isinstance(value, _Object):
            if not value:
                return "{}"
            items = [
                f"{child_pad}{json.dumps(k, ensure_ascii=False)}: {self._render(v, depth + 1)}"
                for k, v in value
            ]
            return "{\n" + ",\n".join(items) + "\n" + pad + "}"
        if isinstance(value, list):
            if not value:
                return "[]"
            items = [f"{child_pad}{self._render(v, depth + 1)}" for v in value]
            return "[\n" + ",\n".join(items) + "\n" + pad + "]"
        if isinstance(value, _Raw):
            # numbers / NaN / Infinity: keep the exact source spelling
            return str(value)
        if isinstance(value, str):
            # a genuine JSON string: always quote it, even if its content
            # looks like a number, boolean or null
            return json.dumps(value, ensure_ascii=False)
        if value is True:
            return "true"
        if value is False:
            return "false"
        if value is None:
            return "null"
        return str(value)

    @staticmethod
    def _conservative_format(source: str) -> str:
        text = source.replace("\r\n", "\n").replace("\r", "\n")
        return text if text.endswith("\n") or not text else text + "\n"


def _key_value(source: str, rng: Range) -> str:
    line = source.split("\n")[rng.start.line]
    raw = line[rng.start.col : rng.end.col]
    try:
        return json.loads(raw)
    except ValueError:
        return raw.strip('"')
