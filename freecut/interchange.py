"""Interchange export for Premiere Pro (and other NLEs).

Premiere Pro imports **Final Cut Pro 7 XML** (``<xmeml>``) and FCPXML.  We write
the former: it is a plain, well-documented XML dialect that PR, DaVinci Resolve
and Final Cut all understand, so a FreeCut timeline can be finished in a real
NLE without us owning a copy of it.

The mapping is the obvious one:

============================  ==========================
FreeCut                       xmeml
============================  ==========================
``Project.canvas``            ``sequence/media/video/format``
``Project.fps``               ``rate/timebase``
``Segment.target_timerange``  ``clipitem/start`` / ``end`` (frames)
``Segment.source_timerange``  ``clipitem/in`` / ``out`` (frames)
``VideoMaterial.path``        ``file/pathurl``
audio tracks                  ``media/audio/track``
============================  ==========================

Text/titles are *not* representable in a portable way; export them as SRT next
to the XML (``subtitles.write_srt``) and re-apply in the target NLE.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence
from xml.etree import ElementTree as ET
from xml.dom import minidom

from .model import AudioMaterial, ImageMaterial, Project, VideoMaterial


def _rate(parent: ET.Element, fps: float) -> ET.Element:
    r = ET.SubElement(parent, "rate")
    tb = max(1, int(round(fps)))
    ET.SubElement(r, "timebase").text = str(tb)
    ET.SubElement(r, "ntsc").text = "FALSE"
    return r


def _frames(us: int, fps: float) -> int:
    return max(0, int(round(us / 1_000_000.0 * fps)))


def _pathurl(path: str) -> str:
    """``file://localhost`` URI with percent-encoded UTF-8 (FCP7 spec)."""
    from urllib.parse import quote

    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p  # drive letters: C:/x -> /C:/x
    return "file://localhost" + quote(p, safe="/:")


def _file_element(parent: ET.Element, path: str, info: dict, file_id: str) -> ET.Element:
    f = ET.SubElement(parent, "file", id=file_id)
    ET.SubElement(f, "name").text = os.path.basename(path)
    ET.SubElement(f, "pathurl").text = _pathurl(path)
    _rate(f, info.get("fps", 30.0))
    ET.SubElement(f, "duration").text = str(int(info.get("duration_frames", 0)))
    media = ET.SubElement(f, "media")
    video = ET.SubElement(media, "video")
    sc = ET.SubElement(video, "samplecharacteristics")
    ET.SubElement(sc, "width").text = str(int(info.get("width", 0) or 0))
    ET.SubElement(sc, "height").text = str(int(info.get("height", 0) or 0))
    if info.get("has_audio", True):
        audio = ET.SubElement(media, "audio")
        asc = ET.SubElement(audio, "samplecharacteristics")
        ET.SubElement(asc, "depth").text = "16"
        ET.SubElement(asc, "samplerate").text = "48000"
        ET.SubElement(audio, "channelcount").text = "2"
    return f


def _basic_motion(clip: ET.Element, seg, project: Project) -> None:
    """Write transform + opacity + crop as FCP7 filters.

    Premiere imports these as Motion / Opacity / Crop, which is exactly the
    subset FreeCut's own transform model can express.  Without them every clip
    lands at 100% in the corner and a split-screen export looks nothing like
    the project.
    """
    clip_ = getattr(seg, "clip", None)
    scale_x = float(getattr(clip_, "scale_x", 1.0) or 1.0) if clip_ is not None else 1.0
    scale_y = float(getattr(clip_, "scale_y", 1.0) or 1.0) if clip_ is not None else 1.0
    scale = (scale_x + scale_y) / 2.0
    pos_x = float(getattr(clip_, "tx", 0.0) or 0.0) if clip_ is not None else 0.0
    pos_y = float(getattr(clip_, "ty", 0.0) or 0.0) if clip_ is not None else 0.0
    rot = float(getattr(clip_, "rotation", 0.0) or 0.0) if clip_ is not None else 0.0
    opacity = float(getattr(clip_, "alpha", 1.0) or 1.0) if clip_ is not None else 1.0

    if abs(scale - 1.0) > 1e-6 or abs(pos_x) > 1e-6 or abs(pos_y) > 1e-6 or abs(rot) > 1e-6:
        filt = ET.SubElement(clip, "filter")
        eff = ET.SubElement(filt, "effect")
        ET.SubElement(eff, "name").text = "Basic Motion"
        ET.SubElement(eff, "effectid").text = "basic"
        ET.SubElement(eff, "effectcategory").text = "motion"
        ET.SubElement(eff, "effecttype").text = "motion"
        ET.SubElement(eff, "mediatype").text = "video"
        for pid, pname, value in (
            ("scale", "Scale", f"{scale * 100.0:.4f}"),
            ("center", "Center", f"{pos_x:.4f} {pos_y:.4f}"),
            ("rotation", "Rotation", f"{rot:.4f}"),
        ):
            param = ET.SubElement(eff, "parameter")
            ET.SubElement(param, "parameterid").text = pid
            ET.SubElement(param, "name").text = pname
            ET.SubElement(param, "value").text = value

    if opacity < 0.9999:
        filt = ET.SubElement(clip, "filter")
        eff = ET.SubElement(filt, "effect")
        ET.SubElement(eff, "name").text = "Opacity"
        ET.SubElement(eff, "effectid").text = "opacity"
        ET.SubElement(eff, "effectcategory").text = "motion"
        ET.SubElement(eff, "effecttype").text = "motion"
        ET.SubElement(eff, "mediatype").text = "video"
        param = ET.SubElement(eff, "parameter")
        ET.SubElement(param, "parameterid").text = "opacity"
        ET.SubElement(param, "name").text = "Opacity"
        ET.SubElement(param, "value").text = f"{opacity * 100.0:.2f}"

    crop = (mat.extra.get("crop") if (mat := project.material(seg.material_id)) else None)
    if crop:
        filt = ET.SubElement(clip, "filter")
        eff = ET.SubElement(filt, "effect")
        ET.SubElement(eff, "name").text = "Crop"
        ET.SubElement(eff, "effectid").text = "crop"
        ET.SubElement(eff, "effectcategory").text = "motion"
        ET.SubElement(eff, "effecttype").text = "motion"
        ET.SubElement(eff, "mediatype").text = "video"
        for pid, pname, key in (("left", "Left", "left"), ("right", "Right", "right"),
                                ("top", "Top", "top"), ("bottom", "Bottom", "bottom")):
            param = ET.SubElement(eff, "parameter")
            ET.SubElement(param, "parameterid").text = pid
            ET.SubElement(param, "name").text = pname
            ET.SubElement(param, "value").text = f"{float(crop.get(key, 0.0)) * 100.0:.2f}"



def _text_generatoritem(parent: ET.Element, project: Project, seg, mat,
                        fps: float, node_id: str) -> ET.Element:
    """A text clip as ``<generatoritem>`` with a ``Text`` effect."""
    item = ET.SubElement(parent, "generatoritem", id=node_id)
    content = str(mat.get("content", "") or "")
    ET.SubElement(item, "name").text = content[:40] or "Text"
    duration = _frames(seg.target_timerange.duration, fps)
    ET.SubElement(item, "duration").text = str(duration)
    _rate(item, fps)
    start = _frames(seg.target_timerange.start, fps)
    ET.SubElement(item, "start").text = str(start)
    ET.SubElement(item, "end").text = str(start + duration)
    ET.SubElement(item, "in").text = "0"
    ET.SubElement(item, "out").text = str(duration)

    effect = ET.SubElement(item, "effect")
    ET.SubElement(effect, "name").text = "Text"
    ET.SubElement(effect, "effectid").text = "Text"
    ET.SubElement(effect, "effectcategory").text = "Text"
    ET.SubElement(effect, "effecttype").text = "generator"
    ET.SubElement(effect, "mediatype").text = "video"

    params = (
        ("str", "Text", content),
        ("fontname", "Font", str(mat.get("font_name", "") or "")),
        ("fontsize", "Size", str(mat.get("font_size", 0) or "")),
        ("fontcolor", "Color", str(mat.get("text_color", "") or "")),
    )
    for pid, label, value in params:
        parameter = ET.SubElement(effect, "parameter")
        ET.SubElement(parameter, "parameterid").text = pid
        ET.SubElement(parameter, "name").text = label
        ET.SubElement(parameter, "value").text = value
    return item

def build_fcp7(project: Project, sequence_name: Optional[str] = None) -> ET.Element:
    fps = float(project.fps or 30.0)
    total_frames = _frames(project.duration(), fps)

    root = ET.Element("xmeml", version="4")
    seq = ET.SubElement(root, "sequence", id="sequence-1")
    ET.SubElement(seq, "name").text = sequence_name or project.name or "FreeCut"
    ET.SubElement(seq, "duration").text = str(total_frames)
    _rate(seq, fps)

    tc = ET.SubElement(seq, "timecode")
    _rate(tc, fps)
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"

    media = ET.SubElement(seq, "media")
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    ET.SubElement(sc, "width").text = str(int(project.canvas.width))
    ET.SubElement(sc, "height").text = str(int(project.canvas.height))
    ET.SubElement(sc, "anamorphic").text = "FALSE"
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    ET.SubElement(sc, "fielddominance").text = "none"
    _rate(sc, fps)

    file_seq = 0
    clip_seq = 0

    # one xmeml track per FreeCut video track, bottom first
    # text 也走视频轨：FCP7 里文字是 generatoritem，但仍然挂在 <video><track> 下
    video_tracks = [t for t in project.tracks if t.type in ("video", "sticker", "text")]
    for track in video_tracks:
        xm_track = ET.SubElement(video, "track")
        for seg in track.sorted_segments():
            mat = project.material(seg.material_id)
            path = getattr(mat, "path", "") if mat is not None else ""
            if not path:
                # 文字没有文件，FCP7 用 generatoritem 表达（PR/FCP 都认）。
                # 少写这一段，XML 一来一回就会把字幕全丢掉。
                if getattr(mat, "type", "") == "text":
                    clip_seq += 1
                    _text_generatoritem(xm_track, project, seg, mat, fps, f"generatoritem-{clip_seq}")
                continue
            clip_seq += 1
            clip = ET.SubElement(xm_track, "clipitem", id=f"clipitem-{clip_seq}")
            name = (mat.get("material_name") or os.path.basename(path)) if mat is not None else path
            ET.SubElement(clip, "name").text = str(name)
            ET.SubElement(clip, "duration").text = str(_frames(seg.target_timerange.duration, fps))
            _rate(clip, fps)
            start = _frames(seg.target_timerange.start, fps)
            end = start + _frames(seg.target_timerange.duration, fps)
            ET.SubElement(clip, "start").text = str(start)
            ET.SubElement(clip, "end").text = str(end)
            ET.SubElement(clip, "in").text = str(_frames(seg.source_timerange.start, fps))
            ET.SubElement(clip, "out").text = str(
                _frames(seg.source_timerange.start + seg.source_timerange.duration, fps)
            )
            _basic_motion(clip, seg, project)
            if abs(seg.speed - 1.0) > 1e-6:
                # PR reads this as a time-remap hint
                filt = ET.SubElement(clip, "filter")
                eff = ET.SubElement(filt, "effect")
                ET.SubElement(eff, "name").text = "Time Remap"
                ET.SubElement(eff, "effectid").text = "timeremap"
                ET.SubElement(eff, "effectcategory").text = "motion"
                ET.SubElement(eff, "effecttype").text = "motion"
                ET.SubElement(eff, "mediatype").text = "video"
                param = ET.SubElement(eff, "parameter")
                ET.SubElement(param, "parameterid").text = "speed"
                ET.SubElement(param, "name").text = "Speed"
                ET.SubElement(param, "value").text = f"{seg.speed:.6f}"
            file_seq += 1
            info = {
                "fps": fps,
                "duration_frames": _frames(int(mat.get("duration", 0) or 0) if mat else 0, fps),
                "width": int(mat.get("width", 0) or 0) if mat is not None else 0,
                "height": int(mat.get("height", 0) or 0) if mat is not None else 0,
                "has_audio": bool(mat.get("has_audio", True)) if mat is not None else True,
            }
            _file_element(clip, path, info, f"file-{file_seq}")

    # audio
    audio_tracks = [t for t in project.tracks if t.type == "audio"]
    audio_media = None
    for track in audio_tracks:
        if audio_media is None:
            audio_media = ET.SubElement(media, "audio")
            afmt = ET.SubElement(audio_media, "format")
            asc = ET.SubElement(afmt, "samplecharacteristics")
            ET.SubElement(asc, "depth").text = "16"
            ET.SubElement(asc, "samplerate").text = "48000"
        xm_track = ET.SubElement(audio_media, "track")
        for seg in track.sorted_segments():
            mat = project.material(seg.material_id)
            path = getattr(mat, "path", "") if mat is not None else ""
            if not path:
                continue
            clip_seq += 1
            clip = ET.SubElement(xm_track, "clipitem", id=f"clipitem-{clip_seq}")
            ET.SubElement(clip, "name").text = str(seg.material_id)
            ET.SubElement(clip, "duration").text = str(_frames(seg.target_timerange.duration, fps))
            _rate(clip, fps)
            start = _frames(seg.target_timerange.start, fps)
            ET.SubElement(clip, "start").text = str(start)
            ET.SubElement(clip, "end").text = str(start + _frames(seg.target_timerange.duration, fps))
            ET.SubElement(clip, "in").text = str(_frames(seg.source_timerange.start, fps))
            ET.SubElement(clip, "out").text = str(
                _frames(seg.source_timerange.start + seg.source_timerange.duration, fps)
            )
            file_seq += 1
            info = {
                "fps": fps,
                "duration_frames": _frames(int(mat.get("duration", 0) or 0) if mat else 0, fps),
                "width": 0,
                "height": 0,
                "has_audio": True,
            }
            _file_element(clip, path, info, f"file-{file_seq}")
    return root


def to_fcp7_xml(project: Project, sequence_name: Optional[str] = None) -> str:
    root = build_fcp7(project, sequence_name)
    raw = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
    text = pretty.decode("utf-8")
    # minidom writes the declaration itself; add the doctype PR expects
    if "<!DOCTYPE" not in text:
        head, rest = text.split("\n", 1)
        text = head + "\n<!DOCTYPE xmeml>\n" + rest
    return text


def write_fcp7(project: Project, path: str, sequence_name: Optional[str] = None) -> str:
    text = to_fcp7_xml(project, sequence_name)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def summary(project: Project) -> Dict[str, object]:
    fps = float(project.fps or 30.0)
    clips = []
    for track in project.tracks:
        if track.type not in ("video", "audio", "sticker"):
            continue
        for seg in track.sorted_segments():
            mat = project.material(seg.material_id)
            path = getattr(mat, "path", "") if mat is not None else ""
            if not path:
                continue
            clips.append(
                {
                    "track": track.type,
                    "start_frame": _frames(seg.target_timerange.start, fps),
                    "end_frame": _frames(seg.target_timerange.end, fps),
                    "in_frame": _frames(seg.source_timerange.start, fps),
                    "out_frame": _frames(seg.source_timerange.end, fps),
                    "path": path,
                }
            )
    return {"fps": fps, "clips": clips, "duration_frames": _frames(project.duration(), fps)}

# --------------------------------------------------------------------------- #
# reading it back (so the export report can be checked, not just trusted)
# --------------------------------------------------------------------------- #
def read_fcp7(source) -> Dict:
    """Parse our own FCP7 XML back into plain dicts.

    Only the subset this module writes is understood: clip timing plus the
    Basic Motion / Opacity / Crop / Time Remap filters.  Anything else in the
    file is reported as unknown rather than silently ignored.
    """
    if isinstance(source, bytes):
        root = ET.fromstring(source.decode("utf-8"))
    elif isinstance(source, str) and source.lstrip().startswith("<"):
        root = ET.fromstring(source)
    else:
        root = ET.parse(source).getroot()

    def _int(node, name, default=0):
        found = node.find(name)
        try:
            return int(found.text) if found is not None and found.text else default
        except Exception:
            return default

    out: Dict = {"clips": [], "fps": 0.0, "width": 0, "height": 0, "unknown_effects": []}
    seq = root.find(".//sequence")
    rate = seq.find("rate/timebase") if seq is not None else None
    try:
        out["fps"] = float(rate.text) if rate is not None else 0.0
    except Exception:
        out["fps"] = 0.0

    media = root.find(".//media")
    video = media.find("video") if media is not None else None
    if video is not None:
        sc = video.find("format/samplecharacteristics")
        if sc is not None:
            out["width"] = _int(sc, "width")
            out["height"] = _int(sc, "height")

    audio_section = media.find("audio") if media is not None else None
    audio_tracks = set()
    if audio_section is not None:
        for track in audio_section.findall("track"):
            audio_tracks.add(id(track))

    for track in root.iter("track"):
        kind = "audio" if id(track) in audio_tracks else "video"
        for clip in track.findall("clipitem"):
            entry = {
                "kind": kind,
                "name": (clip.findtext("name") or "").strip(),
                "start": _int(clip, "start"),
                "end": _int(clip, "end"),
                "in": _int(clip, "in"),
                "out": _int(clip, "out"),
                "scale": 1.0,
                "cx": 0.0,
                "cy": 0.0,
                "rotation": 0.0,
                "opacity": 1.0,
                "crop": None,
                "speed": 1.0,
            }
            for eff in clip.iter("effect"):
                name = (eff.findtext("name") or "").strip()
                params = {}
                for param in eff.findall("parameter"):
                    pid = (param.findtext("parameterid") or "").strip()
                    params[pid] = (param.findtext("value") or "").strip()
                if name == "Basic Motion":
                    entry["scale"] = float(params.get("scale", "100")) / 100.0
                    center = params.get("center", "0 0").split()
                    if len(center) >= 2:
                        entry["cx"] = float(center[0])
                        entry["cy"] = float(center[1])
                    entry["rotation"] = float(params.get("rotation", "0"))
                elif name == "Opacity":
                    entry["opacity"] = float(params.get("opacity", "100")) / 100.0
                elif name == "Crop":
                    entry["crop"] = {k: float(params.get(k, "0")) / 100.0
                                     for k in ("left", "right", "top", "bottom")}
                elif name == "Time Remap":
                    entry["speed"] = float(params.get("speed", "1"))
                else:
                    out["unknown_effects"].append(name)
            out["clips"].append(entry)
    return out

def from_fcp7_xml(source: str):
    """Read an FCP7 ``.xml`` timeline back into a FreeCut project.

    This is the counterpart of :func:`to_fcp7_xml`, and it also gives us a real
    import path for XML coming out of Premiere / Final Cut / DaVinci — those all
    speak this dialect.  Text clips and colour-only clipitems carry no
    ``pathurl`` and are skipped: we cannot invent media we never had.
    """
    import urllib.parse

    from . import builder

    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = source
    root = ET.fromstring(text.encode("utf-8"))
    seq = root.find("sequence")
    if seq is None:
        raise ValueError("这份 XML 里没有 sequence")

    def _num(elem, tag, default=0):
        node = elem.find(tag)
        if node is None or node.text is None:
            return default
        try:
            return float(node.text.strip())
        except ValueError:
            return default

    rate = seq.find("rate/timebase")
    fps = float(rate.text) if rate is not None and rate.text else 30.0
    sc = seq.find("media/video/format/samplecharacteristics")
    width = int(_num(sc, "width", 1920)) if sc is not None else 1920
    height = int(_num(sc, "height", 1080)) if sc is not None else 1080
    name = (seq.findtext("name") or "XML 导入").strip()

    project = builder.new_project(width or 1920, height or 1080, fps, name=name)
    clip_count = 0

    def _frames_to_us(value: float) -> int:
        return int(round(value / max(fps, 1e-6) * 1_000_000))

    text_count = 0
    for kind, xpath in (("video", "media/video/track"),
                        ("audio", "media/audio/track")):
        for track_index, track in enumerate(seq.findall(xpath)):
            # 文字轨里只有 generatoritem，没有 clipitem——不能因为 clipitem 为空就跳过
            items = track.findall("clipitem") + track.findall("generatoritem")
            if not items:
                continue
            from .model import AudioMaterial, Segment, Timerange, Track, VideoMaterial

            fc_track = Track(type=kind)
            project.tracks.append(fc_track)
            for item in items:
                pathurl = item.findtext("file/pathurl")
                if not pathurl:
                    # generatoritem + Text 效果 = 文字片段，可以把字拿回来
                    value = None
                    for parameter in item.findall("effect/parameter"):
                        if (parameter.findtext("parameterid") or "") == "str":
                            value = parameter.findtext("value")
                    if value is None:
                        continue                  # 纯色/形状：我们做不了，跳过
                    start = _frames_to_us(_num(item, "start"))
                    end = _frames_to_us(_num(item, "end"))
                    if end <= start:
                        continue
                    from .model import Segment as _Seg, Timerange as _TR, Track as _Track

                    if not any(t.type == "text" for t in project.tracks):
                        project.tracks.append(_Track(type="text"))
                    text_track = next(t for t in project.tracks if t.type == "text")
                    from .model import TextMaterial as _Text

                    text_mat = _Text(content=value or "")
                    project.add_material(text_mat)
                    text_track.segments.append(_Seg(
                        material_id=text_mat.id,
                        target_timerange=_TR(start, end - start),
                        source_timerange=_TR(0, end - start)))
                    text_count += 1
                    continue
                path = urllib.parse.unquote(pathurl)
                if path.startswith("file://localhost"):
                    path = path[len("file://localhost"):]
                elif path.startswith("file://"):
                    path = path[len("file://"):]
                start = _frames_to_us(_num(item, "start"))
                end = _frames_to_us(_num(item, "end"))
                if end <= start:
                    continue
                in_us = _frames_to_us(_num(item, "in"))
                out_us = _frames_to_us(_num(item, "out"))
                duration = end - start
                if out_us <= in_us:
                    out_us = in_us + duration
                fname = item.findtext("file/name") or os.path.basename(path) or "clip"
                if kind == "video":
                    mat = VideoMaterial(path=path, duration=out_us - in_us,
                                        width=int(_num(item, "file/media/video/samplecharacteristics/width")),
                                        height=int(_num(item, "file/media/video/samplecharacteristics/height")),
                                        has_audio=False, material_name=fname)
                else:
                    mat = AudioMaterial(path=path, duration=out_us - in_us, material_name=fname)
                project.add_material(mat)
                fc_track.segments.append(
                    Segment(material_id=mat.id,
                            target_timerange=Timerange(start, duration),
                            source_timerange=Timerange(in_us, out_us - in_us)))
                clip_count += 1
        # 空轨丢掉，免得导入后一片空行
    project.tracks = [t for t in project.tracks if t.segments]
    return project, {"sequence": name, "fps": fps, "clips": clip_count, "texts": text_count}
