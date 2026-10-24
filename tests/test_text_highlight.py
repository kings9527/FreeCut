"""逐字高亮：开启后按字数均分时间，且不同时刻高亮位置不同（渲染层面）。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestTextHighlight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _setup(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="逐字")
        seg = builder.add_text(project, "你好世界", start=0, duration=2_000_000, size=48)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_toggle_builds_word_timings(self):
        project, seg, inspector = self._setup()
        inspector.chk_highlight.setChecked(True)
        mat = project.material(seg.material_id)
        words = mat.get("words") or []
        self.assertEqual(len(words), 4, "四个字应当有四个时间片")
        self.assertEqual(mat.get("highlight_mode"), 1)
        self.assertEqual(words[0]["start_time"], 0)
        self.assertEqual(words[-1]["end_time"], 2_000_000)

    def test_toggle_off_clears(self):
        project, seg, inspector = self._setup()
        inspector.chk_highlight.setChecked(True)
        inspector.chk_highlight.setChecked(False)
        mat = project.material(seg.material_id)
        self.assertFalse(mat.get("words"))
        self.assertFalse(mat.get("highlight_mode"))

    def test_frames_differ_over_time(self):
        from freecut import builder
        from freecut.render import Renderer
        import numpy as np

        project = builder.new_project(320, 180, 30, name="高亮渲染")
        seg = builder.add_text(project, "你好世界", start=0, duration=2_000_000, size=56)
        mat = project.material(seg.material_id)
        mat["highlight_color"] = "#FF0000"
        span = 2_000_000 / 4
        mat["words"] = [{"start_time": int(i * span), "end_time": int((i + 1) * span),
                         "word": t} for i, t in enumerate("你好世界")]
        mat["highlight_mode"] = 1
        renderer = Renderer(project, 320, 180, 30)
        try:
            early = renderer.render_frame(3).astype("int16")
            late = renderer.render_frame(50).astype("int16")
        finally:
            renderer.close()
        self.assertGreater(float(abs(early - late).mean()), 0.05,
                           "不同时刻的高亮位置应当不同")


if __name__ == "__main__":
    unittest.main(verbosity=2)
