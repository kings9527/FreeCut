"""PyAV 版的取帧器：一个素材只开一次解码器，seek 后取一帧。

为什么要有它（实测，见 `docs/measurements/PLAYBACK-AUDIO-RE.md` §3 与
`docs/ROUTE-V2.md` §0）：

| 方式 | 换一个位置取一帧 |
|---|---|
| ffmpeg 子进程（`-ss` 快进 + 重启） | **218–280 ms**（其中进程启动 35 ms、解码器初始化 ~170 ms） |
| PyAV 常驻解码器（`container.seek` + 解一帧） | **13–17 ms** |

初始化成本与"跳多远"无关（0 秒和 3 秒一样贵），所以关键是**别反复初始化**——
这正是剪映 `ve_abtest_seekOpt` / reader 池在做的事。

对外接口与 `media.FrameReader` 相同：`frame_at(source_us) -> (h, w, 3) uint8`。
"""

from __future__ import annotations

import functools
from typing import Optional

import numpy as np


@functools.lru_cache(maxsize=1)
def av_available() -> bool:
    try:
        import av  # noqa: F401

        return True
    except Exception:                                  # noqa: BLE001
        return False


class AvFrameReader:
    """常驻解码器：seek 后解到目标帧，按目标尺寸等比缩放并补黑边。"""

    def __init__(self, path: str, src_start_us: int = 0, speed: float = 1.0,
                 width: int = 0, height: int = 0, fps: float = 30.0,
                 probe_info: bool = True) -> None:
        import av

        self.path = path
        self.src_start_us = int(src_start_us)
        self.speed = speed if speed > 0 else 1.0
        self.out_w = int(width) or 0
        self.out_h = int(height) or 0
        self.fps = float(fps or 30.0)
        self.cache_frames = 90

        self._container = av.open(path)
        self._stream = self._container.streams.video[0]
        try:
            self._stream.thread_type = "AUTO"
        except Exception:                              # noqa: BLE001
            pass
        self._time_base = self._stream.time_base
        if not self.out_w or not self.out_h:
            self.out_w = int(self._stream.codec_context.width or 0) or 2
            self.out_h = int(self._stream.codec_context.height or 0) or 2
        self._fit = self._fit_box()
        self.has_video = True
        self._last = None
        self._last_index = -1
        self._gen = None            # 当前解到哪的生成器（顺序前进不重新 seek）
        self._pos_us = -1
        self._clear = np.zeros((self.out_h, self.out_w, 3), dtype=np.uint8)
        #: 往前 2 秒内不 seek，直接顺序解（GOP 内 seek 要重解一整段，很贵）
        self.forward_window_us = 2_000_000

    # -- 尺寸 -------------------------------------------------------------- #
    def _fit_box(self):
        """等比缩放到 out_w/out_h 之内，返回 (w, h, x, y)。"""
        sw = int(self._stream.codec_context.width or self.out_w) or self.out_w
        sh = int(self._stream.codec_context.height or self.out_h) or self.out_h
        scale = min(self.out_w / float(sw), self.out_h / float(sh))
        w = max(2, int(round(sw * scale)))
        h = max(2, int(round(sh * scale)))
        return w, h, (self.out_w - w) // 2, (self.out_h - h) // 2

    # -- 取帧 -------------------------------------------------------------- #
    def frame_at(self, source_us: int) -> np.ndarray:
        index = int(round(source_us / (1_000_000.0 / max(1.0, self.fps))))
        if index == self._last_index and self._last is not None:
            return self._last
        target = max(0, int(source_us))
        try:
            ahead = target - self._pos_us
            sequential = (self._gen is not None and 0 <= ahead <= self.forward_window_us)
            if not sequential:
                # PyAV 的 seek 偏移量单位是**给定流的时间基**（不是微秒）
                ticks = int(round(target / 1_000_000.0 / float(self._time_base)))
                self._container.seek(ticks, stream=self._stream, backward=True)
                self._gen = self._container.decode(self._stream)
            frame = self._decode_until(target)
            if frame is None:
                return self._last if self._last is not None else self._clear
            out = self._to_rgb(frame)
        except Exception:                              # noqa: BLE001
            return self._last if self._last is not None else self._clear
        self._last = out
        self._last_index = index
        return out

    def _decode_until(self, target_us: int):
        """从当前位置往后解，直到时间 >= target（最多 8 秒，防止异常文件卡死）。"""
        if self._gen is None:
            return None
        for frame in self._gen:
            when = int((frame.time or 0) * 1_000_000)
            self._pos_us = when
            if when >= target_us:
                return frame
            if when < target_us - 8_000_000:            # 时间轴异常，别无限读
                break
        return None

    def _to_rgb(self, frame) -> np.ndarray:
        from PIL import Image

        arr = frame.to_ndarray(format="rgb24")
        w, h, x, y = self._fit
        if (h, w) != arr.shape[:2]:
            arr = np.asarray(Image.fromarray(arr).resize((w, h), Image.BILINEAR))
        if (w, h) == (self.out_w, self.out_h):
            return np.ascontiguousarray(arr)
        canvas = np.zeros((self.out_h, self.out_w, 3), dtype=np.uint8)
        canvas[y:y + h, x:x + w] = arr
        return canvas

    # -- 生命周期 ---------------------------------------------------------- #
    def close(self) -> None:
        """共享实例不在这里关（池子负责）；独立实例才真关。"""
        if getattr(self, "_shared", False):
            return
        self.close_real()

    def close_real(self) -> None:
        try:
            if self._container is not None:
                self._container.close()
        except Exception:                              # noqa: BLE001
            pass
        self._container = None

    def clear_cache(self) -> None:
        self._last = None
        self._last_index = -1

    @property
    def out_size(self):
        return self.out_w, self.out_h


#: 同一个文件 + 同一尺寸只开一个解码器：38 个片段各开一个容器会卡住首帧
#: （实测一次 1.5 s 的停顿就是这么多容器同时打开造成的）。
_POOL: dict = {}


def shared_reader(path: str, width: int, height: int, fps: float = 30.0):
    """取（或新建）共享的 AvFrameReader。"""
    import os

    key = (os.path.normcase(os.path.abspath(str(path))),
           int(width), int(height), round(float(fps or 30.0), 3))
    reader = _POOL.get(key)
    if reader is None or reader._container is None:
        reader = AvFrameReader(path, width=width, height=height, fps=fps,
                               probe_info=False)
        reader._shared = True
        _POOL[key] = reader
    return reader


def close_pool() -> None:
    for reader in list(_POOL.values()):
        reader.close_real()
    _POOL.clear()
