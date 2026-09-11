"""Lua literal and number format validation (used while exporting).

Goal: cells whose ``type`` is ``table`` / ``any`` hold handwritten Lua (``{...}``).
One full-width bracket, one missing comma, or a JSON ``:`` used as ``=`` makes the
exported lua fail to load, and the reported position is far away from the actual
cell. A cell whose ``type`` is ``number`` but holds something non-numeric
(``100pcs`` / ``1,000``) is **silently written as nil**, losing data without a
trace. This module validates during the export and writes the findings into the log.

Coverage (expressions/literals only, not the full Lua grammar):

- literals: number (decimal / hexadecimal / scientific), string (short / long
  ``[[..]]``), ``nil`` / ``true`` / ``false``
- table constructors ``{...}``: ``[k]=v``, ``name=v`` and positional values mixed
- operators: ``+ - * / % ^ .. == ~= < > <= >= and or not #`` (with precedence and
  associativity)
- prefix expressions: ``a.b`` / ``a[b]`` / ``f(x)`` / ``f{...}`` / ``f"x"`` / ``obj:m(x)``

``validate_number()`` additionally checks whether a ``number`` column converts to a
lua number, reusing the exporter's ``lua_writer.to_number_literal`` so the two can
never contradict each other.

Deliberately lenient: **every escape sequence is accepted** (engine-specific
escapes such as ``\\%`` must not be flagged as errors) and a raw newline inside a
short string is not reported either. ``function`` definitions are not supported
(they do not appear in configuration tables - zero hits across 75476 cells); when
one shows up it is reported explicitly instead of passing silently.
"""

import re

from .i18n import t
from .lua_writer import to_number_literal

#: Lua keywords
_KEYWORDS = frozenset((
    'and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function',
    'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return', 'then',
    'true', 'until', 'while',
))

_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_NUMBER_RE = re.compile(r'0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')
_LONG_OPEN_RE = re.compile(r'\[(=*)\[')

#: Multi-character symbols must come before single-character ones
_SYMBOLS = (
    '...', '..', '==', '~=', '<=', '>=',
    '+', '-', '*', '/', '%', '^', '#', '<', '>', '=',
    '(', ')', '{', '}', '[', ']', ';', ':', ',', '.',
)

#: Binary operator priorities ``op -> (left, right)``, from the priority table in Lua 5.1 ``lparser.c``
_BINARY_PREC = {
    'or': (1, 1),
    'and': (2, 2),
    '<': (3, 3), '>': (3, 3), '<=': (3, 3), '>=': (3, 3),
    '~=': (3, 3), '==': (3, 3),
    '..': (5, 4),                         # right associative
    '+': (6, 6), '-': (6, 6),
    '*': (7, 7), '/': (7, 7), '%': (7, 7),
    '^': (10, 9),                         # right associative
}

#: Unary operator priority (Lua's ``UNARY_PRIORITY``)
_UNARY_PRIORITY = 8


class LuaSyntaxError(Exception):
    """Syntax error; ``pos`` is the index of the offending character (0-based)."""

    def __init__(self, message, pos=None):
        super().__init__(message)
        self.message = message
        self.pos = pos


# ── Lexer ──────────────────────────────────────────────────────

def _scan_short_string(text, start):
    """Scan ``"..."`` / ``'...'``, returning (end index, properly closed).

    Escape sequences are lenient: whatever follows a ``\\`` is skipped as a pair
    (engine-specific escapes are very common).
    """
    quote = text[start]
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '\\':
            i += 2
            continue
        if ch == quote:
            return i + 1, True
        i += 1
    return n, False


def _tokenize(text):
    """Split into a list of ``(kind, value, pos)``; always ends with ``('eof', '', len)``."""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        if ch in ' \t\r\n\v\f':
            i += 1
            continue

        # comment
        if text.startswith('--', i):
            m = _LONG_OPEN_RE.match(text, i + 2)
            if m:
                close = ']' + m.group(1) + ']'
                j = text.find(close, m.end())
                if j < 0:
                    raise LuaSyntaxError(t("syn.unclosed_comment"), i)
                i = j + len(close)
            else:
                j = text.find('\n', i)
                i = n if j < 0 else j + 1
            continue

        # long string [[...]] / [=[...]=]
        m = _LONG_OPEN_RE.match(text, i)
        if m:
            close = ']' + m.group(1) + ']'
            j = text.find(close, m.end())
            if j < 0:
                raise LuaSyntaxError(t("syn.unclosed_long_string", tok=m.group(0)), i)
            end = j + len(close)
            tokens.append(('string', text[i:end], i))
            i = end
            continue

        # short string
        if ch in '"\'':
            end, ok = _scan_short_string(text, i)
            if not ok:
                raise LuaSyntaxError(t("syn.unterminated_string", ch=ch), i)
            tokens.append(('string', text[i:end], i))
            i = end
            continue

        # number (.5 counts, a lone . does not)
        if ch.isdigit() or (ch == '.' and i + 1 < n and text[i + 1].isdigit()):
            m = _NUMBER_RE.match(text, i)
            if m:
                tokens.append(('number', m.group(0), i))
                i = m.end()
                continue
            raise LuaSyntaxError(t("syn.bad_number"), i)

        # name / keyword
        m = _NAME_RE.match(text, i)
        if m:
            word = m.group(0)
            tokens.append(('keyword' if word in _KEYWORDS else 'name', word, i))
            i = m.end()
            continue

        # symbol
        for sym in _SYMBOLS:
            if text.startswith(sym, i):
                tokens.append(('symbol', sym, i))
                i += len(sym)
                break
        else:
            raise LuaSyntaxError(t("syn.bad_char", desc=_char_desc(ch)), i)

    tokens.append(('eof', '', n))
    return tokens


def _char_desc(ch):
    """Describe a character in plain words (naming full-width characters, by far the most common typo).

    The text goes through ``i18n`` so the hints follow the UI language and English
    users understand them too.
    """
    if ch == '（':
        return t("char.fullwidth_lparen")
    if ch == '）':
        return t("char.fullwidth_rparen")
    if ch == '｛':
        return t("char.fullwidth_lbrace")
    if ch == '｝':
        return t("char.fullwidth_rbrace")
    if ch == '【' or ch == '】':
        return t("char.square_bracket", ch=ch)
    if ch == '：':
        return t("char.fullwidth_colon")
    if ch in '，、':
        return t("char.fullwidth_comma", ch=ch)
    if ch == '；':
        return t("char.fullwidth_semicolon")
    if ch == '＝':
        return t("char.fullwidth_equal")
    if ch in '“”':
        return t("char.curly_double_quote", ch=ch)
    if ch in '‘’':
        return t("char.curly_single_quote", ch=ch)
    if ch == '　':
        return t("char.fullwidth_space")
    return repr(ch)


def _describe(token):
    kind, value, _ = token
    if kind == 'eof':
        return t("syn.eof")
    return repr(value)


# ── Parser ─────────────────────────────────────────────────────

class _Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    # -- basic operations --

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else self.toks[-1]

    def advance(self):
        tok = self.toks[self.i]
        if tok[0] != 'eof':
            self.i += 1
        return tok

    def at_symbol(self, *syms):
        tok = self.peek()
        return tok[0] == 'symbol' and tok[1] in syms

    def expect(self, sym):
        tok = self.peek()
        if tok[0] != 'symbol' or tok[1] != sym:
            raise LuaSyntaxError(
                t("syn.expect_symbol", sym=sym, found=_describe(tok)), tok[2])
        return self.advance()

    # -- expressions (precedence climbing, same as the official Lua parser) --

    def parse(self):
        self.expression(0)
        tok = self.peek()
        if tok[0] != 'eof':
            raise LuaSyntaxError(t("syn.trailing", found=_describe(tok)), tok[2])

    def expression(self, limit):
        tok = self.peek()
        is_unary = (tok[0] == 'keyword' and tok[1] == 'not') or \
                   (tok[0] == 'symbol' and tok[1] in ('-', '#'))
        if is_unary:
            self.advance()
            self.expression(_UNARY_PRIORITY)
        else:
            self.simple_exp()

        while True:
            tok = self.peek()
            if tok[0] not in ('symbol', 'keyword'):
                break
            prec = _BINARY_PREC.get(tok[1])
            if prec is None or prec[0] <= limit:
                break
            self.advance()
            self.expression(prec[1])

    def simple_exp(self):
        tok = self.peek()
        kind, value, pos = tok

        if kind in ('number', 'string'):
            self.advance()
            return
        if kind == 'keyword':
            if value in ('nil', 'true', 'false'):
                self.advance()
                return
            if value == 'function':
                raise LuaSyntaxError(t("syn.no_inline_function"), pos)
            raise LuaSyntaxError(t("syn.unexpected_keyword", kw=repr(value)), pos)
        if kind == 'name':
            self.advance()
            self._suffixes()
            return
        if kind == 'symbol':
            if value == '{':
                self.advance()
                self._table_body()
                return
            if value == '...':
                self.advance()
                return
            if value == '(':
                self.advance()
                self.expression(0)
                self.expect(')')
                self._suffixes()
                return

        raise LuaSyntaxError(
            t("syn.expect_value", found=_describe(tok)), pos)

    def _table_body(self):
        """``{`` has already been consumed."""
        if self.at_symbol('}'):
            self.advance()
            return
        while True:
            if self.at_symbol('['):
                self.advance()
                self.expression(0)
                self.expect(']')
                self.expect('=')
                self.expression(0)
            else:
                nxt = self.peek(1)
                if (self.peek()[0] == 'name' and nxt[0] == 'symbol' and nxt[1] == '='):
                    self.advance()
                    self.advance()
                    self.expression(0)
                else:
                    self.expression(0)

            if self.at_symbol(',', ';'):
                self.advance()
                if self.at_symbol('}'):
                    break
                continue
            break
        self.expect('}')

    def _suffixes(self):
        while True:
            if self.at_symbol('.'):
                self.advance()
                tok = self.peek()
                if tok[0] != 'name':
                    raise LuaSyntaxError(
                        t("syn.expect_field_name", found=_describe(tok)), tok[2])
                self.advance()
            elif self.at_symbol('['):
                self.advance()
                self.expression(0)
                self.expect(']')
            elif self.at_symbol(':'):
                self.advance()
                tok = self.peek()
                if tok[0] != 'name':
                    raise LuaSyntaxError(
                        t("syn.expect_method_name", found=_describe(tok)), tok[2])
                self.advance()
                self._call_args()
            elif self.at_symbol('(') or self.at_symbol('{') or self.peek()[0] == 'string':
                self._call_args()
            else:
                return

    def _call_args(self):
        tok = self.peek()
        if tok[0] == 'string':
            self.advance()
            return
        if tok[0] == 'symbol' and tok[1] == '{':
            self.advance()
            self._table_body()
            return
        if tok[0] == 'symbol' and tok[1] == '(':
            self.advance()
            if self.at_symbol(')'):
                self.advance()
                return
            self.expression(0)
            while self.at_symbol(','):
                self.advance()
                self.expression(0)
            self.expect(')')
            return
        raise LuaSyntaxError(
            t("syn.expect_args", found=_describe(tok)), tok[2])


# ── Public interface ─────────────────────────────────────────

def validate_lua_value(text):
    """Validate a Lua expression/literal. Returns ``None`` on success, else the error description."""
    try:
        _Parser(_tokenize(text)).parse()
    except LuaSyntaxError as e:
        pos = len(text) if e.pos is None else e.pos
        return t("syn.with_pos", msg=e.message, pos=pos + 1)
    except RecursionError:
        return t("syn.too_deep")
    return None


#: A literal produced from a ``number`` column should look like this (decimal / fraction / scientific, optional sign).
#: It catches the odd Python values ``to_number_literal`` may let through (``nan`` / ``inf``).
_NUM_LITERAL_RE = re.compile(r'^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$')

#: Max characters for a single error description in the dialog (``compact=True``); longer ones are truncated.
#: ``messagebox`` cannot scroll and wraps at about 3 inches (~288px), so long lines make the window very tall;
#: 48 characters is enough for hints such as "use the half-width ',' instead" and only trims the verbose ones.
_DIALOG_ERR_WIDTH = 48


def validate_number(value):
    """Check whether a ``number`` cell can be exported as a lua number.

    The decision reuses the exporter's ``to_number_literal``, so "validation passed
    but the export wrote nil" can never happen: a convertible value is accepted,
    an unconvertible one (which would silently become ``nil``, i.e. data loss) is
    reported. Typical offenders: digits with a trailing unit (``100pcs``),
    thousands separators (``1,000``), several dots (``1.2.3``), full-width digits,
    or placeholder words such as "n/a".
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None                       # Excel TRUE/FALSE, valid in lua
    text = str(value).strip()
    if not text:
        return None                       # An empty value exports as nil - an existing convention, so let it through
    try:
        literal = to_number_literal(value)
    except (ValueError, OverflowError, TypeError):
        return t("num.not_a_number")
    # to_number_literal returns 'true'/'false' for a boolean written as text - that is valid
    if literal in ('true', 'false'):
        return None
    if literal is None:
        return t("num.would_be_nil")
    if not _NUM_LITERAL_RE.match(literal):
        return t("num.not_a_number_detail", literal=literal)
    return None


def column_letter(col):
    """1-based column number -> Excel column name (1 -> ``A``, 28 -> ``AB``, 702 -> ``AAA``)."""
    letters = ''
    n = int(col)
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord('A') + rem) + letters
    return letters


def cell_ref(row, col):
    """Build the cell address as seen in Excel from the row/column numbers, e.g. ``B12``.

    Returns ``None`` when the row or column is missing (older data without a ``col``
    field must not raise, it just degrades to a bare row number in the log).
    """
    if row is None or col is None:
        return None
    try:
        return '%s%d' % (column_letter(col), int(row))
    except (TypeError, ValueError):
        return None


def format_syntax_errors(table_info, compact=False):
    """Render ``table_info['syntax_errors']`` as lines of text.

    By default (``compact=False``) the output is for the **log** and carries the
    most information: cell address (``B12``) plus row/column numbers, field name,
    error description and a preview of the cell content:

    ``Lua syntax error: book.xlsx / sheet / cell B12 (row 12, column B) / field items -> ... | content: ...``

    ``compact=True`` is for the **dialog**: it drops the category prefix and the
    content preview and truncates the description to ``_DIALOG_ERR_WIDTH``, keeping
    every line short (``messagebox`` cannot scroll, so long or numerous lines make
    the window very tall):

    ``book.xlsx / sheet / cell B12 (row 12, column B) / items -> contains a character that is not valid in Lua ...``
    """
    errors = table_info.get('syntax_errors') or []
    if not errors:
        return []
    source = table_info.get('source_file', '')
    sheet = table_info.get('sheet_name', '')
    lines = []
    for e in errors:
        row, col = e.get('row'), e.get('col')
        ref = cell_ref(row, col)

        # Cell address plus row/column numbers, included in both formats (the dialog must show them too, as requested)
        if ref:
            where = t("err.where_cell", ref=ref, row=row, col=column_letter(col))
        elif row is not None:
            where = t("err.where_row", row=row)
        else:
            where = t("err.where_unknown")

        if compact:
            err = str(e.get('error', ''))
            if len(err) > _DIALOG_ERR_WIDTH:
                err = err[:_DIALOG_ERR_WIDTH] + '…'
            loc = ' / '.join(p for p in (source, sheet) if p)
            lines.append('%s / %s / %s -> %s' % (loc, where, e.get('field'), err))
            continue

        preview = str(e.get('value', '')).replace('\r\n', ' ').replace('\n', ' ').strip()
        if len(preview) > 60:
            preview = preview[:60] + '…'
        kind = t("err.kind.number" if e.get('kind') == 'number' else "err.kind.lua")

        lines.append(t("err.log_line", kind=kind, file=source, sheet=sheet, where=where,
                       field=e.get('field'), error=e.get('error'), preview=preview))
    return lines
