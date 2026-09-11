"""Chinese/English switching for UI text and messages.

It lives in ``core`` because both sides need it: the GUI text in ``gui`` and the
validation messages in ``core.lua_syntax``. This module **does not depend on Tk**,
so plain logic scripts can use it as well.

Conventions:
- **English by default** (open-source project, the README is mainly English).
- Every message is an ``(en, zh)`` tuple; lookup uses the current language and
  falls back to English when it is missing.
- Placeholders use ``str.format``'s ``{name}`` syntax. A literal brace inside a
  message must be doubled (e.g. ``table {{...}}``), otherwise ``format`` treats it
  as a placeholder.
"""

#: Available languages: (key written to the config, name shown in the dropdown).
#: Names are written in their own language and never translated - "Chinese" must be
#: recognisable even while the UI is English.
LANGUAGES = (("en", "English"), ("zh", "中文"))

#: Default language (used when the config has no ``language`` field)
DEFAULT_LANGUAGE = "en"

#: Language key -> index (en=0, zh=1), matching the order of the tuples below
_LANG_INDEX = {"en": 0, "zh": 1}


# ══════════════════════════════════════════════════════════════════
    # Text table
# ══════════════════════════════════════════════════════════════════

_STRINGS = {
        # ── Application / window ────────────────────────────
    "app.title": ("Table Exporter", "导表管理器"),
    "about.title": ("About", "关于"),
    "about.body": (
        "Table Exporter v2.0\nBuilt with ttkbootstrap",
        "导表管理器 v2.0\n基于 ttkbootstrap 构建",
    ),

        # ── Menu bar ─────────────────────────────────────────
    "menu.file": ("File", "文件"),
    "menu.quit": ("Quit  Ctrl+Q", "退出  Ctrl+Q"),
    "menu.export": ("Export", "导出"),
    "menu.export_selected": ("Export Selected  Ctrl+E", "导出选中  Ctrl+E"),
    "menu.export_all": ("Export All  Ctrl+Shift+E", "导出全部  Ctrl+Shift+E"),
    "menu.theme": ("Theme", "主题"),
    "menu.language": ("Language", "语言"),
    "menu.help": ("Help", "帮助"),
    "menu.about": ("About", "关于"),

        # ── Top bar / panel titles ────────────────────────
    "topbar.title": ("📊  Table Exporter", "📊  导表管理器"),
    "panel.tables": ("  Tables  ", "  表格列表  "),
    "panel.projects": ("  Projects  ", "  项目管理  "),
    "label.project": ("Project", "项目"),

        # ── Search and sorting ─────────────────────────────
    "search.placeholder": ("Search tables...", "搜索表格名称..."),
    "sort.name": ("Name", "名称"),
    "sort.time": ("Time", "时间"),

        # ── List state ──────────────────────────────────────
    "list.no_dir": ("📂  Set a table directory first", "📂  请先配置表格目录"),
    "list.no_files": ("📭  No table file found", "📭  未找到表格文件"),
    "list.no_match": ("🔍  No match", "🔍  无匹配结果"),
    "list.count": ("({n} tables)", "({n} 个表格)"),
    "list.count_match": ("({total} tables, {n} matched)", "({total} 个表格, 匹配 {n} 个)"),

        # ── Project configuration ───────────────────────────
    "section.config": ("Project Configuration", "项目配置"),
    "field.project_name": ("Project name", "项目名称"),
    "field.source_dir": ("Table directory", "表格目录"),
    "field.client_output": ("Client output", "客户端输出"),
    "field.server_output": ("Server output", "服务端输出"),
    "btn.browse": ("Browse", "浏览"),

        # ── Quick actions ───────────────────────────────────
    "section.actions": ("Actions", "快捷操作"),
    "btn.save": ("Save", "保存配置"),
    "btn.add_project": ("Add Project", "添加项目"),
    "btn.delete_project": ("Delete Project", "删除项目"),
    "btn.clear_log": ("Clear Log", "清除日志"),

        # ── Log pane / status ──────────────────────────────
    "section.log": ("Log", "日志"),
    "status.ready": ("Ready", "就绪"),
    "status.checking": ("Checking... {i}/{n}", "校验中... {i}/{n}"),
    "status.exporting": ("Exporting... {i}/{n}", "导出中... {i}/{n}"),
    "status.done": ("All succeeded ✓", "全部成功 ✓"),
    "status.done_failed": ("Done ({n} failed)", "完成 ({n} 个失败)"),
    "status.aborted": ("Aborted: {n} data error(s)", "已中断：{n} 处数据错误"),

        # ── Bottom bar buttons ──────────────────────────────
    "btn.select_all": ("Select All", "全选"),
    "btn.invert": ("Invert", "反选"),
    "btn.refresh": ("Refresh", "刷新"),
    "btn.export_selected": ("Export Selected", "导出选中"),
    "btn.export_all": ("Export All", "导出全部"),
    "btn.svn_update": ("Update Tables", "更新表格"),
    "btn.svn_commit": ("Commit Tables", "提交表格"),

        # ── Dialog titles / directory pickers ──────────
    "dlg.error": ("Error", "错误"),
    "dlg.notice": ("Notice", "提示"),
    "dlg.data_error": ("Data Validation Failed", "数据校验失败"),
    "dlg.choose_source": ("Select the table directory", "选择表格目录"),
    "dlg.choose_client": ("Select the client output directory", "选择客户端输出目录"),
    "dlg.choose_server": ("Select the server output directory", "选择服务端输出目录"),

        # ── Prompts and errors ─────────────────────────────
    "msg.project_name_required": ("Project name cannot be empty", "项目名称不能为空"),
    "msg.keep_one_project": ("Keep at least one project", "至少保留一个项目"),
    "msg.no_output_dir": (
        "Set the client or the server output directory first",
        "请先配置客户端或服务端输出目录",
    ),
    "msg.no_source_dir": ("Set the table directory first", "请先配置表格目录"),
    "msg.no_svn": ("svn not found; this action is unavailable", "未找到 svn，无法执行该操作"),
    "msg.terminal_failed": ("Cannot open a terminal: {err}", "无法启动终端: {err}"),
    "msg.svn_commit_failed": ("Cannot start svn commit: {err}", "无法启动 svn commit: {err}"),
    "msg.theme_failed": ("Failed to switch theme: {err}", "切换主题失败: {err}"),
    "msg.no_selection": ("No table selected", "没有选中任何表"),
    "msg.nothing_to_export": ("No table to export", "没有可导出的表"),
    "msg.internal_error": (
        "Something went wrong and the action was stopped:\n\n{err}\n\n"
        "Details were appended to:\n{path}",
        "程序内部出错，本次操作已中止：\n\n{err}\n\n详细信息已追加到：\n{path}",
    ),

        # ── Data error dialog ─────────────────────────────
    "dlg.data_error_header": (
        "Found {n} data error(s). Export aborted; nothing was written to the output directories.",
        "发现 {n} 处数据错误，已中断导出，目标目录没有写入任何文件。",
    ),
    "dlg.data_error_more": ("... {n} more, see the log.", "…… 另有 {n} 处，见日志。"),
    "dlg.data_error_footer": (
        "Fix these cells in the source workbook, then export again.",
        "请先去源表把这些格子改掉，再重新导出。",
    ),

        # ── Log lines ────────────────────────────────────────
    "log.theme_switched": ("Theme switched to {name}", "主题已切换为 {name}"),
    "log.language_switched": ("Language switched to {name}", "语言已切换为 {name}"),
    "log.internal_error": ("Internal error: {err}", "内部错误：{err}"),
    "log.sort_changed": ("Sort by: {label}", "排序方式：{label}"),
    "log.config_saved": ("Configuration saved", "配置已保存"),
    "log.export_start": (
        "Exporting {n} file(s)... (client: {client}, server: {server})",
        "开始导出 {n} 个文件... (客户端: {client}, 服务端: {server})",
    ),
    "log.export_summary": (
        "Export finished: {ok} succeeded, {fail} failed",
        "导出完成: 成功 {ok}, 失败 {fail}",
    ),
    "log.data_error_abort": (
        "Found {n} data error(s); export aborted before writing any file.",
        "发现 {n} 处数据错误，已中断导出（尚未写入任何文件）。",
    ),
    "log.svn_no_tortoise": (
        "TortoiseSVN not found; falling back to the svn command line",
        "未检测到 TortoiseSVN，改用命令行执行 svn update",
    ),
    "log.svn_update_opened": (
        "TortoiseSVN update window opened: {path}",
        "已打开 TortoiseSVN 更新窗口：{path}",
    ),
    "log.svn_commit_opened": (
        "TortoiseSVN commit window opened: {path}",
        "已打开 TortoiseSVN 提交窗口：{path}",
    ),
    "log.svn_terminal": (
        "Running svn {cmd} in Terminal: {path}",
        "已在终端执行 svn {cmd}：{path}",
    ),

        # ── Lua validation: location and category ───────
    "err.kind.lua": ("Lua syntax error", "Lua 语法错误"),
    "err.kind.number": ("Invalid number", "数字格式错误"),
    "err.where_cell": (
        "cell {ref} (row {row}, column {col})",
        "单元格 {ref}（第 {row} 行 {col} 列）",
    ),
    "err.where_row": ("row {row}", "第 {row} 行"),
    "err.where_unknown": ("unknown location", "位置未知"),
    "err.log_line": (
        "{kind}: {file} / {sheet} / {where} / field {field} -> {error}  |  content: {preview}",
        "{kind}: {file} / {sheet} / {where} / 字段 {field} -> {error} ｜ 内容: {preview}",
    ),

        # ── Lua validation: syntax errors ────────────────
    "syn.unclosed_comment": ("Comment block is not closed", "注释块没有闭合"),
    "syn.unclosed_long_string": ("Long string {tok} is not closed", "长字符串 {tok} 没有闭合"),
    "syn.unterminated_string": (
        "String is missing the closing {ch}",
        "字符串缺少结尾的 {ch}",
    ),
    "syn.bad_number": ("Malformed number", "数字写得不对"),
    "syn.bad_char": ("Character not allowed in Lua: {desc}", "出现了 Lua 里不合法的字符 {desc}"),
    "syn.expect_symbol": (
        "Expected '{sym}' here, but found {found}",
        "这里应该是 '{sym}'，实际是 {found}",
    ),
    "syn.trailing": (
        "Extra content after the expression: {found}",
        "表达式后面还有多余的内容 {found}",
    ),
    "syn.no_inline_function": (
        "Inline 'function' definitions are not supported in a config cell",
        "配置单元格里不支持内联 function 定义",
    ),
    "syn.unexpected_keyword": (
        "Keyword {kw} is not allowed here",
        "这里不该出现关键字 {kw}",
    ),
    "syn.expect_value": (
        "Expected a value here (number / string / table {{...}} / variable), but found {found}",
        "这里应该是一个值（数字 / 字符串 / 表格 {{...}} / 变量），实际是 {found}",
    ),
    "syn.expect_field_name": (
        "Expected a field name after '.', but found {found}",
        "'.' 后面应该是字段名，实际是 {found}",
    ),
    "syn.expect_method_name": (
        "Expected a method name after ':', but found {found}",
        "':' 后面应该是方法名，实际是 {found}",
    ),
    "syn.expect_args": (
        "Expected an argument list after the call, but found {found}",
        "函数调用后面应该是参数列表，实际是 {found}",
    ),
    "syn.too_deep": ("Nesting too deep to parse", "嵌套层级过深，无法解析"),
    "syn.with_pos": ("{msg} (position {pos})", "{msg}（位置 {pos}）"),
    "syn.eof": ("end of content", "内容结束"),

        # -- Lua validation: full-width character hints (the most common typo) --
    "char.fullwidth_lparen": (
        "'（' (full-width left parenthesis; use ASCII '(')",
        "'（'（全角左括号，应该用半角 '('）",
    ),
    "char.fullwidth_rparen": (
        "'）' (full-width right parenthesis; use ASCII ')')",
        "'）'（全角右括号，应该用半角 ')'）",
    ),
    "char.fullwidth_lbrace": (
        "'｛' (full-width left brace; use ASCII '{')",
        "'｛'（全角左花括号，应该用半角 '{'）",
    ),
    "char.fullwidth_rbrace": (
        "'｝' (full-width right brace; use ASCII '}')",
        "'｝'（全角右花括号，应该用半角 '}'）",
    ),
    "char.square_bracket": (
        "'{ch}' (wrong bracket; a Lua table uses {{ }})",
        "'{ch}'（方括号不对，Lua 的表格要用 {{ }}）",
    ),
    "char.fullwidth_colon": (
        "'：' (full-width colon; use ASCII ':')",
        "'：'（全角冒号，应该用半角 ':'）",
    ),
    "char.fullwidth_comma": (
        "'{ch}' (full-width comma; use ASCII ',')",
        "'{ch}'（全角逗号，应该用半角 ','）",
    ),
    "char.fullwidth_semicolon": (
        "'；' (full-width semicolon; use ASCII ';')",
        "'；'（全角分号，应该用半角 ';'）",
    ),
    "char.fullwidth_equal": (
        "'＝' (full-width equals sign; use ASCII '=')",
        "'＝'（全角等号，应该用半角 '='）",
    ),
    "char.curly_double_quote": (
        "'{ch}' (curly quote; use ASCII double quote '\"')",
        "'{ch}'（中文引号，应该用半角双引号 '\"'）",
    ),
    "char.curly_single_quote": (
        "'{ch}' (curly quote; use ASCII single quote \"'\")",
        "'{ch}'（中文引号，应该用半角单引号 \"'\"）",
    ),
    "char.fullwidth_space": ("full-width space", "全角空格"),

        # ── Lua validation: number columns ──────────────────
    "num.not_a_number": (
        "Not a valid number (cannot be converted to a Lua number)",
        "不是合法数字（无法转换成 lua 数字）",
    ),
    "num.would_be_nil": (
        "Not a valid number; it would be exported as nil (data loss)",
        "不是合法数字，导出后会变成 nil（数据丢失）",
    ),
    "num.not_a_number_detail": (
        "Not a valid number ({literal})",
        "不是合法数字（{literal}）",
    ),
}


# ══════════════════════════════════════════════════════════════════
# Public interface
# ══════════════════════════════════════════════════════════════════

#: Current language (module-level singleton; set once with set_language at startup)
_language = DEFAULT_LANGUAGE


def normalize(code):
    """把任意输入收敛成受支持的语种键；不认识的一律返回默认语言。"""
    if isinstance(code, str):
        code = code.strip().lower()
        if code in _LANG_INDEX:
            return code
    return DEFAULT_LANGUAGE


def set_language(code):
    """切换语言，返回切换后实际生效的语种键。"""
    global _language
    _language = normalize(code)
    return _language


def get_language():
    """当前语种键（``en`` / ``zh``）。"""
    return _language


def language_label(code=None):
    """语种键 -> 显示名（``en`` -> ``English``）。下拉框里用。"""
    code = normalize(_language if code is None else code)
    for key, label in LANGUAGES:
        if key == code:
            return label
    return code


def t(key, **kwargs):
    """Look up a message. A missing key returns the key itself (so gaps get noticed),
    and a formatting failure also returns the raw text.

    A formatting failure **never raises**, to keep the UI robust: one message with
    a wrong placeholder must not bring the whole export down.
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return key

    text = entry[_LANG_INDEX[_language]] or entry[0] or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def language_from_config(config_path):
    """Read the language preference from ``projects.json``; a missing file or field
    falls back to the default language.

    The default **must** be English - first launch, a deleted config and a broken
    config all end up in English.
    """
    import json
    import os

    if not config_path or not os.path.exists(config_path):
        return DEFAULT_LANGUAGE
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return DEFAULT_LANGUAGE
    if not isinstance(data, dict):
        return DEFAULT_LANGUAGE
    return normalize(data.get("language"))
