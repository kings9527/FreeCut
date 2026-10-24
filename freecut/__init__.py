"""FreeCut - a small, free, scriptable video editor.

The data model and the colour pipeline are reconstructed from the shipped
CapCut / JianyingPro 11.3.0 desktop application (see ../docs/).
"""

__version__ = "0.1.0"

#: 界面版本标记：改完界面就跟着改，显示在标题栏提示、标题后缀与「关于」里。
#: （第 243 轮用户问过"你调的是哪个版本"——有这一行就能一眼对上。）
UI_BUILD = "2026-10-23 #302"

from .model import (  # noqa: F401
    Project,
    Canvas,
    Track,
    Segment,
    Timerange,
    Keyframe,
    new_id,
)
