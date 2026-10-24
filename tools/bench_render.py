"""渲染性能基线：改动前后跑一遍，别靠感觉。

```powershell
python tools\\bench_render.py                 # 默认 426x240（预览档）
python tools\\bench_render.py --size 1280x720 # 全尺寸
```

输出三块：
1. 整帧渲染耗时（预览真正关心的数字）
2. 关键算子单独计时（合成 / 色度抠图 / 解码）
3. 合成快路径的命中率收益

**不要用 cProfile 的单次数字做判断**——第 72 轮我据此"优化"了色度抠图，
单独微基准一跑发现反而慢 3%，只能回滚。先测，再改。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ms(fn, n: int = 30) -> float:
    fn()
    t0 = time.time()
    for _ in range(n):
        fn()
    return (time.time() - t0) / n * 1000.0


def _print_caps():
    try:
        from freecut import caps

        print("caps:", caps.summary())
    except Exception:                          # noqa: BLE001
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", default="426x240")
    parser.add_argument("--project", default=str(ROOT / "out" / "showcase.json"))
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--json", default=None, help="把结果写成 JSON 到这个路径")
    args = parser.parse_args(argv)

    width, height = (int(x) for x in args.size.lower().split("x"))
    metrics = {"size": f"{width}x{height}"}

    from freecut import chroma, transform
    from freecut.model import Project
    from freecut.render import Renderer

    print(f"尺寸 {width}x{height}")
    print("-" * 46)

    # 1) 整帧：顺序 与 跳帧 分开测 —— 跳帧会强制解码器重启，差一个数量级
    if Path(args.project).is_file():
        project = Project.load(args.project)
        r = Renderer(project, width, height, 30)
        total = min(args.frames, max(1, r.frame_count))
        t0 = time.time()
        for i in range(total):                        # 顺序播放的场景
            r.render_frame(i)
        seq = (time.time() - t0) / total * 1000
        r.close()

        r = Renderer(project, width, height, 30)
        stride = max(1, r.frame_count // total)
        t0 = time.time()
        n = 0
        for i in range(0, r.frame_count, stride):
            r.render_frame(i)
            n += 1
            if n >= total:
                break
        skip = (time.time() - t0) / max(1, n) * 1000
        r.close()
        metrics["frame_ms"] = round(seq, 2)
        metrics["fps"] = round(1000 / seq, 1)
        print(f"整帧·顺序    : {seq:6.2f} ms  → {1000 / seq:5.1f} fps  （播放的样子）")
        metrics["seek_ms"] = round(skip, 2)
        print(f"整帧·跳帧    : {skip:6.2f} ms  → {1000 / skip:5.1f} fps  "
              f"（每帧都 seek，{skip / seq:.1f}× 慢）")
    else:
        print(f"整帧渲染     : 跳过（找不到 {args.project}）")

    # 2) 关键算子
    rng = np.random.default_rng(0)
    base = rng.random((height, width, 3), dtype=np.float32)
    layer = rng.random((height, width, 3), dtype=np.float32)
    opaque = np.ones((height, width), dtype=np.float32)
    partial = rng.random((height, width), dtype=np.float32) * 0.5 + 0.25
    key = chroma.parse_hex("#00ff00")

    metrics["over_opaque_ms"] = round(_ms(lambda: transform.over(base, layer, opaque), 200), 4)
    metrics["over_blend_ms"] = round(_ms(lambda: transform.over(base, layer, partial), 50), 3)
    metrics["chroma_ms"] = round(_ms(lambda: chroma.apply(layer, "#00ff00"), 30), 3)
    print(f"over 全不透明: {_ms(lambda: transform.over(base, layer, opaque)):6.3f} ms")
    print(f"over 半透明  : {_ms(lambda: transform.over(base, layer, partial)):6.3f} ms")
    print(f"色度 alpha   : {_ms(lambda: chroma.alpha_from_key(layer, key)):6.3f} ms")
    print(f"色度 完整    : {_ms(lambda: chroma.apply(layer, '#00ff00')):6.3f} ms")

    # 3) 快路径收益
    fast = _ms(lambda: transform.over(base, layer, opaque), 200)
    slow = _ms(lambda: transform.over(base, layer, partial), 50)
    if fast > 0:
        print("-" * 46)
        print(f"全不透明比半透明快 {slow / fast:.0f}× —— 视频片段绝大多数走快路径")

    if args.json:
        import json

        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    _print_caps()
    raise SystemExit(main())
