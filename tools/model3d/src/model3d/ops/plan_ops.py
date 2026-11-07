"""段取り: 段取り表と光のキーと骨の通り道から、render.stage が描くトラックを作る（計算だけ。Blender は使わない）。"""
from ..io import ToolError, atomic_json
from . import StepResult, register, require

PLAN_PARAMETERS = {"bone_prefix"}


@register("plan.stage", inputs=("plan", "keys", "bones", "cut"), outputs=("track", "report"),
          summary="段取り表（plan。model3d-stage-plan/0.1）と光のキー（keys。model3d-light-keys/0.1）と骨の通り道（bones。"
                  "model3d-bone-path/0.1）から、render.stage のトラック（model3d-stage-track/0.1）を作る。コマごとに、区間と動きと"
                  "寄り・揺れからカメラを、区間から壁の模様を、色・強さ・音の帯・拍・印から光と塗りの縁の値を決める。cut（T と f の"
                  "トラック）があればそのコマの並びで、無ければ骨の通り道のコマを T = 1 から順に。report は区間ごとのコマの範囲")
def stage_plan(inputs, parameters, outputs, context):
    from .. import contracts
    from ..stage_plan import build_track
    from .motion_ops import _bone_prefix
    from .render_ops import _track
    require(inputs, ("plan", "keys", "bones"), "plan.stage")
    unknown = set(parameters) - PLAN_PARAMETERS
    if unknown:
        raise ToolError(f"plan.stage の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PLAN_PARAMETERS)}）")
    if "track" not in outputs:
        raise ToolError("plan.stage には outputs の track が要ります")
    plan = contracts.load("stage-plan", inputs["plan"])
    keys = contracts.load("light-keys", inputs["keys"])
    bones = contracts.load("bone-path", inputs["bones"])
    cut = _track(inputs["cut"]) if inputs.get("cut") is not None else None
    track, report = build_track(plan, keys, bones, cut, bone_prefix=_bone_prefix(parameters))
    atomic_json(outputs["track"], contracts.validate("stage-track", track))
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "stage_plan", "frames": report["frames"], "T": report["T"], "seconds": report["seconds"],
                   "shots": sum(1 for s in report["shots"] if s["frames"]), "bones": report["bones"],
                   "beyond_keys": report["keys"]["beyond"]}
    return StepResult(observations=[observation])
