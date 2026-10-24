# 打包 FreeCut（onedir 推荐 / onefile 便携）
#
# 统一走 .spec 文件：spec 是唯一事实来源（里面已经含"语音识别后端 + base 模型"
# 的 datas 配置），不再用一长串命令行参数，避免两条构建路径慢慢分裂。
#
# 用法：
#   .\build_exe.ps1                 # onedir（推荐，0.5 秒启动，语音识别开箱可用）
#   .\build_exe.ps1 -Mode onefile   # 单文件便携版（7 秒启动，不带语音后端）
#   .\build_exe.ps1 -WithFfmpeg "C:\path\to\ffmpeg.exe"

param(
    [ValidateSet("onedir", "onefile")]
    [string]$Mode = "onedir",
    [string]$WithFfmpeg = "",
    [switch]$SkipStage
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 控制台是 GBK，Python 输出 UTF-8 才不会变成乱码
$env:PYTHONIOENCODING = "utf-8"

$python = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# 1) 语音后端整理（只带 base 模型，225 MB）
if (-not $SkipStage) {
    Write-Host "== 整理语音识别后端 ==" -ForegroundColor Cyan
    & $python "tools\stage_asr.py"
    if ($LASTEXITCODE -ne 0) { throw "stage_asr 失败" }
}

# 2) PyInstaller：用 spec，不再手拼参数
$spec = if ($Mode -eq "onefile") { "FreeCut.spec" } else { "FreeCutDir.spec" }
Write-Host "== PyInstaller ($Mode, $spec) ==" -ForegroundColor Cyan
& $python -m PyInstaller $spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

# 3) 可选：把独立构建的 ffmpeg 放进产物
if ($WithFfmpeg) {
    if (-not (Test-Path $WithFfmpeg)) { throw "找不到 $WithFfmpeg" }
    $dest = if ($Mode -eq "onefile") { "dist\ffmpeg.exe" } else { "dist\FreeCutDir\ffmpeg.exe" }
    Copy-Item $WithFfmpeg $dest -Force
    Write-Host "  已放入 ffmpeg.exe（必须是独立构建；剪映自带的是 shim）"
}

# 4) 写 dist\读我.txt（体积与启动时间现测）
Write-Host "== 生成 dist\读我.txt ==" -ForegroundColor Cyan
& $python "tools\write_dist_readme.py"

Write-Host "== 完成 ==" -ForegroundColor Green