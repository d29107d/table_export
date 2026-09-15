import os
import sys
import json
import shutil
import subprocess
import traceback
import tkinter as tk
from tkinter import messagebox, filedialog
from datetime import datetime

import ttkbootstrap as ttk
from ttkbootstrap.constants import PRIMARY, SECONDARY, SUCCESS, INFO, WARNING, DANGER, OUTLINE

from core.excel_reader import list_excel_files, load_excel
from core.exporter import ENCODINGS, export_table, normalize_encoding
from core.i18n import DEFAULT_LANGUAGE, LANGUAGES, set_language, t
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
    shortcut,
    touchpad_dy,
    wheel_units,
)


def _find_tortoise_proc():
    """Locate TortoiseSVN's GUI process TortoiseProc.exe; returns None when it is missing.

    Lookup order: PATH -> registry (``ProcPath``, written by the TortoiseSVN
    installer) -> common install directories. Running update/commit through it pops
    TortoiseSVN's own window instead of a command-line terminal.

    Returns None right away on non-Windows, which also avoids the ``winreg`` import
    that is bound to fail on macOS.
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


#: Runtime config directory. Windows keeps the config/ next to the exe (unchanged);
#: macOS moves to ~/Library/Application Support/ - the inside of a .app is a signed
#: area and must not be written to.
CONFIG_DIR = config_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "projects.json")
#: Fallback config bundled into the package (used as a template on first launch)
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

#: ttkbootstrap 1.x theme name -> 2.x equivalent.
#: The 1.x names (litera / darkly ...) can still be loaded by ``theme_use`` in 2.2,
#: so an old config holding one does not raise; but they do **not** appear in
#: ``theme_names()``. The theme menu is generated from ``theme_names()``, so the
#: "current theme" would not be a menu entry at all and nothing could be checked.
#: Normalising while reading the config also silences the library's
#: DeprecationWarning.
#:
#: Only the two names with a certain mapping (1.x default light / dark) are mapped;
#: every other legacy name falls back to the default instead of being guessed - a
#: wrong guess would silently hand the user a different-looking theme.
LEGACY_THEME_MAP = {
    "litera": "bootstrap-light",
    "darkly": "bootstrap-dark",
}
#: Default theme when the config is missing or the theme name is unknown
DEFAULT_THEME = "bootstrap-light"


def normalize_theme(name):
    """Collapse a configured theme name into a usable ttkbootstrap 2.x name.

    Pure string handling without a Tk dependency, so it can be called **before** the
    window is created (``ttkb.Window(themename=...)`` needs it settled up front).
    """
    if name in LEGACY_THEME_MAP:
        return LEGACY_THEME_MAP[name]
    if isinstance(name, str) and (name.endswith("-light") or name.endswith("-dark")):
        return name
    return DEFAULT_THEME


def _is_dark_theme(name):
    """Is the theme dark?

    ``DARK_THEMES`` holds ttkbootstrap 1.x legacy names - still usable in 2.2, but
    they emit a deprecation warning and **no longer appear in ``theme_names()``**.
    From 2.x on, themes follow the ``xxx-light`` / ``xxx-dark`` naming convention
    (bootstrap-dark, nord-dark ...), and none of those new dark themes are in
    ``DARK_THEMES``. Testing against the old set alone would leave ``self._is_dark``
    False after picking a new dark theme, so the hand-painted widgets (Canvas, list
    rows, log Text, search entry ...) would stay light and clash with the ttk ones.
    """
    return name in DARK_THEMES or name.endswith("-dark")

# ── Fonts ────────────────────────────────────────────────────────
#
# Placeholder values below; the real family names are probed per platform by
# _init_fonts() in __init__ (probing needs a Tk root). Font sizes need no
# per-platform split - Tk 9 aligned the macOS dpi baseline from 72 to 96,
# measured tk scaling = 1.334, the same as Windows.

FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_MONO = ("Consolas", 10)


def _init_fonts(root):
    """Resolve the font family names per platform, overriding the constants above.

    On Windows it probes ``Microsoft YaHei UI`` / ``Consolas``, the same result as
    the hard-coded values; on macOS it picks ``PingFang SC`` / ``Menlo``. Hard-coded
    names fall back silently on macOS, turning the whole UI into the default font and
    destroying the log pane's monospace alignment - without raising anything.
    """
    global FONT, FONT_BOLD, FONT_TITLE, FONT_SMALL, FONT_MONO
    ui, mono = resolve_font_families(root)
    FONT = (ui, 10)
    FONT_BOLD = (ui, 10, "bold")
    FONT_TITLE = (ui, 15, "bold")
    FONT_SMALL = (ui, 9)
    FONT_MONO = (mono, 10)

#: Sorting modes for the table list (the dropdown labels follow the language, see ``MainWindow._sort_label``)
SORT_KEYS = ("name", "time")
#: Sort by name by default
DEFAULT_SORT = "name"

#: Max error details listed in the data error dialog (`messagebox` cannot scroll, too many rows overflow the screen)
_DIALOG_MAX_ERRORS = 10


class MainWindow:
    def __init__(self, root, initial_theme="bootstrap-light", initial_language=None):
        self.root = root
                # Normalise: a 1.x theme name (litera/darkly) in an old config becomes its 2.x
                # equivalent, otherwise the theme menu is built from theme_names() and the
        # current theme can never be checked.
        self.current_theme = normalize_theme(initial_theme)
        self.projects_data = load_projects()
                #: UI language: prefer what the caller passed (main.py already read the config),
                #: then the config file, then English. Stored at the top level of projects.json,
        #: next to sort_by.
        self.language = set_language(
            initial_language or self.projects_data.get("language") or DEFAULT_LANGUAGE)
        self.file_paths = []
        #: Ticked tables, keyed by path: rebuilding the list (typing in the search box)
        #: must not lose the selection.
        self.checked_paths = set()
        self._loading = False
                #: Pixel accumulator for trackpad scrolling (unused on Windows; Tk 8.6 never writes it)
        self._touchpad_accum = 0.0
                #: Widgets whose text follows the language: ``(widget, i18n key)``
        self._i18n_widgets = []
                #: Menu entries whose text follows the language: ``(menu, entry index, i18n key)``
        self._i18n_menu_items = []
                #: Search placeholder text (follows the language; used to tell "is it empty" while filtering)
        self._placeholder = ""
        #: Hover tooltips whose text follows the language: ``(tooltip, i18n key, accel)``
        #: - the same key that labels the widget, plus the platform's shortcut spelling.
        self._i18n_tips = []

                # Must happen before any _build_* - those methods reference the module-level FONT constants
        _init_fonts(root)

                # Safety net: an exception raised inside a Tk callback no longer makes the window vanish silently
        root.report_callback_exception = self._on_callback_exception

        self._is_dark = _is_dark_theme(self.current_theme)
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
            if self.search_var.get() == self._placeholder:
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
        """Build the menu bar (once, at startup).

        Never go back to "destroy and rebuild the whole menu bar on a language
        change": switching is initiated from a menu entry's own ``command`` callback,
        and ``_log()`` calls ``update_idletasks()``, which can run queued callbacks on
        the spot - so the menu bar would be destroyed while the menu is still in use
        (on Windows: destroying the native menu being displayed) and the process dies
        instantly. Entries that follow the language are registered in
        ``_i18n_menu_items``; switching only relabels them.
        """
        if getattr(self, "_menubar", None) is not None:
            return

        self._menubar = tk.Menu(self.root, tearoff=0, font=FONT_SMALL, bg=self.CARD, fg=self.TEXT,
                                activebackground=self.ACCENT, activeforeground="white", borderwidth=0)
        self.root.config(menu=self._menubar)

        file_menu = self._submenu()
        self._menu_item(file_menu, "menu.quit", self.root.quit, accel=shortcut("Q"))
        self._menu_cascade(self._menubar, "menu.file", file_menu)

        export_menu = self._submenu()
        self._menu_item(export_menu, "menu.export_selected", self._export_selected, accel=shortcut("E"))
        self._menu_item(export_menu, "menu.export_all", self._export_all,
                        accel=shortcut("E", shift=True))
        self._menu_cascade(self._menubar, "menu.export", export_menu)

        self.theme_menu = self._submenu()
        self._menu_cascade(self._menubar, "menu.theme", self.theme_menu)
        self._rebuild_theme_menu()

                # Language menu: sits between "Theme" and "Help". Both options are written in
                # their own language so "Chinese" is recognisable even in an English UI.
        self.language_menu = self._submenu()
        self.language_var = tk.StringVar(value=self.language)
        for code, label in LANGUAGES:
            self.language_menu.add_radiobutton(
                label=label, value=code, variable=self.language_var,
                command=lambda c=code: self._switch_language(c))
        self._menu_cascade(self._menubar, "menu.language", self.language_menu)

        help_menu = self._submenu()
        self._menu_item(help_menu, "menu.about", lambda: messagebox.showinfo(
            t("about.title"), t("about.body")))
        self._menu_cascade(self._menubar, "menu.help", help_menu)

    def _menu_item(self, menu, key, command, **fmt):
        """Append a command entry to menu whose text follows the language.

        ``fmt`` is handed to ``t()`` when the label is rendered - it carries the
        platform's shortcut spelling, because a menu label is also a hint and has to
        agree with the real binding (⌘Q on macOS, Ctrl+Q elsewhere).
        """
        menu.add_command(label=t(key, **fmt), command=command)
        self._i18n_menu_items.append((menu, menu.index(tk.END), key, fmt))

    def _menu_cascade(self, parent, key, menu):
        """Append a cascade entry to parent whose text follows the language (attaching submenu)."""
        parent.add_cascade(label=t(key), menu=menu)
        self._i18n_menu_items.append((parent, parent.index(tk.END), key, {}))


    def _submenu(self):
        """Create a dropdown menu styled like the menu bar."""
        return tk.Menu(self._menubar, tearoff=0, font=FONT_SMALL, bg=self.CARD, fg=self.TEXT,
                       activebackground=self.ACCENT, activeforeground="white", borderwidth=0)

    # ── Language ─────────────────────────────────────────────────

    def _reg(self, widget, key):
        """Register a widget whose text follows the language and set the text immediately.

        Usage: ``self._reg(ttk.Label(frame), "field.source_dir").pack(...)`` - once
        registered, ``_apply_language()`` refreshes every text in one go, so no
        scattered refresh calls are needed.
        """
        self._i18n_widgets.append((widget, key))
        widget.configure(text=t(key))
        return widget

    def _tip_text(self, key, accel):
        """A tooltip line: the widget's label plus its shortcut (``全选  (Ctrl+A)``)."""
        return "%s  (%s)" % (t(key), accel)

    def _tip(self, widget, key, accel):
        """Attach a hover tooltip that spells out the widget's shortcut.

        The text is the widget's own label plus the platform's key name (⌘S on macOS,
        Ctrl+S elsewhere), so a hint always matches the real binding. Registered in
        ``_i18n_tips`` so a language switch relabels it along with the widget.
        """
        tip = ttk.ToolTip(widget, text=self._tip_text(key, accel))
        self._i18n_tips.append((tip, key, accel))
        return tip

    def _quick_button(self, parent, key, command, accel, padx=2, **style):
        """A bottom-bar button plus its tooltip: ``accel`` is the shortcut hint."""
        button = self._reg(ttk.Button(parent, command=command, **style), key)
        button.pack(side=tk.LEFT, padx=padx)
        self._tip(button, key, accel)
        return button

    def _switch_language(self, code):
        """Switch the language: relabel, rebuild menus, persist."""
        self.language = set_language(code)
        self.projects_data["language"] = self.language
        save_projects(self.projects_data)
        self._apply_language()
        self._log(t("log.language_switched", name=dict(LANGUAGES)[self.language]), "info")

    def _apply_language(self):
        """Apply the current language to every registered widget (call once after switching)."""
        self.root.title(t("app.title"))

        for widget, key in self._i18n_widgets:
            try:
                widget.configure(text=t(key))
            except tk.TclError:
                pass

        # Tooltips repeat a widget's label and add its shortcut, so they follow the language too
        for tip, key, accel in self._i18n_tips:
            try:
                tip.configure(text=self._tip_text(key, accel))
            except tk.TclError:
                pass

                # Menu bar: only relabel the entries that need translating, never destroy and rebuild
                # (see _build_menu: this method can run inside a menu entry's own callback, and
        # destroying the menu bar destroys the native menu currently in use).
        for menu, index, key, fmt in self._i18n_menu_items:
            try:
                menu.entryconfigure(index, label=t(key, **fmt))
            except tk.TclError:
                pass

                # Sort dropdown: the values themselves are translated text, so its options change
        # along with it and the selected entry is looked up again by key
        if hasattr(self, "sort_combo"):
            sort_key = self._current_sort_key()
            self.sort_combo.configure(values=[self._sort_label(k) for k in SORT_KEYS])
            self.sort_var.set(self._sort_label(sort_key))

                # Search placeholder
        if hasattr(self, "search_entry"):
            self._install_placeholder()

                # The list's empty-state/count text is assembled dynamically, so rebuild it
        self._rebuild_checkbox_list()

                # Status bar text: only safe to rewrite in static states such as "Ready"
        if hasattr(self, "progress_label") and self.progress_var.get() == 0:
            self.progress_label.config(text=t("status.ready"))

    def _theme_names(self):
        """The theme names to list, making sure ``current_theme`` is among them.

        ``theme_names()`` is the library's list of usable themes, but the config may
        hold a name it does not know (hand-edited, or written by an older version).
        Listing only the library names would leave the current theme out of the menu
        with nothing checked - adding it keeps the checkmark consistent with reality.
        """
        names = list(ttk.Style().theme_names())
        if self.current_theme and self.current_theme not in names:
            names.insert(0, self.current_theme)
        return names

    def _rebuild_theme_menu(self):
        """Rebuild the menu from the available themes.

        All radio buttons must share **one** ``StringVar``: with a separate variable
        each, they are unrelated, Tk cannot tell which entry should show the checkmark,
        and no entry is ever checked.
        """
        self.theme_menu.delete(0, tk.END)
        if getattr(self, "theme_var", None) is None:
            self.theme_var = tk.StringVar(self.root)
        self.theme_var.set(self.current_theme)
        for name in self._theme_names():
            self.theme_menu.add_radiobutton(
                label=name,
                command=lambda n=name: self._switch_theme(n),
                variable=self.theme_var,
                value=name,
            )

    def _switch_theme(self, name):
        try:
            ttk.Style().theme_use(name)
            self.current_theme = name
                        # The theme can also be changed outside this menu (shortcut, the light/dark
                        # button in the top bar), so sync the variable explicitly instead of relying
            # on Tk updating it only for menu clicks.
            if getattr(self, "theme_var", None) is not None:
                self.theme_var.set(name)
            self._is_dark = _is_dark_theme(name)
            self._update_theme_colors()
            self._save_theme(name)
            self._apply_theme_colors()
            self._rebuild_theme_menu()
            self._log(t("log.theme_switched", name=name), "info")
        except Exception as e:
            messagebox.showerror(t("dlg.error"), t("msg.theme_failed", err=e))

    def _toggle_dark_mode(self):
        """Toggle between the 2.x light / dark themes.

        Deliberately avoids the legacy litera / darkly names: they emit a
        DeprecationWarning in 2.2 and are scheduled for removal in 3.0.
        """
        target = "bootstrap-dark" if not self._is_dark else "bootstrap-light"
        self._switch_theme(target)

    def _save_theme(self, name):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(THEME_PATH, "w", encoding="utf-8") as f:
            json.dump({"theme": name}, f)

    # ── Shortcuts ────────────────────────────────────────────────

    def _bind_shortcuts(self):
        # One entry per shortcut: the key with its modifiers, then what it runs. Each is
        # registered on Control and - on macOS - on Command as well, so a Mac user can
        # reach for ⌘ while the Windows habit (Ctrl) keeps working. F5 carries no
        # modifier and is bound once at the end.
        keys = [
            ("a", lambda e: self._select_all()),
            ("A", lambda e: self._select_all()),
            ("Shift-A", lambda e: self._deselect_all()),
            ("e", lambda e: self._export_selected()),
            ("E", lambda e: self._export_selected()),
            ("Shift-E", lambda e: self._export_all()),
            ("s", lambda e: self._save_project()),
            ("S", lambda e: self._save_project()),
            ("q", lambda e: self.root.quit()),
            ("Q", lambda e: self.root.quit()),
            ("f", lambda e: self.search_entry.focus_set()),
            ("F", lambda e: self.search_entry.focus_set()),
        ]
        for key, handler in keys:
            self.root.bind("<Control-%s>" % key, handler)
            if IS_MAC:
                self.root.bind("<Command-%s>" % key, handler)
        self.root.bind("<F5>", lambda e: self._refresh_table_list())

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

        self._reg(ttk.Label(top_bar, font=FONT_TITLE, bootstyle=PRIMARY),
                  "topbar.title").pack(side=tk.LEFT, padx=12, pady=6)

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
        self._quick_button(left_group, "btn.select_all", self._select_all, shortcut("A"),
                           bootstyle=(SECONDARY, OUTLINE))
        self._quick_button(left_group, "btn.invert", self._deselect_all, shortcut("A", shift=True),
                           bootstyle=(SECONDARY, OUTLINE))
        self._quick_button(left_group, "btn.refresh", self._refresh_table_list, "F5",
                           bootstyle=(SECONDARY, OUTLINE))

        ttk.Separator(bottom, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=6)

        center_group = ttk.Frame(bottom)
        center_group.pack(side=tk.LEFT, padx=2, pady=4)
        self._quick_button(center_group, "btn.export_selected", self._export_selected, shortcut("E"),
                           bootstyle=SUCCESS)
        self._quick_button(center_group, "btn.export_all", self._export_all, shortcut("E", shift=True),
                           bootstyle=PRIMARY)

                # On macOS without svn these two buttons would only pop an error - hide them
                # completely, separator included. Windows keeps them (it detects svn differently:
        # TortoiseSVN or the system svn).
        if IS_WIN or find_svn():
            ttk.Separator(bottom, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=6)

            svn_group = ttk.Frame(bottom)
            svn_group.pack(side=tk.LEFT, padx=2, pady=4)
            self._reg(ttk.Button(svn_group, bootstyle=(INFO, OUTLINE),
                                 command=self._svn_update), "btn.svn_update").pack(side=tk.LEFT, padx=2)
            self._reg(ttk.Button(svn_group, bootstyle=(INFO, OUTLINE),
                                 command=self._svn_commit), "btn.svn_commit").pack(side=tk.LEFT, padx=2)

    # ── Table list panel (left) ──────────────────────────────────

    def _build_table_panel(self, parent):
        card = self._reg(ttk.LabelFrame(parent, bootstyle=PRIMARY), "panel.tables")
        card.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Header: count + project selector
        header_frame = ttk.Frame(card)
        header_frame.pack(fill=tk.X, padx=14, pady=(10, 4))

        self.table_count_label = ttk.Label(header_frame, text="", font=FONT_SMALL,
                                           bootstyle=SECONDARY)
        self.table_count_label.pack(side=tk.LEFT)

        self._reg(ttk.Label(header_frame, font=FONT_SMALL, bootstyle=SECONDARY),
                  "label.project").pack(side=tk.RIGHT, padx=(0, 4))
        self.project_combo = ttk.Combobox(header_frame, state="readonly", width=16, font=FONT)
        self.project_combo.pack(side=tk.RIGHT)
        self.project_combo.bind("<<ComboboxSelected>>", self._on_project_switch)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=14, pady=(2, 4))

                # Search bar (search entry + sort dropdown)
        search_frame = ttk.Frame(card)
        search_frame.pack(fill=tk.X, padx=14, pady=(0, 6))

                # Pack the fixed-width dropdown on the right first, then let the search entry
                # fill and expand into the rest - that way the entry shrinks automatically and
        # gives the room to the dropdown.
        self.sort_var = tk.StringVar(
            value=self._sort_label(self.projects_data.get("sort_by", DEFAULT_SORT)))
        self.sort_combo = ttk.Combobox(
            search_frame, textvariable=self.sort_var, state="readonly",
            width=6, font=FONT_SMALL, values=[self._sort_label(k) for k in SORT_KEYS])
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
        self._install_placeholder()
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

                # Pack the scrollbar first so it claims its fixed width on the right, then pack
                # the canvas to take the remaining space. In the opposite order the canvas
                # (expand=True) swallows all the horizontal space first and the scrollbar is
        # squeezed to zero width and becomes invisible.
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
        card = self._reg(ttk.LabelFrame(parent, bootstyle=PRIMARY), "panel.projects")
        card.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ── Config section ──
        config_card = self._reg(ttk.LabelFrame(card, bootstyle=SECONDARY), "section.config")
        config_card.pack(fill=tk.X, padx=14, pady=(10, 4))

        self._reg(ttk.Label(config_card, font=FONT), "field.project_name").grid(
            row=0, column=0, sticky=tk.W, padx=12, pady=(12, 2))
        self.proj_name_var = tk.StringVar()
        ttk.Entry(config_card, textvariable=self.proj_name_var, font=FONT).grid(
            row=1, column=0, sticky=tk.EW, padx=12, pady=(0, 8))

        self._reg(ttk.Label(config_card, font=FONT), "field.source_dir").grid(
            row=2, column=0, sticky=tk.W, padx=12, pady=(2, 2))
        row2 = ttk.Frame(config_card)
        row2.grid(row=3, column=0, sticky=tk.EW, padx=12, pady=(0, 8))
        self.source_dir_var = tk.StringVar()
        self.source_dir_var.trace_add("write", self._on_dir_change)
        ttk.Entry(row2, textvariable=self.source_dir_var, font=FONT).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        self._reg(ttk.Button(row2, bootstyle=(SECONDARY, OUTLINE), width=6,
                             command=self._browse_source), "btn.browse").pack(side=tk.LEFT, padx=(6, 0))

        # Client output
        row_label = ttk.Frame(config_card)
        row_label.grid(row=4, column=0, sticky=tk.W, padx=12, pady=(2, 2))
        self._reg(ttk.Label(row_label, font=FONT), "field.client_output").pack(side=tk.LEFT)
        self.client_encoding_var = tk.StringVar(value="utf-8")
        self.client_encoding_combo = ttk.Combobox(
            row_label, textvariable=self.client_encoding_var, font=FONT_SMALL,
            values=list(ENCODINGS),
            state="readonly", width=14)
        self.client_encoding_combo.pack(side=tk.LEFT, padx=(10, 0))
        self.client_encoding_var.trace_add("write", self._on_dir_change)

        row5 = ttk.Frame(config_card)
        row5.grid(row=5, column=0, sticky=tk.EW, padx=12, pady=(0, 8))
        self.client_dir_var = tk.StringVar()
        self.client_dir_var.trace_add("write", self._on_dir_change)
        ttk.Entry(row5, textvariable=self.client_dir_var, font=FONT).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        self._reg(ttk.Button(row5, bootstyle=(SECONDARY, OUTLINE), width=6,
                             command=self._browse_client), "btn.browse").pack(side=tk.LEFT, padx=(6, 0))

        # Server output
        row_label2 = ttk.Frame(config_card)
        row_label2.grid(row=6, column=0, sticky=tk.W, padx=12, pady=(2, 2))
        self._reg(ttk.Label(row_label2, font=FONT), "field.server_output").pack(side=tk.LEFT)
        self.server_encoding_var = tk.StringVar(value="utf-8")
        self.server_encoding_combo = ttk.Combobox(
            row_label2, textvariable=self.server_encoding_var, font=FONT_SMALL,
            values=list(ENCODINGS),
            state="readonly", width=14)
        self.server_encoding_combo.pack(side=tk.LEFT, padx=(10, 0))
        self.server_encoding_var.trace_add("write", self._on_dir_change)

        row7 = ttk.Frame(config_card)
        row7.grid(row=7, column=0, sticky=tk.EW, padx=12, pady=(0, 12))
        self.server_dir_var = tk.StringVar()
        self.server_dir_var.trace_add("write", self._on_dir_change)
        ttk.Entry(row7, textvariable=self.server_dir_var, font=FONT).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        self._reg(ttk.Button(row7, bootstyle=(SECONDARY, OUTLINE), width=6,
                             command=self._browse_server), "btn.browse").pack(side=tk.LEFT, padx=(6, 0))

        config_card.columnconfigure(0, weight=1)

        # ── Action buttons ──
        action_card = self._reg(ttk.LabelFrame(card, bootstyle=SECONDARY), "section.actions")
        action_card.pack(fill=tk.X, padx=14, pady=4)

        btn_frame = ttk.Frame(action_card)
        btn_frame.pack(fill=tk.X, padx=12, pady=10)
        self._quick_button(btn_frame, "btn.save", self._save_project, shortcut("S"),
                           padx=3, bootstyle=PRIMARY)
        self._reg(ttk.Button(btn_frame, bootstyle=(INFO, OUTLINE), command=self._add_project),
                  "btn.add_project").pack(side=tk.LEFT, padx=3)
        self._reg(ttk.Button(btn_frame, bootstyle=(DANGER, OUTLINE), command=self._delete_project),
                  "btn.delete_project").pack(side=tk.LEFT, padx=3)
        self._reg(ttk.Button(btn_frame, bootstyle=(SECONDARY, OUTLINE), command=self._clear_log),
                  "btn.clear_log").pack(side=tk.LEFT, padx=3)

        # ── Log section ──
        log_card = self._reg(ttk.LabelFrame(card, bootstyle=SECONDARY), "section.log")
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
        self.progress_label = ttk.Label(log_card, text=t("status.ready"), font=FONT_SMALL,
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
        # Legacy values (gb2312 / gb18030 / utf-8-sig) are folded onto the two
        # supported encodings, so a config written by an older build still shows a
        # valid choice instead of leaving the combobox empty.
        self.client_encoding_var.set(normalize_encoding(proj.get("client_encoding", "utf-8")))
        self.server_encoding_var.set(normalize_encoding(proj.get("server_encoding", "utf-8")))
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
        d = filedialog.askdirectory(title=t("dlg.choose_source"))
        if d:
            self.source_dir_var.set(d)

    def _browse_client(self):
        d = filedialog.askdirectory(title=t("dlg.choose_client"))
        if d:
            self.client_dir_var.set(d)

    def _browse_server(self):
        d = filedialog.askdirectory(title=t("dlg.choose_server"))
        if d:
            self.server_dir_var.set(d)

    def _save_project(self):
        name = self.proj_name_var.get().strip()
        if not name:
            messagebox.showerror(t("dlg.error"), t("msg.project_name_required"))
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
        self._log(t("log.config_saved"), "info")

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
            messagebox.showerror(t("dlg.error"), t("msg.keep_one_project"))
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
        # Drop ticks for files that no longer exist (switching project or directory),
        # keep the rest so a rescan does not lose the selection either.
        self.checked_paths &= set(self.file_paths)
        self._sort_file_paths()

        # ── Sorting ────────────────────────────────────────────

    @staticmethod
    def _sort_label(key):
        """Sort key -> dropdown label in the current language (an unknown key falls back to the default)."""
        if key not in SORT_KEYS:
            key = DEFAULT_SORT
        return t("sort.time" if key == "time" else "sort.name")

    def _current_sort_key(self):
        """The sort mode currently selected in the dropdown (name / time)."""
        label = self.sort_var.get() if hasattr(self, 'sort_var') else ""
        for key in SORT_KEYS:
            if label == self._sort_label(key):
                return key
        return DEFAULT_SORT

    def _sort_file_paths(self):
        """Order by the current sort mode: name = ascending; time = most recently modified first."""
        try:
            if self._current_sort_key() == "time":
                self.file_paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            else:
                self.file_paths.sort(key=lambda p: os.path.basename(p).lower())
        except Exception:
            pass

    def _on_sort_change(self, event=None):
        """Switch the sort mode: re-sort the list immediately and persist it to the config."""
        self.projects_data["sort_by"] = self._current_sort_key()
        save_projects(self.projects_data)
        self._sort_file_paths()
        self._rebuild_checkbox_list()
        self.list_canvas.yview_moveto(0)
        self._log(t("log.sort_changed", label=self.sort_var.get()), "info")

    def _rebuild_checkbox_list(self):
        if not hasattr(self, 'list_frame'):
            return
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.check_vars = {}

        total = len(self.file_paths)
        self.table_count_label.config(text=t("list.count", n=total) if total else "")

        if not self.file_paths:
            source_dir = self.source_dir_var.get()
            if not source_dir or not os.path.isdir(source_dir):
                tk.Label(self.list_frame, text=t("list.no_dir"),
                         fg=self.TEXT_SEC, bg=self.CARD, font=FONT).pack(pady=40)
            else:
                tk.Label(self.list_frame, text=t("list.no_files"),
                         fg=self.TEXT_SEC, bg=self.CARD, font=FONT).pack(pady=40)
            return

        keyword = self.search_var.get().lower()
        if keyword == self._placeholder.lower():
            keyword = ""
        # Match the table name only, never the extension: every file ends with ".xlsx",
        # so keywords made of common letters (x / l / s) would otherwise match them all.
        visible = [p for p in self.file_paths
                   if not keyword
                   or keyword in os.path.splitext(os.path.basename(p))[0].lower()]

        if not visible:
            tk.Label(self.list_frame, text=t("list.no_match"),
                     fg=self.TEXT_SEC, bg=self.CARD, font=FONT).pack(pady=40)
            return

        for i, path in enumerate(visible):
            bg = self.ROW_EVEN if i % 2 == 0 else self.ROW_ODD
            row = tk.Frame(self.list_frame, bg=bg)
            row.pack(fill=tk.X)

            var = tk.BooleanVar(value=path in self.checked_paths)
            # Mirror every change into checked_paths: a row hidden by the search filter
            # keeps its state, and showing it again restores what the user had ticked.
            var.trace_add("write", lambda *_a, p=path, v=var: self._remember_check(p, v))
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

        self.table_count_label.config(text=t("list.count_match", total=total, n=len(visible)))

    def _remember_check(self, path, var):
        """Record a tick / un-tick in the persistent selection set."""
        if var.get():
            self.checked_paths.add(path)
        else:
            self.checked_paths.discard(path)

    def _on_search(self, *args):
        if not hasattr(self, 'list_frame'):
            return
        self._rebuild_checkbox_list()
        self.list_canvas.yview_moveto(0)

    # ── Mouse wheel ──────────────────────────────────────────────

    def _bind_mousewheel(self, event):
        self.list_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        if HAS_TOUCHPAD_SCROLL:
                        # Since Tk 9, trackpads / Magic Mouse / Magic Trackpad raise their own events
                        # and no longer send <MouseWheel> - without this binding scrolling simply does
            # not work on a Mac trackpad.
            self.list_canvas.bind_all("<TouchpadScroll>", self._on_touchpad_scroll)

    def _unbind_mousewheel(self, event):
        self.list_canvas.unbind_all("<MouseWheel>")
        if HAS_TOUCHPAD_SCROLL:
            self.list_canvas.unbind_all("<TouchpadScroll>")

    def _on_mousewheel(self, event):
        """Mouse wheel (unit conversion lives in platform_compat.wheel_units)."""
        self.list_canvas.yview_scroll(wheel_units(event.delta), "units")

    def _on_touchpad_scroll(self, event):
        """Smooth trackpad / Magic Mouse scrolling (Tk 9's new <TouchpadScroll>).

        Each event reports only a few pixels, so accumulate them and scroll one step
        at a time - otherwise a single swipe throws the list straight to the bottom.
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
        """Ticked tables, in list order.

        Reads the persistent set instead of the currently rendered rows, so filtering
        the list with the search box cannot silently shrink the export.
        """
        return [p for p in self.file_paths if p in self.checked_paths]

    # ── Export ───────────────────────────────────────────────────

    def _export_selected(self):
        paths = self._get_checked()
        if not paths:
            messagebox.showinfo(t("dlg.notice"), t("msg.no_selection"))
            return
        self._do_export(paths)

    def _export_all(self):
        if not self.file_paths:
            messagebox.showinfo(t("dlg.notice"), t("msg.nothing_to_export"))
            return
        self._do_export(self.file_paths)

    def _preflight(self, file_paths):
        """Read-only pre-flight: load every sheet and find all data errors in the cells.

        Checked content (see ``core.excel_reader._CHECKED_TYPES``):
        - ``table`` / ``any``: syntax errors in handwritten Lua (full-width brackets,
          JSON colons, missing commas ...)
        - ``number``: a non-numeric value (``100pcs`` / ``1,000``) that would silently
          turn into nil when exported

        This step **writes no file at all**, so aborting on a problem is clean - the
        target directory keeps no half-written output (and existing old files are not
        overwritten either). A pleasant side effect: every xlsx is loaded only once,
        and the write phase reuses the result.

        Returns ``(loaded, load_fail, error_count, dialog_lines)``:
        - ``loaded``: ``[(file_path, tables)]``, ``tables`` is ``None`` when that file failed to load
        - ``load_fail``: number of files that failed to load
        - ``error_count``: total number of data errors
        - ``dialog_lines``: simplified error lines for the dialog (file/sheet/cell/field -> error),
          while the log always gets the fullest version
        """
        total = len(file_paths)
        loaded = []
        load_fail = 0
        syntax_count = 0
        dialog_lines = []

        self.progress_var.set(0)
        self.progress_label.config(text=t("status.checking", i=0, n=total))

        for idx, fpath in enumerate(file_paths):
            try:
                tables = load_excel(fpath)
                for info in tables:
                    info["source_path"] = fpath
                                        # Typos (full-width brackets, JSON colons, missing commas...) are reported
                                        # right here, so the exported lua does not blow up inside the game and
                    # force a hunt for the offending cell.
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
            self.progress_label.config(text=t("status.checking", i=idx + 1, n=total))
            self.root.update_idletasks()

        return loaded, load_fail, syntax_count, dialog_lines

    def _warn_data_errors(self, count, lines=()):
        """Only warn when there are data errors - the export stops right there.

        ``showwarning`` (a single "OK") is used on purpose instead of ``askyesno``:
        there is **no "export anyway" option**, so half-finished lua never reaches the
        target directory and blows up inside the game.

        The dialog **lists the actual errors** (no digging through the log needed): up
        to ``_DIALOG_MAX_ERRORS`` of them, with one extra "and N more" line; the log
        gets all of them.
        """
        self._log(t("log.data_error_abort", n=count), "error")

        lines = list(lines)
        shown = lines[:_DIALOG_MAX_ERRORS]
        body = "\n".join(shown) if shown else ""
        if count > len(shown):
            more = count - len(shown)
            body += ("\n" if body else "") + t("dlg.data_error_more", n=more)

        msg = t("dlg.data_error_header", n=count) + "\n"
        if body:
            msg += "\n" + body + "\n"
        msg += "\n" + t("dlg.data_error_footer")

        messagebox.showwarning(t("dlg.data_error"), msg)

    def _do_export(self, file_paths):
        client_dir = self.client_dir_var.get()
        server_dir = self.server_dir_var.get()
        client_encoding = normalize_encoding(self.client_encoding_var.get())
        server_encoding = normalize_encoding(self.server_encoding_var.get())

        if not client_dir and not server_dir:
            messagebox.showerror(t("dlg.error"), t("msg.no_output_dir"))
            return
        if not file_paths:
            return

        total = len(file_paths)
        self._log(t("log.export_start", n=total, client=client_encoding,
                    server=server_encoding), "info")

                # ── 1/2 pre-flight (read only) ─────────────
        loaded, load_fail, syntax_count, error_lines = self._preflight(file_paths)
        if syntax_count:
            self._warn_data_errors(syntax_count, error_lines)
            self.progress_var.set(0)
            self.progress_label.config(text=t("status.aborted", n=syntax_count))
            return

                # ── 2/2 write files ──────────────────────────
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
            self.progress_label.config(text=t("status.exporting", i=idx + 1, n=total))
            self.root.update_idletasks()

        self._log(t("log.export_summary", ok=success_count, fail=fail_count), "info")
        self.progress_var.set(100)
        if fail_count:
            status = t("status.done_failed", n=fail_count)
        else:
            status = t("status.done")
        self.progress_label.config(text=status)

    # ── SVN ──────────────────────────────────────────────────────

    def _run_tortoise(self, command, clean_path, extra_args=()):
        """Run the command through TortoiseSVN's own window; returns False when TortoiseSVN is missing."""
        proc = _find_tortoise_proc()
        if not proc:
            return False
        subprocess.Popen([proc, f"/command:{command}", f"/path:{clean_path}", *extra_args])
        return True

    def _svn_on_mac(self, command):
        """macOS branch: run svn in Terminal. Returns True when it handled the call.

        A terminal rather than a silent subprocess, mirroring ``cmd /k`` on Windows:
        the user can see the progress and the output. The buttons are hidden when svn
        is missing (see _build_bottom_bar); the check here is only a safety net.
        """
        if not IS_MAC:
            return False

        source_dir = self.source_dir_var.get()
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror(t("dlg.error"), t("msg.no_source_dir"))
            return True

        svn = find_svn()
        if not svn:
            messagebox.showerror(t("dlg.error"), t("msg.no_svn"))
            return True

        clean_path = os.path.normpath(source_dir)
        try:
            run_svn_in_terminal(svn, command, clean_path)
        except Exception as e:
            messagebox.showerror(t("dlg.error"), t("msg.terminal_failed", err=e))
            return True
        self._log(t("log.svn_terminal", cmd=command, path=clean_path))
        return True

    def _svn_update(self):
        if self._svn_on_mac("update"):
            return
        source_dir = self.source_dir_var.get()
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror(t("dlg.error"), t("msg.no_source_dir"))
            return
        clean_path = os.path.normpath(source_dir)
                    # /closeonend:0 = keep the window open after the update so you can see which files changed
        if self._run_tortoise("update", clean_path, ("/closeonend:0",)):
            self._log(t("log.svn_update_opened", path=clean_path))
            return
                    # Safety net: fall back to the command line when TortoiseSVN is not installed
        self._log(t("log.svn_no_tortoise"), "error")
        subprocess.Popen(["cmd", "/k", f'cd /d "{clean_path}" && svn update'])

    def _svn_commit(self):
        if self._svn_on_mac("commit"):
            return
        source_dir = self.source_dir_var.get()
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror(t("dlg.error"), t("msg.no_source_dir"))
            return
        clean_path = os.path.normpath(source_dir)
        if self._run_tortoise("commit", clean_path):
            self._log(t("log.svn_commit_opened", path=clean_path))
            return
        try:
            subprocess.Popen(["svn", "commit"], cwd=source_dir)
        except Exception as e:
            messagebox.showerror(t("dlg.error"), t("msg.svn_commit_failed", err=e))

    # ── Log ──────────────────────────────────────────────────────

    def _on_callback_exception(self, exc, val, tb):
        """Uncaught exception in a Tk callback: log it, write it to disk, show a dialog.

        The default behaviour prints the traceback to stderr, but once packaged as a
        windowed app stderr is None - the symptom is "the window just disappeared when
        I clicked something" with nothing to investigate afterwards. This guarantees a
        trace is left behind.
        """
        detail = "".join(traceback.format_exception(exc, val, tb))
        summary = "%s: %s" % (exc.__name__, val)

        log_path = os.path.join(CONFIG_DIR, "error.log")
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n%s\n" % (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), summary, detail))
        except Exception:
            pass

        if hasattr(self, "log_text"):
            try:
                self._log(t("log.internal_error", err=summary), "error")
            except Exception:
                pass

        try:
            messagebox.showerror(
                t("dlg.error"), t("msg.internal_error", err=summary, path=log_path))
        except Exception:
            pass

    def _log(self, msg, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.root.update_idletasks()

    def _clear_log(self):
        """Clear the log pane and reset the progress bar / status bar (otherwise the log is empty but the status still shows the previous result)."""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.config(text=t("status.ready"))
        self.root.update_idletasks()

    def _install_placeholder(self):
        """Install the search placeholder for the current language (called again on a language change).

        The placeholder is really grey text pushed into the entry, so a language change
        must first recognise and replace the old text, otherwise leftovers of the
        previous language stay behind; real user input is left untouched.
        """
        new_placeholder = t("search.placeholder", accel=shortcut("F"))
        entry, var = self.search_entry, self.search_var
        showing_placeholder = (not var.get()) or var.get() == self._placeholder

        def on_focus_in(e):
            if var.get() == self._placeholder:
                var.set("")
                entry.config(fg=self.TEXT)

        def on_focus_out(e):
            if not var.get():
                var.set(self._placeholder)
                entry.config(fg=self.TEXT_SEC)

        self._placeholder = new_placeholder
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        if showing_placeholder:
            var.set(new_placeholder)
            entry.config(fg=self.TEXT_SEC)
