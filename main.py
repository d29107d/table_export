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


#: The theme preference lives in the same directory as projects.json (macOS: under
#: Application Support - never inside the .app, which breaks its signature and loses
#: the file on every version swap)
THEME_PATH = os.path.join(config_dir(), "theme.json")


def load_theme():
    """Read the theme preference.

    The value is normalised: an old config may still hold a ttkbootstrap 1.x legacy
    name (litera / darkly). Those can still be loaded but do not appear in
    ``theme_names()`` - and the theme menu is built from that list, so a legacy name
    leaves no menu entry checked and also triggers a DeprecationWarning.
    """
    if os.path.exists(THEME_PATH):
        try:
            with open(THEME_PATH, "r", encoding="utf-8") as f:
                return normalize_theme(json.load(f).get("theme"))
        except Exception:
            pass
    return DEFAULT_THEME


def _maximize(root):
    """Open maximised.

    ``state("zoomed")`` is the right call on all three platforms: Windows supports
    it, and macOS aqua has supported it since Tk 9 - measured on macOS 26 with Tk
    9.0.3, a 400x300+60+60 window becomes 1728x1056+0+33, filling the work area and
    staying clear of the menu bar automatically.

    ``attributes("-zoomed")`` is kept for X11 only: Tk 9's aqua removed that
    attribute entirely, so calling it on macOS always raises TclError, and skipping
    it saves a pointless exception.
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
        # The language must be settled before the window is built - the title and the menus need it
    initial_language = set_language(language_from_config(CONFIG_PATH))

    root = ttkb.Window(title=t("app.title"), themename=initial_theme, iconphoto=None,
                       size=(1200, 750), minsize=(1000, 600))

    app = MainWindow(root, initial_theme, initial_language)
    _maximize(root)
    root.mainloop()


if __name__ == "__main__":
    main()
