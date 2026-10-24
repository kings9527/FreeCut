"""导出对话框：分辨率 / 画质档 / 输出路径 / 进度。

参数与命令行完全一致——用的是同一个 ``freecut.export``，所以界面上导出的
和 ``python -m freecut export`` 导出的东西没有第二套实现。
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QVBoxLayout)

from ... import theme
from ...export import PRESETS, QUALITY_TIERS, ExportPreset, with_quality


class _ExportWorker(QThread):
    progressed = Signal(int, str)
    finished_ok = Signal(str, float)
    failed = Signal(str)

    def __init__(self, project, out_path: str, preset: ExportPreset,
                 extra: list | None = None) -> None:
        super().__init__()
        self.project = project
        self.out_path = out_path
        self.preset = preset
        self.extra = list(extra or [])
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:                             # noqa: D401
        from ...export import export, export_many

        started = time.time()
        try:
            if self.extra:
                targets = [(self.out_path, self.preset)] + self.extra
                export_many(self.project, targets, self._on_progress)
            else:
                export(self.project, self.out_path, preset=self.preset,
                       progress=self._on_progress)
        except Exception as exc:                       # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(self.out_path, time.time() - started)

    def _on_progress(self, ratio: float, message: str) -> None:
        if self._cancelled:
            raise RuntimeError("已取消")
        self.progressed.emit(int(max(0.0, min(1.0, ratio)) * 100), message)


class ExportDialog(QDialog):
    """剪映那种「导出」面板的最小可用版。"""

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.worker = None
        self.setWindowTitle("导出")
        self.setModal(True)
        self.resize(520, 280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 分辨率
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("分辨率", self))
        self.preset_box = QComboBox(self)
        self._preset_keys = list(PRESETS)
        for key in self._preset_keys:
            preset = PRESETS[key]
            self.preset_box.addItem(f"{key} · {preset.width}×{preset.height} @ {preset.fps:.0f}fps")
        default = self._default_preset_key()
        self.preset_box.setCurrentIndex(self._preset_keys.index(default))
        self.preset_box.currentIndexChanged.connect(self._sync_fps_from_preset)
        row1.addWidget(self.preset_box, 1)
        layout.addLayout(row1)

        # 画质
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("画质", self))
        self.quality_box = QComboBox(self)
        for name in QUALITY_TIERS:
            self.quality_box.addItem(name)
        row2.addWidget(self.quality_box, 1)
        layout.addLayout(row2)

        # 帧率：剪映里分辨率和帧率是两个独立下拉，这里对齐
        row_fps = QHBoxLayout()
        row_fps.addWidget(QLabel("帧率", self))
        self.fps_box = QComboBox(self)
        for value in (24, 25, 30, 50, 60):
            self.fps_box.addItem(f"{value} fps", value)
        self.fps_box.setCurrentIndex(2)          # 默认 30
        self.fps_box.currentIndexChanged.connect(lambda _i: self._refresh_hint())
        row_fps.addWidget(self.fps_box, 1)
        layout.addLayout(row_fps)

        from PySide6.QtWidgets import QCheckBox

        self.verify_box = QCheckBox("导出后检测（黑帧 / 静音 / 帧数 / 音画同步）", self)
        layout.addWidget(self.verify_box)

        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        layout.addWidget(QLabel("同时导出其他预设（可选，会多渲染几路）：", self))
        self.variant_list = QListWidget(self)
        self.variant_list.setMaximumHeight(96)
        for key in self._preset_keys:
            item = QListWidgetItem(key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.variant_list.addItem(item)
        layout.addWidget(self.variant_list)

        # 输出路径
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("输出", self))
        self.path_edit = QLineEdit(self._default_path(), self)
        row3.addWidget(self.path_edit, 1)
        browse = QPushButton("选择…", self)
        browse.clicked.connect(self._browse)
        row3.addWidget(browse)
        layout.addLayout(row3)

        self.hint = QLabel("", self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("取消", self)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        self.start_btn = QPushButton("开始导出", self)
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self.start_export)
        buttons.addWidget(self.start_btn)
        layout.addLayout(buttons)

        self._refresh_hint()

    # -- 默认值 ----------------------------------------------------------- #
    def _default_preset_key(self) -> str:
        canvas = self.bridge.project.canvas
        w, h = int(canvas.width), int(canvas.height)
        for key, preset in PRESETS.items():
            if preset.width == w and preset.height == h:
                return key
        if h > w:
            return "vertical1080"
        return "1080p30"

    def _default_path(self) -> str:
        base = self.bridge.path
        if base:
            stem = os.path.splitext(os.path.basename(base))[0]
            return os.path.join(os.path.dirname(base), f"{stem}.mp4")
        return os.path.join(os.path.expanduser("~"), "freecut_export.mp4")

    def _refresh_hint(self) -> None:
        key = self._preset_keys[self.preset_box.currentIndex()]
        preset = PRESETS[key]
        seconds = max(0.1, self.bridge.duration_us / 1e6)
        frames = int(round(seconds * self.selected_fps()))
        self.hint.setText(
            f"时长 {seconds:.1f} 秒 · {frames} 帧 · 画布 {preset.width}×{preset.height}；"
            f"编码器自动选择（优先 libx264，没有就用硬件编码）")
        self._apply_media_guard()

    def _apply_media_guard(self) -> None:
        """素材缺失时禁用「开始导出」，并把原因写进提示（剪映也是提前拦）。"""
        from ...export import missing_media

        try:
            missing = missing_media(self.bridge.project)
        except Exception:                              # noqa: BLE001
            missing = []
        if not missing:
            self.start_btn.setEnabled(True)
            return
        names = "、".join(os.path.basename(p) for p in missing[:3])
        more = f" 等 {len(missing)} 个" if len(missing) > 3 else ""
        self.hint.setText(f"素材缺失，无法导出：{names}{more}（请还原文件或重新链接）")
        self.start_btn.setEnabled(False)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出到", self.path_edit.text(),
                                              "视频 (*.mp4 *.mov)")
        if path:
            self.path_edit.setText(path)

    # -- 开始 ------------------------------------------------------------- #
    def _sync_fps_from_preset(self) -> None:
        key = self._preset_keys[self.preset_box.currentIndex()]
        fps = int(round(PRESETS[key].fps))
        index = self.fps_box.findData(fps)
        if index >= 0:
            self.fps_box.setCurrentIndex(index)
        self._refresh_hint()

    def selected_fps(self) -> float:
        data = self.fps_box.currentData()
        return float(data if data else 30)

    def current_preset(self) -> ExportPreset:
        import dataclasses

        key = self._preset_keys[self.preset_box.currentIndex()]
        preset = PRESETS[key]
        tier = self.quality_box.currentText()
        tuned = with_quality(preset, tier)
        # 帧率以下拉为准
        return dataclasses.replace(tuned, fps=self.selected_fps())

    def start_export(self) -> None:
        out_path = self.path_edit.text().strip()
        if not out_path:
            QMessageBox.warning(self, "导出", "请先选择输出路径。")
            return
        if os.path.exists(out_path):
            answer = QMessageBox.question(self, "覆盖？", f"{os.path.basename(out_path)} 已存在，覆盖吗？")
            if answer != QMessageBox.Yes:
                return
        self.start_btn.setEnabled(False)
        self.preset_box.setEnabled(False)
        self.quality_box.setEnabled(False)
        self.path_edit.setEnabled(False)
        self.worker = _ExportWorker(self.bridge.project, out_path, self.current_preset())
        self.worker.progressed.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()
        self.bridge.statusMessage.emit("开始导出…")

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.hint.setText(message)

    def _on_done(self, path: str, seconds: float) -> None:
        self.progress.setValue(100)
        size_mb = os.path.getsize(path) / 1024 / 1024 if os.path.exists(path) else 0.0
        self.bridge.statusMessage.emit(f"导出完成：{os.path.basename(path)}（{size_mb:.1f} MB，{seconds:.0f}s）")
        report_text = ""
        if getattr(self, "verify_box", None) is not None and self.verify_box.isChecked():
            report_text = self._verify_output(path)
        message = f"{path}\n\n{size_mb:.1f} MB，用时 {seconds:.0f} 秒"
        if report_text:
            message += "\n\n" + report_text
        box = QMessageBox(self)
        box.setWindowTitle("导出完成")
        box.setText(message)
        open_btn = box.addButton("打开所在文件夹", QMessageBox.ActionRole)
        box.addButton("关闭", QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self._reveal_in_folder(path)
        self.accept()

    def _reveal_in_folder(self, path: str) -> None:
        """在资源管理器里定位文件（非 Windows 退回打开目录）。"""
        import subprocess
        import sys as _sys

        if not os.path.exists(path):
            return
        try:
            if _sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except OSError:
            pass

    def _extra_targets(self, out_path: str, preset) -> list:
        """把勾选的其他预设变成 (路径, 预设) 列表。"""
        if not hasattr(self, "variant_list"):
            return []
        from dataclasses import replace

        targets = []
        stem, ext = os.path.splitext(out_path)
        for row in range(self.variant_list.count()):
            item = self.variant_list.item(row)
            if item.checkState() != Qt.Checked:
                continue
            key = item.text()
            if key == self._preset_keys[self.preset_box.currentIndex()]:
                continue
            tuned = with_quality(PRESETS[key], self.quality_box.currentText())
            tuned = replace(tuned, fps=self.selected_fps())
            targets.append((f"{stem}_{key}{ext or '.mp4'}", tuned))
        return targets

    def _verify_output(self, path: str) -> str:
        """跑一遍成品检测，返回给人看的摘要（失败也不影响导出结果）。"""
        try:
            from ... import verify as verify_mod

            report = verify_mod.verify(path)
        except Exception as exc:                       # noqa: BLE001
            return f"检测失败：{exc}"
        head = "检测通过 ✅" if report.ok else "检测发现问题 ❌"
        problems = "；".join(report.problems[:3])
        return f"{head}\n{problems}" if problems else head

    def _on_failed(self, message: str) -> None:
        self.start_btn.setEnabled(True)
        self.preset_box.setEnabled(True)
        self.quality_box.setEnabled(True)
        self.path_edit.setEnabled(True)
        self.progress.setValue(0)
        self.bridge.statusMessage.emit(f"导出失败：{message}")
        QMessageBox.critical(self, "导出失败", message)

    def reject(self) -> None:                          # noqa: D401
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        super().reject()
