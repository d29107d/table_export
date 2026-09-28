"""Minimal Lua parser: turns Lua text into plain Python structures.

Two callers, one grammar:

- ``tools/compare_export.py`` parses a whole generated lua file so that two exports
  can be compared structurally, ignoring ordering
- ``core/json_writer.py`` parses the hand-written Lua sitting in a ``table`` / ``any``
  cell (``{10000, "Revive Envoy", 1503}``) so the JSON export can carry it as native
  JSON instead of a string of Lua source

Anything that is not a plain literal - an expression such as ``100+50``, an unbalanced
brace, a function call - is deliberately *not* an error: ``_coerce`` hands the raw text
back as a string, and :func:`parse_value` reports trailing content so the caller can
fall back to the source text rather than guessing at a meaning.
"""

import re

#: Field names may be non-ASCII or symbol-like (the generated lua really does contain
#: such keys), so a key is not matched as a lua identifier: take the run up to '='
#: that contains no whitespace, brackets or quotes.
_KEY_RE = re.compile(r'([^={}\[\]"\'\s]+?)[ \t]*=(?!=)')

#: Opening of a long bracket: ``[[`` / ``[=[`` / ``[==[`` ... with an unlimited number of ``=``
_LONG_OPEN_RE = re.compile(r'\[(=*)\[')


class LuaParseError(Exception):
    pass


def strip_bom(t):
    if t and t[0] == '\ufeff':
        return t[1:]
    return t


def _skip_ws(text, i):
    n = len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
        elif text.startswith('--[[', i):
            j = text.find(']]', i + 4)
            if j < 0:
                raise LuaParseError('unterminated comment')
            i = j + 2
        elif text.startswith('--', i):
            j = text.find('\n', i)
            i = n if j < 0 else j + 1
        else:
            break
    return i


def _long_open_pad(text, i):
    """If ``text[i]`` starts a long bracket, return its ``=`` level (as a string), else None.

    Lua long brackets have unlimited levels: ``[[`` / ``[=[`` / ``[==[`` ..., and the
    closing one must use the same level. The writer (``core/lua_writer._format_string``)
    raises the level automatically when the content contains ``]``, so recognising
    only ``[[`` and ``[=[`` here would be wrong.
    """
    m = _LONG_OPEN_RE.match(text, i)
    return m.group(1) if m else None


def _read_long_string(text, i):
    # i points at '['
    pad = _long_open_pad(text, i)
    if pad is None:
        raise LuaParseError('bad long string at %d' % i)
    opener = '[' + pad + '['
    closer = ']' + pad + ']'
    start = i + len(opener)
    j = text.find(closer, start)
    if j < 0:
        raise LuaParseError('unterminated %s string' % opener)
    return text[start:j], j + len(closer)


def _read_quoted(text, i):
    q = text[i]
    i += 1
    out = []
    n = len(text)
    while i < n:
        c = text[i]
        if c == '\\' and i + 1 < n:
            nx = text[i + 1]
            mapping = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', "'": "'", '\\': '\\'}
            out.append(mapping.get(nx, nx))
            i += 2
            continue
        if c == q:
            return ''.join(out), i + 1
        out.append(c)
        i += 1
    raise LuaParseError('unterminated quoted string')


def _read_token(text, i):
    n = len(text)
    start = i
    while i < n and text[i] not in ',}]':
        i += 1
    return text[start:i].strip(), i


class Raw(str):
    """A token ``_coerce`` could not reduce to a number, a boolean or nil, kept as text.

    A ``str`` subclass rather than a plain ``str`` so a caller can tell the two apart:
    ``"abc"`` in the source really is the string ``abc``, while a bare ``abc`` is a
    token that only *looks* like one (in Lua it would be a variable reference), and
    ``100+50`` is an expression. Comparison still behaves like ``str``, so structural
    comparison in ``tools/compare_export.py`` is unaffected.
    """


def _coerce(tok):
    if tok == 'nil':
        return None
    if tok == 'true':
        return True
    if tok == 'false':
        return False
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return Raw(tok)


def _parse_value(text, i):
    i = _skip_ws(text, i)
    if i >= len(text):
        raise LuaParseError('eof')
    c = text[i]
    if c == '{':
        return _parse_block(text, i)
    if c == '[':
        return _read_long_string(text, i)
    if c in '"\'':
        return _read_quoted(text, i)
    tok, i = _read_token(text, i)
    return _coerce(tok), i


def _try_key(text, i):
    """Return (key, value start) when position i is a ``key =`` pair, else (None, i)."""
    n = len(text)
    if _long_open_pad(text, i) is not None:
        k, j = _read_long_string(text, i)
        j = _skip_ws(text, j)
        if j < n and text[j] == ']':
            k2 = _skip_ws(text, j + 1)
            if text.startswith('=', k2) and not text.startswith('==', k2):
                return k, k2 + 1
        return None, i

    if text[i] == '[':
        k, j = _parse_value(text, i + 1)
        j = _skip_ws(text, j)
        if j < n and text[j] == ']':
            k2 = _skip_ws(text, j + 1)
            if text.startswith('=', k2) and not text.startswith('==', k2):
                return k, k2 + 1
        return None, i

    m = _KEY_RE.match(text, i)
    if m:
        return m.group(1), m.end()
    return None, i


def _parse_block(text, i):
    """i points at '{'"""
    i += 1
    items = []
    n = len(text)
    while True:
        i = _skip_ws(text, i)
        if i >= n:
            raise LuaParseError('eof in block')
        c = text[i]
        if c == '}':
            return _finalize(items), i + 1

        prev_i = i
        key, i = _try_key(text, i)
        if key is None:
            if c == ',':
                i += 1
                continue
            val, i = _parse_value(text, i)
        else:
            val, i = _parse_value(text, i)
        items.append((key, val))
        if i <= prev_i:
            raise LuaParseError('no progress at %d: %r' % (prev_i, text[prev_i:prev_i + 40]))


class Dup(list):
    """The same key occurs several times in the literal (at runtime the last one wins); compare as a multiset."""

    def __eq__(self, other):
        if not isinstance(other, Dup) or len(self) != len(other):
            return False
        return sorted(map(repr, self)) == sorted(map(repr, other))

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(tuple(sorted(map(repr, self))))


def _hkey(k):
    if isinstance(k, (int, float, str, bool, type(None))):
        return k
    return '<%r>' % (k,)


def _finalize(items):
    if not items:
        return {}
    if all(k is None for k, _ in items):
        return [v for _, v in items]
    if all(k is not None for k, _ in items):
        out = {}
        for k, v in items:
            hk = _hkey(k)
            if hk in out:
                if not isinstance(out[hk], Dup):
                    out[hk] = Dup([out[hk]])
                out[hk].append(v)
            else:
                out[hk] = v
        return out
    raise LuaParseError('mixed keyed/bare entries')


def parse_lua(text):
    text = strip_bom(text)
    i = text.find('return')
    if i < 0:
        raise LuaParseError('no return')
    value, _ = _parse_value(text, i + len('return'))
    if isinstance(value, Raw):
        # A custom file header/footer wraps the table in a local alias
        # (``local cfg = { ... } return cfg``), so ``return`` names a variable rather
        # than carrying the table. Follow the alias; without this a whole class of
        # tables parsed into the bare word ``cfg`` and every comparison against them
        # came out meaninglessly unequal.
        value = _parse_alias(text, value, before=i)
    return value


def _parse_alias(text, name, before):
    """Resolve ``local <name> = <value>`` for a ``return <name>`` at ``before``."""
    match = re.search(r'\blocal\s+' + re.escape(str(name)) + r'\s*=\s*', text[:before])
    if match is None:
        raise LuaParseError('`return %s` with no `local %s =` in front of it' % (name, name))
    return _parse_value(text, match.end())[0]


def parse_value(text):
    """Parse ``text`` as exactly one Lua value and return the Python equivalent.

    Used by the JSON export on a hand-written cell. The whole text must be consumed:
    ``{1, 2} + extra`` is rejected rather than silently read as ``{1, 2}``, because the
    caller's fallback - keep the cell's source text as a string - is only safe while
    "parsed" really means "this cell is nothing but a literal".

    Expression-shaped input does **not** raise: ``100+50`` parses into the string
    ``"100+50"`` (see ``_coerce``), which is the same fallback the caller would have
    chosen anyway. Raises :class:`LuaParseError` for unbalanced or truncated input.
    """
    text = strip_bom(text).strip()
    if not text:
        raise LuaParseError('empty')
    value, i = _parse_value(text, 0)
    rest = text[i:].strip()
    if rest:
        raise LuaParseError('trailing content after the value: %r' % rest[:20])
    return value
