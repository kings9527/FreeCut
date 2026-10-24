"""时间线要用的缩略图与音频波形，带缓存 —— 解码在工作线程，绘制只读缓存。

**为什么改成异步**（HANDOFF D1）：原来 ``drawBackground`` 每次重绘都同步起 ffmpeg
抽帧，一次拖动 20 步实测起了 **190 个 ffmpeg 子进程**（854 ms/次）。
现在绘制的路径上**永不解码**：
- 命中缓存 → 直接 ``drawPixmap``；
- 没命中 → 返回 None（先画纯色块），把任务丢给工作线程，解好后发 ``ready``
  让视图重画一次。

同步接口 ``thumbnail()`` / ``peaks()`` / ``frame()`` 保留给"就要这一帧"的调用方
（源监视器、素材库列表、老测试）；它们解出来的结果同样进缓存。
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage, QPixmap

from ..media import ffmpeg_path

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def _run(args: List[str], limit: int = 8_000_000) -> bytes:
    proc = subprocess.run(args, capture_output=True, timeout=60,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.stdout[:limit]


class MediaCache(QObject):
    """缩略图 + 波形的缓存。一个进程共用一个就够（按 (路径, 时间, 尺寸) 去重）。"""

    #: 解好一张（或一段波形）就发一次；视图收到后 ``update()`` 即可
    ready = Signal()

    _shared: Optional["MediaCache"] = None

    def __init__(self, max_thumbs: int = 1200, max_waves: int = 64, parent=None) -> None:
        super().__init__(parent)
        self.max_thumbs = max_thumbs
        self.max_waves = max_waves
        self._images: "OrderedDict[Tuple, np.ndarray]" = OrderedDict()
        self._pix: "OrderedDict[Tuple, QPixmap]" = OrderedDict()
        self._waves: "OrderedDict[Tuple, np.ndarray]" = OrderedDict()
        self._pending: set = set()
        self._lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue(maxsize=4096)
        self._stop = False
        #: 自检用：真的起了几个解码子进程 / 命中多少次
        self.stats = {"decodes": 0, "hits": 0, "misses": 0}
        self._worker = threading.Thread(target=self._loop, name="fc-media-cache", daemon=True)
        self._worker.start()

    @classmethod
    def shared(cls) -> "MediaCache":
        """全进程一个实例：时间线、素材库、源监视器不各解一遍。"""
        if cls._shared is None:
            cls._shared = MediaCache()
        return cls._shared

    # -- 工作线程 --------------------------------------------------------- #
    def _loop(self) -> None:
        while not self._stop:
            job = self._queue.get()
            if job is None:
                return
            try:
                self._decode(job)
            except Exception:                          # noqa: BLE001
                pass
            finally:
                with self._lock:
                    self._pending.discard(job[0])
                try:
                    self.ready.emit()
                except RuntimeError:                   # 对象已析构
                    return

    def _decode(self, job) -> None:
        kind, key = job[0], job[1]
        if kind == "thumb":
            _, _, path, at_us, width, height = job
            arr = self._decode_thumb(path, at_us, width, height)
            if arr is not None:
                with self._lock:
                    self._images[key] = arr
                    self._images.move_to_end(key)
                    while len(self._images) > self.max_thumbs:
                        self._images.popitem(last=False)
        elif kind == "wave":
            _, _, path, start_us, duration_us, buckets = job
            data = self._decode_audio(path, start_us, duration_us)
            peaks = self._peaks_from(data, buckets)
            if peaks is not None:
                with self._lock:
                    self._waves[key] = peaks
                    self._waves.move_to_end(key)
                    while len(self._waves) > self.max_waves:
                        self._waves.popitem(last=False)

    def _request(self, job) -> None:
        key = job[1]
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            with self._lock:
                self._pending.discard(key)

    def close(self) -> None:
        self._stop = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    # -- 缩略图 ----------------------------------------------------------- #
    @staticmethod
    def _thumb_key(path: str, at_us: int, width: int, height: int) -> Tuple:
        bucket = -1 if path.lower().endswith(IMAGE_EXTS) else int(at_us // 500_000)
        return (path, bucket, int(width), int(height))

    def pixmap(self, path: str, at_us: int, width: int = 96,
               height: int = 54) -> Optional[QPixmap]:
        """绘制路径专用：命中就返回 QPixmap，没命中就排队并返回 None。**从不解码**。"""
        if not path or not os.path.isfile(path) or width <= 0 or height <= 0:
            return None
        key = self._thumb_key(path, at_us, width, height)
        pix = self._pix.get(key)
        if pix is not None:
            self._pix.move_to_end(key)
            self.stats["hits"] += 1
            return pix
        with self._lock:
            arr = self._images.get(key)
            if arr is not None:
                self._images.move_to_end(key)
        if arr is None:
            self.stats["misses"] += 1
            self._request(("thumb", key, path, int(at_us), int(width), int(height)))
            return None
        h, w = arr.shape[:2]
        image = QImage(arr.data, w, h, arr.strides[0], QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(image)
        self._pix[key] = pix
        while len(self._pix) > self.max_thumbs:
            self._pix.popitem(last=False)
        self.stats["hits"] += 1
        return pix

    def thumbnail(self, path: str, at_us: int, width: int = 96,
                  height: int = 54) -> Optional[np.ndarray]:
        """同步版：解出来再返回（源监视器/素材库/老测试用）。"""
        if not path or not os.path.isfile(path):
            return None
        key = self._thumb_key(path, at_us, width, height)
        with self._lock:
            arr = self._images.get(key)
            if arr is not None:
                self._images.move_to_end(key)
        if arr is not None:
            return arr
        arr = self._decode_thumb(path, int(at_us), int(width), int(height))
        if arr is None:
            return None
        with self._lock:
            self._images[key] = arr
            while len(self._images) > self.max_thumbs:
                self._images.popitem(last=False)
        return arr

    def _decode_thumb(self, path: str, at_us: int, width: int, height: int):
        if path.lower().endswith(IMAGE_EXTS):
            return self._image_thumb(path, width, height)
        self.stats["decodes"] += 1
        return self._video_thumb(path, (int(at_us) // 500_000) * 500_000, width, height)

    def _video_thumb(self, path: str, at_us: int, width: int, height: int):
        try:
            ff = ffmpeg_path()
        except Exception:                              # noqa: BLE001
            return None
        args = [
            ff, "-hide_banner", "-loglevel", "error",
            "-ss", f"{at_us / 1e6:.3f}", "-i", path,
            "-frames:v", "1",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                   f"crop={width}:{height}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ]
        try:
            raw = _run(args, width * height * 3)
        except Exception:                              # noqa: BLE001
            return None
        if len(raw) < width * height * 3:
            return None
        return np.frombuffer(raw[:width * height * 3], np.uint8).reshape(height, width, 3)

    def frame(self, path: str, at_us: int, width: int, height: int):
        """任意位置的整帧（源监视器用）。同步，不入 0.5s 桶。"""
        if not path or not os.path.isfile(path):
            return None
        if path.lower().endswith(IMAGE_EXTS):
            return self._image_thumb(path, width, height)
        return self._video_thumb(path, max(0, int(at_us)), width, height)

    def _image_thumb(self, path: str, width: int, height: int):
        key = self._thumb_key(path, 0, width, height)
        with self._lock:
            arr = self._images.get(key)
        if arr is not None:
            return arr
        try:
            from PIL import Image

            img = Image.open(path).convert("RGB")
            img = img.resize((width, height), Image.BILINEAR)
            arr = np.asarray(img, np.uint8)
        except Exception:                              # noqa: BLE001
            arr = None
        if arr is not None:
            with self._lock:
                self._images[key] = arr
                while len(self._images) > self.max_thumbs:
                    self._images.popitem(last=False)
        return arr

    # -- 波形 ------------------------------------------------------------- #
    @staticmethod
    def _wave_key(path: str, start_us: int, duration_us: int, buckets: int) -> Tuple:
        return (path, int(start_us // 100_000), int(duration_us // 100_000), int(buckets))

    def waveform(self, path: str, start_us: int, duration_us: int,
                 buckets: int = 240) -> Optional[np.ndarray]:
        """绘制路径专用：命中返回峰值数组，没命中排队并返回 None。"""
        if not path or not os.path.isfile(path) or duration_us <= 0 or buckets <= 0:
            return None
        key = self._wave_key(path, start_us, duration_us, buckets)
        with self._lock:
            peaks = self._waves.get(key)
            if peaks is not None:
                self._waves.move_to_end(key)
                return peaks
        self._request(("wave", key, path, int(start_us), int(duration_us), int(buckets)))
        return None

    def peaks(self, path: str, start_us: int, duration_us: int,
              buckets: int = 240) -> Optional[np.ndarray]:
        """同步版：返回 0..1 的峰值数组（长度 ``buckets``）。"""
        if not path or not os.path.isfile(path) or duration_us <= 0 or buckets <= 0:
            return None
        key = self._wave_key(path, start_us, duration_us, buckets)
        with self._lock:
            peaks = self._waves.get(key)
            if peaks is not None:
                self._waves.move_to_end(key)
                return peaks
        data = self._decode_audio(path, start_us, duration_us)
        peaks = self._peaks_from(data, buckets)
        if peaks is None:
            return None
        with self._lock:
            self._waves[key] = peaks
            while len(self._waves) > self.max_waves:
                self._waves.popitem(last=False)
        return peaks

    @staticmethod
    def _peaks_from(data, buckets: int) -> Optional[np.ndarray]:
        if data is None or data.size == 0 or buckets <= 0:
            return None
        edges = np.linspace(0, data.size, buckets + 1).astype(np.int64)
        out = np.zeros(buckets, np.float32)
        mag = np.abs(data)
        for i in range(buckets):
            a, b = edges[i], edges[i + 1]
            if b > a:
                out[i] = float(mag[a:b].max())
        peak = float(out.max()) or 1.0
        return out / peak

    def _decode_audio(self, path: str, start_us: int, duration_us: int):
        try:
            ff = ffmpeg_path()
        except Exception:                              # noqa: BLE001
            return None
        self.stats["decodes"] += 1
        rate = 4000
        args = [
            ff, "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_us / 1e6:.3f}", "-t", f"{duration_us / 1e6:.3f}",
            "-i", path, "-vn", "-ac", "1", "-ar", str(rate),
            "-f", "s16le", "-",
        ]
        try:
            raw = _run(args, rate * 2 * 600)           # 最多 10 分钟
        except Exception:                              # noqa: BLE001
            return None
        if not raw:
            return None
        return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
