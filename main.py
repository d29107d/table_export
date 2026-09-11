import sys
import os
import json
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ttkbootstrap as ttkb
from ttkbootstrap.constants import INFO, WARNING, SUCCESS, DANGER, PRIMARY

from gui.main_window import MainWindow

DEFAULT_THEME = "litera"
DARK_THEME = "darkly"


def _get_exe_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


THEME_PATH = os.path.join(_get_exe_dir(), "config", "theme.json")


def load_theme():
    if os.path.exists(THEME_PATH):
        try:
            with open(THEME_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("theme", DEFAULT_THEME)
        except Exception:
            pass
    return DEFAULT_THEME


def _maximize(root):
    """默认最大化打开。

    Windows 用 ``state("zoomed")``；X11 用 ``attributes("-zoomed")``；
    都不支持时退回"铺满整个屏幕"的 geometry 写法。
    """
    try:
        root.state("zoomed")
        return
    except tk.TclError:
        pass
    try:
        root.attributes("-zoomed", True)
        return
    except tk.TclError:
        pass
    ws = root.winfo_screenwidth()
    hs = root.winfo_screenheight()
    root.geometry(f"{ws}x{hs}+0+0")


def main():
    initial_theme = load_theme()

    root = ttkb.Window(title="导表管理器", themename=initial_theme, iconphoto=None,
                       size=(1200, 750), minsize=(1000, 600))

    app = MainWindow(root, initial_theme)
    _maximize(root)
    root.mainloop()


if __name__ == "__main__":
    main()
