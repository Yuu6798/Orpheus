"""動きの下ごしらえ: 推定した 3D の点（SAM 3D Body の MHR70）を、キャラに載せる前に整える。"""
import os
import tempfile
from pathlib import Path

import numpy as np

from .. import motion
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
