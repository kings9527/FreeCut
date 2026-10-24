"""文字描边/阴影参数：能写回，且真的影响渲染。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextStrokeShadow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _setup(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="描边阴影")
        seg = builder.add_text(project, "字", start=0, duration=1_000_000, size=48)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_writes_back(self):
        project, seg, inspector = self._setup()
        inspector.row_border_alpha.set_value(40.0, emit=True)
        inspector.row_shadow_distance.set_value(0.3, emit=True)
        inspector.row_shadow_angle.set_value(200.0, emit=True)
        inspector._apply_text("border_color", "#FF0000")
        inspector._apply_text("shadow_color", "#00FF00")
        mat = project.material(seg.material_id)
        self.assertAlmostEqual(float(mat.get("border_alpha")), 0.4, places=2)
        self.assertAlmostEqual(float(mat.get("shadow_distance")), 0.3, places=2)
        self.assertAlmostEqual(float(mat.get("shadow_angle")), 200.0, places=1)
        self.assertEqual(mat.get("border_color"), "#FF0000")
        self.assertEqual(mat.get("shadow_color"), "#00FF00")

    def test_shadow_changes_rendered_frame(self):
        from freecut import builder
        from freecut.render import Renderer
        import numpy as np

        def render(shadow_alpha: float):
            project = builder.new_project(320, 180, 30, name="阴影渲染")
            seg = builder.add_text(project, "字", start=0, duration=1_000_000, size=64)
            mat = project.material(seg.material_id)
            mat["shadow_alpha"] = shadow_alpha
            mat["shadow_distance"] = 0.5
            mat["shadow_angle"] = 45.0
            mat["shadow_color"] = "#FFFFFF"
            renderer = Renderer(project, 320, 180, 30)
            try:
                return renderer.render_frame(3).astype("int16")
            finally:
                renderer.close()

        none = render(0.0)
        heavy = render(1.0)
        diff = float(abs(none - heavy).mean())
        self.assertGreater(diff, 0.3, f"阴影没影响画面（差 {diff:.3f}）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
