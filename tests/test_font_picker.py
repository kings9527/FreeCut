"""文字页字体下拉：能列出本机字体、切换写回 font_name、渲染器能解析该文件。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestFontPicker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _setup(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="字体")
        seg = builder.add_text(project, "你好", start=0, duration=1_000_000)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        return project, seg, inspector

    def test_font_list_is_not_empty(self):
        _p, _s, inspector = self._setup()
        self.assertGreater(inspector.font_combo.count(), 0, "一个字体都没列出来")

    def test_switch_updates_font_name(self):
        project, seg, inspector = self._setup()
        if inspector.font_combo.count() < 2:
            self.skipTest("本机只有一个字体")
        inspector.font_combo.setCurrentIndex(1)
        chosen = inspector.font_combo.currentData()
        self.assertEqual(project.material(seg.material_id).get("font_name"), chosen)

    def test_chosen_file_is_resolvable_by_renderer(self):
        from freecut import text as text_mod

        _p, _s, inspector = self._setup()
        if inspector.font_combo.count() < 1:
            self.skipTest("没有字体")
        chosen = inspector.font_combo.currentData()
        self.assertTrue(text_mod._resolve_font_path(chosen),
                        f"渲染器找不到字体文件 {chosen}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
