"""拿到一份"真的用了曲线/HSL/色轮"的剪映草稿——把这件事变成照做就行。

R3 卡了很久：我们写进草稿的调色数组，剪映认不认，需要一份**剪映自己生成的**
同类草稿来对照。缺的不是代码，是一份样本。这个脚本负责两件事：

1. ``--prepare``：打印一份三分钟的"造样本"说明书（在剪映里点哪里、拖什么）。
2. ``--inspect <草稿>``：把草稿里四个调色数组的**字段形状**打印出来，
   并把我们写入端期望的键并列显示，一眼就能看出差在哪。

样本拿到后跑 ``--inspect``，把输出贴进 ``RISKS.md`` 的 R3，这条就算有了证据。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

GRADE_ARRAYS = ("color_curves", "hsl", "primary_color_wheels", "log_color_wheels")


def _read_draft_json(draft: str) -> Dict[str, Any]:
    """草稿可能是加密的：先试明文，失败再用剪映的解密路径。"""
    content = os.path.join(draft, "draft_content.json")
    if not os.path.isfile(content):
        raise SystemExit(f"这里没有 draft_content.json：{draft}")
    raw = open(content, encoding="utf-8", errors="replace").read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        from freecut import jianying

        return jianying.load_draft(draft, decrypt=True).to_dict()


def _shape_of(value: Any, depth: int = 0) -> Any:
    """把任意结构压成"键 → 类型"，方便并排比较。"""
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        return {k: _shape_of(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return "[]"
        first = value[0]
        if isinstance(first, (dict, list)):
            return [f"{len(value)} 项", _shape_of(first, depth + 1)]
        return f"[{len(value)} 个数字]"
    return type(value).__name__


def prepare() -> int:
    print("造一份「曲线/HSL/色轮」样本草稿（约三分钟）：")
    print()
    print("  1. 打开剪映 → 新建草稿 → 随便导入一段 5 秒以上的视频，拖到时间线")
    print("  2. 选中片段 → 右侧「调节」页签")
    print("  3. 曲线：把 RGB 曲线中间那个点往上拖一点（画面变亮）")
    print("  4. HSL：选一个色带（比如橙色），把饱和度往右拉一点")
    print("  5. 色轮：把「主色轮」的圆点往暖色方向推一点")
    print("  6. Ctrl+S 保存 → 关掉剪映")
    print()
    print("然后运行：")
    print('  python tools\\curve_sample.py --inspect "E:\\剪映-草稿\\JianyingPro Drafts\\这个草稿"')
    print()
    print("说明：这三步分别对应草稿里的 color_curves / hsl / primary_color_wheels。")
    print("     我们要看的就是这三个数组里**字段叫什么名字、结构长什么样**。")
    return 0


def inspect(draft: str) -> int:
    data = _read_draft_json(draft)
    materials = data.get("materials") or {}
    found = {name: materials.get(name) for name in GRADE_ARRAYS}

    print(f"草稿：{draft}")
    for name in GRADE_ARRAYS:
        value = found[name]
        if not value:
            print(f"\n{name}: 空（这份草稿没用到）")
            continue
        print(f"\n{name}: {len(value)} 条")
        print("  结构：" + json.dumps(_shape_of(value[0]), ensure_ascii=False)[:600])

    # 和我们写入端的期望并列一下
    try:
        import tempfile

        from freecut import builder, jianying_export

        project = builder.new_project(160, 90, 30, name="schema")
        seg = builder.add_image(project, os.path.join(REPO, "out", "_trans_a.png"),
                               start=0, duration=1_000_000)
        builder.set_curves(project, seg, rgb=[(0.0, 0.0), (0.5, 0.6), (1.0, 1.0)])
        builder.set_hsl(project, seg, bands={"orange": {"sat": 0.2}})
        builder.set_wheels(project, seg, lift=(0.01, 0.0, -0.01))
        # 看的是导出成草稿之后的数组名（curve -> color_curves 是导出时映射的）
        with tempfile.TemporaryDirectory() as tmp:
            folder = jianying_export.save_draft(project, tmp, name="schema", encrypt=False)
            ours = json.load(open(os.path.join(folder, "draft_content.json"),
                                  encoding="utf-8")).get("materials") or {}
    except Exception as exc:                       # 缺素材不该让脚本崩
        print(f"\n（没能生成对照样本：{exc}）")
        return 0

    print("\n我们写入端的结构：")
    for name in GRADE_ARRAYS:
        value = ours.get(name)
        if not value:
            print(f"  {name}: 未写入")
            continue
        print(f"  {name}: " + json.dumps(_shape_of(value[0]), ensure_ascii=False)[:400])

    print("\n怎么读这个输出：")
    print("  · 两边键名一致 → 我们的字段形状被证实，R3 可标『已验证』")
    print("  · 键名/层级不同 → 按剪映的写法改 builder，再导出一次对照")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="R3：造样本 / 读样本")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true", help="打印造样本的步骤")
    group.add_argument("--inspect", metavar="草稿目录", help="打印四个调色数组的字段形状")
    args = parser.parse_args(argv)
    if args.prepare:
        return prepare()
    return inspect(os.path.abspath(args.inspect))


if __name__ == "__main__":
    raise SystemExit(main())
