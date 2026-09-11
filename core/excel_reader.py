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
    """行内最后一个非空单元格的列号（列中间有空洞也能跨过去）。"""
    last = 0
    for idx, cell in enumerate(ws[row_num], start=1):
        if cell.value is not None:
            last = idx
    return last


#: 表头行的探测顺序（第 5 行是字段说明行，正常表都有；个别表可能留空）
_HEADER_ROWS = (5, 7, 6, 8)

#: 表头块的四行：注释 / scope / 类型 / 字段名
_HEADER_BLOCK_ROWS = (5, 6, 7, 8)

#: 「断点列」= E 列（第 5 列）
_BREAK_COLUMN = 5


def _header_block_empty(ws, col):
    """第 5~8 行在该列上是否全空（即这列没有注释/scope/类型/字段名，根本不是字段列）。"""
    for r in _HEADER_BLOCK_ROWS:
        if r > ws.max_row:
            continue
        val = ws.cell(row=r, column=col).value
        if val is not None and str(val).strip() != "":
            return False
    return True


def _cut_at_break_column(ws):
    """E 列是不是这张表的「表头断点」——是则 E 及其右侧的列都不导出。

    约定：E 列是留给作者的分隔列，备注/草稿写在它右边。
    即 A~D 是正常字段，E 列空，F 及右侧即使写了字段名或内容也不导出。

    生效条件：A~D 四列的表头块（注释 / scope / 类型 / 字段名）都完整，
    也就是 E 确实是表头**连续段的断点**。
    这样才能避免误伤「中间某列空、右边还有正常字段」的表——
    它们的表头断点不在 E 列，在别处，中间的空列只是缺列而非结束。
    """
    if not _header_block_empty(ws, _BREAK_COLUMN):
        return False
    return all(not _header_block_empty(ws, c) for c in range(1, _BREAK_COLUMN))


def _resolve_column_count(ws):
    """扫描范围 = 第 5 行最靠右的非空单元格列号（第 5 行空则退到 7/6/8 行）。

    两个要点（都对齐旧工具）：
    1. 取"最右非空"而不是"遇到空列就停"——中间某列空、更右边仍有正常字段时，
       右侧那些字段要一并带上。
    2. 只看第 5 行，不把 6/7/8 行一起取最大——右侧可能存在只填了字段名、
       却没有第 5 行说明的脏列，那种不导出。

    例外：表头在 ``_BREAK_COLUMN``（E 列）断开时，E 及右侧一律不算，见 ``_cut_at_break_column``。
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


#: 需要做内容校验的单元格类型
#: - ``table`` / ``any``：内容是"原样写进 lua"的，做 Lua 语法校验
#: - ``number``：填了非数字会被静默写成 nil（数据丢失），做数字格式校验
_CHECKED_TYPES = ("table", "any", "number")

#: tiny 表的值固定写在 E 列（第 5 列：备注/scope/类型/字段名/值）
_TINY_VALUE_COLUMN = 5


def _error_kind(type_str):
    """错误分类，决定日志里显示成"Lua 语法错误"还是"数字格式错误"。"""
    return "number" if type_str == "number" else "lua"


def _check_cell(value, type_str):
    """校验单元格内容。通过（或无需校验）返回 None，否则返回错误描述。"""
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
    """逐行逐列校验受检类型的单元格，收集错误。

    只查字段名合法的列（右侧脏列不导出、也就没有校验的意义）。
    ``first_row`` 是数据区首行的 Excel 行号（base 表固定为 9），用于把
    下标还原成用户在 Excel 里看到的行号；``col`` 同理，是 1 起算的 Excel 列号，
    日志里会渲染成 ``B12`` 这种单元格地址，方便直接定位。
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
    """tiny 表同理：每个字段一行，行号直接取 Excel 行号，值恒在 E 列。"""
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
    """解析表头元信息。

    非配置 sheet（说明页、空 sheet、格式不规范的关键数据页）返回 None，调用方跳过。
    判定依据与旧导表工具一致：B1 必须是 ``base`` / ``tiny``，且 B2 是合法的 ``*.lua`` 文件名。
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

    # 只有字段名合法的列才算"有效列"。判定数据区是否结束、以及第 5 行的截断
    # 都只看这些列：表右侧常残留一大片脏数据（字段名不是合法 lua 标识符），
    # 若把它们算进来，数据区中间的空行就会被当成"有数据"，多导出整整一段。
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
            # 数据区遇到（有效列上）整行为空即结束：旧工具在"左侧真空行、
            # 右侧还留着脏数据"的位置同样就此收尾
            break

    # 第 9 行（base 表数据区首行）为空 => 整表视为无数据，直接跳过、不生成 lua 文件。
    # 有些表第 9 行留空、数据从第 10 行才开始，旧工具同样不导出它们。
    # 注意这里不能"跳过空行继续往下读"——那会让这类表被当成有数据而误导出。
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
    fields = []
    col_count = max(5, _last_used_column(ws, 5))

    for r in range(6, ws.max_row + 1):
        row = [ws.cell(row=r, column=c).value for c in range(1, col_count + 1)]
        if all(v is None for v in row):
            # 空行跳过，继续往下读（表里确实存在"失败特效"这种空一行之后再写的字段）
            continue

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

    # 与 base 表同理：一个字段都没有的 tiny 表不生成 lua（当前源目录无此情况，仅作兜底）
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
