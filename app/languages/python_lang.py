"""Python language plugin (tokenize + ast based).

Fault tolerance: tokenization/parse errors never raise; whatever could be
computed is returned together with precise line/col diagnostics and
``degraded=True``.
"""
from __future__ import annotations

import ast
import io
import keyword
import tokenize

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

_DECL_CATEGORIES = {
    ast.FunctionDef: "function.declaration",
    ast.AsyncFunctionDef: "function.declaration",
    ast.ClassDef: "class.declaration",
}


def _category_of(tok: tokenize.TokenInfo) -> str | None:
    if tok.type == tokenize.COMMENT:
        return "comment"
    if tok.type == tokenize.STRING:
        return "string"
    if tok.type == tokenize.NUMBER:
        return "number"
    if tok.type == tokenize.OP:
        return "operator"
    if tok.type == tokenize.NAME:
        return "keyword" if keyword.iskeyword(tok.string) else "identifier"
    return None  # INDENT/DEDENT/NEWLINE/NL/ENDMARKER carry no semantics


def _diag_from_exc(exc: Exception) -> Diagnostic:
    pos = None
    if isinstance(exc, tokenize.TokenError) and len(exc.args) > 1:
        row, col = exc.args[1]
        pos = Position(max(row - 1, 0), col)
    elif isinstance(exc, SyntaxError) and exc.lineno is not None:
        pos = Position(exc.lineno - 1, max((exc.offset or 1) - 1, 0))
    return Diagnostic(
        code="syntax-error",
        message=f"{type(exc).__name__}: {exc.args[0] if exc.args else exc}",
        severity=Severity.ERROR,
        position=pos,
    )


def _text_of(source: str, rng: Range) -> str:
    lines = source.split("\n")
    if rng.start.line == rng.end.line:
        return lines[rng.start.line][rng.start.col : rng.end.col]
    parts = [lines[rng.start.line][rng.start.col :]]
    parts.extend(lines[rng.start.line + 1 : rng.end.line])
    parts.append(lines[rng.end.line][: rng.end.col])
    return "\n".join(parts)


class PythonPlugin:
    name = "python"

    # -------------------------------------------------- plugin interface
    def analyze(self, source: str) -> AnalysisResult:
        diagnostics: list[Diagnostic] = []
        highlights, protected, ok = self._tokenize(source, diagnostics)
        result = self.rederive(source, highlights, extra_diagnostics=diagnostics)
        if not ok:
            # tokenization failed: fall back to conservative formatting
            result.formatted = self.format(source, None)
        return result

    def tokenize_region(self, snippet: str, line_offset: int) -> list[Highlight] | None:
        """Tokenize an isolated line region (1st line = ``line_offset``).

        Returns None when the region does not end in a clean lexer state;
        the caller must then widen the region or recompute fully.
        """
        out: list[Highlight] = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(snippet).readline):
                cat = _category_of(tok)
                if cat is None:
                    continue
                st = Position(tok.start[0] - 1 + line_offset, tok.start[1])
                en = Position(tok.end[0] - 1 + line_offset, tok.end[1])
                out.append(Highlight(Range(st, en), cat))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return None
        return out

    def lexical(self, highlights: list[Highlight]) -> list[Highlight]:
        return [
            Highlight(h.range, "identifier")
            if h.category.endswith(".declaration")
            else h
            for h in highlights
        ]

    def rederive(
        self,
        source: str,
        highlights: list[Highlight],
        extra_diagnostics: list[Diagnostic] | None = None,
    ) -> AnalysisResult:
        """Compute outline + formatting around an existing highlight list
        (used by both the full and the incremental path)."""
        diagnostics: list[Diagnostic] = list(extra_diagnostics or [])
        tree = self._parse(source, diagnostics)
        if tree is not None:
            highlights = self._mark_declarations(source, highlights, tree)
        outline = self._outline(tree) if tree is not None else []
        protected = self._protected_lines(highlights)
        formatted = self.format(source, protected)
        return AnalysisResult(
            language=self.name,
            highlights=highlights,
            outline=outline,
            formatted=formatted,
            diagnostics=diagnostics,
            degraded=bool(diagnostics),
        )

    # ------------------------------------------------------------ tokenize
    def _tokenize(
        self, source: str, diagnostics: list[Diagnostic]
    ) -> tuple[list[Highlight], set[int], bool]:
        """Return (highlights, protected_lines, ok). Protected lines belong
        to multi-line strings and must not be touched by the formatter."""
        highlights: list[Highlight] = []
        protected: set[int] = set()
        ok = True
        try:
            for tok in tokenize.generate_tokens(io.StringIO(source).readline):
                if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                    protected.update(range(tok.start[0], tok.end[0] + 1))
                cat = _category_of(tok)
                if cat is None:
                    continue
                st = Position(tok.start[0] - 1, tok.start[1])
                en = Position(tok.end[0] - 1, tok.end[1])
                highlights.append(Highlight(Range(st, en), cat))
        except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
            ok = False
            diagnostics.append(_diag_from_exc(exc))
        highlights.sort(key=lambda h: (h.range.start, h.range.end))
        # Fill every gap (whitespace, newlines, untokenized tails after a
        # lexical error) so the ranges tile the whole source exactly once.
        highlights = tile_highlights(source, highlights)
        return highlights, protected, ok

    @staticmethod
    def _protected_lines(highlights: list[Highlight]) -> set[int]:
        """1-based line numbers covered by multi-line string tokens."""
        protected: set[int] = set()
        for h in highlights:
            if h.category == "string" and h.range.end.line > h.range.start.line:
                protected.update(range(h.range.start.line + 1, h.range.end.line + 2))
        return protected

    # ------------------------------------------------------------------ ast
    def _parse(self, source: str, diagnostics: list[Diagnostic]) -> ast.Module | None:
        try:
            return ast.parse(source)
        except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
            diagnostics.append(_diag_from_exc(exc))
            return None

    def _mark_declarations(
        self, source: str, highlights: list[Highlight], tree: ast.Module
    ) -> list[Highlight]:
        """Upgrade the identifier token naming each def/class to a
        ``*.declaration`` category (replaces, never overlaps)."""
        decls: list[tuple[int, int, str, str]] = []  # line0, line1, name, category
        for node in ast.walk(tree):
            category = _DECL_CATEGORIES.get(type(node))
            if category is None:
                continue
            end_line = node.body[0].lineno if node.body else node.lineno
            decls.append((node.lineno - 1, end_line - 1, node.name, category))
        out = list(highlights)
        for line0, line1, name, cat in decls:
            # mark only the FIRST matching identifier: the declaration name
            for i, h in enumerate(out):
                if (
                    h.category == "identifier"
                    and line0 <= h.range.start.line <= line1
                    and _text_of(source, h.range) == name
                ):
                    out[i] = Highlight(h.range, cat)
                    break
        return out

    # -------------------------------------------------------------- outline
    def _outline(self, tree: ast.Module) -> list[Symbol]:
        """Deterministic outline.

        - children sorted by (start line, col, name)
        - duplicate names among siblings get ``#2``, ``#3`` ... id suffixes
        - anonymous lambdas are named ``<lambda>`` with id ``<lambda>@L:C``
        """
        def node_range(node: ast.AST) -> Range:
            return Range(
                Position(node.lineno - 1, node.col_offset),
                Position(node.end_lineno - 1, node.end_col_offset),  # type: ignore[attr-defined]
            )

        def build(node: ast.AST, path: str, in_class: bool) -> list[Symbol]:
            found: list[tuple[ast.AST, str, SymbolKind]] = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    found.append((child, child.name, SymbolKind.CLASS))
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = SymbolKind.METHOD if in_class else SymbolKind.FUNCTION
                    found.append((child, child.name, kind))
                elif isinstance(child, ast.Lambda):
                    found.append((child, "<lambda>", SymbolKind.FUNCTION))
                elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    for t in targets:
                        if isinstance(t, ast.Name):
                            found.append((t, t.id, SymbolKind.VARIABLE))
                    if isinstance(child.value, ast.Lambda):
                        found.append((child.value, "<lambda>", SymbolKind.FUNCTION))
            symbols: list[Symbol] = []
            order = sorted(
                found,
                key=lambda item: (item[0].lineno, item[0].col_offset, item[1]),
            )
            seen: dict[str, int] = {}
            for child, name, kind in order:
                seen[name] = seen.get(name, 0) + 1
                suffix = "" if seen[name] == 1 else f"#{seen[name]}"
                if isinstance(child, ast.Lambda):
                    sym_id = f"{path}/<lambda>@{child.lineno}:{child.col_offset}"
                else:
                    sym_id = f"{path}/{name}{suffix}"
                child_in_class = isinstance(child, ast.ClassDef)
                symbols.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        range=node_range(child),
                        symbol_id=sym_id,
                        children=build(child, sym_id, child_in_class),
                    )
                )
            return symbols

        return build(tree, "module", in_class=False)

    # --------------------------------------------------------------- format
    def format(self, source: str, protected: set[int] | None) -> str:
        """Normalize text. Idempotent and semantics-preserving.

        Steps: CRLF/CR -> LF; expand leading tabs (8-col tabstops, matching
        Python's own indentation rule); strip trailing whitespace; collapse
        3+ blank lines to 2; ensure exactly one trailing newline.
        Lines inside multi-line strings (``protected``) are never touched.
        When ``protected is None`` (tokenization failed) only newline
        normalization is applied, which is always safe.
        """
        text = source.replace("\r\n", "\n").replace("\r", "\n")
        if protected is None:
            return text if text.endswith("\n") or not text else text + "\n"
        out: list[str] = []
        blank_run = 0
        lines = text.split("\n")
        for lineno, line in enumerate(lines, start=1):
            if lineno in protected:
                out.append(line)
                blank_run = 0
                continue
            stripped = line.lstrip(" \t")
            indent_src = line[: len(line) - len(stripped)]
            col = 0
            indent_chars: list[str] = []
            for ch in indent_src:
                if ch == "\t":
                    n = 8 - (col % 8)
                    indent_chars.append(" " * n)
                    col += n
                else:
                    indent_chars.append(" ")
                    col += 1
            normalized = ("".join(indent_chars) + stripped).rstrip()
            if normalized == "":
                blank_run += 1
                if blank_run > 2:
                    continue
            else:
                blank_run = 0
            out.append(normalized)
        return "\n".join(out).rstrip("\n") + "\n"
