"""Generate the example workbooks and the exported output under ``example/``.

Usage::

    python tools/make_examples.py            # both languages (default)
    python tools/make_examples.py en         # English only
    python tools/make_examples.py zh-CN      # Chinese only

Output layout::

    example/
      en/     import/  client/  server/
      zh-CN/  import/  client/  server/

Two jobs:

1. write the example xlsx files into ``example/<lang>/import/`` (covering every
   table layout and field type)
2. export them with the real export logic into ``example/<lang>/client/`` and
   ``example/<lang>/server/``

The example workbooks *are* the format specification - for "what is this row or
column for", this script is more accurate than the documentation. The two languages
differ only in human-facing text (sheet comments, cell data, tiny table headers);
table structure and field names are identical, and so is the exported lua structure.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl

from core.excel_reader import list_excel_files, load_excel
from core.exporter import export_table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Supported languages (also the subdirectory names under example/)
LANGS = ("zh-CN", "en")

#: Examples are always exported as UTF-8 so they can be read directly on GitHub.
#: A real project may use a different encoding per side (chosen in the UI).
ENCODING = "utf-8"

#: Only remove the files this script produced, never anything else in the directory
_OUTPUT_PREFIX = "cfg_example_"


class _Skip:
    """Placeholder: this cell "does not exist" (nothing is written), as opposed to an empty string."""


SKIP = _Skip()


# -- Human-facing text (the only part that differs between the two languages) --

#: Text of the table-level metadata cells (labels in A1/A2/A3 and D1/D2)
_META = {
    "zh-CN": ("导出类型", "导出文件", "key数量", "导出文件头", "导出文件尾"),
    "en": ("Export type", "Export file", "key count", "File header", "File footer"),
}

#: Row 5 header of a tiny table
_TINY_HEADS = {
    "zh-CN": ("配置备注", "导出参数", "值类型", "字段名", "值"),
    "en": ("Note", "Scope", "Type", "Field", "Value"),
}

_CONTENT = {
    "zh-CN": {
        "readme": {
            "sheet": "Readme",
            "lines": ("说明页", "这部分不是配置",
                      "B1 既不是 base 也不是 tiny，整个 sheet 会被忽略。"),
        },
        "item": {
            "sheet": "Item",
            "cols": [
                ("唯一 ID",    "sc", "number", "id"),
                ("名称",       "c",  "string", "name"),
                ("服务端备注", "s",  "string", "server_note"),
                ("描述",       "sc", "string", "desc"),
                ("权重",       "cs", "number", "weight"),
                ("价格",       "c",  "number", "price"),
                ("图标",       "c",  "string", "icon"),
                ("掉落表",     "s",  "table",  "drops"),
                ("自定义参数", "sc", "any",    "extra"),
                ("是否上架",   "c",  "any",    "on_sale"),
            ],
            "rows": [
                [1, "长剑", "新手村产出", "一把普通的长剑", 100, 10, "icon_1001",
                 "{{1001, 2}, {1002, 1}}", "nil", True],
                [2, "铁盾", "铁匠铺出售", "结实的铁盾", 80, 50, "icon_1002",
                 SKIP, "100+50", False],
                [3, "药水", "商店购买", SKIP, 200, 5, "icon_1003",
                 "{{2001, 5}}", "nil", True],
                [4, "卷轴", "活动奖励", "使用后随机传送", 50, SKIP, "icon_1004",
                 "{{3001, 1}}", "{quality=3}", True],
            ],
        },
        "equip": {
            "sheet": "Equip",
            "cols": [
                ("装备 ID",  "sc", "number", "id"),
                ("部位",     "sc", "string", "part"),
                ("攻击",     "sc", "number", "attack"),
                ("防御",     "sc", "number", "defense"),
                ("强化上限", "s",  "number", "max_level"),
                ("套装 ID",  "c",  "number", "suit_id"),
            ],
            "rows": [
                [100, "weapon", 12, 0,  15, 1],
                [101, "armor",  0,  18, 15, 1],
                [102, "helmet", 3,  8,  10, 2],
            ],
        },
        "shop": {
            "sheet": "Shop",
            "cols": [
                ("商店 ID", "sc", "number", "shop_id"),
                ("商品 ID", "sc", "number", "item_id"),
                ("售价",    "c",  "number", "price"),
                ("库存",    "s",  "number", "stock"),
                ("商品名",  "sc", "string", "name"),
            ],
            "rows": [
                [1, 1001, 10, 999, "长剑"],
                [1, 1002, 50, 999, "铁盾"],
                [2, 1001, 12, 100, "长剑"],
                [2, 2001, 5,  200, "药水"],
            ],
        },
        "tips": {
            "sheet": "Tips",
            "cols": [
                ("ID",   "c", "number", "id"),
                ("文案", "c", "string", "text"),
            ],
            "rows": [
                [1, "欢迎来到示例服务器"],
                [2, "按 H 打开帮助"],
                [3, "每天 5 点重置日常"],
            ],
        },
        "empty": {
            "sheet": "EmptyTable",
            "cols": [
                ("ID",   "sc", "number", "id"),
                ("名称", "sc", "string", "name"),
            ],
            "rows": [],
        },
        "tiny": {
            "sheet": "Settings",
            "fields": [
                ("每日免费复活次数", "sc", "number", "free_revive_count", 3),
                ("复活消耗文本",     "c",  "string", "revive_cost_text", "消耗 %d 元宝复活"),
                ("入口 NPC",         "s",  "table",  "entrance_npc",     '{10000, "复活使者", 1503}'),
                ("功能开关",         "c",  "any",    "enable",           True),
                ("额外配置",         "sc", "any",    "extra",            "nil"),
                ("空串开关",         "c",  "any",    "blank_flag",       " "),
                SKIP,  # Leave one blank row in the middle: a tiny table skips blank rows and keeps reading
                ("活动倍率",         "s",  "number", "activity_rate",    1.5e3),
                ("公告",             "c",  "string", "notice",           "欢迎光临"),
            ],
        },
        "edge": {
            "sheet": "Edge",
            "cols": [
                ("ID",                "sc", "number", "id"),
                ("多行文本",          "c",  "string", "multiline"),
                ("含 ]] 的文本",      "c",  "string", "square_brackets"),
                ("以 ] 结尾的文本",   "c",  "string", "trailing_bracket"),
                ("文本：满/空格/空着", "sc", "string", "optional_text"),
                ("数字：满/空格/空着", "c",  "number", "blank_number"),
                ("表：满/空格/空着",  "c",  "table",  "blank_table"),
                ("布尔开关",          "sc", "any",    "flag"),
                ("科学计数",          "s",  "number", "scientific"),
                ("负数",              "s",  "number", "negative"),
            ],
                        # Columns 4/5/6 deliberately show three cases: a real value / spaces only
                        # (= empty string) / the cell left empty
            # -> the output is [[ ]] (empty string), nil and {} respectively, while "cell
            #    left empty" means the field is not written at all
            "rows": [
                [1, "第一行\n第二行", "a]]b", "[边界]", "有值", 100, "{{1, 2}}", True, 1.5e3, -42],
                [2, "单行文本",       "a]b",   "尾]",   " ",    " ",   " ",        False, 2.5e-2, -7],
                [3, SKIP,             "普通",  "正常",  SKIP,   SKIP,  SKIP,       "nil", 1e10,   -1],
            ],
        },
        "custom": {
            "sheet": "CustomHeaderFooter",
            "cols": [
                ("ID",   "sc", "number", "id"),
                ("名称", "sc", "string", "name"),
            ],
            "rows": [[1, "甲"], [2, "乙"]],
        },
    },
    "en": {
        "readme": {
            "sheet": "Readme",
            "lines": ("Notes", "This sheet is not a config",
                      "B1 is neither 'base' nor 'tiny', so the whole sheet is ignored."),
        },
        "item": {
            "sheet": "Item",
            "cols": [
                ("Unique ID",     "sc", "number", "id"),
                ("Name",          "c",  "string", "name"),
                ("Server note",   "s",  "string", "server_note"),
                ("Description",   "sc", "string", "desc"),
                ("Weight",        "cs", "number", "weight"),
                ("Price",         "c",  "number", "price"),
                ("Icon",          "c",  "string", "icon"),
                ("Drop table",    "s",  "table",  "drops"),
                ("Extra params",  "sc", "any",    "extra"),
                ("On sale",       "c",  "any",    "on_sale"),
            ],
            "rows": [
                [1, "Long Sword", "Dropped in the starter village", "A plain long sword",
                 100, 10, "icon_1001", "{{1001, 2}, {1002, 1}}", "nil", True],
                [2, "Iron Shield", "Sold by the blacksmith", "A sturdy iron shield",
                 80, 50, "icon_1002", SKIP, "100+50", False],
                [3, "Potion", "Bought from the shop", SKIP,
                 200, 5, "icon_1003", "{{2001, 5}}", "nil", True],
                [4, "Scroll", "Event reward", "Teleports you to a random place",
                 50, SKIP, "icon_1004", "{{3001, 1}}", "{quality=3}", True],
            ],
        },
        "equip": {
            "sheet": "Equip",
            "cols": [
                ("Equip ID",           "sc", "number", "id"),
                ("Slot",               "sc", "string", "part"),
                ("Attack",             "sc", "number", "attack"),
                ("Defense",            "sc", "number", "defense"),
                ("Max enhance level",  "s",  "number", "max_level"),
                ("Suit ID",            "c",  "number", "suit_id"),
            ],
            "rows": [
                [100, "weapon", 12, 0,  15, 1],
                [101, "armor",  0,  18, 15, 1],
                [102, "helmet", 3,  8,  10, 2],
            ],
        },
        "shop": {
            "sheet": "Shop",
            "cols": [
                ("Shop ID",   "sc", "number", "shop_id"),
                ("Item ID",   "sc", "number", "item_id"),
                ("Price",     "c",  "number", "price"),
                ("Stock",     "s",  "number", "stock"),
                ("Item name", "sc", "string", "name"),
            ],
            "rows": [
                [1, 1001, 10, 999, "Long Sword"],
                [1, 1002, 50, 999, "Iron Shield"],
                [2, 1001, 12, 100, "Long Sword"],
                [2, 2001, 5,  200, "Potion"],
            ],
        },
        "tips": {
            "sheet": "Tips",
            "cols": [
                ("ID",   "c", "number", "id"),
                ("Text", "c", "string", "text"),
            ],
            "rows": [
                [1, "Welcome to the example server"],
                [2, "Press H to open the help panel"],
                [3, "Daily quests reset at 5 AM"],
            ],
        },
        "empty": {
            "sheet": "EmptyTable",
            "cols": [
                ("ID",   "sc", "number", "id"),
                ("Name", "sc", "string", "name"),
            ],
            "rows": [],
        },
        "tiny": {
            "sheet": "Settings",
            "fields": [
                ("Free revives per day",  "sc", "number", "free_revive_count", 3),
                ("Revive cost text",      "c",  "string", "revive_cost_text", "Revive costs %d gold"),
                ("Entrance NPC",          "s",  "table",  "entrance_npc",     '{10000, "Revive Envoy", 1503}'),
                ("Feature switch",        "c",  "any",    "enable",           True),
                ("Extra config",          "sc", "any",    "extra",            "nil"),
                ("Blank string switch",   "c",  "any",    "blank_flag",       " "),
                SKIP,  # a blank row in the middle: tiny sheets skip blank rows and keep going
                ("Event rate",            "s",  "number", "activity_rate",    1.5e3),
                ("Notice",                "c",  "string", "notice",           "Welcome!"),
            ],
        },
        "edge": {
            "sheet": "Edge",
            "cols": [
                ("ID",                          "sc", "number", "id"),
                ("Multi-line text",             "c",  "string", "multiline"),
                ("Text containing ']]'",        "c",  "string", "square_brackets"),
                ("Text ending with ']'",        "c",  "string", "trailing_bracket"),
                ("Text: value / blank / empty", "sc", "string", "optional_text"),
                ("Number: value / blank / empty", "c", "number", "blank_number"),
                ("Table: value / blank / empty", "c", "table",  "blank_table"),
                ("Boolean switch",              "sc", "any",    "flag"),
                ("Scientific notation",         "s",  "number", "scientific"),
                ("Negative number",             "s",  "number", "negative"),
            ],
            # Columns 5/6/7 contrast three cases on purpose: a value / a whitespace-only
            # cell (= empty string) / a cell that is not written at all.
            # Output: an empty string, nil, and "field not emitted at all".
            "rows": [
                [1, "line one\nline two", "a]]b", "[edge]", "has value", 100, "{{1, 2}}",
                 True, 1.5e3, -42],
                [2, "single line",        "a]b",   "tail]",   " ",         " ",  " ",
                 False, 2.5e-2, -7],
                [3, SKIP,                 "plain", "normal",  SKIP,        SKIP, SKIP,
                 "nil", 1e10, -1],
            ],
        },
        "custom": {
            "sheet": "CustomHeaderFooter",
            "cols": [
                ("ID",   "sc", "number", "id"),
                ("Name", "sc", "string", "name"),
            ],
            "rows": [[1, "Alpha"], [2, "Beta"]],
        },
    },
}


# ── Writing sheets ─────────────────────────────────────────────


def _set_meta(ws, lang, kind, filename, key_count=0, header="return {", footer="}"):
    """写 1~3 行的表级元信息（两种表类型都一样）。"""
    kind_l, file_l, keys_l, head_l, foot_l = _META[lang]
    ws["A1"] = kind_l
    ws["B1"] = kind
    ws["A2"] = file_l
    ws["B2"] = filename
    ws["A3"] = keys_l
    ws["B3"] = key_count
    ws["D1"] = head_l
    ws["E1"] = header
    ws["D2"] = foot_l
    ws["E2"] = footer


def write_base_sheet(wb, lang, title, filename, key_count, columns, rows,
                     header="return {", footer="}"):
    """Write one base sheet (one row per record).

    ``columns``: list of ``(comment, scope, type, field name)``
    ``rows``: one list per row, ordered like ``columns``; use ``SKIP`` to leave a
    cell empty
    """
    ws = wb.create_sheet(title)
    _set_meta(ws, lang, "base", filename, key_count, header, footer)

    for col, (note, scope, ftype, fname) in enumerate(columns, start=1):
        ws.cell(row=5, column=col, value=note)
        ws.cell(row=6, column=col, value=scope)
        ws.cell(row=7, column=col, value=ftype)
        ws.cell(row=8, column=col, value=fname)

    for r, row in enumerate(rows, start=9):
        for c, value in enumerate(row, start=1):
            if value is SKIP:
                continue
            ws.cell(row=r, column=c, value=value)
    return ws


def write_tiny_sheet(wb, lang, title, filename, fields, header="return {", footer="}"):
    """Write one tiny sheet (one field per row; the whole file is a single record).

    ``fields``: list of ``(comment, scope, type, field name, value)``; use ``SKIP``
    to leave the whole row empty.
    """
    ws = wb.create_sheet(title)
    _set_meta(ws, lang, "tiny", filename, 0, header, footer)

    for col, head in enumerate(_TINY_HEADS[lang], start=1):
        ws.cell(row=5, column=col, value=head)

    for r, field in enumerate(fields, start=6):
        if field is SKIP:
            continue
        note, scope, ftype, fname, value = field
        ws.cell(row=r, column=1, value=note)
        ws.cell(row=r, column=2, value=scope)
        ws.cell(row=r, column=3, value=ftype)
        ws.cell(row=r, column=4, value=fname)
        if value is not SKIP:
            ws.cell(row=r, column=5, value=value)
    return ws


def _new_workbook():
    """新建工作簿并丢掉默认的 Sheet。"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    return wb


# ── Example files ───────────────────────────────────────────


def build_types_and_scopes(lang, import_dir):
    """01：四种字段类型 × 四种 scope。"""
    c = _CONTENT[lang]
    wb = _new_workbook()

    item = c["item"]
    write_base_sheet(wb, lang, item["sheet"], "cfg_example_item.lua", 1,
                     item["cols"], item["rows"])

    equip = c["equip"]
    write_base_sheet(wb, lang, equip["sheet"], "cfg_example_equip.lua", 1,
                     equip["cols"], equip["rows"])

    wb.save(os.path.join(import_dir, "01_types_and_scopes.xlsx"))
    wb.close()


def build_keys_and_layout(lang, import_dir):
    """02：key 数量 0 / 1 / 2（决定输出是列表还是多级嵌套），外加会被跳过的两种页。"""
    c = _CONTENT[lang]
    wb = _new_workbook()

    shop = c["shop"]              # key count 2 -> [shop_id] = { [item_id] = { ... } }
    write_base_sheet(wb, lang, shop["sheet"], "cfg_example_shop.lua", 2,
                     shop["cols"], shop["rows"])

    tips = c["tips"]              # key count 0 -> one anonymous table element per row
    write_base_sheet(wb, lang, tips["sheet"], "cfg_example_tips.lua", 0,
                     tips["cols"], tips["rows"])

        # The two sheet kinds that get skipped (neither produces a lua file)
    readme = c["readme"]
    ws = wb.create_sheet(readme["sheet"])
    ws["A1"], ws["B1"], ws["A3"] = readme["lines"]

    empty = c["empty"]
    write_base_sheet(wb, lang, empty["sheet"], "cfg_example_empty.lua", 1,
                     empty["cols"], empty["rows"])

    wb.save(os.path.join(import_dir, "02_keys_and_layout.xlsx"))
    wb.close()


def build_tiny_config(lang, import_dir):
    """03：tiny 表——整个文件只有一条记录，一行一个字段。"""
    c = _CONTENT[lang]
    wb = _new_workbook()
    tiny = c["tiny"]
    write_tiny_sheet(wb, lang, tiny["sheet"], "cfg_example_setting.lua", tiny["fields"])
    wb.save(os.path.join(import_dir, "03_tiny_config.xlsx"))
    wb.close()


def build_edge_cases(lang, import_dir):
    """04：各种边界情形。"""
    c = _CONTENT[lang]
    wb = _new_workbook()

    edge = c["edge"]
    write_base_sheet(wb, lang, edge["sheet"], "cfg_example_edge.lua", 1,
                     edge["cols"], edge["rows"])

        # The file header/footer can be customised: this wraps everything in local + return, still valid lua
    custom = c["custom"]
    write_base_sheet(wb, lang, custom["sheet"], "cfg_example_custom.lua", 1,
                     custom["cols"], custom["rows"],
                     header="local cfg = {", footer="}\nreturn cfg")

    wb.save(os.path.join(import_dir, "04_edge_cases.xlsx"))
    wb.close()


def build_all(lang, import_dir):
    os.makedirs(import_dir, exist_ok=True)
    build_types_and_scopes(lang, import_dir)
    build_keys_and_layout(lang, import_dir)
    build_tiny_config(lang, import_dir)
    build_edge_cases(lang, import_dir)


# ── Export ─────────────────────────────────────────────────────


def lang_dirs(lang):
    """某语言下的 (import, client, server) 三个目录。"""
    base = os.path.join(ROOT, "example", lang)
    return (os.path.join(base, "import"),
            os.path.join(base, "client"),
            os.path.join(base, "server"))


def clean_outputs(client_dir, server_dir):
    """只删自己生成过的产物，别动目录里其他文件。"""
    for d in (client_dir, server_dir):
        os.makedirs(d, exist_ok=True)
        for name in os.listdir(d):
            if name.startswith(_OUTPUT_PREFIX) and name.endswith(".lua"):
                os.remove(os.path.join(d, name))


def export_examples(import_dir, client_dir, server_dir):
    exported, failed = 0, 0
    for path in sorted(list_excel_files(import_dir)):
        for info in load_excel(path):
            result = export_table(path, info, client_dir, server_dir, ENCODING, ENCODING)
            for msg in result.success:
                print("  " + msg)
                exported += 1
            for msg in result.failed:
                print("  FAILED " + msg)
                failed += 1
    return exported, failed


def build_lang(lang):
    import_dir, client_dir, server_dir = lang_dirs(lang)
    print("[%s] generating example workbooks -> %s" % (lang, import_dir))
    build_all(lang, import_dir)

    print("[%s] exporting -> %s , %s" % (lang, client_dir, server_dir))
    clean_outputs(client_dir, server_dir)
    exported, failed = export_examples(import_dir, client_dir, server_dir)
    print("[%s] done: %d file(s) written, %d failed" % (lang, exported, failed))
    return failed


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    langs = [a for a in argv if a in LANGS] or list(LANGS)

    failed = sum(build_lang(lang) for lang in langs)
    print("total: %d language(s) generated, %d failure(s)" % (len(langs), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
