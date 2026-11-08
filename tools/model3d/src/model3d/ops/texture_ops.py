"""テクスチャの操作: 参照画像（設定画など）を UV テクスチャへ写す、テクスチャを貼ったメッシュを正射影で描く、マスクを描く、
GLB から材質のテクスチャを取り出す。"""
from pathlib import Path

from .. import glb, texture
from ..io import ToolError, atomic_json, sha256
from ..mesh import Mesh
from . import StepResult, number, register, require, selected

EXTRACT_PARAMETERS = ("slot", "material", "decode")


PROJECTION_PARAMETERS = ("view", "alignment", "min_facing", "facing_ramp", "depth_tolerance", "expand", "max_stretch",
                         "review_note")
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


@register("texture.project_reference", inputs=("mesh", "texture", "reference", "mask"),
          outputs=("texture", "weights", "report", "overlay"),
          selectable=("mesh",),
          summary="参照画像を、目印で合わせた正射影の視点からテクスチャへ写す（z バッファで見え隠れ、面の向き・マスク・アルファで重み。"
                  "select で役割の面だけ）。任意の review_note は unresolved に残して needs_review にする")
def project_reference(inputs, parameters, outputs, context):
    require(inputs, ("mesh", "texture", "reference"), "texture.project_reference")
    if "texture" not in outputs:
        raise ToolError("outputs に texture を宣言してください")
    view, (matrix, offset, fitted), options, note = projection_settings(parameters)
    mesh = Mesh.load(inputs["mesh"])
    chosen = selected(context, "mesh", parameters, "texture.project_reference")
    base, reference = texture.load_texture(inputs["texture"]), texture.load_image(inputs["reference"], alpha=True)
    mask = texture.load_mask(inputs["mask"]) if "mask" in inputs else None
    new, weights, report = texture.project_reference(
        mesh, base, reference, view, matrix, offset, faces=chosen["faces"] if chosen else None, mask=mask, **options)
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
