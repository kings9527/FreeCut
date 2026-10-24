"""导出对话框素材守卫测试。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestExportDialogMediaGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _dialog_for(self, image_path):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        project = builder.new_project(320, 180, 30, name="guard")
        builder.add_image(project, image_path, start=0, duration=1_000_000)
        bridge = Bridge(project)
        dlg = ExportDialog(bridge, None)
        self.addCleanup(dlg.close)
        return dlg

    def test_start_enabled_when_media_present(self):
        image = os.path.join(ROOT, "out", "chroma_src.png")
        if not os.path.isfile(image):
            self.skipTest("没有测试图片")
        dlg = self._dialog_for(image)
        self.assertTrue(dlg.start_btn.isEnabled())
        self.assertNotIn("素材缺失", dlg.hint.text())

    def test_start_disabled_and_hint_explains_when_media_missing(self):
        dlg = self._dialog_for(os.path.join(ROOT, "out", "gone_404.png"))
        self.assertFalse(dlg.start_btn.isEnabled(), "素材缺失时不该还能点导出")
        self.assertIn("素材缺失", dlg.hint.text())
        self.assertIn("gone_404.png", dlg.hint.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
