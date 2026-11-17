# model3d（自作の 3D モデリングツール・Orpheus の外部ツール・実験的）

設定画または既存モデルから、**部位・骨の対応 → 加工レシピの実行 → 形状と動作の検査 → 書き出し**を、レシピと記録で進める道具。
Orpheus（`mv dance` など）とは**出力ファイルと制作記録（RunRecord）だけで接続**し、既存の 2D ダンス工程に 3D 固有のデータ契約は混ぜない
（統合位置は試作の後に決める）。コードは `tools/model3d/`、import 名は `model3d`。`mv_pipeline` とは互いに import しない。

- **再現性**: 内部のメッシュ演算は同じ入力・設定・コード版で同じ数値結果を出す。外部 worker は入力・版・出力を記録して比較する。
  Blender や動画の全バイト一致、環境をまたぐ全画素一致までは保証しない。制作工程から LLM やネットワークは呼ばない。
- **採点しない**: 検査は構造と数値（`pass`）。見た目の採否は人の判定（`model3d accept`）で、RunRecord の `adopted` に別に残る。
- **検査は開発原則の例外**: AGENTS.md の「生成物の開発中は検査を足さない」に対し、このツールは `mv gate` と同じ例外（2026-11-03 ユーザー承認）。設計が実装単位に `validation` を含み §10 で検査を求めるため。単独の検査（`generate inspect`・`mesh compare`・`mesh parts`・`frames compare`・`check`）も構造と数値の報告だけで、採否は決めない。
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
`--tool blender=PATH` や設定 JSON の argv で渡す。VRM の道筋は Blender 5.2 と VRM Add-on が基準
（[Blender 5.2 と VRM Add-on](#blender-52-と-vrm-add-on)）。

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
| stage track `model3d-stage-track/0.1` | `render.stage` と `render.passes` が描くコマの並び（書き出す T と動きのコマ f。f は小数でもよく、前後のコマの間の時刻を描く）と、コマごとのカメラ（位置・狙う点・レンズ）・舞台の模様・壁・輪郭光・光の柱・塗りの共有グループの値 |
| frames `model3d-frames/0.1` | 描いたコマのフォルダとファイル（T・f・sha256・大きさ）、fps・解像度、描いていない T |
| stage plan `model3d-stage-plan/0.1` | `plan.stage` の段取り表: 時刻の印（秒か拍）と色の名前、カメラ（区間ごとの方位・距離・高さ・レンズ・狙う点と動き、寄りと揺れ、骨の通り道のならし方）、舞台の模様の区間、光（色・強さ・拍と印の信号・落とす区間・光ごとの係数） |
| light keys `model3d-light-keys/0.1` | 曲の音の帯ごとの強さ（low・mid・high。コマごとに 0〜1）と拍の格子（period・k0_time）、fps とコマの数。`plan.light_keys` が曲の音から作る |
| light plan `model3d-light-plan/0.1` | `plan.lights` の光の段取り表: 値の名前と既定の値、区間ごとの区間の中の名前（let）と上書き（set）と閃光、色や位置の名前、時刻の印、拍の hit の減り方。値は数・3 つの並び・小さな式（[光の台本](#光の台本planlights)） |
| light track `model3d-light-track/0.1` | `plan.lights` が書く、コマごとの光の値（名前 → 数か 3 つの並び）と区間の番号、fps と拍の格子。閃光の強さと色・暗転で掛ける割合（flash・flash_color・fade）はどのトラックにもある |
| bone path `model3d-bone-path/0.1` | 動きのコマごとの骨の付け根の位置（世界の座標、m）、シーンの scale_length、アーマチュアの名前 |
| passes `model3d-passes/0.1` | `render.passes` が要素ごとに描いたコマのフォルダとファイル（T・f・sha256・大きさ）、要素ごとの形式と透け、fps・解像度、描いていない T |
| cameras `model3d-cameras/0.1` | 要素別の描画のコマごとのカメラ（世界の行列・焦点距離と主点の画素・レンズ・ずらし）と踊り手の点・距離、画面の大きさ。`render.passes` の出力で、入力にも渡せる |
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
  近い面を探して付け替えることはしない（部品のラベルを別のメッシュへ近さで移す `mesh.transfer_parts` は、面領域とは別の工程で、
  面領域の対応は変えない）。

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
| `mesh.list_parts` | parts [, normals] → report | 部品ごとの npz の部品の一覧（[部品の一覧と uv の伸び](#部品の一覧と-uv-の伸び)）: 名前・頂点と面と三角形の数・面の角の数・範囲・uv の範囲。normals を渡すと、部品ごとに法線があるか・形が合うかも見る。見つけたこと（非有限の頂点・uv・法線、無い頂点を指す面、形の合わない uv や法線、どの部品にも当たらない法線）があれば要確認。CLI は `model3d mesh parts` |
| `mesh.transfer_parts` | source, target → labels [, mesh, report] | 移し先の面ごとに、重心がいちばん近い移し元の面の部品を付ける（[部位のラベルを移す](#部位のラベルを移す)）。移し元は部品ごとの npz か parts のあるメッシュ、移し先は 1 つのメッシュの npz（`co`・`faces`）かメッシュ。labels は `names` と面ごとの番号の npz。`max_distance` より遠い面は付けずに要確認。最近傍は numpy だけで探す |
| `mesh.extrude_profile` | profile（`{"points": [[x, y], …]}`）→ mesh | 厳密に凸で反時計回りの断面を `height` だけ押し出す。`twist_deg` か `twist_rad`、`steps`、`offset`。高さが負なら先に断面の有向辺を反転する。ねじり 0 なら 1 段 |
| `mesh.weld` | mesh [, rig] → mesh [, mapping, rig] | `distance` 以内の頂点を結合（`selection` か工程の `select`・`center: mean|first`）。退化面と包含面を除く（結合に関わった面と隣接面のみ）。uv・頂点色・法線（`x_normals`。長さは 1 に戻す）も同じ規則でまとめる。mapping は旧→新頂点番号、旧→新面番号（`face_old_to_new`。消えた面は -1）、双方の topology と部品の名前（`source_name`・`target_name`）。rig は入出力を対で指定し、同じ mean / first 規則でウェイトを移す |
| `mesh.transform` | mesh → mesh | `to_units`（m / cm / mm の換算）→ `to_axes`（`{"up", "forward"}` の約束の換算）→ `scale` → `rotate_z_deg` → `translate`。頂点の法線（`x_normals`）も同じ向きへ回す |
| `mesh.extract_region` | mesh → mesh [, mapping] | 工程の `select` で選んだ役割の面だけのメッシュ（使わない頂点は落とす。uv・頂点色・法線・単位・軸は引き継ぐ）。mapping は新→旧の頂点・面番号と双方の topology。観測に連結成分の数と閉じているか |
| `mesh.smooth_normals` | mesh → mesh [, report] | 頂点の法線をならし、`x_normals` に書く（[部品の法線をならす](#部品の法線をならす)）。頂点と面は変えない。格子 `weld` で同じ位置の頂点をまとめ（UV の継ぎ目の重複点も同じ法線）、面積の重みの法線を、隣の法線の和で `iterations` 回ならす |
| `mesh.smooth_part_normals` | parts → normals [, report] | 部品ごとの npz の全部の部品の法線を、`mesh.smooth_normals` と同じ計算でならし、`n{i}`（float32）の npz に書く（`mesh.import_parts` の入力 normals になる）。`iterations` は整数か、部品ごとの回数 |
| `mesh.fill_holes` | mesh → mesh [, report] | 穴（1 つの面だけが使う辺の輪）を面でふさぎ、閉じたメッシュにする（[穴をふさぐ](#穴をふさぐ)）。先に隣り合う面の向きをそろえる。今の面と頂点の番号は変えず、ふさぐ面を後ろに足す（`cap`: polygon か fan）。辺でつながった面の集まりごとに、体積が負なら裏返して外向きに。`max_sides` より大きい穴は残して要確認。多様体でない辺・2 つの穴の縁が通る頂点・裏表のない面・つぶれた辺は止める |
| `mesh.export_obj` | mesh → obj | v / vt / f / g。見出しの行に units・軸・名前を書き、`mesh.import` で戻す（名前は面領域が部品を区別するのに使う）。数値は往復で変わらない表記 |
| `collision.push_out` | mesh, collider → mesh, deltas, report | 閉じた collider の中の頂点（`selection` か工程の `select` の面の頂点だけにできる）を最も近い面の外へ `clearance` だけ離す（`clearance` は mesh の単位。mesh と collider の単位か軸の約束が違えば止まる）。外でも余裕より近い点は離す（`pad_outside`）。`deltas` は頂点ごとの移動量 |
| `check.structure` | rig [, mesh] → report | 必須骨の欠落（`required_bone_roles`: 配列か `preset` か `humanoid`）・循環・非有限値、ウェイトの正規化（`tolerance`）。mesh があれば頂点数・topology も必須照合。使用する未確認の骨対応は needs_review |
| `check.penetration` | mesh, collider → report | 中にある頂点の数・番号・深さ（工程の `select` があればその面の頂点だけを調べ、数を `checked` に残す。mesh と collider の単位か軸の約束が違えば止まる。`directions` は 1 本以上の有限で長さ 0 でない向き。奇数本を勧める）。交わりを調べる三角形は格子で絞る（`search: grid`。`all` で総当たり。答えは同じ）。脚がまるごと布を通り抜ける失敗は数に出ない（下の「限界」） |
| `check.continuity` | track → report | コマごとの点の列で前のコマからの移動が `max_jump` 以下 |
| `check.uv_stretch` | mesh [, texture] → report | uv の伸び（三角形の、画素での周長 / 3D の周長を中央値で割った値）を測る（[部品の一覧と uv の伸び](#部品の一覧と-uv-の伸び)）。百分位・`threshold`（既定 8）を超える三角形・3D で潰れた三角形・uv の幅（画素）。テクスチャの大きさは入力 texture の画像か `texture_size`。しきい値（`max_over`・`max_degenerate`）を渡したときだけ合否を出す |
| `check.frames` | [left, right] → report [, view] | 2 つのコマの並び（描いたコマの一覧か、`left_dir`・`right_dir` のフォルダ）を、同じ T どうしで画素で比べる（[コマのフォルダを比べる](#コマのフォルダを比べる)）。同じコマの数・違う画素・いちばん大きい差・色の差の平均・透けの違い・差の大きさごとの画素・違いの大きいコマ。左を基準に、右に無いコマと大きさの違うコマも並べる。`view` で [左 \| 右 \| 差] の PNG。しきい値を渡したときだけ合否を出す。CLI は `model3d frames compare` |
| `check.blend_match` | left, right → report | 2 つの .blend を比べる（[.blend どうしを比べる](#blend-どうしを比べる)）。物体の有無・シーン・メッシュごとの頂点・面のつなぎ・uv・属性・置き場所、骨の名前・静止・姿勢、`evaluate` なら変形した世界の形。Blender で中身を書き出し、numpy で比べる。全部が同じかを `exact` に、違いの大きさを `max_*` に出す。`tolerance` を渡せばその中かで合否。Blender が要り、再利用しない |
| `check.motion` | points → report | `motion.prepare_keypoints` の points の動きを数で測る（[整えた点の動きを測る](#整えた点の動きを測る)）。膝とつま先の食い違い・接地中の足の滑り・床へのめり込み・1 コマの移動・骨の長さのぶれ。しきい値を渡したときだけ合否を出す。知らないパラメーターは止める |
| `check.animation` | blend [, expected] → report [, table, sheet, expected_table] | 動きを付けた .blend（VRM の人型の骨。骨へ載せ、揺れものを焼き、手直しをした後）を全コマ調べて、破綻の候補を出来事（信号・場所・コマと秒・いちばん大きい値と位置）にまとめる（[動きを検品する](#動きを検品するcheckanimation)）。静的（腕が胴・頭・脚に入る、腕どうし・脚どうし、体が布を突き抜ける、足が床より下、手首・首・ひざ）と動的（向きの飛び、位置の飛び、震え、着いた足の滑り、全身の切り替え）を 1 本の報告に。体の形はそのモデルの初期姿勢のメッシュから作る。table は読む用の表（Markdown）、sheet は証拠の絵（場所を狙うカメラと赤い印）の一覧。expected（`motion.inject_faults` の、わざと入れた破綻）があれば見つけたかを照らし合わせる。直さない・採点しない（出来事があれば needs_review）。しきい値の既定は仮の値。Blender（VRM Add-on）が要り、再利用しない（毎回実行） |
| `motion.inject_faults` | blend → blend, expected [, report] | きれいな動きの .blend に、決まったコマでわざと破綻を入れて別の .blend に書き、入れた物の一覧（expected）を書く（[正解の分かっている試験の素材](#正解の分かっている試験の素材motioninject_faults)）。`check.animation` が見つけるかを確かめるための素材。入力の .blend は変えない。Blender（VRM Add-on）が要り、再利用しない（毎回実行） |
| `check.jitter` | blend [, track] → report | 布などのメッシュ（`objects`）のちらつきを測る（[ちらつきを測る](#ちらつきを測るcheckjitter)）。頂点を骨から見た座標で読み、前後のコマの中間からのずれの平均・99 百分位・最大と、`jump_mm` を超えた割合、ずれの大きいコマと頂点。`toggle` で変形を 1 つずつ切って測り直す。しきい値を渡したときだけ合否を出す。Blender が要り、再利用しない（毎回実行） |
| `check.mouth` | mesh [, texture] → report [, front, section] | 頭の候補の口の作りを数と絵で調べる（[頭の候補の口の作りを調べる](#頭の候補の口の作りを調べるcheckmouth)）。上下の唇が別の面か（口の幅の区間ごとに、見える面のすぐ裏の浅い所だけで唇の外の面の上から下へつながるか＝くっついている所）、3 枚以上の面が集まる辺と穴の縁、口の中の面と部品と深さ、色の絵があれば口の中と肌の色の差。正面の色分けと真ん中の断面の絵。合否は決めない。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.illustration` | image → report [, view] | 立ち絵（正面）か T ポーズの体の絵の、人物の比率を測る（[立ち絵と体の絵の比率を測る](#立ち絵と体の絵の比率を測るcheckillustration)）。人物の画素は、背景の色（既定は緑）でない画素か透けていない画素。`rows` の行ごとの胴・腕・脚の幅（身長に対する割合）と頭身、または `tpose` で床から腕までの高さを物差しにしたくびれ。`view` は確認用の絵。合否は出さない。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.body_image` | reference, body → report [, overlay] | 体の絵を元絵と同じ物差し（足の裏からウエストまでの高さ U）で比べる（[体の絵と顔の絵を元絵と比べる](#体の絵と顔の絵を元絵と比べるcheckbody_imagecheckface_image)）。ウエスト・太もも・ひざ・ふくらはぎ・足首の幅と裾などの高さ、体の絵 ÷ 元絵の表。`overlay` は輪郭を重ねた絵。合否は出さない。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.face_image` | reference, head → report [, sheet, zoom] | 頭の絵の顔を元絵の顔と、両目の位置で合わせて並べる（同じ節）。両目は瞳の色の決まり `iris` のかたまりの組か手で。目の間隔をそろえ、目から下に目盛りを引いた絵。合否は出さない。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.three_view` | front, side, back → report [, sheet, heads] | 三面図の同じ部位の高さがそろっているかを測る（[三面図の高さを比べる](#三面図の高さを比べる頭の絵の目から下を詰めるcheckthree_viewtextureface_length)）。いちばん上と下・いちばん広い行・印の行（色の決まりの帯）を全高に対する割合で、`iris` で頭のあたりのかたまり。並べた絵と頭の拡大の絵。合否は出さない。numpy と Pillow だけで、同じ入力なら再利用する |
| `texture.face_length` | image → image [, report] | 頭の絵（透けつきの PNG）の目から下の帯だけを、描き直さずに縦に `k` 倍にする（同じ節）。帯の両端では変えず、帯より上の見える画素は変えない（透けが 0 の画素の色は、既定では 0 になる。`above_band: copy` で元のまま）。Lanczos4（OpenCV の remap と同じ計算）。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.candidate` | model → report [, views, check, mesh] | 骨の無い 3D の候補（FBX・glTF・OBJ）を Blender で読み込み、体は床から腕の先までの高さを物差しに、切り口を面のつながりで輪にたどって、くびれ・脚の幅と厚みを測る（[骨の無い 3D の候補を測る](#骨の無い-3d-の候補を測るcheckcandidate)）。粘土の見た目で数方向から描いた絵。合否は出さない。Blender が要り、再利用しない（毎回実行） |
| `check.assembly` | blend [, illustration] → report [, views, bust, compare] | 組み立てたモデル（体・頭・髪が別の物体の .blend）の頭身を、足の裏からくびれまでの高さ U を物差しに測り、元絵の比率（`reference`）か体の側の目印（`anchor`）から頭と髪の大きさと位置の提案を出して、メモリの中で動かして測り直す（[組み立ての頭身を測る](#組み立ての頭身を測るcheckassembly)）。今の配置と動かした後に目印の高さの線を引いた絵と、元絵と並べた絵。元絵の比率・目印・頭身に既定は無い。合否は出さない。Blender が要り、再利用しない（毎回実行） |
| `texture.extract_glb` | glb → image [, report] | GLB（glTF 2.0 のバイナリ）から、材質のテクスチャの画像を取り出す（[GLB からテクスチャを取り出す](#glb-からテクスチャを取り出す)）。`slot`（既定 baseColor）と `material`（省くと全部の材質が同じ画像のときだけ）。既定は埋め込まれたバイトをそのまま書き、`decode: true` で出力の拡張子の形式に書き直す。外のファイル・拡張にしか無い画像・壊れた GLB は止める |
| `texture.apply_to_blend` | blend, texture → blend, report | .blend の材質の画像ノードのうち、`match` に合う画像だけを新しいテクスチャにつなぎ替えて詰め込み、別の .blend に書く（[.blend の画像を差し替える](#blend-の画像を差し替える)）。色空間と大きさがそろうこと・詰め込んだバイトがテクスチャと同じこと・合わない画像ノードと入力が変わらないことを確かめる。Blender が要り、再利用しない（毎回実行） |
| `texture.view_parts` | parts → occlusion [, preview, report] | 部品ごとの npz の部品を視点の正射影で描き、画素ごとにいちばん手前の部品の地図を作る（[手前の部品の地図と投影の遮り](#手前の部品の地図と投影の遮り)）。occlusion は `owner`・`depth`・`names`・`view` の npz で、`texture.project_reference` の入力になる。preview は部品ごとの色、report は部品ごとの三角形と手前の画素の数 |
| `texture.closed_eye_line` | image → image [, report] | 目を閉じた顔の絵の、閉じた目の線を 2D の描き方（下にたわんだ太い弧・目尻のはね・下向きのまつ毛）で描き直す（[表情](#表情頭の絵の差し替えと閉じ目の形modelclosed_eye_key)）。消す帯の中の元の線を肌の色で埋めてから描く。numpy と Pillow だけ |
| `texture.mouth_inside` | mesh, texture → texture, report | 色の絵の、口の中の面（唇の裏の袋）と部品（歯・舌など）が使う所だけを塗る（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。頭のメッシュ（`model.export_mesh` の npz）を正面から描き、口のまわりの見えない三角形と部品の uv の画素を、外から見える面の画素を除いて塗る。口のまわりの箱と部品の見つけ方に既定は無い。numpy と Pillow だけで、同じ入力なら再利用する |
| `texture.render_view` | mesh, texture → image [, report] | uv のあるメッシュにテクスチャを貼り、座標軸に沿った正射影（`view`）で描く。照明なし（色の確認と目印を読むため） |
| `texture.draw_mask` | [like] → mask | 多角形・楕円（`angle` で傾ける）を `value` で順に塗った灰色の画像。大きさは `size` か入力 `like` の画像。`feather` で縁をぼかす。知らないパラメーターは止める |
| `texture.project_reference` | mesh, texture, reference [, mask, occlusion, keep] → texture [, weights, report, overlay] | 参照画像を、目印で合わせた視点からテクスチャの画素へ写す（[設定画をテクスチャへ写す](#設定画をテクスチャへ写す)）。工程の `select` で役割の面だけを塗る。`occlusion`（`texture.view_parts` の手前の部品の地図）を渡すと、ほかの部品に隠れた所へ写さない（[手前の部品の地図と投影の遮り](#手前の部品の地図と投影の遮り)）。`color_match` で参照画像の色をモデルの色に合わせてから写す（[投影の色合わせ](#投影の色合わせ)）。顔の絵を写すときの追加の設定（見え隠れを見ない `visibility`・手前の裏向きの面にも写す `two_sided_depth`・面の向きで弱めない範囲 `core`・縁をぼかした範囲 `regions`）。1 画素も写らないとき・任意の `review_note` があるときは `needs_review`。知らないパラメーターは止める |
| `motion.prepare_keypoints` | keypoints → points [, report] | SAM 3D Body の 70 点（npz の `pred_keypoints_3d` + `pred_cam_t`）を整える。1 コマの跳ねの除去、平滑化（奥行きは窓を広く）、床の水平化（`pitch_deg` で上書き可）、上体を起こす（`torso_back_deg`）、接地の区間で足を止める（`contact`・`step_split`）。points は x 右・y 奥・z 上・m・床 z=0 の npz（[推定した 3D の点を整える](#推定した-3d-の点を整える)）。知らないパラメーターは止める |
| `motion.edit_keypoints` | points → points [, report] | 整えた 70 点を区間ごとに手直しする（[点を手直しする](#点を手直しする)）。`edits` に順に並べる: `head_turn`（顔の向きを足す）・`fist`（片手を握りに）・`forearm_roll`（両の拳が胸の前で回り合う動きを作り直す）。`stance` と `focal` は引き継ぐ。知らないキーは止める |
| `motion.retarget` | points, character [, skip_vertices, head_dpitch] → blend, pose, report | 整えた 70 点を、Mixamo 式の骨のキャラ（.blend）へ Blender の中で移す（[点をキャラの骨へ移す](#点をキャラの骨へ移す)）。`resolve` で手・前腕の胴への刺さりを直す。Blender が要り、再利用しない（毎回実行） |
| `motion.join_clips` | 動きの .blend を何本でも（名前は自由）→ blend, report | 別々に骨へ載せた何本かの動きを、切り替えのコマの表つきで 1 本の並びに組む（[別々に骨へ載せた動きを 1 本に組む](#別々に骨へ載せた動きを-1-本に組む)）。区間ごとに使う本（`clip`）と、その本の 0 秒目を置く秒（`offset_s`）を決め、範囲の外は最初か最後の姿勢で止める。`align: mean`（既定）で本ごとに腰の位置の平均を原点へ。報告の `cuts` は切り替えのコマ。Blender が要り、再利用しない（毎回実行） |
| `motion.twist_split` | blend → blend, report | 骨へ移した動きの前腕のねじれを、肘と手首に半分ずつ持たせ直す（[前腕のねじれを分け直す](#前腕のねじれを分け直す)）。手の向き・前腕の向き・指先の位置が変わらないことを確かめ、許容を超えたら止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.ground_feet` | blend, points → blend, report | 接地のコマ（points の `stance`）に足の一番低い点が床に着くよう、腰の高さを足す（[足を床に着ける](#足を床に着ける)）。足の点は `foot_objects` か足の骨の重み。直した後に測り直し、腰を動かした量だけ足が動かなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `check.arm_overlap` | blend → report | 左右の腕（前腕・袖口・手・指）のメッシュが互いの中に入り込んでいるコマを、変形した後の形で調べる（[腕どうしの入り込みを測る](#腕どうしの入り込みを測るcheckarm_overlap)）。肌が肌に・袖が肌に・肌が袖に・袖が袖にの頂点の数と深さ、重なりのあるコマの数と区間。`max_frames` を渡したときだけ合否。Blender が要り、再利用しない（毎回実行） |
| `motion.push_hands` | blend → blend, report | 手が体（胴・腰まわり・脚）に沈むコマに、二の腕を肩のまわりに回して手を体の面から浮かせる（[手を体から浮かせる](#手を体から浮かせる)）。肘から先は変えない。手と体の点は骨の重み（体は `body_objects` でも）。`passes` 回くり返し、沈みを直す前・後で数える。Blender が要り、再利用しない（毎回実行） |
| `motion.spring_bones` | blend → blend, report | 鎖の骨（髪など。`chains` に根元の骨）を、ばねで体の動きに遅れて揺らし、回転のキーに焼き付ける（[鎖の骨をばねで揺らす](#鎖の骨をばねで揺らす)）。狙いは親に固く付いた形（`rest`。`follow` で骨と高さの並びに沿わせる）か、鎖にある動き（`animation`）。当たり（`colliders`）・鎖のつなぎ（`links`）。焼き付けた骨の向きと根元を確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.sleeve_bones` | blend → blend, report | 袖口の骨（`sleeves` の `bone`）を、関節（`follow`。前腕など）の曲げの `ratio` 倍に、減衰つきのばねで少し遅れて付いて行かせる（[袖口を曲げに遅れて付いて行かせる](#袖口を曲げに遅れて付いて行かせる)）。ねじれは入れない。焼き付けた向きを確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.ribbon_bones` | blend → blend, report | 帯の垂れの鎖の骨（`chains`。長さは鎖ごとに違ってよい）を、重力で垂れて太ももの上を前へ乗り越えるように揺らす（[帯の垂れを揺らす](#帯の垂れを揺らす)）。狙いは `follow` に `thigh`（下ほど太もも）を混ぜられる。当たりは `colliders` と、メッシュの輪切りから作る `fitted`。押す向きは腰（`hips`）の前と上へ寄せる。焼き付けた骨を確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `cloth.skirt_proxy` | blend → blend, report | 脚に当たる裾を、裾の内側の面をなぞった目の粗い筒（身代わり）の布の計算で動かし、細かい裾は Surface Deform で筒に貼り付ける（[衣装を布で動かす](#衣装を布で動かす)）。当たりは体から脚の頂点グループの大きな島だけ。切れ目の延長・追加の切り込み・丈の切り落とし・助走のキー・床。既定の値は 2 作目の VRM のダンスで決めた値。Blender が要り、再利用しない（毎回実行） |
| `cloth.smooth` | blend → blend, report, proxy_cache [, cache] | 布を焼いた筒（と細かい裾）の動きを、骨から見た座標で時間方向にならし（1 コマだけの飛びを中央値で消してからガウス）、頂点の動きのファイル（.pc2。.blend と同じフォルダ）に置き換える（[布の動きをならして頂点の動きのファイルに置き換える](#布の動きをならして頂点の動きのファイルに置き換えるclothsmooth)）。ならす前後のずれを報告に出す。Blender が要り、再利用しない（毎回実行） |
| `model.body_shape` | blend → blend, report | 体つきを部位ごとに広げる（[体つきを部位ごとに広げる](#体つきを部位ごとに広げるmodelbody_shape)）。初期姿勢で、`limbs` の骨は骨の軸からの距離を骨に沿った位置ごとの倍率で、`torso` の骨は高さごとの中心線から幅と厚みを別々の倍率で広げ、ほかの骨の所は動かさない（頂点グループの重みで混ぜる）。`bands`（骨 1 本だけにほぼ付いた小さな島。腕輪など）は島ごとに 1 つの倍率、`band_shift` で腕輪を骨に沿って動かし、肌の継ぎ目も腕輪の下へ動かす。`follow_torso` の物体は胴と同じ広げ方。Blender が要り、再利用しない（毎回実行） |
| `check.body_size` | blend → report | 体の太さを初期姿勢で測る（同じ節）。腕・脚は骨に沿った位置ごとの直径（骨の軸に垂直な広がりの大きい 2 つの向き）、胴は高さごとの幅と厚み（cm）。測るだけで合否は出さない（`report_only` を付けると要確認にしない）。Blender が要り、再利用しない（毎回実行） |
| `model.twist_bones` | blend → blend, report | ねじれを分ける補助の骨を足す（[ねじれを分ける補助の骨](#ねじれを分ける補助の骨modeltwist_bones)）。骨（上腕など）と同じ位置に、Damped Track で先の骨を向くだけの「ねじらない骨」を足し、メッシュの重みを骨に沿った位置で元の骨と分ける（付け根に近いほど足した骨へ。腕輪などの小さな島は島の平均）。`roll`（前腕など）で、先の側に手の軸のまわりの回転を写す骨も足す（重みは 3 つに折れ線で分ける）。`min_weight`・`max_influences`（VRM は 1 頂点 4 本まで）。動きは変えない。拘束は VRM 1.0 の aim・roll として書き出せる形。Blender が要り、再利用しない（毎回実行） |
| `check.twist` | blend → report | 骨が自分の軸のまわりにどれだけねじれているかをコマごとに測る（同じ節）。骨ごとの最小・最大・`over_deg`（既定 60° と 90°）を超えたコマの数・大きいコマ。`max_twist_deg` を渡したときだけ合否を出す。Blender が要り、再利用しない（毎回実行） |
| `motion.bake_roll` | blend → blend, report | `model.twist_bones` の roll の骨の回転を、拘束に頼らず正確な値でキーに焼き、拘束を止める（[手の回転を写す骨を焼く](#手の回転を写す骨を焼くmotionbake_roll)）。写す骨の軸 Y のまわりのねじれ（VRM の roll と同じ決め方）。踊りの .blend だけ。Blender が要り、再利用しない（毎回実行） |
| `check.arm_pinch` | blend → report | 腕の関節のまわりのメッシュが、ねじれで絞ったように細くなっていないかをコマごとに測る（[腕の関節の細りを測る](#腕の関節の細りを測るcheckarm_pinch)）。輪ごとの太さの比（初期姿勢で割る）の最小・5% 点・中央値・細いコマの数。`min_ratio` を渡したときだけ合否。Blender が要り、再利用しない（毎回実行） |
| `model.recolor_part` | blend → blend, texture, report | 1 つの部品（`object`）のメッシュが使う uv の範囲だけ、色の絵に色ごとの倍率 `gain` を掛ける（[色の絵を塗り替える](#色の絵を塗り替えるmodelrecolor_partmodelrecolor)）。`dark_below` を渡すと、それより暗い画素だけ。塗り替えた絵は PNG に書いて詰め込む。Blender が要り、再利用しない（毎回実行） |
| `model.recolor` | blend → blend, report | 色の絵の、色相の範囲だけを補正する（同じ節）。`rules` ごとに、名前に `image` を含む絵の色相・彩度・明るさを変え、範囲の外は変えない。Blender が要り、再利用しない（毎回実行） |
| `model.remove_parts` | blend → blend, report | 部品（物体）を外す（同じ節）。骨と揺れものの設定は残す。シーンに無い名前は要確認。Blender が要り、再利用しない（毎回実行） |
| `model.smooth_skin` | blend → blend, texture, report | 肌の島の、選んだ範囲の溝（形）と焼き込まれた明暗（色）をならす（[肌の島を塗り直す](#肌の島を塗り直すmodelsmooth_skinmodelclean_skin)）。肌色の頂点をいちばん多く含む島だけを相手に、高さ・横の距離・横を向いた面のなだらかな重みで、色は頂点の色をならして塗り直し、形は縮まないならし方（Taubin）。Blender が要り、再利用しない（毎回実行） |
| `model.clean_skin` | blend → blend, texture, report | 外した部品の下の肌のしみを消す（同じ節）。手を含む島の、上腕・前腕などの頂点のうち肌色でないものを、まわりのきれいな肌の色で埋めて塗り直す。Blender が要り、再利用しない（毎回実行） |
| `model.face_shape` | blend → blend, report | 顔の形を参照の絵の比率へ寄せる（[顔の形を参照の比率へ寄せる](#顔の形を参照の比率へ寄せるmodelface_shape)）。頭のメッシュの頂点を、正面の視点の画素で書いた「今の高さ → 上へ動かす量」の表で上へ寄せ、顔の正面だけに効かせる（`narrow` であご先を細く）。Blender が要り、再利用しない（毎回実行） |
| `model.closed_eye_key` | blend → blend, report | 目を閉じたとき用の形状キー（既定 `eyes_closed`、値 0）を頭に足す（[表情](#表情頭の絵の差し替えと閉じ目の形modelclosed_eye_key)）。目の穴の奥の眼球の面を穴の縁の膜の位置まで手前に出し、まつ毛の板などは頭の中へ引っ込める。`render.passes` の `expressions` で、閉じた目の絵の間だけ 1 にする。Blender が要り、再利用しない（毎回実行） |
| `model.export_mesh` | blend → mesh [, texture], report | 物体のメッシュ（頭など）を、Blender の外で計算するための npz に書き出す（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。頂点の基本の形の世界の座標（m）・辺・三角形とその面の角・uv・法線。`texture` で、その物体の材質が使う色の絵を PNG で。.blend は変えない。Blender が要り、再利用しない（毎回実行） |
| `model.face_shapes` | mesh → shapes, report [, debug] | 頭のメッシュ（`model.export_mesh` の npz）から、表情の形状キーの頂点の動きを計算する（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。まばたきと笑顔の閉じ目は、正面から見える目の開きの上下の縁を測って閉じる線へ寄せる。眉は部品をそのまま、口角は口の端のまわりだけ動かす。部品を見つける箱と動かす量に既定は無い。numpy だけで、同じ入力なら再利用する |
| `model.shape_keys` | blend, shapes → blend, report | 計算した形（`model.face_shapes` の npz）を、物体の形状キーとして入れる（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。世界の座標の動きを物体の中の座標へ移して基準のキーの形に足す。並びは npz の並び、値は 0。同じ名前のキーがあれば止める（`replace` で入れ直す）。VRM の表情への割り当ては `vrm.expressions`。Blender が要り、再利用しない（毎回実行） |
| `model.neck_junction` | blend → blend, report | 首と頭のつなぎ目を直す（[首と頭のつなぎ目を直す](#首と頭のつなぎ目を直すmodelneck_junction)）。体のメッシュの首の上の端を頭の骨へなめらかに付け替え、首の中から頭の中へ伸びる栓（ふた付きの筒）を足して、首の肌の 1 色にする。首の直しの量に既定は無い。Blender が要り、再利用しない（毎回実行） |
| `look.toon` | blend → blend, report | キャラに描く前のセル調の塗りと輪郭線を付ける（[セル調の塗りと輪郭線を付ける](#セル調の塗りと輪郭線を付ける)）。体は決まった向きの光で影を 2 段に分け、舞台の色と縁の光を足して Emission で出す。顔（`faces`）は光を少し受けて絵の色を出す。輪郭線は反転ハル。舞台の色と縁の強さは共有のノードグループ `model3d_stage_tone`。Blender が要り、再利用しない（毎回実行） |
| `look.lit` | blend → blend, report | キャラ（VRM の MToon の材質でもよい）の材質を、舞台の光の向きと色を受ける塗りに差し替える（[舞台の光を受ける塗り](#舞台の光を受ける塗りlooklit)）。絵の色 ×（まわりの明るさ ＋ 光の当たり具合を 2 段に分けたもの × 光の色あい）を Emission で出す（EEVEE でだけ働く）。元の材質は書き換えず、材質ごと新しく作る。まわりの明るさは材質ごとの RGB ノードで、描く工程がコマごとに入れる。Blender が要り、再利用しない（毎回実行） |
| `motion.bone_path` | blend [, track] → path | 動きのコマごとに、骨（既定は腰と頭）の付け根の位置を m で測る（[段取りを組む](#段取りを組む)）。`points` を書くと、骨から決めた点（顔・胸・手の先など。[骨から決めた点](#骨から決めた点motionbone_path-の-points)）も測る。track（描くコマの並び）があればその動きのコマを重ねず小さい順に、無ければ `frame_range` か .blend のシーンの全コマ。path は `model3d-bone-path/0.1`。Blender が要り、再利用しない（毎回実行） |
| `plan.light_keys` | audio → keys | 曲の音（WAV など）から、`plan.stage` が光に使う光のキー（`model3d-light-keys/0.1`）を作る（[段取りを組む](#段取りを組む)）。窓（`start` 秒から `frames` コマ）のコマごとに、低・中・高の帯の強さ（0〜1。立ち上がりは即座、下がりはゆっくり）と、拍の格子 `grid`（必須）に入る拍。既定は試作の解析で、numpy だけで計算する（librosa.stft と同じ値）。計算だけで、同じ入力なら再利用する |
| `plan.lights` | plan [, keys] → track [, report] | 光の段取り表（`model3d-light-plan/0.1`）から、コマごとの光の値のトラック（`model3d-light-track/0.1`）を作る（[光の台本](#光の台本planlights)）。`values` の既定を、t を含む区間の `let` と `set` で上書きし、閃光（強い方を残す）を重ねる。`flash`・`flash_color`・`fade` はどのトラックにもある。値は数・3 つの並び・小さな式（決めた名前と関数だけ。`eval` は使わない）。拍の格子と fps は光のキー（`plan.light_keys`）から。計算だけで、同じ入力なら再利用する |
| `plan.retime` | → track | 動きの fps（`motion_fps`。例 24）と違う fps（例 30）で描くコマの並び（T と f だけのトラック）を作る（[動きと違う fps で描く](#動きと違う-fps-で描く小数のコマ)）。f = `start` + (T − 1) × `motion_fps` ÷ `fps` で、小数のコマは前後のコマの間の時刻。`plan.stage` の cut、`motion.bone_path` と `render.passes` の track に渡せる。計算だけで、同じ入力なら再利用する |
| `plan.stage` | plan, keys, bones [, cut] → track [, report] | 段取り表（`model3d-stage-plan/0.1`）と光のキー（`model3d-light-keys/0.1`）と骨の通り道から、`render.stage` のトラックを作る（[段取りを組む](#段取りを組む)）。コマごとに、カメラ（区間・動き・寄りと揺れ）、壁の模様、光と塗りの縁の値を決める。cut（T と f のトラック）があればその並びで。計算だけで、同じ入力なら再利用する。report は区間ごとのコマの範囲 |
| `plan.shots` | plan, bones → cameras, track [, alt, alt_track, report] | 2D の絵コンテのショット表（`model3d-shot-plan/0.1`。1 枚の絵の仮の部屋の中のカメラの移動・向き・寄りと、追う点を置く画面の画素、回り込み、手直し）を、追う点がちょうど狙いの画素に来るように主点を解いた 3D のコマごとのカメラに写す（[絵コンテのカメラを 3D に写す](#絵コンテのカメラを-3d-に写すplanshots)）。部屋の置き方は `render.box_room` と同じ。つなぎの相手のカメラ（`alt`）と揺れなしの基準のカメラも。計算だけで、同じ入力なら再利用する |
| `plan.orbit` | plan, bones → cameras, track [, report] | 踊り手を中心にした極座標のカメラの段取り表（`model3d-orbit-plan/0.1`）と、骨から決めた点の通り道から、コマごとのカメラ（`model3d-cameras/0.1`。行列・焦点距離・主点・踊り手の点）とコマの並び（T と動きのコマ f）を作る（[踊り手を中心にしたカメラ](#踊り手を中心にしたカメラplanorbit)）。区間ごとの方位・距離・高さ・狙う点・画角・ロールのキーとつなぎ方、手持ちの揺れと打点の揺れ、踊りの時刻の止め。狙う点のまわりの仰角・レンズの mm・区間の平均と混ぜる狙い（follow）・区間の中だけのならし・絞りとピント（cameras の `fstop`・`focus_m`）も書ける。`render.passes` の track と cameras にそのまま渡せる。計算だけで、同じ入力なら再利用する |
| `plan.scout` | bones → cameras, track [, report] | 向きの下見（[向きの下見](#向きの下見planscout)）。拍ごと（`beats` と `grid`）か時刻ごと（`times`）の姿勢を、踊り手のまわりのいくつかの方位（`angles`）から見るコマごとのカメラ（`model3d-cameras/0.1`）とコマの並びを作る。コマ T = 1 + 行 × 列の数 + 列。`render.passes` でそのまま描き、`mv sheet` で拍（行）× 方位（列）に並べる。計算だけで、同じ入力なら再利用する |
| `render.stage` | blend, track → frames, report | キャラを舞台と光の中でコマごとに PNG に描く（[舞台で描く](#舞台で描く)）。床・LED の壁・トラス・光の柱とスポット 4 つ、EEVEE と光のにじみ。トラック（`model3d-stage-track/0.1`）のコマの並びとカメラ・光・塗りの値で描き、無ければ腰を追う正面のカメラと動かない光。輪郭線の太さはカメラの距離で画面の上にそろえる。舞台を切って `background` で緑背景か透けにも描ける。`push` で、描く直前に帯などの頂点を当たり（閉じたメッシュ）の外へ出す。描いたコマはとばす（続きから描ける。同じ入力で描いたコマだけ。[入力の控え](#コマのフォルダの入力の控え)）。frames は描いたコマの一覧（`model3d-frames/0.1`）。Blender が要り、再利用しない（毎回実行） |
| `render.prepare_push` | blend → blend, report | `render.stage` の `push` の準備（[押し出しを準備する](#押し出しを準備する)）。`colliders`: 元のメッシュ（脚など）を写して穴をふさぎ、外向きの閉じた当たりにして面を減らし、元と同じ骨で動かす（描かない）。`groups`: 骨（`bones` とその子孫）に重みのある頂点を種に、高さ `top` より下でつながった部分（帯の垂れなど）を選び、上の端から `fade` で重みを付けて、法線の前と後ろで `front` と `back` の頂点の組にする。Blender が要り、再利用しない（毎回実行） |
| `render.passes` | blend, track [, cameras] → passes, cameras, report | キャラを要素に分けて簡易描画（Workbench）でコマごとに描き、カメラと踊り手の点を控える（[要素別に描く](#要素別に描く)）。room（部屋の物体）・char（人物の色の絵のまま。透けの PNG）・shade（白い人物にスタジオの光）・disc（踊り手の点の真下の床の円）・head（顔の物体だけ白）。`expressions` で、頭の絵を時刻の表で差し替え、表情の形状キーを切り替える（形状キーの値を時刻のキーで動かす `values` も）。カメラはトラックか、cameras（`model3d-cameras/0.1`）の行列・焦点距離・主点。重ね合わせは Blender の外で、`render.stage` と並ぶ 2 つ目の描き方。描いたコマはとばす（続きから描ける。同じ入力で描いたコマだけ。[入力の控え](#コマのフォルダの入力の控え)）。Blender が要り、再利用しない（毎回実行） |
| `render.show` | blend, cameras [, lights, hooks] → frames, report | 演出版を Blender 5.x の EEVEE で 1 回でコマごとに PNG に描く（[演出版を 1 回で描く](#演出版を-1-回で描くrendershow)）。カメラ（`model3d-cameras/0.1`）は全部のコマのキーにし、カットの最後のキーを止めるので、動きのぶれ（コマから後ろ）がカットをまたがない。`fstop`・`focus_m` のコマは被写界深度。光のトラック（`plan.lights`）で、名前で指した光の強さと色と、`look.lit` の塗りのまわりの明るさをコマごとに入れる。表情・空・にじみ。作品ごとの舞台はフック（Python の台本。レシピに書いたときだけ実行し、sha256 を残す）で作って動かす。描いたコマは毎回描き直す。Blender が要り、再利用しない（毎回実行） |
| `render.box_room` | blend, image → blend, report | 1 枚の背景の絵を、基準のカメラ（焦点距離・主点・床からの高さ）から床と奥の壁に投影して張った箱の部屋を .blend に足す（[箱の部屋](#箱の部屋)）。基準のカメラから見ると絵と同じ画で、カメラが動くと奥行きが出る。踊り手の立つ所（絵の画素）が世界の原点。絵の届かない向きは暗い箱。物体は `collection`（既定 `model3d_room`）に入れ、`render.passes` の `room.collection` で描く。Blender が要り、再利用しない（毎回実行） |
| `render.part_colors` | blend → frames, report | 部位を色分けして描く診断の絵（[色分けの診断の絵](#色分けの診断の絵renderpart_colors)）。`bone_meshes` は頂点ごとにいちばん重みの大きい頂点グループの名前の頭（`bone_colors`）で、ほかは物体ごと（`object_colors`）。`show` で描画から隠した物も描く。カメラは狙う点の方位 `yaw` から水平に。frames は描いた絵の一覧（`model3d-frames/0.1`。`check.frames` で比べられる）。.blend は変えない。Blender が要り、再利用しない（毎回実行） |
| `render.face_sheet` | blend → sheet, report | 表情の一覧の絵（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。頭の形状キーの組み合わせ（`combos`）ごとに、いくつかの向き（`views`。方位・高さの角・正射影の幅・狙う点）から EEVEE で寄りで描き、表情（行）× 向き（列）に並べる。正面だけでは気づけない、斜めから白目がのぞくなどを見るため。`hide` の物体（髪など）は描かない。.blend は保存しない。Blender が要り、再利用しない（毎回実行） |
| `render.title` | [frames] → frames [, report] | 描いたコマ（frames か、`frames_dir` と `range`）に題字の層（文字か画像）を重ねて、別のフォルダへ書く（[題字を重ねる](#題字を重ねる)）。窓の秒の `start` から `fade_in` 秒で浮かび上がる。文字は書体・形・大きさ・色・縁取り・字送り、層ごとに光り、全体に影。重ね方は既定で試作と同じ（層ごとの `blend` で `over` も選べる）。大きさは基準の幅（既定 1080）の画素。前のコマはそのまま写す。書いた一覧は `render.assemble` の入力になる。書体と画像は隠れた入力なので再利用しない（毎回実行） |
| `render.assemble` | [frames, audio] → video [, report] | 描いたコマ（frames か、`frames_dir` と `range`）と曲の音を、1 本の動画にする（[動画にまとめる](#動画にまとめる)）。BT.709 の式で YUV にして BT.709 の印を付け、H.264 と AAC で書く。音とフェードは窓の秒で書く。抜けたコマ・とびのある T・描いた後に変わったコマは止める。ffmpeg が要り、再利用しない（毎回実行） |
| `check.video_frames` | video [, frames] → report | 書き出した動画からコマを取り出して、もとのコマと比べる（[動画の中身を確かめる](#動画の中身を確かめるcheckvideo_frames)）。動画の n 番目のコマを T = first + n として BT.709 で RGB に戻し、T と T ± offset のコマとの色の差の平均を出す。どれも同じ T にいちばん近く、前後のコマより十分近ければ合格、ずれたコマや古い版の動画なら不合格。ffmpeg が要り、再利用しない（毎回実行） |
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

交わりを調べる三角形は、光線の向きに垂直な面の格子で絞る（`check.penetration` の `search: grid`。既定。`collision.push_out` も同じ）。
三角形をその面に写し、格子の目ごとに、写した範囲が掛かる三角形を並べておく。点は、写した位置の目の三角形とだけ交わりを調べる。
光線が通る三角形は、写すと必ず点を含むので候補から漏れない（範囲は少し広げてある）。交わりの式と距離の畳み方は総当たり（`search: all`）と
同じで、答えも同じになる（凹凸のある閉じた球と立方体で、頂点・辺・面の上の点や四角形の対角線を通る光線も含めて確かめた）。
凹凸のある球（25,280 三角形）と 20,000 点で、総当たりの 55.3 秒が 0.24 秒になった（この PC、3 方向）。いちばん近い面を探す所
（補正の行き先と深さ）は、総当たりのまま。

**限界**: `check.penetration` が数えるのは、相手（閉じたメッシュ）の中に入った頂点だけ。脚がまるごと布を通り抜ける失敗（布の頂点は
どれも脚の外にあり、脚の面が布の面を突き抜ける）は数に出ない（2 作目の VRM のダンスで、数は減っていたのに、左脚が布をまるごと
通り抜けていた）。目で見分けるには、色分けの診断の絵（[render.part_colors](#色分けの診断の絵renderpart_colors)）を使う。

## 部品の法線をならす

`mesh.smooth_normals`（メッシュ 1 つ）と `mesh.smooth_part_normals`（部品ごとの npz の全部の部品）は、頂点の法線をならす。セル調の影の境が、面の細かな凹凸で荒れないようにするため。リポジトリ外の試作で、部品の法線（`n{i}`）を作った台本を移した。

**計算**（どちらも同じ）:
1. 格子 `weld` に丸めて同じになる頂点を、1 つにまとめる。UV の継ぎ目で分かれた頂点は同じ座標なので、同じ法線になる。距離で結ぶ `mesh.weld` と違い、格子の境をまたぐ近い点は別のまま（試作と同じ）。
2. まとめた頂点で、面積で重みを付けた法線を作る（三角形の外積を頂点ごとに足し、長さ 1 に）。四角形以上の面は、面の平面で三角形に分けて足す。
3. `iterations` 回、辺でつながった隣の法線の和に自分の法線を足し、長さ 1 に戻す。
4. まとめた法線を、元の頂点へ配る。
- 和は float64 で取り、試作（scipy の疎行列の積）と同じ順に足す。model3d は scipy に頼らない（numpy だけ）。

**設定**:
- `iterations`（必須、0 以上の整数）: ならす回数。0 なら面積の重みの法線のまま。部品ごとの回数（メッシュの細かさで決めた値）は、リポジトリ外の設定に書く。
  - `mesh.smooth_part_normals` では、全部の部品に同じ整数か、`{部品の名前: 回数}`（npz の全部の部品を書く。足りない・余る名前は止める）。
- `weld`: まとめる格子の幅。既定は 1e-5 m（`mesh.smooth_normals` はメッシュの単位に換算。部品の npz は m とみなす）。

**出力**:
- `mesh.smooth_normals`: 同じメッシュに `x_normals`（頂点ごと）を書く。頂点・面・uv・parts は変えない。
- `mesh.smooth_part_normals`: `n0`, `n1`, …（float32、頂点ごと）の npz。`mesh.import_parts` の入力 `normals` に渡せる。
- `report`（任意）: `model3d-normals/0.1`。部品ごとの頂点の数・まとめた数・回数・格子、ならしで向きが変わった角度（平均と最大）、法線が 0 のままの頂点の数、面の無い頂点の数。
- 向かい合う 2 枚の面が重なる所や、面の無い頂点の法線は 0 のまま返し、数を報告に載せる（直さない）。

**確かめたこと**:
- CI では、次を確かめた。
  - 格子でのまとめ（辞書順の番号）、面の縁の辺。
  - 継ぎ目で分けた立方体が、まとめた立方体と同じ法線になること（外向き、長さ 1）。
  - 回数とともに、でこぼこの面の法線が上へそろうこと。
  - 試作の書き方（scipy の疎行列の積）と、ビット単位で同じ値になること（継ぎ目と極の重なる球、つぶれた三角形を含む。scipy があるとき）。
  - 0 の法線と面の無い頂点を数えること、工程の入出力（単位の換算、`mesh.import_parts` で読めること）、悪い設定を止めること。
- 2026-11-08 に、リポジトリ外の試作の部品（11 部品、約 105 万頂点）を試作と同じ回数でならした。試作の法線（`n{i}`）と、全部の部品で float32 の値がビット単位で同じだった（約 14 秒）。

## 穴をふさぐ

`mesh.fill_holes` は、穴（1 つの面だけが使う辺の輪）を面でふさぎ、閉じたメッシュにする。`collision.push_out` や `check.penetration` は閉じた当たりしか受け付けないので、筒の形の部品（脚など）を当たりにするときに使う。リポジトリ外の試作で、脚の当たりを作った手順のうち、穴ふさぎを Blender なしで移した（Blender の中でする分は `render.prepare_push`）。

**手順**:
1. つぶれた辺（同じ頂点を続けて使う面）があれば止める（先に `mesh.weld` で退化面を除く）。
2. 隣り合う面の向きをそろえる（共有する辺を互いに逆向きに使うように、面の頂点の並びを逆にする。試作の Blender の `recalc_face_normals` と同じ役目）。辺でつながった集まりごとに、裏返す面が少ない方にそろえる。
3. 穴の縁の輪を、面の向きにたどる。いちばん小さい頂点の番号の順に、その頂点から並べる。
4. 輪ごとに、縁を逆にたどる面でふさぐ（隣の面と向きがそろう）。
   - `cap: "polygon"`（既定）: 輪ごとに 1 つの多角形。頂点を足さない（試作の Blender の `holes_fill` と同じ形）。
   - `cap: "fan"`: 輪の頂点の平均に頂点を 1 つ足し、三角形の扇でふさぐ。平らでない穴に向く。足した頂点の uv と頂点色（`x_vertex_colors`）は、輪の平均（入力の色の並びは変えない）。
5. `orient`（既定 true）: 辺でつながった面の集まりごとに体積を測り、負（内向き）なら、その集まりの面を裏返して外向きにする。
   - 頂点だけでつながった 2 つの殻は、別々に測って向きを直す（面の向きは辺のつながりでしか決まらないため）。

**止める形**（どの面でふさぐかが決まらない）: 3 つ以上の面が使う辺、2 つ以上の穴の縁が通る頂点（蝶ネクタイ）、裏表のない面（メビウスの帯のように、向きをそろえられない）。

**そのほかの設定**:
- `max_sides`: この数より多い辺の穴はふさがずに残す（0 は全部ふさぐ。既定 0）。残した穴があれば、工程は要確認にし、向きの直し（`orient`）もしない。
- `part`: ふさいだ面を、この名前の `parts` に入れる（省くと `parts` は変えない）。
- 今の面と頂点の番号は変えず、ふさぐ面（と扇の頂点）を後ろに足す。頂点の法線 `x_normals` は、ふさいだ形に合わないので外す。

**出力**:
- `mesh`: ふさいだメッシュ。
- `report`（任意）: `model3d-fill-holes/0.1`。中身は次のとおり。
  - 輪ごとの頂点と辺の数、ふさいだか
  - ふさいだ辺・足した面と頂点・残した辺の数、裏返して向きをそろえた面の数
  - 閉じたか（ふさいだ後に開いた辺と 3 面以上の辺の数）
  - 体積（メッシュの単位と L）、辺でつながった面の集まりの数（`components`）と、外向きへ裏返した集まりの数

**確かめたこと**:
- CI では、次を確かめた。
  - 開いた筒の穴の縁が 2 つの輪になること。多角形でふさぐと頂点を足さず、体積が角柱と同じになること。扇は頂点を 2 つ足し、uv を付けること。
  - 内向きの筒を外向きに裏返すこと。1 枚だけ向きの逆な面をそろえること。
  - 頂点だけでつながった外向きと内向きの殻（四面体 2 つ）を、別々に外向きにすること。
  - 扇で足した頂点に、輪の頂点色の平均が付くこと（入力の色の並びは変えない。多角形では色の数が変わらない）。
  - 大きい穴を残すと要確認になること。止める形（多様体でない辺・蝶ネクタイ・メビウスの帯・つぶれた辺）。
  - ふさいだ筒が `collision.push_out` の当たりになり、中の点を外へ出すこと（ふさぐ前の筒は「閉じていない」で止まる）。
- 2026-11-08 に、リポジトリ外の試作の脚の部品 2 つ（`mesh.import_parts` → `mesh.weld` 2e-5 m → `mesh.fill_holes`）で確かめた。
  - ふさいだ辺の数は 453 本と 540 本で、試作の記録と同じ。どちらも閉じた（開いた辺 0・3 面以上の辺 0）。片方は、向きの逆な面 1 枚をそろえた。
  - 体積は 6.48 L と 6.14 L。試作の Blender（6.44 L と 6.21 L）と 1% ほど違う。穴の縁は平らでないので、多角形の面を三角形に分ける仕方で体積が変わるため（試作どうしでも、Windows と Linux で 6.44 L と 6.40 L のように違った）。

## GLB からテクスチャを取り出す

`texture.extract_glb` は、GLB（glTF 2.0 のバイナリ。生成器が書く 3D モデル）から、材質のテクスチャの画像を 1 枚取り出す。GLB は標準ライブラリだけで読む（形や骨は読まない）。リポジトリ外の試作で、生成器の GLB から色のテクスチャ（アトラス）を取り出した台本を移した。試作は材質 0 の色だけを Pillow で書き直したが、ここは材質と種類を選べ、既定は埋め込まれたバイトをそのまま書く。

**選び方**:
- `slot`: どのテクスチャか。`baseColor`（既定）・`metallicRoughness`・`normal`・`occlusion`・`emissive`。
- `material`: 材質の番号か名前。省くと、`slot` を使う全部の材質が同じ画像を指すときだけ取り出す（部品ごとに材質が分かれた GLB では、材質ごとに画像が違うので止まり、材質を並べて知らせる）。
  - 材質を選んだときは、その材質の画像だけを確かめる（ほかの材質の、拡張にしか無い画像では止めない）。
  - 名前で選んで、同じ名前の材質が違う画像を指すときは、どれか決めずに止める（番号で選ぶ）。同じ画像なら、その材質を全部、使い手として報告に載せる。

**書き方**:
- 既定（`decode: false`）: 埋め込まれたバイトを、そのまま書く（ハッシュが GLB の中と同じ）。出力の拡張子は画像の種類（`mimeType`）に合わせる（png・jpg / jpeg・webp・ktx2）。合わなければ止める。
- `decode: true`: Pillow で開き、出力の拡張子の形式で書き直す（JPEG を PNG にするなど。画素は開いたまま）。透けのある画像を JPEG には書けないので止める。

**読む所と止めること**:
- 画像は BIN チャンクの中（`bufferView`）か、`data:` の URI だけを読む。外のファイルを指す URI、GLB の外のバッファ、拡張にしか無い画像（`KHR_texture_basisu`・`EXT_texture_webp` など）は止める。
- GLB の頭（`glTF`・版 2・長さ）とチャンクの長さを確かめ、壊れていれば止める。JSON と BIN は最初の 1 つを使い、知らない種類のチャンクは飛ばす。

**出力**:
- `image`: 取り出した画像。
- `report`（任意）: `model3d-glb-image/0.1`。中身は次のとおり。
  - GLB の sha256 と大きさ
  - slot、選んだ材質、同じ画像を使う材質の並び、テクスチャと画像の番号、画像の名前・`mimeType`・どこにあったか
  - 埋め込まれたバイトと書いたファイルの大きさと sha256、書き直したか
  - 画像の大きさとモード（Pillow で読めるとき）
- 入力の GLB だけで決まるので、レシピでは再利用する。

**確かめたこと**:
- CI では、次を確かめた（試しの GLB はテストの中で組む）。
  - GLB の頭とチャンクの確かめ（短い・違う magic・版 1・長さの不一致・途中で切れたチャンク・JSON が無い・読めない JSON）。
  - 材質と slot の選び方（同じ画像を使う材質、材質ごとに違う画像、番号と名前、無い材質と slot、拡張にしか無い画像。選んだ材質は、ほかの材質の拡張にしか無い画像に止められない。同じ名前の材質が違う画像を指せば止める）。
  - 画像の場所（BIN チャンク、data URI、外のファイル、外のバッファ、範囲を超える bufferView、BIN チャンクが無い）。
  - そのまま書いたバイトが埋め込まれたものと同じこと、JPEG を PNG に書き直した画素が開いたままであること、透けのある画像を JPEG に書かないこと、再利用、悪い設定。
- 2026-11-08 に、リポジトリ外の試作の GLB（生成器の 1 メッシュの版。材質 1 つ、画像 3 枚）で確かめた。
  - 色のテクスチャ（4096×4096 の JPEG、約 3.2 MB）をそのまま書くと、GLB の中のバイトと同じ。法線（PNG）も同じ。
  - PNG に書き直した画素は、試作の `basecolor.png`（OpenCV で開いて書いたもの）と全部同じだった。書いた PNG は、試作の作り直し（Pillow で書いたもの）とファイルまで同じ（sha256 が一致）。

## 部品の一覧と uv の伸び

`mesh.list_parts`（CLI は `model3d mesh parts`）は、部品ごとの npz（`names` と `co{i}`・`f{i}`・`uv{i}`。`mesh.import_parts` と同じ形）の中身を一覧にする。`check.uv_stretch` は、メッシュの uv がどれだけ伸びているかを測る。リポジトリ外の試作で、部品の一覧と 1 部品の uv の伸びを見た台本を移した（1 部品をメッシュに書き出す所は、`mesh.import_parts` に移してある）。

**部品の一覧**（`mesh.list_parts`）:
- 部品ごとに、番号・名前・頂点と面と三角形の数・面の角の数・範囲（3 軸）・uv の範囲。
- `normals`（`n{i}` の npz）を渡すと、部品ごとに法線があるか・頂点と同じ形か・有限かも見る。どの部品にも当たらない法線の配列も並べる。
- 見つけたこと（非有限の頂点・uv・法線、無い頂点を指す面、形の合わない uv や法線、法線の抜け）は `problems` に並べ、工程は要確認にする（CLI は終了コード 1）。止めはしない（一覧が目的なので）。
  - 非有限の値がある頂点や uv の範囲は書かない（報告の JSON に NaN は書けない）。非有限の法線は `mesh.import_parts` が読まないので、見つけたことに入れる。
- 報告は `model3d-parts/0.1`。pickle の入った配列は読まない。

**uv の伸び**（`check.uv_stretch`）:
- 三角形ごとに、テクスチャの画素での周長を 3D の周長で割り、その中央値で割った値（`texture.project_reference` の `max_stretch` と同じ値）。uv が島をまたいで引き伸ばされた三角形ほど大きい。
- 百分位（50・99・99.9・最大）、`threshold`（既定 8）を超える三角形の数、3D で潰れた三角形の数（3 つの頂点が同じ位置で周長が 0。比からではなく 3D の形から数えて外す。全部の三角形が潰れていても数えそこなわない）、三角形の uv の幅（画素の 99 百分位と最大）を報告する。
- テクスチャの大きさは、入力 `texture` の画像か、パラメーター `texture_size`（`[幅, 高さ]` か 1 つの数）。
- しきい値（`max_over`: `threshold` を超えてよい三角形の数、`max_degenerate`: 潰れてよい三角形の数）を渡したときだけ合否を出す。渡さなければ測るだけ（`report_only` で要確認にしない）。uv の無いメッシュは未実施。

**確かめたこと**:
- CI では、部品の一覧（三角形と四角形の部品・uv・法線の形違いと抜けと余り・非有限の頂点・非有限の uv と法線（報告を書けること）・無い頂点を指す面・工程と CLI の終了コード）と、uv の伸び（そろった格子は全部 1、伸ばした三角形と潰れた三角形の数え方、全部の三角形が潰れたメッシュ、しきい値の合否、テクスチャの大きさの取り方、悪い設定）を確かめた。
- 2026-11-08 に、リポジトリ外の試作の部品（11 部品）で確かめた。
  - 一覧は、試作の記録（作り直しの照合）と、11 部品とも頂点と三角形の数・法線の形が同じ（合わせて 1,052,420 頂点、1,984,871 三角形）。
  - 1 部品（約 68 万三角形）の uv の伸びは、試作の台本の出力と同じ（百分位 1.0・1.65・2.07・6.87、8 を超える三角形 0、uv の幅 12・26 画素）。

## コマのフォルダを比べる

`check.frames`（CLI は `model3d frames compare`）は、2 つのコマの並びを、同じ T どうしで画素で比べる。作り直した描画が採用版と同じか、設定を変えた描画がどこで違うかを、数で確かめるため。この資料の「画素まで同じ」は、これで測れる。リポジトリ外の試作で、2 つのコマのフォルダを比べた台本と、1 組を並べて見た台本を移した。

**コマの並び**: 描いたコマの一覧（入力 `left`・`right`。`model3d-frames/0.1`）か、コマのフォルダ（`left_dir`・`right_dir`。`f<T>.png`）。`range`（`[最初の T, 最後の T]`）で絞れる。
- 一覧なら、コマのファイルがあることと、一覧の sha256 が今のファイルと同じことを確かめ、違えば止める（一覧を作った後に描き直したフォルダを、古い一覧のまま比べない。`render.assemble` と同じ決まり）。

**比べ方**:
- 左を基準に、同じ T のコマを比べる。ファイルが同じ（sha256）なら、開かずに同じとする。
- コマごとに、次を出す。
  - 違う画素の数（色か透けのどれかが違う画素）、いちばん大きい値の差（0〜255）
  - 色の差の平均（R・G・B の 3 つで割る。試作の比べと同じ値）、透けの違う画素の数
- 全体では、次を出す。
  - コマの数と同じコマの数、違う画素と全部の画素の数
  - 差の大きさごとの画素の数（0・2・8・16・64・128 を超える）、違いの大きいコマ（`top`、既定 5）
- 右に無いコマ、大きさの違うコマも並べる。左に無いコマ（右にしか無いコマ）は並べるだけ（一部のコマを全部のコマと比べられる）。

**合否**: しきい値（コマごとの `max_differing_pixels`・`max_channel_diff`・`max_mean_abs`）を渡したときだけ合否を出す。そのときは、右に無いコマと大きさの違うコマも不合格にする。渡さなければ測るだけ（`report_only` で要確認にしない）。数を出す検査なので、採否は人が決める。

**見比べ**: 出力 `view`（CLI は `--view`）に、1 組のコマを [左 | 右 | 差] に並べた PNG を書く。差は、いちばん大きい値の差を `amplify` 倍（既定 8）にした明るさ。並べるコマは `view_frame`（既定は違う画素がいちばん多いコマ）。

**出力**: `report`（全部のコマの行つき）。工程の観測と CLI の表示は、行を除いたまとめ。CLI の `--out` で、行つきの報告を書く。コマは隠れた入力なので、工程は再利用しない。

**確かめたこと**:
- CI では、次を確かめた。
  - コマの並びの読み方（フォルダと一覧）と、同じファイルの近道。一覧のファイルが描き直されていたり無かったりすれば止めること。
  - 違う画素・いちばん大きい差・色の差の平均が試作の計算と同じこと。透けだけの違いを別に数えること。差の大きさごとの数。
  - 片方にしか無いコマと大きさの違うコマ、しきい値の合否（strict・要確認・report_only）、見比べの PNG、CLI の終了コード、悪い設定。
- 2026-11-08 に、試作の比べの記録（作り直した描画と採用版の 49 コマ）を再現した。
  - 合計（違う画素 5,639,119・全部の画素 11,289,600・いちばん大きい差 238・同じコマ 0）は記録と同じだった。
  - 49 コマ全部の行（違う画素・いちばん大きい差・色の差の平均がビット単位まで）と、違いの大きいコマの並びも同じだった。

## 部位のラベルを移す

`mesh.transfer_parts` は、移し先のメッシュの面ごとに、重心がいちばん近い移し元の面の部品を付ける。生成器は、部品に分けた版（分割版）と分けていない版（テクスチャの細かい元の版）を別々に出すことがある。元の版の面に、分割版の部品の番号を付けるため。リポジトリ外の試作で、部品の番号を付けた台本を移した。

**入力**:
- `source`（移し元）: 部品ごとの npz（`names` と `co{i}`・`f{i}`。`mesh.import_parts` と同じ形）か、`parts`（面の集合）のある model3d のメッシュ。2 つの `parts` に入った面があれば止める。どの `parts` にも入らない面は、移し元に使わない。npz の部品の名前が重なっていれば止める（名前で `parts` を書くので、重なると片方の面が消える）。
- `target`（移し先）: 1 つのメッシュの npz（`co` と `faces`）か、model3d のメッシュ。
- 2 つのメッシュは同じ座標（単位と軸）にあるものとし、合わせ直さない。どちらも model3d のメッシュなら、単位と軸が違えば止める（npz はメートルとみなす）。

**計算**:
- 面の重心は、頂点の平均（頂点の型のまま。npz の float32 なら float32 で平均する。試作と同じ）。
- 最近傍は、格子に分けて近くの升目だけを比べる（`model3d.spatial.nearest`。scipy に頼らない）。升目の大きさは、点の入った升目に平均 3 個ほど入るように決める（`cell` で変えられる）。升目の見積もりには、升目より厚い軸だけを使う（平らな面は面積、線は長さから。厚み 0 の軸まで体積に入れると升目が小さくなりすぎ、ほとんどの問いが全部の点と比べることになる）。見つけた点より近い点が探した升目の外にあり得る問いは、升目を広げて探し直し、それでも残れば全部の点と比べる。距離は float64 で、同じ距離なら番号の小さい面を選ぶ。
- `max_distance`（メッシュの単位）: 重心がこれより遠い面は、部品を付けない（番号 -1）。工程は要確認にする。

**出力**:
- `labels`: `names`（部品の名前）と `labels`（移し先の面ごとの部品の番号、int32、-1 は付けない面）の npz。
- `mesh`（任意）: 移し先が model3d のメッシュなら、部品の名前の `parts` を書き足したメッシュ（ほかの `parts` は残す）。移し先が npz なら書かない（大きなメッシュの JSON は重いので、`labels` を使う）。
  - OBJ（`.obj`）に書くとき、移した面がほかの `parts` にも入っていれば止める（OBJ は 1 つの面を 1 つのグループにしか書けず、移した部品が消えるため。`.json` なら重なったまま書ける）。
  - OBJ は、どの `parts` にも入らない面（部品を付けなかった面など）を `default` のグループに書く。部品 `default` があってそういう面もあれば、読み戻すとまざるので止める。
  - どの確かめも、何かを書く前にする。
- `report`（任意）: `model3d-transfer-parts/0.1`。重心の距離（中央値・99 百分位・最大。mm も）、付けなかった面の数、部品ごとの面の数（移し元と移し先）、一対一の数（移し元の面がちょうど 1 回・0 回・2 回以上選ばれた数）、探し方（升目の大きさ・広げた回数・全部と比べた数）、ラベルの sha256。
- 入力のファイルだけで決まるので、レシピでは再利用する。部品のラベルから作る面領域は、推定なので確かめていない扱いにする。

**確かめたこと**:
- CI では、次を確かめた。
  - 最近傍が、全部と比べた答えと同じこと（点の広がりの外の点、小さい升目を含む）。scipy があれば cKDTree と番号も距離も同じこと。同じ距離なら番号の小さい点。
  - 升目の大きさの既定（平らな面と線の点でも、全部の点と比べずに同じ答え）、重心の型、角の数がまちまちな面。
  - npz とメッシュの入出力、遠い面を付けないこと、単位の違い・重なった parts・parts の無いメッシュを止めること、再利用、悪い設定。
- 2026-11-08 に、リポジトリ外の試作の分割版（11 部品）と元の版（1,984,871 面）で確かめた。
  - 試作のラベル（`seg_labels.npy`）と、全部の面で同じだった。重心の距離は中央値 0.0707 mm・最大 0.079 mm で、試作の記録と同じ。
  - 最近傍の番号と距離は、試作が使った cKDTree と、全部の面でビット単位まで同じだった。
  - 約 19 秒（cKDTree は 1 秒ほど）。一対一にならない面が 54 面あり、2 面が隣の部品へ移る（試作と同じ。2 つの版は、それぞれの読み込みで別々に位置を合わせたので、0.07 mm ほどずれているため）。

## .blend の画像を差し替える

`texture.apply_to_blend` は、.blend の材質の画像ノードのうち、名前の合う画像だけを新しいテクスチャ（顔を写し直した色のアトラスなど）につなぎ替えて詰め込み、別の .blend に書く（Blender の中で）。形・骨・材質のつなぎ方は変えない。リポジトリ外の試作で、作り直したキャラに写し直したテクスチャを当てた台本を移した（dance-recipe の `apply_texture` の段が、まだ試作の台本を呼んでいる。この工程への置き換えは別の PR）。

**選び方**:
- `match`（必須。既定は無い。作品ごとの値なのでレシピに書く）: 差し替える画像の名前。画像の名前かファイルのパスの、ファイル名だけを比べる（大文字と小文字、Blender の重複の印 `.001` は見ない。Blender の相対パスの頭の `//` も外す）。
- 材質のノードの木を、ノードグループの中までたどる（同じ木は 1 度だけ）。
- `min_replacements`（既定 1）より少なければ止め、今ある画像を並べる。

**確かめること**（どれかが合わなければ止める）:
- 合う画像の色空間が 1 つにそろう（新しい画像はその色空間で読む）。新しいテクスチャが今の画像と同じ大きさ。
- 詰め込んだバイトの sha256 が、渡したテクスチャと同じ。合うノードが全部つなぎ替わり、合わない画像ノードは前と同じ画像のまま。
- 圧縮して別の .blend に書いた後も、入力の .blend とテクスチャが変わっていない。

**出力**:
- `blend`: 差し替えた .blend（圧縮して書く。.blend の sha256 は書くたびに変わるので、中身の比べには形や画像の比べを使う）。
- `report`: `model3d-apply-texture/0.1`。入力と出力の sha256、差し替えたノードの数と一つずつ（材質・ノードの木・ノード・元の画像と
  パス・新しい画像）、変わっていないことを確かめた合わない画像ノードの数、詰め込んだテクスチャの sha256 と大きさ、色空間。
- `image_name`: 新しい画像に付ける名前（既定はテクスチャのファイル名）。

**確かめたこと**:
- CI では、名前の比べ方（ファイル名だけ、大文字と小文字・重複の印・相対パスの `//`）と、工程の設定・Blender への渡し方・報告の確かめ（代役の Blender。決まりを満たさない報告は失敗）・悪い設定を確かめた。画像ノードを差し替える所は、本物の Blender が要るので CI では確かめない。
- 2026-11-08 に、本物の Blender 4.5.15 で、試作の作り直しの 2 つの記録と同じ入力で回した。
  - 差し替えたノード 22・変わらないノード 22・アトラス 4096×4096・詰め込んだ sha256 は、2 つの記録とも同じだった（作り直しの写し直しと、顔の写し直しの流れ）。
  - 差し替え 22 件の一つずつ（材質・ノードの木・ノード・元の画像とパス・新しい画像）も、記録と同じだった。

## .blend どうしを比べる

`check.blend_match` は、2 つの .blend の中身を比べる。作り直したキャラが採用版と同じか、工程を移した後も同じ形と骨の動きになるかを、数で確かめるため。リポジトリ外の試作で、作り直したキャラと採用版を比べた台本を移した。Blender で中身を書き出し（`blender_adapter/snapshot_blend.py`）、比べは Blender の外の numpy でする（`model3d.blend_snapshot`）。

**比べるもの**（試作と同じ読み方）:
- 物体の有無（左にだけある・右にだけある）、シーンの最初と最後のコマと fps と `fps_base`（再生の速さは fps / fps_base）。
- メッシュごと: 頂点（物体の中の座標、float32）、面の角の数と面のつなぎ（面の角ごとの頂点）、作用中の uv（面の角ごと）、属性（値と、置き場所 `domain`・型 `type`）、置き場所の行列（シーンの最初のコマに合わせてから読む。保存したときのコマによらない）。
  - `attributes`: 比べる属性の名前の並びか `"all"`（既定。`.` で始まる内部の属性と `position` は除く）。名前を挙げた属性が、選んだメッシュのどれにも無ければ（打ち間違いか、書き出せない型）、形のつくりの違いにする（`requested_attributes_missing`）。
  - `objects`: 比べるメッシュの名前の並び（既定は全部）。名前を挙げたメッシュが片方にでも無ければ、黙って外さず、形のつくりの違いにする（`requested_objects_missing`）。
- 骨: 名前（並べ替えて）、静止の行列、姿勢の行列（`pose_frames` のコマ。`"all"` はシーンの全部のコマ、省くと `frames`）。アーマチュアが 2 つ以上なら `armature` で選ぶ。
- `evaluate: true` なら、`frames` のコマで変形した世界の形（float32 の行列で世界へ移した座標）の sha256 と、等間隔の `samples` 個（既定 512）の頂点。`evaluate` でないときは、コマを動かす前にモディファイアのビューポートを切る（速くするため）。

**合否**:
- `exact`: 全部が同じ（物体・シーン・頂点・面のつなぎ・uv・属性・置き場所・骨の名前と静止と姿勢・変形した形の sha256）。違いの大きさは `max_coordinate_error`・`matrix_world_max_error`・`rest_matrix_max_abs_error`・`pose_matrix_max_abs_error`・`sampled_max_coordinate_error`。
- `tolerance`（`{"coordinate", "matrix", "value"}`）を渡すと、形のつくり（物体・シーン・頂点と面の数とつなぎ・属性の置き場所と型・骨の名前・姿勢のコマ）が同じで、座標の違いが `coordinate`、行列の違いが `matrix`、uv と属性の違いが `value` の中なら合格。渡していない項目は同じであること（`coordinate` が無ければ頂点と変形した形（`exact` と同じく頂点の全部の sha256）、`matrix` が無ければ置き場所と骨の静止と姿勢、`value` が無ければ uv と属性。渡し忘れた項目を上限なしで通さない）。
- `tolerance` を渡さなければ、全部が同じ（`exact`）かで合否を出す（float の端数でも違えば不合格。試作の作り直しの比べでも、端数の 3e-7 で同じにならなかった）。
- 非有限の値（NaN・無限大）があれば、違いの大きさを測らずに不合格にする（`non_finite` に場所を並べ、違いの大きさは null。`tolerance` でも許さない）。
- `report_only` で測るだけ、`strict` で不合格を失敗にする。数を出す検査なので、採否は人が決める。

**出力**: `report`（物体ごとの行と変形した形の行つき）。工程の観測は、行を除いたまとめ。書き出し（`left_snapshot.npz`・`.json` と右）は工程のフォルダに残る。Blender は外部の実行ファイルなので再利用しない。

**確かめたこと**:
- CI では、numpy の比べ（同じ・違いの大きさ・tolerance の合否・形のつくりの違い・uv と属性・骨・変形した形）と、工程の設定と Blender への渡し方（代役の Blender が書き出しを作る）・悪い設定を確かめた。中身を書き出す所は本物の Blender が要るので、CI では確かめない。
- 2026-11-08 に、本物の Blender 4.5.15 で、試作の比べの記録（採用版のキャラと作り直したキャラ、変形を測るコマ 6 つ、姿勢は全部の 147 コマ）を再現した。
  - 27 物体の全部の項目、132 本の骨の静止と 147 コマの姿勢の違い（0）、変形した形 162 件（sha256 の一致と違いの大きさ）が、記録と同じだった。`exact` も記録と同じく真。
  - 属性を全部（`"all"`。uv の属性・頂点色・鋭い辺と面も）比べても同じだった。約 15 秒。

## 手前の部品の地図と投影の遮り

`texture.view_parts` は、部品ごとの npz の部品を視点（`texture.project_reference` と同じ `view`）の正射影で描き、画素ごとにいちばん手前の部品の地図を作る。`texture.project_reference` は、この地図を `occlusion` に受け取ると、ほかの部品（前髪・帽子・手など）に隠れた所へ参照画像を写さない。リポジトリ外の試作で、顔の窓でどの部品が手前かを色分けした台本を移し、その地図を投影の遮りに使えるようにした（遮りは試作に無い新しい所）。

**部品の地図**（`texture.view_parts`）:
- 部品ごとの npz（`names` と `co{i}`・`f{i}`。四角形以上は三角形に分ける）の部品を、`parts`（名前の並び。既定は全部）だけ描く。
- 視点の窓（とその周り `margin` 画素、既定 2）にかかる三角形だけを描く。三角形の範囲で選ぶので、角が全部窓の外でも窓をまたぐ三角形は描く（試作は角のどれかが窓の中の三角形だけだった）。
- z バッファは `texture.zbuffer`（同じ入力なら同じ勝者）。
- `occlusion`: `owner`（画素ごとの部品の番号 int32、無い画素は -1）・`depth`（奥行き、無い画素は inf）・`names`・`view`（JSON）の npz。
- `preview`（任意）: 部品の番号ごとの色（11 色を繰り返す。無い画素は `background`、既定 245）。
- `report`（任意）: `model3d-view-parts/0.1`。部品ごとの三角形・窓にかかる三角形・手前の画素の数。

**投影の遮り**（`texture.project_reference` の入力 `occlusion`）:
- 写す点（テクスチャの画素の断片）ごとに、視点の画素の手前の部品を地図で見る。自分ではない部品があり、その奥行きが点より `depth_tolerance` を超えて手前なら、その点は隠れる（重み 0）。
- 自分の部品は `occlusion_part`（既定はメッシュの名前。`mesh.import_parts` で読んだメッシュは部品の名前）。地図に無い名前を `occlusion_part` に書くと止める。メッシュの名前が地図に無ければ、地図の全部の部品で隠す。
- 地図の視点が写す視点と違えば止める。`occlusion` を渡さなければ、今までとファイルまで同じ。
- 自分の部品の中の見え隠れは、今までどおり z バッファで決める（地図ではなく、手前の三角形の平面の奥行きで比べる）。ほかの部品は、地図の画素の中心の奥行きで比べる。
- 報告に、隠した断片の数（`occluded_fragments`）と、隠した部品ごとの数（`occluded_by`）、地図の部品と自分（`occlusion`）を載せる。
- 同じ部品に溶け込んだもの（部品に分かれていない前髪など）は分けられない。顔の面領域を `select` で選んで塗る。

**確かめたこと**:
- CI では、部品の地図（手前の部品と奥行き、窓をまたぐ三角形、色、工程の出力と部品の絞り、悪い設定）と、投影の遮り（手前の部品の後ろは塗らない・地図を渡さなければ同じ・自分の部品は隠さない・視点と部品の名前の確かめ）を確かめた。
- 2026-11-08 に、リポジトリ外の試作で確かめた。
  - 試作の顔の窓（1000×1000）の部品の地図の色の画像は、試作の `face_parts.png` とファイルまで同じだった（作り直しの部品と採用版の部品のどちらからでも）。手前の部品は髪と頭の 2 つ。
  - 今の顔の投影（遮りなし）を回し直すと、記録した色と重みの画像とファイルまで同じだった。
  - 同じ投影に地図を渡すと、髪に隠れた 1,503 の断片を外した。この顔のマスクの中には髪が重ならないので、塗った画素の数は同じ（58,291）。

## 投影の色合わせ

`texture.project_reference` の `color_match` は、参照画像（設定画）の色をモデルの色に合わせてから写す。設定画の肌の色とモデルの元の肌の色が少し違うと、写した所の境目が見えるため。リポジトリ外の試作で、頬の枠の色の差で設定画の色を直した手順を移した。

**設定**: `color_match`: `{"model": [[x0, y0, x1, y1], …], "reference": [[x0, y0, x1, y1], …]}`（枠の右と下は含まない）。
- `model` の枠は、モデルの描画（同じ `view` で `texture.render_view` に元のテクスチャを貼った絵）の画素。`reference` の枠は、参照画像の画素。どちらも肌だけが入る所（頬など）を選ぶ。枠は作品ごとの値なので、レシピに書く（目印からの決め方は試作の設定に残す）。
- 枠が画像の外にはみ出すか空なら止める（`model` の枠は、設定を確かめる所で視点の大きさと比べるので、描く前に止まる）。

**計算**:
1. 両方の枠の画素を CIELAB（D65、sRGB の変換。numpy で計算する。model3d は OpenCV に頼らない）に直し、成分ごとの中央値を取る。
2. 中央値の差（モデル − 参照画像）を、参照画像の全部の画素の CIELAB に足して sRGB に戻す（0〜255 に切る。アルファは変えない）。
3. `keep`（任意の入力。参照画像と同じ大きさの 0〜255 の重み）を渡すと、`keep` の外はモデルの中央値の 1 色で塗り、`keep` の重みで混ぜる（目のまわりだけを設定画から写し、ほかは元の肌の色にする）。`keep` は `color_match` と一緒にだけ使える。
4. 合わせた参照画像を、いつもどおりテクスチャへ写す（重ね合わせの確認の画像にも、合わせた参照画像を使う）。

**報告**（`color_match`）: モデルと参照画像の中央値（CIELAB と、OpenCV の 8 ビットの Lab の目盛り）、差、枠の画素の数、0〜255 に切った画素の割合、`keep` の外の色、`keep` を使ったか。

**確かめたこと**:
- CI では、CIELAB の標準の値と往復（OpenCV があれば 8 ビットの Lab との差が 2.5 未満）、枠の中央値で色が合うこと・アルファ・`keep` の外の 1 色、枠の確かめ、工程の設定（合わせると赤がモデルの灰色に合う・合わせなければ元のまま・`keep` だけでは止める・悪い設定）を確かめた。
- 2026-11-08 に、リポジトリ外の試作で確かめた。
  - 試作の目印と枠で、今の顔の投影に `color_match` を付けて回した。報告の中央値を 8 ビットの目盛りに丸めると、試作の記録（モデル [235, 136, 132]・参照画像 [232, 138, 133]）とまったく同じだった。
  - 同じ `keep` で直した参照画像の色は、試作の画像と最大 5、平均 1.8 の差（試作は OpenCV で Lab を 8 ビットに丸めてから戻すため）。
  - 瞳の自動検出（試作の目印の決め方）は移していない（目印は推定しない）。

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

**顔の絵（表情の差分など）を写すときの追加の設定**（リポジトリ外の 2 作目の VRM のダンスで、頭の絵を写した台本の設定を移した。
省けば今までとファイルまで同じ）:
- `visibility: false`: 見え隠れを見ない（z バッファと `occlusion` を使わず、視点の中の点は全部写す）。目の所が穴で奥に眼球の面がある頭や、
  両面のまつ毛の板のように、顔の面の奥の面にも同じ絵を写しておくと、斜めから見ても元の絵（開いた目のまつ毛など）が残らない。
  `occlusion_part` とは一緒に使えない。
- `two_sided_depth`（m）: 角が全部この奥行き（視点の向きに沿った値。正面 `-Y` なら y）より手前の三角形は、裏向きの角の法線を表へ返して写す
  （両面の板の裏向きの面に元の絵が残り、閉じた目のまわりの点々になった）。頭の後ろ側は同じ見た目の位置にあるので、奥行きで前側だけに限る。
- `core`（`{"center": [x, y], "radius": [rx, ry], "inner": 0〜1}`。視点の画素）: この楕円の中は、面の向きで弱めない（鼻のわき・鼻の下・唇の下など
  下や横を向いた面にも写す。弱めると、そこだけ元の色が残って帯やしみに見えた。真裏（向きの余弦 −0.2 以下）は除く）。`inner`（既定 0.75）より
  内は 1、縁へなめらかに 0。表情の差分を写すときは目尻まで入れる（目尻に開いた目のまつ毛が残った）。
- `regions`（`core` と同じ形の並び）: 写す範囲を、縁をなめらかにぼかした楕円の集まり（視点の画素。`inner` の既定 0.7。重なりは大きいほう）。
  参照画像の画素で描く `mask` と違い、視点の画素で書ける。
- 報告に、裏から写した断片の数（`two_sided_fragments`）と、芯で向きの重みを上げた断片の数（`core_raised_fragments`）。

同じ絵で、台本と比べた（2026-11-17、Windows。実測）。台本の写し方の設定（表情の差分を写す設定。伸び縮みなしの倍率、頭の正面全体の楕円、
芯、向き 0.12〜0.45、縁の外 5 画素、`two_sided_depth` −0.04）を、同じ表情の絵（微笑みと閉じ目、1254 画素）と同じ頭（3,464 頂点、
4096² の色の絵）で、台本と `texture.project_reference` の両方にかけ、頭の色の絵を比べた（台本をかけ直した絵は、採用した絵と画素まで同じ）。

| 設定 | 微笑み（PSNR・差の平均・差 > 8 の割合） | 閉じ目 | 台本だけが塗った画素 |
|---|---|---|---|
| 今までの設定だけ（参照画像の画素の楕円のマスク、見え隠れあり） | 16.4 dB・25.2・45 % | — | 259 万 |
| 見え隠れなし（`depth_tolerance` 1 m） | 19.5 dB・16.7・37 % | 18.3 dB・18.7・37 % | 170 万 |
| 追加の設定（`visibility: false`・`two_sided_depth`・`core`・`regions`） | 31.0 dB・1.1・2.3 % | 27.2 dB・1.7・2.3 % | 2.5 万 |

- 差の平均は 255 段階、塗った画素（どちらかが塗った約 550 万画素）の中で数えた。
- 追加の設定の後に残る差は、uv の島の縁と三角形の辺に沿った細い線だけ（絵で確かめた）。台本は、島の縁の外へ塗り足す幅を重心座標の許しで
  決め（角の近くで広く届く）、同じ画素を複数の三角形が塗ると最後の三角形の色になる。`texture.project_reference` は、三角形からの距離で
  塗り足し、同じ画素の断片の色を重みで平均する。この 2 つは設定ではなく計算の作りの違いなので、移していない。
- 位置合わせは、台本の「目・鼻・口の範囲は同じ倍率、間の肌だけ伸び縮み」（`zones`）のうち、伸び縮みなし（相似変換と同じ）の場合だけを、
  2 点の目印で同じ変換にした。間の肌だけ伸び縮みさせる写し方は移していない（表情の差分では使わなかった）。色合わせは台本も 0（使わない）。

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
uv run model3d mesh parts parts.npz --normals normals.npz          # 部品ごとの npz の一覧と法線の確かめ（見つけたことがあれば終了コード 1）
uv run model3d frames compare 左のフォルダ 右のフォルダ --view view.png  # 2 つのコマの並びの画素の違い（--max-differing-pixels 0 などで合否）
```

実証したのは正面 1 枚で、背面・側面は入力から拘束していない。記録の生成物は顔・指・衣装の模様が粗く、連結成分が複数で閉じていない
（記録の値）。リグ・ウェイト・表情・衣装物理は無い。

## 検査と判断基準（設計 §10 のうち実装したもの）

| 検査 | 実装 |
|---|---|
| 構造 | `check.structure`: 必須骨・循環・非有限値・ウェイトの正規化。mesh 入力ありの場合は頂点数・topology との整合も必須。rig だけの検査ではメッシュとの対応を保証しない |
| 衝突 | `check.penetration` / `collision.push_out`: 貫通の数と深さ。許容値超過は `needs_review` か `failed` で、未解決のまま成功にしない |
| 連続性 | `check.continuity`: 急跳び。点の列は外部（Blender や姿勢の推定）が書く |
| ちらつき | `check.jitter`: 布などの頂点が前後のコマの中間からずれる量（骨から見た座標）。Blender で読む |
| 体の太さ | `check.body_size`: 腕・脚の直径と胴の幅・厚み（初期姿勢）。測るだけで合否は出さない。Blender で読む |
| ねじれ | `check.twist`: 骨の軸のまわりのねじれの角（コマごと）。`max_twist_deg` を渡したときだけ合否。Blender で読む |
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
  アドオン（VRM Add-on など）が要るときは `--addon` と `--extensions`（下の「Blender 5.2 と VRM Add-on」）。
  
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

## Blender 5.2 と VRM Add-on

2026-11-16 に、ユウが 3D の道筋の基準を **VRM と Blender 5.2** に決めた（これまでの制作で、VRM と Blender 5.2 で作った 2 作目の
VRM のダンスの成果物がいちばんよかったため）。キャラは VRM（人型の骨の割り当て・MToon の材質・揺れもの）で持ち、Blender 5.2 と
VRM Add-on で動かす。今ある Blender の工程は 4.5.15 で確かめたもので、5.2 で確かめたものは下に書いたものだけ。

**アドオンを有効にして起動する**:
- CLI は `model3d blender observe|script` の `--addon MODULE`（くり返し可）と `--extensions DIR`。
- 工程は `blender.run_script` の `addons`（モジュール名の並び）と `blender_extensions`（recipe からの相対パスも可。
  `model3d run --tool blender_extensions=DIR` でも渡せる）。
- 利用者の設定は読まない（`--factory-startup` のまま）。.blend を読む前に、前置き（`--python-expr`）で挙げたアドオンだけを有効にする。
  有効にならなければ、.blend を読む前に終了コード 1 で止まり、工程は失敗する。
- 拡張機能（Blender 4.2 以降）のモジュール名は `bl_ext.<リポジトリ>.<id>`。手元のフォルダに入れた VRM Add-on は `bl_ext.user_default.vrm`
  （`<フォルダ>/user_default/vrm/`）。フォルダは環境変数 `BLENDER_USER_EXTENSIONS` で Blender に渡す。
- 記録（工程の観測と CLI の出力）に、アドオンのモジュール名・版・ライセンス（拡張機能の `blender_manifest.toml` から読む）と、
  拡張機能のフォルダが残る。

**確かめたこと**（Windows の Blender 5.2.4 LTS と VRM Add-on 4.10.5、2026-11-16。実測）:
- 前置きで VRM Add-on が有効になり、VRM のキャラの .blend（骨 131 本）で人型の骨の割り当て（`vrm_addon_extension.vrm1.humanoid`）が
  読めた。同じ .blend の観測（`model3d blender observe`。骨とウェイトのあるキャラを本物の Blender で観測したのは初めて）は 242 件の事実で、
  Observation3D の契約に通った。
- 無いアドオンを挙げると、.blend を読む前に終了コード 1 で止まった。
- 利用者の設定のファイル（userpref.blend）は書かれなかった。拡張機能のフォルダには、Blender が `.cache/compat.dat` を書く。
  共有の設定のフォルダを指さず、写しを指す。
- Blender の起動引数 `--addons` でも有効にはなるが、利用者の設定の一覧に載らないので、VRM Add-on の読み込みの後の処理が
  「No add-on preferences for bl_ext.user_default.vrm」のエラーを出した。前置きで有効にするのはそのため。
- `motion.retarget` を、2 作目の VRM のダンスと同じ入力（720 コマ・骨 131 本。骨の名前を Mixamo 式に付け替えた作業コピー）と
  同じ設定で、Blender 5.2.4 で回した。Blender 4.5.15 で作った採用版と、pose の npz の骨の行列（全コマ・全部の骨）と基本の姿勢
  （rest・tail）はバイト単位で同じ、直したコマ（27）と検査の要約も同じだった。world と frames は値が同じで型だけが違う
  （5.2.4 では float32 と int64。4.5.15 では float64 と int32。Blender に入っている numpy の版の違い）。アドオンは要らない。
  かかった時間は約 9 分（同時に別の検査を回していたので、4.5.15 と速さは比べていない）。

**版の違い**（Blender 5.2.4 で確かめた）:
- EEVEE の名前は `BLENDER_EEVEE`（4.2〜4.5 は `BLENDER_EEVEE_NEXT`。5.2.4 で `BLENDER_EEVEE_NEXT` を入れると TypeError）。
- 合成は `scene.compositing_node_group`（`scene.node_tree` は無い）。
- アクションの F カーブは層（`layers` → `strips` → `channelbags`）からだけ読める（`Action.fcurves` は無い）。
- `render.stage` は、5.x では舞台を組む前に止まる（EEVEE の名前と合成の API が変わったため。描き方は 4.5.15 でだけ確かめた）。
  5.2 で舞台ごと描くときは `render.show`（[演出版を 1 回で描く](#演出版を-1-回で描くrendershow)）、要素別に描くときは簡易描画の `render.passes`（[要素別に描く](#要素別に描く)）を使う（どちらも 5.2.4 で確かめた）。

**ライセンス**: VRM Add-on 4.10.5 のライセンス表記は MIT と GPL-3.0-or-later の両方（`blender_manifest.toml`）。model3d は外の道具として
Blender の中で呼ぶだけで、アドオンのコードは写さない（このリポジトリは Apache-2.0）。アドオン本体もリポジトリに入れない。

## キャラを作る段の確認地点

キャラを作る道具（立ち絵から頭と体の絵・三面図 → 3D の候補 → 組み立てと頭身の調整 → 骨と VRM）には、利用者が選ぶ・決める地点がいくつかある。
3 作目の VRM のダンスでは、その前に数と絵を出してから選んでもらった（2 作目では、体が細い・面長だと、完成したあとで気づいた）。
ここの工程は、その確認地点と、受け取った VRM の作りの癖を直す所で使うもの（`ops/checkpoint_ops.py`）。測る工程は数と絵を出すだけで、
どれを選ぶか・良し悪しは決めない。

### 立ち絵と体の絵の比率を測る（`check.illustration`）

2 作目の VRM のダンスでは、3D のモデルの体が細いことに、完成したあとで気づいた。細さは候補選びでは変わらず、体の絵（T ポーズ）の段で
決まっていた（体の絵と 3D の候補 4 つが、同じ物差しでほぼ同じ細さだった）。3 作目では、確認地点（候補選び・頭身の調整）の前に、立ち絵と
体の絵の比率を数にした。体の細さは絵の段で決まるので、絵を測る。

- 入力 `image`（8 ビットの絵。16 ビット（色の絵も）・浮動小数の絵は止める）。人物の画素は `mask`: `alpha`（透けが `alpha_min`（既定 128）を超える画素）・
  `color`（背景の色の決まり `background` に入らない画素。既定は緑の背景）・`auto`（既定。透けの値に `alpha_min` より小さい画素があれば alpha、
  無ければ color）。
- 色の決まり（`background` など）は `{"g": [151, 255], "g-r": [61, 255], …}` の形で、R・G・B と差（`r-g`・`g-b` など）の範囲 [下, 上]（両端を含む
  整数）。全部に入る画素が当たる。既定の緑の背景は「緑 > 150・緑 − 赤 > 60・緑 − 青 > 60」。
- 立ち絵（`rows`）: 身長は `top_y`（頭のてっぺんの行。省くと人物のいちばん上の行）から、人物のいちばん下の行まで。`rows` の行ごと
  （`{"name", "at": 身長に対する割合（0 = 頭のてっぺん）, "kind"}`）に、`min_run`（既定 6 画素）より狭い区間（髪の毛先など）を除いた区間の幅を
  読む: `torso` は絵の横の真ん中をまたぐ区間（腕が離れていなければ腕も含む幅。区間が 3 つ以上なら `arms_separate`）、`arms` は真ん中を
  またがない区間の平均、`legs` は広い 2 つの区間の平均（1 つなら半分にして `note`）。幅は画素と、身長に対する割合。`chin_y`（あごの行）が
  あれば頭身も。行の高さに既定は無い（絵ごとに、確認用の絵を見て決める）。
- T ポーズの体の絵（`tpose`: `true` か `{"waist_range": [下, 上], "step": きざみ}`）: 骨の無い 3D の候補と同じ物差しで測る。S = 床（人物の
  いちばん下の行）から腕（横にいちばん広い行）までの高さ。くびれは、床から `waist_range`（既定 [0.80, 0.92]）の高さ（S に対する割合、`step`
  （既定 0.005）おき）で、体の中心（床から 0.85 S の行の人物の画素の、横の中央値）をまたぐ区間がいちばん狭い行。`rows` と `tpose` はどちらか 1 つ。
- 出力: `report`（`model3d-illustration/0.1`）、任意で `view`（.png か .jpg。測った行に線を引いた確認用の絵）。正面の絵 1 枚なので、厚み（前後）は
  分からない。絵の遠近や服の厚みも含んだ値になる。数を出すだけで合否は出さない（`report_only` を付けると要確認にしない）。numpy と Pillow だけで、
  同じ入力なら再利用する。

3 作目で分かったこと: 頭のてっぺんを角の先と取ると、測る行が全部ずれた（頭のてっぺんとあごの行は、絵を見て渡した）。確認用の絵で、測る行が
部位に乗っているかを先に見る。

**確かめたこと**:
- CI では、合成の立ち絵と T ポーズの体の絵（緑の背景・透け）で、行の幅（胴・腕・脚、細い区間を除く、脚が 1 つの区間なら半分）・頭身・くびれ・
  確認用の絵・色の決まりと人物の画素の決め方・悪い設定を止めることを確かめた。
- 2026-11-17 に、3 作目の VRM のダンスのリポジトリ外の台本（立ち絵を測る台本。OpenCV）と比べた（実測）。立ち絵（緑の背景）に制作と同じ 10 行・
  頭のてっぺん・あごを渡すと、全部の行の高さ・区間の幅・身長に対する割合と頭身が台本と同じで、制作のときの記録とも同じ。T ポーズの体の絵
  （透けの絵）でも、腕と床の高さ・S・くびれの高さと幅が台本と同じ。確認用の絵は、線は同じ行に引かれるが、字の大きさと描き方が台本と違う。

### 体の絵と顔の絵を元絵と比べる（`check.body_image`・`check.face_image`）

`check.body_image` は、体の絵（キャラを作る道具の T ポーズの正面の絵など）を元絵（立ち絵）と、同じ物差しで比べる。腕の形（A ポーズと
T ポーズ）に左右されないよう、物差しは足の裏からウエストまでの高さ U。
- 入力 `reference`（元絵）・`body`（体の絵）。人物の画素の決め方は `check.illustration` と同じ（`mask`・`background`・`alpha_min`。auto なら絵ごとに決める）。
- どちらの絵も、脚（足の裏の少し上から上へ、体の中心をまたがない左右の区間の組が続く行。中心はなめらかに追う）・裾（脚の見えるいちばん上の行）・
  ウエスト（裾から上へ、脚の見える高さの 0.05〜0.55 倍の行で、中心をまたぐ区間がいちばん狭い行）を見つけ、U に対する、ウエスト・太もも（0.65 U）・
  ひざ・ふくらはぎ・足首の幅と、裾・ひざ・ふくらはぎ・足首の高さを測る（脚の幅は 9 行でならす。足首は 0.10〜0.30 U でいちばん細い行、
  ふくらはぎはその上 0.50 U まででいちばん太い行、ひざはふくらはぎの上 0.62 U までで、いちばん細い幅の 3% 以内の行の真ん中）。表 `table` に、体の絵 ÷ 元絵。
- 出力: `report`（`model3d-body-image/0.1`）、任意で `overlay`（足の裏とウエストを合わせて重ねた絵。左は元絵に両方の輪郭（元絵は水色・体の絵は赤）、
  右は重ねた体の絵に元絵の輪郭）。正面の絵どうしなので、厚みは比べない。
- 3 作目で、うまくいかなかった物差し: 「足の裏からひざ（脚がいちばん細い 1 行）まで」は、ひざの細い所が上下に長く続いて 1 割ぶれた。脚の行を
  「区間がちょうど 2 つ」で拾うと、太ももの横の手の先で止まり、そこを裾と取り違えた。どちらも、重ねた絵の輪郭の大きさが合わないことで気づいた。

`check.face_image` は、頭の絵（正面）の顔を元絵の顔と、両目の位置で合わせて並べる。顔の絵は立ち絵から 2 段階でずれる（頭の絵にするとき、
3D にするとき）ので、1 段目を頭の絵ができた所で見る。
- 入力 `reference`（元絵か、2D の動画のコマ）・`head`（頭の絵）。両目は、瞳の色の決まり `iris`（`check.illustration` の色の決まりと同じ書き方）に
  入る画素を開いて（`kernel`、既定 3）かたまりに分け、大きい順の 14 個から、ほぼ同じ高さに並ぶ（横に 20 画素以上離れ、縦のずれが横の 0.25 倍以下）、
  同じくらいの大きさ（2.5 倍以内）の組のうち、小さいほうがいちばん大きい組。瞳の色は、髪飾りや肌の照りにもあることがある（3 作目では、大きい順の
  2 つを取ると、目と太ももを組にした）。見つからない絵は `reference_eyes`・`head_eyes`（[左の x, 左の y, 右の x, 右の y]。絵の左の目が先で、左右が逆なら止める）で手で渡す。瞳の色に既定は無い。
- 目の間隔 d を `size`（既定 150）画素・傾きを 0 にそろえた顔のまわり（幅 4.4 d、高さ 5.4 d）に、目の高さから下へ 0.5 d ごとの目盛りを引き、
  [元絵 | 頭の絵 | 半分ずつ重ねた絵] を `sheet` に、目からあごまでを 2 倍にして 0.1 d ごとの目盛りを引いた絵を `zoom` に描く。目から鼻・口・あごまでの
  長さ（d に対する割合）は、この絵で読む。
- 出力: `report`（`model3d-face-image/0.1`。絵ごとの両目の位置・見つけ方（iris か手）・間隔・傾き）、任意で `sheet`・`zoom`。
- 顔の比率は、色の絵（瞳）が付いてから測る。3 作目では、色の無い 3D の候補の形から目の位置を目で読んで「あごが短い」と見立てたが、色の絵の付いた
  モデルで測り直すと、逆に少し長かった。顔の小さい絵では、1 画素の読みの差が d の数 % になる。

**確かめたこと**:
- CI では、合成の全身の絵で脚の物差しと表と重ねた絵、合成の顔で瞳の組（高さの違う飾りを組にしない）・顔の切り出し・手で渡した目、悪い設定を
  止めることを確かめた。開き（偶数の四角で 1 画素ずれるのも）とかたまりの番号の順（2 × 2 の区画の順）は、OpenCV がある環境では、乱数の絵で
  OpenCV と同じかも確かめる。
- 2026-11-17 に、3 作目の VRM のダンスのリポジトリ外の台本（体の絵と元絵を比べる台本・顔を並べる台本。OpenCV）と比べた（実測）。元絵と体の絵で、
  両方の絵の物差しの値・倍率・表が台本と同じで、制作のときの記録とも同じ。顔は 3 組（元絵と直す前の頭の絵、2D の完成版のコマ（目は手で渡した）と
  頭の絵、元絵と長さを直した頭の絵）で、両目の位置・間隔・傾きが台本と同じ。絵は、線と字と拡大の補間の描き方が台本と違う。

### 三面図の高さを比べる・頭の絵の目から下を詰める（`check.three_view`・`texture.face_length`）

三面図は頭身の調整の手本になる絵なので、正面と横で肩・帯・裾の高さがずれていると、合わせる相手が 2 つになる。3 作目では、横の絵だけ
帯と裾が低く、原因は横の頭が正面より大きいことだった（全高が同じなので、そのぶん下がった）。三面図は、向きごとに頭の大きさが違うことが
ある。

`check.three_view` は、三面図の同じ部位の高さがそろっているかを測る。
- 入力 `front`・`side`・`back`。人物の画素の決め方は `check.illustration` と同じ。絵ごとに、人物のいちばん上と下の行・全高・体の中央（下 1/4 の
  人物の画素の横の中央値）・横にいちばん広い行（正面と後ろでは腕の高さ）と、印の行を、全高に対する割合でも出す。
- 印（`marks`: `[{"name", "rule", "range": [下, 上], "pick": "top" か "bottom", "least", "strip"}, …]`）: 色の決まり `rule` に入る人物の画素が、
  体の中央の帯（±`strip` × 全高、既定 0.07）に `least`（既定 4）個以上ある行のうち、全高に対する割合 `range` の中で、`top` ならいちばん上、
  `bottom` ならいちばん下の行（帯の紐の上の縁・裾の縁など。色と範囲はキャラごとに決める）。見つからなければ null。
- `iris`（瞳の色の決まり）を渡すと、頭のあたり（いちばん上から全高の 0.2 まで）のそのかたまり（2 × 2 で開く。`kernel`）の大きい順の 3 つ
  （面積と重心）も出す。目の行を読む手がかり。
- 出力: `report`（`model3d-three-view/0.1`）、任意で `sheet`（全高を正面にそろえ足の裏を合わせて並べ、正面の行に線を引いた絵）、`heads`
  （頭のあたりを 3 倍にして、10 画素ごとの線と 50 画素ごとの行番号を引いて並べた絵。絵ごとに高さが違えば、いちばん高い絵にそろえて下を白で埋める）。頭の大きさ（髪のてっぺんからあごまで）は、色では
  自動で拾いにくい（角の照りが白く、首も肌色）ので、`heads` の絵で行を読む。
- 3 作目で分かったこと: 赤い行を探す範囲を広く取ると、正面の裾を帯の側に数えた（並べた絵で気づいて範囲を直した）。

`texture.face_length` は、頭の絵の「目から下」の長さだけを、描き直さずに詰める（行の付け替えだけ）。3 作目では、キャラを作る道具の頭の絵が、
元絵と 2D の完成版より目から下が長かった。利用者がその絵柄を気に入っていたので、目・髪・絵柄はそのまま、長さだけを合わせた。
- 入力 `image`（透けつきの PNG）。`eye_y`（目の中心の行）・`chin_y`（あごの先の行）・`d`（目の間隔。画素）・`k`（帯を縦に何倍にするか。
  1 より小さいと詰める。0〜2）は必須。`start`（既定 0.10）: 帯の上の端を、目の中心から何 d 下に置くか（下まぶたより下）。
- 帯（目の中心 + `start` × d から、あごの先まで）を縦に `k` 倍にする。帯の両端では変えず、真ん中ほど強く（元の行 = y0 + L × (t − (1 − k)
  sin(2πt) / 2π)、t = (出力の行 − y0) / (k L)。L は帯の長さ）。帯より上は見える画素を変えず、帯より下（あごの下の髪）は長さの差だけ上へずらす。
  透けが 0 の画素に残っていた色は、既定（`above_band: resample`。台本と同じく、帯より上の行も同じ付け替えを通す）では 0 になる（制作と同じ）。
  元のまま残すなら `above_band: copy`（帯より上の行を元の絵からそのまま写す。帯から下は同じ）。
  絵の幅いっぱいに同じ付け替えをする（顔だけを詰めると、顔の縁で髪の筋が折れる）。
- 行は Lanczos4 で引く（OpenCV の remap の INTER_LANCZOS4 と同じ計算: 位置を 1/32 に丸め、絵の外は 0）。透けの縁がにじまないよう、色に透けを
  掛けてから付け替え、あとで戻す。
- 向きの違う頭の絵（横・後ろ）にも、正面と同じ `k` と `d` を使うと、絵どうしの長さの関係を変えない（3 作目では 4 枚とも同じ割合で詰めた）。
- 出力: `image`（PNG）、任意で `report`（`model3d-face-length/0.1`。帯の行・ずらした量・詰めた後のあごの行・`above_band`）。

**確かめたこと**:
- CI では、合成の三面図で高さ・いちばん広い行・印の行（範囲の中のいちばん上と下、見つからないとき）・頭のかたまり・絵、合成の頭の絵で帯より上を
  変えないこと（既定では透けが 0 の画素の色が 0 になり、`above_band: copy` では帯より上の行が元とバイトまで同じ）と、あごの線が上へずれること、
  悪い設定を止めることを確かめた。行の付け替えは、OpenCV がある環境では、乱数の絵で OpenCV の remap と
  値まで同じかも確かめる。
- 2026-11-17 に、3 作目の VRM のダンスのリポジトリ外の台本（三面図を測る台本・頭のあたりを拡大する台本・頭の絵を詰める台本。OpenCV）と比べた
  （実測）。三面図は直す前と後の 2 組で、全部の行（いちばん上と下・中央・いちばん広い行・帯の紐と裾の縁）と割合、頭のかたまりが台本と同じで、
  制作のときの記録とも同じ。頭の絵は 4 枚（正面・左・右・後ろ）とも、詰めた絵が台本の出力とも制作で使った絵とも画素まで同じ（既定の
  `above_band: resample` で）。`copy` にすると、4 枚とも帯より上の透けが 0 の画素に残っていた色（134〜219 画素）が元のまま残り、違うのはその画素だけ。

### 骨の無い 3D の候補を測る（`check.candidate`）

キャラを作る道具は、体と頭の 3D の候補をいくつか作り、利用者が 1 つずつ選ぶ。候補は、頭の無い T ポーズの体（骨は無い）と、首の無い頭。
3 作目では、選ぶ前に体の候補を同じ物差しで測り、粘土の見た目で数方向から描いて並べた（2 作目でも 3 作目でも、候補どうしの細さはほぼ同じで、
体の絵とも同じだった。細さは候補選びでは変わらない）。

- 入力 `model`（.fbx・.glb・.gltf・.obj）。Blender で空のシーンに読み込み、全部のメッシュの頂点（基本の形を、物体の置き場所で世界の座標に）と
  三角形を書き出し（出力 `mesh` の npz）、Blender の外の numpy で測る（`model3d/mesh_slices.py`）。元のファイルは読むだけ。
- 体（`kind: body`。既定）: 床・いちばん上・体の中心（下半分の頂点の x の中央値）・腕の先（中心からいちばん遠い頂点の 0.85 倍より遠い頂点の
  高さの中央値）を取り、物差し S = 床から腕の先までの高さ。水平な面で切った切り口を面のつながりで輪にたどり（点を位置で分けると、面の粗い所で
  胴が細切れになった）、横の範囲が `gap`（既定 S の 0.002）より近く重なる輪をまとめる。胴は体の中心を含むかたまり、脚は中心を含まない左右の
  かたまり（幅が S の 0.02 倍を超える広い 2 つ）。
  - `rows`: くびれ（S の 0.80〜0.92 の高さで胴がいちばん狭い所）・ひざ（0.34〜0.42 でいちばん細い所）・ふくらはぎ（0.26〜0.33 で
    いちばん太い所）と、`thigh_at`（S に対する割合）の高さの太ももの幅と厚み（S に対する割合）。`thigh_at` に既定は無く、無ければ太ももは出さない
    （元絵の太ももの行に合わせて渡す）。
  - `by_U`: 足の裏からくびれまでの高さ U に対する、くびれ・足首・ふくらはぎ・ひざ・太もも（0.65 U）の幅と厚みと、裾の高さ（`check.body_image` と
    同じ決め方。足の裏から続けて脚が見えている高さだけ）。長い裾に隠れた脚は測れない（足首を探す高さまで見えなければ、裾の高さだけ）。`profile`: 胴の幅の並び（S の 0.45〜0.95、0.05 おき）。
- `slices`（高さの割合の並び。いちばん下 0 〜 いちばん上 1）: その高さの切り口の輪ごとの [左の端, 右の端, 前後の幅, 点の数]（箱の中心から、全高で
  割った値。点が 6 より少ない輪は除く）。太ももが左右に分かれているか、裾の中に何があるかを見る。
- 頭（`kind: head`）: 測らずに描くだけ。
- 出力: `report`（`model3d-candidate/0.1`）、任意で `views`（粘土の見た目（簡易描画・一色・くぼみの陰）の正射影で、正面・横・後ろと、体は下から見上げた
  正面（腰から下を大きく）、頭は斜め前を、`size`（既定 [520, 760]）で描いて並べた絵。縦長の絵でも、横に広い候補（T ポーズの腕）が切れないよう横の範囲にも合わせる）、`check`（体だけ。正面から見た頂点と、測った高さの線）、`mesh`。
  どれを選ぶかは決めない（合否は出さない）。

3 作目で分かったこと: 脚を測る所は、裾の下に脚と腰の形がある候補で使えた（2 作目は脚が裾に隠れていた）。裾の飾りと重なる高さでは、太ももを
測れない候補があった。

**確かめたこと**:
- CI では、合成の箱の体で、切り口の輪とまとめ方・太さの値・切り口の表・確認用の絵・測れない体を止めることと、工程の受け渡しと悪い設定（代役の
  Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（候補を測る台本・粘土で描く台本）と比べた（実測）。体の候補
  4 つで、全部の値（床・腕・S・くびれ・脚・U に対する値・胴の幅の並び）が台本と同じで、制作のときの記録とも同じ。切り口の表（6 つの高さ）も同じ
  （台本は 3 桁に丸めてから並べるので、並ぶ順だけ違うことがある）。確認用の絵は画素まで同じ。粘土の絵（体 4 つと頭 4 つで 32 枚）は、27 枚が画素
  まで同じで、ほかの 5 枚は 1〜4 画素が少し違う（台本を 2 回描いても、同じ所が同じくらい違う。簡易描画の揺れ）。
- 2026-11-18 に、見直しの指摘で、縦長の絵で横に広い候補が切れないよう、範囲を横にも合わせるようにした（台本の絵は、体の正面と後ろで手の先が
  切れていた）。直したあとは、体の正面と後ろの 8 枚だけが台本と違い（腕の先まで入る）、数・切り口の表・線の絵とほかの絵は前と同じ（簡易描画の揺れのほか）。

### 組み立ての頭身を測る（`check.assembly`）

キャラを作る道具は、選んだ体・頭・髪の候補を組み立て、頭身（頭と体の位置・大きさ）は利用者が道具の中で動かして決める。3 作目では、その前に
「今の配置は何頭身か」「元絵の比率にするには、頭を何倍にしてどの高さに置けばよいか」「そう置いたとき首がつながるか」を、数と絵で出した。
`check.assembly` はその台本を工程にしたもの。どう置くかは決めない（合否は出さない）。.blend は保存しない（元のファイルは変えない）。

- 入力 `blend`: 組み立てた .blend（T ポーズ、Z が上、正面は −Y、原点の上に立つ。1 Blender 単位 = 1 m）。`body`・`head`・`hair`（必須）: 物体の名前の並び。
  名前の物体とその子孫の、描かれるメッシュ（物体とコレクションの描かない設定と、ビューレイヤーの除外を見る）を集める（髪が頭のメッシュに含まれる
  なら、`head` と `hair` に同じ名前を書いてよい）。体のメッシュが頭・髪の名前の物体の子孫だと止める。
- 測る（Blender の中の numpy。`blender_adapter/assembly_measure.py`。切り口の輪のたどり方とくびれの探し方は `check.candidate` と同じ部品）。測るのは
  メッシュの基本の形を物体の置き場所で世界の座標にしたもの（形状キー・モディファイアは使わない。描く絵は Blender が評価した形なので、それらがあると
  線と絵がずれうる）:
  - 体: 床・いちばん上・腕の先（T ポーズ）・くびれ（床から腕の先までの高さ S の 0.80〜0.92 で胴がいちばん狭い所）。物差し U = 足の裏からくびれまで
    （`check.body_image`・`check.candidate` と同じ決め方）。いちばん上から 0.003〜0.08 m の深さの首の切り口の幅・厚み・中心。
  - 頭: 顔の真ん中（頭のメッシュのいちばん下の 4 % の頂点の横の範囲の真ん中）の縦の切り口で、鼻先（下 2/3 でいちばん前）とあご（鼻先より頭の高さの
    0.1 倍より下の点で、前下 45° にいちばん出ている点）。鼻先の高さの顔の幅と、前の輪郭の並び。
  - 髪: 顔の真ん中の縦の切り口でいちばん高い点（横にある角や飾りは入らない）。
  - 頭身 =（髪のてっぺん − 床）÷（髪のてっぺん − あご）。髪のてっぺんとあごの U に対する高さ、あごと体のいちばん上の差、頭のメッシュのいちばん下と
    体のいちばん上の差。体・頭・髪の、原点から横（x）と前後（y）にいちばん遠い所（`reach`。全身の絵の範囲に使う）。
- 提案: 頭と髪を `pivot`（省くと頭のメッシュの箱の中心）を中心に `scale` 倍し、`pivot` を新しい高さへ動かす。動かすのは `head`・`hair` の名前の物体の
  うち、祖先が名前に入っていない物（子は親について動く）。
  - `reference`（`top_U`・`chin_U`）: 元絵の髪のてっぺんとあごの、足の裏から測った高さを U で割った値（元絵の足の裏・くびれ・てっぺん・あごの行から
    （足の裏 − てっぺん）÷（足の裏 − くびれ）など）。頭の長さ L =（top_U − chin_U）× U、あご = 床 + chin_U × U。
  - `anchor`（`z`・`gap`・`heads`）: 体の側の目印（チョーカーの上の縁など）の高さ z（m）、元絵でのあごから目印までの間隔（頭の長さを 1 として）、
    目標の頭身。L =（z − 床）÷（heads − 1 − gap）、あご = z + gap × L。`reference` と両方あれば `anchor` を使う。
  - `scale` = L ÷ 今の頭の長さ、新しい高さ = あご − scale ×（今のあご − `pivot` の高さ）。`place`（`scale`・`z`・`y`）は手で決めた値: `scale` と `z`
    （`pivot` の新しい高さ）は提案の代わり、`y` は前後に動かす量（前がマイナス。前後は横の絵で決める。この工程は決めない）。`place` に `scale` と `z` の
    両方が無ければ、`reference` か `anchor` が要る。元絵の比率・目印・頭身に既定は無い。
  - 提案どおりにメモリの中で動かして、測り直す（`after`）。
- 出力: `report`（`model3d-assembly/0.1`。`now`・`proposal`・`after`・動かした物体・描いた絵の向きと範囲・`render_changes`）と、任意で絵（Workbench の
  正射影。色の絵そのままと粘土。今の配置と動かした後を上下に並べ、目印の高さに線を引く。`sheet_scale`（既定 0.5）倍で書く。.blend から引き継ぐと
  出てくる絵をすり替えうる描く設定（シーケンサー・コンポジター・描く範囲の切り取り・画素の縦横比・多視点・透ける背景・ぶれ・色の補正・出力の形式）と
  マーカーに結んだカメラは、`render.face_sheet` と同じく決まった値にし、.blend と違っていたものを `render_changes` に出す）:
  - `views`: 全身の正面・横・後ろ（色の絵）と正面・横（粘土）。写す範囲は床からいちばん高い所までの 1.06 倍（縦長の絵なので、原点から横に
    いちばん遠い所（`reach`。T ポーズの腕の先）が入らなければ、入るまで広げる）。
  - `bust`: 胸から上の正面・横・斜め（色の絵）と正面・横（粘土）。頭の真ん中から `bust` の `drop` 下を中心に、幅 `size`（m。既定は人の大きさの
    キャラ向けに 0.62 m と 0.10 m）。
  - `compare`: 入力 `illustration`（元絵）を、`illustration_rows`（元絵の `sole`・`waist`・`top`・`chin` の行）の足の裏とくびれが正面の絵にそろうよう
    拡大・縮小して並べる（元絵の側に元絵の行、モデルの側に測った高さの線）。
- 台本から移さなかった物: 首の太さからの倍率の見立て（体の首の切り口の幅 ÷ 頭のいちばん下の輪の幅）。頭の候補に首が無いと、いちばん下の輪は
  あごの先で、目安にならなかった（台本にもそう記録されていた）。物体は名前で選ぶ（台本はキャラを作る道具が物体に付けた印で選び、`pivot` も道具が
  シーンに残した置き方の値だった）。

3 作目で分かったこと: 頭の候補には首が無く（いちばん下があごの先）、首は体の側にある。U の物差しだけで置くと、あごが体の側の目印（チョーカーの
上の縁）に触れた（体の絵は、くびれから目印までが元絵より少し長かった）。あごの近くの目印に合わせ、頭身も守る置き方（`anchor`）のほうが、首の見え方が
元絵に近かった。初期の配置は頭の箱と体の箱の中心を前後でそろえるだけで、顔が首より前に出るので、横の絵で見て後ろへ下げた（`place` の `y`）。

**確かめたこと**:
- CI（Blender なし）では、合成の箱の組み立てで、測り方（くびれ・首・鼻先・あご・髪のてっぺん・頭身）、提案（元絵の比率・目印・手で決めた値）と動かした
  後に目標の比率になること、描く絵の並びと行、物体の集め方・動かす物体の選び方・体と頭の重なりを止めること、工程の受け渡しと絵の大きさ・悪い設定
  （代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（組み立ての頭身を測る台本と、絵に線を引いて元絵と並べる台本）と
  比べた（実測）。制作の 3 回（元絵の比率で置く・体の目印に合わせる・手で決めた値で置く）と同じ値で、今の配置と動かした後の全部の値（体・頭・髪・
  頭身・提案）が台本と同じで、制作の記録とも同じ。絵は、台本と同じ範囲の決め方では 60 枚のうち 51 枚が画素まで同じで、ほかの 9 枚（どれも粘土の
  絵）は 1〜6 画素が少し違った（台本を別の回に描いても、同じ置き方の粘土の絵が 2 画素違う。簡易描画の揺れ）。そのあと、全身の正面と後ろの絵を
  横にも合わせて広げるようにした（台本の絵は手の先が切れていた）。広げたあとは、その 18 枚（回ごとに 6 枚）だけが台本と違い、ほかの 42 枚のうち
  38 枚が画素まで同じで、4 枚（粘土の絵）は 1〜4 画素の揺れ。元絵と並べる絵の、元絵の倍率と上下のずれは台本と同じ式（正面の絵の範囲が広がった
  分だけ値は違う）。

### 材質の色の掛け率を戻す（`vrm.color_factor`）

3 作目の VRM のダンスで受け取った VRM は、下半身の材質（脚・パンツ・裾など）だけ、色の掛け率（MToon の `baseColorFactor`）が 1 より小さく、
影の色（`shadeColorFactor`）も同じ割合で暗かった（ほかの材質は 1）。色の絵そのものの肌の明るさは、太もも・胸もと・ほおでほぼ同じなので、
VRM を表示する道具では脚の肌が腕や顔より暗く出る。テクスチャの色をそのまま描く描き方（簡易描画の下見）は掛け率を使わないので、
下見では気づきにくい。VRM の材質の値で見つけられる。

- 入力 `blend`（VRM のキャラの .blend。VRM Add-on の設定入り）。`materials`（必須）: 戻す材質の名前。元のファイルは変えず、別名で保存する。
- VRM Add-on の MToon の設定の `baseColorFactor` の RGB を 1 にし（A はそのまま）、`shadeColorFactor` の RGB に「1 ÷ 元の掛け率」を掛ける
  （1 を超えない。元の掛け率が 10⁻⁶ 以下の色はそのまま）。VRM Add-on が、材質の中の MToon のノードの値も合わせて書き換える。色の絵は変えない。
- 挙げた材質が無い・MToon でない（VRM Add-on の mtoon1 が有効でない）なら、何も変えずに止める。VRM Add-on を有効にして起動する
  （`addons` の既定は bl_ext.user_default.vrm。ほかの `vrm.*` と同じ）。
- 出力: `blend`、`report`（`model3d-color-factor/0.1`。MToon の全部の材質の、変える前と後の `baseColorFactor` と `shadeColorFactor`、
  値の変わった材質 `changed`）。

**確かめたこと**:
- CI（Blender なし）では、戻す計算（影の色を 1 で止める・透けを変えない・0 に近い掛け率で割らない）と、工程の受け渡しと悪い設定を止めること
  （代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、3 作目の VRM のダンスのリポジトリ外の台本（掛け率を戻す台本）と比べた（実測）。
  同じモデル（裾の手直しの前の版）に、同じ材質を挙げてかけると、全部の材質の MToon の値と材質のノードの入力の値が台本の結果と同じ、
  `check.blend_match` も `exact`（8 物体）。どちらも `vrm.export` で書き出すと、VRM は SHA-256 まで同じ。
  変わったのはその材質の掛け率（1 に戻った）と影の色（同じ割合で明るくなった）と、MToon のノードの 3 つの入力だけ。制作で使った次の版のモデル
  （裾の手直しも入れた物）とも、材質の値は全部同じ。

### 首と頭のつなぎ目を直す（`model.neck_junction`）

3 作目の VRM のダンスで受け取ったモデルは、頭の部品に首が無く（いちばん下があごの先）、体の首は上の開いた筒で、首の骨だけに付いていた。
初期の姿勢では頭の中に隠れて見えないが、頭を上げる・かしげる・回すと、筒の上の縁や、あごと首のあいだの奥（後ろ髪）が見えた。
`model.neck_junction` は体のメッシュだけを直す（頭・髪・骨・色の絵は変えない）。首のまわりの形は入り組んでいて縁の輪がきれいに取れないので、
つながりには頼らず、位置（首の軸からの距離と高さ）だけで決める。

- 入力 `blend`。`body`・`neck_bone`・`head_bone`（必須）: 体のメッシュと、首と頭の骨（体の頂点グループも同じ名前）。`armature`（省くと、ただ 1 つのアーマチュア）。
- 首の上の端: 首の骨の付け根から頭の骨の付け根へ引いた軸から `radius` の内で、高さ `ramp_from_z`（m）より上、首・頭・`remove_groups` の重みの和が
  0.5 を超える頂点。いちばん上の高さを `z_top` とする。
  1. `remove_groups`（目の骨など。既定は無し）の重みを、体のメッシュの全部の頂点から外し、残りの骨で割り直す（残りが無ければ首の骨に 1）。
  2. 首の上の端の頭の骨の重みを、`ramp_from_z` の 0 から `z_top` の `head_weight_top` へなめらか（smoothstep）に変わる値 s で増やす。付け方は
     `head_weight`（既定 `add`）: `add` は、もとの重みを全部 (1 − s) 倍して頭に s を足す（台本と同じ）。もとから頭の重み h がある頂点は h(1 − s) + s に
     なり、s より大きく、栓の下の輪（頭 s）と少しずれる。`set` は頭の重みを s にし、ほかの骨の重みを合わせて 1 − s になるよう同じ割合で縮める（栓の
     下の輪とそろう。重みの和が 1 でない頂点も和が 1 になる。頭の骨だけだった頂点は、残りを首の骨に）。頭の重みが無く、重みの和が 1 の頂点では、
     どちらも同じ（丸めの差のほか）。
  3. 首の中から頭の中へ伸びる栓（ふた付きの筒）を足す。`z_top` より `plug_from_below_top` 下の高さ（±`slab`、既定 0.007 m）の首の上の端の頂点の
     輪郭を `plug_segments`（既定 28）の向きに分けて半径を取り（無い向きは輪の上で補い、[0.1, 0.2, 0.4, 0.2, 0.1] でならして `plug_inset`（既定 0.985）を
     掛ける）、上へ `plug_height` だけ、上ほど `plug_top_scale`（既定 0.5）へすぼめて `plug_rings`（既定 6）段の輪を積み、上をふさぐ（いちばん上の点は
     `cap`（既定 0.004 m）上）。下の輪は首の上の端と同じ重み、上へ向けて頭の骨だけにする。
  4. 栓と、首の上の端の面（栓の下の高さより `recolour_below`（既定 0.006 m）下から上で、頂点が全部首の上の端の面）の uv を 1 点にまとめ、首の肌の
     1 色にする（縁に描かれた筆の跡や帯を消す。ふだんは頭の中に隠れている所）。色は、栓の下の高さから `uv_band`（既定 0.008 m）下までの首の上の端の
     頂点の色（最初の頂点の面の材質のいちばん大きい色の絵の、5×5 画素の平均。頂点ごとに、その材質の面の角の uv で読む）の中央値に、いちばん近い
     頂点の uv。1 色にする面の材質も、その材質にそろえる（首の上の端が別の色の絵の材質にまたがっても、同じ色になるように）。
- 首の直しの量（`radius`・`ramp_from_z`・`head_weight_top`・`plug_from_below_top`・`plug_height`）に既定は無い（モデルの大きさと首の作りで決まる）。
  既定のある細かな値（栓の網の細かさ・色を取る帯などの作り方の値）は、移した台本の既定と同じ。
- 1 Blender 単位 = 1 m のシーンだけ。形状キーのある体（足した栓の形状キーを決められない）・ほかの物体とメッシュを分け合う体（リンク複製）は止める。
  首の上の端・栓の高さの頂点・色を取る頂点・色の絵が無いときも止める。元のファイルは変えず、別名で保存する。
- 出力: `blend`、`report`（`model3d-neck-junction/0.1`。首の上の端の頂点の数と `z_top`、重みを外した頂点・付け替えた頂点（そのうち、もとから頭の重みが
  あった頂点。`set` で結果が変わるのはこの頂点だけ）と `head_weight`・1 色にした面（そのうち、材質をそろえた面）の数、栓の高さ・中心・半径（cm）・
  頂点と面の数・uv・材質、色を取った頂点の数と色）。

3 作目で分かったこと: 栓の色をはじめ「首の前の 1 頂点」から取ると、首の上の縁の黄色い所を拾い、栓だけ色が違って見えた（色の中央値に近い頂点にした）。
1 色にする範囲を栓の下の高さちょうどにすると、境目をまたぐ面に縁の白っぽい帯が残った（`recolour_below`）。直したあとも、上を向くと首の縁に薄い筋が
1 本見え、下を向いた横の絵ではうなじ側に栓の後ろが四角く見えた（長い髪を揺らしていない絵。揺らした髪で隠れるかは確かめていない）。

**確かめたこと**:
- CI（Blender なし）では、頭の重みの段（端で傾きが 0）と `set` の付け方（もとから頭の重みがある頂点・頭の骨だけの頂点・重みの和が 1 でない頂点）、色を取る角の選び方（その材質の面の角）、栓の輪の半径（輪をなぞる・無い向きを補う・ならす）、既定は細かな値と `add` だけなこと、
  工程の受け渡しと悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、3 作目の VRM のダンスのリポジトリ外の台本（首のつなぎ目を直す台本）と比べた（実測）。
  体つきを直した版のモデルに台本と同じ値をかけると、制作で使った首を直したモデルとも、台本をかけ直したものとも `check.blend_match` で `exact`
  （8 物体）。`vrm.export` で書き出した VRM も、制作のモデルから書き出した物と SHA-256 まで同じ。首の上の端の頂点・目の骨の重みを外した頂点・
  1 色にした面の数、栓の頂点と面の数と半径も、台本の記録と同じ。
- 2026-11-18 に、見直しの指摘で頭の重みの付け方（`head_weight`）と、材質をまたぐ所の色（色を取る角と 1 色にする面の材質）を直したあとも、既定で
  上と同じ（材質をそろえた面は 0）。`set` にすると、このモデルでは首の上の端のうち、もとから頭の重みがあった 9 頂点の重みが変わり、書き出した VRM は
  制作の物と同じにならない（`check.blend_match` は `exact` のまま。重みは比べない）。

## VRM のキャラを動かす

VRM のキャラ（VRM Add-on で読み込んだ .blend）を、model3d の動きの工程で動かす道筋。2 作目の VRM のダンスで、リポジトリの外の
台本で通した手順を工程にしたもの。工程の `vrm.*` は、どれも VRM Add-on を有効にして Blender を起動する（既定
`addons: ["bl_ext.user_default.vrm"]`。拡張機能のフォルダは `blender_extensions` か `--tool blender_extensions=DIR`）。

1. `vrm.motion_rig`: 骨の名前を Mixamo 式に付け替えた作業コピーと、名前の対応表を作る（下）。
2. 作業コピーへ動きを移す（`motion.prepare_keypoints` → `check.motion` → `motion.retarget`。Mixamo 式の骨の名前を前提にする
   ほかの動きの工程も、作業コピーで使える）。何本かの動画から 1 つの踊りを作るときは、本ごとに移してから `motion.join_clips` で
   1 本に組む（[別々に骨へ載せた動きを 1 本に組む](#別々に骨へ載せた動きを-1-本に組む)）。
3. `vrm.apply_motion`: 動きを元の VRM の .blend へ戻す。`vrm.bake_springs`: VRM の揺れものを動きに合わせて骨のキーに焼く（下）。
4. アセットにするときは `vrm.export`（VRM 1.0 の書き出し）と `check.vrm`（中身の検査。Blender なし）。表情を形状キーで作るときは、その前に
   `vrm.expressions` で VRM の表情に割り当てる（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。

### 骨の名前を Mixamo 式に付け替える（`vrm.motion_rig`）

- 入力 `character`: VRM のキャラの .blend（VRM 1.0。VRM Add-on の人型の骨の割り当て入り）。
- 人型の骨の割り当て（`vrm_addon_extension.vrm1.humanoid`）から、model3d が役割を知っている骨（`semantic.MIXAMO_ROLES`）だけを
  Mixamo 式の名前（`bone_prefix` ＋ 名前。既定の接頭辞は `mixamorig:`）に付け替える。

  | VRM 1.0 の人型の骨 | Mixamo の名前 |
  |---|---|
  | hips・spine・chest・upperChest・neck・head | Hips・Spine・Spine1・Spine2・Neck・Head |
  | left／right の Shoulder・UpperArm・LowerArm・Hand | Left／Right の Shoulder・Arm・ForeArm・Hand |
  | left／right の UpperLeg・LowerLeg・Foot・Toes | Left／Right の UpLeg・Leg・Foot・ToeBase |
  | 親指の Metacarpal・Proximal・Distal | HandThumb1・2・3 |
  | 人差し指・中指・薬指・小指の Proximal・Intermediate・Distal | HandIndex・HandMiddle・HandRing・HandPinky の 1・2・3 |

  - 目とあご（leftEye・rightEye・jaw）は付け替えず、報告の `unmapped` に残す。
  - `motion.retarget` が要る骨（腰・背骨 3 本・首・頭・腕と脚の 12 本。upperChest も要る）に割り当てが無ければ止まる。指とつま先は無くてもよい。
  - 付け替えた名前がほかの骨の名前とぶつかるとき（Blender は `.001` を付けてずらす）は、付け替える前に止まる。すでに Mixamo 式の
    名前の骨（Mixamo 式の骨から VRM にしたキャラ）は、そのまま（付け替えの数 0）。
- 骨の名前を変えると、その骨で動くメッシュ（アーマチュアのモディファイアか、アーマチュアを親にした変形）の頂点グループの名前も
  Blender が付け替える（付け替えるはずの頂点グループが元の名前のまま残ったメッシュがあれば止まる）。
- キャラの骨のあるシーンだけを残す（作業用のシーンがほかにあるファイルがある）。ほかのアーマチュアも作業コピーに持ち込まない
  （`motion.retarget` などの工程は、ファイルの最初のアーマチュアを使うため。消したものは報告の `removed_armatures`）。元のファイルは変えない。
- 出力:
  - `blend`: 作業コピー
  - `bone_map`: `model3d-vrm-bone-map/0.1`（`prefix`・`map`（元の名前 → 付け替えた名前。上の表の並び）・`humanoid`・`unmapped`）
  - `report`: `model3d-vrm-motion-rig/0.1`（付け替えた骨の数、メッシュごとの付け替わった頂点グループの数、残したシーン、消したシーン）
  - `skip`（`skip_meshes` を挙げたとき）: 挙げたメッシュの頂点をすべて真にした npz（鍵はメッシュの名前）。`motion.retarget` の
    `skip_vertices` に渡すと、髪の前に手があるだけで胴への刺さりと判定しない（名前で当てるので、頂点の数が同じ別のメッシュには当てない）。

**確かめたこと**（Windows の Blender 5.2.4 と VRM Add-on 4.10.5、2026-11-16。実測）: 2 作目の VRM のダンスのキャラ（骨 131 本・メッシュ 11）で、
リポジトリの外の台本（決めた名前の表で付け替える）と同じ入力から作った作業コピーと比べた。
- 対応表の `map`（52 本）が、並びまで同じ。目の 2 本は `unmapped` に残った。
- 作業コピーのシーン・物・骨（名前・親・基本の姿勢）・メッシュごとの頂点グループの名前と並び・頂点の数・モディファイアが同じ。
- `skip` の npz の値が同じ（髪の 24,943 頂点）。
- 制作で使った作業コピー（元のモデルから作ったもの）とも、骨・頂点グループ・シーン・物が同じ。

**限界**: VRM 0.x の人型の骨は未対応（VRM 1.0 だけ）。割り当てが正しいかは確かめない（VRM Add-on の割り当てをそのまま使う）。
VRM Add-on は骨を名前ではなく骨ごとの識別子で追うので、作業コピーの人型の骨の割り当ては付け替えた名前を指す（実測。目の骨は元の名前のまま）。
作業コピーは動きを付けるためだけに使い、VRM として書き出さない（書き出すのは、動きを戻した元の VRM の .blend）。

### 動きを元の VRM へ戻す（`vrm.apply_motion`）

- 入力: `character`（元の VRM のキャラの .blend）、`motion`（作業コピーで動きを付けた .blend。`motion.retarget` などの出力）、
  `bone_map`（`vrm.motion_rig` の対応表。`map` だけの表でもよい）。
- `motion` のアクションのうち F カーブのいちばん多いものを取り込み、骨の F カーブの名前を対応表の逆で元の名前へ戻して、
  キャラのアーマチュアに付ける。キャラに無い骨の動きがあれば止まる。対応表に無い骨（揺れものの骨など）の名前はそのまま。
- キャラにもとからある動き（アクション・NLA の層）は外す（`motion.retarget` と同じ。残すと新しい動きに混ざる。ドライバーは残す）。
  骨の回転の持ち方（クォータニオン・オイラー・軸角）は、motion の .blend の骨にそろえる（`motion.retarget` は骨をクォータニオンにして
  キーを打つので、オイラーのままの骨ではキーが使われない）。そのうえで、`motion.retarget` が移す前にするのと同じく、骨の回転と位置を
  基本の姿勢に戻す（キーの無いほかの骨の位置などが元のキャラの姿勢のまま残らないように。大きさは変えない）。
  作業コピーの値をそのまま写す方法は使わない（移し替えで解いた後の小さな残り、たとえば脚の大きさ 0.99998 を持ち込み、
  採用した踊りから 0.25 mm ずれた）。
- キャラのアーマチュアの入ったシーンを今のシーンにする（作業用のシーンを開いたまま保存されたファイルがある）。`vrm.bake_springs` も同じ。
- 外したもの・持ち方を変えた骨の数・基本の姿勢に戻した骨の数・使ったシーンは報告に残る。
- シーンのコマの範囲を動きのキーの範囲に、fps を `motion` の .blend のシーンの fps（`motion.retarget` の `fps`。パラメーター `fps` で
  上書きできる）にする。`model3d_points_frames`（各コマが points の何コマ目か）も写す。
- 出力: `blend`（VRM の設定と材質はキャラのまま）、`report`（`model3d-vrm-apply-motion/0.1`）。

### 揺れものを焼く（`vrm.bake_springs`）

- 入力: `blend`（動きを付けた VRM のキャラ）、任意で `overrides`（揺れものの設定の上書きの JSON）と `joined`（`motion.join_clips` の report）。
- VRM Add-on の揺れもの（VRM 1.0 のばね）の計算を `1/rate` 秒（既定 1/60 秒）刻みで進め、コマごとに揺れものの骨の回転をキーとして打つ。
  最初のコマの姿勢で `settle_s` 秒（既定 2）進めて落ち着かせてから始める。刻みの回数は「経過秒 ÷ 刻み」を丸める（24 fps と 1/60 秒では
  奇数コマでちょうど .5 になり、式の順で丸めが変わるので、制作で使った台本と同じ式にしてある）。
- 切り替えのコマ（`cuts`。Blender のコマ番号の並び。または `joined` の report の `cuts`。両方は渡せない）: 別々に骨へ載せた動きを組んだ踊り
  （[別々に骨へ載せた動きを 1 本に組む](#別々に骨へ載せた動きを-1-本に組む)）で、新しい本が始まるコマ。そのコマでは、揺れものの骨を初期の向きに、
  計算の状態を初期に戻し、そのコマの姿勢で `cut_settle_s` 秒（既定は `settle_s` と同じ）進めて落ち着かせ直してから、キーを打つ。
  刻みの回数は、切り替えのコマから数え直す。切り替えのコマでは姿勢が飛ぶので、続けて計算すると、切り替えの直後に裾と髪が大きく跳ね上がる。
  - 最初のコマより後で最後のコマまでにないコマは止める。`joined` のときは、組んだ動きのコマの範囲（report の `frames`）が、焼く .blend の
    シーンの範囲と同じかも確かめる（別の動きの report を渡したときに止める）。渡さなければ、今までと同じ焼き方（同じ引数で Blender を起動する）。
- 運ぶ区間（`carry`。[最初, 最後] の並び。両端を含む）: その区間のコマでは、計算を進める前に、揺れものの節が覚えている位置を、`carry_bone`
  （既定は VRM の人型の hips に割り当てた骨）の前のコマからこのコマへの動きで写す。揺れものから見ると腰が止まっていたことになり、
  体が急に落ちる・上がる勢いが揺れものに伝わらない（脚などの当たりや硬さはそのまま効く）。切り替えのコマでは運ばない（落ち着かせ直すので）。
  - 使いどころ: 元の 2D の動画が 1 コマで姿勢を切り替えているのに、骨へ移した動きがそれを数コマの急な動きでつないでいる所。3 作目の
    VRM のダンスでは、立ち姿から深いしゃがみへの降下（数コマで腰が数十 cm 下がる。ほぼ自由落下の速さ）で、裾が一面に裏地を見せて
    跳ね上がり、髪も大きく広がった。運ぶと収まった。
  - `carry_skip`（揺れものの名前に含む文字の並び）: 運ばない揺れもの（勢いをそのまま伝える）。太ももの上に乗るには勢いで一度持ち上がる
    必要のある鎖（3 作目では裾の前寄りの鎖）は運ばない。運ぶと太ももの内側・下側に入ったままになり、立ち上がったあとまで太ももが裾を
    突き抜けた。
  - 採らなかった方法: 区間のコマごとに落ち着かせ直す（`cuts` を毎コマ）。しゃがんだ姿勢で初期の向きから計算をやり直すと、鎖が
    太ももの下側へ入り、区間のあと 100 コマほど裾が乱れた。
  - VRM Add-on は、揺れものの節の位置を、節ごとの `animation_state` の `previous_world_translation`・`current_world_translation`
    （世界の座標）に持つ（4.10.5 で確かめた）。運ぶのは、この 2 つを運ぶ骨の動きで写すこと。区間・運ぶ骨・運ばない揺れものに当たらない
    名前は止める。
- 動きの側に揺れものの骨のキーがあれば先に消す（残すと、コマを進めるたびに計算の結果が打ち消される）。アドオン自身のコマ送りの計算は、
  焼く間だけ止めて、終わったら戻す。
- `overrides`（この .blend の中だけ。元の VRM は変えない）:
  - `{"<揺れものの名前に含まれる文字>": {"<関節の設定の名前>": 値}}`（例 `{"sleeve": {"stiffness": 0.2, "gravity_power": 0.9}}`）
  - `"_capsule_radius": {"<骨の名前に含まれる文字>": 半径}`（今あるカプセルの当たり判定の太さ）
  - `"_colliders": [{"bone", "offset", "tail", "radius", "groups"}]`（カプセルの当たり判定を足す。位置は骨から見た座標。groups は当たりの組の名前）
  - `"_release_walls": [{…}]`（壁をゆるめる。姿勢に応じて、揺れものの「壁」の球の当たりを小さくする。見張りの当たりつき）:
    - `springs`（揺れものの名前に含む文字の並び）・`group`（当たりの組の名前に含む文字）: その揺れものが使う、名前に `group` を含む組の
      球の当たり（壁）を小さくする。球が 1 つも無い・ほかの指定と同じ球を選ぶ指定は止める。
    - `bones`（見張る骨の並び）・`reference`（基準の骨。既定は VRM の人型の hips）・`on_deg`・`off_deg`: 見張る骨の向き（付け根から先）が、
      基準の骨から見て、静止の姿勢の向きから何度離れたかを測る。全部が `on_deg` 以上になったら小さくし始め、どれかが `off_deg` 以下に
      なったら戻し始める（間の角度では今の状態を続ける）。`bones` の代わりに `frames: [最初, 最後]` と書くと、コマの区間で決める（シーンのコマの範囲の外は止める）。
    - `radius_to`（小さくした球の半径。m）。小さくするのは `ease_in_s` 秒、戻すのは `ease_out_s` 秒かけて、なだらかに
      （既定は 3 作目の VRM のダンスで使った 0.2 秒と 0.3 秒）。
    - `guard_bone`: 小さくしている間だけ「見張りの当たり」を立てる。小さくする球と同じ球を、静止の姿勢の位置のまま `guard_bone` に
      付けたもので、ふだんは半径 `guard_radius_from`（既定 0.01 m。効かない）、小さくしている間は元の半径 × `guard_radius_scale`（既定 1）。
      壁を小さくしている間に、鎖が逆の側（腰の下など）へ巻き込まれるのを止める。
    - 焼き終えたら、球は元の大きさに戻し、見張りの当たりは外して保存する（足した組は名前でなく識別子で探して外す。当たりと組の数は元のまま。違えば止める）。報告の `released_walls`
      に、ゆるめていたコマの区間。**VRM には入らない**（VRM の当たりの大きさは姿勢で変えられない）。
  - `_` で始まるほかの鍵（メモ）は読まない。値は数（向きは数 3 つ）。どの揺れもの・当たり判定・組にも当たらない鍵、関節に無い設定の名前、
    数でない値は、Blender を起動する前か .blend を書く前に止まる。
- `no_spring` に挙げた文字を名前に含む揺れものの骨は動かさず、初期の向きのままにする（布の計算など、別の方法で動かす部位）。
- 出力: `blend`、`report`（`model3d-vrm-bake-springs/0.1`。関節の数・止めた関節・刻みの回数・上書きした揺れもの・切り替えのコマと
  落ち着かせ直した秒数・運んだコマ（最初・最後・数）と運ぶ骨・壁をゆるめていたコマの区間・焼く前と後の当たりの数・焼いた時間）。

**揺れものの知見**（2 作目の VRM のダンスの制作で分かったこと）: 袖のように T ポーズの向きへ戻ろうとする部品は、硬さを下げて重力を
上げないと（例 硬さ 0.2・重力 0.9）、腕を下ろしたときに体の内側へ巻き込まれた。別のキャラでは、腕の当たり判定がしゃがみながら腕を
開く振りで髪をはじいた（腕の当たりを外して直った）。脚に当たる長い布は、揺れものの骨数本では骨の間の布が脚を通り抜けるので、布の計算に
する（`no_spring` で揺れものから外す）。

**裾の「壁」の知見**（3 作目の VRM のダンスで分かったこと）: キャラを作った道具の裾は、鎖ごとに、太ももに付いた大きな球の当たり（面が
太ももの軸から少し外にある、ほぼ平らな壁）で太ももの外へ押し出されている。
- 球の当たりは、鎖が正しい側にいる時しか押し出せない（球の中心は太ももの内側・下側にあり、そこより下へ入った鎖は、下へ押される）。
- 両方の太ももが水平より上がる深いしゃがみでは、前の真ん中の鎖の壁が台になり、裾の前を皿の形に持ち上げた（下着と後ろの裏地が見えた）。
- 壁は立っている時もいつも裾を押し出しているので、壁を外す・腰の骨へ付け替えると、ふだんの踊りの裾の前まで変わった（中央値で 6〜8 cm）。
  深くしゃがんでいる間だけ壁を小さくした（`_release_walls`）。全部引っこめる（脚の当たりの中まで）と、前の真ん中が深く垂れて太ももの付け根が出た。
- 見張りの当たり無しで壁をゆるめると、立ち上がる時に裾の前の鎖の先が腰の下（脚の付け根の間）へ巻き込まれ、裾の前が体の中へ入った
  （裾には腰の当たりが無い）。正面だけでなく、斜め・後ろ・横からも描いて見つけた。
- 壁の大きさを姿勢で変える手当ては VRM に書けない。VRM をビューアなどで動かして深くしゃがませると、皿の形のまま（[動画とアセットを近づける](#vrm-を書き出すvrmexportと中身を確かめるcheckvrm)）。

**確かめたこと**（Windows の Blender 5.2.4 と VRM Add-on 4.10.5、2026-11-16。実測）: 2 作目の VRM のダンスで、リポジトリの外の台本
（動きを戻して揺れものを焼く 1 本の台本。押し出しの補正は外して）と同じ入力・同じ上書き（`no_spring: ["skirt"]`）から作った .blend と比べた。
- アクションの F カーブ 399 本のキーと取っ手が、バイト単位で同じ（名前を戻した F カーブ 211 本。揺れものの関節は 76 本で、そのうち
  動かさない 32 本はキーを打たない）。
- 7 コマ（1・150・300・450・600・690・720）で、全メッシュの変形した頂点の位置の差が 0.0 mm。
- 採用した踊りの .blend（制作の利用者の設定で焼いたもの。その後の段で助走のキーが 2 つ足されている）とも、1 コマ目からのキーの値が
  399 本すべて同じだった（`--factory-startup` で焼いても変わらない）。
- 720 コマで、動きを戻すのが約 3 秒、揺れものを焼くのが約 23 秒。

**切り替え・運ぶ区間・壁の確かめ**（Windows の Blender 5.2.4 と VRM Add-on 4.10.5、2026-11-17。実測）: 3 作目の VRM のダンス（3 本の動きを組んだ
954 コマ・揺れものの関節 76 本）の、動きを戻した .blend と同じ上書きから焼いた。
- 切り替えを渡さないとき: 切り替えを足す前の `vrm.bake_springs` で焼いた .blend と、F カーブ 527 本のキー・取っ手がバイト単位で同じ
  （今までと同じ焼き方）。
- 切り替え 2 か所を渡したとき: リポジトリの外の台本（切り替えのコマで落ち着かせ直す台本。ほかの手当てを外して）と同じ入力・同じ指定で焼いた .blend と、
  F カーブ 527 本のキー・取っ手がバイト単位で同じ。`joined`（`motion.join_clips` の report）で渡しても同じ。
- 渡さないときと比べると、最初の切り替えのコマより前のキーは同じで、そのコマから後の揺れものの骨の F カーブ 212 本が変わる。
- 運ぶ区間（1 つ。運ばない揺れものあり）を足したとき: リポジトリの外の台本（切り替えと運ぶ区間の手当て。壁の手当てを外して）と
  同じ入力・同じ指定で焼いた .blend と、F カーブ 527 本のキー・取っ手がバイト単位で同じ。運ぶ骨は既定の読み方（人型の hips の割り当て）で、
  台本が名前で決めていた骨と同じになった。運ばないときと比べると、区間の最初のコマより前のキーは同じ。
- 切り替えも運ぶ区間も渡さないときは、手当てを足したあとも、足す前の `vrm.bake_springs` とバイト単位で同じ。足す前の工程を 2 度回しても同じ
  （焼き方は毎回同じ）。
- 壁をゆるめる指定（`_release_walls`。見張りの当たりつき）も足したとき: リポジトリの外の通しの作り直しの、揺れものを焼く段と同じ入力・
  同じ指定（上書きのファイルも同じ）で焼いた .blend が、その段の出力と、F カーブ 527 本のキー・取っ手がバイト単位で同じ。
  `check.blend_match`（`pose_frames: all`、変形した形は 14 コマ）で `exact`: 骨 137 本の姿勢が全 954 コマで差 0、8 つのメッシュの変形した形
  （112 件）も同じ。ゆるめていたコマの区間も台本の記録と同じで、見張りの当たりを外したあとの当たりの数は元のまま（19）。
- model3d の工程だけで、本ごとに骨へ載せた 3 本から `motion.join_clips` → `motion.twist_split` → `vrm.apply_motion` →
  `vrm.bake_springs`（切り替えのコマは `joined`）と通したものも、台本の流れの各段の出力（組んだ動き・ねじれを分けた動き・動きを戻した
  .blend・揺れものを焼いた .blend）と `check.blend_match` で `exact`（骨の姿勢は全コマ）。
- 954 コマで、Blender の起動を含めて約 45 秒。

**限界**: 揺れものの計算は VRM Add-on の中の関数（`<アドオン>.editor.spring_bone1.handler.update_pose_bone_rotations`）を直接呼び、
運ぶ区間では節の計算の状態（`animation_state`）を書き換えるので、アドオンの版が変わると動かないことがある（4.10.5 で確かめた）。食い込みの押し出しは入れていない（`collision.push_out` と `render.stage` の
`push` を使う）。

### VRM を書き出す（`vrm.export`）と中身を確かめる（`check.vrm`）

- `vrm.export` の入力: `blend`（VRM のキャラの .blend。VRM Add-on の設定入り）、任意で `meta_from`（メタを写す元の VRM 1.0）。
- 骨を基本の姿勢に戻し（動きはアクションも NLA の層も外し、姿勢の値を消す。ドライバーは残す）、別名の .blend（出力 `blend`）に保存してから、
  VRM Add-on で VRM 1.0（出力 `vrm`）を書き出す。
  動きは VRM に入らない。元のファイルは変えない。キャラのアーマチュアの入ったシーンを今のシーンにしてから書き出す（`vrm.apply_motion` と同じ）。
- `meta_from` があれば、メタの名前・版・作者を写す（.blend には VRM の名前と作者が残らないことがある。利用条件などほかのメタは .blend のまま）。
- `jpeg_quality`（1〜100。既定 0 は変えない）で、材質が使う画像を JPEG にして埋め込み直す（色の絵を PNG のまま埋めると大きい。
  2 作目の VRM のダンスでは 4 枚で 68 MB → JPEG 品質 95 で 14 MB）。透過の要る画像には使わない。
- 書き出した VRM を `check.vrm` と同じ検査にかけ、出力 `check` に残す。不合格なら needs_review（`strict` なら失敗）。
- 骨の拘束: VRM Add-on は、VRM 1.0 の拘束（`VRMC_node_constraint` の aim・roll・rotation）の形の拘束だけを、骨 1 本に 1 つ書き出す
  （ミュートした拘束は書かない。形は [ねじれを分ける補助の骨](#ねじれを分ける補助の骨modeltwist_bones)）。報告 `report` の `bone_constraints` に
  .blend の骨の拘束（骨・拘束の名前・種類・ミュート・影響・相手と、Copy Rotation の軸・混ぜ方・空間・反転、Damped Track の向き・`head_tail`）を並べる。
  書き出した拘束（`check` の `structure` の `node_constraints`）ごとに、同じ骨の拘束のうち、VRM の拘束の形（roll: 軸 1 つ・Add・ローカルどうし・
  反転なし。rotation: 同じで軸 3 つ。aim: Damped Track の `head_tail` 0）の種類と軸・相手・重み（影響）が同じ最初のものを書き出されたものとし、
  観測に、書き出した数（`constraints_exported`。VRM から読んだ数）と、書き出されなかった拘束（`constraints_not_exported`）、.blend に見つからなかった
  VRM の拘束（`constraints_unmatched`。ふつうは無い）を載せる。合否には使わない（IK など、VRM に要らない拘束もあるため）。
- `check.vrm`（Blender なし。GLB を読むだけ）: メタ（名前・作者・利用条件）、人型の骨の数と必須の骨、メッシュ、材質の数と MToon の数、画像（種類と大きさ）、
  揺れもの（名前と関節の数）・当たり判定・組の数、形の割り当てのある表情（`expressions_bound`）。合否は、VRM 1.0 の拡張があるか・人型の必須の骨（VRM 1.0 の 15 本）・
  メタの名前と作者とライセンスの URL（VRM 1.0 で必須）・番号の参照が範囲の中か（人型の骨・揺れものの関節と center・当たり判定と組・
  firstPerson・表情の割り当ての node と material・形の割り当ての node のメッシュと形の番号・サムネイルの画像）、だけ。見た目は判定しない。
  不合格は needs_review（`strict` なら失敗、`report_only` なら測るだけ）。
  - 形（モーフ）: `morph_targets` に、形のあるメッシュごとの番号・名前・形の数・形の名前（glTF の `mesh.extras.targetNames`。VRM Add-on が書く。無ければ null）。
  - 表情の形の割り当て: `expressions` の `preset`・`custom` に、表情ごとの割り当ての並び（node・メッシュの名前・形の番号 `index`・形の名前 `target`・重み）。
    割り当ての無い表情は空の並び。node にメッシュが無い割り当てと、`index` がそのメッシュの形の数の外の割り当ては不合格。
  - 作りの一覧（`structure`）: キャラを作る道具から受け取った VRM を、Blender に持ち込む前に知るためのもの（3 作目の VRM のダンスで、
    リポジトリの外の台本で調べていたもの）。数と名前を並べるだけで、良し悪しは決めない。
    - `meshes`: メッシュごとの primitive（頂点・三角形・描き方 `mode`・材質・形の数・属性）と、そのメッシュを使う node。
    - `skeleton`: skin の関節の数、人型の骨の node と位置（glTF の座標。Y が上、m。親の回転と拡大も入れる）、揺れものの関節の node の数、
      人型でも揺れものでもない関節（`other_joints`）、名前に twist・roll を含む関節（`twist_like`。名前で見るだけ）。
    - `node_constraints`: 骨の拘束（VRMC_node_constraint）の node ごとの種類（roll・aim・rotation）・source・軸・重み。
    - `springs`: 揺れものの鎖ごとの関節の数・最初と最後の node・固さ・重力・引きずり・当たりの半径の [最小, 最大]（全部の関節）・当たりの組・center。
      `colliders`（node・形・半径・位置）、`collider_shapes`（形ごとの数）、`collider_groups`（名前と当たりの数）。
    - `materials`: 透け方・両面・色の掛け率（`baseColorFactor`）と色の絵、MToon の値（影の色 `shadeColorFactor` など）と影の色の絵。
      MToon でない材質は `mtoon` が null。
    - `images`（形式・[幅, 高さ]・バイト数。大きさは PNG の IHDR と JPEG の SOF から）、`look_at`（視線）、`first_person`。
    - `weights`: skin のある node ごとの、頂点の数・使う骨の数・1 頂点に付く骨のいちばん多い数・JOINTS_1 があるか・骨ごとの頂点の数
      （重みが 1e-4 を超える (頂点, 枠) を、骨の node の番号ごとに数える。名前は重なりうるので添えるだけ）。VRM は 1 頂点に骨 4 本まで（JOINTS_0）
      なので、重みを書き換えたモデルは書き出した VRM で数え直す。
    - 値はファイルにあるまま（無い値は null。決まりの既定の値は入れない。`alphaMode`・`doubleSided` だけ glTF の既定 OPAQUE・false）。
    - たどった参照の番号（skin の joints・primitive の材質と accessor・骨の拘束の source・材質のテクスチャとその画像・画像と accessor の
      bufferView・JOINTS_0 の番号・node の children）が範囲の外なら不合格。node の親子の循環も不合格。sparse の accessor・GLB の外の
      バッファとファイル・BIN チャンクに収まらない値は読まずに `unread` に理由を書く（不合格にはしない）。

**VRM に入らないもの**（2 作目の VRM のダンスで分かったこと）: 表情を頭の絵の差し替えで作ったもの（VRM の表情は形の変化。形状キーで作った表情は、
`vrm.expressions` で割り当てれば入る。[表情を形で作る](#表情を形で作るvrm-に入る表情)）、布の計算、
VRM 1.0 の拘束（aim・roll・rotation）の形でない骨の拘束（`model.twist_bones` の骨は書き出せる形。`VRMC_node_constraint` に対応しない
アプリでは効かず腕が崩れうる。[ねじれを分ける補助の骨](#ねじれを分ける補助の骨modeltwist_bones)）。動画用とアセット用は別物になると、最初に伝える。

**動画とアセットを近づける**（3 作目の VRM のダンスで分かったこと）: 踊りの .blend の中で直した物のうち、重み・材質の値・メッシュの手直しなど
モデルに属する物は、同じ日にモデル（書き出しの元の .blend）にも入れ、`vrm.export` の確認用の書き出しで確かめた（6 回の書き出しのうち 5 回が確認用）。
アセットに入らなかったのは、姿勢に応じて大きさを変えた揺れものの当たり（VRM の当たり判定の大きさは、姿勢で変えられない。`vrm.bake_springs` の
`_release_walls`。[揺れものを焼く](#揺れものを焼くvrmbake_springs)）と、踊りごとの腕の動きの直し
（動きは VRM に入らない）だけだった（2 作目では、動画とアセットが別物になった）。`check.vrm` は中身の名前・数・参照の範囲を見るだけなので、
納める前にビューアでも開き、表情を 1 つずつ効かせて確かめた。

**確かめたこと**（Windows の Blender 5.2.4 と VRM Add-on 4.10.5、2026-11-16。実測）:
- 2 作目の VRM のダンスの衣装のアセット（制作で書き出した VRM 1.0、14.2 MB）の書き出し元の .blend から書き出し、制作の VRM とバイト単位で同じだった。
- メタの写しと JPEG（品質 95）の経路も、制作の台本（衣装を切る手直しを外して）と同じ入力から書き出して、バイト単位で同じだった。
- `check.vrm` で、制作の VRM は合格（人型の骨 54・材質 4（どれも MToon）・画像 4（JPEG）・揺れもの 13・当たり判定 19）。
  別の作り方（Mixamo 式の骨の 1 枚の殻から VRM にしたもの）の VRM 3 版（約 120 MB）も合格した（人型の骨 52・揺れもの 6）。
- 形の名前と表情の割り当て（2026-11-17。実測）: 3 作目の VRM のダンスのアセットの VRM で、頭のメッシュに形 11、形の割り当てのある表情 8
  （blink 2・blinkLeft 1・blinkRight 1・happy 5・relaxed 3・angry 2・sad 2・surprised 2 個の割り当て）。リポジトリ外の台本（VRM の中の表情を読む台本）と、
  そのアセット・キャラを作った道具が書き出した元の VRM・2 作目の衣装のアセットの 3 本で、メッシュごとの形の名前と、表情ごとの割り当て（メッシュ・形の名前・重み）が
  全部同じ。元の VRM と 2 作目の衣装は形も割り当ても無い（作った道具の初期値の表情は、.blend では形の名前だけでメッシュの無い割り当てで、VRM には書き出されない）。
- 作りの一覧（2026-11-17。実測）: 3 作目の VRM のダンスの VRM 8 本（キャラを作った道具が書き出した元の VRM・確認用の書き出し 5 本・アセット 2 本）と
  2 作目の衣装のアセットで、リポジトリ外の 2 本の台本（VRM の作りを読む台本・骨の重みを読む台本）と、メッシュと primitive・骨の数と名前・骨の拘束の数・
  揺れものの鎖ごとの値・当たり判定・視線・材質の値・画像の大きさ・メッシュごとの骨の重み（骨ごとの頂点の数まで）が全部同じ。
  人型の骨の位置だけは違う: 台本は親の平行移動だけを足していた（回転のある node があるので目安、と台本自身が書いていた）。model3d は回転と拡大を入れて
  求め、Blender 5.2.4 の glTF の読み込みで読んだ骨の付け根の位置と 6.4×10⁻⁷ m の中で同じ（アセット。人型の骨 54）。
  元の VRM（関節 131・骨の拘束 0）と、腕のねじれの骨を足したあとの VRM（関節 137・骨の拘束 6: 前腕の roll 2・aim 4）の違いも一覧で読める。
- 骨の拘束（2026-11-18。実測）: 3 作目の VRM のダンスの、腕のねじれの骨を足したモデル（`model.twist_bones`）から書き出すと、
  `constraints_exported` 6（上腕と前腕の左右の aim 4・前腕の左右の roll 2）・`constraints_not_exported` 0。roll を焼いて拘束を止めた踊りの .blend
  （`motion.bake_roll`）からは、書き出した拘束 4（aim）・書き出されなかった拘束 2（止めた roll）。骨の拘束を並べる前と、書き出した VRM のバイト列は
  同じ（SHA-256 が同じ）。roll の骨の拘束の先頭に、書き出されない形の Copy Rotation（混ぜ方 REPLACE・相手と軸は同じ）を足した写しでも、VRM Add-on は
  roll を書き出し、`constraints_not_exported` は足した 1 つだけ（`constraints_unmatched` は 0）。CI では、VRM の拘束の形の見分け方と、突き合わせ
  （先にある書き出されない形・骨 1 本に 2 つ目の拘束・ミュート・VRM の拘束でない種類・重みや相手の違う拘束）を確かめた。

**限界**: VRM 0.x は読まない（`meta_from` も VRM 1.0 だけ）。ほかのアプリ（VRChat など）で読めるかは確かめない。

## 衣装を布で動かす

脚に当たる長い衣装（スカートの裾など）を、布の計算で動かす道筋。リポジトリ外の 2 作目の VRM のダンスで、いちばん時間を使った所を
工程と検査にしている。揺れものの骨数本では、骨の間の布が脚を通り抜ける（[揺れものを焼く](#揺れものを焼くvrmbake_springs)の知見）。

1. `vrm.bake_springs` の `no_spring` で、裾の揺れものを外す（裾の骨は動かさない）。
2. `cloth.skirt_proxy`: 裾を身代わりの筒の布で動かす（下）。
3. `cloth.smooth`: 布の動きを腰から見た座標で時間方向にならし、頂点の動きのファイル（.pc2）に置き換える（ちらつきを消す）。
4. 確かめる: `check.jitter`（ちらつき）と `render.part_colors`（脚が布を通り抜けていないかを絵で）。足を大きく開く場面は設定に
   敏感なので、焼き直したら必ず絵で見る。

### 裾を身代わりの筒の布で動かす（`cloth.skirt_proxy`）

細かいひだのある裾に直接布の計算をかけると、体全体を相手にして 330 コマで 45 分たっても終わらず、相手を軽くするとひだが潰れて
破れた紙のようになった（2 作目の VRM のダンス）。`cloth.skirt_proxy` は、裾の内側の面をなぞった目の粗い筒（身代わり）だけを布として
計算し、細かい裾は Surface Deform で筒に貼り付けて、ひだの形のまま付いて行かせる。

- 入力 `blend`: 動きを付けた .blend（裾の骨は動かさない。VRM のキャラなら `vrm.bake_springs` の `no_spring` で裾の揺れものを外す）。
  1 Blender 単位 = 1 m のシーン（VRM を読み込んだ .blend はそう。違えば止める）。
- 名前（必須）: `skirt`（細かい裾のメッシュ）・`body`（体のメッシュ）・`hips_bone`（筒を付ける腰の骨）・`leg_groups`（脚の頂点グループの
  名前の頭の並び。例 `["upper_leg", "lower_leg"]`）。`armature`（省くと最初のアーマチュア）。
- やること（どれも初期姿勢で測ってから。元のファイルは変えない）:
  1. `hem_cut_z` > 0 なら、裾を床からその高さで水平に切り落とす（丈を詰める）。
  2. 腰の軸（裾の上 12 cm の頂点の平均の位置を通る縦の線）から放射状に、裾の内側の面までの距離を測る（高さ `rows` × 向き `columns`
     の格子）。切れ目（裾が当たらない向き）には面を張らない。`close_gap` 以下のすき間は埋め、`min_strip` 以下の細い帯は消し、周りの
     百分位（`inner_percentile`）と平均でならす。
  3. 体から、脚の頂点グループの頂点を `collider_min_leg_vertices` 以上持つ、`collider_min_island` 頂点以上の島（脚・骨盤）だけを取り出し、
     `collider_cut_below` m より下（足首・靴）を除き、面を `collider_ratio` に減らして当たりの相手（`skirt_collider`）にする。小さな島
     （腰の飾りや花）は入らない。写しの形状キー（表情など）は外す。ほかの物の当たり判定は外す。
  4. 細かい裾の頂点のうち、脚に近すぎるもの（脚＋`clearance`。腰に固定する段は `clearance_top`）を外へ出す。
  5. `extend_slit` なら、前の切れ目を、腰に固定する段（`pin_rows_front`）の下まで伸ばす（筒と細かい裾の両方に切り込み）。
     `extra_cuts_deg` の向き（度。0 が +X、90 が +Y）にも、帯の下から裾まで切り込みを足せる。
  6. 筒（`skirt_proxy`）を腰の骨に付ける。上から `pin_rows` 段（前側 `front_zone_deg` は `pin_rows_front` 段）は固定、その下は
     `goal_below_pin` から `goal_hem` へ弱まる「元の形へ戻るばね」。細かい裾は筒に貼り付け（骨の変形は外す）、床の高さで止め、
     脚に浅く食い込んだ所を `pushout_after` だけ外へ戻す。
  7. 初期姿勢から最初のコマの姿勢へ移る助走（`preroll` コマ）と、最初の姿勢で落ち着かせる `hold` コマを、骨の動きのキーとして前に
     足す（キーはアクションに残る）。床（`skirt_floor`）を置き（`floor`: `collide` は布の計算の相手、`clamp` は床より下へ行った分だけ
     止める、`none` は置かない）、布を計算して焼く（`end` までのコマ。0 ならシーンの終わり。`bake: false` で焼かずに形だけ）。
- 出力: `blend`（筒・当たりの写し・床・貼り付けた裾と、焼いた布の計算）、`report`（`model3d-skirt-proxy/0.1`。軸・格子・筒の頂点と面の
  数・段ごとの開いた格子・当たりの島と面の数・外へ出した頂点・丈の切り落とし・切れ目の向きと高さ・コマの範囲・焼いた時間）。
- アドオンは既定で有効にしない。VRM の設定は .blend に残る（下）。VRM Add-on の揺れものの計算の印（`enable_animation`）は切る
  （揺れものは `vrm.bake_springs` で焼いたキーで動かす。布を焼く間に計算が骨を動かさないように）。

既定の値は、2 作目の VRM のダンスで決めた値（なぜその値かは、リポジトリ外の台本の記録から）:

| 設定 | 既定 | 意味 |
|---|---|---|
| `columns`・`rows` | 60・26 | 筒の目（向き × 高さ）。段の間隔が 3〜4 cm になるように `rows` を選ぶ（丈を詰めたら減らす） |
| `close_gap`・`min_strip` | 3・3 | 埋めるすき間と消す細い帯の幅（目）。細い帯は布の計算でばたつき、しわのもとになった |
| `inner_percentile` | 25 | 筒の半径は、周りで測った値の低い方（内側寄り）。50 で中央値 |
| `clearance`・`clearance_top` | 0.011・0.003 m | 脚から離す量。腰に固定する段は小さく（腰の飾りを埋めないため） |
| `collider_min_island`・`collider_min_leg_vertices` | 150・5 | 当たりに残す島の大きさと、脚の頂点の数 |
| `collider_ratio`・`collider_cut_below` | 0.5・0.16 m | 当たりの面を減らす割合と、外す高さ（床と靴の間に布が挟まれないように） |
| `collider_outer`・`collider_inner`・`friction` | 0.005・0.006 m・2.0 | 当たりの厚み。内側を深く（0.03）すると、挟まれて入り込んだ布が中で捕まり、立ち上がっても団子のまま残った |
| `distance_min`・`quality`・`collision_quality` | 0.003 m・12・5 | 布の計算の当たりの距離と細かさ |
| `mass`・`air_damping`・`gravity` | 0.15・3.0・1.0 | 布の重さ・空気の抵抗・重力の効き |
| `tension`・`compression`・`shear`・`bending` | 20・20・8・8 | 布の硬さ（それぞれの `_damping` は 8・8・8・2） |
| `pin_rows`・`pin_rows_front`・`front_zone_deg` | 4・3・[185, 355] | 腰に固定する段と、前側（切れ目の側）の段と向き |
| `goal_below_pin`・`goal_hem`・`goal_curve`・`pin_stiffness` | 0.05・0・2・20 | 元の形へ戻るばね。強くすると上げた脚の中へ布を引き戻して脚とけんかし、しわになった。形は曲げにくさ（`bending`）で保つ |
| `smooth_iterations`・`smooth_factor` | 6・0.5 | 筒の小じわをならしてから細かい裾へ伝える |
| `bind_falloff` | 2.5 | 貼り付けの効き方 |
| `preroll`・`hold` | 40・14 | 助走と落ち着かせるコマ |
| `floor`・`floor_offset`・`floor_friction` | collide・0.006 m・2.0 | 床 |
| `self_collision` | false | 布どうしの当たり |
| `pushout_after` | 0.003 m | 最後に脚へ浅く食い込んだ頂点を外へ戻す（0 で無効） |
| `hem_cut_z` | 0 | 丈を詰める高さ（床から。0 で切らない） |
| `extend_slit`・`extra_cuts_deg` | true・[] | 切れ目を帯の下まで伸ばす・追加の切り込み |

**うまくいかなかった方法**（やり直さないための記録。2 作目の VRM のダンス）:
- 細かい裾に直接布の計算: 遅すぎ、相手を軽くするとひだが潰れた（上）。
- 揺れものの骨数本＋押し出し: 骨と骨の間の布が脚を横切り、左脚は布をまるごと通り抜けた（内側に入った頂点としても数えられない）。
- 筒に「元の形へ戻るばね」を強く: 上げた脚と布がけんかして、しわになった。
- 体の内側へ入った布を深く（3 cm）まで押し戻す設定や、床を計算から外して後から床の高さで止める方法: どちらも、足を大きく開く場面で
  腰のまわりにできる団子は直らなかった。原因は衣装の作り（切れ目が太ももの付け根から始まり、その上の輪になった布が股の下へ束ねられた）で、
  切れ目を帯の下まで伸ばして直った（`extend_slit`）。
- 左の腰にもう 1 本切り込み（`extra_cuts_deg`）: どの設定でも巻きつかなくなるが、前の布が脚の前に垂れたままで上げた脚が隠れ、
  元の絵の見え方から離れたので使っていない。
- しゃがみから足を開く場面は設定に敏感（床の摩擦 0.3・脚の摩擦 1.0・ならし 10 に変えたら団子が再発した）。動きやコマ数や設定を変えて
  焼き直したら、必ずその場面を絵（[色分けの診断の絵](#色分けの診断の絵renderpart_colors)）で確かめる。

**確かめたこと**:
- CI（Blender なし）では、筒の形の計算（格子の埋め方・輪の区間・すき間と細い帯・切れ目の列・ならし・脚からの離し方・外へ出す頂点・
  筒の面（切る格子と縁の三角）・固定の重み・当たりの島の選び方・助走の始まりの値）と、工程の受け渡しと悪い設定を止めること（代役の Blender）
  を確かめた。
- 2026-11-16 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（裾の身代わりの布の台本）と比べた（実測）。
  入力は、台本の流れの 1 段目（動きを付けて揺れものを焼いた .blend）。設定は丈 0.60 m・13 段（ほかは既定）。
  - 報告の値（軸・格子・筒の頂点 690 と面 1,244・段ごとの開いた格子・当たりの島 7 と面 3,130 → 1,564・外へ出した頂点 174・
    丈 4,598 → 2,103 頂点・切れ目の向き 237° と高さ・コマ −53〜720・助走のキー）が全部同じ。
  - `check.blend_match` で、物体 14・メッシュの頂点と面と uv と属性・骨、7 コマ（−53・−13・1・300・613・690・720）の変形した形が
    台本の .blend と全部同じ。
  - 焼いた布を台本のならしの台本にかけると、布の動きのファイル 2 つが、採用した踊りのものと SHA-256 まで同じ（筒の全 774 コマの動きと、
    それに付いて行く細かい裾が同じ）。
  - VRM Add-on を有効にしなくても同じ結果で、保存した .blend の VRM の設定（人型の骨の割り当て・揺れもの・当たり判定）も残った。
  - 774 コマの布の計算を含めて約 40 秒。

### 布の動きをならして頂点の動きのファイルに置き換える（`cloth.smooth`）

布の計算は、当たり判定のあたりで細かく震え、細かいひだがそれを増幅して、動画でちらつく（2 作目の VRM のダンスで、ならす前の裾の
ずれは激しい場面で平均 14 mm、大きい所で 20 cm）。動きの元のコマで測っても同じなので、コマの間の補間のせいではない。`cloth.smooth` は、
布の筒と細かい裾の動きを、腰の骨から見た座標で時間方向にならし、頂点の動きのファイル（.pc2）に置き換える。

- 入力 `blend`: `cloth.skirt_proxy` で布を焼いた .blend。
- パラメーター: `proxy`（布を焼いた筒。既定 `skirt_proxy`）、`detail`（細かい裾。`detail_sigma` > 0 なら必須）、`bone`（既定 `Hips`）と
  `bone_prefix`（既定 `mixamorig:`）、`sigma`（筒のならしの強さ。コマ数。既定 1.2）、`detail_sigma`（細かい裾の 2 回目のならし。既定 0.8。
  0 で細かい裾はそのまま）、`median`（ならす前に 1 コマだけの飛びを消す中央値の窓。奇数のコマ数。既定 3。1 で消さない）、`armature`。
- やること:
  1. 筒の頂点を、布の計算のコマの範囲（焼いた範囲の最初〜最後）の全コマぶん読む（ほかのメッシュは評価しない）。
  2. 骨から見た座標に直し、1 コマだけの飛びを前後 `median` コマの中央値で消してから、σ `sigma` コマのガウス（±⌈3σ⌉ コマ）で
     ならす。端は端のコマの値で延ばす。骨に付いて動く所は動かないので、そのまま残る。
  3. 世界の座標に戻して PC2 に書き、筒の変形（骨・布の計算・ならし）を外して「このファイルを読む」（Mesh Cache）だけにする。
  4. 細かい裾も、ならした筒に付いて行く動きを読み（貼り付けと押し戻しの相手だけを残して評価する）、σ `detail_sigma` で同じように
     ならして、ファイルに置き換える。筒だけならしても、細かい裾は筒より大きく揺れたままだった（貼り付けが筒の小さな向きの変化を遠い
     ひだで増幅するのと、押し戻しの補正がコマごとに効いたり切れたりするため）。
- 出力: `blend`、`report`（`model3d-cloth-smooth/0.1`。筒と細かい裾の、骨から見たずれ（前後のコマの中間から。平均と 99 百分位）の
  ならす前後と、動かした量）、`proxy_cache`（筒の .pc2）、`cache`（細かい裾の .pc2。`detail_sigma` > 0 のとき）。
- 骨から見た座標は、骨の世界の行列をそのまま逆にして求める（リポジトリ外の台本と同じ）。骨やアーマチュアに大きさ（scale）があると、
  報告のずれの数はその逆数の倍になる（報告の `bone_scale` に大きさの範囲を出す。ならした形そのものは、大きさがコマで一定なら変わらない）。
  ずれを世界の長さで測り直すときは `check.jitter` の `space: rigid` を使う。
- .pc2 は .blend と同じフォルダに置き（outputs の名前で決まる。違うフォルダなら止める）、.blend からの相対パス `//<名前>.pc2` で読む。
  .blend を動かすときは .pc2 も一緒に動かす。
- 変形で頂点の数が変わる物（Subdivision・Mirror・Solidify などのモディファイアがある）は止める（.pc2 は元のメッシュの頂点の並びで
  読むため。先に当てるか外す）。物体の中の座標へは、物体が動かなければ 1 つの行列で（台本と同じ計算）、物体そのものが動く
  （動きのある親に付いているなど）ときはコマごとの物体の行列で直す（報告の `object_moves`）。
- 読む間は .pc2 を絶対パスで読み、保存の前に相対パスへ戻す。リポジトリ外の台本は相対パスのまま読んでいたので、入力と出力の .blend が
  同じフォルダのときだけ正しく動いた（model3d では工程ごとにフォルダが分かれる）。入力の .blend が別のドライブにあるときは、
  保存してから相対パスに直して保存し直す。
- ならした後の .blend は、裾の動きを .pc2 から読むだけになる。`look.toon` のセル調の塗りは通るが、輪郭線（`outline.meshes`）に
  裾を入れると止まる（Mesh Cache も元の頂点の並びに結び付いているため。布を焼いた後・ならす前の Surface Deform も同じ）。
  裾の輪郭線が要るときは、輪郭線から裾を外す（Blender 5.2.4 で確かめた）。

**確かめたこと**:
- CI（Blender なし）では、ならし方（骨に付いて動く所はそのまま・1 コマだけの飛びは中央値で消える・端は端の値で延ばす・ガウスの幅）、
  PC2 の形（見出しと往復）、.blend からの相対パス、工程の受け渡しと悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-16 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（裾の動きをならす台本）と比べた（実測）。
  - 台本の流れの 2 段目（布を焼いた .blend）にかけると、.pc2 の 2 つ（筒 690 頂点・細かい裾 2,137 頂点、どちらも 774 コマ）が、採用した
    踊りが読むものと SHA-256 まで同じ。報告のならす前後のずれ（筒 5.42 → 0.84 mm、細かい裾 1.99 → 1.12 mm。平均）と動かした量も同じ。
    保存した .blend は、自分のフォルダの .pc2 を相対パスで読み、7 コマの変形した形が台本の .blend と全部同じ（`check.blend_match`）。
  - モデル（動きを付ける前の VRM のキャラの .blend）から、model3d の工程だけで `vrm.apply_motion` → `vrm.bake_springs`（`no_spring`:
    裾）→ `cloth.skirt_proxy` → `cloth.smooth` と回すと、.pc2 の 2 つが採用した踊りのものと SHA-256 まで同じになった（台本の流れの
    1 段目にある帯と袖の押し出しの補正は持ち帰っていないが、裾の布には効かない）。4 工程で約 76 秒（布の計算 49 秒）。
  - 1 回 約 6 秒（774 コマ）。
  - 合成の場面で、細かい裾に Subdivision を足すと止まること、物体そのものを 20 コマで 1 m 動かした布の板をならすと、置き換えた後の
    形の違いがならした分（最大 8.6 mm）だけで、物体の動きの分ずれないことを確かめた。

### ちらつきを測る（`check.jitter`）

布の計算で動かした衣装は、当たり判定のあたりで細かく震え、細かいひだがそれを増幅して、動画で「ちらつき」に見える（2 作目の VRM の
ダンスの激しい場面で、ならす前の裾の頂点のずれが平均 14 mm、大きい所で 20 cm あった）。`check.continuity` は点の列の 1 コマの移動
（跳び）だけを見るので、体の速い動きと区別できない。`check.jitter` は、頂点が前後のコマの中間からどれだけずれるか（2 階差分の半分）を、
骨（腰など）から見た座標で測る。骨に付いて動く所は骨から見ると動かないので、体の動きそのものはずれにならず、布の震えや 1 コマだけの
跳びが残る。

- 入力: `blend`（必須）、`track`（任意。`model3d-stage-track/0.1` の T と f。小数のコマは前後のコマの間の時刻。`plan.retime` の
  並びを渡せば、書き出すコマで測れる）。
- パラメーター:
  - `objects`（必須）: 測るメッシュの名前の並び。
  - `bone`（既定 `Hips`）と `bone_prefix`（既定 `mixamorig:`）: この骨から見た座標で測る。アーマチュアは `armature`（省くと最初のアーマチュア）。
  - `frames`: [最初のコマ, 最後のコマ]（整数）。`track` と一緒には使えない。どちらも無ければシーンの全コマ。3 コマ以上が要る。
  - `jump_mm`（既定 4）: ずれがこれを超えたものを「飛び」と数える。
  - `toggle`（既定 false）: 物体ごとに、変形（`toggle_types`。既定は Shrinkwrap・Surface Deform・Cloth・Smooth のうち、表示の印の
    付いたもの）を 1 つずつ切って測り直す。どの変形がちらつきを作るかを見分ける。.blend は保存しない。
  - `top`（既定 8）: ずれの大きいコマと頂点を何個並べるか。
  - `space`（既定 `bone`）: 骨から見た座標の取り方。`bone` は骨の世界の行列をそのまま逆にする（リポジトリ外の台本と同じ）。
    アーマチュアや骨に大きさ（scale）があると、ずれはその逆数の倍で測られる（2 倍のアーマチュアでは 12 mm の跳びが 6 mm）。
    `rigid` は回転と位置だけを逆にして、世界の長さで測る（回転は行列にいちばん近い回転。鏡写しは向きを反対にしてから）。
    報告の `bone_scale` に、測ったコマの骨の行列の大きさの範囲を出す。`bone` で 1 から 0.001 より離れていれば `warnings` に書き、
    工程を要確認にする（`strict` なら止める）。
  - しきい値（渡したときだけ合否を出す）: `max_mean_mm`・`max_p99_mm`・`max_peak_mm`・`max_over_percent`。メッシュごとに、変形を
    切らないそのままの値と比べる（`toggle` の測り直しは見分けるためのもので、合否に入れない）。
- 出力 `report`（`model3d-jitter/0.1`）: メッシュごとに、頂点の数、ずれの平均・99 百分位・最大（mm。丸めない）、`jump_mm` を超えた
  （コマ, 頂点）の割合（%）、ずれの大きいコマ（T・f・そのコマの最大と平均）と頂点の番号。`toggle` なら、切った変形ごとに同じ値。
- 長さは m（シーンの `scale_length` で直す）で測り、mm で書く。全部のコマの位置を持つので、頂点 × コマが多いとメモリを使う。
- アドオンは既定で有効にしない（VRM Add-on の揺れものを焼いた .blend は、アドオンなしでも同じ値になった。下）。

**確かめたこと**:
- CI（Blender なし）では、ずれの式と要約、骨から見た座標の読み方（代役の bpy。骨に付いて回る頂点はずれ 0、1 コマだけの跳びは
  跳んだ大きさ、世界の座標では骨の動きがずれになる）、工程の受け渡しと合否（代役の Blender）、悪い設定を止めることを確かめた。
- 2026-11-16 に、Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（ずれを測る台本）と比べた（実測）。
  同じ .blend・同じ 38 コマ（30 コマ/秒で書き出す 654〜691 コマを、24 コマ/秒の動きへ写した小数のコマ）・同じ 2 つのメッシュ（裾と飾り帯）で、
  - 採用した踊り（裾は時間方向にならした後）: 2 つの条件（そのまま、飾り帯の押し出しを切る）のどれも、平均・99 百分位・割合・最大が
    丸める前の値まで同じ。裾は平均 1.37 mm・最大 22.1 mm、飾り帯は平均 1.08 mm・最大 22.2 mm。
  - ならす前の布（同じ台本の流れで作り直したもの）: 5 つの条件（そのまま、裾の貼り付け・床で止める補正・押し出し、飾り帯の押し出しを
    それぞれ切る）のどれも、丸める前の値まで同じ。裾は平均 14.02 mm・99 百分位 94.36 mm・最大 200.2 mm。
  - 台本は VRM Add-on を有効にして測り、`check.jitter` は有効にしないで測った。38 コマ・2 つのメッシュ（2,137 と 739 頂点）で、
    変形を切る測り直しを含めて 0.5〜2 秒。
  - `space: rigid` でも、7 条件の丸めた値は台本と同じ（丸める前の値の差は、長さで最大 4×10⁻⁵ mm、割合で 0.0013 %）。このキャラの
    腰の骨の行列の大きさは 1 ± 1.2×10⁻⁷ だった。
  - 踊り全体（動きの 720 コマ）では、裾のずれは、ならす前が平均 8.72 mm・最大 272.7 mm、ならした後が平均 1.14 mm・最大 44.0 mm
    （1 回 約 8 秒）。

### 色分けの診断の絵（`render.part_colors`）

布が脚を通り抜けていないかは、数では拾いきれない。`check.penetration`（とリポジトリ外の試作の「体の面の裏側へ入った頂点を数える」
台本）は、相手の中に入った頂点を数えるので、脚がまるごと布を通り抜ける失敗（布の頂点はどれも脚の外にあり、脚の面が布の面を突き抜ける）
を拾えない。2 作目の VRM のダンスでは、揺れものの骨の間の布を左脚がまるごと通り抜けていたのを、色分けの絵で見つけた（数は減っていた）。
`render.part_colors` は、部位を色で分けた簡易描画（Workbench）の絵を描く。何が何を通り抜けているかを目で見分けるためのもの。

- 入力 `blend`。パラメーター:
  - `frames`（必須）: 描く動きのコマの並び（小数は前後のコマの間の時刻）。
  - `bone_meshes` と `bone_colors`: 挙げたメッシュ（体など）は、頂点ごとに、いちばん重みの大きい頂点グループの名前で色を分ける。
    `bone_colors` は `[名前の頭, [r, g, b]]` の並びで、上から見て最初に名前の頭が合ったものの色（どれにも合わなければ `bone_default`、
    既定 [0.6, 0.6, 0.6]）。`bone_meshes` を挙げるときは `bone_colors` も要る。
  - `object_colors`: ほかのメッシュの物体ごとの色（`{名前: [r, g, b]}`。無ければ `default_color`、既定 [0.95, 0.95, 0.95]）。シーンに無い
    名前は報告の `unused_object_colors` に残すだけ（色の表を使い回せる）。
  - `show`: 描画から隠してある物も描く（布の身代わりの筒・当たりの写しなど）。`hide`: 描かない物。ほかの、描画から隠してある物は描かない。
    `bone_meshes`・`show`・`hide` にシーンに無い名前があれば止める。
  - 色はシーンの線形の値（0 以上）。
  - `camera`: 狙う点 `target`（[x, y] m。既定 [0, 0]）の真上の高さ `center`（既定 0.75 m）から水平に、方位 `yaw`（度。既定 0 で -Y 側の
    正面、90 で +X 側）・レンズ `lens`（既定 85 mm、受光は縦に 36 mm）で、縦 `height`（既定 1.5 m）が画面の高さに入る距離に置く
    （横長の画面でも縦で合わせる）。
  - `resolution`（既定 [720, 960]）・`format`（`jpeg`（既定。品質 `quality` 90）か `png`）・`antialiasing`（既定 "8"）・
    `cavity`（くぼみの強調。既定 true）・`world`（背景の色。既定 [0.2, 0.2, 0.22]）。
  - `armature`: 描くシーンを決めるアーマチュア（省くと最初のアーマチュアのシーン。アーマチュアが無ければ今のシーン）。
- 描き方は、頂点の色・スタジオの光・裏の面も描く・色の変換は Standard。リポジトリ外の台本が .blend の値のまま使っていた設定（スタジオの光の
  種類、影と輪郭なし、画面のくぼみ）も、Blender の既定の値で入れ直す（.blend の表示の設定に左右されない）。画像に日時などの控えを書かない。
- 出力: `frames`（`model3d-frames/0.1`。工程のフォルダの `frames/f<T>.jpg`。T は並びの 1 から、f は動きのコマ。直す前後の 2 組を
  `check.frames` で比べられる）、`report`（`model3d-part-colors/0.1`。物体ごとの色の決め方、骨で色を分けたメッシュの色ごとの頂点の数、
  描いた物・隠した物、カメラ）。
- .blend は変えない（色の属性・カメラ・背景は、描くための読み込みの中だけ）。
- 色の例（2 作目の VRM のダンス）: 体は、腰 `hips` 赤・左の太もも `upper_leg.L` 明るい緑・右の太もも `upper_leg.R` 暗い緑・すね
  `lower_leg` 青・足 `foot`・`toes` 紺、そのほか（胴と腕）は灰。裾は黄、飾り帯は水色、`show` で見せた布の筒は橙、当たりの写しは紫。
  頂点グループの名前はキャラによって違うので、キャラごとに書く。

**確かめたこと**:
- CI（Blender なし）では、色の決め方（最初に名前の頭が合ったもの）、カメラの置き方（縦の高さが画面に入る距離・方位・狙う点・
  1 Blender 単位 = 1 cm のシーン）、工程の受け渡しと描いた絵の一覧（代役の Blender。一覧は `check.frames` で読める）、悪い設定を止めることを
  確かめた。
- 2026-11-16 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（色分けの絵の台本）と、同じ .blend・同じ 5 コマ・
  同じ色とカメラで、4 通り（採用した踊りの正面・横・筒と当たりの写しを見せて裾を隠したもの、ならす前の布の正面）を描いて比べた（実測）。
  - 3 回描いて、20 コマ中 14・17・15 コマが台本の絵と画素まで同じ。違ったコマは、どれも 8×8 の塊か小さな範囲だけ（20〜573 画素、
    最大 24/255。全体のずれではない）。
  - 台本どうしでも、2 回描くと 20 コマ中 3 コマが 20〜157 画素（最大 8/255）違った（簡易描画の描くたびの揺れ。[要素別に描く](#要素別に描く)と同じ）。
  - 1 通り 5 コマを約 1 秒で描いた。

## モデルを手直しする

画像から作ったモデル（設定画から 3D にしたキャラ）には、作り方の癖がある。リポジトリ外の 2 作目の VRM のダンスで、モデルを
直した台本を工程にしたもの。どれも**部位ごとに測ってから直し、直した後も同じ数で確かめる**。

- 体が細い（ウエスト幅 16 cm など）。部位ごとに測ってから（`check.body_size`）、骨の軸のまわりに広げる（`model.body_shape`）。
  装身具（腕輪など）も腕と同じ倍率で広げると、装身具のほうが腕より太いままになる。
- 肌は装身具の下で別の筒に分かれていて、装身具が継ぎ目を隠していることがある。装身具だけ動かすと継ぎ目が見える。
- 肩から上腕に、ねじれを受け持つ骨が無いことがある。踊りでは上腕が軸のまわりに 90° を超えてねじれるコマが数百あり（`check.twist`）、
  上腕のメッシュが付け根から一気にねじれて、絞ったように見えた（`model.twist_bones`）。前腕も、手が軸のまわりに回ると手首のまわりが絞られる
  （3 作目の VRM のダンス。前腕の骨を足し、手の回転の一部を写した。`model.twist_bones` の `roll`）。

どの工程も、部位を**島（辺でつながった頂点の集まり）と頂点グループ（いちばん重い骨）で選ぶ**。体と服が 1 つのメッシュのモデルでは、
島で部位を分けられないので使えない（[限界](#限界未検証)）。頂点グループは、アーマチュアの変形に使う骨（`use_deform`）の名前のものだけを
骨の重みとして読む（選択や押し出しのためのグループ、変形に使わない骨のグループは混ぜない。混ぜると頼んだ広げ方が薄まり、いちばん重い骨も
取り違える。読まなかったグループは報告の `ignored_groups`）。値（倍率・高さ・骨の名前）はキャラごとに違うので、レシピに書く
（既定の値は何も変えない値か、台本の決まった値）。1 Blender 単位 = 1 m のシーンだけを扱う。
.blend は工程のフォルダへ別名で保存し、中の相対パス（`cloth.smooth` の .pc2 など）は保存先から見た相対パスに直る（Blender の既定。
5.2.4 で確かめた）。保存した .blend だけを別のフォルダへ動かすと、相対パスの先が読めなくなる。

### 体つきを部位ごとに広げる（`model.body_shape`）

- 入力 `blend`（キャラの .blend）。初期姿勢（骨の rest）で測って直す。元のファイルは変えず、別名で保存する。骨と色の絵は変えない。
- `body`（必須）: 体のメッシュ。頂点ごとに、頂点グループの重みで次の広げ方を混ぜる（重みの足りない頂点は動かさない）。
  - `limbs`: `[{"bones": [骨, …], "profile": [[位置, 倍率], …]}, …]`。その骨の頂点グループの所は、骨の軸からの距離を、骨に沿った位置
    （0 = 付け根、1 = 先）ごとの倍率で広げる（表は直線で補間、表の外は端の値）。
  - `torso`: `{"bones": [骨, …], "width": [[高さ, 倍率], …], "depth": [[高さ, 倍率], …]}`。その骨の所は、高さ（床から m）ごとの中心線
    から、左右の幅（x = 0 のまわり）と前後の厚みを別々の倍率で広げる（片方だけでもよい）。中心線は、`bones` がいちばん重い頂点の、
    高さ `levels`（`[最初, 終わり（含まない）, きざみ]`。省くとその頂点の高さの範囲を `step`（既定 0.02 m）おき）ごとの帯
    （±`band`、既定 0.015 m。|x| < `half_width`、既定 0.25 m）の前と後ろの中央で、帯に `min_points`（既定 7）に満たない高さは
    ほかの高さから補い、[0.25, 0.5, 0.25] でならす。胴の幅は体が x = 0 を中心に置かれている前提（VRM はそう）。
  - それ以外の骨（頭・首・手・指・足など）の所は動かさない。重みで混ぜるので、境目はなだらかにつながる。
- `bands`: `{"bones": [骨, …], "factor": 倍率}`。`limbs` の骨 1 本だけにほぼ付いた小さな島（`max_verts`（既定 600）頂点以下で、
  その骨がいちばん重い頂点が `share`（既定 0.95）を超える島。腕輪など）は、島ごとに 1 つの倍率 `factor`（既定 1 = 広げない）にする
  （模様が歪まないように。腕と同じ倍率で広げると、腕輪のほうが腕より太いままになる）。
- `band_shift`: `{"pairs": [[腕輪の骨, その先の骨], …], "shift": 割合}`。腕輪の島を、骨に沿って `shift`（骨の長さに対する割合）だけ
  先へ動かす。腕の肌が腕輪の下で 2 つの筒（付け根側と先側）に分かれていれば、継ぎ目も腕輪の下へ動かす: 付け根側の筒の
  `stretch_from`（既定 0.20）より先を伸ばして端を「腕輪の付け根側の端 + `seam_inside`（既定 0.07）」に、先側の筒の `keep_from`
  （既定 0.88）より付け根側を縮めて端をその `seam_gap`（既定 0.02）先にする（割合はどれも骨の長さに対する値）。動かすのは、2 本の骨の
  重みの和が `weight_from`（既定 0.3）から `weight_span`（既定 0.5）で 0 → 1 になる頂点（胴の肌は動かさない）。肌が 1 つの筒なら
  腕輪だけを動かす。1 本の骨に腕輪の島が 2 つ以上あるとき、`pairs` の骨の頂点グループが体に無いとき（打ち間違いで重みが 0 のままに
  ならないように）は止める。腕輪を動かしてから太さを変える。
- `follow_torso`: 胴と同じ広げ方をする物体（裾・帯など。頂点グループに関係なく全部の頂点）。`torso` の `width` か `depth` が要る。
- 形状キーのあるメッシュは、どのキーにも同じ動き（基本の形で求めた移動量）を足す（台本のモデルには形状キーが無かった）。
- `armature`（省くと最初のアーマチュア）。そのアーマチュアのシーンを今のシーンにする。
- 出力: `blend`、`report`（`model3d-body-shape/0.1`。物体ごとの頂点の数と動いた量（mm、平均と最大）、見つけた腕輪の島、腕輪と継ぎ目の
  動かす前後の位置（骨に沿った割合）、胴の中心線、`limbs` と `torso` に挙げたのに体に頂点グループの無い骨（`groups_not_on_body`。
  キャラをまたいで同じ表を使えるよう、止めずに残す）、読まなかった頂点グループ（`ignored_groups`）、設定）。

`check.body_size` は体の太さを初期姿勢で測る（.blend は変えない。頂点の基本の形を読むので、骨の姿勢に左右されない）。
- `body`（必須）、`limbs`: `[{"bone": 骨, "at": [位置, …], "groups": [頂点グループ, …]}, …]`。骨に沿った位置 `at`（0〜1）の ±`window`
  （既定 0.06）の帯にある、`groups`（既定はその骨）がいちばん重い頂点を、骨の軸に垂直な面へ写し、広がりの大きい 2 つの向き（主成分）の
  幅を直径（cm）として測る。
- `torso`: `{"groups": [頂点グループ, …], "heights": [高さ, …]}`。`groups` がいちばん重い頂点の、高さごとの帯（±`band`、既定 0.012 m。
  |x| < `half_width`、既定 0.25 m）の左右の幅と前後の厚み（cm）。
- 頂点が `min_points`（腕・脚は既定 6、胴は既定 7）に満たない所は null。腕・脚の `min_points` は 3 以上（直径を 2 つの向きで測るので、
  1〜2 頂点では決まらない）。出力 `report`（`model3d-body-size/0.1`）に背の高さ（体のメッシュのいちばん高い頂点、m）と、
  読まなかった頂点グループ（`ignored_groups`）も載せる。測るだけで合否は出さない（`report_only: true` を付けると要確認にしない）。

**確かめたこと**:
- CI（Blender なし）では、骨の軸のまわりの広がり（骨に沿った位置は変わらない）、胴の幅と厚みと中心線、腕輪の島の見つけ方、腕輪と継ぎ目の
  動かし方（肌が 1 つの筒のとき・腕輪の島が 2 つのとき）、重みで混ぜること、形状キーへの書き込み（代役の物体）、直径と胴の幅の測り方、
  工程の受け渡しと悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（体を広げる台本と測る台本）と比べた（実測）。
  - 台本の決まった値（腕・脚の 4 つの表、胴の幅と厚みの表、腕輪の倍率と動かす量。値はリポジトリ外のレシピ）を同じモデルにかけると、
    採用したモデル（台本が作ったもの）と `check.blend_match` で 11 物体の頂点・面・uv・属性・骨が全部同じ（`exact`）。台本をかけ直した
    ものとも同じ。見つけた腕輪の島（左右 352 頂点ずつ）と、腕輪と継ぎ目の動かす前後の位置、動いた量も台本の報告と同じ。1 回 約 3 秒。
  - はじめは左腕の 10 頂点が 1.5×10⁻⁸ m 違った。骨の付け根と先は Blender から単精度で読まれ、台本はそのまま引いていたのに、倍精度に
    直してから引いていたため。読んだ型のまま引くようにして同じになった。
  - 測る値（背の高さ、腕・脚の 8 か所の直径、胴の 7 つの高さの幅と厚み）は、直す前と直した後のモデルで、台本の値（0.1 cm に丸めたもの）と
    全部同じ。

### ねじれを分ける補助の骨（`model.twist_bones`）

骨へ移した動きで上腕が自分の軸のまわりに大きくねじれると、ねじれを受け持つ骨の無いモデルでは、上腕のメッシュ全体が付け根から一気に
ねじれて絞ったように見える。`model.twist_bones` は、踊りの動きは変えずに、ねじれを付け根から先へ徐々に分ける（前腕の動きのキーを
打ち直す `motion.twist_split` とは別で、骨と重みを足す）。

- 入力 `blend`（動きを付けた .blend か、モデルの .blend）。元のファイルは変えず、別名で保存する。
- `bones`（必須）: `[{"bone": ねじれる骨, "aim": 向く骨, "name": 足す骨, "roll": {…}}, …]`（`name` を省くと `<bone>_swing`。`roll` は任意）。1 つごとに、
  1. `bone` と同じ位置（付け根・先・ロール）に骨 `name` を足す（親は `bone` の親、変形に使う、つながりなし）。拘束 Damped Track
     （軸 Y）で `aim` の付け根を向くだけにする。いちばん小さい回転で向くので、軸のまわりには回らない（ねじらない骨）。
  2. `roll`（`{"name": 足す骨, "from": 回転を写す骨, "start": 割合, "euler_order": 順}`）があれば、`bone` の付け根から `start`（既定 0.5）の所から
     先までに、骨 `roll.name` を足す（親は `bone`、ロールは `bone` と同じ、変形に使う、つながりなし）。拘束 Copy Rotation（Y だけ・Add・ローカルどうし、
     角度の分け方の順 `euler_order`。既定 ZYX）で、`roll.from` の骨（前腕なら手）の軸のまわりの回転を足す。骨は `bones` の順に、足した骨・`roll` の骨の順で足す。
  3. メッシュ（`meshes`。省くと、このアーマチュアで動く全部のメッシュ。アーマチュアのモディファイアの相手か、アーマチュアを親にした変形。
     挙げたメッシュがこのアーマチュアで動かなければ止める。同じ骨の名前のキャラが 2 体ある .blend で、ほかのキャラの重みを触らない）の `bone` の頂点グループの重みを、初期姿勢の骨に沿った位置 t（0 = 付け根、1 = 先。
     範囲の外は端）で分ける。`roll` が無ければ、元の骨に t、足した骨に 1 − t（付け根に近いほど、ねじらない骨で動く）。
     `roll` があれば、t が `start` までは足した骨から元の骨へ、`start` から先は元の骨から `roll` の骨へ、折れ線で移す（`start` 0.5 なら、
     足した骨 max(0, 1 − 2t)・元の骨 1 − |2t − 1|・`roll` の骨 max(0, 2t − 1)。肘では上腕から見てねじれ 0、手首では手と同じねじれ）。
  4. `bone` だけにほぼ付いた小さな島（`rigid_max_verts`（既定 600）頂点以下で、`bone` がいちばん重い頂点が `rigid_share`（既定 0.95）を
     超える島。腕輪など）は、島の t の平均の 1 つの割合にする（島の中でねじれて模様が歪まないように）。いちばん重い骨は、変形に使う骨の
     頂点グループの中で比べる（選択などのためのグループは数えない）。
- `min_weight`（任意）: 分けた重みがこの値以下になる所は、その頂点グループ（元の骨・足した骨のどちらも）から外す（足した骨の名前の
  グループが前から残っていても、古い重みを残さない。省くと、0 の重みも頂点グループに書く。2 作目の台本と同じ）。3 作目の台本は 1e-5。
- `max_influences`（任意。1 以上の整数）: 重みを分けた頂点の骨（変形に使う骨の頂点グループで、重みが 0 より大きいもの）を、重みの大きい順にこの本数までにする。
  切った重みは、残した骨に同じ割合で割り振る（頂点の重みの合計は変えない。同じ重みは先のもの）。VRM は 1 頂点に骨 4 本までなので、VRM に書き出すモデルでは 4
  （Blender と VRM で動きが変わらないように）。報告の `influences` に、調べた頂点・いちばん多かった本数・切った頂点の数・切った重みの割合の最大。
- 骨 `name`（`roll.name` も）がもうあれば止める（2 回かけない）。`bone` の重みを持つメッシュが無ければ要確認。
- メッシュのデータをほかの物体と分け合う（リンク複製）なら、先にその物体だけのメッシュにしてから重みを分ける。重みはメッシュに付くので、
  分け合ったままだと、同じ重みを 2 回分けたり、選んでいない複製の重みまで変わったりするため（選んだ複製どうしなら、片方を分ければ
  もう片方は元のメッシュのまま）。分けた物体と分け合っていた物体を報告の `separated_from` に載せる。
- **VRM に書き出すとき**: 2 つの拘束の形（Damped Track の軸 Y・`head_tail` 0、Copy Rotation の軸 1 つ・Add・ローカルどうし。どちらも
  ミュートしない）は、VRM Add-on が VRM 1.0 の拘束（`VRMC_node_constraint` の aim と roll）として書き出す。3 作目の VRM のダンスでは、
  上腕と前腕の左右 6 本を VRM に入れ、ビューア（three-vrm）で読み込むと拘束 6 個が読まれ、揺れを試す動きで崩れなかった（制作の記録）。
  `VRMC_node_constraint` に対応しないアプリでは足した骨が動かず、腕の形が崩れうる（ほかのアプリでは確かめていない）。
- **Blender の Copy Rotation と VRM の roll は計算が違う**: Copy Rotation は回転を 3 つの角度（オイラー角）に分けた Y を使い、VRM の roll は
  回転を「曲がり × 軸のまわりのねじれ」に分けたねじれを使う。手を大きく曲げたコマで、3 作目では中央値 3°、いちばん大きいコマで 30〜55° ずれた
  （分け方の順を 6 通り試し、いちばん小さい ZYX でも 28° 残った）。動画（Blender で描く）では、ねじれの正確な値を骨のキーに焼いて
  拘束を止めると、VRM と同じ形になる（`motion.bake_roll`。下の節）。拘束を止めると VRM には書き出されないので、焼くのは踊りの .blend だけ（モデルは拘束のまま）。
- 出力: `blend`、`report`（`model3d-twist-bones/0.1`。足した骨（`kind` が `swing` か `roll`）、メッシュと骨ごとの重みを分けた頂点の数と、島ごとに分けた島の数、
  1 頂点の骨の数を切った数（`influences`）、重みを分けたメッシュ（`meshes`）とリンク複製から分けた物体（`separated_from`））。

`check.twist` は、骨の回転（初期姿勢からの骨自身の回転）を「向きを変える分（振り）」と「骨の軸 Y のまわりに回る分（ねじれ）」に分け、
ねじれの角（−180〜180°）をコマごとに測る（メッシュは評価しない）。
- `bones`（必須）: 測る骨の名前。`frames`（[最初, 最後]。省くとシーンの全コマ）、`over_deg`（既定 [60, 90]）・`top`（既定 8）。
- 出力 `report`（`model3d-twist/0.1`）: 骨ごとの最小・最大・絶対値の最大（度）、`over_deg` を超えたコマの数、ねじれの大きいコマ。
  コマごとの値は工程のフォルダの `twist_raw.json`。`max_twist_deg` を渡したときだけ合否を出す（ねじれの絶対値がどれかのコマで超えたら不合格）。

**確かめたこと**:
- CI（Blender なし）では、骨に沿った位置の切り方、小さな島の割合（島の平均・大きすぎる島は分けない）、ねじれの角の式と −180〜180° への戻し方、
  要約と合否、工程の受け渡し（足す骨の名前の既定、重みを持つメッシュが無いと要確認）と悪い設定を止めること（代役の Blender）を確かめた。
  `roll` の折れ線（`start` 0.5 で台本の式とビットまで同じ、ほかの `start` でも 3 つの和が 1）、1 頂点の骨の数の切り方（大きい順・同じ重みは先・
  合計を保つ）、`roll`・`min_weight`・`max_influences` の受け渡しと悪い設定（名前の重なり、`start` の範囲、角度の分け方の順）も確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（骨を足す台本と測る台本）と比べた（実測）。
  - 台本の流れの 4 段目（袖を外した踊りの .blend。凍結した台本で作り直したもの）に、左右の上腕をかけると、台本の結果と、骨 133 本（静止の
    行列・親・変形・つながり・拘束）と、12 のメッシュの頂点グループの重み（float32 のバイトと、入っている頂点）が全部同じ。
    `check.blend_match` でも、物体・頂点・uv・属性・骨と、7 コマの変形した形（12 メッシュ）が全部同じ（`exact`）。重みを分けた頂点は左 953・右 985、
    島ごとに分けた島（腕輪）は左右 1 つずつで、台本の報告と同じ。
  - ねじれの角は、その .blend と採用した踊りの .blend の両方で、上腕と前腕の 4 本の最小・最大（0.1° に丸めたもの）と 60°・90° を超えたコマの数が
    台本と全部同じ（720 コマ。上腕が 90° を超えるコマは左 192・右 90）。
- 2026-11-18 に Windows の Blender 5.2.4（VRM Add-on 4.10.5）で、3 作目の VRM のダンスのリポジトリ外の台本（前腕の骨も足す台本）と比べた（実測）。
  - 台本で骨を足す前のモデルに、左右の上腕（`aim` は前腕）と前腕（`aim` は手、`roll` は手から。`start`・`euler_order` は既定）をかけ、
    `min_weight` 1e-5・`max_influences` 4（台本の値）にすると、台本が作ったモデルと、骨 137 本（名前・並び・静止の行列の float32 のバイト・
    親・変形・つながり）・6 本の拘束（種類・相手・軸・混ぜ方・空間・角度の分け方の順）・8 メッシュの頂点グループ（グループの並び、頂点ごとの
    グループと重みの float32 のバイトを頂点の中の並びのまま）が全部同じ。`check.blend_match` でも `exact`。台本を同じモデルにかけ直したものとも同じ。
    重みを分けた頂点は左上腕 2855・左前腕 1542・右上腕 2779・右前腕 1534、骨の数を 4 本に切った頂点は 46（台本の記録と同じ）。
  - そのモデルを `vrm.export` で書き出した VRM は、台本のモデルを書き出した VRM とも、制作のとき（2026-11-16）に書き出した確認用の VRM とも
    SHA-256 まで同じ（41,277,264 バイト）。`VRMC_node_constraint` は 6 個（上腕と前腕の左右の aim 4・前腕の左右の roll 2。軸 Y、重み 1）。

### 手の回転を写す骨を焼く（`motion.bake_roll`）

`model.twist_bones` の `roll` の骨は、Copy Rotation で手の軸のまわりの回転を足す。Blender の Copy Rotation と VRM の roll は計算が違う
（上の節）ので、踊りの .blend（動画を描く方）では、`motion.bake_roll` で roll の骨の回転を正確な値のキーに焼き、拘束を止める。
- 入力 `blend`（踊りの .blend。roll の骨のあるモデルに動きを載せたもの）。元のファイルは変えず、別名で保存する。
- `bones`（任意）: 焼く roll の骨の名前の並び。省くと、止めていない roll の形の Copy Rotation（Y だけ・Add・ローカルどうし・相手は骨）がある骨を全部
  （骨の並びの順）。挙げた骨に、止めていない Copy Rotation が 1 つでないとき・roll の形でないとき・相手がこのアーマチュアの骨でないときは止める。
- 骨ごとに: シーンの全コマで、写す骨（拘束の `subtarget`）の回転（初期姿勢からの骨自身の回転の四元数）の、軸 Y のまわりのねじれ 2·atan2(y, w)
  （−180〜180°。`check.twist` と同じ決め方で、VRM の roll と同じ）に、そのコマの拘束の影響（influence。VRM の roll の重み。動きやドライバーがあればコマごとの値）を掛け、roll の骨の回転を
  「軸 Y のまわりにその角度」にしてコマごとにキーを打つ（前のコマの四元数と向きをそろえる。roll の骨自身の回転は使わず、上書きする）。拘束は止める。
- 「向くだけの骨」（Damped Track）は、VRM の aim と同じ決め方（いちばん小さい回転で向く）なので焼かない。
- 出力: `blend`、`report`（`model3d-bake-roll/0.1`。骨ごとの写す骨・拘束の名前と影響（全コマの最小と最大）・コマの数・ねじれの絶対値の最大、止める前の拘束が出していた
  角度との差の中央値・95% 点・最大（度））。
- `armature`（省くと最初のアーマチュア）。そのアーマチュアのシーンのコマの範囲で焼く。

**確かめたこと**:
- CI（Blender なし）では、角度の戻し方（台本の式とビットまで同じ）、コマごとの影響の掛け方、roll の形の見分け方、差の要約（180° をまたぐ差）、工程の受け渡しと悪い設定を止めること
  （代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスの流れの 5 段目（裾の押し出しまで済んだ踊りの .blend。凍結した台本で作り直したもの、954 コマ）に
  `bones` を省いてかけ、台本が焼いた 6 段目と比べた（実測）。左右の前腕の roll の骨が選ばれ、F カーブ 535 本のキー・取っ手・補間がバイトまで同じ、
  拘束（止めたものも）・骨 137 本も同じ、`check.blend_match` で 954 コマの骨の姿勢の差 0（`exact`）。報告の数も台本と同じ
  （ねじれの最大 左 43.9°・右 45.2°。止める前の拘束との差は 左 中央値 2.4°・95% 点 10.3°・最大 27.9°、右 2.9°・14.6°・28.5°）。
- 同じ踊りの写しで、左の roll の拘束の影響に動き（最初のコマ 0 → 最後のコマ 1）、右にドライバー（0.5）を付けてかけると、焼いた角度が
  「写す骨のねじれ × そのコマの影響」と全 954 コマで同じ（ずれ 0.0000°。実測）。

### 腕の関節の細りを測る（`check.arm_pinch`）

ねじれを受け持つ骨が無いと、骨が軸のまわりに回ったとき、関節のまわりのメッシュが飴の包み紙のようにねじれて細くなる（3 作目の VRM のダンスでは、
前腕と手が逆向きに ±180° 近く回り、手首と肘がくびれて見えた）。骨の角度（`check.twist`）だけでなく、描かれる形そのものの太さを測る
（.blend は変えない）。
- 入力 `blend`（踊りの .blend）。`body`（必須）: 体のメッシュ。
- `rings`（必須）: `[{"name", "at", "axis", "offset", "band", "radius", "groups"}, …]`。輪ごとに、初期姿勢で、中心を「骨 `at` の付け根 + 骨 `axis` の向き ×
  `offset` × `axis` の長さ」に置き、`axis` に沿った位置が中心の ±`band`（m）の中にあり、骨の軸からの距離が `radius`（m）より小さく、`groups` の
  頂点グループの重みの和が `min_weight`（既定 0.9）を超える頂点を、輪の頂点にする（体のメッシュの基本の形）。
  - `groups` には、その腕の骨（肩・上腕・前腕・手）と、足したねじれの骨（`model.twist_bones` の骨）を挙げる（挙げないと、ねじれの骨へ重みを移した頂点が輪から外れる）。
  - 3 作目では、肩の付け根・上腕の真ん中（頂点が粗いので帯を広く）・肘・前腕の 2 か所・手首（手の付け根。向きは前腕）の 6 か所を左右で測った。
- コマごとに、輪の頂点の「重心からの距離」の平均（そのコマの骨 `axis` の向きに垂直な分だけ）を、初期姿勢の値で割った比を出す。1.0 なら初期姿勢と
  同じ太さ。ねじれて絞られると小さくなる（半回転でほぼ 0）。体の Armature 以外のモディファイア（押し出しの補正など）は止めて、骨の変形だけを見る。
  輪の頂点が 6 に満たない輪は測らず（どのコマも 1.0）、報告の `unmeasured` に載せる。輪の大きさは m なので、1 Blender 単位 = 1 m でないシーンは止める。
  Armature の効き方が表示と描画で違えば、描画にそろえる（報告の `modifiers_synced`）。
- 出力 `report`: 輪ごとの頂点の数・初期姿勢の太さ（mm）・最小とそのコマ・5% 点・中央値・`thin`（既定 0.75）より細いコマの数・半分より細いコマの数。
  コマごとの比は工程のフォルダの `arm_pinch_raw.json`（丸めない）。`min_ratio` を渡したときだけ合否を出す（どれかの輪がどこかのコマで下回れば不合格）。
- `frames`（[最初, 最後]。省くとシーンの全コマ）、`armature`（省くと最初のアーマチュア）。
- 数の既定（`min_weight`・`thin`）は 3 作目で使った値。輪の位置と大きさ（`rings`）は体ごとに違うので必須。
- **読み方**: 肘は、曲げただけでも細く出る（3 作目で、ねじれを直した後も肘の輪は 954 コマのうち 645・477 コマで 0.75 より細かった。中央値 0.70・0.75）。
  ねじれの直しの効き目は手首と、肘の最小（半分より細いコマ）で見る。上腕を付け根から分けると、上腕の途中が少し細くなる（3 作目で中央値 6%）。

**確かめたこと**:
- CI（Blender なし）では、輪の頂点の選び方（帯・距離・重み）、太さの式（軸に沿ったずれを数えない）、頂点グループの重みの足し方、要約と合否、
  工程の受け渡しと悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（腕の細りを測る台本）と、同じ .blend・同じ 12 の輪で比べた（実測）。
  - ねじれを直す前の腕（腕どうしを離した後、ねじれの骨も、前腕のねじれの分け直しも無い。凍結の台本で作り直したもの）: 12 の輪の頂点の数・最小とそのコマ・5% 点・
    中央値・細いコマの数と、コマごとの比（台本と同じ 3 桁）が全部同じで、制作の記録とも同じ。手首が 0.75 より細いコマは左 392・右 211（最小 0.51・0.54）、
    肘が半分より細いコマは左 419・右 170。
  - 直した後（ねじれの骨・`motion.twist_split`・`motion.bake_roll` の後。流れの 6 段目）: 同じく全部同じ。手首が 0.75 より細いコマは左右とも 0（最小 0.84・0.83）、
    肘が半分より細いコマも 0。
  - アーマチュアの物体を 1〜60 コマで縦の軸のまわりに 90° 回した写しでも、輪ごとのコマの比が回さないものと 1e-6 の中で同じ（測る軸は、そのコマのアーマチュアの向きで決める）。

### 色の絵を塗り替える（`model.recolor_part`・`model.recolor`）

色の差の大半は灯りのことが多い（2 作目の VRM のダンスで、同じコマで部位ごとの色を測ると、肌・上衣・帯は「3D の色 × 暖色の灯り」でほぼ 2D に
なった）。灯りで合わない部位（元の色が違う）だけ、色の絵を塗り替える。どちらも、塗り替えた絵を詰め込んで、元の絵を使っていた所を付け替える
（元のファイルは変えない）。

- `model.recolor_part`: 1 つの部品（`object`。必須）のメッシュの、その絵を使う材質（物体が実際に描く材質。枠が物体の側の上書きなら
  その材質）の面の uv の範囲（三角形と、その外の `margin`（既定 3）
  画素）だけ、色の絵に色ごとの倍率 `gain`（[赤, 緑, 青]。必須）を掛ける。ほかの部品と分け合う 1 枚の色の絵（裾・帯・脚が分け合う下半身の絵
  など）でも、その部品の所だけが変わる。範囲に画素が無ければ要確認。
  - `dark_below` を渡すと、明るさ（RGB の最大。0〜1）がそれより暗い画素だけ（`dark_below` + `feather`（既定 0.1）で 0 へなめらかに）。
    明るい縁取りの模様などを変えない。省くと範囲の全部。
  - `image`: 色の絵の名前の一部（省くと、その部品が使う絵が 1 つのときだけ）。
  - 出力: `blend`、`texture`（塗り替えた絵の PNG）、`report`（`model3d-recolor-part/0.1`。範囲の画素と変えた画素の数）。
- `model.recolor`: `rules` の 1 つごとに、名前に `image` を含む絵（最初に見つかったもの）の、色相の範囲 `hue_range_deg`（[下, 上]、度。
  0 より下も書ける。幅は 360 まで。縁は `soft`（既定 6）度でなめらかに。範囲の外の色相は、円の上で近いほうの縁から測る）で、彩度が `min_saturation`（既定 0.15。そこから 0.1 の幅でなめらかに）を
  超える画素の、色相（`hue_shift`、度）・彩度（`sat_scale` 倍）・明るさ（`value_gamma` 乗して `value_scale` 倍）を変える。範囲の外
  （髪の絵の中の金の飾り、彩度の低い生成りの布など）は変えない。補正した絵は `<元の名前>_recolor` として詰め込む。同じ絵を指す規則は、
  前の規則の結果に順に重ねる（絵は塗り替える前に規則ごとに決める）。
  - 出力: `blend`、`report`（`model3d-recolor/0.1`。絵ごとの変えた画素の割合と、変える前後の色の中央値）。
- `model.remove_parts`: `objects`（必須）の物体を消す（使う物の無くなったメッシュも）。骨と揺れものの設定は残す（メッシュが無いだけで、
  動きには影響しない）。シーンに無い名前は要確認。報告（`model3d-remove-parts/0.1`）に、外した物と残ったメッシュ、まだ読む頂点の動きの
  ファイル（Mesh Cache）。袖などを外すと、その下の肌に部品の色がにじんだしみが出ることがある（`model.clean_skin` で直す）。

**確かめたこと**:
- CI（Blender なし）では、HSV の往復、色相の範囲の補正（範囲の外・彩度の低い画素は変えない、0 をまたぐ範囲、180° より広い範囲と一周の範囲）、
  同じ絵を指す規則を順に重ねること、uv の範囲と外の幅、範囲を作る材質の選び方、暗い画素だけの倍率（なめらかな縁、`dark_below` なし）、
  工程の受け渡しと悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本と比べた（実測）。
  - `model.remove_parts`（袖の布 2 枚）: 台本の流れの 3 段目にかけて、台本の 4 段目と `check.blend_match` で全部同じ（12 物体）。
  - `model.recolor_part`（裾。暗い布だけ）: 台本の流れの 5 段目にかけて、書いた PNG が台本の PNG とも、採用した踊りが使う裾の絵とも
    SHA-256 まで同じ。範囲の画素と変えた画素の数、.blend の中の画像の画素も台本と同じ。
  - `model.recolor`（髪の色相、肌の彩度と明るさ）: 顔の形を直したモデルに 2 通りの補正をかけ、採用したモデル（台本が作ったもの）と、全部の画像の
    画素・どの材質がどの画像を使うか・`check.blend_match`（11 物体）が全部同じ。

### 肌の島を塗り直す（`model.smooth_skin`・`model.clean_skin`）

画像から作ったモデルの肌には、作り方の癖が残る（2 作目の VRM のダンスで分かったこと）。
- 脇から腰の肌に、形の溝と、色の絵に焼き込まれた明暗の帯の両方があった（`model.smooth_skin`）。色だけで肌を選ぶと、色の近い服（生成りの上衣など）
  まで崩した。ただの平均でならすと肌が内側へ縮み（平均 10 mm、最大 43 mm）、服の縁との間にすき間が出て、服の下に隠れていた暗い肌が見えた。
  そこで、肌の島（つながり）だけを相手にし、形は縮めて戻すならし方（Taubin）にした。
- 部品（袖など）を外すと、その下の肌（腕輪から肘、とくに裏側）に、部品の色がにじんだ灰色のしみが見えた（`model.clean_skin`）。

どちらも、頂点の色（その頂点を使う角のうち、その絵を使う材質の面の角の色の平均。色の絵を双線形に引く）をならしてから、色の絵を塗り直す
（その絵を使わない頂点は相手にしない）。塗り直すのは 3 頂点とも対象の三角形で、
色の絵の上で頂点の色の重心補間で塗り、三角形の外へ `paint_margin_px`（既定 4）画素塗り足す（縮小して描いたときに、島の縁の元の色が線に
ならないように）。先に全部の塗り足しを塗り、あとで内側を塗る（逆にすると、隣の三角形の内側を塗り足しが上書きして、辺に沿って細い線が出る）。
混ぜる重みは頂点の重みの重心補間。塗り直した絵は `texture` の PNG に書いて詰め込み、元の絵を使っていた所を付け替える（元のファイルは変えない）。
- `body`・`image`（色の絵の名前の一部。その絵を使う材質の面だけを塗る。合う絵が 2 つ以上なら止める）・`skin`（肌色: `{"hue_deg": [下, 上], "saturation": [下, 上], "value_min": 下}`。
  色相は度、彩度と明るさは 0〜1）は必須。

`model.smooth_skin`:
- 肌の島: `skin` の色で、`image` を使う面の頂点のうち、高さ `seed_z`（[下, 上] m）・体の中心から `x_min` 以上・横を向いた面（法線の x の絶対値が
  `facing` の上の値以上）の頂点をいちばん多く含む島。島の外は触らない。
- 重み: 高さ `z_range`（[下, 上]。縁は ±`z_soft`、既定 0.03）× 横を向いた面（`facing` の [下, 上] で 0 → 1）× 体の中心からの横の距離（`x_min` から
  `x_soft`（既定 0.03）で 0 → 1、`x_max` の手前 `x_soft` で 1 → 0）の、なめらかな段（smoothstep）の積。
- 色: 頂点の色を、肌の島の中の隣どうしで `color_iterations`（既定 60）回ならす（1 回ごとに、重みの分だけ隣の平均へ寄せる。重み 0 の頂点は動かさず、
  境目の基準になる）。
- 形（`shape` を渡したとき）: 縮める（`factor`、既定 0.5）→ 戻す（`inflate`、既定 −0.53）を `iterations`（既定 10）回。重みは色と同じ重みに、高さ
  `z_max`（省くと `z_range` の上 + `z_soft`）の手前 `z_soft`（既定 0.02）で 1 → 0 を掛けたもの（服と重ならない所だけにする）。
- 報告（`model3d-smooth-skin/0.1`）: 島と肌と選んだ頂点の数（左右）、形の動き（mm、平均と最大）、塗った画素の数。

`model.clean_skin`:
- 範囲: いちばん重い頂点グループの名前が `island_groups`（名前の頭。手・指など）で始まる頂点を `island_min`（既定 50）以上含む島（腕の島）の中の、
  名前が `region_groups`（名前の頭。上腕・前腕など）で始まる頂点。手は塗らない（手首の境目は、手の元の色へなだらかにつながる）。
  いちばん重い頂点グループは、アーマチュア（`armature`。省くと最初のアーマチュア）の変形に使う骨のグループの中で比べる。
- 肌色でない頂点（しみ）を、まわりのきれいな色で埋める: きれいな隣のある頂点を、きれいな隣の平均にして、きれいな頂点に足す。これを `fill_iterations`
  （既定 300）回まで（外側から順に広がる）。そのあと範囲の中を、島の中の隣の平均へ `smooth_factor`（既定 0.5）ずつ `smooth_iterations`（既定 25）回ならす
  （範囲の外は動かさず、基準になる）。
- 埋めきれない頂点が残れば要確認。報告（`model3d-clean-skin/0.1`）: 腕の島の数、範囲・しみ・埋めきれない頂点の数、範囲のきれいな肌の色の中央値、
  塗った画素の数。

**確かめたこと**:
- CI（Blender なし）では、色の引き方（双線形・端）、頂点の色の平均、肌色の判定、隣の平均（使える隣だけも）、三角形の塗り（内側・塗り足し・内側が勝つ）、
  範囲の重み、縮まないならし（ただの平均は輪ごと縮む）、工程の受け渡し（埋めきれないと要確認）と悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（脇の筋を消す台本・腕のしみを消す台本）と比べた（実測）。
  - `model.smooth_skin`: 台本をかけた前のモデルにかけると、塗り直した PNG が採用したモデルの絵と SHA-256 まで同じ。.blend も採用したモデルと
    `check.blend_match` で全部同じ（11 物体。ならした形を含む）、画像の画素とどの材質がどの画像を使うかも同じ。島・肌・選んだ頂点の数と、形の動き
    （平均 0.61 mm、最大 8.31 mm）、塗った画素の数も台本と同じ。
  - `model.clean_skin`: 同じく、PNG が採用したモデルの絵と SHA-256 まで同じ、.blend も全部同じ。範囲・しみ・埋めきれない頂点の数と、きれいな肌の色も
    台本と同じ。

### 顔の形を参照の比率へ寄せる（`model.face_shape`）

顔の絵は立ち絵から 2 段階でずれる（頭の絵にするとき、3D にするとき。2 作目の VRM のダンスでは、目の間隔をそろえて比べると、3D は目から口・
あごまでが長かった）。**形 → 色 → 絵の順で詰める**（形を先に決めると、絵を描く人が 3D の顔の配置に合わせて描ける）。`model.face_shape` は
頭のメッシュの目より下の頂点を、高さに応じて上へ寄せ、あご先を細くする。色の絵は頂点に付いて動くので、描かれた鼻や口も一緒に上がる。

- 入力 `blend`。`head`（必須）: 頭のメッシュ。元のファイルは変えず、別名で保存する。形状キーのあるメッシュは、どのキーにも同じ動きを足す。
- `view`（必須）: 高さを読む正面の正射影の視点。`texture.project_reference` と同じ書き方で、`from` は `-Y` だけ（例 `{"from": "-Y",
  "center": [0, 1.5], "span": 0.32, "size": [1024, 1024]}`。1 画素は span ÷ 高さの画素）。目印（目・鼻先・口・あご）は、同じ視点で
  `texture.render_view` に描いた絵で読む。
- `lift`（必須）: `[{"vy": 今の高さ（視点の画素。下へ増える）, "up": 上へ動かす量（画素）, "name": メモ}, …]`。表を 1 画素おきに直線で補間し
  （最初の `vy` の 120 画素上から最後の `vy` の 200 画素下まで。表の外は端の値）、σ `smooth`（既定 10）画素のガウスで角を丸める。上下が入れ替わる
  （隣の高さを追い越す）所があれば、Blender の前で止める。動かし始め（`up` 0）を目の少し下に置くと、下まぶたを崩さない。
- `depth`（必須）: `{"front": y, "back": y}`（m）。顔の正面（y が `front` 以下）で 1、耳のあたり（y が `back` 以上）で 0 の段で効かせる
  （頭の後ろ側と、首とのつなぎ目は動かさない）。
- `narrow`（既定 0、0.5 まで）: 上へ動かす量に比例して、横（x）を顔の中心（`view` の `center` の x）へ詰める（あご先を細く）。
- 出力: `blend`、`report`（`model3d-face-shape/0.1`。頂点の数、1 mm 以上動いた頂点の数、上へ動かした最大（mm）、いちばん低い頂点の高さの前後）。
  1 mm 以上動いた頂点が無ければ要確認。

**確かめたこと**:
- CI（Blender なし）では、寄せる量の表（動かし始めより上は動かさない・角を丸めても表の値の近く・上下が入れ替わる表を止める）、奥行きの効き、あご先を
  細くする向き、工程の受け渡し（何も動かなければ要確認）と悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（顔の形を寄せる台本）と比べた（実測）。元のモデル（写し。
  元と sha256 が同じことを確かめた）に台本の設定（4 つの目印の表・丸め 6 画素・奥行き・あご先 6%）をかけると、採用した形のモデルと
  `check.blend_match` で全部同じ（11 物体）。台本をかけ直したものとも同じ。1 mm 以上動いた頂点（1,372）・上へ動かした最大（14.3 mm）・
  いちばん低い頂点の高さの前後も台本と同じ。

### 表情（頭の絵の差し替えと閉じ目の形。`model.closed_eye_key`）

表情は、頭の色の絵を時刻の表で差し替えて作る（`render.passes` の `expressions`。[要素別に描く](#要素別に描く)）。表情の絵は、通常の顔と同じ配置で
描いてもらった絵を、通常の顔と同じ設定で頭の色の絵に写したもの（`texture.project_reference`。設定を同じにすると、切り替えても目と口のほかは
変わらない）。目を閉じた絵にするときは、頭の形も変える（`model.closed_eye_key`）。2 作目の VRM のダンスで分かったこと:
- 画像から作った頭は、顔の面の目の所が穴で、5〜15 mm 奥に眼球の面があり、まつ毛は顔より手前に出た両面の板だった。目を閉じた絵（閉じた目の線）を
  写すと、正面からは合うが、斜めから見ると穴の縁と奥の面で線がずれて、欠けたり段になったりした。
- 目のくぼみとまつ毛の形が作り込まれていると、閉じた目の絵では、その陰影が目の形の薄い輪になって見えた（重ねる側で、顔の範囲の中だけ陰影を
  なだらかにする。`mv dance passes` の `figure` の `face_smooth`）。
- VRM の表情は形の変化なので、絵の差し替えで作った表情は VRM に入らない（動画用とアセット用は別物になる）。

閉じた目の線が細く薄いとき（届いた閉じ目の絵の線が目の幅の 2.5% しかなかった）は、頼み直さず、線を描き直す（`texture.closed_eye_line`）。
線を太らせる方法では形が足りなかった（先細りが消えて棒になった）。

`texture.closed_eye_line`（Blender なし。numpy と Pillow だけ）:
- 入力 `image`（目を閉じた顔の絵。`mv handoff check` で範囲の外を戻したものなど）。出力 `image`（PNG だけ。透けのある絵は透けを
  そのまま残す）と `report`。長さと位置は、幅 `base_width`
  （既定は絵の幅）の絵の画素で書く（絵の幅が違えば伸ばす）。
- 1. 元の線を消す: 消す帯（`erase_rows` × `erase_columns`。必須。絵の範囲に切り詰め、全部が絵の外なら止める）の中を、まわりの肌の色で埋める。肌の色は、絵を楕円の窓（`close_px`、既定 21）で
  「閉じて」（最大の窓のあと最小の窓。暗い細い線が消える）σ `close_blur`（既定 1.2）でぼかしたもの。帯の縁は σ `band_blur`（既定 3）でつなぐ。
- 2. 線を描く（`supersample`（既定 4）倍の細かさで塗り、輪郭も 1 画素でなぞってから、面積の平均で縮める）。目ごと（`eyes`: [目頭の x, 目尻の x]
  の並び。必須）に、
  - 弧: 両端の高さ `end_y`（必須）、下の縁が中央で `sag` だけ下にたわみ、太さ `thickness`（目尻寄りに `outer_bias`。両端へ細く）。
  - 目尻のはね `wing`（長さ・上向きの角・根もとの幅）と、下向きのまつ毛 `lashes`（弧の下の縁の位置 `at`（0 が中央、1 が目尻）ごとの長さ、
    根もとの幅、目尻ほど外へ寝かせる `lean_deg`）。
  - 線の色 `color`（[R, G, B]）で、塗った割合だけ混ぜる。線の形の既定は試作の値（1254 画素の顔の絵で決めた）。
- 報告: 変わった画素の数と箱（`base_width` の座標）、線の画素の数、設定。線が 1 画素も描けなければ要確認。
- 線の目頭側の端は、鼻のわきの急な面に入れない（2 作目の顔では顔の中心から 18 mm より内側。正面から写した線が、斜めから見ると縦に伸びて、
  とげに見えた）。

`model.closed_eye_key`:
- 入力 `blend`。`head`（必須）: 頭のメッシュ。`key`（既定 `eyes_closed`）の形状キーを足す（値 0。形状キーが無ければ Basis も足す。もうあれば止める）。
  頭の形状キーが絶対（Relative のチェックなし。時間で形を選ぶ）なら止める（値で入り切りできないため）。
- 目のまわりの箱: `eye_x`（|x| の [下, 上] m。左目は x > 0、右目は x < 0）・`eye_z`（高さの [下, 上] m）・`front_y`（y がこれより手前）。どれも必須。
  長さは m で、シーンの scale_length（1 Blender 単位が何 m か）で直してから形を作る。
- 左右の目ごとに:
  1. 箱に全部収まる島のうち、`eyeball`（頂点 `min_verts`（既定 100）以上・高さの幅 `min_height`（既定 0.02 m）より大きい）の島を眼球の面とする。
  2. その中心にいちばん近い「顔の面の縁の輪」（いちばん大きい島＝顔の面の、面が 1 枚しか付いていない辺の輪。`rim_min_verts`（既定 6）頂点以上）を
     目の穴の縁とする。
  3. 眼球の面を、穴の縁に張った膜の位置まで手前に出す（縁の頂点の y を、x と高さの距離の 2 乗の逆数で混ぜた値 + `membrane`（既定 0.0002 m））。
     左右と高さは変えないので、写した絵はずれない。
  4. ほかの、箱に全部収まる島（まつ毛の板・目頭の小さな部品）は、頭の中へ `tuck`（既定 0.03 m）引っ込める（閉じた目の線は絵で描く）。
  眼球の面か穴の縁が見つからなければ止める。
- 出力: `blend`、`report`（`model3d-closed-eye-key/0.1`。顔の面の島の頂点の数、左右ごとの穴の縁の頂点の数と範囲、動かした部品）。

**確かめたこと**:
- CI（Blender なし）では、合成の頭（顔の面と穴の縁の輪・眼球の面・まつ毛の板）で、眼球の面が縁の膜の位置へ出て左右と高さが変わらないこと・まつ毛が
  引っ込むこと・顔の面は動かないこと、眼球の面が無いと止めること、縁の輪の見つけ方、悪い設定を止めることを確かめた。
- `texture.closed_eye_line` は、CI で、楕円の窓の形・閉じる処理・ぼかしが OpenCV と同じ値になること（OpenCV があるとき）、合成の顔で元の細い線が
  消えて弧が描かれること・太さで塗る量が変わること・幅の違う絵へ位置を伸ばすこと、工程の受け渡し（同じ絵と設定なら再利用）と悪い設定を止めることを
  確かめた。2026-11-17 に、2 作目の VRM のダンスの台本（OpenCV で描く）と、同じ閉じ目の絵（1254 画素）と同じ設定で比べた（実測）: 変わった範囲の箱が
  台本と同じ。消す帯は画素まで同じで、違うのは線の縁（アンチエイリアスの塗り方の違い）だけ。台本が変えた 5,772 画素の中の差の平均 1.7、差が 8 を
  超える画素 569、32 を超える画素 4、最大 38/255。
- 2026-11-17 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（閉じ目の形を足す台本）と比べた（実測）。台本の流れの 6 段目に
  かけると、頭の形状キー（Basis と閉じ目。3,464 頂点）が、台本の 7 段目とも採用した踊りとも float32 まで同じ。報告（顔の面の島 1,703 頂点、左右の
  穴の縁と部品）も台本と同じ。

### 表情を形で作る（VRM に入る表情）

頭の絵の差し替えで作った表情は VRM に入らない（上）。VRM の表情は形の変化なので、表情を形状キーで作り、VRM の表情に割り当てる。
リポジトリ外の 3 作目の VRM のダンスで通した手順を工程にしたもの。形の計算は Blender の外で行う（numpy だけ。同じ入力なら同じ値）。

1. `model.export_mesh`: 頭のメッシュ（と色の絵）を、Blender の外で計算するための npz（と PNG）に書き出す（下）。
2. `model.face_shapes`: 頭のメッシュから、形状キーの頂点の動き（まばたき・笑顔の閉じ目・眉・口角）を計算する（下）。
3. `model.shape_keys`: 計算した形を、頭の形状キーとして入れる（下）。
4. `vrm.expressions`: 形状キーを VRM の表情（happy・blink など）に割り当てる（下。VRM Add-on を使う）。書き出しは `vrm.export`、
   書き出した VRM の形と割り当ては `check.vrm` で読む（[VRM を書き出す](#vrm-を書き出すvrmexportと中身を確かめるcheckvrm)）。
5. `render.face_sheet`: 表情の組み合わせを、いくつかの向き（正面・斜め・上下）から描いて一覧の絵にする（下）。
6. `texture.mouth_inside`: 色の絵の、口の中の面と部品が使う所だけを塗る（口を開ける形を作るとき。下）。

3 作目の VRM のダンスで分かったこと:
- 画像から作った頭は、目のまわりの面に細かい穴が多く、左右の並びも同じでない。面のつながりではなく、正面から見た位置（横と高さ）で動かす量を決める。
- 上まつ毛が立体で手前に張り出していると、まぶたを下ろしても、斜めからは下に白目がのぞく。閉じるときに上下のまぶたの奥行きを合わせる。
  正面だけで見ると気づけないので、形を作ったら斜め・上下からも描いて見る。
- 眉は前髪に隠れると効かない。

`model.export_mesh`:
- 入力 `blend`。`object`（必須）: 書き出すメッシュの物体（頭など）。.blend は変えない。
- 出力 `mesh`（.npz）: `co`（頂点の基本の形。形状キーがあれば基準のキーの形。物体の置き場所の行列はファイルを開いたときのまま。世界の座標で、長さは m。
  シーンの scale_length で直す）・`edges`（辺）・`tris`（三角形の頂点。Blender の loop_triangles）・`tri_loops`（三角形の面の角の番号）・`uv`（今の uv の、
  面の角ごとの座標。uv が無ければ書かない）・`normals`（頂点の法線を、置き場所の行列の 3×3 で移したもの。長さはそろえない）。頂点の並びと番号は .blend のまま。
- 出力 `texture`（任意、.png）: その物体の材質（実際に描く材質）が使う色の絵。`image`（絵の名前の一部）で選ぶ。省くと、使う絵が 1 つのときだけ
  （0 か 2 つ以上なら止める）。
- 出力 `report`（`model3d-export-mesh/0.1`。頂点・辺・三角形・面の角の数、uv の名前、形状キーの有無、scale_length、置き場所の行列、書いた絵の名前と大きさ）。

**確かめたこと**:
- CI（Blender なし）では、絵の選び方、世界の座標への移し方（scale_length も）、工程の受け渡し（絵は頼んだときだけ書く）と悪い設定を止めること（代役の Blender）を
  確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（頭のメッシュを書き出す台本）と比べた（実測）。同じモデル（頭 14,076 頂点・
  26,270 三角形）から書き出した npz と色の絵（4096×4096 の PNG）が、台本の書き出しと SHA-256 まで同じ。約 4 秒。

`model.face_shapes`（Blender なし。numpy だけで、同じ入力なら再利用する）:
- 入力 `mesh`（`model.export_mesh` の npz の `co`・`edges`・`tris`。頭は −Y を向き、長さは m）。島（辺でつながった頂点）は大きい順に数え、いちばん大きい島を肌とする。
- 出力 `shapes`（.npz）: 形の名前ごとの頂点の動き (N, 3)（世界の座標、m）。並びは右の目（R）・左の目（L）・口角の順で、形状キーの並びになる。L はキャラの左（+x）。
  `report`（`model3d-face-shapes/0.1`。目ごとの測った値と部品の頂点の数、口の合わせ目の高さと口の端、形ごとの動いた頂点の数と最大（mm）、設定）、
  `debug`（任意、.npz。目ごとの目の開きの画素と、測った上下の縁）。
- 部品を見つける箱と、動かす量（落ち方・閉じる線・弧・瞳を下げる量・眉・口角）には既定が無い（頭ごとに決める。3 作目の VRM のダンスの値はリポジトリ外のレシピ）。
  既定があるのは、画素の大きさ・ならし方・境のなだらかさ・まぶたを瞳の前に保つすき間などの細かな値だけ（既定は 3 作目の頭で使った値）。
- `front_y`（必須）: 正面の z バッファに描く三角形（いちばん手前の頂点の y がこれより手前。頭の後ろ側を除く）と、口のまわりの頂点の y の上限。
  `resolution`（既定 0.0001 m）: 正面の z バッファの画素。
- `eyes`（まばたき `blink_L`・`blink_R`）:
  - `find`: 白目の扇の中心を探す箱（|x| の `x`・高さ `z`・`front_y`）。箱の中の肌の頂点のうち、辺のいちばん多い頂点を扇の中心、その隣の輪を白目の扇とする。
  - `window`: 正面から測る窓（輪の x の平均 ± `half_width`、高さ `z`）。
  - `iris`: 瞳の部品（中心が輪の範囲にあり、中心の y が `front_y` より手前、大きさ（いちばん長い箱の辺）が `max_size` 以下、奥行きの幅が `max_depth` 未満、
    平均の y が `back_y` より奥の島）。見つからなければ止める。
  - `brow`（任意）: 眉の部品（窓の x の中に中心があり、中心の y が `front_y` より手前・高さが `z` の中・大きさが `min_size` 以上・いちばん低い頂点が `min_z`
    より上の島）。まぶたと一緒に動かさない。`brows` に要る。
  - `region`: まぶたとして動かす頂点（窓の中で y が `front_y` より手前の肌（扇・瞳・眉は除く）と、窓に中心のある大きさ `max_part_size` 以下の部品（下まつ毛など））。
  - 目の開き: 窓を正面から描いて、白目の扇と瞳が見えている画素。`min_rows`（既定 3）画素以上ある列の、いちばん長い連続（列の番号の差が `gap_px`（既定 5。1 以上）より大きい所で切る）を
    目の開きとする。列ごとの上の縁 U・下の縁 L と、縁のすぐ外（`edge_rows`（既定 3）画素）のいちばん手前の奥行きを測り、`smooth_px`（既定 15。奇数）画素の平均でならす。
  - 動かし方: 開きの真ん中より上（上まぶた）は閉じる線まで下へ、下（下まぶた）は上へ。上まぶたは縁から `upper.keep` をそのまま動かし（まつ毛の線を伸ばさない）、
    さらに `upper.fade` で 0 へ。下まぶたは縁から `lower.fade` で 0 へ。目頭と目尻の外は `corner_margin`（既定 0.004 m）で 0 へ。白目の扇は動かさない（まぶたが
    その前を通る）。
  - 奥行き: 閉じるとき、まぶたを上下の縁の奥行きの中ほどへ寄せる（下まぶたは、閉じる量の `lower_depth_gain`（既定 4）倍の割合で）。瞳は `iris_back` だけ奥へ下げ、
    瞳の前（瞳の x の範囲から `iris_margin`（既定 0.004 m）でなだらかに）では、閉じた線を下げた瞳より `iris_gap`（既定 0.0012 m）手前に出し、上の縁の
    `cover_above`（既定 0.001 m）より下へ下りた上まぶたは、下げた瞳より `cover_gap`（既定 0.001 m）手前に保つ。
  - まばたきの閉じる線は、下の縁から `close_at`（0〜1 の割合）の高さ。
  - `smile`（任意。笑顔の閉じ目 `blink_smile_L`・`blink_smile_R`）: 閉じる線は、目頭と目尻の縁の中ほどを結ぶ線から、上へ `arc` ふくらむ弧（sin の `power`
    （既定 0.8）乗）。下の縁と上の縁の間（`clip`。既定 [0.05, 0.10] は、下の縁から 5%・上の縁から 10% の内側）に収める。
- `brows`（任意。`eyes.brow` が要る）: `shapes`（{形の名前: {"inner": 内側の高さの動き, "outer": 外側の動き}}。名前に `_L`・`_R` を付けた形になる）。
  眉の部品の頂点を、外側から内側へ直線で混ぜた量だけ上下に動かし（同じ量なら一定）、`forward` だけ手前へ。
- `mouth`（任意。口角 `mouth_smile`）: 窓（|x| < `window.half_width`・高さ `window.z`）を正面から描き、口のまわり（|x| < `zone.half_width`。窓の幅以下で、
  広いと止める。窓の外の頂点は、見えるかどうかを窓の端の奥行きと比べてしまうため）の肌の頂点のうち、
  見えている面（奥行き `visible`（既定 0.0008 m）まで）のすぐ裏（`seam.depth` まで）を唇の合わせ目とする。合わせ目の高さは |x| < `seam.centre` の中央値、
  口の端は合わせ目の高さ ± `seam.band` の見えない頂点の |x| の `corner_percentile`（既定 98）百分位。口の幅を `bins`（既定 15）の点で区切って合わせ目の高さを
  測る（`min_points`（既定 3）点以上の区間だけ）。口の端のまわり（半径 `corner.radius` のガウス。見えている面から奥へ `corner.depth` まで）の頂点を、
  `smile` の `out`（外へ）・`up`（上へ）・`back`（奥へ）だけ動かす。口は開けない。
- 部品が見つからない・目の開きが無い・合わせ目が無いときは止める。動く頂点の無い形があれば要確認。

**確かめたこと**:
- CI（Blender なし）では、合成の頭（顔の面の目の穴・奥の白目の扇・瞳・眉・唇の合わせ目）で、部品の見つけ方、まばたきで上下のまぶたが閉じる線まで動くこと・
  離れた頂点と白目と眉は動かないこと・瞳は奥へ下げるだけなこと・左右が対称なこと、笑顔の閉じ目の弧、眉の動き、口角の動き（口の真ん中と離れた所は動かない）、
  部品が無いと止まること、工程の受け渡し（同じ入力なら再利用）と悪い設定を止めることを確かめた。
- 2026-11-17 に、3 作目の VRM のダンスのリポジトリ外の台本（表情の形を計算する台本）と、同じ頭のメッシュ（上の `model.export_mesh` の書き出し）と同じ値の設定で
  比べた（実測）: 11 個の形（まばたき・笑顔の閉じ目・眉 3 つの左右と口角）が、値まで全部同じ。10 個はビットまで同じで、口角の形だけ、口の中の部品の 678 頂点の
  z が台本では −0、ここでは +0（台本の、口を開ける計算の名残。値は同じ）。報告の測った値（扇の中心・輪・目の開きの範囲と高さ・縁の高さと奥行き・瞳の手前・
  合わせ目の高さと口の端・形ごとの動いた頂点の数と最大）と、確かめ用の目の開きの画素・縁も同じ。動きの最大は、まばたき 6.80・6.98 mm、笑顔の閉じ目 7.82・
  7.92 mm、眉 2.02・2.82・2.62 mm、口角 3.34 mm。約 1 秒。

`model.shape_keys`:
- 入力 `blend`、`shapes`（`model.face_shapes` の npz。形の名前ごとの世界の座標の動き (N, 3)、m）。`object`（必須）: 形状キーを足すメッシュ。元のファイルは変えない。
- 動きを、物体の置き場所の行列の 3×3 の逆で物体の中の座標へ移し（シーンの scale_length で直す）、基準のキーの形に足した形状キーにする。並びは npz の並び、
  値は 0、範囲は 0〜1。形状キーが無ければ Basis も足す。
- 同じ名前のキーがあれば止める（`replace: true` なら消してから足し直す。並びの最後になる）。形状キーが絶対（Relative のチェックなし）のメッシュ・頂点の数の
  違う形・非有限の値は止める。
- 出力: `blend`、`report`（`model3d-shape-keys/0.1`。足したキーと動いた頂点の数・最大、全部のキーの並び、基準のキー）。

`vrm.expressions`（VRM Add-on を有効にして起動する。既定 `addons: ["bl_ext.user_default.vrm"]`）:
- 入力 `blend`（VRM のキャラの .blend）。`object`（必須）: 形状キーを持つメッシュ。`binds`（必須）: {VRM の表情の名前: {形状キー: 重み}}。
  名前は VRM 1.0 の決まった表情（`happy`・`angry`・`sad`・`relaxed`・`surprised`・`aa`・`ih`・`ou`・`ee`・`oh`・`blink`・`blinkLeft`・`blinkRight`・
  `lookUp`・`lookDown`・`lookLeft`・`lookRight`・`neutral`。`check.vrm` と同じ名前）。重みは 0〜1。カスタムの表情は扱わない。
- 挙げた表情ごとに今の割り当てを外し、挙げた順に割り当てる。空の {} は外すだけ（口を開ける形が無いので `aa` などを空にする、など）。挙げない表情は変えない。
- 形状キーが `object` に無ければ止める。アーマチュアが 2 つ以上なら `armature` で名前を渡す。元のファイルは変えない。
- 出力: `blend`、`report`（`model3d-vrm-expressions/0.1`。表情ごとの変える前と後の割り当て（物体の名前・形状キー・重み）、割り当てのある表情、形の名前だけで
  メッシュの無い割り当てが残る表情 `name_only`）。
- キャラを作った道具の初期値の表情は、形の名前だけでメッシュの無い割り当てのことがある（VRM には書き出されず、`check.vrm` では割り当ての無い表情になる。
  3 作目の VRM のダンスで見た）。

**確かめたこと**（`model.shape_keys`・`vrm.expressions`）:
- CI（Blender なし）では、形の読み方（並び・頂点の数・形・非有限）、世界の動きを物体の中の座標へ移すこと（scale_length も）、表情の名前の表、無い形状キーの
  見つけ方、工程の受け渡し（形状キーを入れるだけならアドオンは要らない・表情の割り当ては VRM Add-on を有効にする前置きで起動する・挙げた順）と悪い設定を
  止めること（代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で（実測）、3 作目の VRM のダンスのモデル（表情を入れる前）から、model3d の工程だけで
  `model.export_mesh` → `model.face_shapes` → `model.shape_keys` → `vrm.expressions` → `vrm.export`（メタはキャラを作った道具の VRM から）とつなぐと
  （約 26 秒）、書き出した VRM が制作のアセットの VRM とバイトまで同じ（SHA-256 が同じ）。`.blend` も、制作の表情入りのモデルと `check.blend_match` で exact、
  形状キー（Basis と 11 個の座標の float32・値・範囲・相対の元）と VRM の表情の割り当て（13 の表情を変え、ほかの 5 つはそのまま）が全部同じ。
  踊りの .blend（表情を入れる前の版）にかけたものも、制作の踊りと形状キーと割り当てが同じで、`check.blend_match` で exact（骨の姿勢は全コマ、変形した形は 13 コマ）。

`render.face_sheet`（描くのは Blender の中、並べるのは Pillow）:
- 入力 `blend`。`object`（必須）: 形状キーを持つ頭（相対の形状キーだけ。絶対の形状キーは値で形を選べないので止める）。`combos`（必須）: [[札, {形状キー: 値}], …]。
  組み合わせごとに、ほかの形状キー（基準のキーのほか）を 0 にしてから値を入れる。値は −10〜10（Blender の形状キーの値の幅）。基準のキーは入れられない。
  入れる値が効くように、その場で（保存しない）入れる形状キーのミュートを外し、値（0 にするキーの 0 も）が範囲（slider_min〜slider_max）の外なら範囲を広げ、
  形状キーを 1 つだけ見せる設定（ピン）を切り、形状キーの値を動かすアニメーションとドライバーを外す（報告の `key_changes`）。
  `views`（必須）: [{"name", "azimuth"（度。0 が正面、正でカメラがキャラの左へ回る）, "elevation"（度。正で上から）, "scale"（正射影の幅 m）,
  "centre"（狙う点 [x, y, z] m。省くと頭の箱の中心）}, …]。カメラは狙う点から 1 m。長さ（狙う点・幅・カメラまでの 1 m・見える奥行きの既定の範囲）は m で、
  シーンの `scale_length` で Blender の単位に直す（`model.shape_keys` と同じ。1 ならそのまま）。札と向きの名前は英数字と `_ . -`（絵のファイルの名前
  `<札>_<向き>.png` になる）。別の組み合わせと名前が重なる（`smile_left` と `front`、`smile` と `left_front`。大文字と小文字の違いだけも）ときは、描く前に止める。
- `hide`: 描かない物体（髪など。無い名前は止める）。`size`（既定 500）: 絵の幅の画素（高さは × 0.9）。`samples`（既定 16）: EEVEE の標本。`background`
  （既定 [0.18, 0.18, 0.2]）: 背景の色（シーンの直線の値。世界を足す）。灯りは足さない。色の変換は Standard。.blend は保存しない。
  .blend のままだと出てくる絵をすり替えうる描く設定（シーケンサー・コンポジター・描く範囲の切り取り・画素の縦横比・多視点・透ける背景・ぶれ・色の補正の
  look・露出・ガンマ・カーブ・表示の装置・出力の形式（PNG の RGBA 8 ビット、色の管理はシーンのまま））は、その場で決まった値にし、マーカーに結んだカメラと、
  頭の描かない設定（hide_render）も外す（.blend と違っていたものは報告の `render_changes`）。頭が描かないコレクションの中にしかない（コレクションの
  hide_render・ビューレイヤーの除外）か、`hide` に頭を入れたときは止める。
- 並べ方: 表情（行）× 向き（列）。こまは `cell`（既定 320）× int(`cell` × 0.9) 画素、左の列の絵の左上に札（`font`（書体のファイル。省くと Pillow の内蔵の書体で、
  ラテン文字だけ）・`font_size`（既定 16））。出力 `sheet`（.jpg は `quality`（既定 90）、.png）、`report`（`model3d-face-sheet/0.1`。描いた絵の一覧・箱の中心 m・`scale_length`・`key_changes`・`render_changes`・設定）。
  描いた絵は工程のフォルダの `views/<札>_<向き>.png`。絵に日時・ファイル名などの控え（メタデータ）を書かない（同じ入力なら同じバイト列）。

**確かめたこと**（`render.face_sheet`）:
- CI（Blender なし）では、カメラの向き、長さ（m）を Blender の単位に直すこと、絶対の形状キーを止める説明、値が効くように変える形状キー（ミュート・範囲）、
  決まった値にする描く設定、頭が描かれないコレクションを見つけること、並べ方（行と列・札は左の列だけ・JPEG と PNG）、絵の名前が重なる組み合わせを止めること、工程の受け渡し（組み合わせと向きの全部を描いて並べる・既定の値）と
  悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（表情を描く台本と並べる台本）を、同じ表情入りのモデル・7 つの組み合わせ・3 つの向き・
  同じ大きさと書体にかけ直したものと比べた（実測）: 描いた 21 枚が画素まで同じ、並べた一覧の絵（JPEG）がバイトまで同じ。2 回描いても 21 枚がバイトまで同じ。約 13 秒。
- 同じ日に、同じモデルを「1 Blender 単位 = 1 cm」のシーン（親の無い物体の位置と大きさを 100 倍、`scale_length` 0.01）に作り直し、同じ設定（m）で描いた（実測）:
  1 m のシーンの絵とほぼ同じ（21 枚とも、画素ごとの差の平均 0.012〜0.013 段、8 段を超えて違う画素は 1 枚あたり 22 個まで）。長さを直す前は、顔が写らず一面の灰色だった。
- 同じ日に、同じモデルの写しで（実測）: 頭の形状キーを絶対にした写しは、描く前に止まって絵を描かない。「値が効かない」罠（入れるキーのミュート・入れる値より狭い範囲・
  0 にできない範囲・値を動かすアクションとドライバー・ピン）を入れた写しは、21 枚と一覧の絵が罠の無いモデルとバイトまで同じ（`key_changes` に変えたことが全部出る）。
  値が効くようにする前は、21 枚のうち 20 枚が違う絵だった。描く設定の罠（シーケンサーに色の素材・絵を反転するコンポジター・描く範囲の切り取り・画素の縦横比 2・
  透ける背景・ぶれ・露出とガンマとカーブ・別のカメラを結んだマーカー・白黒の出力・頭の描かない設定）を入れた写しも、21 枚と一覧の絵が罠の無いモデルとバイトまで同じ
  （設定を決まった値にする前は、21 枚ともシーケンサーの色の素材が写った）。

`texture.mouth_inside`（Blender なし。numpy と Pillow だけで、同じ入力なら再利用する）:
- 画像から作った頭は、口の中の面（唇の裏の袋と、中の歯や舌の部品）が使う色の絵の所にも、肌の色が入っていることがある（外から見えない所に、
  まわりの色がそのまま回っている）。口を開ける形を作ると、肌色のかたまりが見える。そこだけを塗る（外から見える面が使う画素には触らない）。
- 3 作目の VRM のダンスで分かったこと: 口を開けたとき肌色の板が見えたら、色の絵のせいとは限らない（そのときは、唇の合わせ目の面が伸びた物だった）。
  見えている面が何かを、色分けで先に確かめる。
- 入力 `mesh`（`model.export_mesh` の npz の `co`・`edges`・`tris`・`tri_loops`・`uv`）と `texture`（その頭の色の絵。8 ビットの絵だけで、16 ビットの灰色や浮動小数の
  絵は止める。色に変えると 255 で切られて白くなるため）。出力 `texture`（PNG だけ。透けのある絵（透明色キーの PNG も）は透けを残し、塗った画素だけ不透明にする。透けたままだと
  塗った色が見えない）と `report`（`model3d-mouth-inside/0.1`。袋・部品・舌の三角形の数、部品の頂点の数、塗った画素の数、見える面と分け合うので触らなかった画素の数、
  不透明にした画素の数、uv が 0〜1 の外にかかる塗る三角形の数、設定）。
- 口のまわり: 窓（|x| < `window.half_width`・高さ `window.z`）を正面から描き（画素 `resolution`、既定 0.0001 m。`front_y` より手前の三角形）、口のまわり
  （|x| < `zone.half_width`・y < `front_y`。`zone.half_width` は窓の幅以下で、広いと止める）の頂点のうち、いちばん手前の面より `visible`（既定 0.0008 m）
  以上奥の頂点を「見えない」とする。肌（いちばん大きい島）の
  三角形のうち 3 頂点とも見えないもの（唇のふちをまたぐ面は塗らない）が、口の中の面。
- 部品: 中心が |x| < `parts.half_width`・y < `parts.front_y`・高さ `parts.z` の中にあり、大きさ（いちばん長い箱の辺）が `parts.max_size` 未満で、いちばん手前の頂点が
  `parts.front_limit` より奥の島。そのうち、いちばん奥の頂点が `tongue.back_y` より奥まで届き、x の平均が `tongue.centre` 未満の島を舌とする。
- 塗り方: 選んだ三角形が uv で掛かる画素（三角形の外 `uv_margin`（既定 0.02。重心の座標の割合）まで）を、縦横の 4 近傍で `grow`（既定 2）回ふくらませ、
  ほかの面が使う画素を除いて、`inside_color`（既定 [92, 26, 30]）か舌は `tongue_color`（既定 [168, 72, 74]）で塗る（色の既定は 3 作目で使った色。ふくらませた所が
  重なれば舌の色。塗った画素の数は重なりを 1 回だけ数える）。
- 口のまわりの箱と部品の見つけ方に既定は無い（頭ごとに決める）。塗った画素が無ければ要確認。uv が 0〜1 の外の所は絵の外として塗らない（絵を繰り返して使う uv
  では塗れていない所が残る）ので、塗る三角形がそこにかかれば要確認。

**確かめたこと**（`texture.mouth_inside`）:
- CI（Blender なし）では、合成の口（顔の面・その奥の袋・歯と舌の部品を、uv の別の所に置いたもの）で、袋と歯は暗い赤・舌は明るい赤に塗り、顔の面の画素には
  触らないこと、ふくらませ方、重なった塗りを 1 回だけ数えること、透け（透明色キーも）を残して塗った画素だけ不透明にすること、uv が絵の外の三角形を数えて要確認にすること、工程の受け渡し（同じ入力なら再利用）と
  悪い設定（窓より広い口のまわりも）・uv の無いメッシュ・16 ビットの絵を止めることを確かめた。
- 2026-11-17 に、3 作目の VRM のダンスのリポジトリ外の台本（口の中の色を塗る台本）と、同じ頭のメッシュと色の絵（4096×4096）と同じ値の設定で比べた（実測）:
  塗った絵が台本の絵と SHA-256 まで同じ。報告の数（袋の三角形 299・部品の三角形 694・舌の三角形 464・塗った画素 620,748・見える面と分け合うので触らなかった
  画素 5,261）も同じ。約 6 秒。3 作目では、この絵はモデルに入れていない（口を開ける形を作れなかったため）。

**限界**:
- 部品の見つけ方は、白目が辺のいちばん多い頂点を中心にした扇で、瞳と眉が別の部品の頭を前提にしている（3 作目の頭の作り）。別の作りの頭では、見つけ方から
  作り直す所がある。
- 口を開ける形は作らない。3 作目の頭は、唇の合わせ目の面がところどころ上下で同じ頂点を使っていて、下唇を下げると開きがぎざぎざになった（合わせ目を切り分けると、
  ほおに裂け目が出た）。
- 視線（目の向き）は動かさない。

### 頭の候補の口の作りを調べる（`check.mouth`）

3 作目の VRM のダンスでは、口を開ける形を作れなかった。頭の唇の合わせ目の面が、ところどころ上下で同じ頂点を使っていて（くっついていて）、下唇を
下げると開きがぎざぎざになり、くっついた頂点を切り分けると、ほおに裂け目が出た。次の作品では、頭の候補を選ぶ前に（体の太さを測るのと同じ確認地点で）
口の作りを調べ、唇がくっついた候補は選ぶ前に伝える。`check.mouth` はそのための 3D ツールの検査（置き場はユーザーの決定）。

- 数と絵だけを出し、合否は決めない（AGENTS.md の model3d の例外「構造と数値だけを報告し、好みは判定しない」）。くっついた所があると口を開けにくいが、
  口の動きが要るか（歌う振り付けか）・頭を作り直せるかは作品ごとに違うので、選ぶのは人。
- 入力 `mesh`（`model.export_mesh` の npz。頭は −Y を向き、長さは m。候補が別の形式なら、先に .blend に読み込んで書き出す）、任意で `texture`（その頭の
  色の絵。npz の `uv` と `tri_loops` が要る。8 ビットの絵だけで、16 ビットの灰色や浮動小数の絵は止める。`texture.mouth_inside` と同じ読み方）。
  Blender なし（numpy と Pillow）で、同じ入力なら再利用する。
- 口のまわりの見つけ方: `front_y`・`window`・`zone`（`texture.mouth_inside` と同じ書き方）、唇の合わせ目 `seam`（`model.face_shapes` の `mouth.seam` と同じ:
  見える面のすぐ裏 `depth` まで・真ん中 |x| < `centre`・合わせ目の高さ ± `band`）。どれも既定は無い（頭ごとに決める）。
- 出す数（報告 `model3d-mouth-check/0.1`）:
  1. 上下の唇が別の面か（`lips`）: 口の幅を `bins`（既定 15）の点で区切った区間ごとに、その x の幅の中の浅い所（見える面から `seam.depth` まで）だけを
     通って、唇の外の面の、合わせ目の上の帯から下の帯へ（帯は合わせ目から `seeds`（既定 [0.003, 0.008] m）の間）、面のつながりで行けるかを調べる。
     上下の唇が別の面なら、浅い所ではつながらない（つながるのは口の奥）。つながる区間の数がくっついている所（`glued_bins`）、つながらない区間の数
     （`separate_bins`）、くっついた所の頂点の数、口の奥を回ってもよいときのいちばん短い道（`deep_path_mm`）。区間ごとの道の長さも報告に残る。
     区間の中だけで調べるのは、横へ回ると、離れた所からも近くのくっついた所を渡れてしまうため。
  2. 口のまわりの肌の辺のうち、3 枚以上の面が集まる辺と、1 枚の面しか無い辺（穴の縁）の数（`edges`）。
  3. 口の中（`inside`）: 3 頂点とも正面から見えない肌の三角形（唇の裏の袋）の数、見えない頂点の数、見える面からいちばん深い所（mm）。`parts`・`tongue`
     （`texture.mouth_inside` と同じ書き方）を渡すと、口の中の部品（歯・舌など）の頂点の数も。
  4. 色（`color`。`texture` があるとき）: 口の中の面と、口のまわりの見える肌の面が uv で使う画素の色の中央値と、その差（RGB の距離）。差が小さいと、
     口を開けたときに口の中が肌色に見える（`texture.mouth_inside` で塗れる）。uv が 0〜1 の外の所は絵の外として数えず、そこにかかる三角形の数を出す
     （`uv_outside_triangles`。絵を繰り返して使う uv なら、中央値に入っていない所がある）。
- 絵（任意）: `front`（正面の色分け。上唇は赤み、下唇は青み、口の中は暗い赤、ほかの肌は明るい灰色、ほかの部品は灰色、合わせ目は黄色の線、くっついた所の
  頂点は赤い点）、`section`（x = `section_x`（既定 0）の面で切った線を横から見た絵。左が顔の前。肌は白、口の中は赤、部品は灰色）。
- 制作のときの調べ方のうち「下唇を下げて色分けで見る」は入れていない（口を開ける形は作らない。1. の数で代える。ユーザーの決定）。

**確かめたこと**（`check.mouth`）:
- CI（Blender なし）では、合成の唇（上下の外の面が合わせ目で同じ位置に来るが別の頂点で、裏へ折れて口の奥の壁でだけつながる面）で、上下が別の面なら
  どの区間もつながらず口の奥を回る道だけがあること、真ん中の列だけ浅い所でつないだ唇ではその区間だけをくっついていると数えること、3 枚の面が集まる辺の
  数え方、口の中の色と肌の色の差（uv が絵の外の三角形は数えずに数を出す）、絵の大きさ、工程の受け渡し（くっついていても要確認にしない）と悪い設定、
  16 ビットの色の絵と、合わせ目が見つからないときに止めることを確かめた。
- 2026-11-17 に、3 作目の VRM のダンスの頭（`model.export_mesh` の書き出し）にかけた（実測。照合する台本は無く、制作の記録の調べた結果と比べた）: 口の幅の
  11 区間のうち 7 区間でくっついていて、4 区間は離れていた（制作の記録の「ところどころ同じ頂点でくっついている」と同じ）。口のまわりで 3 枚以上の面が
  集まる辺 1・穴の縁 4。唇の裏の袋 299 三角形（いちばん深い所 44 mm）、口の中の部品 7（うち舌 1）。口の中の色は肌の色との差 60.5（口の中も肌色寄りで、
  口を開けると肌色のかたまりに見えた制作の記録と同じ向き）。約 2 秒。

**限界**: 口を閉じた頭（上下の唇の外の面が合わせ目で会う頭）が前提。口が少し開いた頭は、合わせ目の見つけ方が合わない。2 作目の頭やほかの作り方の候補では
確かめていない。

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
- **横から撮った振りでは、奥行きの向きの差が点にほとんど入らなかった**（3 作目の VRM のダンスの踊り。推定の値）。横向きで両腕を交互に上下させる振りで、
  左右の手首の、カメラの奥行きの向き（体の左右）の差が 0 になり、骨へ移すと左手が右の前腕を上から下へまっすぐ通り抜けた。腕や手が通り抜けたら、
  直す前に、元の動画の同じコマと並べて、本来どうすれ違う動きかを見る（原因は、横からの動画で奥行きが読めないためと見ている。確かめていない）。

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

## 動きを検品する（`check.animation`）

動きを付けた .blend（VRM の人型の骨。骨へ載せ、揺れものを焼き、手直しをした後）を全コマ調べて、貫通と破綻の候補を「いつ・どこが・どれだけ」の
出来事で挙げる。ダンスの動画は動き続けるので、1 コマで分かる破綻（静的）と、前後のコマと比べて分かる破綻（動的）を 1 本の報告にする。
リポジトリ外の 3 作目の VRM のダンスのあとに作った試作（動きの検品の台本）を、Blender の中で測る所（`blender_adapter/animation_sample.py`）と、
Blender の外で数える所（`animation_check.py`。numpy だけ）に分けて移した。

- **直さない・採点しない・関門にしない**（ユウ 2026-11-17〜18。作る側のエージェントが破綻に気づくための手助け）。出来事はどれも
  `status: review`・`evidence: measured` で、数と場所だけを出す。異常と読むかは、絵を見て決める。**出来事が無いことは、破綻が無いことではない**。
  - 脚の太さを測れない（その骨にいちばん重い頂点が体のメッシュに無い。重みが補助の骨にある、など）ときは、推定の太さ 5 cm（仮の値）で見て、
    その脚を使った腕と脚・脚どうしの重なりの出来事は `evidence: estimated` にする（実測と分ける）。報告の `leg_radius_estimated` に、推定にした脚を並べる。
  - 工程は、出来事があれば `needs_review`、無ければ `succeeded`（`report_only: true` なら測るだけで `succeeded`）。合否（`pass`）・`strict` は無い。
  - 縮める・押し出すなどの直す機能は入れない（直すのは `motion.push_hands`・`motion.ground_feet` などの別の工程）。
- **報告の形は `mv inspect` に合わせた**（出来事ごとの `status: review`・`evidence: measured`、コマと秒、いちばん大きいコマ）。ただしコマは
  Blender のコマの番号で、`frames` は最初と最後を両方含む（`mv inspect` は動画のコマの番号で `[開始, 終了)`）。2 つの報告をまとめる決まりは、まだ無い。
- **`check.motion` とは段が違う**: `check.motion` は骨へ載せる前の点（`motion.prepare_keypoints` の points）を測る。こちらは仕上がった .blend を見る。

**見る物**:

| 種類 | 信号 | 見つける物 | 測り方 |
|---|---|---|---|
| 静的 | `penetration.arm_body` | 腕が胴・頭に入る | 腕の上の 7 点（二の腕 2・ひじ・前腕 2・手首・こぶし）と太さが、体の形に入っている深さ。胴と頭のうち近い方 |
| | `penetration.arm_leg` | 前腕・手首・こぶしが太もも・すねに入る | 点と、骨のまわりの太さ |
| | `penetration.arm_arm`・`penetration.leg_leg` | 左右の腕どうし・脚どうしが重なる | 同上（腕は手首とこぶしを見ない。道具の上で隣り合うため） |
| | `penetration.body_cloth` | 体が布（揺れものの骨で動くメッシュ。裾・垂れなど）を突き抜ける | 初期姿勢で布の近く（`cloth_near_m` 未満）にある体の頂点から、その骨の軸へ向けた光線が `cloth_depth_min_m` 以上奥で布に当たる（体が布の外へ出ている）頂点を、(骨, 布) ごとに数え、`cloth_min_vertices` 以上のコマだけ挙げる（光線の当たり判定は Blender の BVHTree） |
| | `penetration.floor` | 足が床（z = 0）より下 | 足とつま先の骨にいちばん重い頂点の、いちばん低い点（下の `foot_lowest`） |
| | `joint.wrist_bend`・`joint.wrist_twist` | 手首の曲げすぎ・ねじれすぎ | 二の腕から見て軸まわりに回っていない前腕の向きから見た、手の回り（振りとねじれに分ける） |
| | `joint.neck`・`joint.knee_back` | 首の曲げすぎ、ひざが逆に曲がる | 胸から見た頭の回り。ひざが股と足首を結ぶ線より後ろにある長さ |
| 動的 | `motion.pop` | 向きが 1 コマで飛ぶ（戻る／切り替わる） | 部位の向きの変わりが、前後のコマ（`pop_window`）の中央値の `pop_ratio` 倍以上で `pop_min_deg` 以上 |
| | `motion.position_pop` | 関節の位置が 1 コマだけ飛んで戻る | 前後のコマの中間から `pos_pop_min_m` 以上ずれて、次のコマで戻る |
| | `motion.jitter` | 小刻みに震える | 回りの向きが `jitter_window` コマに `jitter_reversals` 回以上逆になる |
| | `motion.foot_slide` | 着いた足が滑る | 床に着いた足が、1 コマに `slide_min_m_per_frame`〜`slide_max_m_per_frame` 横へ動くコマが `slide_frames` 以上続く |
| | `motion.cut` | 全身の切り替え（動きのつなぎ目。知らせるだけ） | 人型の骨（指と目を除く）の `cut_share` 以上が同じコマで `cut_bone_deg` 以上回る。前後 1 コマのほかの動的な出来事は挙げない |

同じ信号・同じ場所の続いたコマ（`merge_gap_frames` 以下のすき間）は 1 件にまとめ、いちばん大きいコマの値と場所（世界の座標）を出す。
静的な出来事が 2 コマ以下なら `momentary`（一瞬。止め絵では気づきにくい）。指の飛びは 1 本ずつ挙げず、手ごとにまとめる。
`severity` は絵で見る順番の目安（cm はそのまま、度は 30 で割る、足の滑りは 0.4 倍、指は 0.02 倍）で、良し悪しの点数ではない。

**決めごと**（どれも、試作で絵を見て誤りを直した結果）:
- **体の形は、そのモデルの初期姿勢のメッシュから作る**。面の向き（法線）には頼らない（服の縁や髪の束で逆を向く）。
  - 胴: 胴の骨（腰・腹・胸・首）の重みが `torso_weight_min`（既定 0.85）以上の頂点を、高さ 2 cm ごとの輪切りの「向きごとの半径」（24 の向き）にする。
    わきの下のように腕の骨と混ざった所は、T の字の初期姿勢では外へふくらんでいて、腕を下ろすと引っこむ（入れると、腕を下ろしただけで胸に 10 cm 入ると出た）。
  - 頭: 頭の骨だけに重みのある面までの距離と、頭の中の球（中心は頭の高さの 42%、半径は幅と奥行きの小さい方の 33%）の近い方。
  - 脚: 骨に重みのある頂点の、骨の線分までの距離の中央値。腕: 点ごとの太さの表（`arm_upper`・`arm_lower`・`fist`）。
- **その体で避けられない重なりは差し引く**: 腕を自然に下ろした時の二の腕と胴の重なりを先に測り（報告の `arm_torso_baseline_m`）、二の腕の 3 点の深さから引く
  （肩幅より胸まわりが広い体では、下ろしただけで重なる。3 作目は 2.5〜3.1 cm、2 作目は 2.7〜3.3 cm）。
- **描かない補助のメッシュは数えない**（`hide_render` の物。押し出し用の身代わりなど）。
- **布として見るのは、揺れものの骨で動くメッシュだけ**: 頂点の 6 割以上が人型でない骨にいちばん重く、そのうち頭の骨の下の骨のものが 5 割未満の
  メッシュ（裾・垂れ・帯の飾り）。頭の下の物（髪）は布にも体にも数えない。体の皮と一体の服は「体」。アーマチュアで動かないメッシュ（布の計算で
  動かす裾など）は見ない。報告の `cloth_objects`・`hair_objects`。
- **初期姿勢でもう布の外にある頂点は見ない**（帯の下に差し込んだ裾の上など、作りとしてそうなっている所。骨の軸へ向けた光線が `cloth_depth_min_m` の
  半分以上奥で布に当たる頂点。報告の `cloth.tucked_vertices`）。
- **布の小さい所は挙げない**: `cloth_min_vertices` より少ない頂点の所（帯の結び目・垂れの先）は、数字は大きくても絵では気にならなかった
  （2 作目の VRM のダンスで、帯の結び目が 4 cm 沈むと 15 回出た。13〜20 頂点の所で、太ももが裾を突き抜ける所は 25〜33 頂点だった）。
- **浅い重なりは件数だけ**: `contact_min_m`〜挙げ始める深さの重なりは、報告の `contact_frames` にコマ数だけ数える。
- **腕と胴は、腕の場所ごとに挙げ始める深さを変える**（`arm_body_min_m`）: 二の腕とひじを体の脇に付けた姿勢は、袖に隠れて目には破綻に見えにくい。
- **太ももどうしは深い時だけ**: 柔らかく、閉じたり交差したりで重なるので、片方の軸が相手の中ほどまで入った時（太さの和 − 細い方の半分）だけ挙げる。
- **飛びは「目に見える向き」で測る**: 見た目を動かす骨（どれかの頂点に 0.25 以上の重みがある骨）だけを、部位そのものの向き（アーマチュアの中）で測る。
  腕・脚の筒は軸まわりの回りを除く（前腕の骨が軸まわりに 172° 裏返っても、見た目はねじれを分ける補助の骨が決めていて変わらない。親から見た回りで
  測ると「飛び」「ねじれ 180°」と誤って出た）。見た目を動かすいちばん近い親が同じコマで飛んだ時の子は挙げない。揺れものの骨（人型でない骨）は
  `spring_pop_min_deg`・`spring_pop_ratio` から。腕ごと速く振っているコマ（付け根が 1 コマに `swing_m_per_frame` 以上動く）は、戻る時だけ挙げる。
- **着いた足の滑りは、低く速い踏み出しを数えない**（1 コマ `slide_max_m_per_frame` より速い足）。
- **手首のねじれは、補助の骨に左右されない**: `check.twist`（骨の初期姿勢からの回りを、その骨の軸で分ける）とは測り方が違う。
  前腕の骨が軸まわりに裏返り、手が逆に回って打ち消している時は、`check.twist` では前腕と手のねじれが大きく出うる（同じ場面で比べてはいない）。

**入力** `blend`。**パラメーター**:
- `armature`（省くと、VRM 1.0 の人型の骨の割り当てのあるアーマチュアが 1 つのときそれ）、`frames`（[最初のコマ, 最後のコマ]。省くとシーンの全コマ）。
- `cloth`（既定 true）: false で布を見ない（体が布を突き抜けるを測らない。報告の `skipped` に残る）。
- 布の面はコマごとに変形のあとのメッシュから作る（頂点を足し引きする変形でも合う）。見ている体のメッシュの頂点の数がそのコマで変わる（頂点を
  足し引きする変形）と、頂点の番号が合わないので、そのメッシュはそのコマだけ見ない（報告の `skipped` に、メッシュとコマの区間）。
- `skip_bones`: 飛びと震えを見ない骨の名前の型（fnmatch。例 `["*twist*", "*_end"]`。ねじれを分ける補助の骨や、骨の先の印の骨）。既定は無し。
- `foot_lowest`: 足が床より下を測る点。`world`（既定。世界の z がいちばん小さい頂点）か `object`（物体の中の z がいちばん小さい頂点。試作と同じ）。
  - **試作の測り方は誤りだった**（2026-11-18 に移す時に見つけた）。体のメッシュが物体ごと X のまわりに 90° 回っている（Y を上にして作ったメッシュを立てた）と、
    物体の中の z は前後の向きになり、選ばれるのは、かかとの後ろの点になる（初期姿勢で床から 3 作目は 0.2〜0.4 cm、2 作目は 4.3 cm 上）。
  - ユウが「正しい測り方を既定」に決めた（2026-11-18）。**これまでの決まり（試作との照合を崩す直しは、既定を試作どおりに残してオプションで足す）とは
    逆の向き**で、わけは、試作の測り方が「いちばん低い点」という試作自身の説明とも合わない誤りだったため。試作との照合は `object` で取った。
- `max_images`（既定 30）: 証拠の絵を描く出来事の数（下の「証拠の絵と読む用の表」）。
- しきい値（下の表。どれも仮の値）、`report_only`、`blender`・`addons`（既定 VRM Add-on）・`blender_extensions`・`timeout`。知らないパラメーターは止める。

**しきい値の既定は仮の値**で、コードで作った試しの動きと、2 作目・3 作目の VRM のダンスで、試作の時に絵を見ながら決めた（ユウ 2026-11-18「公開の既定にする」）。
ユウと線を合わせる作業は当面しない。体の大きさで変わる値（腕の太さ、cm のしきい値）を、身長で直していない。

| パラメーター | 既定 | 中身 |
|---|---|---|
| `arm_upper`・`arm_lower` | [[0.55, 0.042], [0.80, 0.042], [1.00, 0.040]]・[[0.33, 0.036], [0.66, 0.033], [1.00, 0.030]] | 腕の上の点 [付け根からの割合, 太さの半分 m]。3 点ずつで、最後の点（u = 1）がひじ・手首 |
| `fist` | [0.075, 0.032] | 手首からこぶしの中心まで、こぶしの太さの半分（m） |
| `penetration_min_m`・`contact_min_m` | 0.03・0.01 | 挙げ始める深さ（腕と頭・腕と脚・腕どうし）、数え始める深さ |
| `arm_body_min_m` | 二の腕・ひじ 0.08、前腕 0.04、手首・こぶし 0.03 | 腕と胴の、挙げ始める深さ（鍵は `upper_arm`・`elbow`・`forearm`・`wrist`・`fist`。一部だけ渡せる） |
| `leg_overlap_min_m`・`floor_min_m`・`knee_back_min_m` | 0.07・0.015・0.025 | 脚どうし、床より下、ひざの逆曲がり（m） |
| `torso_weight_min` | 0.85 | 胴の形にする頂点の、胴の骨の重みの割合 |
| `wrist_bend_max_deg`・`wrist_twist_max_deg`・`neck_max_deg` | 75・120・70 | 関節の角度（度） |
| `pop_min_deg`・`pop_ratio`・`pop_window` | 30・3・6 | 向きの飛び（人型の骨） |
| `spring_pop_min_deg`・`spring_pop_ratio` | 45・4 | 向きの飛び（揺れものの骨） |
| `cut_share`・`cut_bone_deg` | 0.5・25 | 全身の切り替え |
| `swing_m_per_frame`・`pos_pop_min_m` | 0.06・0.03 | 腕ごと速く振っているコマ、位置の飛び（m） |
| `jitter_window`・`jitter_reversals`・`jitter_min_deg` | 8・5・2 | 震え |
| `slide_min_m_per_frame`・`slide_max_m_per_frame`・`slide_frames`・`plant_height_m`・`plant_dz_m` | 0.008・0.04・3・0.015・0.004 | 着いた足の滑り（床に着いている = 足の低い骨の付け根が初期姿勢の高さ + `plant_height_m` 以下で、上下の動きが `plant_dz_m` 以下） |
| `cloth_depth_min_m`・`cloth_min_vertices`・`cloth_near_m` | 0.008・22・0.06 | 体が布を突き抜ける（深さ m、頂点の数、初期姿勢で布から見る範囲 m） |
| `merge_gap_frames` | 2 | 出来事にまとめる、すき間のコマ数 |

**出力** `report`（`model3d-animation-check/0.1`）: `overall`（`review`＝出来事がある／`clean_with_skips`／`clean`。どれも合否ではない）・`frames`・`fps`・
使ったしきい値・`skip_bones`・`foot_lowest`・飛びを見た骨の数（`bones`）・体と布と髪のメッシュ・布（`cloth`。測ったか・見た体の頂点の数・
初期姿勢でもう布の外にあった頂点の数）・胴の点と輪切りの数・頭の三角形の数・脚の太さ（`leg_radius_m`。推定にした脚は `leg_radius_estimated`）・
`cuts`（切り替えのコマ）・`contact_frames`・`arm_torso_baseline_m`・信号ごとの数（`counts`）・見なかった検査（`skipped`。頭の面が無い、足の点が無い
（一部のコマだけなら、その区間を `frames` に）、布が無い・`cloth: false`・布の近くに体の頂点が無い）・
`events`（`id`・`category`・`signal`・`title`・`part`・`frames`・`seconds`・`peak_frame`・`peak_value`・`unit`・`kind`・`momentary`・`location`・
`normal`・`severity`・`evidence`・`status`。体が布を突き抜ける所は `vertices`＝頂点の数のいちばん多いコマの数）。Blender の書き出し（`sample.npz`・`sample.json`）は工程のフォルダに残る。
- 部位の名前（`part`）は、人型の骨の割り当てから作る読む用の言葉（「左の前腕 → 腰」など）。揺れものの骨は骨の名前のまま。
- シーンの `scale_length` が 1 でないとき、アーマチュアの物体に大きさがあるときは止める（m で測るため）。アーマチュアが原点にないときは、体の形を
  アーマチュアの中の座標で作る。
- 人型の骨の割り当て（VRM 1.0。VRM Add-on）が要る。腰・腹・頭と、左右の二の腕・前腕・手・太もも・すね・足が無ければ止める。

**証拠の絵と読む用の表**（出来事を絵で確かめる手がかり。どれも任意の出力）:
- `table`（.md）: 読む用の表。頭に、見つけた物と場所ごとのまとめ（`severity` の大きい順。件数・合わせて何コマ・いちばん大きい値・最初に出る秒）、
  続けて出来事の一覧（時間の順。秒・コマ・種類・いちばん大きい所・証拠の絵の名前）、終わりに見なかった検査。
- `sheet`（.jpg）: 証拠の絵の一覧（6 列・1 枚に 5 段まで。多ければ `<名前>_2.jpg` … に分け、報告の `sheets` に並べる）。字は ASCII だけ
  （番号・秒・コマ・信号・値。場所の言葉は表で見る）。`sheet` を挙げたときだけ、もう一度 Blender で証拠の絵を描く
  （`blender_adapter/animation_evidence.py`）。絵は報告と同じフォルダの `img/`（`e<番号>_f<コマ>.jpg`）で、報告の出来事の `images` に名前、
  `images_directory` にフォルダ。
- 描く出来事は、全身の切り替えを除いた `severity` の大きい順に `max_images` 件まで。いちばん大きいコマ（動的な出来事は前後のコマも）を、
  場所を狙うカメラから描く: 場所の外向き（`normal`。無ければ腰から場所へ）と体の前と少し上から、静的 0.95 m・動的 1.3 m 離れた所
  （高さ 0.35 m より下からは撮らない）。貫通（床を除く）は場所に赤い玉の印、床（z = 0）に板を置く。簡易描画（Workbench・平らな光・
  絵の色・輪郭）で 480 画素四方の JPEG。.blend の描く設定のうち絵をすり替えうるもの（シーケンサー・コンポジター・切り取り・縦横比・
  多視点・透ける背景・ぶれ・色の補正・表示の装置・出力の形式）はその場で決まった値にし（`render.face_sheet` と同じ一覧。変えたものは報告の
  `render_changes`）、画像に .blend のパスや日時などの控えを書かない。.blend は保存しない。描き方の値（離れる距離・高さ・印の大きさ・色・
  絵の大きさと品質）は仮の値（ユウの事前の指示で、親が推奨で決めた。2026-11-18）。
- `render.part_colors`（色分けの診断の絵）では置き換えなかった。出来事ごとに場所を狙うカメラと印が要るため（`render.part_colors` のカメラは、
  狙う点の方位と高さだけ）。

### 正解の分かっている試験の素材（`motion.inject_faults`）

`check.animation` が破綻を見つけるかは、決まったコマにわざと破綻を入れた動きで確かめる（`mv inspect demo`・`mv gate demo` と同じ役目）。
`motion.inject_faults` は、きれいな動きの .blend に破綻を入れて別の .blend に書き、入れた物の一覧（`expected`）を書く。
`check.animation` の入力 `expected` に渡すと、報告の `expected`（破綻ごとに見つけた出来事の id・見つけた数・入れていない所で挙がった出来事）と、
出力 `expected_table`（照らし合わせの表。Markdown）に出す。

- パラメーター `faults`（上から順に入れる）。破綻ごとに:
  - `name`（重ならない名前）、`bone`（骨の名前）か `role`（人型の骨の役割。`left_hand` など）、`frames`（[最初のコマ, 最後のコマ]）
  - `kind`: `local`（骨の今の回りに、骨の中の軸 `axis` [x, y, z] のまわりの回りを足す）・`world`（骨を付け根のまわりに、世界の軸 `axis`
    （"X"・"Y"・"Z"）で回す。子も一緒に動く）・`shift`（骨の付け根を、世界の向きの `delta` [x, y, z] m だけ動かす）。`local`・`world` は `degrees`（度）
  - `shape`: `ramp`（既定。最初と最後の `ramp` コマ（既定 3）でなめらかに 0 から入って 0 へ戻る）・`constant`・`alternate`（1 コマおきに + と −。
    奇数のコマが +）
  - `expect`: `signals`（見つかるはずの信号）と `part`（場所の言葉に含むはずの言葉。省くと場所は見ない）
- 入れたコマごとに、触った骨の回り（`rotation_quaternion`）のキーを打つ（`shift` は位置も）。骨の回りの持ち方は QUATERNION のこと。
  シーンのコマの範囲の外に置いた破綻は入らない（要確認にする）。入れている間はメッシュを評価しない（速い）。
- 破綻を入れる前に、触る骨の元の値（回り・位置・大きさ）を読んでおき、入れるコマではその値から始める（前のコマで打ったキーが混ざらない。
  キーの少ない動きや、元はキーの無い位置でも、量が積み重ならない）。
- 区間の外は、キーを打つ値ごとに元の値のキーで留める（区間の外へ破綻が伸びない）: 元のキーがあれば、区間の前は前の元のキーから、後ろは次の
  元のキーまで全部のコマ（間引いたキーの間の形を変えない）。元のキーが無ければ前後 1 コマ。ほかの破綻の区間の中のコマには打たない
  （報告の `boundary_frames`）。`world` は骨の行列を入れるので位置と大きさに小さなずれが出るが、キーを打たない位置と大きさは終わりに元の値へ
  戻す（キーが無いと全部のコマに効くため）。
- `world` の軸と `shift` の向きは世界の座標。アーマチュアの物体が回っていれば、その向きをアーマチュアの中へ直し、`shift` は親の今の姿勢を
  通して骨の位置へ直す（アーマチュアが回っていない・親の無い骨では、試作と同じ式）。
- 見つけたかの決め方（試作と同じ）: 信号が `signals` のどれかで、コマが前後 1 コマの余裕で重なり、場所の言葉に `part` のどれかを含む出来事。
  入れた破綻の巻き添え（振った腕が脚に入る、など）や、きれいな動きにもともとある物は「入れていない所で挙がった出来事」に並ぶ。

**試作で誤って多く挙げた例**（どれも絵で見て分かった。上の決めごとのもと）: 3 作目の VRM のダンスに最初にかけたときは 380 件挙がり、大半は
「腕を下ろしただけで胸に 10 cm 入る」という誤りだった（描かない身代わりのメッシュを体に数えた・その体で避けられない重なりを差し引かなかった・
袖に隠れる二の腕とひじを手首と同じ深さで挙げた）。ほかに、足が床より下を関節の高さで測る（厚底の靴で外す）、低く速い踏み出しを滑りに数える、
前腕の骨の軸まわりの裏返しを「飛び」「ねじれ 180°」と出す（親から見た回りで測った。2 作目）、閉じた・交差した太ももを重なりに出す（2 作目）、
帯の結び目の小さな沈みを 15 回出す（2 作目）。移すときに、足の低い点を物体の中の z で選んでいた誤りも見つけた（上の `foot_lowest`）。

**確かめたこと**:
- CI（Blender なし）では、合成の人形（胴の筒・頭の球・脚の筒・足の箱）に決まったコマで入れた破綻（手首を 95° 曲げる、垂らした腕を体の中へ振る、
  手を 1 コマだけ 120° ねじる、腰を 5 cm 下げる、頭を 1 コマおきに ±5° 揺らす、脚を内へ振って重ねる、前腕を 1 コマだけ 40° 振る、ひざの逆曲がり、
  首の曲げすぎ、全身の切り替え、足の滑り、頭に手を入れる）を、入れたコマと場所で見つけること、きれいな動きでは何も挙げないこと、決めごと
  （差し引く重なり・太ももどうし・手首のねじれの測り方・親の飛びの巻き添え・揺れものの骨・速い踏み出し・布の小さい所）、Blender の中の布の光線の
  計算（代役の値と平らな布で、骨の軸へ向けた光線・初期姿勢でもう布の外にある頂点・(骨, 布) ごとのまとめ）、工程の受け渡し（代役の Blender）、
  悪い設定を止めることを確かめた。わざと入れる破綻の量の形（なめらかに入る・ずっと同じ・1 コマおき）と、照らし合わせの決め方・表、
  `motion.inject_faults` の受け渡しと悪い破綻の指定を止めることも確かめた。
- 2026-11-18 に Windows の Blender 5.2.4（VRM Add-on 4.10.5）で、試作と同じ 4 つの入力（コードで作った試しの動き 354 コマ（きれいな動きと、
  わざと 9 つの破綻を入れた動き）、3 作目の VRM のダンス 954 コマ、2 作目の VRM のダンス 720 コマ）にかけ、試作の報告と比べた（実測。
  `foot_lowest: object` と、試作が名前で外していた骨（骨の先の印・ねじれを分ける補助の骨）を `skip_bones` に渡して）:
  - 出来事（28・8・107・41 件）が、3 つの入力で全部の欄（コマ・秒・値・位置・向き・`severity`・布の頂点の数）まで同じ。3 作目は 107 件のうち 1 件だけ、手首の曲げすぎの値の丸めが違った（試作 79.7°、こちら 79.8°。丸める前は 79.7502°。試作は Blender の単精度で計算していて、丸めの境 79.75 の下に出た）。体が布を突き抜ける出来事（わざと破綻を入れた動き 5 件・きれいな動き 4 件・3 作目 15 件。2 作目は 0 件）は、頂点の数まで同じ。
  - 信号ごとの数・切り替えのコマ・浅い接触のコマ数・差し引く重なり・飛びを見た骨の数も同じ。
  - 既定（`foot_lowest: world`）では、床より 1.5 cm 以上下のコマが 3 作目で 328 → 615 コマ（最大 4.9 → 5.6 cm）、2 作目で 56 → 542 コマ
    （最大 3.7 → 7.2 cm）になった。正しい測り方だけで挙がるコマを、床の高さから横と前の 2 つの向きで描いて、3 作目 5 コマ・2 作目 5 コマ
    （そのうち正しい測り方だけで挙がるコマは 4 コマずつ）のどれも、靴の底が床より下（3 作目は 2〜6 cm、2 作目は 4〜7 cm）にあるのを絵で確かめた。
    2 つの作品は完成したもので、ここで挙がった物を「直すべき」と言っているのではない（目に見える破綻かは、ユウと合わせていない）。
  - 証拠の絵（`sheet` を挙げて。`max_images` 30）: 4 つの入力で、同じ出来事に同じコマの絵が付き（50・14・30・36 枚）、130 枚のうち 129 枚は試作の絵と画素まで同じ。3 作目の 1 枚だけ、8×8 の 1 区画（58 画素、いちばん大きい差 32/255）が違った（簡易描画の描くたびの揺れと同じ形。[色分けの診断の絵](#色分けの診断の絵renderpart_colors)）。読む用の表は、調べた物のパスの行と、終わりに足した節を除いて試作の表と同じ（3 作目は上の丸めの 1 行だけ違う）。試作の絵には .blend のパスや日時が控えとして入っていた（こちらは書かない）。
  - `motion.inject_faults` で、試作が入れたのと同じ 9 つの破綻をきれいな動きに入れ直すと、試作の試験の素材と `check.blend_match`（全部のコマの骨の姿勢）で同じ（`exact`）、入れた物の一覧も同じ。それを `check.animation`（照合のレシピのとおり `foot_lowest: object`）にかけると、9 つとも入れたコマと場所で見つけ、入れていない所で挙がった出来事は 16 件（試作と同じ）、照らし合わせの表は試作の表と同じ（行末の形を除く）。
    その後の直し（元の値の読んでおき・区間の外の留め方・位置と大きさのずれを戻す）のあとは、試作の素材と `check.blend_match` で骨の姿勢の差が
    最大 1.5e-6 になった（試作は `world` で行列を入れたときの位置と大きさのずれを、キーの無い値として全部のコマに残していた。こちらは戻す）。
    破綻の区間の外の 266 コマは、きれいな動きと 1 ビットまで同じ。`check.animation` は 9 つとも見つけ、入れていない所の出来事も同じ。出来事
    28 件のうち 1 件（左の手首の曲げすぎ）だけ、いちばん大きいコマが 14 → 10（同じ 95° の平らな所で、ずれの分だけ並びが入れ替わった）。
  - 2026-11-18 に Windows の Blender 5.2.4 で、きれいな動きの写し 2 つで確かめた（実測）: 腰の位置のキーを 30 コマおきに間引いた写しに、腰を
    下げる破綻を入れると、区間の中は量が積み重ならず（ずれ 3.8e-8 以下）、区間の外（85〜130 コマ目）はきれいな写しと 1 ビットまで同じ。
    アーマチュアを Z のまわりに 90° 回した写しに、世界の X のまわりに 30° 回す破綻と、子の骨（左手）を世界の X へ 3 cm 動かす破綻を入れると、
    どちらも世界の座標で決めたとおり（ずれ 9e-8 以下）で、区間の外は同じ。
