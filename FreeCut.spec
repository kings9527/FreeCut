# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_freecut.py'],
    pathex=[],
    binaries=[],
    datas=[('freecut/data', 'freecut/data'),
           # 语音识别后端随包分发：否则「自动字幕」在打包版里不可用。
           # 只带 base 模型（141 MB）；small 太大，留给用户按需下载。
           # onefile 不带语音识别后端：加了之后每次启动都要多解压 225 MB
           # （实测 6.5s → 10.8s），便携版宁可让「自动字幕」提示缺后端。
           ],
    hiddenimports=['tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.simpledialog', 'PIL._tkinter_finder', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='FreeCut',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
