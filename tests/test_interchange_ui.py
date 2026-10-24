"""界面能导出 FCP7 XML 与 .prproj（此前只有命令行）。"""

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


class TestInterchangeUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from freecut.qtui.app import MainWindow

        sample = os.path.join(ROOT, "out", "showcase.json")
        if not os.path.isfile(sample):
            self.skipTest("没有示例工程")
        win = MainWindow(sample)
        self.addCleanup(win.close)
        return win

    def test_menu_has_both_actions(self):
        win = self.window()
        texts = []
        for menu in win.menuBar().findChildren(type(win.menuBar().addMenu("x"))):
            texts.extend(action.text() for action in menu.actions())
        joined = " ".join(texts)
        self.assertIn("FCP7 XML", joined)
        self.assertIn(".prproj", joined)

    def test_export_fcp7_writes_file(self):
        win = self.window()
        out = os.path.join(tempfile.mkdtemp(prefix="xml-"), "t.xml")
        written = win.export_fcp7_to(out)
        self.assertTrue(os.path.isfile(written))
        self.assertGreater(os.path.getsize(written), 500)

    def test_export_prproj_writes_file(self):
        win = self.window()
        out = os.path.join(tempfile.mkdtemp(prefix="prproj-"), "t.prproj")
        written = win.export_prproj_to(out)
        self.assertTrue(os.path.isfile(written))
        self.assertGreater(os.path.getsize(written), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
