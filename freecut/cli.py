"""Command line interface::

    python -m freecut info
    python -m freecut probe video.mp4
    python -m freecut demo --out out/demo.mp4
    python -m freecut export project.json --preset 1080p30 --out out.mp4
    python -m freecut still project.json --at 3.5 --out frame.png
    python -m freecut import-draft DRAFT_DIR --out project.json
    python -m freecut gui
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__


def _cmd_info(args) -> int:
    from .media import ffmpeg_path, ffprobe_path, list_encoders

    print(f"FreeCut {__version__}")
    try:
        print("ffmpeg :", ffmpeg_path())
    except Exception as exc:
        print("ffmpeg : 不可用 ->", exc)
        return 1
    fp = ffprobe_path()
    print("ffprobe:", fp or "(未安装，使用 ffmpeg 解析代替)")
    enc = list_encoders()
    interesting = [e for e in enc if any(k in e for k in ("264", "265", "hevc", "mpeg4", "vp9", "av1"))]
    print("视频编码器:", ", ".join(interesting[:16]) or "(无)")
    print("音频编码器:", ", ".join([e for e in enc if e in ("aac", "libmp3lame", "libopus", "pcm_s16le")]))
    return 0


def _cmd_probe(args) -> int:
    from .media import probe

    info = probe(args.path)
    print(json.dumps(info.__dict__, ensure_ascii=False, indent=2))
    return 0


def _cmd_demo(args) -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from examples.make_demo import build_show, gen_media

    media = gen_media(args.media)
    project = build_show(media)
    return _do_export(project, args)


def _cmd_export(args) -> int:
    from .model import Project

    project = Project.load(args.project)
    return _do_export(project, args)


def _do_export(project, args) -> int:
    from .export import PRESETS, ExportPreset, export, with_quality

    preset = PRESETS.get(args.preset)
    if preset is None:
        preset = ExportPreset()
    if getattr(args, "quality", None):
        preset = with_quality(preset, args.quality)
    if getattr(args, "width", None):
        preset.width = args.width
    if getattr(args, "height", None):
        preset.height = args.height
    if getattr(args, "fps", None):
        preset.fps = args.fps
    if getattr(args, "crf", None):
        preset.crf = args.crf
    for attr, name in (("video_bitrate", "bitrate"), ("max_bitrate", "max_bitrate"),
                       ("gop", "gop"), ("audio_bitrate", "audio_bitrate"),
                       ("audio_channels", "channels"), ("profile", "profile"),
                       ("level", "level")):
        value = getattr(args, name, None)
        if value not in (None, "", 0):
            setattr(preset, attr, value)
    if getattr(args, "no_faststart", False):
        preset.faststart = False

    if getattr(args, "gif", False):
        from .export import export_gif

        out = export_gif(project, args.out,
                         width=getattr(args, "width", None) or 480,
                         fps=getattr(args, "fps", None) or 12.0)
        print(f"已导出 GIF: {out}")
        return 0

    if getattr(args, "audio_only", False):
        from .export import export_audio_only

        out = export_audio_only(project, args.out, preset, progress=None)
        print(f"已导出音频: {out}")
        return 0

    def progress(frac: float, msg: str) -> None:
        bar = int(frac * 32)
        sys.stdout.write(f"\r[{'#' * bar}{'.' * (32 - bar)}] {frac * 100:5.1f}% {msg[:52]:52s}")
        sys.stdout.flush()

    variants = getattr(args, "variants", None)
    if variants:
        from .export import export_many

        names = [n.strip() for n in variants.split(",") if n.strip()]
        bad = [n for n in names if n not in PRESETS]
        if bad:
            print("未知预设: " + ", ".join(bad))
            print("可用预设: " + ", ".join(PRESETS))
            return 2
        stem, ext = os.path.splitext(args.out)
        targets = [(f"{stem}_{n}{ext or '.mp4'}", PRESETS[n]) for n in names]
        paths = export_many(project, [(args.out, preset)] + targets, progress,
                            jobs=getattr(args, "jobs", None))
        for path in paths:
            print(f"\n输出: {path}  {os.path.getsize(path)/1024:.0f} KB")
        return 0

    out = export(project, args.out, preset, progress, jobs=getattr(args, "jobs", None))
    print(f"\n输出: {out}")
    if getattr(args, "verify", False):
        from . import audio as _audio
        from .verify import verify

        clips = [c for c in _audio.collect_clips(project) if not c.muted]
        rep = verify(out, expect_audio=bool(clips))
        print()
        print(rep.as_text())
        if not rep.ok:
            return 1
    return 0


def _cmd_still(args) -> int:
    from PIL import Image

    from .model import Project
    from .render import Renderer

    project = Project.load(args.project)
    r = Renderer(project, args.width, args.height)
    try:
        idx = min(max(0, int(round(args.at * r.fps))), r.frame_count - 1)
        frame = r.render_frame(idx)
    finally:
        r.close()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    Image.fromarray(frame).save(args.out)
    print("已保存", args.out)
    return 0


def _cmd_import_draft(args) -> int:
    from .jianying import (load_project_draft, unsupported_animations,
                       unsupported_transitions_for_path, unknown_material_keys,
                       _read_draft_json)

    project = load_project_draft(args.path)
    project.save(args.out)
    missing = unsupported_transitions_for_path(args.path)
    if missing:
        shown = "、".join(missing[:5])
        more = f" 等 {len(missing)} 个" if len(missing) > 5 else ""
        print(f"提示: 以下转场没有对应实现，已按叠化处理：{shown}{more}")
    try:
        _raw = _read_draft_json(args.path)
        missing_anims = unsupported_animations(_raw)
    except Exception:                              # noqa: BLE001
        missing_anims = []
    _missing_materials = unknown_material_keys(_raw) if '_raw' in dir() else {}
    if missing_anims:
        shown = "、".join(missing_anims[:5])
        more = f" 等 {len(missing_anims)} 个" if len(missing_anims) > 5 else ""
        print(f"提示: 以下动画没有实现（多为云端资源包），已忽略：{shown}{more}")
    if _missing_materials:
        parts = [f"{k}({v})" for k, v in sorted(_missing_materials.items())]
        print(f"提示: 以下材料类型没有导入：{', '.join(parts)}")
    print(f"已导入: {len(project.tracks)} 条轨道, {sum(len(t.segments) for t in project.tracks)} 个片段")
    print("已保存", args.out)
    return 0


def _cmd_export_draft(args) -> int:
    from .jianying_export import register_draft, save_draft
    from .model import Project

    project = Project.load(args.project)
    original = project                      # 展平后用原始工程比音频
    if getattr(args, "encrypt", False) and getattr(args, "no_encrypt", False):
        print("--encrypt 和 --no-encrypt 冲突，按明文导出", file=sys.stderr)
    encrypt = bool(getattr(args, "encrypt", False)) and not bool(getattr(args, "no_encrypt", False))
    notes: list = []
    if getattr(args, "flatten", False):
        # 把 FreeCut 自己渲染的东西先烧成一段片，再交给剪映：
        # 画面对得上（所见即所得），代价是剪映里只剩一个片段、不能改结构。
        from .export import PRESETS, export
        from .jianying_export import flatten_project

        os.makedirs(args.root, exist_ok=True)
        flat_dir = os.path.join(args.root, "_freecut_flatten")
        os.makedirs(flat_dir, exist_ok=True)
        flat_mp4 = os.path.join(flat_dir, (args.name or project.name or "flattened") + ".mp4")
        print("展平：先渲染成片 ->", flat_mp4)
        export(project, flat_mp4, PRESETS["720p30"], jobs=getattr(args, "jobs", 0) or None)
        project = flatten_project(project, flat_mp4)
        notes.append("已展平：画面与 FreeCut 渲染结果一致，但剪映里是一个整片段，不能再拆")
    draft_dir = save_draft(project, args.root, name=args.name, encrypt=encrypt,
                           overwrite=args.overwrite, warnings=notes)
    print("已写出剪映草稿:", draft_dir)
    from . import draftreport

    report = draftreport.analyze(project, draft_dir=draft_dir, notes=notes)
    try:
        report.write(os.path.join(draft_dir, "freecut_export_report.txt"))
        print("导出报告: freecut_export_report.txt")
    except Exception as exc:                 # 报告写不出去也不该让导出失败
        print("（导出报告写不出去:", exc, "）")
    if getattr(args, "report", False):
        print()
        print(report.as_text())
    for f in sorted(os.listdir(draft_dir)):
        print(f"   {f}  {os.path.getsize(os.path.join(draft_dir, f))} B")
    for note in notes:
        print("⚠", note)
    if getattr(args, "selfcheck", False):
        from .selfcheck import check_draft

        print()
        rep = check_draft(project, draft_dir,
                          samples=int(getattr(args, "selfcheck_frames", 5) or 5),
                          compare_pixels=not getattr(args, "no_pixel_check", False),
                          original=original)
        print(rep.as_text())
        if not rep.ok:
            return 1
    if args.register:
        root = register_draft(draft_dir)
        print("已注册到剪映工程列表:", root)
    else:
        print("提示: 加 --register 可把它加入剪映首页的工程列表（会先备份 root_meta_info.json）")
    return 0


def _cmd_media_smoke(args) -> int:
    from .qtui.app import media_smoke

    return media_smoke(["freecut", getattr(args, "media", "out/demo.mp4")]) or 0


def _cmd_caps(args) -> int:
    from . import caps

    data = caps.probe()
    print("caps:", caps.summary(data))
    if getattr(args, "save", None):
        caps.save(args.save, data)
        print("caps: 已写入", args.save)
    return 0


def _cmd_audio_smoke(args) -> int:
    from .qtui.app import audio_smoke

    return audio_smoke(["freecut"]) or 0


def _cmd_gui_smoke(args) -> int:
    from .qtui.app import smoke

    return smoke(["freecut"]) or 0


def _cmd_gui(args) -> int:
    """默认开 Qt 版界面；`--tk` 回退到旧的 Tk 版（第 200 轮切换）。"""
    # 位置参数和 --project 都接受：`freecut gui 工程.json` 是用户最自然的写法
    path = getattr(args, "project_opt", None) or getattr(args, "project", None)
    if getattr(args, "tk_shell", False):
        from .gui import main as gui_main

        return gui_main(path) or 0
    try:
        from .qtui.app import main as qt_main
    except Exception as exc:                          # noqa: BLE001
        # 没装 PySide6 的机器仍然能用：退回 Tk，并说明原因
        print(f"Qt 界面不可用（{exc}），改用 Tk 界面", file=sys.stderr)
        from .gui import main as gui_main

        return gui_main(path) or 0
    return qt_main(["freecut"] + ([path] if path else [])) or 0


def _cmd_autocut(args) -> int:
    from . import autocut

    opts = autocut.AutoCutOptions(
        min_clip_s=args.min_clip,
        max_clip_s=args.max_clip,
        transition=args.transition,
        fps=args.fps,
    )
    if args.mode == "beat":
        if not args.music:
            raise SystemExit("--mode beat 需要 --music")
        project = autocut.build_beat_project(args.media, args.music, opts, beats_per_cut=args.beats_per_cut)
    else:
        project = autocut.build_scene_project(args.media, opts)

    project.save(args.out)
    n = sum(len(t.segments) for t in project.tracks)
    print(f"自动剪辑完成: {n} 个片段 / {project.duration()/1e6:.2f}s -> {args.out}")
    if args.export:
        from .export import ExportPreset, export

        export(project, args.export, ExportPreset(project.canvas.width, project.canvas.height, project.fps))
        print("导出:", args.export)
    return 0


def _cmd_subtitle(args) -> int:
    from . import subtitles
    from .model import Project

    if args.action == "replace":
        return _cmd_subtitle_replace(args)
    if args.action == "shift":
        return _cmd_subtitle_shift(args)
    if not args.srt:
        print(f"{args.action} 需要给出字幕文件路径", file=sys.stderr)
        return 2

    if args.action == "export":
        project = Project.load(args.project)
        subs = subtitles.extract_subtitles(project)
        subtitles.write_srt(subs, args.srt)
        print(f"已导出 {len(subs)} 条字幕 -> {args.srt}")
        return 0

    # import
    project = Project.load(args.project)
    if args.srt.lower().endswith(".vtt"):
        subs = subtitles.parse_vtt(args.srt)
    else:
        subs = subtitles.parse_srt(args.srt)
    if not subs:
        print("没有解析到字幕")
        return 1
    style = {
        "size": args.size,
        "y": args.y,
        "color": args.color,
        "stroke": args.stroke,
        "highlight_color": args.highlight,
    }
    created = subtitles.add_subtitles(project, subs, style=style, karaoke=not args.no_karaoke)
    if args.out:
        project.save(args.out)
        print(f"已导入 {len(created)} 条字幕 -> {args.out}")
    else:
        project.save(args.project)
        print(f"已导入 {len(created)} 条字幕 -> {args.project}")
    return 0


def _cmd_export_xml(args) -> int:
    from . import interchange, subtitles
    from .model import Project

    project = Project.load(args.project)
    path = interchange.write_fcp7(project, args.out, sequence_name=args.name)
    print("已写出 FCP7 XML（Premiere / Resolve / FCP 可导入）:", path)
    info = interchange.summary(project)
    print(f"   {info['fps']:g} fps / {info['duration_frames']} 帧 / {len(info['clips'])} 个片段")
    if args.srt:
        subs = subtitles.extract_subtitles(project)
        subtitles.write_srt(subs, args.srt)
        print(f"   同时导出字幕 {len(subs)} 条 -> {args.srt}")

    from . import draftreport

    report = draftreport.analyze_xml(project, xml_path=path)
    report_path = os.path.splitext(path)[0] + "_report.txt"
    try:
        report.write(report_path)
        print("   导出报告:", os.path.basename(report_path))
    except Exception as exc:
        print("   （导出报告写不出去:", exc, "）")
    if getattr(args, "report", False):
        print()
        print(report.as_text())
    if getattr(args, "verify", False):
        check = draftreport.verify_xml(project, path)
        print()
        print(check.as_text())
        if not check.ok:
            return 1
    return 0


def _cmd_autosub(args) -> int:
    from . import asr, subtitles
    from .model import Project

    try:
        data = asr.transcribe(args.media, language=args.language, model=args.model,
                              device=args.device, threads=args.threads,
                              prompt=args.prompt or None)
    except asr.ASRError as exc:
        print("语音识别失败:", exc)
        return 1
    subs = asr.segments_to_subtitles(data, offset_us=int(args.offset * 1_000_000))
    if not subs:
        print("没有识别到语音")
        return 1
    print(f"识别到 {len(subs)} 条字幕:")
    for s in subs[:args.preview]:
        print(f"  [{s.start_us/1e6:7.2f} -> {s.end_us/1e6:7.2f}] {s.text}")

    if args.srt:
        subtitles.write_srt(subs, args.srt)
        print("已写出 SRT:", args.srt)

    project = Project.load(args.project) if args.project else None
    if project is None:
        from . import builder

        project = builder.new_project(1920, 1080, 30, name="自动字幕")
    style = {"size": args.size, "y": args.y, "color": args.color,
             "stroke": args.stroke, "highlight_color": args.highlight}
    subtitles.add_subtitles(project, subs, style=style, karaoke=not args.no_karaoke)
    project.save(args.out)
    print("已写入工程:", args.out)
    return 0


def _cmd_lut(args) -> int:
    from . import luts

    if args.action == "make":
        return _cmd_lut_make(args)
    if args.action == "refresh":
        entries = luts.library(refresh=True)
    else:
        entries = luts.library(refresh=args.refresh)
    print(luts.summary(entries))
    print("\n扫描目录:")
    for root in luts.default_roots():
        print("  " + root)
    return 0


def _cmd_import_prproj(args) -> int:
    from . import prproj

    try:
        project, info = prproj.import_project(args.path, sequence_index=args.sequence)
    except prproj.PrprojError as exc:
        print("导入失败:", exc)
        return 1
    project.save(args.out)
    segs = sum(len(t.segments) for t in project.tracks)
    print(f"已导入 PR 工程: 序列 {info['sequence']!r} / {info['fps']:.3f} fps / "
          f"{len(project.tracks)} 轨 / {segs} 个片段 -> {args.out}")
    if info.get("skipped_adjustment"):
        print(f"   （跳过 {info['skipped_adjustment']} 个调整图层：FreeCut 暂无该功能）")
    return 0


def _cmd_verify(args) -> int:
    from .verify import verify

    rc = 0
    for path in args.paths:
        rep = verify(path, expect_duration_s=args.expect_duration,
                     expect_audio=getattr(args, "expect_audio", None))
        print(rep.as_text())
        print()
        if not rep.ok:
            rc = 1
    return rc


def _cmd_doctor(args) -> int:
    from . import relink
    from .model import Project

    project = Project.load(args.project)
    roots = list(args.search or [])
    items = relink.suggest(project, roots) if roots else relink.missing_media(project)
    print(relink.format_report(items))
    if not items:
        return 0
    if args.apply:
        if not roots:
            print("\n--apply 需要同时给 --search <目录> 才能找候选")
            return 1
        changed = relink.auto_relink(project, roots)
        if changed:
            project.save(args.out or args.project)
            print(f"\n已重链 {changed} 个素材 -> {args.out or args.project}")
        else:
            print("\n没有可以唯一确定的候选，未改动")
        still = relink.missing_media(project)
        if still:
            print(f"仍有 {len(still)} 个素材缺失")
            return 1
    return 0


def _cmd_fromscript(args) -> int:
    import tempfile

    from . import scriptfilm, tts

    # utf-8-sig：记事本 / Set-Content 存的文案文件带 BOM，用 utf-8 读会把
    # BOM 当成第一个字符，字幕第一句前面就多一个看不见的字
    with open(args.script, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    work = args.work or tempfile.mkdtemp(prefix="freecut-tts-")
    stats = scriptfilm.build_script_project(
        text, args.media or [], work_dir=work,
        voice=args.voice or "", rate=int(args.rate or 0),
        width=args.width, height=args.height, fps=args.fps,
        use_tts=not args.no_tts,
    )
    print(scriptfilm.summary(stats))
    if not stats["used_tts"] and not args.no_tts:
        print("提示: 没有用上语音合成（本机没有可用语音），已按估算时长排布")
    project = stats["project"]
    project.save(args.out)
    print("已写出工程:", args.out)
    if args.render:
        from . import export

        out = export.export(project, args.render, preset=export.PRESETS[args.preset],
                            jobs=args.jobs)
        print("已导出视频:", out)
    return 0


def _cmd_tts(args) -> int:
    from . import tts

    if args.list:
        print(tts.speaker_summary())
        return 0
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    if not text:
        print("没有文本")
        return 1
    ok, dur = tts.ensure_wav(text, args.out, voice=args.voice or "", rate=int(args.rate or 0))
    if not ok:
        print("语音合成不可用（已按估算时长返回）")
        return 1
    print(f"已合成: {args.out}  {dur/1e6:.2f} 秒")
    return 0


def _cmd_showcase(args) -> int:
    import tempfile

    from . import showcase

    work = args.work or os.path.join(tempfile.gettempdir(), "freecut-showcase")
    stats = showcase.build_showcase(work, width=args.width, height=args.height,
                                    fps=args.fps, with_asr=args.asr)
    print(showcase.summary(stats))
    if not args.asr:
        pass
    elif "asr" in stats:
        info = stats["asr"]
        print(f"  说: {info['spoken']}")
        print(f"  听: {info['heard']}  (字符重合 {info['similarity']*100:.0f}%, {info['words']} 词)")
    else:
        print("  ⚠ 这台机器没有可用的语音识别后端，已跳过 ASR 环节")
    project = stats["project"]
    project.save(args.out)
    print("已写出工程:", args.out)
    if args.draft:
        result = showcase.export_and_check(project, args.draft, name=args.draft_name,
                                           samples=args.selfcheck_frames)
        print("已写出草稿:", result["draft_dir"])
        for note in result["warnings"]:
            print("⚠", note)
        print(result["report"].as_text())
        if not result["report"].ok:
            return 1
    if args.render:
        from .export import PRESETS, export

        out = export(project, args.render, PRESETS[args.preset], jobs=args.jobs or None)
        print("已导出:", out)
        if args.verify:
            from .verify import verify

            print(verify(out).as_text())
    return 0


def _cmd_jyprobe(args) -> int:
    from . import jyprobe
    from .model import Project

    expected = None
    if args.project:
        expected = Project.load(args.project).duration()
    if not args.no_register:
        # 剪映只扫描它自己工程列表里的草稿，没注册过的目录它根本不看
        from .jianying_export import register_draft

        try:
            register_draft(args.draft)
            print("已把草稿注册进剪映工程列表")
        except Exception as exc:
            print("注册失败（继续）:", exc)
    saved = None
    if not args.no_perturb:
        # 把剪映会自己算的两个字段清零：只有真读了时间线才填得回来
        saved = jyprobe.perturb_meta(args.draft)
        if saved:
            print("已把 tm_duration / 素材体积清零（用于判定剪映是否真的解析了草稿）")
    print("探针：启动剪映并观察它是否真的读了这个草稿…")
    try:
        result = jyprobe.probe(args.draft, expected_duration_us=expected,
                               exe=args.exe, timeout=args.timeout)
    finally:
        if saved:
            jyprobe.restore_meta(args.draft, saved)
    print()
    print(result.as_text())
    return 0 if result.parsed_timeline else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("freecut", description="FreeCut - 免费开源的简易视频剪辑工具")
    p.add_argument("--version", action="version", version=f"FreeCut {__version__}")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("info", help="显示环境信息").set_defaults(func=_cmd_info)

    sp = sub.add_parser("probe", help="查看媒体信息")
    sp.add_argument("path")
    sp.set_defaults(func=_cmd_probe)

    for name, func, help_text in (
        ("demo", _cmd_demo, "生成演示项目并导出"),
        ("export", _cmd_export, "导出项目"),
    ):
        sp = sub.add_parser(name, help=help_text)
        if name == "export":
            sp.add_argument("project")
        else:
            sp.add_argument("--media", default=os.path.join(os.getcwd(), "out", "media"))
        sp.add_argument("--out", default=os.path.join("out", f"{name}.mp4"))
        sp.add_argument("--preset", default="1080p30", choices=["1080p30", "1080p60", "720p30", "vertical1080", "square1080", "4k30"])
        sp.add_argument("--width", type=int)
        sp.add_argument("--height", type=int)
        sp.add_argument("--fps", type=float)
        sp.add_argument("--crf", type=int)
        sp.add_argument("--quality", default=None, choices=["推荐", "更高", "高码率", "小体积"],
                        help="画质档位（在预设之上叠码率）")
        sp.add_argument("--bitrate", dest="bitrate", default=None, help="目标视频码率，如 8M")
        sp.add_argument("--max-bitrate", dest="max_bitrate", default=None, help="码率上限")
        sp.add_argument("--gop", type=int, default=None, help="关键帧间隔（帧）")
        sp.add_argument("--audio-bitrate", dest="audio_bitrate", default=None, help="音频码率，如 192k")
        sp.add_argument("--channels", type=int, default=None, choices=[1, 2], help="声道数")
        sp.add_argument("--profile", default=None, help="H.264 profile，如 high")
        sp.add_argument("--level", default=None, help="H.264 level，如 4.1")
        sp.add_argument("--no-faststart", action="store_true", help="不把 moov 放到文件头")
        sp.add_argument("--gif", action="store_true",
                        help="导出 GIF（默认宽 480、12fps，用 --width/--fps 调整）")
        sp.add_argument("--audio-only", action="store_true",
                        help="只导出音频（m4a/AAC），忽略画面")
        sp.add_argument("--variants", default=None,
                        help="一次渲染多路输出，用逗号分隔预设名，如 720p30,1080p30")
        sp.add_argument("--verify", action="store_true",
                        help="导出后自动做成品检测（黑帧/静音/帧数/音画同步）")
        sp.add_argument("--jobs", type=int, default=None,
                        help="并行渲染线程数（默认 CPU-1，最多 4；1 = 单线程）")
        sp.set_defaults(func=func)

    sp = sub.add_parser("still", help="渲染单帧 PNG")
    sp.add_argument("project")
    sp.add_argument("--at", type=float, default=0.0, help="时间（秒）")
    sp.add_argument("--out", default="out/still.png")
    sp.add_argument("--width", type=int)
    sp.add_argument("--height", type=int)
    sp.set_defaults(func=_cmd_still)

    sp = sub.add_parser("import-draft", help="导入剪映草稿（加密草稿自动解密）")
    sp.add_argument("path")
    sp.add_argument("--out", default="out/imported_project.json")
    sp.set_defaults(func=_cmd_import_draft)

    sp = sub.add_parser("export-draft", help="导出为剪映草稿目录（可加密、可注册）")
    sp.add_argument("project")
    sp.add_argument("--root", default="out/drafts", help="草稿根目录")
    sp.add_argument("--name", default=None, help="草稿名（默认用项目名）")
    # 默认就是明文（加密回环在当前版本不可靠，见 内部实测 报告 13.8）。
    # --encrypt 是显式选择加密；--no-encrypt 保留为兼容别名，两者都不写就是明文。
    sp.add_argument("--encrypt", action="store_true", help="尝试加密草稿（默认明文）")
    sp.add_argument("--no-encrypt", action="store_true", help="不加密（默认行为，保留兼容）")
    sp.add_argument("--overwrite", action="store_true", default=True)
    sp.add_argument("--register", action="store_true", help="写入剪映工程列表（会备份）")
    sp.add_argument("--flatten", action="store_true",
                    help="先把时间线渲染成一段片再出草稿（所见即所得，但不可再编辑）")
    sp.add_argument("--jobs", type=int, default=0)
    sp.add_argument("--report", action="store_true",
                    help="打印导出报告：哪些精确、哪些等价、哪些丢失")
    sp.add_argument("--selfcheck", action="store_true",
                    help="导出后立刻反向校验（结构 + 语义 + 像素）")
    sp.add_argument("--selfcheck-frames", type=int, default=5, help="像素对比抽帧数")
    sp.add_argument("--no-pixel-check", action="store_true", help="只比对结构，不渲染对比")
    sp.set_defaults(func=_cmd_export_draft)

    sp = sub.add_parser("jianying-probe",
                        help="启动剪映，验证它是否真的读入了我们写的草稿（会打开剪映）")
    sp.add_argument("draft", help="草稿目录")
    sp.add_argument("--project", default=None, help="原始工程，用来比时长")
    sp.add_argument("--exe", default=None, help="剪映可执行文件（默认自动找最新版）")
    sp.add_argument("--no-perturb", action="store_true",
                    help="不要清零 meta 里的时长/体积字段")
    sp.add_argument("--no-register", action="store_true",
                    help="不要先注册进剪映工程列表（默认会注册，否则剪映不扫描）")
    sp.add_argument("--timeout", type=float, default=90.0)
    sp.set_defaults(func=_cmd_jyprobe)

    sp = sub.add_parser("gui", help="打开图形界面（可带一个工程路径）")
    sp.add_argument("project", nargs="?", default=None, help="要打开的工程 JSON")
    sp.add_argument("--project", dest="project_opt", default=None, help="同上（旧写法）")
    sp.add_argument("--qt", dest="qt_shell", action="store_true",
                    help="用 Qt 界面（现在已是默认，保留这个开关只为兼容）")
    sp.add_argument("--tk", dest="tk_shell", action="store_true",
                    help="改用旧的 Tk 界面（冻结，只修 bug）")
    sp.set_defaults(func=_cmd_gui)

    sp = sub.add_parser("gui-smoke", help="无头自检 Qt 界面（打包版用它证明运行时齐全）")
    sp.set_defaults(func=_cmd_gui_smoke)

    sp = sub.add_parser("caps",
                        help="探测硬件解码/编码能力（对应剪映的 ve_hw_check）")
    sp.add_argument("--save", default=None, help="把探测结果写成 JSON")
    sp.set_defaults(func=_cmd_caps)

    sp = sub.add_parser("media-smoke", help="无头自检「素材拖进时间线」（回归用）")
    sp.add_argument("media", nargs="?", default="out/demo.mp4")
    sp.set_defaults(func=_cmd_media_smoke)

    sp = sub.add_parser("audio-smoke",
                        help="无头自检预览音频（合成一段音，验证混音链路出声）")
    sp.set_defaults(func=_cmd_audio_smoke)

    sp = sub.add_parser("subtitle", help="导入/导出/批量替换字幕（SRT / VTT）")
    sp.add_argument("action", choices=["import", "export", "replace", "shift"])
    sp.add_argument("project")
    sp.add_argument("srt", nargs="?", default=None,
                    help="import/export 用的字幕文件；replace 时不需要")
    sp.add_argument("--out", default=None, help="导入后的工程输出路径（默认覆盖原工程）")
    sp.add_argument("--find", default=None, help="replace：要被替换掉的文字")
    sp.add_argument("--with", dest="replace", default="",
                    help="replace：替换成什么（留空即删除）")
    sp.add_argument("--by", type=float, default=0.0,
                    help="shift：整体平移多少秒（负数为提前）")
    sp.add_argument("--size", type=float, default=10.0)
    sp.add_argument("--y", type=float, default=-0.72)
    sp.add_argument("--color", default="#FFFFFF")
    sp.add_argument("--stroke", type=float, default=0.028)
    sp.add_argument("--highlight", default="#FFE066")
    sp.add_argument("--no-karaoke", action="store_true")
    sp.set_defaults(func=_cmd_subtitle)

    sp = sub.add_parser("autocut", help="自动剪辑：按分镜或音乐节拍切")
    sp.add_argument("--media", nargs="+", required=True, help="素材（可多个）")
    sp.add_argument("--music", default=None, help="音乐（踩点模式必需）")
    sp.add_argument("--mode", choices=["scene", "beat"], default="scene")
    sp.add_argument("--beats-per-cut", type=int, default=2)
    sp.add_argument("--min-clip", type=float, default=0.8)
    sp.add_argument("--max-clip", type=float, default=6.0)
    sp.add_argument("--transition", default=None, help="转场名，如 叠化 / 闪黑")
    sp.add_argument("--fps", type=float, default=30.0)
    sp.add_argument("--out", default="out/autocut_project.json")
    sp.add_argument("--export", default=None, help="顺带导出 mp4 到该路径")
    sp.set_defaults(func=_cmd_autocut)

    sp = sub.add_parser("export-xml", help="导出 FCP7 XML（Premiere / Resolve / FCP 可导入）")
    sp.add_argument("project")
    sp.add_argument("--out", default="out/timeline.xml")
    sp.add_argument("--name", default=None, help="序列名")
    sp.add_argument("--srt", default=None, help="同时导出字幕到该路径")
    sp.add_argument("--report", action="store_true",
                    help="打印 XML 导出报告：哪些能映射、哪些只能渲染进画面")
    sp.add_argument("--verify", action="store_true",
                    help="把 XML 读回来，逐项核对起点/缩放/不透明度/裁剪/速度")
    sp.set_defaults(func=_cmd_export_xml)

    sp = sub.add_parser("autosub", help="离线语音识别 → 自动字幕（逐字时间）")
    sp.add_argument("media", help="音频或视频文件")
    sp.add_argument("--project", default=None, help="把字幕写进这个工程（不给则新建）")
    sp.add_argument("--out", default="out/autosub_project.json")
    sp.add_argument("--language", default="zh")
    sp.add_argument("--model", default="base",
                    # argparse 把 % 当格式符，帮助文本里要写 %%
                    help="tiny / base / small …；实测 base 命中 74%% 且比 tiny 快，"
                         "small 命中 89%% 但要先下载模型")
    sp.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    sp.add_argument("--threads", type=int, default=8, help="CPU 线程数（默认 8）")
    sp.add_argument("--prompt", default=None,
                    help="热词/提示词；不传时显式关闭后端默认的 auto（会把“字幕”听成“自目”）")
    sp.add_argument("--offset", type=float, default=0.0, help="整体平移秒数")
    sp.add_argument("--srt", default=None, help="同时导出 SRT")
    sp.add_argument("--size", type=float, default=10.0)
    sp.add_argument("--y", type=float, default=-0.72)
    sp.add_argument("--color", default="#FFFFFF")
    sp.add_argument("--stroke", type=float, default=0.028)
    sp.add_argument("--highlight", default="#FFE066")
    sp.add_argument("--no-karaoke", action="store_true")
    sp.add_argument("--preview", type=int, default=5, help="打印前几条")
    sp.set_defaults(func=_cmd_autosub)
    sp = sub.add_parser("lut", help="LUT 滤镜库：列出本机可用的 .cube，或生成自研 LUT")
    sp.add_argument("action", choices=["list", "refresh", "make"], nargs="?", default="list")
    sp.add_argument("--refresh", action="store_true", help="忽略缓存重新扫描")
    sp.add_argument("--dir", default=None,
                    help="make：写到哪个目录（默认 %%LOCALAPPDATA%%\\FreeCut\\luts）")
    sp.add_argument("--grid", type=int, default=17, help="make：网格精度（默认 17）")
    sp.set_defaults(func=_cmd_lut)

    sp = sub.add_parser("import-prproj", help="读取 Premiere Pro 工程（.prproj）")
    sp.add_argument("path")
    sp.add_argument("--out", default="out/imported_pr.json")
    sp.add_argument("--sequence", type=int, default=0)
    sp.set_defaults(func=_cmd_import_prproj)
    sp = sub.add_parser("stabilize", help="防抖（近似）：估计帧间平移并反向补偿")
    sp.add_argument("media")
    sp.add_argument("--out", required=True)
    sp.add_argument("--max-shift", type=int, default=12, help="每帧最大位移（像素）")
    sp.add_argument("--radius", type=int, default=10, help="平滑窗口半径（帧）")
    sp.add_argument("--limit", type=int, default=24, help="单帧最大补偿量")
    sp.add_argument("--rotation", action="store_true",
                    help="同时补偿旋转抖动（更慢，只处理 ±3°）")
    sp.add_argument("--force", action="store_true",
                    help="即使处理后更抖也保留输出（默认丢弃）")
    sp.set_defaults(func=_cmd_stabilize)

    sp = sub.add_parser("reverse", help="把一段素材倒放（音画一起倒，或丢掉声音）")
    sp.add_argument("media", help="源视频")
    sp.add_argument("--out", default=None, help="输出路径")
    sp.add_argument("--no-audio", action="store_true", help="不要声音")
    sp.set_defaults(func=_cmd_reverse)

    sp = sub.add_parser("freeze", help="把某一帧定格成静图插进工程")
    sp.add_argument("project", help="工程 JSON")
    sp.add_argument("--at", type=float, required=True, help="定格时间点（秒）")
    sp.add_argument("--duration", type=float, default=1.0, help="定格时长（秒）")
    sp.add_argument("--out", default="out/freeze", help="工作目录")
    sp.add_argument("--project-out", default=None, help="另存工程到这个路径")
    sp.set_defaults(func=_cmd_freeze)

    sp = sub.add_parser("import-xml", help="导入 FCP7 XML（PR/FCP/达芬奇都能导出这种）")
    sp.add_argument("xml", help="XML 文件")
    sp.add_argument("--out", default=None, help="工程输出路径")
    sp.set_defaults(func=_cmd_import_xml)

    sp = sub.add_parser("export-prproj", help="写成 .prproj（结构近似，未在 Premiere 验证）")
    sp.add_argument("project", help="工程 JSON")
    sp.add_argument("--out", default=None, help="输出路径")
    sp.add_argument("--name", default="", help="序列名")
    sp.set_defaults(func=_cmd_export_prproj)

    sp = sub.add_parser("jianying-diff", help="对比剪映打开前后的草稿，看它认没认我们写的字段")
    sp.add_argument("draft", help="草稿目录")
    sp.add_argument("--save", action="store_true", help="拍快照（导出后立刻做一次）")
    sp.set_defaults(func=_cmd_jianying_diff)

    sp = sub.add_parser("batch", help="按清单批量执行（渲染/导出草稿/XML/校验/剧照）")
    sp.add_argument("plan", help="清单 JSON 文件")
    sp.add_argument("--report", default=None, help="把结果写成 JSON")
    sp.add_argument("--quiet", action="store_true", help="只输出汇总")
    sp.add_argument("--jobs", default="1",
                    help="并行任务数，或 auto（默认 1；互不依赖的任务会并行跑）")
    sp.add_argument("--var", action="append", default=[], metavar="K=V",
                    help="覆盖清单里的变量，可重复：--var outdir=out/2026-10")
    sp.set_defaults(func=_cmd_batch)

    sp = sub.add_parser("verify", help="检查导出的成品：黑帧 / 静音 / 帧数 / 音画时长")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--expect-duration", type=float, default=None)
    sp.add_argument("--expect-audio", dest="expect_audio", action="store_true", default=None,
                    help="要求必须有声音；不加则静音只算警告")
    sp.set_defaults(func=_cmd_verify)
    sp = sub.add_parser("doctor", help="检查缺失素材并在指定目录里找同名文件重链")
    sp.add_argument("project")
    sp.add_argument("--search", nargs="*", default=None, help="在这些目录里找同名素材")
    sp.add_argument("--apply", action="store_true", help="唯一候选时自动重链并保存")
    sp.add_argument("--out", default=None)
    sp.set_defaults(func=_cmd_doctor)
    sp = sub.add_parser("fromscript", help="图文成片：文案 → 配音 + 字幕 + 画面")
    sp.add_argument("script", help="文案文件（UTF-8）")
    sp.add_argument("--media", nargs="*", default=None, help="用作画面的视频/图片")
    sp.add_argument("--out", required=True, help="输出工程 JSON")
    sp.add_argument("--work", default=None, help="配音 wav 的输出目录")
    sp.add_argument("--voice", default=None, help="SAPI 语音名（默认挑中文）")
    sp.add_argument("--rate", type=int, default=0, help="语速 -10..10")
    sp.add_argument("--no-tts", action="store_true", help="不做语音合成，只按估算时长排布")
    sp.add_argument("--width", type=int, default=1920)
    sp.add_argument("--height", type=int, default=1080)
    sp.add_argument("--fps", type=float, default=30.0)
    sp.add_argument("--render", default=None, help="顺带导出成片")
    from .export import PRESETS as _PRESETS

    sp.add_argument("--preset", default="1080p30", choices=sorted(_PRESETS))
    sp.add_argument("--jobs", type=int, default=0)
    sp.set_defaults(func=_cmd_fromscript)
    sp = sub.add_parser("tts", help="离线语音合成（Windows SAPI）")
    sp.add_argument("text", nargs="?", default="", help="要合成的文本")
    sp.add_argument("--file", default=None, help="从文件读文本")
    sp.add_argument("--out", default="tts.wav")
    sp.add_argument("--voice", default=None)
    sp.add_argument("--rate", type=int, default=0, help="-10..10")
    sp.add_argument("--list", action="store_true", help="列出可用语音")
    sp.set_defaults(func=_cmd_tts)
    sp = sub.add_parser("showcase", help="生成功能总演示（10 段画面/音频能力，加 --asr 追加自动字幕段）")
    sp.add_argument("--out", default="out/showcase.json")
    sp.add_argument("--work", default=None, help="合成素材的目录")
    sp.add_argument("--render", default=None, help="顺带导出成片")
    sp.add_argument("--preset", default="720p30", choices=["1080p30", "1080p60", "720p30"])
    sp.add_argument("--width", type=int, default=1280)
    sp.add_argument("--height", type=int, default=720)
    sp.add_argument("--fps", type=float, default=30.0)
    sp.add_argument("--jobs", type=int, default=0)
    sp.add_argument("--verify", action="store_true")
    sp.add_argument("--draft", default=None, help="同时导出剪映草稿到这个目录")
    sp.add_argument("--draft-name", default="FreeCut 功能总演示")
    sp.add_argument("--selfcheck-frames", type=int, default=3, help="草稿自检抽帧数")
    sp.add_argument("--asr", action="store_true",
                    help="加一段自动字幕：TTS 生成旁白 → Whisper 识别 → 字幕")
    sp.set_defaults(func=_cmd_showcase)
    return p


def _make_output_safe() -> None:
    """Never crash on a glyph the console encoding cannot represent.

    Chinese Windows defaults to GBK, and our warnings use ⚠ / ✅ / ❌ — so
    ``print()`` used to raise ``UnicodeEncodeError`` whenever stdout was
    redirected (`> out.txt`, CI logs).  Reconfigure to UTF-8 with replacement:
    readable in Windows Terminal, never raises elsewhere.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _make_output_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # 双击 exe（打包版）时没有参数：直接开界面，而不是弹一段帮助文本。
        # 命令行里 `python -m freecut` 仍然打印帮助（frozen 才走界面）。
        if getattr(sys, "frozen", False):
            return int(_cmd_gui(args) or 0)
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

def _cmd_batch(args) -> int:
    """Run a job list; a failing job never stops the rest."""
    from . import batch

    overrides = {}
    for item in getattr(args, "var", []) or []:
        if "=" not in item:
            print(f"--var 需要 K=V 形式: {item}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        overrides[key.strip()] = value

    try:
        jobs = batch.load_plan(args.plan, overrides or None)
    except batch.BatchError as exc:
        print(f"清单有问题: {exc}", file=sys.stderr)
        return 2

    def show(result) -> None:
        if not args.quiet:
            print(result.as_line(), flush=True)

    try:
        workers = batch.resolve_workers(args.jobs)
    except batch.BatchError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    results = batch.run(jobs, on_result=show, workers=workers)
    # 每个任务已经随跑随打了，这里只补总计（--quiet 时才补全量）
    for line in batch.summary_lines(results, include_jobs=args.quiet):
        print(line)
    if args.report:
        path = batch.write_report(results, args.report)
        print(f"报告: {path}")
    return 0 if all(r.ok for r in results) else 1

def _cmd_reverse(args) -> int:
    from .retime import reverse_video

    out = args.out or os.path.join("out", f"reverse_{os.path.splitext(os.path.basename(args.media))[0]}.mp4")
    path = reverse_video(args.media, out, keep_audio=not args.no_audio)
    print(f"已倒放: {path}")
    return 0


def _cmd_freeze(args) -> int:
    from .model import Project
    from .retime import add_freeze_clip

    project = Project.load(args.project)
    add_freeze_clip(project, int(args.at * 1_000_000), int(args.duration * 1_000_000), args.out)
    target = args.project_out or os.path.join(args.out, "project.json")
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    project.save(target)
    print(f"已插入定格 {args.duration}s: {target}")
    return 0

def _cmd_jianying_diff(args) -> int:
    """Snapshot before opening in 剪映, then diff after it saves the draft."""
    from . import jyverify

    if args.save:
        path = jyverify.save_snapshot(args.draft)
        data = jyverify.snapshot(args.draft)
        print(f"已拍快照: {path}")
        print(f"  时长 {data['duration']/1e6:.3f}s | 轨道 {len(data['tracks'])} | "
              f"素材 {data['materials_total']}")
        print("接着在剪映里打开这个草稿，动一下并保存，再跑一次（不带 --save）")
        return 0

    before = jyverify.load_snapshot(args.draft)
    if before is None:
        print("没有快照。先跑一次：freecut jianying-diff <草稿目录> --save", file=sys.stderr)
        return 2
    after = jyverify.snapshot(args.draft)
    changes = jyverify.diff(before, after)
    print(f"快照时间 {before.get('captured_at')}  →  现在")
    if not changes:
        print("  没有变化。")
    for change in changes:
        print("  " + change.as_line())
    print()
    print("结论: " + jyverify.verdict(changes))
    return 0

def _cmd_export_prproj(args) -> int:
    """Write a .prproj-shaped file; the caveat is printed every time."""
    from .model import Project
    from .prproj_export import export_prproj

    project = Project.load(args.project)
    out = args.out or os.path.join("out", (args.name or os.path.basename(args.project).split(".")[0]) + ".prproj")
    path = export_prproj(project, out, name=args.name)
    size = os.path.getsize(path)
    print(f"已写出: {path}（{size} 字节）")
    print("注意: 这是结构近似的 .prproj —— 我们自己的导入器能读回，" )
    print("      但没有在真正的 Premiere 里打开验证过；要交付成品请用 export。")
    return 0

def _cmd_import_xml(args) -> int:
    from .interchange import from_fcp7_xml

    project, info = from_fcp7_xml(args.xml)
    out = args.out or os.path.join("out", os.path.basename(args.xml).rsplit(".", 1)[0] + ".json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    project.save(out)
    extra = f" + {info['texts']} 条文字" if info.get("texts") else ""
    print(f"已导入 XML: 序列 '{info['sequence']}' / {info['fps']:.3f} fps / "
          f"{info['clips']} 个片段{extra} -> {out}")
    if info["clips"] == 0:
        print("注意: 一个片段都没导入——这份 XML 里的 clipitem 可能都没有 pathurl",
              file=sys.stderr)
    return 0

def _cmd_subtitle_replace(args) -> int:
    """批量替换字幕文本（ReaCut 有的"批量替换"，第 137 轮补）。"""
    from .model import Project

    project = Project.load(args.project)
    find = args.find
    if not find:
        print("需要 --find", file=sys.stderr)
        return 2
    replaced = 0
    for track in project.tracks:
        if track.type != "text":
            continue
        for seg in track.segments:
            mat = project.material(seg.material_id)
            if mat is None:
                continue
            old = str(mat.get("content", "") or "")
            if find not in old:
                continue
            new = old.replace(find, args.replace or "")
            mat["content"] = new
            replaced += 1
    out = args.out or args.project
    project.save(out)
    print(f"替换了 {replaced} 条字幕（'{find}' → '{args.replace or ''}'）-> {out}")
    return 0 if replaced else 1

def _cmd_subtitle_shift(args) -> int:
    """整体平移字幕时间（ReaCut 也有的 shift）。

    提前平移时如果某条会跑到 0 之前，就把它**裁到 0 开始**并缩短，
    而不是丢帧或者留下负数起点——剪辑软件里这两种做法都会让人困惑。
    """
    from .model import Project

    if not args.by:
        print("需要 --by 秒数（负数为提前）", file=sys.stderr)
        return 2
    delta = int(round(float(args.by) * 1_000_000))
    project = Project.load(args.project)
    moved = 0
    clamped = 0
    for track in project.tracks:
        if track.type != "text":
            continue
        for seg in track.segments:
            start = seg.target_timerange.start + delta
            duration = seg.target_timerange.duration
            if start < 0:
                # 裁到 0 开始，同时把时长减掉被切掉的部分，
                # 否则字幕会"提前结束"或者和后面那条重叠
                clamped += 1
                duration = max(1, duration + start)
                start = 0
            seg.target_timerange.start = start
            seg.target_timerange.duration = duration
            moved += 1
    out = args.out or args.project
    project.save(out)
    tail = f"，{clamped} 条被裁到 0 秒" if clamped else ""
    print(f"平移了 {moved} 条字幕（{args.by:+.2f}s）{tail} -> {out}")
    return 0 if moved else 1

def _cmd_lut_make(args) -> int:
    """生成 FreeCut 自研的 .cube LUT（不涉及剪映的任何素材）。"""
    from . import lutgen, luts

    target = args.dir or lutgen.default_dir()
    grid = max(2, min(65, int(args.grid or 17)))
    paths = lutgen.write_all(target, grid=grid)
    print(f"已生成 {len(paths)} 个 LUT -> {target}")
    for name, label in lutgen.labels().items():
        print(f"  {label:6} {name}.cube")
    try:
        # 生成完要刷新缓存，否则紧接着的 `lut list` 还是旧的（TTL 内不重扫）
        entries = luts.library(refresh=True)
        print(f"LUT 库现在共 {len(entries)} 个（含刚生成的 {len(paths)} 个）")
    except Exception as exc:
        print(f"（刷新库时出错，可忽略：{exc}）")
    return 0

def _cmd_stabilize(args) -> int:
    from . import stabilize

    stats = stabilize.stabilize_video(args.media, args.out,
                                      max_shift=args.max_shift,
                                      radius=args.radius, limit=args.limit,
                                      keep_if_worse=args.force,
                                      rotation=args.rotation)
    w, h = stats["size"]
    print(f"处理 {stats['frames']} 帧（{w}x{h} @ {stats['fps']:.2f}）")
    before = float(stats.get("jitter_before", 0.0))
    after = float(stats.get("jitter_after", 0.0))
    print(f"抖动（帧间位移标准差）前 {before:.2f} → 后 {after:.2f} 像素"
          f"（平均补偿 {stats['correction']:.2f}）")
    if stats.get("rotation"):
        print(f"旋转抖动 前 {stats['rot_before']:.2f}° → 后 {stats['rot_after']:.2f}°")
    if stats.get("rejected"):
        print("结论：这次处理后反而更抖，已丢弃输出（要保留加 --force）。")
        print("原因：防抖只做平移估计，对重度压缩、纯色画面、含旋转/缩放的抖动无效。")
        return 1
    print(f"已写出: {stats['out']}")
    return 0
