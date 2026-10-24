"""在 ``dist/`` 里写一份中文说明，告诉用户该拿哪个产物。

打包完之后跑一次：

```powershell
python tools\\write_dist_readme.py
```

它不去猜数字——体积和启动时间都是现测的（启动时间取 `--version` 三次的中位数，
测不出就写"未测"）。这样 ``dist/读我.txt`` 不会像 README 那样慢慢过时。
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ONEDIR = DIST / "FreeCutDir" / "FreeCutDir.exe"
ONEFILE = DIST / "FreeCut.exe"


def _dir_mb(path: Path) -> float:
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return total / 1024 / 1024


def _startup_ms(exe: Path, runs: int = 3) -> "int | None":
    if not exe.is_file():
        return None
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        try:
            subprocess.run([str(exe), "--version"], timeout=120,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception:
            return None
        times.append((time.perf_counter() - t0) * 1000)
    return int(statistics.median(times))


def _asr_available() -> bool:
    bundled = DIST / "FreeCutDir" / "_internal" / "tools" / "whisper-faster"
    return bundled.is_dir()


def main() -> int:
    if not DIST.is_dir():
        print("没有 dist/ 目录，先打包再运行")
        return 1

    onedir_exe = ONEDIR if ONEDIR.is_file() else None
    onefile_exe = ONEFILE if ONEFILE.is_file() else None
    if onedir_exe is None and onefile_exe is None:
        print("dist/ 里没有找到产物")
        return 1

    lines = [
        "FreeCut 打包产物说明",
        "=" * 40,
        "",
        "两种形态，内容一样，启动方式不同。",
        "",
    ]

    if onedir_exe is not None:
        size = _dir_mb(ONEDIR.parent)
        boot = _startup_ms(onedir_exe)
        lines += [
            "【推荐】onedir —— 双击这个：",
            "  FreeCutDir\\FreeCutDir.exe",
            f"  体积：{size:.0f} MB（整个目录一起拷走）",
            f"  启动：{boot} ms" if boot else "  启动：未测",
            "  语音识别：随包自带，开箱可用" if _asr_available()
            else "  语音识别：需要外置后端（tools/whisper-faster）",
            "  便携用法：把整个 FreeCutDir 目录拷到 U 盘即可",
            "",
        ]

    if onefile_exe is not None:
        size = onefile_exe.stat().st_size / 1024 / 1024
        boot = _startup_ms(onefile_exe)
        lines += [
            "onefile —— 单文件，适合偶尔用一次：",
            "  FreeCut.exe",
            f"  体积：{size:.0f} MB",
            f"  启动：{boot} ms（每次都要解压到临时目录）" if boot
            else "  启动：未测",
            "  语音识别：不带后端，需要外置或改用 onedir",
            "",
        ]

    lines += [
        "第一次用建议：",
        "  1. 双击 FreeCutDir\\FreeCutDir.exe",
        "  2. 空工程界面里点「打开示例工程」",
        "  3. 空格播放，工具 → 导出报告 看导出到剪映会丢什么",
        "",
        "遇到问题：",
        "  - 找不到 ffmpeg：把独立构建的 ffmpeg.exe 放到 exe 同目录",
        "    （剪映自带的那份是 shim，离开它自己的目录跑不起来）",
        "  - 自动字幕说缺后端：见 README 的「语音识别」一节",
        "",
        f"生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    out = DIST / "读我.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写出 {out}（{len(lines)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
