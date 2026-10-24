# -*- mode: python ; coding: utf-8 -*-

# 预览音频用 sounddevice（portaudio）：DLL 放在 _sounddevice_data 里，
# 不显式收集的话打包版会静音（源码里却是好的）。
from PyInstaller.utils.hooks import collect_data_files

_sd_data = collect_data_files('sounddevice')

a = Analysis(
    ['run_freecut.py'],
    pathex=[],
    binaries=[],
    datas=[('freecut/data', 'freecut/data'),
           # 语音识别后端随包分发：否则「自动字幕」在打包版里不可用。
           # 只带 base 模型（141 MB）；small 太大，留给用户按需下载。
           # 只带 base 模型（stage 目录在 build/asr-bundle，由 tools/stage_asr.py 生成）
           ('build/asr-bundle/Whisper-Faster',
            'tools/whisper-faster/app/Whisper-Faster'),] + _sd_data,
    hiddenimports=['tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox',
                   'tkinter.simpledialog', 'PIL._tkinter_finder',
                   # 预览音频（缺了它打包版会静音）
                   'sounddevice', '_sounddevice_data',
                   # Qt 版界面（第 200 轮起是默认入口）
                   'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'],
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
    [],
    exclude_binaries=True,
    name='FreeCutDir',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='FreeCutDir',
)
