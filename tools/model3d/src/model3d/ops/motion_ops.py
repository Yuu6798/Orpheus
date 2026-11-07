"""動きの下ごしらえ: 推定した 3D の点（SAM 3D Body の MHR70）を、キャラに載せる前に整える。"""
import os
import tempfile
from pathlib import Path

import numpy as np

from .. import motion, motion_edit
from ..io import ToolError, atomic_json
from . import StepResult, number, register, require

FORMATS = ("sam3d_body",)
PARAMETERS = {"format", "depth_window", "spike", "pitch_deg", "torso_back_deg", "contact", "contact_height", "step_split",
              "blend_frames", "min_span_frames"}


def _optional(parameters, key, minimum=None, exclusive=False, limit=None):
    if parameters.get(key) is None:
        return None
    value = number(parameters, key, minimum=minimum, exclusive=exclusive)
    if limit is not None and abs(value) > limit:
        raise ToolError(f"パラメーター {key} は ±{limit} の中にしてください: {value!r}")
    return float(value)


def _read(path):
    """SAM 3D Body の npz から点・並進と、記録に残す数（焦点距離の中央値・有効なコマ・画像全体の枠を使ったコマ）。"""
    try:
        with np.load(path, allow_pickle=False) as data:
            keypoints, translation = data["pred_keypoints_3d"], data["pred_cam_t"]
            notes = {"focal_length_px_median": float(np.median(data["focal_length"])) if "focal_length" in data.files else None}
            if "valid" in data.files:
                notes["invalid_frames"] = int((~data["valid"].astype(bool)).sum())
            if "used_full_image_box" in data.files:
                notes["full_image_box_frames"] = int(data["used_full_image_box"].astype(bool).sum())
    except (OSError, KeyError, ValueError) as error:
        raise ToolError(f"keypoints の npz を読めません（pred_keypoints_3d と pred_cam_t が要ります）: {error}") from error
    return keypoints, translation, notes


def _save_npz(path, **arrays):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:   # ファイルを渡すと numpy は拡張子を足さない（出力名どおりに書く）
            np.savez(stream, **arrays)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@register("motion.prepare_keypoints", inputs=("keypoints",), outputs=("points", "report"),
          summary="SAM 3D Body の 70 点（npz の pred_keypoints_3d + pred_cam_t）を整える: 1 コマの跳ねの除去、平滑化（奥行きは窓を広く）、"
                  "床の水平化（pitch_deg で上書き可）、上体を起こす（torso_back_deg）、接地の区間で足を止める（contact・step_split）。"
                  "points は x 右・y 奥・z 上・m・床 z=0 の npz（points・stance・focal）")
def prepare_keypoints(inputs, parameters, outputs, context):
    require(inputs, ("keypoints",), "motion.prepare_keypoints")
    unknown = set(parameters) - PARAMETERS
    if unknown:
        raise ToolError(f"motion.prepare_keypoints の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PARAMETERS)}）")
    form = parameters.get("format", "sam3d_body")
    if form not in FORMATS:
        raise ToolError(f"format は {FORMATS} のどれか: {form!r}")
    depth_window = number(parameters, "depth_window", 11, minimum=5, integer=True)
    if depth_window % 2 == 0:
        raise ToolError(f"depth_window は奇数にしてください: {depth_window}")
    contact = parameters.get("contact", "absolute")
    if contact not in motion.CONTACT_MODES:
        raise ToolError(f"contact は {motion.CONTACT_MODES} のどれか: {contact!r}")
    settings = {
        "depth_window": depth_window,
        "spike": float(number(parameters, "spike", 0.03, minimum=0, exclusive=True)),
        "pitch_deg": _optional(parameters, "pitch_deg", limit=45),
        "torso_back_deg": float(number(parameters, "torso_back_deg", 0.0)),
        "contact": contact,
        "contact_height": float(number(parameters, "contact_height", 0.05, minimum=0, exclusive=True)),
        "step_split": _optional(parameters, "step_split", minimum=0, exclusive=True),
        "blend": number(parameters, "blend_frames", 4, minimum=0, integer=True),
        "min_span": number(parameters, "min_span_frames", 4, minimum=1, integer=True),
    }
    if abs(settings["torso_back_deg"]) > 45:
        raise ToolError(f"torso_back_deg は ±45 の中にしてください: {settings['torso_back_deg']}")
    keypoints, translation, notes = _read(inputs["keypoints"])
    try:
        points, stance, report = motion.prepare(keypoints, translation, **settings)
    except ValueError as error:
        raise ToolError(f"motion.prepare_keypoints: {error}") from error
    report = {"schema_version": "model3d-prepared-keypoints/0.1", "input": {"format": form, **notes}, **report}
    if "points" in outputs:
        focal = notes["focal_length_px_median"]
        _save_npz(outputs["points"], points=points.astype(np.float32), stance=stance.astype(np.float32),
                  focal=np.float64(np.nan if focal is None else focal))
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    floor, contact_report = report["floor"], report["contact"]
    observation = {"kind": "prepare_keypoints", "frames": report["frames"], "replaced_points": report["smoothing"]["replaced_points"],
                   "measured_pitch_deg": floor["measured_pitch_deg"], "applied_pitch_deg": floor["applied_pitch_deg"],
                   "torso_back_deg": report["torso_back_deg"], "locked_frames": contact_report["locked_frames"],
                   "max_ankle_step_m": report["max_ankle_step_m"], **notes}
    return StepResult(observations=[observation])


# 手直しの種類ごとのパラメーター: 名前 → (既定、検査)。None の既定は必須
_EDITS = {
    "head_turn": {"degrees": (None, "angle"), "fade_in": (0, "count"), "fade_out": (0, "count")},
    "fist": {"side": (None, "side"), "fade": (3, "count")},
    "forearm_roll": {"stack": (None, "number"), "half": (7.1, "positive"), "fade": (4, "count"), "radius": (0.06, "positive"),
                     "radius_lat": (0.035, "nonnegative"), "tilt_deg": (25.0, "angle"), "lateral": (-0.06, "number"),
                     "forward_add": (0.0, "number"), "up_add": (0.0, "number"), "sigma": (5.0, "positive"),
                     "direction": (1, "direction"), "palm": ("down", "palm"), "palm_down_deg": (20.0, "angle"),
                     "elbows": ("ik", "elbows")},
}
_CHOICES = {"side": motion_edit.SIDES, "direction": (1, -1), "palm": ("down", "in"), "elbows": ("ik", "fixed")}


def _edit_settings(edit, number_of_frames, position):
    label = f"edits[{position}]"
    if not isinstance(edit, dict) or edit.get("kind") not in _EDITS:
        raise ToolError(f"{label}: kind は {sorted(_EDITS)} のどれかにしてください: {edit!r}")
    kind, spec = edit["kind"], _EDITS[edit["kind"]]
    unknown = set(edit) - set(spec) - {"kind", "frames"}
    if unknown:
        raise ToolError(f"{label}（{kind}）の知らないキー: {sorted(unknown)}（使えるのは {sorted(spec)} と frames）")
    frames = edit.get("frames")
    if not (isinstance(frames, list) and len(frames) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in frames)
            and 0 <= frames[0] < frames[1] <= number_of_frames):
        raise ToolError(f"{label}: frames は 0 以上 {number_of_frames} 以下の [開始, 終了) の整数 2 つにしてください: {frames!r}")
    settings = {"start": frames[0], "end": frames[1]}
    for key, (default, rule) in spec.items():
        value = edit.get(key, default)
        if value is None:
            raise ToolError(f"{label}（{kind}）に {key} が要ります")
        if rule in _CHOICES:
            if value not in _CHOICES[rule]:
                raise ToolError(f"{label}: {key} は {_CHOICES[rule]} のどれか: {value!r}")
        else:
            value = number({key: value}, key, minimum=0 if rule in ("count", "nonnegative", "positive") else None,
                           exclusive=rule == "positive", integer=rule == "count")
            if rule == "angle" and abs(value) > 180:
                raise ToolError(f"{label}: {key} は ±180 の中にしてください: {value!r}")
        settings[key] = value
    return kind, settings


@register("motion.edit_keypoints", inputs=("points",), outputs=("points", "report"),
          summary="整えた 70 点（motion.prepare_keypoints の points）を区間ごとに手直しする。edits に順に並べる: head_turn（顔の向きを足す）、"
                  "fist（片手を握りに）、forearm_roll（両の拳が胸の前で回り合う動きを作り直す。elbows fixed で上腕を止める）。"
                  "区間は frames [開始, 終了) と前後の fade。stance と focal はそのまま引き継ぐ")
def edit_keypoints(inputs, parameters, outputs, context):
    require(inputs, ("points",), "motion.edit_keypoints")
    unknown = set(parameters) - {"edits"}
    if unknown:
        raise ToolError(f"motion.edit_keypoints の知らないパラメーター: {sorted(unknown)}（使えるのは edits）")
    edits = parameters.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ToolError("motion.edit_keypoints には edits（手直しの一覧）が要ります")
    try:
        with np.load(inputs["points"], allow_pickle=False) as data:
            points = data["points"].astype(np.float64)
            carried = {key: data[key] for key in data.files if key != "points"}
    except (OSError, KeyError, ValueError) as error:
        raise ToolError(f"points の npz を読めません（points が要ります）: {error}") from error
    if points.ndim != 3 or points.shape[1:] != (motion.N_POINTS, 3) or not np.isfinite(points).all():
        raise ToolError(f"points は有限の (コマ, {motion.N_POINTS}, 3) にしてください: {points.shape}")
    plan = [_edit_settings(edit, len(points), position) for position, edit in enumerate(edits)]   # 全部確かめてから直す
    applied = []
    for kind, settings in plan:
        if kind == "head_turn":
            points, result = motion_edit.head_turn(points, settings["degrees"], settings["start"], settings["end"],
                                                   settings["fade_in"], settings["fade_out"])
        elif kind == "fist":
            points, result = motion_edit.fist(points, settings["side"], settings["start"], settings["end"], settings["fade"])
        else:
            points, result = motion_edit.forearm_roll(points, **settings)
        applied.append({"kind": kind, **settings, **result})
    if not np.isfinite(points).all():
        raise ToolError("motion.edit_keypoints: 手直しした点に NaN・無限大が出ました")
    report = {"schema_version": "model3d-edited-keypoints/0.1", "frames": int(len(points)), "edits": applied}
    if "points" in outputs:
        _save_npz(outputs["points"], points=points.astype(np.float32), **carried)
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "edit_keypoints", "edits": [{k: v for k, v in a.items()
                                                                          if k in ("kind", "start", "end", "changed_frames")}
                                                                         for a in applied]}])


def _blender(parameters, context, operation):
    blender = parameters.get("blender") or (getattr(context, "tools", None) or {}).get("blender")
    if not blender:
        raise ToolError(f"{operation} には Blender の実行ファイル（パラメーター blender か --tool blender=PATH）が要ります")
    return blender


def _bone_prefix(parameters):
    prefix = parameters.get("bone_prefix", "mixamorig:")
    if not isinstance(prefix, str):
        raise ToolError(f"bone_prefix は文字列にしてください: {prefix!r}")
    return prefix


def _timeout(parameters):
    return None if parameters.get("timeout") is None else number(parameters, "timeout", minimum=0, exclusive=True)


RETARGET_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "retarget.py"
RETARGET_PARAMETERS = {"blender", "frames", "bone_prefix", "pitch0_deg", "foot_yaw", "resolve", "smooth_arms", "check", "fps",
                       "timeout"}


@register("motion.retarget", inputs=("points", "character", "skip_vertices", "head_dpitch"), outputs=("blend", "pose", "report"),
          cacheable=False,
          summary="整えた 70 点（points）を、Mixamo 式の骨のキャラ（character の .blend）へ Blender の中で移す。骨を点へ向け、脚は IK、"
                  "接地中は足を平らに。resolve で手・前腕の胴への刺さりを直し、smooth_arms でその前後をならす。出力は動きを打った .blend・"
                  "コマごとの骨の行列（pose の npz）・報告。Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def retarget(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    require(inputs, ("points", "character"), "motion.retarget")
    unknown = set(parameters) - RETARGET_PARAMETERS
    if unknown:
        raise ToolError(f"motion.retarget の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(RETARGET_PARAMETERS)}）")
    missing = [name for name in ("blend", "pose", "report") if name not in outputs]
    if missing:
        raise ToolError(f"motion.retarget には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "motion.retarget")
    foot_yaw = parameters.get("foot_yaw", "toes")
    if foot_yaw not in ("toes", "heel"):
        raise ToolError(f"foot_yaw は toes か heel: {foot_yaw!r}")
    frames = parameters.get("frames", "")
    if not isinstance(frames, str) or (frames and not all(part.strip().lstrip("-").isdigit() for part in frames.replace(",", ":").split(":"))):
        raise ToolError(f"frames は a:b[:step] か 0,5,9 の文字列にしてください: {frames!r}")
    args = ["--points", inputs["points"], "--character", inputs["character"], "--out-blend", outputs["blend"],
            "--pose", outputs["pose"], "--report", outputs["report"], "--bone-prefix", _bone_prefix(parameters), "--foot-yaw", foot_yaw,
            "--fps", str(number(parameters, "fps", 30, minimum=1, integer=True))]
    if frames:
        args += ["--frames", frames]
    if parameters.get("pitch0_deg") is not None:
        args += ["--pitch0", repr(float(number(parameters, "pitch0_deg")))]
    for name, flag in (("skip_vertices", "--skip-vertices"), ("head_dpitch", "--head-dpitch")):
        if name in inputs:
            args += [flag, inputs[name]]
    for name, flag in (("resolve", "--resolve"), ("smooth_arms", "--smooth-arms"), ("check", "--check")):
        value = parameters.get(name, False)
        if not isinstance(value, bool):
            raise ToolError(f"{name} は true か false: {value!r}")
        if value:
            args.append(flag)
    record = launcher.run_blender(blender, RETARGET_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    info = report.get("info", {})
    observation = {"kind": "retarget", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "frames": info.get("frames"), "scale": info.get("scale"),
                   "leg_scale": info.get("leg_scale"), "pitch0_deg": info.get("pitch0_deg"),
                   "resolved_frames": (info.get("resolve") or {}).get("frames_fixed")}
    if report.get("check"):
        observation["check_summary"] = report["check"]["summary"]
    return StepResult(observations=[observation])


TWIST_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "twist_split.py"
TWIST_PARAMETERS = {"blender", "frames", "bone_prefix", "timeout"}
# 直した後も変わってはいけない量の許容。単精度の誤差（実測で 0.0004 mm・0.00004° ほど）より十分大きく、目に見える差より十分小さい
TWIST_TOLERANCE = {"hand_turn_deg": 0.01, "forearm_direction_deg": 0.01, "fingertip_mm": 0.1}


@register("motion.twist_split", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="骨へ移した動き（blend）の前腕のねじれを、肘と手首に半分ずつ持たせ直す（Blender の中で）。手の向き・前腕の向き・"
                  "指先の位置は変えず、変わっていないことを確かめて報告に残す（許容を超えたら止まる）。frames は a:b（b を含む）で、"
                  "既定はシーンの開始〜終了。Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def twist_split(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    require(inputs, ("blend",), "motion.twist_split")
    unknown = set(parameters) - TWIST_PARAMETERS
    if unknown:
        raise ToolError(f"motion.twist_split の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(TWIST_PARAMETERS)}）")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"motion.twist_split には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "motion.twist_split")
    frames = parameters.get("frames", "")
    bounds = frames.split(":") if isinstance(frames, str) and frames else []
    if (not isinstance(frames, str) or (frames and (len(bounds) != 2 or not all(b.strip().lstrip("-").isdigit() for b in bounds)
                                                     or int(bounds[1]) < int(bounds[0])))):
        raise ToolError(f"frames は a:b（a ≤ b、b を含む）の文字列にしてください: {frames!r}")
    args = ["--blend", inputs["blend"], "--out-blend", outputs["blend"], "--report", outputs["report"],
            "--bone-prefix", _bone_prefix(parameters)]
    if frames:
        args += ["--frames", ":".join(b.strip() for b in bounds)]
    record = launcher.run_blender(blender, TWIST_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    unchanged = report.get("unchanged") or {}
    over = {name: unchanged.get(name) for name, limit in TWIST_TOLERANCE.items()
            if not (isinstance(unchanged.get(name), (int, float)) and unchanged[name] <= limit)}
    if over:
        raise ToolError(f"motion.twist_split の後で手・前腕の向きか指先の位置が変わりました: {over}（許容 {TWIST_TOLERANCE}）。"
                        "前腕や手に IK などの拘束やドライバーが掛かっているリグは扱えません")
    observation = {"kind": "twist_split", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "frames": report.get("frames"), "sides": report.get("sides"),
                   "unchanged": {name: round(float(value), 4) for name, value in unchanged.items()}}
    return StepResult(observations=[observation])


GROUND_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "ground_feet.py"
GROUND_PARAMETERS = {"blender", "bone_prefix", "foot_objects", "first_points_frame", "smooth_sigma_frames", "allow_sink_mm",
                     "timeout"}
# 腰を動かした量と、足の一番低い点が動いた量の差の許容。単精度の誤差（0.001 mm ほど）より十分大きく、目に見える差より十分小さい
GROUND_TOLERANCE_MM = 0.1


@register("motion.ground_feet", inputs=("blend", "points"), outputs=("blend", "report"), cacheable=False,
          summary="骨へ移した動き（blend）で、接地のコマ（points の stance）に足の一番低い点が床（z=0）に着くよう、腰の高さを足す"
                  "（Blender の中で）。足の点は foot_objects のメッシュか、足の骨の重みがいちばん大きい頂点。接地の無いコマは前後から"
                  "つなぎ、ガウスでならす。直した後に測り直し、腰を動かした量だけ足が動かなければ止まる。Blender は外部の実行ファイル"
                  "なので再利用しない（毎回実行）")
def ground_feet(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    require(inputs, ("blend", "points"), "motion.ground_feet")
    unknown = set(parameters) - GROUND_PARAMETERS
    if unknown:
        raise ToolError(f"motion.ground_feet の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(GROUND_PARAMETERS)}）")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"motion.ground_feet には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "motion.ground_feet")
    args = ["--blend", inputs["blend"], "--points", inputs["points"], "--out-blend", outputs["blend"],
            "--report", outputs["report"], "--bone-prefix", _bone_prefix(parameters),
            "--sigma", repr(float(number(parameters, "smooth_sigma_frames", 1.5, minimum=0, exclusive=True))),
            "--allow-sink-mm", repr(float(number(parameters, "allow_sink_mm", 3, minimum=0)))]
    foot_objects = parameters.get("foot_objects")
    if foot_objects is not None:
        if not isinstance(foot_objects, list) or not foot_objects or not all(isinstance(n, str) and n for n in foot_objects):
            raise ToolError(f"foot_objects は足のメッシュの名前（空でない文字列）の配列にしてください: {foot_objects!r}")
        for name in foot_objects:
            args += ["--foot-object", name]
    if parameters.get("first_points_frame") is not None:
        args += ["--first-points-frame", str(number(parameters, "first_points_frame", minimum=0, integer=True))]
    record = launcher.run_blender(blender, GROUND_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    check = report.get("lift_check_mm")
    if isinstance(check, bool) or not isinstance(check, (int, float)) or not check <= GROUND_TOLERANCE_MM:
        raise ToolError(f"motion.ground_feet で、腰を動かした量だけ足が動きませんでした（差 {check} mm、許容 {GROUND_TOLERANCE_MM} mm）。"
                        "足が腰の子でない・脚に IK などの拘束が掛かっているリグは扱えません")
    observation = {"kind": "ground_feet", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "frames": report.get("frames"),
                   "points_frames": report.get("points_frames"), "feet_from": (report.get("feet") or {}).get("from"),
                   "stance_frames": report.get("stance_frames"), "lift_mm": report.get("lift_mm"),
                   "lowest_point": report.get("lowest_point"), "lift_check_mm": round(float(check), 4)}
    return StepResult(observations=[observation])


PUSH_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "push_hands.py"
PUSH_PARAMETERS = {"blender", "bone_prefix", "body_objects", "passes", "gap_mm", "reach_mm", "cap_mm", "timeout"}


@register("motion.push_hands", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="骨へ移した動き（blend）で、手が体（胴・腰まわり・脚）に沈むコマに、二の腕を肩のまわりに少し回して、手を体の面から"
                  "浮かせる（Blender の中で）。肘から先の曲げと手の形は変えない。手と体の点は骨の重みで選ぶ（体は body_objects でも）。"
                  "passes 回くり返し、体の面の内側に入った手の点を直す前・後で数えて報告に残す。Blender は外部の実行ファイルなので"
                  "再利用しない（毎回実行）")
def push_hands(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    require(inputs, ("blend",), "motion.push_hands")
    unknown = set(parameters) - PUSH_PARAMETERS
    if unknown:
        raise ToolError(f"motion.push_hands の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PUSH_PARAMETERS)}）")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"motion.push_hands には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "motion.push_hands")
    args = ["--blend", inputs["blend"], "--out-blend", outputs["blend"], "--report", outputs["report"],
            "--bone-prefix", _bone_prefix(parameters),
            "--passes", str(number(parameters, "passes", 2, minimum=1, integer=True)),
            "--gap-mm", repr(float(number(parameters, "gap_mm", 6, minimum=0))),
            "--reach-mm", repr(float(number(parameters, "reach_mm", 40, minimum=0, exclusive=True))),
            "--cap-mm", repr(float(number(parameters, "cap_mm", 40, minimum=0, exclusive=True)))]
    body_objects = parameters.get("body_objects")
    if body_objects is not None:
        if not isinstance(body_objects, list) or not body_objects or not all(isinstance(n, str) and n for n in body_objects):
            raise ToolError(f"body_objects は体のメッシュの名前（空でない文字列）の配列にしてください: {body_objects!r}")
        for name in body_objects:
            args += ["--body-object", name]
    record = launcher.run_blender(blender, PUSH_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    observation = {"kind": "push_hands", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "frames": report.get("frames"),
                   "body_from": (report.get("selection") or {}).get("body_from"), "passes": report.get("passes"),
                   "inside": report.get("inside")}
    return StepResult(observations=[observation])


SPRING_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "spring_bones.py"
SPRING_PARAMETERS = {"blender", "chains", "colliders", "links", "target", "follow", "f_root_hz", "f_tip_hz", "damping", "bend",
                     "substeps", "push", "settle_s", "timeout"}
# 焼き付けた骨の向きとばねの節の向きの差、根元の head と根元の節の差の許容。単精度の誤差より十分大きく、目に見える差より十分小さい
SPRING_TOLERANCE = {"direction_deg": 0.01, "root_mm": 0.1}


def _chain_settings(parameters, f_root, f_tip, damping):
    """鎖の骨の工程（髪・帯）に共通の設定: 鎖・当たりのカプセル・狙いの形・付いて行く骨とばねの数"""
    chains = parameters.get("chains")
    if not isinstance(chains, list) or not chains or not all(isinstance(n, str) and n for n in chains) or len(set(chains)) != len(chains):
        raise ToolError(f"chains は鎖の根元の骨の名前（空でない・重ならない文字列）の配列にしてください: {chains!r}")
    colliders = parameters.get("colliders", [])
    if not isinstance(colliders, list):
        raise ToolError(f"colliders は配列にしてください: {colliders!r}")
    checked = []
    for c in colliders:
        if not isinstance(c, dict) or set(c) != {"from", "to", "radius_mm"} or not all(isinstance(c[k], str) and c[k] for k in ("from", "to")):
            raise ToolError(f"colliders の各項目は {{from: 骨, to: 骨, radius_mm: 数}} にしてください: {c!r}")
        checked.append({"from": c["from"], "to": c["to"],
                        "radius_mm": float(number(c, "radius_mm", minimum=0, exclusive=True))})
    target = parameters.get("target", "rest")
    if target not in ("rest", "animation"):
        raise ToolError(f"target は rest か animation: {target!r}")
    follow = parameters.get("follow", [])
    if not isinstance(follow, list) or not all(isinstance(x, dict) and set(x) == {"bone", "height_m"} and isinstance(x["bone"], str)
                                               and x["bone"] for x in follow):
        raise ToolError(f"follow は {{bone: 骨, height_m: 静止の高さ}} の配列にしてください: {follow!r}")
    follow = [{"bone": x["bone"], "height_m": float(number(x, "height_m"))} for x in follow]
    if len({x["height_m"] for x in follow}) != len(follow):
        raise ToolError(f"follow の height_m は、骨ごとに違う高さにしてください: {follow!r}")
    if follow and target != "rest":
        raise ToolError("follow は target が rest のときだけ使えます")
    return {"chains": chains, "colliders": checked, "target": target, "follow": follow,
            "f_root_hz": float(number(parameters, "f_root_hz", f_root, minimum=0, exclusive=True)),
            "f_tip_hz": float(number(parameters, "f_tip_hz", f_tip, minimum=0, exclusive=True)),
            "damping": float(number(parameters, "damping", damping, minimum=0)),
            "substeps": number(parameters, "substeps", 8, minimum=1, integer=True),
            "settle_s": float(number(parameters, "settle_s", 1.0, minimum=0))}


def _spring_settings(parameters):
    settings = _chain_settings(parameters, 3.2, 1.1, 0.30)
    links = parameters.get("links", [])
    if not isinstance(links, list) or not all(
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(i, int) and not isinstance(i, bool)
                                                             and 0 <= i < len(settings["chains"]) for i in pair)
            and pair[0] != pair[1] for pair in links):
        raise ToolError(f"links は chains の番号の組（[i, j]、i と j は違う）の配列にしてください: {links!r}")
    bend = float(number(parameters, "bend", 0.12, minimum=0))
    push = float(number(parameters, "push", 0.3, minimum=0, exclusive=True))
    if bend > 1 or push > 1:
        raise ToolError(f"bend と push は 1 以下にしてください: bend {bend!r}, push {push!r}")
    return {**settings, "links": links, "bend": bend, "push": push}


def _fraction(parameters, key, default):
    value = float(number(parameters, key, default, minimum=0))
    if value > 1:
        raise ToolError(f"{key} は 0〜1 にしてください: {value!r}")
    return value


def _ribbon_settings(parameters):
    settings = _chain_settings(parameters, 4.0, 1.8, 0.35)
    thigh = parameters.get("thigh")
    if thigh is not None:
        if not isinstance(thigh, dict) or set(thigh) != {"bone", "weight", "from_height_m", "span_m"} or not (
                isinstance(thigh["bone"], str) and thigh["bone"]):
            raise ToolError(f"thigh は {{bone: 骨, weight: 割合, from_height_m: 高さ, span_m: 幅}} にしてください: {thigh!r}")
        thigh = {"bone": thigh["bone"], "weight": _fraction(thigh, "weight", None),
                 "from_height_m": float(number(thigh, "from_height_m")),
                 "span_m": float(number(thigh, "span_m", minimum=0, exclusive=True))}
        if not settings["follow"]:
            raise ToolError("thigh は follow と一緒に使ってください（骨と高さの並びに、下ほど太ももの動きを混ぜる）")
    fitted = parameters.get("fitted", [])
    if not isinstance(fitted, list):
        raise ToolError(f"fitted は配列にしてください: {fitted!r}")
    checked = []
    for spec in fitted:
        ok = (isinstance(spec, dict) and {"bones", "slices"} <= set(spec) <= {"bones", "slices", "objects", "extend_first"}
              and isinstance(spec["bones"], list) and len(spec["bones"]) >= 2 and all(isinstance(b, str) and b for b in spec["bones"])
              and isinstance(spec["slices"], list) and len(spec["slices"]) == len(spec["bones"]) - 1
              and all(isinstance(ts, list) and ts and all(isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= t <= 1
                                                          for t in ts) for ts in spec["slices"])
              and (spec.get("objects") is None or (isinstance(spec["objects"], list) and spec["objects"]
                                                  and all(isinstance(o, str) and o for o in spec["objects"])))
              and isinstance(spec.get("extend_first", True), bool))
        if not ok:
            raise ToolError("fitted の各項目は {bones: 骨の並び（2 本以上）, slices: 骨ごとの輪切りの位置（0〜1）の並び"
                            f"（骨の数 - 1 個）, objects: メッシュの名前（任意）, extend_first: 真偽（任意）}} にしてください: {spec!r}")
        checked.append({k: spec[k] for k in ("bones", "slices", "objects", "extend_first") if k in spec})
    hips = parameters.get("hips", "mixamorig:Hips")
    if not isinstance(hips, str) or not hips:
        raise ToolError(f"hips は腰の骨の名前にしてください: {hips!r}")
    interp = parameters.get("interp", True)
    if not isinstance(interp, bool):
        raise ToolError(f"interp は true か false: {interp!r}")
    return {**settings, "model": "ribbon", "thigh": thigh, "fitted": checked,
            "fit_margin_mm": float(number(parameters, "fit_margin_mm", 22, minimum=0)), "hips": hips,
            "gravity": float(number(parameters, "gravity", 1.0, minimum=0)), "lane": _fraction(parameters, "lane", 0.8),
            "over": float(number(parameters, "over", 0.5, minimum=0)),
            "root_move_mm": float(number(parameters, "root_move_mm", 25, minimum=0)),
            "inelastic": _fraction(parameters, "inelastic", 1.0), "interp": interp}


def _run_chain_bones(operation, allowed, make_settings, inputs, parameters, outputs, context):
    """鎖の骨の工程（髪・帯）を Blender で動かし、焼き付けの確かめを許容と比べて、報告と起動の記録を返す"""
    from ..blender_adapter import launcher
    from ..io import read_json
    require(inputs, ("blend",), operation)
    unknown = set(parameters) - allowed
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(allowed)}）")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"{operation} には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, operation)
    settings_path = Path(context.step_dir) / "spring_settings.json"
    atomic_json(settings_path, make_settings(parameters))
    args = ["--blend", inputs["blend"], "--out-blend", outputs["blend"], "--report", outputs["report"],
            "--settings", str(settings_path)]
    record = launcher.run_blender(blender, SPRING_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    check = report.get("bake_check") or {}
    over = {name: check.get(name) for name, limit in SPRING_TOLERANCE.items()
            if isinstance(check.get(name), bool) or not isinstance(check.get(name), (int, float)) or not check[name] <= limit}
    if over:
        raise ToolError(f"{operation} で、焼き付けた骨がばねの節と合いません: {over}（許容 {SPRING_TOLERANCE}）。"
                        "鎖や親の骨の拡大が 1 でない・拘束が掛かっているリグは扱えません")
    common = {"exit_code": record["exit_code"], "seconds": record["seconds"], "blender_version": record["blender_version"],
              "frames": report.get("frames"), "chains": len(report.get("chains") or []),
              "spring_stretch_mm": report.get("spring_stretch_mm"), "bones_vs_springs_mm": report.get("bones_vs_springs_mm"),
              "bake_check": {name: round(float(value), 5) for name, value in check.items()}}
    return report, common


@register("motion.spring_bones", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="骨へ移した動き（blend）で、鎖の骨（髪など。chains に根元の骨）を、ばねで体の動きに遅れて揺らし、回転のキーに"
                  "焼き付ける（Blender の中で）。狙いは、親に固く付いた形（rest。follow で骨と高さの並びに沿わせる）か、鎖にある動き"
                  "（animation）。根元ほどばねが強く毛先ほど弱い。節の間の長さを保ち、隣の鎖（links）と離れすぎず、"
                  "当たりのカプセル（colliders）の外へ押し出す。焼き付けた後に骨を測り直し、骨の向きと根元がばねの節と合わなければ止まる。"
                  "Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def spring_bones(inputs, parameters, outputs, context):
    report, common = _run_chain_bones("motion.spring_bones", SPRING_PARAMETERS, _spring_settings, inputs, parameters, outputs,
                                      context)
    return StepResult(observations=[{"kind": "spring_bones", **common,
                                     "bones_per_chain": (report.get("chains") or [{}])[0].get("bones"),
                                     "collision_pushes": report.get("collision_pushes"), "motion": report.get("motion")}])


RIBBON_PARAMETERS = {"blender", "chains", "colliders", "target", "follow", "thigh", "fitted", "fit_margin_mm", "hips", "f_root_hz",
                     "f_tip_hz", "damping", "gravity", "lane", "over", "root_move_mm", "inelastic", "interp", "substeps", "settle_s",
                     "timeout"}


@register("motion.ribbon_bones", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="骨へ移した動き（blend）で、帯の垂れの鎖の骨（chains に根元の骨。長さは鎖ごとに違ってよい）を、重力で垂れて"
                  "太ももの上を前へ乗り越えるように揺らし、回転のキーに焼き付ける（Blender の中で）。各節は、ひとつ上の節から狙いの形の"
                  "向きへ元の長さだけ下へばねで引かれる。狙いは follow（骨と高さの並び）に thigh（下ほど太ももの動き）を混ぜられる。"
                  "当たりは colliders のカプセルと、fitted（骨に沿ってメッシュを輪切りにして測る、太さの変わるカプセル）。押す向きは"
                  "腰（hips）の左右を減らし、前と上へ寄せる。焼き付けた後に骨を測り直し、合わなければ止まる。Blender は外部の"
                  "実行ファイルなので再利用しない（毎回実行）")
def ribbon_bones(inputs, parameters, outputs, context):
    report, common = _run_chain_bones("motion.ribbon_bones", RIBBON_PARAMETERS, _ribbon_settings, inputs, parameters, outputs,
                                      context)
    return StepResult(observations=[{"kind": "ribbon_bones", **common, "colliders": report.get("colliders"),
                                     "ribbons": report.get("ribbons")}])


SLEEVE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "sleeve_bones.py"
SLEEVE_PARAMETERS = {"blender", "sleeves", "ratio", "f_hz", "damping", "substeps", "timeout"}
SLEEVE_TOLERANCE_DEG = 0.01   # 焼き付けた袖口の向きと狙いの差の許容（単精度の誤差より十分大きく、目に見える差より十分小さい）


def _sleeve_settings(parameters):
    sleeves = parameters.get("sleeves")
    if not isinstance(sleeves, list) or not sleeves or not all(
            isinstance(s, dict) and set(s) == {"bone", "follow"} and all(isinstance(s[k], str) and s[k] for k in ("bone", "follow"))
            for s in sleeves):
        raise ToolError(f"sleeves は {{bone: 袖口の骨, follow: 曲げに付いて行く骨}} の配列（空でない）にしてください: {sleeves!r}")
    if len({s["bone"] for s in sleeves}) != len(sleeves) or any(s["bone"] == s["follow"] for s in sleeves):
        raise ToolError(f"sleeves の袖口の骨は重ならず、follow と違う骨にしてください: {sleeves!r}")
    ratio = float(number(parameters, "ratio", 0.75, minimum=0))
    if ratio > 1:
        raise ToolError(f"ratio は 0〜1 にしてください: {ratio!r}")
    return {"sleeves": [{"bone": s["bone"], "follow": s["follow"]} for s in sleeves], "ratio": ratio,
            "f_hz": float(number(parameters, "f_hz", 4.0, minimum=0, exclusive=True)),
            "damping": float(number(parameters, "damping", 0.45, minimum=0)),
            "substeps": number(parameters, "substeps", 8, minimum=1, integer=True)}


@register("motion.sleeve_bones", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="骨へ移した動き（blend）で、袖口の骨（sleeves の bone）を、関節（follow の骨。前腕など）の曲げの ratio 倍に、"
                  "減衰つきのばねで少し遅れて付いて行かせ、回転のキーに焼き付ける（Blender の中で）。ねじれは入れない。焼き付けた後に"
                  "向きを測り直し、狙いと合わなければ止まる。Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def sleeve_bones(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    require(inputs, ("blend",), "motion.sleeve_bones")
    unknown = set(parameters) - SLEEVE_PARAMETERS
    if unknown:
        raise ToolError(f"motion.sleeve_bones の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(SLEEVE_PARAMETERS)}）")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"motion.sleeve_bones には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "motion.sleeve_bones")
    settings_path = Path(context.step_dir) / "sleeve_settings.json"
    atomic_json(settings_path, _sleeve_settings(parameters))
    args = ["--blend", inputs["blend"], "--out-blend", outputs["blend"], "--report", outputs["report"],
            "--settings", str(settings_path)]
    record = launcher.run_blender(blender, SLEEVE_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    check = report.get("bake_check_deg")
    if isinstance(check, bool) or not isinstance(check, (int, float)) or not check <= SLEEVE_TOLERANCE_DEG:
        raise ToolError(f"motion.sleeve_bones で、焼き付けた袖口の向きが狙いと合いません（差 {check}°、許容 {SLEEVE_TOLERANCE_DEG}°）。"
                        "袖口の骨や親の骨の拡大が 1 でない・拘束が掛かっているリグは扱えません")
    observation = {"kind": "sleeve_bones", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "frames": report.get("frames"), "sleeves": report.get("sleeves"),
                   "bake_check_deg": round(float(check), 5)}
    return StepResult(observations=[observation])


BONE_PATH_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "bone_path.py"
BONE_PATH_PARAMETERS = {"blender", "bone_prefix", "bones", "armature", "frame_range", "timeout"}


def _bone_path_settings(parameters, track):
    prefix = _bone_prefix(parameters)
    bones = parameters.get("bones", ["Hips", "Head"])
    if not isinstance(bones, list) or not bones or not all(isinstance(b, str) and b for b in bones) or len(set(bones)) != len(bones):
        raise ToolError(f"bones は骨の名前（bone_prefix の後ろ。空でない・重ならない文字列）の並びにしてください: {bones!r}")
    armature = parameters.get("armature")
    if armature is not None and not (isinstance(armature, str) and armature):
        raise ToolError(f"armature はアーマチュアの物体の名前にしてください: {armature!r}")
    span = parameters.get("frame_range")
    if span is not None and track is not None:
        raise ToolError("frame_range と track は一緒に使えません（track があれば、その動きのコマを測ります）")
    frames = None
    if track is not None:
        frames = sorted({int(fr["f"]) for fr in track["frames"]})
    elif span is not None:
        if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in span)
                or span[0] > span[1]):
            raise ToolError(f"frame_range は [最初, 最後] のコマ（整数、最初 ≤ 最後）にしてください: {span!r}")
        frames = list(range(span[0], span[1] + 1))
    return {"bones": [prefix + b for b in bones], "frames": frames, "armature": armature}


@register("motion.bone_path", inputs=("blend", "track"), outputs=("path",), cacheable=False,
          summary="動きのコマごとに、骨（既定は腰と頭）の付け根の位置（世界の座標、m）を測る（Blender の中で）。track（描くコマの"
                  "並び。model3d-stage-track/0.1）があればその動きのコマを重ねず小さい順に、無ければ frame_range か .blend のシーンの"
                  "全コマを測る。path は model3d-bone-path/0.1（plan.stage がカメラの狙いに使う）。Blender は外部の実行ファイル"
                  "なので再利用しない（毎回実行）")
def bone_path(inputs, parameters, outputs, context):
    from .. import contracts
    from ..blender_adapter import launcher
    from ..io import read_json
    from .render_ops import _track
    require(inputs, ("blend",), "motion.bone_path")
    unknown = set(parameters) - BONE_PATH_PARAMETERS
    if unknown:
        raise ToolError(f"motion.bone_path の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(BONE_PATH_PARAMETERS)}）")
    if "path" not in outputs:
        raise ToolError("motion.bone_path には outputs の path が要ります")
    blender = _blender(parameters, context, "motion.bone_path")
    track = _track(inputs["track"]) if inputs.get("track") is not None else None
    settings = _bone_path_settings(parameters, track)
    settings_path = Path(context.step_dir) / "bone_path_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--out", outputs["path"]]
    record = launcher.run_blender(blender, BONE_PATH_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    path = contracts.validate("bone-path", read_json(outputs["path"]))
    observation = {"kind": "bone_path", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "frames": len(path["frames"]), "bones": sorted(path["bones"]),
                   "scale_length": path.get("scale_length")}
    return StepResult(observations=[observation])
