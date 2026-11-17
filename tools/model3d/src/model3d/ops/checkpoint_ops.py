"""キャラを作る段の確認地点で測る・直す工程（docs/model3d.md の「キャラを作る段の確認地点」）。キャラを作る道具（画像 → 候補 →
組み立て → VRM）から受け取った物を、選ぶ前・直す前に数で確かめ、受け取った VRM の作りの癖を直す（3 作目の VRM のダンスで、
リポジトリの外の台本でしていたもの）。測る工程は数と絵を出すだけで、良し悪しは決めない。

- vrm.color_factor: VRM の材質（MToon）の色の掛け率を 1 に戻し、影の色も同じ割合で戻す。
"""
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

COLOR_FACTOR_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "color_factor.py"
COLOR_FACTOR_PARAMETERS = {"materials", "blender", "addons", "blender_extensions", "timeout"}


def _names(value, where):
    if (not isinstance(value, list) or not value or not all(isinstance(name, str) and name for name in value)
            or len(set(value)) != len(value)):
        raise ToolError(f"{where} は名前（空でない・重ならない文字列）の並びにしてください: {value!r}")
    return value


def _refuse_unknown(parameters, allowed, operation):
    unknown = set(parameters) - set(allowed)
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(allowed)}）")


def _outputs(outputs, names, operation):
    missing = [name for name in names if name not in outputs]
    if missing:
        raise ToolError(f"{operation} には outputs の {', '.join(missing)} が要ります")


@register("vrm.color_factor", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="VRM の材質（MToon）の色の掛け率 baseColorFactor の RGB を 1 に戻し、影の色 shadeColorFactor に同じ割合を掛ける"
                  "（1 を超えない）。Blender の中で VRM Add-on の設定を書き換える。materials の材質だけで、無い材質・MToon でない材質は止める。"
                  "色の絵は変えない（テクスチャの色をそのまま描く描き方は変わらない）。報告に MToon の全部の材質の前と後の値。"
                  "元のファイルは変えない。Blender が要り、再利用しない（毎回実行）")
def color_factor(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    from .vrm_ops import VRM_ADDON
    require(inputs, ("blend",), "vrm.color_factor")
    _refuse_unknown(parameters, COLOR_FACTOR_PARAMETERS, "vrm.color_factor")
    _outputs(outputs, ("blend", "report"), "vrm.color_factor")
    settings = {"materials": _names(parameters.get("materials"), "materials")}
    blender = _blender(parameters, context, "vrm.color_factor")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    atomic_json(step_dir / "color_factor_settings.json", settings)
    record = launcher.run_blender(blender, COLOR_FACTOR_SCRIPT, None, ["--blend", inputs["blend"], "--settings",
                                                                       step_dir / "color_factor_settings.json", "--out-blend",
                                                                       outputs["blend"], "--report", outputs["report"]],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "color_factor", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "changed": report.get("changed"),
                   "materials": {name: {"before": (report.get("before") or {}).get(name), "after": (report.get("after") or {}).get(name)}
                                 for name in settings["materials"]}}
    return StepResult(observations=[observation])
