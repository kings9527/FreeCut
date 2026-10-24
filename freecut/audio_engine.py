"""预览音频引擎：混音 + 输出。

结构照着剪映的逆向结果（`docs/measurements/PLAYBACK-AUDIO-RE.md` §1）：

    AudioBackend            ← 抽象：只有 start/stop/write，换后端不改混音层
      └─ SoundDeviceBackend ← portaudio（Windows 上就是 WASAPI）
      └─ NullBackend        ← 测试用：把 PCM 收进内存，不出声

    Mixer                   ← 每个输出块：把该时间段内的片段解码成 PCM 再叠加
    AudioEngine             ← 跟播放头走：start/stop/seek，块回调里取混音结果

和剪映一致的几个点：
- **输出采样率取自设备**（剪映 `IsSampleRateSupported`），不是写死 48 kHz；
- 混音在**音频线程**里做，UI 线程只在播放/暂停/seek 时碰状态；
- 拿不到工程锁时输出静音（宁可静音也不阻塞音频线程）。
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections import OrderedDict
from typing import Callable, Optional

import numpy as np

from .media import ffmpeg_path

#: 混音块大小（帧）。1024 @48k ≈ 21 ms，和剪映的"设备周期"同一量级。
BLOCK_FRAMES = 1024
#: 单个片段解码缓存上限（秒）
MAX_CLIP_SECONDS = 120


def _decode_pcm(path: str, start_us: int, duration_us: int,
                samplerate: int, channels: int = 2) -> Optional[np.ndarray]:
    """用 ffmpeg 解一段 PCM（int16 交错）。失败返回 None。"""
    if not path or not os.path.isfile(path):
        return None
    duration_us = max(1, min(int(duration_us), MAX_CLIP_SECONDS * 1_000_000))
    cmd = [
        ffmpeg_path(), "-v", "error", "-nostdin",
        "-ss", f"{max(0, int(start_us)) / 1e6:.6f}",
        "-t", f"{duration_us / 1e6:.6f}",
        "-i", path,
        "-vn", "-f", "s16le", "-ac", str(int(channels)),
        "-ar", str(int(samplerate)), "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
    except Exception:                                  # noqa: BLE001
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    data = np.frombuffer(proc.stdout, dtype="<i2")
    if channels > 1:
        data = data[: len(data) // channels * channels].reshape(-1, channels)
    return data


class Mixer:
    """把工程在某一段时间里的声音混成 PCM。

    只做预览需要的部分：音量、淡入淡出、静音/独奏、恒速与曲线变速、轨道叠加。
    降噪/变声这类滤镜暂不参与预览（导出仍然走完整链），这一点写在 `docs/ROUTE-V2.md`。
    """

    def __init__(self, project, samplerate: int, channels: int = 2):
        self.project = project
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self._cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()

    # -- 解码缓存 ---------------------------------------------------------- #
    def _clip_pcm(self, path: str, start_us: int, duration_us: int) -> Optional[np.ndarray]:
        key = (path, int(start_us), int(duration_us), self.samplerate)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        pcm = _decode_pcm(path, start_us, duration_us, self.samplerate, self.channels)
        if pcm is None:
            return None
        self._cache[key] = pcm
        while len(self._cache) > 8:                     # 缓存 8 段就够预览来回拖
            self._cache.popitem(last=False)
        return pcm

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- 混音 -------------------------------------------------------------- #
    def mix(self, start_us: int, frames: int) -> np.ndarray:
        out = np.zeros((int(frames), self.channels), dtype=np.float32)
        if frames <= 0:
            return out.astype(np.int16)
        end_us = int(start_us) + int(round(frames * 1e6 / self.samplerate))
        any_audio = False
        for track in self.project.tracks:
            if track.type != "audio":
                continue
            if self._track_silent(track):
                continue
            for seg in track.segments:
                start = int(seg.target_timerange.start)
                stop = start + int(seg.target_timerange.duration)
                if stop <= start_us or start >= end_us:
                    continue
                mixed = self._mix_segment(seg, start_us, frames)
                if mixed is not None:
                    out += mixed
                    any_audio = True
        if not any_audio:
            return np.zeros((int(frames), self.channels), dtype=np.int16)
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16)

    def _track_silent(self, track) -> bool:
        """静音/独奏：只在桥接层有状态时生效（Mixer 也能独立用）。"""
        state = getattr(self, "track_state", None)
        if not state:
            return False
        try:
            return bool(state(track))
        except Exception:                              # noqa: BLE001
            return False

    def _mix_segment(self, seg, start_us: int, frames: int) -> Optional[np.ndarray]:
        mat = self.project.material(seg.material_id)
        path = getattr(mat, "path", None) or seg.__dict__.get("path") or ""
        if not path:
            return None
        src_start = int(seg.source_timerange.start)
        src_dur = int(seg.source_timerange.duration)
        pcm = self._clip_pcm(path, src_start, src_dur)
        if pcm is None or len(pcm) == 0:
            return None

        # 时间轴上的这一块 → 片段内的偏移（微秒）
        offsets = start_us + np.arange(frames, dtype=np.float64) * 1e6 / self.samplerate
        seg_start = int(seg.target_timerange.start)
        seg_dur = max(1, int(seg.target_timerange.duration))
        rel = offsets - seg_start
        inside = (rel >= 0) & (rel < seg_dur)
        if not inside.any():
            return None

        # 时间轴偏移 → 源内偏移（恒速线性，曲线变速走查表）
        speed = float(getattr(seg, "speed", 1.0) or 1.0)
        curve = list(getattr(seg, "speed_curve", []) or [])
        if curve:
            from .speed import cumulative

            table = np.asarray(cumulative(curve), dtype=np.float64)
            xs = np.linspace(0.0, 1.0, len(table))
            frac = np.interp(rel / seg_dur, xs, table)
            src_index = frac * len(pcm)
        else:
            src_index = (rel * speed) / 1e6 * self.samplerate

        idx = np.clip(src_index.astype(np.int64), 0, len(pcm) - 1)
        block = pcm[idx].astype(np.float32) / 32768.0

        gain = np.ones(frames, dtype=np.float32)
        gain *= float(getattr(seg, "volume", 1.0) or 0.0)
        gain *= self._fade_gain(seg, rel, seg_dur)
        gain[~inside] = 0.0
        return block * gain[:, None]

    def _fade_gain(self, seg, rel, seg_dur) -> np.ndarray:
        fade_in = fade_out = 0
        for ref in self.project.refs_of(seg):
            if getattr(ref, "type", "") == "audio_fade":
                fade_in = int(ref.get("fade_in_duration", 0) or 0)
                fade_out = int(ref.get("fade_out_duration", 0) or 0)
        gain = np.ones(len(rel), dtype=np.float32)
        if fade_in > 0:
            gain *= np.clip(rel / float(fade_in), 0.0, 1.0).astype(np.float32)
        if fade_out > 0:
            tail = (seg_dur - rel) / float(fade_out)
            gain *= np.clip(tail, 0.0, 1.0).astype(np.float32)
        return gain


class NullBackend:
    """测试/无声卡环境：把每次回调的 PCM 存进内存。"""

    def __init__(self):
        self.blocks = []
        self.callback = None
        self.samplerate = 48000
        self.channels = 2
        self.started = False

    def start(self, samplerate: int, channels: int, callback) -> None:
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self.callback = callback
        self.started = True

    def stop(self) -> None:
        self.started = False

    def pump(self, frames: int = BLOCK_FRAMES) -> None:
        """手动跑一块（测试用）。"""
        if self.callback is not None:
            self.blocks.append(self.callback(frames))


class SoundDeviceBackend:
    """portaudio（Windows 上后端就是 WASAPI）。"""

    def __init__(self):
        self._stream = None

    def default_samplerate(self) -> int:
        try:
            import sounddevice as sd

            info = sd.query_devices(kind="output")
            return int(info.get("default_samplerate") or 48000)
        except Exception:                              # noqa: BLE001
            return 48000

    def start(self, samplerate: int, channels: int, callback) -> None:
        import sounddevice as sd

        def wrapped(*args):                            # noqa: ANN002
            # 只有输出通道时 portaudio 回调是 (outdata, frames, time, status)；
            # 有输入通道时会多一个 indata。按"数组 + 整数帧数"来识别，别写死位置。
            outdata = next((a for a in args if hasattr(a, "fill")), None)
            frames = next((a for a in args if isinstance(a, int)), BLOCK_FRAMES)
            if outdata is None:
                return
            try:
                outdata[:] = callback(int(frames))
            except Exception:                          # noqa: BLE001
                outdata.fill(0)

        self._stream = sd.OutputStream(
            samplerate=int(samplerate), channels=int(channels), dtype="int16",
            blocksize=BLOCK_FRAMES, callback=wrapped, latency="low")
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None


class AudioEngine:
    """跟播放头走的预览音频：`start/stop/seek` + 块回调。"""

    def __init__(self, bridge, backend=None):
        self.bridge = bridge
        self.backend = backend
        self._lock = threading.Lock()
        self._position_us = 0
        self._playing = False
        self.samplerate = 48000
        self.channels = 2
        self.mixer: Optional[Mixer] = None
        self.last_error = ""
        #: 设备是否已经打开（打开后暂停只是不出声，不再反复开关设备）
        self._backend_open = False
        #: 保持设备打开：暂停时不关流，避免每次播放都等设备初始化
        self._keep_open = True

    # -- 生命周期 ---------------------------------------------------------- #
    def available(self) -> bool:
        if isinstance(self.backend, NullBackend):
            return True
        try:
            import sounddevice  # noqa: F401

            return True
        except Exception:                              # noqa: BLE001
            return False

    def prepare(self) -> bool:
        """先把音频设备打开（回调输出静音），之后 start() 只是翻开关。

        实测：在 WASAPI 上打开一条输出流要 ~1 秒；如果放在 toggle_play 里同步做，
        点播放会卡住界面一秒多（bench 里 maxstall=1.4s 就是它）。
        """
        with self._lock:
            if self._playing:
                return True
            return self._open_backend_locked()

    def _open_backend_locked(self) -> bool:
        if self.backend is None:
            # 测试/无头环境可以用 FREECUT_NO_AUDIO=1 强制不出声（避免打开设备）
            if os.environ.get("FREECUT_NO_AUDIO"):
                self.backend = NullBackend()
            else:
                self.backend = (SoundDeviceBackend() if self.available()
                                else NullBackend())
        if getattr(self, "_backend_open", False):
            return True
        try:
            self.samplerate = int(
                getattr(self.backend, "default_samplerate", lambda: 48000)())
        except Exception:                              # noqa: BLE001
            self.samplerate = 48000
        if self.samplerate <= 0:
            self.samplerate = 48000
        self.mixer = Mixer(self.bridge.project, self.samplerate, self.channels)
        self.mixer.track_state = self._track_silent
        try:
            self.backend.start(self.samplerate, self.channels, self._callback)
        except Exception as exc:                       # noqa: BLE001
            self.last_error = str(exc)
            return False
        self._backend_open = True
        return True

    def start(self, position_us: int = 0) -> bool:
        with self._lock:
            self._position_us = int(position_us)
            if self._playing:
                return True
            if not self._open_backend_locked():
                return False
            self._playing = True
            return True

    def stop(self) -> None:
        with self._lock:
            self._playing = False
        if self.backend is not None and not getattr(self, "_keep_open", True):
            try:
                self.backend.stop()
                self._backend_open = False
            except Exception:                          # noqa: BLE001
                pass

    def seek(self, position_us: int) -> None:
        with self._lock:
            self._position_us = int(position_us)

    def shutdown(self) -> None:
        """真正关掉设备（退出时用；平时暂停只是不出声）。"""
        with self._lock:
            self._playing = False
        if self.backend is not None:
            try:
                self.backend.stop()
            except Exception:                          # noqa: BLE001
                pass
        self._backend_open = False

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def position_us(self) -> int:
        return self._position_us

    # -- 回调 -------------------------------------------------------------- #
    def _track_silent(self, track) -> bool:
        """静音/独奏状态由桥接层提供（没有就都出声）。"""
        try:
            if self.bridge.is_muted(track):
                return True
            if any(self.bridge.is_solo(t) for t in self.bridge.project.tracks):
                return not self.bridge.is_solo(track)
        except Exception:                              # noqa: BLE001
            return False
        return False

    def _callback(self, frames: int) -> np.ndarray:
        out = np.zeros((int(frames), self.channels), dtype=np.int16)
        if not self._playing or self.mixer is None:
            return out
        start = self._position_us
        try:
            out = self.mixer.mix(start, frames)
        except Exception as exc:                       # noqa: BLE001
            self.last_error = str(exc)
            return np.zeros((int(frames), self.channels), dtype=np.int16)
        self._position_us = start + int(round(frames * 1e6 / self.samplerate))
        return out
