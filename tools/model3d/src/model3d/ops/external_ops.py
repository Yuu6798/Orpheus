"""別プロセスの道具（生成器・リギングなど）を工程として呼ぶ。入力と出力の名前が argv の {名前} になる。"""
from ..adapters import process
from ..io import ToolError, read_json, resolve_path
from . import StepResult, register


@register("external.run", cacheable=False,
          summary="設定 JSON の argv で別プロセスを呼ぶ。{入力名}・{出力名}・{work} を置き換える。版・ライセンス・重みを記録する。再利用しない（毎回実行）")
def external_run(inputs, parameters, outputs, context):
    config = parameters.get("config")
    if isinstance(config, str):
        config = read_json(resolve_path(context.recipe_dir, config))
    if not isinstance(config, dict):
        raise ToolError("external.run にはパラメーター config（設定ファイルのパスか設定そのもの）が要ります")
    placeholders = {**{k: str(v) for k, v in inputs.items()}, **{k: str(v) for k, v in outputs.items()},
                    "work": str(context.step_dir), **{str(k): str(v) for k, v in parameters.get("placeholders", {}).items()}}
    record = process.run(config, placeholders, log=context.step_dir / "external.log", cwd=parameters.get("cwd"),
                         timeout=parameters.get("timeout"))
    return StepResult(observations=[{"kind": "external", **record}])
