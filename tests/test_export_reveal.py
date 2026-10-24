"""导出完成后"打开所在文件夹"：调用可替换、缺文件时不动。"""

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


class TestRevealInFolder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        project = builder.new_project(320, 180, 30, name="reveal")
        dialog = ExportDialog(Bridge(project))
        self.addCleanup(dialog.close)
        return dialog

    def test_opens_explorer_with_the_file_selected(self):
        import subprocess as sp

        dialog = self._dialog()
        target = os.path.join(tempfile.mkdtemp(prefix="reveal-"), "out.mp4")
        with open(target, "wb") as fh:
            fh.write(b"x")

        calls = []
        original = sp.Popen

        def fake_popen(args, *a, **k):
            calls.append(list(args))
            return original(["cmd", "/c", "exit", "0"], *a, **k)

        sp.Popen = fake_popen
        try:
            dialog._reveal_in_folder(target)
        finally:
            sp.Popen = original
        self.assertTrue(calls, "没有调用任何打开命令")
        joined = " ".join(calls[0])
        self.assertIn("out.mp4", joined)

    def test_missing_file_is_a_noop(self):
        import subprocess as sp

        dialog = self._dialog()
        calls = []
        original = sp.Popen
        sp.Popen = lambda args, *a, **k: calls.append(args)
        try:
            dialog._reveal_in_folder(os.path.join(tempfile.gettempdir(), "nope_404.mp4"))
        finally:
            sp.Popen = original
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
