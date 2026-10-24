"""界面性能基线：首帧 / 拖动 / 缩略图绘制。

HANDOFF D1/D2 的数字（首帧 9 508 ms、20 次拖动 17 725 ms）就是这套测法来的。
优化前后各跑一次，输出可直接对照。

用法::

    python tools\bench_gui.py                     # 默认 out\\showcase.json
    python tools\\bench_gui.py --project a.json --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def count_ffmpeg_calls():
    """数一次拖动里起了多少个 ffmpeg 子进程（0 才是对的）。

    返回 ``(counter, restore)``：``counter["n"]`` 是次数，用完必须 ``restore()``。
    """
    import subprocess

    counter = {"n": 0}
    real = {"run": subprocess.run, "popen": subprocess.Popen}

    def run(*a, **kw):
        counter["n"] += 1
        return real["run"](*a, **kw)

    def popen(*a, **kw):
        counter["n"] += 1
        return real["popen"](*a, **kw)

    subprocess.run = run
    subprocess.Popen = popen

    def restore():
        subprocess.run = real["run"]
        subprocess.Popen = real["popen"]

    return counter, restore


def bench(project: str, runs: int = 3, width: int = 846, height: int = 476) -> dict:
    app = _app()
    from freecut.qtui.app import MainWindow

    report = {"project": project, "size": [width, height]}

    # -- 首帧：用户看到的第一张画面（异步渲染线程那条路）------------------- #
    from PySide6.QtCore import QEventLoop, QTimer

    ui_first = []
    for _ in range(runs):
        window = MainWindow(project)
        window.resize(1460, 880)
        window.show()
        got = {"at": None}
        started = time.perf_counter()

        def _first(_token, image, got=got, started=started):
            if image is not None and got["at"] is None:
                got["at"] = (time.perf_counter() - started) * 1000.0

        window.bridge.render_service.frameReady.connect(_first)
        window.viewer.schedule()
        loop = QEventLoop()
        QTimer.singleShot(4000, loop.quit)

        def _check():
            if got["at"] is not None:
                loop.quit()
            else:
                QTimer.singleShot(20, _check)

        QTimer.singleShot(20, _check)
        loop.exec()
        if got["at"] is not None:
            ui_first.append(got["at"])
        window.close()
        app.processEvents()
    report["first_frame_ui_ms"] = round(min(ui_first), 1) if ui_first else None

    # -- 首帧（同步接口：导出/截图走这条）--------------------------------- #
    first = []
    for _ in range(runs):
        window = MainWindow(project)
        window.resize(1460, 880)
        window.show()
        app.processEvents()
        t0 = time.perf_counter()
        image = window.bridge.frame_image(width, height)
        first.append((time.perf_counter() - t0) * 1000.0)
        assert image is not None, "首帧渲染失败"
        window.close()
        app.processEvents()
    report["first_frame_ms"] = round(min(first), 1)

    # -- 稳态每帧 ---------------------------------------------------------- #
    window = MainWindow(project)
    window.resize(1460, 880)
    window.show()
    app.processEvents()
    window.bridge.frame_image(width, height)
    steady = []
    for i in range(6):
        window.bridge.set_playhead(300_000 + i * 66_667)
        t0 = time.perf_counter()
        window.bridge.frame_image(width, height)
        steady.append((time.perf_counter() - t0) * 1000.0)
    report["steady_frame_ms"] = round(sum(steady) / len(steady), 1)

    # -- 时间线一次重绘 ---------------------------------------------------- #
    timeline = window.timeline
    timeline.refresh()
    app.processEvents()
    paints = []
    for _ in range(3):
        timeline.viewport().repaint()
        t0 = time.perf_counter()
        timeline.viewport().repaint()
        paints.append((time.perf_counter() - t0) * 1000.0)
    report["timeline_repaint_ms"] = round(sum(paints) / len(paints), 1)

    # -- 模拟拖动 20 次 ---------------------------------------------------- #
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    track = next((t for t in timeline.rows() if t.type == "video" and len(t.segments) > 1), None)
    if track is None:
        window.close()
        report["drag20_ms"] = None
        return report
    seg = track.segments[0]
    y = timeline._track_y(timeline.rows().index(track)) + 20
    x0 = timeline._us_to_x(int(seg.target_timerange.start)) + 40

    def mouse(kind, x, y):
        pos = QPoint(int(x), int(y))
        button = Qt.LeftButton if kind != QMouseEvent.MouseMove else Qt.NoButton
        event = QMouseEvent(kind, pos, button, Qt.LeftButton if kind != QMouseEvent.MouseMove
                            else Qt.NoButton, Qt.NoModifier)
        if kind == QMouseEvent.MouseButtonPress:
            timeline.mousePressEvent(event)
        elif kind == QMouseEvent.MouseMove:
            timeline.mouseMoveEvent(event)
        else:
            timeline.mouseReleaseEvent(event)

    counter, restore = count_ffmpeg_calls()
    try:
        mouse(QMouseEvent.MouseButtonPress, x0, y)
        t0 = time.perf_counter()
        for step in range(20):
            mouse(QMouseEvent.MouseMove, x0 + step * 3, y)
            timeline.viewport().repaint()
        total = (time.perf_counter() - t0) * 1000.0
        mouse(QMouseEvent.MouseButtonRelease, x0 + 60, y)
    finally:
        restore()
    report["drag20_ms"] = round(total, 1)
    report["drag_each_ms"] = round(total / 20.0, 1)
    report["ffmpeg_spawns_during_drag"] = counter["n"]

    # -- 播放 2 秒：帧数 + 界面最大停顿 ------------------------------------ #
    window.close()
    app.processEvents()
    window = MainWindow(project)
    window.resize(1460, 880)
    window.show()
    app.processEvents()
    service = window.bridge.render_service
    rendered = {"n": 0}

    def _count(_token, image):
        if image is not None:
            rendered["n"] += 1

    service.frameReady.connect(_count)
    # 用真的 Qt 事件循环跑 2 秒：忙等 processEvents 会抢 GIL，
    # 测出来的帧率比实际低一个量级（真实界面是闲等事件）
    from PySide6.QtCore import QEventLoop, QTimer

    stalls = []
    stamps = []
    last = [time.perf_counter()]

    def _probe():
        now = time.perf_counter()
        stalls.append((now - last[0]) * 1000.0)
        stamps.append(now)
        last[0] = now

    probe_timer = QTimer(window)
    probe_timer.setInterval(10)
    probe_timer.timeout.connect(_probe)
    probe_timer.start()

    loop = QEventLoop(window)
    window.toggle_play()
    t_start = time.perf_counter()
    QTimer.singleShot(2000, loop.quit)
    loop.exec()
    elapsed = time.perf_counter() - t_start
    window.toggle_play()
    probe_timer.stop()
    report["playback_seconds"] = round(elapsed, 2)
    report["playback_frames"] = rendered["n"]
    report["playback_fps"] = round(rendered["n"] / max(elapsed, 1e-6), 1)
    report["playback_max_stall_ms"] = round(max(stalls) if stalls else 0.0, 1)
    # 头 500 ms 要付"第一个解码器打开 + 首帧"的钱（PyAV 开容器 150–600 ms），
    # 单独报一份"稳定期"的停顿，避免把冷启动算成播放卡顿。
    warm_from = t_start + 0.5
    warm = [value for value, stamp in zip(stalls, stamps) if stamp >= warm_from]
    report["playback_max_stall_steady_ms"] = round(max(warm) if warm else 0.0, 1)
    report["playback_requests"] = service.stats["requested"]
    window.close()
    app.processEvents()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="界面性能基线")
    parser.add_argument("--project", default=os.path.join("out", "showcase.json"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.project):
        print(f"找不到工程 {args.project}")
        return 2
    report = bench(args.project, args.runs)
    line = (f"first_frame_ui={report.get('first_frame_ui_ms')}ms  "
            f"first_frame_sync={report.get('first_frame_ms')}ms  "
            f"steady={report.get('steady_frame_ms')}ms  "
            f"repaint={report.get('timeline_repaint_ms')}ms  "
            f"drag20={report.get('drag20_ms')}ms "
            f"({report.get('drag_each_ms')}ms/次)  "
            f"spawns={report.get('ffmpeg_spawns_during_drag')}  "
            f"play={report.get('playback_fps')}fps/"
            f"{report.get('playback_frames')}帧  "
            f"maxstall={report.get('playback_max_stall_ms')}ms "
            f"steady_stall={report.get('playback_max_stall_steady_ms')}ms")
    print("bench-gui:", line)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
