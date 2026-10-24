"""HSL 色彩化界面：色圈选色带、三条滑杆有渐变槽、改值真的改变画面。"""

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


class TestHslColorUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _inspector(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="HSL")
        seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        self.addCleanup(inspector.close)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_eight_swatches_and_click_selects_band(self):
        _project, _seg, inspector = self._inspector()
        self.assertEqual(inspector.band_swatches.count(), 8)
        inspector.band_swatches.click("cyan")
        self.assertEqual(inspector.band_box.currentData(), "cyan")
        self.assertEqual(inspector.band_swatches.current(), "cyan")

    def test_three_sliders_have_gradients(self):
        _project, _seg, inspector = self._inspector()
        for row in (inspector.row_hue, inspector.row_sat, inspector.row_lum):
            self.assertIn("qlineargradient", row.slider.styleSheet())

    def test_saturation_gradient_follows_the_band(self):
        _project, _seg, inspector = self._inspector()
        inspector.band_swatches.click("red")
        red_sheet = inspector.row_sat.slider.styleSheet()
        inspector.band_swatches.click("cyan")
        cyan_sheet = inspector.row_sat.slider.styleSheet()
        self.assertNotEqual(red_sheet, cyan_sheet, "换色带后饱和度渐变没变")

    def test_slider_writes_hsl_and_changes_frame(self):
        import numpy as np

        from freecut.render import Renderer

        project, seg, inspector = self._inspector()

        def frame():
            renderer = Renderer(project, 320, 180, 30)
            try:
                return renderer.render_frame(10).astype("float32")
            finally:
                renderer.close()

        before = frame()
        inspector.band_swatches.click("green")
        inspector.row_sat.set_value(-80.0, emit=True)
        bands = [r.get("bands") for r in project.refs_of(seg) if r.type == "hsl"]
        self.assertTrue(bands and "green" in bands[0], f"没写进 HSL：{bands}")
        diff = float(np.abs(before - frame()).mean())
        self.assertGreater(diff, 0.3, f"HSL 改了但画面没变（差 {diff:.2f}）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
