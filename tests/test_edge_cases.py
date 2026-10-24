"""边界用例：空工程、零时长、极端画幅、重叠片段都不应该崩。"""

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


def render(project, w=None, h=None):
    from freecut.render import Renderer

    w = w or int(project.canvas.width)
    h = h or int(project.canvas.height)
    renderer = Renderer(project, w, h, 30)
    try:
        return renderer.render_frame(0)
    finally:
        renderer.close()


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")

    def test_empty_project_renders(self):
        from freecut import builder

        frame = render(builder.new_project(160, 90, 30, name="空"))
        self.assertEqual(frame.shape[:2], (90, 160))

    def test_zero_duration_segment_does_not_crash(self):
        from freecut import builder

        project = builder.new_project(160, 90, 30, name="零时长")
        builder.add_image(project, IMAGE, start=0, duration=0)
        frame = render(project)
        self.assertEqual(frame.shape[:2], (90, 160))

    def test_tiny_and_extreme_canvas(self):
        from freecut import builder

        for w, h in ((8, 8), (4096, 16), (16, 4096), (1, 64)):
            with self.subTest(size=(w, h)):
                project = builder.new_project(w, h, 30, name="极端")
                builder.add_image(project, IMAGE, start=0, duration=1_000_000)
                frame = render(project, 64, 64)
                self.assertEqual(frame.shape[:2], (64, 64),
                                 f"{w}x{h} 工程渲染失败")

    def test_overlapping_and_gapped_segments(self):
        from freecut import builder

        project = builder.new_project(160, 90, 30, name="重叠")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        builder.add_image(project, IMAGE, start=500_000, duration=1_000_000)   # 重叠
        builder.add_image(project, IMAGE, start=3_000_000, duration=1_000_000)  # 有空隙
        for t in (0, 600_000, 2_000_000, 3_200_000):
            with self.subTest(t=t):
                from freecut.render import Renderer

                renderer = Renderer(project, 160, 90, 30)
                try:
                    frame = renderer.render_frame(int(t / 1_000_000 * 30))
                finally:
                    renderer.close()
                self.assertEqual(frame.shape[:2], (90, 160))


if __name__ == "__main__":
    unittest.main(verbosity=2)
