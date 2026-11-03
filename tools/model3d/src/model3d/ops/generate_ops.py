"""設定画から初期モデルを作る操作。生成器は別プロセス（backends/）で、この操作は前処理・呼び出し・検査・記録だけを行う。"""
import shutil

from .. import generate
from ..adapters import process
from ..io import ToolError, atomic_json, read_json, resolve_path
from ..mesh import Mesh
from . import StepResult, number, register, require


def _config(parameters, context):
    config = parameters.get("backend")
    if isinstance(config, str):
        config = read_json(resolve_path(context.recipe_dir, config))
    if not isinstance(config, dict):
        raise ToolError("generate.initial_model にはパラメーター backend（生成器の設定ファイルのパスか設定そのもの）が要ります")
    process.check_config(config)
    outputs = config.get("outputs")
    if not isinstance(outputs, dict) or not outputs.get("obj"):
        raise ToolError("生成器の設定には outputs.obj（生成器が out_dir に書く OBJ のファイル名）が要ります")
    if config.get("kind", "single_view") not in ("single_view", "multi_view", "stand_in"):
        raise ToolError(f"生成器の kind は single_view / multi_view / stand_in です: {config.get('kind')!r}")
    return config


@register("generate.prepare_reference", inputs=("image",), outputs=("prepared", "metadata"),
          summary="設定画（RGBA）を前景の矩形で切り出し、正方形に置いて余白を足し、灰色に合成した RGB の PNG にする")
def prepare_reference(inputs, parameters, outputs, context):
    require(inputs, ("image",), "generate.prepare_reference")
    if "prepared" not in outputs:
        raise ToolError("outputs に prepared を宣言してください")
    metadata = generate.prepare_file(inputs["image"], outputs["prepared"], parameters.get("foreground_ratio", generate.DEFAULT_FOREGROUND_RATIO))
    if "metadata" in outputs:
        atomic_json(outputs["metadata"], metadata)
    return StepResult(observations=[{"kind": "prepare_reference", **metadata}])


@register("generate.initial_model", inputs=("image",), outputs=("mesh", "record", "prepared"), cacheable=False,
          summary="設定画 1 枚から初期モデルを作る: 前処理 → 別プロセスの生成器（backend の設定）→ 検査 → 記録。常に needs_review")
def initial_model(inputs, parameters, outputs, context):
    require(inputs, ("image",), "generate.initial_model")
    for name in ("mesh", "record"):
        if name not in outputs:
            raise ToolError(f"outputs に {name} を宣言してください")
    config = _config(parameters, context)
    ratio = parameters.get("foreground_ratio", generate.DEFAULT_FOREGROUND_RATIO)
    resolution = number(parameters, "resolution", 128, minimum=8, integer=True)
    threshold = number(parameters, "density_threshold", 25.0)
    seed = number(parameters, "seed", 20261103, integer=True)
    prepared_path = outputs.get("prepared") or context.step_dir / "prepared.png"
    preparation = generate.prepare_file(inputs["image"], prepared_path, ratio)
    out_dir = context.step_dir / "backend"
    out_dir.mkdir(parents=True, exist_ok=True)
    placeholders = {"prepared": str(prepared_path), "out_dir": str(out_dir), "resolution": str(resolution),
                    "threshold": repr(float(threshold)), "seed": str(seed), "work": str(context.step_dir)}
    record = process.run(config, placeholders, log=context.step_dir / "backend.log", cwd=parameters.get("cwd"),
                         timeout=parameters.get("timeout"))
    obj = out_dir / config["outputs"]["obj"]
    if not obj.is_file():
        raise ToolError(f"生成器が宣言した OBJ を書いていません: {obj}（ログ: {context.step_dir / 'backend.log'}）")
    mesh = Mesh.load(obj)
    mesh.name = str(parameters.get("name") or f"initial_{config['tool']}")
    mesh.units = parameters.get("units", "m")
    mesh.extra["x_scale_calibrated"] = False      # 生成器の正規化した空間のまま。実寸は preset の身長から別工程で合わせる
    mesh.validate()
    mesh.save(outputs["mesh"])
    checks = generate.inspect_mesh(mesh)
    produced = {"mesh": generate.fileref(outputs["mesh"]), "obj": generate.fileref(obj)}
    for key in ("glb", "result"):
        name = config["outputs"].get(key)
        if name and (out_dir / name).is_file():
            produced[key] = generate.fileref(out_dir / name)
    backend_result = read_json(out_dir / config["outputs"]["result"]) if "result" in produced else None
    backend = generate.backend_identity(config, record)
    backend["result"] = backend_result if isinstance(backend_result, dict) else None
    notes = []
    if backend["kind"] == "stand_in":
        notes.append("stand_in の生成器: 形は設定画の輪郭の箱で、生成ではない（接続の確認用）")
    record_data = generate.build_record(preparation, backend, {"foreground_ratio": float(ratio), "resolution": resolution,
                                                              "density_threshold": float(threshold), "seed": seed},
                                        produced, checks, single_view_only=backend["kind"] != "multi_view", notes=notes)
    atomic_json(outputs["record"], record_data)
    observation = {"kind": "initial_model", "backend": {k: backend[k] for k in ("tool", "version", "kind")}, **checks,
                   "seconds": record.get("seconds")}
    return StepResult(observations=[observation], unresolved=[generate.REVIEW_NOTE], review=True)


@register("generate.inspect_initial_model", inputs=("mesh",), outputs=("report",),
          summary="初期モデルの幾何と構造（頂点・面・有限・bounds・連結成分・閉じているか・巻き方・頂点色）を検査して報告する")
def inspect_initial_model(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "generate.inspect_initial_model")
    report = {"check": "initial_model", **generate.inspect_mesh(Mesh.load(inputs["mesh"]))}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    return StepResult(observations=[report])
