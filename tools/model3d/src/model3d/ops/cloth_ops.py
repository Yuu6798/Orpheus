"""衣装を布で動かす工程（docs/model3d.md の「衣装を布で動かす」）。どれも Blender の中で動かす。Blender は外部の実行ファイルなので
再利用しない（毎回実行）。

- cloth.skirt_proxy: 裾を身代わりの筒の布で動かす（細かい裾は筒に貼り付けて付いて行かせる）。
"""
import math
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, number, register, require

SKIRT_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "skirt_proxy.py"
SKIRT_PARAMETERS = {"blender", "addons", "blender_extensions", "timeout", "armature", "skirt", "body", "hips_bone", "leg_groups",
                    "end", "bake"}
# 数の設定: 名前 → (最小, 最小を含まないか, 整数か)
SKIRT_NUMBERS = {
    "columns": (8, False, True), "rows": (3, False, True), "close_gap": (0, False, True), "min_strip": (0, False, True),
    "inner_percentile": (0, False, False), "clearance_top": (0, False, False), "clearance": (0, False, False),
    "collider_min_island": (1, False, True), "collider_min_leg_vertices": (1, False, True), "collider_ratio": (0, True, False),
    "collider_cut_below": (None, False, False), "collider_outer": (0, False, False), "collider_inner": (0, False, False),
    "friction": (0, False, False), "distance_min": (0, False, False), "quality": (1, False, True),
    "collision_quality": (1, False, True), "mass": (0, True, False), "air_damping": (0, False, False), "gravity": (None, False, False),
    "tension": (0, False, False), "compression": (0, False, False), "shear": (0, False, False), "bending": (0, False, False),
    "tension_damping": (0, False, False), "compression_damping": (0, False, False), "shear_damping": (0, False, False),
    "bending_damping": (0, False, False), "pin_rows": (1, False, True), "pin_rows_front": (1, False, True),
    "hem_cut_z": (0, False, False), "goal_below_pin": (0, False, False), "goal_hem": (0, False, False), "goal_curve": (0, False, False),
    "pin_stiffness": (0, False, False), "smooth_iterations": (0, False, True), "smooth_factor": (0, False, False),
    "bind_falloff": (0, True, False), "preroll": (1, False, True), "hold": (0, False, True), "floor_offset": (0, False, False),
    "floor_friction": (0, False, False), "pushout_after": (0, False, False),
}
SKIRT_FLAGS = ("extend_slit", "self_collision")
FLOORS = ("collide", "clamp", "none")


def _names(value, key):
    if not isinstance(value, list) or not value or not all(isinstance(n, str) and n for n in value) or len(set(value)) != len(value):
        raise ToolError(f"{key} は名前（空でない・重ならない文字列）の並びにしてください: {value!r}")
    return value


def _name(parameters, key, operation):
    value = parameters.get(key)
    if not isinstance(value, str) or not value:
        raise ToolError(f"{operation} にはパラメーター {key}（名前）が要ります: {value!r}")
    return value


def skirt_settings(parameters):
    """cloth.skirt_proxy のパラメーターを確かめ、台本の settings にする（省いた値は台本の既定）。"""
    from ..blender_adapter import skirt_proxy
    allowed = SKIRT_PARAMETERS | set(SKIRT_NUMBERS) | set(SKIRT_FLAGS) | {"front_zone_deg", "extra_cuts_deg", "floor"}
    unknown = set(parameters) - allowed
    if unknown:
        raise ToolError(f"cloth.skirt_proxy の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(allowed)}）")
    s = {key: _name(parameters, key, "cloth.skirt_proxy") for key in ("skirt", "body", "hips_bone")}
    if s["skirt"] == s["body"]:
        raise ToolError("skirt と body は別のメッシュにしてください")
    s["leg_groups"] = _names(parameters.get("leg_groups"), "leg_groups")
    if parameters.get("armature") is not None:
        s["armature"] = _name(parameters, "armature", "cloth.skirt_proxy")
    for key, (minimum, exclusive, integer) in SKIRT_NUMBERS.items():
        if key in parameters:
            value = number(parameters, key, minimum=minimum, exclusive=exclusive, integer=integer)
            s[key] = value
    for key in ("inner_percentile", "collider_ratio", "smooth_factor"):
        limit = 100 if key == "inner_percentile" else 1
        if s.get(key, 0) > limit:
            raise ToolError(f"{key} は {limit} 以下にしてください: {s[key]!r}")
    for key in SKIRT_FLAGS + ("bake",):
        if key in parameters:
            if not isinstance(parameters[key], bool):
                raise ToolError(f"{key} は true か false にしてください: {parameters[key]!r}")
            s[key] = parameters[key]
    if "floor" in parameters:
        if parameters["floor"] not in FLOORS:
            raise ToolError(f"floor は {', '.join(FLOORS)} のどれかにしてください: {parameters['floor']!r}")
        s["floor"] = parameters["floor"]
    if "front_zone_deg" in parameters:
        zone = parameters["front_zone_deg"]
        if not (isinstance(zone, list) and len(zone) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 360 for v in zone) and zone[0] < zone[1]):
            raise ToolError(f"front_zone_deg は前側の向きの範囲 [から, まで]（度。0〜360、から < まで）にしてください: {zone!r}")
        s["front_zone_deg"] = zone
    if "extra_cuts_deg" in parameters:
        cuts = parameters["extra_cuts_deg"]
        if not isinstance(cuts, list) or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in cuts):
            raise ToolError(f"extra_cuts_deg は切り込みの向き（度）の並びにしてください: {cuts!r}")
        s["extra_cuts_deg"] = cuts
    if "end" in parameters:
        s["end"] = number(parameters, "end", minimum=0, integer=True)
    merged = skirt_proxy.merged(s)
    if merged["pin_rows"] >= merged["rows"] or merged["pin_rows_front"] >= merged["rows"]:
        raise ToolError(f"pin_rows・pin_rows_front は rows（{merged['rows']}）より小さくしてください")
    if merged["close_gap"] >= merged["columns"] or merged["min_strip"] >= merged["columns"]:
        raise ToolError(f"close_gap・min_strip は columns（{merged['columns']}）より小さくしてください")
    return s


@register("cloth.skirt_proxy", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="脚に当たる裾（skirt）を、裾の内側の面をなぞった目の粗い筒（身代わり）の布の計算で動かし、細かい裾は Surface Deform で"
                  "筒に貼り付けて付いて行かせる（Blender の中で）。当たりの相手は体（body）から脚の頂点グループ（leg_groups）の頂点を持つ"
                  "大きな島だけを取り出して面を減らしたもの。切れ目の延長（extend_slit）・追加の切り込み・丈の切り落とし（hem_cut_z）・"
                  "初期姿勢からの助走（preroll・hold。骨の動きのキーを前に足す）・床。腰の骨（hips_bone）に付ける。元のファイルは変えない。"
                  "既定の値は 2 作目の VRM のダンスで決めた値")
def skirt_proxy(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "cloth.skirt_proxy")
    settings = skirt_settings(parameters)
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"cloth.skirt_proxy には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "cloth.skirt_proxy")
    addons, extensions = blender_addons(parameters, context)
    settings_path = Path(context.step_dir) / "skirt_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, SKIRT_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                "--out-blend", outputs["blend"], "--report", outputs["report"]],
                                  log=Path(context.step_dir) / "blender.log", timeout=_timeout(parameters), addons=addons,
                                  extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "skirt_proxy", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"),
                   **{key: report.get(key) for key in ("proxy_verts", "proxy_faces", "collider_islands", "hem_cut", "slit_extended",
                                                       "skirt_verts_pushed_out_at_rest", "frames", "baked")}}
    unresolved = [] if report.get("baked") or settings.get("bake") is False else ["布の計算を焼けませんでした"]
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))

