"""報告の書き出し: 出来事の静止画（前・山・後のコマ）、修正計画の前後のコマ、コマごとの数値（CSV）、報告（JSON と Markdown）。

静止画の文字は ASCII だけ（Pillow の既定の字体。日本語の字体を持ち込まない）。日本語の説明は Markdown と JSON に書く。
"""
import csv
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..fx.compare import label_patch, save_png_atomic, stamp
from . import CATEGORY_LABELS, reference

STILL_WIDTH = 320
GAP = 6
BACKGROUND = 24
RED, YELLOW = (255, 60, 60), (255, 220, 40)
SKIP_LABELS = {**CATEGORY_LABELS, "motion.position": "動作（位置の飛び・揺れ）", "reference": "設定画との照合"}
EVIDENCE = {"measured": "実測", "estimated": "推定（VLM）", "measured+estimated": "実測＋推定（VLM）"}
OVERALL = {"review": "要確認あり", "clean": "検出なし", "clean_with_skips": "検出なし（見ていない検査あり）"}


def _panel(image, width):
    height = max(2, int(round(image.shape[0] * width / image.shape[1])))
    return np.array(Image.fromarray(image).resize((width, height), Image.Resampling.BOX)) if image.shape[1] != width else image.copy()


def _box(panel, box, color):
    if not box:
        return
    height, width = panel.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in (box[0] * width, box[1] * height, box[2] * width, box[3] * height))
    cv2.rectangle(panel, (max(0, x0), max(0, y0)), (min(width - 1, max(x0, x1 - 1)), min(height - 1, max(y0, y1 - 1))), color, 2)


MAX_VLM_PANELS = 4


def _vlm_frames(event):
    """VLM の指摘のコマ（指摘の順。山・前・後のコマと重なってもよい）。"""
    return list(dict.fromkeys(s["peak"] for s in event["signals"] if s["check"] == "vlm"))


def contact_sheet(event, frames, palettes):
    """コマの順に [前のコマ | 山のコマ | VLM の指摘のコマ… | 後のコマ]（設定画の信号があれば設定画も）。

    赤い箱 = 数値の場所（山のコマ）、黄色の箱 = VLM の場所（その指摘のコマにだけ描く）。"""
    a, b = event["frames"]
    named = {a - 1: "", b: ""}
    for number in _vlm_frames(event)[:MAX_VLM_PANELS]:
        named[number] = " VLM"
    named[event["peak"]] = " PEAK"
    panels = []
    width = min(STILL_WIDTH, next(iter(frames.values())).shape[1])
    for number in sorted(named):
        image = frames.get(number)
        if image is None:
            continue
        panel = _panel(image, width)
        if number == event["peak"]:
            _box(panel, event["region"], RED)
        for signal in event["signals"]:
            if signal["check"] == "vlm" and signal["peak"] == number:
                _box(panel, signal["region"], YELLOW)
        stamp(panel, label_patch(f"f{number}{named[number]}", panel.shape[0]), 0, 0)
        panels.append(panel)
    used = {s.get("reference") for s in event["signals"] if s["check"].startswith("reference_")}
    for number, palette in enumerate(palettes):
        if palette["label"] in used:
            thumb = palette["thumb"]
            height = panels[0].shape[0]
            scaled = np.array(Image.fromarray(thumb).resize((max(2, int(round(thumb.shape[1] * height / thumb.shape[0]))), height), Image.Resampling.BOX))
            stamp(scaled, label_patch(reference_tag(palette, number), height), 0, 0)
            panels.append(scaled)
    height = max(p.shape[0] for p in panels)
    canvas = np.full((height, sum(p.shape[1] for p in panels) + GAP * (len(panels) - 1), 3), BACKGROUND, np.uint8)
    x = 0
    for panel in panels:
        canvas[:panel.shape[0], x:x + panel.shape[1]] = panel
        x += panel.shape[1] + GAP
    return canvas


def reference_tag(palette, number):
    """静止画に書く設定画の名前（ASCII だけ）。向き（view）が ASCII ならそれを、ほかは番号（1 始まり）を書く。"""
    view = palette.get("view") or ""
    return f"REF {view}" if view and view.isascii() and view.isprintable() and len(view) <= 16 else f"REF {number + 1}"


def write_references(out, palettes):
    """設定画ごとに [切り出した設定画 | 色を取った画素（市松模様 = 背景として除いた所）] を references/NN.png に書く。→ パスの一覧。"""
    names = []
    for number, palette in enumerate(palettes):
        thumb, cutout = palette["thumb"], palette["cutout"]
        canvas = np.full((thumb.shape[0], thumb.shape[1] * 2 + GAP, 3), BACKGROUND, np.uint8)
        canvas[:, :thumb.shape[1]] = thumb
        canvas[:, thumb.shape[1] + GAP:] = cutout
        stamp(canvas, label_patch(reference_tag(palette, number), canvas.shape[0]), 0, 0)
        name = f"references/{number:02d}.png"
        save_png_atomic(canvas, Path(out) / name)
        names.append(name)
    return names


def needed_frames(events, plan_segments):
    """{クリップ: {コマ番号, …}}。出来事の前・山・後・VLM の指摘のコマと、修正計画の前後のコマ。"""
    wanted = {}
    for event in events:
        a, b = event["frames"]
        wanted.setdefault(event["clip"], set()).update({a - 1, event["peak"], b, *_vlm_frames(event)[:MAX_VLM_PANELS]})
    for segment in plan_segments:
        for side in ("before", "after"):
            if segment["anchors"][side]:
                wanted.setdefault(segment["clip"], set()).add(segment["anchors"][side]["frame"])
    return wanted


def write_stills(out, events, grabbed, palettes):
    folder = Path(out) / "stills"
    for event in events:
        frames = grabbed.get(event["clip"], {})
        if event["peak"] not in frames:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        name = f"stills/{event['id']}.jpg"
        Image.fromarray(contact_sheet(event, frames, palettes)).save(Path(out) / name, format="JPEG", quality=88)
        event["still"] = name


def write_anchors(out, plan, grabbed):
    for segment in plan["segments"]:
        for side in ("before", "after"):
            anchor = segment["anchors"][side]
            if not anchor:
                continue
            image = grabbed.get(segment["clip"], {}).get(anchor["frame"])
            if image is None:
                continue
            name = f"anchors/{segment['clip']}_f{anchor['frame']:06d}.png"
            save_png_atomic(image, Path(out) / name)
            anchor["image"] = name


def _num(value, digits=5):
    return "" if value is None or not math.isfinite(value) else f"{value:.{digits}f}"


def write_metrics(path, features, fps, palettes=(), thresholds=None):
    """コマごとの数値（しきい値を自作に合わせて直すときの材料）。差の列は前のコマとの比較。

    設定画の列: reference_overlap はいちばん重なる設定画との重なり。reference_missing はそのコマをいちばんよく説明する設定画で欠けた
    主な色の数、reference_extra は設定画に無い色の割合（どちらも reference.explaining で選んだ設定画。欠けと余りの検査と同じ値）。"""
    overlap = reference.overlap_series(features) if features.ref else [math.nan] * features.count
    explained = [reference.explaining(stats, palettes, thresholds) for stats in features.ref] if features.ref and palettes else []
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frame", "t", "luma", "frame_diff", "structure_diff", "present", "area", "cx", "cy", "body", "components",
                         "shape_change", "area_change", "centroid_change", "palette_change", "flow_p95", "reference_overlap",
                         "reference_missing", "reference_extra"])
        for i in range(features.count):
            structure = features.diffs(i - 1, i)[1] if i else math.nan
            pick = (lambda f: f(i - 1, i)) if i else (lambda f: math.nan)
            writer.writerow([features.frame(i), _num(features.frame(i) / fps if fps else math.nan, 4), _num(features.luma[i]),
                             _num(features.fdiff[i]), _num(structure), int(bool(features.present[i])), _num(features.area[i]),
                             _num(features.centroid[i, 0]), _num(features.centroid[i, 1]), _num(features.body[i]), int(features.components[i]),
                             _num(pick(features.shape_change)), _num(pick(features.area_change)), _num(pick(features.centroid_change)),
                             _num(pick(features.palette_change)), _num(features.flow[i]), _num(overlap[i]),
                             len(explained[i][1]) if i < len(explained) and explained[i] else "",
                             _num(features.ref[i][explained[i][2]]["extra"]) if i < len(explained) and explained[i] else ""])


def _frames_text(frames):
    a, b = frames
    return f"{a}" if b - a == 1 else f"{a}〜{b - 1}"


def _seconds_text(t):
    return "" if not t else f"{t[0]:.3f}〜{t[1]:.3f}"


def _where(event):
    target = event["target"]
    base = f"`{target['path']}` のコマ {_frames_text(target['source_frames'])}"
    if target["kind"] != "dance_clip":
        return base
    uses = [f"`{u['pointer']}`（出力 " + "・".join(f"{t[0]:.2f}〜{t[1]:.2f} 秒" for t in u["shot_t"]) + "）" for u in target["uses"]]
    return base + "。" + "、".join(uses)


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def markdown(report, plan):
    lines = [f"# 生成動画の検品: {OVERALL[report['overall']]}（要確認 {report['summary']['events']} 件）", "",
             f"- 設定: `{report['config']}`（sha256 {report['config_sha256'][:12]}…）",
             "- **検出が無いことは合格ではありません。** 異常かどうか・採否は人が見て決めます（数値は動画から測った値、VLM の指摘は推定）。",
             "- しきい値はどれも仮の値です（`docs/inspect.md`）。コマ番号は 0 始まりで、表の範囲は終わりのコマを含みます。", "",
             "| クリップ | 見た範囲 | 連続性 | 動作 | 形状 | 衣装 | その他 | 設定画との重なり（中央値・最小） | 見ていない検査 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for clip in report["clips"]:
        counts = clip["by_category"]
        ref = clip.get("reference") or {}
        overlap = f"{ref['overlap_median']:.2f}・{ref['overlap_min']:.2f}（コマ {ref['overlap_min_frame']}）" if ref.get("frames") else "—"
        skipped = "、".join(SKIP_LABELS.get(s["check"], s["check"]) for s in report["skipped"] if s["clip"] == clip["id"]) or "—"
        lines.append(f"| {clip['id']} | {_frames_text(clip['inspected'])}（{clip['inspected'][1] - clip['inspected'][0]} コマ） | "
                     + " | ".join(str(counts.get(c, 0)) for c in ("continuity", "motion", "shape", "costume", "other")) + f" | {overlap} | {skipped} |")
    for clip in report["clips"]:
        events = [e for e in report["events"] if e["clip"] == clip["id"]]
        rate = f"{clip['frames_total']} コマ・{clip['fps']:g} fps" if clip["fps"] else "静止画"
        lines += ["", f"## {clip['id']}（`{clip['path']}`・{rate}）", ""]
        if not clip["decode"]["ok"]:
            lines += [f"- **復号のエラー**: {clip['decode']['error']}", ""]
        if not events:
            lines.append("見つけた所はありません（見た検査の範囲で）。")
            continue
        lines += ["| ID | コマ | 秒 | 分類 | 内容 | 根拠 | 直す場所 | 静止画 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for event in events:
            others = [CATEGORY_LABELS[c] for c in event["categories"] if c != event["category"]]
            kind = CATEGORY_LABELS[event["category"]] + (f"（ほかに {'・'.join(others)}）" if others else "")
            remarks = list(dict.fromkeys(s["detail"] for s in event["signals"] if s["check"] == "vlm" and s["detail"] != event["summary"]))
            checks = sorted({s["label"] for s in event["signals"] if not (remarks and s["check"] == "vlm")} - {event["label"]})
            colors = list(dict.fromkeys(s["color"] for s in event["signals"]           # 同じ検査で別の色（欠けた色が 2 つなど）
                                        if s["label"] == event["label"] and s["detail"] != event["summary"] and s.get("color")))
            detail = (f"{event['label']}: {event['summary']}" + (f"（同じ検査のほかの色: {'・'.join(colors)}）" if colors else "")
                      + (f"（ほかの信号: {'・'.join(checks)}）" if checks else "") + "".join(f"（VLM の指摘: {remark}）" for remark in remarks))
            lines.append(f"| {event['id']} | {_frames_text(event['frames'])} | {_seconds_text(event['t'])} | {kind} | {_cell(detail)} | "
                         f"{EVIDENCE[event['evidence']]} | {_cell(_where(event))} | {event.get('still', '')} |")
    if report["unused"]:
        lines += ["", "## 出力に出ない区間で見つけたもの", "",
                  f"dance の spec では使っていない元のコマで {len(report['unused'])} 件見つけました（一覧は inspect_report.json の unused）。"]
    lines += ["", "## 修正計画（提案。何も実行していません）", ""]
    if not plan["segments"]:
        lines.append("区間はありません。")
    else:
        lines += ["自動の再生成は未実装です。区間ごとに前後のコマ（anchors）と生成の素性（origin）を `fix_plan.json` に書きました。", "",
                  "| 区間 | クリップ | 生成し直す元のコマ | 前後のコマ | 出来事 | 手当ての候補 |", "| --- | --- | --- | --- | --- | --- |"]
        for segment in plan["segments"]:
            anchors = "・".join(f"{side} {segment['anchors'][side]['frame']}" for side in ("before", "after") if segment["anchors"][side]) or "—"
            actions = "・".join(a["kind"] for a in segment["actions"])
            lines.append(f"| {segment['id']} | {segment['clip']} | {_frames_text(segment['source_frames'])} | {anchors} | "
                         f"{'・'.join(segment['events'])} | {actions} |")
    if report["skipped"]:
        lines += ["", "## 見ていない検査（未実施は合格ではありません）", ""]
        lines += [f"- {s['clip']}: {SKIP_LABELS.get(s['check'], s['check'])} — {s['reason']}" for s in report["skipped"]]
    if report.get("notes"):
        lines += ["", "## 注意", ""] + [f"- {note}" for note in report["notes"]]
    vlm = report["vlm"]
    lines += ["", "## VLM", "", f"- 状態: {vlm['status']}"]
    if vlm.get("frames"):
        lines.append(f"- 見せたコマ: {vlm['frames']['frames']}（出来事の山 {vlm['frames']['event_peaks']}・見本 {vlm['frames']['samples']}）")
    if vlm.get("tool"):
        lines.append(f"- 道具: {vlm['tool']}（外部へ送る設定: {'はい' if vlm.get('sends_data') else 'いいえ'}）・指摘 {vlm.get('findings', 0)} 件")
    lines += [f"- 注意: {w}" for w in vlm.get("warnings", [])]
    if report["references"]:
        lines += ["", "## 設定画", "",
                  "色を取った画素は `references/` の画像で確かめてください（左が切り出した設定画、右が色を取った画素。市松模様は背景として除いた所）。"
                  "設定画が複数なら、欠けと余りはコマごとにいちばんよく説明する 1 枚と比べます。", ""]
        for ref in report["references"]:
            colors = "・".join(f"{c['hex']}（{c['share']:.0%}）" for c in ref["colors"])
            note = "（縁の色から切り抜いた推定）" if ref["estimated"] else ""
            image = f"・`{ref['cutout']}`" if ref.get("cutout") else ""
            lines.append(f"- {ref['label']}{note}{image}: 人物として使った画素 {ref['foreground_share']:.0%}。主な色 {colors}")
    return "\n".join(lines) + "\n"
