"""VRM のキャラを model3d の工程で動かす（Blender 5.2 と VRM Add-on。docs/model3d.md の「VRM のキャラを動かす」）。

どの工程も Blender の中で VRM Add-on を使うので、既定で bl_ext.user_default.vrm を有効にして起動する（addons で変えられる。
拡張機能のフォルダは blender_extensions か --tool blender_extensions=DIR）。Blender は外部の実行ファイルなので再利用しない（毎回実行）。
"""
from pathlib import Path

from ..io import ToolError, read_json
from . import StepResult, register, require
from .blender_ops import blender_addons
from .motion_ops import _blender, _bone_prefix, _timeout

VRM_ADDON = "bl_ext.user_default.vrm"
MOTION_RIG_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "vrm_motion_rig.py"
MOTION_RIG_PARAMETERS = {"blender", "addons", "blender_extensions", "bone_prefix", "armature", "skip_meshes", "timeout"}


def _names(parameters, key):
    value = parameters.get(key, [])
    if not isinstance(value, list) or not all(isinstance(name, str) and name.strip() and "," not in name for name in value) \
            or len(set(value)) != len(value):
        raise ToolError(f"{key} は名前（空でない・カンマを含まない・重ならない文字列）の並びにしてください: {value!r}")
    return value


@register("vrm.motion_rig", inputs=("character",), outputs=("blend", "bone_map", "report", "skip"), cacheable=False,
          summary="VRM のキャラの .blend（VRM Add-on の人型の骨の割り当て入り）から、骨の名前を Mixamo 式（bone_prefix）に付け替えた"
                  "作業コピー（blend）と、元の名前 → 付け替えた名前の対応表（bone_map）を作る（Blender の中で）。付け替えるのは "
                  "model3d が役割を知っている骨だけで、目・あごは付け替えない。motion.retarget が要る骨が無ければ止まる。"
                  "skip_meshes を挙げると、motion.retarget の skip_vertices に渡す npz（skip）も書く。元のファイルは変えない")
def motion_rig(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    require(inputs, ("character",), "vrm.motion_rig")
    unknown = set(parameters) - MOTION_RIG_PARAMETERS
    if unknown:
        raise ToolError(f"vrm.motion_rig の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(MOTION_RIG_PARAMETERS)}）")
    missing = [name for name in ("blend", "bone_map", "report") if name not in outputs]
    if missing:
        raise ToolError(f"vrm.motion_rig には outputs の {', '.join(missing)} が要ります")
    skip_meshes = _names(parameters, "skip_meshes")
    if bool(skip_meshes) != ("skip" in outputs):
        raise ToolError("vrm.motion_rig: skip_meshes と outputs の skip は一緒に書いてください")
    armature = parameters.get("armature", "")
    if not isinstance(armature, str):
        raise ToolError(f"armature はアーマチュアの名前（文字列）にしてください: {armature!r}")
    blender = _blender(parameters, context, "vrm.motion_rig")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    args = ["--character", inputs["character"], "--out-blend", outputs["blend"], "--bone-map", outputs["bone_map"],
            "--report", outputs["report"], "--bone-prefix", _bone_prefix(parameters)]
    if armature:
        args += ["--armature", armature]
    if skip_meshes:
        args += ["--skip-meshes", ",".join(skip_meshes), "--skip", outputs["skip"]]
    record = launcher.run_blender(blender, MOTION_RIG_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "vrm_motion_rig", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "mapped": report.get("mapped"), "renamed": report.get("renamed"), "unmapped": sorted(report.get("unmapped") or {}),
                   "removed_scenes": report.get("removed_scenes")}
    return StepResult(observations=[observation])
