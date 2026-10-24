"""Output verification: does the file we just produced actually look and sound right?

Writing tests against "the code did not raise" is how a silent-audio bug survived
several rounds: the mix graph was invalid, FFmpeg failed, ``export()`` quietly
fell back to a silent track, and every test still passed.  This module inspects
the **artifact** instead of the code:

* video: decode a low-rate sample, mean luma, black-frame ratio, motion, and the
  real frame count at the container's own frame rate;
* audio: decode to PCM and measure RMS / peak so "has an audio stream" is
  distinguished from "actually has sound";
* sync: compare the audio duration against the video duration.

``freecut verify out.mp4`` prints the report and exits non-zero when a problem
is found.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .media import _creationflags, ffmpeg_path

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_VIDEO_LINE_RE = re.compile(r"Stream #[\d:]+.*?Video:\s*([A-Za-z0-9_]+)")
#: "0x31637661" (an avc1 fourcc) must not be mistaken for "1920x1080", so the
#: width/height match needs non-word boundaries on both sides
_RES_RE = re.compile(r"(?<![\w])(\d{2,5})x(\d{2,5})(?![\w])")
_FPS_RE = re.compile(r"([\d.]+)\s*fps")
_AUDIO_LINE_RE = re.compile(r"Stream #[\d:]+.*?Audio:\s*([A-Za-z0-9_]+)")


@dataclass
class VerifyReport:
    path: str
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    sample_frames: int = 0
    mean_luma: float = 0.0
    black_ratio: float = 0.0
    motion: float = 0.0
    frame_count: int = 0
    expected_frames: int = 0
    audio_rms_db: float = -120.0
    audio_peak_db: float = -120.0
    audio_duration_s: float = 0.0
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_text(self) -> str:
        lines = [
            f"文件: {os.path.basename(self.path)}",
            f"  时长 {self.duration_s:.3f}s  {self.width}x{self.height} @ {self.fps:g} fps  "
            f"[{self.video_codec or '-'} / {self.audio_codec or '无音轨'}]",
            f"  帧数 {self.frame_count}（期望 {self.expected_frames}）",
            f"  画面：抽样 {self.sample_frames} 帧，平均亮度 {self.mean_luma:.1f}/255，"
            f"黑帧占比 {self.black_ratio * 100:.0f}%，帧间差 {self.motion:.2f}",
            f"  声音：RMS {self.audio_rms_db:.1f} dBFS，峰值 {self.audio_peak_db:.1f} dBFS，"
            f"时长 {self.audio_duration_s:.3f}s",
        ]
        if self.warnings:
            lines.extend(f"  注意：{w}" for w in self.warnings)
        if self.problems:
            lines.append("  问题：")
            lines.extend(f"    - {p}" for p in self.problems)
            lines.append("  结论：不通过")
        else:
            lines.append("  结论：通过")
        return "\n".join(lines)


def _probe(path: str) -> Tuple[float, int, int, float, str, str]:
    out = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-i", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_creationflags(),
    )
    text = (out.stderr or "") + (out.stdout or "")
    duration = 0.0
    m = _DURATION_RE.search(text)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    width = height = 0
    fps = 0.0
    vcodec = ""
    for line in text.splitlines():
        mv = _VIDEO_LINE_RE.search(line)
        if not mv:
            continue
        vcodec = mv.group(1)
        res = _RES_RE.search(line)
        if res:
            width, height = int(res.group(1)), int(res.group(2))
        fr = _FPS_RE.search(line)
        if fr:
            fps = float(fr.group(1))
        break
    acodec = ""
    for line in text.splitlines():
        ma = _AUDIO_LINE_RE.search(line)
        if ma:
            acodec = ma.group(1)
            break
    return duration, width, height, fps, vcodec, acodec


def _sample_video(path: str, fps: float = 2.0, w: int = 96, h: int = 54) -> np.ndarray:
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", path,
         "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, creationflags=_creationflags(),
    )
    n = w * h * 3
    count = len(proc.stdout) // n
    if count == 0:
        return np.zeros((0, h, w, 3), dtype=np.uint8)
    return np.frombuffer(proc.stdout[: count * n], dtype=np.uint8).reshape(count, h, w, 3)


def _decode_audio(path: str, rate: int = 16000) -> np.ndarray:
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", path,
         "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
        capture_output=True, creationflags=_creationflags(),
    )
    if not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _count_frames(path: str, fps: float) -> int:
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-i", path, "-map", "0:v:0",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_creationflags(),
    )
    frames = re.findall(r"frame=\s*(\d+)", proc.stderr or "")
    return int(frames[-1]) if frames else 0


def verify(path: str, expect_duration_s: Optional[float] = None,
           expect_fps: Optional[float] = None,
           expect_audio: Optional[bool] = None) -> VerifyReport:
    """Inspect ``path`` and report anything that looks broken.

    ``expect_audio``: ``True`` = the project had sound, so silence is a failure;
    ``False`` = a silent output is expected; ``None`` (default) = report silence
    as a warning only, because FreeCut always muxes a silent track for projects
    that simply have no audio.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    rep = VerifyReport(path=path)
    rep.duration_s, rep.width, rep.height, rep.fps, rep.video_codec, rep.audio_codec = _probe(path)
    if rep.width == 0:
        rep.problems.append("没有视频流")
        return rep
    if expect_fps:
        rep.fps = expect_fps

    frames = _sample_video(path)
    rep.sample_frames = len(frames)
    if rep.sample_frames:
        luma = frames[..., 0] * 0.299 + frames[..., 1] * 0.587 + frames[..., 2] * 0.114
        rep.mean_luma = float(luma.mean())
        per_frame = luma.reshape(len(luma), -1).mean(axis=1)
        rep.black_ratio = float((per_frame < 8.0).mean())
        if len(frames) > 1:
            rep.motion = float(np.abs(np.diff(frames.astype(np.int16), axis=0)).mean())
        if rep.mean_luma < 8.0:
            rep.problems.append(f"画面几乎全黑（平均亮度 {rep.mean_luma:.1f}/255）")
        elif rep.black_ratio > 0.9:
            rep.problems.append(f"90% 以上是黑帧（{rep.black_ratio * 100:.0f}%）")
    else:
        rep.problems.append("无法解码出任何画面")

    rep.frame_count = _count_frames(path, rep.fps)
    if rep.fps:
        rep.expected_frames = int(round(rep.duration_s * rep.fps))
        if rep.frame_count and abs(rep.frame_count - rep.expected_frames) > 2:
            rep.problems.append(
                f"帧数不符：{rep.frame_count} vs 期望 {rep.expected_frames}"
            )

    if rep.audio_codec:
        samples = _decode_audio(path)
        if samples.size:
            rep.audio_duration_s = samples.size / 16000.0
            peak = float(np.abs(samples).max())
            rms = float(np.sqrt((samples ** 2).mean()))
            rep.audio_peak_db = 20.0 * np.log10(max(peak, 1e-9))
            rep.audio_rms_db = 20.0 * np.log10(max(rms, 1e-9))
            if rep.audio_peak_db < -60.0:
                msg = f"音轨是静音（峰值 {rep.audio_peak_db:.1f} dBFS）"
                if expect_audio is False:
                    rep.warnings.append(msg + "，但工程本身没有音频，属正常")
                elif expect_audio is True:
                    rep.problems.append(msg)
                else:
                    rep.warnings.append(msg + "（工程没有音频时属正常，可用 --expect-audio 判定）")
            if rep.duration_s and abs(rep.audio_duration_s - rep.duration_s) > 0.3:
                rep.problems.append(
                    f"音视频时长差 {abs(rep.audio_duration_s - rep.duration_s):.2f}s"
                )
        else:
            rep.problems.append("有音轨但解码不出样本")
    elif expect_audio is True:
        rep.problems.append("工程有音频，但输出没有音频流")
    else:
        rep.warnings.append("输出没有音频流")

    if expect_duration_s is not None:
        if abs(rep.duration_s - expect_duration_s) > 1.0 / max(1.0, rep.fps) * 2:
            rep.problems.append(
                f"总时长不符：{rep.duration_s:.3f}s vs 期望 {expect_duration_s:.3f}s"
            )
    return rep
