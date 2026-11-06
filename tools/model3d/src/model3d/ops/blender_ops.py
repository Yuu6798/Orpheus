"""Blender を工程として呼ぶ（未検証: この環境に Blender が無い）。"""
from ..adapters import process
from ..blender_adapter import launcher
from ..io import ToolError, resolve_path
from . import StepResult, register


@register("blender.run_script", cacheable=False,
          summary="blender --background --factory-startup [blend] --python script -- args… を呼ぶ。{入力名}・{出力名}・{work} を置き換える。"
                  "利用者の設定・アドオンが要るときだけ factory_startup: false。再利用しない（毎回実行）")
def blender_run_script(inputs, parameters, outputs, context):
    blender = parameters.get("blender") or context.tools.get("blender")
    if not blender:
        raise ToolError("blender.run_script には Blender の実行ファイル（パラメーター blender か --tool blender=PATH）が要ります")
    script = parameters.get("script")
    if not script:
        raise ToolError("blender.run_script にはパラメーター script が要ります")
    script = resolve_path(context.recipe_dir, script)
    blend = inputs.get("blend") or (resolve_path(context.recipe_dir, parameters["blend"]) if parameters.get("blend") else None)
    placeholders = {**{k: str(v) for k, v in inputs.items()}, **{k: str(v) for k, v in outputs.items()},
                    "work": str(context.step_dir)}
    args = process.substitute([str(a) for a in parameters.get("args", [])], placeholders)
    record = launcher.run_blender(blender, script, blend, args, log=context.step_dir / "blender.log",
                                  timeout=parameters.get("timeout"), cwd=parameters.get("cwd"),
                                  background=parameters.get("background", True),
                                  factory_startup=parameters.get("factory_startup", True))
    return StepResult(observations=[{"kind": "blender", **record}])
