"""Read a Premiere Pro ``.prproj`` project into a FreeCut project.

A ``.prproj`` is gzip-compressed XML::

    <PremiereData Version="3">
      <VideoTrackGroup ObjectID="77" ClassID="…">
        <TrackGroup><Tracks><Track Index="0" ObjectURef="<uuid>"/></Tracks>
          <FrameRate>8475667200</FrameRate>
        </TrackGroup>
        …
      <VideoClipTrack ObjectUID="<uuid>" …>
        <ClipItems><TrackItems><TrackItem Index="0" ObjectRef="99"/></TrackItems>
      <VideoClipTrackItem ObjectID="99" …>
        <ClipTrackItem><TrackItem><Start>…</Start><End>…</End></TrackItem>
          <SubClip ObjectRef="118"/></ClipTrackItem>
      <SubClip ObjectID="118"><Clip ObjectRef="132"/>
      <VideoClip ObjectID="132"><Clip><Source ObjectRef="64"/>
          <InPoint>…</InPoint><OutPoint>…</OutPoint></Clip>
      <VideoMediaSource ObjectID="64"><Media ObjectURef="<uuid>"/>
      <Media ObjectUID="<uuid>"><MediaPath>…</MediaPath>

Two reference styles coexist: ``ObjectID``/``ObjectRef`` are per-file integers,
``ObjectUID``/``ObjectURef`` are GUIDs.  Everything is a tree of *containers*
with a ``<Clip>`-style payload, so the reader indexes both spaces and then walks
``track → trackitem → subclip → clip → source → media``.

Time is in **ticks**, 254 016 000 000 per second (verified two independent ways,
see ``docs/REPORT.md`` §3.3); ``<FrameRate>`` stores *ticks per frame*
(8 475 667 200 ⇒ 30000/1001 fps).
"""

from __future__ import annotations

import gzip
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TICKS_PER_SECOND = 254_016_000_000


def ticks_to_us(ticks: int) -> int:
    return int(round(ticks * 1_000_000 / TICKS_PER_SECOND))


def us_to_ticks(us: int) -> int:
    return int(round(us * TICKS_PER_SECOND / 1_000_000))


def ticks_per_frame_to_fps(tpf: int) -> float:
    if tpf <= 0:
        return 30.0
    return TICKS_PER_SECOND / float(tpf)


def _text(elem: Optional[ET.Element], path: str) -> Optional[str]:
    if elem is None:
        return None
    found = elem.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _num(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


@dataclass
class TrackItem:
    object_id: str
    kind: str                 # "video" | "audio"
    name: str
    start_us: int
    end_us: int
    in_us: int
    out_us: int
    media_path: str
    is_adjustment: bool = False
    media_duration_us: int = 0


@dataclass
class Sequence:
    name: str
    width: int
    height: int
    fps: float
    duration_us: int
    video_tracks: List[List[TrackItem]] = field(default_factory=list)
    audio_tracks: List[List[TrackItem]] = field(default_factory=list)


class PrprojError(RuntimeError):
    pass


class Prproj:
    """A parsed ``.prproj`` with both reference spaces indexed."""

    def __init__(self, xml_text: str):
        try:
            self.root = ET.fromstring(xml_text)
        except ET.ParseError as exc:  # pragma: no cover - malformed input
            raise PrprojError(f"不是合法的 prproj XML: {exc}") from exc
        self.by_id: Dict[str, ET.Element] = {}
        self.by_uid: Dict[str, ET.Element] = {}
        for elem in self.root.iter():
            oid = elem.get("ObjectID")
            if oid:
                self.by_id[oid] = elem
            uid = elem.get("ObjectUID")
            if uid:
                self.by_uid[uid] = elem

    # -- loading ------------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "Prproj":
        with open(path, "rb") as fh:
            data = fh.read()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return cls(data.decode("utf-8", "replace"))

    @property
    def version(self) -> str:
        return self.root.get("Version", "?") if self.root is not None else "?"

    # -- lookup -------------------------------------------------------------
    def resolve(self, elem: Optional[ET.Element]) -> Optional[ET.Element]:
        """Follow an ``ObjectRef`` / ``ObjectURef`` attribute on ``elem``."""
        if elem is None:
            return None
        ref = elem.get("ObjectRef")
        if ref:
            return self.by_id.get(ref)
        uref = elem.get("ObjectURef")
        if uref:
            return self.by_uid.get(uref)
        return None

    def _source_path(self, source: Optional[ET.Element]) -> Tuple[str, int]:
        if source is None:
            return "", 0
        # NB: never use ``a or b`` on Elements - an element with no children is
        # falsy, so ``<Media ObjectURef="…"/>`` would be skipped.
        ref = source.find("MediaSource/Media")
        if ref is None:
            ref = source.find("Media")
        media = self.resolve(ref)
        if media is None:
            return "", _num(_text(source, "OriginalDuration")) or 0
        path = None
        for tag in ("ActualMediaFilePath", "MediaPath", "FilePath", "Path"):
            node = media.find(tag)
            if node is not None and node.text and node.text.strip():
                path = node.text.strip()
                break
        if path is None:
            for node in media.iter():
                if node.tag.endswith("FilePath") and node.text and node.text.strip():
                    if node.text.strip().isdigit():
                        continue
                    path = node.text.strip()
                    break
        duration = _num(_text(source, "OriginalDuration")) or 0
        return path or "", duration

    def _item_from_ref(self, ref: Optional[ET.Element], kind: str) -> Optional[TrackItem]:
        item = self.resolve(ref)
        if item is None:
            return None
        start = _num(_text(item, "ClipTrackItem/TrackItem/Start"))
        end = _num(_text(item, "ClipTrackItem/TrackItem/End"))
        if start is None or end is None or end <= start:
            return None
        sub = self.resolve(item.find("ClipTrackItem/SubClip"))
        clip = self.resolve(sub.find("Clip")) if sub is not None else None
        in_pt = _num(_text(clip, "Clip/InPoint")) or 0
        out_pt = _num(_text(clip, "Clip/OutPoint")) or 0
        if out_pt <= in_pt:
            out_pt = in_pt + (end - start)
        master = self.resolve(sub.find("MasterClip")) if sub is not None else None
        name = _text(master, "Name") or ""
        is_adjust = (_text(clip, "AdjustmentLayer") or "").lower() == "true" or \
                    (_text(master, "IsAdjustmentLayer") or "").lower() == "true"
        source = self.resolve(clip.find("Clip/Source")) if clip is not None else None
        if source is None and clip is not None:
            source = self.resolve(clip.find("Source"))
        path, media_dur = self._source_path(source)
        if not name and path:
            name = os.path.basename(path)
        return TrackItem(
            object_id=item.get("ObjectID") or "",
            kind=kind,
            name=name,
            start_us=ticks_to_us(start),
            end_us=ticks_to_us(end),
            in_us=ticks_to_us(in_pt),
            out_us=ticks_to_us(out_pt),
            media_path=path,
            is_adjustment=is_adjust,
            media_duration_us=ticks_to_us(media_dur),
        )

    def _track_items(self, track_group: ET.Element, kind: str) -> List[List[TrackItem]]:
        out: List[List[TrackItem]] = []
        tracks_node = track_group.find("TrackGroup/Tracks")
        if tracks_node is None:
            return out
        entries = sorted(tracks_node.findall("Track"), key=lambda e: int(e.get("Index", "0") or 0))
        for entry in entries:
            track = self.resolve(entry)
            if track is None:
                continue
            items: List[TrackItem] = []
            track_items = track.find("ClipItems/TrackItems")
            if track_items is None:
                track_items = track.find("ClipTrack/ClipItems/TrackItems")
            if track_items is None:
                track_items = track.find(".//ClipItems/TrackItems")
            if track_items is not None:
                for ref in sorted(track_items.findall("TrackItem"),
                                  key=lambda e: int(e.get("Index", "0") or 0)):
                    item = self._item_from_ref(ref, kind)
                    if item is not None:
                        items.append(item)
            out.append(items)
        return out

    # -- sequences ----------------------------------------------------------
    def sequences(self) -> List[Sequence]:
        result: List[Sequence] = []
        seen: set = set()
        for seq_elem in self.root.iter("Sequence"):
            uid = seq_elem.get("ObjectUID")
            # ``<Sequence ObjectURef="…"/>`` elements are *references*; only the
            # definition carries an ObjectUID (and the children)
            if not uid or uid in seen:
                continue
            seen.add(uid)
            name = _text(seq_elem, "Name") or "Sequence"
            # <Sequence><TrackGroups><TrackGroup Index="0">
            #     <First>「媒体类型 GUID」</First><Second ObjectRef="77"/>
            # — a two-column map: media-type GUID → Video/AudioTrackGroup
            groups = []
            for entry in seq_elem.findall("TrackGroups/TrackGroup"):
                resolved = self.resolve(entry.find("Second"))
                if resolved is not None:
                    groups.append(resolved)
            vgroup = next((g for g in groups if g.tag == "VideoTrackGroup"), None)
            agroup = next((g for g in groups if g.tag == "AudioTrackGroup"), None)
            frame_rect = _text(vgroup, "FrameRect") or _text(seq_elem, "FrameRect") or ""
            width = height = 0
            if frame_rect:
                parts = [p for p in frame_rect.replace(",", " ").split() if p.strip()]
                if len(parts) >= 4:
                    width, height = int(float(parts[2])), int(float(parts[3]))
            fps = 0.0
            for group in (vgroup, agroup):
                tpf = _num(_text(group, "TrackGroup/FrameRate"))
                if tpf:
                    fps = ticks_per_frame_to_fps(tpf)
                    break
            video_tracks = self._track_items(vgroup, "video") if vgroup is not None else []
            audio_tracks = self._track_items(agroup, "audio") if agroup is not None else []
            ends = [it.end_us for t in video_tracks + audio_tracks for it in t]
            result.append(
                Sequence(
                    name=name,
                    width=width,
                    height=height,
                    fps=fps or 30.0,
                    duration_us=max(ends) if ends else 0,
                    video_tracks=video_tracks,
                    audio_tracks=audio_tracks,
                )
            )
        return result


def import_project(path: str, sequence_index: int = 0):
    """Convert a ``.prproj`` into a :class:`freecut.model.Project`."""
    from . import builder
    from .model import AudioMaterial, Segment, Timerange, Track, VideoMaterial

    doc = Prproj.load(path)
    seqs = doc.sequences()
    if not seqs:
        raise PrprojError("这个工程里没有序列（可能只是项目设置，没有时间线）")
    if sequence_index >= len(seqs):
        raise PrprojError(f"只有 {len(seqs)} 个序列，索引 {sequence_index} 越界")
    seq = seqs[sequence_index]

    project = builder.new_project(seq.width or 1920, seq.height or 1080, seq.fps, name=seq.name)
    materials: Dict[str, object] = {}

    def material_for(item: TrackItem, kind: str):
        key = item.media_path or f"{item.name}#{item.object_id}"
        if key in materials:
            return materials[key]
        cls = VideoMaterial if kind == "video" else AudioMaterial
        mat = cls(
            path=item.media_path or "",
            duration=item.media_duration_us or 0,
            width=seq.width if kind == "video" else 0,
            height=seq.height if kind == "video" else 0,
            has_audio=kind == "video",
            material_name=item.name,
        )
        project.add_material(mat)
        materials[key] = mat
        return mat

    skipped_adjustment = 0
    adjustments: List[TrackItem] = []
    for kind, groups in (("video", seq.video_tracks), ("audio", seq.audio_tracks)):
        for items in groups:
            usable = []
            for it in items:
                if it.is_adjustment:
                    adjustments.append(it)
                else:
                    usable.append(it)
            if not usable:
                continue
            track = Track(type=kind)
            project.tracks.append(track)
            for item in usable:
                mat = material_for(item, kind)
                track.segments.append(
                    Segment(
                        material_id=mat.id,
                        target_timerange=Timerange(item.start_us, max(1, item.end_us - item.start_us)),
                        source_timerange=Timerange(item.in_us, max(1, item.out_us - item.in_us)),
                    )
                )

    # 调节图层：PR 里是一等公民，FreeCut 也支持；但 PR 的 Lumetri 参数我们还没解析，
    # 所以先按"中性"导入，保留时间范围，用户可以在 FreeCut 里再调
    from .model import AdjustmentMaterial

    for item in adjustments:
        if item.kind != "video":
            continue
        amat = AdjustmentMaterial(adjust={}, material_name=item.name)
        project.add_material(amat)
        track = Track(type="video")
        last_video = -1
        for i, t in enumerate(project.tracks):
            if t.type == "video":
                last_video = i
        project.tracks.insert(last_video + 1, track)
        track.segments.append(
            Segment(
                material_id=amat.id,
                target_timerange=Timerange(item.start_us, max(1, item.end_us - item.start_us)),
                source_timerange=Timerange(0, max(1, item.end_us - item.start_us)),
                is_adjustment=True,
            )
        )
    skipped_adjustment = 0
    return project, {"sequence": seq.name, "fps": seq.fps,
                     "adjustment": len([a for a in adjustments if a.kind == "video"]),
                     "skipped_adjustment": skipped_adjustment}
