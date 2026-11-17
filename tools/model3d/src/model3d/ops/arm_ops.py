"""腕の手直しと検査の工程（docs/model3d.md）。リポジトリ外の 3 作目の VRM のダンスで、腕を直した台本を工程にしたもの。どれも Blender の
中で動かす。Blender は外部の実行ファイルなので再利用しない（毎回実行）。

- motion.bake_roll: 手の回転の一部を写す骨（model.twist_bones の roll）の回転を、拘束に頼らず正確な値でキーに焼く（拘束は止める）。
- check.arm_pinch: 腕の関節のまわりのメッシュが、ねじれで絞ったように細くなっていないかをコマごとに測る（太さの比）。
"""
import math
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

BAKE_ROLL_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "bake_roll.py"
ARM_PINCH_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "arm_pinch.py"
COMMON = {"blender", "addons", "blender_extensions", "timeout", "armature"}


def _name(value, where):
    if not isinstance(value, str) or not value:
        raise ToolError(f"{where} は名前（空でない文字列）にしてください: {value!r}")
    return value


def _real(value, where, minimum=None, exclusive=False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{where} は有限の数にしてください: {value!r}")
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        raise ToolError(f"{where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    if maximum is not None and value > maximum:
        raise ToolError(f"{where} は {maximum} 以下にしてください: {value!r}")
    return value


def _frames(parameters):
    span = parameters.get("frames")
    if span is None:
        return None
    if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in span)
            or span[0] > span[1]):
        raise ToolError(f"frames は [最初のコマ, 最後のコマ]（整数、最初 ≤ 最後）にしてください: {span!r}")
    return span


def _refuse_unknown(values, allowed, where):
    unknown = set(values) - set(allowed)
    if unknown:
        raise ToolError(f"{where} の知らない設定: {sorted(unknown)}（使えるのは {sorted(allowed)}）")


def _names(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(n, str) and n for n in value) or len(set(value)) != len(value):
        raise ToolError(f"{where} は名前（空でない・重ならない文字列）の並びにしてください: {value!r}")
    return value


def _armature(parameters):
    armature = parameters.get("armature")
    if armature is not None and (not isinstance(armature, str) or not armature):
        raise ToolError(f"armature は名前（空でない文字列）にしてください: {armature!r}")
    return armature


def _outputs(outputs, names, operation):
    missing = [name for name in names if name not in outputs]
    if missing:
        raise ToolError(f"{operation} には outputs の {', '.join(missing)} が要ります")


def _run(script, inputs, parameters, outputs, context, settings, operation, extra=()):
    """台本を settings で動かす（settings は工程のフォルダの JSON に書いて渡す）。戻り: 起動の記録。"""
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    blender = _blender(parameters, context, operation)
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path = step_dir / f"{operation.split('.')[-1]}_settings.json"
    atomic_json(settings_path, settings)
    return launcher.run_blender(blender, script, None, ["--blend", inputs["blend"], "--settings", str(settings_path), *extra],
                                log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)


@register("motion.bake_roll", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="手の回転の一部を写す骨（model.twist_bones の roll。Copy Rotation の Y だけ・Add・ローカルどうし）の回転を、拘束に頼らず"
                  "正確な値でコマごとのキーに焼く（Blender の中で）: 写す骨の回転の、軸 Y のまわりのねじれ（VRM の roll と同じ決め方）に"
                  "影響を掛けて打ち、拘束は止める。bones を省くと、止めていない roll の形の Copy Rotation がある骨を全部。止める前の拘束との"
                  "差を報告に出す。踊りの .blend だけに使う（止めた拘束は VRM に書き出されない）。元のファイルは変えない")
def bake_roll(inputs, parameters, outputs, context):
    require(inputs, ("blend",), "motion.bake_roll")
    _refuse_unknown(parameters, COMMON | {"bones"}, "motion.bake_roll")
    _outputs(outputs, ("blend", "report"), "motion.bake_roll")
    settings = {"bones": _names(parameters["bones"], "bones") if parameters.get("bones") is not None else [],
                "armature": _armature(parameters)}
    record = _run(BAKE_ROLL_SCRIPT, inputs, parameters, outputs, context, settings, "motion.bake_roll",
                  ["--out-blend", outputs["blend"], "--report", outputs["report"]])
    report = read_json(outputs["report"])
    observation = {"kind": "bake_roll", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "bones": {name: {key: value.get(key) for key in ("from", "largest_twist_deg", "constraint_was_off_by_deg")}
                             for name, value in (report.get("bones") or {}).items()}}
    return StepResult(observations=[observation])


RING_KEYS = ("name", "at", "axis", "offset", "band", "radius", "groups")


def pinch_settings(parameters):
    """check.arm_pinch のパラメーターを確かめ、台本の settings にする。"""
    _refuse_unknown(parameters, COMMON | {"body", "rings", "frames", "min_weight", "thin", "min_ratio", "strict", "report_only"},
                    "check.arm_pinch")
    rings = parameters.get("rings")
    if not isinstance(rings, list) or not rings:
        raise ToolError("rings は [{\"name\", \"at\", \"axis\", \"offset\", \"band\", \"radius\", \"groups\"}, …] にしてください: "
                        f"{rings!r}")
    checked = []
    for k, ring in enumerate(rings):
        if not isinstance(ring, dict):
            raise ToolError(f"rings[{k}] は {{\"name\", \"at\", \"axis\", …}} にしてください: {ring!r}")
        _refuse_unknown(ring, RING_KEYS, f"rings[{k}]")
        checked.append({"name": _name(ring.get("name"), f"rings[{k}].name"), "at": _name(ring.get("at"), f"rings[{k}].at"),
                        "axis": _name(ring.get("axis"), f"rings[{k}].axis"),
                        "offset": _real(ring.get("offset", 0.0), f"rings[{k}].offset"),
                        "band": _real(ring.get("band"), f"rings[{k}].band", 0.0, exclusive=True),
                        "radius": _real(ring.get("radius"), f"rings[{k}].radius", 0.0, exclusive=True),
                        "groups": _names(ring.get("groups"), f"rings[{k}].groups")})
    names = [ring["name"] for ring in checked]
    if len(set(names)) != len(names):
        raise ToolError(f"rings の name が重なっています: {names}")
    return {"body": _name(parameters.get("body"), "body"), "rings": checked, "frames": _frames(parameters),
            "armature": _armature(parameters),
            "min_weight": _real(parameters.get("min_weight", 0.9), "min_weight", 0.0, maximum=1.0)}


def pinch_summary(raw, thin=0.75, min_ratio=None):
    """check.arm_pinch の測った値（輪ごとのコマの太さの比）の要約と合否（min_ratio を渡したときだけ）。数の丸めは台本と同じ。"""
    import numpy as np
    frames = raw["frames"]
    rings, failures, unmeasured = {}, [], []
    for name, ring in raw["rings"].items():
        values = np.asarray(ring["ratio"], dtype=np.float64)
        worst = int(np.argmin(values))
        rings[name] = {"verts": ring["verts"], "rest_spread_mm": ring.get("rest_spread_mm"), "min": round(float(values.min()), 3),
                       "min_at_frame": int(frames[worst]), "p05": round(float(np.percentile(values, 5)), 3),
                       "median": round(float(np.median(values)), 3), "frames_thin": int((values < thin).sum()),
                       "frames_below_half": int((values < 0.5).sum())}
        if ring.get("rest_spread_mm") is None:
            unmeasured.append(name)
        if min_ratio is not None and values.min() < min_ratio:
            failures.append(f"{name}: いちばん細いコマ {frames[worst]} で初期姿勢の {values.min():.3f} 倍（しきい値 {min_ratio:g}。"
                            f"下回ったコマ {int((values < min_ratio).sum())}）")
    report = {"check": "arm_pinch", "frames": [frames[0], frames[-1]] if frames else [], "frame_count": len(frames), "thin_below": thin,
              "rings": rings, "unmeasured": unmeasured, "thresholds": {} if min_ratio is None else {"min_ratio": min_ratio}}
    if min_ratio is None:
        report.update({"pass": None, "reason": "しきい値が無いので測っただけ（report_only で測るだけにできる）", "failures": []})
    else:
        report.update({"pass": not failures, "failures": failures})
    return report


@register("check.arm_pinch", inputs=("blend",), outputs=("report",), cacheable=False,
          summary="腕の関節のまわり（肩の付け根・肘・手首など）のメッシュが、ねじれで絞ったように細くなっていないかをコマごとに測る（Blender の"
                  "中で。.blend は変えない）: rings の輪ごとに、初期姿勢で骨に沿った帯の頂点を選び、コマごとの太さ（骨の軸に垂直な、重心からの"
                  "距離の平均）を初期姿勢で割った比。輪ごとの最小・5% 点・中央値・thin（既定 0.75）より細いコマの数・半分より細いコマの数。"
                  "体の Armature 以外のモディファイアは止めて骨の変形だけを見る。min_ratio を渡したときだけ合否を出す。好みは判定しない")
def check_arm_pinch(inputs, parameters, outputs, context):
    from .check_ops import _finish
    require(inputs, ("blend",), "check.arm_pinch")
    settings = pinch_settings(parameters)
    thin = _real(parameters.get("thin", 0.75), "thin", 0.0, exclusive=True, maximum=1.0)
    limit = None if parameters.get("min_ratio") is None else _real(parameters["min_ratio"], "min_ratio", 0.0, exclusive=True, maximum=1.0)
    step_dir = Path(context.step_dir)
    raw_path = step_dir / "arm_pinch_raw.json"
    record = _run(ARM_PINCH_SCRIPT, inputs, parameters, outputs, context, settings, "check.arm_pinch", ["--report", str(raw_path)])
    raw = read_json(raw_path)
    report = pinch_summary(raw, thin, limit)
    report.update({"blend": str(inputs["blend"]), "blender_version": record["blender_version"], "armature": raw.get("armature"),
                   "body": raw.get("body"), "modifiers_off": raw.get("modifiers_off"), "raw": str(raw_path)})
    return _finish(report, outputs, parameters, "腕の関節の細りの検査")
