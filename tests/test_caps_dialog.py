"""硬件能力对话框：能列出解码/编码器，且菜单里有入口。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestCapsDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_summary_lists_capabilities(self):
        from freecut import caps
        from freecut.qtui.dialogs.caps_dialog import CapsDialog

        if not caps.ffmpeg_path() if hasattr(caps, "ffmpeg_path") else False:
            pass
        dialog = CapsDialog()
        self.addCleanup(dialog.close)
        text = dialog.text.toPlainText()
        self.assertIn("硬件解码", text)
        self.assertIn("默认 H.264", text)
        self.assertGreater(len(text), 40)

    def test_menu_entry_exists(self):
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        try:
            texts = []
            for menu in win.menuBar().findChildren(type(win.menuBar().addMenu("x"))):
                texts.extend(action.text() for action in menu.actions())
            self.assertTrue(any("硬件加速" in t for t in texts), texts[:12])
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
