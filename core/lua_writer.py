"""Lua code generation.

Mirrors the legacy exporter's output rules (all of them verified by structurally
diffing every table against the legacy artifacts found in the target directories):

- scope: ``c`` = client only, ``s`` = server only, ``sc`` / ``cs`` = both sides
- key_count == 0: every row becomes an anonymous table element ``{ ... },``
- key_count >= 1: nest level by level over the first key_count fields,
  ``[k1] = { [k2] = { ... } }``
- key columns are taken from "all valid columns" and are not affected by scope
  filtering (some tables have their key field marked ``s`` (server only), so the
  client does not export that field, yet the client file still uses it as key)
- a key used by several rows keeps only the last row (Lua table semantics), which
  is silent data loss - ``duplicate_key_errors`` reports it so the export aborts
- an empty cell (None) => the field is omitted entirely; an empty string => an
  empty value is written (``nil`` for ``number`` / ``any``, ``{}`` for ``table``)
- a field name must be a valid Lua identifier, otherwise the whole column is
  dropped (the right-hand side of a sheet often carries junk columns such as a
  duplicated ``id``, symbol-only columns, or orphan ``{`` / ``,`` / ``}`` tokens,
  and the legacy tool drops them as well)
- two tab characters separate the file header from the comment
"""

import math
import re

from .i18n import t

#: Scope values meaning "export to both sides" (the two spellings are synonyms)
SCOPE_BOTH = ("sc", "cs")

_FIELD_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_NUMERIC_RE = re.compile(r'^-?\d')

#: How many row numbers a duplicate-key message spells out before it elides the rest
_MAX_ROWS_SHOWN = 8


def valid_field_name(name):
    """A field name must be a valid lua identifier.

    Sheets often keep a block of junk on the right-hand side (duplicated ``id``
    columns, symbol-only columns, or even orphan ``{`` / ``,`` / ``}`` tokens that
    split the object syntax apart); the legacy tool dropped every one of them.
    Validating identifiers rejects the same set and also guarantees that the lua
    we write is syntactically valid.
    """
    return bool(name) and _FIELD_NAME_RE.match(name) is not None


def _crlf(s):
    """In-cell line breaks are normalised to CRLF (the legacy tool's output is CRLF as well)."""
    return s.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n')


def _is_blank_str(value):
    return isinstance(value, str) and value.strip() == ""


def _format_string(value):
    """Write text as a lua long-string literal ``[[...]]``.

    The content of a long bracket is **not escaped**, so a ``]`` inside it gets in
    the way of the closing ``]]``:

    - content containing ``]]`` closes early (``[[a]]b]]`` only yields ``a``)
    - content ending with ``]`` merges with the closing ``]]`` into ``]]]``
      (``[[c]]]`` fails to parse)

    The literal therefore raises its ``=`` level as needed (``[[`` -> ``[=[`` ->
    ``[==[`` ...) until the delimiters cannot collide with any ``]`` in the
    content. The level is only raised when the content contains ``]``, so plain
    text stays byte-identical to the legacy tool's output.
    """
    if value is None:
        return '[[]]'
    s = _crlf(str(value))
    level = 0
    while ']' + '=' * level + ']' in s or s.endswith(']' + '=' * level):
        level += 1
    pad = '=' * level
    return f'[{pad}[{s}]{pad}]'


def _float_literal(value):
    # inf / nan have no literal in Lua and cannot be converted (int() would raise OverflowError/ValueError)
    if not math.isfinite(value):
        return None
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    # %.15g swallows the binary-mantissa noise Excel and float math leave behind (969000.0000000001 -> 969000)
    s = '%.15g' % value
    if 'e' not in s and 'E' not in s and '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s


def to_number_literal(value):
    """Convert to a lua numeric literal; returns None when it cannot be parsed.

    Exposed for the syntax checker (``lua_syntax.validate_number``) - validation
    must use **the same rule** as the exporter, otherwise we end up reporting
    "validation passed" while the export silently wrote nil.
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
    # The legacy tool writes nil for a cell that is number-typed but holds an empty string
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
    """Format a key field as a lua literal.

    - ``number``: written as a number directly (falls back to 0 when it cannot be
      parsed, so an invalid ``[nil]`` is never emitted)
    - ``string``: wrapped in double quotes
    - anything else (e.g. a source table really using ``{{5,1}}`` as a key): passed
      through as-is
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
    """Column indexes actually written by this export (already filtered by valid field name and scope)."""
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
    """Group level by level over the keys; a later row with the same key wins (Lua table semantics)."""
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


def _row_list(rows, limit=_MAX_ROWS_SHOWN):
    """Row numbers as ``9, 10, 11`` - elided with a trailing ellipsis when there are many."""
    shown = ", ".join(str(r) for r in rows[:limit])
    if len(rows) > limit:
        shown += ", …"
    return shown


def duplicate_key_errors(table_info, first_row):
    """Keys used by more than one row - each one drops the earlier rows without a word.

    :func:`_build_nested` keeps the rows in a dict, so a row whose key is already
    taken replaces the earlier one: the earlier row's data never reaches the output
    while the export still reports success. On the real tables that is not
    hypothetical - ``Z-装备注灵 / 升级`` writes 26 rows under ``[13][15]`` and only
    the last of them survives.

    The comparison is on the key **as it is written** (:func:`_format_key`), because
    that literal is what actually collides in the generated Lua. Two rows whose key
    cell is empty are duplicates for the same reason (both become ``0`` or ``""``),
    and so are ``1`` and ``1.0`` in a ``number`` column.

    Returns one entry per duplicated key, shaped like the ``syntax_errors`` the cell
    validator produces (see ``excel_reader._collect_base_syntax_errors``) so that
    the pre-flight can report both through a single path:

    - ``row`` / ``col``: the first row that would be lost and the key column
    - ``field`` / ``type``: the first key field
    - ``kind``: ``"key"``
    - ``value``: the key as written, e.g. ``[1]`` (one key) or ``[2][3]`` (nested)
    - ``error``: text naming every row that collides

    ``first_row`` is the Excel row number of ``data_rows[0]`` (9 for a base sheet).
    The writer only knows row indexes, so the caller owns the numbering.
    """
    key_count = table_info.get("key_count", 0)
    if key_count <= 0:
        return []

    field_names = table_info.get("field_names", [])
    types = table_info.get("types", [])
    data_rows = table_info.get("data_rows", [])

    # Same key columns as _build_base_lua: the first key_count columns with a valid field name
    key_indices = _valid_indices(field_names)[:key_count]
    if not key_indices:
        return []

    # key literal ("[1][2]") -> the row indexes carrying it, in file order
    used = {}
    for i, row in enumerate(data_rows):
        parts = []
        for idx in key_indices:
            type_str = types[idx] if idx < len(types) else "number"
            value = row[idx] if idx < len(row) else None
            parts.append(_format_key(value, type_str))
        used.setdefault("[" + "][".join(parts) + "]", []).append(i)

    errors = []
    for key, indexes in used.items():
        if len(indexes) < 2:
            continue
        # A later row wins (dict assignment), so everything before the last one is lost
        dropped = [first_row + i for i in indexes[:-1]]
        key_col = key_indices[0]
        errors.append({
            "row": dropped[0],
            "col": key_col + 1,
            "field": field_names[key_col],
            "type": types[key_col] if key_col < len(types) else "",
            "kind": "key",
            "value": key,
            # "key_literal" rather than "key": t(key, **kw) already has a parameter
            # called key, so passing key= would raise TypeError
            "error": t("key.duplicate", n=len(indexes), key_literal=key,
                       kept=first_row + indexes[-1], rows=_row_list(dropped)),
        })
    return errors


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
