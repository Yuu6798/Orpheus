# model3d（自作の 3D モデリングツール・Orpheus の外部ツール・実験的）

設定画または既存モデルから、**部位・骨の対応 → 加工レシピの実行 → 形状と動作の検査 → 書き出し**を、レシピと記録で進める道具。
Orpheus（`mv dance` など）とは**出力ファイルと制作記録（RunRecord）だけで接続**し、既存の 2D ダンス工程に 3D 固有のデータ契約は混ぜない
（統合位置は試作の後に決める）。コードは `tools/model3d/`、import 名は `model3d`。`mv_pipeline` とは互いに import しない。

- **再現性**: 内部のメッシュ演算は同じ入力・設定・コード版で同じ数値結果を出す。外部 worker は入力・版・出力を記録して比較する。
  Blender や動画の全バイト一致、環境をまたぐ全画素一致までは保証しない。制作工程から LLM やネットワークは呼ばない。
- **採点しない**: 検査は構造と数値（`pass`）。見た目の採否は人の判定（`model3d accept`）で、RunRecord の `adopted` に別に残る。
- **検査は開発原則の例外**: AGENTS.md の「生成物の開発中は検査を足さない」に対し、このツールは `mv gate` と同じ例外（2026-11-03 ユーザー承認）。設計が実装単位に `validation` を含み §10 で検査を求めるため。単独の検査（`generate inspect`・`mesh compare`・`check`）も構造と数値の報告だけで、採否は決めない。
- **固有の値を持たない**: 部品番号（`part_3`）や特定サービスの骨名（`mixamorig:Hips`）は preset で役割（`garment.sash.front`・`hips`）に
  対応付け、共通処理は役割だけを参照する。実キャラの preset・素材・動画・重み・鍵は Git に入れない。
- **推定を実測扱いしない**: 入力の sha256 が宣言と違えば実行しない。役割の対応表が無い・未確認、観測不足は `unresolved` に残して
  `needs_review` にし、成功扱いにしない。未実施の検査は合格ではない。
- **実験的**: データ契約（下記）は `tools/model3d/src/model3d/schemas/`（`contracts/` の外）。`contracts/` への昇格はユーザー承認が要る。
- 合成素材（`model3d demo`）と JSCAD の原本出力との照合に加え、非公開の既存制作 worker を
  `model3d dance-recipe` で接続する。[実素材の再構築・描画と検証範囲](model3d-dance.md)を参照。
  汎用 Blender 観測・本物の生成器・別メッシュへのリギングは、それぞれ別の検証範囲として扱う。

設計の元は 2026-11-03 の設計たたき台 v0.3（ユーザーのクラウド）。その後の v0.5 とも照合し、別試作の実績とこの実装の実績を分けている
（[実行確認と残作業](model3d-review.md)）。実装単位は `core`・`ops`・`adapters`・`blender_adapter`・`rea_bridge`・
`presets`・`validation`（`checks.py`）。初期版の完成は M3（30 秒の検査・観測接続・候補比較・再開）で、ここにあるのは M0〜M1 の土台
（資産一覧・SemanticMap・レシピ実行器・検査・記録）と、別メッシュへの適用（M2）に向けた役割ベースの部品。

## 準備

```sh
uv sync --locked --extra analysis --extra fx --extra model3d    # model3d extra を足す（uv sync は指定外の extra を外す）
uv run model3d --version
```

依存は numpy・jsonschema・Pillow（ルートと同じ版）。Blender（r6 の再現では 4.5.15 を固定）・生成器・リギングは別プロセスで、
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
→ 構造の検査 → OBJ 書き出し** の 9 工程で、`status` は `succeeded`。結合では旧→新頂点対応と 35 頂点用のリグも書き、
構造の検査は補正後メッシュとそのリグを照合する。素材はすべてプログラムで作った合成物で、実キャラの素材は使わない。
デモ自身が定義した部位と骨の対応だけを、合成素材の出自を付けて確認済みにする。利用者の資産を確認・採用する処理ではない。
`projects/` は `.gitignore` 済み。

```sh
uv run model3d run projects/model3d-demo/recipe_generate.json   # 合成の設定画 → 前処理 → 代役の生成器 → 検査 → 記録（status は needs_review）
```

`recipe_generate.json` は、合成の設定画 1 枚（頭の円・胴の四角・帯の横帯）を前処理し、**生成器の代役**（`backends/standin_initial_model.py`。
前景の輪郭の箱を書くだけで生成ではない）を別プロセスで呼び、検査と記録（`model3d-initial-model/0.1`）を書いて OBJ に出す。
生成の工程は常に `needs_review` なので run の `status` も `needs_review`（`candidates` は `["generate"]`）。本物の生成器は下の
「設定画から初期モデルを生成する」。

合成素材の検査が通ることは、実素材で同じ処理が成立することを保証しない。実素材では、まず対応表の確認（`semantic confirm`）と
入力のハッシュの宣言から始める。

## データ契約（`model3d validate KIND FILE`）

| 契約（`schema_version`） | 中身 |
|---|---|
| AssetManifest `model3d-asset-manifest/0.1` | 入力の参照と sha256・大きさ・種類、部品 ID、メッシュの頂点構成のハッシュ（`topology_sha256`）と形のハッシュ、単位、軸、変換行列、資産の版 |
| SemanticMap `model3d-semantic-map/0.1` | 部品 → 役割、骨 → 役割（親・レスト行列）、面領域 → 役割（面番号を部品の名前と頂点構成のハッシュに結び付ける。別の部品や構成の違うメッシュには当てない）、根拠（preset / user / inferred）、確認状態（confirmed / unconfirmed / rejected）、未対応と必須の欠落、履歴 |
| CorrespondenceMap `model3d-correspondence/0.1` | 転送元と転送先の版と頂点構成、対応点（面と重心座標）、距離と曖昧さ、未対応の役割。空間的に近いだけの対応は作らない（形式だけ。転送の処理は未実装） |
| RecipeGraph `model3d-recipe/0.1` | 入力（パスと任意の sha256）、工程（操作・入力の参照・パラメーター・出力・依存・前提・座標系・適用する役割・面領域の選択 `select`・根拠） |
| Observation3D `rea-3d-observation/0.1` | 観測対象、取得方法、座標系、事実（facts）、出自（evidence: observed / inferred / unknown / unavailable）、範囲（coverage）、限界と未解決。推定値は facts に混ぜない |
| RunRecord `model3d-run/0.1` | 入力・設定・環境（Python・numpy・コードの指紋・道具）、工程ごとの状態・key・生成物の sha256・観測・未解決・再利用元、候補、人の判定、手修正、警告 |
| mesh `model3d-mesh/0.1` | 頂点・面（多角形）・頂点ごとの uv・面の集合 parts・頂点の selection・単位・軸。OBJ と相互変換（`model3d mesh convert`）。面積・体積・衝突のための三角形化は、凸な面は扇形、凹んだ四角形は凹んだ頂点を通る対角線、凹んだ n 角形は耳切り |
| rig `model3d-rig/0.1` | 骨（名前・親・head/tail・レスト行列）と頂点ウェイト（骨ごとの [頂点番号, 重み]）。weights.topology_sha256 は任意だが、メッシュとの整合検査・結合時のウェイト移し替えでは必須 |
| preset `model3d-preset/0.1` | キャラごとの対応表と値（`tools/model3d/presets/README.md`） |
| stage track `model3d-stage-track/0.1` | `render.stage` が描くコマの並び（書き出す T と動きのコマ f）と、コマごとのカメラ（位置・狙う点・レンズ）・舞台の模様・壁・輪郭光・光の柱・塗りの共有グループの値 |
| frames `model3d-frames/0.1` | 描いたコマのフォルダとファイル（T・f・sha256・大きさ）、fps・解像度、描いていない T |
| stage plan `model3d-stage-plan/0.1` | `plan.stage` の段取り表: 時刻の印（秒か拍）と色の名前、カメラ（区間ごとの方位・距離・高さ・レンズ・狙う点と動き、寄りと揺れ、骨の通り道のならし方）、舞台の模様の区間、光（色・強さ・拍と印の信号・落とす区間・光ごとの係数） |
| light keys `model3d-light-keys/0.1` | 曲の音の帯ごとの強さ（low・mid・high。コマごとに 0〜1）と拍の格子（period・k0_time）、fps とコマの数 |
| bone path `model3d-bone-path/0.1` | 動きのコマごとの骨の付け根の位置（世界の座標、m）、シーンの scale_length、アーマチュアの名前 |
| handoff `model3d-handoff/0.1` | Orpheus へ渡す成果物 1 つの控え（run・レシピ・成果物の sha256） |
| initial model `model3d-initial-model/0.1` | 設定画から生成した初期モデルの記録: 入力のハッシュ、前処理の条件、生成器の素性（版・重みの sha256・ライセンス・実行ファイルの sha256）、パラメーター、生成物のハッシュ、幾何と構造の検査。review は常に needs_review |

未知の鍵は拒否し、`x_` で始まる鍵はどの階層でも許す（記録には残る）。

## 役割の語彙

`tools/model3d/src/model3d/semantic.py` の `PART_ROLES`（`body.leg.upper.L`、`hair.front`、`garment.sash.front`、`collider.leg.upper.L` など）と
`BONE_ROLES`（`hips`、`spine`、`leg.upper.L`、`cuff.L`、`sash.1` など）。左右は `.L` / `.R`。語彙に無いものは `x.` で始めれば通る。
Mixamo 風の `mixamorig:Hips` は接頭辞を外して `Hips` とも照合する。
- 指の骨は `finger.<指>.<節>.<左右>`（指は thumb・index・middle・ring・pinky、節は付け根から 1・2・3、4 は先の位置だけを持つ骨。例 `finger.index.1.L`）。つま先の先は `toe.tip.L` / `toe.tip.R`。
- `MIXAMO_ROLES` は、Mixamo の標準の骨の名前（接頭辞を外したもの）から骨の役割への対応表（指とつま先の先を含む、65 本）。
  - `semantic.mixamo_bones(prefix)` は、それに接頭辞を付けた表を返す。preset の `bones` の元に使う。
  - `motion.retarget` が要る骨（`REQUIRED_BONES`）と、あれば使う骨（`OPTIONAL_BONES`。指の 1〜3 節とつま先）は、どれもこの表にある（テストで確かめる）。

```sh
uv run model3d semantic build preset.json semantic_map.json --asset-id ASSET --parts part_1,part_2 --rig rig.json
uv run model3d semantic confirm semantic_map.json --kind parts --key part_1 --state confirmed --by NAME
```

未対応（`unmapped`）と必須の欠落（`required_missing`）は出力に残る。確認していない対応がある run には警告が付く。

### 面領域（部位分けの無いメッシュの役割）

生成器の出力のように部位分けの無いメッシュは、面領域で役割を面に結び付ける。

```sh
uv run model3d semantic components mesh.obj --out components.json --obj components.obj   # 連結成分の一覧と、成分ごとの g を付けた OBJ
uv run model3d semantic add-region semantic_map.json mesh.obj --key body --role body --component 0 --basis inferred --by NAME
uv run model3d semantic check semantic_map.json mesh.obj                     # このメッシュ版に当たるか（bound / other / stale / invalid）
uv run model3d semantic preview semantic_map.json mesh.obj preview.obj       # 面領域ごとの g と ~unassigned を付けた OBJ
uv run model3d semantic confirm semantic_map.json --kind regions --key body --state confirmed --by NAME
uv run model3d semantic remap semantic_map.json weld/vertex_mapping.json semantic_map_welded.json --by NAME
```

- 面の選び方は、連結成分（`--component`。番号は `semantic components` の `component`）、面番号の JSON（`--faces`。Blender で選んだ面など）、
  メッシュの面集合（`--mesh-part`。OBJ の `g`）、重心が入る軸に沿った箱（`--box`。ほかの選び方と併用すると、その中で箱に入る面だけ）。
  選び方とメッシュの sha256 は `history` に残る。成分の一覧には面・頂点の数、面積、bounds、面積で重みを付けた重心、閉じているか、
  頂点色の平均が入る（役割を人が決める材料で、役割の推定ではない）。
- 面領域は、追加したメッシュの名前（`part`。model3d-mesh の `name`。OBJ は model3d が書いた見出しの名前か、無ければファイル名）と
  頂点構成（`topology_sha256`）の両方に結び付き、状態は `unconfirmed`。名前の無いメッシュには作れない。`--basis` は人が選んだら `user`、
  成分の大きさなどから選んだ候補なら `inferred`。人が `preview` の OBJ を見てから `confirm` する。色・形・骨からの自動の部位分割はしない。
- 名前の違うメッシュには、頂点構成が同じでも当てない（左右に写した袖など。`check` は `other` と報告し、`select` の工程は止まる）。
  同じ形の部品は別の名前にする（同じ名前で同じ頂点構成の別のメッシュは区別できない）。
- 同じ部品で頂点構成の違うメッシュには当てない（古い対応。`check` が `stale` と報告し、`select` の工程は止まる）。位置だけを変える工程
  （`mesh.transform`・`collision.push_out`。名前も引き継ぐ）の後はそのまま当たる。結合のように構成を変えた後は、その工程の mapping の正確な面の対応
  （`face_old_to_new`）で `remap` する（元の対応表は残し、別のファイルに書く）。付け替えるのは対応の source と同じ部品（`source_name`）・
  同じ頂点構成の面領域だけで、頂点構成が同じ別の部品の面領域は変えない。面が消えた領域は `unconfirmed` に戻す。
  近い面を探して付け替えることはしない。

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
  `applies_to` と `role:` で使用する役割が未確認なら `unresolved` に残して `needs_review` とし、その工程は成功キャッシュから再利用しない。
  使用しない役割の未確認状態は警告だけに残す。
  **`applies_to` は役割の存在・確認状態の宣言であり、メッシュの部分選択ではない。**
- 選択 `select`: `{"<入力名>": "<役割>"}`。実行器は対応表から、その入力メッシュの名前と頂点構成に結び付いた面領域と、対応表の部品 ID と
  同じ名前の面集合（`mesh.parts`）を選んで操作に渡し、記録の `observations` に選んだ面・頂点の数と出どころを残す。別の部品の面領域しか
  無い、古い対応（構成の違う面領域しか無い）、当たる面の無い役割では工程を止め、メッシュ全体へ黙って当てない。`select` の役割は使用する役割に数え、未確認なら
  `needs_review`。受け付けるのは `mesh.weld`・`collision.push_out`・`check.penetration`・`mesh.extract_region`・`texture.project_reference` で、ほかの操作に書くか、
  recipe に `semantic_map` が無いと実行前に止まる。パラメーターの `selection` / `use_selection` と同時には使えない。
  1 つのレシピの中で構成を変える工程（結合）の後に `select` するには、役割を面集合（`mesh.parts`）として持たせる（結合が一緒に付け替える）。
  面領域は工程の中では自動で付け替えない（run の間で `semantic remap`）。
- 工程の状態は `pending / running / succeeded / needs_review / failed`。検査に通らない工程は `needs_review`（`strict: true` なら `failed`、
  `report_only: true` なら測るだけ）。材料が無くて未実施の検査（点の無いコマなど）も `needs_review` で、`strict` なら `failed`。
  工程の `space`（座標系）は記録と生成物（`collision.push_out` の deltas）に写る。失敗した工程でレシピは止まり、後ろは `pending` のまま。run の状態は、失敗が 1 つでもあれば `failed`、
  `needs_review` があれば `needs_review`、全部通れば `succeeded`。`needs_review` の工程は `candidates` に残り、再利用の索引には載らない。
- 再利用: 工程の key（操作・パラメーター・入力の sha256・コードの指紋・numpy と Pillow の版・preset と対応表の sha256）が一致し、前の成功した生成物が
  sha256 まで揃っているときだけ写して `reused_from` に残す。ファイルがあるだけでは再利用しない。`--force` で全部やり直す。
  recipe の入力（`$inputs`）は工程の前後で読み込んだときの sha256 と比べ、変わっていればその工程を止める
  （変わった中身で作った結果を、古い sha256 の名で記録・再利用しない）。
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
| `mesh.import_parts` | parts [, normals] → mesh | 部品ごとの配列を持つ npz（`names` と、i 番目の部品の `co{i}`・`f{i}`・`uv{i}`。法線は別の npz の `n{i}`）から `part` の部品を読む。法線は `x_normals`（部品の順で対応させる）、名前は部品の名前。形・整数・有限を確かめ、読めないファイルを名指す。pickle の入った配列は読まない |
| `mesh.extrude_profile` | profile（`{"points": [[x, y], …]}`）→ mesh | 厳密に凸で反時計回りの断面を `height` だけ押し出す。`twist_deg` か `twist_rad`、`steps`、`offset`。高さが負なら先に断面の有向辺を反転する。ねじり 0 なら 1 段 |
| `mesh.weld` | mesh [, rig] → mesh [, mapping, rig] | `distance` 以内の頂点を結合（`selection` か工程の `select`・`center: mean|first`）。退化面と包含面を除く（結合に関わった面と隣接面のみ）。uv・頂点色・法線（`x_normals`。長さは 1 に戻す）も同じ規則でまとめる。mapping は旧→新頂点番号、旧→新面番号（`face_old_to_new`。消えた面は -1）、双方の topology と部品の名前（`source_name`・`target_name`）。rig は入出力を対で指定し、同じ mean / first 規則でウェイトを移す |
| `mesh.transform` | mesh → mesh | `to_units`（m / cm / mm の換算）→ `to_axes`（`{"up", "forward"}` の約束の換算）→ `scale` → `rotate_z_deg` → `translate`。頂点の法線（`x_normals`）も同じ向きへ回す |
| `mesh.extract_region` | mesh → mesh [, mapping] | 工程の `select` で選んだ役割の面だけのメッシュ（使わない頂点は落とす。uv・頂点色・法線・単位・軸は引き継ぐ）。mapping は新→旧の頂点・面番号と双方の topology。観測に連結成分の数と閉じているか |
| `mesh.export_obj` | mesh → obj | v / vt / f / g。見出しの行に units・軸・名前を書き、`mesh.import` で戻す（名前は面領域が部品を区別するのに使う）。数値は往復で変わらない表記 |
| `collision.push_out` | mesh, collider → mesh, deltas, report | 閉じた collider の中の頂点（`selection` か工程の `select` の面の頂点だけにできる）を最も近い面の外へ `clearance` だけ離す（`clearance` は mesh の単位。mesh と collider の単位か軸の約束が違えば止まる）。外でも余裕より近い点は離す（`pad_outside`）。`deltas` は頂点ごとの移動量 |
| `check.structure` | rig [, mesh] → report | 必須骨の欠落（`required_bone_roles`: 配列か `preset` か `humanoid`）・循環・非有限値、ウェイトの正規化（`tolerance`）。mesh があれば頂点数・topology も必須照合。使用する未確認の骨対応は needs_review |
| `check.penetration` | mesh, collider → report | 中にある頂点の数・番号・深さ（工程の `select` があればその面の頂点だけを調べ、数を `checked` に残す。mesh と collider の単位か軸の約束が違えば止まる。`directions` は 1 本以上の有限で長さ 0 でない向き。奇数本を勧める） |
| `check.continuity` | track → report | コマごとの点の列で前のコマからの移動が `max_jump` 以下 |
| `check.motion` | points → report | `motion.prepare_keypoints` の points の動きを数で測る（[整えた点の動きを測る](#整えた点の動きを測る)）。膝とつま先の食い違い・接地中の足の滑り・床へのめり込み・1 コマの移動・骨の長さのぶれ。しきい値を渡したときだけ合否を出す。知らないパラメーターは止める |
| `texture.render_view` | mesh, texture → image [, report] | uv のあるメッシュにテクスチャを貼り、座標軸に沿った正射影（`view`）で描く。照明なし（色の確認と目印を読むため） |
| `texture.draw_mask` | [like] → mask | 多角形・楕円（`angle` で傾ける）を `value` で順に塗った灰色の画像。大きさは `size` か入力 `like` の画像。`feather` で縁をぼかす。知らないパラメーターは止める |
| `texture.project_reference` | mesh, texture, reference [, mask] → texture [, weights, report, overlay] | 参照画像を、目印で合わせた視点からテクスチャの画素へ写す（[設定画をテクスチャへ写す](#設定画をテクスチャへ写す)）。工程の `select` で役割の面だけを塗る。1 画素も写らないとき・任意の `review_note` があるときは `needs_review`。知らないパラメーターは止める |
| `motion.prepare_keypoints` | keypoints → points [, report] | SAM 3D Body の 70 点（npz の `pred_keypoints_3d` + `pred_cam_t`）を整える。1 コマの跳ねの除去、平滑化（奥行きは窓を広く）、床の水平化（`pitch_deg` で上書き可）、上体を起こす（`torso_back_deg`）、接地の区間で足を止める（`contact`・`step_split`）。points は x 右・y 奥・z 上・m・床 z=0 の npz（[推定した 3D の点を整える](#推定した-3d-の点を整える)）。知らないパラメーターは止める |
| `motion.edit_keypoints` | points → points [, report] | 整えた 70 点を区間ごとに手直しする（[点を手直しする](#点を手直しする)）。`edits` に順に並べる: `head_turn`（顔の向きを足す）・`fist`（片手を握りに）・`forearm_roll`（両の拳が胸の前で回り合う動きを作り直す）。`stance` と `focal` は引き継ぐ。知らないキーは止める |
| `motion.retarget` | points, character [, skip_vertices, head_dpitch] → blend, pose, report | 整えた 70 点を、Mixamo 式の骨のキャラ（.blend）へ Blender の中で移す（[点をキャラの骨へ移す](#点をキャラの骨へ移す)）。`resolve` で手・前腕の胴への刺さりを直す。Blender が要り、再利用しない（毎回実行） |
| `motion.twist_split` | blend → blend, report | 骨へ移した動きの前腕のねじれを、肘と手首に半分ずつ持たせ直す（[前腕のねじれを分け直す](#前腕のねじれを分け直す)）。手の向き・前腕の向き・指先の位置が変わらないことを確かめ、許容を超えたら止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.ground_feet` | blend, points → blend, report | 接地のコマ（points の `stance`）に足の一番低い点が床に着くよう、腰の高さを足す（[足を床に着ける](#足を床に着ける)）。足の点は `foot_objects` か足の骨の重み。直した後に測り直し、腰を動かした量だけ足が動かなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.push_hands` | blend → blend, report | 手が体（胴・腰まわり・脚）に沈むコマに、二の腕を肩のまわりに回して手を体の面から浮かせる（[手を体から浮かせる](#手を体から浮かせる)）。肘から先は変えない。手と体の点は骨の重み（体は `body_objects` でも）。`passes` 回くり返し、沈みを直す前・後で数える。Blender が要り、再利用しない（毎回実行） |
| `motion.spring_bones` | blend → blend, report | 鎖の骨（髪など。`chains` に根元の骨）を、ばねで体の動きに遅れて揺らし、回転のキーに焼き付ける（[鎖の骨をばねで揺らす](#鎖の骨をばねで揺らす)）。狙いは親に固く付いた形（`rest`。`follow` で骨と高さの並びに沿わせる）か、鎖にある動き（`animation`）。当たり（`colliders`）・鎖のつなぎ（`links`）。焼き付けた骨の向きと根元を確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.sleeve_bones` | blend → blend, report | 袖口の骨（`sleeves` の `bone`）を、関節（`follow`。前腕など）の曲げの `ratio` 倍に、減衰つきのばねで少し遅れて付いて行かせる（[袖口を曲げに遅れて付いて行かせる](#袖口を曲げに遅れて付いて行かせる)）。ねじれは入れない。焼き付けた向きを確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.ribbon_bones` | blend → blend, report | 帯の垂れの鎖の骨（`chains`。長さは鎖ごとに違ってよい）を、重力で垂れて太ももの上を前へ乗り越えるように揺らす（[帯の垂れを揺らす](#帯の垂れを揺らす)）。狙いは `follow` に `thigh`（下ほど太もも）を混ぜられる。当たりは `colliders` と、メッシュの輪切りから作る `fitted`。押す向きは腰（`hips`）の前と上へ寄せる。焼き付けた骨を確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `look.toon` | blend → blend, report | キャラに描く前のセル調の塗りと輪郭線を付ける（[セル調の塗りと輪郭線を付ける](#セル調の塗りと輪郭線を付ける)）。体は決まった向きの光で影を 2 段に分け、舞台の色と縁の光を足して Emission で出す。顔（`faces`）は光を少し受けて絵の色を出す。輪郭線は反転ハル。舞台の色と縁の強さは共有のノードグループ `model3d_stage_tone`。Blender が要り、再利用しない（毎回実行） |
| `motion.bone_path` | blend [, track] → path | 動きのコマごとに、骨（既定は腰と頭）の付け根の位置を m で測る（[段取りを組む](#段取りを組む)）。track（描くコマの並び）があればその動きのコマを重ねず小さい順に、無ければ `frame_range` か .blend のシーンの全コマ。path は `model3d-bone-path/0.1`。Blender が要り、再利用しない（毎回実行） |
| `plan.stage` | plan, keys, bones [, cut] → track [, report] | 段取り表（`model3d-stage-plan/0.1`）と光のキー（`model3d-light-keys/0.1`）と骨の通り道から、`render.stage` のトラックを作る（[段取りを組む](#段取りを組む)）。コマごとに、カメラ（区間・動き・寄りと揺れ）、壁の模様、光と塗りの縁の値を決める。cut（T と f のトラック）があればその並びで。計算だけで、同じ入力なら再利用する。report は区間ごとのコマの範囲 |
| `render.stage` | blend, track → frames, report | キャラを舞台と光の中でコマごとに PNG に描く（[舞台で描く](#舞台で描く)）。床・LED の壁・トラス・光の柱とスポット 4 つ、EEVEE と光のにじみ。トラック（`model3d-stage-track/0.1`）のコマの並びとカメラ・光・塗りの値で描き、無ければ腰を追う正面のカメラと動かない光。輪郭線の太さはカメラの距離で画面の上にそろえる。舞台を切って `background` で緑背景か透けにも描ける。`push` で、描く直前に帯などの頂点を当たり（閉じたメッシュ）の外へ出す。描いたコマはとばす（続きから描ける）。frames は描いたコマの一覧（`model3d-frames/0.1`）。Blender が要り、再利用しない（毎回実行） |
| `generate.prepare_reference` | image → prepared, metadata | 設定画（RGBA）を前景の矩形で切り出し（互換のため最後の行と列は含まない）、正方形の中央に置き、`foreground_ratio`（既定 0.85）で余白を足し、アルファを灰色 0.5 に合成した RGB の PNG にする |
| `generate.initial_model` | image → mesh, record, prepared | 前処理 → `backend`（生成器の設定）の別プロセス → OBJ を読む → 検査 → 記録。`resolution`・`density_threshold`・`seed` を渡す。**常に needs_review**。再利用しない |
| `generate.inspect_initial_model` | mesh → report | 頂点・三角形・有限・bounds・連結成分・閉じているか・巻き方・頂点色・実寸の校正 |
| `external.run` | 任意 → 任意 | 設定 JSON の argv で別プロセスを呼ぶ（`{入力名}`・`{出力名}`・`{work}`）。版・ライセンス・重み・実行ファイルの sha256 を記録。シェルは使わない。任意の `review_note`（空でない文字列）は `unresolved` に残して `needs_review` にする。再利用しない |
| `blender.run_script` | 任意 → 任意 | `blender --background --factory-startup [blend] --python script -- args…`（利用者の設定・アドオンが要るときだけ `factory_startup: false`）。再利用しない。起動は本物の Blender で確かめた。recipe の工程としては**未検証** |

`mesh.weld` のウェイト移し替えは結合時に得た正確な頂点対応だけを使う。別メッシュへの近傍転送ではない。
元のリグが入力の頂点数・topology と一致しなければ止まり、未正規化や負のウェイトを黙って修復しない。
rig のない結合も従来どおり使えるが、メッシュとリグの整合を確認したことにはならない。
単独 CLI では `model3d check structure rig.json --mesh processed_mesh.json` で同じ照合を行う。

押し出しと結合は独自実装で、原本ツールとの一致は主張しない。押し出しは、2026-11-03 の再構成の実験で無変更の JSCAD が作った合成部品 6 件と、
三角形の数・頂点集合・bounds・面積・体積（許容差 1e-12）で一致した（`tools/model3d/tests/data/jscad/`。不正な steps の拒否も同じ）。
結合は一般的な「距離で結合」で、Blockbench のコード（GPL-3.0-or-later）は使っていない。Blockbench の結合は選択順に依存する非推移的な
ものだが、ここは推移的に結合する（A-B と B-C が近ければ 3 つが 1 つになる）。

内外の判定は、複数方向の光線と面の交わりの回数の奇偶の多数決で決める。最も近い面の向きで決める方法（Shrinkwrap の Outside など）は、
口の開いた筒やふさいだ口のふちで読み違えた（2026-11-02 の記録）。辺や頂点を共有する三角形を同じ距離で通る交点は 1 回と数える。
口の開いた衝突相手は拒否する。補正は位置だけを直し、速度には入れない。

## 設定画をテクスチャへ写す

設定画の描き込み（目・口・飾りなど）を、既にあるメッシュの uv テクスチャへ写す工程。形は変えない。

- 視点は座標軸に沿った正射影: `{"from": "-Y", "center": [右, 上], "span": 画像の高さに当たる長さ, "size": [幅, 高さ]}`。
  `from` はカメラのある側（-Y が正面、+Y が背面、±X が横）で上は +Z。長さはメッシュの単位で、軸の約束が 上 +Z・正面 -Y でない
  メッシュは止まる（`mesh.transform` の `to_axes` でそろえる）。
- 位置合わせは目印の組 `alignment: {"model": [[x, y], …], "reference": [[x, y], …], "kind": "similarity" | "affine"}`。
  model は視点の画素（`texture.render_view` の画像で読む）、reference は参照画像の画素。2 点の相似変換（両目など）か、3 点以上の
  最小二乗。**目印は推定しない**。残差は記録の `alignment.max_residual_px` に残る。
- 色はテクスチャの画素ごとに、その画素が載る面の点を視点へ写して参照画像から引く（テクスチャ側から引くので穴が空かない）。重みは
  見えているか（メッシュ全体の z バッファ。点の写る画素でいちばん手前の面の平面を、その点の位置まで延ばした奥行きから
  `depth_tolerance` 以内。傾いた面が画素の中心との奥行きの差で自分を隠さない）× 面の向き（`min_facing` から `facing_ramp` で 0→1）×
  `mask` × 参照画像のアルファ。重みの無い画素は元の色のまま。三角形から `expand`（画素）以内の画素も塗る（uv の島の縁の外。
  細い三角形も同じ幅だけ広がる）。テクスチャにアルファがあれば、色だけを塗ってアルファはそのまま残す。
- uv が島をまたいで引き伸ばされた三角形（周長の比が中央値の `max_stretch` 倍を超えるもの。継ぎ目の頂点を結合したときなど）は塗らない。
  繰り返しの uv で 1 をまたぐ三角形（u が 0.75〜1.25 など）は、重なる 1 枚ごとに基本の 1 枚へずらして塗る（描画と同じ画素に当たる）。
- `overlay` は元のテクスチャの描画に、合わせた参照画像をマスクの所だけ半分重ねた確認用の画像。目印とマスクはこれを見て決める。

```json
{"id": "mask", "operation": "texture.draw_mask", "inputs": {"like": "$inputs.sheet"},
 "parameters": {"feather": 3.0, "shapes": [{"ellipse": [200, 300, 30, 15], "angle": 8}, {"ellipse": [300, 300, 30, 15], "angle": -8},
                                          {"polygon": [[180, 315], [320, 315], [250, 400]]}]},
 "outputs": {"mask": "mask.png"}},
{"id": "face", "operation": "texture.project_reference",
 "inputs": {"mesh": "$inputs.head", "texture": "$inputs.atlas", "reference": "$inputs.sheet", "mask": "$steps.mask.mask"},
 "parameters": {"view": {"from": "-Y", "center": [0.0, 1.5], "span": 0.3, "size": [1000, 1000]},
                "alignment": {"model": [[400, 600], [600, 600]], "reference": [[200, 300], [300, 300]]}},
 "outputs": {"texture": "atlas_face.png", "weights": "weights.png", "report": "face.json", "overlay": "overlay.png"}}
```

2026-11-03 にユーザーの環境（CPU）で、実キャラの頭の部品（約 19 万頂点・35 万三角形、4096² の atlas）へ頭の設定画の正面の顔を
両目の 2 点で合わせて写した（1 工程 約 50 秒）。前髪と顎の線を写さないよう、マスクは目の楕円 2 つと目の下から顎の手前までの多角形にした。
素材・目印・マスクの値は作品ごとの private な recipe に置き、Git には入れない。

## 設定画から初期モデルを生成する

設定画（RGBA 1 枚）から初期モデルを作る工程。2026-11-03 の検証（ユーザーの環境、CPU。Drive の検証記録と再現 zip）で、固定した
公開 TripoSR（MIT）のネットワークと重みを使い、**前処理・ジョブの入出力・密度場からメッシュへの処理の再構成・元の実装との比較・
記録を自作**した構成に実行上の裏付けが得られた。前処理は実設定画 1 件と合成 6 件で原本の関数と画素単位で一致し、形状は元の抽出と
座標・向き付き三角形・RGBA が許容差 0 で一致した（記録の値）。この repo に入れたのはその自作の部分で、ニューラルネットと重みは入れない。
合成 6 件の記録（sha256）は `tools/model3d/tests/data/prepare_reference_cases.json` に取り込み、移植した前処理をテストで照合している。

```
設定画 (RGBA) → generate.prepare_reference（自作）→ 生成器（別プロセス。backends/）→ OBJ → generate.initial_model が検査して記録
```

- **入力**: RGBA の設定画 1 枚、`foreground_ratio`、生成器の設定（`backend`: `tool`・`version`・`argv`・`outputs.obj`、任意で
  `license`・`weights`・`kind`）、`density_threshold`（既定 25.0）、`resolution`（既定 128）、`seed`。
- **出力**: 頂点色付きの mesh（JSON）と OBJ（生成器が書けば GLB も）、入力と前処理済み PNG の sha256、前処理の条件、幾何と構造の検査、
  生成器の素性（版・重みの sha256・ライセンス・実行ファイルの sha256・argv・終了コード・秒）、`model3d-initial-model/0.1` の記録。
- **採用の条件**: 生成が終わっただけでは採用しない。工程は常に `needs_review` で、正面・側面・背面を人が見てから、分離・形状修正・
  リトポロジーへ渡す（`model3d accept` は run 全体の判定）。
- **座標**: 生成器の正規化した空間のまま（実寸は未校正。`checks.scale_calibrated` が false）。preset の身長に合わせる換算は
  `mesh.transform` で別工程として行う。
- **生成器は別プロセスの交換可能な部品**: まず TripoSR（単一視点）で動く入口を保つ。多視点の生成器（Hunyuan3D-2mv は Tencent の
  community license なので任意の追加）も同じ設定・検査の契約（`kind: multi_view`）で足す。InstantMesh の既定カメラ（6 視点・仰角 ±）に
  直交三面図をそのまま差し替えられるとは判定しない。
- **環境**: TripoSR の環境（Python 3.12・torch CPU・trimesh・torchmcubes MPL-2.0・重み約 1.68 GB）は別の仮想環境に作る。手順と固定した
  版・sha256 は `tools/model3d/backends/README.md`。この環境（CI）では代役の生成器だけが動き、本物の生成器は**未検証**。

```sh
uv run model3d generate prepare 設定画.png prepared.png            # 前処理だけ（人が見る）
uv run model3d generate inspect initial_model.obj --out report.json  # 生成物の検査だけ
uv run model3d mesh compare a.obj b.obj                            # 頂点番号と面の列挙順の違いを除いて同じか
```

実証したのは正面 1 枚で、背面・側面は入力から拘束していない。記録の生成物は顔・指・衣装の模様が粗く、連結成分が複数で閉じていない
（記録の値）。リグ・ウェイト・表情・衣装物理は無い。

## 検査と判断基準（設計 §10 のうち実装したもの）

| 検査 | 実装 |
|---|---|
| 構造 | `check.structure`: 必須骨・循環・非有限値・ウェイトの正規化。mesh 入力ありの場合は頂点数・topology との整合も必須。rig だけの検査ではメッシュとの対応を保証しない |
| 衝突 | `check.penetration` / `collision.push_out`: 貫通の数と深さ。許容値超過は `needs_review` か `failed` で、未解決のまま成功にしない |
| 連続性 | `check.continuity`: 急跳び。点の列は外部（Blender や姿勢の推定）が書く |
| 再実行 | 同じ資産・設定・環境なら同じ key で再利用され、違えば再実行される（RunRecord で比べる） |
| 作業量 | `model3d edit` で手修正の内容・回数・時間を残す |
| 外見・動作・書き出し（再読み込み） | 外見の採否は人（`accept`）。合成の OBJ の形状・頂点色の読み戻しは確認済み。骨・材質・アニメの往復は未実装。別試作の採用区間は 147 フレーム／30 fps（4.9 秒）で、30 秒の動作検査は未実施 |

数値のしきい値は基準ケースで校正してから別メッシュに使う。点の検査の結果を全表面の無貫通へ広げない。

## Blender・生成器・リギング・REA

- **Blender**（`model3d blender observe --blender EXE --blend FILE --out obs.json`）: `blender_adapter/observe.py` を Blender の中で動かし、
  オブジェクト・骨・ウェイトの要約・modifier・シェイプキーを Observation3D に書く。`model3d blender script` で任意のスクリプトを呼ぶ。
  観測の `space` は `rest` のみ。骨は armature-local の rest 座標、`matrix_world` は現在の object-local→world 変換として意味を分けて記録する。
  world／pose の骨座標や動作フレームは評価しない。
  起動では、次の 2 つを渡す。
  - `--factory-startup`: 利用者の設定・アドオン・起動用 .blend を読まず、PC ごとの差を結果に入れない。
  - `--python-exit-code 1`: `--python` より前に置く。
  
  起動の argv・版の記録・観測の整形は、代役のプロセスでテストした。
  
  2026-11-06 に、Windows の Blender 4.5.15 で次を確かめた（実測）。
  - スクリプトの実行（終了コード 0）、例外での終了コード 1、UTF-8 のログ（日本語も）、版の記録。
  - 既定のシーン（Camera・Cube・Light）の観測。
  - Blender の Python は環境変数を見ない（ignore_environment）。Windows でも UTF-8 モードで動く。
  
  骨やウェイトのあるキャラの観測は、まだ本物の Blender で確かめていない。
  Windows で `model3d` の CLI の出力をファイルへ回すと、cp932 で書かれる（別件）。
- **生成器・リギング**（`external.run`）: 設定 JSON（`docs/adapters.md` と同じ約束。`tool`・`version`・`argv`、任意で `license`・`weights`）。
  **設定の argv はローカルで実行するプログラム**なので、recipe が参照する設定は自分で中身を確かめたものだけを使う（シェルは通さず、
  実行ファイルは存在する絶対パスに限る）。Hunyuan3D-2mv・TRELLIS.2・SkinTokens などの候補は、この接続口で別プロセスとして呼ぶ想定。
  本体はこのリポジトリに入れない。timeout で止めたときも、そこまでの出力は `external.log` に残る。
- **REA**（`model3d rea report analysis_report.json out.json` / `model3d rea summary summary.json out.json` /
  `model3d rea pyc evidence-summary.json out.json`）: REA 6.4.0（commit `a56f7bb6…`）の解析報告と実験の集計、6.8 系の
  parsePythonPyc の根拠（TripoSR の 8 ファイル・84 code object の記号と位置）を、工程の `evidence`（observed / inferred / unknown /
  unavailable）に写す。pyc の根拠は、REA が確かめた記号（rea）と人が原本を読んだ契約（oss）を分けて写す。
  coverage が unknown のグラフは unknown のまま。集計に比較の結果（`final_all_pass` か `passed`/`total`）が無いツールの比較は
  unknown にし、観測にしない。REA の JS/TS 意味解析と 6.8 系 Python code object の構造解析は区別する。3D 専用の provider は無く、
  この CLI は保存済みの解析結果を変換する入口であり、REA 本体の解析実行や制作工程の自動回復は行わない。
- 2026-11-03 の再構成の実験一式（Blockbench の原本・再構成コード・REA の生出力）は**リポジトリに入れない**。Blockbench 由来の部分が
  GPL-3.0-or-later で、このリポジトリの LICENSE（Apache-2.0、2026-11-06 に決定）での配布と両立しないため。JSCAD（MIT）の原本出力の合成部品だけをテストに取り込んだ。

## 推定した 3D の点を整える

`motion.prepare_keypoints` は、動画から推定した人の 3D の点を、キャラに載せる前に整える（ツールキットの段 1 の最初の工程）。

**入力**: SAM 3D Body の出力の npz。
- `pred_keypoints_3d`（コマ × 70 × 3。MHR70 の並び）と `pred_cam_t`（コマ × 3）を使う。カメラ座標（x 右・y 下・z 前、m）。
- あれば次も読み、報告に残す。
  - `focal_length` の中央値
  - `valid` が偽のコマ数
  - `used_full_image_box`（人が見つからず画像全体を使ったコマ）の数

**出力**:
- `points`: npz。中身は 3 つ。
  - `points`（コマ × 70 × 3、float32。x 右・y 奥（カメラから遠ざかる向き）・z 上、m、床が z = 0）
  - `stance`（コマ × 2。左・右の接地の重み）
  - `focal`
- `report`: JSON（`model3d-prepared-keypoints/0.1`）。中身は次のとおり。
  - 測った床の傾きと使った傾き、床に合わせて下げた量
  - 跳ねとして置き換えた点の数
  - 足ごとの接地の区間、足首の最大の 1 コマの移動

**手順**:
1. 腰の中心からの相対位置で、前後 3 コマの中央値から `spike`（既定 0.03 m）より飛び出した点を中央値に置き換える。端のコマは同じコマを繰り返して数える。
2. Savitzky–Golay（2 次）でならす。窓は 5 コマで、奥行き（y）だけ `depth_window`（既定 11）。腰の中心と腰からの相対位置を別々にならす。
3. 足の最下点に床の平面を当て（高い方の点を外しながら 8 回）、床が水平になるよう全体を回し、足の最下点の 15 パーセンタイルを z = 0 に置く。`pitch_deg` を渡すと、前後の傾きはその値を使う（測った値も報告に残す）。
   - 残した点で平面が決まらなくなったら（点が足りない、1 本の直線の上、残差がそろって 1 点も残らない）、外すのをやめて直前の当てはめを使う。
   - 足が止まっていて足の点が 1 本の直線の上にしかないと、前後の傾きは測れない。そのときは `pitch_deg` が要り、左右の傾きと高さだけを当てる（報告の `measured_pitch_deg` は null）。`pitch_deg` が無ければ止める。
4. `torso_back_deg` だけ、腰より上（MHR70 の 0〜8・21〜69）を腰の中心のまわりに起こし、上の端をカメラから遠ざける。
5. 接地の区間で足（足首と足の 3 点）を横に動かさず、区間の中央値の位置に止める。区間の前後 `blend_frames`（既定 4）でなめらかにつなぎ、膝は脚の長さを保って解き直す。
   - `contact: absolute`（既定）: 足の最下点が床から `contact_height`（既定 0.05 m）未満なら接地。
   - `contact: relative`: 低い方の足より `contact_height` 未満なら接地。
   - `step_split`（m）: 接地の間に足が止めた位置からその距離（奥行きは半分に数える）より動いたら区間を分け、新しい位置で止め直す。
   - `min_span_frames`（既定 4）より短い区間は止めない。
   - 止めた足首が脚の届く長さを外れるコマ（止めた区間の中で腰が動いた）は、足全体を股関節の方へまっすぐ、外れた分だけ動かす。
     2 本の骨の長さを保つため（足首を残して膝の計算だけを縮めると、脛が伸びる）。
     動かしたコマ数と最大の距離は、報告の `contact.pulled_in` に残る。大きいときは止める区間が長すぎるので、`step_split` を使う。

値はクリップごとに recipe で決める。この工程は、決めた量の処理だけを行い、推定の良し悪しは判定しない。

**SAM 3D Body の推定の癖**（リポジトリ外の試作で、AI で生成した 1 人の踊り手の 5 クリップを推定した値。実測の正解と比べたものではない）:
- **奥行き方向にほとんど動かない踊りでは、床の平面の前後の傾きが 10° 以上ずれた**（足の点が奥行きに 23 cm しか広がらないクリップで −17.9°。立った脚が鉛直になり、重心が足の上に来るのは −2〜−7°）。そうしたクリップでは `pitch_deg` で決める。
- **上半身が、向きによらずカメラ側へ 10〜19° 倒れて出た**。後ろ向きのコマでは反り返りに見える。`torso_back_deg` 12 で起こした。
- **立っているだけの所で、体ごと約 13 コマごとに 5〜9 cm 上下した**。`absolute` だと両足が離れたと判定され、足の向きが揺れる。跳ばないクリップでは `relative` を使った。
- **接地の区間が長いと、足が平均の位置に止まって足幅が広がった**（16 → 37 cm）。`step_split` 0.06 で一歩ごとに止め直すと、推定の足幅に戻った。足の速さで止める区間を決める方法は、足が滑ったので採らなかった。
- 奥行きの揺れは横の 2〜3 倍だった。画角はクリップの 9 コマの中央値で固定して推定した（コマごとだと焦点距離が 1164〜1423 px で揺れ、奥行きもそれに引きずられた）。
- そのほか、移し替えで直す癖があった（この工程の外）。
  - つま先の点が不安定で、足が膝から 40〜60° 開いた。
  - 腰の左右の揺れは、帯を色で追った値の約 6 割しか出なかった。
  - 横顔での首の振りは約 60° に留まった（元は 75〜85°）。
  - 速い拳の回しは、拳が離れて開いた。

**確かめたこと**:
- 合成の 70 点で、手順の各段を確かめた（`tests/test_m3d_motion.py`）。
  - 1 コマの跳ね、傾けた床の戻り、pitch の上書き、上体の角度
  - 体ごとの上下での `relative`、一歩の止め直し、recipe での実行と再利用、悪い入力の拒否
  - Savitzky–Golay は、scipy の `savgol_filter`（`mode="interp"`）と端まで一致する（scipy がある環境で）。
- リポジトリ外の 32 秒の試作の 3 クリップ（312・88・101 コマ）で、同じ値を渡すと、試作の出力と一致した（2026-11-06）。
  違いは次の 2 つだけ。
  - 端の 6 コマほど: 試作が中央値の端を 0 で埋めていたため（最大 87 mm ずれていた）。端の埋め方をそろえると、差は 0.0 mm。
  - 止めた足首が脚の届く長さを外れたコマ（3 クリップで計 8 コマ）: 試作は脛を最大 7.1 mm 伸ばしていた。ここでは足を、外れた分だけ（同じく最大 7.1 mm）寄せる。

## 整えた点の動きを測る

`check.motion` は、`motion.prepare_keypoints` が書いた points（npz）の動きを数で測る。検査の例外（AGENTS.md）として、構造と数だけを報告し、動きの良し悪しは判定しない。

**測るもの**（左右それぞれ。`frames: [開始, 終了)` で区間を絞れる）:
- **膝とつま先の食い違い**: 股関節→足首の軸のまわりで、膝の出ている向きと足の向き（かかと→つま先）の角度。膝を `bend_min_deg`（既定 25°）以上曲げたコマだけを数える（まっすぐな脚では膝の向きが決まらない）。
  - 中央値・p90・最大・悪い順のコマを残す。
  - 移し替えはこの 2 つの向きで太ももと脛をひねるので、食い違いが大きいと脚がねじれて見える。
- **接地中の足の滑り**: 接地のコマのうち、足の中心が水平に `skate_speed`（既定 0.2 m/s、`fps` 既定 30）より速く動いたコマの割合と、接地中の最大の速さ。
  - 接地は points の `stance`（> 0.5）を使う。無ければ、足の最下点が `contact_height`（既定 0.03 m）未満のコマ。
  - 速さと 1 コマの移動は区間で切る前に数えるので、区間の最初のコマも前のコマと比べる。クリップの最初のコマは前が無いので数えない（`measured_contact_frames`）。
  - `step_split` を使った points では、一歩ごとに止め直す間のコマも接地に入る。
- **床へのめり込み**: 接地中の足の最下点の、床（z = 0）より下の深さの最大と、`floor_tolerance`（既定 0.005 m）より深いコマの数。
  - 整える工程は床を足の最下点の 15 パーセンタイルに置くので、推定の上下の揺れの分は必ず出る。
- **1 コマの移動**: 腰の中心から見た足首・手首・鼻の、1 コマの移動の最大とそのコマ。腰の中心そのものの移動も残す。
- **骨の長さのぶれ**: 太もも・脛・上腕・前腕の長さの (p95 − p5) / 中央値。リグの骨の長さは一定なので、大きいと移し替えで位置が合わない。

**合否**: しきい値を 1 つでも渡したときだけ出す（`max_swivel_deg`・`max_skate_fraction`・`max_floor_depth_m`・`max_step_m`・`max_length_spread`）。
- 渡さなければ `pass` は null（測っただけ）で、工程は `needs_review` になる。`report_only: true` なら測るだけで済む。
- 不合格は `needs_review` になり、`strict: true` なら止める。

**確かめたこと**:
- 合成の 70 点で、5 つの量を確かめた。
  - 脚の軸のまわりに 70° 回したつま先は 70°。
  - 接地中に 30 cm 滑らせた足、4 cm めり込ませた足、1 コマ 20 cm 跳ばせた手首、1 コマおきに 15% 伸ばした前腕を、それぞれ不合格にする。
- リポジトリ外の試作の点（32 秒の 2 クリップ）で、膝とつま先の食い違いが、試作の測り方と同じ数になった（曲げたコマの数・中央値・悪い順のコマと角度）。
- 同じ 5 クリップの値（測っただけ。推定の値で、実測の正解と比べたものではない）:

  | 量 | 値 |
  |---|---|
  | 膝とつま先の食い違い | 最大 14〜34° |
  | 接地中の足の滑り | 7〜23% |
  | 床へのめり込み | 最大 5.5 cm |
  | 1 コマの手首の移動 | 最大 27 cm |
  | 前腕の長さのぶれ | 13〜27% |

## 点を手直しする

`motion.edit_keypoints` は、推定が届かない動きを、整えた 70 点の上で作り直す。
- 対象は、真横まで振り向く顔、画面の端や体の陰で開いた指、速い拳の回しなど。
- 値は recipe で決める。この工程は、決めた量の処理だけを行う。
- `edits` に手直しを順に並べると、前の手直しの結果に次の手直しを当てる（先に全部の形を確かめてから直す）。

区間は `frames: [開始, 終了)`（終了のコマは含まない）。その前後の出入りのコマ（`fade`、顔は `fade_in`・`fade_out`）で、重みが smoothstep で 0 から 1 へ上がり、また下がる。クリップの端では出入りを詰める。

| kind | 中身 | キー（既定） |
|---|---|---|
| `head_turn` | 頭の点（鼻・両目・両耳）を、両耳の中点を通る鉛直の軸のまわりに `degrees` 回す。+ は上から見て反時計回りで、顔が +x の方へ向く。移し替えは頭の向きを鼻と両耳から決めるので、首と頭が付いてくる | `degrees`（必須、±180）、`fade_in`（0）、`fade_out`（0） |
| `fist` | 片手（`side`: `left`・`right`）の指を握りの形にする。手首の位置と手の向き（手首 → 中指の付け根、人差し指と小指の付け根）は推定のまま | `side`（必須）、`fade`（3） |
| `forearm_roll` | 両の拳が胸の前で回り合う動きを作り直す。詳しくは下 | `stack`（必須）、ほかは下 |

`forearm_roll` の中身:
- **胸の座標**: 肩の中点を原点に、前・体の左・上の 3 つの向きを取る。
- **回る中心**: 推定の両手首の中点の前と上を `sigma`（5）コマでならしたもの。横は `lateral`（−0.06 m）に置き、`forward_add`・`up_add`（0）を足す。
- **両の拳**: 中心のまわりの縦長の輪に、反対向きに置く。
  - 輪の大きさは、上下 `radius`（0.06 m）・横 `radius_lat`（0.035 m）。前へ `tilt_deg`（25°）傾ける。
  - 半周を `half`（7.1）コマで回す。`stack` のコマで右の拳が下に来る。`direction`（1 か −1）で回る向きを決める。
- **`elbows`（`ik`）**:
  - `ik`: 肩から拳までを腕の長さで毎コマ解く（腕全体が動く）。
  - `fixed`: 肘を、手が回る中心にあるときの位置に止める（胸に対して動かない。中心は胸の座標で一定）。前腕だけを肘のまわりに回す。
- **`palm`（`down`）**:
  - `down`: 手のひらを下と内向きにする。
  - `in`: 内向きで親指が上の縦の拳にし、`palm_down_deg`（20°）だけ下へ傾ける。
- **長さ**: 腕と手の長さは、クリップ全体の中央値。
- **報告**: `fixed` では、肘のぶれと前腕の振りの最大も報告に残す。

**確かめたこと**:
- 合成の 70 点で、次を確かめた（`tests/test_m3d_motion.py`）。
  - 出入りの重み
  - 顔の向き: 全部効かせるコマで 30°、途中で 15°。頭の点の両耳からの距離は保たれ、ほかの点は動かない。
  - 握り: 指はみな手の長さの 1.25 倍の中に入り、手首と反対の手は動かない。
  - 拳の回し: 前腕の長さが保たれ、`ik` では上腕も保たれ、`fixed` では肘のぶれが 1 mm 未満。
  - recipe での実行、悪い手直しの拒否
- リポジトリ外の 32 秒の試作で、1 クリップに 3 つの手直しを当てた結果が、試作の点と一致した（285 コマ、差の最大 0.0002 mm）。
  - 拳の回し: 肘を止め、縦の拳、輪 7.2 × 4.5 cm を 20° 傾ける
  - 顔の向き: −22°
  - 握り: 右手

## 点をキャラの骨へ移す

`motion.retarget` は、整えた 70 点（`motion.prepare_keypoints` の points。手直しした後のものでもよい）を、Mixamo 式の骨（`mixamorig:Hips` など。接頭辞は `bone_prefix`）と重みを持つキャラの .blend へ移す。
- Blender の中で動く台本 `blender_adapter/retarget.py` を、model3d の Blender の起動（`--factory-startup`）で呼ぶ。
- 実行ファイルは、パラメーター `blender` か `model3d run --tool blender=PATH` で渡す。

**骨の向きの決め方**（リポジトリ外の 32 秒の試作の手順）:
- **腰**: 股関節の左右と、腰の中心 → 肩の中心の上向きで向きを決める。位置は、踊り手とキャラの脚の長さの比で縮めた腰の中心。
- **背骨 3 本**: 上向きに沿わせる。左右は、腰と肩の左右を 2:1・1:2・0:1 で混ぜる。
- **首と頭**: 両耳と鼻から、顔の上向きと左右を取る。頭の上下の基準角 `pitch0_deg`（既定はクリップの中央値）を引く。踊りの頷きで基準がずれないよう、クリップをまたいで固定できる。
- **腕**: 上腕を肘へ、前腕を手首へ向ける。ひねりは、肘の曲がり具合で「前腕の向き」と「手の左右（人差し指 → 小指の付け根）」を混ぜる。
- **指**: 各指の 3 本の骨を、点の並びに沿わせる（指の骨が無いリグでは飛ばす）。
- **脚**: キャラの太もも・脛の長さで、縮めた足首の位置へ IK で届かせる。膝は点の膝の向きに曲げる。
  - 接地のコマ（points の `stance`）は、足を床に平らに置く。
  - 床は、接地中の足首の高さの中央値を、キャラの足首の高さに合わせて決める。
- **足の向き**（`foot_yaw`）: `toes` は親指 → 小指の線。`heel` はかかと → つま先の向きで、推定のつま先の点が不安定なときに使う。

**刺さりの直し**（`resolve: true`）:
- 手・前腕の頂点が胴（腰・背骨・首・頭・太ももの骨に主に付いた面）の内側に入ったコマで、直す。
  - 前腕が入ったときは、肩〜手首の軸のまわりに肘を外へ振る。
  - 手が入ったときは、手首を外へ押して肩〜肘を解き直す。
- 内外は、骨の軸から外向きに飛ばした光線（BVH）で見る。
- `smooth_arms: true` は、直したコマの前後で、肩から見た肘と手首の通り道をならす。
- 入力 `skip_vertices`（真偽の配列の npz）の頂点は、胴の面として数えない（髪の前に手があるだけで内側と判定しない）。頂点の数が合うメッシュにだけ当てる。

**出力**:
- `blend`: 骨に動きを打った .blend。シーンの `model3d_points_frames` に、各コマが points の何コマ目かを残す（後の工程が接地の印を合わせるため）。
- `pose`: コマごとの骨の行列の npz（`names`・`rest`・`tail`・`pose`・`world`・`frames`）。
- `report`: `model3d-retarget/0.1`。
  - 縮めた比、頭の基準角、直したコマ
  - `check: true` のときは、コマごとの刺さり・頭と胸の角度・腕のひねりの跳び

**限界**:
- 骨の名前は Mixamo 式に限る（役割の地図からの対応は未対応）。
- 接地の重みが無い points では、床をつま先・足首の骨の高さから決める。その差の値（1〜8.5 cm）は人の足の目安。
- 刺さりの直しは、腕の頂点を最初の 2 つのメッシュから取る（試作と同じ）。

**確かめたこと**:
- CI（Blender なし）では、台本の引数と、工程の受け渡しを確かめた（代役の Blender）。
  - points・character・選んだオプションが Blender へ渡る。
  - 出力がそろう、毎回実行する、悪い設定を止める。
- 2026-11-06 に Windows の Blender 4.5.15 で、リポジトリ外の試作の 1 クリップ（101 コマ・65 本の骨）を、試作と同じ入力とオプションで移した。
  - オプション: `pitch0_deg` 35、`foot_yaw` heel、`resolve`・`smooth_arms`・`check`、髪とスカートの頂点を外す。
  - 結果は試作と一致した。
    - 骨の行列の差の最大: 回転の成分 1.8e-5、位置 4.6 µm
    - 直したコマ: 34
    - 検査の要約: 同じ
  - 101 コマで約 96 秒。

## 前腕のねじれを分け直す

`motion.twist_split` は、骨へ移した動き（`motion.retarget` の blend）の前腕のねじれを、肘と手首に半分ずつ持たせ直す。
- 骨の向きを点へ合わせると、前腕が自分の軸のまわりに大きく回り、手がそれを逆に回して打ち消すことがある。そうなると肘と手首で皮膚がねじれてつぶれる。
- ねじれは、骨の回転を「曲がり × 骨の軸（Y）のまわりの回転」に分けたときの、軸のまわりの角度。
- 肘と手首のねじれの合計を −180〜180° に戻し、前腕を軸のまわりに回し直して半分ずつ持たせる。手はその分を逆に回すので、**手の向き・前腕の向き・指先の位置は変わらない**。
- 扱えるのは、手の骨の親が前腕の骨で、両方の回転が四元数のリグだけ（違えば止まる）。
- 直した後に、手の向き・前腕の向き・指先（人差し指の先の骨。無ければ手の骨の先）の位置を、直す前と比べる。
  - 許容（向きは 0.01°、指先は 0.1 mm）を超えたら工程を止める。
  - 前腕や手に IK などの拘束やドライバーが掛かっているリグで起こりうる。
- `frames` は `a:b`（b を含む）。既定はシーンの開始〜終了。
  - 範囲の外のコマは元のままなので、範囲の端でねじれの分け方が切り替わる。動き全体に当てるのが基本。
- 骨の名前は `bone_prefix` + Mixamo の名前（既定 `mixamorig:`）。

**出力**:
- `blend`: 範囲のコマで、前腕と手の回転のキーを打ち直した .blend。
- `report`: `model3d-twist-split/0.1`。
  - 左右ごとの、肘と手首のねじれの大きさの中央値（直す前・後）と、合計の半分の最大
  - 変わっていないことの確かめ（`unchanged`。許容と比べるので丸めない）

**確かめたこと**:
- CI（Blender なし）では、台本の引数・範囲の読み方・工程の受け渡しを、代役の Blender で確かめた。許容を超えたら止まること（丸める前の値で比べること）も確かめた。
- 2026-11-06 に Windows の Blender 4.5.15 で、`motion.retarget` を確かめたのと同じクリップ（101 コマ・65 本の骨）に当てた。入力は、試作の手順で腰の高さを直した後の .blend。
  - 結果はリポジトリ外の試作と一致した（骨の行列の差 0）。
  - 変わっていないことの確かめ: 手の向き 0.00004°、前腕の向き 0.00002°、指先 0.0004 mm。
  - ねじれの大きさの中央値（直す前 → 後）:
    - 左: 肘 12.22° → 8.78°、手首 12.09° → 8.45°
    - 右: 肘 4.22° → 3.17°、手首 5.88° → 3.30°
  - 合計の半分の最大: 左 27.92°、右 20.35°。
  - Blender の実行は 101 コマで約 2 秒。
  - 指の骨を 40 本外したリグ（10〜20 コマだけ）でも動いた。左手の親を上腕に付け替えたリグは、最初の確かめで止まった。

## 足を床に着ける

`motion.ground_feet` は、骨へ移した動き（`motion.retarget` の blend）で、接地のコマに足の一番低い点が床（z=0）に着くよう、腰の高さを足す。
- 移し替えは足首の高さで床に合わせる。それでも、靴の底やかかとの厚みのぶんだけ、足が床に沈む・浮くことがある。
- **足の点**:
  - `foot_objects`（足のメッシュの名前の配列）を渡せば、その全頂点。
  - 無ければ、足の骨（`Foot`・`ToeBase`・`Toe_End`）の重みがいちばん大きい頂点と、足の骨に親子付けしたメッシュの全頂点。重みは骨の名前の頂点グループだけを見る。レンダーに出さないメッシュは選ばない（物体とコレクションの `hide_render`、ビューレイヤーでの除外を見るので、コレクションで隠した着替えの衣装なども選ばない）。
  - 番号で選んだ頂点は、頂点の数を変えるモディファイアを止めて測る（止めたものは報告の `feet.modifiers_off`）。
- **高さの決め方**（リポジトリ外の試作の手順）:
  1. 接地のコマ（points の `stance` で、どちらかの足が 0.5 より大きいコマ）では、足の一番低い点を床へ合わせる高さにする。
  2. 接地の無いコマ（跳んでいる所）は、前後の接地のコマからつなぐ。
  3. 急に上下しないよう、ガウスでならす（幅 `smooth_sigma_frames`、既定 1.5 コマ。±2.5σ で打ち切る）。
  4. ならした後も `allow_sink_mm`（既定 3 mm）より深く沈むコマは、その分の 1.5 倍をならして足す。
- 腰の骨の位置を世界の上向きに動かしてキーを打つ（腰の回転と、ほかの骨は変えない）。
- 直した後に足の一番低い点を測り直す。腰を動かした量と足が動いた量の差が 0.1 mm を超えたら、工程を止める。足が腰の子でない・脚に IK などの拘束が掛かっているリグで起こる。
- コマは .blend のシーンの開始〜終了。.blend の各コマが points の何コマ目かは、次の順で決める:
  1. `first_points_frame`（.blend の最初のコマ。続いたコマとみなす）
  2. `motion.retarget` がシーンに残した `model3d_points_frames`
  3. コマの数が同じなら 0 から
  - どれでも決まらないときは止まる。
- 骨の名前は `bone_prefix` + Mixamo の名前（既定 `mixamorig:`）。

**出力**:
- `blend`: 腰の位置のキーを打ち直した .blend。
- `report`: `model3d-ground-feet/0.1`。
  - コマの対応、足の点の選び方と頂点の数、接地のコマの数
  - 腰に足した高さ（最小・最大・1 コマの最大の変化）
  - 足の一番低い点の、直す前・後（中央値・最小・最大と、そのコマ。1 cm より沈むコマ、3 cm・6 cm より浮くコマの数）
  - 腰を動かした量と足が動いた量の差（`lift_check_mm`。許容と比べるので丸めない）

**限界**:
- 接地の印は points のもの（`motion.prepare_keypoints` の推定）をそのまま使う。
- ならすので、急に沈む 1〜2 コマは残ることがある（下の確かめでは、直した後の最も深い沈みが 6.5 mm）。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 高さの計算: 試作の式と一致する。沈んだ足を床まで上げる。接地の無いコマを前後からつなぐ。接地が無ければ沈んだ足だけを上げる。
  - コマの対応の決め方。
  - 台本の引数と工程の受け渡し、許容を超えたら止まること（丸める前の値で比べること）。
- 2026-11-06 に Windows の Blender 4.5.15 で、`motion.retarget` を確かめたのと同じクリップ（101 コマ）で確かめた。
  - 試作が足を測ったキャラ（部品を組んだもの）に、試作と同じ足のメッシュ 2 つを `foot_objects` で渡して当てた。
    - 直す前の足の一番低い点は、試作の測定と同じだった（中央値 −11.70 mm、最小 −79.16 mm、最大 8.07 mm）。
    - 腰に足した高さは試作と一致した（差 0。−1.20〜76.47 mm、1 コマの最大の変化 15.50 mm）。
    - 共通の 65 本の骨の行列の差は、位置 1.5 µm・回転 7.2e-6。入力の 2 つのファイル（試作が足を測ったキャラと、直した動き）のあいだに、もとからある差。
  - `foot_objects` を渡さず、骨の重みで選んでも、同じ高さになった。
  - 1 cm より沈むコマは 56 → 0 になった。直した後の足の一番低い点は、中央値 0.04 mm、最小 −6.51 mm、最大 15.0 mm。
  - `motion.retarget` の後に続けて当てると、コマの対応をシーンの記録から読んだ（全コマと、10〜40 コマだけの両方）。
    - 1 つのメッシュのキャラでも、骨の重みで足の点を選べた。
    - 記録を足しても、`motion.retarget` の骨の行列は前と同じだった（差 0）。
  - 左足を IK で 1 コマ目の場所に止めたリグは、腰を動かした量と足が動いた量の差（73 mm）で止まった。
  - Blender の実行は 101 コマで約 5 秒。
- 2026-11-07 に Windows の Blender 4.5.15 で、コレクションで見え方を変えた合成の場面で、骨の重みで選ぶ足のメッシュを確かめた。場面には、隠したコレクション・その子・ビューレイヤーで除外したコレクションに、別の靴を置いた。
  - 見える 2 つだけを選び、腰を 20 mm 下げた。
  - 直す前（物体の `hide_render` だけを見る）は、隠した大きな靴と除外した靴まで選び、腰を 40 mm 上げる形だった。

## 手を体から浮かせる

`motion.push_hands` は、骨へ移した動き（`motion.retarget` の blend）で、手が体（胴・腰まわり・脚）に沈むコマに、二の腕を肩のまわりに少し回して、手を体の面から浮かせる。肘から先の曲げと手の形は変えない。
- `motion.retarget` の `resolve` は、移すときに手・前腕の胴への刺さりを直す。こちらは移した後のキャラの面（スカートや脚も含む）で、残った沈みを直す。
- **手の点**: 手の骨（`Hand`）とその先の骨（指）の重みがいちばん大きい頂点（左右別）。
- **体の点**: 腰の骨から下がる骨のうち、首・肩・足から先を除いた骨の重みがいちばん大きい頂点。
  - 腰・背骨・太もも・すねと、その下に足した骨（スカートの骨など）が入る。
  - `body_objects`（メッシュの名前の配列）を渡せば、そのメッシュの全頂点にできる。
  - 重みは骨の名前の頂点グループだけを見る。番号で選んだ頂点は、頂点の数を変えるモディファイアを止めて測る。
  - 骨の重みで選ぶときは、レンダーに出すメッシュだけを見る（物体とコレクションの `hide_render`、ビューレイヤーでの除外を見る）。材質で透明にしただけの形（当たりの写しなど）は体に入るので、外すときは `body_objects` で体のメッシュを選ぶ。
- **動かす量**（リポジトリ外の試作の手順）:
  1. コマごとに、手の点にいちばん近い体の点を探す。体の面の法線の向きに測った距離が `gap_mm`（既定 6 mm。輪郭線が見えるように）より小さい点の、足りない量を測る。体の面から `reach_mm`（既定 40 mm）より遠い点は数えない。近い点は、手の点の囲みを `reach_mm`（5 cm より小さければ 5 cm）だけ広げた中から探す。
  2. 2 mm より足りない点が 15 未満なら、手のひらの丸みの誤差とみなして動かさない。
  3. 足りない量で重みをつけた法線の平均の向きに、足りない量（向きの傾きで割り増し）の 98% 点だけ手首を動かす。1 回に `cap_mm`（既定 40 mm）まで。
  4. コマの前後でならす。前後 3 コマでいちばん大きい量を広げてから、σ 1.5 コマのガウス（±3σ）でならす。
  5. 二の腕の骨を肩のまわりに回して、手首をその量の腕に垂直な分だけ動かす。回しても手首は腕に沿っては動かないので、腕に沿う分は除いて角度を決める（30° まで）。
- **試作との違い**: 試作は、5 の角度を量の長さ全部で決めていた。腕に沿う押しでは、わずかな横の分の向きに手首を大きく振り、その分の向きがコマで入れ替わると跳んだ。公開版は腕に垂直な分だけを使う（腕に垂直な押しでは同じ）。
- これを `passes`（既定 2）回くり返し、最後に測り直す。
- コマは .blend のシーンの開始〜終了。骨の名前は `bone_prefix` + Mixamo の名前。二の腕の回転は四元数に限る。

**出力**:
- `blend`: 二の腕の回転のキーを打ち直した .blend。
- `report`: `model3d-push-hands/0.1`。
  - 選んだ点（メッシュごとの頂点の数）と、止めたモディファイア
  - 回ごと・左右ごとの、動かしたコマの数・いちばん大きい動き・いちばん深い足りなさ
  - 体の面の内側へ 2 mm より深く入った手の点の、直す前・後。その点が 50 を超えるコマの数、1 コマの最大の点の数、いちばん深い深さとそのコマ

**限界**:
- 腕を肩から回すだけなので、腕に沿う向きの沈みは直らない。
- 1 回の動きに上限があるので、深い沈みは残ることがある。
- 測るのに時間がかかる。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 骨の選び方（止める骨とその下を除く）。
  - 手首を動かす量とならしが、試作の式と一致する（乱数の点で、値まで同じ）。
  - 平らな面に沈んだ手を、法線の向きに沈み + 隙間だけ押し出す。1 回の上限で止まる。点が少ない・遠いときは動かさない。
  - 回す角度は、量の腕に垂直な分で決まる。腕に沿う 40 mm の押しに 0.04 mm の横の分があっても、手首は 0.04 mm しか動かない（向きが入れ替わっても）。
  - 台本の引数と工程の受け渡し、悪い設定を止めること（代役の Blender）。
- 2026-11-06 に Windows の Blender 4.5.15 で、`motion.retarget` を確かめたのと同じクリップ（101 コマ）で確かめた。
  - 試作が測ったキャラ（部品を組んだもの）で、試作と同じ点（体の部品 4 つ、腕の部品の「手」の印）を渡して測った。
    - ならした後の手首を動かす量は、試作が残した値と一致した（差 0）。動かしたコマは左 73・右 57。いちばん深い足りなさは左 46.0 mm・右 45.9 mm。
    - 試作と同じ回し方（量の長さ全部で角度を決める）では、骨の行列も試作と一致した（差 0）。
    - 腕に沿う分を除く回し方では、手首の位置が、その分だけ試作と違った（1 mm を超えるコマは左 35・右 24、最大は左 22.3 mm・右 18.2 mm）。
      試作の量と腕の向きから計算した、除いた横の動き（最大は左 22.2 mm・右 18.2 mm）と合う。
      このクリップの押しは、長さのうち腕に垂直な分の割合が中央値で 0.92（左）・0.95（右）。いちばん小さいコマは 0.07 で、ほぼ腕に沿う押しだった。
    - 押しが足した手首の動きの、1 コマごとの変化の最大: 試作の回し方は左 29.4 mm・右 24.6 mm（10 mm を超えるコマは左 5・右 13）。
      公開版は左 12.2 mm・右 20.0 mm（同じく左 2・右 9）。
    - 101 コマを 1 回測るのに約 60 秒。
  - 同じキャラに、既定（骨の重みで選ぶ、2 回）で工程として当てた。体には 12 のメッシュ、手には腕の部品の頂点が入った。
    - 体の面の内側へ 2 mm より深い手の点が 50 を超えるコマ: 左 40 → 9、右 16 → 6。
    - いちばん深い深さ: 左 40.0 → 40.0 mm、右 40.0 → 30.3 mm（深い所は 1 回の上限と、腕に沿う向きの沈みで残る）。
    - 3 回測って約 5 分（約 100 万頂点）。
  - 公開版の `motion.retarget` と `motion.ground_feet` を通した 1 つのメッシュのキャラ（約 6 万頂点）でも動いた。
    - 50 を超えるコマ: 左 31 → 0、右 14 → 10。約 19 秒。
    - 右は、試作の回し方（14 → 4）より多く残った。腕に沿う押しを横へ大きく振る分で、たまたま外へ出ていたコマがあった。
    - `reach_mm` 80 では、より深い点まで数えた（直す前の左のいちばん深い深さ 77.9 mm）。50 を超えるコマは、左 31 → 6、右 14 → 3。
- 2026-11-07 に Windows の Blender 4.5.15 で、コレクションで見え方を変えた合成の場面（[足を床に着ける](#足を床に着ける) と同じ）で、骨の重みで選ぶ点を確かめた。
  - 体は見える 2 つ（背骨・太もも）、手は見える左右 1 つずつだけを選んだ。
  - 直す前は、隠した体・脚・手と、除外した脚も入った。

## 鎖の骨をばねで揺らす

`motion.spring_bones` は、骨へ移した動き（blend）で、鎖の骨（髪など）を、ばねで体の動きに遅れて揺らし、回転のキーに焼き付ける。
- **鎖**: `chains` に根元の骨の名前を並べる。根元から、子が 1 本の間を毛先までたどる（枝分かれは止める）。
  - 根元より下の骨は、親につながっている（head が親の tail）こと。回転は四元数で、親の回転と拡大をそのまま受け継ぐ骨に限る。
  - どの鎖も同じ骨の数にする。違う長さの鎖は、別の工程に分ける。
  - 鎖の骨と、そこへの重みは、リグに用意しておく（足す道具は段 2）。
- **狙いの形**（`target`）:
  - `rest`（既定）: 鎖が親の骨に固く付いて動いたときの形。
  - `rest` に `follow`（骨と静止の高さの並び。例: 頭・首・背骨・腰）を渡すと、それに沿わせる。各節は、静止の高さで前後の骨の動きを線形に混ぜて付いて行く（いちばん上より上・下より下は端の骨）。背中に沿う長い髪に使う。
  - `animation`: 鎖の骨にすでにある動き。
- **ばね**（リポジトリ外の試作の手順）:
  - 節（骨の head と、毛先の骨の tail）を狙いの位置へばねで引く。固有振動数は、根元の `f_root_hz`（既定 3.2）から毛先の `f_tip_hz`（既定 1.1）へ等比で弱くする。減衰比 `damping`（既定 0.30）は、狙いの速さとの差に掛ける。根元の節は狙いに止める。
  - 1 コマを `substeps`（既定 8）に分けて進める。1 刻みごとに、次をする:
    - 狙いからのずれを隣の節とならす（`bend`、既定 0.12）。
    - 節の間の長さを、根元から毛先へ 2 回戻す。
    - `links`（鎖の番号の組）の鎖を、静止の 1.25 倍より離れないように寄せる。
    - `colliders`（2 本の骨の head を結ぶ、半径 `radius_mm` のカプセル。同じ骨にすると、その head を中心にした球）の内側の節を、`push`（既定 0.3）の割合だけ外へ出す。静止で近い節は、その近さまで許す。軸の上にちょうど乗った節は、1 刻み前の位置の向きへ（それも軸の上なら、軸と鎖の両方に垂直な向きへ）出す。
  - 踊りの前に `settle_s`（既定 1 秒）、最初の形で落ち着かせる。
  - ばねが 1 刻みに対して速すぎる設定（いちばん速いばねで 2π × 振動数 × 刻みの秒数が 1 を超える）は止める。
- **骨にする**: 狙いの骨の向きに、毛の向き（節から次の節）への最小の回転を足し、親からの回転にしてキーを打つ。根元の骨には、head をばねの根元の節に置く位置のキーも打つ。
  - 焼き付けた後に骨を測り直す。骨の向きとばねの節の向きの差が 0.01°、根元の head と根元の節の差が 0.1 mm を超えたら止める。
  - 節の間の長さは、鎖のつなぎと当たりが節を動かすぶん伸び縮みする（試作のばねの性質）。骨は長さを保つので、伸び縮みした節より先の位置は、ばねの節とずれる。その伸び縮みと位置の差は報告に残す。
- コマは .blend のシーンの開始〜終了、1 秒あたりのコマはシーンの値。アーマチュアの物体そのものが動いている .blend は止める。

**出力**:
- `blend`: 鎖の骨の回転と、根元の位置のキーを打ち直した .blend。
- `report`: `model3d-spring-bones/0.1`。
  - 鎖と設定、当たりで押した回数
  - 狙いからのずれ（節ごとの中央値・99% 点）、1 コマの動きとその変化
  - 節の間の伸び縮み、焼き付けの確かめ（許容と比べるので丸めない）、骨とばねの節の位置の差

**限界**:
- 帯の垂れ（脚の形から作る当たり、太ももへの追従、重力）と袖口は、試作の別の作りで、まだ入れていない。
- 重力はない（狙いの形の垂れ方が、そのまま残る）。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - ばねの計算が、試作の式と一致する（乱数の入力で、値まで同じ）。
  - 根元は狙いのまま動かない。節の間の長さを保つ。止まった狙いは動かさず、動く狙いには毛先ほど遅れる。当たりの外へ出す（両端が同じ骨の球でも、軸の上にちょうど乗った節でも。軸の上の節は、すぐ近くの節と同じ所に落ち着く）。鎖のつなぎで離れすぎない。
  - 付いて行く重みと混ぜ方が、試作と一致する。根元の位置のキー、骨の向きの組み立て、拡大と鏡映を除く回転、速すぎるばねを止めること。
  - 台本の引数と工程の受け渡し、悪い設定を止めること（代役の Blender）。
- 2026-11-06 に、`motion.retarget` を確かめたのと同じクリップ（101 コマ）で確かめた。
  - Blender なしで、試作が残した髪の節（8 本 × 8 節）と狙いの位置を渡すと、ばねの節は試作と一致した（差 0 m。当たりで押した回数 3873 も同じ）。
  - Windows の Blender 4.5.15 で、試作の節の位置に、8 本 × 7 本の鎖の骨を頭の下に作った。試作と同じ設定（`follow` に頭・首・背骨 3 本・腰と高さ、当たり 10 個、鎖のつなぎ）で、工程として当てた。
    - 骨の向きは、試作のばねの節の向きと一致した（最大 0.001°）。根元の位置の差は 0.0002 mm。狙いからのずれ・1 コマの動きの数も、試作の記録と同じ。
    - 試作のばねの節の間は、最大 12.3 mm 伸び、8.7 mm 縮んでいた（上の節に集まる）。骨は長さを保つので、その先の節の位置は最大 14.0 mm ずれた。
  - `follow` なし（頭に固く付く）では、腰まで届くこの髪の狙いが頭の振りで大きく動いた。当たりで押した回数は 10925、節の間の伸び縮みは最大 60.7 mm になった。背中に沿う長い髪には `follow` を使う。
  - 鎖の骨にキーが無いとき、`animation` は `rest` と同じ結果になった（差 0.008 mm）。
  - 両端が同じ骨の球（左前腕の head、半径 100 mm）を足すと、当たりで押した回数が 3873 → 4015 になった（Blender なしの計算と同じ数）。
  - Blender の実行は、1 回 約 2 秒。

## 袖口を曲げに遅れて付いて行かせる

`motion.sleeve_bones` は、骨へ移した動き（blend）で、袖口の骨を、関節の曲げに少し遅れて、一部だけ付いて行かせ、回転のキーに焼き付ける。袖口が前腕にぴったり付いて曲がると、布に見えないため。
- **骨**: `sleeves` に `{bone: 袖口の骨, follow: 曲げに付いて行く骨}` を並べる（例: 袖口の骨と前腕）。
  - 袖口の骨は親を持つこと。ふつうは `follow` の骨の親（二の腕）にし、静止は `follow` の骨と同じ所に置く。そうすると、肘で前腕と一緒に曲がる。
  - 回転は四元数で、親の回転と拡大をそのまま受け継ぐ骨に限る。袖口の骨と、そこへの重みは、リグに用意しておく（足す道具は段 2）。
- **付いて行き方**（リポジトリ外の試作の手順）:
  1. `follow` の骨が親に固く付いて動いたとき（曲げなし）の向きから、今の向きへの曲げを求める。骨の Y 軸どうしの最小の回転で、ねじれは入れない（前腕のねじれは 1 コマで大きく飛ぶことがあるため）。Y 軸がちょうど真逆（180° の曲げ）のときは、曲げなしの向きの X 軸のまわりの半回転にする。
  2. その `ratio`（既定 0.75）倍へ、減衰つきのばね（`f_hz` 既定 4.0、減衰比 `damping` 既定 0.45）で遅れて付いて行く。止まる所で軽く行きすぎる。1 コマを `substeps`（既定 8）に分けて進める。
  3. 袖口の骨を、曲げなしの向きにその回転を掛けた向きにする（静止での `follow` の骨との向きの差は保つ）。
  - ばねが 1 刻みに対して速すぎる設定は止める。
- 焼き付けた後に袖口の骨の向きを測り直し、狙いとの差が 0.01° を超えたら止める。
- コマは .blend のシーンの開始〜終了、1 秒あたりのコマはシーンの値。

**出力**:
- `blend`: 袖口の骨の回転のキーを打った .blend。
- `report`: `model3d-sleeve-bones/0.1`。袖口ごとの曲げの最大（`ratio` を掛ける前）と遅れ（中央値・最大）、設定、焼き付けの確かめ（丸めない）。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 曲げと遅れのばねが、試作の式と一致する（乱数の入力で）。
  - 骨の軸のまわりのねじれは入れない。180° の曲げは半回転になる。遅れて付いて行き、急な曲げでは軽く行きすぎてから落ち着く。報告の曲げは `ratio` が 0 でも測ったまま。
  - 回転ベクトルと行列、単精度でも 0 の近くで粗くならない角度の測り方、速すぎるばねを止めること。
  - 台本の引数と工程の受け渡し、悪い設定を止めること（代役の Blender）。
- 2026-11-07 に、`motion.retarget` を確かめたのと同じクリップ（101 コマ）で確かめた。
  - Blender なしで、試作の袖口の向きと比べると、最大 0.00027° の差だった。試作は姿勢の行列をそのまま使い、公開版は拡大のあるリグでも角度がずれないよう直交に戻してから使う、そのぶんの差。
  - Windows の Blender 4.5.15 で、試作と同じ所（前腕の head から手の head、ロール 0）に袖口の骨を作り、二の腕の子にして工程として当てた。焼き付けた向きは試作と最大 0.00027° の差、head の位置の差は 0.75 µm 以下。
  - 曲げの最大は左 109.0°・右 108.2°、遅れは中央値 3.7°・4.6°、最大 34.6°・26.0°（試作の記録と同じ）。Blender の実行は約 9 秒。

## 帯の垂れを揺らす

`motion.ribbon_bones` は、骨へ移した動き（blend）で、前に垂れる帯の鎖の骨を、重力で垂れて太ももの上を前へ乗り越えるように揺らし、回転のキーに焼き付ける。髪のばね（`motion.spring_bones`）と同じ台本で、物理だけが違う。
- **鎖**: `chains` に根元の骨の名前を並べる（たどり方と骨の決まりは `motion.spring_bones` と同じ）。帯は 1 本ずつ計算するので、鎖ごとに骨の数が違ってよい。
- **狙いの形**（`target`、`follow` は `motion.spring_bones` と同じ）:
  - `thigh`（`{bone, weight, from_height_m, span_m}`）を渡すと、`follow` の付いて行き方に、下ほど太ももの動きを混ぜる。静止の高さが `from_height_m` から `span_m` 下がるまでに、0 から `weight` へ増える。腰だけに付けると、上げた太ももに押されて帯が横へ水平に飛び出したため。
- **帯の物理**（リポジトリ外の試作の手順）:
  - 各節を「ひとつ上の節から、狙いの形の向きへ、元の長さだけ下」へばねで引く。固有振動数は根元の `f_root_hz`（既定 4.0）から先の `f_tip_hz`（既定 1.8）へ弱くする。減衰比は `damping`（既定 0.35）。点に引かないのは、太ももが前に出たとき引く先が太ももの中に入り、押し出しと引き合って帯がよじれたため。
  - 重力（`gravity`、既定 1.0 倍の 9.8 m/s²）で下がる。節の間の長さは、毎回戻す（伸び縮みしない）。
  - 当たりの中の節は、面まで押し出す。押す向きは、腰（`hips`、既定 `mixamorig:Hips`）の左右の成分を `lane`（既定 0.8）の割合だけ減らし、腰の前と上の側へ `over`（既定 0.5）の強さで寄せる。真っすぐ外へ出すと横へ飛び出し、上げた太ももの下へ押し込まれて裏へ抜けたため。
  - 押し出した分は、`inelastic`（既定 1.0）の割合だけ次の速さから除く（弾まない）。根元も `root_move_mm`（既定 25 mm）まで当たりから逃がす。
  - 押し出す距離は、カプセルの途中では軸に直角に解く。カプセルの列の本当の端（最初と最後）の丸い所に入った節は、球の面までを解く。試作は端も無限の円柱として解いていたので、端の先の節を横へ押しすぎた。`fitted` のカプセルどうしの継ぎ目は、試作どおり円柱として解く（継ぎ目も球として解くと押し出しが弱まり、この帯では 3 mm より中に残るコマが 76 → 81 に増えた）。押す向きが決まらない節（カプセルの軸の上で `over` が 0、など）は、腰の前と上の側へ（それも軸と平行なら、軸に直角な決まった向きへ）押す。
  - 当たりの位置はコマの間で補う（`interp`、既定 true）。1 コマを `substeps`（既定 8）に分けて進め、踊りの前に `settle_s`（既定 1 秒）落ち着かせる。
- **当たり**:
  - `colliders`: `motion.spring_bones` と同じカプセル。
  - `fitted`（`{bones, slices, objects, extend_first}` の配列）: 骨の並び（例: 太もも・すね・足）に沿ってメッシュを輪切りにし、断面の中心と半径（中心からの距離の 98% 点）を測る。つなぐと、太さの変わるカプセルの並びになる。骨は、肉の中心より後ろにあることが多いため。
    - `slices` は骨ごとの輪切りの位置（0〜1）。輪切りの頂点が 50 より少なければ飛ばす。
    - `extend_first`（既定 true）なら、最初の骨の最初の輪切りの太さを付け根まで伸ばす（脚の部品は付け根の近くが切れていて、輪切りの形が測れないため）。
    - メッシュは `objects` の全頂点。無ければ、その骨の重みがいちばん大きい頂点（レンダーに出すメッシュだけ。物体とコレクションの `hide_render`、ビューレイヤーでの除外を見る）。
    - 半径に `fit_margin_mm`（既定 22 mm）を足す。帯の幅の端の傾きと、面から浮かせる分。輪切りはアーマチュアの中の単位で測るので、半径はアーマチュアの拡大で世界の単位に直してから足す（軸ごとに拡大が違うアーマチュアでは止める）。
- **骨にする・確かめ**: `motion.spring_bones` と同じ（骨の向きと根元を確かめ、合わなければ止める）。

**出力**:
- `blend`: 帯の骨の回転と、根元の位置のキーを打ち直した .blend。
- `report`: `model3d-spring-bones/0.1`（`settings.model` が `ribbon`）。
  - 当たりの数と、輪切りから測った半径（世界の mm、余裕を足す前）、帯ごとの狙いからのずれ・1 コマの動き・当たりの面までの近さ（最小と、3 mm より中に入るコマの数）
  - 節の間の伸び縮み、焼き付けの確かめ、骨とばねの節の位置の差

**限界**:
- 帯の鎖の骨と、そこへの重みは、リグに用意しておく（足す道具は段 2）。
- 当たりの面までの近さは、カプセルとの近さで測る（メッシュの面とではない）。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 帯の物理が、カプセルの両端を継ぎ目として扱うと試作の式と一致する（合成の帯と、前へ上がる太ももの当たりで、値まで同じ）。本当の端の丸い所は球として解き、継ぎ目より押す量が小さい。軸の上にちょうど乗った帯の節も、`over` が 0 のとき軸の上に残らない。
  - 長さを保つ。根元は狙いのまま。重力で先が下がり、重力が無ければ狙いの形のまま。上がった太ももがあると、中に入る深さが浅く、先は前へ乗り越える。
  - 輪切りの中心と半径、付け根まで伸ばす点、頂点の少ない輪切りを飛ばすこと。カプセルの両端が骨と一緒に動くこと。
  - 太ももを混ぜる付いて行き方が試作と一致する。腰の向き。台本の引数と工程の受け渡し、悪い設定を止めること（代役の Blender）。
- 2026-11-07 に、`motion.retarget` を確かめたのと同じクリップ（101 コマ）で確かめた。
  - Blender なしで、試作の帯の静止の節・太ももを混ぜた狙い・脚の部品の輪切りの当たり（14 個）を公開版の関数で作って動かした。端も円柱として解けば、試作の帯の動きと一致した（2 本とも差 2×10⁻¹⁶ m）。
  - 本当の端を球として解く公開版では、帯 B は試作と同じ。帯 A は、コマの間の刻みで脚の当たりの端に入るぶん、最大 30 mm 違う。当たりの面までの近さ（帯 A: 最小 −26 mm、3 mm より中のコマ 76。帯 B: −7 mm、1 コマ）と、動きの急な変化の最大（9.9 cm・5.9 cm）は、試作の記録と同じ。
  - Windows の Blender 4.5.15 で、試作の帯の節に 2 本の帯の鎖の骨（5 本と 4 本）を腰の下に作り、試作と同じ設定で工程として当てた。焼き付けた帯の節は、端の解き方を試作に合わせると試作と最大 0.0016 mm の差（公開版の解き方では、帯 B は 0.0009 mm、帯 A は端のぶん最大 30 mm）。焼き付けの確かめは 0.0002°、節の間の伸び縮みは 0。約 13 秒。
  - アーマチュアの物体を 0.5 倍にしたリグでは、測った半径がちょうど半分になった（拡大 1 のリグの半径は、試作の記録と同じ 95・86・73・60 mm …）。
  - `objects` を渡さず骨の重みで脚の頂点を選んでも動いた（当たり 14 個）。ただし、脚の骨に主に付いた頂点を全部のメッシュから集める（スカート・ブーツの筒・当たりの写しも入る）ので、結果は部品を渡したときと少し違う（3 mm より中のコマ: 帯 A 60、帯 B 31）。
- 2026-11-07 に Windows の Blender 4.5.15 で、コレクションで見え方を変えた合成の場面（[足を床に着ける](#足を床に着ける) と同じ）で確かめた。骨の重みで選ぶ当たりの頂点は、見える脚の 8 個だけになった（直す前は、隠した脚と除外した脚も入って 24 個）。

## セル調の塗りと輪郭線を付ける

`look.toon` は、骨へ移した動きのキャラ（blend）に、描く前の見た目（セル調の塗りと輪郭線）を付ける。リポジトリ外の試作で、32 秒の本番を描いた見た目と同じ組み方。描く工程（カメラ・光・コマの割り当て）は次の工程。
- **メッシュ**: `meshes`（既定: アーマチュアで動く、レンダーに出すメッシュ全部）をセル調にする。レンダーに出るかは、物体とコレクションの `hide_render`、ビューレイヤーでの除外で決める（コレクションで隠した着替えの衣装などは選ばない）。`faces` に入れたメッシュは顔の塗りにする。材質に Principled BSDF が要る。
  - 当たりの写しなど、材質で隠しただけのメッシュもレンダーに出るものは既定に入るので、そのときは `meshes` を渡す。
  - 材質の枠はすべて塗る（Principled BSDF のない枠は飛ばす）。塗り方の違うメッシュ（セル調・顔・塗らないメッシュ）と分け合う材質は、塗り方ごとに写して付け替えてから書き換える（報告の `copied_materials`）。メッシュをほかの物体と分け合う（リンクした複製の）枠は、物体の側の枠に付け替える（メッシュの側を替えると、塗らない複製の材質も替わるため）。
- **セル調の塗り**（`toon`）:
  - 絵の色（Base Color）を出し、影は決まった向きの光（`light`）と面の向きの内積で 2 段に分ける。境は `threshold` ± `soft` の smoothstep（既定 0.10 ± 0.05）。
  - 影の側は、影の色（`shade`、元の色に掛ける）に、舞台の色を `tint`（既定 0.22）だけ混ぜた色を掛ける。`skin_shade` を渡すと、肌（明るく彩度の低い色）の影だけ別の色にする。光の側は `lit`（既定 0.96）倍。
  - 縁の光: 面が横を向く所（Layer Weight の Facing が `rim_from`〜`rim_to`）に、カメラから見て左は 1 つ目、右は 2 つ目の `rim_colors` を、強さ `rim_gain` でスクリーンで足す。
  - `face_attribute`（既定 `face_near`）の頂点の属性の所は、影を入れず縁の光も入れない。`weak_rim`（既定 `hand` 0.75・`norim` 0.85）の属性の所は縁の光を弱める。
  - 裏の面と `inside_attribute`（既定 `inside`）の所は `inside_color` を掛ける。全部を Emission で出し（舞台の光は受けない）、EEVEE が使う材質の出力につなぐ。
  - 元の透け（Principled BSDF の Alpha につないだ出口か、1 未満の値）は、Transparent BSDF と混ぜて残す（髪の房やまつげの切り抜き）。
- **顔の塗り**（`face`）: Principled BSDF のつやを消し（Roughness 1、Specular 0）、絵の色の `lit`（既定 0.20）倍を受け、絵の色を `emit`（既定 0.62）の強さで出す（絵に影が描いてあるため）。Roughness・Specular・Emission Strength につないだノードは外してから値を入れる。
- **輪郭線**（`outline`。反転ハル）:
  - `meshes`（既定はセル調のメッシュと同じ）ごとに写しを作る。写しは元の親子付け・変換・モディファイアの並び（骨の変形の設定も）をそのまま持つ。
  - 写しの重なる頂点を溶かし、`ratio`（メッシュごとの残す面の割合。既定 1）まで面を減らす。面を減らすのとならすのは、並びの先頭で元の面に当てる。
  - `hand_attribute`（既定 `hand`）のあるメッシュは写しをならし、その所の線を `hand_line`（既定 0.45）倍の太さにする。
    - 形状キーがあると、Blender はならしのモディファイアを当てられない。そこで、同じ頂点と辺だけの仮のメッシュで基本の形（Basis）をならし、そのずれを全部のキーに足す（キーごとの動きはそのまま。Smooth は辺だけを使うので、ならし方はキーがないときと同じ）。
  - 材質の枠ごとに線の材質を作る（面の材質の番号はそのまま）。線の材質は元の材質の写しで、面の出力だけを替える。
    - 線の色は、元の Base Color につながった色（絵・UV・頂点の色などのつなぎ方は元のまま。つながっていなければ Base Color の色）を `dark`（既定 0.12）倍・彩度 `saturation`（既定 1.25）倍した暗い色。
    - 元の透け（Alpha）は残す（髪の房やまつげの切り抜きに、線の殻を出さない）。線は切り抜きの描き方（Dithered）で描く。
    - Emission で出し、裏の面は描かない。
  - 並びの後ろ（骨の変形と、Mirror や Subdivision Surface のような形を変えるモディファイアの後）で、面を裏返す。これはジオメトリノードの共有のグループ `model3d_flip_faces` で、写しの `model3d_outline_flip` に名前を残す。元の面のままモディファイアを通すので、Solidify のように面の向きで形が変わるモディファイアも元と同じ向きで働く。
  - 並びの最後に、法線の向きへ `width_mm`（既定 3 mm）だけ太らせる。これは Displace で、名前は写しの `model3d_outline_modifier` に残す（ふつうは `out`）。描く工程が、カメラの距離に合わせて太さを書き換える。Displace は物体の中の単位で押すので、物体の拡大とシーンの単位（`scale_length`）で直した強さにし、その換算を写しの `model3d_outline_m_per_local` に残す（拡大が軸ごとに違えば平均）。写しは影を落とさず、`model3d_outline` の印を持つ。印のある .blend にもう一度当てると止める。
  - 写しは元と同じコレクションに入る。コレクションでレンダーから隠したメッシュの写しも隠れ、ビューレイヤーでの除外も元と同じになる。
  - 次のモディファイアは元の頂点の並びに結び付いている。写しの頂点を溶かし面を減らすと働かないので、これらのあるメッシュに輪郭線を付けようとすると止める。先にモディファイアを当てるか、`outline.meshes` から外す。
    - Surface Deform・Mesh Deform・Laplacian Deform・Multires・Mesh Cache・Mesh Sequence Cache・Soft Body・Cloth
    - 結び付けた形で戻す Corrective Smooth
  - 形状キーのあるメッシュは面を減らせない。`ratio` を 1 未満にすると止める。
- **共有のノードグループ** `model3d_stage_tone`（`a`・`b`・`gain`）に、縁の色と強さを置く。描く工程がコマごとに書き換える。

**出力**:
- `blend`: 塗りと輪郭線を付けた .blend。
- `report`: `model3d-toon-look/0.1`。塗ったメッシュと材質、輪郭線の写し（三角形の数・残した割合）、設定。

**確かめたこと**:
- CI（Blender なし）では、設定の重ね方、台本の引数と工程の受け渡し、悪い設定を止めることを確かめた（代役の Blender）。
- 本物の Blender で、1 つの材質をセル調・顔・塗らないメッシュが分け合う合成の場面に当てると、セル調と顔はそれぞれ写した材質で塗られ、塗らないメッシュは元の材質のまま。材質の枠が 2 つの顔は、2 つとも顔の塗りになった。
- 2026-11-07 に Windows の Blender 4.5.15 で、試作が 32 秒の本番に使ったキャラの、輪郭線を付ける前の .blend に当てた。設定は試作と同じ（部品 11 個と、背中の継ぎ・左右の筒をセル調に、部品 11 個に輪郭線、部品ごとの残す割合、縁の光 0.76〜0.86）。
  - 輪郭線の写し 11 個は、試作の本番の .blend の写しと、次がすべて同じ。
    - 30 コマ目に骨で動かして太らせた形（頂点の位置は差 0。面の向きと、面の頂点の回り順も同じ）
    - 元の頂点の位置（差 0）・手の所の線の重み・材質の値
    - 親子付けと物体の変換・骨の変形の設定
  - 違うのは面を裏返す場所だけ。試作は元の面で裏返し、公開版は並びの後ろのジオメトリノードで裏返す。
  - 試作の塗りの組み方を本番の .blend に当てたものと、公開版を当てたものを、同じカメラで 1 コマ描くと、画素まで同じ（51 万 8,400 画素で差 0）。
  - 14 個のメッシュを塗り、写し 11 個（三角形 80 万）を作るのに約 41 秒。
  - アーマチュアを 0.5 倍にし、シーンの単位を 1 Blender 単位 = 1 cm にした合成の場面でも、輪郭線の写しは元のメッシュから 3.000 mm 外に出た。
  - 合成の場面: 半球を Mirror で閉じ、親なしで Armature モディファイア（体積を保つ）で曲げた。アーマチュアには位置・向き・1.5 倍の拡大を持たせた。
    - 写しは元と同じ 482 頂点で、物体の変換も同じ。モディファイアの並びは元と同じ Mirror・Armature の後ろに、裏返す flip と太らせる out が付く。太らせる前は元と差 0、太らせた後は 482 頂点すべてが外へ 3.000 mm。
    - 直す前の作り方（モディファイアを消し、アーマチュアの子にする）では、写しは Mirror のない 257 頂点で、元から最大 830 mm ずれた。
  - 頂点の並びに結び付いたモディファイア（上の 9 種）を 1 つずつ持つメッシュは、どれも止まった。Corrective Smooth が元の形で戻すときは止めない。形状キーのあるメッシュは、面を減らすときだけ止まった。
  - 合成の場面: 半径 0.3 m の球に、内側へ 20 mm の厚み（Solidify、offset -1）を付けた。
    - 写しは、元の外の面から 3.0 mm 外、内の面から 3.0 mm 内にでき、外の面はすべて内を向いた（輪郭線の写しの向き）。
    - 直す前の作り方（元の面で裏返してから Solidify を通す）では、厚みが外へ付いた。外の面も外を向き、暗い殻でキャラを覆う形になった。
  - 合成の場面: コレクションで見え方を変えたメッシュ（レンダーから隠したコレクション、その子のコレクション、ビューレイヤーで除外したコレクション）。
    - 既定の選び方は、見えるコレクションにあるメッシュだけを選び、写しは元と同じコレクションに入った。隠したメッシュを `meshes` で渡すと、写しも元と同じように隠れた。
    - 直す前は 5 個すべてを選び、写しを場面の根に置いたので、隠した衣装の輪郭線も描かれる形だった。
  - 手の属性と、動く形状キー（1→10 コマで 0→1）を持つメッシュでも写しを作れた（直す前は、ならしを当てる所で止まった）。
    - 写しのキーと基本の形の差は、元と 0.00002 mm まで同じ。
    - キーの動き（アクション）は元と同じものを使い、10 コマ目に元と同じだけふくらんだ。
  - 合成の場面で、塗りの直しを確かめた。
    - 発光の強さと粗さにノードがつながった顔の材質も、つなぎを外して 0.62 と 1 にした。
    - 材質の枠が 2 つのメッシュの写しは、枠ごとに線の材質を持った（面の材質の番号もそのまま）。法線の絵が先に置かれた枠でも、Base Color につながった絵を使い、色だけの枠はその色を使った。
    - Cycles 用と EEVEE 用の出力がある材質は、EEVEE 用の出力にセル調をつないだ（Cycles 用はそのまま）。
    - 1 つのメッシュを分け合う物体（リンクした複製）でも、塗る物体だけが変わった。
      - 塗らない複製は元の材質のまま。セル調と顔の塗りの組は、それぞれの塗りになった（セル調の側は物体の枠）。
      - 直す前は、塗らない複製もセル調になり、セル調の側も顔の塗りで上書きされた。
    - 右半分が透けた絵の板と、Alpha 0.5 の箱で、透けを確かめた。
      - セル調と線の材質は、元の透けを Transparent BSDF と混ぜて残した。
      - 緑の背景の上で描くと、透けた所に背景が見えた（直す前は、黒い板が描かれた）。

## 舞台で描く

`render.stage` は、塗りと輪郭線を付けたキャラ（`look.toon` の blend）を、舞台と光の中で、コマごとに PNG に描く。リポジトリ外の試作で 32 秒の本番を描いた舞台と光の組み方を移した。コマごとのカメラと光の値は、トラック（`model3d-stage-track/0.1`）で受け取る。曲に合わせてトラックを作るのは `plan.stage`（[段取りを組む](#段取りを組む)）。
- **入力**: `blend`（必須）と `track`（任意）。
- **舞台**（`stage.enabled`、既定 true）:
  - 床（40 m の暗い板。EEVEE の画面の反射を受ける）、背面の LED の壁（帯の模様を Emission で出す）、上のトラスと両脇の塔、光の柱 8 本（発光する半透明の円錐）、ほぼ黒の空。
  - 壁の暗い帯は、明るい帯の色の `stripe_floor`（既定 0.02）倍。`stripe_soft` で帯の境を広げる。
  - スポット 4 つ: 主光（影をやわらかく）、補助光（影なし）、左右の色つきの輪郭光。
- **長さの単位**: トラックのカメラ、既定のカメラの距離と高さ、舞台の大きさは m。シーンの `scale_length`（1 Blender 単位が何 m か）で直す。
  - 舞台のメッシュは、1/`scale_length` 倍の親 `model3d_stage_root` の下に m のまま作る（壁の帯や光の柱の模様は物体の中の座標で決まるので、そのまま）。
  - 光は Blender 単位の距離の 2 乗で弱まるので、強さを 1/`scale_length`² 倍、影の半径を 1/`scale_length` 倍にする。カメラの写る範囲と画面の反射の厚みも直す。
- **描き方**: EEVEE（`samples` 既定 64、`raytracing` で画面の光線追跡）、色の変換は Standard（Look なし）、`bloom` で合成の Glare の Bloom、`resolution` 既定 1080×1920、PNG（RGB・8 bit）。
- **背景**（`background`。省くと舞台の空か、舞台なしなら .blend の空）: 人物だけを緑背景か透けで描き、`mv dance` で合成したり `mv inspect` で検品したりするため。どちらも `stage.enabled: false` が要る（舞台の床や壁が背景を隠すので、そうでなければ止める）。
  - `{"color": [R, G, B]}`（0〜255 の sRGB）: その色の背景。透けで描いてから合成で色を敷くので、背景の色で人物を照らさない。ディザーを 0 にして、背景を画素まで決まった色にする（緑抜きのキーの色と同じ値になる）。輪郭のなじみは、透けの縁に色を敷いた形（実物の緑背景と同じく、抜くときの `softness` で扱う）。
  - `"transparent"`: 透けの RGBA の PNG。
- **コマ**:
  - トラックがあれば、その並びで描く（T は書き出す番号、f は動きのコマ。同じ f を続ければ止まり、とばせば速くなる）。無ければ、シーンの全コマを T = 1 から描く。
  - ファイルは `f<T>.png`。`only` で T を選ぶ。すでにあるファイルはとばすので、続きから描ける（描き直すときはファイルを消す）。
  - `frames_dir`（既定は工程のフォルダの `frames`）に書く。
- **コマごとの値**（トラック。省いた所は、毎コマ、動かない既定の値か .blend の最初の値に戻す。前のコマの値は引き継がないので、`only` や続きから描いても同じ画になる）:
  - `camera`: 位置と狙う点（世界の座標、m）とレンズ。
    - 省くと、腰の通り道を `camera.follow_sigma`（既定 6）コマのガウスでならし、その `follow`（既定 0.6）倍の所の高さ `aim` を狙う。
    - そのときの置き場所は、方位 `az`・距離 `dist`・高さ `height`（既定は正面の 3.6 m・1.1 m、レンズ 35 mm）。
  - `stage`（led・led_diag・void）、`wall`（明るさ・明るい帯と暗い帯の色・模様のずれ）、`rims`（左右の輪郭光のエネルギーと色）、`beams`（光の柱の明るさ・向き・色）。
  - `tone`: 塗りの共有グループ `model3d_stage_tone` の a・b・gain（省くと、.blend の最初の値）。
- **輪郭線**: 写し（`look.toon` の印）の Displace の強さを、カメラから腰までの距離で書き換える。
  - 高さ `reference_height`（既定 1920）の画で `line_px`（既定 2.4）画素の太さにそろえる（結果は解像度によらない）。
  - `outline.scale` で、元のメッシュごとに太さを掛ける。
  - 距離と太さは Blender 単位で求め、シーンの `scale_length` で m に直してから、写しの `model3d_outline_m_per_local`（中の単位 1 が何 m か）で割る。報告の距離と太さは m と mm。
- **押し出し**（`push`。省くと押し出さない）: 描く直前に、布の頂点（帯の垂れなど）が当たり（脚など）に入った所を外へ出す。リポジトリ外の試作で、太ももを上げたときに帯の垂れが脚に入るのを直した手順を移した。
  - 各要素の鍵:
    - `meshes`: 動かすメッシュ。`outlines`（既定 true）なら、その輪郭線の写し（`look.toon`）も同じに動かす。
    - `colliders`: 当たり。
    - `groups`: 動かす頂点の組（`[{"name": 組の名前, "offset_mm": 離す量}, …]`）。
    - `smooth`: ならす回数（既定 6）。
    - `recheck`（既定 true）: 直した位置で全部の当たりを調べ直し、まだ足りない分を足す。法線の向きに太らせる Displace（輪郭線の写し）のあるメッシュは、描く形も測り直してずれを打ち消す（下）。false なら、リポジトリ外の試作と同じく、当たりごとの直しのうちいちばん長いものだけを使い、測り直さない（試作と同じ画を作り直すとき）。
  - 当たりは閉じたメッシュにする（脚の部品の穴をふさいだ写しなど。キャラと一緒に骨で動くもの）。報告に、当たりの開いた辺の数を載せる。
    - 当たりの面は、毎コマ、体積が正になる向き（外向き）にそろえてから使う。鏡に映した当たり（`scale.x = -1` など）や、面が裏返った当たりでも、面の向きで中か外かを読み違えないため。報告の `reversed` に、そろえ直したかを載せる。
  - 押し出しが測るのはビューポートの形（評価した形）で、描くのは描画の形。動かすメッシュと当たりのモディファイアは、描く間だけ、ビューポートの効き方を描画の効き方にそろえる（片方だけで効く Armature などで、測った形と描いた形がずれないように。.blend は保存しない）。そろえたものは報告の `synced` に載せる。
  - 1 つのメッシュは、1 つの押し出しにだけ入れる（輪郭線の写しも数える）。
    - 2 つの押し出しで動かすと同じシェイプキーを書き合い、後の押し出しが前の直しを打ち消すため。
    - 同じ押し出しの中の重なり（元と写しを両方名指しするなど）は、1 つにまとめる。
    - 2 つの押し出しにまたがる重なりと、同じ押し出しで動かすメッシュと当たりの両方に入るものは、描き始める前に止める。
  - 動かすメッシュが、別の物体とメッシュのデータを分け合う（リンク複製）ときは、描く間だけその物体だけのデータにする。
    - シェイプキーはデータに付くので、分け合ったままだと片方の直しがもう片方も動かし、両方を直すと後の直しで上書きされるため。
    - .blend は保存しない。報告の `separated` に載せる。
  - 動かすメッシュのモディファイア（ビューポートか描画で効いているもの）は、直しを戻すときに扱えるものだけにする。
    - 扱えるのは、Armature、位置で変わらない Displace（テクスチャが無いか UV で読むもの。輪郭線の太らせ）、`look.toon` の面の裏返し（`model3d_flip_faces`）、頂点を動かさないもの（Triangulate・UV・法線）。
    - 法線の向きに太らせる Displace は、量は変わらないが、直しで面の向きが変わると太らせる向きが変わり、描く形が「測った位置 + 直し」からずれる。`recheck` が true なら、キーを書いた後に描く形を測り直し、ずれの半分ずつを打ち消す直しを足す（4 回まで。組の頂点の平均のずれがいちばん小さかった直しを使う）。報告のコマごとの `drift_mm` に、ずれ（打ち消す前・後の最大と平均）を載せる。
    - ほかの頂点を動かすもの（Simple Deform・Lattice・Smooth など）は、直しの向きを変えてしまう。頂点の組の重みを書き換えるもの（Vertex Weight・Data Transfer）は、骨の動きの戻し方を変えてしまう。
    - どちらも、描き始める前に名前を挙げて止める（先に適用するか、切る）。
  - 動かすメッシュは、モディファイアで頂点の数が変わらないものにする。
    - 元の頂点の番号で、評価した形の頂点を読むため。
    - 描き始める前と毎コマ確かめ、変わるなら理由（モディファイアの名前と頂点の数）を書いて止める。
    - Mask・Decimate・Subdivision などは、先に適用するか、切る。
  - コマごとに、組に重みのある頂点のうち、当たりの箱の中にある頂点を調べる。箱は、2 cm と、いちばん大きい `offset_mm` の大きい方だけ広げる（箱の外でも、離す量より近い頂点は直すため）。
    - 当たりの面のいちばん近い点から、面の外側の向きにはっきり離れている頂点は、とばす。
    - 中か外かは、5 本の光線が当たりの面と奇数回交わる向きの数で決める（3 本以上なら中）。
    - 中の頂点は、面を越えて `offset_mm` 外へ出す。外でも `offset_mm` より近い頂点は、`offset_mm` まで離す。
    - 面から 1 µm より近い（面の上の）頂点は、面の外向きに離す。頂点と面のいちばん近い点の差では向きが決まらない（0 か、BVH の float32 の丸めの向き）ため。
    - 組が複数あるときの量は、並べた順で最初に重みのある組のもの。
    - 当たりが複数あるときは、当たりごとの直しのうちいちばん長いものを選ぶ。`recheck` が true なら、直した位置で全部の当たりを調べ直し、まだ中にあるか近すぎる分を足す（4 回まで。2 つの当たりに違う向きで入った頂点を、片方に入ったまま残さないため）。足した頂点の数は、報告のコマごとの `rechecked` に載せる。
  - 動かす量のならし方:
    - 面のつながりでならす（隣の平均と半々を `smooth` 回。要る量は下回らない）。
    - 同じ位置の頂点（継ぎ目）はそろえ、組の重みを掛ける。
  - 骨で動かす前の差に直して、シェイプキー `model3d_push` に書く。測るのは、輪郭線の太さを入れた後の形。
    - キーは相対のシェイプキーで、値の 0 と 1 で直しを入れ切りする。絶対のシェイプキー（Relative のチェックなし。時間で形を選ぶ）のメッシュは、キーを足す前に止める。前からある相対のキーの動きは、そのまま残る。
    - 前からある `model3d_push` を使い直すときは、消音・頂点の組・基準のキー・値の範囲を、直しが効く設定に戻す。
    - 骨の曲げは、骨の重みで混ぜた行列（線形）として戻す。Preserve Volume（二重四元数）の骨では近い値になる。
    - Armature モディファイアの頂点の組の絞り（反転も）は、頂点ごとの骨の効きとして混ぜる（効かない頂点は骨で動かないものとして戻す）。
    - 骨の包み（Bone Envelopes）で動かす Armature と、効いている Armature が 2 つ以上あるメッシュは、描き始める前に止める。
    - 骨の重みの無い頂点と、Armature モディファイアの無いメッシュは、骨で動かないものとして戻す（物体の行列だけ）。
    - 戻すのは直しが要る頂点だけ。骨の拡大が 0 などで骨の動きが潰れた頂点は、直しが要らなければ止めずにとばす。直しが要るのに戻せない頂点は、直さずに描き、コマごとの数を報告の `unconverted` に載せる。
  - 毎コマ、先にすべての押し出しを直す前の形に戻してから、並べた順に当てる。
    - ある押し出しの当たりが、別の押し出しで動かすメッシュでもよい（前のコマの直しのまま測らない）。
    - そのため、`only` や続きから描いても同じ画になる。長さは m で、シーンの `scale_length` で直す。

**出力**:
- `frames`: `model3d-frames/0.1`。並びのコマのうち描いたファイルの sha256 と大きさ、描いていない T（`missing`）。
- `report`: `model3d-stage-render/0.1`。描いた・とばしたコマ、色の変換、カメラ（トラックか既定か、腰までの距離）、輪郭線の太さの幅、時間、設定。押し出しがあれば、動かすメッシュと頂点の数、当たりの開いた辺の数、コマごとの動かした頂点の数（1 mm より多く）と要った最大の量（mm）、骨の動きが潰れて直せなかった頂点の数（あれば）。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 設定の重ね方、コマの並び、腰のならし、カメラの位置、輪郭線の太さ、光の値の重ね方、2 つの契約。
  - 台本の引数と工程の受け渡し、描いたコマの一覧と続きからの描画、悪い設定を止めること（代役の Blender）。
  - 押し出しの、頂点の割合と離す量の選び方、動かす量、ならし方、継ぎ目のそろえ方。設定の受け渡しと、悪い設定を止めること。
  - 押し出しの、骨の重みの無い頂点の骨の動き（単位行列）と、全部の押し出しを先に戻してから順に当てること。頂点の数が変わるときと、押し出しが重なるときの説明。Armature の組の絞りの効き。当たりの面を外向きにそろえること。直しの要る頂点だけを戻し、骨の動きが潰れた頂点で止めないこと。扱えるモディファイアの見分け方。2 つの当たりに入った頂点の調べ直し。面の上の頂点を外向きに離すこと。絶対のシェイプキーを止めること。法線の Displace のずれを半分ずつ打ち消すこと。
- 2026-11-07 に Windows の Blender 4.5.15 で、試作が 32 秒の本番の最後のクリップ（118 コマ）を描いたのと同じ設定で確かめた。設定は 1080×1920、64 標本、線 1.6 画素、髪の線 0.7 倍。
  - 試作の台本の写しで、コマごとのカメラ・舞台の模様・壁・輪郭光・光の柱・塗りの共有グループの値を、トラックに書き出した。帯の押し出しはこのときまだ無かったので、試作の側も止めた（後で `push` を足し、下で比べた）。
  - 公開版の `look.toon` を当てたキャラをそのトラックで描くと、4 コマ（T 843・880・920・960）とも試作と画素まで同じだった（207 万 3,600 画素で差 0）。
  - 1 コマ約 13.5 秒（試作は 13〜15 秒）。
- 合成の場面（1 Blender 単位 = 1 cm、半径 30 cm の球を腰の高さに置いた）でも確かめた。
  - 3.6 m 先から描くと、報告の距離は 3.6 m、線の太さは 3.0857 mm（線 2.4 画素）。直す前は Blender 単位のまま割っていたので、線の写しが 100 倍の太さになる形だった。
  - 1 コマ目だけ塗りの色を変えたトラックで、2 コマ目を 1 コマ目の後に描いても、2 コマ目だけ描いても、同じ画になった（直す前は、前のコマの色が残って 644 画素違った）。
  - 同じ大きさの場面を 1 単位 = 1 m と 1 単位 = 1 cm で作り、同じトラック（m）で舞台ごと描いて比べた。
    - 報告の距離（2.52〜3.62 m）と線の太さ（1.51〜3.11 mm）は同じ。
    - 画はほぼ同じで、1/255 より違う画素は 2 コマで 0.3%・1.2%（最大 2/255・7/255）。直していない EEVEE の細かな長さの設定（影など）のぶんと見ている。
- 2026-11-07 に Windows の Blender 4.5.15 で、背景（`background`）を確かめた。本番と同じキャラ（クリップ D）に `look.toon` を当て、舞台なしで描いた。
  - `{"color": [0, 255, 0]}`: 101 コマ（540×960・16 標本）を 132 秒で描いた。背景はどのコマも画素まで (0, 255, 0) で、`mv inspect` の緑抜きで人物を切り抜けた（docs/inspect.md の「3 回目」）。
  - `"transparent"`: RGBA の PNG で、人物の外の透けは 0。
  - 背景を使わない描画は変わらない: 舞台ありの 1 コマを、直す前と後の台本で描くと、画素まで同じ（差 0）。
  - 舞台も背景もなしで描くと、キャラ（look.toon なしの .blend）は黒い影絵になった（その .blend に光も空もないため。塗りは Emission の `look.toon` を当てる）。
- 2026-11-07 に Windows の Blender 4.5.15 で、押し出し（`push`）を、試作の帯の直し（`SashFix`）と比べた。
  - 条件: 本番の最後のクリップのキャラに `look.toon` を当てた .blend を使った。帯の垂れの組は `sash_front` 7 mm・`sash_back` 4 mm、当たりは脚の穴をふさいだ写し 2 つ（どちらも開いた辺 0）、`smooth` 6。
  - 動きの 77 コマで、同じコマに両方を当てて比べた。
    - シェイプキーに書いた位置は、全コマで試作と同じだった（差 0）。動かした頂点の数と量も同じ。この比べは、調べ直し（`recheck`）を足す前のもので、今は `recheck: false` のときに同じになる。
    - 41 コマで帯を動かし、最大の量は本体 20.8 mm・輪郭線の写し 22.5 mm だった。
    - 1 コマにかかるのは約 1.8 秒（試作も同じ）。
  - 本番と同じ設定で 4 コマ（T 843・880・920・960）を描いて比べた。
    - 3 コマは画素まで同じ。T 960 も、違うのは 1 画素（1/255）だけだった。
    - T 880 は帯を直したコマで、直さない画とは 99 画素（最大 43/255）違う。
  - 作り物の場面でも確かめた。閉じた箱の当たりを布の板が横切る場面を、1 単位 = 1 m・1 cm・1 mm の 3 通りで作った。
    - 動かした頂点の数（102）、最大の量（0.084651 m）、合計は、どれも同じだった。
    - 布の頂点がちょうど当たりの面の上に乗ると、中か外かは丸めで決まり、単位を変えると数が変わった（はじめの置き方で 95 と 99）。
  - 端の場合も作り物の場面で確かめた。骨を Z のまわりに 30° 回した姿勢で、次の 3 つを当てた。
    - 骨の重みの無い頂点を半分（66）含む布。
    - Armature の無い布。
    - 押し出し同士が当たりになり合う形（ある押し出しで箱の外へ出す小さな箱が、別の押し出しの当たり）。
  - 結果:
    - 落ちずに直り、直した後に当たりの中に残る頂点は 0（直す前は 42・45・4）。
    - 同じコマを 2 回続けて直しても同じだった。押し出しごとに自分のキーだけ戻す前の作りでは、2 回目に布が最大 19.5 mm 変わった。
    - 直した後も、本番のキャラで試作の帯の直しと同じ（5 コマでシェイプキーの差 0、T 880 を描き直して画素まで同じ）。
  - 頂点の数を変えるモディファイアも、作り物の場面で確かめた。
    - Decimate が効いた布は、描き始める前に、理由を書いて止まった（121 → 63 頂点）。
    - Triangulate（頂点の数は変わらない）の布は、そのまま直った。
    - 途中のコマで Mask が効き始めた布は、そのコマで止まった（121 → 55 頂点）。
    - 本番のキャラ（輪郭線の写しに、面の向きを裏返す Geometry Nodes）は止まらず、試作と同じだった。
  - 押し出しの重なりも、作り物の場面（布と、その輪郭線の写し）で確かめた。
    - 押し出し 1 が布（写しも）、押し出し 2 が写しを名指しする形は、描き始める前に止まった。
    - 1 つの押し出しで布と写しを両方名指しすると、1 つにまとめ、写しは 1 回だけ動かした。
    - 写しを当たりにも入れる形は、止まった。本番のキャラは止まらず、試作と同じだった。
  - メッシュのデータを分け合う物体（リンク複製。片方は箱から離れた所）も確かめた。
    - 片方だけを動かすと、もう片方は動かなかった（0.0 mm。シェイプキーも付かない）。
    - 両方を動かすと、箱に入る側だけが直り（最大 85.8 mm）、離れた側は 0 のままだった。
    - 本番のキャラ（分け合うデータなし）は、試作と同じだった。
  - 離す量が 2 cm より大きいときも確かめた。箱の上の面から 25 mm の所の布を、30 mm 離す設定で直した。
    - 布は上の面から 30 mm まで離れた（25 頂点を 5 mm）。直す前は、箱を 2 cm しか広げなかったので調べられず、25 mm のままだった。
    - 本番の量（7 mm・4 mm）では、箱の広げ方は 2 cm のままで変わらない。
  - Armature の組の絞りも確かめた。骨を Z のまわりに 90° 回し、布の半分だけに骨が効くよう組で絞った。
    - 直した後、箱の中の 42 頂点は 0 になり、面からの距離の最小は 5.0 mm（離す量）だった。
    - 直す前は、骨が効かない頂点の直しまで 90° 回して戻したので、18 頂点が中に残った（最小 3.1 mm）。
    - 骨の包みで動かす Armature は、描き始める前に止まった。本番のキャラは、試作と同じだった。
  - 鏡に映した当たり（`scale.x = -1`）と、面が裏返った当たりも確かめた。
    - どちらも面を外向きにそろえ直し、普通の箱と同じく、中の 42 頂点が 0 になった。
    - 直す前は、面の向きの近道で深く入った頂点を外と読み、42 頂点が中に残った。
    - 本番の当たり（外向き）はそろえ直さず、試作と同じだった。
  - 拡大が 0 の骨も確かめた。布の半分を、その骨の付け根の 1 点に潰した。
    - 潰れた点が箱の外なら、止まらずに残りの側を直した（中の 18 頂点が 0）。
    - 潰れた点が箱の中なら、直せない 55 頂点は直さずに数を返し、残りの側は直した（中の 73 頂点が 55）。
    - 直す前は、どちらも逆の行列が作れずに止まった（LinAlgError）。本番のキャラは、試作と同じだった。
  - Armature のほかに形を変えるモディファイアも確かめた。
    - ねじり（Simple Deform の Twist 90°）は、描き始める前に止まった。直す前は、直しがねじれて 44 頂点のうち 40 頂点が中に残った。
    - 位置で読むテクスチャの Displace は止まった。UV で読むテクスチャの Displace と、テクスチャの無い Displace は、そのまま直った（中の 42 頂点が 0）。
    - 本番のキャラ（輪郭線の写しに、面の裏返しと太らせの Displace）は止まらず、試作と同じだった。
  - 直した位置での調べ直し（`recheck`）を、本番のキャラで確かめた（動きの 40・59・66 コマ）。
    - `recheck: false` は、試作の帯の直しと同じだった（シェイプキーの差 0）。
    - `recheck: true` は、試作の直しで足りなかった頂点に足した（59 コマで本体 178・写し 125 頂点。シェイプキーの差は最大 6.8 mm）。
    - 直した後の帯の頂点のうち、脚の当たりの中にあるか離す量より近いものは、少し減った（59 コマの本体で、中 29 → 28・近すぎ 290 → 282）。残る頂点の多くは、結び目の近くで重みが 1 より小さく、押し切らない頂点と見ている。
    - 試作と同じ画を作り直すレシピでは `recheck: false` にする。
  - モディファイアの効き方のずれも確かめた。布を、骨で動かす前は箱の外、骨で動かすと箱に入る位置に置き、Armature を描画だけで効かせた。
    - 直した後は、描く形と同じ効き方で測り、描く形で箱の中に残る頂点は 0 だった（直す前の 30 頂点）。
    - 直す前は、ビューポートの（骨で動かない）形を測ったので直しが入らず、描く形で 30 頂点が中に残った。
  - 頂点がちょうど当たりの面の上にある場面も確かめた（箱の上の面に重なる布、離す量 5 mm、ならさない）。
    - 直した後は、どの頂点も上の面からちょうど 5.0 mm 離れ、横にはずれなかった。
    - 直す前は、BVH の丸めのでたらめな向きに横へ最大 22.2 mm ずれ、高さは 0 のままだった。
    - 本番のキャラは、`recheck: false` で試作と同じだった（5 コマでシェイプキーの差 0）。
  - 動かすメッシュのシェイプキーも確かめた（箱を横切る布）。
    - 絶対のシェイプキーの布は、止まった。直す前は、直しを計算しても当たらず、42 頂点が中に残った。
    - 相対のシェイプキー（布を 2 mm 横へずらすキー、値 1）がある布は直り、そのキーの 2 mm のずれも残った。
    - 前に作られた `model3d_push`（消音・頂点の組つき）がある布は、設定を戻して使い直し、直った。直す前は、42 頂点が中に残った。
  - 輪郭線の写しの、法線の Displace のずれを、本番のキャラで測った（動きの 40・59・66 コマ、輪郭線の太さ 2 mm）。
    - 打ち消す前は、描く形が「測った位置 + 直し」から、最大 3.9〜4.0 mm、組の頂点の平均 0.01〜0.08 mm ずれた（本体はずれ 0）。
    - 打ち消すと、平均は 0.008〜0.040 mm に下がった。最大は 3.5〜3.9 mm で、面の向きが大きく変わる少数の頂点に残った。
    - 重み 1 の写しの頂点のうち脚の当たりの中にあるか離す量より近いものは、`recheck: false`（試作と同じ）の 0・108・35 から、0・11・12 に減った。
    - `recheck: false` は、今も試作の帯の直しと同じだった（シェイプキーの差 0）。

## 段取りを組む

`plan.stage` は、段取り表（`model3d-stage-plan/0.1`）と光のキー（`model3d-light-keys/0.1`）と骨の通り道（`model3d-bone-path/0.1`）から、`render.stage` のトラックを作る。リポジトリ外の試作で 32 秒の本番のカメラと光を決めた計算を、曲によらない形に移した。曲ごとの時刻と値は、段取り表としてリポジトリの外のレシピに置く。

**流れ**（レシピの工程）:
1. `motion.bone_path`（blend [, track] → path）: 動きのコマごとに、骨の付け根の位置を m で測る。
   - 骨は `bones`（既定は `Hips` と `Head`）に `bone_prefix`（既定 `mixamorig:`）を前に付けた名前。アーマチュアは `armature`（省くと最初のアーマチュア。`render.stage` と同じ）。
   - `track`（描くコマの並び）があればその動きのコマを、無ければ `frame_range` か .blend のシーンの全コマを測る。
   - 骨だけを動かして測る（メッシュの変形は止める）。.blend は保存しない。
2. `plan.stage`（plan, keys, bones [, cut] → track [, report]）: コマごとにカメラ・舞台・光の値を決める。
   - `cut`（T と f だけのトラック）があればその並び、無ければ骨の通り道のコマを T = 1 から順に。
   - コマ T の時刻は (T - 1) / fps 秒（fps は光のキー）。光のキーより後のコマは最後の値を使い、その数を報告する。
3. `render.stage` でそのトラックを描く。

**段取り表**（省いた部分は既定の値。既定は試作の値）:
- 時刻は、秒の数・印（`marks` の名前）・拍 `{"beat": k}` のどれか。拍は光のキーの格子で k0_time + period × k 秒。
  - 時刻の境目は、1e-6 秒前のコマから届いたとみなす。区間の切り替え、寄りと揺れの始まりと終わり、拍、印のどれも同じ。
  - ちょうど境目に当たるコマが、浮動小数の丸めで前の側に残り、1 コマ遅れないようにするため。
- 色は、`colors` の名前か RGB。
- `camera`:
  - `base`: 方位 `az`（度。0 で正面、正で +X 側。キャラは -Y を向く）、水平の距離 `dist` と高さ `height`（m）、レンズ `lens`（mm）、狙う点 `target`。
    - 既定は、`render.stage` の既定のカメラと同じ（正面の 3.6 m・高さ 1.1 m・35 mm。腰の通り道の 0.6 倍の所の、高さ 0.95 m を狙う）。
  - `shots`: カメラの区間の並び。値は `base` に重ねる。
    - 区間の頭は、前の区間の `until`（最初は 0 秒）。最後の区間だけ `until` を省ける（その先ずっと）。
    - `to` を書くと、区間の頭から `over` 秒（省くと区間の長さ）かけて、`to` の値まで `ease`（`smooth` か `linear`）で動く。
    - 狙う点は次のどれか。高さも、頭からの高さ `{"head": 0.05}` で書ける。
      - 腰の通り道の `hips` 倍の所の、高さ `z`（`{"hips": 0.6, "z": 0.95}`。0 に近いほど腰の揺れを追わない）
      - 頭から上へ `head` m（`{"head": -0.1}`）
      - 決まった点（`{"point": [x, y, z]}`）
    - `effects: false` の区間では、下の寄りと揺れを入れない。
  - `smooth`: 骨の通り道をならすガウス（コマ）。
    - 既定は、腰が σ 6（幅 ±round(2σ)）、頭が σ 1.5・幅 ±6。`z` で上下だけ別にならせる。
    - ならすのは、描くコマの動きのコマを、重ねず小さい順に並べた列。
  - `effects`（並べた順に掛ける）:
    - `whip`: `at` の `before` 秒前から寄りながら回り、`hold` 秒そのまま回り続け、`after` 秒で戻る（距離の縮み `zoom`、回る角度 `turn`）。
    - `push`: `at`（1 つか並び）から `length` 秒、距離に 1 - `amount` × exp(-経過 / `tau`) を掛ける。
    - `shake`: `at` から `length` 秒、横と上下に揺らす（`x`・`z` は [振れ幅 m, 角周波数]）。
    - `beat_push`: `from`〜`until` の拍ごとに寄る（`every` の倍数の拍は `strong`、ほかは `weak`）。
  - カメラの位置は、狙う点 + (sin(az) × dist + 横の揺れ, -cos(az) × dist)。高さは height + 上下の揺れ。
- `stage`: 区間ごとの壁の模様（led・led_diag・void）。`fade`（[頭, 終わり]）で、壁の明るさに掛ける割合を変える。
- `lights`:
  - `palette`: 区間ごとの縁の色 a・b。
    - 壁・左の輪郭光・塗りの a は a、右の輪郭光・塗りの b は b。光の柱は交互（奇数番が a）。
  - `level`: 舞台の光の強さ。`segments` の最初に当たる区間の値（[頭, 終わり] なら変わる）。どれにも当たらなければ `default`（既定 1）。
  - 信号:
    - `low`・`mid`・`high` は、光のキーのそのコマの値。
    - `hit` は、直前の拍から `hit.frames`（既定 9）コマで 1 → 0。
    - `punch` は、直前の `punch.at` から `punch.frames`（既定 12）コマで 1 → 0。
  - 明るさ（`wall`・`rims` の 2 つ・`beams`・`tone`）= (`base` + Σ 係数 × 信号) × 舞台の光の強さ（`level` が true のとき）× 落とす割合。負になったら止める。
    - `dims`: 光を落とす区間。数なら掛ける。`{"level": x}` なら、舞台の光の強さの代わりに x を使う。
    - `wall` には、舞台の `fade` と、レンズが `closeup.lens` 以上のときの `closeup.scale` も掛ける。模様の横のずれは `scroll` × T。
    - `tone`（塗りの縁）は、既定では舞台の光の強さを掛けない。`scale` を掛ける。
  - `beams` の向き: `phase` からの拍 b で、柱 j（0〜7。0〜3 は左）を振る。
    - 下向きに `tilt.base` + `tilt.swing` × sin(2π b / `tilt.period` + j × `tilt.step`) 度。
    - 横に ±`pan.spread`（左は -）+ `pan.swing` × sin(2π b / `pan.period` + j × `pan.step`) 度。

**出力**:
- `track`: `model3d-stage-track/0.1`。コマごとのカメラ（位置・狙う点・レンズ）、舞台の模様、塗りの a・b・gain、壁（明るさ・色・模様のずれ）、輪郭光、光の柱。
  - 壁の暗い帯の色は書かない（`render.stage` が、明るい帯の色 × `stripe_floor` にする）。
- `report`（任意）: `model3d-stage-plan-report/0.1`。区間（カメラ・舞台・色）ごとの時刻と T の範囲とコマの数、使った骨、光のキーより後のコマの数。

光のキーを曲から作る道具は、まだ公開版に無い（リポジトリ外の試作の道具で作り、`schema_version` を足して使った）。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 時刻の読み方（秒・印・拍）、区間の並び、なめらかさ、ガウス、直前の出来事からの秒。
  - カメラ: 既定のカメラ、頭と決まった点の狙い、動き、whip・push・shake・beat_push、寄りと揺れを切る区間、描くコマだけでならすこと。
  - ちょうど拍や境目に当たるコマ（拍の選び方、区間・舞台・色・強さ・落とす区間の切り替え）。
  - 光: 色・強さ・拍と印の信号・落とす区間、壁の fade とアップ、模様のずれ、光の柱の向き。
  - 悪い段取り表と、食い違う入力を止めること。3 つの契約。
  - 工程の受け渡しと再利用、`motion.bone_path` の設定と引数（代役の Blender）。
- 2026-11-07 に、リポジトリ外の試作（32 秒の本番。案 2 のカメラ）で確かめた。
  - 試作のカメラと光の決め方を、段取り表（15 の区間、5 つの寄りと揺れ）に書き直した。
  - 試作の台本から計算の関数をそのまま取り出し、作り物の骨の通り道で、32 秒の全 960 コマを突き合わせた。カメラ・舞台の模様・光・塗りの値の差は 1e-12 より小さかった（浮動小数の丸めだけ）。
  - Windows の Blender 4.5.15 で、5 つのクリップ（合わせて 960 コマ）の動きの .blend から `motion.bone_path` で腰と頭を測った。`plan.stage` で作ったトラックを、試作が Blender の中で計算した値と比べた。
    - 全コマで、差は float32 の丸めの範囲（相対 1e-7 未満）だった。
    - float32 にすると、光と塗りの値はすべて同じ。カメラの位置の y だけが 1 ulp（0.24 µm）違う（試作は Blender の float32 のベクトルで足すため）。
  - 最後のクリップの 2 コマ（T 880・920）を、そのトラックで描いて試作の画と比べた。
    - 1/255 より違う画素は 131 と 116（207 万 3,600 画素の 0.006%）で、最大 6/255。カメラの位置の 1 ulp のぶんと見ている。
    - 2/255 以上違う画素の 85〜97% は、まわり 3×3 の明暗の差が 40/255 以上の縁（線や輪郭）にあった。
    - 試作が書き出した値のトラックで描くと、画素まで同じになる（[舞台で描く](#舞台で描く)）。

## Orpheus との接続（受け渡し）

1. 3D 側で描いた動画（緑背景か透過の連番から作った動画）を、`model3d handoff RUN <工程 id>/<出力名> handoff.json` で控えにする。
2. dance の spec の `performer.clips[].path` にその動画を書き、spec のどこかに `"x_model3d": {"handoff": "handoff.json"}` のように参照を置く
   （`x_` の鍵は dance が無視するので、`dance-shot/0.2` の契約は変えない）。
3. `mv dance render` の manifest に残る素材の sha256 と、`handoff.json` の `artifact.sha256` を照合する。`mv gate check` はそのまま使える。

既存制作 worker による描画と動画化は `model3d dance-recipe` の経路を使う（[手順](model3d-dance.md)）。
Blender での30秒描画と最終品質の全編出力は、まだこの経路で確かめていない。リポジトリ外の手順では、2026-11-06 に 32 秒を描いた。
汎用 `blender.run_script` を recipe の工程として通す確認も、まだしていない。

## 限界・未検証

- **既存制作 worker の接続は限定的**。元 GLB／分割 GLB／FBX、採用済みのウェイト・法線・動作・補正を再利用する。
  `dance-recipe` は確認済みの配置と CLI 契約に対応し、任意の作品へ自動適応するものではない。素材・preset 自体は Git に入れない。
- **汎用 Blender 観測は、既定のシーンでだけ確かめた。生成器と別メッシュへのリギングは未検証**。既存 worker の再実行とは区別する。
- **別メッシュへの転送（CorrespondenceMap の処理）・揺れのベイクと再開・30 秒の動作検査・GLB 書き出しは未実装**。
- 面領域は部品の名前と頂点構成に結び付けて持ち、工程の `select` で対象の面だけを加工できるが、**部位の自動分割（髪・顔・衣装を分ける処理）は無い**。
  部品の区別は名前だけで、同じ名前・同じ頂点構成の別のメッシュ（取り違えたファイルなど）は見分けられない。
  面は連結成分・面番号・面集合・箱で人が選び、確認も人が行う。融合した部位を分けるのは形状修正の工程（Blender の手作業と `model3d edit`）。
  構成を変えた後の付け替えは、結合の正確な面の対応だけに対応する（リトポロジーなど対応の無い変更では、面領域を作り直す）。
- 設定画からの生成は、この環境では代役の生成器（輪郭の箱）だけで通した。本物の生成器（TripoSR）はユーザーの環境の記録が根拠で、
  ここでは未検証。多視点の生成器は未接続。
- 貫通の補正は凸の衝突相手で確かめた。凹んだ相手では 1 回の補正で出ないことがあり、`residual_inside` に残る。
- `render.stage` の押し出し（`push`）は、当たりの閉じたメッシュと、動かす頂点の組を .blend に用意しておく必要がある。
  - これらを作る道具（脚の穴をふさいだ写しと、帯の垂れの頂点の印）は、まだ公開版に無い。リポジトリ外の試作では、キャラを組む段階で作った。
- 結合の候補は格子で絞るが、大きなメッシュ（数十万頂点）での速さは未測定。内外の判定と貫通の補正は 点 × 三角形 の総当たり
  （帯と大腿のような部品どうしを想定）で、全身どうしの検査には空間分割（BVH など）が要る（未実装）。
- 骨で動かす前の形へ `deltas` を戻してシェイプキーに書く工程は Blender 側で、未実装。
- テクスチャへの写しは座標軸に沿った正射影だけ（斜めの視点・透視は無い）。見え隠れは渡したメッシュの中だけで決め、別の部品の陰は
  考えない（手前に別の部品があるなら、その部品も含めたメッシュを渡す）。目印の自動検出・色合わせ（設定画と元の肌の色の差をそろえる）・
  複数の視点を 1 工程で混ぜる処理は無い（視点ごとに工程を重ねる）。uv を共有する面（左右で同じ uv など）は、見える面と隠れた面の重みを平均する。
