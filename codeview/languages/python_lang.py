"""Python language backend (CPython tokenize + ast based).

Incremental strategy
--------------------
The document is partitioned at *top-level blank-line runs* (see
``split_regions``). Such a boundary can never lie inside a lexical token or
inside an indented block, so every region contains whole statements and is
independently tokenizable/parsable. The full and incremental pipelines both
assemble results by analysing regions with the same code path, which
guarantees identical output.
"""

from __future__ import annotations

import ast
import builtins
import io
import keyword
import tokenize

from codeview.incremental import analyze_full
from codeview.models import Analysis, Diagnostic, Region, Symbol
from codeview.textutil import build_line_index, make_range, normalize_newlines, tile_spans

_BUILTIN_NAMES = frozenset(dir(builtins))

_PUNCTUATION = frozenset("()[]{},:;@.")
# keywords that introduce a definition name
_DEF_KEYWORDS = frozenset({"def", "class"})


class PythonBackend:
    language_id = "python"

    # ---- public API -------------------------------------------------------
    def analyze(self, text: str) -> Analysis:
        return analyze_full(self, text)

    def split_regions(self, text: str) -> tuple[Region, ...]:
        return tuple(Region(a, b) for a, b in _compute_region_bounds(text))

    # ---- region analysis (used by the incremental driver) -----------------
    def analyze_region(self, text: str, is_first: bool, is_last: bool) -> Analysis:
        formatted = _canonicalize(text, is_first=is_first, is_last=is_last)
        diagnostics: list[Diagnostic] = []
        segments: list[tuple[int, int, str]] = []
        symbols: tuple[Symbol, ...] = ()

        try:
            tokens = list(_tokenize(text))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            tokens = None

        if tokens is not None:
            segments = _classify_tokens(text, tokens)
            tree = _parse_tree(text, diagnostics)
            if tree is not None:
                extra = _semantic_segments(text, tree)
                segments.extend(extra)
                symbols = _build_symbols(text, tree)
        else:
            # Tokenizer failure: degrade honestly but keep formatting.
            diagnostics.append(_whole_document_diagnostic(text))

        spans = tile_spans(segments, len(text))
        return Analysis(
            language=self.language_id,
            spans=spans,
            symbols=symbols,
            formatted=formatted,
            diagnostics=tuple(diagnostics),
        )


# ===========================================================================
# Region partition
# ===========================================================================
def _tokenize(text: str):
    yield from tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline)


def _compute_region_bounds(text: str) -> list[tuple[int, int]]:
    """Return half-open region bounds partitioning the whole text.

    A region boundary is a run of blank lines located *at module level*:

    - each blank line in the run starts at column 0;
    - the nearest non-empty lines before and after the run both start at
      column 0 (so the run sits between top-level statements, never inside
      an indented block or bracket continuation);
    - the run is not inside a multiline string literal;
    - the previous non-empty line does not end with a continuation
      backslash.

    Leading and trailing blank runs stay attached to the first/last region.
    A tokenization failure degrades to a single whole-document region.
    """
    n = len(text)
    if not text:
        return [(0, n)]

    protected_rows: set[int] = set()
    try:
        tokens = list(_tokenize(text))
        line_index = build_line_index(text)
        for s0, e0 in _multiline_token_ranges(text, tokens):
            start_line = _row_of(line_index, s0)
            end_line = _row_of(line_index, e0)
            protected_rows.update(range(start_line, end_line + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return [(0, n)]

    lines = text.split("\n")
    row_count = len(lines)

    def is_blank(r: int) -> bool:
        return lines[r - 1].strip() == ""

    def starts_at_zero(r: int) -> bool:
        line = lines[r - 1]
        return line != "" and line[0] not in (" ", "\t")

    cuts: list[int] = []
    r = 1
    while r <= row_count:
        if not is_blank(r) or r in protected_rows:
            r += 1
            continue
        run_start = r
        while r <= row_count and is_blank(r):
            r += 1
        run_end = r - 1
        # Walk back past blanks AND indented lines to the enclosing
        # top-level statement; the run only separates top-level regions if
        # both surrounding statements begin at column 0.
        prev_r = run_start - 1
        while prev_r >= 1 and (is_blank(prev_r) or not starts_at_zero(prev_r)):
            prev_r -= 1
        next_r = run_end + 1
        while next_r <= row_count and (is_blank(next_r) or not starts_at_zero(next_r)):
            next_r += 1
        if (
            prev_r >= 1
            and next_r <= row_count
            and starts_at_zero(prev_r)
            and starts_at_zero(next_r)
            and not lines[prev_r - 1].rstrip().endswith("\\")
        ):
            # every blank in the run must be column 0 and outside strings
            col0 = all(lines[br - 1] == "" for br in range(run_start, run_end + 1))
            unprotected = all(br not in protected_rows for br in range(run_start, run_end + 1))
            if col0 and unprotected:
                cuts.append(line_index[run_start - 1])

    cuts.sort()
    bounds: list[tuple[int, int]] = []
    start = 0
    for cut in cuts:
        if cut > start:
            bounds.append((start, cut))
            start = cut
    bounds.append((start, n))
    return bounds


def _row_of(line_index: list[int], offset: int) -> int:
    import bisect

    return bisect.bisect_right(line_index, offset)


# ===========================================================================
# Highlighting
# ===========================================================================
def _offset(line_index: list[int], row: int, col: int) -> int:
    # tokenize emits a virtual ENDMARKER row one past the last physical
    # line when the source has no trailing newline; clamp it to EOF.
    idx = min(max(row - 1, 0), len(line_index) - 1)
    line_start = line_index[idx]
    return line_start + col


def _multiline_token_ranges(text: str, tokens) -> list[tuple[int, int]]:
    """Ranges of multiline string literals (protected during formatting)."""
    line_index = build_line_index(text)
    ranges: list[tuple[int, int]] = []
    fstart_kind = getattr(tokenize, "FSTRING_START", None)
    for tok in tokens:
        if tok.type == tokenize.STRING and tok.start[0] != tok.end[0]:
            ranges.append((_offset(line_index, *tok.start), _offset(line_index, *tok.end)))
        elif fstart_kind is not None and tok.type == fstart_kind and tok.start[0] != tok.end[0]:
            # The start token itself already spans to end row when the
            # opening delimiter is on a different line than the closing.
            ranges.append((_offset(line_index, *tok.start), _offset(line_index, *tok.end)))
    return ranges


def _classify_tokens(text: str, tokens) -> list[tuple[int, int, str]]:
    line_index = build_line_index(text)
    segments: list[tuple[int, int, str]] = []
    # Expectation for the next NAME token:
    #   None | "def_name" | "class_name" | "decorated"
    expect: list[str | None] = [None]
    at_kind = getattr(tokenize, "FSTRING_START", None)
    for tok in tokens:
        kind = tok.type
        s = _offset(line_index, *tok.start)
        e = _offset(line_index, *tok.end)
        if kind in (
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
            tokenize.ENCODING,
        ):
            continue
        if kind == tokenize.COMMENT:
            segments.append((s, e, "comment"))
            continue
        if kind == tokenize.STRING:
            segments.append((s, e, "string"))
            continue
        if at_kind is not None and kind == at_kind:
            segments.append((s, e, "string"))
            continue
        if kind == tokenize.NUMBER:
            segments.append((s, e, "number"))
            continue
        if kind == tokenize.OP:
            segments.append(
                (s, e, "punctuation" if tok.string in _PUNCTUATION else "operator")
            )
            if tok.string == "@" and (not segments or True):
                expect.append("decorated")
            continue
        if kind == tokenize.NAME:
            wanted = expect[-1]
            if keyword.iskeyword(tok.string) or keyword.issoftkeyword(tok.string):
                segments.append((s, e, "keyword"))
                if tok.string == "def":
                    expect.append("def_name")
                elif tok.string == "class":
                    expect.append("class_name")
                elif tok.string == "async":
                    expect.append("maybe_async_def")
            elif wanted == "def_name":
                segments.append((s, e, "function"))
                expect.pop()
            elif wanted == "class_name":
                segments.append((s, e, "class"))
                expect.pop()
            elif wanted == "decorated":
                segments.append((s, e, "decorator"))
                expect.pop()
            elif wanted == "maybe_async_def":
                if tok.string == "def":
                    # unusual (NAME 'def' won't happen; def is keyword)
                    expect[-1] = "def_name"
                    segments.append((s, e, "keyword"))
                else:
                    expect.pop()
                    if tok.string in _BUILTIN_NAMES:
                        segments.append((s, e, "builtin"))
                    else:
                        segments.append((s, e, "identifier"))
            elif tok.string in _BUILTIN_NAMES:
                segments.append((s, e, "builtin"))
            else:
                segments.append((s, e, "identifier"))
            continue
    return segments


# ===========================================================================
# AST based enrichment and outline
# ===========================================================================
def _parse_tree(text: str, diagnostics: list[Diagnostic]) -> ast.AST | None:
    try:
        return ast.parse(text)
    except SyntaxError as exc:
        diagnostics.append(_syntax_diagnostic(text, exc))
        return None


def _syntax_diagnostic(text: str, exc: SyntaxError) -> Diagnostic:
    n = len(text)
    line_index = build_line_index(text)
    if exc.lineno is not None:
        start = line_index[max(1, exc.lineno) - 1] + (exc.offset or 0) - 1
        end_lineno = getattr(exc, "end_lineno", None) or exc.lineno
        end_col = getattr(exc, "end_offset", None) or (exc.offset or 0)
        end = line_index[max(1, end_lineno) - 1] + max(end_col - 1, 0)
        start = max(0, min(start, n))
        end = max(start, min(end, n))
    else:
        start = end = min(n, 0)
    return Diagnostic(
        reason="syntax_error",
        message=str(exc.msg),
        range=make_range(text, start, end),
    )


def _whole_document_diagnostic(text: str) -> Diagnostic:
    return Diagnostic(
        reason="syntax_error",
        message="source could not be tokenized",
        range=make_range(text, 0, min(len(text), 1)),
    )


def _semantic_segments(text: str, tree: ast.AST) -> list[tuple[int, int, str]]:
    line_index = build_line_index(text)
    segments: list[tuple[int, int, str]] = []

    def off(node, row_attr="lineno", col_attr="col_offset"):
        return line_index[getattr(node, row_attr) - 1] + getattr(node, col_attr)

    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            s = off(node)
            segments.append((s, s + len(node.arg), "parameter"))
        elif isinstance(node, ast.Name) and node.id.isupper() and len(node.id) > 1:
            if isinstance(node.ctx, ast.Store):
                s = off(node)
                segments.append((s, s + len(node.id), "constant"))
    return segments


def _node_offset(line_index, row, col) -> int:
    return line_index[row - 1] + col


def _build_symbols(text: str, tree: ast.AST) -> tuple[Symbol, ...]:
    line_index = build_line_index(text)

    def name_range(node: ast.AST) -> tuple[int, int]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kw_len = 6 if isinstance(node, ast.AsyncFunctionDef) else 3
            line_start = line_index[node.lineno - 1]
            j = line_start + node.col_offset + kw_len
            while j < len(text) and text[j] == " ":
                j += 1
            return j, j + len(node.name)
        if isinstance(node, ast.ClassDef):
            j = line_index[node.lineno - 1] + node.col_offset + 5
            while j < len(text) and text[j] == " ":
                j += 1
            return j, j + len(node.name)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name):
                s = _node_offset(line_index, target.lineno, target.col_offset)
                return s, s + len(target.id)
        raise ValueError("unsupported node")

    def full_range(node: ast.AST) -> tuple[int, int]:
        start_row = node.lineno
        start_col = node.col_offset
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
            dec = node.decorator_list[0]
            start_row, start_col = dec.lineno, dec.col_offset
        s = _node_offset(line_index, start_row, start_col)
        e = _node_offset(line_index, node.end_lineno, node.end_col_offset)
        return s, e

    def kind_of(node: ast.AST) -> str:
        if isinstance(node, ast.AsyncFunctionDef):
            return "async function"
        if isinstance(node, ast.FunctionDef):
            return "function"
        if isinstance(node, ast.ClassDef):
            return "class"
        return "variable"

    def node_name(node: ast.AST) -> str:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return node.name
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        return target.id

    def convert(node: ast.AST) -> Symbol | None:
        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign),
        ):
            return None
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and not isinstance(target, ast.Name):
            return None
        children = _child_symbols(node)
        rs, re_ = full_range(node)
        ss, se = name_range(node)
        return Symbol(
            name=node_name(node),
            kind=kind_of(node),
            range=make_range(text, rs, re_),
            selection_range=make_range(text, ss, se),
            children=children,
        )

    def _child_symbols(node: ast.AST) -> tuple[Symbol, ...]:
        bodies: list[ast.AST] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bodies = list(node.body)
        elif isinstance(node, ast.ClassDef):
            bodies = list(node.body)
        out: list[Symbol] = []
        for child in bodies:
            conv = convert(child)
            if conv is not None:
                out.append(conv)
        return tuple(out)

    result: list[Symbol] = []
    for stmt in tree.body:
        conv = convert(stmt)
        if conv is not None:
            result.append(conv)
    return tuple(result)


# ===========================================================================
# Canonical formatting
# ===========================================================================
def _in_protected(pos: int, protected: list[tuple[int, int]]) -> bool:
    for s, e in protected:
        if s <= pos < e:
            return True
        if s > pos:
            break
    return False


def _canonicalize(raw: str, *, is_first: bool, is_last: bool) -> str:
    """Normalize a region deterministically.

    Operations (all semantics-preserving, skipped inside multiline strings):
    CRLF/CR -> LF; strip trailing horizontal whitespace (unless the line
    ends with a line-continuation backslash); collapse runs of >=2 blank
    lines to one blank line; drop leading blank lines for the first region
    and trailing blank lines for the last; guarantee one final newline for
    non-empty documents.
    """
    text = normalize_newlines(raw)
    if text == "":
        return ""

    protected: list[tuple[int, int]] = []
    try:
        tokens = list(_tokenize(text))
        protected = _multiline_token_ranges(text, tokens)
        protected.sort()
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # Cannot safely locate string literals: only normalize newlines.
        return text

    raw_lines = text.split("\n")
    # final split element after trailing newline is ""; keep line table
    line_index = build_line_index(text)

    # 1) trailing horizontal whitespace (outside protected ranges)
    cleaned: list[str] = []
    for idx, line in enumerate(raw_lines):
        line_start = line_index[idx] if idx < len(line_index) else len(text)
        if _in_protected(line_start, protected):
            cleaned.append(line)
            continue
        stripped = line.rstrip(" \t")
        if stripped.endswith("\\"):
            cleaned.append(line)  # continuation whitespace is significant
        else:
            cleaned.append(stripped)

    # 2) blank-line collapse + edge handling, marking protected blank lines
    is_blank = [ln == "" for ln in cleaned]
    protected_blank = [False] * len(cleaned)
    for idx, line in enumerate(raw_lines):
        line_start = line_index[idx] if idx < len(line_index) else len(text)
        if is_blank[idx] and _in_protected(line_start, protected):
            protected_blank[idx] = True

    kept: list[str] = []
    blanks = 0
    for idx, line in enumerate(cleaned):
        if is_blank[idx] and not protected_blank[idx]:
            blanks += 1
            # collapse internal runs; edges handled below
            if len(kept) > 0:
                kept.append("")
                # mark by limiting via second pass instead: simplest correct
                # approach is post-filtering below.
            continue
        blanks = 0
        kept.append(line)

    # The loop above keeps every blank; instead rebuild with collapse.
    kept = _collapse_blanks(cleaned, is_blank, protected_blank)

    # 3) leading / trailing edge policy
    if is_first:
        while kept and kept[0] == "":
            kept.pop(0)
    else:
        # exactly one blank line separating this region from the previous
        lead = 0
        while lead < len(kept) and kept[lead] == "":
            lead += 1
        core = kept[lead:]
        kept = [""] + core if core else core

    if is_last:
        while kept and kept[-1] == "":
            kept.pop()
        if kept:
            return "\n".join(kept) + "\n"
        return ""

    if not kept:
        return ""
    return "\n".join(kept)


def _collapse_blanks(
    lines: list[str], is_blank: list[bool], protected_blank: list[bool]
) -> list[str]:
    out: list[str] = []
    run = 0
    for idx, line in enumerate(lines):
        if is_blank[idx] and not protected_blank[idx]:
            run += 1
            if run <= 1:
                out.append("")
        else:
            run = 0
            out.append(line)
    return out
