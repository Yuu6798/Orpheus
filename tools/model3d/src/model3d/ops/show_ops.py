"""演出版（EEVEE で 1 回で描く）の工程: 光の段取り表からコマごとの光の値のトラックを作る（plan.lights。計算だけ）。"""
from ..io import ToolError, atomic_json
from . import StepResult, register, require


@register("plan.lights", inputs=("plan", "keys"), outputs=("track", "report"),
          summary="光の段取り表（plan。model3d-light-plan/0.1）と光のキー（keys。model3d-light-keys/0.1。任意）から、コマごとの光の値の"
                  "トラック（track。model3d-light-track/0.1）を作る。コマ T の値は、values の既定を、t を含む区間の let と set で上書きし、"
                  "閃光（flashes。強い方を残す）を重ねたもの。flash・flash_color・fade はどのトラックにもある。値は数・3 つの並び・小さな式"
                  "（t・low・mid・high・hit と、beat・ramp・ease・mix・max・min・exp、if。式は読む前に形を確かめ、eval は使わない）。"
                  "描く工程（render.show）と仕上げ（mv dance finish）が読む。計算だけで、同じ入力なら再利用する")
def plan_lights(inputs, parameters, outputs, context):
    from .. import contracts
    from ..light_plan import build
    require(inputs, ("plan",), "plan.lights")
    if parameters:
        raise ToolError(f"plan.lights の知らないパラメーター: {sorted(parameters)}（設定は光の段取り表に書きます）")
    if "track" not in outputs:
        raise ToolError("plan.lights には outputs の track が要ります")
    plan = contracts.load("light-plan", inputs["plan"])
    keys = contracts.load("light-keys", inputs["keys"]) if inputs.get("keys") is not None else None
    track, report = build(plan, keys)
    atomic_json(outputs["track"], contracts.validate("light-track", track))
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "light_plan", "frames": report["frames"], "fps": report["fps"],
                   "segments": sum(1 for s in report["segments"] if s["frames"]), "names": len(report["names"]),
                   "flash_frames": report["flash_frames"], "fade_frames": report["fade_frames"],
                   "beyond_keys": (report["keys"] or {}).get("beyond")}
    return StepResult(observations=[observation])
