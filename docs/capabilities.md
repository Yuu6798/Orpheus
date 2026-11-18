# AI向け能力カタログ

`orpheus` は、Orpheus にある道具を**実行せずに**一覧・検索する入口です。制作依頼から既存の道具を逆引きし、READMEや各資料を端から読む前に候補を絞ります。

```sh
uv run orpheus search "目の破綻を確認して直したい"
uv run orpheus show workflow.eye_rebuild
uv run orpheus capabilities --domain model3d --available-only
uv run orpheus workflows --query "朗読動画"
uv run orpheus validate
```

出力は成功・失敗ともJSONです。成功は `{"ok": true, ...}`、失敗は次の形です。

```json
{
  "ok": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "能力または workflow が見つかりません: ...",
    "details": {},
    "retryable": true
  }
}
```

## 何を載せるか

- `mv` は既存の `argparse` 定義からコマンド・必須引数・選択肢・既定値を実行時に収集する。
- `model3d` が導入されていれば、`model3d ops` と同じ登録済み操作を実行時に読み取る。
- よく使う目的は `workflow.*` として、道具の順番、人が決める箇所、必要入力をまとめる。
- optional extra が無いときもworkflowが参照する重要操作は索引に残し、`availability.available: false` と理由を返す。

`availability` が確かめるのは、現在のPython環境でコマンドとoptional extraを発見できるかまでです。Blender・FFmpeg・Nodeなど外部実行ファイルの存在や、素材・設定の成立は自動で確定しません。`requires.tools` が埋まっている能力ではその値を確認し、空の能力も対応する資料で依存を確認します。

`search` は目的と、ID・説明・別名・タグ・入出力・workflowの工程を照合します。検索順位は候補を絞るためのもので、実行可否や作品の採用を保証しません。上位結果の `availability`、`maturity`、`parameters` を確認してから使います。

## `parameters.status`

| 値 | 意味 |
|---|---|
| `schema` | 機械可読な設定スキーマがある |
| `cli` | 既存CLIのparserから引数仕様を取得した |
| `docs` | 資料に値と制約があり、機械可読スキーマは無い |
| `undocumented` | 操作は見つかったが、引数の機械可読な説明をカタログへまだ持ち帰っていない |

`undocumented` は「引数を推測してよい」という意味ではありません。`entrypoint.docs`、`model3d ops`、操作のテスト・実装を確認し、作品固有の値は非公開のrecipeやpresetに置きます。

## workflowの位置づけ

workflowは自動実行器ではなく、目的から既存の道具へ到達するための目録です。

例として `workflow.eye_rebuild` は、次を順に示します。

1. `model3d.check.eyes` で現在の目を調べる。
2. `model3d.model.eye_rig` で目の骨と瞳ウェイトを整える。
3. `model3d.model.eye_keys` でまばたきと形状キーを整える。
4. `model3d.vrm.look_at` でVRMの視線を設定する。
5. `model3d.render.eye_sheet` で姿勢と視点を変えて確認する。

各工程の値と見た目の採否は、人が対象モデルに合わせて決めます。`technical_pass`や`status: succeeded`から、作品の`accepted`を自動で決めません。

## 追加・更新

- 目的別workflowは `src/mv_pipeline/capability_workflows.py` に追加する。
- `mv`の用途語・資料・成熟度は `src/mv_pipeline/capability_catalog.py` の `MV_OVERRIDES` で補う。引数仕様は既存parserを正典とする。
- `model3d`の操作は登録表から自動収集される。検索語を補う必要がある重要操作だけ `MODEL3D_OVERRIDES` に別名と説明を置く。
- 操作は `model3d.ops.register(..., capability={...})` で任意の検索語、資料、必要ツール、parameter schemaを公開できる。仕様が無い間は `undocumented` のままにする。
- `uv run orpheus validate` と `tests/test_capability_catalog.py` で、IDの重複、workflowの参照切れ、必須欄を確認する。

この入口はネットワーク、外部AI、Blender、FFmpeg、描画処理を呼びません。現在のPython環境から登録情報を読むだけです。
