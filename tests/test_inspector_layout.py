"""属性面板布局：文字页有分区、标签不被截断、色彩页不挤在一起。

背景：用户截图反馈"文字页混乱""HSL/曲线/色轮拥挤"，这组测试锁住修复结果。
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREECUT_NO_AUDIO", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SECTIONS = ["基础", "字形", "排版", "描边", "阴影", "背景框", "透明度", "逐字高亮", "附加"]


class TestInspectorLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _inspector(self, kind: str = "text"):
        from freecut import builder
        from freecut.qtui.bridge import Bridge
        from freecut.qtui.widgets.inspector import Inspector

        project = builder.new_project(320, 180, 30, name="布局")
        if kind == "text":
            seg = builder.add_text(project, "测试文字", start=0, duration=1_000_000)
        else:
            seg = builder.add_image(project, os.path.join(ROOT, "out", "chroma_src.png"),
                                    start=0, duration=1_000_000)
        bridge = Bridge(project)
        inspector = Inspector(bridge)
        bridge.select(seg)
        inspector.resize(350, 1200)
        inspector.show()
        self.app.processEvents()
        self.addCleanup(inspector.close)
        return inspector

    def test_text_page_has_nine_sections_in_order(self):
        inspector = self._inspector("text")
        labels = [w.text() for w in inspector.findChildren(type(inspector.title))
                  if w.styleSheet().startswith("color:#7f8792")]
        self.assertEqual(labels, SECTIONS)

    def test_no_slider_label_is_truncated(self):
        inspector = self._inspector("text")
        from freecut.qtui.widgets.inspector import SliderRow

        truncated = []
        for row in inspector.findChildren(SliderRow):
            label = row.findChildren(type(inspector.title))[0]
            need = label.fontMetrics().horizontalAdvance(label.text())
            if label.width() < need + 2:
                truncated.append((label.text(), label.width(), need))
        self.assertEqual(truncated, [], f"这些标签被截断：{truncated}")

    def test_numeric_spinbox_is_wide_enough(self):
        inspector = self._inspector("text")
        from freecut.qtui.widgets.inspector import SliderRow

        for row in inspector.findChildren(SliderRow):
            self.assertGreaterEqual(row.spin.width(), 64, f"{row.spin.width()} 太窄")

    def test_curve_and_wheel_are_not_squeezed(self):
        inspector = self._inspector("image")
        index = inspector.TABS.index("曲线")
        inspector.tabs.setCurrentIndex(index)
        self.app.processEvents()
        curve = inspector.curve
        self.assertGreaterEqual(curve.height(), 190, f"曲线高度 {curve.height()}")
        # 曲线与"曲线复位"按钮不能重叠
        reset = [b for b in curve.parent().findChildren(type(inspector.title))
                 if False]
        buttons = [b for b in inspector.pages["曲线"].findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton)
            if b.text() == "曲线复位"]
        self.assertTrue(buttons, "找不到曲线复位按钮")
        self.assertGreaterEqual(buttons[0].geometry().top(), curve.geometry().bottom(),
                                "曲线复位按钮压在曲线上")

    def test_short_panel_scrolls_vertically(self):
        """面板矮的时候要出现竖向滚动条，而不是把行压扁。"""
        inspector = self._inspector("text")
        inspector.resize(350, 520)
        self.app.processEvents()
        scroll = inspector.tabs.widget(inspector.TABS.index("文字"))
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0,
                           "内容放不下但没有竖向滚动条")

    def test_spinbox_is_tall_enough_for_digits(self):
        inspector = self._inspector("text")
        from freecut.qtui.widgets.inspector import SliderRow

        for row in inspector.findChildren(SliderRow)[:8]:
            need = row.spin.fontMetrics().height() + 6
            self.assertGreaterEqual(row.spin.height(), need,
                                    f"数字框只有 {row.spin.height()}px，会被切")

    def test_curve_reset_button_does_not_overlap_at_small_height(self):
        from PySide6.QtWidgets import QPushButton

        inspector = self._inspector("image")
        inspector.resize(350, 520)
        inspector.tabs.setCurrentIndex(inspector.TABS.index("曲线"))
        self.app.processEvents()
        buttons = [b for b in inspector.pages["曲线"].findChildren(QPushButton)
                   if b.text() == "曲线复位"]
        self.assertTrue(buttons)
        self.assertGreaterEqual(buttons[0].geometry().top(),
                                inspector.curve.geometry().bottom(),
                                "曲线复位按钮压在曲线上")


if __name__ == "__main__":
    unittest.main(verbosity=2)
