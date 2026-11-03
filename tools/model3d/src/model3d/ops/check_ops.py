"""検査の操作。合否は report に残し、不合格は needs_review（strict なら失敗、report_only なら測るだけ）にする。未実施は合格にしない。"""
from .. import checks, semantic
from ..io import ToolError, atomic_json, read_json
from ..mesh import Mesh
from . import StepResult, register, require


def _finish(report, outputs, parameters, label):
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    passed = report.get("pass")
    if parameters.get("report_only"):   # 測るだけ（補正前の値を残すなど）。合否は report に残り、工程の状態は変えない
        return StepResult(observations=[report])
    if passed is None:
        return StepResult(observations=[report], unresolved=[f"{label}: 未実施（{report.get('reason')}）"], review=True)
    if passed:
        return StepResult(observations=[report])
    failures = report.get("failures") or [f"{label}: 不合格"]
    if parameters.get("strict"):
        raise ToolError(f"{label} が不合格です: " + "; ".join(str(f) for f in failures))
    return StepResult(observations=[report], unresolved=[f"{label}: {f}" for f in failures], review=True)


@register("check.structure", inputs=("rig",), outputs=("report",),
          summary="骨の欠落・循環・非有限値とウェイトの正規化を検査する。required_bone_roles は preset か配列")
def check_structure(inputs, parameters, outputs, context):
    require(inputs, ("rig",), "check.structure")
    from .. import contracts
    rig = contracts.validate("rig", read_json(inputs["rig"]), inputs["rig"])
    required = parameters.get("required_bone_roles")
    if required == "preset":
        if context.preset is None:
            raise ToolError("required_bone_roles: preset には preset が要ります")
        required = context.preset["required_roles"].get("bones", [])
    elif required == "humanoid":
        required = list(semantic.HUMANOID_REQUIRED_BONES)
    report = checks.structure(rig, context.semantic_map, required, parameters.get("tolerance", 1e-3))
    return _finish(report, outputs, parameters, "構造の検査")


@register("check.penetration", inputs=("mesh", "collider"), outputs=("report",),
          summary="mesh の頂点が閉じた collider の中にないことを検査する")
def check_penetration(inputs, parameters, outputs, context):
    require(inputs, ("mesh", "collider"), "check.penetration")
    report = checks.penetration(Mesh.load(inputs["mesh"]), Mesh.load(inputs["collider"]), parameters.get("directions"))
    if report["inside"]:
        report["failures"] = [f"{report['inside']} 頂点が中にあります（最大の深さ {report['max_depth']:.4g}）"]
    return _finish(report, outputs, parameters, "貫通の検査")


@register("check.continuity", inputs=("track",), outputs=("report",),
          summary="コマごとの点の列で、前のコマからの移動が max_jump を超えないことを検査する")
def check_continuity(inputs, parameters, outputs, context):
    require(inputs, ("track",), "check.continuity")
    from . import number
    report = checks.continuity(read_json(inputs["track"]), number(parameters, "max_jump", minimum=0, exclusive=True))
    if report.get("jumps"):
        report["failures"] = [f"コマ {j['frame']}（t={j['t']}）で {j['max_move']:.4g} 動いています" for j in report["jumps"]]
    return _finish(report, outputs, parameters, "連続性の検査")
