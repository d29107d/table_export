# Table Exporter

**English** · [中文](README.zh-CN.md)

A small cross-platform desktop app that turns Excel workbooks (`.xlsx`) into Lua
config tables.

Point it at a directory of workbooks, and it writes the generated `.lua` files to
a **client** directory and/or a **server** directory — each side with its own text
encoding. Fields can be routed to the client only, the server only, or both.

Built with Python + Tkinter (ttkbootstrap). Runs on Windows and macOS.

---

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Run from source](#run-from-source)
- [Using the app](#using-the-app)
- [Excel table format](#excel-table-format)
- [Examples](#examples)
- [Build a standalone executable](#build-a-standalone-executable)
- [Project layout](#project-layout)
- [Notes](#notes)
- [License](#license)

## Features

- **One workbook, many tables** — every sheet is parsed on its own and can produce
  its own `.lua` file.
- **Client / server split** — a per-field `scope` decides where each column goes;
  each side is written with its own encoding (UTF-8 or GBK).
- **Two table shapes** — flat record tables (`base`) and single-record setting
  tables (`tiny`), plus `key_count` for nested output.
- **Validate before writing** — Lua-valued cells are syntax-checked and numeric
  cells are checked to be really numeric. If *anything* is wrong the export is
  cancelled and **no file is written at all**, so you never end up with a
  half-exported directory.
- **Multi-project** — keep several source/output directory sets and switch between
  them; everything is remembered in `config/projects.json`.
- **SVN helpers** — *Update tables* / *Commit tables* buttons (a TortoiseSVN window
  on Windows, Terminal + `svn` on macOS).
- **Light/dark themes and an English / 中文 UI** — switch them from the menu bar;
  both choices stick.

## Requirements

- Python 3.9+ with **Tkinter** (bundled with the official python.org installers and
  with conda; some Linux distros ship it as a separate package)
- [`openpyxl`](https://openpyxl.readthedocs.io/) ≥ 3.0
- [`ttkbootstrap`](https://ttkbootstrap.readthedocs.io/) ≥ 2.2, < 3
  (2.0.1+ is required for Tk 9 / Python 3.14 — 1.x mis-scales widgets and breaks
  scrolling on Tk 9)

## Run from source

```bash
git clone https://github.com/d29107d/table_export.git
cd table_export
python -m pip install -r requirements.txt
python main.py
```

The app opens maximized. The window title, menu bar and every label follow the
selected language; the default is **English**.

## Using the app

1. **Table directory** — the folder holding your `.xlsx` workbooks. Every `.xlsx`
   in that folder (non-recursive, `~$*` temp files skipped) is scanned; the table
   list on the left shows them.
2. **Client output** / **Server output** — where generated Lua files go. Either may
   be left empty; each has its own encoding selector (UTF-8 or GBK).
3. **Export Selected** / **Export All** — analyse, then write.

While exporting, the progress bar runs in two phases: the first half is the
read-only pre-check, the second half is the actual writing. If the pre-check finds
problems the export stops there (see [Data validation](#data-validation)).

Shortcuts: `Ctrl+E` export selected · `Ctrl+Shift+E` export all · `Ctrl+A` select
all · `Ctrl+Shift+A` invert · `F5` refresh · `Ctrl+F` focus search · `Ctrl+S` save
config · `Ctrl+Q` quit. Hovering a button shows its shortcut.
On macOS every one of them except `F5` is bound to `⌘` as well (`⌘E`, `⌘⇧E`, `⌘A`,
`⌘⇧A`, `⌘F`, `⌘S`, `⌘Q`); the `Ctrl` bindings stay, so either habit works.

The table list can be sorted by **name** or by **modification time**, and filtered
with the search box.

**Language and theme** live in the menu bar: `File / Export / Theme / Language / Help`.
Pick `English` or `中文` from the `Language` menu — the whole UI switches instantly
and the choice is written to `config/projects.json`.

### Where settings are stored

| Platform | Location |
|---|---|
| Windows | `config/` next to `table_exporter.exe` (or the source root when running `python main.py`) |
| macOS | `~/Library/Application Support/table_exporter/` |

`projects.json` holds the project list, the active project, the table-list sort
order and the UI language. `theme.json` holds the theme. Delete them to start over.

## Excel table format

Each **sheet** is one table. A sheet is treated as a config table only when:

- `B1` is exactly `base` or `tiny`, **and**
- `B2` is a file name ending in `.lua`

Anything else — a cover sheet, an empty sheet, a notes sheet — is silently
ignored, so you can keep documentation in the same workbook.

### Rows 1–3: table metadata

| Cell | Meaning | Example |
|---|---|---|
| `B1` | Table kind: `base` or `tiny` | `base` |
| `B2` | Output file name (must end with `.lua`) | `cfg_item.lua` |
| `B3` | `key_count` — how many fields form the nested key (`base` only) | `1` |
| `E1` | File header — written before the table | `return {` |
| `E2` | File footer — written at the end of the file | `}` |

`A1`, `A2`, `A3`, `D1`, `D2` are just labels for humans; the parser reads the `B`
and `E` cells.

The header and footer are free text: `local cfg = {` + `}` / `return cfg` produces
a file wrapped in a local variable.

### `base` tables — rows 5–8 describe the columns, data starts at row 9

| Row | Meaning |
|---|---|
| 5 | Comment for this column (for humans) |
| 6 | `scope` — `c` / `s` / `sc` / `cs` (empty means `c`) |
| 7 | Type — `number` / `string` / `table` / `any` (empty means `string`) |
| 8 | Field name (must be a valid Lua identifier) |
| 9+ | One record per row |

The generated file looks like this (for `key_count = 1`):

```lua
return {		--[[ cfg_item.xlsx -> Item ]]
[1] = {
	id = 1,
	name = [[Sword]],
	weight = 100,
},
[2] = {
	id = 2,
	name = [[Shield]],
	weight = 80,
},
}
```

### `tiny` tables — rows 6+ are one field each

| Column | Meaning |
|---|---|
| `A` | Comment (for humans) |
| `B` | `scope` |
| `C` | Type |
| `D` | Field name |
| `E` | Value |

Row 5 holds the headings. The field area **ends at the first blank row**: nothing below
the gap is imported, even when it looks like more fields. If row 6 itself is blank the
table has no field at all and no file is written. This is the same rule as `base` — an
empty row 9 means "no file", and the first empty data row ends the table. The output is
a single flat table:

```lua
return {		--[[ cfg_setting.xlsx -> Settings ]]
	free_revive_count = 3,
	entrance_npc = {10000, "NPC", 1503},
	notice = [[Welcome]],
}
```

### Field types

Type the **plain value** into the cell — the exporter adds whatever Lua syntax is
needed (long-string delimiters, quotes). You never write `[[ ]]` yourself.

| Type | What you type in the cell | What lands in the `.lua` | Notes |
|---|---|---|---|
| `number` | `12` | `12` | A value that cannot be converted to a number would silently become `nil`, so it is **reported as an error** instead. |
| `string` | `Sword` — plain text, **do not** add `[[ ]]` | `[[Sword]]` | The exporter wraps the text in a long string for you, and nothing inside needs escaping. If the text contains `]`, the bracket level is raised (`[[` → `[=[` → `[==[` …) so the result stays valid Lua. |
| `table` | `{{1001, 2}, {1002, 1}}` | `{{1001, 2}, {1002, 1}}` | You write the Lua table yourself; the contents are **syntax-checked**. |
| `any` | `nil` / `true` / `100+50` / `{quality=3}` | same as typed | Same as `table`, but plain numbers and booleans also pass through. |

> **`string` cells take plain text only.** If you add `[[ ]]` by hand they become part
> of the value: the writer still escapes the content correctly, but the exported string
> literally contains the brackets.

### Scopes

| Value | Meaning |
|---|---|
| `c` | client only (also the default when the cell is empty) |
| `s` | server only |
| `sc` / `cs` | both — the two spellings are equivalent |

A field excluded by scope is simply absent from that side's file. If that leaves a
sheet with nothing to write, no file is produced for it.

### `key_count`

| `key_count` | Output shape |
|---|---|
| `0` | a list — each row is one anonymous `{ ... },` element |
| `1` | `[k1] = { ... },` |
| `2` | `[k1] = { [k2] = { ... } },` |
| `n` | nested `n` levels deep |

The keys are the first `key_count` fields *that have a valid field name* — scope
filtering does **not** apply to keys, so a key column marked `s` still keys the
client file. If the same key combination appears twice, the later row wins (that is
what Lua does with duplicate table keys).

### Rules that surprise people

- **Columns with a non-identifier name are dropped.** Anything to the right of the
  real table (stray notes, a duplicated `id`, a lone `{` or `★`) is not exported.
  This is why a column's field name must look like `some_field`.
- **The last column comes from row 5.** The right-most non-empty cell of row 5 defines
  the column range (rows 7/6/8 are only consulted if row 5 is completely empty).
  Rows 6–8 can never extend the range, so a stray field name to the right of the
  table is ignored.
- **A break at column E drops E and everything to its right.** If column E has an
  empty header block (rows 5–8 all blank) while columns A–D are complete, then E
  onwards are treated as free-form notes. It is a common way to park annotations
  to the right of a table.
- **An empty data area means "do not export".** If row 9 is empty across the valid
  columns, the whole table is skipped and no file is generated — even if rows 10+
  have data. (Write your first record in row 9.)
- **A blank row ends a `tiny` table.** The field area stops at the first blank row
  below row 6, so anything written further down is ignored — it does not matter that
  the cells still look like fields. A blank row 6 means the sheet has no fields at
  all and produces no file. This mirrors `base`, which ends at its first empty data
  row.
- **Empty cell ≠ blank text.** An empty cell makes the field *disappear from that
  record*. A cell containing only whitespace (or an empty string) is a value, and
  is written according to its type: `number`/`any` → `nil`, `table` → `{}`,
  `string` → `[[ ]]`.
- **Line endings** in generated files are always CRLF.

### Data validation

Before anything is written, the tool parses every `table` / `any` cell as a Lua
expression (a hand-written recursive-descent parser using Lua 5.1 operator
precedence) and checks every `number` cell with the very same routine that formats
it — so "passes validation" and "exports as a number" can never disagree.

Full-width punctuation is the most common data-entry mistake in CJK workbooks, so
those cases get an explicit message (`'，' is a full-width comma; use ','`).

If any cell fails:

- the export is **cancelled** — not a single file is written, and existing files are
  left untouched;
- a dialog lists the first 10 problems as `file / sheet / cell address / field -> reason`;
- the log holds the complete list, including the offending cell's contents.

There is deliberately no "force export anyway" button.

## Examples

`example/` contains a runnable sample set, provided in two languages. Both sets
have the same structure and the same field names — only the human-readable text
(comments, sample data, `tiny` sheet headers) differs.

```
example/
├── en/
│   ├── import/     source workbooks
│   ├── client/     generated for the client
│   └── server/     generated for the server
└── zh-CN/          the same set with Chinese comments and sample data
```

| Workbook | Shows |
|---|---|
| `01_types_and_scopes.xlsx` | all four field types × all four scopes, two sheets in one workbook |
| `02_keys_and_layout.xlsx` | `key_count` 0 / 1 / 2, plus a notes sheet and an empty table that are both skipped |
| `03_tiny_config.xlsx` | the `tiny` layout — one record per file, no blank row in between |
| `04_edge_cases.xlsx` | multi-line text, text containing `]]`, text ending with `]`, the empty-cell vs blank-text contrast, booleans, scientific notation, a custom file header/footer, plus the two `tiny` blank-row cases (`EarlyStop` stops at the gap, `NoFirstRow` writes no file) |

Open the workbooks next to their generated `.lua` output — e.g.
`en/import/01_types_and_scopes.xlsx` → `en/client/cfg_example_item.lua`. The same
table produces different files on each side because of `scope`.

Regenerate them with:

```bash
python tools/make_examples.py            # both languages
python tools/make_examples.py en         # English only
python tools/make_examples.py zh-CN      # Chinese only
```

The generator script doubles as executable documentation of the layout.

## Build a standalone executable

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean table_exporter.spec
```

The result is `dist/table_exporter.exe` (Windows) or `dist/table_exporter`
(macOS).

Notes:

- Use a Python that has Tkinter. On Windows the official installer and conda both
  do; a bare `venv` created from a portable Python may not.
- `table_exporter.spec` resolves the Python installation from `sys.base_prefix`, so
  it is not tied to any particular machine.
- If a `config/` directory exists next to the spec, it is bundled as a fallback
  template for the first run; that is optional.

## Project layout

```
main.py                     entry point
core/
  excel_reader.py           workbook -> table dicts, plus cell validation
  lua_writer.py             table dict -> Lua source (field formatting, nesting)
  exporter.py               writes the .lua files (encoding, CRLF)
  lua_syntax.py             Lua literal checker + number checker + error rendering
  i18n.py                   English / Chinese strings (no Tk dependency)
gui/
  main_window.py            the Tkinter UI
  platform_compat.py        Windows / macOS differences (fonts, config dir, svn)
tools/
  make_examples.py          regenerates example/ (en / zh-CN)
  compare_export.py         structural diff of two export directories
  luaparse.py               minimal Lua parser used by compare_export.py
example/                    sample workbooks and their generated output (en / zh-CN)
table_exporter.spec         PyInstaller build description
```

`tools/` is developer tooling and is not packaged into the executable.

## Notes

- Generated files are plain text; re-running an export overwrites them.
- **Writes are all-or-nothing.** The text is encoded in memory and then swapped in
  with an atomic replace, so a failed write leaves the previous file untouched.
  If a sheet holds a character the chosen encoding cannot store (for example `·`
  with GB2312), the export reports the sheet, the output file and the offending
  character instead of dumping a codec error - and never truncates the file.
- Only two output encodings are offered: `utf-8` and `gbk`. `gbk` covers
  Simplified Chinese game data; pick `utf-8` when the pipeline expects it.
- `tools/compare_export.py` compares two output directories by *structure* rather
  than bytes, which is how the exporter was verified against a legacy tool:
  ```bash
  python tools/compare_export.py <source-dir> <client-dir> <server-dir>
  ```
  With no arguments it reads `tools/compare_paths.json` (not tracked by git), then
  falls back to environment variables `COMPARE_SRC` / `COMPARE_CLI` / `COMPARE_SRV`.

## License

[MIT](LICENSE)
