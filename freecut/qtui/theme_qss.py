"""界面令牌 → QSS。

令牌只有一份（``freecut/theme.py``），Tk 版和 Qt 版都从那里取，
所以两套界面的配色与尺寸不会慢慢分叉。
"""

from __future__ import annotations

from .. import theme


def stylesheet() -> str:
    """整窗样式表。数值全部来自 ``freecut.theme``。"""
    t = theme
    return f"""
QWidget {{
    background: {t.BG_PANEL};
    color: {t.FG};
    font-family: "Microsoft YaHei", "Segoe UI";
    font-size: 9pt;
}}
QMainWindow, QDialog {{ background: {t.BG_APP}; }}

/* ---- 顶部工具条 / 标题条 ---- */
#TitleBar {{
    background: {t.BG_APP};
    border-bottom: 1px solid {t.BORDER};
    min-height: {t.APP_BAR_H - 1}px;
    max-height: {t.APP_BAR_H - 1}px;
}}
#AppName {{ font-size: 10pt; font-weight: 600; padding-left: 10px; }}
#BuildTag {{
    color: {t.FG_FAINT}; font-size: 8pt; padding: 0 4px;
    border: 1px solid {t.BORDER}; border-radius: 4px;
}}
#TitleBar QLabel {{ color: {t.FG_DIM}; background: transparent; }}
#ToolBar {{
    background: {t.BG_PANEL};
    border-bottom: 1px solid {t.BORDER};
}}
#ToolBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 10px;
    color: {t.FG};
}}
#ToolBar QToolButton:hover {{
    background: {t.BG_ELEV};
    border-color: {t.BORDER};
}}
#ToolBar QToolButton:pressed {{ background: {t.BORDER_STRONG}; }}

/* ---- 面板标题（52px 约定）---- */
#PanelHeader {{
    background: {t.BG_PANEL};
    border-bottom: 1px solid {t.BORDER};
    min-height: {t.PANEL_HEADER_H - 1}px;
    max-height: {t.PANEL_HEADER_H - 1}px;
}}
#PanelHeader QLabel {{ font-size: 10pt; font-weight: 600; padding-left: 12px; }}

/* ---- 左栏类别 ---- */
#Rail {{
    background: {t.BG_APP};
    border-right: 1px solid {t.BORDER};
}}
#Rail QToolButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    color: {t.FG_DIM};
    padding: 6px 2px;
    font-size: 8pt;
}}
#Rail QToolButton:hover {{ background: {t.BG_ELEV}; color: {t.FG}; }}
#Rail QToolButton:checked {{
    background: {t.ACCENT};
    color: #08222a;
    font-weight: 600;
}}

/* ---- 列表 / 表格 ---- */
QListWidget, QTreeWidget, QTableWidget {{
    background: {t.BG_ELEV};
    border: none;
    outline: 0;
}}
QListWidget::item {{ padding: 5px 8px; border-radius: 4px; }}
QListWidget::item:hover {{ background: {t.BORDER}; }}
QListWidget::item:selected {{ background: {t.ACCENT_DIM}; color: {t.FG}; }}

/* ---- 按钮 ---- */
QPushButton {{
    background: {t.BG_ELEV};
    border: 1px solid {t.BORDER};
    border-radius: 6px;
    padding: 5px 10px;
}}
QPushButton:hover {{ background: {t.BORDER}; }}
QPushButton:pressed {{ background: {t.BORDER_STRONG}; }}
QPushButton:disabled {{ color: {t.FG_FAINT}; }}

/* ---- 输入 / 滑杆 ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {t.BG_INPUT};
    border: 1px solid {t.BORDER};
    border-radius: 5px;
    padding: 3px 6px;
    selection-background-color: {t.ACCENT_DIM};
}}
QComboBox QAbstractItemView {{
    background: {t.BG_ELEV};
    border: 1px solid {t.BORDER};
    selection-background-color: {t.ACCENT_DIM};
}}
QSlider::groove:horizontal {{
    height: 4px; background: {t.BORDER}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {t.ACCENT_DIM}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 12px; margin: -5px 0; border-radius: 6px;
    background: {t.FG}; border: 1px solid {t.BORDER_STRONG};
}}

/* ---- 页签 ---- */
QTabWidget::pane {{ border: none; background: {t.BG_PANEL}; }}
QTabBar::tab {{
    background: transparent; color: {t.FG_DIM};
    padding: 7px 12px; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {t.FG}; }}
QTabBar::tab:selected {{ color: {t.FG}; border-bottom: 2px solid {t.ACCENT}; }}

/* ---- 分隔条 ---- */
QSplitter::handle {{ background: {t.BG_APP}; }}
QSplitter::handle:hover {{ background: {t.ACCENT_DIM}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}

/* ---- 预览区 ---- */
#Viewer {{ background: #0b0b0e; border: none; }}
#PlayerBar {{ background: {t.BG_PANEL}; border-top: 1px solid {t.BORDER}; }}
#Timecode {{
    color: #7ec0ff; font-family: Consolas, monospace; font-size: 11pt;
    padding: 0 8px;
}}
#StatusBar {{ background: {t.BG_APP}; color: {t.FG_DIM}; padding: 3px 10px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {t.BORDER_STRONG}; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.FG_FAINT}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {t.BORDER_STRONG}; border-radius: 5px; min-width: 28px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
"""


def graphicsscene_colors() -> dict:
    """时间线场景要用的颜色（QSS 管不到 QGraphicsScene）。"""
    t = theme
    return {
        "bg": "#17171b",
        "header": "#15151a",
        "row": "#232329",
        "row_line": "#2e2e36",
        "label": "#8c8c96",
        "clip_video": "#3b6ea5",
        "clip_text": t.TRACK_TEXT,
        "clip_audio": "#2f6f52",
        "clip_other": "#5a5a68",
        "selected": t.SELECT_BORDER,
        "playhead": "#e2574c",
        "ruler_text": t.FG_DIM,
    }
