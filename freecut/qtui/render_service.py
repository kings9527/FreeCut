"""预览渲染的线程边界：渲染在工作线程，界面线程只收 QImage。

HANDOFF D2 的根因是「全部在 UI 线程同步渲染」：一帧 80–120 ms，播放时界面跟着卡。
这里把 ``Renderer`` 整个搬到工作线程，并且只保留**最新**一个请求：

    界面线程   request(w,h,t) ──▶ 只留最新的一个
    工作线程   渲染 ──▶ frameReady(token, QImage)
    界面线程   token 还是最新的就显示，否则丢弃（迟到的帧不覆盖新帧）

``Renderer`` 的构造与释放都在工作线程里做；工程侧的读写用 ``bridge.render_lock``
互斥（GUI 线程改工程时短暂阻塞一帧，换来的是预览不撕裂、不崩）。
"""

from __future__ import annotations

import atexit
import threading
import weakref
from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QImage

#: 活着的渲染线程：进程退出时统一收掉，否则 Qt 会警告
#: "QThread: Destroyed while thread is still running"
_SERVICES: "weakref.WeakSet" = weakref.WeakSet()


def _shutdown_all() -> None:
    for service in list(_SERVICES):
        try:
            service.shutdown()
        except Exception:                              # noqa: BLE001
            pass


atexit.register(_shutdown_all)


class _RenderWorker(QObject):
    done = Signal(int, object)          # token, QImage | None

    def __init__(self, bridge) -> None:
        super().__init__()
        self.bridge = bridge
        self.renderer = None
        self.size = (0, 0)
        self.epoch = -1
        self.alive = True

    @Slot(int, int, int, int)
    def render(self, token: int, width: int, height: int, playhead_us: int) -> None:
        if not self.alive:
            return
        image = None
        try:
            with self.bridge.render_lock:
                image = self._render_locked(token, width, height, playhead_us)
        except Exception:                              # noqa: BLE001
            image = None
        try:
            self.done.emit(token, image)
        except RuntimeError:                           # 窗口已经销毁
            pass

    def _render_locked(self, token, width, height, playhead_us) -> Optional[QImage]:
        from ..render import Renderer

        bridge = self.bridge
        if (self.renderer is None or self.size != (width, height)
                or self.epoch != bridge.epoch):
            self._drop()
            try:
                self.renderer = Renderer(bridge.project, width, height, bridge.project.fps)
            except Exception:                          # noqa: BLE001
                self.renderer = None
                return None
            self.size = (width, height)
            self.epoch = bridge.epoch
        if self.renderer is None:
            return None
        fps = float(bridge.project.fps or 30)
        index = int(round(playhead_us / 1e6 * fps))
        index = max(0, min(index, self.renderer.frame_count - 1))
        frame = self.renderer.render_frame(index)
        if frame is None:
            return None
        h, w = frame.shape[:2]
        return QImage(frame.data, w, h, frame.strides[0], QImage.Format_RGB888).copy()

    def _drop(self) -> None:
        if self.renderer is not None:
            try:
                self.renderer.close()
            except Exception:                          # noqa: BLE001
                pass
        self.renderer = None

    @Slot()
    def shutdown(self) -> None:
        self.alive = False
        self._drop()


class RenderService(QObject):
    """一次只有一个在飞、永远只渲染最新请求的预览渲染器。"""

    frameReady = Signal(int, object)     # token, QImage
    _renderRequested = Signal(int, int, int, int)

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._token = 0
        self._inflight = False
        self._pending = None
        self._closed = False
        self.stats = {"requested": 0, "rendered": 0, "dropped": 0}
        self._thread = QThread()
        self._thread.setObjectName("fc-preview")
        self._worker = _RenderWorker(bridge)
        self._worker.moveToThread(self._thread)
        self._worker.done.connect(self._on_done, Qt.QueuedConnection)
        self._renderRequested.connect(self._worker.render, Qt.QueuedConnection)
        self._thread.start()
        _SERVICES.add(self)

    # -- 生命周期（对象被回收也要收线程）----------------------------------- #
    def __del__(self):                                 # pragma: no cover
        try:
            self.shutdown()
        except Exception:                              # noqa: BLE001
            pass

    # -- 请求 ------------------------------------------------------------- #
    def request(self, width: int, height: int, playhead_us: int) -> int:
        if self._closed:
            return -1
        self._token += 1
        self._pending = (self._token, int(width), int(height), int(playhead_us))
        self.stats["requested"] += 1
        self._dispatch()
        return self._token

    def _dispatch(self) -> None:
        if self._inflight or self._pending is None or self._closed:
            return
        token, width, height, us = self._pending
        self._pending = None
        self._inflight = True
        self._renderRequested.emit(token, width, height, us)

    def _on_done(self, token: int, image) -> None:
        self._inflight = False
        if image is not None:
            self.stats["rendered"] += 1
        else:
            self.stats["dropped"] += 1
        # **所有**画完的帧都往上送：播放时下一个请求几乎总是已经在路上，
        # 如果只送"最新 token"的那一帧，就一帧也送不出去（实测 0 帧）。
        # 新旧由界面侧按 token 单调判断（Viewer._on_frame）。
        if image is not None:
            self.frameReady.emit(token, image)
        self._dispatch()

    # -- 生命周期 ---------------------------------------------------------- #
    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        # 等工作线程把手上这一帧画完（它渲染时持有同一把锁），
        # 再从**界面线程**改它的状态，避免渲染到一半把解码器关掉
        with self.bridge.render_lock:
            try:
                self._worker.shutdown()
            except Exception:                          # noqa: BLE001
                pass
        self._thread.quit()
        self._thread.wait(3000)


class RenderLock:
    """给 bridge 用的可重入锁 + epoch（改工程 → epoch+1 → 工作线程重建渲染器）。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.epoch = 0
