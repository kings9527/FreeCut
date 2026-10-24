"""第 13 轮：叠加顺序（用户实测：轨道1空白处看不到轨道2）。

结论（逆向证据见 docs/measurements/RUNTIME-CLOCK-TRACK-GPU.md）：
剪映里**第一条轨道在最上面**，盖住后面的轨道；后面的轨道只在上面没有内容的地方露出来。
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MEDIA = os.path.join("out", "demo.mp4")
TOP_IMAGE = os.path.join("out", "chroma_src.png")


class TestStackingOrder(unittest.TestCase):
    def setUp(self):
        from freecut import builder

        if not (os.path.isfile(MEDIA) and os.path.isfile(TOP_IMAGE)):
            self.skipTest("没有测试素材")

        # 轨道 0（最上面一行）：0-1s 和 2-3s 有内容，中间 1-2s 是空白
        # 轨道 1（下面一行）：0-3s 一直有内容（一张好认的图）
        self.project = builder.new_project(160, 90, 30, name="叠加顺序")
        builder.add_video(self.project, MEDIA, start=0, duration=1_000_000, track_index=0)
        builder.add_video(self.project, MEDIA, start=2_000_000, duration=1_000_000, track_index=0)
        builder.add_image(self.project, TOP_IMAGE, start=0, duration=3_000_000, track_index=1)

    def _rgb(self, us: int):
        from freecut.render import Renderer

        renderer = Renderer(self.project, 160, 90)
        return renderer.render_frame(int(round(us / 1e6 * renderer.fps)))

    def test_top_track_wins_where_it_has_content(self):
        with_video = self._rgb(500_000)
        only_image = self._rgb(1_500_000)
        self.assertNotEqual(with_video.tobytes(), only_image.tobytes(),
                            "上层轨道有内容时，画面应当和下层不同")

    def test_lower_track_shows_through_a_gap(self):
        only_image = self._rgb(1_500_000)
        back_to_video = self._rgb(2_500_000)
        self.assertNotEqual(only_image.tobytes(), back_to_video.tobytes(),
                            "上层轨道恢复内容后，画面应当变回去")
        # 空白处应当是那张图（有内容），不是黑屏
        self.assertGreater(float(only_image.mean()), 5.0,
                           "上层空白处渲染成了黑屏，说明下层没画出来")

    def test_gap_frame_matches_the_lower_track_alone(self):
        """空白处这一帧 == 只有下层轨道时的那一帧。"""
        from freecut import builder
        from freecut.render import Renderer

        only_lower = builder.new_project(160, 90, 30, name="只有下层")
        builder.add_image(only_lower, TOP_IMAGE, start=0, duration=3_000_000)
        reference = Renderer(only_lower, 160, 90).render_frame(45)
        gap = self._rgb(1_500_000)
        self.assertEqual(gap.tobytes(), reference.tobytes(),
                         "上层空白处的画面和\"只有下层\"不一致（叠加顺序或裁切有问题）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
