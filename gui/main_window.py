import os
import sys
import json
import shutil
import subprocess
import tkinter as tk
from tkinter import messagebox, filedialog
from datetime import datetime

import ttkbootstrap as ttk
from ttkbootstrap.constants import PRIMARY, SECONDARY, SUCCESS, INFO, WARNING, DANGER, OUTLINE

from core.excel_reader import list_excel_files, load_excel
from core.exporter import export_table
from core.lua_syntax import format_syntax_errors
from gui.platform_compat import (
    HAS_TOUCHPAD_SCROLL,
    IS_MAC,
    IS_WIN,
    TOUCHPAD_STEP,
    bundled_config_dir,
    config_dir,
    find_svn,
    resolve_font_families,
    run_svn_in_terminal,
    touchpad_dy,
    wheel_units,
)


def _find_tortoise_proc():
    """定位 TortoiseSVN 的 GUI 进程 TortoiseProc.exe；找不到返回 None。

    查找顺序：PATH → 注册表（TortoiseSVN 安装时写入的 ``ProcPath``）→ 常见安装目录。
    用它执行 update/commit 就会弹出 TortoiseSVN 自己的窗口，而不是命令行终端。

    非 Windows 直接返回 None —— 省掉 macOS 上那次注定 ImportError 的 ``winreg``。
    """
    if not IS_WIN:
        return None

    exe = shutil.which("TortoiseProc.exe")
    if exe:
        return exe

    try:
        import winreg
        for root, sub in ((winreg.HKEY_CURRENT_USER, r"Software\TortoiseSVN"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\TortoiseSVN"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\WOW6432Node\TortoiseSVN")):
            try:
                with winreg.OpenKey(root, sub) as k:
                    path, _ = winreg.QueryValueEx(k, "ProcPath")
                if path and os.path.isfile(path):
                    return path
            except OSError:
                continue
    except Exception:
        pass

    locals_dir = os.environ.get("LOCALAPPDATA", "")
    for base in (os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)"),
                 os.path.join(locals_dir, "Programs") if locals_dir else None):
        if not base:
            continue
        candidate = os.path.join(base, "TortoiseSVN", "bin", "TortoiseProc.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


#: 运行期配置目录。Windows 是 exe 同级的 config/（原样）；macOS 换到
#: ~/Library/Application Support/ —— .app 内部是会签名的区域，不能往里写。
CONFIG_DIR = config_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "projects.json")
#: 打包进包里的兜底配置（首次启动当模板）
FALLBACK_CONFIG = os.path.join(bundled_config_dir(), "projects.json")
THEME_PATH = os.path.join(CONFIG_DIR, "theme.json")


def load_projects():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    if os.path.exists(FALLBACK_CONFIG):
        with open(FALLBACK_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"projects": {"default": {"source_dir": "", "client_output_dir": "", "server_output_dir": ""}}, "active": "default"}


def save_projects(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Theme detection ──────────────────────────────────────────────

DARK_THEMES = {"darkly", "cyborg", "solar", "superhero", "vapor", "simplex"}


def _is_dark_theme(name):
    """判断主题是否为暗色。

    ``DARK_THEMES`` 是 ttkbootstrap 1.x 的遗留名 —— 2.2 里仍能用，但会打弃用警告，
    且**不再出现在 ``theme_names()`` 里**。2.x 起主题改用 ``xxx-light`` /
    ``xxx-dark`` 的命名约定（bootstrap-dark、nord-dark…），这些新暗色主题都不在
    ``DARK_THEMES`` 中。只按旧集合判断的话，选了新暗色主题 ``self._is_dark`` 仍是
    False，应用手绘的控件（Canvas / 列表行 / 日志 Text / 搜索框…）不会跟着变暗，
    和 ttk 控件配色对不上。
    """
    return name in DARK_THEMES or name.endswith("-dark")

# ── Fonts ────────────────────────────────────────────────────────
#
# 下面是占位值，真正的族名由 _init_fonts() 在 __init__ 里按平台探测后覆盖
# （探测要先有 Tk root）。字号不需要按平台区分 —— Tk 9 已把 macOS 的 dpi 基准
# 从 72 对齐到 96，实测 tk scaling = 1.334，与 Windows 一致。

FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_MONO = ("Consolas", 10)


def _init_fonts(root):
    """按平台解析字体族名，覆盖上面那组常量。

    Windows 上探测到 ``Microsoft YaHei UI`` / ``Consolas``，结果与写死时一致；
    macOS 上换成 ``PingFang SC`` / ``Menlo``。写死会在 macOS 上整片静默回退，
    中文界面变默认字体、日志区失去等宽对齐，而且不报任何错。
    """
    global FONT, FONT_BOLD, FONT_TITLE, FONT_SMALL, FONT_MONO
    ui, mono = resolve_font_families(root)
    FONT = (ui, 10)
    FONT_BOLD = (ui, 10, "bold")
    FONT_TITLE = (ui, 15, "bold")
    FONT_SMALL = (ui, 9)
    FONT_MONO = (mono, 10)

#: 表格列表排序方式：(写进配置的键, 下拉框显示文案)
SORT_OPTIONS = (("name", "名称"), ("time", "时间"))
#: 默认按名称排序
DEFAULT_SORT = "name"
_SORT_KEY2LABEL = {k: label for k, label in SORT_OPTIONS}
_SORT_LABEL2KEY = {label: k for k, label in SORT_OPTIONS}

#: 数据错误弹窗里最多列几条明细（`messagebox` 不能滚动，列太多会撑到屏幕外）
_DIALOG_MAX_ERRORS = 10


class MainWindow:
    def __init__(self, root, initial_theme="bootstrap-light"):
        self.root = root
        self.current_theme = initial_theme
        self.projects_data = load_projects()
        self.file_paths = []
        self._loading = False
        #: 触摸板滚动的像素累积器（Windows 用不到，Tk 8.6 也不会写它）
        self._touchpad_accum = 0.0

        # 必须在任何 _build_* 之前 —— 那些方法直接引用模块级的 FONT 常量
        _init_fonts(root)

        self._is_dark = _is_dark_theme(initial_theme)
        self._update_theme_colors()
        self.root.configure(bg=self.BG)

        self._tk_widgets = []  # (widget, attr_name) pairs for theme updates

        self._build_menu()
        self._build_layout()
        self._bind_shortcuts()
        self._load_project_config()

    # ── Theme colors ─────────────────────────────────────────────

    def _update_theme_colors(self):
        if self._is_dark:
            self.BG = "#1E1E1E"
            self.CARD = "#2D2D2D"
            self.TEXT = "#E0E0E0"
            self.TEXT_SEC = "#888888"
            self.BORDER = "#404040"
            self.ROW_EVEN = "#2A2A2A"
            self.ROW_ODD = "#2D2D2D"
            self.LOG_BG = "#1A1A1A"
            self.SEARCH_BG = "#333333"
            self.ACCENT = "#0D6EFD"
            self.HOVER = "#1A3A5C"
            self.SUCCESS_C = "#28A745"
            self.ERROR_C = "#DC3545"
            self.INFO_C = "#17A2B8"
        else:
            self.BG = "#F0F2F5"
            self.CARD = "#FFFFFF"
            self.TEXT = "#212529"
            self.TEXT_SEC = "#6C757D"
            self.BORDER = "#DEE2E6"
            self.ROW_EVEN = "#F8F9FA"
            self.ROW_ODD = "#FFFFFF"
            self.LOG_BG = "#F8F9FA"
            self.SEARCH_BG = "#F8F9FA"
            self.ACCENT = "#0D6EFD"
            self.HOVER = "#E8F0FE"
            self.SUCCESS_C = "#28A745"
            self.ERROR_C = "#DC3545"
            self.INFO_C = "#17A2B8"

    def _apply_theme_colors(self):
        """Update all tk widget colors after a theme switch."""
        self.root.configure(bg=self.BG)

        # Update menu colors
        self._update_menu_colors(self._menubar)

        # Update canvas & list frame
        if hasattr(self, 'list_canvas'):
            self.list_canvas.configure(bg=self.CARD)
        if hasattr(self, 'list_frame'):
            self.list_frame.configure(bg=self.CARD)

        # Update search area
        if hasattr(self, 'search_entry'):
            self.search_entry.configure(bg=self.SEARCH_BG, fg=self.TEXT, insertbackground=self.TEXT)
        if hasattr(self, 'search_inner'):
            self.search_inner.configure(bg=self.SEARCH_BG, highlightbackground=self.BORDER)
        if hasattr(self, 'search_icon_label'):
            self.search_icon_label.configure(bg=self.SEARCH_BG, fg=self.TEXT_SEC)

        # Update placeholder color
        if hasattr(self, 'search_var'):
            if self.search_var.get() == "搜索表格名称...":
                self.search_entry.configure(fg=self.TEXT_SEC)

        # Update log text
        if hasattr(self, 'log_text'):
            self.log_text.configure(bg=self.LOG_BG, fg=self.TEXT, insertbackground=self.TEXT)
            self.log_text.tag_config("success", foreground=self.SUCCESS_C)
            self.log_text.tag_config("error", foreground=self.ERROR_C)
            self.log_text.tag_config("info", foreground=self.INFO_C)

        # Update theme toggle button
        if hasattr(self, 'theme_toggle_btn'):
            self.theme_toggle_btn.configure(text="🌙" if not self._is_dark else "☀️")

        # Rebuild checkbox list with new row colors
        self._rebuild_checkbox_list()

    def _update_menu_colors(self, menu):
        try:
            menu.configure(bg=self.CARD, fg=self.TEXT,
                           activebackground=self.ACCENT, activeforeground="white")
        except Exception:
            pass
        last = menu.index(tk.END)
        if last is not None:
            for i in range(last + 1):
                try:
                    sub = menu.nametowidget(menu.entrycget(i, "menu"))
                    if sub:
                        self._update_menu_colors(sub)
                except Exception:
                    pass

    # ── Menu ─────────────────────────────────────────────────────

    def _build_menu(self):
        self._menubar = tk.Menu(self.root, font=FONT_SMALL, bg=self.CARD, fg=self.TEXT,
                                activebackground=self.ACCENT, activeforeground="white", borderwidth=0)
        self.root.config(menu=self._menubar)

        file_menu = tk.Menu(self._menubar, tearoff=0, font=FONT_SMALL, bg=self.CARD, fg=self.TEXT,
                            activebackground=self.ACCENT, activeforeground="white", borderwidth=0)
        file_menu.add_command(label="退出  Ctrl+Q", command=self.root.quit)
        self._menubar.add_cascade(label="文件", menu=file_menu)

        export_menu = tk.Menu(self._menubar, tearoff=0, font=FONT_SMALL, bg=self.CARD, fg=self.TEXT,
                              activebackground=self.ACCENT, activeforeground="white", borderwidth=0)
        export_menu.add_command(label="导出选中  Ctrl+E", command=self._export_selected)
        export_menu.add_command(label="导出全部  Ctrl+Shift+E", command=self._export_all)
        self._menubar.add_cascade(label="导出", menu=export_menu)

        self.theme_menu = tk.Menu(self._menubar, tearoff=0, font=FONT_SMALL, bg=self.CARD, fg=self.TEXT,
                                  activebackground=self.ACCENT, activeforeground="white", borderwidth=0)
        self._menubar.add_cascade(label="主题", menu=self.theme_menu)
        self._rebuild_theme_menu()

        help_menu = tk.Menu(self._menubar, tearoff=0, font=FONT_SMALL, bg=self.CARD, fg=self.TEXT,
                            activebackground=self.ACCENT, activeforeground="white", borderwidth=0)
        help_menu.add_command(label="关于", command=lambda: messagebox.showinfo(
            "关于", "导表管理器 v2.0\n基于 ttkbootstrap 构建"))
        self._menubar.add_cascade(label="帮助", menu=help_menu)

    def _rebuild_theme_menu(self):
        self.theme_menu.delete(0, tk.END)
        style = ttk.Style()
        for name in style.theme_names():
            self.theme_menu.add_radiobutton(
                label=name,
                command=lambda n=name: self._switch_theme(n),
                variable=tk.StringVar(value=self.current_theme),
                value=name,
            )

    def _switch_theme(self, name):
        try:
            ttk.Style().theme_use(name)
            self.current_theme = name
            self._is_dark = _is_dark_theme(name)
            self._update_theme_colors()
            self._save_theme(name)
            self._apply_theme_colors()
            self._rebuild_theme_menu()
            self._log(f"主题已切换为 {name}", "info")
        except Exception as e:
            messagebox.showerror("错误", f"切换主题失败: {e}")

    def _toggle_dark_mode(self):
        """在 2.x 的浅色 / 暗色主题间切换。

        刻意不走 legacy 的 litera / darkly：那两个名字在 2.2 会打
        DeprecationWarning，且官方计划在 3.0 移除。
        """
        target = "bootstrap-dark" if not self._is_dark else "bootstrap-light"
        self._switch_theme(target)

    def _save_theme(self, name):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(THEME_PATH, "w", encoding="utf-8") as f:
            json.dump({"theme": name}, f)

    # ── Shortcuts ────────────────────────────────────────────────

    def _bind_shortcuts(self):
        self.root.bind("<Control-a>", lambda e: self._select_all())
        self.root.bind("<Control-A>", lambda e: self._select_all())
        self.root.bind("<Control-Shift-A>", lambda e: self._deselect_all())
        self.root.bind("<Control-e>", lambda e: self._export_selected())
        self.root.bind("<Control-E>", lambda e: self._export_selected())
        self.root.bind("<Control-Shift-E>", lambda e: self._export_all())
        self.root.bind("<F5>", lambda e: self._refresh_table_list())
        self.root.bind("<Control-q>", lambda e: self.root.quit())
        self.root.bind("<Control-Q>", lambda e: self.root.quit())
        self.root.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.root.bind("<Control-F>", lambda e: self.search_entry.focus_set())

    # ── Layout ───────────────────────────────────────────────────

    def _build_layout(self):
        self._build_top_bar()
        self._build_bottom_bar()

        main_paned = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=4)

        left_frame = ttk.Frame(main_paned)
        right_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=4)
        main_paned.add(right_frame, weight=5)

        self._build_table_panel(left_frame)
        self._build_project_panel(right_frame)

    def _build_top_bar(self):
        top_bar = ttk.Frame(self.root)
        top_bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 0))

        ttk.Label(top_bar, text="📊  导表管理器", font=FONT_TITLE,
                  bootstyle=PRIMARY).pack(side=tk.LEFT, padx=12, pady=6)

        self.theme_toggle_btn = ttk.Button(
            top_bar,
            text="🌙" if not self._is_dark else "☀️",
            bootstyle=(SECONDARY, OUTLINE),
            command=self._toggle_dark_mode,
            width=5,
        )
        self.theme_toggle_btn.pack(side=tk.RIGHT, padx=8, pady=4)

    def _build_bottom_bar(self):
        bottom = ttk.Frame(self.root)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 8))

        left_group = ttk.Frame(bottom)
        left_group.pack(side=tk.LEFT, padx=2, pady=4)
        ttk.Button(left_group, text="全选", bootstyle=(SECONDARY, OUTLINE),
                   command=self._select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_group, text="反选", bootstyle=(SECONDARY, OUTLINE),
                   command=self._deselect_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_group, text="刷新", bootstyle=(SECONDARY, OUTLINE),
                   command=self._refresh_table_list).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bottom, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=6)

        center_group = ttk.Frame(bottom)
        center_group.pack(side=tk.LEFT, padx=2, pady=4)
        ttk.Button(center_group, text="导出选中", bootstyle=SUCCESS,
                   command=self._export_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(center_group, text="导出全部", bootstyle=PRIMARY,
                   command=self._export_all).pack(side=tk.LEFT, padx=2)

        # macOS 上没装 svn 时，这两个按钮点了只会弹错误 —— 直接不显示，连分隔线
        # 一起省掉。Windows 保持原样（那边靠 TortoiseSVN 或系统 svn，检测方式不同）。
        if IS_WIN or find_svn():
            ttk.Separator(bottom, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=6)

            svn_group = ttk.Frame(bottom)
            svn_group.pack(side=tk.LEFT, padx=2, pady=4)
            ttk.Button(svn_group, text="更新表格", bootstyle=(INFO, OUTLINE),
                       command=self._svn_update).pack(side=tk.LEFT, padx=2)
            ttk.Button(svn_group, text="提交表格", bootstyle=(INFO, OUTLINE),
                       command=self._svn_commit).pack(side=tk.LEFT, padx=2)

    # ── Table list panel (left) ──────────────────────────────────

    def _build_table_panel(self, parent):
        card = ttk.LabelFrame(parent, text="  表格列表  ", bootstyle=PRIMARY)
        card.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Header: count + project selector
        header_frame = ttk.Frame(card)
        header_frame.pack(fill=tk.X, padx=14, pady=(10, 4))

        self.table_count_label = ttk.Label(header_frame, text="", font=FONT_SMALL,
                                           bootstyle=SECONDARY)
        self.table_count_label.pack(side=tk.LEFT)

        ttk.Label(header_frame, text="项目", font=FONT_SMALL,
                  bootstyle=SECONDARY).pack(side=tk.RIGHT, padx=(0, 4))
        self.project_combo = ttk.Combobox(header_frame, state="readonly", width=16, font=FONT)
        self.project_combo.pack(side=tk.RIGHT)
        self.project_combo.bind("<<ComboboxSelected>>", self._on_project_switch)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=14, pady=(2, 4))

        # Search bar（搜索框 + 排序方式下拉框）
        search_frame = ttk.Frame(card)
        search_frame.pack(fill=tk.X, padx=14, pady=(0, 6))

        # 先 pack 右侧固定宽度的下拉框，搜索框再 fill+expand 剩下的空间 ——
        # 这样搜索框会自动变窄，把位置让给排序下拉框
        self.sort_var = tk.StringVar(
            value=self._sort_label(self.projects_data.get("sort_by", DEFAULT_SORT)))
        self.sort_combo = ttk.Combobox(
            search_frame, textvariable=self.sort_var, state="readonly",
            width=6, font=FONT_SMALL, values=[label for _, label in SORT_OPTIONS])
        self.sort_combo.pack(side=tk.RIGHT, padx=(6, 0))
        self.sort_combo.bind("<<ComboboxSelected>>", self._on_sort_change)

        self.search_inner = tk.Frame(search_frame, bg=self.SEARCH_BG,
                                     highlightbackground=self.BORDER, highlightthickness=1)
        self.search_inner.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.search_icon_label = tk.Label(self.search_inner, text=" 🔍 ", font=FONT,
                                          fg=self.TEXT_SEC, bg=self.SEARCH_BG)
        self.search_icon_label.pack(side=tk.LEFT, padx=(4, 0))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        self.search_entry = tk.Entry(self.search_inner, textvariable=self.search_var, font=FONT,
                                     fg=self.TEXT, bg=self.SEARCH_BG, relief=tk.FLAT, bd=0,
                                     highlightthickness=0, insertbackground=self.TEXT)
        _setup_placeholder(self.search_entry, self.search_var, "搜索表格名称...",
                           self.TEXT, self.TEXT_SEC)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=6)

        # Scrollable list
        list_container = ttk.Frame(card)
        list_container.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))

        self.list_canvas = tk.Canvas(list_container, highlightthickness=0, bg=self.CARD)
        scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL,
                                  command=self.list_canvas.yview)
        self.list_frame = tk.Frame(self.list_canvas, bg=self.CARD)

        self.list_frame.bind("<Configure>",
                             lambda e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw", tags="inner")
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        self.list_canvas.bind("<Configure>", self._on_canvas_resize)

        # 先 pack 滚动条占住右侧固定宽度，再 pack 画布吃掉剩余空间。
        # 顺序反过来的话，expand=True 的画布会先把横向空间全部占满，
        # 滚动条被挤成 0 宽而看不见。
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.list_scrollbar = scrollbar

        self.list_canvas.bind("<Enter>", self._bind_mousewheel)
        self.list_canvas.bind("<Leave>", self._unbind_mousewheel)
        self.list_frame.bind("<Enter>", self._bind_mousewheel)
        self.list_frame.bind("<Leave>", self._unbind_mousewheel)

    def _on_canvas_resize(self, event):
        self.list_canvas.itemconfig("inner", width=event.width)

    # ── Project panel (right) ────────────────────────────────────

    def _build_project_panel(self, parent):
        card = ttk.LabelFrame(parent, text="  项目管理  ", bootstyle=PRIMARY)
        card.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ── Config section ──
        config_card = ttk.LabelFrame(card, text="项目配置", bootstyle=SECONDARY)
        config_card.pack(fill=tk.X, padx=14, pady=(10, 4))

        ttk.Label(config_card, text="项目名称", font=FONT).grid(
            row=0, column=0, sticky=tk.W, padx=12, pady=(12, 2))
        self.proj_name_var = tk.StringVar()
        ttk.Entry(config_card, textvariable=self.proj_name_var, font=FONT).grid(
            row=1, column=0, sticky=tk.EW, padx=12, pady=(0, 8))

        ttk.Label(config_card, text="表格目录", font=FONT).grid(
            row=2, column=0, sticky=tk.W, padx=12, pady=(2, 2))
        row2 = ttk.Frame(config_card)
        row2.grid(row=3, column=0, sticky=tk.EW, padx=12, pady=(0, 8))
        self.source_dir_var = tk.StringVar()
        self.source_dir_var.trace_add("write", self._on_dir_change)
        ttk.Entry(row2, textvariable=self.source_dir_var, font=FONT).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row2, text="浏览", bootstyle=(SECONDARY, OUTLINE),
                   width=6, command=self._browse_source).pack(side=tk.LEFT, padx=(6, 0))

        # Client output
        row_label = ttk.Frame(config_card)
        row_label.grid(row=4, column=0, sticky=tk.W, padx=12, pady=(2, 2))
        ttk.Label(row_label, text="客户端输出", font=FONT).pack(side=tk.LEFT)
        self.client_encoding_var = tk.StringVar(value="utf-8")
        self.client_encoding_combo = ttk.Combobox(
            row_label, textvariable=self.client_encoding_var, font=FONT_SMALL,
            values=["utf-8", "utf-8-sig", "gb2312", "gbk", "gb18030"],
            state="readonly", width=14)
        self.client_encoding_combo.pack(side=tk.LEFT, padx=(10, 0))
        self.client_encoding_var.trace_add("write", self._on_dir_change)

        row5 = ttk.Frame(config_card)
        row5.grid(row=5, column=0, sticky=tk.EW, padx=12, pady=(0, 8))
        self.client_dir_var = tk.StringVar()
        self.client_dir_var.trace_add("write", self._on_dir_change)
        ttk.Entry(row5, textvariable=self.client_dir_var, font=FONT).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row5, text="浏览", bootstyle=(SECONDARY, OUTLINE),
                   width=6, command=self._browse_client).pack(side=tk.LEFT, padx=(6, 0))

        # Server output
        row_label2 = ttk.Frame(config_card)
        row_label2.grid(row=6, column=0, sticky=tk.W, padx=12, pady=(2, 2))
        ttk.Label(row_label2, text="服务端输出", font=FONT).pack(side=tk.LEFT)
        self.server_encoding_var = tk.StringVar(value="utf-8")
        self.server_encoding_combo = ttk.Combobox(
            row_label2, textvariable=self.server_encoding_var, font=FONT_SMALL,
            values=["utf-8", "utf-8-sig", "gb2312", "gbk", "gb18030"],
            state="readonly", width=14)
        self.server_encoding_combo.pack(side=tk.LEFT, padx=(10, 0))
        self.server_encoding_var.trace_add("write", self._on_dir_change)

        row7 = ttk.Frame(config_card)
        row7.grid(row=7, column=0, sticky=tk.EW, padx=12, pady=(0, 12))
        self.server_dir_var = tk.StringVar()
        self.server_dir_var.trace_add("write", self._on_dir_change)
        ttk.Entry(row7, textvariable=self.server_dir_var, font=FONT).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row7, text="浏览", bootstyle=(SECONDARY, OUTLINE),
                   width=6, command=self._browse_server).pack(side=tk.LEFT, padx=(6, 0))

        config_card.columnconfigure(0, weight=1)

        # ── Action buttons ──
        action_card = ttk.LabelFrame(card, text="快捷操作", bootstyle=SECONDARY)
        action_card.pack(fill=tk.X, padx=14, pady=4)

        btn_frame = ttk.Frame(action_card)
        btn_frame.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(btn_frame, text="保存配置", bootstyle=PRIMARY,
                   command=self._save_project).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="添加项目", bootstyle=(INFO, OUTLINE),
                   command=self._add_project).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="删除项目", bootstyle=(DANGER, OUTLINE),
                   command=self._delete_project).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="清除日志", bootstyle=(SECONDARY, OUTLINE),
                   command=self._clear_log).pack(side=tk.LEFT, padx=3)

        # ── Log section ──
        log_card = ttk.LabelFrame(card, text="日志", bootstyle=SECONDARY)
        log_card.pack(fill=tk.BOTH, expand=True, padx=14, pady=(4, 10))

        log_inner = ttk.Frame(log_card)
        log_inner.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))

        self.log_text = tk.Text(log_inner, wrap=tk.WORD, state=tk.DISABLED, font=FONT_MONO,
                                bg=self.LOG_BG, fg=self.TEXT, relief=tk.FLAT, borderwidth=0,
                                padx=10, pady=8, highlightthickness=0, insertbackground=self.TEXT)
        log_scroll = ttk.Scrollbar(log_inner, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_config("success", foreground=self.SUCCESS_C)
        self.log_text.tag_config("error", foreground=self.ERROR_C)
        self.log_text.tag_config("info", foreground=self.INFO_C)

        # Progress bar
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(log_card, variable=self.progress_var, maximum=100,
                                            bootstyle=SUCCESS)
        self.progress_bar.pack(fill=tk.X, padx=12, pady=(2, 4))
        self.progress_label = ttk.Label(log_card, text="就绪", font=FONT_SMALL,
                                        bootstyle=SECONDARY)
        self.progress_label.pack(anchor=tk.W, padx=12, pady=(0, 8))

    # ── Project config logic ─────────────────────────────────────

    def _load_project_config(self):
        projects = self.projects_data.get("projects", {})
        active = self.projects_data.get("active", "default")

        names = list(projects.keys())
        self.project_combo["values"] = names

        if active in projects:
            self.project_combo.set(active)
            self._display_project(active, defer_scan=True)
        elif names:
            self.project_combo.set(names[0])
            self._display_project(names[0], defer_scan=True)

    def _display_project(self, name, defer_scan=False):
        projects = self.projects_data.get("projects", {})
        proj = projects.get(name, {})
        self._loading = True
        self.proj_name_var.set(name)
        self.source_dir_var.set(proj.get("source_dir", ""))
        self.client_dir_var.set(proj.get("client_output_dir", ""))
        self.server_dir_var.set(proj.get("server_output_dir", ""))
        self.client_encoding_var.set(proj.get("client_encoding", "utf-8"))
        self.server_encoding_var.set(proj.get("server_encoding", "utf-8"))
        self._loading = False
        if defer_scan:
            self.root.after(50, self._refresh_table_list)
        else:
            self._refresh_table_list()

    def _on_project_switch(self, event=None):
        name = self.project_combo.get()
        if name:
            self.projects_data["active"] = name
            save_projects(self.projects_data)
            self._display_project(name)

    def _browse_source(self):
        d = filedialog.askdirectory(title="选择表格目录")
        if d:
            self.source_dir_var.set(d)

    def _browse_client(self):
        d = filedialog.askdirectory(title="选择客户端输出目录")
        if d:
            self.client_dir_var.set(d)

    def _browse_server(self):
        d = filedialog.askdirectory(title="选择服务端输出目录")
        if d:
            self.server_dir_var.set(d)

    def _save_project(self):
        name = self.proj_name_var.get().strip()
        if not name:
            messagebox.showerror("错误", "项目名称不能为空")
            return
        projects = self.projects_data.get("projects", {})
        projects[name] = {
            "source_dir": self.source_dir_var.get(),
            "client_output_dir": self.client_dir_var.get(),
            "server_output_dir": self.server_dir_var.get(),
            "client_encoding": self.client_encoding_var.get(),
            "server_encoding": self.server_encoding_var.get(),
        }
        self.projects_data["projects"] = projects
        self.projects_data["active"] = name
        save_projects(self.projects_data)
        self._load_project_config()
        self._log("配置已保存", "info")

    def _on_dir_change(self, *args):
        if self._loading:
            return
        self._auto_save_project()

    def _auto_save_project(self):
        name = self.proj_name_var.get().strip()
        if not name:
            return
        projects = self.projects_data.get("projects", {})
        if name not in projects:
            return
        projects[name] = {
            "source_dir": self.source_dir_var.get(),
            "client_output_dir": self.client_dir_var.get(),
            "server_output_dir": self.server_dir_var.get(),
            "client_encoding": self.client_encoding_var.get(),
            "server_encoding": self.server_encoding_var.get(),
        }
        save_projects(self.projects_data)

    def _add_project(self):
        name = self.proj_name_var.get().strip()
        if not name:
            name = "new_project"
        projects = self.projects_data.get("projects", {})
        if name in projects:
            i = 1
            while f"{name}_{i}" in projects:
                i += 1
            name = f"{name}_{i}"
        projects[name] = {"source_dir": "", "client_output_dir": "", "server_output_dir": "",
                          "client_encoding": "utf-8", "server_encoding": "utf-8"}
        self.projects_data["projects"] = projects
        save_projects(self.projects_data)
        self._load_project_config()
        self._display_project(name)

    def _delete_project(self):
        name = self.proj_name_var.get().strip()
        projects = self.projects_data.get("projects", {})
        if len(projects) <= 1:
            messagebox.showerror("错误", "至少保留一个项目")
            return
        if name in projects:
            del projects[name]
        self.projects_data["active"] = list(projects.keys())[0]
        save_projects(self.projects_data)
        self._load_project_config()

    # ── Table list logic ─────────────────────────────────────────

    def _refresh_table_list(self):
        self._do_scan()
        self._rebuild_checkbox_list()

    def _do_scan(self):
        source_dir = self.source_dir_var.get()
        self.file_paths = list_excel_files(source_dir)
        self._sort_file_paths()

    # ── 排序 ─────────────────────────────────────────────────────

    @staticmethod
    def _sort_label(key):
        """配置里的键 -> 下拉框文案（未知键退回默认的"名称"）。"""
        return _SORT_KEY2LABEL.get(key, _SORT_KEY2LABEL[DEFAULT_SORT])

    def _current_sort_key(self):
        """当前下拉框选中的排序方式（name / time）。"""
        label = self.sort_var.get() if hasattr(self, 'sort_var') else ""
        return _SORT_LABEL2KEY.get(label, DEFAULT_SORT)

    def _sort_file_paths(self):
        """按当前排序方式排列：名称 = 升序；时间 = 修改时间新的在前。"""
        try:
            if self._current_sort_key() == "time":
                self.file_paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            else:
                self.file_paths.sort(key=lambda p: os.path.basename(p).lower())
        except Exception:
            pass

    def _on_sort_change(self, event=None):
        """切换排序方式：立即重排列表，并记进配置文件。"""
        self.projects_data["sort_by"] = self._current_sort_key()
        save_projects(self.projects_data)
        self._sort_file_paths()
        self._rebuild_checkbox_list()
        self.list_canvas.yview_moveto(0)
        self._log(f"排序方式：{self.sort_var.get()}", "info")

    def _rebuild_checkbox_list(self):
        if not hasattr(self, 'list_frame'):
            return
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.check_vars = {}

        total = len(self.file_paths)
        self.table_count_label.config(text=f"({total} 个表格)" if total else "")

        if not self.file_paths:
            source_dir = self.source_dir_var.get()
            if not source_dir or not os.path.isdir(source_dir):
                tk.Label(self.list_frame, text="📂  请先配置表格目录",
                         fg=self.TEXT_SEC, bg=self.CARD, font=FONT).pack(pady=40)
            else:
                tk.Label(self.list_frame, text="📭  未找到表格文件",
                         fg=self.TEXT_SEC, bg=self.CARD, font=FONT).pack(pady=40)
            return

        keyword = self.search_var.get().lower()
        if keyword == "搜索表格名称...":
            keyword = ""
        visible = [p for p in self.file_paths if not keyword or keyword in os.path.basename(p).lower()]

        if not visible:
            tk.Label(self.list_frame, text="🔍  无匹配结果",
                     fg=self.TEXT_SEC, bg=self.CARD, font=FONT).pack(pady=40)
            return

        for i, path in enumerate(visible):
            bg = self.ROW_EVEN if i % 2 == 0 else self.ROW_ODD
            row = tk.Frame(self.list_frame, bg=bg)
            row.pack(fill=tk.X)

            var = tk.BooleanVar(value=False)
            self.check_vars[path] = var

            cb = tk.Checkbutton(row, text="  " + os.path.basename(path), variable=var,
                                font=FONT, fg=self.TEXT, bg=bg, activebackground=bg,
                                selectcolor=bg, relief=tk.FLAT, anchor=tk.W,
                                padx=16, pady=4, borderwidth=0,
                                disabledforeground=self.TEXT_SEC)
            cb.pack(fill=tk.X)

            # Hover effect
            def _on_enter(e, r=row, b=bg):
                r.configure(bg=self.HOVER)
                cb.configure(bg=self.HOVER, activebackground=self.HOVER)
            def _on_leave(e, r=row, b=bg):
                r.configure(bg=b)
                cb.configure(bg=b, activebackground=b)
            cb.bind("<Enter>", _on_enter)
            cb.bind("<Leave>", _on_leave)
            row.bind("<Enter>", _on_enter)
            row.bind("<Leave>", _on_leave)

        self.table_count_label.config(text=f"({total} 个表格, 匹配 {len(visible)} 个)")

    def _on_search(self, *args):
        if not hasattr(self, 'list_frame'):
            return
        self._rebuild_checkbox_list()
        self.list_canvas.yview_moveto(0)

    # ── Mouse wheel ──────────────────────────────────────────────

    def _bind_mousewheel(self, event):
        self.list_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        if HAS_TOUCHPAD_SCROLL:
            # Tk 9 起，触摸板 / Magic Mouse / Magic Trackpad 走独立事件，
            # 不再发 <MouseWheel> —— 不绑这个的话 Mac 上触摸板完全滚不动。
            self.list_canvas.bind_all("<TouchpadScroll>", self._on_touchpad_scroll)

    def _unbind_mousewheel(self, event):
        self.list_canvas.unbind_all("<MouseWheel>")
        if HAS_TOUCHPAD_SCROLL:
            self.list_canvas.unbind_all("<TouchpadScroll>")

    def _on_mousewheel(self, event):
        """鼠标滚轮（量纲换算见 platform_compat.wheel_units）。"""
        self.list_canvas.yview_scroll(wheel_units(event.delta), "units")

    def _on_touchpad_scroll(self, event):
        """触摸板 / Magic Mouse 的平滑滚动（Tk 9 新增的 <TouchpadScroll>）。

        每个事件只报几像素，所以先累积、凑够一格再滚 —— 否则一次滑动手势会把
        列表直接甩到最底。
        """
        dy = touchpad_dy(event.delta)
        if not dy:
            return
        self._touchpad_accum -= dy
        steps = int(self._touchpad_accum / TOUCHPAD_STEP)
        if steps:
            self._touchpad_accum -= steps * TOUCHPAD_STEP
            self.list_canvas.yview_scroll(steps, "units")

    # ── Selection ────────────────────────────────────────────────

    def _select_all(self):
        for var in self.check_vars.values():
            var.set(True)

    def _deselect_all(self):
        for var in self.check_vars.values():
            var.set(not var.get())

    def _get_checked(self):
        return [path for path, var in self.check_vars.items() if var.get()]

    # ── Export ───────────────────────────────────────────────────

    def _export_selected(self):
        paths = self._get_checked()
        if not paths:
            messagebox.showinfo("提示", "没有选中任何表")
            return
        self._do_export(paths)

    def _export_all(self):
        if not self.file_paths:
            messagebox.showinfo("提示", "没有可导出的表")
            return
        self._do_export(self.file_paths)

    def _preflight(self, file_paths):
        """只读预检：把表全部读进来，并把单元格里的数据错误全找出来。

        受检内容（见 ``core.excel_reader._CHECKED_TYPES``）：
        - ``table`` / ``any``：手写 Lua 的语法错误（全角括号、JSON 冒号、漏逗号…）
        - ``number``：填了非数字（``100个`` / ``1,000``），导出后会静默变成 nil

        这一步**不写任何文件**，所以发现问题时中断是干净的——目标目录不会留下
        "导了一半"的产物（已存在的旧文件也不会被覆盖）。顺带的好处是每个 xlsx
        只加载一次，写文件阶段直接用这里的结果。

        返回 ``(loaded, load_fail, error_count, dialog_lines)``：
        - ``loaded``：``[(file_path, tables)]``，``tables`` 为 ``None`` 表示该文件加载失败
        - ``load_fail``：加载失败的文件数
        - ``error_count``：数据错误总数
        - ``dialog_lines``：给弹窗用的**精简版**错误行（文件/表/单元格/字段 -> 错误），
          日志里仍写信息最全的那版
        """
        total = len(file_paths)
        loaded = []
        load_fail = 0
        syntax_count = 0
        dialog_lines = []

        self.progress_var.set(0)
        self.progress_label.config(text=f"校验中... 0/{total}")

        for idx, fpath in enumerate(file_paths):
            try:
                tables = load_excel(fpath)
                for info in tables:
                    info["source_path"] = fpath
                    # 填错的字符（全角括号、JSON 冒号、漏逗号…）在这里就报出来，
                    # 免得导出的 lua 到游戏里才崩、还得回头找是哪一格。
                    for msg in format_syntax_errors(info):
                        self._log(msg, "error")
                        syntax_count += 1
                    dialog_lines.extend(format_syntax_errors(info, compact=True))
                loaded.append((fpath, tables))
            except Exception as e:
                self._log(f"{os.path.basename(fpath)}: {str(e)}", "error")
                loaded.append((fpath, None))
                load_fail += 1

            self.progress_var.set((idx + 1) / total * 50)
            self.progress_label.config(text=f"校验中... {idx + 1}/{total}")
            self.root.update_idletasks()

        return loaded, load_fail, syntax_count, dialog_lines

    def _warn_data_errors(self, count, lines=()):
        """有数据错误时只作提示，导出直接中断。

        故意用 ``showwarning``（只有"确定"）而不是 ``askyesno``——**不提供"强行继续导出"**，
        免得半成品 lua 被写进目标目录、到游戏里才炸。

        弹窗里**直接列出具体错误**（不让人再去日志里翻）：最多列 ``_DIALOG_MAX_ERRORS`` 条，
        超出的用一行"另有 N 处"带过，日志里是全量。
        """
        self._log(f"发现 {count} 处数据错误，已中断导出（尚未写入任何文件）。", "error")

        lines = list(lines)
        shown = lines[:_DIALOG_MAX_ERRORS]
        body = "\n".join(shown) if shown else ""
        if count > len(shown):
            more = count - len(shown)
            body += ("\n" if body else "") + f"…… 另有 {more} 处，见日志。"

        msg = f"发现 {count} 处数据错误，已中断导出，目标目录没有写入任何文件。\n"
        if body:
            msg += "\n" + body + "\n"
        msg += "\n请先去源表把这些格子改掉，再重新导出。"

        messagebox.showwarning("数据校验失败", msg)

    def _do_export(self, file_paths):
        client_dir = self.client_dir_var.get()
        server_dir = self.server_dir_var.get()
        client_encoding = self.client_encoding_var.get()
        server_encoding = self.server_encoding_var.get()

        if not client_dir and not server_dir:
            messagebox.showerror("错误", "请先配置客户端或服务端输出目录")
            return
        if not file_paths:
            return

        total = len(file_paths)
        self._log(f"开始导出 {total} 个文件... (客户端: {client_encoding}, 服务端: {server_encoding})", "info")

        # ── 1/2 预检（只读）──────────────────────────────────────
        loaded, load_fail, syntax_count, error_lines = self._preflight(file_paths)
        if syntax_count:
            self._warn_data_errors(syntax_count, error_lines)
            self.progress_var.set(0)
            self.progress_label.config(text=f"已中断：{syntax_count} 处数据错误")
            return

        # ── 2/2 写文件 ──────────────────────────────────────────
        success_count = 0
        fail_count = load_fail

        for idx, (fpath, tables) in enumerate(loaded):
            if tables is not None:
                try:
                    for info in tables:
                        result = export_table(fpath, info, client_dir, server_dir,
                                             client_encoding, server_encoding)
                        for msg in result.success:
                            self._log(msg, "success")
                            success_count += 1
                        for msg in result.failed:
                            self._log(msg, "error")
                            fail_count += 1
                except Exception as e:
                    self._log(f"{os.path.basename(fpath)}: {str(e)}", "error")
                    fail_count += 1

            self.progress_var.set(50 + (idx + 1) / total * 50)
            self.progress_label.config(text=f"导出中... {idx + 1}/{total}")
            self.root.update_idletasks()

        summary = f"导出完成: 成功 {success_count}, 失败 {fail_count}"
        self._log(summary, "info")
        self.progress_var.set(100)
        if fail_count:
            status = f"完成 ({fail_count} 个失败)"
        else:
            status = "全部成功 ✓"
        self.progress_label.config(text=status)

    # ── SVN ──────────────────────────────────────────────────────

    def _run_tortoise(self, command, clean_path, extra_args=()):
        """用 TortoiseSVN 自己的窗口执行命令；未检测到 TortoiseSVN 时返回 False。"""
        proc = _find_tortoise_proc()
        if not proc:
            return False
        subprocess.Popen([proc, f"/command:{command}", f"/path:{clean_path}", *extra_args])
        return True

    def _svn_on_mac(self, command):
        """macOS 分支：在 Terminal 里执行 svn。返回 True 表示已处理。

        用终端而不是静默的 subprocess，是为了对应 Windows 那边 cmd /k 的行为 ——
        用户能看见进度和输出。没有 svn 时按钮不会显示（见 _build_bottom_bar），
        这里的检查只是兜底。
        """
        if not IS_MAC:
            return False

        source_dir = self.source_dir_var.get()
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror("错误", "请先配置表格目录")
            return True

        svn = find_svn()
        if not svn:
            messagebox.showerror("错误", "未找到 svn，无法执行该操作")
            return True

        clean_path = os.path.normpath(source_dir)
        try:
            run_svn_in_terminal(svn, command, clean_path)
        except Exception as e:
            messagebox.showerror("错误", f"无法启动终端: {e}")
            return True
        self._log(f"已在终端执行 svn {command}：{clean_path}")
        return True

    def _svn_update(self):
        if self._svn_on_mac("update"):
            return
        source_dir = self.source_dir_var.get()
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror("错误", "请先配置表格目录")
            return
        clean_path = os.path.normpath(source_dir)
        # /closeonend:0 = 更新完成后保留窗口，方便查看本次更新了哪些文件
        if self._run_tortoise("update", clean_path, ("/closeonend:0",)):
            self._log(f"已打开 TortoiseSVN 更新窗口：{clean_path}")
            return
        # 兜底：机器上没装 TortoiseSVN 时退回命令行
        self._log("未检测到 TortoiseSVN，改用命令行执行 svn update", "error")
        subprocess.Popen(["cmd", "/k", f'cd /d "{clean_path}" && svn update'])

    def _svn_commit(self):
        if self._svn_on_mac("commit"):
            return
        source_dir = self.source_dir_var.get()
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror("错误", "请先配置表格目录")
            return
        clean_path = os.path.normpath(source_dir)
        if self._run_tortoise("commit", clean_path):
            self._log(f"已打开 TortoiseSVN 提交窗口：{clean_path}")
            return
        try:
            subprocess.Popen(["svn", "commit"], cwd=source_dir)
        except Exception as e:
            messagebox.showerror("错误", f"无法启动 svn commit: {e}")

    # ── Log ──────────────────────────────────────────────────────

    def _log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.root.update_idletasks()

    def _clear_log(self):
        """清空日志区，并把进度条/状态栏复位（否则日志空了、状态栏还留着上次的结论）。"""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text="就绪")
        self.root.update_idletasks()


def _setup_placeholder(entry, var, placeholder, text_color, placeholder_color):
    def on_focus_in(e):
        if var.get() == placeholder:
            var.set("")
            entry.config(fg=text_color)
    def on_focus_out(e):
        if not var.get():
            var.set(placeholder)
            entry.config(fg=placeholder_color)
    entry.bind("<FocusIn>", on_focus_in)
    entry.bind("<FocusOut>", on_focus_out)
    if not var.get():
        var.set(placeholder)
        entry.config(fg=placeholder_color)
