"""把 FreeCut 工程写成 ``.prproj``（gzip XML）。

**这是"结构近似"，不是 Adobe 认证的格式。** 我们只复刻了读端能认的那些元素：
``Sequence`` / ``VideoTrackGroup`` / ``VideoClipTrack`` / ``VideoClipTrackItem`` /
``SubClip`` / ``Clip`` / ``Media``，以及 ``ObjectID`` 与 ``ObjectURef`` 两种引用。

因此它有两条明确的性质：

* **自洽**：我们自己的 ``prproj.import_project`` 能把它读回来，画布、帧率、
  时长、片段数量都对得上（有测试守着）。
* **未证实**：没有在真正的 Premiere 里打开过。PR 的工程文件里还有大量
  我们没解析的节点（效果栈、音频增益、序列设置全集、项目项索引），
  缺了它们 PR 很可能拒绝加载或只认一部分。

所以命令行会明确提示"未在 Premiere 中验证"，不要把它当成导出交付的可靠途径——
要交付渲染结果用 ``freecut export``，要交付剪映用 ``export-draft``。
"""

from __future__ import annotations

import gzip
import uuid
from pathlib import Path
from typing import Dict, List
from xml.etree import ElementTree as ET

from .model import Project
from .prproj import TICKS_PER_SECOND, us_to_ticks

VIDEO_TYPE_GUID = "0547b302-c849-46b3-ae2a-b245e9dd59eb"
AUDIO_TYPE_GUID = "{b1c7b7c1-0000-0000-0000-000000000000}"


def _uid() -> str:
    return "{" + str(uuid.uuid4()) + "}"


def export_prproj(project: Project, path: str, name: str = "") -> str:
    """Write ``project`` to ``path`` as a gzip-compressed ``.prproj``."""
    seq_name = name or project.name or "FreeCut Sequence"
    width = int(project.canvas.width or 1920)
    height = int(project.canvas.height or 1080)
    fps = float(project.fps or 30.0)
    ticks_per_frame = int(round(TICKS_PER_SECOND / max(fps, 1e-6)))

    root = ET.Element("PremiereData", {"Version": "3"})
    seq_uid = _uid()
    video_groups: List[str] = []
    audio_groups: List[str] = []

    next_id = [100]

    def take_id() -> str:
        next_id[0] += 1
        return str(next_id[0])

    # --- track groups -----------------------------------------------------
    def make_group(kind: str) -> ET.Element:
        group = ET.SubElement(root, "VideoTrackGroup" if kind == "video" else "AudioTrackGroup",
                              {"ObjectID": take_id()})
        if kind == "video":
            frame_rect = ET.SubElement(group, "FrameRect")
            frame_rect.text = f"0,0,{width},{height}"
        inner = ET.SubElement(group, "TrackGroup")
        rate = ET.SubElement(inner, "FrameRate")
        rate.text = str(ticks_per_frame)
        tracks = ET.SubElement(inner, "Tracks")
        index = 0
        for track in project.tracks:
            # PR 没有独立的"文字轨"：文字是普通视频片段，所以字幕轨也进视频组
            videoish = ("video", "sticker", "effect", "filter", "text")
            if kind == "video":
                if track.type not in videoish:
                    continue
            elif track.type != kind:
                continue
            track_uid = _uid()
            ET.SubElement(tracks, "Track", {"Index": str(index), "ObjectURef": track_uid})
            _write_track(root, project, track, kind, track_uid, take_id)
            index += 1
        return group

    video_group = make_group("video")
    audio_group = make_group("audio")

    # --- sequence ---------------------------------------------------------
    seq = ET.SubElement(root, "Sequence", {"ObjectID": take_id(), "ObjectUID": seq_uid})
    seq_name_elem = ET.SubElement(seq, "Name")
    seq_name_elem.text = seq_name
    groups = ET.SubElement(seq, "TrackGroups")
    for order, (group, guid) in enumerate(((video_group, VIDEO_TYPE_GUID),
                                           (audio_group, AUDIO_TYPE_GUID))):
        entry = ET.SubElement(groups, "TrackGroup", {"Index": str(order)})
        first = ET.SubElement(entry, "First")
        first.text = guid
        ET.SubElement(entry, "Second", {"ObjectRef": group.get("ObjectID", "")})

    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wb") as fh:
        fh.write(payload)
    return str(target)


def _write_track(root: ET.Element, project: Project, track, kind: str,
                 track_uid: str, take_id) -> None:
    tag = "VideoClipTrack" if kind == "video" else "AudioClipTrack"
    node = ET.SubElement(root, tag, {"ObjectID": take_id(), "ObjectUID": track_uid})
    items = ET.SubElement(node, "ClipItems")
    entries = ET.SubElement(items, "TrackItems")
    for index, seg in enumerate(sorted(track.segments, key=lambda s: s.target_timerange.start)):
        item_id = take_id()
        ET.SubElement(entries, "TrackItem", {"Index": str(index), "ObjectRef": item_id})

        item = ET.SubElement(root, f"{tag}Item", {"ObjectID": item_id})
        holder = ET.SubElement(item, "ClipTrackItem")
        inner = ET.SubElement(holder, "TrackItem")
        for tag_name, value in (("Start", us_to_ticks(seg.target_timerange.start)),
                                ("End", us_to_ticks(seg.target_timerange.end))):
            node_elem = ET.SubElement(inner, tag_name)
            node_elem.text = str(value)

        mat = project.material(seg.material_id)
        is_adjust = bool(getattr(seg, "is_adjustment", False)) or getattr(mat, "type", "") == "adjustment"

        sub_id = take_id()
        # SubClip 必须挂在 ClipTrackItem 里面：解析器读的是 ClipTrackItem/SubClip
        ET.SubElement(holder, "SubClip", {"ObjectRef": sub_id})
        sub = ET.SubElement(root, "SubClip", {"ObjectID": sub_id})
        clip_id = take_id()
        ET.SubElement(sub, "Clip", {"ObjectRef": clip_id})
        clip_holder = ET.SubElement(root, "Clip", {"ObjectID": clip_id})
        # 解析器读的是 Clip/InPoint 这种**嵌套**路径（真实文件里字段在
        # <Clip><Clip>…</Clip></Clip> 里），所以这里必须再套一层
        clip = ET.SubElement(clip_holder, "Clip")
        in_us = seg.source_timerange.start if seg.source_timerange is not None else 0
        out_us = (seg.source_timerange.end if seg.source_timerange is not None
                  else seg.target_timerange.duration)
        for tag_name, value in (("InPoint", us_to_ticks(in_us)), ("OutPoint", us_to_ticks(out_us))):
            node_elem = ET.SubElement(clip, tag_name)
            node_elem.text = str(value)
        if is_adjust:
            adj = ET.SubElement(clip, "AdjustmentLayer")
            adj.text = "true"

        media_path = str(getattr(mat, "path", "") or "")
        if not is_adjust:
            source = ET.SubElement(clip, "Source")
            media_id = take_id()
            ET.SubElement(source, "Media", {"ObjectRef": media_id})
            media = ET.SubElement(root, "Media", {"ObjectID": media_id})
            file_node = ET.SubElement(media, "ActualMediaFilePath")
            file_node.text = media_path
            original = ET.SubElement(source, "OriginalDuration")
            original.text = str(us_to_ticks(getattr(mat, "duration", 0) or 0))

        master_id = take_id()
        ET.SubElement(sub, "MasterClip", {"ObjectRef": master_id})
        master = ET.SubElement(root, "MasterClip", {"ObjectID": master_id})
        master_name = ET.SubElement(master, "Name")
        master_name.text = str(getattr(mat, "material_name", "") or (Path(media_path).name if media_path else "clip"))
        if is_adjust:
            flag = ET.SubElement(master, "IsAdjustmentLayer")
            flag.text = "true"
