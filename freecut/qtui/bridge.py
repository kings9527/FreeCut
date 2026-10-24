"""Project ↔ Qt 的唯一耦合点。

界面层不直接改 ``Project``，也不自己算帧：所有读写都从这里过，
这样以后换 QML 或换别的壳时，只有这一个文件要动。
"""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from .. import builder
from ..model import Project, Segment


class Bridge(QObject):
    """工程状态 + 播放头 + 预览渲染。"""

    playheadChanged = Signal(int)          # 微秒
    projectReloaded = Signal()
    selectionChanged = Signal(object)      # Segment | None
    statusMessage = Signal(str)
    edited = Signal()                      # 片段时间被改过（时间线要重画）

    def __init__(self, project: Optional[Project] = None):
        super().__init__()
        self.project: Project = project or builder.new_project(1280, 720, 30)
        self.path: Optional[str] = None
        self.playhead: int = 0
        self.selected: Optional[Segment] = None
        #: 多选集合（``selected`` 是其中的"主选"，用于属性面板/焦点）
        self.selection: list = []
        self._renderer = None
        self._renderer_size = (0, 0)
        self.dirty = False
        #: 预览渲染线程与界面线程的互斥锁（见 qtui/render_service.py）
        self.render_lock = threading.RLock()
        #: 工程内容版本号：改一次 +1，工作线程据此重建渲染器
        self._epoch = 0
        self._service = None
        # PR 的 track targeting：插入/覆盖只落在点亮的轨道上（剪映没有）
        self.targets: dict = {}   # id(track) → 轨道类型
        self.source: Optional[dict] = None   # 源监视器（PR 三点编辑）
        # 简单撤销：只记片段时间的三元组，够 P1 用
        self._undo: list = []
        self._redo: list = []
        #: 剪映式「复制/粘贴」：内部剪贴板（不碰系统剪贴板）
        self._clipboard: Optional[dict] = None
        #: 「复制属性」的暂存
        self._attributes: Optional[dict] = None
        #: 轨道状态（S/静音/锁）——会话内有效，见 track_state()
        self._track_state: dict = {}
        self._volume_backup: dict = {}

    # -- 内容版本 --------------------------------------------------------- #
    @property
    def epoch(self) -> int:
        return self._epoch

    def touch(self) -> None:
        self.dirty = True
        """工程内容变了：让预览线程下次重建渲染器。"""
        with self.render_lock:
            self._epoch += 1

    @property
    def render_service(self):
        """预览渲染线程（第一次用到时才建，测试里同步渲染不会碰它）。"""
        if self._service is None:
            from .render_service import RenderService

            self._service = RenderService(self)
        return self._service

    def shutdown(self) -> None:
        if self._service is not None:
            try:
                self._service.shutdown()
            except Exception:                      # noqa: BLE001
                pass
            self._service = None

    # -- 工程 ------------------------------------------------------------- #
    def load(self, path: str) -> bool:
        self.dirty = False
        try:
            project = Project.load(path)
        except Exception as exc:                       # noqa: BLE001
            self.statusMessage.emit(f"打不开工程: {exc}")
            return False
        with self.render_lock:
            self.project = project
            self.path = path
            self.playhead = 0
            self.selected = None
            self.selection = []
            self._drop_renderer()
            self._epoch += 1
        self.projectReloaded.emit()
        self.statusMessage.emit(f"已打开 {os.path.basename(path)}")
        return True

    def save(self, path: Optional[str] = None) -> bool:
        self.dirty = False
        target = path or self.path
        if not target:
            return False
        try:
            self.project.save(target)
        except Exception as exc:                       # noqa: BLE001
            self.statusMessage.emit(f"保存失败: {exc}")
            return False
        self.path = target
        self.statusMessage.emit(f"已保存 {os.path.basename(target)}")
        return True

    def set_project(self, project: Project, path: Optional[str] = None) -> None:
        with self.render_lock:
            self.project = project
            self.path = path
            self.playhead = 0
            self.selected = None
            self.selection = []
            self._drop_renderer()
            self._epoch += 1
        self.projectReloaded.emit()

    # -- 源监视器（PR 的三点编辑，剪映没有）-------------------------------- #
    def load_source(self, path: str, kind: str) -> None:
        """把素材装进源监视器；入出点默认整段。"""
        from ..media import probe

        try:
            info = probe(path)
            duration = int(info.duration_us) or 5_000_000
        except Exception:                              # noqa: BLE001
            duration = 5_000_000
        self.source = {"path": path, "kind": kind, "duration_us": duration,
                       "in_us": 0, "out_us": duration, "playhead_us": 0}
        self.statusMessage.emit(
            f"源：{os.path.basename(path)}（{duration / 1e6:.1f}s；I/O 标记，逗号插入）")

    def clear_source(self) -> None:
        self.source = None

    @property
    def source_span_us(self) -> int:
        if not self.source:
            return 0
        return max(0, int(self.source["out_us"]) - int(self.source["in_us"]))

    def set_source_playhead(self, us: int) -> None:
        if not self.source:
            return
        self.source["playhead_us"] = max(0, min(int(us), int(self.source["duration_us"])))

    def mark_source_in(self) -> None:
        if not self.source:
            self.statusMessage.emit("源监视器里还没有素材")
            return
        self.source["in_us"] = int(self.source["playhead_us"])
        if self.source["out_us"] <= self.source["in_us"]:
            self.source["out_us"] = int(self.source["duration_us"])
        self.statusMessage.emit(f"入点 {self.source['in_us'] / 1e6:.2f}s")

    def mark_source_out(self) -> None:
        if not self.source:
            self.statusMessage.emit("源监视器里还没有素材")
            return
        self.source["out_us"] = max(int(self.source["playhead_us"]), int(self.source["in_us"]) + 100_000)
        self.statusMessage.emit(f"出点 {self.source['out_us'] / 1e6:.2f}s"
                                f"（选中 {self.source_span_us / 1e6:.2f}s）")

    def _target_track(self, track_type: str):
        """优先点亮的轨道，其次同类第一条，都没有就新建一条。"""
        from .. import builder

        targeted = [t for t in self.targeted_tracks() if t.type == track_type]
        if targeted:
            return targeted[0]
        for track in self.project.tracks:
            if track.type == track_type:
                return track
        try:
            return builder.track_of(self.project, track_type, 0)
        except Exception:                              # noqa: BLE001
            return None

    def insert_from_source(self, at_us: Optional[int] = None) -> Optional[Segment]:
        """插入（ripple）：把目标轨上落点之后的片段整体后移。"""
        if not self.source:
            self.statusMessage.emit("源监视器里还没有素材")
            return None
        from .. import builder

        # 三点编辑：入出点来自源监视器，落点来自**时间线播放头**
        start = int(self.playhead if at_us is None else at_us)
        span = self.source_span_us or int(self.source["duration_us"])
        track_type = "audio" if self.source["kind"] == "audio" else "video"
        track = self._target_track(track_type)
        if track is None:
            self.statusMessage.emit(f"没有可用的{track_type}轨")
            return None
        with self.render_lock:
            for seg in track.segments:
                if int(seg.target_timerange.start) >= start:
                    seg.target_timerange.start = int(seg.target_timerange.start) + span
            seg = self._place_source(start, span)
        if seg is not None:
            self.statusMessage.emit(f"插入 {span / 1e6:.2f}s（后面的片段已后移）")
        return seg

    def overwrite_from_source(self, at_us: Optional[int] = None) -> Optional[Segment]:
        """覆盖：把落点区间里的旧片段裁掉，再放新的。"""
        if not self.source:
            self.statusMessage.emit("源监视器里还没有素材")
            return None

        start = int(self.playhead if at_us is None else at_us)
        span = self.source_span_us or int(self.source["duration_us"])
        end = start + span
        track_type = "audio" if self.source["kind"] == "audio" else "video"
        track = self._target_track(track_type)
        if track is None:
            self.statusMessage.emit(f"没有可用的{track_type}轨")
            return None
        with self.render_lock:
            for seg in list(track.segments):
                seg_start = int(seg.target_timerange.start)
                seg_end = seg_start + int(seg.target_timerange.duration)
                if seg_end <= start or seg_start >= end:
                    continue
                if seg_start >= start and seg_end <= end:
                    track.segments.remove(seg)
                    continue
                if seg_start < start < seg_end:
                    seg.target_timerange.duration = start - seg_start
                elif seg_start < end < seg_end:
                    trim = end - seg_start
                    seg.target_timerange.start = end
                    seg.target_timerange.duration = seg_end - end
                    seg.source_timerange.start = int(seg.source_timerange.start) + trim
            seg = self._place_source(start, span)
        if seg is not None:
            self.statusMessage.emit(f"覆盖 {span / 1e6:.2f}s")
        return seg

    def _place_source(self, start: int, span: int) -> Optional[Segment]:
        from .. import builder

        src_in = int(self.source["in_us"])
        with self.render_lock:
            try:
                if self.source["kind"] == "audio":
                    seg = builder.add_audio(self.project, self.source["path"], start=start,
                                            source_in=src_in, duration=span)
                elif self.source["kind"] == "image":
                    seg = builder.add_image(self.project, self.source["path"], start=start,
                                            duration=span)
                else:
                    seg = builder.add_video(self.project, self.source["path"], start=start,
                                            source_in=src_in, duration=span)
            except Exception as exc:                   # noqa: BLE001
                self.statusMessage.emit(f"放置失败: {exc}")
                return None
        self.select(seg)
        self._push(("refs", seg, 0, 0, "三点编辑"))
        self.projectReloaded.emit()
        self.edited.emit()
        return seg

    # -- 素材 → 时间线 ----------------------------------------------------- #
    def add_media_to_timeline(self, path: str, kind: str, start_us: int,
                              track_index: Optional[int] = None) -> Optional[Segment]:
        """把素材库里的东西放到时间线。

        ``track_index`` 给了就落在那条轨道上（拖到第几行就是第几条），
        没给就落在点亮的「目标轨道」上（PR 习惯），再不然落第一条。
        """
        from .. import builder

        start = max(0, int(start_us))
        track_type = "audio" if kind == "audio" else "video"
        index = 0
        targeted = [t for t in self.targeted_tracks() if t.type == track_type]
        tracks = [t for t in self.project.tracks if t.type == track_type]
        if track_index is not None:
            index = max(0, int(track_index))
        elif targeted:
            index = tracks.index(targeted[0]) if targeted[0] in tracks else 0
        with self.render_lock:
            try:
                if kind == "audio":
                    seg = builder.add_audio(self.project, path, start=start, track_index=index)
                elif kind == "image":
                    seg = builder.add_image(self.project, path, start=start, track_index=index)
                else:
                    seg = builder.add_video(self.project, path, start=start, track_index=index)
            except Exception as exc:                   # noqa: BLE001
                self.statusMessage.emit(f"加到时间线失败: {exc}")
                return None
        self.select(seg)
        self._push(("refs", seg, 0, 0, "添加素材"))
        self.statusMessage.emit(f"已加到时间线：{os.path.basename(path)}")
        self.projectReloaded.emit()
        self.edited.emit()
        return seg

    # -- 播放头 ----------------------------------------------------------- #
    @property
    def duration_us(self) -> int:
        """工程时长。注意 ``Project.duration`` 是**方法**，不是属性。"""
        try:
            value = self.project.duration
            value = value() if callable(value) else value
            return max(1, int(value))
        except Exception:                              # noqa: BLE001
            return 1_000_000

    def set_playhead(self, us: int) -> None:
        us = max(0, min(int(us), self.duration_us))
        if us == self.playhead:
            return
        self.playhead = us
        self.playheadChanged.emit(us)

    def step_frames(self, frames: int) -> None:
        fps = float(self.project.fps or 30)
        self.set_playhead(self.playhead + int(frames * 1_000_000 / fps))

    # -- 选择 ------------------------------------------------------------- #
    # -- 轨道目标（PR 借鉴）---------------------------------------------- #
    def is_targeted(self, track) -> bool:
        return id(track) in self.targets

    def toggle_target(self, track) -> None:
        key = id(track)
        if key in self.targets:
            self.targets.pop(key, None)
            self.statusMessage.emit(f"取消目标：{track.type}")
            return
        # 同类型只留一个目标，和 PR 一样
        for other_key in [k for k, kind in self.targets.items() if kind == track.type]:
            self.targets.pop(other_key, None)
        self.targets[key] = track.type
        self.statusMessage.emit(f"目标轨道：{track.type}")

    def targeted_tracks(self):
        """当前点亮的轨道（按工程里的顺序）。"""
        return [t for t in self.project.tracks if id(t) in self.targets]

    # -- 编辑记录（撤销用）------------------------------------------------- #
    def record_edit(self, seg, before, after, verb: str = "编辑") -> None:
        """片段时间：before/after 是 (start, duration, source_start)。"""
        self._push(("time", seg, before, after, verb))

    def record_transform(self, seg, before: dict, after: dict, verb: str = "调整画面") -> None:
        """画面变换：before/after 是 {tx, ty, scale, rotation, alpha}。"""
        if before == after:
            return
        self._push(("xform", seg, before, after, verb))

    def record_crop(self, seg, before: dict, after: dict, verb: str = "裁剪") -> None:
        if before == after:
            return
        self._push(("crop", seg, dict(before), dict(after), verb))

    def record_keyframes(self, seg, before: dict, after: dict,
                         verb: str = "关键帧") -> None:
        if before == after:
            return
        self._push(("keyframes", seg, dict(before), dict(after), verb))

    def record_speed(self, seg, before: dict, after: dict, verb: str = "变速") -> None:
        """变速：speed + speed_curve（曲线的平均速度也会改 seg.speed）。"""
        if before == after:
            return
        self._push(("speed", seg, dict(before), dict(after), verb))

    def record_color(self, seg, before: dict, after: dict, verb: str = "调色") -> None:
        """HSL / 曲线 / 色轮：before/after 是 {hsl, curve, wheel} 三份字典。"""
        if before == after:
            return
        self._push(("colour", seg, dict(before), dict(after), verb))

    def record_text(self, seg, before: dict, after: dict, verb: str = "编辑文字") -> None:
        """文字内容/字号/颜色/描边/阴影。"""
        if before == after:
            return
        self._push(("text", seg, dict(before), dict(after), verb))

    def record_audio(self, seg, before: dict, after: dict, verb: str = "调整声音") -> None:
        """声音参数：音量/淡入淡出/降噪/变声。"""
        if before == after:
            return
        self._push(("audio", seg, dict(before), dict(after), verb))

    def record_materials(self, seg, before_count: int, verb: str = "加特效") -> None:
        """材质类改动（特效/转场）：记下改前的 refs 数量，撤销时截断回去。"""
        self._push(("refs", seg, int(before_count),
                    len(seg.extra_material_refs), verb))

    def record_refs(self, seg, before_refs: list, verb: str = "换转场") -> None:
        """精确记录 refs 列表：换转场是"替换"，数量不变，截断法撤不回来。

        同时把被替换掉的材质 id 一起存着，撤销时能连材质一起还回去。
        """
        after = list(seg.extra_material_refs)
        if list(before_refs) == after:
            return
        self._push(("refs_list", seg, list(before_refs), after, verb))

    def record_adjust(self, seg, before: dict, after: dict, verb: str = "调节") -> None:
        """调节滑杆：before/after 是 {亮度键: 值}。"""
        if before == after:
            return
        self._push(("adjust", seg, dict(before), dict(after), verb))

    def _push(self, entry) -> None:
        self._redo.clear()
        self._undo.append(entry)
        self.touch()
        self.statusMessage.emit(f"{entry[4]}（可撤销）")
        self.edited.emit()

    def undo(self) -> bool:
        if not self._undo:
            return False
        entry = self._undo.pop()
        with self.render_lock:
            self._apply(entry[1], entry[2], entry[0])
            self._epoch += 1
        self._redo.append(entry)
        self.statusMessage.emit(f"撤销：{entry[4]}")
        self.edited.emit()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        entry = self._redo.pop()
        with self.render_lock:
            self._apply(entry[1], entry[3], entry[0])
            self._epoch += 1
        self._undo.append(entry)
        self.statusMessage.emit(f"重做：{entry[4]}")
        self.edited.emit()
        return True

    def _apply(self, seg, payload, kind: str = "time") -> None:
        if kind == "structure":
            import copy

            for track, segs in (payload or []):
                track.segments = [copy.deepcopy(s) for s in segs]
            live = {id(s) for t in self.project.tracks for s in t.segments}
            # 撤销/重做会换掉片段对象：选择集合里死掉的引用要清掉
            self.selection = [s for s in self.selection if id(s) in live]
            if self.selected is not None and id(self.selected) not in live:
                self.selected = self.selection[-1] if self.selection else None
                self.selectionChanged.emit(self.selected)
            return
        if kind == "flip":
            flip_h, flip_v = payload
            seg.clip.flip_h = bool(flip_h)
            seg.clip.flip_v = bool(flip_v)
            return
        if kind == "volume":
            from .. import builder

            builder.set_volume(seg, float(payload))
            return
        if kind == "crop":
            try:
                material = self.project.material(seg.material_id)
            except Exception:                      # noqa: BLE001
                return
            if material is not None:
                material["crop"] = dict(payload or {})
            return
        if kind == "keyframes":
            from ..model import Keyframe

            seg.keyframes = {
                name: [Keyframe(int(t), float(v), str(c)) for t, v, c in items]
                for name, items in (payload or {}).items()
            }
            return
        if kind == "speed":
            from .. import speed as speed_mod

            seg.speed = float(payload.get("speed", 1.0))
            curve = payload.get("curve") or []
            seg.speed_curve = list(curve)
            for ref in self.project.refs_of(seg):
                if getattr(ref, "type", "") == "speed":
                    if curve:
                        pts = [{"x": float(x), "y": float(y)} for x, y in curve]
                        ref["mode"] = "curve"
                        cs = dict(ref.get("curve_speed") or {})
                        cs["speed_points"] = speed_mod.to_cube_points(
                            speed_mod.normalize(pts))
                        ref["curve_speed"] = cs
                    else:
                        ref["mode"] = "normal"
            return
        if kind == "colour":
            refs = self.project.refs_of(seg)
            for ref in refs:
                kind_name = getattr(ref, "type", "")
                if kind_name == "hsl":
                    ref["bands"] = dict(payload.get("hsl") or {})
                elif kind_name == "curve":
                    ref["curves"] = dict(payload.get("curve") or {})
                elif kind_name == "color_wheel":
                    ref["wheels"] = dict(payload.get("wheel") or {})
            return
        if kind == "text":
            mat = self.project.material(seg.material_id)
            if mat is not None:
                for key, value in payload.items():
                    mat[key] = value
            return
        if kind == "audio":
            from .. import builder

            seg.volume = float(payload.get("volume", 1.0))
            builder.set_audio_fade(self.project, seg,
                                   fade_in_us=int(payload.get("fade_in_us", 0)),
                                   fade_out_us=int(payload.get("fade_out_us", 0)))
            builder.set_audio_effects(self.project, seg,
                                      denoise=float(payload.get("denoise", 0.0)),
                                      pitch=float(payload.get("pitch", 1.0)))
            return
        if kind == "refs":
            count = int(payload)
            refs = seg.extra_material_refs
            if count < len(refs):
                del refs[count:]
            if not seg.transition_id:
                pass
            return
        if kind == "refs_list":
            seg.extra_material_refs = list(payload)
            return
        if kind == "cover":
            self.project.cover_time = int(payload)
            return
        if kind == "tracks":
            import copy

            self.project.tracks = [copy.deepcopy(t) for t in (payload or [])]
            self.selected = None
            self.selection = []
            return
        if kind == "adjust":
            mat = self.project.material(seg.material_id)
            if mat is not None:
                mat["adjust"] = dict(payload)
            return
        if kind == "xform":
            from .. import builder

            builder.set_transform(seg, scale=payload.get("scale"), x=payload.get("tx"),
                                  y=payload.get("ty"), rotation=payload.get("rotation"),
                                  alpha=payload.get("alpha"))
            return
        start, dur, src_start = payload
        seg.target_timerange.start = int(start)
        seg.target_timerange.duration = int(dur)
        seg.source_timerange.start = int(src_start)

    def select(self, seg: Optional[Segment], additive: bool = False) -> None:
        """选中片段。``additive=True`` 时切换它（Ctrl+点 / 框选累加）。

        编组过的片段会**整组一起选中**（剪映的行为）。
        """
        if seg is None:
            self.selection = []
        else:
            members = self.group_members(seg)
            if additive:
                if any(s is seg for s in self.selection):
                    drop = {id(s) for s in members}
                    self.selection = [s for s in self.selection if id(s) not in drop]
                else:
                    for member in members:
                        if not any(s is member for s in self.selection):
                            self.selection = self.selection + [member]
            else:
                self.selection = list(members)
        self.selected = self.selection[-1] if self.selection else None
        self.selectionChanged.emit(self.selected)

    def select_many(self, segs, additive: bool = False) -> None:
        """框选：一次选中一批片段。"""
        chosen = [s for s in (segs or [])]
        if additive:
            for seg in chosen:
                if not any(s is seg for s in self.selection):
                    self.selection = self.selection + [seg]
        else:
            self.selection = chosen
        self.selected = self.selection[-1] if self.selection else None
        self.selectionChanged.emit(self.selected)

    def is_selected(self, seg) -> bool:
        return any(s is seg for s in self.selection)

    def clear_selection(self) -> None:
        self.select(None)

    def selection_count(self) -> int:
        return len(self.selection)

    # -- 编辑命令（时间线工具栏 / 右键菜单 / 快捷键共用同一份实现）---------- #
    # 结构类改动（分割/删除/粘贴/倒放/定格）用「轨道快照」做撤销：
    # 每条受影响轨道在改动前后各存一份 deepcopy，撤销时整体换回去。
    # 片段数量是几十级的，deepcopy 不贵，换来的是**不会漏字段**。
    def track_of_segment(self, seg):
        for track in self.project.tracks:
            if seg in track.segments:
                return track
        return None

    @staticmethod
    def _copy_segments(track) -> list:
        import copy

        return [copy.deepcopy(s) for s in track.segments]

    def _record_structure(self, before: list, verb: str) -> None:
        after = [(track, self._copy_segments(track)) for track, _segs in before]
        self._push(("structure", None, before, after, verb))

    def _snapshot(self, tracks) -> list:
        return [(track, self._copy_segments(track)) for track in tracks]

    def _clone_extra_refs(self, seg) -> list:
        """把片段引用的材质也复制一份，粘贴出来的片段才能独立调参。"""
        import copy

        from ..model import new_id

        out = []
        for mid in seg.extra_material_refs:
            mat = self.project.material(mid)
            if mat is None:
                continue
            clone = copy.deepcopy(mat)
            clone.id = new_id()
            self.project.materials[clone.id] = clone
            out.append(clone.id)
        return out

    def clone_segment(self, seg, start: int):
        """把一个片段复制到 ``start``（内部用：粘贴/复制/定格）。"""
        import copy

        from ..model import Segment, Timerange

        clone = Segment(
            material_id=seg.material_id,
            target_timerange=Timerange(int(start), int(seg.target_timerange.duration)),
            source_timerange=Timerange(int(seg.source_timerange.start),
                                       int(seg.source_timerange.duration)),
            speed=float(seg.speed),
            volume=float(seg.volume),
            visible=bool(seg.visible),
            render_index=int(seg.render_index),
            clip=copy.deepcopy(seg.clip),
            extra_material_refs=self._clone_extra_refs(seg),
            source=str(seg.source),
            is_adjustment=bool(seg.is_adjustment),
            speed_curve=list(seg.speed_curve or []),
            enable=dict(seg.enable or {}),
        )
        clone.keyframes = {
            name: [copy.deepcopy(k) for k in items] for name, items in (seg.keyframes or {}).items()
        }
        return clone

    def clear_span(self, track, start: int, end: int) -> None:
        """把 ``[start, end)`` 里的旧片段裁掉（覆盖/粘贴用）。"""
        for seg in list(track.segments):
            seg_start = int(seg.target_timerange.start)
            seg_end = seg_start + int(seg.target_timerange.duration)
            if seg_end <= start or seg_start >= end:
                continue
            if seg_start >= start and seg_end <= end:
                track.segments.remove(seg)
                continue
            if seg_start < start < seg_end:
                seg.target_timerange.duration = start - seg_start
            elif seg_start < end < seg_end:
                trim = end - seg_start
                seg.target_timerange.start = end
                seg.target_timerange.duration = seg_end - end
                seg.source_timerange.start = int(seg.source_timerange.start) + trim

    # -- 分割 ------------------------------------------------------------- #
    def split_selected(self, at_us: Optional[int] = None) -> list:
        """在播放头（或给定时间）分割。选中片段就只分它，否则分所有轨道上的片段。"""
        from .. import builder

        at = int(self.playhead if at_us is None else at_us)
        targets = []
        if self.selection:
            # 有选中就只分选中的（播放头不在它们身上就明确说不分，别偷偷分全轨）
            hits = [s for s in self.selection if s.target_timerange.contains(at)]
            if not hits:
                self.statusMessage.emit("播放头不在选中的片段上，无法分割")
                return []
            targets = hits
        elif self.selected is not None and self.selected.target_timerange.contains(at):
            targets = [self.selected]
        else:
            for track in self.project.tracks:
                if self.is_locked(track):
                    continue
                for seg in track.segments:
                    if seg.target_timerange.contains(at):
                        targets.append(seg)
        if not targets:
            self.statusMessage.emit("播放头不在任何片段上，无法分割")
            return []
        tracks = []
        for seg in targets:
            track = self.track_of_segment(seg)
            if track is not None and track not in tracks and not self.is_locked(track):
                tracks.append(track)
        if not tracks:
            self.statusMessage.emit("轨道被锁定了")
            return []
        with self.render_lock:
            before = self._snapshot(tracks)
            made = []
            for seg in targets:
                track = self.track_of_segment(seg)
                if track is None or track not in tracks:
                    continue
                try:
                    made.append(builder.split(self.project, seg, at))
                except Exception as exc:               # noqa: BLE001
                    self.statusMessage.emit(f"分割失败: {exc}")
            if made:
                self.select(made[0])
                self._record_structure(before, f"分割（{len(made)} 段）")
        return made

    # -- 轨道增删 ----------------------------------------------------------- #
    def main_track(self):
        """主轨（第一条视频轨）。剪映里主轨唯一且不可删，删掉会被补一条空主轨。"""
        for track in self.project.tracks:
            if track.type == "video":
                return track
        return None

    def delete_track(self, index: int) -> bool:
        """删掉整条轨道（连带片段）。主轨拒绝删除；一次撤销可还原。"""
        import copy

        tracks = list(self.project.tracks)
        if not (0 <= index < len(tracks)):
            return False
        track = tracks[index]
        if track is self.main_track():
            self.statusMessage.emit(
                "主轨不能删除（剪映会补一条空主轨）；要清空内容请用「清空轨道」")
            return False
        with self.render_lock:
            before = copy.deepcopy(tracks)
            tracks.pop(index)
            self.project.tracks = tracks
            self._epoch += 1
            self.selected = None
            self.selection = []
        after = copy.deepcopy(self.project.tracks)
        self._push(("tracks", None, before, after, f"删除{track.name or track.type}轨道"))
        self.projectReloaded.emit()
        self.selectionChanged.emit(None)
        self.statusMessage.emit(f"已删除一条轨道（{len(track.segments)} 个片段，可撤销）")
        return True

    def clear_track(self, index: int) -> int:
        """清空轨道上的片段，轨道本身保留（主轨也能清空）。"""
        import copy

        tracks = list(self.project.tracks)
        if not (0 <= index < len(tracks)):
            return 0
        track = tracks[index]
        if self.is_locked(track):
            self.statusMessage.emit("轨道被锁定了")
            return 0
        removed = len(track.segments)
        if not removed:
            self.statusMessage.emit("这条轨道本来就是空的")
            return 0
        with self.render_lock:
            before = copy.deepcopy(tracks)
            track.segments = []
            self._epoch += 1
            self.selected = None
            self.selection = []
        after = copy.deepcopy(self.project.tracks)
        self._push(("tracks", None, before, after, f"清空轨道（{removed} 段）"))
        self.projectReloaded.emit()
        self.selectionChanged.emit(None)
        self.statusMessage.emit(f"已清空轨道（{removed} 个片段，可撤销）")
        return removed

    # -- 解码器预热 --------------------------------------------------------- #
    def prewarm_decoders(self, limit: int = 4) -> None:
        """后台把常用素材的解码器先打开：第一帧就不用等容器初始化。

        PyAV 打开一个容器要 150–600 ms（实测），38 个片段同时开会让首帧卡 1 秒以上。
        这里按"不同的文件"去重，只开前几个。
        """
        try:
            from ..av_reader import av_available, shared_reader
        except Exception:                              # noqa: BLE001
            return
        if not av_available():
            return
        wanted = []
        for track in self.project.tracks:
            for seg in track.segments:
                mat = self.project.material(seg.material_id)
                path = str(getattr(mat, "path", None) or "")
                if not path or not os.path.isfile(path):
                    continue
                width = int(getattr(mat, "width", 0) or 0)
                height = int(getattr(mat, "height", 0) or 0)
                if width <= 0 or height <= 0:
                    continue
                key = (os.path.normcase(path), width, height)
                if key not in wanted:
                    wanted.append(key)
                if len(wanted) >= limit:
                    break
            if len(wanted) >= limit:
                break
        if not wanted:
            return
        if not hasattr(self, "_warm_keys"):
            self._warm_keys = set()
        todo = [item for item in wanted if item not in self._warm_keys]
        if not todo:
            return
        self._warm_keys.update(todo)

        def run() -> None:
            for path, width, height in todo:
                try:
                    shared_reader(path, width, height)
                except Exception:                      # noqa: BLE001
                    pass

        import threading

        threading.Thread(target=run, name="fc-prewarm", daemon=True).start()

    def record_snapshot(self, before, verb: str) -> None:
        """用按下时抓的轨道快照记一次撤销（编组整体移动用）。"""
        if before:
            self._record_structure(before, verb)

    # -- 编组 --------------------------------------------------------------- #
    def group_members(self, seg) -> list:
        """和 ``seg`` 同组的所有片段（自己也算；没编组就只有自己）。"""
        gid = str(getattr(seg, "group_id", "") or "")
        if not gid:
            return [seg] if seg is not None else []
        out = []
        for track in self.project.tracks:
            for other in track.segments:
                if str(getattr(other, "group_id", "") or "") == gid:
                    out.append(other)
        return out

    def group_selected(self) -> int:
        """把选中的片段编成一组（≥2 个才有意义）。"""
        from ..model import new_id

        segs = self.selection or ([self.selected] if self.selected is not None else [])
        segs = [s for s in segs if s is not None]
        if len(segs) < 2:
            self.statusMessage.emit("至少选中两个片段才能编组")
            return 0
        tracks = []
        for seg in segs:
            track = self.track_of_segment(seg)
            if track is not None and track not in tracks:
                tracks.append(track)
        gid = "group-" + new_id()[:8]
        with self.render_lock:
            before = self._snapshot(tracks)
            for seg in segs:
                seg.group_id = gid
            self._record_structure(before, f"编组（{len(segs)} 段）")
        self.statusMessage.emit(f"已编组 {len(segs)} 个片段（Ctrl+G / Ctrl+Shift+G）")
        return len(segs)

    def ungroup_selected(self) -> int:
        """拆掉选中片段所在的编组。"""
        segs = self.selection or ([self.selected] if self.selected is not None else [])
        members = []
        for seg in segs:
            for other in self.group_members(seg):
                if other not in members and str(getattr(other, "group_id", "")):
                    members.append(other)
        if not members:
            self.statusMessage.emit("选中的片段没有编组")
            return 0
        tracks = []
        for seg in members:
            track = self.track_of_segment(seg)
            if track is not None and track not in tracks:
                tracks.append(track)
        with self.render_lock:
            before = self._snapshot(tracks)
            for seg in members:
                seg.group_id = ""
            self._record_structure(before, f"取消编组（{len(members)} 段）")
        self.statusMessage.emit(f"已取消编组（{len(members)} 段）")
        return len(members)

    # -- 封面 --------------------------------------------------------------- #
    def set_cover(self, at_us: Optional[int] = None) -> int:
        """把播放头这一帧设为封面（剪映的"封面"）；0 = 用第一帧。"""
        at = int(self.playhead if at_us is None else at_us)
        before = int(getattr(self.project, "cover_time", 0) or 0)
        with self.render_lock:
            self.project.cover_time = max(0, at)
            self._epoch += 1
        self._push(("cover", None, before, int(self.project.cover_time), "设置封面"))
        self.statusMessage.emit(
            f"封面已设在 {at / 1e6:.2f}s（主轨轨头的封面会显示这一帧）")
        self.edited.emit()
        return at

    # -- 多时间线（序列）---------------------------------------------------- #
    def sequences(self) -> list:
        return list(self.project.sequences)

    def sequence_names(self) -> list:
        return [s.name or f"时间线 {i + 1}" for i, s in enumerate(self.project.sequences)]

    def switch_sequence(self, index: int) -> bool:
        """切到另一条时间线：选中清空、播放头归零、时间线整体重画。"""
        total = len(self.project.sequences)
        if not (0 <= index < total) or index == self.project.active_sequence:
            return False
        with self.render_lock:
            self.project.active_sequence = int(index)
            self.selected = None
            self.selection = []
            self.playhead = 0
            self._drop_renderer()
            self._epoch += 1
        self.projectReloaded.emit()
        self.selectionChanged.emit(None)
        self.playheadChanged.emit(0)
        self.statusMessage.emit(f"已切到 {self.sequence_names()[index]}")
        return True

    def new_sequence(self, name: Optional[str] = None) -> int:
        """新建一条空时间线并切过去。返回新序列的下标。"""
        from ..model import Sequence

        with self.render_lock:
            index = len(self.project.sequences) + 1
            seq = Sequence(name=name or f"时间线 {index}")
            self.project.sequences.append(seq)
            self.project.active_sequence = len(self.project.sequences) - 1
            self.selected = None
            self.selection = []
            self.playhead = 0
            self._drop_renderer()
            self._epoch += 1
        self.projectReloaded.emit()
        self.selectionChanged.emit(None)
        self.statusMessage.emit(f"已新建 {seq.name}（空时间线）")
        return self.project.active_sequence

    def delete_sequence(self, index: Optional[int] = None) -> bool:
        """删掉一条时间线（只剩一条时拒绝）。"""
        index = self.project.active_sequence if index is None else int(index)
        total = len(self.project.sequences)
        if total <= 1:
            self.statusMessage.emit("至少保留一条时间线")
            return False
        if not (0 <= index < total):
            return False
        with self.render_lock:
            removed = self.project.sequences.pop(index)
            self.project.active_sequence = max(0, min(index, len(self.project.sequences) - 1))
            self.selected = None
            self.selection = []
            self.playhead = 0
            self._drop_renderer()
            self._epoch += 1
        self.projectReloaded.emit()
        self.selectionChanged.emit(None)
        self.statusMessage.emit(f"已删除 {removed.name}")
        return True

    def move_segment_to_track(self, seg, track, restore=None) -> bool:
        """把片段连同时长一起挪到另一条轨道（跨轨拖动用，一次撤销）。

        ``restore`` 是拖动**之前**的 (start, duration, source_start)：
        拖动时字段已经被改过，撤销要回到拖动前的样子，所以先临时还原再快照。
        """
        old = self.track_of_segment(seg)
        if old is None or track is None or old is track:
            return False
        if self.is_locked(old) or self.is_locked(track):
            self.statusMessage.emit("轨道被锁定了，不能跨轨移动")
            return False
        if old.type != track.type:
            self.statusMessage.emit("只能在同一类轨道之间移动")
            return False
        current = (int(seg.target_timerange.start), int(seg.target_timerange.duration),
                   int(getattr(seg.source_timerange, "start", 0) or 0))
        with self.render_lock:
            if restore is not None:
                seg.target_timerange.start, seg.target_timerange.duration, \
                    seg.source_timerange.start = (int(restore[0]), int(restore[1]), int(restore[2]))
            before = self._snapshot([old, track])
            seg.target_timerange.start, seg.target_timerange.duration, \
                seg.source_timerange.start = current
            old.segments.remove(seg)
            track.segments.append(seg)
            self._record_structure(before, "移到另一条轨道")
        self.statusMessage.emit("已移到另一条轨道")
        return True

    def split_segment(self, seg, at_us: int):
        """在任意位置切开**指定**片段（切割工具用）。返回右半段。"""
        from .. import builder

        track = self.track_of_segment(seg)
        if track is None:
            return None
        if self.is_locked(track):
            self.statusMessage.emit("这条轨道已锁定（点轨头的「锁」解锁）")
            return None
        start = int(seg.target_timerange.start)
        end = start + int(seg.target_timerange.duration)
        at = int(at_us)
        if not (start + 100_000 <= at <= end - 100_000):
            self.statusMessage.emit("离片段边缘太近，切不开")
            return None
        with self.render_lock:
            before = self._snapshot([track])
            try:
                right = builder.split(self.project, seg, at)
            except Exception as exc:                   # noqa: BLE001
                self.statusMessage.emit(f"分割失败: {exc}")
                return None
            self.select(right)
            self._record_structure(before, "分割片段")
        self.statusMessage.emit(f"已切开：{at / 1e6:.2f}s")
        return right

    # -- 删除 ------------------------------------------------------------- #
    def speak_text(self, seg, voice: str = "", rate: int = 0):
        """把文字片段读成音频，返回新音频片段（失败返回 None）。"""
        from .. import builder, tts

        mat = self.project.material(seg.material_id)
        content = str(mat.get("content") or "") if mat is not None else ""
        if not content.strip():
            self.statusMessage.emit("文字是空的，先写点内容")
            return None
        work = os.path.join(tempfile.gettempdir(), "freecut-tts")
        os.makedirs(work, exist_ok=True)
        digest = str(abs(hash((content, voice, rate))))
        wav = os.path.join(work, f"say_{digest}.wav")
        ok = tts.synthesize(content, wav, voice=voice, rate=rate)
        if not ok or not os.path.isfile(wav):
            self.statusMessage.emit("本机没有可用的朗读声音（SAPI 不可用）")
            return None
        duration = tts.wav_duration_us(wav)
        start = int(seg.target_timerange.start)
        length = int(duration or seg.target_timerange.duration or 1_000_000)
        with self.render_lock:
            new_seg = builder.add_audio(self.project, wav, start=start, duration=length)
            self._epoch += 1
        self.touch()
        self.edited.emit()
        self.statusMessage.emit(f"已朗读成音频：{length / 1e6:.1f} 秒")
        return new_seg

    def delete_selected(self, ripple: bool = False) -> int:
        segs = self.selection or ([self.selected] if self.selected is not None else [])
        if not segs:
            self.statusMessage.emit("先选中片段再删除")
            return 0
        return self.delete_segments(segs, ripple=ripple)

    def delete_segments(self, segs, ripple: bool = False) -> int:
        tracks = []
        for seg in segs:
            track = self.track_of_segment(seg)
            if track is not None and track not in tracks:
                tracks.append(track)
        tracks = [t for t in tracks if not self.is_locked(t)]
        if not tracks:
            return 0
        with self.render_lock:
            before = self._snapshot(tracks)
            removed = 0
            for seg in list(segs):
                track = self.track_of_segment(seg)
                if track is None or track not in tracks:
                    continue
                span = int(seg.target_timerange.duration)
                start = int(seg.target_timerange.start)
                track.segments.remove(seg)
                removed += 1
                if ripple:
                    for other in track.segments:
                        if int(other.target_timerange.start) >= start + span:
                            other.target_timerange.start = int(other.target_timerange.start) - span
                        elif int(other.target_timerange.start) >= start:
                            other.target_timerange.start = max(start, int(other.target_timerange.start) - span)
            self.selected = None
            self.selection = []
            if removed:
                self._record_structure(before, f"删除 {removed} 个片段")
        self.selectionChanged.emit(None)
        return removed

    # -- 复制 / 粘贴 ------------------------------------------------------- #
    def copy_selected(self) -> bool:
        import copy

        segs = self.selection or ([self.selected] if self.selected is not None else [])
        if not segs:
            self.statusMessage.emit("先选中片段再复制")
            return False
        first = min(int(s.target_timerange.start) for s in segs)
        items = []
        for seg in segs:
            track = self.track_of_segment(seg)
            items.append({
                "segment": copy.deepcopy(seg),
                "track_type": track.type if track is not None else "video",
                # 相对第一个片段的偏移：多选粘贴时保持相互位置
                "offset": int(seg.target_timerange.start) - first,
            })
        self._clipboard = {"items": items,
                           "segment": items[0]["segment"],
                           "track_type": items[0]["track_type"]}
        if len(items) > 1:
            self.statusMessage.emit(f"已复制 {len(items)} 个片段（Ctrl+V 粘贴到播放头）")
        else:
            self.statusMessage.emit("已复制片段（Ctrl+V 粘贴到播放头）")
        return True

    def cut_selected(self) -> bool:
        if not self.copy_selected():
            return False
        return self.delete_selected() > 0

    def has_clipboard(self) -> bool:
        return getattr(self, "_clipboard", None) is not None

    def paste(self, at_us: Optional[int] = None):
        data = getattr(self, "_clipboard", None)
        if not data:
            self.statusMessage.emit("剪贴板是空的（先 Ctrl+C）")
            return None
        items = data.get("items") or [{"segment": data["segment"],
                                       "track_type": data.get("track_type") or "video",
                                       "offset": 0}]
        base = int(self.playhead if at_us is None else at_us)
        made = []
        touched = []
        with self.render_lock:
            for item in items:
                seg = item["segment"]
                track_type = item.get("track_type") or "video"
                start = base + int(item.get("offset") or 0)
                track = self._target_track(track_type)
                if track is None or self.is_locked(track):
                    self.statusMessage.emit(f"没有可用的{track_type}轨（或被锁定）")
                    continue
                if track not in touched:
                    touched.append(track)
            if not touched:
                return None
            before = self._snapshot(touched)
            for item in items:
                seg = item["segment"]
                track = self._target_track(item.get("track_type") or "video")
                start = base + int(item.get("offset") or 0)
                span = int(seg.target_timerange.duration)
                self.clear_span(track, start, start + span)
                clone = self.clone_segment(seg, start)
                track.segments.append(clone)
                made.append(clone)
            if made:
                self.select_many(made)
                self._record_structure(before, "粘贴片段" if len(made) == 1
                                       else f"粘贴 {len(made)} 个片段")
        return made[0] if made else None

    def duplicate_selected(self):
        segs = self.selection or ([self.selected] if self.selected is not None else [])
        if not segs:
            self.statusMessage.emit("先选中片段")
            return None
        made = []
        touched = []
        for seg in segs:
            track = self.track_of_segment(seg)
            if track is None or self.is_locked(track) or track in touched:
                continue
            touched.append(track)
        if not touched:
            return None
        with self.render_lock:
            before = self._snapshot(touched)
            for seg in segs:
                track = self.track_of_segment(seg)
                if track is None or track not in touched:
                    continue
                start = int(seg.target_timerange.start + seg.target_timerange.duration)
                clone = self.clone_segment(seg, start)
                track.segments.append(clone)
                made.append(clone)
            if made:
                self.select_many(made)
                self._record_structure(before, "复制片段到末尾")
        return made[0] if made else None

    def copy_attributes(self) -> bool:
        """复制「画面属性」：变换、调色、特效、变速、音量（剪映右键的"复制属性"）。"""
        import copy

        if self.selected is None:
            self.statusMessage.emit("先选中一个片段")
            return False
        self._attributes = {
            "clip": copy.deepcopy(self.selected.clip),
            "speed": float(self.selected.speed),
            "volume": float(self.selected.volume),
            "keyframes": copy.deepcopy(self.selected.keyframes or {}),
            "enable": dict(self.selected.enable or {}),
            "refs": [copy.deepcopy(self.project.material(m))
                     for m in self.selected.extra_material_refs
                     if self.project.material(m) is not None],
        }
        self.statusMessage.emit("已复制属性（右键目标片段 → 粘贴属性）")
        return True

    def has_attributes(self) -> bool:
        return getattr(self, "_attributes", None) is not None

    def paste_attributes(self) -> bool:
        import copy

        from ..model import new_id

        data = getattr(self, "_attributes", None)
        if not data:
            self.statusMessage.emit("还没有复制过属性")
            return False
        if self.selected is None:
            self.statusMessage.emit("先选中要粘贴属性的片段")
            return False
        seg = self.selected
        before = {"clip": copy.deepcopy(seg.clip), "speed": float(seg.speed),
                  "volume": float(seg.volume)}
        with self.render_lock:
            seg.clip = copy.deepcopy(data["clip"])
            seg.speed = float(data["speed"])
            seg.volume = float(data["volume"])
            seg.keyframes = copy.deepcopy(data["keyframes"])
            seg.enable = dict(data["enable"])
            refs = []
            for mat in data["refs"]:
                clone = copy.deepcopy(mat)
                clone.id = new_id()
                self.project.materials[clone.id] = clone
                refs.append(clone.id)
            seg.extra_material_refs = refs
            after = {"clip": copy.deepcopy(seg.clip), "speed": float(seg.speed),
                     "volume": float(seg.volume)}
        self.record_transform(seg, before, after, "粘贴属性")
        return True

    # -- 画面操作 ---------------------------------------------------------- #
    def mirror(self, axis: str = "h") -> bool:
        """镜像：h 左右翻转 / v 上下翻转。多选时一次翻所有选中的。"""
        segs = self.selection or ([self.selected] if self.selected is not None else [])
        if not segs:
            self.statusMessage.emit("先选中片段")
            return False
        verb = "水平镜像" if axis != "v" else "垂直镜像"
        if len(segs) == 1:
            seg = segs[0]
            before = [bool(seg.clip.flip_h), bool(seg.clip.flip_v)]
            if axis == "v":
                seg.clip.flip_v = not seg.clip.flip_v
            else:
                seg.clip.flip_h = not seg.clip.flip_h
            after = [bool(seg.clip.flip_h), bool(seg.clip.flip_v)]
            self._push(("flip", seg, before, after, verb))
            return True
        # 多选：用轨道快照做一次撤销（一次 Ctrl+Z 全回来）
        tracks = []
        for seg in segs:
            track = self.track_of_segment(seg)
            if track is not None and track not in tracks:
                tracks.append(track)
        with self.render_lock:
            before = self._snapshot(tracks)
            for seg in segs:
                if axis == "v":
                    seg.clip.flip_v = not seg.clip.flip_v
                else:
                    seg.clip.flip_h = not seg.clip.flip_h
            self._record_structure(before, f"{verb}（{len(segs)} 段）")
        return True

    def reset_transform(self) -> bool:
        seg = self.selected
        if seg is None:
            return False
        from ..model import Clip

        before = {k: getattr(seg.clip, k) for k in
                  ("scale_x", "scale_y", "tx", "ty", "rotation", "alpha")}
        seg.clip = Clip(material_id=seg.clip.material_id)
        after = {k: getattr(seg.clip, k) for k in before}
        self.record_transform(seg, before, after, "重置画面")
        return True

    def set_volume_percent(self, percent: float) -> bool:
        if self.selected is None:
            return False
        from .. import builder

        seg = self.selected
        before = float(seg.volume)
        after = max(0.0, min(2.0, float(percent) / 100.0))
        builder.set_volume(seg, after)
        self._push(("volume", seg, before, after, f"音量 {after * 100:.0f}%"))
        return True

    # -- 轨道状态（S 独奏 / 静音 / 锁）------------------------------------ #
    def track_state(self, track) -> dict:
        if not hasattr(self, "_track_state"):
            self._track_state: dict = {}
        return self._track_state.setdefault(
            track.id, {"solo": False, "mute": False, "lock": False})

    def is_locked(self, track) -> bool:
        return bool(self.track_state(track)["lock"])

    def is_muted(self, track) -> bool:
        return bool(self.track_state(track)["mute"])

    def is_solo(self, track) -> bool:
        return bool(self.track_state(track)["solo"])

    def toggle_track_lock(self, track) -> bool:
        state = self.track_state(track)
        state["lock"] = not state["lock"]
        self.statusMessage.emit(("锁定" if state["lock"] else "解锁") + f"轨道 {track.type}")
        self.edited.emit()
        return state["lock"]

    def toggle_track_mute(self, track) -> bool:
        state = self.track_state(track)
        state["mute"] = not state["mute"]
        self._apply_track_volumes()
        self.edited.emit()
        return state["mute"]

    def toggle_track_solo(self, track) -> bool:
        state = self.track_state(track)
        state["solo"] = not state["solo"]
        self._apply_track_volumes()
        self.edited.emit()
        return state["solo"]

    def _apply_track_volumes(self) -> None:
        """静音/独奏落到 ``seg.volume``：导出混音读的就是这个字段（audio.write_audio_mix）。

        原音量存在 ``_volume_backup`` 里，取消静音时原样还回去。
        """
        if not hasattr(self, "_volume_backup"):
            self._volume_backup: dict = {}
        soloed = {t.id for t in self.project.tracks if self.is_solo(t)}
        for track in self.project.tracks:
            silent = self.is_muted(track) or (bool(soloed) and track.id not in soloed)
            for seg in track.segments:
                if silent:
                    self._volume_backup.setdefault(seg.id, float(seg.volume))
                    seg.volume = 0.0
                elif seg.id in self._volume_backup:
                    seg.volume = self._volume_backup.pop(seg.id)

    # -- 倒放 / 定格 -------------------------------------------------------- #
    def reverse_selected(self) -> bool:
        """倒放：整段素材反序重编码后再放回原位置（剪映同样要重编码）。"""
        seg = self.selected
        if seg is None:
            self.statusMessage.emit("先选中一个片段")
            return False
        import os
        import tempfile

        from .. import builder, retime

        path = self._media_path_of(seg)
        if not path or not os.path.isfile(path):
            self.statusMessage.emit("这个片段没有可倒放的素材文件")
            return False
        if int(seg.target_timerange.duration) > 90_000_000:
            self.statusMessage.emit("片段超过 90 秒，倒放要重编码很久，请先在命令行处理")
            return False
        self.statusMessage.emit("倒放中…（要重新编码这段素材）")
        work = os.path.join(tempfile.gettempdir(), "freecut-retime")
        os.makedirs(work, exist_ok=True)
        target = os.path.join(work, f"reverse_{seg.id[:8]}.mp4")
        try:
            retime.reverse_video(path, target)
        except Exception as exc:                       # noqa: BLE001
            self.statusMessage.emit(f"倒放失败: {exc}")
            return False
        track = self.track_of_segment(seg)
        start = int(seg.target_timerange.start)
        duration = int(seg.target_timerange.duration)
        track_type = track.type if track is not None else "video"
        if track is not None:
            with self.render_lock:
                before = self._snapshot([track])
                track.segments.remove(seg)
                index = self._track_ordinal(track, track_type)
                if track_type == "audio":
                    new_seg = builder.add_audio(self.project, target, start=start,
                                                duration=duration, track_index=index)
                else:
                    new_seg = builder.add_video(self.project, target, start=start,
                                                duration=duration, track_index=index)
                self.select(new_seg)
                self._record_structure(before, "倒放")
        self.statusMessage.emit("倒放完成")
        return True

    def freeze_selected(self, duration_us: int = 2_000_000) -> bool:
        """定格：把播放头这一帧渲成 PNG，按 2 秒插在播放头处。"""
        import os
        import tempfile

        from .. import builder, retime

        at = int(self.playhead)
        work = os.path.join(tempfile.gettempdir(), "freecut-retime")
        os.makedirs(work, exist_ok=True)
        track_type = "video"
        track = self._target_track(track_type)
        if track is None:
            self.statusMessage.emit("没有可用的视频轨")
            return False
        self.statusMessage.emit("定格中…")
        try:
            target = retime.freeze_frame(self.project, at,
                                         os.path.join(work, f"freeze_{at // 1000}.png"))
        except Exception as exc:                       # noqa: BLE001
            self.statusMessage.emit(f"定格失败: {exc}")
            return False
        with self.render_lock:
            before = self._snapshot([track])
            index = self._track_ordinal(track, "video")
            new_seg = builder.add_image(self.project, target, start=at,
                                        duration=max(100_000, int(duration_us)),
                                        track_index=index)
            self.select(new_seg)
            self._record_structure(before, "定格")
        self.statusMessage.emit("定格完成")
        return True

    def _track_ordinal(self, track, track_type: str) -> int:
        """在**同类型轨道**里的序号（builder 的 track_index 是按类型数的）。"""
        same = [t for t in self.project.tracks if t.type == track_type]
        try:
            return max(0, same.index(track))
        except ValueError:
            return 0

    def _media_path_of(self, seg) -> str:
        mat = self.project.material(seg.material_id)
        return str(getattr(mat, "path", "") or "") if mat is not None else ""

    # -- 预览 ------------------------------------------------------------- #
    def _drop_renderer(self) -> None:
        if self._renderer is not None:
            try:
                self._renderer.close()
            except Exception:                          # noqa: BLE001
                pass
        self._renderer = None
        self._renderer_size = (0, 0)

    def frame_image(self, width: int, height: int) -> Optional[QImage]:
        """按给定尺寸渲染当前帧。失败返回 None（界面显示占位）。

        同步接口：导出、截图、测试用它（要"给我这一帧"）。
        播放/拖动走 ``render_service``（异步，不冻结界面）。
        """
        from ..render import Renderer

        width = max(16, int(width))
        height = max(16, int(height))
        with self.render_lock:
            if self._renderer is None or self._renderer_size != (width, height):
                self._drop_renderer()
                self._renderer = Renderer(self.project, width, height, self.project.fps)
                self._renderer_size = (width, height)
            fps = float(self.project.fps or 30)
            index = int(round(self.playhead / 1e6 * fps))
            index = max(0, min(index, self._renderer.frame_count - 1))
            frame = self._renderer.render_frame(index)
            if frame is None:
                return None
            h, w = frame.shape[:2]
            return QImage(frame.data, w, h, frame.strides[0], QImage.Format_RGB888).copy()
