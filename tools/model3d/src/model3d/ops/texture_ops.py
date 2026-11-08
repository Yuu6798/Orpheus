"""テクスチャの操作: 参照画像（設定画など）を UV テクスチャへ写す、テクスチャを貼ったメッシュを正射影で描く、マスクを描く、
GLB から材質のテクスチャを取り出す。"""
from pathlib import Path

from .. import glb, texture
from ..io import ToolError, atomic_json, sha256
from ..mesh import Mesh
from . import StepResult, number, register, require, selected

EXTRACT_PARAMETERS = ("slot", "material", "decode")
APPLY_PARAMETERS = ("match", "min_replacements", "image_name", "blender", "timeout")
APPLY_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "apply_texture.py"


PROJECTION_PARAMETERS = ("view", "alignment", "min_facing", "facing_ramp", "depth_tolerance", "expand", "max_stretch",
                         "review_note", "occlusion_part")
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
    note = parameters.get("review_note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise ToolError("texture.project_reference の review_note は空でない文字列にしてください")
    return view, fitted, options, note


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


@register("texture.project_reference", inputs=("mesh", "texture", "reference", "mask", "occlusion"),
          outputs=("texture", "weights", "report", "overlay"),
          selectable=("mesh",),
          summary="参照画像を、目印で合わせた正射影の視点からテクスチャへ写す（z バッファで見え隠れ、面の向き・マスク・アルファで重み。"
                  "select で役割の面だけ）。occlusion（texture.view_parts の手前の部品の地図）を渡すと、ほかの部品に隠れた所へ写さない"
                  "（自分の部品は occlusion_part、既定はメッシュの名前）。任意の review_note は unresolved に残して needs_review にする")
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
    new, weights, report = texture.project_reference(
        mesh, base, reference, view, matrix, offset, faces=chosen["faces"] if chosen else None, mask=mask, occlusion=occlusion,
        **options)
    if occlusion is not None:
        report["occlusion"] = {"parts": occlusion[3], "self": occlusion[3][occlusion[2]] if occlusion[2] >= 0 else None}
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
