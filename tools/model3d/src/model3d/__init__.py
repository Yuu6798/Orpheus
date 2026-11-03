"""model3d — 自作の 3D モデリングツール（Orpheus の外部ツール）。

設定画または既存モデルから、部位・骨の対応（SemanticMap）、加工レシピ（RecipeGraph）の実行、形状と動作の検査、
書き出しまでを**レシピと記録**で進める共通処理。Blender・生成器・リギングは別プロセスに置き、入力ファイル・設定・
結果ファイルで受け渡す。Orpheus（mv_pipeline）とは**出力ファイルと制作記録（RunRecord）**だけで接続し、
このパッケージは mv_pipeline を import しない（逆も同じ）。

- 決定論: 同じ入力・設定・コード版なら同じ出力（LLM もネットワークも使わない）。
- 採点しない: 検査は構造と数値の検査（`pass`）で、見た目の採否は人の判定（RunRecord の `adopted`）。
- 固有の値を持たない: 部品番号・特定サービスの骨名・絶対パスは preset と recipe に置き、共通処理は役割名を参照する。
"""
import hashlib
from pathlib import Path

VERSION = "0.1.0"
PACKAGE_DIR = Path(__file__).resolve().parent

SCHEMAS = {
    "mesh": "model3d-mesh/0.1",
    "asset-manifest": "model3d-asset-manifest/0.1",
    "semantic-map": "model3d-semantic-map/0.1",
    "correspondence": "model3d-correspondence/0.1",
    "recipe": "model3d-recipe/0.1",
    "observation": "rea-3d-observation/0.1",
    "run": "model3d-run/0.1",
    "rig": "model3d-rig/0.1",
    "preset": "model3d-preset/0.1",
    "handoff": "model3d-handoff/0.1",
}


def fingerprint():
    """パッケージのコード（.py と .schema.json）の sha256。キャッシュ key に入れ、コードが変わったら再実行させる。"""
    digest = hashlib.sha256()
    for path in sorted(PACKAGE_DIR.rglob("*")):
        if path.suffix in {".py", ".json"} and path.is_file():
            digest.update(path.relative_to(PACKAGE_DIR).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()
