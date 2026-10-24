"""属性页端到端：改了画面参数，渲染结果必须真的变（不是只改字段）。"""

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


class TestClipPropertiesRender(unittest.TestCase):
    def _render(self, mutate):
        from freecut import builder
        from freecut.render import Renderer

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(160, 90, 30, name="属性")
        seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        mutate(seg)
        renderer = Renderer(project, 160, 90, 30)
        try:
            return renderer.render_frame(3).astype("int16")
        finally:
            renderer.close()

    def test_opacity_changes_the_frame(self):
        base = self._render(lambda seg: None)
        faded = self._render(lambda seg: setattr(seg.clip, "alpha", 0.35))
        diff = float(abs(base - faded).mean())
        self.assertGreater(diff, 3.0, f"不透明度改了画面却没变：{diff:.2f}")

    def test_rotation_changes_the_frame(self):
        base = self._render(lambda seg: None)
        turned = self._render(lambda seg: setattr(seg.clip, "rotation", 30.0))
        diff = float(abs(base - turned).mean())
        self.assertGreater(diff, 2.0, f"旋转改了画面却没变：{diff:.2f}")

    def test_position_changes_the_frame(self):
        base = self._render(lambda seg: None)
        moved = self._render(lambda seg: setattr(seg.clip, "tx", 0.3))
        diff = float(abs(base - moved).mean())
        self.assertGreater(diff, 2.0, f"位置改了画面却没变：{diff:.2f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
