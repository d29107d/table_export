"""Lua 数据字面量 & 数字格式校验（给导表用）。

目的：配置表里 ``type`` 是 ``table`` / ``any`` 的单元格要手写 Lua（``{...}``），
填错一个全角括号、漏个逗号、把 JSON 的 ``:`` 当成 ``=`` 用，导出来的 lua 就会
加载失败、而且报错位置离现场很远；``type`` 是 ``number`` 的单元格填了非数字
（``100个`` / ``1,000``）则会被**静默写成 nil**，数据悄悄丢掉。这里在导出时先校验一遍，
把问题直接写进日志。

覆盖范围（只做"表达式/字面量"这一层，不做完整 Lua 语法）：

- 字面量：number（十进制 / 十六进制 / 科学计数）、string（短串 / 长串 ``[[..]]``）、
  ``nil`` / ``true`` / ``false``
- 表格构造式 ``{...}``：``[k]=v``、``name=v``、位置值三种写法混用
- 运算符：``+ - * / % ^ .. == ~= < > <= >= and or not #``（含优先级与结合性）
- 前缀表达式：``a.b`` / ``a[b]`` / ``f(x)`` / ``f{...}`` / ``f"x"`` / ``obj:m(x)``

另外 ``validate_number()`` 校验 ``number`` 列能否转成 lua 数字，判定复用导出用的
``lua_writer.to_number_literal``（同一套规则，不会自相矛盾）。

刻意宽松的地方：**转义序列一律接受**（``\\%``、``\\墓`` 这类引擎自定义转义不能判错），
短字符串里出现裸换行也不报错。``function`` 定义不支持（配置里不会出现，
全量源表 75476 个单元格零命中），遇到会明确报出来而不是静默通过。
"""

import re

from .lua_writer import to_number_literal

#: Lua 关键字
_KEYWORDS = frozenset((
    'and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function',
    'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return', 'then',
    'true', 'until', 'while',
))

_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_NUMBER_RE = re.compile(r'0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')
_LONG_OPEN_RE = re.compile(r'\[(=*)\[')

#: 多字符符号要排在单字符前面
_SYMBOLS = (
    '...', '..', '==', '~=', '<=', '>=',
    '+', '-', '*', '/', '%', '^', '#', '<', '>', '=',
    '(', ')', '{', '}', '[', ']', ';', ':', ',', '.',
)

#: 二元运算符优先级 ``op -> (left, right)``，取自 Lua 5.1 ``lparser.c`` 的 priority 表
_BINARY_PREC = {
    'or': (1, 1),
    'and': (2, 2),
    '<': (3, 3), '>': (3, 3), '<=': (3, 3), '>=': (3, 3),
    '~=': (3, 3), '==': (3, 3),
    '..': (5, 4),                         # 右结合
    '+': (6, 6), '-': (6, 6),
    '*': (7, 7), '/': (7, 7), '%': (7, 7),
    '^': (10, 9),                         # 右结合
}

#: 一元运算符优先级（Lua 的 ``UNARY_PRIORITY``）
_UNARY_PRIORITY = 8


class LuaSyntaxError(Exception):
    """语法错误；``pos`` 是出错的字符下标（从 0 开始）。"""

    def __init__(self, message, pos=None):
        super().__init__(message)
        self.message = message
        self.pos = pos


# ── 词法 ─────────────────────────────────────────────────────────

def _scan_short_string(text, start):
    """扫描 ``"..."`` / ``'...'``，返回 (结束下标, 是否正常闭合)。

    转义序列宽松处理：``\\`` 后面无论是谁都跳过两个字符（引擎自定义转义很常见）。
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
    """切成 ``(kind, value, pos)`` 列表，末尾必有 ``('eof', '', len)``。"""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        if ch in ' \t\r\n\v\f':
            i += 1
            continue

        # 注释
        if text.startswith('--', i):
            m = _LONG_OPEN_RE.match(text, i + 2)
            if m:
                close = ']' + m.group(1) + ']'
                j = text.find(close, m.end())
                if j < 0:
                    raise LuaSyntaxError('注释块没有闭合', i)
                i = j + len(close)
            else:
                j = text.find('\n', i)
                i = n if j < 0 else j + 1
            continue

        # 长字符串 [[...]] / [=[...]=]
        m = _LONG_OPEN_RE.match(text, i)
        if m:
            close = ']' + m.group(1) + ']'
            j = text.find(close, m.end())
            if j < 0:
                raise LuaSyntaxError('长字符串 %s 没有闭合' % m.group(0), i)
            end = j + len(close)
            tokens.append(('string', text[i:end], i))
            i = end
            continue

        # 短字符串
        if ch in '"\'':
            end, ok = _scan_short_string(text, i)
            if not ok:
                raise LuaSyntaxError('字符串缺少结尾的 %s' % ch, i)
            tokens.append(('string', text[i:end], i))
            i = end
            continue

        # 数字（.5 也算，但单独的 . 不算）
        if ch.isdigit() or (ch == '.' and i + 1 < n and text[i + 1].isdigit()):
            m = _NUMBER_RE.match(text, i)
            if m:
                tokens.append(('number', m.group(0), i))
                i = m.end()
                continue
            raise LuaSyntaxError('数字写得不对', i)

        # 名字 / 关键字
        m = _NAME_RE.match(text, i)
        if m:
            word = m.group(0)
            tokens.append(('keyword' if word in _KEYWORDS else 'name', word, i))
            i = m.end()
            continue

        # 符号
        for sym in _SYMBOLS:
            if text.startswith(sym, i):
                tokens.append(('symbol', sym, i))
                i += len(sym)
                break
        else:
            raise LuaSyntaxError('出现了 Lua 里不合法的字符 %s' % _char_desc(ch), i)

    tokens.append(('eof', '', n))
    return tokens


def _char_desc(ch):
    """把字符描述成人话（全角字符点出来，这几乎是填表最常见的手误）。"""
    if ch == '（':
        return "'（'（全角左括号，应该用半角 '('）"
    if ch == '）':
        return "'）'（全角右括号，应该用半角 ')'）"
    if ch == '｛':
        return "'｛'（全角左花括号，应该用半角 '{'）"
    if ch == '｝':
        return "'｝'（全角右花括号，应该用半角 '}'）"
    if ch == '【' or ch == '】':
        return "'%s'（方括号不对，Lua 的表格要用 { }）" % ch
    if ch == '：':
        return "'：'（全角冒号，应该用半角 ':'）"
    if ch in '，、':
        return "'%s'（全角逗号，应该用半角 ','）" % ch
    if ch == '；':
        return "'；'（全角分号，应该用半角 ';'）"
    if ch == '＝':
        return "'＝'（全角等号，应该用半角 '='）"
    if ch in '“”':
        return "'%s'（中文引号，应该用半角双引号 '\"'）" % ch
    if ch in '‘’':
        return "'%s'（中文引号，应该用半角单引号 \"'\"）" % ch
    if ch == '　':
        return '全角空格'
    return repr(ch)


def _describe(token):
    kind, value, _ = token
    if kind == 'eof':
        return '内容结束'
    return repr(value)


# ── 语法 ─────────────────────────────────────────────────────────

class _Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    # -- 基础操作 --

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
                "这里应该是 '%s'，实际是 %s" % (sym, _describe(tok)), tok[2])
        return self.advance()

    # -- 表达式（优先级爬升，与 Lua 官方解析器一致）--

    def parse(self):
        self.expression(0)
        tok = self.peek()
        if tok[0] != 'eof':
            raise LuaSyntaxError('表达式后面还有多余的内容 %s' % _describe(tok), tok[2])

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
                raise LuaSyntaxError('配置单元格里不支持内联 function 定义', pos)
            raise LuaSyntaxError('这里不该出现关键字 %r' % value, pos)
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
            '这里应该是一个值（数字 / 字符串 / 表格 {...} / 变量），实际是 %s'
            % _describe(tok), pos)

    def _table_body(self):
        """``{`` 已消费。"""
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
                        "'.' 后面应该是字段名，实际是 %s" % _describe(tok), tok[2])
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
                        "':' 后面应该是方法名，实际是 %s" % _describe(tok), tok[2])
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
            '函数调用后面应该是参数列表，实际是 %s' % _describe(tok), tok[2])


# ── 对外接口 ─────────────────────────────────────────────────────

def validate_lua_value(text):
    """校验一段 Lua 表达式/字面量。通过返回 ``None``，否则返回错误描述字符串。"""
    try:
        _Parser(_tokenize(text)).parse()
    except LuaSyntaxError as e:
        pos = len(text) if e.pos is None else e.pos
        return '%s（位置 %d）' % (e.message, pos + 1)
    except RecursionError:
        return '嵌套层级过深，无法解析'
    return None


#: ``number`` 列转出来的字面量应当长这样（十进制 / 小数 / 科学计数，可带负号）。
#: 用来兜住 ``to_number_literal`` 可能放过的 Python 怪东西（``nan`` / ``inf``）。
_NUM_LITERAL_RE = re.compile(r'^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$')

#: 弹窗（``compact=True``）里单条错误描述的字符上限，超了截断加省略号。
#: ``messagebox`` 不能滚动、只按约 3 英寸（≈288px）折行，行太长会把窗口撑得很高；
#: 48 字符够放下"全角逗号，应该用半角 ','"这类关键提示，只裁剪异常冗长的报错。
_DIALOG_ERR_WIDTH = 48


def validate_number(value):
    """校验 ``number`` 类型的单元格能不能导出成 lua 数字。

    判定直接复用导出用的 ``to_number_literal``，所以**不会出现"校验通过、导出却是 nil"**：
    转得出来就放行，转不出来（会被静默写成 ``nil``，等于数据丢了）就报错。
    典型该报的填法：``100个`` / ``1,000`` / ``1.2.3`` / 全角 ``１２３`` / ``暂无``。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None                       # Excel 里的 TRUE/FALSE，lua 里合法
    text = str(value).strip()
    if not text:
        return None                       # 空值导出成 nil，是既有约定，放行
    try:
        literal = to_number_literal(value)
    except (ValueError, OverflowError, TypeError):
        return '不是合法数字（无法转换成 lua 数字）'
    # to_number_literal 对"填了 true/false 文本"的布尔值返回 'true'/'false'，那是合法的
    if literal in ('true', 'false'):
        return None
    if literal is None:
        return '不是合法数字，导出后会变成 nil（数据丢失）'
    if not _NUM_LITERAL_RE.match(literal):
        return '不是合法数字（%s）' % literal
    return None


def column_letter(col):
    """1 起算的列号 -> Excel 列名（1 → ``A``，28 → ``AB``，702 → ``AAA``）。"""
    letters = ''
    n = int(col)
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord('A') + rem) + letters
    return letters


def cell_ref(row, col):
    """把行号/列号拼成 Excel 里看到的单元格地址，如 ``B12``。

    行或列缺失时返回 ``None``（老数据没有 ``col`` 字段时不至于报错，
    日志里退化成只写行号）。
    """
    if row is None or col is None:
        return None
    try:
        return '%s%d' % (column_letter(col), int(row))
    except (TypeError, ValueError):
        return None


def format_syntax_errors(table_info, compact=False):
    """把 ``table_info['syntax_errors']`` 渲染成一行行文本。

    默认（``compact=False``）给**日志**用，信息最全，带单元格地址（``B12``）与行列号、
    字段名、错误描述和单元格内容预览：

    ``Lua 语法错误: 某表.xlsx / 某页 / 单元格 B12（第 12 行 B 列） / 字段 items -> … ｜ 内容: …``

    ``compact=True`` 给**弹窗**用：去掉错误分类前缀和单元格内容预览，错误描述截断到
    ``_DIALOG_ERR_WIDTH``，控制单行宽度（``messagebox`` 不能滚动，行太长/太多会把窗口撑高）：

    ``某表.xlsx / 某页 / 单元格 B12（第 12 行 B 列） / items -> 出现了 Lua 里不合法的字符 '，'…``
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

        # 单元格地址 + 行列号，两种档位都带（用户明确要求弹窗里也要有"第 X 行 Y 列"）
        if ref:
            where = '单元格 %s（第 %s 行 %s 列）' % (ref, row, column_letter(col))
        elif row is not None:
            where = '第 %s 行' % row
        else:
            where = '位置未知'

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
        kind = '数字格式错误' if e.get('kind') == 'number' else 'Lua 语法错误'

        lines.append(
            '%s: %s / %s / %s / 字段 %s -> %s ｜ 内容: %s'
            % (kind, source, sheet, where, e.get('field'), e.get('error'), preview))
    return lines
