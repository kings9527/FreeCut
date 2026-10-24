"""Generate demo media and build a small project, then export it.

    python examples/make_demo.py [--out out.mp4]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from freecut import builder, export, render  # noqa: E402
from freecut.media import _creationflags, ffmpeg_path  # noqa: E402


def gen_media(dst: str) -> dict:
    os.makedirs(dst, exist_ok=True)
    ff = ffmpeg_path()

    def run(args):
        subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error", "-y"] + args,
            check=True,
            creationflags=_creationflags(),
        )

    a = os.path.join(dst, "bars.mp4")
    b = os.path.join(dst, "gradient.mp4")
    music = os.path.join(dst, "music.m4a")

    if not os.path.isfile(a):
        run([
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=4",
            "-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p", a,
        ])
    if not os.path.isfile(b):
        run([
            "-f", "lavfi",
            "-i", "gradients=size=1280x720:rate=30:duration=4:c0=0x203050:c1=0xF0A020:x0=0:y0=0:x1=1280:y1=720",
            "-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p", b,
        ])
    if not os.path.isfile(music):
        run([
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
            "-c:a", "aac", "-b:a", "128k", music,
        ])
    return {"a": a, "b": b, "music": music}


def build_show(media: dict):
    p = builder.new_project(1280, 720, 30, name="FreeCut 演示")
    clip_a = builder.add_video(p, media["a"], start=0)
    clip_b = builder.add_video(p, media["b"], start=3_000_000)
    builder.add_transition(p, clip_b, "cross_dissolve", 800_000)

    # colour grade on the second clip: the reconstructed adjustment chain
    builder.set_adjust(p, clip_b, brightness=8, contrast=12, saturation=18, shadow=6, highlight=-8, sharpen=10)

    # zoom-in with keyframes on the first clip
    builder.set_transform(clip_a, scale=1.0)
    builder.add_keyframe(clip_a, "KFTypeScaleX", 0, 1.0, "CurveInOut")
    builder.add_keyframe(clip_a, "KFTypeScaleX", 2_500_000, 1.25, "CurveInOut")
    builder.add_keyframe(clip_a, "KFTypeScaleY", 0, 1.0, "CurveInOut")
    builder.add_keyframe(clip_a, "KFTypeScaleY", 2_500_000, 1.25, "CurveInOut")

    # background music with fades
    builder.add_audio(p, media["music"], start=0, duration=7_000_000,
                      volume=0.35, fade_in_ms=600, fade_out_ms=1200)

    # captions
    builder.add_text(p, "FreeCut", start=200_000, duration=2_600_000,
                     y=0.62, size=17, color="#FFFFFF", bold=True,
                     stroke=0.035, stroke_color="#101010", shadow=0.5)
    builder.add_text(p, "免费 · 简洁 · 可编程的视频剪辑", start=2_800_000, duration=2_600_000,
                     y=-0.68, size=9, color="#FFE9A8", stroke=0.03)
    builder.add_text(p, "调色 / 关键帧 / 转场 / 字幕 / 音频混合", start=5_400_000, duration=2_000_000,
                     y=0.0, size=11, color="#FFFFFF", shadow=0.6)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "demo.mp4"))
    ap.add_argument("--stills", action="store_true", help="also dump PNG stills")
    ap.add_argument("--media", default=os.path.join(tempfile.gettempdir(), "freecut-demo-media"))
    args = ap.parse_args()

    media = gen_media(args.media)
    project = build_show(media)
    project.save(os.path.join(ROOT, "out", "demo_project.json"))

    def progress(frac: float, msg: str) -> None:
        bar = int(frac * 30)
        sys.stdout.write(f"\r[{'#' * bar}{'.' * (30 - bar)}] {frac*100:5.1f}%  {msg[:48]:48s}")
        sys.stdout.flush()

    out = export.export(project, args.out, export.ExportPreset(1280, 720, 30.0), progress)
    print(f"\n导出完成: {out}")
    if args.stills:
        stills = export.export_stills(project, 4, os.path.join(ROOT, "out", "stills"))
        for s in stills:
            print("still:", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

