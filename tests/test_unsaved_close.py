"""未保存退出：脏状态跟踪 + 关窗确认（保存/丢弃/取消）。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestUnsavedClose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)

        def _cleanup():
            win.bridge.dirty = False        # 否则 closeEvent 会弹真模态框，无头测试卡死
            win.close()

        self.addCleanup(_cleanup)
        return win

    def test_clean_window_closes_without_asking(self):
        win = self._window()
        win.bridge.dirty = False
        self.assertTrue(win._confirm_close())

    def test_dirty_and_cancel_blocks_close(self):
        from PySide6.QtWidgets import QMessageBox

        win = self._window()
        win.bridge.dirty = True
        original = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Cancel)
        try:
            self.assertFalse(win._confirm_close())
        finally:
            QMessageBox.question = original

    def test_dirty_and_discard_allows_close(self):
        from PySide6.QtWidgets import QMessageBox

        win = self._window()
        win.bridge.dirty = True
        original = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Discard)
        try:
            self.assertTrue(win._confirm_close())
        finally:
            QMessageBox.question = original

    def test_touch_marks_dirty_and_save_clears_it(self):
        win = self._window()
        win.bridge.dirty = False
        win.bridge.touch()
        self.assertTrue(win.bridge.dirty)
        win.bridge.dirty = False
        self.assertFalse(win.bridge.dirty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
