"""キャラを作る段の確認地点で測る・直す工程（docs/model3d.md の「キャラを作る段の確認地点」）。キャラを作る道具（画像 → 候補 →
組み立て → VRM）から受け取った物を、選ぶ前・直す前に数で確かめ、受け取った VRM の作りの癖を直す（3 作目の VRM のダンスで、
リポジトリの外の台本でしていたもの）。測る工程は数と絵を出すだけで、良し悪しは決めない。

- vrm.color_factor: VRM の材質（MToon）の色の掛け率を 1 に戻し、影の色も同じ割合で戻す。
- vrm.mtoon_params: MToon の影・縁の光・マットキャップの設定を材質ごとに変える。
- model.neck_junction: 首と頭のつなぎ目を直す（首の上の端を頭の骨へ付け替え、頭の中へ伸びる栓を足す）。
- check.illustration: 立ち絵か T ポーズの体の絵の、人物の比率を測る（numpy と Pillow。model3d/checkpoint.py）。
- check.body_image・check.face_image: 体の絵と顔の絵を、元絵と同じ物差しで比べる（numpy と Pillow）。
- check.three_view: 三面図の同じ部位の高さがそろっているかを測る。texture.face_length: 頭の絵の目から下を縦に詰める（numpy と Pillow）。
- check.candidate: 骨の無い 3D の候補を読み込んで太さを測り、粘土の見た目で描く（Blender で読み込み、測るのは model3d/mesh_slices.py）。
- check.assembly: 組み立てたモデルの頭身を測り、頭の大きさと位置の提案を出す（Blender の中で。測るのは blender_adapter/assembly_measure.py）。
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


MTOON_PARAMS_SCRIPT = COLOR_FACTOR_SCRIPT.with_name("mtoon_params.py")
MTOON_PARAMS = {"materials", "blender", "addons", "blender_extensions", "timeout"}


@register("vrm.mtoon_params", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="VRM の MToon 材質の影色・境目のかたさとずらし・縁の光・マットキャップを材質ごとに設定し、"
                  "前後の値を JSON に記録する。マットキャップ画像は工程の追加の入力で渡す。Blender と VRM Add-on が必要",
          capability={"aliases": ["MToon の影色と縁の光を変える", "マットキャップを材質に貼る", "MToon shading and rim"],
                      "tools": ["blender"], "network_mode": "never", "external_process_mode": "always",
                      "parameters": {"status": "docs", "schema": None}})
def mtoon_params(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher, mtoon_params as adapter
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    from .vrm_ops import VRM_ADDON

    operation = "vrm.mtoon_params"
    require(inputs, ("blend",), operation)
    _refuse_unknown(parameters, MTOON_PARAMS, operation)
    _outputs(outputs, ("blend", "report"), operation)
    try:
        settings = adapter.compile_settings(parameters.get("materials"), inputs)
    except ValueError as error:
        raise ToolError(f"{operation}: {error}") from error
    blender = _blender(parameters, context, operation)
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "mtoon_params_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(
        blender, MTOON_PARAMS_SCRIPT, None,
        ["--blend", inputs["blend"], "--settings", settings_path, "--out-blend", outputs["blend"], "--report", outputs["report"]],
        log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions,
    )
    report = read_json(outputs["report"])
    return StepResult(observations=[{"kind": "mtoon_params", "changed": report["changed"], "skipped": report["skipped"],
                                     "blender_version": record["blender_version"], "addons": record.get("addons")}])


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


THREE_VIEW_PARAMETERS = {"mask", "background", "alpha_min", "marks", "iris", "kernel", "strict", "report_only"}
VIEWS = ("front", "side", "back")


def _marks(value):
    """marks（[{"name", "rule", "range": [下, 上], "pick": top・bottom, "least", "strip"}, …]）を確かめ、既定を入れる。"""
    from .. import checkpoint
    if value is None:
        return []
    if not isinstance(value, list):
        raise ToolError(f"marks は印の並びにしてください: {value!r}")
    out, names = [], set()
    for k, mark in enumerate(value):
        if not isinstance(mark, dict) or set(mark) - {"name", "rule", "range", "pick", "least", "strip"}:
            raise ToolError(f"marks[{k}] は {{\"name\", \"rule\", \"range\", \"pick\"（と least・strip）}} にしてください: {mark!r}")
        name = mark.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ToolError(f"marks[{k}].name は空でない・重ならない名前にしてください: {name!r}")
        names.add(name)
        span = mark.get("range")
        if (not isinstance(span, list) or len(span) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in span)
                or not 0 <= span[0] < span[1] <= 1):
            raise ToolError(f"marks[{k}].range は全高に対する割合 [下, 上]（0〜1、下 < 上。0 = いちばん上）にしてください: {span!r}")
        if mark.get("pick") not in ("top", "bottom"):
            raise ToolError(f"marks[{k}].pick は top（いちばん上の行）か bottom（いちばん下の行）にしてください: {mark.get('pick')!r}")
        least, strip = mark.get("least", 4), mark.get("strip", 0.07)
        if isinstance(least, bool) or not isinstance(least, int) or least < 1:
            raise ToolError(f"marks[{k}].least は 1 以上の整数にしてください: {least!r}")
        if isinstance(strip, bool) or not isinstance(strip, (int, float)) or not 0 < strip <= 0.5:
            raise ToolError(f"marks[{k}].strip は 0 より大きく 0.5 以下（全高に対する割合）にしてください: {strip!r}")
        out.append({"name": name, "rule": checkpoint.check_rule(mark.get("rule"), f"marks[{k}].rule"), "range": span,
                    "pick": mark["pick"], "least": least, "strip": strip})
    return out


@register("check.three_view", inputs=VIEWS, outputs=("report", "sheet", "heads"),
          summary="三面図（正面・横・後ろ）で、同じ部位の高さがそろっているかを測る（numpy と Pillow）。絵ごとに人物のいちばん上と下の行・"
                  "全高・横にいちばん広い行と、印の行（marks。色の決まりに入る画素が体の中央の帯にある行のうち、全高に対する割合の範囲で"
                  "いちばん上か下）を、全高に対する割合で。iris（瞳の色の決まり）を渡すと、頭のあたりのかたまりも。sheet は全高と足の裏を"
                  "そろえて並べ正面の行に線を引いた絵、heads は頭のあたりを拡大して目盛りを引いた絵（頭の大きさを目で読む）。合否は出さない"
                  "（report_only で要確認にしない）。同じ入力なら再利用する")
def three_view(inputs, parameters, outputs, context):
    from .. import checkpoint
    from .check_ops import _finish
    require(inputs, VIEWS, "check.three_view")
    _refuse_unknown(parameters, THREE_VIEW_PARAMETERS, "check.three_view")
    masking = _masking(parameters)
    marks = _marks(parameters.get("marks"))
    iris = checkpoint.check_rule(parameters["iris"], "iris") if "iris" in parameters else None
    kernel = parameters.get("kernel", 2)
    if isinstance(kernel, bool) or not isinstance(kernel, int) or not 1 <= kernel <= 15:
        raise ToolError(f"kernel は 1〜15 の整数にしてください: {kernel!r}")
    report = {"schema_version": "model3d-three-view/0.1", "check": "three_view", "marks": marks, "iris": iris, "views": {}}
    drawn = []
    for view in VIEWS:
        rgb, mask, used = _read_person(inputs[view], masking)
        rows = checkpoint.view_rows(rgb, mask, marks)
        rows.update(image=str(inputs[view]), mask=used)
        if iris is not None:
            rows["head_blobs"] = checkpoint.head_blobs(rgb, mask, iris, rows, kernel)
        report["views"][view] = rows
        drawn.append((checkpoint.whitened(rgb, mask), rows))
    if "sheet" in outputs:
        checkpoint.three_view_sheet(drawn, outputs["sheet"])
    if "heads" in outputs:
        checkpoint.heads_sheet(drawn, VIEWS, outputs["heads"])
    report.update({"pass": None, "reason": "測っただけ（合否は出さない。report_only で測るだけにできる）", "failures": []})
    return _finish(report, outputs, parameters, "三面図の高さ")


FACE_LENGTH_PARAMETERS = {"eye_y", "chin_y", "d", "k", "start", "above_band"}


@register("texture.face_length", inputs=("image",), outputs=("image", "report"),
          summary="頭の絵（透けつきの PNG）の、目の少し下（目の中心の行 eye_y から start × d 下。既定 0.10）からあごの先の行 chin_y までの帯を、"
                  "描き直さずに縦に k 倍にする（行の付け替えだけ。帯の両端では変えず真ん中ほど強く、帯より上は動かさず、下は長さの差だけずらす）。"
                  "絵の幅いっぱいに同じ付け替えをするので髪の筋が横にずれない。Lanczos4 で引き、透けを掛けてから付け替えて戻す。d は目の間隔（画素）。"
                  "帯より上の見える画素は変えない。透けが 0 の画素に残っていた色は、既定（above_band: resample。台本と同じ）では 0 になり、"
                  "above_band: copy で帯より上の行を元の絵から写すと元のまま。k に既定は無い。numpy と Pillow だけで、同じ入力なら再利用する")
def face_length(inputs, parameters, outputs, context):
    import numpy as np
    from PIL import Image

    from .. import checkpoint
    require(inputs, ("image",), "texture.face_length")
    _refuse_unknown(parameters, FACE_LENGTH_PARAMETERS, "texture.face_length")
    _outputs(outputs, ("image",), "texture.face_length")
    if Path(outputs["image"]).suffix.lower() != ".png":
        raise ToolError(f"texture.face_length の出力 image は .png にしてください（透けを残す）: {outputs['image']}")
    eye_y, chin_y = _real(parameters, "eye_y", 0.0, required=True), _real(parameters, "chin_y", 0.0, required=True)
    d = _real(parameters, "d", 0.0, exclusive=True, required=True)
    k = _real(parameters, "k", 0.0, exclusive=True, required=True)
    if k >= 2:
        raise ToolError(f"k は 0 より大きく 2 より小さくしてください（1 より小さいと詰め、大きいと伸ばす）: {k!r}")
    start = _real(parameters, "start", 0.0)
    start = 0.10 if start is None else start
    if chin_y <= eye_y + start * d:
        raise ToolError(f"chin_y（{chin_y}）は、帯の上の端（eye_y + start × d = {eye_y + start * d}）より下の行にしてください")
    rgb, alpha = checkpoint.read_image(inputs["image"])
    if alpha is None:
        raise ToolError("texture.face_length には透けのある絵（RGBA の PNG）を渡してください")
    if chin_y >= rgb.shape[0]:
        raise ToolError(f"chin_y（{chin_y}）が絵の高さ（{rgb.shape[0]}）の外です")
    above_band = parameters.get("above_band", "resample")
    if above_band not in checkpoint.ABOVE_BAND:
        raise ToolError(f"above_band は resample（既定。台本と同じ）か copy（帯より上の行を元の絵から写す）にしてください: {above_band!r}")
    out, info = checkpoint.shorten_face(np.dstack([rgb, alpha]), eye_y, chin_y, d, k, start, above_band)
    Image.fromarray(out).save(outputs["image"], format="PNG")
    report = {"schema_version": "model3d-face-length/0.1", "image": str(inputs["image"]), "eye_y": eye_y, "d": d, **info,
              "size": [int(out.shape[1]), int(out.shape[0])]}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "face_length", **{key: report[key] for key in ("band_y", "k", "shift_px", "chin_y_after",
                                                                                         "above_band")}}])


CANDIDATE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "candidate_mesh.py"
CANDIDATE_PARAMETERS = {"kind", "gap", "thigh_at", "slices", "size", "blender", "timeout", "strict", "report_only"}


@register("check.candidate", inputs=("model",), outputs=("report", "views", "check", "mesh"), cacheable=False,
          summary="3D の候補（FBX・glTF・OBJ。骨の無い T ポーズの体か頭）を Blender で読み込んで測り、粘土の見た目で数方向から描く。体（kind: body）"
                  "は、床から腕の先までの高さ S を物差しに、切り口を面のつながりで輪にたどって、くびれ・ひざ・ふくらはぎと、thigh_at の高さの太もも"
                  "（既定は無い）の幅と厚み（S に対する割合）と、足の裏からくびれまでの高さ U に対する値（元絵や体の絵と同じ決め方）、胴の幅の並び。slices の高さの切り口の輪の数と範囲も。"
                  "views は正面・横・後ろと、体は下から見上げた正面・頭は斜め前を並べた絵、check は測った高さの線の絵、mesh は頂点と三角形の npz。"
                  "どれを選ぶかは決めない（合否は出さない。report_only で要確認にしない）。Blender が要り、再利用しない（毎回実行）")
def candidate(inputs, parameters, outputs, context):
    import numpy as np
    from PIL import Image

    from .. import checkpoint, mesh_slices
    from ..blender_adapter import launcher
    from .check_ops import _finish
    from .motion_ops import _blender, _timeout
    require(inputs, ("model",), "check.candidate")
    _refuse_unknown(parameters, CANDIDATE_PARAMETERS, "check.candidate")
    if Path(inputs["model"]).suffix.lower() not in (".fbx", ".glb", ".gltf", ".obj"):
        raise ToolError(f"check.candidate の model は .fbx・.glb・.gltf・.obj にしてください: {inputs['model']}")
    kind = parameters.get("kind", "body")
    if kind not in ("body", "head"):
        raise ToolError(f"kind は body（体）か head（頭）にしてください: {kind!r}")
    if kind == "head" and "check" in outputs:
        raise ToolError("check（測った高さの線の絵）は kind: body だけです（頭は測らない）")
    gap = _real(parameters, "gap", 0.0)
    gap = 0.002 if gap is None else gap
    thigh_at = _real(parameters, "thigh_at", 0.0, exclusive=True, maximum=1.0)
    if thigh_at is not None and kind != "body":
        raise ToolError("thigh_at（太ももを測る高さ）は kind: body だけです")
    slices = parameters.get("slices", [])
    if (not isinstance(slices, list) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
                                            for v in slices)):
        raise ToolError(f"slices は高さの割合（0 = いちばん下、1 = いちばん上）の並びにしてください: {slices!r}")
    size = parameters.get("size", [520, 760])
    if (not isinstance(size, list) or len(size) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or not 16 <= v <= 4096 for v in size)):
        raise ToolError(f"size は描く絵の [幅, 高さ]（16〜4096 の整数）にしてください: {size!r}")
    blender = _blender(parameters, context, "check.candidate")
    step_dir = Path(context.step_dir)
    mesh_path = Path(outputs["mesh"]) if "mesh" in outputs else step_dir / "candidate_mesh.npz"
    args = ["--model", inputs["model"], "--mesh", mesh_path, "--kind", kind, "--size", f"{size[0]}x{size[1]}"]
    if "views" in outputs:
        args += ["--views", step_dir / "views"]
    record = launcher.run_blender(blender, CANDIDATE_SCRIPT, None, args, log=step_dir / "blender.log", timeout=_timeout(parameters))
    with np.load(mesh_path, allow_pickle=False) as data:
        V, T, names = data["V"], data["T"], [str(n) for n in data["names"]]
    lo, hi = V.min(axis=0), V.max(axis=0)
    report = {"schema_version": "model3d-candidate/0.1", "check": "candidate", "model": str(inputs["model"]), "kind": kind,
              "meshes": names, "verts": int(len(V)), "triangles": int(len(T)), "bounds": [lo.tolist(), hi.tolist()],
              "blender_version": record["blender_version"]}
    if kind == "body":
        report["body"] = mesh_slices.measure_candidate(V, T, gap, thigh_at)
        if "check" in outputs:
            mesh_slices.check_picture(V, report["body"], outputs["check"])
    if slices:
        report["slices"] = mesh_slices.slice_rows(V, T, slices)
    if "views" in outputs:
        files = [step_dir / "views" / f"{name}.png" for name, _ in _view_names(kind)]
        report["views"] = [f.name for f in files]
        panels = [np.asarray(Image.open(f).convert("RGB")) for f in files]
        checkpoint.save_picture(Image.fromarray(checkpoint.side_by_side(panels, gap=4)), outputs["views"])
    report.update({"pass": None, "reason": "測っただけ（どれを選ぶかは決めない。report_only で測るだけにできる）", "failures": []})
    return _finish(report, outputs, parameters, "候補の測り")


def _view_names(kind):
    from ..blender_adapter.candidate_mesh import view_list
    return view_list(kind)


ASSEMBLY_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "assembly.py"
ASSEMBLY_PARAMETERS = {"body", "head", "hair", "reference", "anchor", "place", "pivot", "bust", "illustration_rows", "sheet_scale",
                       "blender", "timeout", "strict", "report_only"}
WHOLE_VIEWS = ("front_tex", "side_tex", "back_tex", "front_clay", "side_clay")
BUST_VIEWS = ("bust_front_tex", "bust_side_tex", "bust_oblique_tex", "bust_front_clay", "bust_side_clay")
BUST_DEFAULT = {"size": 0.62, "drop": 0.10}       # 胸から上の絵: 写す幅と、頭の真ん中から下げる量（m。人の大きさのキャラ向け）


def _numbers(parameters, key, names, required=False):
    """パラメーター key（名前 → 有限の数の対応）。names の外の名前は止める。required なら names の全部が要る。"""
    value = parameters.get(key)
    if (not isinstance(value, dict) or not value or set(value) - set(names)
            or (required and set(names) - set(value))):
        need = "全部" if required else "どれか"
        raise ToolError(f"{key} は {{{', '.join(names)}}} の{need}を数で書いた対応にしてください: {value!r}")
    return {name: _real(value, name, required=True) for name in names if name in value}


def assembly_settings(parameters):
    """check.assembly のパラメーターを確かめ、台本の settings にする（元絵の比率・目印・頭身に既定は無い）。"""
    _refuse_unknown(parameters, ASSEMBLY_PARAMETERS, "check.assembly")
    settings = {key: _names(parameters.get(key), key) for key in ("body", "head", "hair")}
    reference = anchor = None
    if "reference" in parameters:
        reference = _numbers(parameters, "reference", ("top_U", "chin_U"), required=True)
        if not 0 < reference["chin_U"] < reference["top_U"]:
            raise ToolError(f"reference は 0 < chin_U < top_U にしてください（U に対する、あごと髪のてっぺんの高さ）: {reference}")
    if "anchor" in parameters:
        anchor = _numbers(parameters, "anchor", ("z", "gap", "heads"), required=True)
        if anchor["gap"] < 0 or not anchor["heads"] > 1 + anchor["gap"]:
            raise ToolError(f"anchor は gap ≥ 0、heads > 1 + gap にしてください: {anchor}")
    place = _numbers(parameters, "place", ("scale", "z", "y")) if "place" in parameters else {}
    if "scale" in place and not place["scale"] > 0:
        raise ToolError(f"place の scale は正の数にしてください: {place['scale']}")
    if reference is None and anchor is None and not {"scale", "z"} <= set(place):
        raise ToolError("check.assembly には reference か anchor が要ります（place に scale と z の両方があるときは要りません）")
    pivot = parameters.get("pivot")
    if pivot is not None and (not isinstance(pivot, list) or len(pivot) != 3 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in pivot)):
        raise ToolError(f"pivot は [x, y, z]（m）にしてください: {pivot!r}")
    bust = {**BUST_DEFAULT, **(_numbers(parameters, "bust", ("size", "drop")) if "bust" in parameters else {})}
    if not bust["size"] > 0:
        raise ToolError(f"bust の size は正の数（m）にしてください: {bust['size']}")
    settings.update({"reference": reference, "anchor": anchor, "place": place, "pivot": pivot, "bust": [bust["size"], bust["drop"]]})
    return settings


@register("check.assembly", inputs=("blend", "illustration"), outputs=("report", "views", "bust", "compare"), cacheable=False,
          summary="組み立てたモデル（体・頭・髪が別の物体の .blend。T ポーズ）の頭身を測り、頭と髪の大きさと位置の提案を出す（Blender の中で、"
                  "保存しない）。物差しは足の裏からくびれまでの高さ U。reference（元絵の髪のてっぺんとあごの U に対する高さ）か anchor（体の側の目印の"
                  "高さ・あごとの間隔・頭身）から、頭と髪を pivot を中心に何倍にして pivot をどこへ動かすかを出し（place で手で決めた値にもできる）、"
                  "メモリの中で動かして測り直す。views・bust は、今の配置と動かした後を正射影で描いて目印の高さの線を引いた絵、compare は元絵"
                  "（illustration と illustration_rows）を足の裏とくびれでそろえて並べた絵。元絵の比率・目印・頭身に既定は無い。どう置くかは決めない"
                  "（合否は出さない。report_only で要確認にしない）。Blender が要り、再利用しない（毎回実行）")
def assembly(inputs, parameters, outputs, context):
    import numpy as np
    from PIL import Image

    from .. import checkpoint
    from ..blender_adapter import launcher
    from .check_ops import _finish
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "check.assembly")
    settings = assembly_settings(parameters)
    _outputs(outputs, ("report",), "check.assembly")
    rows = None
    if "compare" in outputs:
        if "illustration" not in inputs:
            raise ToolError("compare（元絵と並べた絵）には入力 illustration（元絵）が要ります")
        rows = _numbers(parameters, "illustration_rows", ("sole", "waist", "top", "chin"), required=True)
        if not rows["top"] < rows["chin"] < rows["waist"] < rows["sole"]:
            raise ToolError(f"illustration_rows は、上から top < chin < waist < sole の行にしてください: {rows}")
    elif "illustration_rows" in parameters or "illustration" in inputs:
        raise ToolError("illustration と illustration_rows は、compare（元絵と並べた絵）を書くときだけ使います")
    scale = _real(parameters, "sheet_scale", 0.0, exclusive=True, maximum=1.0)
    scale = 0.5 if scale is None else scale
    blender = _blender(parameters, context, "check.assembly")
    step_dir = Path(context.step_dir)
    atomic_json(step_dir / "assembly_settings.json", settings)
    raw = step_dir / "assembly_raw.json"
    folder = step_dir / "views"
    args = ["--settings", step_dir / "assembly_settings.json", "--report", raw]
    pictures = [name for name in ("views", "bust", "compare") if name in outputs]
    if pictures:
        args += ["--views", folder]
    record = launcher.run_blender(blender, ASSEMBLY_SCRIPT, inputs["blend"], args, log=step_dir / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(raw)
    report.update({"schema_version": "model3d-assembly/0.1", "check": "assembly", "blend": str(inputs["blend"]),
                   "blender_version": record["blender_version"]})
    now, after, plan = report["now"], report["after"], report["proposal"]
    titles = ("now: %.2f heads" % now["ratio"]["heads"],
              "after: scale %.4f, pivot to (%.3f, %.3f, %.3f): %.2f heads" % (plan["scale"], *plan["to"], after["ratio"]["heads"]))
    for name, views in (("views", WHOLE_VIEWS), ("bust", BUST_VIEWS)):
        if name in outputs:
            checkpoint.assembly_sheet(folder, report["views"], [(titles[0], ["now_" + v for v in views], now),
                                                                (titles[1], ["after_" + v for v in views], after)],
                                      outputs[name], scale)
    if "compare" in outputs:
        rgb, alpha = checkpoint.read_image(inputs["illustration"])
        illustration = Image.fromarray(rgb if alpha is None else np.dstack([rgb, alpha]))
        report["compare"] = {"illustration": str(inputs["illustration"]), "rows": rows,
                             "placed": checkpoint.illustration_sheet(folder, report["views"], illustration, rows,
                                                                     [(titles[0], "now_front_tex", now), (titles[1], "after_front_tex", after)],
                                                                     outputs["compare"], scale)}
    report["pictures"] = {name: str(outputs[name]) for name in pictures}
    report.update({"pass": None, "reason": "測って提案を出しただけ（どう置くかは決めない。report_only で測るだけにできる）", "failures": []})
    return _finish(report, outputs, parameters, "組み立ての頭身")
