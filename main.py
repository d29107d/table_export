import sys
import os
import json
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ttkbootstrap as ttkb
from ttkbootstrap.constants import INFO, WARNING, SUCCESS, DANGER, PRIMARY

from gui.main_window import CONFIG_PATH, DEFAULT_THEME, MainWindow, normalize_theme
from gui.platform_compat import IS_MAC, config_dir

from core.i18n import language_from_config, set_language, t


#: 主题偏好与 projects.json 放同一个目录（macOS 在 Application Support 下，
#: 不再往 .app 内部写 —— 那会破坏签名，换版本时也会丢）
THEME_PATH = os.path.join(config_dir(), "theme.json")


def load_theme():
    """读取主题偏好。

    要做一次归一化：旧配置里可能存着 ttkbootstrap 1.x 的遗留名（litera / darkly），
    它们能被加载、但不在 ``theme_names()`` 里 —— 主题菜单照那个列表生成，
    留着旧名会导致"当前主题"一项都勾不上，还会打 DeprecationWarning。
    """
    if os.path.exists(THEME_PATH):
        try:
            with open(THEME_PATH, "r", encoding="utf-8") as f:
                return normalize_theme(json.load(f).get("theme"))
        except Exception:
            pass
    return DEFAULT_THEME


def _maximize(root):
    """默认最大化打开。

    ``state("zoomed")`` 在三个平台上都是正解：Windows 支持；macOS 的 aqua 从
    Tk 9 起也支持 —— 实测 macOS 26 + Tk 9.0.3 下 400x300+60+60 会变成
    1728x1056+0+33，正好铺满工作区并自动避开菜单栏。

    ``attributes("-zoomed")`` 只留给 X11：Tk 9 的 aqua 已经把这个属性整个移除，
    在 macOS 上调必抛 TclError，所以直接跳过，省一次无谓的异常。
    """
    try:
        root.state("zoomed")
        return
    except tk.TclError:
        pass
    if not IS_MAC:
        try:
            root.attributes("-zoomed", True)
            return
        except tk.TclError:
            pass
    root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")


def main():
    initial_theme = load_theme()
    # 语言要在建窗口之前定下来 —— 窗口标题、菜单都是建的时候就要用
    initial_language = set_language(language_from_config(CONFIG_PATH))

    root = ttkb.Window(title=t("app.title"), themename=initial_theme, iconphoto=None,
                       size=(1200, 750), minsize=(1000, 600))

    app = MainWindow(root, initial_theme, initial_language)
    _maximize(root)
    root.mainloop()


if __name__ == "__main__":
    main()
