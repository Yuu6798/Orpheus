"""腕の手直しと検査の工程（docs/model3d.md）。リポジトリ外の 3 作目の VRM のダンスで、腕を直した台本を工程にしたもの。どれも Blender の
中で動かす。Blender は外部の実行ファイルなので再利用しない（毎回実行）。

- motion.bake_roll: 手の回転の一部を写す骨（model.twist_bones の roll）の回転を、拘束に頼らず正確な値でキーに焼く（拘束は止める）。
"""
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

BAKE_ROLL_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "bake_roll.py"
COMMON = {"blender", "addons", "blender_extensions", "timeout", "armature"}


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
