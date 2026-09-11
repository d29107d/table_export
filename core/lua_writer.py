"""Lua 代码生成。

对齐旧导表工具的输出规则（均经过与目标目录旧产物逐表结构化对拍验证）：

- scope：``c`` = 仅客户端，``s`` = 仅服务端，``sc`` / ``cs`` = 前后端都要
- key_count == 0：每行输出为一个匿名 table 元素 ``{ ... },``
- key_count >= 1：按前 key_count 个字段逐层嵌套 ``[k1] = { [k2] = { ... } }``
- key 列从"全部合法列"里取，不受 scope 过滤影响
  （存在这样的表：key 字段自身是 ``s``（仅服务端），客户端不导出该字段，
  但客户端文件里的 key 仍然用它）
- 单元格为空（None）=> 该字段整体不输出；单元格是空字符串 => 按空值输出
  （``number`` / ``any`` 写 ``nil``，``table`` 写 ``{}``）
- 字段名必须是合法 lua 标识符，否则整列丢弃（表右侧常残留
  ``{`` / ``,`` / ``★`` / ``地图`` 这类脏列，旧工具不导出它们）
- 文件头与注释之间用两个制表符分隔
"""

import math
import re

#: 表示"前后端都要导出"的 scope 值（两种写法同义）
SCOPE_BOTH = ("sc", "cs")

_FIELD_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_NUMERIC_RE = re.compile(r'^-?\d')


def valid_field_name(name):
    """字段名必须是合法 lua 标识符。

    表右侧常残留脏数据块（重复的 ``id``、``★`` 之类的符号列，甚至 ``{`` / ``,`` / ``}``
    这种把对象语法拆散的孤立符号），旧工具把这些列全部丢弃。
    用标识符校验把它们一并挡掉，也保证写出来的 lua 不会语法错误。
    """
    return bool(name) and _FIELD_NAME_RE.match(name) is not None


def _crlf(s):
    """单元格里的换行统一成 CRLF（旧工具产物即为 CRLF）。"""
    return s.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n')


def _is_blank_str(value):
    return isinstance(value, str) and value.strip() == ""


def _format_string(value):
    if value is None:
        return '[[]]'
    s = _crlf(str(value))
    if ']]' in s:
        return f'[=[{s}]=]'
    return f'[[{s}]]'


def _float_literal(value):
    # inf / nan 在 Lua 里没有字面量，转不了（否则 int() 会抛 OverflowError/ValueError）
    if not math.isfinite(value):
        return None
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    # %.15g 可以吃掉 Excel/浮点运算带来的二进制尾数噪声（如 969000.0000000001 -> 969000）
    s = '%.15g' % value
    if 'e' not in s and 'E' not in s and '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s


def to_number_literal(value):
    """转成 lua 数字字面量；无法解析返回 None。

    公开出来是给语法校验用的（``lua_syntax.validate_number``）——
    校验必须和导出用**同一套判定**，否则会出现"校验说没问题、导出却是 nil"的矛盾。
    """
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _float_literal(value)
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return str(int(s))
    except ValueError:
        pass
    try:
        return _float_literal(float(s))
    except (ValueError, OverflowError):
        return None


def _format_number(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    n = to_number_literal(value)
    # 旧工具对"填了空字符串但类型是 number"的单元格写 nil
    return n if n is not None else 'nil'


def _format_value(value, type_str):
    if type_str == "number":
        return _format_number(value)

    if type_str == "string":
        return _format_string(value)

    if type_str == "table":
        if value is None or _is_blank_str(value):
            return "{}"
        return _crlf(str(value))

    if type_str == "any":
        if value is None or _is_blank_str(value):
            return "nil"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return _format_number(value)
        return _crlf(str(value))

    return _format_string(value)


def _format_key(value, type_str):
    """把 key 字段格式化成 lua 字面量。

    - ``number``：直接写数字（解析不出来时退化成 0，避免写出非法的 ``[nil]``）
    - ``string``：加双引号
    - 其它（例如源表里确实拿 ``{{5,1}}`` 这种 table 当 key）：原样输出
    """
    if type_str == "number":
        n = to_number_literal(value)
        if n is None or not _NUMERIC_RE.match(n):
            return '0'
        return n
    if value is None:
        return '""'
    if type_str == "string":
        s = str(value)
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'
    return str(value)


def _valid_indices(field_names):
    return [i for i, fname in enumerate(field_names) if valid_field_name(fname)]


def _scope_filtered(indices, scopes, scope_filter):
    out = []
    for i in indices:
        scope = scopes[i] if i < len(scopes) else "c"
        if scope_filter == "c" and scope != "c" and scope not in SCOPE_BOTH:
            continue
        if scope_filter == "s" and scope != "s" and scope not in SCOPE_BOTH:
            continue
        out.append(i)
    return out


def _get_filtered_indices(scopes, field_names, scope_filter):
    """本次导出真正要写出的列下标（已按字段名合法性与 scope 过滤）。"""
    return _scope_filtered(_valid_indices(field_names), scopes, scope_filter)


def _emit_fields(lines, row, indices, types, field_names, depth):
    pad = '\t' * depth
    for idx in indices:
        t = types[idx] if idx < len(types) else "string"
        v = row[idx] if idx < len(row) else None
        if v is None:
            continue
        lines.append(f'{pad}{field_names[idx]} = {_format_value(v, t)},')


def _build_nested(rows, key_indices, types):
    """按 key 逐层归组；同 key 后出现的行覆盖先出现的行（Lua table 语义）。"""
    data = {}
    for row in rows:
        parts = []
        for idx in key_indices:
            t = types[idx] if idx < len(types) else "number"
            v = row[idx] if idx < len(row) else None
            parts.append(_format_key(v, t))
        node = data
        for p in parts[:-1]:
            nxt = node.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                node[p] = nxt
            node = nxt
        node[parts[-1]] = row
    return data


def _build_base_lua(table_info, scope_filter):
    header = table_info.get("file_header", "")
    footer = table_info.get("file_footer", "")
    key_count = table_info.get("key_count", 0)
    scopes = table_info.get("scopes", [])
    types = table_info.get("types", [])
    field_names = table_info.get("field_names", [])
    data_rows = table_info.get("data_rows", [])

    source_file = table_info.get("source_file", "")
    sheet_name = table_info.get("sheet_name", "")
    comment = f'--[[ {source_file} -> {sheet_name} ]]'

    all_indices = _valid_indices(field_names)
    indices = _scope_filtered(all_indices, scopes, scope_filter)
    if not indices:
        return None

    lines = [f'{header}\t\t{comment}' if header else f'return {{\t\t{comment}']

    key_count = min(key_count, len(all_indices))

    if key_count == 0:
        for row in data_rows:
            lines.append('{')
            _emit_fields(lines, row, indices, types, field_names, 1)
            lines.append('},')
    else:
        key_indices = all_indices[:key_count]
        data = _build_nested(data_rows, key_indices, types)

        def emit(node, depth):
            pad = '\t' * depth
            for k, v in node.items():
                lines.append(f'{pad}[{k}] = {{')
                if isinstance(v, dict):
                    emit(v, depth + 1)
                else:
                    _emit_fields(lines, v, indices, types, field_names, depth + 1)
                lines.append(f'{pad}}},')

        emit(data, 0)

    lines.append(footer if footer else "")
    return "\n".join(lines)


def _build_tiny_lua(table_info, scope_filter):
    header = table_info.get("file_header", "")
    footer = table_info.get("file_footer", "")
    fields = table_info.get("fields", [])

    source_file = table_info.get("source_file", "")
    sheet_name = table_info.get("sheet_name", "")
    comment = f'--[[ {source_file} -> {sheet_name} ]]'

    filtered = [
        f for f in fields
        if valid_field_name(f["field_name"])
        and (f["scope"] == scope_filter or f["scope"] in SCOPE_BOTH)
    ]
    if not filtered:
        return None

    lines = [f'{header}\t\t{comment}' if header else f'return {{\t\t{comment}']

    for f in filtered:
        v = f["value"]
        if v is None:
            continue
        lines.append(f'\t{f["field_name"]} = {_format_value(v, f["type"])},')

    lines.append(footer if footer else "")
    return "\n".join(lines)


def generate_lua(table_info, scope_filter):
    export_type = table_info.get("export_type", "base")
    if export_type == "base":
        return _build_base_lua(table_info, scope_filter)
    elif export_type == "tiny":
        return _build_tiny_lua(table_info, scope_filter)
    else:
        return None
