"""图文成片：文案 → 配音 → 时间线（剪映同名功能的离线版）。"""

from __future__ import annotations

import os

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QLabel,
                               QPlainTextEdit, QVBoxLayout)


class ScriptDialog(QDialog):
    """把一段文案变成带配音和字幕的时间线。"""

    def __init__(self, project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.stats = None
        self.setWindowTitle("图文成片")
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("把文案粘进来（按句号/换行分句，每句一条字幕）：", self))
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlaceholderText("第一句。第二句。\n也可以每行一句。")
        layout.addWidget(self.text_edit, 1)

        voice_row = QVBoxLayout()
        layout.addWidget(QLabel("配音（本机 SAPI，离线）：", self))
        self.voice_box = QComboBox(self)
        from ... import tts

        self.voice_box.addItem("不配音（静音）", "")
        for name in tts.voices():
            self.voice_box.addItem(name, name)
        layout.addWidget(self.voice_box)

        self.hint = QLabel("", self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("生成时间线")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.build)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- 生成 -------------------------------------------------------------- #
    def media_paths(self) -> list:
        """用时间线上已有的视频素材来铺画面（没有就留空，由实现方处理）。"""
        paths = []
        for track in self.project.tracks:
            if getattr(track, "type", "") != "video":
                continue
            for seg in track.segments:
                mat = self.project.material(seg.material_id)
                path = getattr(mat, "path", "") if mat is not None else ""
                if path and os.path.isfile(str(path)) and str(path) not in paths:
                    paths.append(str(path))
        return paths

    def build(self) -> None:
        from ... import scriptfilm

        text = self.text_edit.toPlainText().strip()
        if not text:
            self.hint.setText("先粘一段文案。")
            return
        voice = self.voice_box.currentData() or ""
        try:
            self.stats = scriptfilm.build_script_project(
                text, self.media_paths(), work_dir=os.path.join(os.path.dirname(__file__), "..", "..", "..", "out", "scriptfilm"),
                voice=voice, rate=0,
                width=int(getattr(self.project.canvas, "width", 1920) or 1920),
                height=int(getattr(self.project.canvas, "height", 1080) or 1080),
                fps=float(getattr(self.project, "fps", 30.0) or 30.0),
                use_tts=bool(voice),
            )
        except Exception as exc:                       # noqa: BLE001
            self.hint.setText(f"生成失败：{exc}")
            return
        self.accept()
