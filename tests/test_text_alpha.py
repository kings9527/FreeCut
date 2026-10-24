"""文字透明度：两个 alpha 都能写回，且文字 alpha 影响渲染。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextAlpha(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _setup(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="alpha")
        seg = builder.add_text(project, "字", start=0, duration=1_000_000, size=64)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_sliders_write_back(self):
        project, seg, inspector = self._setup()
        inspector.row_text_alpha.set_value(40.0, emit=True)
        inspector.row_global_alpha.set_value(20.0, emit=True)
        mat = project.material(seg.material_id)
        self.assertAlmostEqual(float(mat.get("text_alpha")), 0.4, places=2)
        self.assertAlmostEqual(float(mat.get("global_alpha")), 0.2, places=2)

    def test_alpha_changes_render(self):
        from freecut import builder
        from freecut.render import Renderer

        def mean(alpha: float) -> float:
            project = builder.new_project(320, 180, 30, name="alpha-r")
            seg = builder.add_text(project, "字", start=0, duration=1_000_000, size=64)
            project.material(seg.material_id)["text_alpha"] = alpha
            renderer = Renderer(project, 320, 180, 30)
            try:
                return float(renderer.render_frame(3).mean())
            finally:
                renderer.close()

        opaque = mean(1.0)
        faded = mean(0.2)
        self.assertGreater(abs(opaque - faded), 0.5, "文字 alpha 没影响画面")


if __name__ == "__main__":
    unittest.main(verbosity=2)
