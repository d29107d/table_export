"""JSON code generation - the same tables, spelled for a non-Lua reader.

Which columns and rows are exported is **not** decided here: the helpers that pick the
valid, scope-matching columns come from :mod:`core.lua_writer`, so the two exports can
never drift apart on *what* goes into the file. What differs is only how a value is
spelled:

- a keyed table (``key_count >= 1``) becomes a JSON object with string keys, so
  ``[1] = { id = 1 }`` -> ``{"1": {"id": 1}}``, and a two-level key
  (``[1][2]``) becomes ``{"1": {"2": ...}}``. JSON has no numeric keys, and keeping
  the nesting is what preserves "look this row up by id" for the reader.
- a table with no keys becomes a JSON array, the natural reading of the anonymous
  ``{ ... }, { ... },`` list the lua output emits
- **a field whose lua value is ``nil`` is left out of the object.** Lua cannot store
  nil, so ``x = nil`` and "there is no x" are the same thing; JSON *can* store null,
  and writing null would invent a distinction the source does not have.
- the ``--[[ file.xlsx -> Sheet ]]`` comment and any custom ``file_header`` /
  ``file_footer`` do not survive: JSON has no comment syntax, and the header/footer
  are lua syntax (``local cfg = {`` ... ``} return cfg``) with no JSON counterpart

Hand-written Lua in a ``table`` / ``any`` cell (``{10000, "Revive Envoy", 1503}``) is
carried over as native JSON when it is a plain literal, and kept as the cell's source
text when it is not - see :func:`_fragment_value`.

CRLF line endings and the write itself (encode first, then swap in) are shared with the
Lua export and live in :mod:`core.exporter`.
"""

import json
import math

from . import luaparse
from .lua_writer import (
    SCOPE_BOTH,
    to_number_literal,
    valid_field_name,
    # Private on purpose: these four decide which columns reach the output and how a
    # blank cell is recognised. Re-implementing them here is exactly how the two
    # exports would start disagreeing, so they are imported instead.
    _NUMERIC_RE,
    _crlf,
    _is_blank_str,
    _scope_filtered,
    _valid_indices,
)

#: "This field must not appear in the output at all" - unlike ``None``, which is a
#: perfectly good JSON value and means something different (an explicit null).
_OMIT = object()


def _number_value(value):
    """``number`` cell -> int / float / bool, or :data:`_OMIT` when lua would write ``nil``.

    Mirrors ``lua_writer._format_number``: a cell that is number-typed but holds
    something unparseable (``100pcs``, ``1,000``) or a non-finite float is written as
    ``nil`` by the lua export, which means the field is not there at all.
    """
    literal = to_number_literal(value)
    if literal is None:
        return _OMIT
    if literal == 'true':
        return True
    if literal == 'false':
        return False
    try:
        return int(literal)
    except ValueError:
        pass
    try:
        return float(literal)
    except (ValueError, OverflowError):
        return _OMIT


def _string_value(value):
    """``string`` cell -> str. An empty cell becomes ``""`` (the lua export writes ``[[]]``)."""
    return "" if value is None else _crlf(str(value))


def _json_safe(value):
    """Whether a parsed lua value maps onto JSON without losing anything.

    :class:`luaparse.Raw` is judged unsafe: it is the parser saying "this token was not
    a number, a boolean, nil or a string", i.e. an expression such as ``100+50``, and an
    expression only looks like data until someone reads it as one.

    A repeated key (``Dup``) is judged by the element that survives, because that is the
    value Lua itself would see - the last assignment wins.
    """
    if isinstance(value, luaparse.Raw):
        return False
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        # json.dumps would happily write Infinity / NaN - which no JSON parser reads back
        return math.isfinite(value)
    if isinstance(value, luaparse.Dup):
        return _json_safe(value[-1])
    if isinstance(value, dict):
        return all(_json_safe(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_json_safe(v) for v in value)
    return False


def _normalize(value):
    """Parsed lua -> plain JSON-able Python. Keys become strings, uniformly.

    The parser hands back whatever the literal used - ``{ a = 1 }`` gives the string
    ``a``, ``[1] = 2`` gives the int ``1``. JSON keys are always strings, so both
    become the same kind of key here and the reader is not left with a dictionary that
    is sometimes keyed by number.

    An integral float becomes an int, matching ``lua_writer._float_literal``: the lua
    export writes ``1.5e3`` as ``1500``, and json.dumps would spell the same number
    ``1500.0``. Same value, but the two files should not disagree on the token.
    """
    if isinstance(value, luaparse.Dup):
        return _normalize(value[-1])
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


def _fragment_value(raw):
    """A hand-written ``table`` / ``any`` cell -> native JSON, or the source text.

    ``{10000, "Revive Envoy", 1503}`` is a plain literal and becomes the array
    ``[10000, "Revive Envoy", 1503]``. ``100+50`` is an expression: this tool does not
    evaluate lua, and neither guessing ``150`` nor quietly dropping the field would be
    honest, so the cell keeps its source text and the reader decides what to do with it.
    A ``""``-only difference is intentional - the fallback is byte-identical to what the
    lua export writes, so the same cell reads the same way in both files.
    """
    text = _crlf(str(raw))
    if text.strip() == "nil":
        return _OMIT
    try:
        value = luaparse.parse_value(text)
    except luaparse.LuaParseError:
        return text
    if not _json_safe(value):
        return text
    normalized = _normalize(value)
    # Lua has a single empty table; JSON has two empty containers. A table-typed cell
    # builds a sequence in practice, so an empty one is written as [] rather than {}.
    if normalized == {}:
        return []
    return normalized


def _json_value(value, type_str):
    """One cell -> a JSON-able Python value, or :data:`_OMIT`.

    ``lua_writer._format_value`` branch for branch, so both exports carry the same
    fields. Read them side by side when changing either one.
    """
    if type_str == "number":
        return _number_value(value)

    if type_str == "string":
        return _string_value(value)

    if type_str == "table":
        if value is None or _is_blank_str(value):
            return []                       # the lua export writes {}
        return _fragment_value(value)

    if type_str == "any":
        if value is None or _is_blank_str(value):
            return _OMIT                    # the lua export writes nil
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return _number_value(value)
        return _fragment_value(value)

    return _string_value(value)


def _json_key(value, type_str):
    """A key cell as JSON key text - the value ``_format_key`` writes, without lua quoting.

    ``_format_key`` already decides what the key *is*: a numeric literal, a quoted
    string, or a verbatim token. JSON needs the same value, so the number branch is
    reproduced (including the ``0`` an unparseable number falls back to, and the same
    ``_NUMERIC_RE`` test) and everything else is the plain text of the cell - which is
    what ``_format_key`` wraps in quotes for a string key.
    """
    if type_str == "number":
        literal = to_number_literal(value)
        if literal is None or not _NUMERIC_RE.match(literal):
            return '0'
        return literal
    if value is None:
        return ''                           # the lua export writes [""]
    return str(value)


def _row_object(row, indices, types, field_names):
    """One data row -> a JSON object, skipping the fields lua would leave out."""
    obj = {}
    for idx in indices:
        type_str = types[idx] if idx < len(types) else "string"
        value = row[idx] if idx < len(row) else None
        if value is None:
            continue                        # an empty cell: the lua export omits the field
        json_value = _json_value(value, type_str)
        if json_value is _OMIT:
            continue
        obj[field_names[idx]] = json_value
    return obj


def _group(rows, key_indices, types):
    """Nest the rows level by level over the key columns; a later row with a key wins.

    Same shape as ``lua_writer._build_nested``, but keyed by JSON key text rather than
    by a lua literal. A duplicate key cannot reach this far: the pre-flight rejects the
    table first (``lua_writer.duplicate_key_errors``) because the lua export would
    silently drop the earlier rows.
    """
    data = {}
    for row in rows:
        parts = []
        for idx in key_indices:
            type_str = types[idx] if idx < len(types) else "number"
            value = row[idx] if idx < len(row) else None
            parts.append(_json_key(value, type_str))
        node = data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = row
    return data


def _nest_object(node, indices, types, field_names):
    """Walk the grouped keys; a dict is another key level, a list is one row."""
    out = {}
    for key, value in node.items():
        if isinstance(value, dict):
            out[key] = _nest_object(value, indices, types, field_names)
        else:
            out[key] = _row_object(value, indices, types, field_names)
    return out


def _dumps(data):
    """Tab-indented, with the trailing newline the lua files have too.

    ``ensure_ascii=False`` keeps Chinese readable in the file instead of ``\\u4e2d``;
    the chosen output encoding (utf-8 / gbk) is applied when the text is written.
    """
    return json.dumps(data, indent="\t", ensure_ascii=False) + "\n"


def _build_base_json(table_info, scope_filter):
    scopes = table_info.get("scopes", [])
    types = table_info.get("types", [])
    field_names = table_info.get("field_names", [])
    data_rows = table_info.get("data_rows", [])
    key_count = table_info.get("key_count", 0)

    all_indices = _valid_indices(field_names)
    indices = _scope_filtered(all_indices, scopes, scope_filter)
    if not indices:
        return None

    key_count = min(key_count, len(all_indices))

    if key_count == 0:
        data = [_row_object(row, indices, types, field_names) for row in data_rows]
    else:
        key_indices = all_indices[:key_count]
        grouped = _group(data_rows, key_indices, types)
        data = _nest_object(grouped, indices, types, field_names)

    return _dumps(data)


def _build_tiny_json(table_info, scope_filter):
    fields = table_info.get("fields", [])

    filtered = [
        f for f in fields
        if valid_field_name(f["field_name"])
        and (f["scope"] == scope_filter or f["scope"] in SCOPE_BOTH)
    ]
    if not filtered:
        return None

    data = {}
    for f in filtered:
        value = f["value"]
        if value is None:
            continue
        json_value = _json_value(value, f["type"])
        if json_value is _OMIT:
            continue
        data[f["field_name"]] = json_value

    return _dumps(data)


def generate_json(table_info, scope_filter):
    export_type = table_info.get("export_type", "base")
    if export_type == "base":
        return _build_base_json(table_info, scope_filter)
    elif export_type == "tiny":
        return _build_tiny_json(table_info, scope_filter)
    else:
        return None
