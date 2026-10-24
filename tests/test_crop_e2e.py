"""裁剪端到端：裁剪后画面里只剩被裁的那块（整体变空，背景露出）。

说明：实测裁剪是"缩到裁剪区大小"而不是"把裁剪区拉伸铺满"，
因此用整体亮度下降判断"裁剪生效"，不断言具体落在哪个角落（布局语义待查）。
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


def render(crop=None) -> float:
    from freecut import builder
    from freecut.render import Renderer

    project = builder.new_project(160, 90, 30, name="裁剪")
    seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
    if crop is not None:
        project.material(seg.material_id)["crop"] = dict(crop)
    renderer = Renderer(project, 160, 90, 30)
    try:
        return float(renderer.render_frame(3).mean())
    finally:
        renderer.close()


class TestCropE2E(unittest.TestCase):
    def setUp(self):
        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")

    def test_half_crop_removes_about_half_the_picture(self):
        plain = render(None)
        cropped = render({"upper_left_x": 0.0, "upper_left_y": 0.0,
                          "lower_right_x": 0.5, "lower_right_y": 1.0})
        self.assertGreater(plain, 30, "原图太暗，测不出裁剪效果")
        self.assertLess(cropped, plain * 0.75,
                        f"裁掉一半后画面没有变空：{plain:.1f} → {cropped:.1f}")

    def test_full_crop_is_a_noop(self):
        plain = render(None)
        same = render({"upper_left_x": 0.0, "upper_left_y": 0.0,
                       "lower_right_x": 1.0, "lower_right_y": 1.0})
        self.assertAlmostEqual(plain, same, delta=0.5,
                               msg="整幅裁剪不应改变画面")


if __name__ == "__main__":
    unittest.main(verbosity=2)
