"""表情を形で作る工程（docs/model3d.md の「表情を形で作る」）。頭のメッシュを書き出し、Blender の外で形状キーの動きを計算し、
形状キーとして入れて VRM の表情に割り当てる。リポジトリ外の 3 作目の VRM のダンスで通した手順を工程にしたもの。

- model.export_mesh: 物体のメッシュ（頭など）と色の絵を、Blender の外で計算するための npz と PNG に書き出す（Blender の中で）。
"""
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

EXPORT_MESH_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "mesh_export.py"
COMMON = {"blender", "addons", "blender_extensions", "timeout"}


def _refuse_unknown(values, allowed, where):
    unknown = set(values) - set(allowed)
    if unknown:
        raise ToolError(f"{where} の知らない設定: {sorted(unknown)}（使えるのは {sorted(allowed)}）")


def _name(value, where):
    if not isinstance(value, str) or not value:
        raise ToolError(f"{where} は名前（空でない文字列）にしてください: {value!r}")
    return value


@register("model.export_mesh", inputs=("blend",), outputs=("mesh", "texture", "report"), cacheable=False,
          summary="物体（object）のメッシュを、Blender の外で計算するための npz に書き出す（Blender の中で）: 頂点の基本の形の世界の座標（m）・"
                  "辺・三角形とその面の角・uv・法線。texture を挙げると、その物体の材質が使う色の絵（image は名前の一部。省くと使う絵が 1 つの"
                  "ときだけ）を PNG で書く。.blend は変えない")
def export_mesh(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "model.export_mesh")
    _refuse_unknown(parameters, COMMON | {"object", "image"}, "model.export_mesh")
    settings = {"object": _name(parameters.get("object"), "object")}
    if parameters.get("image") is not None:
        settings["image"] = _name(parameters["image"], "image")
    if "mesh" not in outputs or "report" not in outputs:
        raise ToolError("model.export_mesh には outputs の mesh と report が要ります")
    if Path(outputs["mesh"]).suffix.lower() != ".npz":
        raise ToolError(f"model.export_mesh の outputs の mesh は .npz にしてください: {Path(outputs['mesh']).name}")
    if "texture" in outputs and Path(outputs["texture"]).suffix.lower() != ".png":
        raise ToolError(f"model.export_mesh の outputs の texture は .png にしてください: {Path(outputs['texture']).name}")
    if "image" in settings and "texture" not in outputs:
        raise ToolError("image は outputs の texture（色の絵の PNG）を挙げたときだけ使えます")
    blender = _blender(parameters, context, "model.export_mesh")
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "export_mesh_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--out-mesh", outputs["mesh"], "--report", outputs["report"]]
    if "texture" in outputs:
        args += ["--out-texture", outputs["texture"]]
    record = launcher.run_blender(blender, EXPORT_MESH_SCRIPT, None, args, log=step_dir / "blender.log", timeout=_timeout(parameters),
                                  addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "export_mesh", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"],
                   **{key: report.get(key) for key in ("object", "vertices", "edges", "triangles", "uv_layer", "shape_keys", "scale_length",
                                                       "texture")}}
    return StepResult(observations=[observation])
