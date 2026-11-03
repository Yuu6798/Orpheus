# model3d（自作の 3D モデリングツール・Orpheus の外部ツール・実験的）

設定画または既存モデルから、**部位・骨の対応 → 加工レシピの実行 → 形状と動作の検査 → 書き出し**を、レシピと記録で進める道具。
Orpheus（`mv dance` など）とは**出力ファイルと制作記録（RunRecord）だけで接続**し、既存の 2D ダンス工程に 3D 固有のデータ契約は混ぜない
（統合位置は試作の後に決める）。コードは `tools/model3d/`、import 名は `model3d`。`mv_pipeline` とは互いに import しない。

- **決定論**: 同じ入力・設定・コード版なら同じ出力。LLM もネットワークも使わない。
- **採点しない**: 検査は構造と数値（`pass`）。見た目の採否は人の判定（`model3d accept`）で、RunRecord の `adopted` に別に残る。
- **固有の値を持たない**: 部品番号（`part_3`）や特定サービスの骨名（`mixamorig:Hips`）は preset で役割（`garment.sash.front`・`hips`）に
  対応付け、共通処理は役割だけを参照する。実キャラの preset・素材・動画・重み・鍵は Git に入れない。
- **推定を実測扱いしない**: 入力の sha256 が宣言と違えば実行しない。役割の対応表が無い・未確認、観測不足は `unresolved` に残して
  `needs_review` にし、成功扱いにしない。未実施の検査は合格ではない。
- **実験的**: データ契約（下記）は `tools/model3d/src/model3d/schemas/`（`contracts/` の外）。`contracts/` への昇格はユーザー承認が要る。
- 動作確認は**合成素材（`model3d demo`）と、JSCAD の原本出力との照合**まで。実キャラの資産では未確認。Blender・生成器・リギングの
  接続はこの環境に本体が無いため**未検証**（下記「未検証」）。

設計の元は 2026-11-03 の設計たたき台 v0.3（ユーザーのクラウド）。実装単位は `core`・`ops`・`adapters`・`blender_adapter`・`rea_bridge`・
`presets`・`validation`（`checks.py`）。初期版の完成は M3（30 秒の検査・観測接続・候補比較・再開）で、ここにあるのは M0〜M1 の土台
（資産一覧・SemanticMap・レシピ実行器・検査・記録）と、別メッシュへの適用（M2）に向けた役割ベースの部品。

## 準備

```sh
uv sync --locked --extra analysis --extra fx --extra model3d    # model3d extra を足す（uv sync は指定外の extra を外す）
uv run model3d --version
```

依存は numpy と jsonschema だけ（ルートと同じ版）。Blender（r6 の再現では 4.5.15 を固定）・生成器・リギングは別プロセスで、
`--tool blender=PATH` や設定 JSON の argv で渡す。

**開発者へ**: `tools/model3d/tests` のテストは `--extra model3d` で同期していない環境では `importorskip` で**黙って skip され、緑のまま終わる**。
model3d に触れたら必ず `--extra model3d` の環境で `uv run pytest` を回す（CI は extra あり）。

## 5 分で試す（合成素材）

```sh
uv run model3d demo projects/model3d-demo                     # 合成素材・preset・対応表・リグ・recipe.json
uv run model3d run projects/model3d-demo/recipe.json          # 実行して run を書く（projects/model3d-demo/model3d_runs/<run_id>/）
uv run model3d handoff projects/model3d-demo/model3d_runs/<run_id> push/mesh projects/model3d-demo/handoff.json
uv run model3d accept projects/model3d-demo/model3d_runs/<run_id> --decision accepted --by NAME
```

デモは、八角柱（左の大腿の代役）に中ほどが入り込んだ薄い帯（2 枚の板を継ぎ目で重ね、継ぎ目の頂点は二重）と、ねじった六角柱の袖、
Mixamo 風の骨名のリグ。レシピは **押し出し → 結合（継ぎ目 7 組）→ 貫通の測定（15 頂点が中）→ 補正（余裕 4 mm）→ 貫通の検査（0）
→ 構造の検査 → OBJ 書き出し** の 9 工程で、`status` は `succeeded`。素材はすべてプログラムで作った合成物で、実キャラの素材は使わない。
`projects/` は `.gitignore` 済み。

合成素材の検査が通ることは、実素材で同じ処理が成立することを保証しない。実素材では、まず対応表の確認（`semantic confirm`）と
入力のハッシュの宣言から始める。

## データ契約（`model3d validate KIND FILE`）

| 契約（`schema_version`） | 中身 |
|---|---|
| AssetManifest `model3d-asset-manifest/0.1` | 入力の参照と sha256・大きさ・種類、部品 ID、メッシュの頂点構成のハッシュ（`topology_sha256`）と形のハッシュ、単位、軸、変換行列、資産の版 |
| SemanticMap `model3d-semantic-map/0.1` | 部品 → 役割、骨 → 役割（親・レスト行列）、面領域 → 役割（頂点構成のハッシュに結び付ける）、根拠（preset / user / inferred）、確認状態（confirmed / unconfirmed / rejected）、未対応と必須の欠落 |
| CorrespondenceMap `model3d-correspondence/0.1` | 転送元と転送先の版と頂点構成、対応点（面と重心座標）、距離と曖昧さ、未対応の役割。空間的に近いだけの対応は作らない（形式だけ。転送の処理は未実装） |
| RecipeGraph `model3d-recipe/0.1` | 入力（パスと任意の sha256）、工程（操作・入力の参照・パラメーター・出力・依存・前提・座標系・適用する役割・根拠） |
| Observation3D `rea-3d-observation/0.1` | 観測対象、取得方法、座標系、事実（facts）、出自（evidence: observed / inferred / unknown / unavailable）、範囲（coverage）、限界と未解決。推定値は facts に混ぜない |
| RunRecord `model3d-run/0.1` | 入力・設定・環境（Python・numpy・コードの指紋・道具）、工程ごとの状態・key・生成物の sha256・観測・未解決・再利用元、候補、人の判定、手修正、警告 |
| mesh `model3d-mesh/0.1` | 頂点・面（多角形）・頂点ごとの uv・面の集合 parts・頂点の selection・単位・軸。OBJ と相互変換（`model3d mesh convert`） |
| rig `model3d-rig/0.1` | 骨（名前・親・head/tail・レスト行列）と頂点ウェイト（骨ごとの [頂点番号, 重み]） |
| preset `model3d-preset/0.1` | キャラごとの対応表と値（`tools/model3d/presets/README.md`） |
| handoff `model3d-handoff/0.1` | Orpheus へ渡す成果物 1 つの控え（run・レシピ・成果物の sha256） |

未知の鍵は拒否し、`x_` で始まる鍵はどの階層でも許す（記録には残る）。

## 役割の語彙

`tools/model3d/src/model3d/semantic.py` の `PART_ROLES`（`body.leg.upper.L`、`hair.front`、`garment.sash.front`、`collider.leg.upper.L` など）と
`BONE_ROLES`（`hips`、`spine`、`leg.upper.L`、`cuff.L`、`sash.1` など）。左右は `.L` / `.R`。語彙に無いものは `x.` で始めれば通る。
Mixamo 風の `mixamorig:Hips` は接頭辞を外して `Hips` とも照合する。

```sh
uv run model3d semantic build preset.json semantic_map.json --asset-id ASSET --parts part_1,part_2 --rig rig.json
uv run model3d semantic confirm semantic_map.json --kind parts --key part_1 --state confirmed --by NAME
```

未対応（`unmapped`）と必須の欠落（`required_missing`）は出力に残る。確認していない対応がある run には警告が付く。

## レシピと実行

```json
{
  "schema_version": "model3d-recipe/0.1", "name": "sash-fix", "preset": "preset.json", "semantic_map": "semantic_map.json",
  "inputs": {"sash": {"path": "inputs/sash.json", "sha256": "…"}, "leg": {"path": "inputs/leg.json"}},
  "steps": [
    {"id": "weld", "operation": "mesh.weld", "inputs": {"mesh": "$inputs.sash"}, "parameters": {"distance": 1e-6},
     "outputs": {"mesh": "sash_welded.json"}, "applies_to": ["garment.sash.front"]},
    {"id": "push", "operation": "collision.push_out", "inputs": {"mesh": "$steps.weld.mesh", "collider": "$inputs.leg"},
     "parameters": {"clearance": 0.004}, "outputs": {"mesh": "sash_fixed.json", "deltas": "deltas.json", "report": "push.json"},
     "preconditions": ["closed:collider", "role:garment.sash.front", "units:m"], "space": "pose",
     "evidence": [{"source": "record", "ref": "2026-11-02 の制作記録", "state": "observed"}]},
    {"id": "after", "operation": "check.penetration", "inputs": {"mesh": "$steps.push.mesh", "collider": "$inputs.leg"},
     "parameters": {"strict": true}, "outputs": {"report": "after.json"}}
  ]
}
```

- 入力の参照は `$inputs.<名前>`、前の工程の出力は `$steps.<id>.<出力名>`。依存順は参照と `depends_on` から決める（循環は拒否）。
- 前提 `preconditions`: `closed:<入力名>`（閉じたメッシュ）、`role:<役割>`（対応表にある）、`units:<単位>`。コロンを含まない文は説明として記録だけする。
  `applies_to` の役割も対応表で確かめる（対応表が無ければ `unresolved` に残して `needs_review`）。
- 工程の状態は `pending / running / succeeded / needs_review / failed`。検査に通らない工程は `needs_review`（`strict: true` なら `failed`、
  `report_only: true` なら測るだけ）。失敗した工程でレシピは止まり、後ろは `pending` のまま。run の状態は、失敗が 1 つでもあれば `failed`、
  `needs_review` があれば `needs_review`、全部通れば `succeeded`。`needs_review` の工程は `candidates` に残り、再利用の索引には載らない。
- 再利用: 工程の key（操作・パラメーター・入力の sha256・コードの指紋・numpy の版・preset と対応表の sha256）が一致し、前の成功した生成物が
  sha256 まで揃っているときだけ写して `reused_from` に残す。ファイルがあるだけでは再利用しない。`--force` で全部やり直す。
  `external.run` と `blender.run_script` は設定が参照する実行ファイルや素材（隠れた入力）を知らないので、再利用せず毎回実行する。
- 記録: `<out>/<run_id>/run_record.json`・`run.log`・`recipe.json`（写し）・`<工程 id>/<出力>`。`<out>/index.json` が再利用の索引。
  実行中に止まった run は次にロックしたとき `failed` に直す。
- 人の記録: `model3d accept RUN --decision accepted|rejected|hold --by NAME`（採否）、`model3d edit RUN --by NAME --what … --minutes N`（手修正の内容・回数・時間）。

```sh
uv run model3d run recipe.json [--out DIR] [--force] [--preset P] [--tool blender=/path/to/blender] [--quiet]
```

## 操作（`model3d ops`）

| 操作 | 入力 → 出力 | 内容 |
|---|---|---|
| `mesh.import` | file → mesh | JSON か OBJ を読んで `model3d-mesh/0.1` で書く |
| `mesh.extrude_profile` | profile（`{"points": [[x, y], …]}`）→ mesh | 厳密に凸で反時計回りの断面を `height` だけ押し出す。`twist_deg` か `twist_rad`、`steps`、`offset`。高さが負なら先に断面の有向辺を反転する。ねじり 0 なら 1 段 |
| `mesh.weld` | mesh → mesh | `distance` 以内の頂点を結合（`selection`・`center: mean|first`）。退化した面と他の面に含まれる面を除く |
| `mesh.transform` | mesh → mesh | `scale` → `rotate_z_deg` → `translate` |
| `mesh.export_obj` | mesh → obj | v / vt / f / g |
| `collision.push_out` | mesh, collider → mesh, deltas, report | 閉じた collider の中の頂点を最も近い面の外へ `clearance` だけ離す。外でも余裕より近い点は離す（`pad_outside`）。`deltas` は頂点ごとの移動量 |
| `check.structure` | rig → report | 必須骨の欠落（`required_bone_roles`: 配列か `preset` か `humanoid`）・循環・非有限値、ウェイトの正規化（`tolerance`） |
| `check.penetration` | mesh, collider → report | 中にある頂点の数・番号・深さ |
| `check.continuity` | track → report | コマごとの点の列で前のコマからの移動が `max_jump` 以下 |
| `external.run` | 任意 → 任意 | 設定 JSON の argv で別プロセスを呼ぶ（`{入力名}`・`{出力名}`・`{work}`）。版・ライセンス・重み・実行ファイルの sha256 を記録。シェルは使わない。再利用しない |
| `blender.run_script` | 任意 → 任意 | `blender --background [blend] --python script -- args…`。再利用しない。**未検証** |

押し出しと結合は独自実装で、原本ツールとの一致は主張しない。押し出しは、2026-11-03 の再構成の実験で無変更の JSCAD が作った合成部品 6 件と、
三角形の数・頂点集合・bounds・面積・体積（許容差 1e-12）で一致した（`tools/model3d/tests/data/jscad/`。不正な steps の拒否も同じ）。
結合は一般的な「距離で結合」で、Blockbench のコード（GPL-3.0-or-later）は使っていない。Blockbench の結合は選択順に依存する非推移的な
ものだが、ここは推移的に結合する（A-B と B-C が近ければ 3 つが 1 つになる）。

内外の判定は、複数方向の光線と面の交わりの回数の奇偶の多数決で決める。最も近い面の向きで決める方法（Shrinkwrap の Outside など）は、
口の開いた筒やふさいだ口のふちで読み違えた（2026-11-02 の記録）。口の開いた衝突相手は拒否する。補正は位置だけを直し、速度には入れない。

## 検査と判断基準（設計 §10 のうち実装したもの）

| 検査 | 実装 |
|---|---|
| 構造 | `check.structure`: 必須骨・循環・非有限値・ウェイトの正規化 |
| 衝突 | `check.penetration` / `collision.push_out`: 貫通の数と深さ。許容値超過は `needs_review` か `failed` で、未解決のまま成功にしない |
| 連続性 | `check.continuity`: 急跳び。点の列は外部（Blender や姿勢の推定）が書く |
| 再実行 | 同じ資産・設定・環境なら同じ key で再利用され、違えば再実行される（RunRecord で比べる） |
| 作業量 | `model3d edit` で手修正の内容・回数・時間を残す |
| 外見・動作・書き出し（再読み込み） | 未実装。外見の採否は人（`accept`）。30 秒の動作検査は動作データが要る（現状約 8 秒） |

数値のしきい値は基準ケースで校正してから別メッシュに使う。点の検査の結果を全表面の無貫通へ広げない。

## Blender・生成器・リギング・REA

- **Blender**（`model3d blender observe --blender EXE --blend FILE --out obs.json`）: `blender_adapter/observe.py` を Blender の中で動かし、
  オブジェクト・骨・ウェイトの要約・modifier・シェイプキーを Observation3D に書く。`model3d blender script` で任意のスクリプトを呼ぶ。
  起動の argv・版の記録・観測の整形はテストした（代役のプロセスで）。**本物の Blender では未検証**。
- **生成器・リギング**（`external.run`）: 設定 JSON（`docs/adapters.md` と同じ約束。`tool`・`version`・`argv`、任意で `license`・`weights`）。
  Hunyuan3D-2mv・TRELLIS.2・SkinTokens などの候補は、この接続口で別プロセスとして呼ぶ想定。本体はこのリポジトリに入れない。
- **REA**（`model3d rea report analysis_report.json out.json` / `model3d rea summary summary.json out.json`）: REA 6.4.0
  （commit `a56f7bb6…`）の解析報告と実験の集計を、工程の `evidence`（observed / inferred / unknown / unavailable）に写す。
  coverage が unknown のグラフは unknown のまま。集計に比較の結果（`final_all_pass` か `passed`/`total`）が無いツールの比較は
  unknown にし、観測にしない。REA は JS/TS の静的解析で、3D 専用の provider は無い。
- 2026-11-03 の再構成の実験一式（Blockbench の原本・再構成コード・REA の生出力）は**リポジトリに入れない**。Blockbench 由来の部分が
  GPL-3.0-or-later で、このリポジトリの LICENSE は未決のため（STATUS の DECIDE-1）。JSCAD（MIT）の原本出力の合成部品だけをテストに取り込んだ。

## Orpheus との接続（受け渡し）

1. 3D 側で描いた動画（緑背景か透過の連番から作った動画）を、`model3d handoff RUN <工程 id>/<出力名> handoff.json` で控えにする。
2. dance の spec の `performer.clips[].path` にその動画を書き、spec のどこかに `"x_model3d": {"handoff": "handoff.json"}` のように参照を置く
   （`x_` の鍵は dance が無視するので、`dance-shot/0.2` の契約は変えない）。
3. `mv dance render` の manifest に残る素材の sha256 と、`handoff.json` の `artifact.sha256` を照合する。`mv gate check` はそのまま使える。

描画そのもの（Blender での 30 秒の描画）は `blender.run_script` か `mv fx legacy` の経路で、どちらもこの環境では未検証。

## 限界・未検証

- **実キャラの資産では未確認**。r6（2026-11-02 に採用した第 6 版）の再実行は、Blender と素材のある PC で行う。元からの再構築に要る
  `parts.blend`・元の GLB/FBX・部品データは不足している（設計 §2）。
- **Blender・生成器・リギングの実行は未検証**（この環境に本体が無い）。接続口の形だけがある。
- **別メッシュへの転送（CorrespondenceMap の処理）・揺れのベイクと再開・30 秒の動作検査・GLB 書き出しは未実装**。
- 貫通の補正は凸の衝突相手で確かめた。凹んだ相手では 1 回の補正で出ないことがあり、`residual_inside` に残る。
- 結合は O(候補の組) で、大きなメッシュ（数十万頂点）では遅い可能性がある（未測定）。
- 骨で動かす前の形へ `deltas` を戻してシェイプキーに書く工程は Blender 側で、未実装。
