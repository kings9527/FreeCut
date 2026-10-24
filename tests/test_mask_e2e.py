"""蒙版端到端：加上圆形蒙版后，圆外应当露出背景，圆内画面保留。"""

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


def render(masked: bool):
    from freecut import builder
    from freecut.render import Renderer

    project = builder.new_project(160, 90, 30, name="蒙版")
    seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
    if masked:
        builder.add_mask(project, seg, name="圆形", center_x=0.0, center_y=0.0,
                         width=0.5, height=0.5, feather=0.02)
    renderer = Renderer(project, 160, 90, 30)
    try:
        return renderer.render_frame(3).astype("int16")
    finally:
        renderer.close()


class TestMaskE2E(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")

    def test_outside_the_mask_shows_background(self):
        plain = render(False)
        masked = render(True)
        # 四角：有蒙版时应该明显变暗（露出黑背景）
        corners_plain = float(plain[:12, :12].mean())
        corners_masked = float(masked[:12, :12].mean())
        self.assertGreater(corners_plain, 20, "原图左上角本来就不是亮的，测不出效果")
        self.assertLess(corners_masked, corners_plain * 0.5,
                        f"蒙版外没有变暗：{corners_plain:.0f} → {corners_masked:.0f}")

    def test_inside_the_mask_stays(self):
        plain = render(False)
        masked = render(True)
        center_plain = plain[38:52, 72:88]
        center_masked = masked[38:52, 72:88]
        diff = float(abs(center_plain - center_masked).mean())
        self.assertLess(diff, 12.0, f"蒙版内画面被改了：平均差 {diff:.1f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
