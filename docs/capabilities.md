# AI向け能力カタログ

`orpheus` は、Orpheus にある道具を**実行せずに**一覧・検索する入口です。制作依頼から既存の道具を逆引きし、READMEや各資料を端から読む前に候補を絞ります。

```sh
uv run orpheus search "目の破綻を確認して直したい" --limit 3 --brief
uv run orpheus search "目の破綻を確認して直したい"
uv run orpheus show workflow.eye_rebuild
uv run orpheus capabilities --domain model3d --available-only
uv run orpheus workflows --query "朗読動画"
uv run orpheus capabilities --parameter-status undocumented
uv run orpheus workflows --machine-readable-only
uv run orpheus schema catalog
uv run orpheus validate
```

出力は成功・失敗ともJSONです。成功は `{"ok": true, ...}`、失敗は次の形です。

`search`と`workflows`に`--brief`を付けると、結果ごとに`id`・`kind`・`maturity`・`score`（検索のときだけ）・`summary`・`docs`・使えるか（`available`。使えなければ`reason`と`missing`）・workflowの`starters`のコマンドだけを、字下げなしのJSONで出します（`orpheus schema brief`。`--limit 3 --brief`で1〜2 KBほど。既定の10件の全項目は70 KBほど）。引数・工程・成熟度の根拠など詳しくは`uv run orpheus show ID`で読みます。

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
- workflow の `asset_expectations` は必要な資産の種類と出所、`starters` は既存の合成例のコマンド・期待出力・限界を示す。
- optional extra が無いときもworkflowが参照する重要操作は索引に残し、`availability.available: false` と理由を返す。
- `orpheus schema catalog|search|response` で、消費側がフィールド位置を推測せずに使える公開JSON Schemaを返す。

カタログのコードは `src/orpheus_catalog/` に置く。これは `mv_pipeline` と `model3d` のどちらにも属さない読み取り専用の統合層で、両方の登録情報を参照する。制作実行層どうしは互いにimportせず、動画・3Dの接続は従来どおりファイルとRunRecordで行う。

## `maturity` と確認の根拠

カタログ・検索・検証結果の公開 schema は `0.4`。`maturity` に `verified`（実制作検証済み）を追加した。
`stable`（本体・安定契約）、`experimental`（残る確認事項あり）、`external`（外部実装への接続）も引き続き使う。
意味・昇格条件・操作ごとの記録は [機能の成熟度](maturity.md) を参照。

```sh
uv run orpheus capabilities --maturity verified
uv run orpheus show mv.deliver.join
uv run orpheus workflows --maturity experimental
```

見直した操作には `maturity_assessment` を付ける。`reviewed_on`・`review_basis`・`reason`・`scope`・`evidence`・
`limitations`・`promotion_criteria`・`next_review` から、何を根拠にどこまで確認したか、残る確認は何かを読む。
これは既存の実制作照合記録を監査した結果で、今回非公開の素材を再実行したという記録ではない。
同じグループの兄弟操作や workflow 全体へは自動で昇格を広げない。

`maturity` は `availability`・`parameters.status`・`technical_pass`・人の作品採用とは独立している。
実制作検証済みの操作も、必要な環境や素材が無ければ今は実行できない。設定形式が `contracts/` の外であることだけを理由に、
実素材の確認実績を消さない。

## `availability` とコンポーネント状態

`availability` が確かめるのは、現在のPython環境でコマンドとoptional extraを発見できるかまでです。Blender・FFmpeg・Nodeなど外部実行ファイルの存在や、素材・設定の成立は自動で確定しません。

`requires.tools` は互換用の名前一覧です。実際の解決方法は `requires.tool_requirements` を読みます。MV側のFFmpegは `ORPHEUS_FFMPEG → PATH → imageio-ffmpeg同梱版`、ffprobeは `ORPHEUS_FFPROBE → PATH → Remotion同梱版` の順で解決するため、PATHに無くても直ちに利用不能ではありません。Nodeは `ORPHEUS_NODE` またはPATH、Blenderや外部adapterは設定で実行ファイルを渡します。`model3d render.assemble` のFFmpegは別の実装で、recipeの`ffmpeg`パラメーター → `model3d run --tool ffmpeg=PATH` → imageio-ffmpeg同梱版 → PATHの順です。`required_on_path`は文字どおりPATH上の実行ファイルが必須かを表し、環境変数・recipe・設定で明示できる場合は`false`です。

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
| `environment-dependent` | キャッシュ・環境変数・初回取得の状態で変わる。`note`とtool解決情報を確認する |
| `unknown` | 操作は登録済みだが、実行効果のmetadataがまだ公開されていない。`never`とは扱わず実装・資料を確認する |

例:

- `mv render`・`mv preview`・`mv resume`・`mv layer render` のネットワークは `environment-dependent`。`ORPHEUS_BROWSER`を指定せず管理ブラウザーも未取得の初回は、RemotionがChrome Headless Shellを取得し得る。
- `mv inspect check` のネットワークは `opt-in`。`--vlm`と`--allow-send`を明示し、設定が送信型の場合だけ外部送信できる。
- `mv run-adapter`、`mv fx legacy`、`model3d external.run` のネットワークは `recipe-defined`。呼び出す外部プログラムを確認する。
- `mv gate check` は設定に動画検査の材料がある場合だけFFmpegを起動するため、外部processは`opt-in`。
- `model3d check.eyes` はNumPyとPillowで動き、Blenderを起動しない。
- `model3d model.eye_rig`、`model.eye_keys`、`vrm.look_at`、`render.eye_sheet`、`blender.run_script`はBlenderを起動する。`render.assemble`はFFmpegを起動する。
- 実行効果のmetadataをまだ持たない`model3d`操作は`unknown`になる。道具が登録されているだけで`never`とは推定しない。

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

workflowは自動実行器ではなく、目的から既存の道具へ到達するための目録です。各workflowには次の補助情報があります。

- `asset_expectations`: 必須・任意の資産、MIME相当の種類、想定する出所、用途。
- `starters`: 既存の合成例を実行するargv、期待する出力、評価していない範囲。空なら汎用スターターはまだ無い。
- `parameter_readiness`: 必須工程と全工程について、`schema / cli / docs / undocumented`の件数と、機械可読な引数仕様が揃っているか。

`--parameter-status undocumented`で未整備の道具・workflowを抽出でき、`--machine-readable-only`で必須工程の引数がすべて`schema`または`cli`のworkflowだけに絞れます。これは`undocumented`を推測で埋めず、実装・資料を確認すべき場所を明示するためのものです。

例えば `workflow.lyric_mv` の `starter.synthetic_lyric_mv` は、次の既存例へ直接案内します。

```sh
uv sync --locked --extra analysis
pnpm install --frozen-lockfile
uv run --no-sync python examples/workflow/run.py PROJECT_DIR
```

この例は合成音・架空歌詞・幾何学画像を生成し、描画・verify・workflow checkまで通します。実曲の歌唱同期や作品採用を評価する例ではありません。

`workflow.eye_rebuild` は、次を順に示します。

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

`workflow.shorts_from_timeline`は、本編のfx timelineから縦のShortsを作る順を示します（手順と罠は[fx](fx.md)の「本編の timeline から縦の Shorts を作る」）。

1. `mv fx timeline plan`: 縦用のspecに差し替えたtimelineのショット・つなぎ・秒を確かめる（任意）。
2. `mv fx timeline render`: 区間だけを本編の秒で縦に描く（`--start`・`--duration`・`--hooks`）。
3. `mv overlay`: 歌詞と終わりのカードの透過連番を重ねる（任意。`first_frame`は区間の頭のコマ番号）。
4. `mv deliver join`: 同じ区間の音を付ける（`--audio-start`は区間の頭）。そのあとの確認版と分割は`workflow.delivery`と同じです。

`workflow.one_image`は、一枚の画像から短い動画を作る最短の順を示します（手順と罠は[一枚の画像から動画](recipes/one-image.md)。`mv.fx.quick`の資料もこのレシピ）。

1. `mv fx guides --brief`: 顔・線・山場の座標を読む（任意）。
2. `mv fx quick`: spec・timeline・hooks・plan.md を組み、`--preview`で下見の一覧、`--render`で本番と確認（`check.jpg`と要約: 寄りの最大・明滅・顔の切れ・元画像との差・止まったコマ）を出す。実行のたびに`log.md`へ記録を足す。
3. `mv inspect check --brief`: 検品（任意。`mv gate flashes`は quick の確認が同じ測り方で済ませる）。

## 追加・更新

- 目的別workflowは `src/orpheus_catalog/capability_workflows.py` に追加する。
- `mv`の用途語・資料・成熟度・実行効果は `src/orpheus_catalog/capability_catalog.py` の `MV_OVERRIDES`と`MV_RUNTIME`で補う。まとまりの中の1つの命令だけに足す用途語と資料（レシピなど）は`MV_COMMAND_OVERRIDES`に置く。引数仕様は既存parserを正典とする。
- よく頼まれる目的の最短の手順は `docs/recipes/` に置き（4,000字程度まで）、対応するworkflowと命令の`docs`から指す。`AGENTS.md`の最初の項目が、docsの全体より先にレシピを読むよう案内する。
- 実制作に基づく成熟度の見直しは `src/orpheus_catalog/maturity_assessments.json` に完全な能力IDで記録し、`docs/maturity.md` の一覧も更新する。グループの既定値より個別の見直しを優先する。実装の挙動が変わったときは根拠の適用範囲も見直す。
- `model3d`の操作は登録表から自動収集される。検索語を補う必要がある重要操作だけ `MODEL3D_OVERRIDES` に別名と説明を置く。
- 操作は `model3d.ops.register(..., capability={...})` で任意の検索語、資料、必要ツール、parameter schema、通信・外部プロセスのmodeを公開できる。仕様が無い間は `undocumented` のままにする。
- 公開schemaは `src/orpheus_catalog/schemas/` に置き、`uv run orpheus schema NAME`で返す（`catalog`・`search`・`response`・`brief`）。
- `uv run orpheus validate` と能力カタログのテストで、JSON Schema、IDの重複、workflowの参照切れ、スターターのargv、tool解決情報、コンポーネント破損、実行効果のmodeを確認する。
