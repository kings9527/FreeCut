"""本机硬件/编码能力：对应 CLI 的 ``freecut caps``。"""

from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel,
                               QPlainTextEdit, QVBoxLayout)


class CapsDialog(QDialog):
    """展示 ffmpeg 探测到的硬解/编码器，以及我们默认会选哪个。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("硬件加速与编码器")
        self.resize(620, 380)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("下面的结果来自 ffmpeg 探测（不联网）：", self))
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.refresh_btn = buttons.addButton("重新探测", QDialogButtonBox.ActionRole)
        self.refresh_btn.clicked.connect(self.reload)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)

        self.reload()

    def summary_text(self) -> str:
        from ... import caps

        info = caps.probe()
        try:
            caps.save(info)
        except Exception:                              # noqa: BLE001
            pass
        hw = "、".join(info.get("hwaccels") or []) or "（无）"
        encoders = info.get("encoders") or []
        usable = [e for e in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
                              "h264_mf", "h264_amf", "h264_videotoolbox")
                  if e in encoders]
        return "\n".join([
            f"硬件解码：{hw}",
            f"编码器总数：{len(encoders)}",
            f"可用硬件编码：{'、'.join(usable) or '（无，将退回软件编码）'}",
            f"默认 H.264：{info.get('default_h264') or '（探测失败）'}",
            "",
            "说明：导出时优先用硬件编码；没有硬件时用 libx264（若 ffmpeg 未带 libx264 会退回 mpeg4）。",
        ])

    def reload(self) -> None:
        try:
            self.text.setPlainText(self.summary_text())
        except Exception as exc:                       # noqa: BLE001
            self.text.setPlainText(f"探测失败：{exc}")
