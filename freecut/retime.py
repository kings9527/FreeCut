"""倒放与定格：两件 ReaCut 有、我们原来没有的小能力。

两者都不是"改时间轴参数"，而是**生成一段新媒体再插进工程**：

* 倒放：FFmpeg ``reverse`` 滤镜重排帧序（音频一起倒，或丢掉）。
* 定格：把某一帧渲成 PNG，再当作静态图按指定时长插入。

这类操作放在这里而不是 ``builder`` 里，是因为它们要碰 FFmpeg 与渲染器，
而 ``builder`` 只该管内存里的工程结构。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .model import Project


class RetimeError(Exception):
    """Raised when a media file cannot be produced or read."""


def _ffmpeg() -> str:
    from .media import ffmpeg_path

    return ffmpeg_path()


def _run(args: list, timeout: float = 1800.0) -> None:
    creationflags = 0x08000000 if os.name == "nt" else 0     # 不弹控制台窗口
    proc = subprocess.run(
        args, capture_output=True, timeout=timeout, creationflags=creationflags,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RetimeError(
            "FFmpeg 失败:\n" + (proc.stderr or proc.stdout or "")[-800:])


def probe_duration_us(path: str) -> int:
    """Duration of a media file in microseconds (0 when unknown)."""
    from .media import probe

    try:
        info = probe(path)
    except Exception as exc:
        raise RetimeError(f"读不出时长: {path}: {exc}") from exc
    return int(getattr(info, "duration_us", 0) or 0)


def reverse_video(source: str, target: str, keep_audio: bool = True,
                  timeout: float = 1800.0) -> str:
    """Write ``source`` played backwards to ``target``.

    ``reverse`` buffers the whole clip in memory, so this is meant for clips of a
    few minutes, not a feature film.  ``keep_audio=False`` drops the audio track
    (useful when the sound only makes sense forwards).
    """
    if not os.path.isfile(source):
        raise RetimeError(f"找不到源文件: {source}")
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    # 编码参数要跟着导出模块走：剪映自带的 ffmpeg 是精简构建，
    # 没有 -crf/-preset 这类 x264 选项（实测直接报 "Unrecognized option 'crf'"）。
    from .export import pick_video_codec

    codec = pick_video_codec()
    args = [_ffmpeg(), "-y", "-i", source, "-vf", "reverse"]
    if keep_audio:
        args += ["-af", "areverse"]
    else:
        args += ["-an"]
    args += ["-c:v", codec, "-q:v", "3", "-pix_fmt", "yuv420p"]
    if codec in ("libx264", "h264"):
        args += ["-preset", "veryfast"]
    args += [target]
    _run(args, timeout=timeout)
    if not os.path.isfile(target):
        raise RetimeError(f"倒放没有产出文件: {target}")
    return target


def freeze_frame(project: Project, at_us: int, target: str,
                 width: Optional[int] = None, height: Optional[int] = None) -> str:
    """Render the frame at ``at_us`` to a PNG — the input for a freeze clip."""
    from .render import Renderer

    w = int(width or project.canvas.width or 1280)
    h = int(height or project.canvas.height or 720)
    if at_us < 0 or at_us > project.duration():
        raise RetimeError(f"时间点 {at_us} 不在工程范围内（0~{project.duration()}）")
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    renderer = Renderer(project, w, h, project.fps or 30)
    try:
        frame_index = max(0, int(round(at_us / 1_000_000 * (project.fps or 30.0))))
        image = renderer.render_frame(frame_index)
    finally:
        renderer.close()
    from PIL import Image

    Image.fromarray(image).save(target)
    return target


def add_reverse_clip(project: Project, source: str, work_dir: str,
                     start_us: int = 0, track_index: int = 0,
                     keep_audio: bool = True):
    """Reverse ``source`` into ``work_dir`` and append it to the timeline."""
    from . import builder

    target = os.path.join(work_dir, f"reverse_{Path(source).stem}.mp4")
    reverse_video(source, target, keep_audio=keep_audio)
    return builder.add_video(project, target, start=start_us, track_index=track_index)


def add_freeze_clip(project: Project, at_us: int, duration_us: int,
                    work_dir: str, track_index: int = 0,
                    width: Optional[int] = None, height: Optional[int] = None):
    """Insert a still of the frame at ``at_us`` for ``duration_us``."""
    from . import builder

    target = os.path.join(work_dir, f"freeze_{int(at_us / 1000)}.png")
    freeze_frame(project, at_us, target, width=width, height=height)
    return builder.add_image(project, target, start=at_us,
                             duration=max(1, int(duration_us)), track_index=track_index)
