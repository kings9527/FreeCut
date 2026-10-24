"""Media probing, decoding and encoding helpers built on FFmpeg.

FFmpeg is free software; FreeCut shells out to it instead of bundling codecs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import numpy as np

# --------------------------------------------------------------------------- #
# tool discovery
# --------------------------------------------------------------------------- #
_WINDOWS_FALLBACKS = [
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\ProgramData\chocolatey\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
]


def _which(name: str) -> Optional[str]:
    p = shutil.which(name)
    if p:
        return p
    for root in _WINDOWS_FALLBACKS:
        cand = os.path.join(root, name + ".exe")
        if os.path.isfile(cand):
            return cand
    return None


def _bundled_dirs() -> List[str]:
    """Directories that may hold a ``ffmpeg.exe`` shipped next to the app."""
    dirs: List[str] = []
    try:
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
        pass
    meipass = getattr(sys, "_MEIPASS", None)          # PyInstaller onefile
    if meipass:
        dirs.append(str(meipass))
    here = os.path.dirname(os.path.abspath(__file__))
    dirs.append(os.path.dirname(here))                # repo root / source checkout
    dirs.append(here)
    return dirs


def _find_beside_app(name: str) -> Optional[str]:
    exe = name + (".exe" if sys.platform == "win32" else "")
    for d in _bundled_dirs():
        cand = os.path.join(d, exe)
        if os.path.isfile(cand):
            return cand
    return None


def ffmpeg_path() -> str:
    explicit = os.environ.get("FREECUT_FFMPEG")
    if explicit and os.path.isfile(explicit):
        return explicit
    # 放在程序旁边的 ffmpeg 优先于 PATH：绿色版双击就该能用
    beside = _find_beside_app("ffmpeg")
    if beside:
        return beside
    p = _which("ffmpeg")
    if p:
        return p
    # 这台机器上有剪映：它自带 ffmpeg，就在 apps 下面。打包版双击就能用，
    # 用户不必自己装 FFmpeg（第 184 轮补）。
    found = _find_in_jianying()
    if found:
        return found
    raise RuntimeError(
        "未找到 ffmpeg。把 ffmpeg.exe 放到 FreeCut.exe 同目录即可，"
        "或安装 FFmpeg 并加入 PATH，或设置环境变量 FREECUT_FFMPEG。"
        "（装了剪映的话，FreeCut 会自动用剪映自带的那份）"
    )


def _find_in_jianying() -> Optional[str]:
    """Look for 剪映's bundled ffmpeg under %LOCALAPPDATA%\\JianyingPro\\apps\\*.

    Only used as a last resort, and only when it actually answers ``-version``
    (剪映's ffmpeg 是个壳，单拷 exe 出来会因为缺 DLL 而不能用)。
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    apps = os.path.join(base, "JianyingPro", "apps")
    if not os.path.isdir(apps):
        return None

    def _version_key(name: str):
        parts = re.findall(r"\d+", name)
        return [int(p) for p in parts] if parts else [0]

    for name in sorted(os.listdir(apps), key=_version_key, reverse=True):
        cand = os.path.join(apps, name, "ffmpeg.exe")
        if not os.path.isfile(cand):
            continue
        try:
            _run([cand, "-hide_banner", "-version"], timeout=20)
        except Exception:
            continue
        return cand
    return None


def ffprobe_path() -> Optional[str]:
    explicit = os.environ.get("FREECUT_FFPROBE")
    if explicit and os.path.isfile(explicit):
        return explicit
    return _find_beside_app("ffprobe") or _which("ffprobe")


def have_encoder(name: str) -> bool:
    try:
        out = _run([ffmpeg_path(), "-hide_banner", "-encoders"], timeout=30)
    except Exception:
        return False
    return re.search(rf"\b{re.escape(name)}\b", out) is not None


def list_encoders() -> List[str]:
    try:
        out = _run([ffmpeg_path(), "-hide_banner", "-encoders"], timeout=30)
    except Exception:
        return []
    return re.findall(r"^\s*[VAS][.A-Z]{5}\s+(\S+)", out, re.M)


def _run(cmd: List[str], timeout: int = 60) -> str:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        creationflags=_creationflags(),
    )
    return proc.stdout.decode("utf-8", "replace")


def _creationflags() -> int:
    if sys.platform.startswith("win"):
        return 0x08000000  # CREATE_NO_WINDOW
    return 0


# --------------------------------------------------------------------------- #
# probing
# --------------------------------------------------------------------------- #
@dataclass
class MediaInfo:
    path: str
    duration_us: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    sample_rate: int = 48000
    channels: int = 2
    rotation: int = 0
    bitrate: int = 0

    @property
    def duration_s(self) -> float:
        return self.duration_us / 1_000_000.0


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: (Video|Audio): (.*)")

#: probe 结果缓存。键里带 (大小, mtime)，文件被替换后自动失效。
#: 没有它时 Renderer 每建一次就要对**每个片段**起一个 ffprobe——
#: 38 个片段的工程首帧 8 秒，全部耗在这里（HANDOFF D2 的根因）。
_PROBE_CACHE: "OrderedDict[Tuple[str, int, int], MediaInfo]" = OrderedDict()
_PROBE_CACHE_MAX = 512


def _probe_key(path: str) -> Optional[Tuple[str, int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), int(st.st_size), int(st.st_mtime_ns))


def clear_probe_cache() -> None:
    """测试与「素材被替换」场景用；正常路径不需要手动清。"""
    _PROBE_CACHE.clear()


def probe_cache_info() -> dict:
    """给测试/自检看缓存状态。"""
    return {"entries": len(_PROBE_CACHE), "max": _PROBE_CACHE_MAX}


def probe(path: str) -> MediaInfo:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    key = _probe_key(path)
    if key is not None:
        hit = _PROBE_CACHE.get(key)
        if hit is not None:
            _PROBE_CACHE.move_to_end(key)
            return hit
    info = _probe_uncached(path)
    if key is not None:
        _PROBE_CACHE[key] = info
        while len(_PROBE_CACHE) > _PROBE_CACHE_MAX:
            _PROBE_CACHE.popitem(last=False)
    return info


def _probe_uncached(path: str) -> MediaInfo:
    info = MediaInfo(path=path)
    probe_exe = ffprobe_path()
    if probe_exe:
        try:
            raw = _run(
                [
                    probe_exe,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    path,
                ]
            )
            data = json.loads(raw or "{}")
            fmt = data.get("format") or {}
            info.duration_us = int(float(fmt.get("duration", 0) or 0) * 1e6)
            info.bitrate = int(float(fmt.get("bit_rate", 0) or 0))
            for st in data.get("streams") or []:
                if st.get("codec_type") == "video" and not info.has_video:
                    info.has_video = True
                    info.width = int(st.get("width") or 0)
                    info.height = int(st.get("height") or 0)
                    info.fps = _parse_rate(st.get("r_frame_rate"))
                    info.rotation = _rotation_of(st)
                elif st.get("codec_type") == "audio" and not info.has_audio:
                    info.has_audio = True
                    info.sample_rate = int(st.get("sample_rate") or 48000)
                    info.channels = int(st.get("channels") or 2)
            if info.has_video:
                return info
        except Exception:
            pass

    # Fallback: parse ``ffmpeg -i`` banner (the stripped bundled build has no ffprobe).
    out = _run([ffmpeg_path(), "-hide_banner", "-i", path], timeout=60)
    m = _DUR_RE.search(out)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info.duration_us = int(round((h * 3600 + mi * 60 + s) * 1e6))
    for m in _STREAM_RE.finditer(out):
        kind, rest = m.group(1), m.group(2)
        if kind == "Video" and not info.has_video:
            info.has_video = True
            vm = re.search(r"(\d{2,5})x(\d{2,5})", rest)
            if vm:
                info.width, info.height = int(vm.group(1)), int(vm.group(2))
            fm = re.search(r"([\d.]+) fps", rest)
            if fm:
                info.fps = float(fm.group(1))
            if "90k tbr" in rest:
                pass
        elif kind == "Audio" and not info.has_audio:
            info.has_audio = True
            sm = re.search(r"(\d+) Hz", rest)
            if sm:
                info.sample_rate = int(sm.group(1))
            cm = re.search(r"stereo|mono|(\d+) channels", rest)
            if cm:
                info.channels = 1 if (cm.group(0) == "mono") else (2 if cm.group(0) == "stereo" else int(cm.group(1)))
    if not info.has_video and not info.has_audio:
        raise RuntimeError(f"无法读取媒体文件: {path}")
    return info


def _parse_rate(text: Optional[str]) -> float:
    if not text:
        return 0.0
    if "/" in text:
        try:
            num, den = text.split("/")
            if float(den) == 0.0:
                return 0.0
            return float(Fraction(int(num), int(den)))
        except Exception:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _rotation_of(st: dict) -> int:
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(sd["rotation"])
            except Exception:
                pass
    try:
        return int(st.get("tags", {}).get("rotate", 0))
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# sequential video reader
# --------------------------------------------------------------------------- #
class FrameReader:
    """取帧器门面：**优先用 PyAV 常驻解码器**，没有 av 就退回 ffmpeg 子进程。

    实测（`docs/ROUTE-V2.md` §0）：换一个位置取一帧，子进程要 218–280 ms
    （进程启动 35 ms + 解码器初始化 ~170 ms，与跳多远无关），
    常驻解码器只要 13–17 ms。接口对调用方完全一致。
    """

    def __new__(cls, *args, **kwargs):
        if cls is FrameReader:
            from .av_reader import AvFrameReader, av_available

            # 注意：这里**不做全局池化**。共享解码器由 Renderer 持有并在关闭时释放，
            # 否则我们开着容器会让用户删不掉/改不了素材文件（Windows 文件锁）。
            if av_available():
                try:
                    return AvFrameReader(*args, **kwargs)
                except Exception:                      # noqa: BLE001
                    pass
            return FfmpegFrameReader(*args, **kwargs)
        return super().__new__(cls)


class FfmpegFrameReader:
    """Pull frames from one media file at the timeline's own frame rate.

    FFmpeg decodes a contiguous run and writes raw RGB24 into a pipe.  When the
    caller asks for an earlier instant (or skips far ahead) the process is
    restarted with ``-ss``, which is what JianyingPro's reader pool does
    (``ve_abtest_seekOpt`` / ``ve_enable_reader_refactor`` in ``ve_ab_conf.ini``).
    """

    def __init__(
        self,
        path: str,
        src_start_us: int = 0,
        speed: float = 1.0,
        width: int = 0,
        height: int = 0,
        fps: float = 30.0,
        probe_info: bool = True,
    ) -> None:
        self.path = path
        self.src_start_us = src_start_us
        self.speed = speed if speed > 0 else 1.0
        self.out_w = int(width) or 0
        self.out_h = int(height) or 0
        self.fps = fps
        self._proc: Optional[subprocess.Popen] = None
        self._next_us = 0          # source time of the next frame in the pipe
        self._last: Optional[np.ndarray] = None
        #: 最近解过的帧（帧号 → RGB）。拖动时来回拖命中缓存，不用重启解码器。
        self._cache: "OrderedDict[int, np.ndarray]" = OrderedDict()
        #: 缓存帧数上限（每帧 out_w*out_h*3 字节，528×298 约 0.45 MB）
        self.cache_frames = 90
        self._frame_bytes = 0
        self._lock = threading.Lock()
        #: 调用方已经知道尺寸时（草稿里就写着 width/height）不必再问 ffprobe
        self.info = probe(path) if (probe_info and os.path.isfile(path)) else None
        self.has_video = bool(self.info.has_video) if self.info is not None else True
        if self.info and not self.out_w:
            self.out_w = self.info.width or 1280
            self.out_h = self.info.height or 720
        self._clear = np.zeros((self.out_h or 720, self.out_w or 1280, 3), dtype=np.uint8)

    # -- lifecycle ----------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                self._proc.stdout.close()  # type: ignore[union-attr]
            except Exception:
                pass
            # 必须回收：不 wait 的话 Windows 上 ffmpeg 的句柄可能还开着，
            # 紧接着删除源文件会 PermissionError（测试里表现为临时目录删不掉）
            try:
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None

    def __del__(self) -> None:  # pragma: no cover
        self.close()

    # -- decoding -----------------------------------------------------------
    def _start(self, source_us: int) -> None:
        self._stop()
        if source_us < 0:
            source_us = 0
        ss = source_us / 1_000_000.0
        cmd = [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ss:.6f}",
            "-i",
            self.path,
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"scale={self.out_w}:{self.out_h}:force_original_aspect_ratio=decrease,"
            f"pad={self.out_w}:{self.out_h}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=_creationflags(),
        )
        self._frame_bytes = self.out_w * self.out_h * 3
        self._next_us = int(source_us)

    def _read_frame(self) -> Optional[np.ndarray]:
        assert self._proc is not None and self._proc.stdout is not None
        buf = self._proc.stdout.read(self._frame_bytes)
        if not buf or len(buf) < self._frame_bytes:
            return None
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(self.out_h, self.out_w, 3)
        self._next_us += int(round(1_000_000.0 / self.fps))
        return arr

    #: 顺序读最多跨多少帧（再远就重启解码器）
    READ_AHEAD_FRAMES = 120
    #: 同时按解码字节数兜底，避免 4K 素材把内存/时间吃光
    READ_AHEAD_BYTES = 64 << 20

    def _read_ahead_frames(self) -> int:
        frame_bytes = max(1, (self.out_w or 1) * (self.out_h or 1) * 3)
        by_bytes = max(1, self.READ_AHEAD_BYTES // frame_bytes)
        return int(min(self.READ_AHEAD_FRAMES, by_bytes))

    def frame_at(self, source_us: int) -> np.ndarray:
        """Return the frame covering ``source_us`` as RGB uint8."""
        with self._lock:
            if not self.has_video:
                return self._clear
            step = 1_000_000.0 / self.fps
            # 帧缓存：往回拖时大多数帧就在附近，命中就毫秒级返回，不用重启 ffmpeg。
            # （实测回跳一次 280 ms，见 docs/ROUTE-V2.md §0）
            index = int(round(source_us / step))
            hit = self._cache.get(index)
            if hit is not None:
                self._cache.move_to_end(index)
                self._last = hit
                return hit
            # 跳转策略按"要读多少帧"决定，而不是按秒：顺序读一帧只要 1~2 ms，
            # 重启 ffmpeg 却要几百毫秒。播放丢帧时播放头会一次跳几十帧，
            # 按秒判断会疯狂重启，反而更慢（实测 0.25x 实时的主因）。
            budget = self._read_ahead_frames()
            restart = self._proc is None or source_us < self._next_us - 2 * step
            if not restart:
                ahead = (source_us - self._next_us) / step
                if ahead > budget:
                    restart = True
            if restart:
                self._start(max(0, source_us - int(step)))
                self._last = None
            guard = 0
            while self._next_us <= source_us + step * 0.5 and guard < budget + 12:
                frame = self._read_frame()
                if frame is None:
                    if self._last is not None:
                        return self._last
                    self._last = self._clear
                    return self._last
                self._last = frame
                self._remember(self._next_us - step, frame)
                guard += 1
            if self._last is None:
                frame = self._read_frame()
                self._last = self._clear if frame is None else frame
            self._remember(source_us, self._last)
            if restart:
                # 刚重启过：顺手多解几帧存起来，接下来的前后拖动就能命中缓存
                self._prefetch(min(12, budget), step)
            return self._last

    def _prefetch(self, count: int, step: float) -> None:
        for _ in range(max(0, int(count))):
            frame = self._read_frame()
            if frame is None:
                return
            self._remember(self._next_us - step, frame)

    # -- 帧缓存 ------------------------------------------------------------ #
    def _remember(self, us: float, frame) -> None:
        """按帧号缓存解出来的帧（超过上限丢最旧的）。"""
        if frame is None:
            return
        step = 1_000_000.0 / max(1.0, float(self.fps or 30.0))
        index = int(round(float(us) / step))
        self._cache[index] = frame
        self._cache.move_to_end(index)
        while len(self._cache) > self.cache_frames:
            self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        self._cache.clear()


# --------------------------------------------------------------------------- #
# image loader
# --------------------------------------------------------------------------- #
class ImageSource:
    """Static image decoded once and cached, fitted to a box."""

    def __init__(self, path: str, width: int = 0, height: int = 0) -> None:
        self.path = path
        self.width = width
        self.height = height
        self._cache: Dict[Tuple[int, int], np.ndarray] = {}
        from PIL import Image

        img = Image.open(path).convert("RGBA")
        self.src = np.asarray(img).astype(np.uint8)

    def frame(self, width: int, height: int) -> np.ndarray:
        key = (int(width), int(height))
        if key in self._cache:
            return self._cache[key]
        from PIL import Image

        img = Image.fromarray(self.src, "RGBA")
        img.thumbnail((max(1, width), max(1, height)), Image.LANCZOS)
        canvas = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
        canvas.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
        arr = np.asarray(canvas).astype(np.uint8)
        self._cache[key] = arr
        return arr
