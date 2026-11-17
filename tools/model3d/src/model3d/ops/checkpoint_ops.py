"""キャラを作る段の確認地点で測る・直す工程（docs/model3d.md の「キャラを作る段の確認地点」）。キャラを作る道具（画像 → 候補 →
組み立て → VRM）から受け取った物を、選ぶ前・直す前に数で確かめ、受け取った VRM の作りの癖を直す（3 作目の VRM のダンスで、
リポジトリの外の台本でしていたもの）。測る工程は数と絵を出すだけで、良し悪しは決めない。

- vrm.color_factor: VRM の材質（MToon）の色の掛け率を 1 に戻し、影の色も同じ割合で戻す。
- model.neck_junction: 首と頭のつなぎ目を直す（首の上の端を頭の骨へ付け替え、頭の中へ伸びる栓を足す）。
- check.illustration: 立ち絵か T ポーズの体の絵の、人物の比率を測る（numpy と Pillow。model3d/checkpoint.py）。
- check.body_image・check.face_image: 体の絵と顔の絵を、元絵と同じ物差しで比べる（numpy と Pillow）。
"""
import math
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

COLOR_FACTOR_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "color_factor.py"
COLOR_FACTOR_PARAMETERS = {"materials", "blender", "addons", "blender_extensions", "timeout"}


def _names(value, where):
    if (not isinstance(value, list) or not value or not all(isinstance(name, str) and name for name in value)
            or len(set(value)) != len(value)):
        raise ToolError(f"{where} は名前（空でない・重ならない文字列）の並びにしてください: {value!r}")
    return value


def _refuse_unknown(parameters, allowed, operation):
    unknown = set(parameters) - set(allowed)
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(allowed)}）")


def _outputs(outputs, names, operation):
    missing = [name for name in names if name not in outputs]
    if missing:
        raise ToolError(f"{operation} には outputs の {', '.join(missing)} が要ります")


@register("vrm.color_factor", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="VRM の材質（MToon）の色の掛け率 baseColorFactor の RGB を 1 に戻し、影の色 shadeColorFactor に同じ割合を掛ける"
                  "（1 を超えない）。Blender の中で VRM Add-on の設定を書き換える。materials の材質だけで、無い材質・MToon でない材質は止める。"
                  "色の絵は変えない（テクスチャの色をそのまま描く描き方は変わらない）。報告に MToon の全部の材質の前と後の値。"
                  "元のファイルは変えない。Blender が要り、再利用しない（毎回実行）")
def color_factor(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    from .vrm_ops import VRM_ADDON
    require(inputs, ("blend",), "vrm.color_factor")
    _refuse_unknown(parameters, COLOR_FACTOR_PARAMETERS, "vrm.color_factor")
    _outputs(outputs, ("blend", "report"), "vrm.color_factor")
    settings = {"materials": _names(parameters.get("materials"), "materials")}
    blender = _blender(parameters, context, "vrm.color_factor")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    atomic_json(step_dir / "color_factor_settings.json", settings)
    record = launcher.run_blender(blender, COLOR_FACTOR_SCRIPT, None, ["--blend", inputs["blend"], "--settings",
                                                                       step_dir / "color_factor_settings.json", "--out-blend",
                                                                       outputs["blend"], "--report", outputs["report"]],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "color_factor", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "changed": report.get("changed"),
                   "materials": {name: {"before": (report.get("before") or {}).get(name), "after": (report.get("after") or {}).get(name)}
                                 for name in settings["materials"]}}
    return StepResult(observations=[observation])


NECK_JUNCTION_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "neck_junction.py"
NECK_JUNCTION_PARAMETERS = {"body", "neck_bone", "head_bone", "armature", "remove_groups", "radius", "ramp_from_z", "head_weight_top", "head_weight",
                            "plug_from_below_top", "plug_height", "plug_rings", "plug_segments", "plug_inset", "plug_top_scale",
                            "uv_band", "recolour_below", "slab", "cap", "blender", "addons", "blender_extensions", "timeout"}


def _name(value, where):
    if not isinstance(value, str) or not value:
        raise ToolError(f"{where} は名前（空でない文字列）にしてください: {value!r}")
    return value


def _real(parameters, key, minimum=None, exclusive=False, maximum=None, required=False):
    """パラメーター key の有限の数（無ければ None。required なら止める）。"""
    if key not in parameters:
        if required:
            raise ToolError(f"パラメーター {key} が要ります")
        return None
    value = parameters[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{key} は有限の数にしてください: {value!r}")
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        raise ToolError(f"{key} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    if maximum is not None and value > maximum:
        raise ToolError(f"{key} は {maximum} 以下にしてください: {value!r}")
    return value


def neck_junction_settings(parameters):
    """model.neck_junction のパラメーターを確かめ、台本の settings にする（長さは m。首の直しの量に既定は無い）。"""
    _refuse_unknown(parameters, NECK_JUNCTION_PARAMETERS, "model.neck_junction")
    settings = {key: _name(parameters.get(key), key) for key in ("body", "neck_bone", "head_bone")}
    if settings["neck_bone"] == settings["head_bone"]:
        raise ToolError("neck_bone と head_bone は別の骨にしてください")
    if parameters.get("armature") is not None:
        settings["armature"] = _name(parameters["armature"], "armature")
    if "remove_groups" in parameters:
        remove = parameters["remove_groups"]
        if (not isinstance(remove, list) or not all(isinstance(n, str) and n for n in remove) or len(set(remove)) != len(remove)):
            raise ToolError(f"remove_groups は頂点グループの名前（空でない・重ならない文字列）の並びにしてください: {remove!r}")
        if {settings["neck_bone"], settings["head_bone"]} & set(remove):
            raise ToolError("remove_groups に neck_bone・head_bone を入れないでください（その重みへ付け替える）")
        settings["remove_groups"] = remove
    settings["ramp_from_z"] = _real(parameters, "ramp_from_z", required=True)
    for key in ("radius", "plug_height"):
        settings[key] = _real(parameters, key, 0.0, exclusive=True, required=True)
    settings["plug_from_below_top"] = _real(parameters, "plug_from_below_top", 0.0, required=True)
    settings["head_weight_top"] = _real(parameters, "head_weight_top", 0.0, exclusive=True, maximum=1.0, required=True)
    if "head_weight" in parameters:
        if parameters["head_weight"] not in ("add", "set"):
            raise ToolError(f"head_weight は add（もとの重みを縮めて頭に足す。既定）か set（頭の重みをその値にする）にしてください: "
                            f"{parameters['head_weight']!r}")
        settings["head_weight"] = parameters["head_weight"]
    for key, minimum, exclusive in (("plug_inset", 0.0, True), ("plug_top_scale", 0.0, False), ("uv_band", 0.0, True),
                                    ("recolour_below", 0.0, False), ("slab", 0.0, True), ("cap", 0.0, False)):
        value = _real(parameters, key, minimum, exclusive)
        if value is not None:
            settings[key] = value
    for key, minimum in (("plug_rings", 1), ("plug_segments", 3)):
        if key in parameters:
            value = parameters[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ToolError(f"{key} は {minimum} 以上の整数にしてください: {value!r}")
            settings[key] = value
    return settings


@register("model.neck_junction", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="首と頭のつなぎ目を直す（Blender の中で、体のメッシュ body だけ）: remove_groups（目の骨など）の重みを外し、首の上の端"
                  "（首の軸から radius の内・ramp_from_z より上）の頭の骨の重みを、いちばん上で head_weight_top へなめらかに増やし、首の中から"
                  "頭の中へ伸びる栓（ふた付きの筒）を足して、栓と首の上の端を首の肌の 1 色にする。頭が首の無い部品で、体の首が上の開いた筒の"
                  "モデルで、首を曲げたときのすき間を消す。首の直しの量に既定は無い。元のファイルは変えない。Blender が要り、再利用しない（毎回実行）")
def neck_junction(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "model.neck_junction")
    _outputs(outputs, ("blend", "report"), "model.neck_junction")
    settings = neck_junction_settings(parameters)
    blender = _blender(parameters, context, "model.neck_junction")
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    atomic_json(step_dir / "neck_junction_settings.json", settings)
    record = launcher.run_blender(blender, NECK_JUNCTION_SCRIPT, None, ["--blend", inputs["blend"], "--settings",
                                                                        step_dir / "neck_junction_settings.json", "--out-blend",
                                                                        outputs["blend"], "--report", outputs["report"]],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "neck_junction", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"),
                   **{key: report.get(key) for key in ("body", "region_verts", "z_top", "removed_weight_verts", "remove_groups_missing",
                                                        "head_weight", "ramped_verts", "ramped_with_head_weight", "neck_top_faces_recoloured",
                                                        "neck_top_faces_material_changed")},
                   "plug": {key: (report.get("plug") or {}).get(key) for key in ("verts", "faces", "radius_cm", "material")}}
    missing = report.get("remove_groups_missing") or []
    if missing:              # 打ち間違いで黙って何もしないことにならないよう、体に無い頂点グループは要確認にする
        return StepResult(observations=[observation], unresolved=[f"model.neck_junction: remove_groups の頂点グループが体にありません: {missing}"],
                          review=True)
    return StepResult(observations=[observation])


ILLUSTRATION_PARAMETERS = {"mask", "background", "alpha_min", "rows", "top_y", "chin_y", "min_run", "tpose", "strict", "report_only"}


def _masking(parameters):
    """人物の画素の決め方（mask・background・alpha_min）を確かめる。"""
    from .. import checkpoint
    mask = parameters.get("mask", "auto")
    if mask not in ("auto", "alpha", "color"):
        raise ToolError(f"mask は auto・alpha・color のどれかにしてください: {mask!r}")
    background = checkpoint.check_rule(parameters["background"], "background") if "background" in parameters else None
    alpha_min = parameters.get("alpha_min", checkpoint.ALPHA_MIN)
    if isinstance(alpha_min, bool) or not isinstance(alpha_min, int) or not 0 <= alpha_min <= 254:
        raise ToolError(f"alpha_min は 0〜254 の整数にしてください: {alpha_min!r}")
    return {"mask": mask, "background": background, "alpha_min": alpha_min}


def _pixel_row(parameters, key):
    value = parameters.get(key)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ToolError(f"{key} は絵の行（0 以上の整数。上が 0）にしてください: {value!r}")
    return value


def _illustration_rows(rows):
    """rows（[{"name", "at", "kind"}, …]）を確かめる。"""
    from .. import checkpoint
    if not isinstance(rows, list) or not rows:
        raise ToolError(f"rows は [{{\"name\": 名前, \"at\": 高さの割合, \"kind\": torso・arms・legs}}, …] にしてください: {rows!r}")
    names = set()
    for k, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"name", "at", "kind"}:
            raise ToolError(f"rows[{k}] は {{\"name\", \"at\", \"kind\"}} にしてください: {row!r}")
        if not isinstance(row["name"], str) or not row["name"] or row["name"] in names:
            raise ToolError(f"rows[{k}].name は空でない・重ならない名前にしてください: {row['name']!r}")
        names.add(row["name"])
        at = row["at"]
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or not 0 <= at <= 1:
            raise ToolError(f"rows[{k}].at は身長に対する割合（0〜1。0 = 頭のてっぺん）にしてください: {at!r}")
        if row["kind"] not in checkpoint.KINDS:
            raise ToolError(f"rows[{k}].kind は {'・'.join(checkpoint.KINDS)} のどれかにしてください: {row['kind']!r}")
    return rows


def _tpose(value):
    if value is True:
        value = {}
    if not isinstance(value, dict) or set(value) - {"waist_range", "step"}:
        raise ToolError(f"tpose は true か {{\"waist_range\": [下, 上], \"step\": きざみ}} にしてください: {value!r}")
    span, step = value.get("waist_range", [0.80, 0.92]), value.get("step", 0.005)
    if (not isinstance(span, list) or len(span) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in span)
            or not 0 < span[0] < span[1] <= 1):
        raise ToolError(f"tpose.waist_range は [下, 上]（0 より大きく 1 以下、下 < 上）にしてください: {span!r}")
    if isinstance(step, bool) or not isinstance(step, (int, float)) or not math.isfinite(step) or not 0 < step <= span[1] - span[0]:
        raise ToolError(f"tpose.step は 0 より大きく、waist_range の幅以下にしてください: {step!r}")
    return {"waist_range": span, "step": step}


@register("check.illustration", inputs=("image",), outputs=("report", "view"),
          summary="立ち絵（正面）か T ポーズの体の絵の、人物の比率を測る（numpy と Pillow）。人物の画素は、背景の色（既定は緑）でない画素か、"
                  "透けていない画素（mask）。rows の高さ（身長に対する割合）の行ごとに、胴（真ん中をまたぐ区間）・腕（またがない区間）・脚（広い"
                  " 2 つの区間）の幅を身長に対する割合で、chin_y（あごの行）があれば頭身も。tpose を渡すと、床から腕までの高さ S を物差しに、"
                  "くびれの高さと幅（骨の無い 3D の候補と同じ物差し）。view は測った行に線を引いた確認用の絵。数を出すだけで合否は出さない"
                  "（report_only で要確認にしない）。同じ入力なら再利用する")
def illustration(inputs, parameters, outputs, context):
    from .. import checkpoint
    from .check_ops import _finish
    require(inputs, ("image",), "check.illustration")
    _refuse_unknown(parameters, ILLUSTRATION_PARAMETERS, "check.illustration")
    masking = _masking(parameters)
    if ("rows" in parameters) == ("tpose" in parameters):
        raise ToolError("check.illustration には rows（立ち絵の行）か tpose（T ポーズの体の絵）のどちらか 1 つを渡してください")
    if "tpose" in parameters:
        settings = _tpose(parameters["tpose"])
        if "top_y" in parameters or "chin_y" in parameters or "min_run" in parameters:
            raise ToolError("tpose には top_y・chin_y・min_run は使えません（立ち絵の行の設定）")
    else:
        rows = _illustration_rows(parameters["rows"])
        top_y, chin_y = _pixel_row(parameters, "top_y"), _pixel_row(parameters, "chin_y")
        min_run = parameters.get("min_run", 6)
        if isinstance(min_run, bool) or not isinstance(min_run, int) or min_run < 1:
            raise ToolError(f"min_run は 1 以上の整数にしてください: {min_run!r}")
    rgb, alpha = checkpoint.read_image(inputs["image"])
    mask, used = checkpoint.person_mask(rgb, alpha, **masking)
    report = {"schema_version": "model3d-illustration/0.1", "check": "illustration", "image": str(inputs["image"]), "mask": used,
              "background": masking["background"] or checkpoint.GREEN_BACKGROUND if used == "color" else None}
    if "tpose" in parameters:
        report["tpose"] = settings
        report.update(checkpoint.measure_tpose(mask, settings["waist_range"], settings["step"]))
    else:
        if top_y is not None and top_y >= mask.shape[0] or chin_y is not None and chin_y >= mask.shape[0]:
            raise ToolError(f"top_y・chin_y は絵の高さ（{mask.shape[0]}）より小さい行にしてください")
        report.update(checkpoint.measure_standing(mask, rows, top_y, chin_y, min_run))
    if "view" in outputs:
        checkpoint.draw_view(rgb, mask, report, outputs["view"], green_background=used == "alpha")
    report.update({"pass": None, "reason": "測っただけ（合否は出さない。report_only で測るだけにできる）", "failures": []})
    return _finish(report, outputs, parameters, "絵の比率")


BODY_IMAGE_PARAMETERS = {"mask", "background", "alpha_min", "min_run", "strict", "report_only"}


def _read_person(path, masking):
    """絵を読み、人物の画素を決める: (RGB, 人物の画素, 使った決め方)。"""
    from .. import checkpoint
    rgb, alpha = checkpoint.read_image(path)
    mask, used = checkpoint.person_mask(rgb, alpha, **masking)
    return rgb, mask, used


@register("check.body_image", inputs=("reference", "body"), outputs=("report", "overlay"),
          summary="体の絵（T ポーズでも）を元絵（立ち絵）と、同じ物差しで比べる（numpy と Pillow）: どちらも、足の裏からウエストまでの高さ U に"
                  "対する、ウエスト・太もも（0.65 U）・ひざ・ふくらはぎ・足首の幅と、裾・ひざ・ふくらはぎ・足首の高さ。表に、体の絵 ÷ 元絵。"
                  "腕の形に左右されない。overlay は足の裏とウエストを合わせて輪郭を重ねた絵。正面の絵どうしなので厚みは比べない。数を出すだけで"
                  "合否は出さない（report_only で要確認にしない）。同じ入力なら再利用する")
def body_image(inputs, parameters, outputs, context):
    from .. import checkpoint
    from .check_ops import _finish
    require(inputs, ("reference", "body"), "check.body_image")
    _refuse_unknown(parameters, BODY_IMAGE_PARAMETERS, "check.body_image")
    masking = _masking(parameters)
    min_run = parameters.get("min_run", 6)
    if isinstance(min_run, bool) or not isinstance(min_run, int) or min_run < 1:
        raise ToolError(f"min_run は 1 以上の整数にしてください: {min_run!r}")
    ref_rgb, ref_mask, ref_used = _read_person(inputs["reference"], masking)
    body_rgb, body_mask, body_used = _read_person(inputs["body"], masking)
    reference, body = checkpoint.measure_legs(ref_mask, min_run), checkpoint.measure_legs(body_mask, min_run)
    report = {"schema_version": "model3d-body-image/0.1", "check": "body_image", "reference": str(inputs["reference"]),
              "body": str(inputs["body"]), "mask": {"reference": ref_used, "body": body_used},
              "unit": "U = 足の裏からウエスト（中心をまたぐ区間がいちばん狭い行）までの高さ",
              "scale_body_to_reference": reference["U_px"] / body["U_px"], "reference_measure": reference, "body_measure": body,
              "table": checkpoint.compare_legs(reference, body)}
    if "overlay" in outputs:
        checkpoint.body_overlay(ref_rgb, ref_mask, body_rgb, body_mask, reference, body, outputs["overlay"])
    report.update({"pass": None, "reason": "測っただけ（合否は出さない。report_only で測るだけにできる）", "failures": []})
    return _finish(report, outputs, parameters, "体の絵と元絵の比べ")


FACE_IMAGE_PARAMETERS = {"mask", "background", "alpha_min", "iris", "reference_eyes", "head_eyes", "kernel", "size", "strict", "report_only"}


def _eyes(value, key):
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
        raise ToolError(f"{key} は両目の位置 [左の x, 左の y, 右の x, 右の y]（画素）にしてください: {value!r}")
    if not value[0] < value[2]:
        raise ToolError(f"{key} は絵の左の目（x が小さいほう）を先に書いてください（同じ位置か左右が逆です）: {value!r}")
    return [[float(value[0]), float(value[1])], [float(value[2]), float(value[3])]]


@register("check.face_image", inputs=("reference", "head"), outputs=("report", "sheet", "zoom"),
          summary="頭の絵（正面）の顔を元絵の顔と、両目の位置で合わせて並べる（numpy と Pillow）。両目は、瞳の色の決まり iris に入る画素の"
                  "かたまりの組で見つける（見つからなければ reference_eyes・head_eyes で手で渡す）。目の間隔 d を size 画素・傾きを 0 にそろえた"
                  "顔のまわりに、目の高さから下へ 0.5 d ごとの目盛りを引き、[元絵 | 頭の絵 | 半分ずつ重ねた絵]（sheet）と、目からあごまでを"
                  "拡大して 0.1 d ごとの目盛りの絵（zoom）にする。鼻・口・あごの位置は絵で読む。報告は両目の位置・間隔・傾き。数を出すだけで"
                  "合否は出さない（report_only で要確認にしない）。同じ入力なら再利用する")
def face_image(inputs, parameters, outputs, context):
    import numpy as np
    from .. import checkpoint
    from .check_ops import _finish
    require(inputs, ("reference", "head"), "check.face_image")
    _refuse_unknown(parameters, FACE_IMAGE_PARAMETERS, "check.face_image")
    masking = _masking(parameters)
    manual = {"reference": _eyes(parameters.get("reference_eyes"), "reference_eyes"), "head": _eyes(parameters.get("head_eyes"), "head_eyes")}
    iris = checkpoint.check_rule(parameters["iris"], "iris") if "iris" in parameters else None
    if iris is None and None in manual.values():
        raise ToolError("check.face_image には瞳の色の決まり iris か、両方の絵の両目の位置（reference_eyes・head_eyes）が要ります")
    kernel = parameters.get("kernel", 3)
    if isinstance(kernel, bool) or not isinstance(kernel, int) or not 1 <= kernel <= 15:
        raise ToolError(f"kernel は 1〜15 の整数にしてください: {kernel!r}")
    size = parameters.get("size", 150)
    if isinstance(size, bool) or not isinstance(size, int) or not 20 <= size <= 1000:
        raise ToolError(f"size（出力での目の間隔）は 20〜1000 の整数（画素）にしてください: {size!r}")
    report = {"schema_version": "model3d-face-image/0.1", "check": "face_image", "size": size, "iris": iris}
    crops = {}
    for side in ("reference", "head"):
        rgb, mask, used = _read_person(inputs[side], masking)
        white = checkpoint.whitened(rgb, mask)
        eyes, how = manual[side], "manual"
        if eyes is None:
            eyes, how = checkpoint.find_eyes(white, iris, kernel), "iris"
            if eyes is None:
                raise ToolError(f"{side} の絵に瞳が 2 つ見つかりません（iris の色の決まりを確かめるか、{side}_eyes で手で渡してください）")
        crop, d, tilt = checkpoint.face_crop(white, eyes, size)
        crops[side] = crop
        report[side] = {"image": str(inputs[side]), "mask": used, "eyes": eyes, "eyes_from": how, "d_px": d, "tilt_deg": tilt}
    if "sheet" in outputs:
        from PIL import Image
        blend = ((crops["reference"].astype(np.uint16) + crops["head"]) / 2 + 0.5).astype(np.uint8)
        sheet = checkpoint.side_by_side([checkpoint.face_guides(crops[k], size) for k in ("reference", "head")]
                                        + [checkpoint.face_guides(blend, size)])
        checkpoint.save_picture(Image.fromarray(sheet), outputs["sheet"])
    if "zoom" in outputs:
        from PIL import Image
        zoom = checkpoint.side_by_side([checkpoint.face_zoom(crops[k], size) for k in ("reference", "head")])
        checkpoint.save_picture(Image.fromarray(zoom), outputs["zoom"])
    report.update({"pass": None, "reason": "測っただけ（合否は出さない。report_only で測るだけにできる）", "failures": []})
    return _finish(report, outputs, parameters, "顔の絵と元絵の比べ")
