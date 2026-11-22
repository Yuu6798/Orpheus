"""テクスチャの操作: 参照画像（設定画など）を UV テクスチャへ写す、テクスチャを貼ったメッシュを正射影で描く、マスクを描く、
GLB から材質のテクスチャを取り出す。"""
import math
from pathlib import Path

from .. import glb, texture
from ..io import ToolError, atomic_json, sha256
from ..mesh import Mesh
from . import StepResult, number, register, require, selected

EXTRACT_PARAMETERS = ("slot", "material", "decode")
APPLY_PARAMETERS = ("match", "min_replacements", "image_name", "blender", "timeout")
APPLY_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "apply_texture.py"


PROJECTION_PARAMETERS = ("view", "alignment", "min_facing", "facing_ramp", "depth_tolerance", "expand", "max_stretch",
                         "review_note", "occlusion_part", "color_match", "visibility", "two_sided_depth", "core", "regions", "prefilter")
ELLIPSE_KEYS = ("center", "radius", "inner", "name")
VIEW_PARTS_PARAMETERS = ("view", "parts", "margin", "background")
ALIGNMENT_KEYS = ("model", "reference", "kind")
MASK_PARAMETERS = ("size", "shapes", "feather")


def _view(parameters):
    view = parameters.get("view")
    texture.check_view(view)
    return view


def _refuse_unknown(parameters, known, operation):
    unknown = sorted(set(parameters) - set(known))
    if unknown:   # 綴りを誤ったキーが黙って既定値に戻らないように止める
        raise ToolError(f"{operation} の知らないパラメーター: {', '.join(unknown)}（使えるのは {', '.join(known)}）")


def projection_settings(parameters):
    """texture.project_reference のパラメーターを確かめ、視点・位置合わせ (A, t, 報告)・数の設定・review_note を返す。
    dance-recipe も工程を作る前にこれで確かめる（同じ規則を 2 か所に書かない）。"""
    _refuse_unknown(parameters, PROJECTION_PARAMETERS, "texture.project_reference")
    view = _view(parameters)
    marks = parameters.get("alignment")
    if not isinstance(marks, dict):
        raise ToolError("alignment に {\"model\": [[x, y], …], \"reference\": [[x, y], …]} を書いてください（目印は推定しない）")
    _refuse_unknown(marks, ALIGNMENT_KEYS, "texture.project_reference の alignment")
    fitted = texture.alignment(marks.get("model"), marks.get("reference"), marks.get("kind", "similarity"))
    options = {"min_facing": number(parameters, "min_facing", 0.2, minimum=-1.0),
               "facing_ramp": number(parameters, "facing_ramp", 0.3, minimum=0, exclusive=True),
               "depth_tolerance": number(parameters, "depth_tolerance", 0.002, minimum=0),
               "expand": number(parameters, "expand", 1.0, minimum=0),
               "max_stretch": number(parameters, "max_stretch", 8.0, minimum=1.0)}
    visibility = parameters.get("visibility", True)
    if not isinstance(visibility, bool):
        raise ToolError(f"visibility は true か false にしてください: {visibility!r}")
    if not visibility:
        options["visibility"] = False
        if parameters.get("occlusion_part") is not None:
            raise ToolError("visibility: false では見え隠れを見ないので、occlusion_part は使えません")
    if parameters.get("two_sided_depth") is not None:
        options["two_sided_depth"] = number(parameters, "two_sided_depth")
    if parameters.get("core") is not None:
        options["core"] = _ellipse(parameters["core"], "core")
    if parameters.get("regions") is not None:
        regions = parameters["regions"]
        if not isinstance(regions, list) or not regions:
            raise ToolError(f"regions は楕円 {{\"center\": [x, y], \"radius\": [rx, ry], \"inner\": 0〜1}} の並びにしてください: {regions!r}")
        options["regions"] = [_ellipse(region, f"regions[{k}]") for k, region in enumerate(regions)]
    if parameters.get("prefilter") is not None:          # 写す前のならし: "auto"（升目 1 つの半分）か、参照画像の画素の σ
        prefilter = parameters["prefilter"]
        if prefilter != "auto" and (isinstance(prefilter, bool) or not isinstance(prefilter, (int, float))
                                    or not math.isfinite(prefilter) or prefilter < 0):
            raise ToolError(f"prefilter は \"auto\"（升目 1 つの半分）か、参照画像の画素の σ（0 以上の数）にしてください: {prefilter!r}")
        options["prefilter"] = prefilter
    note = parameters.get("review_note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise ToolError("texture.project_reference の review_note は空でない文字列にしてください")
    color_match_settings(parameters, view["size"])
    return view, fitted, options, note


def _ellipse(value, where):
    """視点の画素の楕円 {"center": [x, y], "radius": [rx, ry], "inner": 0〜1（任意）, "name": メモ（任意）} を確かめて返す（name は外す）。"""
    if not isinstance(value, dict):
        raise ToolError(f"{where} は {{\"center\": [x, y], \"radius\": [rx, ry], \"inner\": 0〜1}}（視点の画素）にしてください: {value!r}")
    _refuse_unknown(value, ELLIPSE_KEYS, f"texture.project_reference の {where}")
    center, radius = value.get("center"), value.get("radius")
    if (not isinstance(center, list) or len(center) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in center)):
        raise ToolError(f"{where}.center は [x, y]（視点の画素、有限の数）にしてください: {center!r}")
    if (not isinstance(radius, list) or len(radius) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in radius)):
        raise ToolError(f"{where}.radius は [rx, ry]（視点の画素、正の数）にしてください: {radius!r}")
    out = {"center": list(center), "radius": list(radius)}
    if "inner" in value:
        inner = value["inner"]
        if isinstance(inner, bool) or not isinstance(inner, (int, float)) or not 0 <= inner < 1:
            raise ToolError(f"{where}.inner は 0 以上 1 未満にしてください（ここより内は 1、縁へなめらかに 0）: {inner!r}")
        out["inner"] = inner
    return out


def color_match_settings(parameters, size=None):
    """texture.project_reference の color_match（{"model": [[x0, y0, x1, y1], …], "reference": […]}）を確かめて返す（無ければ None）。
    model の枠はモデルの描画（視点の画素）、reference の枠は参照画像の画素。右と下は含まない。size（視点の [幅, 高さ]）を
    渡せば、model の枠がその中かも確かめる（dance-recipe が長い再構築の前に止められるように。reference の枠は参照画像の大きさで
    確かめる。工程では描いた絵と読んだ参照画像で、dance-recipe では参照画像を開いて）。"""
    value = parameters.get("color_match")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"model", "reference"}:
        raise ToolError(f"color_match は {{\"model\": [[x0, y0, x1, y1], …], \"reference\": [[x0, y0, x1, y1], …]}} にしてください: {value!r}")
    boxes = {}
    for key in ("model", "reference"):
        items = value[key]
        if (not isinstance(items, list) or not items
                or not all(isinstance(b, list) and len(b) == 4 and all(isinstance(v, int) and not isinstance(v, bool) for v in b)
                           and b[2] > b[0] and b[3] > b[1] for b in items)):
            raise ToolError(f"color_match.{key} は枠 [x0, y0, x1, y1]（整数、x1 > x0、y1 > y0）の並びにしてください: {items!r}")
        boxes[key] = [list(b) for b in items]
    if size is not None:
        texture.check_boxes(boxes["model"], size[0], size[1], "color_match.model")
    return boxes


def mask_from(parameters, size):
    """texture.draw_mask のパラメーター（size は呼ぶ側が決める）を確かめてマスクを描く。dance-recipe も事前の確認に使う。"""
    _refuse_unknown(parameters, MASK_PARAMETERS, "texture.draw_mask")
    return texture.draw_mask(size, parameters.get("shapes"), number(parameters, "feather", 0.0, minimum=0))


@register("texture.extract_glb", inputs=("glb",), outputs=("image", "report"),
          summary="GLB（glTF 2.0 のバイナリ）から、材質のテクスチャの画像を取り出す（slot: baseColor・metallicRoughness・normal・"
                  "occlusion・emissive。既定 baseColor）。material（番号か名前）を省くと、全部の材質が同じ画像を指すときだけ取り出す。"
                  "既定は埋め込まれたバイトをそのまま書く（出力の拡張子は画像の種類に合わせる）。decode: true なら Pillow で開いて、"
                  "出力の拡張子の形式で書き直す。外のファイルを指す画像・拡張にしか無い画像・壊れた GLB は止める")
def extract_glb(inputs, parameters, outputs, context):
    import hashlib
    import io
    require(inputs, ("glb",), "texture.extract_glb")
    _refuse_unknown(parameters, EXTRACT_PARAMETERS, "texture.extract_glb")
    if "image" not in outputs:
        raise ToolError("texture.extract_glb には outputs の image（書き出す画像のファイル名）が要ります")
    slot = parameters.get("slot", "baseColor")
    material = parameters.get("material")
    if material is not None and not (isinstance(material, str) and material
                                     or isinstance(material, int) and not isinstance(material, bool) and material >= 0):
        raise ToolError(f"material は材質の番号（0 以上の整数）か名前にしてください: {material!r}")
    decode = parameters.get("decode", False)
    if not isinstance(decode, bool):
        raise ToolError(f"decode は true か false にしてください: {decode!r}")
    source = Path(inputs["glb"])
    data = source.read_bytes()
    document, blob = glb.read_glb(data, f"GLB（{source.name}）")
    use, users = glb.choose(document, slot, material)
    raw, record = glb.image_bytes(document, blob, use["image"])
    target = Path(outputs["image"])
    suffix = target.suffix.lower()
    from PIL import Image, UnidentifiedImageError
    size = mode = None
    try:
        with Image.open(io.BytesIO(raw)) as image:
            size, mode = list(image.size), image.mode
            if decode:
                image.load()
                try:
                    image.save(target)
                except (KeyError, ValueError, OSError) as error:
                    raise ToolError(f"画像を {suffix or '拡張子なし'} の形式で書けません（{image.mode}）: {error}") from error
    except UnidentifiedImageError as error:
        if decode:
            raise ToolError(f"埋め込まれた画像（{record['mimeType']}）を Pillow で開けません: {error}") from error
    if not decode:
        allowed = glb.SUFFIXES.get(record["mimeType"])
        if allowed is None:
            raise ToolError(f"画像の種類（mimeType {record['mimeType']!r}）が分からないので、そのまま書く拡張子を決められません")
        if suffix not in allowed:
            raise ToolError(f"埋め込まれた画像は {record['mimeType']} です。そのまま書くなら出力の拡張子を {' か '.join(allowed)} に"
                            f"してください（{suffix or '拡張子なし'} の形式に変えるなら decode: true）")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    report = {"schema_version": "model3d-glb-image/0.1",
              "glb": {"file": source.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
              "slot": slot, "material": {"index": use["material"], "name": use["name"]} if material is not None else None,
              "materials": [{"index": u["material"], "name": u["name"]} for u in users], "texture": use["texture"],
              "image": {**record, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
              "written": {"file": target.name, "bytes": target.stat().st_size, "sha256": sha256(target), "decoded": decode},
              "size": size, "mode": mode}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "extract_glb", "slot": slot, "image": record["index"], "name": record["name"],
                   "mimeType": record["mimeType"], "bytes": len(raw), "size": size, "mode": mode, "decoded": decode,
                   "materials": len(users)}
    return StepResult(observations=[observation])


@register("texture.apply_to_blend", inputs=("blend", "texture"), outputs=("blend", "report"), cacheable=False,
          summary=".blend の材質の画像ノードのうち、match（画像の名前かパスのファイル名。大文字と小文字・.001 の印は見ない）に合う画像だけを、"
                  "新しいテクスチャにつなぎ替えて詰め込み、別の .blend に書く（Blender の中で）。合う画像は色空間がそろい、同じ大きさで"
                  "あること。詰め込んだバイトがテクスチャと同じこと・合わない画像ノードが変わらないこと・入力が変わらないことを確かめる。"
                  "形・骨・材質のつなぎ方は変えない。Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def apply_to_blend(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend", "texture"), "texture.apply_to_blend")
    _refuse_unknown(parameters, APPLY_PARAMETERS, "texture.apply_to_blend")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"texture.apply_to_blend には outputs の {', '.join(missing)} が要ります")
    match = parameters.get("match")
    if not isinstance(match, str) or not match.strip():
        raise ToolError("texture.apply_to_blend には match（差し替える画像の名前）が要ります（既定は無い。作品ごとの値なのでレシピに書く）")
    minimum = number(parameters, "min_replacements", 1, minimum=1, integer=True)
    image_name = parameters.get("image_name", Path(inputs["texture"]).name)
    if not isinstance(image_name, str) or not image_name:
        raise ToolError(f"image_name は新しい画像の名前（空でない文字列）にしてください: {image_name!r}")
    if not str(outputs["blend"]).lower().endswith(".blend"):
        raise ToolError(f"outputs の blend は .blend にしてください: {outputs['blend']}")
    blender = _blender(parameters, context, "texture.apply_to_blend")
    settings_path = Path(context.step_dir) / "apply_texture_settings.json"
    atomic_json(settings_path, {"match": match, "min_replacements": minimum, "image_name": image_name})
    args = ["--blend", inputs["blend"], "--texture", inputs["texture"], "--out-blend", outputs["blend"],
            "--report", outputs["report"], "--settings", str(settings_path)]
    record = launcher.run_blender(blender, APPLY_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    if (not report.get("replacement_texture_packed") or not report.get("input_files_unchanged")
            or report.get("replaced_node_count", 0) < minimum):
        raise ToolError("画像の差し替えが、確かめの決まり（詰め込み・入力が変わらない・差し替えた数）を満たしていません: "
                        f"{ {k: report.get(k) for k in ('replacement_texture_packed', 'input_files_unchanged', 'replaced_node_count')} }")
    observation = {"kind": "apply_texture", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "replaced": report.get("replaced_node_count"),
                   "unrelated_unchanged": report.get("unrelated_image_nodes_verified_unchanged"),
                   "atlas_size_wh": report.get("atlas_size_wh"), "packed_texture_sha256": report.get("packed_texture_sha256")}
    return StepResult(observations=[observation])


@register("texture.render_view", inputs=("mesh", "texture"), outputs=("image", "report"),
          summary="テクスチャを貼ったメッシュを座標軸に沿った正射影で描く（照明なし。色の確認と目印を読むため）")
def render_view(inputs, parameters, outputs, context):
    require(inputs, ("mesh", "texture"), "texture.render_view")
    if "image" not in outputs:
        raise ToolError("outputs に image を宣言してください")
    background = parameters.get("background", [245, 245, 245])
    if not isinstance(background, list) or len(background) != 3:
        raise ToolError(f"background は [R, G, B] にしてください: {background!r}")
    image, report = texture.render_view(Mesh.load(inputs["mesh"]), texture.load_image(inputs["texture"]), _view(parameters),
                                        tuple(float(v) for v in background))
    texture.save_image(outputs["image"], image)
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    return StepResult(observations=[report])


@register("texture.draw_mask", inputs=("like",), outputs=("mask",),
          summary="多角形・楕円を塗った灰色のマスク画像を描く（大きさは size か入力 like の画像。縁は feather でぼかす）")
def draw_mask(inputs, parameters, outputs, context):
    if "mask" not in outputs:
        raise ToolError("outputs に mask を宣言してください")
    size = parameters.get("size")
    if "like" in inputs:
        like = texture.load_mask(inputs["like"])
        if size is not None and list(size) != [like.shape[1], like.shape[0]]:
            raise ToolError(f"size {size!r} が入力 like の大きさ {[like.shape[1], like.shape[0]]} と違います")
        size = [like.shape[1], like.shape[0]]
    mask = mask_from(parameters, size)
    texture.save_image(outputs["mask"], mask)
    report = {"kind": "draw_mask", "size": list(size), "shapes": len(parameters["shapes"]),
              "covered_pixels": int((mask > 0).sum()), "full_pixels": int((mask >= 255).sum())}
    return StepResult(observations=[report])


def load_occlusion(path, view):
    """texture.view_parts の手前の部品の地図を読む: (owner (H,W) int, depth (H,W), 名前の並び)。視点が違えば止める。"""
    import json
    import numpy as np
    try:
        with np.load(path, allow_pickle=False) as data:
            owner, depth, names, saved = data["owner"], data["depth"], [str(n) for n in data["names"]], json.loads(str(data["view"]))
    except (OSError, KeyError, ValueError) as error:
        raise ToolError(f"手前の部品の地図（owner・depth・names・view の npz）を読めません: {path}（{error}）") from error
    if _normal_view(saved) != _normal_view(view):
        raise ToolError(f"手前の部品の地図の視点 {saved} が、写す視点 {view} と違います（同じ view で texture.view_parts を回してください）")
    return owner, depth, names


def _normal_view(view):
    return (view["from"], tuple(float(v) for v in view["center"]), float(view["span"]), tuple(int(v) for v in view["size"]))


@register("texture.view_parts", inputs=("parts",), outputs=("occlusion", "preview", "report"),
          summary="部品ごとの npz（names と co{i}・f{i}）の部品を、視点（view）の正射影で描き、画素ごとにいちばん手前の部品の地図を作る"
                  "（occlusion: owner・depth・names・view の npz。texture.project_reference の入力 occlusion になり、ほかの部品に隠れた所へ"
                  "写さない）。preview は部品ごとの色の画像、report は部品ごとの三角形・窓にかかる三角形・手前の画素の数。parts で部品を絞れる")
def view_parts(inputs, parameters, outputs, context):
    import json
    import numpy as np
    from ..io import atomic_npz
    from ..mesh import Mesh, read_all_parts
    require(inputs, ("parts",), "texture.view_parts")
    _refuse_unknown(parameters, VIEW_PARTS_PARAMETERS, "texture.view_parts")
    if "occlusion" not in outputs and "preview" not in outputs:
        raise ToolError("texture.view_parts には outputs の occlusion（手前の部品の地図）か preview が要ります")
    view = _view(parameters)
    parts = read_all_parts(inputs["parts"])
    wanted = parameters.get("parts")
    if wanted is not None:
        if not isinstance(wanted, list) or not wanted or not all(isinstance(n, str) for n in wanted):
            raise ToolError(f"parts は部品の名前の並びにしてください: {wanted!r}")
        unknown = sorted(set(wanted) - {name for name, _, _ in parts})
        if unknown:
            raise ToolError(f"npz に無い部品です: {unknown}")
        parts = [part for part in parts if part[0] in wanted]
    triangles = []
    for name, vertices, faces in parts:
        if faces.shape[1] != 3:                          # 四角形以上は三角形に分ける
            faces = Mesh(vertices, faces.tolist()).triangles()[0]
        triangles.append((name, vertices, faces))
    margin = number(parameters, "margin", 2.0, minimum=0)
    background = number(parameters, "background", 245.0, minimum=0)
    owner, depth, report = texture.view_parts(triangles, view, margin)
    names = [name for name, _, _ in triangles]
    if "occlusion" in outputs:
        atomic_npz(outputs["occlusion"], owner=owner, depth=depth, names=np.array(names), view=np.array(json.dumps(view)))
    if "preview" in outputs:
        texture.save_image(outputs["preview"], texture.parts_preview(owner, background))
    report.update(schema_version="model3d-view-parts/0.1", names=names)
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "view_parts", "parts": len(names), "covered_pixels": report["covered_pixels"],
                                     "front": {r["name"]: r["front_pixels"] for r in report["parts"] if r["front_pixels"]}}])


@register("texture.project_reference", inputs=("mesh", "texture", "reference", "mask", "occlusion", "keep"),
          outputs=("texture", "weights", "report", "overlay"),
          selectable=("mesh",),
          summary="参照画像を、目印で合わせた正射影の視点からテクスチャへ写す（z バッファで見え隠れ、面の向き・マスク・アルファで重み。"
                  "select で役割の面だけ）。occlusion（texture.view_parts の手前の部品の地図）を渡すと、ほかの部品に隠れた所へ写さない"
                  "（自分の部品は occlusion_part、既定はメッシュの名前）。color_match（モデルの描画と参照画像の枠）で、参照画像の色を"
                  "CIELAB の中央値の差でモデルの色に合わせてから写す（keep の外はモデルの 1 色）。任意の review_note は unresolved に"
                  "残して needs_review にする")
def project_reference(inputs, parameters, outputs, context):
    require(inputs, ("mesh", "texture", "reference"), "texture.project_reference")
    if "texture" not in outputs:
        raise ToolError("outputs に texture を宣言してください")
    view, (matrix, offset, fitted), options, note = projection_settings(parameters)
    options = dict(options)
    mesh = Mesh.load(inputs["mesh"])
    chosen = selected(context, "mesh", parameters, "texture.project_reference")
    base, reference = texture.load_texture(inputs["texture"]), texture.load_image(inputs["reference"], alpha=True)
    mask = texture.load_mask(inputs["mask"]) if "mask" in inputs else None
    occlusion = None
    part = parameters.get("occlusion_part")
    if part is not None and (not isinstance(part, str) or not part):
        raise ToolError(f"occlusion_part は手前の部品の地図の部品の名前にしてください: {part!r}")
    if inputs.get("occlusion") is not None:
        owner, depth, names = load_occlusion(inputs["occlusion"], view)
        if part is not None and part not in names:
            raise ToolError(f"occlusion_part {part!r} が手前の部品の地図にありません（{names}）")
        own = part if part is not None else mesh.name
        occlusion = (owner, depth, names.index(own) if own in names else -1, names)
    elif part is not None:
        raise ToolError("occlusion_part は入力 occlusion（手前の部品の地図）と一緒に使ってください")
    matching = color_match_settings(parameters)
    matched = None
    if matching is not None:                             # 参照画像の色を、モデルの描画の色に合わせてから写す
        keep = texture.load_mask(inputs["keep"]) if inputs.get("keep") is not None else None
        front, _ = texture.render_view(mesh, base, view)
        reference, matched = texture.color_match(reference, front, matching["model"], matching["reference"], keep)
    elif inputs.get("keep") is not None:
        raise ToolError("入力 keep は color_match と一緒に使ってください")
    new, weights, report = texture.project_reference(
        mesh, base, reference, view, matrix, offset, faces=chosen["faces"] if chosen else None, mask=mask, occlusion=occlusion,
        **options)
    if occlusion is not None:
        report["occlusion"] = {"parts": occlusion[3], "self": occlusion[3][occlusion[2]] if occlusion[2] >= 0 else None}
    if matched is not None:
        report["color_match"] = matched
    report["alignment"] = fitted
    report["units"] = mesh.units
    if "overlay" in outputs:   # 位置合わせの確認: 元のテクスチャの描画に、合わせた参照画像を半分重ねる
        texture.save_image(outputs["overlay"], texture.aligned_overlay(mesh, base, reference, view, matrix, offset, mask=mask))
    texture.save_image(outputs["texture"], new)
    if "weights" in outputs:
        texture.save_image(outputs["weights"], weights * 255.0)
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    unresolved = []
    if not report["painted_texels"]:
        unresolved.append("参照画像の色が 1 画素も写りませんでした（視点・目印・選んだ面・マスクを確かめてください）")
    if note:
        unresolved.append(note)
    return StepResult(observations=[report], unresolved=unresolved, review=bool(unresolved))


EYE_LINE_PARAMETERS = {"eyes", "end_y", "erase_rows", "erase_columns", "sag", "thickness", "outer_bias", "wing", "lashes", "color",
                       "supersample", "close_px", "close_blur", "band_blur", "base_width", "strokes", "skin_fill"}
STROKE_KEYS = ["color", "curve", "name", "points", "samples", "span", "widths"]
SKIN_FILL_KEYS = ["boxes", "exclude", "exclude_all", "exclude_boxes", "feather", "gain", "grow", "keep_dark_below", "sigmas", "skin", "soften"]
SKIN_RULE_KEYS = ["red_at_least_blue", "red_minus_green_below", "rgb_min"]


def _color(value, where):
    if not isinstance(value, list) or len(value) != 3 or not all(
            not isinstance(v, bool) and isinstance(v, (int, float)) and 0 <= v <= 255 for v in value):
        raise ToolError(f"{where} は線の色 [R, G, B]（0〜255）にしてください: {value!r}")
    return value


def _point(value, where):
    if not isinstance(value, list) or len(value) != 2:
        raise ToolError(f"{where} は点 [x, y]（画素）にしてください: {value!r}")
    return [_finite(v, where) for v in value]


def stroke_settings(strokes):
    """strokes（点の列に沿って太さの変わる線の並び）を確かめ、eye_line.STROKE_DEFAULTS で埋めた並びにする。"""
    from .. import eye_line
    if not isinstance(strokes, list) or not strokes:
        raise ToolError(f"strokes は線 {{\"points\": [[x, y], …], \"widths\": …}} の並び（1 本以上）にしてください: {strokes!r}")
    checked = []
    for k, stroke in enumerate(strokes):
        where = f"strokes[{k}]"
        if not isinstance(stroke, dict):
            raise ToolError(f"{where} は {{\"points\", \"widths\", \"curve\", \"samples\", \"span\", \"color\"}} にしてください: {stroke!r}")
        _refuse_unknown(stroke, STROKE_KEYS, f"texture.closed_eye_line の {where}")
        merged = {**eye_line.STROKE_DEFAULTS, **{key: v for key, v in stroke.items() if key != "name"}}
        points = merged.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise ToolError(f"{where}.points は 2 点以上の [[x, y], …]（画素）にしてください: {points!r}")
        merged["points"] = [_point(p, f"{where}.points") for p in points]
        if not isinstance(merged["curve"], bool):
            raise ToolError(f"{where}.curve は true か false にしてください: {merged['curve']!r}")
        if merged["curve"] and len(points) != 3:
            raise ToolError(f"{where}.curve は 3 点（始め・真ん中で通る点・終わり）のときだけ使えます: {len(points)} 点")
        samples = merged["samples"]
        if isinstance(samples, bool) or not isinstance(samples, int) or samples < 2:
            raise ToolError(f"{where}.samples は 2 以上の整数にしてください: {samples!r}")
        span = merged["span"]
        if span is not None:
            if not isinstance(span, list) or len(span) != 2 or not all(
                    not isinstance(v, bool) and isinstance(v, (int, float)) and 0 <= v <= 1 for v in span) or span[0] >= span[1]:
                raise ToolError(f"{where}.span は [始め, 終わり]（0〜1、始め < 終わり）にしてください: {span!r}")
        widths = merged.get("widths")
        if isinstance(widths, dict):
            _refuse_unknown(widths, ["ends", "middle"], f"texture.closed_eye_line の {where}.widths")
            if set(widths) != {"ends", "middle"}:
                raise ToolError(f"{where}.widths の弓の形は {{\"middle\": 太さ, \"ends\": 太さ}} にしてください: {widths!r}")
            merged["widths"] = {key: _finite(widths[key], f"{where}.widths.{key}", 0.0) for key in ("middle", "ends")}
        elif isinstance(widths, list) and widths and all(isinstance(w, list) and len(w) == 2 for w in widths):
            at = [_finite(w[0], f"{where}.widths の位置", 0.0) for w in widths]
            if any(a > 1 for a in at) or any(b <= a for a, b in zip(at, at[1:])):
                raise ToolError(f"{where}.widths の位置は 0〜1 で、増える順にしてください: {at!r}")
            merged["widths"] = [[a, _finite(w[1], f"{where}.widths の太さ", 0.0)] for a, w in zip(at, widths)]
        else:
            raise ToolError(f"{where}.widths は [[位置 0〜1, 太さ], …] か {{\"middle\", \"ends\"}} にしてください: {widths!r}")
        if merged["color"] is not None:
            merged["color"] = _color(merged["color"], f"{where}.color")
        checked.append(merged)
    return checked


def skin_fill_settings(value):
    """skin_fill（目の枠を、まわりの肌の色で埋める消し方）を確かめ、eye_line.SKIN_FILL_DEFAULTS で埋める。"""
    from .. import eye_line
    if not isinstance(value, dict):
        raise ToolError(f"skin_fill は {{\"boxes\": [[x0, y0, x1, y1], …], \"skin\": {{\"rgb_min\": [R, G, B], …}}}} にしてください: {value!r}")
    _refuse_unknown(value, SKIN_FILL_KEYS, "texture.closed_eye_line の skin_fill")
    missing = [key for key in eye_line.SKIN_FILL_REQUIRED if value.get(key) is None]
    if missing:                                          # 顔と目の大きさに寄る値は既定を置かない
        raise ToolError(f"skin_fill に {', '.join(missing)} を書いてください（既定は無い。boxes: 目の枠、skin: 肌らしい画素の決まり、"
                        "sigmas: 肌の色を探すぼかしの広さ（画素）の並び、exclude: 枠のまわりの肌の色を取らない幅、grow: 埋める範囲を枠から広げる幅）")
    merged = {**eye_line.SKIN_FILL_DEFAULTS, **value}
    boxes = merged.get("boxes")
    if not isinstance(boxes, list) or not boxes:
        raise ToolError(f"skin_fill.boxes は目の枠 [x0, y0, x1, y1]（画素。右と下は含まない）の並びにしてください: {boxes!r}")
    excluded = merged.get("exclude_boxes")
    if excluded is not None and (not isinstance(excluded, list) or len(excluded) != len(boxes)):
        raise ToolError(f"skin_fill.exclude_boxes は boxes と同じ数の枠（肌の色を取らない所）にしてください: {excluded!r}")
    for name, group in (("boxes", boxes), ("exclude_boxes", excluded or [])):
        for k, box in enumerate(group):
            if (not isinstance(box, list) or len(box) != 4 or not all(not isinstance(v, bool) and isinstance(v, (int, float)) for v in box)
                    or box[0] >= box[2] or box[1] >= box[3]):
                raise ToolError(f"skin_fill.{name}[{k}] は [x0, y0, x1, y1]（x0 < x1、y0 < y1）にしてください: {box!r}")
    rule = merged.get("skin")
    if not isinstance(rule, dict) or "rgb_min" not in rule:
        raise ToolError("skin_fill.skin に肌らしい画素の決まり {\"rgb_min\": [R, G, B], \"red_minus_green_below\": 数, "
                        f"\"red_at_least_blue\": true}} を書いてください（既定は無い）: {rule!r}")
    _refuse_unknown(rule, SKIN_RULE_KEYS, "texture.closed_eye_line の skin_fill.skin")
    _color(rule["rgb_min"], "skin_fill.skin.rgb_min")
    if rule.get("red_minus_green_below") is not None:
        _finite(rule["red_minus_green_below"], "skin_fill.skin.red_minus_green_below")
    if not isinstance(rule.get("red_at_least_blue", False), bool):
        raise ToolError(f"skin_fill.skin.red_at_least_blue は true か false にしてください: {rule['red_at_least_blue']!r}")
    if merged["keep_dark_below"] is not None:
        _finite(merged["keep_dark_below"], "skin_fill.keep_dark_below", 0.0)
    if not isinstance(merged["exclude_all"], bool):
        raise ToolError(f"skin_fill.exclude_all は true か false にしてください: {merged['exclude_all']!r}")
    sigmas = merged["sigmas"]
    if not isinstance(sigmas, list) or not sigmas:
        raise ToolError(f"skin_fill.sigmas はぼかしの広さ（画素）の並びにしてください: {sigmas!r}")
    merged["sigmas"] = [_finite(v, "skin_fill.sigmas", 0.0, True) for v in sigmas]
    for key, minimum, exclusive in (("gain", 0.0, True), ("feather", 0.0, True), ("soften", 0.0, True), ("exclude", 0.0, False),
                                    ("grow", 0.0, False)):
        merged[key] = _finite(merged[key], f"skin_fill.{key}", minimum, exclusive)
    return merged


def _finite(value, where, minimum=None, exclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{where} は有限の数にしてください: {value!r}")
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        raise ToolError(f"{where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    return value


def _span(value, where):
    if not isinstance(value, list) or len(value) != 2 or value[0] >= value[1]:
        raise ToolError(f"{where} は [始め, 終わり]（始め < 終わり。画素）にしてください: {value!r}")
    return [_finite(v, where) for v in value]


def eye_line_settings(parameters):
    """texture.closed_eye_line のパラメーターを確かめ、eye_line.draw の設定にする（省いた値は eye_line.DEFAULTS）。"""
    from .. import eye_line
    _refuse_unknown(parameters, sorted(EYE_LINE_PARAMETERS), "texture.closed_eye_line")
    eyes = parameters.get("eyes")
    strokes = parameters.get("strokes")
    if eyes is None and strokes is None:
        raise ToolError("texture.closed_eye_line には、弧を描く eyes（目ごとの [目頭の x, 目尻の x]）か、線の並び strokes の"
                        "どちらかが要ります")
    if eyes is not None:
        if not isinstance(eyes, list) or not eyes:
            raise ToolError(f"eyes は目ごとの [目頭の x, 目尻の x]（画素）の並びにしてください: {eyes!r}")
        for k, eye in enumerate(eyes):
            if not isinstance(eye, list) or len(eye) != 2 or eye[0] == eye[1]:
                raise ToolError(f"eyes[{k}] は [目頭の x, 目尻の x]（違う 2 つの数）にしてください: {eye!r}")
            [_finite(v, f"eyes[{k}]") for v in eye]
    settings = {**eye_line.DEFAULTS, "eyes": eyes}
    if eyes is not None:
        settings["end_y"] = _finite(parameters.get("end_y"), "end_y")
    elif parameters.get("end_y") is not None:
        raise ToolError("end_y は弧の両端の高さです。eyes と一緒に書いてください")
    if parameters.get("skin_fill") is not None:          # 消し方の選択肢: 目の枠を、まわりの肌の色で埋める
        if parameters.get("erase_rows") is not None or parameters.get("erase_columns") is not None:
            raise ToolError("skin_fill（目の枠を肌の色で埋める）と、消す帯（erase_rows・erase_columns）は一緒に使えません（消し方はどちらか 1 つ）")
        settings["skin_fill"] = skin_fill_settings(parameters["skin_fill"])
    elif eyes is not None or parameters.get("erase_rows") is not None or parameters.get("erase_columns") is not None:
        settings.update(erase_rows=_span(parameters.get("erase_rows"), "erase_rows"),
                        erase_columns=_span(parameters.get("erase_columns"), "erase_columns"))
    else:                                                # strokes だけなら、消さずに線だけを描ける
        settings.update(erase_rows=None, erase_columns=None)
    if strokes is not None:
        settings["strokes"] = stroke_settings(strokes)
    if parameters.get("sag") is not None:                 # 負なら上へたわむ弧（笑顔の閉じ目）
        settings["sag"] = _finite(parameters["sag"], "sag")
    for key, minimum, exclusive in (("thickness", 0.0, True), ("close_blur", 0.0, True), ("band_blur", 0.0, True),
                                    ("base_width", 0.0, True)):
        if parameters.get(key) is not None:
            settings[key] = _finite(parameters[key], key, minimum, exclusive)
    if "outer_bias" in parameters:
        settings["outer_bias"] = _finite(parameters["outer_bias"], "outer_bias")
        if not -1 < settings["outer_bias"] < 1:
            raise ToolError(f"outer_bias は −1 より大きく 1 より小さくしてください: {settings['outer_bias']!r}")
    for key, low in (("supersample", 1), ("close_px", 1)):
        if key in parameters:
            settings[key] = number(parameters, key, minimum=low, integer=True)
    if settings["supersample"] > 8:
        raise ToolError(f"supersample は 1〜8 にしてください: {settings['supersample']}")
    if "wing" in parameters:
        wing = parameters["wing"]
        if not isinstance(wing, dict):
            raise ToolError(f"wing は {{\"length\", \"angle_deg\", \"width\"}} にしてください: {wing!r}")
        _refuse_unknown(wing, ["angle_deg", "length", "width"], "texture.closed_eye_line の wing")
        merged = {**eye_line.DEFAULTS["wing"], **wing}
        settings["wing"] = {"length": _finite(merged["length"], "wing.length", 0.0), "angle_deg": _finite(merged["angle_deg"], "wing.angle_deg"),
                            "width": _finite(merged["width"], "wing.width", 0.0)}
    if "lashes" in parameters:
        lashes = parameters["lashes"]
        if not isinstance(lashes, dict):
            raise ToolError(f"lashes は {{\"at\", \"length\", \"width\", \"lean_deg\"}} にしてください: {lashes!r}")
        _refuse_unknown(lashes, ["at", "lean_deg", "length", "width"], "texture.closed_eye_line の lashes")
        merged = {**eye_line.DEFAULTS["lashes"], **lashes}
        at, length = merged["at"], merged["length"]
        if not isinstance(at, list) or not isinstance(length, list) or len(at) != len(length):
            raise ToolError(f"lashes.at と lashes.length は同じ長さの並びにしてください: {at!r} {length!r}")
        settings["lashes"] = {"at": [_finite(v, "lashes.at", 0.0) for v in at], "length": [_finite(v, "lashes.length", 0.0) for v in length],
                              "width": _finite(merged["width"], "lashes.width", 0.0), "lean_deg": _finite(merged["lean_deg"], "lashes.lean_deg")}
    if "color" in parameters:
        color = parameters["color"]
        if not isinstance(color, list) or len(color) != 3 or not all(
                not isinstance(v, bool) and isinstance(v, (int, float)) and 0 <= v <= 255 for v in color):
            raise ToolError(f"color は線の色 [R, G, B]（0〜255）にしてください: {color!r}")
        settings["color"] = color
    return settings


@register("texture.closed_eye_line", inputs=("image",), outputs=("image", "report", "mask"),
          summary="目を閉じた顔の絵の、閉じた目の線を 2D の描き方で描き直す（Blender なし）: 消す帯（erase_rows × erase_columns）の中の元の線を"
                  "まわりの肌の色で埋め、目ごと（eyes の [目頭の x, 目尻の x]）に、下にたわんだ太い弧（end_y・sag・thickness。sag が負なら上へ"
                  "たわむ笑顔の弧）・目尻のはね（wing）・下向きのまつ毛（lashes）を color で描く。strokes で点の列に沿って太さの変わる線（しわ・"
                  "涙袋・半目の縁）を重ね、skin_fill で目の枠をまわりの肌らしい画素から埋める消し方も選べる。mask に描き直した所の重みの PNG。"
                  "長さと位置は幅 base_width（既定は絵の幅）の絵の画素。既定の線の形は試作の値")
def closed_eye_line(inputs, parameters, outputs, context):
    from PIL import Image
    import numpy as np
    from .. import eye_line
    require(inputs, ("image",), "texture.closed_eye_line")
    if "image" not in outputs:
        raise ToolError("texture.closed_eye_line には outputs の image が要ります")
    if Path(outputs["image"]).suffix.lower() != ".png":   # 拡張子で書き方が決まる（JPEG などは描き直していない所の画素まで変える）
        raise ToolError(f"texture.closed_eye_line の outputs の image は .png にしてください: {Path(outputs['image']).name}")
    settings = eye_line_settings(parameters)
    loaded = texture.load_texture(inputs["image"])          # 透けのある絵は (H, W, 4)
    image = loaded[..., :3]
    if "mask" in outputs and Path(outputs["mask"]).suffix.lower() != ".png":
        raise ToolError(f"texture.closed_eye_line の outputs の mask は .png にしてください: {Path(outputs['mask']).name}")
    try:
        drawn = eye_line.draw(image, settings, with_mask="mask" in outputs)
    except ValueError as error:
        raise ToolError(str(error)) from error
    result, report = drawn[0], drawn[1]
    pixels = np.clip(result + 0.5, 0, 255).astype(np.uint8)   # 台本と同じ丸め（+0.5 を切り捨て）
    if loaded.shape[2] == 4:                              # 透けはそのまま残す（project_reference は参照の絵の透けを重みに使う）
        pixels = np.dstack([pixels, loaded[..., 3].astype(np.uint8)])
    Image.fromarray(pixels).save(outputs["image"], format="PNG")
    if "mask" in outputs:                                 # 描き直した所の重み（0〜255。texture.project_reference の mask に渡せる）
        Image.fromarray(np.clip(drawn[2] * 255 + 0.5, 0, 255).astype(np.uint8), "L").save(outputs["mask"], format="PNG")
    report = {"kind": "closed_eye_line", "size": [int(image.shape[1]), int(image.shape[0])], **report, "alpha_kept": loaded.shape[2] == 4,
              "settings": settings}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    unresolved = [] if report["line_pixels"] else ["線が 1 画素も描けませんでした（eyes・end_y・base_width を確かめてください）"]
    return StepResult(observations=[{k: report[k] for k in ("kind", "size", "changed_box", "changed_pixels", "line_pixels")}],
                      unresolved=unresolved, review=bool(unresolved))


WARP_MOUTH_PARAMETERS = ["bulge", "corner_lift", "corner_mark", "inside_color", "jaw", "level_tilt", "line", "lips", "open", "scale", "scale_x",
                         "tilt_after_stretch",
                         "tongue_color", "upper_arch", "upper_sag", "window"]


def _rgb(value, where):
    if not isinstance(value, list) or len(value) != 3 or not all(
            not isinstance(v, bool) and isinstance(v, (int, float)) and 0 <= v <= 255 for v in value):
        raise ToolError(f"{where} は色 [R, G, B]（0〜255）にしてください: {value!r}")
    return value


def warp_mouth_settings(parameters):
    """texture.warp_mouth のパラメーターを確かめ、mouth_warp.warp の設定にする。計算の作りの長さ（mouth_warp.LENGTHS・JAW）は scale 倍。
    顔の形に寄る長さ（lips・jaw の必須のキー）は、書いた値のまま（scale は掛けない）。"""
    from .. import color_marks, mouth_warp
    _refuse_unknown(parameters, WARP_MOUTH_PARAMETERS, "texture.warp_mouth")
    window = parameters.get("window")
    if (not isinstance(window, list) or len(window) != 4 or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in window)
            or window[0] >= window[2] or window[1] >= window[3]):
        raise ToolError(f"window はもとの口を探す範囲 [x0, y0, x1, y1]（0 以上の整数の画素、x0 < x1、y0 < y1）にしてください: {window!r}")
    line = parameters.get("line")
    if not isinstance(line, dict) or line.get("red_minus_green_above") is None:
        raise ToolError("line にもとの口の線の色の決まり {\"red_minus_green_above\": 数, \"red_above\": 数, \"green_below\": 数} を書いてください"
                        f"（既定は無い）: {line!r}")
    _refuse_unknown(line, list(color_marks.RULE_KEYS), "texture.warp_mouth の line")
    line = {key: _finite(line[key], f"line.{key}") for key in color_marks.RULE_KEYS if line.get(key) is not None}
    scale = _finite(parameters.get("scale", 1.0), "scale", 0.0, True)
    settings = {"window": window, "line": line, "scale": scale,
                "scale_x": _finite(parameters.get("scale_x", 1.0), "scale_x", 0.0, True),
                "corner_lift": _finite(parameters.get("corner_lift", 0.0), "corner_lift"),
                "upper_sag": _finite(parameters.get("upper_sag", 0.0), "upper_sag"),
                "upper_arch": _finite(parameters.get("upper_arch", 0.0), "upper_arch"),
                "open": _finite(parameters.get("open", 0.0), "open", 0.0),
                "bulge": _finite(parameters.get("bulge", 2.0), "bulge", 0.0, True),
                "level_tilt": parameters.get("level_tilt", True),
                "tilt_after_stretch": parameters.get("tilt_after_stretch", False),
                "lengths": {key: value * scale for key, value in mouth_warp.LENGTHS.items()}}
    lips = parameters.get("lips")                        # 顔の形に寄る長さ（唇の高さ）。既定は無い
    if not isinstance(lips, dict) or set(lips) != set(mouth_warp.LIPS_KEYS):
        raise ToolError("lips に、口の線のまわりの唇の高さ（画素）{\"above\": 線の上で横に伸ばしきる幅, \"below\": 線の下で横に伸ばしきる幅, "
                        "\"upper_keep\": 上を動かしきる幅, \"lower_keep\": 下を動かしきる幅（下唇の赤みと光）} を全部書いてください"
                        f"（既定は無い）: {lips!r}")
    settings["lengths"].update({key: _finite(lips[key], f"lips.{key}", 0.0) for key in mouth_warp.LIPS_KEYS})
    if not isinstance(settings["level_tilt"], bool):
        raise ToolError(f"level_tilt は true か false にしてください: {settings['level_tilt']!r}")
    if not isinstance(settings["tilt_after_stretch"], bool):
        raise ToolError(f"tilt_after_stretch は true か false にしてください: {settings['tilt_after_stretch']!r}")
    needs_mark = settings["open"] > 0 or settings["upper_arch"] != 0 or settings["corner_lift"] < 0
    if parameters.get("corner_mark") is not None:
        settings["corner_mark"] = _finite(parameters["corner_mark"], "corner_mark", 0.0)
    elif needs_mark:
        raise ToolError("open・upper_arch・負の corner_lift には、口角の印の長さ corner_mark（画素。すき間はこの内側で 0 になる）が要ります")
    if settings["open"] > 0:
        for key in ("inside_color", "tongue_color"):
            if parameters.get(key) is None:
                raise ToolError(f"開ける口（open > 0）には {key}（[R, G, B]）が要ります（既定は無い）")
            settings[key] = _rgb(parameters[key], key)
    jaw = parameters.get("jaw")
    if jaw is not None:
        if not isinstance(jaw, dict) or any(jaw.get(key) is None for key in mouth_warp.JAW_KEYS):
            raise ToolError("jaw は {\"green_below\": 首の色の緑の値の上限, \"start_below_line\": 口の線の何画素下から探すか, "
                            "\"keep_above\": 動きを 0 に戻すあごの線からの高さ, \"mask_above\": 写さないあごの線からの高さ} にしてください"
                            f"（既定は無い）: {jaw!r}")
        _refuse_unknown(jaw, [*mouth_warp.JAW_KEYS, *mouth_warp.JAW], "texture.warp_mouth の jaw")
        merged = {**{key: value * scale for key, value in mouth_warp.JAW.items()}, **jaw}
        settings["jaw"] = {key: _finite(merged[key], f"jaw.{key}", 0.0) for key in merged}
        if settings["jaw"]["blur"] <= 0 or settings["jaw"]["mask_fade"] <= 0:
            raise ToolError("jaw.blur と jaw.mask_fade は 0 より大きくしてください")
    return settings


@register("texture.warp_mouth", inputs=("image",), outputs=("image", "mask", "report"),
          summary="顔が絵のモデルの正面の描画の上で、口の表情を、もとの口の絵を変形して作る（Blender なし）: もとの口の線を色（line）で測り"
                  "（window の中のいちばん大きいかたまり）、横に scale_x 倍し、線を境に上と下を別々に上下へ動かす（口角 corner_lift・上唇の"
                  "真ん中 upper_sag・upper_arch・開ける量 open。線から離れるほど 0 へ戻す）。すき間は口の中の色（inside_color）と舌"
                  "（tongue_color）で埋め、口角の印（corner_mark）の内側で 0 にする。jaw であごの線の内側に収める。mask に写す範囲。"
                  "texture.project_reference で色の絵へ写す")
def warp_mouth(inputs, parameters, outputs, context):
    from PIL import Image
    import numpy as np
    from .. import mouth_warp
    require(inputs, ("image",), "texture.warp_mouth")
    for key in ("image", "mask"):
        if key not in outputs:
            raise ToolError(f"texture.warp_mouth には outputs の {key} が要ります")
        if Path(outputs[key]).suffix.lower() != ".png":
            raise ToolError(f"texture.warp_mouth の outputs の {key} は .png にしてください: {Path(outputs[key]).name}")
    settings = warp_mouth_settings(parameters)
    loaded = texture.load_texture(inputs["image"])
    image = loaded[..., :3]
    x0, y0, x1, y1 = settings["window"]
    if x1 > image.shape[1] or y1 > image.shape[0]:
        raise ToolError(f"window {settings['window']} が、絵（{image.shape[1]}×{image.shape[0]}）の外にはみ出しています")
    try:
        warped, mask, report = mouth_warp.warp(image, settings)
    except ValueError as error:
        raise ToolError(str(error)) from error
    pixels = np.clip(warped + 0.5, 0, 255).astype(np.uint8)
    if loaded.shape[2] == 4:
        pixels = np.dstack([pixels, loaded[..., 3].astype(np.uint8)])
    Image.fromarray(pixels).save(outputs["image"], format="PNG")
    Image.fromarray(np.clip(mask * 255 + 0.5, 0, 255).astype(np.uint8), "L").save(outputs["mask"], format="PNG")
    report = {"kind": "warp_mouth", "size": [int(image.shape[1]), int(image.shape[0])], **report, "settings": settings}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    unresolved = []
    if report["mask_box"] is None:
        unresolved.append("口が 1 画素も動きませんでした（scale_x・corner_lift・open などを確かめてください）")
    if report["columns_not_above_jaw"]:
        unresolved.append(f"あごの線の上で動きを 0 に戻せない列が {report['columns_not_above_jaw']} あります（あごの線を越えて動かしています）")
    return StepResult(observations=[{k: report[k] for k in ("kind", "size", "left", "right", "centre_x", "line_y_mean", "mask_box",
                                                            "opened_pixels")}], unresolved=unresolved, review=bool(unresolved))
