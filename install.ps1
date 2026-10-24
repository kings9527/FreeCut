# FreeCut 安装脚本（Windows PowerShell）
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# 做的事：建虚拟环境 -> 装依赖 -> 报告 ffmpeg 状态。不修改系统 PATH，不装全局包。

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "== FreeCut 安装 ==" -ForegroundColor Cyan

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "找不到 python，请先安装 Python 3.9+（https://www.python.org/downloads/）" -ForegroundColor Red
    exit 1
}
Write-Host ("Python: " + (& python --version))

if (-not (Test-Path ".venv")) {
    Write-Host "创建虚拟环境 .venv ..."
    & python -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "虚拟环境创建失败" -ForegroundColor Red
    exit 1
}

Write-Host "安装依赖（numpy / Pillow）..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements.txt

Write-Host ""
Write-Host "== FFmpeg ==" -ForegroundColor Cyan
$ff = $env:FREECUT_FFMPEG
if (-not $ff) {
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($cmd) {
        $ff = $cmd.Source
    } else {
        $jy = Join-Path $env:LOCALAPPDATA "JianyingPro\apps"
        if (Test-Path $jy) {
            $cand = Get-ChildItem $jy -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
                    Sort-Object FullName -Descending | Select-Object -First 1
            if ($cand) { $ff = $cand.FullName }
        }
    }
}
if ($ff) {
    Write-Host ("找到 ffmpeg: " + $ff) -ForegroundColor Green
    Write-Host ("提示：可设置环境变量 FREECUT_FFMPEG 固定这个路径")
} else {
    Write-Host "没有找到 ffmpeg。FreeCut 需要它来解码/编码。" -ForegroundColor Yellow
    Write-Host "下载一份放进 PATH，或设置 FREECUT_FFMPEG 指向 ffmpeg.exe"
}

Write-Host ""
Write-Host "安装完成。启动方式：" -ForegroundColor Cyan
Write-Host "  双击 FreeCut.bat          （图形界面）"
Write-Host "  .venv\Scripts\python.exe run_freecut.py --help"
