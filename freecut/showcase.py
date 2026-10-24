"""Showcase: one project that exercises every major feature.

The point is a **verifiable** demo, not a pretty one: it builds its own media,
lays out every capability in sequence, and can export + self-check itself.  If
this renders and round-trips, the feature list is real rather than claimed.

Sequence (about 24 s at 30 fps):

===  ==============================================================
0 s  色度抠图：绿幕素材抠到渐变背景上
3 s  文字模板：综艺黄底标题 + 字幕条
6 s  分屏：左右两路素材（自动裁剪填满）
9 s  曲线变速：慢→快→慢
12 s 调色：曲线 + HSL + 色轮 + 调节图层
15 s 转场 + 入场动画
18 s 程序化特效（故障 / 老电影）
21 s 画中画 + 关键帧推近
===  ==============================================================
"""

from __future__ import annotations

import math
import os
import subprocess
import wave
from typing import Dict, List, Optional

import numpy as np

from . import builder
from .model import Project


def _ffmpeg() -> str:
    from .media import ffmpeg_path

    return ffmpeg_path()


def _flags() -> int:
    from .media import _creationflags

    return _creationflags()


def _clip(path: str, spec: str) -> str:
    """Render a tiny synthetic clip once (cached on disk).

    ``spec`` is a complete lavfi source, e.g. ``testsrc2=s=640x360:d=6:r=30``
    — some sources (``life``) do not accept ``d=``, so callers spell it out.
    """
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", spec,
         "-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p", path],
        check=True, creationflags=_flags(),
    )
    return path


def _tone(path: str, seconds: float, freq: float = 220.0) -> str:
    """A soft tone (cached) so the showcase has audio to preview and export."""
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rate = 22050
    t = np.arange(int(rate * seconds)) / rate
    env = np.minimum(1.0, np.minimum(t * 4, (seconds - t) * 4))
    data = (0.25 * env * np.sin(2 * math.pi * freq * t) * 32767).astype("<i2")
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(data.tobytes())
    return path


def _green_screen(path: str, size=(640, 360)) -> str:
    """A green-screen still with a subject and spill, for the chroma segment."""
    if os.path.isfile(path):
        return path
    from PIL import Image

    w, h = size
    img = np.zeros((h, w, 3), np.float32)
    img[..., 1] = 0.85
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy, r = w // 2, h // 2, int(h * 0.32)
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    inner = d2 <= r ** 2
    rim = (d2 <= (r + 12) ** 2) & ~inner
    img[inner] = [0.86, 0.30, 0.22]
    img[rim] = [0.45, 0.68, 0.30]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(path)
    return path


def _text_overlap(a: str, b: str) -> float:
    """Character-level recall of ``a`` in ``b`` (order-free, punctuation-free)."""
    strip = lambda s: [c for c in str(s or "") if c not in "，。！？、,.!? \t\n"]  # noqa: E731
    left = strip(a)
    if not left:
        return 0.0
    right = set(strip(b))
    hit = sum(1 for ch in left if ch in right)
    return hit / len(left)

def build_showcase(work_dir: str, width: int = 1280, height: int = 720,
                   fps: float = 30.0, with_asr: bool = False,
                   with_voice: bool = True) -> Dict:
    """Build the demo project; returns ``{"project", "steps"}``."""
    os.makedirs(work_dir, exist_ok=True)
    media = lambda name: os.path.join(work_dir, name)          # noqa: E731

    bars = _clip(media("bars.mp4"), "testsrc2=s=640x360:d=6:r=30")
    grad = _clip(media("grad.mp4"), "gradients=s=640x360:d=6:r=30")
    life = _clip(media("life.mp4"), "smptehdbars=s=640x360:d=6:r=30")
    green = _green_screen(media("green.png"))
    tone = _tone(media("tone.wav"), 26, 220)

    p = builder.new_project(width, height, fps, name="FreeCut 功能总演示")
    p.canvas.background = "#101218"
    steps: List[str] = []

    # 0-3s 色度抠图 ---------------------------------------------------------
    # 背景铺满 0-6 秒：标题那 3 秒不能是黑场
    bg = builder.add_video(p, grad, start=0, duration=6_000_000, track_index=0)
    sub = builder.add_image(p, green, start=0, duration=3_000_000, track_index=1)
    builder.add_chroma(p, sub, key="#00ff00")
    steps.append("色度抠图")

    # 3-6s 文字模板 ---------------------------------------------------------
    title = builder.add_text(p, "FreeCut 功能总演示", start=0, duration=6_000_000,
                             size=9.0, y=0.72)
    from . import textstyles

    textstyles.apply(p.material(title.material_id), "综艺黄底")
    steps.append("文字模板")

    # 6-9s 左右分屏 ---------------------------------------------------------
    left = builder.add_video(p, bars, start=6_000_000, duration=3_000_000, track_index=0)
    right = builder.add_video(p, life, start=6_000_000, duration=3_000_000, track_index=1)
    from . import layouts

    layouts.apply(p, "左右分屏", [left, right])
    steps.append("左右分屏")

    # 9-12s 曲线变速 --------------------------------------------------------
    fast = builder.add_video(p, bars, start=9_000_000, duration=3_000_000, track_index=0)
    builder.set_speed_curve(p, fast, [{"x": 0, "y": 0.4}, {"x": 0.5, "y": 2.2}, {"x": 1, "y": 0.6}])
    steps.append("曲线变速")

    # 12-15s 调色 -----------------------------------------------------------
    graded = builder.add_video(p, grad, start=12_000_000, duration=3_000_000, track_index=0)
    builder.set_curves(p, graded, rgb=[[0, 0.05], [0.5, 0.58], [1, 0.95]])
    builder.set_hsl(p, graded, bands={"orange": {"sat": 0.35, "hue": 0.05}})
    builder.set_wheels(p, graded, gain=[1.08, 1.0, 0.94])
    builder.set_adjust(p, graded, contrast=18, saturation=12)
    builder.add_adjustment(p, start=13_000_000, duration=1_500_000, vignette=35)
    steps.append("调色 + 调节图层")

    # 15-18s 转场 + 动画 ----------------------------------------------------
    a = builder.add_video(p, bars, start=15_000_000, duration=1_500_000, track_index=0)
    b = builder.add_video(p, grad, start=16_500_000, duration=1_500_000, track_index=0)
    builder.add_transition(p, b, name="叠化", duration_us=500_000)
    builder.add_animation(p, b, "渐显")
    steps.append("转场 + 入场动画")

    # 18-21s 程序化特效 -----------------------------------------------------
    fx_seg = builder.add_video(p, life, start=18_000_000, duration=3_000_000, track_index=0)
    builder.add_effect(p, fx_seg, "glitch", strength=0.35)
    steps.append("程序化特效")

    # 21-24s 画中画 + 关键帧 ------------------------------------------------
    main = builder.add_video(p, grad, start=21_000_000, duration=3_000_000, track_index=0)
    inset = builder.add_video(p, bars, start=21_000_000, duration=3_000_000, track_index=1)
    layouts.apply(p, "画中画·右下", [main, inset])
    builder.add_keyframe(inset, "KFTypeScaleX", 0, 0.34)
    builder.add_keyframe(inset, "KFTypeScaleX", 2_500_000, 0.55)
    builder.add_keyframe(inset, "KFTypeScaleY", 0, 0.34)
    builder.add_keyframe(inset, "KFTypeScaleY", 2_500_000, 0.55)
    steps.append("画中画 + 关键帧")

    # 24-30s 音频处理 ------------------------------------------------------
    audio_seg = builder.add_audio(p, tone, start=24_000_000, duration=6_000_000, track_index=0)
    builder.set_audio_effects(p, audio_seg, denoise=0.55, pitch=1.35, eq=(0.0, 2.0, 5.0))
    builder.set_volume(audio_seg, 0.9)
    a_clip = builder.add_video(p, life, start=24_000_000, duration=6_000_000, track_index=0)
    from . import textstyles as _ts

    audio_note = builder.add_text(p, "音频：降噪 / 变声 1.35x / EQ / 淡入淡出",
                                  start=24_000_000, duration=6_000_000, size=6.5, y=-0.78)
    _ts.apply(p.material(audio_note.material_id), "字幕条")
    steps.append("音频处理")

    # 30-33s 字幕 / 卡拉OK --------------------------------------------------
    from . import subtitles as _subs

    karaoke_text = "逐字高亮 卡拉OK 字幕"
    sub = _subs.Subtitle(
        start_us=30_000_000,
        end_us=33_000_000,
        text=karaoke_text,
        words=_subs.distribute_words(karaoke_text, 0, 3_000_000),
    )
    _subs.add_subtitles(
        p, [sub], karaoke=True,
        style={"size": 8.5, "y": -0.74, "color": "#FFFFFF",
               "highlight_color": "#FFD84D", "background_color": "#000000",
               "background_alpha": 0.4},
    )
    builder.add_video(p, bars, start=30_000_000, duration=3_000_000, track_index=0)
    steps.append("字幕 / 卡拉OK")

    # 33-38s 自动字幕（TTS → ASR → 字幕） --------------------------------
    asr_info = {}
    if with_asr:
        from . import asr as _asr
        from . import tts as _tts

        narration_text = "这是自动字幕测试。语音识别能把它变成字幕。"
        narration = os.path.join(work_dir, "narration.wav")
        ok, _dur = _tts.ensure_wav(narration_text, narration)
        if ok:
            try:
                recognized = _asr.transcribe_to_subtitles(
                    narration, device="cpu", compute_type="int8", prompt=None)
            except Exception:
                recognized = []
            if recognized:
                offset = 33_000_000
                for sub in recognized:
                    sub.start_us += offset
                    sub.end_us += offset
                _subs.add_subtitles(p, recognized, karaoke=True,
                                    style={"size": 8.0, "y": -0.80})
                builder.add_audio(p, narration, start=offset,
                                  duration=5_000_000, track_index=0)
                builder.add_video(p, life, start=offset, duration=5_000_000, track_index=0)
                heard = " ".join(s.text for s in recognized)
                asr_info = {
                    "spoken": narration_text,
                    "heard": heard,
                    "similarity": _text_overlap(narration_text, heard),
                    "words": sum(len(s.words or []) for s in recognized),
                }
                steps.append("自动字幕（ASR）")

    # 全片说明字幕：每个环节一条，放在画面下方。
    # 30 秒之后的两段本来就有自己的演示字幕（卡拉OK / ASR），
    # 这里不再叠加，免得两行字幕抢同一块地方。
    captions = [
        (0_000_000, 3_000_000, "色度抠图：绿幕按色相抠除，保留边缘过渡"),
        (3_000_000, 6_000_000, "文字模板：14 套花字，描边/底色/阴影可调"),
        (6_000_000, 9_000_000, "分屏布局：左右分屏与画中画的裁剪表达"),
        (9_000_000, 12_000_000, "曲线变速：分段速度点，音频同步拉伸"),
        (12_000_000, 15_000_000, "调色：亮度/对比度/曲线/色轮/HSL 全链路"),
        (15_000_000, 18_000_000, "转场与出场动画：按资源 id 匹配剪映内置"),
        (18_000_000, 21_000_000, "程序化特效：噪点、扫描线、暗角"),
        (21_000_000, 24_000_000, "关键帧：位置/缩放/旋转多条曲线同时插值"),
        # 24-30s 那一段本来就有一条说明文字（降噪/变声/EQ），这里不再叠加
    ]
    _subs.add_subtitles(
        p,
        [_subs.Subtitle(start_us=s, end_us=e, text=text) for s, e, text in captions],
        karaoke=False,
        style={"size": 5.4, "y": -0.90, "color": "#F2F4FA",
               "background_color": "#000000", "background_alpha": 0.45},
    )

    # 24 秒的底噪，让预览与导出的声音都真实存在。
    # 音量压到 0.18：下面还有旁白，底噪不该盖住人声。
    builder.add_audio(p, tone, start=0, duration=24_000_000, volume=0.18, track_index=0)

    # 每段一句旁白（TTS 现场合成）。放在单独一条音轨上，
    # 和底噪并存，也让"配音"这个能力在总演示里真的出现。
    # 一句说明，而不是一个名词：观众不用猜这一段在演示什么。
    # rate=3 是先量过的——这八句在 rate=2 下有一句 2.65s 会顶到 3 秒边界。
    narration_lines = [
        (0_000_000, "绿幕按色相抠除"),
        (3_000_000, "十四套花字模板"),
        (6_000_000, "左右分屏与画中画"),
        (9_000_000, "分段速度点控制曲线"),
        (12_000_000, "调色链路八个环节"),
        (15_000_000, "转场按资源号匹配"),
        (18_000_000, "噪点扫描线与暗角"),
        (21_000_000, "位置缩放旋转同时插值"),
    ]
    if with_voice:
        from . import tts as _tts

        for start, line in narration_lines:
            clip = os.path.join(work_dir, f"voice_{start // 1_000_000}.wav")
            ok, dur = _tts.ensure_wav(line, clip, rate=3)
            if not ok:
                continue
            # 段首留 0.12s、段尾留 0.1s：人声不要贴边，听感更自然
            builder.add_audio(p, clip, start=start + 120_000,
                              duration=min(dur, 2_780_000), volume=1.0,
                              fade_in_ms=40, fade_out_ms=90, track_index=1)
        steps.append("旁白配音（TTS）")


    stats = {"project": p, "steps": steps, "total_s": p.duration() / 1e6}
    if asr_info:
        stats["asr"] = asr_info
    return stats


def summary(stats: Dict) -> str:
    steps = stats.get("steps") or []
    return f"{stats.get('total_s', 0):.1f} 秒 / {len(steps)} 个环节：" + "、".join(steps)


def export_and_check(project: Project, draft_root: str, name: str = "FreeCut 功能总演示",
                     samples: int = 3) -> Dict:
    """Write a JianyingPro draft of the showcase and self-check it."""
    from .jianying_export import save_draft
    from .selfcheck import check_draft

    notes: List[str] = []
    draft_dir = save_draft(project, draft_root, name=name, encrypt=False, warnings=notes)
    report = check_draft(project, draft_dir, samples=samples)
    return {"draft_dir": draft_dir, "report": report, "warnings": notes}
