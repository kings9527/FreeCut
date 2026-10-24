"""界面设计令牌（design tokens）。

**数值不是拍脑袋定的**，来自两份可核对的资料：

1. **ReaCut**（`Xiangyu-CAS/rea-capcut`，MIT）的 `docs/UI_PARITY.md`：面板标题 52px、
   应用标题栏 44px、1024×720 下三栏 280/434/280、中性深灰分层、青色强调、
   白色选中边框、红棕色文字轨、缩放滑块与数字同步、三栏分隔线可拖且跨启动记忆。
2. **剪映 11.x 桌面版**的运行界面（用户截图 + 本机安装的合法观察）：顶部资源类别顺序
   素材/音频/文本/贴纸/特效/转场/字幕，左侧类别栏 + 右侧卡片流。

我们借用的是**布局与配色约定**，不是它的代码：栈不同（Tk/Python vs Electron/React），
逐像素复刻也不在目标里。原作者与许可记录在 `docs/NOTICE-UI.md`。
"""

from __future__ import annotations

from typing import Tuple

# --------------------------------------------------------------------------- #
# 颜色：中性深灰分三层 + 青色强调
# --------------------------------------------------------------------------- #
BG_APP = "#16161a"        # 最外层（窗口底色）
BG_PANEL = "#1e1e24"      # 面板
BG_ELEV = "#26262e"       # 面板里抬起来的一块（卡片/输入框底）
BG_INPUT = "#2d2d37"      # 输入框
BORDER = "#35353f"        # 细分隔线
BORDER_STRONG = "#44444f"

FG = "#e8e8ef"
FG_DIM = "#9b9ba7"
FG_FAINT = "#6d6d78"

ACCENT = "#4cc2d4"        # 剪映/ReaCut 都用青色表示"当前"
ACCENT_DIM = "#2c7e8a"

# 轨道配色：文字轨红棕（写进 NOTICE-UI 的那条约定）
TRACK_TEXT = "#7b4a3c"
TRACK_TEXT_SEL = "#9d5f4c"

# 选中片段用浅色边框（ReaCut 用白色，我们用亮一档的灰白以免抢眼）
SELECT_BORDER = "#e2e2ea"

# --------------------------------------------------------------------------- #
# 尺寸（逻辑像素，乘上 DPI 缩放后用）
# --------------------------------------------------------------------------- #
APP_BAR_H = 44
PANEL_HEADER_H = 52
RAIL_W = 64
GAP = 8

#: 1024 宽窗口下的三栏宽度（素材/预览/属性）
PANES_1024: Tuple[int, int, int] = (280, 434, 280)
#: 最小窗口宽度（低于它排版会挤，ReaCut 自己也定在 1060）
MIN_WIDTH = 1060
MIN_HEIGHT = 700

#: 顶部资源类别，顺序照剪映桌面版。**Tk 版**用它（7 个有内容的页）。
CATEGORIES: Tuple[str, ...] = ("素材", "音频", "文本", "贴纸", "特效", "转场", "字幕")

#: 剪映专业版左栏实际是 12 个类别（HANDOFF §5.2）。Qt 版按 12 个摆，
#: 做不到的灰显并标注「需云端」——**不隐藏**，因为用户要看到剪映有什么。
JY_CATEGORIES: Tuple[str, ...] = (
    "媒体", "音频", "文本", "贴纸", "特效", "转场", "字幕",
    "模板", "数字人", "智能包装", "滤镜", "调节",
)

#: 类别 → 灰显原因。不在表里 = 本机可用。
CLOUD_ONLY: dict = {
    "贴纸": "剪映贴纸是云端素材包（本机 0 个），不联网不盗链",
    "模板": "模板库在云端，本机没有模板素材",
    "数字人": "数字人要云端形象与驱动模型",
    "智能包装": "智能包装要云端识别与素材库",
}

#: 我们真正做到的类别：剪映 12 个里去掉 4 个云端-only 的（删掉，不灰显）。
QT_CATEGORIES: Tuple[str, ...] = tuple(c for c in JY_CATEGORIES if c not in CLOUD_ONLY)

#: 类别对应的小图标（没有图标字体可用，用 unicode 近似）
CATEGORY_GLYPH = {
    "素材": "▤", "媒体": "▤",
    "音频": "♪",
    "文本": "T",
    "贴纸": "✿",
    "特效": "✦",
    "转场": "⇄",
    "字幕": "字",
    "模板": "▦",
    "数字人": "☻",
    "智能包装": "◈",
    "滤镜": "◐",
    "调节": "◑",
}


def px(value: float, scale: float = 1.0) -> int:
    """把逻辑像素换算成当前 DPI 下的像素。"""
    return max(1, int(round(float(value) * float(scale or 1.0))))


def panes_for(width: int, scale: float = 1.0) -> Tuple[int, int, int]:
    """按窗口宽度给出三栏建议宽度：左右固定，剩余给预览。

    1024 宽时正好是 280/434/280；更宽的窗口把多出来的宽度全给预览，
    这样素材与属性栏不会因为窗口拉宽而变得空旷。
    """
    left = px(PANES_1024[0], scale)
    right = px(PANES_1024[2], scale)
    # 1024 宽时 280+434+280=994，剩下 30px 给分隔条与边距——和 ReaCut 记录的
    # 「1024×720 三栏 280/434/280、右边界 1016」对得上。
    middle = max(px(320, scale), int(width) - left - right - px(30, scale))
    return left, middle, right


def palette() -> dict:
    """给测试和界面自检用的一份可读色板。"""
    return {
        "bg_app": BG_APP, "bg_panel": BG_PANEL, "bg_elev": BG_ELEV,
        "border": BORDER, "fg": FG, "fg_dim": FG_DIM,
        "accent": ACCENT, "track_text": TRACK_TEXT, "select_border": SELECT_BORDER,
    }


def summary() -> str:
    return (f"{len(CATEGORIES)} 个类别 / 三栏 {PANES_1024[0]}+{PANES_1024[1]}+{PANES_1024[2]}"
            f" / 强调色 {ACCENT}")
