"""描く: 塗りと輪郭線を付けたキャラを、舞台と光の中で、コマごとに PNG に描く（Blender の中で）。"""
from pathlib import Path

from ..io import ToolError, atomic_json, resolve_path, sha256
from . import StepResult, number, register, require

RENDER_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "stage_render.py"
RENDER_PARAMETERS = {"blender", "resolution", "samples", "raytracing", "bloom", "bone_prefix", "fps", "camera", "outline", "stage",
                     "background", "frames_dir", "only", "timeout"}
CAMERA_KEYS = {"sensor_height", "lens", "az", "dist", "height", "aim", "follow", "follow_sigma"}
OUTLINE_KEYS = {"line_px", "reference_height", "scale"}
STAGE_KEYS = {"enabled", "stripe_floor", "stripe_soft"}


def _section(parameters, key, allowed):
    section = parameters.get(key, {})
    if not isinstance(section, dict):
        raise ToolError(f"{key} は辞書にしてください: {section!r}")
    unknown = set(section) - allowed
    if unknown:
        raise ToolError(f"{key} の知らない鍵: {sorted(unknown)}（使えるのは {sorted(allowed)}）")
    return section


def _flag(parameters, key, where=None):
    value = parameters[key]
    if not isinstance(value, bool):
        raise ToolError(f"{where or key} は true か false: {value!r}")
    return value


def _render_settings(parameters):
    s = {}
    if "resolution" in parameters:
        res = parameters["resolution"]
        if (not isinstance(res, list) or len(res) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 16
                                                                    for v in res)):
            raise ToolError(f"resolution は [幅, 高さ]（16 以上の整数）にしてください: {res!r}")
        s["resolution"] = res
    if "samples" in parameters:
        s["samples"] = int(number(parameters, "samples", minimum=1, integer=True))
    for key in ("raytracing", "bloom"):
        if key in parameters:
            s[key] = _flag(parameters, key)
    if "bone_prefix" in parameters:
        if not isinstance(parameters["bone_prefix"], str):
            raise ToolError(f"bone_prefix は文字列にしてください: {parameters['bone_prefix']!r}")
        s["bone_prefix"] = parameters["bone_prefix"]
    if parameters.get("fps") is not None:
        s["fps"] = float(number(parameters, "fps", minimum=0, exclusive=True))
    camera = _section(parameters, "camera", CAMERA_KEYS)
    c = {}
    for key in ("sensor_height", "lens", "dist", "follow_sigma"):
        if key in camera:
            c[key] = float(number(camera, key, minimum=0, exclusive=True))
    for key in ("az", "height", "aim"):
        if key in camera:
            c[key] = float(number(camera, key))
    if "follow" in camera:
        c["follow"] = float(number(camera, "follow", minimum=0))
        if c["follow"] > 1:
            raise ToolError(f"camera.follow は 0〜1 にしてください: {c['follow']!r}")
    outline = _section(parameters, "outline", OUTLINE_KEYS)
    o = {}
    for key in ("line_px", "reference_height"):
        if key in outline:
            o[key] = float(number(outline, key, minimum=0, exclusive=True))
    if "scale" in outline:
        scale = outline["scale"]
        if not isinstance(scale, dict) or not all(isinstance(k, str) and k and isinstance(v, (int, float)) and not isinstance(v, bool)
                                                  and v > 0 for k, v in scale.items()):
            raise ToolError(f"outline.scale は {{元のメッシュの名前: 太さの倍率（0 より大きい）}} にしてください: {scale!r}")
        o["scale"] = {k: float(v) for k, v in scale.items()}
    stage = _section(parameters, "stage", STAGE_KEYS)
    st = {}
    if "enabled" in stage:
        st["enabled"] = _flag(stage, "enabled", "stage.enabled")
    for key in ("stripe_floor", "stripe_soft"):
        if key in stage:
            st[key] = float(number(stage, key, minimum=0))
    if st.get("stripe_soft", 0.0) >= 0.05:
        raise ToolError("stage.stripe_soft は 0.05 より小さくしてください（帯の境 0.66〜0.76 が入れ替わる）")
    background = parameters.get("background")
    if background is not None:
        color = background.get("color") if isinstance(background, dict) and set(background) == {"color"} else None
        if background != "transparent" and not (isinstance(color, list) and len(color) == 3 and all(
                isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in color)):
            raise ToolError(f"background は \"transparent\" か {{\"color\": [R, G, B]}}（0〜255 の整数）にしてください: {background!r}")
        if st.get("enabled", True):
            raise ToolError("background を使うときは stage.enabled を false にしてください（舞台の床や壁が背景を隠す）")
        s["background"] = background
    only = parameters.get("only")
    if only is not None:
        if (not isinstance(only, list) or not only or len(set(only)) != len(only)
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in only)):
            raise ToolError(f"only は描くコマの T（1 以上の整数、重ならない）の並びにしてください: {only!r}")
        s["only"] = only
    return {**s, "camera": c, "outline": o, "stage": st}


def _track(path):
    """トラックを読み、契約と、T が重ならないことを確かめる。"""
    from .. import contracts
    track = contracts.load("stage-track", path)
    seen = set()
    for frame in track["frames"]:
        if frame["T"] in seen:
            raise ToolError(f"トラックの T が重なっています: {frame['T']}")
        seen.add(frame["T"])
    return track


def frames_manifest(report, frames_dir):
    """描くコマの並び（報告の planned）のうち、ファイルのあるコマの sha256 と大きさ。無いコマは missing。"""
    from .. import contracts
    from ..blender_adapter.stage_render import frame_name
    frames, missing = [], []
    for item in report["planned"]:
        file = Path(frames_dir) / frame_name(item["T"])
        if file.is_file():
            frames.append({"T": item["T"], "f": item["f"], "file": file.name, "sha256": sha256(file), "bytes": file.stat().st_size})
        else:
            missing.append(item["T"])
    manifest = {"schema_version": "model3d-frames/0.1", "directory": str(Path(frames_dir).resolve()), "fps": report["fps"],
                "resolution": report["resolution"], "frames": frames, "missing": missing}
    return contracts.validate("frames", manifest)


@register("render.stage", inputs=("blend", "track"), outputs=("frames", "report"), cacheable=False,
          summary="塗りと輪郭線を付けたキャラ（blend）を、舞台（床・LED の壁・トラス・光の柱）と 4 つのスポットの中で、コマごとに "
                  "PNG に描く（Blender の EEVEE）。トラック（model3d-stage-track/0.1）があれば、そのコマの並びとカメラ・舞台・光・"
                  "塗りの値で、無ければシーンの全コマを腰を追う正面のカメラと動かない光で描く。輪郭線の太さはカメラの距離で"
                  "画面の上の太さにそろえる。舞台を切って background で緑背景か透けにも描ける。描いたコマはとばす（続きから描ける）。frames は描いたコマの一覧（model3d-frames/0.1）。"
                  "Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def stage_render(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "render.stage")
    unknown = set(parameters) - RENDER_PARAMETERS
    if unknown:
        raise ToolError(f"render.stage の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(RENDER_PARAMETERS)}）")
    missing = [name for name in ("frames", "report") if name not in outputs]
    if missing:
        raise ToolError(f"render.stage には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "render.stage")
    settings = _render_settings(parameters)
    if inputs.get("track") is not None:
        _track(inputs["track"])
    if parameters.get("frames_dir"):
        frames_dir = resolve_path(context.recipe_dir, parameters["frames_dir"])
    else:
        frames_dir = Path(context.step_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    settings_path = Path(context.step_dir) / "render_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--frames-dir", str(frames_dir),
            "--report", outputs["report"]]
    if inputs.get("track") is not None:
        args += ["--track", inputs["track"]]
    record = launcher.run_blender(blender, RENDER_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    manifest = frames_manifest(report, frames_dir)
    atomic_json(outputs["frames"], manifest)
    observation = {"kind": "stage_render", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "planned": len(report["planned"]),
                   "rendered": len(report.get("rendered") or []), "skipped_existing": len(report.get("skipped_existing") or []),
                   "frames": len(manifest["frames"]), "missing": len(manifest["missing"])}
    return StepResult(observations=[observation])
