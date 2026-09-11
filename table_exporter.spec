# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

路径全部相对化，不再写死开发机路径：
- 源码 / 资源目录取 ``SPECPATH``（spec 文件所在目录）；
- tcl/tk/ssl 等 DLL 与 ttkbootstrap 资源取当前 Python 环境的 ``sys.base_prefix``，
  因此**必须用装了 tkinter 的那套 Python 来执行本 spec**（本工程是 miniconda）。

打包命令：
    python -m PyInstaller --noconfirm --clean table_exporter.spec
产物：
    dist/table_exporter.exe
"""

import os
import sys

block_cipher = None

SPEC_DIR = SPECPATH                                  # spec 所在目录
PY_PREFIX = sys.base_prefix                          # 当前 Python 安装根
LIB_BIN = os.path.join(PY_PREFIX, 'Library', 'bin')  # conda 风格的 DLL 目录
TTK_ASSETS = os.path.join(PY_PREFIX, 'Lib', 'site-packages',
                          'ttkbootstrap', 'assets')

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


def p(*parts):
    """拼路径并统一成正斜杠，避免反斜杠在 spec 里被当转义符。"""
    return os.path.join(*parts).replace('\\', '/')


datas = [
    (p(SPEC_DIR, 'core'), 'core'),
    (p(SPEC_DIR, 'gui'), 'gui'),
]
# config/ 是本机配置（不入库），存在才作为兜底打进包；
# 程序首次启动本来就会自己重建该目录。
if os.path.isdir(p(SPEC_DIR, 'config')):
    datas.append((p(SPEC_DIR, 'config'), 'config'))

a = Analysis([p(SPEC_DIR, 'main.py')],
             pathex=[p(SPEC_DIR)],
             binaries=[(p(LIB_BIN, name), '.') for name in DLLS],
             datas=datas + [(p(TTK_ASSETS), 'ttkbootstrap/assets')],
             hiddenimports=['tkinter', 'tkinter.ttk', 'ttkbootstrap',
                            'ttkbootstrap.constants', 'PIL', 'PIL._tkinter_finder'],
             hookspath=[],
             runtime_hooks=[],
             excludes=['pkg_resources', 'setuptools'],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
          cipher=block_cipher)
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
