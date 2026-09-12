import os
import openpyxl
from concurrent.futures import ThreadPoolExecutor, as_completed

from .lua_writer import valid_field_name
from .lua_syntax import validate_lua_value, validate_number


def _find_column_count(ws, row_num, stop_at_null=True):
    count = 0
    for cell in ws[row_num]:
        if cell.value is not None:
            count += 1
        elif stop_at_null:
            break
    return count


def _find_last_column(ws, row_num):
    return _find_column_count(ws, row_num, stop_at_null=False)


def _last_used_column(ws, row_num):
    """Column number of the last non-empty cell in the row (gaps in between are crossed)."""
    last = 0
    for idx, cell in enumerate(ws[row_num], start=1):
        if cell.value is not None:
            last = idx
    return last


#: Order in which header rows are probed (row 5 holds the field comments and is present in most sheets; a few leave it blank)
_HEADER_ROWS = (5, 7, 6, 8)

#: The four rows of the header block: comment / scope / type / field name
_HEADER_BLOCK_ROWS = (5, 6, 7, 8)

#: The "break column" is E (column 5)
_BREAK_COLUMN = 5


def _header_block_empty(ws, col):
    """Whether rows 5~8 are all empty in this column (no comment/scope/type/field name, so it is not a field column at all)."""
    for r in _HEADER_BLOCK_ROWS:
        if r > ws.max_row:
            continue
        val = ws.cell(row=r, column=col).value
        if val is not None and str(val).strip() != "":
            return False
    return True


def _cut_at_break_column(ws):
    """Is column E the header "break column" of this sheet? If so, E and everything to its right is not exported.

    Convention: column E is a separator reserved for the author, with notes and
    drafts written to its right. A~D carry the real fields, E is empty, and F
    onwards is not exported even when it holds field names or data.

    It only applies when the A~D header block (comment / scope / type / field name)
    is complete, i.e. E really is the break of a continuous header. That keeps
    sheets with a gap in the middle - and more real fields to the right - safe:
    their break is not at E but somewhere else, and the empty column in the middle
    is a missing column rather than the end of the table.
    """
    if not _header_block_empty(ws, _BREAK_COLUMN):
        return False
    return all(not _header_block_empty(ws, c) for c in range(1, _BREAK_COLUMN))


def _resolve_column_count(ws):
    """Scan range = the right-most non-empty cell in row 5 (falling back to rows 7 / 6 / 8 when row 5 is blank).

    Two rules, both matching the legacy tool:
    1. Take the right-most non-empty cell rather than stopping at the first empty
       column - when a column in the middle is empty but real fields continue to the
       right, those fields must be included.
    2. Look at row 5 only and do not take the maximum over rows 6/7/8 - the right
       side may hold junk columns that have a field name but no row-5 comment, and
       those are not exported.

    Exception: when the header breaks at ``_BREAK_COLUMN`` (column E), E and
    everything to its right is ignored; see ``_cut_at_break_column``.
    """
    for r in _HEADER_ROWS:
        if r > ws.max_row:
            continue
        count = _last_used_column(ws, r)
        if count:
            if count >= _BREAK_COLUMN and _cut_at_break_column(ws):
                return _BREAK_COLUMN - 1
            return count
    return 0


def parse_sheet(ws):
    meta = _parse_meta(ws)
    if meta is None:
        return None

    export_type = meta.get("export_type", "base")

    if export_type == "base":
        return _parse_base(ws, meta)
    elif export_type == "tiny":
        return _parse_tiny(ws, meta)
    else:
        return None


#: Cell types whose content is validated
#: - ``table`` / ``any``: written into the lua verbatim, so validate the Lua syntax
#: - ``number``: a non-numeric value is silently written as nil (data loss), so validate the number format
_CHECKED_TYPES = ("table", "any", "number")

#: A tiny table always keeps its value in column E (column 5: comment/scope/type/field/name -> value)
_TINY_VALUE_COLUMN = 5


def _error_kind(type_str):
    """Error category: decides whether the log says 'Lua syntax error' or 'number format error'."""
    return "number" if type_str == "number" else "lua"


def _check_cell(value, type_str):
    """Validate a cell's content. Returns None when it passes (or needs no check), otherwise the error description."""
    if type_str not in _CHECKED_TYPES:
        return None
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    if type_str == "number":
        return validate_number(value)
    return validate_lua_value(text)


def _collect_base_syntax_errors(data_rows, types, field_names, first_row=9):
    """Validate the cells of the checked types row by row, column by column, collecting errors.

    Only columns with a valid field name are inspected (junk columns on the right
    are not exported, so validating them is meaningless). ``first_row`` is the Excel
    row number of the first data row (always 9 for base sheets) and converts an index
    back into the row number the user sees in Excel; ``col`` likewise is the 1-based
    Excel column number, rendered in the log as a cell address such as ``B12`` so the
    problem can be located directly.
    """
    errors = []
    for i, row in enumerate(data_rows):
        excel_row = first_row + i
        for c, fname in enumerate(field_names):
            if c >= len(types):
                break
            if types[c] not in _CHECKED_TYPES:
                continue
            if not valid_field_name(fname):
                continue
            value = row[c] if c < len(row) else None
            err = _check_cell(value, types[c])
            if err:
                errors.append({
                    "row": excel_row,
                    "col": c + 1,
                    "field": fname,
                    "type": types[c],
                    "kind": _error_kind(types[c]),
                    "value": value,
                    "error": err,
                })
    return errors


def _collect_tiny_syntax_errors(fields):
    """Same for tiny sheets: one row per field, the row number is the Excel row number, and the value always sits in column E."""
    errors = []
    for f in fields:
        ftype = f.get("type")
        err = _check_cell(f.get("value"), ftype)
        if err:
            errors.append({
                "row": f.get("row"),
                "col": _TINY_VALUE_COLUMN,
                "field": f.get("field_name"),
                "type": ftype,
                "kind": _error_kind(ftype),
                "value": f.get("value"),
                "error": err,
            })
    return errors


def _cell_value(ws, row, col):
    if row > ws.max_row or col > ws.max_column:
        return None
    return ws.cell(row=row, column=col).value


def _parse_meta(ws):
    """Parse the table-level metadata.

    Returns None for a sheet that is not a configuration table (a notes page, an
    empty sheet, a badly formatted key sheet); the caller skips it. The rule matches
    the legacy exporter: B1 must be ``base`` / ``tiny`` and B2 a valid ``*.lua`` file
    name.
    """
    meta = {}

    raw_type = _cell_value(ws, 1, 2)
    export_type = str(raw_type).strip().lower() if raw_type is not None else ""
    if export_type not in ("base", "tiny"):
        return None
    meta["export_type"] = export_type

    raw_name = _cell_value(ws, 2, 2)
    output_filename = str(raw_name).strip() if raw_name is not None else ""
    if not output_filename.lower().endswith(".lua"):
        return None
    meta["output_filename"] = output_filename

    val = _cell_value(ws, 1, 5)
    meta["file_header"] = str(val) if val is not None else ""

    val = _cell_value(ws, 2, 5)
    meta["file_footer"] = str(val) if val is not None else ""

    val = _cell_value(ws, 3, 2)
    try:
        meta["key_count"] = int(val) if val is not None else 0
    except (TypeError, ValueError):
        meta["key_count"] = 0

    return meta


def _parse_base(ws, meta):
    annotations = []
    scopes = []
    types = []
    field_names = []
    data_rows = []

    col_count = _resolve_column_count(ws)

    for i in range(1, col_count + 1):
        row5_val = ws.cell(row=5, column=i).value
        annotations.append(str(row5_val).strip() if row5_val is not None else "")

        row6_val = ws.cell(row=6, column=i).value
        scopes.append(str(row6_val).strip().lower() if row6_val is not None else "c")

        row7_val = ws.cell(row=7, column=i).value
        types.append(str(row7_val).strip().lower() if row7_val is not None else "string")

        row8_val = ws.cell(row=8, column=i).value
        field_names.append(str(row8_val).strip() if row8_val is not None else "")

    # Only columns with a valid field name count as an "effective column". Deciding
        # where the data area ends, and the row-5 cut-off, both look at these columns
        # only: the right-hand side of a sheet often carries a block of junk whose field
    # names are not valid lua identifiers, and counting it would make a blank row in
    # the middle of the data area look like data and export an entire extra block.
    valid_cols = {i for i in range(1, col_count + 1)
                  if valid_field_name(field_names[i - 1])}

    for r in range(9, ws.max_row + 1):
        row_vals = []
        has_data = False
        for c in range(1, col_count + 1):
            val = ws.cell(row=r, column=c).value
            if val is not None and c in valid_cols:
                has_data = True
            row_vals.append(val)
        if has_data:
            data_rows.append(row_vals)
        else:
                        # The data area ends at the first row that is entirely empty across the
                        # effective columns: the legacy tool wraps up at the same place, even when
            # junk is still lying to the right of that blank row.
            break

        # Row 9 (the first data row of a base sheet) being empty => the whole table counts
        # as having no data: skip it and write no lua file at all. Some sheets leave row 9
        # blank and start their data on row 10, and the legacy tool skips those as well.
    # Note that we must not "skip the blank row and keep reading" - that would export
    # those sheets by mistake.
    if not data_rows:
        return None

    return {
        "export_type": "base",
        "sheet_name": ws.title,
        "output_filename": meta["output_filename"],
        "file_header": meta.get("file_header", ""),
        "file_footer": meta.get("file_footer", ""),
        "key_count": meta["key_count"],
        "annotations": annotations,
        "scopes": scopes,
        "types": types,
        "field_names": field_names,
        "data_rows": data_rows,
        "syntax_errors": _collect_base_syntax_errors(data_rows, types, field_names),
    }


def _parse_tiny(ws, meta):
    """Parse a tiny sheet: rows 6+ are one field each, and the field area ends at the first blank row."""
    fields = []
    col_count = max(5, _last_used_column(ws, 5))

    for r in range(6, ws.max_row + 1):
        row = [ws.cell(row=r, column=c).value for c in range(1, col_count + 1)]
        if all(v is None for v in row):
            # The field area ends at the first blank row: everything below the gap is
            # ignored, even when it looks like more fields. This mirrors a base sheet
            # ending at its first empty data row, and it keeps stray notes - or a second
            # draft of the table further down - from leaking into the export.
            break

        config_note = str(row[0]).strip() if row[0] is not None else ""
        scope = str(row[1]).strip().lower() if row[1] is not None else "c"
        type_str = str(row[2]).strip().lower() if row[2] is not None else "string"
        field_name = str(row[3]).strip() if row[3] is not None else ""
        value = row[4]

        if not field_name:
            continue

        fields.append({
            "config_note": config_note,
            "scope": scope,
            "type": type_str,
            "field_name": field_name,
            "value": value,
            "row": r,
        })

    # Same rule as base sheets: a tiny sheet with no field at all produces no lua file.
    # That is the case when row 6 is blank, and also when no row carries a field name.
    if not fields:
        return None

    return {
        "export_type": "tiny",
        "sheet_name": ws.title,
        "output_filename": meta["output_filename"],
        "file_header": meta.get("file_header", ""),
        "file_footer": meta.get("file_footer", ""),
        "key_count": meta["key_count"],
        "fields": fields,
        "syntax_errors": _collect_tiny_syntax_errors(fields),
    }


def list_excel_files(source_dir):
    if not os.path.isdir(source_dir):
        return []
    result = []
    for fname in os.listdir(source_dir):
        if fname.startswith("~$"):
            continue
        if not fname.lower().endswith(".xlsx"):
            continue
        result.append(os.path.join(source_dir, fname))
    return result


def load_excel(filepath):
    wb = openpyxl.load_workbook(filepath, data_only=True)
    tables = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        table = parse_sheet(ws)
        if table is None:
            continue
        table["source_file"] = os.path.basename(filepath)
        tables.append(table)
    wb.close()
    return tables
