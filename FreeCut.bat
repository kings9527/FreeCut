@echo off
rem FreeCut 一键启动（Windows）
rem 双击运行：优先用已存在的虚拟环境，否则用系统 Python。
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

rem FFmpeg：优先用环境变量，其次 PATH 里的 ffmpeg
if not defined FREECUT_FFMPEG (
    where ffmpeg >nul 2>nul && set "FREECUT_FFMPEG=ffmpeg"
)

if "%~1"=="" (
    "%PY%" run_freecut.py gui
) else (
    "%PY%" run_freecut.py %*
)
if errorlevel 1 (
    echo.
    echo FreeCut 退出，错误码 %errorlevel%
    pause
)
endlocal
