"""把语音识别后端整理成一个可打包的精简目录（只带 base 模型）。

```powershell
python tools\stage_asr.py            # 生成 build\asr-bundle\Whisper-Faster
```

打包时 `FreeCutDir.spec` 指的就是这个目录。为什么需要单独一步：
`tools/whisper-faster/` 里有 base / small / tiny 三套模型（共 679 MB），
全打进安装包会让体积翻倍；实际只需要 base。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / "tools" / "whisper-faster" / "app" / "Whisper-Faster"
DST = ROOT / "build" / "asr-bundle" / "Whisper-Faster"
KEEP_MODELS = ("faster-whisper-base",)


def main() -> int:
    if not SRC.is_dir():
        print(f"找不到后端目录: {SRC}")
        return 1
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True, exist_ok=True)
    for item in SRC.iterdir():
        if item.name == "_models":
            (DST / "_models").mkdir()
            for model in item.iterdir():
                if model.name in KEEP_MODELS:
                    shutil.copytree(model, DST / "_models" / model.name)
        elif item.is_dir():
            shutil.copytree(item, DST / item.name)
        else:
            shutil.copy2(item, DST / item.name)
    total = sum(f.stat().st_size for f in DST.rglob("*") if f.is_file())
    print(f"已生成 {DST}（{total / 1024 / 1024:.0f} MB，模型: {', '.join(KEEP_MODELS)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
