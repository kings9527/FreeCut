"""导出保真：关键帧在成片里生效，且颜色与渲染器直出一致。"""

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


def _decode_frames(path: str, wanted):
    import av
    import numpy as np

    got = {}
    container = av.open(path)
    try:
        stream = container.streams.video[0]
        for index, frame in enumerate(container.decode(stream)):
            if index in wanted:
                got[index] = frame.to_ndarray(format="rgb24").astype("int16")
            if index > max(wanted):
                break
    finally:
        container.close()
    return got


class TestExportFidelity(unittest.TestCase):
    def setUp(self):
        try:
            import av  # noqa: F401
            import numpy as np  # noqa: F401
        except Exception:                              # noqa: BLE001
            self.skipTest("没有 PyAV / numpy")
        if not os.path.isfile(IMAGE):
            self.skipTest("没有测试图片")

    def _export(self, project):
        from freecut.export import PRESETS, export, with_quality

        tmp = tempfile.mkdtemp(prefix="freecut-fid-")
        out = os.path.join(tmp, "fid.mp4")
        export(project, out, with_quality(PRESETS["720p30"], "推荐"))
        return out

    def test_keyframes_are_rendered_into_the_file(self):
        from freecut import builder
        from freecut.model import Keyframe
        import numpy as np

        project = builder.new_project(1280, 720, 30, name="关键帧成片")
        seg = builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        seg.keyframes["KFTypeScaleX"] = [Keyframe(0, 1.0), Keyframe(800_000, 1.6)]

        frames = _decode_frames(self._export(project), {3, 27})
        self.assertIn(3, frames)
        self.assertIn(27, frames)
        diff = float(abs(frames[3] - frames[27]).mean())
        self.assertGreater(diff, 2.0, f"成片里关键帧没生效（差 {diff:.2f}）")

    def test_exported_frame_matches_renderer(self):
        from freecut import builder
        from freecut.render import Renderer
        import numpy as np

        project = builder.new_project(1280, 720, 30, name="颜色保真")
        builder.add_image(project, IMAGE, start=0, duration=1_000_000)
        out_path = self._export(project)

        rendered = _decode_frames(out_path, {5})[5]
        renderer = Renderer(project, 1280, 720, 30)
        try:
            direct = renderer.render_frame(5).astype("int16")
        finally:
            renderer.close()
        diff = float(abs(rendered - direct).mean())
        self.assertLess(diff, 12.0, f"成片与渲染器色差过大：{diff:.1f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
