"""导出目标路径校验测试。"""

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


class TestExportTarget(unittest.TestCase):
    def test_existing_writable_dir_passes(self):
        from freecut.export import check_export_target

        folder = tempfile.mkdtemp(prefix="freecut-target-")
        check_export_target(os.path.join(folder, "out.mp4"))   # 不抛

    def test_missing_dir_is_reported(self):
        from freecut.export import ExportError, check_export_target

        bad = os.path.join(ROOT, "out", "no_such_dir_404", "out.mp4")
        with self.assertRaises(ExportError) as ctx:
            check_export_target(bad)
        self.assertIn("输出目录不存在", str(ctx.exception))

    def test_empty_filename_is_reported(self):
        from freecut.export import ExportError, check_export_target

        folder = tempfile.mkdtemp(prefix="freecut-target-")
        with self.assertRaises(ExportError) as ctx:
            check_export_target(folder + os.sep)
        self.assertIn("输出文件名", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
