"""段取り: 曲の音から光のキーを作り（plan.light_keys）、段取り表と光のキーと骨の通り道から、render.stage が描くトラックを
作る（plan.stage）。どちらも計算だけ（Blender は使わない）。"""
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


@register("plan.light_keys", inputs=("audio",), outputs=("keys",),
          summary="曲の音（audio。WAV など）から、plan.stage が光に使う光のキー（keys。model3d-light-keys/0.1）を作る。窓"
                  "（start 秒から frames コマ。省けば音の終わりまで）のコマごとに、低・中・高の帯の強さ（0〜1。立ち上がりは"
                  "即座、下がりはゆっくり）と、拍の格子 grid（period と k0_time。秒、窓の頭が 0）に入る拍。帯・減り方・伸ばし方"
                  "の既定は試作と同じで、numpy だけで計算する（librosa.stft と同じ値）")
def light_keys(inputs, parameters, outputs, context):
    from .. import contracts
    from .. import light_keys as lk
    require(inputs, ("audio",), "plan.light_keys")
    if "keys" not in outputs:
        raise ToolError("plan.light_keys には outputs の keys が要ります")
    s = lk.settings(parameters)
    samples, rate, frames, length = lk.read_window(inputs["audio"], s["start"], s["frames"], s["fps"], s["n_fft"])
    keys, shown = lk.light_keys(samples, rate, frames, s["fps"], s["grid"], song_start=s["song_start"], bands=s["bands"],
                                release=s["release"], percentiles=s["percentiles"], kick_gain=s["kick_gain"],
                                low_floor=s["low_floor"], n_fft=s["n_fft"])
    atomic_json(outputs["keys"], contracts.validate("light-keys", keys))
    observation = {"kind": "light_keys", "frames": frames, "fps": s["fps"], "seconds": round(frames / s["fps"], 6),
                   "rate": rate, "channels": int(samples.shape[1]), "audio_seconds": round(length, 6),
                   "silent_frames": frames - shown, "beats": len(keys["beats"]),
                   "mean": {name: round(sum(keys[name]) / frames, 3) for name in ("low", "mid", "high")}}
    return StepResult(observations=[observation])
