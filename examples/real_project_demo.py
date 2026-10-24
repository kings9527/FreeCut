"""Open a real JianyingPro project (encrypted or not) and export the first N seconds.

    python examples/real_project_demo.py "E:\\剪映-草稿\\JianyingPro Drafts\\7月22日" --seconds 6

This is the interop path that no 同类项目 tool covers: the draft is read through
the app's own DLL when encrypted, the timeline/transitions/colour are rebuilt in
FreeCut, and the result is encoded with FFmpeg.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from freecut import export, jianying  # noqa: E402
from freecut.model import Timerange  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", help="草稿目录或 draft_content.json")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "real_preview.mp4"))
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    args = ap.parse_args()

    project = jianying.load_draft(args.draft)
    print(f"导入: {len(project.tracks)} 轨 / {sum(len(t.segments) for t in project.tracks)} 片段"
          f" / {project.duration()/1e6:.2f}s / {project.canvas.width}x{project.canvas.height}")

    limit = int(args.seconds * 1_000_000)
    for track in project.tracks:
        kept = []
        for seg in track.sorted_segments():
            if seg.target_timerange.start >= limit:
                continue
            if seg.target_timerange.end > limit:
                cut = limit - seg.target_timerange.start
                seg.target_timerange.duration = cut
                seg.source_timerange.duration = int(cut * seg.speed)
            kept.append(seg)
        track.segments = kept
    print(f"裁剪到 {args.seconds}s -> {project.duration()/1e6:.2f}s")

    def progress(frac: float, msg: str) -> None:
        bar = int(frac * 28)
        sys.stdout.write(f"\r[{'#'*bar}{'.'*(28-bar)}] {frac*100:5.1f}% {msg[:44]:44s}")
        sys.stdout.flush()

    preset = export.ExportPreset(args.width, args.height, project.fps or 30.0, crf=18)
    out = export.export(project, args.out, preset, progress)
    print("\n导出:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
