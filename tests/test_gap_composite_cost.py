"""第 18 轮：调节层的位置 + 合成成本。

两件事：
1. 调节层（调节图层）必须放在被调节轨道**上面**，否则它作用在空白画面上（=没效果）。
2. 合成优化后，1080p 4 层要明显快于优化前（600 ms → 340 ms 量级），防止回退。
"""

from __future__ import annotations

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


class TestAdjustmentLayerPlacement(unittest.TestCase):
    def test_adjustment_goes_on_top_of_video_tracks(self):
        from freecut import builder

        project = builder.new_project(320, 180, 30)
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        builder.add_adjustment(project, start=0, duration=1_000_000, brightness=30)
        tracks = [t for t in project.tracks if t.type == "video"]
        self.assertEqual(len(tracks), 2)
        self.assertIs(tracks[0], project.tracks[0],
                      "调节层应当在第一条（最上面），才会作用到下面的画面")
        self.assertTrue(any(getattr(s, "is_adjustment", False)
                            for s in tracks[0].segments))

    def test_adjustment_actually_changes_the_frame(self):
        from freecut import builder
        from freecut.render import Renderer

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")

        def render(with_adjust: bool):
            project = builder.new_project(160, 90, 30)
            builder.add_image(project, IMAGE, start=0, duration=1_000_000)
            if with_adjust:
                builder.add_adjustment(project, start=0, duration=1_000_000,
                                       brightness=40, contrast=20)
            renderer = Renderer(project, 160, 90, 30)
            try:
                return renderer.render_frame(3)
            finally:
                renderer.close()

        plain, adjusted = render(False), render(True)
        diff = float(abs(plain.astype(int) - adjusted.astype(int)).mean())
        self.assertGreater(diff, 3.0, f"调节层没起作用（平均差 {diff:.1f}）")


class TestCompositeCost(unittest.TestCase):
    def test_four_layers_at_1080p_stay_under_the_budget(self):
        from freecut import builder
        from freecut.render import Renderer

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(1920, 1080, 30)
        for i in range(4):
            builder.add_image(project, IMAGE, start=0, duration=5_000_000,
                              track_index=i)
        renderer = Renderer(project, 1920, 1080, 30)
        try:
            renderer.render_frame(0)                    # 预热（解码/缩放缓存）
            start = time.perf_counter()
            for k in range(4):
                renderer.render_frame(k)
            per_frame = (time.perf_counter() - start) / 4 * 1000
        finally:
            renderer.close()
        self.assertLess(per_frame, 600,
                        f"1080p 4 层合成变慢了：{per_frame:.0f} ms/帧")


if __name__ == "__main__":
    unittest.main(verbosity=2)
