"""段取り: 曲の音から光のキーを作り（plan.light_keys）、段取り表と光のキーと骨の通り道から、render.stage が描くトラックを
作る（plan.stage）。動きと違う fps で描くコマの並び（小数のコマ）も作る（plan.retime）。踊り手を中心にした極座標のカメラの
キーから、render.passes が描くコマごとのカメラを作る（plan.orbit）。拍ごとの姿勢を数方向から見る下見のカメラも作る（plan.scout）。
決めた踊りのコマを 1 つずつ見る見本のカメラと光の値も作る（plan.stills）。どれも計算だけ（Blender は使わない）。"""
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


def _camera_plan(inputs, parameters, outputs, operation, kind, table):
    """plan.orbit と plan.shots の前置き: 入力（plan・bones）とパラメーター（無し）と出力（cameras・track）を確かめ、
    表（契約 kind）と骨の通り道を読む。table は文言の中の表の名前（段取り表・ショット表）。"""
    from .. import contracts
    require(inputs, ("plan", "bones"), operation)
    if parameters:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(parameters)}（設定は{table}に書きます）")
    missing = [name for name in ("cameras", "track") if name not in outputs]
    if missing:
        raise ToolError(f"{operation} には outputs の {', '.join(missing)} が要ります")
    return contracts.load(kind, inputs["plan"]), contracts.load("bone-path", inputs["bones"])


@register("plan.orbit", inputs=("plan", "bones"), outputs=("cameras", "track", "report"),
          summary="踊り手を中心にした極座標のカメラの段取り表（plan。model3d-orbit-plan/0.1。区間ごとの方位・距離・高さ・狙う点・"
                  "画角・ロールのキーとつなぎ方、手持ちの揺れと打点の揺れ、踊りの時刻の止め）と、骨から決めた点の通り道（bones。"
                  "model3d-bone-path/0.1。plan.retime のトラックで等間隔に測ったもの）から、コマごとのカメラ（cameras。"
                  "model3d-cameras/0.1。行列・焦点距離・主点・踊り手の点）とコマの並び（track。T と動きのコマ f）を作る。"
                  "狙う点のまわりの仰角（el）・レンズの mm（lens）・2 乗のつなぎ方・世界の座標のずらし（offset）・区間の平均と混ぜる"
                  "狙い（follow）・区間の中だけのならし（smooth.per_shot）・絞りとピント（fstop・focus。cameras の fstop・focus_m）・"
                  "角度を揺らす手持ちの揺れ（handheld.form orbit）も書ける。"
                  "render.passes の track と cameras にそのまま渡せる。plan.stage と同じ時刻の読み方・区間・寄りと揺れを使う。計算だけ")
def orbit(inputs, parameters, outputs, context):
    from .. import contracts
    from ..orbit_plan import build
    plan, bones = _camera_plan(inputs, parameters, outputs, "plan.orbit", "orbit-plan", "段取り表")
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
    plan, bones = _camera_plan(inputs, parameters, outputs, "plan.shots", "shot-plan", "ショット表")
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


SCOUT_PARAMETERS = {"beats", "grid", "times", "angles", "el", "dist", "lens", "aim", "aim_floor", "dz", "dance", "camera"}


def _scout_settings(parameters):
    """plan.scout のパラメーターを確かめ、既定を足す（scout_plan.DEFAULTS）。"""
    from ..scout_plan import DEFAULTS

    def real(value, where, minimum=None, exclusive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
            raise ToolError(f"plan.scout の {where} は数にしてください: {value!r}")
        if minimum is not None and ((value <= minimum) if exclusive else (value < minimum)):
            raise ToolError(f"plan.scout の {where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
        return float(value)

    unknown = set(parameters) - SCOUT_PARAMETERS
    if unknown:
        raise ToolError(f"plan.scout の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(SCOUT_PARAMETERS)}）")
    s = {key: value for key, value in DEFAULTS.items() if key not in ("dance", "camera")}
    if ("beats" in parameters) == ("times" in parameters):
        raise ToolError("plan.scout には beats（[最初の拍, 最後の拍]。grid が要る）か times（秒の並び）のどちらか 1 つが要ります")
    if "beats" in parameters:
        beats = parameters["beats"]
        if (not isinstance(beats, list) or len(beats) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in beats)
                or beats[0] > beats[1]):
            raise ToolError(f"plan.scout の beats は [最初の拍, 最後の拍]（整数、最初 ≤ 最後）にしてください: {beats!r}")
        grid = parameters.get("grid")
        if not isinstance(grid, dict) or set(grid) != {"period", "k0_time"}:
            raise ToolError(f"plan.scout の beats には grid（{{\"period\": 1 拍の秒, \"k0_time\": 拍 0 の秒}}）が要ります: {grid!r}")
        s["beats"] = beats
        s["grid"] = {"period": real(grid["period"], "grid.period", 0, True), "k0_time": real(grid["k0_time"], "grid.k0_time")}
    else:
        times = parameters["times"]
        if not isinstance(times, list) or not times:
            raise ToolError(f"plan.scout の times は秒の並び（1 つ以上）にしてください: {times!r}")
        s["times"] = [real(v, f"times[{n}]") for n, v in enumerate(times)]
        if "grid" in parameters:
            raise ToolError("plan.scout の grid は beats といっしょに使ってください")
    if "angles" in parameters:
        angles = parameters["angles"]
        if not isinstance(angles, list) or not angles:
            raise ToolError(f"plan.scout の angles は方位（度）の並び（1 つ以上）にしてください: {angles!r}")
        s["angles"] = [real(v, f"angles[{n}]") for n, v in enumerate(angles)]
    if "el" in parameters:
        s["el"] = real(parameters["el"], "el")
        if not -90 < s["el"] < 90:
            raise ToolError(f"plan.scout の el（仰角）は −90 より大きく 90 より小さくしてください: {s['el']!r}")
    for key in ("dist", "lens"):
        if key in parameters:
            s[key] = real(parameters[key], key, 0, True)
    if "dz" in parameters:
        s["dz"] = real(parameters["dz"], "dz")
    if "aim_floor" in parameters:
        s["aim_floor"] = None if parameters["aim_floor"] is None else real(parameters["aim_floor"], "aim_floor")
    if "aim" in parameters:
        if not isinstance(parameters["aim"], str) or not parameters["aim"]:
            raise ToolError(f"plan.scout の aim は点の名前（骨の通り道の points か bones）にしてください: {parameters['aim']!r}")
        s["aim"] = parameters["aim"]
    dance = parameters.get("dance", {})
    if not isinstance(dance, dict) or set(dance) - {"frame0", "fps"}:
        raise ToolError(f"plan.scout の dance は {{\"frame0\": 踊りの時刻 0 秒の動きのコマ, \"fps\": 動きの fps}} にしてください: {dance!r}")
    s["dance"] = {"frame0": real(dance.get("frame0", DEFAULTS["dance"]["frame0"]), "dance.frame0"),
                  "fps": real(dance.get("fps", DEFAULTS["dance"]["fps"]), "dance.fps", 0, True)}
    camera = parameters.get("camera", {})
    if not isinstance(camera, dict) or set(camera) - {"resolution", "sensor_height"}:
        raise ToolError(f"plan.scout の camera は {{\"resolution\": [幅, 高さ], \"sensor_height\": mm}} にしてください: {camera!r}")
    resolution = camera.get("resolution", DEFAULTS["camera"]["resolution"])
    if (not isinstance(resolution, list) or len(resolution) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in resolution)):
        raise ToolError(f"plan.scout の camera.resolution は [幅, 高さ]（1 以上の整数）にしてください: {resolution!r}")
    s["camera"] = {"resolution": resolution,
                   "sensor_height": real(camera.get("sensor_height", DEFAULTS["camera"]["sensor_height"]), "camera.sensor_height", 0, True)}
    return s


@register("plan.scout", inputs=("bones",), outputs=("cameras", "track", "report"),
          summary="向きの下見: 拍ごと（beats と grid）か時刻ごと（times）の姿勢を、踊り手のまわりのいくつかの方位（angles。既定は −90〜90° の 7 つ）"
                  "から見るコマごとのカメラ（cameras。model3d-cameras/0.1）とコマの並び（track。T と動きのコマ f）を作る。狙う点は骨の通り道"
                  "（bones）の点 aim（既定 hips。高さは max(aim_floor, z) + dz）、カメラは狙う点のまわりの仰角 el・距離 dist・レンズ lens。"
                  "コマ T = 1 + 行 × 列の数 + 列。render.passes でそのまま描き、mv sheet で拍（行）× 方位（列）に並べる。report は行と列の表。"
                  "計算だけ")
def scout(inputs, parameters, outputs, context):
    from .. import contracts
    from ..scout_plan import build
    require(inputs, ("bones",), "plan.scout")
    missing = [name for name in ("cameras", "track") if name not in outputs]
    if missing:
        raise ToolError(f"plan.scout には outputs の {', '.join(missing)} が要ります")
    settings = _scout_settings(parameters)
    cameras, track, report = build(settings, contracts.load("bone-path", inputs["bones"]))
    atomic_json(outputs["cameras"], contracts.validate("cameras", cameras))
    atomic_json(outputs["track"], contracts.validate("stage-track", track))
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "scout_plan", "frames": report["frames"], "rows": len(report["rows"]), "columns": report["columns"],
                   "f": [report["rows"][0]["f"], report["rows"][-1]["f"]], "outside_samples": report["outside"]}
    return StepResult(observations=[observation])


STILLS_PARAMETERS = {"shots", "lights", "base", "fps", "camera", "clip_m"}
SHOT_KEYS = {"name", "f", "offset", "aim", "dz", "lens", "fstop", "focus", "lights"}


def _real(value, where, minimum=None, exclusive=False):
    """有限の数（minimum があれば、それ以上か、exclusive ならそれより大きい）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        raise ToolError(f"plan.stills の {where} は数にしてください: {value!r}")
    if minimum is not None and ((value <= minimum) if exclusive else (value < minimum)):
        raise ToolError(f"plan.stills の {where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    return float(value)


def _light_values(values, where):
    """光の値 {名前: 数か 3 つの数}。flash・fade は 0 以上の数、flash_color は 3 つの数（光のトラックの契約と同じ）。"""
    if not isinstance(values, dict) or not all(isinstance(k, str) and k for k in values):
        raise ToolError(f"plan.stills の {where} は {{値の名前: 数か [R, G, B]}} にしてください: {values!r}")
    out = {}
    for name, value in values.items():
        if isinstance(value, list):
            if len(value) != 3:
                raise ToolError(f"plan.stills の {where}.{name} は数か 3 つの数にしてください: {value!r}")
            out[name] = [_real(v, f"{where}.{name}") for v in value]
        else:
            out[name] = _real(value, f"{where}.{name}")
    for name in ("flash", "fade"):
        if name in out and (isinstance(out[name], list) or out[name] < 0):
            raise ToolError(f"plan.stills の {where}.{name} は 0 以上の数にしてください: {out[name]!r}")
    if "flash_color" in out and not isinstance(out["flash_color"], list):
        raise ToolError(f"plan.stills の {where}.flash_color は 3 つの数にしてください: {out['flash_color']!r}")
    return out


def _stills_shot(shot, where, names, frames):
    """見本 1 つを確かめる。"""
    if not isinstance(shot, dict):
        raise ToolError(f"plan.stills の {where} は辞書にしてください: {shot!r}")
    unknown = set(shot) - SHOT_KEYS
    if unknown:
        raise ToolError(f"plan.stills の {where} の知らない鍵: {sorted(unknown)}（使えるのは {sorted(SHOT_KEYS)}）")
    missing = [key for key in ("name", "f", "offset", "aim", "lens") if key not in shot]
    if missing:
        raise ToolError(f"plan.stills の {where} には {', '.join(missing)} が要ります")
    if not isinstance(shot["name"], str) or not shot["name"] or shot["name"] in names:
        raise ToolError(f"plan.stills の {where}.name は、空でない・重ならない名前にしてください: {shot['name']!r}")
    f = shot["f"]
    if isinstance(f, bool) or not isinstance(f, int):
        raise ToolError(f"plan.stills の {where}.f（踊りのコマ）は整数にしてください: {f!r}")
    if f in frames:
        raise ToolError(f"plan.stills: 同じ踊りのコマ {f} の見本が 2 つあります（render.show は、T の順に動きのコマが増える並びだけを"
                        "描くので、1 つの踊りのコマは 1 回だけ使えます）")
    offset = shot["offset"]
    if not isinstance(offset, list) or len(offset) != 3:
        raise ToolError(f"plan.stills の {where}.offset は [x, y, z]（m）にしてください: {offset!r}")
    for key in ("aim", "focus"):
        if key in shot and (not isinstance(shot[key], str) or not shot[key]):
            raise ToolError(f"plan.stills の {where}.{key} は点の名前（骨の通り道の points か bones）にしてください: {shot[key]!r}")
    if "focus" in shot and "fstop" not in shot:
        raise ToolError(f"plan.stills の {where}.focus（ピントの点）は fstop（絞り）といっしょに書いてください")
    item = {"name": shot["name"], "f": f, "offset": [_real(v, f"{where}.offset") for v in offset], "aim": shot["aim"],
            "dz": _real(shot.get("dz", 0.0), f"{where}.dz"), "lens": _real(shot["lens"], f"{where}.lens", 0, True),
            "lights": _light_values(shot.get("lights", {}), f"{where}.lights")}
    if "fstop" in shot:
        item["fstop"] = _real(shot["fstop"], f"{where}.fstop", 0, True)
    if "focus" in shot:
        item["focus"] = shot["focus"]
    names.add(shot["name"])
    frames.add(f)
    return item


def _stills_settings(parameters):
    """plan.stills のパラメーターを確かめ、既定を足す（stills_plan.DEFAULTS）。"""
    from ..stills_plan import DEFAULTS
    unknown = set(parameters) - STILLS_PARAMETERS
    if unknown:
        raise ToolError(f"plan.stills の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(STILLS_PARAMETERS)}）")
    shots = parameters.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ToolError("plan.stills には shots（見本の並び。1 つ以上）が要ります")
    names, frames = set(), set()
    s = {"shots": [_stills_shot(shot, f"shots[{n}]", names, frames) for n, shot in enumerate(shots)],
         "lights": _light_values(parameters.get("lights", {}), "lights"), "base": DEFAULTS["base"], "fps": DEFAULTS["fps"],
         "camera": dict(DEFAULTS["camera"]), "clip_m": None}
    if "base" in parameters:
        if not isinstance(parameters["base"], str) or not parameters["base"]:
            raise ToolError(f"plan.stills の base は点の名前にしてください: {parameters['base']!r}")
        s["base"] = parameters["base"]
    if "fps" in parameters:
        s["fps"] = _real(parameters["fps"], "fps", 0, True)
    camera = parameters.get("camera", {})
    if not isinstance(camera, dict) or set(camera) - {"resolution", "sensor_height"}:
        raise ToolError(f"plan.stills の camera は {{\"resolution\": [幅, 高さ], \"sensor_height\": mm}} にしてください: {camera!r}")
    if "resolution" in camera:
        resolution = camera["resolution"]
        if (not isinstance(resolution, list) or len(resolution) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in resolution)):
            raise ToolError(f"plan.stills の camera.resolution は [幅, 高さ]（1 以上の整数）にしてください: {resolution!r}")
        s["camera"]["resolution"] = resolution
    if "sensor_height" in camera:
        s["camera"]["sensor_height"] = _real(camera["sensor_height"], "camera.sensor_height", 0, True)
    if parameters.get("clip_m") is not None:
        clip = parameters["clip_m"]
        if not isinstance(clip, list) or len(clip) != 2:
            raise ToolError(f"plan.stills の clip_m は [近く, 遠く]（m）にしてください: {clip!r}")
        near, far = _real(clip[0], "clip_m", 0, True), _real(clip[1], "clip_m", 0, True)
        if not near < far:
            raise ToolError(f"plan.stills の clip_m は近くを遠くより小さくしてください: {clip!r}")
        s["clip_m"] = [near, far]
    return s


@register("plan.stills", inputs=("bones",), outputs=("cameras", "track", "lights", "report"),
          summary="見本のコマ: 決めた踊りのコマ（shots の f）を 1 つずつ、足もと（base の点。既定 hips）の真下の床からの位置 offset に置いた"
                  "カメラで、骨の通り道（bones）の狙う点 aim（＋ dz）を傾けずに見るカメラ（cameras。model3d-cameras/0.1。レンズ lens、絞り fstop と"
                  "ピントの点 focus）と、コマの並び（track。T と動きのコマ f）と、そのコマの光の値（lights。model3d-light-track/0.1。lights に"
                  "見本ごとの lights を重ねる）を作る。T は f の小さい順に 1 から。同じ f の見本は止める。plan.orbit は T から時刻を決めるので、"
                  "ばらばらの踊りのコマの見本には、こちらを使う。render.show でそのまま描く。report は T と見本の名前の表。計算だけ")
def stills(inputs, parameters, outputs, context):
    from .. import contracts
    from ..stills_plan import build
    require(inputs, ("bones",), "plan.stills")
    missing = [name for name in ("cameras", "lights") if name not in outputs]
    if missing:
        raise ToolError(f"plan.stills には outputs の {', '.join(missing)} が要ります")
    settings = _stills_settings(parameters)
    cameras, track, lights, report = build(settings, contracts.load("bone-path", inputs["bones"]))
    atomic_json(outputs["cameras"], contracts.validate("cameras", cameras))
    atomic_json(outputs["lights"], contracts.validate("light-track", lights))
    if "track" in outputs:
        atomic_json(outputs["track"], contracts.validate("stage-track", track))
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "stills_plan", "frames": report["frames"], "f": [report["shots"][0]["f"], report["shots"][-1]["f"]],
                   "dof_frames": sum(1 for row in report["shots"] if row["dof"]), "outside_samples": report["outside"]}
    return StepResult(observations=[observation])
