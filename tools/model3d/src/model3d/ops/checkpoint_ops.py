"""キャラを作る段の確認地点で測る・直す工程（docs/model3d.md の「キャラを作る段の確認地点」）。キャラを作る道具（画像 → 候補 →
組み立て → VRM）から受け取った物を、選ぶ前・直す前に数で確かめ、受け取った VRM の作りの癖を直す（3 作目の VRM のダンスで、
リポジトリの外の台本でしていたもの）。測る工程は数と絵を出すだけで、良し悪しは決めない。

- vrm.color_factor: VRM の材質（MToon）の色の掛け率を 1 に戻し、影の色も同じ割合で戻す。
- model.neck_junction: 首と頭のつなぎ目を直す（首の上の端を頭の骨へ付け替え、頭の中へ伸びる栓を足す）。
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
NECK_JUNCTION_PARAMETERS = {"body", "neck_bone", "head_bone", "armature", "remove_groups", "radius", "ramp_from_z", "head_weight_top",
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
                                                        "ramped_verts", "neck_top_faces_recoloured")},
                   "plug": {key: (report.get("plug") or {}).get(key) for key in ("verts", "faces", "radius_cm", "material")}}
    missing = report.get("remove_groups_missing") or []
    if missing:              # 打ち間違いで黙って何もしないことにならないよう、体に無い頂点グループは要確認にする
        return StepResult(observations=[observation], unresolved=[f"model.neck_junction: remove_groups の頂点グループが体にありません: {missing}"],
                          review=True)
    return StepResult(observations=[observation])
