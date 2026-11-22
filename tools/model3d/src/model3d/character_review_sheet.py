"""Six body-relative views of each event, with local links to the unscaled evidence."""
from collections.abc import Mapping, Sequence
from html import escape
import math
from pathlib import Path
from urllib.parse import quote

from .io import ToolError


VIEW_LABELS = {
    "left-front": "左斜め前 45°", "left": "左真横 90°", "left-back": "左斜め後ろ 135°",
    "right-front": "右斜め前 45°", "right": "右真横 90°", "right-back": "右斜め後ろ 135°",
}

_STYLE = """
:root { color-scheme: light; font-family: system-ui, sans-serif; color: #242725; background: #f6f5f0; }
* { box-sizing: border-box; }
body { max-width: 1600px; margin: auto; padding: clamp(1rem, 3vw, 3rem); }
h1, h2 { font-family: Georgia, "Yu Mincho", serif; margin: 0 0 .5rem; }
h1 { font-size: clamp(1.8rem, 4vw, 3rem); } h2 { font-size: 1.65rem; }
p { margin: .5rem 0; } a { color: #1f6359; text-underline-offset: .17em; }
a:focus-visible { outline: 3px solid #197168; outline-offset: 2px; }
nav { display: flex; flex-wrap: wrap; gap: .4rem 1.3rem; margin: 1.4rem 0; }
section { border-top: 2px solid #54635d; padding: 1.3rem 0; margin-top: 2rem; }
.meta { color: #43544c; font-variant-numeric: tabular-nums; }
.tables { overflow-x: auto; } table { border-collapse: collapse; width: 100%; min-width: 680px; table-layout: fixed; }
th, td { text-align: left; padding: .4rem; }
th:first-child { width: 8.5rem; } thead { border-bottom: 1px solid #a4ada5; }
th[scope=row] { font-weight: 500; } tbody + tbody { border-top: 2px solid #bfc6be; }
tr { break-inside: avoid; } tr:target { outline: 2px solid #197168; outline-offset: -2px; }
td img { display: block; width: 100%; height: clamp(150px, 21vw, 310px); object-fit: contain; background: #e6e6df; }
td a { display: block; } .empty { color: #5c625c; font-size: .9rem; }
details { margin: .65rem 0; } summary { cursor: pointer; }
@media print { body { max-width: none; padding: 0; } nav { display: none; } table { min-width: 0; } td img { height: 115px; } }
"""


def _media_url(path: Path, directory: Path, *, external: bool = False) -> str:
    path = Path(path)
    path = (directory / path).resolve()
    if not path.is_file():
        raise ToolError(f"比較用ファイルがありません: {path}")
    if path.is_relative_to(directory):
        # A ./ prefix also makes a colon in the first path segment harmless.
        return "./" + quote(path.relative_to(directory).as_posix(), safe="/")
    if external:
        return path.as_uri()
    raise ToolError(f"比較画像が出力先の外にあります: {path}")


def _image_cell(url: str, label: str) -> str:
    href = escape(url, quote=True)
    return (f'<td><a href="{href}"><img src="{href}" '
            f'loading="lazy" alt="{escape(label, quote=True)}"></a></td>')


def _views_table(art, rows, event_label, control_label, anchor):
    has_control = any(row.get("control_off") for row in rows.values())
    if has_control and not all(row.get("control_off") for row in rows.values()):
        raise ToolError(f"対照画像を6方向そろえてください: {event_label}")
    columns = [("before", "修正前"), ("after", "修正後")]
    if has_control:
        control = f"修正後・制御オフ（{control_label}）" if control_label else "修正後・制御オフ"
        columns.append(("control_off", control))
    headings = ["本人から見た向き", "設定画（外見）", *(name for _, name in columns)]
    cells = "".join(f'<th scope="col">{escape(h)}</th>' for h in headings)
    sections = [f'<div class="tables"><table><thead><tr>{cells}</tr></thead>']
    for side in ("left", "right"):
        sections.append("<tbody>")
        for view in (side + "-front", side, side + "-back"):
            direction = VIEW_LABELS[view]
            sections.append(f'<tr id="{anchor}-{view}"><th scope="row">{direction}</th>')
            if view in art:
                sections.append(_image_cell(art[view], f"設定画・{direction}"))
            else:
                sections.append('<td class="empty">この向きの設定画なし</td>')
            for key, name in columns:
                sections.append(_image_cell(rows[view][key], f"{event_label}・{direction}・{name}"))
            sections.append("</tr>")
        sections.append("</tbody>")
    return "".join([*sections, "</table></div>"])


def write_character_review_sheet(
    output_dir: Path, *, events: Sequence[dict], references: Mapping[str, Path],
    motion_reference: Path | None, renders: Sequence[dict], fps: float, first_frame: int = 1,
    roundtrip_report: Path | None = None,
) -> Path:
    """Write index.html beside staged reference art and renders. The motion clip can remain external.

    Each render row has event, view, before, after and optionally control_off. The
    six view IDs in VIEW_LABELS are in the character's own coordinate system.
    The caller stages this whole directory before publication.
    """
    directory = Path(output_dir).resolve()
    if not math.isfinite(fps) or fps <= 0:
        raise ToolError("フレームレートには正の有限値が必要です")
    event_ids = [event["id"] for event in events]
    if not event_ids or len(set(event_ids)) != len(event_ids):
        raise ToolError("異なる ID の確認イベントが必要です")
    if set(references) - VIEW_LABELS.keys():
        raise ToolError("設定画の向きを確認してください")
    art = {view: _media_url(path, directory) for view, path in references.items()}
    render_rows = {event_id: {} for event_id in event_ids}
    for row in renders:
        event_id, view = row["event"], row["view"]
        if event_id not in render_rows or view not in VIEW_LABELS or view in render_rows[event_id]:
            raise ToolError(f"描画のイベントまたは向きが不正か重複しています: {event_id}, {view}")
        paths = {"before": row["before"], "after": row["after"]}
        if row.get("control_off"):
            paths["control_off"] = row["control_off"]
        render_rows[event_id][view] = {key: _media_url(p, directory) for key, p in paths.items()}

    title = "キャラクターの多方向比較"
    body = [f"<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{title}</title><style>{_STYLE}</style></head><body><main><h1>{title}</h1>",
            "<p>画像を選ぶと原寸で開きます。</p>"]
    if motion_reference is not None:
        link = escape(_media_url(motion_reference, directory, external=True), quote=True)
        body.append(f'<p><a href="{link}">振付のリファレンス動画を開く</a></p>')
    if roundtrip_report is not None:
        link = escape(_media_url(roundtrip_report, directory), quote=True)
        body.append(f'<p><a href="{link}">配布 GLB の測定結果を全コマ開く（JSON）</a></p>')
    navigation = []
    sections = []
    for number, event in enumerate(events, 1):
        key = event["id"]
        label = event.get("label") or key
        frame = event["frame"]
        seconds = (frame - first_frame) / fps
        anchor = f"event-{number}"
        focus = event.get("focus_view")
        if focus is not None and focus not in VIEW_LABELS:
            raise ToolError(f"確認対象の向きが不明です: {focus}")
        destination = f"{anchor}-{focus}" if focus else anchor
        jump = f" · {VIEW_LABELS[focus]}" if focus else ""
        navigation.append(f'<a href="#{destination}">{seconds:.3f} 秒 · {escape(label)}{jump}</a>')
        sections.append(f'<section id="{anchor}"><h2>{escape(label)}</h2>'
                        f'<p class="meta">{seconds:.3f} 秒 · フレーム {frame:d} · {fps:g} fps</p>')
        if region := event.get("region"):
            sections.append(f"<p>確認部位　{escape(region)}</p>")
        if focus:
            sections.append(f'<p><a href="#{destination}">{VIEW_LABELS[focus]}の比較へ</a></p>')
        if notes := event.get("observations"):
            items = "".join(f"<li>{escape(note)}</li>" for note in notes)
            sections.append(f"<details open><summary>実測・検出メモ</summary><ul>{items}</ul></details>")
        if render_rows[key].keys() != VIEW_LABELS.keys():
            raise ToolError(f"修正前後を6方向そろえてください: {key}")
        sections.append(_views_table(art, render_rows[key], label, event.get("control_label"), anchor))
        sections.append("</section>")
    body.append(f'<nav aria-label="確認イベント">{"".join(navigation)}</nav>')
    body.extend(sections)
    body.append("</main></body></html>")
    target = directory / "index.html"
    target.write_text("\n".join(body), encoding="utf-8")
    return target
