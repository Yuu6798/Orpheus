"""演出版（EEVEE で 1 回で描く）の工程: 光の段取り表からコマごとの光の値のトラックを作る（plan.lights。計算だけ）。
演出版を Blender 5.x の EEVEE で 1 回でコマごとに描く（render.show）。"""
from pathlib import Path

from ..io import ToolError, atomic_json, sha256
from . import StepResult, number, register, require


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


SHOW_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "show_render.py"
SHOW_PARAMETERS = {"blender", "timeout", "resolution", "samples", "raytracing", "shadows", "view_transform", "camera", "motion_blur",
                   "cuts", "lights", "ambient", "world", "bloom", "expressions", "only"}
CAMERA_KEYS = {"sensor_height", "clip"}
BLUR_KEYS = {"shutter", "steps", "off"}
LIGHT_KEYS = {"energy", "base", "color"}
BLOOM_KEYS = {"quality", "threshold", "smoothness", "clamp", "maximum", "strength", "size"}
QUALITY = ("High", "Medium", "Low")


def _frames(value, key, minimum=1):
    if (not isinstance(value, list) or not all(isinstance(T, int) and not isinstance(T, bool) and T >= minimum for T in value)
            or len(set(value)) != len(value)):
        raise ToolError(f"{key} は {minimum} 以上の整数（重ならない）の並びにしてください: {value!r}")
    return sorted(value)


def _flag(value, key):
    if not isinstance(value, bool):
        raise ToolError(f"{key} は true か false にしてください: {value!r}")
    return value


def _section(parameters, key, allowed):
    section = parameters.get(key, {})
    if not isinstance(section, dict):
        raise ToolError(f"{key} は辞書にしてください: {section!r}")
    unknown = set(section) - allowed
    if unknown:
        raise ToolError(f"{key} の知らない鍵: {sorted(unknown)}（使えるのは {sorted(allowed)}）")
    return section


def _show_settings(parameters):
    """render.show の設定を確かめる。省いたものは書かない（Blender の側の既定で埋める）。"""
    s = {}
    if "resolution" in parameters:
        res = parameters["resolution"]
        if not isinstance(res, list) or len(res) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in res):
            raise ToolError(f"resolution は [幅, 高さ]（1 以上の整数）にしてください: {res!r}")
        s["resolution"] = res
    if "samples" in parameters:
        s["samples"] = number(parameters, "samples", minimum=1, integer=True)
    for key in ("raytracing", "shadows"):
        if key in parameters:
            s[key] = _flag(parameters[key], key)
    if "view_transform" in parameters:
        if not isinstance(parameters["view_transform"], str) or not parameters["view_transform"]:
            raise ToolError(f"view_transform は色の変換の名前にしてください: {parameters['view_transform']!r}")
        s["view_transform"] = parameters["view_transform"]
    camera = _section(parameters, "camera", CAMERA_KEYS)
    if camera:
        s["camera"] = {}
        if "sensor_height" in camera:
            s["camera"]["sensor_height"] = float(number(camera, "sensor_height", minimum=0, exclusive=True))
        if "clip" in camera:
            clip = camera["clip"]
            if (not isinstance(clip, list) or len(clip) != 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in clip)
                    or not 0 < clip[0] < clip[1]):
                raise ToolError(f"camera.clip は [近い, 遠い]（0 < 近い < 遠い。m）にしてください: {clip!r}")
            s["camera"]["clip"] = [float(v) for v in clip]
    blur = _section(parameters, "motion_blur", BLUR_KEYS)
    if blur:
        s["motion_blur"] = {}
        if "shutter" in blur:
            s["motion_blur"]["shutter"] = float(number(blur, "shutter", minimum=0))
        if "steps" in blur:
            s["motion_blur"]["steps"] = number(blur, "steps", minimum=1, integer=True)
        if "off" in blur:
            s["motion_blur"]["off"] = _frames(blur["off"], "motion_blur.off")
    if "cuts" in parameters:
        s["cuts"] = _frames(parameters["cuts"], "cuts", minimum=2)
    if "lights" in parameters:
        lights = parameters["lights"]
        if not isinstance(lights, dict):
            raise ToolError(f"lights は {{光の物体の名前: {{energy, base, color}}}} にしてください: {lights!r}")
        s["lights"] = {}
        for name, spec in lights.items():
            if not isinstance(spec, dict) or not ({"energy", "color"} & set(spec)):
                raise ToolError(f"lights.{name} は {{energy: 値の名前, base: W, color: 値の名前}}（energy か color は要る）にしてください")
            unknown = set(spec) - LIGHT_KEYS
            if unknown:
                raise ToolError(f"lights.{name} の知らない鍵: {sorted(unknown)}（使えるのは {sorted(LIGHT_KEYS)}）")
            entry = {}
            for key in ("energy", "color"):
                if key in spec:
                    if not isinstance(spec[key], str) or not spec[key]:
                        raise ToolError(f"lights.{name}.{key} は光のトラックの値の名前にしてください: {spec[key]!r}")
                    entry[key] = spec[key]
            if "base" in spec:
                entry["base"] = float(number(spec, "base", minimum=0))
            s["lights"][name] = entry
    if parameters.get("ambient") is not None:
        if not isinstance(parameters["ambient"], str) or not parameters["ambient"]:
            raise ToolError(f"ambient は光のトラックの値の名前にしてください: {parameters['ambient']!r}")
        s["ambient"] = parameters["ambient"]
    if parameters.get("world") is not None:
        world = parameters["world"]
        if (not isinstance(world, list) or len(world) != 3
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in world)):
            raise ToolError(f"world は空の色 [R, G, B]（線形、0 以上）にしてください: {world!r}")
        s["world"] = [float(v) for v in world]
    if parameters.get("bloom") is not None:
        bloom = _section(parameters, "bloom", BLOOM_KEYS)
        s["bloom"] = {}
        if "quality" in bloom:
            if bloom["quality"] not in QUALITY:
                raise ToolError(f"bloom.quality は {QUALITY} のどれかにしてください: {bloom['quality']!r}")
            s["bloom"]["quality"] = bloom["quality"]
        if "clamp" in bloom:
            s["bloom"]["clamp"] = _flag(bloom["clamp"], "bloom.clamp")
        for key in ("threshold",):
            if key in bloom:
                s["bloom"][key] = float(number(bloom, key))
        for key in ("maximum", "strength"):
            if key in bloom:
                s["bloom"][key] = float(number(bloom, key, minimum=0))
        for key in ("smoothness", "size"):
            if key in bloom:
                value = float(number(bloom, key, minimum=0))
                if value > 1:
                    raise ToolError(f"bloom.{key} は 0〜1 にしてください: {value!r}")
                s["bloom"][key] = value
    if parameters.get("expressions") is not None:
        s["expressions"] = _expressions(parameters["expressions"])
    if parameters.get("only") is not None:
        s["only"] = _frames(parameters["only"], "only")
    return s


def _expressions(value):
    """表情: {objects: 物体の名前の並び, values: {形状キー: [[秒, 値], …]（秒の小さい順）}}（render.passes の values と同じ書き方）。"""
    if not isinstance(value, dict) or set(value) != {"objects", "values"}:
        raise ToolError(f"expressions は {{objects: 物体の名前の並び, values: {{形状キー: [[秒, 値], …]}}}} にしてください")
    objects, values = value["objects"], value["values"]
    if not isinstance(objects, list) or not objects or not all(isinstance(n, str) and n for n in objects) or len(set(objects)) != len(objects):
        raise ToolError(f"expressions.objects は物体の名前（重ならない）の並びにしてください: {objects!r}")
    if not isinstance(values, dict) or not values:
        raise ToolError("expressions.values は {形状キー: [[秒, 値], …]} にしてください")
    for key, points in values.items():
        if (not isinstance(points, list) or not points
                or not all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)
                           for p in points)):
            raise ToolError(f"expressions.values.{key} は [[秒, 値], …] にしてください")
        if any(b[0] < a[0] for a, b in zip(points, points[1:])):
            raise ToolError(f"expressions.values.{key} の秒は小さい順にしてください")
    return {"objects": objects, "values": values}


def show_manifest(report, frames_dir):
    """描くコマの並び（報告の planned）のうち、ファイルのあるコマの sha256 と大きさ。無いコマは missing。"""
    from .. import contracts
    from ..blender_adapter.show_render import frame_name
    frames, missing = [], []
    for item in report["planned"]:
        file = Path(frames_dir) / frame_name(item["T"])
        if file.is_file():
            frames.append({"T": item["T"], "f": item["f"], "file": file.name, "sha256": sha256(file), "bytes": file.stat().st_size})
        else:
            missing.append(item["T"])
    return contracts.validate("frames", {"schema_version": "model3d-frames/0.1", "directory": str(Path(frames_dir).resolve()),
                                         "fps": report["fps"], "resolution": report["resolution"], "frames": frames, "missing": missing})


@register("render.show", inputs=("blend", "cameras", "lights", "hooks"), outputs=("frames", "report"), cacheable=False,
          summary="演出版を、Blender 5.x の EEVEE で 1 回でコマごとに PNG に描く（3 作目の VRM のダンスの演出版の描き方）。カメラ"
                  "（cameras。model3d-cameras/0.1）は全部のコマをキーにして置き、カットの最後のキー（cuts の 1 つ前のコマ）を止めるので、"
                  "動きのぶれ（motion_blur。コマから後ろ）がカットをまたがない。fstop と focus_m のコマは被写界深度。光の値"
                  "（lights。plan.lights の model3d-light-track/0.1）で、名前で指した光の強さと色、look.lit の塗りのまわりの明るさ"
                  "（ambient）をコマごとに入れる。表情の形状キー（expressions）、空（world）、にじみ（bloom）。作品ごとの舞台は、"
                  "フック（hooks。Python の台本。レシピに明示したときだけ実行し、sha256 を報告に残す）の build と frame で作って動かす。"
                  "描いたコマは毎回描き直す（とばさない）。frames は描いたコマの一覧（model3d-frames/0.1）。Blender が要り、再利用しない（毎回実行）")
def show_render(inputs, parameters, outputs, context):
    from .. import contracts
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend", "cameras"), "render.show")
    unknown = set(parameters) - SHOW_PARAMETERS
    if unknown:
        raise ToolError(f"render.show の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(SHOW_PARAMETERS)}）")
    missing = [name for name in ("frames", "report") if name not in outputs]
    if missing:
        raise ToolError(f"render.show には outputs の {', '.join(missing)} が要ります")
    settings = _show_settings(parameters)
    if (settings.get("lights") or settings.get("ambient")) and inputs.get("lights") is None:
        raise ToolError("render.show の lights・ambient には、入力 lights（plan.lights の光のトラック）が要ります")
    cameras = contracts.load("cameras", inputs["cameras"])
    seen = set()
    for frame in cameras["frames"]:
        if frame["T"] in seen:
            raise ToolError(f"cameras の T が重なっています: {frame['T']}")
        seen.add(frame["T"])
    if inputs.get("lights") is not None:
        contracts.load("light-track", inputs["lights"])
    blender = _blender(parameters, context, "render.show")
    step = Path(context.step_dir)
    settings_path = step / "show_settings.json"
    atomic_json(settings_path, settings)
    frames_dir = step / "frames"                         # 実行ごとに新しいフォルダ（前の描画のコマは残らない）
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--cameras", inputs["cameras"], "--frames-dir", str(frames_dir),
            "--report", outputs["report"]]
    if inputs.get("lights") is not None:
        args += ["--lights", inputs["lights"]]
    if inputs.get("hooks") is not None:
        args += ["--hooks", inputs["hooks"]]
    record = launcher.run_blender(blender, SHOW_SCRIPT, None, args, log=step / "blender.log", timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    manifest = show_manifest(report, frames_dir)
    atomic_json(outputs["frames"], manifest)
    observation = {"kind": "show_render", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "planned": len(report.get("planned") or []),
                   "rendered": len(report.get("rendered") or []), "missing": len(manifest["missing"]),
                   "motion_blur_frames": (report.get("motion_blur") or {}).get("frames"),
                   "held_camera_keys": len((report.get("camera") or {}).get("held") or []),
                   "hooks": {"path": str(inputs["hooks"]), "sha256": sha256(inputs["hooks"])} if inputs.get("hooks") is not None else None}
    return StepResult(observations=[observation])
