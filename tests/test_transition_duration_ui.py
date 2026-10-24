"""属性页「转场时长」可编辑：读回原时长，改了就写进转场材质。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

IMAGE = os.path.join(ROOT, "out", "chroma_src.png")


class TestTransitionDurationEdit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _setup(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="转场时长")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        second = builder.add_image(project, IMAGE, start=1_000_000, duration=1_000_000)
        builder.add_transition(project, second, kind="wipe_left", duration_us=400_000)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(second)
        self.app.processEvents()
        return project, second, inspector

    def test_spin_loads_existing_duration(self):
        _project, _seg, inspector = self._setup()
        self.assertTrue(inspector.transition_spin.isEnabled())
        self.assertAlmostEqual(inspector.transition_spin.value(), 0.4, delta=0.05)

    def test_changing_spin_updates_the_material(self):
        project, seg, inspector = self._setup()
        inspector.transition_spin.setValue(1.2)
        ref = project.first_ref(seg, "transition")
        self.assertEqual(ref.get("duration"), 1_200_000)

    def test_spin_disabled_without_transition(self):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")
        project = builder.new_project(320, 180, 30, name="无转场")
        seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        self.app.processEvents()
        self.assertFalse(inspector.transition_spin.isEnabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
