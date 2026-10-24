"""渲染全部文字模板，做一张真实对比图。

存在的理由：`out/text_presets.png` 早期是"示意"出来的，看不出模板到底差在哪。
这个脚本用**真渲染器**逐条出图，并断言任意两个模板的像素确实不同——
14 个模板长得一样的话，这个能力就是假的。

```powershell
python tools\\make_text_styles_sheet.py
```
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freecut import builder, textstyles                    # noqa: E402
from freecut.render import Renderer                        # noqa: E402

SAMPLE = "FreeCut 花字"


def render_style(name: str, width: int = 640, height: int = 200) -> np.ndarray:
    """One frame with only a text segment using ``name``, on a mid-grey backdrop."""
    project = builder.new_project(width, height, 10, name=f"style-{name}")
    # 浅灰底：深色底条/深色描边才看得见（深底会让它们"消失"）
    project.canvas.background = "#c9ced8"
    mat = builder.add_text(project, SAMPLE, start=0, duration=1_000_000,
                           size=11.0, y=0.0)
    material = project.material(mat.material_id)
    textstyles.apply(material, name)
    # 只有定义了高亮色的模板才传词级时间戳：
    # 给所有模板都传词会让没有 highlight_color 的模板被默认色"染"掉第一个词
    if material.get("highlight_color"):
        from freecut import subtitles

        material.extra["words"] = subtitles.distribute_words(SAMPLE, 0, 1_000_000)
    renderer = Renderer(project, width, height, 10)
    try:
        frame = renderer.render_frame(0)
    finally:
        renderer.close()
    return np.asarray(frame).astype(np.int16)


def main() -> int:
    from PIL import Image, ImageDraw

    from freecut.text import get_font

    names = textstyles.names()
    width, height = 640, 200
    frames = {}
    for name in names:
        frames[name] = render_style(name, width, height)

    # 两两比较，证明模板真的不一样
    diffs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = frames[names[i]], frames[names[j]]
            mean = float(np.abs(a - b).mean())
            diffs.append((mean, names[i], names[j]))
    diffs.sort()
    identical = [d for d in diffs if d[0] < 0.5]
    print(f"{len(names)} 个模板，两两比较 {len(diffs)} 对")
    print(f"最接近的一对: {diffs[0][1]} vs {diffs[0][2]}，平均像素差 {diffs[0][0]:.2f}")
    print(f"几乎相同的对: {len(identical)}")
    for mean, a, b in identical[:3]:
        print(f"  ⚠ {a} vs {b}: {mean:.2f}")

    # 拼成网格
    cols, rows = 2, (len(names) + 1) // 2
    grid = Image.new("RGB", (cols * width, rows * (height + 26)), (16, 16, 20))
    draw = ImageDraw.Draw(grid)
    font = get_font("", 17)
    for index, name in enumerate(names):
        col, row = index % cols, index // cols
        x, y = col * width, row * (height + 26)
        grid.paste(Image.fromarray(frames[name].astype(np.uint8)), (x, y + 26))
        group = next((g for g, items in textstyles.groups().items() if name in items), "")
        draw.text((x + 10, y + 4), f"{name}　[{group}]", fill=(255, 232, 150), font=font)
        draw.rectangle([x, y, x + width - 1, y + height + 25], outline=(60, 66, 78))

    out = ROOT / "out" / "text_styles_rendered.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    grid.save(out)
    print(f"已写出 {out}  ({grid.width}x{grid.height}, {os.path.getsize(out) // 1024} KB)")
    return 0 if not identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
