# model3d — 自作の 3D モデリングツール（Orpheus の外部ツール）

設定画または既存モデルから、部位・骨の対応（SemanticMap）、加工レシピ（RecipeGraph）の実行、形状と動作の検査、
書き出しまでを**レシピと記録（RunRecord）**で進める共通処理。Blender・生成器・リギングは別プロセスで呼び、
Orpheus（`mv dance` など）とは出力ファイルと記録だけで接続する。使い方と限界は `docs/model3d.md`（リポジトリの docs）。

```sh
uv sync --locked --extra analysis --extra fx --extra model3d
uv run model3d demo projects/model3d-demo
uv run model3d run projects/model3d-demo/recipe.json
```

- `src/model3d/`: core（mesh / collide / semantic / manifest / recipe / executor / checks）、`ops/`（登録済みの操作）、
  `adapters/`（別プロセス）、`blender_adapter/`（汎用 Blender 起動・観測）、`rea_bridge/`（REA の根拠と Observation3D）、
  `schemas/`（データ契約）。
- `dance_recipe.py`: 非公開の既存制作 worker を5工程の RecipeGraph に接続。`model3d dance-recipe --help` と
  [接続手順](../../docs/model3d-dance.md)を参照。完成済みモデルを再構築入力には使わず、投影の目視確認を `needs_review` に残す。
- `presets/`: 合成素材のデモ用の preset だけ。実キャラの preset は Git に入れない。
- `tests/`: `uv run pytest`（ルートの testpaths に含まれる。extra が無いと skip される）。
