"""调色与文字端到端：滑杆/字号改了，渲染出来的像素必须真的变。"""

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


class TestAdjustE2E(unittest.TestCase):
    def _mean(self, brightness: float) -> float:
        from freecut import builder
        from freecut.render import Renderer

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(160, 90, 30, name="调色")
        seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        builder.set_adjust(project, seg, brightness=brightness)
        renderer = Renderer(project, 160, 90, 30)
        try:
            return float(renderer.render_frame(3).mean())
        finally:
            renderer.close()

    def test_brightness_slider_changes_pixels(self):
        dark = self._mean(-40.0)
        bright = self._mean(40.0)
        self.assertGreater(bright - dark, 20.0,
                           f"亮度滑杆没改变画面：{dark:.1f} → {bright:.1f}")

    def test_saturation_slider_changes_pixels(self):
        from freecut import builder
        from freecut.render import Renderer

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")

        def render(sat: float) -> "object":
            project = builder.new_project(160, 90, 30, name="饱和")
            seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
            builder.set_adjust(project, seg, saturation=sat)
            renderer = Renderer(project, 160, 90, 30)
            try:
                return renderer.render_frame(3).astype("int16")
            finally:
                renderer.close()

        low = render(-100.0)
        high = render(100.0)
        diff = float(abs(low - high).mean())
        self.assertGreater(diff, 3.0, f"饱和度滑杆没改变画面：差 {diff:.2f}")


class TestTextE2E(unittest.TestCase):
    def _render(self, size: float, content: str = "文字测试"):
        from freecut import builder
        from freecut.render import Renderer

        project = builder.new_project(320, 180, 30, name="文字")
        builder.add_text(project, content, start=0, duration=1_000_000, size=size)
        renderer = Renderer(project, 320, 180, 30)
        try:
            return renderer.render_frame(3).astype("int16")
        finally:
            renderer.close()

    def test_text_is_drawn(self):
        blank = self._render(10.0, content="")
        with_text = self._render(20.0)
        diff = float(abs(blank - with_text).mean())
        self.assertGreater(diff, 0.3, f"文字没有画到画面上：差 {diff:.3f}")

    def test_larger_font_changes_the_frame(self):
        small = self._render(12.0)
        large = self._render(40.0)
        diff = float(abs(small - large).mean())
        self.assertGreater(diff, 0.5, f"字号改了画面没变：差 {diff:.3f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
