"""剪映常用快捷键：空格、S、左右方向键都要真的连上。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestEditShortcuts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        """用带内容的工程（空工程 duration=0，播放头会被钳到 1µs，测不出步进）。"""
        from freecut.qtui.app import MainWindow

        sample = os.path.join(ROOT, "out", "showcase.json")
        win = MainWindow(sample if os.path.isfile(sample) else None)
        win.resize(1200, 800)
        self.app.processEvents()
        return win

    def _sequences(self, win):
        from PySide6.QtGui import QShortcut

        return {sc.key().toString() for sc in win.findChildren(QShortcut)}

    def test_space_s_and_arrows_are_bound(self):
        win = self.window()
        try:
            seqs = self._sequences(win)
            for expected in ("Space", "S", "Right", "Left"):
                self.assertIn(expected, seqs, f"{expected} 没有绑定")
        finally:
            win.close()

    def test_step_frames_moves_playhead_by_one_frame(self):
        win = self.window()
        try:
            win.bridge.set_playhead(1_000_000)     # 微秒
            before = win.bridge.playhead
            win.step_frames(1)
            fps = float(win.bridge.project.fps or 30)
            self.assertAlmostEqual(win.bridge.playhead - before, 1_000_000 / fps, delta=2.0)
            win.step_frames(-1)
            self.assertAlmostEqual(win.bridge.playhead, before, delta=1.0)
        finally:
            win.close()

    def test_step_frames_never_goes_negative(self):
        win = self.window()
        try:
            win.bridge.set_playhead(0)
            win.step_frames(-5)
            self.assertGreaterEqual(win.bridge.playhead, 0)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
