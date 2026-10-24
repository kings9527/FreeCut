"""图文成片对话框：分句、生成项目、菜单入口都在。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestScriptDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        from freecut import builder
        from freecut.qtui.dialogs.script_dialog import ScriptDialog

        project = builder.new_project(320, 180, 30, name="图文")
        dialog = ScriptDialog(project)
        self.addCleanup(dialog.close)
        return dialog

    def test_build_creates_a_project_from_sentences(self):
        dialog = self._dialog()
        dialog.text_edit.setPlainText("第一句。第二句！第三句？")
        dialog.build()
        self.assertIsNotNone(dialog.stats, f"没生成：{dialog.hint.text()}")
        self.assertEqual(len(dialog.stats["sentences"]), 3)
        self.assertGreater(dialog.stats["total_us"], 0)
        project = dialog.stats["project"]
        self.assertTrue(any(t.segments for t in project.tracks), "时间线是空的")

    def test_empty_script_is_rejected(self):
        dialog = self._dialog()
        dialog.build()
        self.assertIsNone(dialog.stats)
        self.assertIn("粘一段文案", dialog.hint.text())

    def test_menu_has_the_action(self):
        from freecut.qtui.app import MainWindow

        sample = os.path.join(ROOT, "out", "showcase.json")
        win = MainWindow(sample if os.path.isfile(sample) else None)
        try:
            texts = []
            for menu in win.menuBar().findChildren(type(win.menuBar().addMenu("x"))):
                texts.extend(action.text() for action in menu.actions())
            self.assertTrue(any("图文成片" in t for t in texts), texts[:10])
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
