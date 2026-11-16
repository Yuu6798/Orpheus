"""段取り: 曲の音から光のキーを作り（plan.light_keys）、段取り表と光のキーと骨の通り道から、render.stage が描くトラックを
作る（plan.stage）。動きと違う fps で描くコマの並び（小数のコマ）も作る（plan.retime）。踊り手を中心にした極座標のカメラの
キーから、render.passes が描くコマごとのカメラを作る（plan.orbit）。どれも計算だけ（Blender は使わない）。"""
from ..io import ToolError, atomic_json
from . import StepResult, number, register, require

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


@register("plan.orbit", inputs=("plan", "bones"), outputs=("cameras", "track", "report"),
          summary="踊り手を中心にした極座標のカメラの段取り表（plan。model3d-orbit-plan/0.1。区間ごとの方位・距離・高さ・狙う点・"
                  "画角・ロールのキーとつなぎ方、手持ちの揺れと打点の揺れ、踊りの時刻の止め）と、骨から決めた点の通り道（bones。"
                  "model3d-bone-path/0.1。plan.retime のトラックで等間隔に測ったもの）から、コマごとのカメラ（cameras。"
                  "model3d-cameras/0.1。行列・焦点距離・主点・踊り手の点）とコマの並び（track。T と動きのコマ f）を作る。"
                  "render.passes の track と cameras にそのまま渡せる。plan.stage と同じ時刻の読み方・区間・寄りと揺れを使う。計算だけ")
def orbit(inputs, parameters, outputs, context):
    from .. import contracts
    from ..orbit_plan import build
    require(inputs, ("plan", "bones"), "plan.orbit")
    if parameters:
        raise ToolError(f"plan.orbit の知らないパラメーター: {sorted(parameters)}（設定は段取り表に書きます）")
    missing = [name for name in ("cameras", "track") if name not in outputs]
    if missing:
        raise ToolError(f"plan.orbit には outputs の {', '.join(missing)} が要ります")
    plan = contracts.load("orbit-plan", inputs["plan"])
    bones = contracts.load("bone-path", inputs["bones"])
    cameras, track, report = build(plan, bones)
    atomic_json(outputs["cameras"], contracts.validate("cameras", cameras))
    atomic_json(outputs["track"], contracts.validate("stage-track", track))
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "orbit_plan", "frames": report["frames"], "seconds": report["seconds"], "points": report["points"],
                   "shots": sum(1 for s in report["shots"] if s["frames"]), "held_frames": report["held_frames"],
                   "outside_samples": report["samples"]["outside"], "f": report["f"]}
    return StepResult(observations=[observation])


@register("plan.shots", inputs=("plan", "bones"), outputs=("cameras", "track", "alt", "alt_track", "report"),
          summary="2D の絵コンテのショット表（plan。model3d-shot-plan/0.1。1 枚の絵の仮の部屋（render.box_room と同じ置き方）の中の"
                  "カメラの移動・向き・寄りと、追う点（骨の通り道の点か、踊り手の立つ面の上の画素）を置く画面の画素のキー、回り込みの区間、"
                  "ショットごとの手直し、手持ちの揺れと打点の揺れ、踊りの時刻の止め）と、骨から決めた点の通り道（bones）から、追う点が"
                  "ちょうど狙いの画素に来るように主点を解いたコマごとのカメラ（cameras。model3d-cameras/0.1）とコマの並び（track）を作る。"
                  "wipes を書くと、つなぎの最中の相手のショットのカメラ（alt）とそのコマの並び（alt_track）も作り、references の揺れなしの"
                  "基準のカメラを report に書く。"
                  "render.passes の track と cameras にそのまま渡せる。計算だけ")
def shots(inputs, parameters, outputs, context):
    from .. import contracts
    from ..shot_plan import build
    require(inputs, ("plan", "bones"), "plan.shots")
    if parameters:
        raise ToolError(f"plan.shots の知らないパラメーター: {sorted(parameters)}（設定はショット表に書きます）")
    missing = [name for name in ("cameras", "track") if name not in outputs]
    if missing:
        raise ToolError(f"plan.shots には outputs の {', '.join(missing)} が要ります")
    plan = contracts.load("shot-plan", inputs["plan"])
    bones = contracts.load("bone-path", inputs["bones"])
    cameras, track, alt, report = build(plan, bones)
    if ("alt" in outputs or "alt_track" in outputs) and alt is None:
        raise ToolError("plan.shots の outputs の alt・alt_track（つなぎの相手のカメラとコマの並び）には、ショット表の wipes が要ります")
    atomic_json(outputs["cameras"], contracts.validate("cameras", cameras))
    atomic_json(outputs["track"], contracts.validate("stage-track", track))
    if "alt" in outputs:
        atomic_json(outputs["alt"], contracts.validate("cameras", alt))
    if "alt_track" in outputs:
        atomic_json(outputs["alt_track"], contracts.validate("stage-track", report["alt_track"]))
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "shot_plan", "frames": report["frames"], "seconds": report["seconds"], "points": report["points"],
                   "shots": sum(1 for s in report["shots"] if s["frames"]), "held_frames": report["held_frames"],
                   "alt_frames": len(report["alt"]), "references": sorted(report["references"]),
                   "anchor_error_px": report["anchor_error_px"], "outside_samples": report["samples"]["outside"], "f": report["f"]}
    return StepResult(observations=[observation])


RETIME_PARAMETERS = {"fps", "motion_fps", "start", "frames"}


def retime_track(fps, motion_fps, start, frames):
    """書き出す fps のコマ T（1 から frames まで）に、動きのコマ f = start + (T − 1) × motion_fps ÷ fps を当てたトラック
    （model3d-stage-track/0.1。T と f だけ）。f はちょうど整数なら整数、そうでなければ小数（前後のコマの間の時刻）。"""
    from ..blender_adapter.stage_render import motion_frame
    rows = [{"T": T, "f": motion_frame(start + (T - 1) * motion_fps / fps)} for T in range(1, frames + 1)]
    return {"schema_version": "model3d-stage-track/0.1", "fps": fps, "frames": rows}


@register("plan.retime", outputs=("track",),
          summary="動きのコマの fps（motion_fps。例 24）と違う fps（例 30）で描くための、コマの並び（track。model3d-stage-track/0.1 の "
                  "T と f だけ）を作る。コマ T（1 から frames まで）の動きのコマは f = start + (T − 1) × motion_fps ÷ fps で、小数のコマは"
                  "render.stage・render.passes・motion.bone_path が前後のコマの間の時刻で描く。plan.stage の cut にも渡せる。計算だけ")
def retime(inputs, parameters, outputs, context):
    from .. import contracts
    unknown = set(parameters) - RETIME_PARAMETERS
    if unknown:
        raise ToolError(f"plan.retime の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(RETIME_PARAMETERS)}）")
    if "track" not in outputs:
        raise ToolError("plan.retime には outputs の track が要ります")
    fps = number(parameters, "fps", minimum=0, exclusive=True)
    motion_fps = number(parameters, "motion_fps", minimum=0, exclusive=True)
    start = number(parameters, "start", 1)
    frames = number(parameters, "frames", minimum=1, integer=True)
    track = retime_track(fps, motion_fps, start, frames)
    atomic_json(outputs["track"], contracts.validate("stage-track", track))
    last = track["frames"][-1]["f"]
    observation = {"kind": "retime", "frames": frames, "fps": fps, "motion_fps": motion_fps, "f": [track["frames"][0]["f"], last],
                   "fractional": sum(1 for fr in track["frames"] if isinstance(fr["f"], float))}
    return StepResult(observations=[observation])
