"""导出对话框的"导出后检测"：勾选框存在，检测摘要能算出来。"""

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

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


class TestExportVerifyOption(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.dialogs.export_dialog import ExportDialog

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="detect")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        dialog = ExportDialog(Bridge(project))
        self.addCleanup(dialog.close)
        return dialog

    def test_checkbox_exists_and_defaults_off(self):
        dialog = self._dialog()
        self.assertFalse(dialog.verify_box.isChecked(), "默认不该自动检测（会拖慢导出）")

    def test_verify_summary_on_a_real_file(self):
        from freecut.export import PRESETS, export, with_quality

        dialog = self._dialog()
        project = dialog.bridge.project
        out = os.path.join(tempfile.mkdtemp(prefix="verify-"), "v.mp4")
        export(project, out, with_quality(PRESETS["720p30"], "推荐"))
        summary = dialog._verify_output(out)
        self.assertTrue(summary.startswith("检测"), summary[:50])


if __name__ == "__main__":
    unittest.main(verbosity=2)
