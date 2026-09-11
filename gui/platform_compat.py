"""平台适配层。

本项目原本只在 Windows 上跑，Windows 专有的东西（雅黑字体、TortoiseSVN、
``cmd /k``、conda 的 dll、exe 同级的 config 目录）散落在各处。这里集中收口，
上层只问"该用什么"，不自己判断平台。

设计原则：**Windows 行为逐字不变** —— 每个分支在 Windows 上走到的都是原来的
那条路，新增的只是 macOS 那一侧。
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

#: Tk 9 起给触摸板 / 精密设备单开了 ``<TouchpadScroll>`` 事件，它们不再发 ``<MouseWheel>``
HAS_TOUCHPAD_SCROLL = tk.TkVersion >= 8.7

#: 应用名，用于 macOS 的配置目录
APP_NAME = 'table_exporter'

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


# ── 字体 ─────────────────────────────────────────────────────────────

#: 界面字体候选，按优先级。Windows 用雅黑，macOS 用苹方；
#: 后面几个是兜底，防止在精简过的系统上整片回退到默认字体。
_UI_FAMILIES = {
    'win32': ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', 'Arial'),
    'darwin': ('PingFang SC', 'Hiragino Sans GB', 'Helvetica Neue', 'Helvetica'),
}

#: 等宽字体候选（日志区、错误明细用）
_MONO_FAMILIES = {
    'win32': ('Consolas', 'Courier New'),
    'darwin': ('Menlo', 'Monaco', 'Courier New'),
}


def resolve_font_families(root=None):
    """挑出本机真实存在的 (界面字体族, 等宽字体族)。

    Tk 对不存在的字体**不报错，只静默回退**。原来写死的 ``Microsoft YaHei UI``
    和 ``Consolas`` 在 macOS 上都没有，中文界面会整片变成默认字体、日志区的
    等宽对齐也会失效 —— 而且不报任何错，很难发现。所以改成运行期探测。

    Windows 上 ``Microsoft YaHei UI`` 存在，返回值与写死时完全一致。
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
        # 候选一个都没有时，退回 Tk 自己的默认字体族（同样是真实存在的族名，
        # 比继续拿一个不存在的名字去赌回退行为要踏实）
        try:
            return tkfont.nametofont(fallback_font, root).actual('family')
        except Exception:
            return candidates[0]

    return (pick(_UI_FAMILIES[key], 'TkDefaultFont'),
            pick(_MONO_FAMILIES[key], 'TkFixedFont'))


# ── 配置目录 ─────────────────────────────────────────────────────────

def config_dir():
    """运行期配置目录（projects.json / theme.json 放这儿）。

    Windows：沿用 exe（或源码根）同级的 ``config/`` —— 绿色版习惯，原样不动。

    macOS：不能再用 exe 同级目录。打成 ``.app`` 后那是
    ``Xxx.app/Contents/MacOS/config`` —— PyInstaller 会自动给 .app 做 ad-hoc
    签名，往里写文件会让签名失效；而且用户换新版本时整个目录会被替换，配置
    跟着丢。所以放到 ``~/Library/Application Support/`` 下。
    """
    if IS_MAC:
        return os.path.join(os.path.expanduser('~'), 'Library',
                            'Application Support', APP_NAME)
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'config')
    return os.path.join(_PROJECT_ROOT, 'config')


def bundled_config_dir():
    """打包进包里的兜底 config（首次启动时当模板用，之后用 config_dir()）。"""
    if getattr(sys, 'frozen', False):
        return os.path.join(getattr(sys, '_MEIPASS', _PROJECT_ROOT), 'config')
    return os.path.join(_PROJECT_ROOT, 'config')


# ── SVN ──────────────────────────────────────────────────────────────

#: svn 的常见安装位置。
#: **不能只靠 shutil.which**：从 Finder / Dock 启动的 GUI 应用继承的是 launchd
#: 的精简 PATH（``/usr/bin:/bin:/usr/sbin:/sbin``），Homebrew 的
#: ``/opt/homebrew/bin`` 不在里面 —— 终端里敲 svn 没问题，程序里却找不到。
_SVN_CANDIDATES = (
    '/opt/homebrew/bin/svn',   # Homebrew (Apple Silicon)
    '/usr/local/bin/svn',      # Homebrew (Intel) / 手工编译
    '/opt/local/bin/svn',      # MacPorts
    '/usr/bin/svn',            # Xcode 26 之前自带（现已移除）
)


def find_svn():
    """定位 svn 可执行文件；找不到返回 None。仅 macOS 用。"""
    exe = shutil.which('svn')
    if exe:
        return exe
    for path in _SVN_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def run_svn_in_terminal(svn_exe, command, path):
    """在 Terminal.app 里跑 ``svn <command>``。

    对应 Windows 那边的 ``cmd /k``：让用户看得见进度和输出，而不是一个没有
    反馈的等待。首次调用 macOS 会弹「允许 Terminal 控制」的授权，同意后不再问。

    :param command: ``update`` / ``commit`` / ``status`` 等
    :param path: 工作副本目录
    """
    shell_cmd = f'cd {shlex.quote(path)} && {shlex.quote(svn_exe)} {command}'
    # AppleScript 的字符串只能用双引号，所以这里要转义反斜杠和双引号
    escaped = shell_cmd.replace('\\', '\\\\').replace('"', '\\"')
    script = ('tell application "Terminal"\n'
              '    activate\n'
              f'    do script "{escaped}"\n'
              'end tell')
    subprocess.Popen(['osascript', '-e', script])


# ── 滚轮 ─────────────────────────────────────────────────────────────

#: 触摸板事件每个只报几像素，累积到这个阈值才滚一格
TOUCHPAD_STEP = 40


def wheel_units(delta):
    """把 ``<MouseWheel>`` 的 delta 换算成滚动单位数。

    Tk 9 起各平台（含 macOS）都归一化成 ±120 的倍数；Tk 8.6 的 macOS 则是
    每个刻度报 1。用阈值判断，两种量纲都能得到"一格 = 1 单位"。

    注意本项目的 Canvas 没设 ``yscrollincrement``，所以一个 unit 就是画布
    高度的 1/10。
    """
    if abs(delta) >= 120:
        delta = int(delta / 120)
    return -int(delta)


def touchpad_dy(delta):
    """从 ``<TouchpadScroll>`` 的打包 delta 里取出垂直位移。

    Tk 把两个轴的像素位移打包进一个整数：高 16 位是 dx、低 16 位是 dy，
    各按有符号 16 位解释。
    """
    dy = delta & 0xFFFF
    if dy >= 0x8000:
        dy -= 0x10000
    return dy
