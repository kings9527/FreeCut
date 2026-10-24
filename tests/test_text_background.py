"""文字背景框：不透明度/圆角/颜色都能写回，并且真的画出来。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextBackgroundBox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _setup(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="文字背景")
        seg = builder.add_text(project, "你好", start=0, duration=1_000_000)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_alpha_and_radius_write_back(self):
        project, seg, inspector = self._setup()
        inspector.row_bg_alpha.set_value(70.0, emit=True)
        inspector.row_bg_radius.set_value(10.0, emit=True)
        mat = project.material(seg.material_id)
        self.assertAlmostEqual(float(mat.get("background_alpha")), 0.7, places=2)
        self.assertAlmostEqual(float(mat.get("background_round_radius")), 10.0, places=1)

    def test_background_changes_the_rendered_frame(self):
        from freecut import builder
        from freecut.render import Renderer

        def mean(bg: str, alpha: float) -> float:
            project = builder.new_project(320, 180, 30, name="bg")
            seg = builder.add_text(project, "背景", start=0, duration=1_000_000, size=40)
            mat = project.material(seg.material_id)
            mat["background_color"] = bg
            mat["background_alpha"] = alpha
            renderer = Renderer(project, 320, 180, 30)
            try:
                return float(renderer.render_frame(3).mean())
            finally:
                renderer.close()

        dark = mean("#000000", 1.0)
        bright = mean("#FFDD00", 1.0)
        self.assertGreater(bright - dark, 3.0, "背景色没影响画面")


if __name__ == "__main__":
    unittest.main(verbosity=2)
