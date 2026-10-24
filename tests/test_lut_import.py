"""滤镜「导入 LUT」入口：合法文件进库、重名加后缀、坏文件拒绝、面板列表刷新。"""

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


_SALT = [0.0]


def _write_cube(folder: str, name: str = "my.cube") -> str:
    """生成一个合法但**内容唯一**的 2x2x2 cube（库按 md5 去重）。"""
    _SALT[0] += 0.125
    salt = min(0.875, _SALT[0] % 1.0)
    path = os.path.join(folder, name)
    values = ["0.0 0.0 0.0", "1.0 0.0 0.0", "0.0 1.0 0.0", "0.0 0.0 1.0",
              "1.0 1.0 0.0", "1.0 0.0 1.0", "0.0 1.0 1.0",
              f"{salt:.3f} 1.0 1.0"]
    lines = ['TITLE "t"', "LUT_3D_SIZE 2"] + values
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


class TestLutImport(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("LOCALAPPDATA")
        self.lad = tempfile.mkdtemp(prefix="lad-")
        os.environ["LOCALAPPDATA"] = self.lad

    def tearDown(self):
        if self._old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old

    def test_import_lands_in_library(self):
        from freecut import luts

        src = _write_cube(tempfile.mkdtemp(prefix="cube-"))
        dest = luts.import_cube(src)
        self.assertTrue(os.path.isfile(dest))
        self.assertTrue(dest.startswith(os.path.join(self.lad, "FreeCut", "luts")))
        names = [os.path.basename(entry.path) for entry in luts.library(refresh=True)]
        self.assertIn(os.path.basename(dest), names)

    def test_duplicate_names_get_suffix(self):
        from freecut import luts

        src = _write_cube(tempfile.mkdtemp(prefix="cube-"))
        first = luts.import_cube(src)
        second = luts.import_cube(src)
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.basename(second).endswith("-2.cube"))

    def test_broken_cube_is_rejected(self):
        from freecut import luts

        bad = os.path.join(tempfile.mkdtemp(prefix="bad-"), "bad.cube")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("这不是 LUT\n")
        with self.assertRaises(Exception):
            luts.import_cube(bad)
        self.assertFalse(any(name.endswith("bad.cube")
                             for name in (os.path.basename(e.path)
                                          for e in luts.library(refresh=True))))

    def test_panel_button_refreshes_list(self):
        from PySide6.QtWidgets import QApplication, QFileDialog

        app = QApplication.instance() or QApplication([])
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.bin_panel import BinPanel

        panel = BinPanel(Bridge(builder.new_project(320, 180, 30, name="lut")))
        self.addCleanup(panel.close)
        # 库有进程内缓存，跨用例会残留；先强制刷新一次再取基准
        from freecut import luts as _luts
        _luts.library(refresh=True)
        panel.refresh_luts()
        before = panel.lut_list.count()
        src = _write_cube(tempfile.mkdtemp(prefix="cube-"), "imp.cube")
        original = QFileDialog.getOpenFileName
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (src, "LUT (*.cube)"))
        try:
            panel.import_lut()
        finally:
            QFileDialog.getOpenFileName = original
        texts = [panel.lut_list.item(i).text()
                 for i in range(panel.lut_list.count())]
        self.assertTrue(any("imp" in t for t in texts),
                        "列表里没有导入的 LUT：%s" % texts)
        self.assertGreaterEqual(panel.lut_list.count(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
