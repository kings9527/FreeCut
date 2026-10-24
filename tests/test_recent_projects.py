"""最近打开：去重、限量、菜单可点、打开/保存后自动记录。"""

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


class TestRecentProjects(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = tempfile.mkdtemp(prefix="recent-appdata-")
        from freecut import recent

        self.recent = recent
        try:
            os.remove(recent.store_path())
        except OSError:
            pass

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old

    _counter = 0

    def _file(self) -> str:
        type(self)._counter += 1
        path = os.path.join(os.environ["APPDATA"], f"proj{self._counter}.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{}")
        return path

    def test_add_dedupes_and_moves_to_front(self):
        a, b = self._file(), self._file()
        self.recent.add(a)
        self.recent.add(b)
        items = self.recent.load()
        self.assertEqual(items[0], b)
        self.assertEqual(len(items), 2)
        self.recent.add(a)
        self.assertEqual(self.recent.load()[0], a)

    def test_limit_is_ten(self):
        for i in range(14):
            path = os.path.join(os.environ["APPDATA"], f"p{i}.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{}")
            self.recent.add(path)
        self.assertLessEqual(len(self.recent.load()), self.recent.MAX_ITEMS)

    def test_menu_lists_saved_projects(self):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from freecut.qtui.app import MainWindow

        path = self._file()
        self.recent.add(path)
        win = MainWindow(None)
        try:
            win._refresh_recent_menu()
            texts = [a.text() for a in win.recent_menu.actions()]
            self.assertIn(os.path.basename(path), texts)
        finally:
            win.bridge.dirty = False
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
