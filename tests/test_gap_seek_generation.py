"""第 17 轮：拖动时的"代际"——过期结果必须丢掉。

对应剪映的 `seekToTimeAndWaitForFrameRendered`：每次 seek 带一个序号，
只有最新那次的结果会显示（见 docs/measurements/PLAYBACK-AUDIO-RE.md §3）。
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


class TestStaleFramesAreDropped(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_viewer_ignores_a_frame_older_than_the_one_shown(self):
        from freecut import builder
        from freecut.qtui.app import MainWindow
        from PySide6.QtGui import QImage

        win = MainWindow(None)
        try:
            win.bridge.set_project(builder.new_project(320, 180, 30), None)
            win._refresh_all()
            self.app.processEvents()

            def make(color):
                image = QImage(64, 36, QImage.Format_RGB888)
                image.fill(color)
                return image

            newest = make(0x00FF00)
            stale = make(0xFF0000)
            win.viewer._on_frame(10, newest)
            shown_after_new = win.viewer._shown_token
            win.viewer._on_frame(7, stale)          # 迟到的旧帧
            self.assertEqual(win.viewer._shown_token, shown_after_new,
                             "过期的帧把已显示的帧顶掉了")
        finally:
            win.close()

    def test_service_coalesces_burst_requests(self):
        """连发 5 次请求，渲染次数应当远少于请求次数（只画最新的）。"""
        from freecut import builder
        from freecut.qtui.app import MainWindow
        from freecut.render import Renderer

        win = MainWindow(None)
        try:
            project = builder.new_project(320, 180, 30, name="合并")
            image = os.path.join(ROOT, "out", "chroma_src.png")
            if not os.path.isfile(image):
                self.skipTest("没有测试图片")
            builder.add_image(project, image, start=0, duration=5_000_000)
            win.bridge.set_project(project, None)
            win._refresh_all()
            service = win.bridge.render_service
            # 界面自己也会请求一帧（schedule），所以按增量断言
            base_req = service.stats["requested"]
            base_rendered = service.stats["rendered"]
            for i in range(5):
                service.request(160, 90, i * 500_000)
            for _ in range(80):
                self.app.processEvents()
                if service.stats["rendered"] - base_rendered >= 2:
                    break
                from PySide6.QtCore import QThread
                QThread.msleep(10)
            rendered = service.stats["rendered"] - base_rendered
            requested = service.stats["requested"] - base_req
            self.assertGreaterEqual(requested, 5)
            self.assertLessEqual(rendered, 2,
                                 f"合并失败：请求 {requested} 次、渲染 {rendered} 次")
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
