"""崩溃恢复：能找回自动保存、用户拒绝时不动、退出时清理。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestAutosaveRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        self.addCleanup(win.close)
        self.addCleanup(win.clear_autosave)
        return win

    def test_no_autosave_means_no_recovery(self):
        win = self._window()
        win.clear_autosave()
        self.assertFalse(win.recover_autosave(ask=lambda p: True))

    def test_recovery_loads_the_autosave(self):
        from freecut import builder

        win = self._window()
        folder = win._autosave_dir()
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "recover_me.json")
        project = builder.new_project(320, 180, 30, name="恢复")
        project.save(target)

        asked = []
        ok = win.recover_autosave(ask=lambda p: asked.append(p) or True)
        self.assertTrue(ok)
        self.assertEqual(asked[0], target)
        self.assertIn("恢复", win.status_label.text())

    def test_declining_keeps_the_project_untouched(self):
        from freecut import builder

        win = self._window()
        folder = win._autosave_dir()
        os.makedirs(folder, exist_ok=True)
        project = builder.new_project(320, 180, 30, name="不要恢复")
        project.save(os.path.join(folder, "skip_me.json"))

        before = win.bridge.path
        self.assertFalse(win.recover_autosave(ask=lambda p: False))
        self.assertEqual(win.bridge.path, before)

    def test_clear_removes_the_file(self):
        from freecut import builder

        win = self._window()
        folder = win._autosave_dir()
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "clean_me.json")
        builder.new_project(320, 180, 30, name="清理").save(target)
        self.assertTrue(os.path.isfile(target))
        win.clear_autosave()
        self.assertIsNone(win.latest_autosave())


if __name__ == "__main__":
    unittest.main(verbosity=2)
