# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows / macOS 通用）。

打包命令（两个平台都是这一条，PyInstaller 不能交叉编译）：

    Windows:  .venv\\Scripts\\python -m PyInstaller --noconfirm --clean table_exporter.spec
    macOS:    .venv/bin/python     -m PyInstaller --noconfirm --clean table_exporter.spec

产物：
    Windows:  dist/table_exporter.exe
    macOS:    dist/table_exporter.app

平台差异全部收在这一处（下面用 IS_MAC 分叉）：
- Windows 走 conda：dll 放在 ``Library/bin``，必须显式带上；产物是单文件 exe，
  与改动前逐字一致。
- macOS 走 onedir + COLLECT + BUNDLE 出 .app；tcl/tk 是 .framework，由
  PyInstaller 的 ``_tkinter`` hook 自动收集（6.18+ 支持 Tcl/Tk 9），不用手拷。

**macOS 不要用 onefile**：每次启动都要把 Tcl/Tk 框架解压到临时目录，启动明显
变慢；而且 .app 才是 macOS 的正常形态 —— 能设图标、能被 Launchpad 识别、能签名。
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

SPEC_DIR = SPECPATH                                    # spec 所在目录
IS_MAC = sys.platform == 'darwin'
IS_WIN = sys.platform.startswith('win')


def p(*parts):
    """拼路径并统一成正斜杠，避免反斜杠在 spec 里被当转义符。"""
    return os.path.join(*parts).replace('\\', '/')


# ── 数据文件 ─────────────────────────────────────────────────────────

# ttkbootstrap 没有官方 PyInstaller hook（社区做法是命令行加 --collect-all）。
# 用 collect_data_files 让 PyInstaller 自己去定位资源，**不要手写 assets 子路径**：
# 2.x 相对 1.x 调整过资源目录结构，写死会在升级后静默失效。
datas = collect_data_files('ttkbootstrap')

datas += [
    (p(SPEC_DIR, 'core'), 'core'),
    (p(SPEC_DIR, 'gui'), 'gui'),
]
# config/ 是本机配置（不入库），存在才作为兜底打进包；
# 程序首次启动本来就会自己重建该目录。
if os.path.isdir(p(SPEC_DIR, 'config')):
    datas.append((p(SPEC_DIR, 'config'), 'config'))


# ── 二进制 ───────────────────────────────────────────────────────────

binaries = []

if IS_WIN:
    #: tcl/tk 及其它需要随包分发的动态库（conda 把 dll 放在 Library/bin）
    DLLS = (
        'tcl86t.dll',
        'tk86t.dll',
        'libcrypto-3-x64.dll',
        'libssl-3-x64.dll',
        'libexpat.dll',
        'ffi.dll',
        'libbz2.dll',
        'liblzma.dll',
    )
    LIB_BIN = os.path.join(sys.base_prefix, 'Library', 'bin')
    # 加 isfile 过滤：conda 结构万一变了就跳过那一项，而不是让整个打包失败
    binaries = [(p(LIB_BIN, name), '.') for name in DLLS
                if os.path.isfile(p(LIB_BIN, name))]
# macOS 留空：tcl/tk 由 PyInstaller 的 _tkinter hook 收集，不需要手工列 dylib


a = Analysis([p(SPEC_DIR, 'main.py')],
             pathex=[p(SPEC_DIR)],
             binaries=binaries,
             datas=datas,
             hiddenimports=['tkinter', 'tkinter.ttk', 'ttkbootstrap',
                            'ttkbootstrap.constants', 'PIL', 'PIL._tkinter_finder',
                            'gui.platform_compat'],
             hookspath=[],
             runtime_hooks=[],
             excludes=['pkg_resources', 'setuptools'],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)

pyz = PYZ(a.pure, a.zipped_data,
          cipher=block_cipher)


if IS_MAC:
    # onedir + BUNDLE
    exe = EXE(pyz,
              a.scripts,
              [],
              exclude_binaries=True,
              name='table_exporter',
              debug=False,
              bootloader_ignore_signals=False,
              strip=False,
              upx=False,               # UPX 对 arm64 的 Mach-O 支持很差，还会干扰签名
              console=False)

    coll = COLLECT(exe,
                   a.binaries,
                   a.zipfiles,
                   a.datas,
                   strip=False,
                   upx=False,
                   upx_exclude=[],
                   name='table_exporter')

    app = BUNDLE(coll,
                 # 目录名保持 ASCII，中文只放进 CFBundleDisplayName ——
                 # Finder 里照样显示中文，但避免路径编码带来的麻烦
                 name='table_exporter.app',
                 icon=None,            # 工程里没有 .icns；需要的话先由 .ico 转一份
                 bundle_identifier='com.cxj.tableexporter',
                 info_plist={
                     'CFBundleName': '导表管理器',
                     'CFBundleDisplayName': '导表管理器',
                     'CFBundleShortVersionString': '2.0',
                     'CFBundleVersion': '2.0',
                     # Retina 下按原生分辨率渲染，否则整个界面是模糊的
                     'NSHighResolutionCapable': True,
                     # 允许跟随系统深色模式（Tk 9 的 aqua 支持 -appearance）
                     'NSRequiresAquaSystemAppearance': False,
                     'LSMinimumSystemVersion': '11.0',
                     'LSApplicationCategoryType': 'public.app-category.developer-tools',
                 })
else:
    # Windows：单文件 exe，与改动前完全一致
    exe = EXE(pyz,
              a.scripts,
              a.binaries,
              a.zipfiles,
              a.datas,
              [],
              name='table_exporter',
              debug=False,
              bootloader_ignore_signals=False,
              strip=False,
              upx=True,
              upx_exclude=[],
              runtime_tmpdir=None,
              console=False)
