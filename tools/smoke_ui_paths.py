"""手动冒烟：把工具栏/右键菜单/编辑命令都真的点一遍。

测试没覆盖到的入口（focus_inspector、分离音频、定格、倒放）在这里跑一次，
确认没有 AttributeError / 弹窗死锁这类"点了才知道"的错误。
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ".")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from freecut import builder  # noqa: E402
from freecut.qtui.app import MainWindow  # noqa: E402
from freecut.qtui.menus import build_context_menu  # noqa: E402

media = os.path.join("out", "demo.mp4")
win = MainWindow(None)
win.resize(1360, 820)
win.show()
app.processEvents()

project = builder.new_project(640, 360, 30, name="冒烟")
seg = builder.add_video(project, media, start=0, duration=4_000_000)
win.bridge.set_project(project, None)
win._refresh_all()
app.processEvents()
win.bridge.select(seg)
app.processEvents()

print("1. 分割")
win.bridge.set_playhead(2_000_000)
win.split_at_playhead()
app.processEvents()
print("   clips =", sum(len(t.segments) for t in win.bridge.project.tracks))

print("2. 工具栏 focus_inspector")
for page, keyword in (("画面", "裁剪"), ("音频", "音量"), ("音频", "降噪"),
                      ("变速", "变速"), ("动画", "动画")):
    ok = win.focus_inspector(page, keyword)
    print(f"   {page}/{keyword} -> {ok} (当前页 {win.inspector.tabs.tabText(win.inspector.tabs.currentIndex())})")

print("3. 右键菜单动作")
menu = build_context_menu(win, win.bridge.selected)
for key in ("copy", "paste", "duplicate", "mirror_h", "copy_attributes"):
    action = menu._actions_by_key.get(key)
    if action is None:
        print(f"   {key}: 不在菜单里")
        continue
    menu.triggered.emit(action)
    app.processEvents()
    print(f"   {key}: enabled={action.isEnabled()} clips={sum(len(t.segments) for t in win.bridge.project.tracks)}")

print("4. 分离音频")
win.bridge.select(win.bridge.project.tracks[0].segments[0])
win.detach_audio()
app.processEvents()
kinds = [(t.type, len(t.segments)) for t in win.bridge.project.tracks]
print("   轨道:", kinds)

print("5. 定格")
win.bridge.set_playhead(500_000)
ok = win.bridge.freeze_selected(1_000_000)
app.processEvents()
print("   freeze ->", ok, "clips =", sum(len(t.segments) for t in win.bridge.project.tracks))

print("6. 倒放（4 秒素材）")
win.bridge.select(win.bridge.project.tracks[0].segments[0])
ok = win.bridge.reverse_selected()
app.processEvents()
print("   reverse ->", ok)

print("7. 时间线工具栏逐个按钮（只点不会开对话框的）")
for key in ("split", "delete", "copy", "paste", "mirror", "crop", "volume",
            "denoise", "speed", "animation", "magnetic", "snap", "link",
            "playhead_axis"):
    btn = win.timeline_toolbar.buttons[key]
    if btn.isEnabled():
        btn.click()
        app.processEvents()
    print(f"   {key}: enabled={btn.isEnabled()}")

print("8. 渲染一帧")
image = win.bridge.frame_image(320, 180)
print("   frame ->", image is not None, image.width() if image else None)
win.close()
print("冒烟完成")
