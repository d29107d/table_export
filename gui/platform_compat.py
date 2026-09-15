"""Platform adaptation layer.

The project used to run on Windows only, and Windows-specific things (the YaHei
font, TortoiseSVN, ``cmd /k``, conda's DLLs, the ``config`` directory next to the
exe) were scattered all over the code base. They are collected here; upper layers
only ask "what should I use" instead of testing the platform themselves.

Design rule: **Windows behaviour is unchanged, character for character** - every
branch still takes the old path on Windows, and only the macOS side is new.
"""

import os
import shlex
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import font as tkfont

IS_MAC = sys.platform == 'darwin'
IS_WIN = sys.platform.startswith('win')

#: How the platform spells a shortcut in the UI: macOS uses the Command glyph (⌘),
#: Windows spells out "Ctrl". Keep hints in the UI and the real bindings in sync.
MOD_KEY = '⌘' if IS_MAC else 'Ctrl'

#: How the platform spells Shift in a hint: the glyph on macOS (⌘⇧E), the word on
#: Windows (Ctrl+Shift+E).
SHIFT_KEY = '⇧' if IS_MAC else 'Shift'


def shortcut(key, shift=False):
    """Render a shortcut hint.

    ``shortcut('F')`` -> ``⌘F`` on macOS, ``Ctrl+F`` elsewhere;
    ``shortcut('E', shift=True)`` -> ``⌘⇧E`` / ``Ctrl+Shift+E``.
    """
    parts = [MOD_KEY] + ([SHIFT_KEY] if shift else []) + [key]
    return ''.join(parts) if IS_MAC else '+'.join(parts)


#: Since Tk 9, trackpads / precision devices raise their own ``<TouchpadScroll>`` event and no longer send ``<MouseWheel>``
HAS_TOUCHPAD_SCROLL = tk.TkVersion >= 8.7

#: Application name, used for the macOS config directory
APP_NAME = 'table_exporter'

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


# ── Fonts ──────────────────────────────────────────────────────────

#: UI font candidates in priority order. YaHei on Windows, PingFang on macOS;
#: the rest are fallbacks so a stripped-down system does not fall back to the default font everywhere.
_UI_FAMILIES = {
    'win32': ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', 'Arial'),
    'darwin': ('PingFang SC', 'Hiragino Sans GB', 'Helvetica Neue', 'Helvetica'),
}

#: Monospace font candidates (log pane, error details)
_MONO_FAMILIES = {
    'win32': ('Consolas', 'Courier New'),
    'darwin': ('Menlo', 'Monaco', 'Courier New'),
}


def resolve_font_families(root=None):
    """Pick the (UI font family, monospace family) that really exist on this machine.

    Tk does **not** raise for a missing font, it silently falls back. The hard-coded
    ``Microsoft YaHei UI`` and ``Consolas`` do not exist on macOS, so the whole
    Chinese UI turned into the default font and the log pane lost its monospace
    alignment - without any error, which makes it very hard to notice. Hence the
    runtime probe.

    On Windows ``Microsoft YaHei UI`` exists, so the result is identical to the
    hard-coded values.
    """
    key = 'darwin' if IS_MAC else 'win32'

    try:
        available = {name.lower() for name in tkfont.families(root)}
    except Exception:
        available = set()

    def pick(candidates, fallback_font):
        for name in candidates:
            if name.lower() in available:
                return name
                # When none of the candidates exists, fall back to Tk's own default family (a
                # real family name, which beats gambling on the fallback behaviour of a name
        # that does not exist)
        try:
            return tkfont.nametofont(fallback_font, root).actual('family')
        except Exception:
            return candidates[0]

    return (pick(_UI_FAMILIES[key], 'TkDefaultFont'),
            pick(_MONO_FAMILIES[key], 'TkFixedFont'))


# ── Config directory ─────────────────────────────────────────────

def config_dir():
    """Runtime config directory (holds projects.json / theme.json).

    Windows: keeps the ``config/`` next to the exe (or the source root) - the
    portable-app convention, unchanged.

    macOS: the directory next to the exe can no longer be used. Inside a ``.app``
    that is ``Xxx.app/Contents/MacOS/config`` - PyInstaller ad-hoc signs the bundle,
    and writing there invalidates the signature; on top of that the whole directory
    is replaced when the user installs a new version, losing the settings along with
    it. So it moves to ``~/Library/Application Support/``.
    """
    if IS_MAC:
        return os.path.join(os.path.expanduser('~'), 'Library',
                            'Application Support', APP_NAME)
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'config')
    return os.path.join(_PROJECT_ROOT, 'config')


def bundled_config_dir():
    """Fallback config bundled into the package (used as a template on first launch, then config_dir())."""
    if getattr(sys, 'frozen', False):
        return os.path.join(getattr(sys, '_MEIPASS', _PROJECT_ROOT), 'config')
    return os.path.join(_PROJECT_ROOT, 'config')


# ── SVN ──────────────────────────────────────────────────────────────

#: Common svn install locations.
#: **shutil.which alone is not enough**: a GUI app launched from Finder / Dock
#: inherits launchd's minimal PATH (``/usr/bin:/bin:/usr/sbin:/sbin``), which does
#: not include Homebrew's ``/opt/homebrew/bin`` - svn works in a terminal, yet the
#: app cannot find it.
_SVN_CANDIDATES = (
    '/opt/homebrew/bin/svn',   # Homebrew (Apple Silicon)
    '/usr/local/bin/svn',      # Homebrew (Intel) / built by hand
    '/opt/local/bin/svn',      # MacPorts
    '/usr/bin/svn',            # Bundled with Xcode before 26 (now removed)
)


def find_svn():
    """Locate the svn executable; returns None when it is not found. macOS only."""
    exe = shutil.which('svn')
    if exe:
        return exe
    for path in _SVN_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def run_svn_in_terminal(svn_exe, command, path):
    """Run ``svn <command>`` inside Terminal.app.

    The counterpart of ``cmd /k`` on Windows: the user can see the progress and the
    output instead of waiting with no feedback. The first call makes macOS ask for
    permission to control Terminal; afterwards it stops asking.

    :param command: ``update`` / ``commit`` / ``status`` etc.
    :param path: working copy directory
    """
    shell_cmd = f'cd {shlex.quote(path)} && {shlex.quote(svn_exe)} {command}'
        # AppleScript strings accept double quotes only, so backslashes and double quotes need escaping
    escaped = shell_cmd.replace('\\', '\\\\').replace('"', '\\"')
    script = ('tell application "Terminal"\n'
              '    activate\n'
              f'    do script "{escaped}"\n'
              'end tell')
    subprocess.Popen(['osascript', '-e', script])


# ── Mouse wheel ────────────────────────────────────────────────────

#: A trackpad event reports only a few pixels; accumulate up to this threshold before scrolling one step
TOUCHPAD_STEP = 40


def wheel_units(delta):
    """Convert a ``<MouseWheel>`` delta into a number of scroll units.

    Since Tk 9 every platform (macOS included) normalises it to a multiple of +/-120;
    Tk 8.6 on macOS reports 1 per notch. A magnitude threshold gives "one notch = 1
    unit" for both scales.

    **A small delta on Windows + Tk 8.6 must stay 0**: such a delta comes from a
    precision touchpad (Windows reports two-finger scrolling as <MouseWheel> with a
    delta that is not a multiple of 120) and the old ``int(-delta/120)`` yielded 0.
    Returning -delta here would be treated as that many single steps and fling the
    list around. Note that this project's Canvas has no ``yscrollincrement``, so one
    unit is a tenth of the canvas height.
    """
    if abs(delta) >= 120:
        return -int(delta / 120)
    if IS_MAC:
                # Tk 8.6 aqua: one notch reports +/-1 (Tk 9 normalises it and takes the branch above)
        return -int(delta)
        # Windows Tk 8.6: a small delta comes from a precision touchpad - keep the old
        # behaviour, otherwise a pixel shift would be treated as a "step" and scroll wildly
    return 0


def touchpad_dy(delta):
    """Extract the vertical displacement from a packed ``<TouchpadScroll>`` delta.

    Tk packs both axes into one integer: the high 16 bits are dx and the low 16 bits
    are dy, each interpreted as a signed 16-bit value.
    """
    dy = delta & 0xFFFF
    if dy >= 0x8000:
        dy -= 0x10000
    return dy
