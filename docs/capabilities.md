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

カタログのコードは `src/orpheus_catalog/` に置く。これは `mv_pipeline` と `model3d` のどちらにも属さない読み取り専用の統合層で、両方の登録情報を参照する。制作実行層どうしは互いにimportせず、動画・3Dの接続は従来どおりファイルとRunRecordで行う。

## `availability` とコンポーネント状態

`availability` が確かめるのは、現在のPython環境でコマンドとoptional extraを発見できるかまでです。Blender・FFmpeg・Nodeなど外部実行ファイルの存在や、素材・設定の成立は自動で確定しません。`requires.tools` と対応する資料を確認します。

コンポーネントには次の `state` があります。

| 値 | 意味 |
|---|---|
| `available` | 登録表を読めた |
| `missing` | optional component が導入されていない |
| `broken` | 導入済みの可能性があるが、import・登録表生成・parser生成に失敗した |

`missing` はカタログ全体の破損ではありません。`broken` は利用不能を隠さず、`orpheus validate`を不合格にします。登録表を読めたのにworkflowが参照する能力が存在しない場合も不合格です。

## 通信と外部プロセス

`requires.network` と `requires.external_process` は、**カタログを読むときではなく、その能力を実行したとき**の副作用を表します。

| `mode` | 意味 |
|---|---|
| `never` | その能力の実装経路では行わない |
| `opt-in` | 明示したフラグや設定のときだけ行う。既定は無効 |
| `always` | 通常の実行で行う |
| `recipe-defined` | adapter・renderer・recipeなど、ユーザーが渡した設定と外部実装次第 |

例:

- `mv inspect check` のネットワークは `opt-in`。`--vlm`と`--allow-send`を明示し、設定が送信型の場合だけ外部送信できる。
- `mv run-adapter`、`mv fx legacy`、`model3d external.run` のネットワークは `recipe-defined`。呼び出す外部プログラムを確認する。
- `model3d check.eyes` はNumPyとPillowで動き、Blenderを起動しない。
- `model3d model.eye_rig`、`model.eye_keys`、`vrm.look_at`、`render.eye_sheet`はBlenderを起動する。

`orpheus capabilities`、`search`、`show`、`workflows`、`validate`自身はネットワーク送信、外部AI、Blender、FFmpeg、描画処理を起動しません。現在のPython環境からparserと登録情報を読むだけです。

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

1. `model3d.check.eyes` でBlenderを起動せず現在の目を調べる。
2. `model3d.model.eye_rig` で目の骨と瞳ウェイトを整える。
3. `model3d.model.eye_keys` でまばたきと形状キーを整える。
4. `model3d.vrm.look_at` でVRMの視線を設定する。
5. `model3d.render.eye_sheet` で姿勢と視点を変えて確認する。

`workflow.delivery`は、工程範囲を次のように分けます。

1. `mv deliver join`: 区間の結合と任意の音声付与。
2. `mv deliver review-copy`: 必要な場合だけ容量を抑えた確認版を作る。
3. `mv deliver split`: 必要な場合だけ分割片、結合用bat、`SHA256SUMS`を作る。

各工程の値と見た目の採否は、人が対象作品に合わせて決めます。`technical_pass`や`status: succeeded`から、作品の`accepted`を自動で決めません。

## 追加・更新

- 目的別workflowは `src/orpheus_catalog/capability_workflows.py` に追加する。
- `mv`の用途語・資料・成熟度・実行効果は `src/orpheus_catalog/capability_catalog.py` の `MV_OVERRIDES`と`MV_RUNTIME`で補う。引数仕様は既存parserを正典とする。
- `model3d`の操作は登録表から自動収集される。検索語を補う必要がある重要操作だけ `MODEL3D_OVERRIDES` に別名と説明を置く。
- 操作は `model3d.ops.register(..., capability={...})` で任意の検索語、資料、必要ツール、parameter schema、通信・外部プロセスのmodeを公開できる。仕様が無い間は `undocumented` のままにする。
- `uv run orpheus validate` と能力カタログのテストで、IDの重複、workflowの参照切れ、コンポーネント破損、実行効果のmodeを確認する。
