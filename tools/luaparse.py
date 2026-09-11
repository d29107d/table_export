"""极简 Lua table 解析器：把导出的 lua 文件解析成 python 嵌套结构，用于顺序无关的结构化对比。"""

import re

#: 字段名可能是中文 / 符号（导出的 lua 里确实存在 ``地图 = ...`` ``★ = ...``），
#: 所以 key 不能只按 lua 标识符匹配：取"到 '=' 为止、且不含空白与括号引号"的一段。
_KEY_RE = re.compile(r'([^={}\[\]"\'\s]+?)[ \t]*=(?!=)')


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


def _read_long_string(text, i):
    # i points at '['
    if text.startswith('[[', i):
        j = text.find(']]', i + 2)
        if j < 0:
            raise LuaParseError('unterminated [[ string')
        return text[i + 2:j], j + 2
    if text.startswith('[=[', i):
        j = text.find(']=]', i + 3)
        if j < 0:
            raise LuaParseError('unterminated [=[ string')
        return text[i + 3:j], j + 3
    raise LuaParseError('bad long string at %d' % i)


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
    return tok


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
    """判断 i 处是否是 ``key =`` 形式，是则返回 (key, 值起始位置)，否则 (None, i)。"""
    n = len(text)
    if text.startswith('[[', i) or text.startswith('[=[', i):
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
    """同一个 key 在字面量里出现多次（运行时后者覆盖前者）；对比时按"多重集"比较。"""

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
    i += len('return')
    return _parse_value(text, i)[0]
