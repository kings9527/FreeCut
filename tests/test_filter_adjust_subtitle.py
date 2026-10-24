"""滤镜 / 调节 / 字幕 三个面板：按钮真的生效（不只是有控件）。

对应 2026-10-24 用户问的"这三样能不能用"。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


def _frame(project, index: int = 10):
    from freecut.render import Renderer

    renderer = Renderer(project, 320, 180, 30)
    try:
        return renderer.render_frame(index).astype("float32")
    finally:
        renderer.close()


class TestFilterPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        from freecut import builder, luts
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        if not list(luts.library(refresh=True)):
            self.skipTest("本机没有 LUT")
        project = builder.new_project(320, 180, 30, name="滤镜")
        seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        bridge = Bridge(project)
        panel = BinPanel(bridge)
        self.addCleanup(panel.close)
        bridge.select(seg)
        return project, seg, panel

    def test_applying_a_lut_changes_the_frame(self):
        import numpy as np

        project, seg, panel = self._panel()
        before = _frame(project)
        panel.lut_list.setCurrentRow(0)
        panel.apply_selected_lut()
        refs = [r.type for r in project.refs_of(seg)]
        self.assertIn("filter", refs, "滤镜材质没挂上")
        diff = float(np.abs(before - _frame(project)).mean())
        self.assertGreater(diff, 2.0, f"套了 LUT 但画面几乎没变（差 {diff:.2f}）")


class TestAdjustPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_adjustment_layer_recolours_the_clip_below(self):
        import numpy as np

        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel
        from freecut.qtui.widgets.inspector import Inspector

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="调节")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        bridge = Bridge(project)
        panel = BinPanel(bridge)
        inspector = Inspector(bridge)
        self.addCleanup(panel.close)
        self.addCleanup(inspector.close)

        before = _frame(project)
        panel.add_adjustment_layer()
        layers = [s for t in project.tracks for s in t.segments
                  if getattr(s, "is_adjustment", False)]
        self.assertEqual(len(layers), 1, "没有建出调节图层")

        bridge.select(layers[0])
        self.app.processEvents()
        inspector.adjust_rows["brightness"].set_value(40.0, emit=True)
        mat = project.material(layers[0].material_id)
        self.assertAlmostEqual(float((mat.get("adjust") or {}).get("brightness")), 40.0, places=1)

        diff = float(np.abs(before - _frame(project)).mean())
        self.assertGreater(diff, 3.0, f"调节图层没影响下方画面（差 {diff:.2f}）")


class TestSubtitlePanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _srt(self) -> str:
        path = os.path.join(tempfile.mkdtemp(prefix="srt-"), "a.srt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
                     "2\n00:00:01,000 --> 00:00:02,000\n第二句\n")
        return path

    def _panel(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        project = builder.new_project(320, 180, 30, name="字幕")
        bridge = Bridge(project)
        panel = BinPanel(bridge)
        self.addCleanup(panel.close)
        return project, panel

    def test_import_srt_creates_text_segments(self):
        from PySide6.QtWidgets import QFileDialog

        project, panel = self._panel()
        srt = self._srt()
        original = QFileDialog.getOpenFileName
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (srt, "SRT (*.srt)"))
        try:
            panel.import_subtitles()
        finally:
            QFileDialog.getOpenFileName = original
        texts = [project.material(s.material_id).get("content")
                 for t in project.tracks for s in t.segments]
        self.assertEqual(len(texts), 2, f"字幕没建出来：{texts}")
        self.assertIn("第一句", texts[0])

    def test_export_srt_writes_file(self):
        from PySide6.QtWidgets import QFileDialog

        from freecut import subtitles

        project, panel = self._panel()
        subtitles.add_subtitles(project, subtitles.parse_srt(open(self._srt(), encoding="utf-8").read()))
        out = os.path.join(tempfile.mkdtemp(prefix="srt-out-"), "o.srt")
        original = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, "字幕 (*.srt)"))
        try:
            panel.export_subtitles()
        finally:
            QFileDialog.getSaveFileName = original
        self.assertTrue(os.path.isfile(out))
        self.assertGreater(os.path.getsize(out), 20)

    def test_refresh_lists_subtitles(self):
        from freecut import subtitles

        project, panel = self._panel()
        subtitles.add_subtitles(project, subtitles.parse_srt(open(self._srt(), encoding="utf-8").read()))
        panel.refresh_subtitles()
        self.assertGreaterEqual(panel.sub_list.count(), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
