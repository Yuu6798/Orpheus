"""Blender を工程として呼ぶ。起動は Windows の Blender 4.5.15 で確かめた（2026-11-06）。recipe の成功・失敗は実素材と Blender 5.2.4 で確かめた（2026-11-27）。
アドオンを有効にした起動は、Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で確かめた（2026-11-16）。"""
from ..adapters import process
from ..blender_adapter import launcher
from ..io import ToolError, resolve_path
from . import StepResult, register


def blender_addons(parameters, context, default=()):
    """工程のアドオンの指定。addons（モジュール名の並び。省くと default）と、拡張機能のフォルダ（パラメーター blender_extensions。
    recipe からの相対パスも可。無ければ `model3d run --tool blender_extensions=DIR` の絶対パス）。(addons, extensions) を返す。"""
    addons = launcher.check_addons(parameters.get("addons", default))
    if "blender_extensions" in parameters:
        extensions = parameters["blender_extensions"]
        if not isinstance(extensions, str) or not extensions:
            raise ToolError(f"blender_extensions は拡張機能のフォルダのパス（文字列）にしてください: {extensions!r}")
        recipe_dir = getattr(context, "recipe_dir", None)
        return addons, launcher.check_extensions(resolve_path(recipe_dir, extensions) if recipe_dir is not None else extensions)
    extensions = (getattr(context, "tools", None) or {}).get("blender_extensions")
    # --tool の値は、--tool blender=PATH と同じく存在する絶対パスに限る（recipe のフォルダからは読まない）
    return addons, launcher.check_extensions(extensions) if extensions is not None else None


@register("blender.run_script", cacheable=False,
          summary="blender --background --factory-startup [blend] --python script -- args… を呼ぶ。{入力名}・{出力名}・{work} を置き換える。"
                  "addons（モジュール名の並び）を挙げると、.blend を読む前にそのアドオンだけを有効にする（拡張機能のフォルダは "
                  "blender_extensions か --tool blender_extensions=DIR）。利用者の設定・アドオンが要るときだけ factory_startup: false。"
                  "再利用しない（毎回実行）")
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
    addons, extensions = blender_addons(parameters, context)
    record = launcher.run_blender(blender, script, blend, args, log=context.step_dir / "blender.log",
                                  timeout=parameters.get("timeout"), cwd=parameters.get("cwd"),
                                  background=parameters.get("background", True),
                                  factory_startup=parameters.get("factory_startup", True),
                                  addons=addons, extensions=extensions)
    return StepResult(observations=[{"kind": "blender", **record}])
