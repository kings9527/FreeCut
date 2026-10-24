"""转场端到端：真的接到渲染里——不同引擎在 u=0.5 必须渲染出不同画面。

之前的 test_transition_engines 只验证掩码数组；这条验证"掩码真的被用于合成"。
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

A = os.path.join(ROOT, "out", "chroma_src.png")
B = A   # 同一张图，靠第二段的摆放区分前后画面


def frame(kind: str, at_us: int = 1_000_000):
    """在切点（1.0s）取一帧：转场以切点为中心，此处 u=0.5，各引擎差异最大。"""
    from freecut import builder
    from freecut.render import Renderer

    project = builder.new_project(160, 90, 30, name="转场")
    first = builder.add_image(project, A, start=0, duration=1_000_000)
    second = builder.add_image(project, A, start=1_000_000, duration=1_000_000)
    # 让后一段明显不同于前一段（否则擦除和叠化看起来一样）
    second.clip.tx = 0.45
    second.clip.scale = 1.1
    builder.add_transition(project, second, kind=kind, duration_us=400_000)
    renderer = Renderer(project, 160, 90, 30)
    try:
        fps = 30
        index = int(round(at_us / 1_000_000 * fps))
        return renderer.render_frame(index).astype("int16")
    finally:
        renderer.close()


class TestTransitionRender(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(A):
            self.skipTest("缺少测试图片")

    def test_wipes_differ_from_dissolve(self):
        base = frame("cross_dissolve")
        for kind in ("wipe_left", "wipe_up", "circle_in", "blinds_vertical", "wipe_clock"):
            with self.subTest(kind=kind):
                shot = frame(kind)
                diff = float(abs(base - shot).mean())
                self.assertGreater(diff, 1.0, f"{kind} 渲染结果和叠化几乎一样（差 {diff:.2f}）")

    def test_different_engines_differ_from_each_other(self):
        a = frame("wipe_left")
        b = frame("wipe_right")
        diff = float(abs(a - b).mean())
        self.assertGreater(diff, 1.0, f"左右擦除渲染出同一张画面（差 {diff:.2f}）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
