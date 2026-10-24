"""第 17 轮：主轨磁吸写进草稿（剪映的 DRAFT_SET_MAIN_TRACK_ADSORB_ACTION）。

逆向依据：`docs/measurements/RUNTIME-CLOCK-TRACK-GPU.md` §2 ——
`maintrack_adsorb` / `setMainTrackAdsorb` / `DRAFT_SET_MAIN_TRACK_ADSORB_ACTION`
说明它是**草稿级**开关，不是纯界面状态。我们原来只存在内存里，换工程就丢。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


class TestMainTrackAdsorb(unittest.TestCase):
    def test_default_is_on(self):
        from freecut import builder

        project = builder.new_project(640, 360, 30)
        self.assertTrue(project.main_track_adsorb)

    def test_round_trips_through_the_project_file(self):
        from freecut import builder
        from freecut.model import Project

        project = builder.new_project(640, 360, 30, name="磁吸")
        project.main_track_adsorb = False
        tmp = tempfile.mkdtemp(prefix="freecut-adsorb-")
        path = os.path.join(tmp, "p.json")
        Project.save(project, path)
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertIn("main_track_adsorb", raw,
                      "草稿里没有 main_track_adsorb 字段")
        self.assertFalse(raw["main_track_adsorb"])
        loaded = Project.load(path)
        self.assertFalse(loaded.main_track_adsorb)

    def test_old_projects_default_to_on(self):
        """老工程（没有这个字段）读进来应当是开着的。"""
        from freecut import builder
        from freecut.model import Project

        project = builder.new_project(640, 360, 30, name="老工程")
        tmp = tempfile.mkdtemp(prefix="freecut-adsorb-")
        path = os.path.join(tmp, "p.json")
        Project.save(project, path)
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        raw.pop("main_track_adsorb", None)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        self.assertTrue(Project.load(path).main_track_adsorb)


class TestToolbarSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_toggle_writes_into_the_project(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        try:
            win.bridge.set_project(builder.new_project(640, 360, 30), None)
            win._refresh_all()
            win.set_option("magnetic", False)
            self.assertFalse(win.bridge.project.main_track_adsorb,
                             "关掉主轨磁吸没写进工程")
            win.set_option("magnetic", True)
            self.assertTrue(win.bridge.project.main_track_adsorb)
        finally:
            win.close()

    def test_opening_a_project_updates_the_toolbar(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow

        win = MainWindow(None)
        try:
            project = builder.new_project(640, 360, 30)
            project.main_track_adsorb = False
            win.bridge.set_project(project, None)
            win._refresh_all()
            self.assertFalse(win.timeline_toolbar.buttons["magnetic"].isChecked(),
                             "工程里磁吸是关的，工具栏却显示开着")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
