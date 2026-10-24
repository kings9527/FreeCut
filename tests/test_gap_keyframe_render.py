"""关键帧渲染的回归测试（渲染层，不经 UI）。

本轮修的 bug：调节层 `_apply_adjustment` 没拿到当前时间，导致关键帧永远取 offset=0，
画面在整段时间里都是第一个关键帧的值。
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

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


class TestKeyframeRender(unittest.TestCase):
    def test_adjustment_keyframes_interpolate_over_time(self):
        from freecut import builder
        from freecut.model import Keyframe
        from freecut.render import Renderer

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")

        project = builder.new_project(160, 90, 30, name="调节关键帧")
        builder.add_image(project, IMAGE, start=0, duration=2_000_000)
        adj = builder.add_adjustment(project, start=0, duration=2_000_000, brightness=0)
        adj.keyframes["KFTypeBrightness"] = [
            Keyframe(0, -40.0), Keyframe(1_000_000, 40.0),
        ]
        renderer = Renderer(project, 160, 90, 30)
        try:
            dark = float(renderer.render_frame(3).mean())
            bright = float(renderer.render_frame(27).mean())
        finally:
            renderer.close()
        self.assertGreater(bright - dark, 20,
                           f"亮度关键帧没有随时间插值：{dark:.1f} → {bright:.1f}")

    def test_scale_keyframes_change_the_frame(self):
        from freecut import builder
        from freecut.model import Keyframe
        from freecut.render import Renderer

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")

        project = builder.new_project(160, 90, 30, name="缩放关键帧")
        seg = builder.add_image(project, IMAGE, start=0, duration=2_000_000)
        seg.keyframes["KFTypeScaleX"] = [Keyframe(0, 1.0), Keyframe(1_000_000, 0.4)]
        renderer = Renderer(project, 160, 90, 30)
        try:
            first = renderer.render_frame(3).astype("int16")
            later = renderer.render_frame(27).astype("int16")
        finally:
            renderer.close()
        diff = float(abs(first - later).mean())
        self.assertGreater(diff, 2.0, f"缩放关键帧没有改变画面：差 {diff:.2f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
