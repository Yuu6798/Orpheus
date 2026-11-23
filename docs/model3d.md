# model3d（自作の 3D モデリングツール・Orpheus の外部ツール）

キャラクター改訂の確認は [立ち絵・修正前後の6方向比較と配布GLBの読み戻し](character-review.md) を参照。

成熟度は操作ごとに、実制作検証済みと実験的を分ける。2 作目・3 作目の VRM のダンスなどから持ち帰った
現行実装の照合範囲は [機能の成熟度](maturity.md) と `orpheus show model3d.OPERATION` で確認する。

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
- **設定形式**: データ契約（下記）は `tools/model3d/src/model3d/schemas/`（`contracts/` の外）で版を管理する。操作の実制作確認とは別に、`contracts/` への昇格はユーザー承認が要る。
- 合成素材（`model3d demo`）と JSCAD の原本出力との照合に加え、非公開の既存制作 worker を
  `model3d dance-recipe` で接続する。[実素材の再構築・描画と検証範囲](model3d-dance.md)を参照。
  汎用 Blender 観測・本物の生成器・別メッシュへのリギングは、それぞれ別の検証範囲として扱う。

設計の元は 2026-11-03 の設計たたき台 v0.3（ユーザーのクラウド）。その後の v0.5 とも照合し、別試作の実績とこの実装の実績を分けている
（[実行確認と残作業](model3d-review.md)）。実装単位は `core`・`ops`・`adapters`・`blender_adapter`・`rea_bridge`・
`presets`・`validation`（`checks.py`）。M0〜M1 の土台（資産一覧・SemanticMap・レシピ実行器・検査・記録）は初期監査時の位置づけである。
その後、VRM・動作・布・描画・確認の部品を実制作から持ち帰った。現在の実装と実素材の確認範囲は各操作の節と成熟度の記録を正とする。
当初の M3（30 秒の検査・観測接続・候補比較・再開）や、任意の別メッシュへの適用が一括で完了したとは扱わない。

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
  - 工程の実行中も、その工程の recipe の入力を 2 秒おきに見張る（大きさと更新時刻。変わったら sha256 で確かめる）。中身が変わったか消えたら、すぐ `run.log`・画面・記録の `warnings` に「この工程は、終わっても失敗になります」と知らせる（入力ごとに 1 回。触っただけで中身が同じなら知らせない）。
    止めはしない: 外のプロセス（Blender など）は始めに入力を読むことが多く、描いた絵は書き換える前の入力の正しい絵のことがあるため。終わったあとの確かめは今のまま（失敗にして、索引に載せない）。失敗の文に、出力が残っている工程のフォルダを書く。
  - 4 作目の 3D のダンスで分かったこと: `render.show` で 1 時間ほど描いている間にフックを書き換え、描き終えてから失敗になって、後ろの動画の工程が回らなかった（絵は書き換える前のフックの正しい絵だった）。描いている間は、入力（フック・カメラ・光・.blend）に触らない。新しい仕掛けは別のファイルに書き、試し用のフックの写しで試す。
  - 入力として宣言していないファイル（フックが読む隣のファイルなど）の書き換えは、見張りでも見つからない（`render.show` の値は入力 `hook_config` にできる）。
  - 確かめたこと: CI では、外のプロセスの工程が自分の入力を書き換えると、工程が終わる前に `run.log` に知らせが出ること、触っただけでは出ないこと、入力ごとに 1 回・消えた入力も知らせること、終わったあとは今までどおり失敗で索引に載らないことを確かめた。2026-11-23 に Windows の Blender 5.2.4 の `render.show`（合成の場面・8 コマ）で、始めて 8.0 秒にフックを書き換えると、9.0 秒に知らせが出て、描き終えた 17.5 秒に工程が失敗になった（8 コマは工程のフォルダに残った）。
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
| `collision.cuff_pushout` | blend → blend, report | 袖と袖口のフリルを、相手の腕の、骨に沿った閉じた筒の外へ押し出す補正（Shrinkwrap の Outside と頂点ウェイトの近接）を .blend に付ける（[袖口を相手の腕の外へ押し出す](#袖口を相手の腕の外へ押し出すcollisioncuff_pushout)）。腕は動かさない。Blender が要り、再利用しない（毎回実行） |
| `check.pushout_moves` | blend → report | 押し出しの補正が、頂点を `limit_mm` より遠くへ動かしていないかをコマごとに調べる（[押し出しの動きを測る](#押し出しの動きを測るcheckpushout_moves)）。超えたコマがあれば不合格。Blender が要り、再利用しない（毎回実行） |
| `check.penetration` | mesh, collider → report | 中にある頂点の数・番号・深さ（工程の `select` があればその面の頂点だけを調べ、数を `checked` に残す。mesh と collider の単位か軸の約束が違えば止まる。`directions` は 1 本以上の有限で長さ 0 でない向き。奇数本を勧める）。交わりを調べる三角形は格子で絞る（`search: grid`。`all` で総当たり。答えは同じ）。脚がまるごと布を通り抜ける失敗は数に出ない（下の「限界」） |
| `check.continuity` | track → report | コマごとの点の列で前のコマからの移動が `max_jump` 以下 |
| `check.uv_stretch` | mesh [, texture] → report | uv の伸び（三角形の、画素での周長 / 3D の周長を中央値で割った値）を測る（[部品の一覧と uv の伸び](#部品の一覧と-uv-の伸び)）。百分位・`threshold`（既定 8）を超える三角形・3D で潰れた三角形・uv の幅（画素）。テクスチャの大きさは入力 texture の画像か `texture_size`。しきい値（`max_over`・`max_degenerate`）を渡したときだけ合否を出す |
| `check.frames` | [left, right] → report [, view] | 2 つのコマの並び（描いたコマの一覧か、`left_dir`・`right_dir` のフォルダ）を、同じ T どうしで画素で比べる（[コマのフォルダを比べる](#コマのフォルダを比べる)）。同じコマの数・違う画素・いちばん大きい差・色の差の平均・透けの違い・差の大きさごとの画素・違いの大きいコマ。左を基準に、右に無いコマと大きさの違うコマも並べる。`view` で [左 \| 右 \| 差] の PNG。`zone` で、変わった画素を数える範囲の内と外に分けて数える（外が 0 でなければはみ出し）。しきい値を渡したときだけ合否を出す。CLI は `model3d frames compare` |
| `check.track_match` | left, right → report | 2 つの台本の値（`model3d-cameras/0.1` どうしか `model3d-stage-track/0.1` どうし）を、同じ T どうしで比べる（[台本の値を比べる](#台本の値を比べる変えたコマだけ描き直す)）。同じコマの範囲（前の版のコマを写せる）と、描き直すコマの範囲と T の並び（`only` に渡せる）、欄ごとの差の最大。測るだけで、写すかは人が決める |
| `check.blend_match` | left, right → report | 2 つの .blend を比べる（[.blend どうしを比べる](#blend-どうしを比べる)）。物体の有無・シーン・メッシュごとの頂点・面のつなぎ・uv・属性・置き場所、骨の名前・静止・姿勢、`evaluate` なら変形した世界の形。Blender で中身を書き出し、numpy で比べる。全部が同じかを `exact` に、違いの大きさを `max_*` に出す。`tolerance` を渡せばその中かで合否。Blender が要り、再利用しない |
| `check.motion` | points → report | `motion.prepare_keypoints` の points の動きを数で測る（[整えた点の動きを測る](#整えた点の動きを測る)）。膝とつま先の食い違い・接地中の足の滑り・床へのめり込み・1 コマの移動・骨の長さのぶれ。しきい値を渡したときだけ合否を出す。知らないパラメーターは止める |
| `check.animation` | blend [, expected] → report [, table, sheet, expected_table] | 動きを付けた .blend（VRM の人型の骨。骨へ載せ、揺れものを焼き、手直しをした後）を全コマ調べて、破綻の候補を出来事（信号・場所・コマと秒・いちばん大きい値と位置）にまとめる（[動きを検品する](#動きを検品するcheckanimation)）。静的（腕が胴・頭・脚に入る、腕どうし・脚どうし、体が布を突き抜ける、足が床より下、手首・首・ひざ）と動的（向きの飛び、位置の飛び、震え、着いた足の滑り、全身の切り替え）を 1 本の報告に。体の形はそのモデルの初期姿勢のメッシュから作る。table は読む用の表（Markdown）、sheet は証拠の絵（場所を狙うカメラと赤い印）の一覧。expected（`motion.inject_faults` の、わざと入れた破綻）があれば見つけたかを照らし合わせる。直さない・採点しない（出来事があれば needs_review）。しきい値の既定は仮の値。Blender（VRM Add-on）が要り、再利用しない（毎回実行） |
| `motion.inject_faults` | blend → blend, expected [, report] | きれいな動きの .blend に、決まったコマでわざと破綻を入れて別の .blend に書き、入れた物の一覧（expected）を書く（[正解の分かっている試験の素材](#正解の分かっている試験の素材motioninject_faults)）。`check.animation` が見つけるかを確かめるための素材。入力の .blend は変えない。Blender（VRM Add-on）が要り、再利用しない（毎回実行） |
| `check.motion_amount` | path → report | 区間ごとの動きの量（[区間ごとの動きの量](#区間ごとの動きの量checkmotion_amount)）。骨の通り道から、区間（秒か拍）ごとに、腰・両手・両足などの速さ、頭の上下の幅、体の向きの幅と回った量、拍の格子があれば止めの拍（遅い拍が続いた所）を数で出す。動きの少ない区間を、カメラと光を決める前に見つけるため。数を出すだけで、どの区間が弱いかは決めない |
| `plan.motion_marks` | path → marks | 出来事の時刻の候補（[出来事の時刻の候補を拾う](#出来事の時刻の候補を拾うplanmotion_marks)）。骨の通り道から足の着地・点を向きに写した値の山と谷を、曲の打音の秒から届く最初のコマを出す。コマの番号は出来事の表に `frame` でそのまま書ける。拾うだけ |
| `check.on_screen` | cameras [, path] → report | 世界の点（座標か骨の点）が、カメラの控えのコマの画面のどこに映るか（[画面のどこに映るか](#画面のどこに映るかcheckon_screen)）。`margin` の内側に無い点とカメラの後ろの点を数える（縦長の画面で、出来事を置く前に） |
| `motion.bone_offset` | blend → offsets | メッシュの決まった所（箱の中でいちばん高い頂点など）を、決めた姿勢で骨の軸の位置（m）に直す（[骨の軸の位置を測る](#骨の軸の位置を測るmotionbone_offset)）。`motion.bone_path` の `points` にそのまま書ける（Blender） |
| `check.jitter` | blend [, track] → report | 布などのメッシュ（`objects`）のちらつきを測る（[ちらつきを測る](#ちらつきを測るcheckjitter)）。頂点を骨から見た座標で読み、前後のコマの中間からのずれの平均・99 百分位・最大と、`jump_mm` を超えた割合、ずれの大きいコマと頂点。`toggle` で変形を 1 つずつ切って測り直す。しきい値を渡したときだけ合否を出す。Blender が要り、再利用しない（毎回実行） |
| `check.mouth` | mesh [, texture] → report [, front, section] | 頭の候補の口の作りを数と絵で調べる（[頭の候補の口の作りを調べる](#頭の候補の口の作りを調べるcheckmouth)）。上下の唇が別の面か（口の幅の区間ごとに、見える面のすぐ裏の浅い所だけで唇の外の面の上から下へつながるか＝くっついている所）、3 枚以上の面が集まる辺と穴の縁、口の中の面と部品と深さ、色の絵があれば口の中と肌の色の差。正面の色分けと真ん中の断面の絵。合否は決めない。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.illustration` | image → report [, view] | 立ち絵（正面）か T ポーズの体の絵の、人物の比率を測る（[立ち絵と体の絵の比率を測る](#立ち絵と体の絵の比率を測るcheckillustration)）。人物の画素は、背景の色（既定は緑）でない画素か透けていない画素。`rows` の行ごとの胴・腕・脚の幅（身長に対する割合）と頭身、または `tpose` で床から腕までの高さを物差しにしたくびれ。`view` は確認用の絵。合否は出さない。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.body_image` | reference, body → report [, overlay] | 体の絵を元絵と同じ物差し（足の裏からウエストまでの高さ U）で比べる（[体の絵と顔の絵を元絵と比べる](#体の絵と顔の絵を元絵と比べるcheckbody_imagecheckface_image)）。ウエスト・太もも・ひざ・ふくらはぎ・足首の幅と裾などの高さ、体の絵 ÷ 元絵の表。`overlay` は輪郭を重ねた絵。合否は出さない。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.face_image` | reference, head → report [, sheet, zoom] | 頭の絵の顔を元絵の顔と、両目の位置で合わせて並べる（同じ節）。両目は瞳の色の決まり `iris` のかたまりの組か手で。目の間隔をそろえ、目から下に目盛りを引いた絵。合否は出さない。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.three_view` | front, side, back → report [, sheet, heads] | 三面図の同じ部位の高さがそろっているかを測る（[三面図の高さを比べる](#三面図の高さを比べる頭の絵の目から下を詰めるcheckthree_viewtextureface_length)）。いちばん上と下・いちばん広い行・印の行（色の決まりの帯）を全高に対する割合で、`iris` で頭のあたりのかたまり。並べた絵と頭の拡大の絵。合否は出さない。numpy と Pillow だけで、同じ入力なら再利用する |
| `texture.face_length` | image → image [, report] | 頭の絵（透けつきの PNG）の目から下の帯だけを、描き直さずに縦に `k` 倍にする（同じ節）。帯の両端では変えず、帯より上の見える画素は変えない（透けが 0 の画素の色は、既定では 0 になる。`above_band: copy` で元のまま）。Lanczos4（OpenCV の remap と同じ計算）。numpy と Pillow だけで、同じ入力なら再利用する |
| `check.candidate` | model → report [, views, check, mesh] | 骨の無い 3D の候補（FBX・glTF・OBJ）を Blender で読み込み、体は床から腕の先までの高さを物差しに、切り口を面のつながりで輪にたどって、くびれ・脚の幅と厚みを測る（[骨の無い 3D の候補を測る](#骨の無い-3d-の候補を測るcheckcandidate)）。粘土の見た目で数方向から描いた絵。合否は出さない。Blender が要り、再利用しない（毎回実行） |
| `check.assembly` | blend [, illustration] → report [, views, bust, compare] | 組み立てたモデル（体・頭・髪が別の物体の .blend）の頭身を、足の裏からくびれまでの高さ U を物差しに測り、元絵の比率（`reference`）か体の側の目印（`anchor`）から頭と髪の大きさと位置の提案を出して、メモリの中で動かして測り直す（[組み立ての頭身を測る](#組み立ての頭身を測るcheckassembly)）。今の配置と動かした後に目印の高さの線を引いた絵と、元絵と並べた絵。元絵の比率・目印・頭身に既定は無い。合否は出さない。Blender が要り、再利用しない（毎回実行） |
| `check.eyes` | mesh [, texture] → report [, front, parts, sections, top] | 頭の候補の目の作りを数と絵で調べる（[頭の候補の目の作りを調べる](#頭の候補の目の作りを調べるcheckeyes)）。窓に中心がある部品（辺でつながった島）の頂点と三角形の数・中心・範囲・色・絵の中の場所、目ごとの白目の扇かもしれない所（探す箱の肌の頂点のうち辺がいちばん多い頂点を中心にした三角形。輪の範囲・円すいの深さ・その範囲の部品）。正面・部品の色分け・縦と横の断面・上から見た断面（目の骨の軸も）の絵。合否は決めない。numpy と Pillow だけで、同じ入力なら再利用する |
| `model.eye_rig` | blend, geometry → blend, report | 目の骨の根もとを目の球の中心へ移し、瞳の頂点の重みを目の骨だけに付ける（[目を動かせる作りにする](#目を動かせる作りにする)）。geometry は目の形の値（`model3d-eye-geometry/0.1`）。入力の .blend は変えない。Blender（VRM Add-on）が要り、再利用しない（毎回実行） |
| `model.eye_keys` | blend, geometry → blend, report | まばたきのキーを直し（上まぶたを下まぶたのふちの下まで届かせ、下りてきたまぶたを目の板より手前に置く）、目の新しい形状キー（瞳が縮む・横へ伸びる・つぶれる・見開き。左右別）を足す（[目を動かせる作りにする](#目を動かせる作りにする)）。量と形に既定は無い。入力の .blend は変えない。Blender が要り、再利用しない（毎回実行） |
| `vrm.look_at` | blend, geometry → blend, report | VRM の視線を目の骨を回す作り（bone）にし、回す角度の範囲を、視線がいちばん端のときに瞳が目の板の上を滑る量（`slide`）と目の球の半径から決める（[目を動かせる作りにする](#目を動かせる作りにする)）。`slide` と `input_max` に既定は無い。入力の .blend は変えない。Blender（VRM Add-on）が要り、再利用しない（毎回実行） |
| `render.eye_sheet` | blend, geometry → sheet, report | 目を動かせる作りにした頭を、姿勢（視線＝瞳が滑る量と形状キーの値）× 見る向きで描いて並べる確かめの絵（[目を動かせる作りにする](#目を動かせる作りにする)）。`paint` で目の板と瞳を塗れば、顔の面からのはみ出しを見られる。姿勢と向きに既定は無い。.blend は保存しない。Blender（VRM Add-on）が要り、再利用しない（毎回実行） |
| `motion.eye_plan` | blend, geometry, plan → blend, timeline, report | 目の動きの台本（`model3d-eye-plan/0.1`。視線の飛び・追いかける目・まばたき・驚き・笑顔の時刻と量）を、コマごとの値にして .blend の動きのキー（頭の形状キーの値と目の骨の回転）にする（[目の動きの台本](#目の動きの台本motioneye_planrendereye_captions)）。台本の値に既定は無い。入力の .blend は変えない。Blender（VRM Add-on）が要り、再利用しない（毎回実行） |
| `render.eye_captions` | timeline → report | 描いたコマに、台本の区間の説明の字を入れて別のフォルダへ書く（下に半ば透けた黒い帯と説明、左上に題。Pillow）。元のコマは書き換えない。再利用しない（毎回実行） |
| `texture.extract_glb` | glb → image [, report] | GLB（glTF 2.0 のバイナリ）から、材質のテクスチャの画像を取り出す（[GLB からテクスチャを取り出す](#glb-からテクスチャを取り出す)）。`slot`（既定 baseColor）と `material`（省くと全部の材質が同じ画像のときだけ）。既定は埋め込まれたバイトをそのまま書き、`decode: true` で出力の拡張子の形式に書き直す。外のファイル・拡張にしか無い画像・壊れた GLB は止める |
| `texture.apply_to_blend` | blend, texture → blend, report | .blend の材質の画像ノードのうち、`match` に合う画像を差し替えて詰め込み、別の .blend に書く。MToon などと共有する画像の参照を保つ指定もある（[.blend の画像を差し替える](#blend-の画像を差し替える)）。色空間と大きさがそろうこと・詰め込んだバイトがテクスチャと同じこと・合わない画像ノードと入力が変わらないことを確かめる。Blender が要り、再利用しない（毎回実行） |
| `texture.view_parts` | parts → occlusion [, preview, report] | 部品ごとの npz の部品を視点の正射影で描き、画素ごとにいちばん手前の部品の地図を作る（[手前の部品の地図と投影の遮り](#手前の部品の地図と投影の遮り)）。occlusion は `owner`・`depth`・`names`・`view` の npz で、`texture.project_reference` の入力になる。preview は部品ごとの色、report は部品ごとの三角形と手前の画素の数 |
| `texture.closed_eye_line` | image → image [, report, mask] | 目を閉じた顔の絵の、閉じた目の線を 2D の描き方（下にたわんだ太い弧・目尻のはね・下向きのまつ毛）で描き直す（[表情](#表情頭の絵の差し替えと閉じ目の形modelclosed_eye_key)）。消す帯の中の元の線を肌の色で埋めてから描く。上へたわむ弧（負の `sag`）、点の列に沿って太さの変わる線（`strokes`。しわ・涙袋・半目の縁）、目の枠を肌らしい画素から埋める消し方（`skin_fill`）、写す範囲の `mask` も。numpy と Pillow だけ |
| `check.drawn_mark` | reference, image → report | 描いた印（口など）のずれを、目で読まずに色で測る（[表情](#表情頭の絵の差し替えと閉じ目の形modelclosed_eye_key)）。もとの絵と描いた絵の印の色のいちばん大きいかたまりの、中心の横のずれ・上の縁の縦のずれ・幅の比（mm も）と、許す枠の外で変わった画素の数。しきい値を渡したときだけ合否を出す |
| `texture.warp_mouth` | image → image, mask [, report] | 顔が絵のモデルの正面の描画の上で、口の表情を、もとの口の絵を変形して作る（[表情](#表情頭の絵の差し替えと閉じ目の形modelclosed_eye_key)）。もとの口の線を色で測り、横に伸び縮みさせ、線を境に上と下を別々に動かし、すき間を口の中の色と舌で埋める（口角の印の内側で 0）。あごの線の内側に収める。`mask` に写す範囲。numpy と Pillow だけ |
| `texture.mouth_inside` | mesh, texture → texture, report | 色の絵の、口の中の面（唇の裏の袋）と部品（歯・舌など）が使う所だけを塗る（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。頭のメッシュ（`model.export_mesh` の npz）を正面から描き、口のまわりの見えない三角形と部品の uv の画素を、外から見える面の画素を除いて塗る。口のまわりの箱と部品の見つけ方に既定は無い。numpy と Pillow だけで、同じ入力なら再利用する |
| `texture.render_view` | mesh, texture → image [, report] | uv のあるメッシュにテクスチャを貼り、座標軸に沿った正射影（`view`）で描く。照明なし（色の確認と目印を読むため） |
| `texture.draw_mask` | [like] → mask | 多角形・楕円（`angle` で傾ける）を `value` で順に塗った灰色の画像。大きさは `size` か入力 `like` の画像。`feather` で縁をぼかす。知らないパラメーターは止める |
| `texture.project_reference` | mesh, texture, reference [, mask, occlusion, keep] → texture [, weights, report, overlay] | 参照画像を、目印で合わせた視点からテクスチャの画素へ写す（[設定画をテクスチャへ写す](#設定画をテクスチャへ写す)）。工程の `select` で役割の面だけを塗る。`occlusion`（`texture.view_parts` の手前の部品の地図）を渡すと、ほかの部品に隠れた所へ写さない（[手前の部品の地図と投影の遮り](#手前の部品の地図と投影の遮り)）。`color_match` で参照画像の色をモデルの色に合わせてから写す（[投影の色合わせ](#投影の色合わせ)）。顔の絵を写すときの追加の設定（見え隠れを見ない `visibility`・手前の裏向きの面にも写す `two_sided_depth`・面の向きで弱めない範囲 `core`・縁をぼかした範囲 `regions`）。写す前に参照画像を升目の大きさでならす `prefilter`（`auto` か σ）。1 画素も写らないとき・任意の `review_note` があるときは `needs_review`。知らないパラメーターは止める |
| `motion.prepare_keypoints` | keypoints → points [, report] | SAM 3D Body の 70 点（npz の `pred_keypoints_3d` + `pred_cam_t`）を整える。1 コマの跳ねの除去、平滑化（奥行きは窓を広く）、床の水平化（`pitch_deg` で上書き可）、上体を起こす（`torso_back_deg`）、接地の区間で足を止める（`contact`・`step_split`）。points は x 右・y 奥・z 上・m・床 z=0 の npz（[推定した 3D の点を整える](#推定した-3d-の点を整える)）。知らないパラメーターは止める |
| `motion.edit_keypoints` | points → points [, report] | 整えた 70 点を区間ごとに手直しする（[点を手直しする](#点を手直しする)）。`edits` に順に並べる: `head_turn`（顔の向きを足す）・`fist`（片手を握りに）・`forearm_roll`（両の拳が胸の前で回り合う動きを作り直す）。`stance` と `focal` は引き継ぐ。知らないキーは止める |
| `motion.retarget` | points, character [, skip_vertices, head_dpitch] → blend, pose, report | 整えた 70 点を、Mixamo 式の骨のキャラ（.blend）へ Blender の中で移す（[点をキャラの骨へ移す](#点をキャラの骨へ移す)）。`resolve` で手・前腕の胴への刺さりを直す。Blender が要り、再利用しない（毎回実行） |
| `motion.join_clips` | 動きの .blend を何本でも（名前は自由）→ blend, report | 別々に骨へ載せた何本かの動きを、切り替えのコマの表つきで 1 本の並びに組む（[別々に骨へ載せた動きを 1 本に組む](#別々に骨へ載せた動きを-1-本に組む)）。区間ごとに使う本（`clip`）と、その本の 0 秒目を置く秒（`offset_s`）を決め、範囲の外は最初か最後の姿勢で止める。`align: mean`（既定）で本ごとに腰の位置の平均を原点へ。報告の `cuts` は切り替えのコマ。Blender が要り、再利用しない（毎回実行） |
| `motion.twist_split` | blend → blend, report | 骨へ移した動きの前腕のねじれを、肘と手首に半分ずつ持たせ直す（[前腕のねじれを分け直す](#前腕のねじれを分け直す)）。手の向き・前腕の向き・指先の位置が変わらないことを確かめ、許容を超えたら止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.ground_feet` | blend, points → blend, report | 接地のコマ（points の `stance`）に足の一番低い点が床に着くよう、腰の高さを足す（[足を床に着ける](#足を床に着ける)）。足の点は `foot_objects` か足の骨の重み。直した後に測り直し、腰を動かした量だけ足が動かなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `check.arm_overlap` | blend → report | 左右の腕（前腕・袖口・手・指）のメッシュが互いの中に入り込んでいるコマを、変形した後の形で調べる（[腕どうしの入り込みを測る](#腕どうしの入り込みを測るcheckarm_overlap)）。肌が肌に・袖が肌に・肌が袖に・袖が袖にの頂点の数と深さ、重なりのあるコマの数と区間。`max_frames` を渡したときだけ合否。Blender が要り、再利用しない（毎回実行） |
| `motion.separate_hands` | blend [, joined] → blend, report | 左右の腕・手・指が互いを通り抜けているコマで、上腕を肩のまわりに少し回して離す（[腕どうしを離す](#腕どうしを離すmotionseparate_hands)）。腕を太さのある棒の集まりとみなし、前腕の両方に垂直な向きへ区間ごとに離す。残った短い重なりはすれ違う向きと量を計算で探す。骨の名前は `bones` の型。Blender が要り、再利用しない（毎回実行） |
| `motion.push_hands` | blend → blend, report | 手が体（胴・腰まわり・脚）に沈むコマに、二の腕を肩のまわりに回して手を体の面から浮かせる（[手を体から浮かせる](#手を体から浮かせる)）。肘から先は変えない。手と体の点は骨の重み（体は `body_objects` でも）。`passes` 回くり返し、沈みを直す前・後で数える。Blender が要り、再利用しない（毎回実行） |
| `motion.spring_bones` | blend → blend, report | 鎖の骨（髪など。`chains` に根元の骨）を、ばねで体の動きに遅れて揺らし、回転のキーに焼き付ける（[鎖の骨をばねで揺らす](#鎖の骨をばねで揺らす)）。狙いは親に固く付いた形（`rest`。`follow` で骨と高さの並びに沿わせる）か、鎖にある動き（`animation`）。当たり（`colliders`）・鎖のつなぎ（`links`）。焼き付けた骨の向きと根元を確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.sleeve_bones` | blend → blend, report | 袖口の骨（`sleeves` の `bone`）を、関節（`follow`。前腕など）の曲げの `ratio` 倍に、減衰つきのばねで少し遅れて付いて行かせる（[袖口を曲げに遅れて付いて行かせる](#袖口を曲げに遅れて付いて行かせる)）。ねじれは入れない。焼き付けた向きを確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `motion.ribbon_bones` | blend → blend, report | 帯の垂れの鎖の骨（`chains`。長さは鎖ごとに違ってよい）を、重力で垂れて太ももの上を前へ乗り越えるように揺らす（[帯の垂れを揺らす](#帯の垂れを揺らす)）。狙いは `follow` に `thigh`（下ほど太もも）を混ぜられる。当たりは `colliders` と、メッシュの輪切りから作る `fitted`。押す向きは腰（`hips`）の前と上へ寄せる。焼き付けた骨を確かめ、合わなければ止まる。Blender が要り、再利用しない（毎回実行） |
| `cloth.skirt_proxy` | blend → blend, report | 脚に当たる裾を、裾の内側の面をなぞった目の粗い筒（身代わり）の布の計算で動かし、細かい裾は Surface Deform で筒に貼り付ける（[衣装を布で動かす](#衣装を布で動かす)）。当たりは体から脚の頂点グループの大きな島だけ。切れ目の延長・追加の切り込み・丈の切り落とし・助走のキー・床。既定の値は 2 作目の VRM のダンスで決めた値。Blender が要り、再利用しない（毎回実行） |
| `cloth.pushout` | blend → blend, report | 衣装（裾・垂れなど）が体に入った分を、体を輪切りにして作った骨盤と太ももの閉じた筒の外へ押し出す補正（Shrinkwrap の Outside）を付ける（[布を骨盤と太ももの筒の外へ押し出す](#布を骨盤と太ももの筒の外へ押し出すclothpushout)）。重なった布は層ごとの距離。初期姿勢でもぐった付け根は動かさない。Blender が要り、再利用しない（毎回実行） |
| `cloth.lining_gap` | blend → blend, report | 重なった布の裏地を、表から `lining_gap` まで内側へ離す（形を書き換える。モデル用。[裏地を表から離す](#裏地を表から離すclothlining_gap)）。Blender が要り、再利用しない（毎回実行） |
| `cloth.follow` | blend → blend, report | 上に重なる布（腰の垂れなど）を、下の布（裾など）の表面と同じ動きにする（骨の重みを、すぐ下の布のいちばん外の面の重みで置きかえる。[上に重なる布を下の布に付ける](#上に重なる布を下の布に付けるclothfollow)）。1 頂点の骨は `max_influences` 本まで。Blender が要り、再利用しない（毎回実行） |
| `check.cloth_over` | blend → report | 上に重なる布が、下の布の内側へ入っているコマを、外向きの線が下の布の面に当たるかで数える（[上の布が下の布の内側に入るコマを数える](#上の布が下の布の内側に入るコマを数えるcheckcloth_over)）。1 コマ平均・最大・コマの数。しきい値を渡したときだけ合否。Blender が要り、再利用しない（毎回実行） |
| `cloth.patch_lining` | blend → blend, report | 布の内側から見て、裏地の色（`lining_color`）でなく見える面のすぐ内側に、裏地の色の当て布（面）を足す（[裏から見える所に当て布を足す](#裏から見える所に当て布を足すclothpatch_lining)）。外から見えてしまう面は張らない。当て布の頂点に頂点グループ `lining_patch`。Blender が要り、再利用しない（毎回実行） |
| `check.cloth_layers` | blend → report | 重なった布の順番が入れ替わっているコマを数える（[布の層の順番を数える](#布の層の順番を数えるcheckcloth_layers)）。初期姿勢で覚えたすぐ外の層の面より、頂点が外へ出たら入れ替わり。当て布だけ・いちばん内だけ・補正を外しても数えられる。しきい値を渡したときだけ合否。Blender が要り、再利用しない（毎回実行） |
| `cloth.smooth` | blend → blend, report, proxy_cache [, cache] | 布を焼いた筒（と細かい裾）の動きを、骨から見た座標で時間方向にならし（1 コマだけの飛びを中央値で消してからガウス）、頂点の動きのファイル（.pc2。.blend と同じフォルダ）に置き換える（[布の動きをならして頂点の動きのファイルに置き換える](#布の動きをならして頂点の動きのファイルに置き換えるclothsmooth)）。ならす前後のずれを報告に出す。Blender が要り、再利用しない（毎回実行） |
| `model.body_shape` | blend → blend, report | 体つきを部位ごとに広げる（[体つきを部位ごとに広げる](#体つきを部位ごとに広げるmodelbody_shape)）。初期姿勢で、`limbs` の骨は骨の軸からの距離を骨に沿った位置ごとの倍率で、`torso` の骨は高さごとの中心線から幅と厚みを別々の倍率で広げ、ほかの骨の所は動かさない（頂点グループの重みで混ぜる）。`bands`（骨 1 本だけにほぼ付いた小さな島。腕輪など）は島ごとに 1 つの倍率、`band_shift` で腕輪を骨に沿って動かし、肌の継ぎ目も腕輪の下へ動かす。`follow_torso` の物体は胴と同じ広げ方。Blender が要り、再利用しない（毎回実行） |
| `check.body_size` | blend → report | 体の太さを初期姿勢で測る（同じ節）。腕・脚は骨に沿った位置ごとの直径（骨の軸に垂直な広がりの大きい 2 つの向き）、胴は高さごとの幅と厚み（cm）。測るだけで合否は出さない（`report_only` を付けると要確認にしない）。Blender が要り、再利用しない（毎回実行） |
| `model.twist_bones` | blend → blend, report | ねじれを分ける補助の骨を足す（[ねじれを分ける補助の骨](#ねじれを分ける補助の骨modeltwist_bones)）。重みを骨に沿った位置で分ける。既定の `aim` は Damped Track を使う。`parented_roll` を選ぶと、拘束が無い再生でも補助の骨が元の骨に付いて行く。`roll` で手のねじれを写す骨を足せる。Blender が要り、再利用しない（毎回実行） |
| `check.twist` | blend → report | 骨が自分の軸のまわりにどれだけねじれているかをコマごとに測る（同じ節）。骨ごとの最小・最大・`over_deg`（既定 60° と 90°）を超えたコマの数・大きいコマ。`max_twist_deg` を渡したときだけ合否を出す。Blender が要り、再利用しない（毎回実行） |
| `motion.bake_roll` | blend → blend, report | `model.twist_bones` の roll の骨の回転を、拘束に頼らず正確な値でキーに焼き、拘束を止める（[手の回転を写す骨を焼く](#手の回転を写す骨を焼くmotionbake_roll)）。写す骨の軸 Y のまわりのねじれ（VRM の roll と同じ決め方）。踊りの .blend だけ。Blender が要り、再利用しない（毎回実行） |
| `check.arm_pinch` | blend → report | 腕の関節のまわりのメッシュが、ねじれで絞ったように細くなっていないかをコマごとに測る（[腕の関節の細りを測る](#腕の関節の細りを測るcheckarm_pinch)）。輪ごとの太さの比（初期姿勢で割る）の最小・5% 点・中央値・細いコマの数。`min_ratio` を渡したときだけ合否。Blender が要り、再利用しない（毎回実行） |
| `model.recolor_part` | blend → blend, texture, report | 1 つの部品（`object`）のメッシュが使う uv の範囲だけ、色の絵に色ごとの倍率 `gain` を掛ける（[色の絵を塗り替える](#色の絵を塗り替えるmodelrecolor_partmodelrecolor)）。`dark_below` を渡すと、それより暗い画素だけ。塗り替えた絵は PNG に書いて詰め込む。Blender が要り、再利用しない（毎回実行） |
| `model.recolor` | blend → blend, report | 色の絵の、色相の範囲だけを補正する（同じ節）。`rules` ごとに、名前に `image` を含む絵の色相・彩度・明るさを変え、範囲の外は変えない。Blender が要り、再利用しない（毎回実行） |
| `model.remove_parts` | blend → blend, report | 部品（物体）を外す（同じ節）。骨と揺れものの設定は残す。シーンに無い名前は要確認。Blender が要り、再利用しない（毎回実行） |
| `model.smooth_skin` | blend → blend, texture, report | 肌の島の、選んだ範囲の溝（形）と焼き込まれた明暗（色）をならす（[肌の島を塗り直す](#肌の島を塗り直すmodelsmooth_skinmodelclean_skin)）。肌色の頂点をいちばん多く含む島だけを相手に、高さ・横の距離・横を向いた面のなだらかな重みで、色は頂点の色をならして塗り直し、形は縮まないならし方（Taubin）。Blender が要り、再利用しない（毎回実行） |
| `model.clean_skin` | blend → blend, texture, report | 外した部品の下の肌のしみを消す（同じ節）。手を含む島の、上腕・前腕などの頂点のうち肌色でないものを、まわりのきれいな肌の色で埋めて塗り直す。肌色の色相は 0° をまたいでもよく、隣を近さで決める口（`neighbours`）と、色むらを明るい側の肌の色へ寄せる段（`tone`）もある。Blender が要り、再利用しない（毎回実行） |
| `model.split_material` | blend → blend, report | 1 つの部品の中の選んだ面（顔の面など）を、材質の写しの新しい枠に分ける（[部品の中の面を別の材質に分ける](#部品の中の面を別の材質に分けるmodelsplit_material)）。面の中心が箱の中で、色の絵の色が決まりに入る面か、ほかの箱の中の面。`look.lit` の前の下ごしらえ。Blender が要り、再利用しない（毎回実行） |
| `model.face_shape` | blend → blend, report | 顔の形を参照の絵の比率へ寄せる（[顔の形を参照の比率へ寄せる](#顔の形を参照の比率へ寄せるmodelface_shape)）。頭のメッシュの頂点を、正面の視点の画素で書いた「今の高さ → 上へ動かす量」の表で上へ寄せ、顔の正面だけに効かせる（`narrow` であご先を細く）。Blender が要り、再利用しない（毎回実行） |
| `model.closed_eye_key` | blend → blend, report | 目を閉じたとき用の形状キー（既定 `eyes_closed`、値 0）を頭に足す（[表情](#表情頭の絵の差し替えと閉じ目の形modelclosed_eye_key)）。目の穴の奥の眼球の面を穴の縁の膜の位置まで手前に出し、まつ毛の板などは頭の中へ引っ込める。`render.passes` の `expressions` で、閉じた目の絵の間だけ 1 にする。Blender が要り、再利用しない（毎回実行） |
| `model.export_mesh` | blend → mesh [, texture], report | 物体のメッシュ（頭など）を、Blender の外で計算するための npz に書き出す（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。頂点の基本の形の世界の座標（m）・辺・三角形とその面の角・uv・法線。`texture` で、その物体の材質が使う色の絵を PNG で。.blend は変えない。Blender が要り、再利用しない（毎回実行） |
| `model.face_shapes` | mesh → shapes, report [, debug] | 頭のメッシュ（`model.export_mesh` の npz）から、表情の形状キーの頂点の動きを計算する（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。まばたきと笑顔の閉じ目は、正面から見える目の開きの上下の縁を測って閉じる線へ寄せる。眉は部品をそのまま、口角は口の端のまわりだけ動かす。部品を見つける箱と動かす量に既定は無い。numpy だけで、同じ入力なら再利用する |
| `model.shape_keys` | blend, shapes → blend, report | 計算した形（`model.face_shapes` の npz）を、物体の形状キーとして入れる（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。世界の座標の動きを物体の中の座標へ移して基準のキーの形に足す。並びは npz の並び、値は 0。同じ名前のキーがあれば止める（`replace` で入れ直す）。VRM の表情への割り当ては `vrm.expressions`。Blender が要り、再利用しない（毎回実行） |
| `model.neck_junction` | blend → blend, report | 首と頭のつなぎ目を直す（[首と頭のつなぎ目を直す](#首と頭のつなぎ目を直すmodelneck_junction)）。体のメッシュの首の上の端を頭の骨へなめらかに付け替え、首の中から頭の中へ伸びる栓（ふた付きの筒）を足して、首の肌の 1 色にする。首の直しの量に既定は無い。Blender が要り、再利用しない（毎回実行） |
| `look.toon` | blend → blend, report | キャラに描く前のセル調の塗りと輪郭線を付ける（[セル調の塗りと輪郭線を付ける](#セル調の塗りと輪郭線を付ける)）。体は決まった向きの光で影を 2 段に分け、舞台の色と縁の光を足して Emission で出す。顔（`faces`）は光を少し受けて絵の色を出す。輪郭線は反転ハル。舞台の色と縁の強さは共有のノードグループ `model3d_stage_tone`。Blender が要り、再利用しない（毎回実行） |
| `look.lit` | blend → blend, report | キャラ（VRM の MToon の材質でもよい）の材質を、舞台の光の向きと色を受ける塗りに差し替える（[舞台の光を受ける塗り](#舞台の光を受ける塗りlooklit)）。絵の色 ×（まわりの明るさ ＋ 光の当たり具合を 2 段に分けたもの × 光の色あい）を Emission で出す（EEVEE でだけ働く）。元の材質は書き換えず、材質ごと新しく作る。まわりの明るさは材質ごとの RGB ノードで、描く工程がコマごとに入れる。`rim` で縁の光（カメラから見て左と右で色を分ける。材質ごとの割合。色と強さは 1 つのノードグループで、コマごとに書き換えられる）を足せる。`color_attribute` で絵の色を色の属性（頂点の色）に、`unlit` で光を受けない決まった明るさの材質にできる。Blender が要り、再利用しない（毎回実行） |
| `motion.bone_path` | blend [, track] → path | 動きのコマごとに、骨（既定は腰と頭）の付け根の位置を m で測る（[段取りを組む](#段取りを組む)）。`points` を書くと、骨から決めた点（顔・胸・手の先など。[骨から決めた点](#骨から決めた点motionbone_path-の-points)）も測る。track（描くコマの並び）があればその動きのコマを重ねず小さい順に、無ければ `frame_range` か .blend のシーンの全コマ。path は `model3d-bone-path/0.1`。Blender が要り、再利用しない（毎回実行） |
| `plan.light_keys` | audio → keys | 曲の音（WAV など）から、`plan.stage` が光に使う光のキー（`model3d-light-keys/0.1`）を作る（[段取りを組む](#段取りを組む)）。窓（`start` 秒から `frames` コマ）のコマごとに、低・中・高の帯の強さ（0〜1。立ち上がりは即座、下がりはゆっくり）と、拍の格子 `grid`（必須）に入る拍。既定は試作の解析で、numpy だけで計算する（librosa.stft と同じ値）。計算だけで、同じ入力なら再利用する |
| `plan.lights` | plan [, keys] → track [, report] | 光の段取り表（`model3d-light-plan/0.1`）から、コマごとの光の値のトラック（`model3d-light-track/0.1`）を作る（[光の台本](#光の台本planlights)）。`values` の既定を、t を含む区間の `let` と `set` で上書きし、閃光（強い方を残す）を重ねる。`flash`・`flash_color`・`fade` はどのトラックにもある。値は数・3 つの並び・小さな式（決めた名前と関数だけ。`eval` は使わない）。拍の格子と fps は光のキー（`plan.light_keys`）から。計算だけで、同じ入力なら再利用する |
| `plan.retime` | → track | 動きの fps（`motion_fps`。例 24）と違う fps（例 30）で描くコマの並び（T と f だけのトラック）を作る（[動きと違う fps で描く](#動きと違う-fps-で描く小数のコマ)）。f = `start` + (T − 1) × `motion_fps` ÷ `fps` で、小数のコマは前後のコマの間の時刻。`plan.stage` の cut、`motion.bone_path` と `render.passes` の track に渡せる。計算だけで、同じ入力なら再利用する |
| `plan.stage` | plan, keys, bones [, cut] → track [, report] | 段取り表（`model3d-stage-plan/0.1`）と光のキー（`model3d-light-keys/0.1`）と骨の通り道から、`render.stage` のトラックを作る（[段取りを組む](#段取りを組む)）。コマごとに、カメラ（区間・動き・寄りと揺れ）、壁の模様、光と塗りの縁の値を決める。cut（T と f のトラック）があればその並びで。計算だけで、同じ入力なら再利用する。report は区間ごとのコマの範囲 |
| `plan.shots` | plan, bones → cameras, track [, alt, alt_track, report] | 2D の絵コンテのショット表（`model3d-shot-plan/0.1`。1 枚の絵の仮の部屋の中のカメラの移動・向き・寄りと、追う点を置く画面の画素、回り込み、手直し）を、追う点がちょうど狙いの画素に来るように主点を解いた 3D のコマごとのカメラに写す（[絵コンテのカメラを 3D に写す](#絵コンテのカメラを-3d-に写すplanshots)）。部屋の置き方は `render.box_room` と同じ。つなぎの相手のカメラ（`alt`）と揺れなしの基準のカメラも。計算だけで、同じ入力なら再利用する |
| `plan.orbit` | plan, bones → cameras, track [, report] | 踊り手を中心にした極座標のカメラの段取り表（`model3d-orbit-plan/0.1`）と、骨から決めた点の通り道から、コマごとのカメラ（`model3d-cameras/0.1`。行列・焦点距離・主点・踊り手の点）とコマの並び（T と動きのコマ f）を作る（[踊り手を中心にしたカメラ](#踊り手を中心にしたカメラplanorbit)）。区間ごとの方位・距離・高さ・狙う点・画角・ロールのキーとつなぎ方、手持ちの揺れと打点の揺れ、踊りの時刻の止め。狙う点のまわりの仰角・レンズの mm・区間の平均と混ぜる狙い（follow）・区間の中だけのならし・絞りとピント（cameras の `fstop`・`focus_m`）も書ける。`render.passes` の track と cameras にそのまま渡せる。計算だけで、同じ入力なら再利用する |
| `plan.scout` | bones → cameras, track [, report] | 向きの下見（[向きの下見](#向きの下見planscout)）。拍ごと（`beats` と `grid`）か時刻ごと（`times`）の姿勢を、踊り手のまわりのいくつかの方位（`angles`）から見るコマごとのカメラ（`model3d-cameras/0.1`）とコマの並びを作る。コマ T = 1 + 行 × 列の数 + 列。`render.passes` でそのまま描き、`mv sheet` で拍（行）× 方位（列）に並べる。計算だけで、同じ入力なら再利用する |
| `plan.stills` | bones → cameras, lights [, track] [, report] | 見本のコマ（[見本のコマ](#見本のコマplanstills)）。決めた踊りのコマを 1 つずつ、足もとの真下の床からの位置で、狙う点を傾けずに見るカメラ（`model3d-cameras/0.1`。レンズ・絞りとピント）と、そのコマの光の値（`model3d-light-track/0.1`）を作る。T は踊りのコマの小さい順。`render.show` でそのまま描く。計算だけで、同じ入力なら再利用する |
| `render.stage` | blend, track → frames, report | キャラを舞台と光の中でコマごとに PNG に描く（[舞台で描く](#舞台で描く)）。床・LED の壁・トラス・光の柱とスポット 4 つ、EEVEE と光のにじみ。トラック（`model3d-stage-track/0.1`）のコマの並びとカメラ・光・塗りの値で描き、無ければ腰を追う正面のカメラと動かない光。輪郭線の太さはカメラの距離で画面の上にそろえる。舞台を切って `background` で緑背景か透けにも描ける。`push` で、描く直前に帯などの頂点を当たり（閉じたメッシュ）の外へ出す。描いたコマはとばす（続きから描ける。同じ入力で描いたコマだけ。[入力の控え](#コマのフォルダの入力の控え)）。frames は描いたコマの一覧（`model3d-frames/0.1`）。Blender が要り、再利用しない（毎回実行） |
| `render.prepare_push` | blend → blend, report | `render.stage` の `push` の準備（[押し出しを準備する](#押し出しを準備する)）。`colliders`: 元のメッシュ（脚など）を写して穴をふさぎ、外向きの閉じた当たりにして面を減らし、元と同じ骨で動かす（描かない）。`groups`: 骨（`bones` とその子孫）に重みのある頂点を種に、高さ `top` より下でつながった部分（帯の垂れなど）を選び、上の端から `fade` で重みを付けて、法線の前と後ろで `front` と `back` の頂点の組にする。Blender が要り、再利用しない（毎回実行） |
| `render.passes` | blend, track [, cameras] → passes, cameras, report | キャラを要素に分けて簡易描画（Workbench）でコマごとに描き、カメラと踊り手の点を控える（[要素別に描く](#要素別に描く)）。room（部屋の物体）・char（人物の色の絵のまま。透けの PNG）・shade（白い人物にスタジオの光）・disc（踊り手の点の真下の床の円）・head（顔の物体だけ白）。`expressions` で、頭の絵を時刻の表で差し替え、表情の形状キーを切り替える（形状キーの値を時刻のキーで動かす `values` も）。カメラはトラックか、cameras（`model3d-cameras/0.1`）の行列・焦点距離・主点。重ね合わせは Blender の外で、`render.stage` と並ぶ 2 つ目の描き方。描いたコマはとばす（続きから描ける。同じ入力で描いたコマだけ。[入力の控え](#コマのフォルダの入力の控え)）。Blender が要り、再利用しない（毎回実行） |
| `render.show` | blend, cameras [, lights, hooks] → frames, report | 演出版を Blender 5.x の EEVEE で 1 回でコマごとに PNG に描く（[演出版を 1 回で描く](#演出版を-1-回で描くrendershow)）。カメラ（`model3d-cameras/0.1`）は全部のコマのキーにし、カットの最後のキーを止めるので、動きのぶれ（コマから後ろ）がカットをまたがない。`fstop`・`focus_m` のコマは被写界深度。光のトラック（`plan.lights`）で、名前で指した光の強さと色と、`look.lit` の塗りのまわりの明るさをコマごとに入れる。表情・空・にじみ。作品ごとの舞台はフック（Python の台本。レシピに書いたときだけ実行し、sha256 を残す）で作って動かす。描いたコマは毎回描き直す。Blender が要り、再利用しない（毎回実行） |
| `render.box_room` | blend, image → blend, report | 1 枚の背景の絵を、基準のカメラ（焦点距離・主点・床からの高さ）から床と奥の壁に投影して張った箱の部屋を .blend に足す（[箱の部屋](#箱の部屋)）。基準のカメラから見ると絵と同じ画で、カメラが動くと奥行きが出る。踊り手の立つ所（絵の画素）が世界の原点。絵の届かない向きは暗い箱。物体は `collection`（既定 `model3d_room`）に入れ、`render.passes` の `room.collection` で描く。Blender が要り、再利用しない（毎回実行） |
| `render.part_colors` | blend → frames, report | 部位を色分けして描く診断の絵（[色分けの診断の絵](#色分けの診断の絵renderpart_colors)）。`bone_meshes` は頂点ごとにいちばん重みの大きい頂点グループの名前の頭（`bone_colors`）で、ほかは物体ごと（`object_colors`）。`show` で描画から隠した物も描く。カメラは狙う点の方位 `yaw` から水平に。frames は描いた絵の一覧（`model3d-frames/0.1`。`check.frames` で比べられる）。.blend は変えない。Blender が要り、再利用しない（毎回実行） |
| `render.face_sheet` | blend → sheet, report | 表情の一覧の絵（[表情を形で作る](#表情を形で作るvrm-に入る表情)）。頭の形状キーの組み合わせ（`combos`）ごとに、いくつかの向き（`views`。方位・高さの角・正射影の幅・狙う点）から EEVEE で寄りで描き、表情（行）× 向き（列）に並べる。正面だけでは気づけない、斜めから白目がのぞくなどを見るため。`hide` の物体（髪など）は描かない。.blend は保存しない。Blender が要り、再利用しない（毎回実行） |
| `render.title` | [frames] → frames [, report] | 描いたコマ（frames か、`frames_dir` と `range`）に題字の層（文字か画像）を重ねて、別のフォルダへ書く（[題字を重ねる](#題字を重ねる)）。窓の秒の `start` から `fade_in` 秒で浮かび上がる。文字は書体・形・大きさ・色・縁取り・字送り、層ごとに光り、全体に影。重ね方は既定で試作と同じ（層ごとの `blend` で `over` も選べる）。大きさは基準の幅（既定 1080）の画素。前のコマはそのまま写す。書いた一覧は `render.assemble` の入力になる。書体と画像は隠れた入力なので再利用しない（毎回実行） |
| `render.panels` | [frames, frames_b, frames_c] → frames [, report] | コマ割り（[コマを割って同じ瞬間を見せる](#コマを割って同じ瞬間を見せるrenderpanels)）。描いたコマ（frames か、`frames_dir` と `range`）の決めた区間を、同じ T の別のカメラのコマ（`frames_b`・`frames_c` の一覧か、`frames_b_dir`・`frames_c_dir`）と上下に割って、別のフォルダへ書く。境は折れ線（同じ種なら同じ）、境の線は色のにじみと芯で、割れた最初のコマで太く明るく `settle` コマで落ち着く。大きさは基準の高さ（既定 1 で画面の高さの比）。別のカメラのコマが無い T は割らずに写して報告に書く（要確認）。書いた一覧は `render.assemble` の入力になる。再利用しない（毎回実行） |
| `render.afterimage` | [frames] → frames [, report] | 目の残像（[目の残像を足す](#目の残像を足すrenderafterimage)）。描いたコマ（frames か、`frames_dir` と `range`）の `span` の T に、`source` の T の明るいコマをぼかして、明るい所だけを `color` でうすく足して、別のフォルダへ書く。濃さは `span` の最初で `amount`、`decay_frames` コマで 1/e。ぼかしは基準の高さ（既定 1 で画面の高さの比）。ほかのコマはそのまま写す。書いた一覧は `render.assemble` の入力になる。再利用しない（毎回実行） |
| `render.assemble` | [frames, audio] → video [, report] | 描いたコマ（frames か、`frames_dir` と `range`）と曲の音を、1 本の動画にする（[動画にまとめる](#動画にまとめる)）。BT.709 の式で YUV にして BT.709 の印を付け、H.264 と AAC で書く。音とフェードは窓の秒で書く。抜けたコマ・とびのある T・描いた後に変わったコマは止める。ffmpeg が要り、再利用しない（毎回実行） |
| `check.video_frames` | video [, frames] → report | 書き出した動画からコマを取り出して、もとのコマと比べる（[動画の中身を確かめる](#動画の中身を確かめるcheckvideo_frames)）。動画の n 番目のコマを T = first + n として BT.709 で RGB に戻し、T と T ± offset のコマとの色の差の平均を出す。どれも同じ T にいちばん近く、前後のコマより十分近ければ合格、ずれたコマや古い版の動画なら不合格。ffmpeg が要り、再利用しない（毎回実行） |
| `check.colors` | → report | 色の数を、絵の組で比べる（[色の数を、絵の組で比べる](#色の数を絵の組で比べるcheckcolors)）。参考の絵（`left`）とこちらの絵（`right`。絵か、動画の決めた時刻のコマ）の明るさの平均・5/50/95 %、R・G・B の平均、緑÷赤・青÷赤、彩度と、決めた所の色の平均を測り、組の平均と差を出す。合否は出さない。再利用しない（毎回実行） |
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

**数える範囲**（`zone`。4 作目の 3D のダンスで、描き直した表情の絵を写したあと、ねらった所（目や口のまわり）の外が変わっていないかを確かめた
引き算の検査から移した。CLI には無い）:
- `{"boxes": [[x0, y0, x1, y1], …], "threshold": 差のしきい（既定 0）, "metres_per_pixel": 1 画素の長さ（任意）}`。枠は画素で、右と下は含まない。
- コマごとの行に `zone`: 差（色と透けのいちばん大きい値の差）が `threshold` より大きい画素の数と枠（左上と右下を含む）、範囲の内と外の数、外の画素の枠。
  `metres_per_pixel` があれば、枠の幅と高さを mm でも（(右 − 左 + 1) × 1 画素の長さ。0.1 mm に丸める）。
- まとめに、全部のコマの内と外の数と、外が 0 でないコマの並び。外が 0 でなければ、ねらった所の外へはみ出している。しきい値 `max_outside_pixels`
  （`zone` と一緒に使う）で合否を出せる。
- 知見: 正面の絵だけでなく、斜め（±25°）の絵どうしでも比べる。あごの後ろに隠れた首の面へのはみ出しは、正面からは見えなかった。
- 確かめたこと: CI で、範囲の外の 1 画素が外の 1 として数えられること、しきいより小さい差を数えないこと、同じファイルの近道の行、枠と mm が試作の数え方と同じこと、
  `max_outside_pixels` の合否と工程の要確認、悪い設定を止めることを確かめた。2026-11-23 に、4 作目の 3D のダンスの引き算の検査（台本をかけ直した結果は、
  残してある記録と同じ）と同じ絵（表情 3 つ × 正面・斜め 25°・−25° の 7 組、900 画素）で比べた（実測）: 7 組とも、変わった画素の数・枠・mm が台本と同じ。

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

## 台本の値を比べる（変えたコマだけ描き直す）

演出の一部だけを作り直したとき、変わっていないコマは前の版のコマを写せば、描く時間を減らせる。写してよいのは、台本がそのコマに渡す値（カメラ・光・塗り・動きのコマ）が前の版と同じコマだけ。`check.track_match` は、2 つの台本の値を同じ T どうしで比べ、同じコマと描き直すコマを出す。リポジトリ外の 3 作目の VRM のダンスで、演出版の前半だけを作り直したときに、前の版と今の版のカメラと光の値が後半の全部のコマで同じことを確かめてから、後半は前の版のコマを写した（全部のコマを描くより 22 分ほど短く済んだ）使い捨ての比べを部品にした。

**入力**: `left`（前の版。コマを写す元）・`right`（今の版。これから描く台本）: コマの並びの記録 2 つ。どちらも `model3d-cameras/0.1`（`plan.orbit`・`plan.shots`・`plan.scout` の出力）か、どちらも `model3d-stage-track/0.1`（`plan.stage` の出力など）。

**比べ方**:
- T ごとに、T のほかの欄を全部たどる（入れ子の辞書と並び。欄の名前は `camera.location[1]`・`matrix[0][3]` の形）。数は差の大きさ、数でない値は同じかを見る。
- 同じコマ: どの欄も、数は差が `tolerance`（既定 0 = ちょうど同じ）以下、数でない値は同じで、欄の有無も同じ。
- `ignore`: 見ない欄の名前の並び（その名前で始まる欄も見ない。例 `["f"]`）。
- 今の版にしか無い T（足したコマ）は、描き直すコマに入れる（`only_right` にも並べる）。前の版にしか無い T（今の版で消えたコマ）は、
  描かないので描き直すコマに入れず、`only_left` に並べるだけにする（`only` に今の版のトラックに無い T を渡すと、描く工程が止まるため）。
- 記録の頭の欄（画面の大きさ・fps・受光など。`notes` と `x_` の欄は見ない）が違えば、コマの値の意味が変わる（同じ焦点距離の画素でも画角が違う）ので、全部のコマを描き直すコマにする（`header_differing`）。

**出力**: `report`（同じコマの範囲 `same`・描き直すコマの範囲 `redo` と T の並び `redo_T`（空でなければ、`render.stage`・`render.passes` の `only` にそのまま渡せる。空なら描き直すコマは無い。`only` は空の並びを受け付けない）・欄のまとまりごとの差の最大とその T・差の大きいコマ・T ごとの行）。測るだけで、写すかは人が決める（`pass` は null。`report_only` で要確認にしない）。

**変えたコマだけ描き直す流れ**:
1. 前の版と今の版の台本の値を `check.track_match` で比べる（カメラの記録と、光や塗りの値を持つ記録の両方）。
2. 描き直すコマ（`redo_T`）だけを、新しいフォルダへ描く（`only`）。`redo_T` が空なら（2 つの台本の値が全部のコマで同じ）、描くコマは無いのでこの段を飛ばし、全部のコマを前の版から写す（`only` に空の並びを渡すと止まる）。
3. 同じコマのうちいくつかも描き直して、前の版のコマと `check.frames` で比べる（描く台本や .blend の変更が、台本の値に出ない違いを入れていないか。3 作目では、舞台に光を 1 つ足しただけで、台本の値が同じコマの 1 つが、207 万画素のうち 1,177 画素だけ少し違った（最大 17/255。影の計算の細かい所と見ている））。
4. 同じコマを前の版のフォルダから写し、`render.assemble` でまとめる。写したコマはそのフォルダで描いたコマではないので、写したあとで同じフォルダへ続きから描かない。

**確かめたこと**:
- CI（Blender なし）では、欄のたどり方、同じコマと描き直すコマの範囲、差の許し、見ない欄、数でない値と欄の有無、片方にしか無い T（前の版にしか無い T は描き直すコマに入れない）、記録の頭の欄の違い、重なった T、記録の種類、悪いパラメーター、工程の受け渡し（測るだけ）を確かめた。
- 2026-11-18 に、リポジトリ外の 3 作目の VRM のダンスの演出版の、前半を作り直す前の台本（r1）と後の台本（r3。カメラは前半を作り直した版と同じ）を、`plan.orbit` で同じ写し方でカメラにして比べた（実測。どちらの台本の写しも、その台本の計算と位置の差は最大 1.3e-15 m、レンズと絞りは全コマ同じ）。
  - 同じコマは 462 コマ（2 つの続いた範囲）、描き直すコマは 447 コマ（1 つの続いた範囲）。作業メモの「後半のカメラは前の版と全部同じ」と同じ（その作品のカットの境目のコマは、ここには書かない）。
  - 最初のカットも、カメラは前の版と同じだった（作り直したときは前半をまとめて描き直していた）。光の値の比べは、光の台本の部品（別の持ち帰り）で行う。

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
- `preserve_image_datablocks: true` は、選んだ単一ファイル画像のデータブロック・名前・参照を保ったまま、中身を読み直して詰め込む。
  MToon の設定が同じ画像を指していれば、アドオンが設定から材質を読み直しても差し替え後の絵が使われる。
  同じデータブロックを共有する参照には一緒に反映される。ノードと設定がすでに別の画像を指している場合は、先に材質を整える。
  `image_name` は通常のつなぎ替え（既定）の指定。

**確かめること**（どれかが合わなければ止める）:
- 合う画像の色空間が 1 つにそろう（新しい画像はその色空間で読む）。新しいテクスチャが今の画像と同じ大きさ。
- 詰め込んだバイトの sha256 が、渡したテクスチャと同じ。合うノードが全部つなぎ替わり、合わない画像ノードは前と同じ画像のまま。
- データブロックを保つ場合は、全画像ノードの参照が同じことを確かめる。選んだ画像の中身は、それぞれ差し替え元と同じ sha256 にする。
- 圧縮して別の .blend に書いた後も、入力の .blend とテクスチャが変わっていない。

**出力**:
- `blend`: 差し替えた .blend（圧縮して書く。.blend の sha256 は書くたびに変わるので、中身の比べには形や画像の比べを使う）。
- `report`: `model3d-apply-texture/0.1`。入力と出力の sha256、差し替えたノードの数と一つずつ（材質・ノードの木・ノード・元の画像と
  パス・新しい画像）、変わっていないことを確かめた合わない画像ノードの数、詰め込んだテクスチャの sha256 と大きさ、色空間。
  データブロックを保つ場合は `preserved_image_names` に元の画像名を列挙する。複数の画像があるとき、報告の `image_name` は null。
- `image_name`: 新しい画像に付ける名前（既定はテクスチャのファイル名）。

**確かめたこと**:
- CI では、名前の比べ方（ファイル名だけ、大文字と小文字・重複の印・相対パスの `//`）、工程の設定・Blender への渡し方・報告の確認（代役の Blender）、悪い設定、共有参照と色空間を保った読み直し（合成の画像）を確かめた。
- 2026-11-08 に、本物の Blender 4.5.15 で、試作の作り直しの 2 つの記録と同じ入力で回した。
  - 差し替えたノード 22・変わらないノード 22・アトラス 4096×4096・詰め込んだ sha256 は、2 つの記録とも同じだった（作り直しの写し直しと、顔の写し直しの流れ）。
  - 差し替え 22 件の一つずつ（材質・ノードの木・ノード・元の画像とパス・新しい画像）も、記録と同じだった。
- データブロックを保つ指定は、Blender 5.2.4 と VRM Add-on 4.10.5 で合成材質を 3 個作り、試作の画像差し替えに同じ入力を渡して比べた。
  画像ノード 6 個の差は 0 件（差し替え 4 個・保持 2 個）。MToon の色と影色の設定は 4 件とも画像ノードと一致し、設定から画像を
  読み直した後も 4 件とも同じだった。通常のつなぎ替えでは、同じ合成入力の MToon の設定 3 件が古い画像を指した。

## .blend どうしを比べる

`check.blend_match` は、2 つの .blend の中身を比べる。作り直したキャラが採用版と同じか、工程を移した後も同じ形と骨の動きになるかを、数で確かめるため。リポジトリ外の試作で、作り直したキャラと採用版を比べた台本を移した。Blender で中身を書き出し（`blender_adapter/snapshot_blend.py`）、比べは Blender の外の numpy でする（`model3d.blend_snapshot`）。

**比べるもの**（試作と同じ読み方）:
- 物体の有無（左にだけある・右にだけある）、シーンの最初と最後のコマと fps と `fps_base`（再生の速さは fps / fps_base）。
- メッシュごと: 頂点（物体の中の座標、float32）、面の角の数と面のつなぎ（面の角ごとの頂点）、作用中の uv（面の角ごと）、属性（値と、置き場所 `domain`・型 `type`）、置き場所の行列（シーンの最初のコマに合わせてから読む。保存したときのコマによらない）。
  - `attributes`: 比べる属性の名前の並びか `"all"`（既定。`.` で始まる内部の属性と `position` は除く）。名前を挙げた属性が、選んだメッシュのどれにも無ければ（打ち間違いか、書き出せない型）、形のつくりの違いにする（`requested_attributes_missing`）。
  - `objects`: 比べるメッシュの名前の並び（既定は全部）。名前を挙げたメッシュが片方にでも無ければ、黙って外さず、形のつくりの違いにする（`requested_objects_missing`）。
- 骨: 名前（並べ替えて）、静止の行列、姿勢の行列（`pose_frames` のコマ。`"all"` はシーンの全部のコマ、省くと `frames`）。アーマチュアが 2 つ以上なら `armature` で選ぶ。
- `evaluate: true` なら、`frames` のコマで変形した世界の形（float32 の行列で世界へ移した座標）の sha256 と、等間隔の `samples` 個（既定 512）の頂点。`evaluate` でないときは、コマを動かす前にモディファイアのビューポートを切る（速くするため）。
  - `mute_pose_constraints: ["right"]` で、指定した側のアーマチュアのポーズボーン拘束を評価用のシーンから外す。`["left"]` または `["left", "right"]` も可。同じ .blend を両方に渡せば、拘束の有無で比べられる。入力ファイルは読み取りだけで、すでにキーに焼いた動きには効かない。外した拘束の数は `muted_pose_constraint_count` に出る。
  - `evaluated_distances: true` で、同じ順番の全頂点を対応させてコマ・メッシュごとに距離の最大 `max_evaluated_distance` と 95 パーセンタイル `p95_evaluated_distance` を出す。単位は世界座標の Blender 単位。頂点数が違うと距離は出さない。この設定では、変形した形の `coordinate` の幅も全頂点の座標成分の最大差で判定する。
  - どちらも `evaluate: true` と `frames` が必要。骨の組み方などが異なるモデルを比べるときは `report_only` で座標の差を読む。

**合否**:
- `exact`: 全部が同じ（物体・シーン・頂点・面のつなぎ・uv・属性・置き場所・骨の名前と静止と姿勢・変形した形の sha256）。違いの大きさは `max_coordinate_error`・`matrix_world_max_error`・`rest_matrix_max_abs_error`・`pose_matrix_max_abs_error`・`sampled_max_coordinate_error`。
- `tolerance`（`{"coordinate", "matrix", "value"}`）を渡すと、形のつくり（物体・シーン・頂点と面の数とつなぎ・属性の置き場所と型・骨の名前・姿勢のコマ）が同じで、座標の違いが `coordinate`、行列の違いが `matrix`、uv と属性の違いが `value` の中なら合格。渡していない項目は同じであること（`coordinate` が無ければ頂点と変形した形（`exact` と同じく頂点の全部の sha256）、`matrix` が無ければ置き場所と骨の静止と姿勢、`value` が無ければ uv と属性。渡し忘れた項目を上限なしで通さない）。
- `tolerance` を渡さなければ、全部が同じ（`exact`）かで合否を出す（float の端数でも違えば不合格。試作の作り直しの比べでも、端数の 3e-7 で同じにならなかった）。
- 非有限の値（NaN・無限大）があれば、違いの大きさを測らずに不合格にする（`non_finite` に場所を並べ、違いの大きさは null。`tolerance` でも許さない）。
- `report_only` で測るだけ、`strict` で不合格を失敗にする。数を出す検査なので、採否は人が決める。

**出力**: `report`（物体ごとの行と変形した形の行つき）。工程の観測は、行を除いたまとめ。書き出し（`left_snapshot.npz`・`.json` と右）は工程のフォルダに残る。Blender は外部の実行ファイルなので再利用しない。

**確かめたこと**:
- 拘束で四角形の 1 頂点だけを動かす合成素材を Blender 5.2.4 で比較。拘束あり・なしの 2 通りとも、試作が書き出した変形後の全 4 頂点と座標の最大差 0。1 頂点だけを抽出したときに見えない変化を、全頂点の最大距離 0.625、95 パーセンタイル 0.53125 として検出した。
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

**写す前のならし**（`prefilter`。4 作目の 3D のダンスで、正面の描画の上に描いた表情の絵を色の絵へ写した台本から移した。省けば今までとファイルまで同じ）:
- 参照画像が色の絵の升目より細かいと（描画の 1 画素 0.1 mm に対して、色の絵の升目 1 つが描画の約 4 画素だった）、升目ごとに 1 点だけ引くので、
  線のふちがぎざぎざになった。写す前に、参照画像を升目の大きさでならす。
- `"auto"`: 写す重みのある三角形（見え隠れ・面の向き・`regions`・`mask` で決まる。参照画像の色とアルファは見ない）ごとに、√(視点の面積 ÷ 色の絵の面積)
  を測り、その中央値（升目 1 つが視点の何画素か）の半分を σ にする。位置合わせの倍率で、参照画像の画素の σ に直してならす。
- 数を渡すと、その σ（参照画像の画素）でならす。0 はならさないのと同じ。
- ならしは、ガウスの窓 ±ceil(3σ)、縁は鏡写し（台本は巻き戻し。違うのは絵の縁の 3σ の中だけ）。アルファのある参照画像は、色にアルファを掛けてから
  ならして割り戻す（透明な所の色をにじませない）。アルファはならさない。マスクもならさない。
- 報告の `prefilter` に、升目 1 つの視点の画素・数えた三角形・σ（視点と参照画像の画素）。
- 確かめたこと: CI では、ならしが台本のならしと絵の縁の 3σ の外で同じ値になること、升目の大きさの測り方（面積の比の中央値・面積 0 の三角形を数えない）、
  `auto` の σ（升目 4 画素で σ 2）と位置合わせの倍率での直し方、σ 0 がならさないのと同じこと、升目より細かい縞（幅 1 画素の線）の拾い方のむらが減ること、
  透明な所の色がにじまないこと、悪い設定を止めることを確かめた。省いたときの結果は今までのテストのまま。
- 2026-11-23 に、4 作目の 3D のダンスの写す台本（`--prefilter auto`）と、同じモデル（顔の部品 34 万 5,860 三角形。Blender 5.2.4 の `model.export_mesh` で
  書き出した）と同じ目の絵で比べた（実測）:
  - 升目 1 つの大きさ: 台本と同じ三角形（正面を向き、前髪より奥で、写す範囲の枠にかかる物）で測ると、閉じ目 3.9126・半目 3.9573 画素（台本の記録は
    3.91・3.96）。`auto` が自分で選ぶ三角形（写す重みのある物）では 3.9386・4.0121。
  - ならした絵: 絵の縁の 3σ より内は、差が最大 0.00008/255。縁の 3σ の中（約 2.3 万画素）だけ、巻き戻しと鏡写しの違いで最大 83/255 違う。
  - 前髪より奥の三角形だけの部品へ写して、台本の色の絵と比べると（どちらかが変えた約 1 万〜1.7 万画素）、ならしありで差の平均 6.25（閉じ目）・6.55（半目）、
    なしで 6.54・6.82。差が 32 を超える画素は 898・536（なしは 984・610）。残る差は、写し方の作りの違い（台本は画素の中心を整数の座標に置く（半画素ずれる）・
    重心座標の許しで縁を塗る・縁の外へ 3 画素広げる）で、ならしではない。

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
  
  2026-11-06 時点では、骨やウェイトのあるキャラの観測は未確認だった。後述の 2026-11-16 の実 VRM の観測では、131 骨を含む 242 件を契約に通している。確認範囲は各日付の記録を読む。
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

#### VRM の roll 拘束を検算する（`check.vrm_roll`）

`check.vrm_roll` は書き出した VRM（GLB）を読み、roll 拘束ごとに、元 node の軸まわりにねじってから軸を倒した姿勢を作る。
送り先と元の基本姿勢のローカル回転から、[VRMC_node_constraint 1.0 の roll の式](https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_node_constraint-1.0/README.md#roll-constraint)
に従って、送り先の軸まわりに残ったねじれを計算する。Blender や再生アプリを起動せず、入力ファイルを書き換えない。
基本姿勢は `rotation` または `matrix` から読み、指定がなければ単位回転とする。
腕を倒すだけでねじれが出る場合は、この報告で該当する拘束を見つけられる。

```json
{
  "id": "roll",
  "operation": "check.vrm_roll",
  "inputs": {"vrm": "$inputs.avatar"},
  "parameters": {"tolerance_deg": 0.2},
  "outputs": {"report": "roll.json"}
}
```

- `twists_deg` は -180 度より大きく 180 度未満のねじれ角（省略時 -120・-60・0・60・120 度）。`swings_deg` は 0 度以上
  180 度未満の倒す角（省略時 0・30・60・90・120 度）。いずれも 0 度を加え、重複を除く。倒す向きは両方の垂直軸の正負の 4 方向。
- 報告には `max_error_deg`（重み × 元のねじれとの差の最大値）、`max_error_case`（その姿勢での計算値と狙い）、
  `max_twist_when_only_swung_deg`（ねじれ 0 度で倒したときの不要なねじれ）を拘束ごとに残す。拘束の `weight` が未指定なら 1。
- `tolerance_deg` を指定した場合だけ、角度差が許容値を超えると要確認。読めない roll も要確認、対象がなければ未実施。
  `strict` では要確認と未実施が失敗、`report_only` では測るだけ。対象は単独の roll の計算で、拘束の連鎖や動画中の姿勢は検査しない。
  aim・rotation 拘束を含む構造の一覧は `check.vrm` で読む。

確かめたこと：合成 GLB の roll 5 件（X・Y・Z、軸がずれた 2 件）で、1 件あたり 96 姿勢を同じ入力で試作と比較した。
最大角度差、倒しただけの最大ねじれ、基本姿勢の回転差の計 15 項目は、試作の報告精度（0.001 度）で差が 0 だった。
合成のレシピでも、許容値の有無、要確認と報告のみの扱い、入力ファイルが変わらないことを確認した。

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
- `constraint_mode`（任意）: `aim`（既定）では以下の手順を使う。`parented_roll` では、重みの振り分けと手の `roll` を共通にして、
  Damped Track の代わりに元の骨の子孫へ逆向きの補助骨を置く。元の骨の親座標に合わせる台の骨 `<name>_basis` をはさみ、
  補助骨に Copy Rotation（Y だけ・Add・ローカルどうし）をかける。骨自身の Z 軸で半回転してあるため、この回転が元の骨のねじれを打ち消す。
  台の骨に重みは付けず、VRM の書き出しに残るよう変形用にする。台の名前が既存の骨または変形対象の頂点グループと重なれば止める。
  このモードでは `aim` を省ける。拘束を読まない再生では、補助骨も元の骨の曲げとねじれに付いて行く。
  ねじれを分ける効果は拘束が効く再生で現れる。線形スキニングで回転差 Δ の骨に半々の重みが付くと、断面が cos(Δ/2) 倍に細ることがある。
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
  `VRMC_node_constraint` に対応しないアプリでは、既定の `aim` で足した骨が元の腕に付いて行かず、腕の形が崩れうる。
- **Blender の Copy Rotation と VRM の roll は計算が違う**: Copy Rotation は回転を 3 つの角度（オイラー角）に分けた Y を使い、VRM の roll は
  回転を「曲がり × 軸のまわりのねじれ」に分けたねじれを使う。手を大きく曲げたコマで、3 作目では中央値 3°、いちばん大きいコマで 30〜55° ずれた
  （分け方の順を 6 通り試し、いちばん小さい ZYX でも 28° 残った）。動画（Blender で描く）では、ねじれの正確な値を骨のキーに焼いて
  拘束を止めると、VRM と同じ形になる（`motion.bake_roll`。下の節）。拘束を止めると VRM には書き出されないので、焼くのは踊りの .blend だけ（モデルは拘束のまま）。
- 出力: `blend`、`report`（`model3d-twist-bones/0.1`。足した骨（`kind` が `swing`、`roll`、`parented_roll` の台では `basis`）、メッシュと骨ごとの重みを分けた頂点の数と、島ごとに分けた島の数、
  1 頂点の骨の数を切った数（`influences`）、重みを分けたメッシュ（`meshes`）とリンク複製から分けた物体（`separated_from`））。

`check.twist` は、骨の回転（初期姿勢からの骨自身の回転）を「向きを変える分（振り）」と「骨の軸 Y のまわりに回る分（ねじれ）」に分け、
ねじれの角（−180〜180°）をコマごとに測る（メッシュは評価しない）。
- `bones`（必須）: 測る骨の名前。`frames`（[最初, 最後]。省くとシーンの全コマ）、`over_deg`（既定 [60, 90]）・`top`（既定 8）。
- 出力 `report`（`model3d-twist/0.1`）: 骨ごとの最小・最大・絶対値の最大（度）、`over_deg` を超えたコマの数、ねじれの大きいコマ。
  コマごとの値は工程のフォルダの `twist_raw.json`。`max_twist_deg` を渡したときだけ合否を出す（ねじれの絶対値がどれかのコマで超えたら不合格）。

**確かめたこと**:
- `parented_roll`: Blender 5.2.4 で、合成の筒 88 頂点を 3 姿勢で比較した。拘束を止めた形と補助骨なしの形の最大頂点差は、
  `aim` で 0.224254、`parented_roll` で 0.00000021（Blender 単位）。拘束を有効にした純粋なねじれ 2 姿勢では両方式の最大頂点差は 0.00000012。
  台の骨との親子関係、補助骨の逆向きの Y 軸、写す元と先の親座標での初期回転も検査した。
  左右の筒 182 頂点を 3 姿勢で、同じ合成入力から試作と比較した最大頂点差は、拘束ありで 0.00000008 未満、拘束なしで 0.00000007 未満。
  別の合成人型を VRM Add-on で書き出し、roll 拘束 6 件と重みのない台のノード 4 件が残ることも確認した。
  今回の検証範囲は 1 区間の合成素材。
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

### 部品の中の面を別の材質に分ける（`model.split_material`）

画像から作ったモデルは、頭と上半身のように、1 つの部品に顔と服や髪が入っていることがある。4 作目の 3D のダンスでは、舞台の光を受ける塗り
（[`look.lit`](#舞台の光を受ける塗りlooklit)）で顔だけ段をやわらかくするため、顔の面を別の材質に分けた（`look.lit` の前の下ごしらえ）。
`model.split_material` は、その台本の面の選び方を移した物。
- 入力 `blend`。`object`（必須）: 部品のメッシュ。`material`（必須）: 新しい材質の名前（もうあれば止める）。
- 選ぶ面: 面の中心（世界の座標。m）が箱 `region`（必須。`{"x": [下, 上], "y": …, "z": …}`。省いた軸と null の端は限らない。端は含まない）の中で、
  - (a) `color`: 色の絵の、面の uv の中心の画素（u × 幅・v × 高さを切り捨て）の値が、`rgb_min`（[R, G, B]、0〜1。どれもこれより大きい）・
    `red_at_least_blue`・`red_at_least_green` の決まりに入る面（肌の色の面）。色の絵は、材質の Base Color へつながる画像（`image` で名前の一部を指せる）。
  - (b) `boxes`: 箱のどれかの中の面（目と眉の枠の中で、前髪より奥の面など。肌の色でない瞳やまつ毛の面も入れる）。
  `color` と `boxes` の少なくとも 1 つが要る。値に既定は無い（モデルごとに決める）。
- 選んだ面のうち、材質の枠 `source_slot`（既定 0）を使う面だけを、その材質の写し（`material`）の新しい枠へ移す。ほかの面と、元のファイルは変えない。
- メッシュをほかの物体と分け合っている（リンクの複製）ときは、どの物体と分け合っているかを書いて止める（材質の枠と面の番号はメッシュの物で、
  分けるとほかの物体まで変わるため）。
- 出力: `blend`、`report`（`model3d-split-material/0.1`。面の数、選んだ面・色で選んだ面・箱だけで選んだ面・移した面の数、材質と枠の番号、色の絵の名前）。
  移した面が無ければ要確認。

**確かめたこと**:
- CI（Blender なし）では、箱の端と省いた軸、色の絵の画素の引き方（下の行から・端）と色の決まり、範囲の中で色か箱で選ぶこと、工程の受け渡しと要確認、
  悪い設定を止めること、分け合うメッシュを見つけて止めること（代役の Blender）を確かめた。
- 2026-11-23 に Windows の Blender 5.2.4 で、4 作目の 3D のダンスの下ごしらえの台本と、同じモデル（頭と上半身の部品 345,860 面）と同じ値で比べた（実測）:
  面ごとの材質の番号が、台本の書いた .blend と全部同じ（顔の面 27,162。色で選んだ 25,507、箱だけで選んだ 1,655 も台本の記録と同じ）。
- 限界: 面の色は、uv の中心の 1 画素だけで決める（面の中の色の混ざりは見ない）。影の中の肌も入るように、決まりは明るい肌より広く書く。

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
- 足した物（4 作目の 3D のダンスで、画像から作ったモデルの腕の肌（袖の色のにじみと、濃さの違う肌色のまだら）を塗り直した台本から移した。省けば
  今までと同じ）:
  - `skin.hue_deg` の下が上より大きいときは、0° をまたぐ範囲（`[335, 25]` は 335〜360° と 0〜25°）。その腕の肌は、色相が 350〜360° と 0〜10° に
    半分ずつあり、またげないと肌の半分が「しみ」になった。
  - `neighbours: {"radius": m}`: 隣を、面のつながりでなく近さ（頂点どうしの距離が radius 以下）で決める。腕の島も、近さでつながった頂点の集まりにする。
    画像から作ったメッシュは uv の境目で面が切れていて、つながりで広げると境目で止まった。報告に組の数（`neighbour_pairs`）。
  - `tone: {"flat", "bins", "min_vertices", "light_bins", "percentile"}`: 埋めてならしたあと、範囲の色を「明るい側の肌の色」へ `flat` の割合で寄せる
    （色むらと、ひじから上の濃い桃色を消す）。範囲の頂点のいちばん広がる軸に沿って `bins` 区間に分け（最後の区間は軸の端の頂点も含む）、区間ごとに、
    もとからきれいな肌の頂点（`min_vertices` 未満の区間は使わない）のうち、いちばん明るい値が `percentile` 以上の頂点の色の中央値を取る。そのうち明るい
    `light_bins` 区間（手首の側。手とつながる色）の色の中央値へ寄せる。使える区間が無ければ止める。報告の `tone` に、寄せた色と、最初と最後の区間の色。
    `flat`・`bins`・`min_vertices`・`light_bins` は、部品の大きさと頂点の細かさに寄るので既定を置かない（その腕では 0.95・12・20・4）。`percentile` は
    計算の作りの既定 60（「明るい側」とみなす百分位）。
    1 色に寄せると、ひじの赤みのような、もとからある濃淡も消える。

**確かめたこと**:
- CI（Blender なし）では、色の引き方（双線形・端）、頂点の色の平均、肌色の判定、隣の平均（使える隣だけも）、三角形の塗り（内側・塗り足し・内側が勝つ）、
  範囲の重み、縮まないならし（ただの平均は輪ごと縮む）、工程の受け渡し（埋めきれないと要確認）と悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-17 に Windows の Blender 5.2.4 で、2 作目の VRM のダンスのリポジトリ外の台本（脇の筋を消す台本・腕のしみを消す台本）と比べた（実測）。
  - `model.smooth_skin`: 台本をかけた前のモデルにかけると、塗り直した PNG が採用したモデルの絵と SHA-256 まで同じ。.blend も採用したモデルと
    `check.blend_match` で全部同じ（11 物体。ならした形を含む）、画像の画素とどの材質がどの画像を使うかも同じ。島・肌・選んだ頂点の数と、形の動き
    （平均 0.61 mm、最大 8.31 mm）、塗った画素の数も台本と同じ。
  - `model.clean_skin`: 同じく、PNG が採用したモデルの絵と SHA-256 まで同じ、.blend も全部同じ。範囲・しみ・埋めきれない頂点の数と、きれいな肌の色も
    台本と同じ。
- 足した物（0° をまたぐ色相・近さの隣・色むらを寄せる段）は、CI で、またぐ色相の判定が台本の決まりと同じこと（2,000 色）とまたがない範囲は今まで通りなこと、
  近さの組が総当たりと同じことと切れた面が近さでつながること、明るい側の色の取り方（しみの多い区間を使わない・使える区間が無ければ None）、設定の受け渡しと
  悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-23 に Windows の Blender 5.2.4 で、4 作目の 3D のダンスの腕の肌を塗り直した台本と、同じモデル（腕の部品 2 つ、約 3 万頂点ずつ。色の絵 4096²）で
  比べた（実測。設定は色相 [335, 25]・近さ 4 mm・寄せる割合 0.95・ならし 12 回・塗り足し 3 画素）:
  - 台本が変えた 332,764 画素のうち 313,275 画素を、こちらも変えた（こちらが変えたのは 316,808 画素）。両方が変えた画素の差の平均 0.29/255、差が 8 を
    超える画素 1,362、32 を超える画素 4（台本の塗り直しが、もとの絵から動かした量は平均 45.7/255）。
  - 区間の色（ひじ側・手首側）は、台本の記録と 0.01 ほどの差。しみの頂点 2,034・2,015（台本 2,061・2,134）、埋めきれない頂点 0（台本も 0）。
  - 違いの元: 範囲の決め方（台本は、腕と袖の骨の重みの割合が 0.02 より大きい頂点を、重みで混ぜる。`model.clean_skin` は、いちばん重い骨が
    `region_groups` の頂点を全部。範囲の頂点 14,992・15,046、台本 16,431・16,526）、頂点の色の引き方（台本はいちばん近い画素、こちらは双線形）、
    塗り方（台本は重心座標の許し −0.03 と縁の外への広げ方）。

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

**足した描き方**（4 作目の 3D のダンスで、顔が 1 枚の色の絵で描かれたモデル（目も口も絵。形のキーが無い）の表情の絵を描いた台本から移した。
笑顔の閉じ目・半目・まぶたのしわが要った。省けば今までとファイルまで同じ）:
- `sag` を負にすると、弧が上へたわむ（笑顔の閉じ目）。`eyes` を省いて `strokes` だけで描いてもよい（そのときは `end_y` も省き、消す帯も省ける。省けば元の画素の上に線だけを描く）。
- `strokes`: 点の列に沿って太さの変わる線の並び。前の線の上に、順に重ねる（線ごとに、線の範囲だけを `supersample` 倍に広げて塗り、最後に面積の平均で縮める。
  線の無い所は元の画素のまま）。
  - `points`: 点の列（画素）。3 点で `curve: true` なら、両端を通り、真ん中で 2 点目を通る 2 次の曲線を `samples`（既定 120）点で。2 点なら `samples` 点に
    等分（先細りのまつ毛・目尻のはね）。ほかは点の列のまま。`span`（[始め, 終わり]、0〜1）で列の一部だけを描く（弧の真ん中だけのしわなど）。
  - `widths`: 区間ごとの太さ。`[[位置 0〜1, 太さ], …]` の折れ線（目尻側を太く、目頭へ細く）か、`{"middle", "ends"}`（真ん中が太い弓の形。弧の上の影・
    涙袋・しわ）。区間の両端に丸を付ける（継ぎ目に角を出さない）。
  - `color`: 線の色（省けば `color`）。`name` はメモ。
- `skin_fill`（消し方の選択肢。消す帯 `erase_rows`・`erase_columns` とは一緒に使えない）: 目の枠 `boxes`（[x0, y0, x1, y1]）の中を、枠の外の肌らしい画素
  だけを重みにしたぼかしで埋める。暗い髪や、切れた背景（灰色）の色を混ぜない。
  - `skin`（必須。既定は無い）: 肌らしい画素の決まり（`rgb_min` の [R, G, B] より大きい、`red_minus_green_below`（R − G がこれより小さい）、
    `red_at_least_blue`（R ≥ B））。
  - 顔と目の大きさに寄る値（必須。既定は無い）: 肌の色を探すぼかしの広さ `sigmas`（画素の並び。狭い順に重ね、先の広さで足りた所に後の広さを使わない）、
    肌の色を取らない枠の外の幅 `exclude`、混ぜる範囲を枠から広げる幅 `grow`。4 作目の 3D のダンスでは [25, 50, 100]・6・4（0.1 mm/画素）。
  - 計算の作りの既定: 重みの強さ `gain`（8）、混ぜる範囲の縁のぼかし σ `feather`（7）と、ならし σ `soften`（1.5）。台本を 0.1 mm/画素の描画で
    回したときの値。長さは `base_width` で伸ばす。
  - 枠の外のぼかしの帯では、`keep_dark_below` より暗い画素（いちばん明るい色の値で比べる。顔に寄った横の髪）を埋めない。
  - 枠の中に、肌らしい画素がぼかしの届く所に無い画素があれば止める（重みが 0 で黒へ寄せてしまうため）。
  - `exclude_all: true`: 肌の色を取らない所を、全部の枠（`exclude_boxes` があればその全部）にする。既定（false）は台本と同じく、今の目の枠だけを
    外す。両目の枠が近く、ぼかしの窓がもう片方の目に届くと、その白目が肌らしい画素として混ざり、白い斑になる（照合した顔では、台本の既定のまま）。
  - 半目: `boxes` を瞳の上まで（上まぶたを下ろす所）にし、`exclude_boxes` に目全体の枠を書く（目の下半分の白目を、肌の色に使わない）。下ろした縁に、
    `strokes` で太い線を描く。
- 出力 `mask`（任意。PNG）: 描き直した所の重み（0〜255。消した範囲と、線のある画素を 2 画素ふくらませた所）。`texture.project_reference` の `mask` に
  そのまま渡せる（線の縁のなめらかな塗りは絵に入っているので、線の所は重み 1 で写す）。
- 知見: 線の太さは、もとの上まつ毛の太さ（その顔では 3〜4 mm、目尻側が太い）に合わせる。2 mm の 1 本線は、舞台の光の中で目が弱く見えた。

**絵の口を、もとの口の絵を変形して作る**（`texture.warp_mouth`。Blender なし。numpy と Pillow だけ）: 4 作目の 3D のダンスは、顔が 1 枚の色の絵で描かれた
モデル（目も口も絵。形のキーが無い）で、表情は色の絵の差し替えで作った。流れは、正面の正射影で描く（その作品では 0.1 mm/画素）→ その絵の上で、もとの
画素を動かす・描き足す → 色の絵へ写す（`texture.project_reference`）→ 引き算で確かめる（`check.frames` の `zone`）。口を肌で消して形を描く作り方は、
もとの絵にある物（口角の濃い印・やわらかい線・下唇の赤みと光）が無くなり、色の形を貼ったように見えたので、もとの絵の画素を動かす（消さない）。
- 入力 `image`（正面の描画）。出力 `image`（PNG）・`mask`（写す範囲。PNG）・`report`。
- 1. もとの口の線を色で測る: 探す範囲 `window`（[x0, y0, x1, y1]）の中で、`line`（`red_minus_green_above`・`red_above`・`green_below`。必須。既定は無い）に
  入る画素の、いちばん大きいかたまり（8 近傍）。左右の端、列ごとの線の高さ（25 画素の幅でならす）、左右の傾き。目で読まない。
- `lips`（必須。既定は無い。顔の形に寄る唇の高さ（画素））: `above`・`below`（線の上と下で、横の伸び縮みを全部効かせる幅）、`upper_keep`・`lower_keep`（上と下を、そのまま動かす幅。下は下唇の赤みと光の高さ）。4 作目の 3D のダンスでは 30・70・10・40（0.1 mm/画素）。
- 2. 口の真ん中のまわりで、横に `scale_x` 倍する（線の上 `lips.above`・下 `lips.below` までは全部、その外でなだらかに元へ。横に戻す長さは、横に動く量の 2 倍より短くしない
  （短いと、大きく広げたときに列の並びが減って逆に引けない。台本の値の範囲では台本と同じ））。口角の印もいっしょに動く。
- 3. 線を境に、上（上唇の側）と下（下唇の側）を別々に上下へ動かす。上 = 口角を上げる `corner_lift`（負なら下げる。印の内側の端で最大にして、印は水平の
  まま下げる）・真ん中を下げる `upper_sag`・真ん中を上げる `upper_arch`。下 = さらに開ける量 `open`（真ん中。下の縁のふくらみ `bulge`、2 で放物線）だけ
  下げる。線から離れるほど 0 へ戻す（上は `lips.upper_keep`、下は `lips.lower_keep` までは、そのまま動かす。戻す長さは、動く量の 1.3 倍より短く
  しない（短いと行の並びが折れ返り、顔の画素がとんだり重なったりする。台本は上の戻す長さを広げなかったが、台本の値の範囲では同じ））。口角を上げるときは、もとの線の左右の
  傾きもならす（`level_tilt`、既定 true）。横に `scale_x` 倍したあとの線の傾きは「もとの傾き ÷ `scale_x`」だが、台本は「× `scale_x`」で
  引いていた（広げた口は直しすぎ、狭めた口は足りない）。既定は台本と同じにし（照合を崩さない）、`tilt_after_stretch: true` で正しい ÷ を選べる。
- 4. 上と下のすき間を、口の中の色 `inside_color`（上ほど暗い）で埋め、下に舌 `tongue_color`。すき間は口角の印 `corner_mark`（画素。測った口の幅の半分より小さく）の内側で 0 になる
  ので、口角はもとの絵の口角のまま細くとがる。`open`・`upper_arch`・負の `corner_lift` には `corner_mark` が、`open` には 2 つの色が要る（既定は無い）。
- あごの線（`jaw`。任意。`green_below`・`start_below_line`・`keep_above`・`mask_above` が要る。既定は無い）: 列ごとに、口の線の `start_below_line` 画素下から、緑の値（σ 1.5 でならす）が `green_below` より小さくなる所（肌が首の色に
  変わる所）をあごの線とし、動きはあごの線の `keep_above` 画素上までに 0 へ戻し、写す範囲はあごの線の `mask_above` 画素上より下を外す（4 作目の 3D のダンスでは 185・60・34・22）。収めないと、あごの線がずれて、
  首に顔の肌色がはみ出した（斜めから見て首に白い所が出た）。収まらない列は要確認。
- 写す範囲: 動かした画素とすき間を 13 画素ふくらませ、σ 4 でぼかす。
- 計算の作りの長さ（線のならしの幅 25（奇数にそろえる）・なだらかに戻す距離・写す範囲のふくらませとぼかし・あごの線のならし）は、台本を 0.1 mm/画素の描画で回したときの値を既定にし、`scale` で伸ばす。顔の形に寄る長さ（`lips`・`jaw` の必須のキー）は、書いた値のまま。
- 知見: 開けた口は、あごが下がるぶん口角も下げる（開ける量の 27 % ほど。`corner_lift` を負に）。こうしないと、上の縁が平らで、口角がいちばん上にある
  「コップの形」になった。
- 限界: もとの口が、閉じた線 1 本の絵であることを使っている。開いた口の絵・歯の見える絵では試していない。
- 確かめたこと: CI で、色のかたまりの分け方と 3 次の補間が OpenCV と同じ値になること（OpenCV があるとき）、合成の顔（肌色の地に線 1 本と口角の印 2 つ）で、
  線を色で測ること、何もしなければ同じ絵になること、開けた口で口角の印が動かずすき間が口角で 0 になること、笑う口で口角が上がり横に広がること、あごの
  線の内側に収めること、scale が 1 以外（0.5・1.2・2）でも長さを伸ばして描けること、口の幅の半分以上の corner_mark を止めること、工程の受け渡しと悪い設定を止めることを確かめた。2026-11-23 に、4 作目の 3D のダンスの台本（台本をかけ直した絵は、残してある絵と
  SHA-256 まで同じ）と、同じ正面の描画（1600 画素）と同じ値（口 7 つ。値は照合の台本に置いた）で比べた（実測）:
  - 測った線（左右の端 678・873、真ん中 775.5、線の高さ、傾き 0.0303、あごの線 1434）と、写す範囲の枠・あごの線までの残り（23〜27 画素）は 7 つとも台本と同じ。
  - 描いた絵は、7 つとも差が最大 1/255。台本と同じく 8 ビットへ切り捨てて比べると、違う画素は 6,903〜10,555（写す範囲の約 12 万画素の中。台本は単精度、
    こちらは倍精度で、整数のすぐ下と上に分かれた所）。操作は 8 ビットへ四捨五入するので、出した PNG の違う画素は 7.3 万〜8.5 万（どれも 1/255）。
  - 写す範囲も差が最大 1/255。台本は単精度のぼかしの和が 1 にわずかに足りず、切り捨てで真ん中が 254 になる（こちらは 255）。

**描いた印のずれを、色で測る**（`check.drawn_mark`。Blender なし）: 4 作目の 3D のダンスで、口の中心を目盛りつきの拡大から目で読んで、5.7 mm ずらした。
描いた口が、もとの口と同じ場所にあるかを、目で読まずに色で測る（写した色の絵で描いた絵どうしを比べる。もとの顔と同じカメラ）。
- 入力 `reference`（もとの絵）と `image`（描いた絵。同じ大きさ）。出力 `report`。
- もとの印: 探す範囲 `window` の中で、印の色の決まり `reference_rule`（`red_minus_green_above`・`red_above`・`green_below`。必須。既定は無い）に入る画素を
  `close`（既定 5）× `close` の窓で閉じ、いちばん大きいかたまり（8 近傍）の左右・上下の端、中心の x（左右の端の真ん中。かたまりの重心は線の濃さで
  片寄る）、重心の y、中心の x のまわりの 2 割の列（幅 5 画素以下の細い印は、真ん中の列）の上の縁と下の縁、幅、面積。
- 描いた印: もとの印の枠を `search_margin`（[左, 上, 右, 下] 画素。既定 0）広げた枠（えりや髪の赤みを拾わない）で、`mark_rule`（省けば `reference_rule`。
  開けた口は、口の中と舌も入る決まりにする）の印を同じく測る。
- `close_halo`（既定 false）: true なら、色の決まりで拾って閉じる処理を、探す範囲の外に閉じる窓（`close`）の大きさの縁を足した所でしてから、探す範囲の中のかたまりを選ぶ。既定（台本と同じ）は探す範囲の中だけで閉じるので、印のすぐ外まで狭めた枠（`search_margin` 0）では、浅い弧の線が範囲の縁で埋まり、もとの印（広い `window` で測る）と形が変わることがある。4 作目の 3D のダンスの照合では、どちらでも同じ数になった。
- 中心の横のずれ・真ん中の列の上の縁の縦のずれ（開けた口は、もとの口の線の高さにあるはず）・幅の比・真ん中の列の高さ（画素。`metres_per_pixel` があれば mm も）。
- 2 枚の差（色のいちばん大きい値の差）が `change_threshold`（既定 0）より大きい画素の数と枠。`allowed_margin`（もとの印の枠から広げる [左, 上, 右, 下]）を
  渡すと、その枠の外で変わった画素の数（口だけを変えた絵なら 0 のはず）。
- しきい値（`max_centre_shift_px`・`max_top_shift_px`・`max_outside_pixels`）を渡したときだけ合否を出す。数を出す検査で、好みは判定しない。
- 確かめたこと: CI で、閉じてからかたまりを測ること（閉じないと線のすき間で分かれる）、印の測り方が OpenCV と同じ値になること（OpenCV があるとき）、合成の顔で
  5 画素ずらした印のずれ（0.5 mm）・許す枠の外の 1 画素・同じ絵、しきい値の合否と工程の要確認、悪い設定を止めることを確かめた。2026-11-23 に、
  4 作目の 3D のダンスの検査の台本と、同じ絵（もとの顔の描画と、表情 2 つの描画。1600 画素）と同じ値で比べた（実測）: もとの口・描いた口の測った値
  （端・中心・重心・上と下の縁・幅・面積）、中心と上の縁のずれ（−0.1 mm・+0.2 mm）、幅・高さ、変わった画素の数と枠、枠の外の数が、どれも台本と同じ。
  台本は広げた枠の右と下を含まない（ここでは含むので、右と下の幅を 1 小さく書いて合わせた）。
- 知見: 台本の記録の「枠の外で変わった画素」は、ふだんの顔（もとの描画と、表情の描画で描き方の違う絵）でも 20 万画素あり、使えなかった。もとの絵には、
  表情の絵と同じ描き方で描いたふだんの顔を渡す（目も変えた表情では、目の所も外に数える）。

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
- 足した描き方（`sag` の負・`strokes`・`skin_fill`・`mask`）は、CI で、今までの設定の結果が足す前とバイトまで同じこと（合成の顔の SHA-256）、負の `sag` で
  弧の真ん中が端より高くなること、曲線が真ん中で 2 点目を通り太さが位置で変わること、線ごとの色と重ねる順・`span`、肌で埋める消し方（枠の中の瞳と白目は
  肌の色になり、帯の髪と灰色の背景は使わない・変えない）、半目（上だけ埋めて縁に線。`exclude_boxes` を外すと白目の色が混ざる）、`mask` の PNG、
  広いぼかしが今のぼかしと同じ値になること、消す帯なしで線だけを描けること、肌らしい画素が届かない枠を止めること、悪い設定を止めることを確かめた。
  今までの設定は、工程（設定の組み立てから）を通しても、書いた PNG の画素が足す前と同じ（SHA-256）。
- 2026-11-23 に、4 作目の 3D のダンスの台本（OpenCV で 3 倍に広げて描く。目の絵 3 つ）と、同じ正面の描画（1600 画素、0.1 mm/画素）と同じ値（目の枠・
  点・色・太さは照合の台本に置いた）で比べた（実測）。台本をかけ直した絵は、残してある絵と SHA-256 まで同じ。比べたのは、もとの絵に、描いた絵を写す範囲で
  重ねた見た目（どちらかの範囲が 0 より大きい約 29 万画素）:
  - 閉じ目: 差の平均 0.71、差が 8 を超える画素 2,376、32 を超える画素 1,190、最大 139/255。笑顔の閉じ目: 0.77・3,292・1,415・137。大きい差は、線の縁に
    沿った 1 画素の幅だけ（絵で確かめた）。台本は線の点を 3 倍の画素へ切り捨て、太さを整数に丸め、OpenCV のなめらかな塗りで描く。移した口は小数のまま
    塗って縮める。
  - 線の無い肌の所（埋めた所）は、差の平均 0.54・0.58（半目 0.53）。台本は埋めた絵を 8 ビットへ切り捨ててから、広げて縮める（平均で約 0.5 暗い）。
    写す範囲は、閉じ目で差の平均 0.0011、片方だけ 0.5 を超える画素 1。
  - 半目: 描いた絵どうしは差の平均 0.92（台本の範囲の中）。見た目は差の平均 4.5。台本の写す範囲は、下ろした縁の下 24 画素の箱までで、目頭へ下がる線の
    端が箱の外で切れていた（写されていなかった）。移した口は、線の所を全部写す。
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

### 頭の候補の目の作りを調べる（`check.eyes`）

目を動かせる作りに直す（目の板・瞳・目の骨。3 作目の VRM のダンスのあとの試し）前に、頭の候補の目がどう作られているかを、数と絵で調べる。
頭によって目の作りが違う（3 作目の頭は、肌がまぶたのふちで奥へ折れてくぼみになり、白目はくぼみの奥の円すいの扇で肌とひと続き、瞳はくぼみに
浮いた別の部品だった。白目が肌と別の殻になった頭や、肌の面に目が描いてある頭もある）ので、どこを切って何を入れ替えるかを決める前に見る。
試しで目の作りを調べた台本（部品の一覧・扇・絵の中の場所・正面の絵・断面）を、決め打ちの箱と島の番号を外して移した。

- 数と絵だけを出し、合否は決めない（AGENTS.md の model3d の例外「構造と数値だけを報告し、好みは判定しない」）。`check.mouth` と同じく、頭の候補を
  選ぶ確認地点で使う。
- 入力 `mesh`（`model.export_mesh` の npz。頭は −Y を向き、長さは m）、任意で `texture`（色の絵。`check.mouth` と同じ読み方）。Blender なし
  （numpy と Pillow）で、同じ入力なら再利用する。
- パラメーター（箱に既定は無い。頭ごとに決める）:
  - `window`: `{"x": [x0, x1], "z": [z0, z1], "y": 中心の y の上限}`。中心がこの中にある部品（辺でつながった頂点の島。大きい順に番号。0 が
    いちばん大きい島＝肌で、いつも並べる）を並べ、正面の絵の範囲にもする。
  - `front_y`: 正面の絵に描く三角形（いちばん手前の頂点の y がこれより手前。頭の後ろ側を除く）。
  - `eyes.find`: `{"x": [内, 外], "z": [z0, z1], "front_y": y}`。目ごとに（右目 x < 0、左目 x > 0。x は |x| で）、この箱の肌の頂点のうち辺が
    いちばん多い頂点を、白目の扇の中心かもしれない所とする（`model.face_shapes` の `eyes.find` と同じ書き方）。
  - 絵の細かさ `resolution`（正面。既定 0.1 mm）・`section_pixel`（断面。既定 0.05 mm）・`top_pixel`（上から。既定 0.25 mm）・`margin`
    （断面の窓を扇の輪から広げる幅。既定 5 mm）。既定の値は仮の値（ユウの事前の指示で、親が推奨で決めた。2026-11-18）。
  - `sections`（任意）: `{"x": [縦に切る x …], "z": [横に切る z …], "y": [y0, y1]}`。無ければ目ごとの扇の輪の真ん中で切る。
  - `top`（任意）: `{"z": 切る高さ, "x": [x0, x1], "y": [y0, y1]}`。無ければ扇の中心の高さで頭の全体を切る。
  - `bones`（任意）: `[{"name", "head": [x, y, z], "tail": [x, y, z]}]`（m）。上から見た断面に目の骨の軸を描く（目の骨を球の中心へ移した後の
    しくみの図に使う）。
- 出す数（報告 `model3d-eye-check/0.1`）:
  - `parts`: 部品ごとの頂点と三角形の数・中心・範囲（`min`・`max`）、色の絵があれば色（三角形の uv の重心の色の平均）と、絵の中の場所（`uv_min`・
    `uv_max`・`px`＝画素の大きさ。肌は除く）。小さな部品の uv が絵の端から端まで広がっていたら、uv が壊れている（3 作目の頭の、瞳のまわりの
    かけらがそうだった）。
  - `eyes.R`・`eyes.L`: 扇の中心の頂点（`fan_centre`。辺の数 `valence`）と位置、それを使う三角形の数（`fan_tris`）、輪の頂点の数と範囲、円すいの
    深さ（中心が輪の平均より奥にある長さ。`cone_depth_mm`）、扇の絵の中の場所、輪の範囲（正面から見て）に中心がある肌でない部品（瞳・光・まつ毛の
    候補。`parts_in_ring`）。箱に肌の頂点が無ければ `found: false`。辺の数が少ない（数個）なら、扇の作りではない。
- 絵（任意）: `front`（正面。色の絵があれば絵の色、無ければ黒）、`parts`（部品の色分けと番号。肌は灰色）、`sections`（縦の断面を横に並べ、その下に
  横の断面を高さごとに右目と左目で並べる。肌は緑、扇は白、ほかの部品は部品の色と同じ）、`top`（上から見た断面。下が顔の前）。字は ASCII だけ。
  断面は三角形を面で切った線分で、面にちょうど乗る辺も描く（隣り合う三角形で 1 本）。横の断面の目のこまには、こまの幅に重なる線分を全部描く。

**確かめたこと**（`check.eyes`）:
- CI（Blender なし）では、合成の頭（穴の開いた肌の板・奥の扇・浮いた瞳・まつ毛）で、部品の一覧（肌・瞳・まつ毛）と色と絵の中の場所、扇（辺の数・
  輪・円すいの深さ・輪の中の部品）、扇の無い箱、絵の大きさと目の骨の軸、工程の受け渡し（要確認にしない・再利用する）と悪い設定を確かめた。
- 2026-11-18 に、3 作目の VRM のダンスの頭（試しの書き出しと同じ npz と色の絵）に、試しの調べ物と同じ箱でかけた（実測）: 窓の中の部品 14 個
  （頂点と三角形の数・中心・範囲・色）、左右の扇（中心の頂点・三角形 26・輪 26・範囲・絵の中の場所）、部品 10 個の絵の中の場所が試しの記録と同じ、
  正面の絵は画素まで同じ。約 7 秒。断面の切り口の直し（面にちょうど乗る辺・目のこまをまたぐ線分）のあとも、この頭では報告と 4 枚の絵が
  直す前と画素まで同じ（どちらもこの頭の切る位置では起きない）。合成の頭では、目の高さの面に瞳の直径が乗り、直す前は上から見た断面から瞳が消えていた。

**限界**: 扇の見つけ方は「辺がいちばん多い肌の頂点」なので、白目が肌と別の部品の頭（殻）や、肌の面に目が描いてある頭では、扇は見つからないか小さく出る
（そのときは部品の一覧と断面で見る）。部品の重み・目の骨・VRM の視線の設定は読まない（Blender の中の物。`check.vrm` の作りの一覧が視線を読む）。
3 作目の頭でしか確かめていない。

### 目を動かせる作りにする

3 作目の VRM のダンスのあとの試しで、目を作り直して動かせるようにした（視線・まばたき・瞳の変形）。考え方: 目の板（白目）を「球の面の
一部」にし、瞳を同じ中心の球（少し手前）に乗せ、目の骨の根もとを球の中心に置く。こうすると、骨を少し回すだけで、瞳は板から浮きも沈みもせずに
滑る（滑る量 = 半径 × 回す角。たとえば半径 10 cm なら、5 mm 滑らせるのに 2.9°）。瞳の変形は形状キーにする（VRM の表情に入る。骨の大きさは VRM の表情に入らない）。
ここに持ち帰ったのは、目の板と瞳が入った頭に、目の骨・まばたき・新しいキー・VRM の視線を付ける工程。どこを切って何を入れ替えるか（目の板と瞳を
入れる工程）は頭ごとに違うので、2 つ目の頭での当てはめを待ってから持ち帰る（ユウの決定）。それまでは、その工程が出すはずの値を
「目の形の値」として渡す。

**目の形の値**（`model3d-eye-geometry/0.1`。JSON）: 目の板と瞳を入れる工程が出し、下の工程が読む。位置は世界の座標（m。x はキャラの左が +、
y は手前が −、z は上が +）。`object`（頭のメッシュの物体の名前）と、`eyes.R`・`eyes.L` ごとに:
- `sphere`: 目の板を乗せた球の `centre`・`radius`（m）・`normal`（板の向き。長さ 1。目の骨の向きになる）
- `pupil`（瞳の中心の、正面から見た [x, z]）・`iris`（瞳の楕円の横とたての半径 `a`・`b`）
- `rim`: 目の開きの上下の縁（正面から見た x ごとの上の縁 `upper` と下の縁 `lower` の z。`x` は小さい順）
- `window`（目のまわりの窓 [x0, x1, z0, z1]）・`iris_vertices`（瞳の頂点の番号）・`lid_vertices`（見開きで動かすまぶたの頂点の番号）・
  `plate_vertices`（目の板の頂点の番号。あれば。確かめの絵で塗り分ける）

ユウの事前の指示で、親が推奨で決めた（2026-11-18）: 目の骨・まばたき・新しいキー・VRM の視線の工程は、この値を入力にする作りにし、目の板と瞳の
工程を持ち帰るときに、その工程がこの値を出すようにつなぐ。

#### 目の骨を球の中心へ（`model.eye_rig`）

- 入力 `blend`・`geometry`（目の形の値）。目の骨は `bones`（`{"R": 右目の骨, "L": 左目の骨}`）か、省くと VRM の人型の骨の割り当て
  （`right_eye`・`left_eye`）。`armature`（省くと人型の骨の割り当てのあるアーマチュアが 1 つのときそれ）。
- すること: 目の骨の頂点グループが無ければ左目・右目の順に足し、瞳の頂点（`iris_vertices`）をほかの頂点グループから外して、その目の骨に重み 1 で
  付ける。目の骨の根もと = 球の中心、先 = 中心 + 板の向き × 半径（骨の先がちょうど板に届く）、つながり（`use_connect`）を外し、ロールは世界の
  上（+Z）にそろえる（アーマチュアの物体が回っていれば、世界の上をアーマチュアの中の向きへ直してからそろえる）。板が顔の横向きにかたむいていると、
  根もとは頭の反対側の後ろに来る。作業用のシーンを開いたまま保存されたファイルでも、アーマチュアのあるシーンに切り替えてから直す（ほかの VRM の工程と同じ）。
- 出力: `blend`（入力は変えない）、`report`（足した頂点グループ・目ごとの瞳の頂点の数・骨の前と後の根もとと先（アーマチュアの中の座標）・
  世界での長さ `length_mm`（アーマチュアの物体の大きさを入れる））。

**確かめたこと**（`model.eye_rig`）:
- CI（Blender なし）では、目の形の値の確かめ（向きの長さ・窓・縁の並び・頂点の番号の重なりと範囲ほか）、骨の根もとと先の計算とアーマチュアの中への
  移し方、目の骨の名前の決め方、工程の受け渡し（代役の Blender）と悪い設定を止めることを確かめた。
- 2026-11-18 に Windows の Blender 5.2.4（VRM Add-on 4.10.5）で、試しで目の板と瞳だけを入れた途中の頭に、試しの値（目の形の値に写した物）でかけ、
  試しのできあがりの頭と、試しの書き出しの道具で比べた（実測）: 頂点・面・uv・頂点グループの並び・重み（頂点・頂点グループ・値）・骨（目の骨 2 本と
  頭の骨の初期姿勢の行列と長さと親）・色の絵が同じ。違うのは、まだ付けていないまばたきのキーの直し・新しいキー・VRM の視線と表情だけ。
  同じ頭のアーマチュアの物体を X のまわりに 30°・Z のまわりに 20° 回して大きさ 2 にした写しでも、目の骨の根もとと先は世界の座標の球の中心と先に
  あり（ずれ 1.2e-7 m 以下）、骨の x 軸は水平（世界の上との内積 1.4e-7 以下）、報告の長さは球の半径と同じ。直す前のコードを同じ写しにかけると、
  骨の x 軸は世界の上へかたむき（内積 0.40・−0.11）、長さはアーマチュアの中の長さで半分に出た。回していない頭の結果は、直す前と
  `check.blend_match` で同じ（exact）。同じ頭に作業用のシーンを足し、それを開いたまま保存した写しでも、報告と骨の置き場所が回していない頭と
  同じ（直す前のコードは、アーマチュアが今のビューレイヤーに無くて止まった）。

#### まばたきのキーを直し、目の新しいキーを足す（`model.eye_keys`）

目の板と瞳を入れると、もとのまばたきのキーでは、閉じたまぶたが板の後ろに沈んだり、閉じきらずにすき間が残ったりする。この工程は、まばたきの
キーをそれに合わせて直し、瞳の変形と見開きのキーを足す（どれも VRM の表情に入れられる形状キー）。

- 入力 `blend`（目の板と瞳を入れた頭。`model.eye_rig` のあとでよい）・`geometry`（目の形の値）。
- 量（どれも要る。既定は無い）: `lid_front`（もとが板より何 m 手前にある頂点を、下りてくるまぶたと見なすか）、`lid_margin`（下りてきた
  まぶたを、板より何 m 手前に置くか）、`close_below`（閉じた上まぶたを、下まぶたの
  ふちより何 m 下まで届かせるか）、`close_reach`（もとのまばたきのキーが、開きの何割まで閉じるか。0 より大きく 1 以下）、`close_max_scale`
  （伸ばす倍率の上限。1 以上）、`wide_up`・`wide_down`（見開きで上まぶたを上げる・下まぶたを下げる量 m）、`iris_small`（瞳が縮むキーの倍率）、
  `iris_stretch`・`iris_squash`（横へ伸びる・つぶれるキーの [横の倍率, たての倍率]）、`iris_lift`（瞳を乗せる球の、板の球からの浮き m）。
- 見開きの形（どれも要る。既定は無い）: `side_fade`（目の幅の外で 0 へ減らす幅 m）、`bump_power`（目頭と目尻へ減らす sin の指数）、
  `upper_fall`（[上の縁から減らし始める所 m, 減らす幅 m]）、`lower_fall`（下の縁から減らす幅 m）。
- 量と形に既定を置かないのは、頭に合わせて決める値だから（ユウの事前の指示で、親が推奨で決めた。2026-11-18）。
- 名前: `blink`（`{"R", "L"}`。既定 `blink_R`・`blink_L`）、`smile`（笑顔の閉じ目。既定 `blink_smile_R`・`blink_smile_L`。`false` で直さない）、
  `names`（新しいキーの名前の頭 `{"wide", "small", "stretch", "squash"}`。既定 `eye_wide_`・`iris_small_`・`iris_wide_`・`iris_squash_`。後ろに
  `R`・`L` を付ける）。同じ名前の形状キーがもうあれば止める。`blink` と `smile` の名前が重なる（同じキーを 2 回直してしまう）とき、目の形の値に
  `lid_vertices`（見開きのキーに要る）が無いときも止める。頭の形状キーが絶対（Relative のチェックなし。時間で形を選ぶので値 0 で消せず、表情として
  入り切りできない）なら、キーを変える前に止める（`model.closed_eye_key` と同じ考え）。
- まばたきの直し（右目・左目の順。頂点ごと）: 目の窓（`window`）の中で、目の球の中心より板の側（板の向きの側。顔の前）にあって、キーで動く頂点
  だけを見る（瞳の頂点は見ない。試しは世界の y < 0 で前を選んでいた。頭が動いても同じ頂点を選ぶよう、目の球を基準にした）。まばたきでは、下りてくる
  上まぶたを `(開き + close_below) ÷ (close_reach × 開き)` 倍（1〜`close_max_scale`）に伸ばす（開きは、その x での上の縁と下の縁の差）。笑顔の
  閉じ目は伸ばさない。もとが板より `lid_front` より手前にある頂点（下りてくるまぶた）が、球（半径 + `lid_margin`）より内に入ったら、その球の面へ押し出す。瞳の頂点は、
  キーでは動かさない（キーの値を土台の位置に戻す）。
- 新しいキー（右目・左目の順に、瞳が縮む・横へ伸びる・つぶれる・見開き）: 瞳のキーは、瞳の頂点を瞳の中心のまわりに横・たての倍率で動かして、
  球（半径 + `iris_lift`）の面に乗せる。つぶれるキーの下限（`slider_min`）は −1（負の値でたてに伸びる）。見開きは、まぶたの頂点
  （`lid_vertices`）を、上まぶたは上へ `wide_up`、下まぶたは下へ `wide_down` 動かす（目の幅の真ん中でいちばん大きく、目頭と目尻へ
  `sin` の `bump_power` 乗で減らす。縁から離れると `upper_fall`・`lower_fall` で減らし、目の幅の外は `side_fade` で 0 へ）。
- 計算の桁: 頭の物体の行列は Blender が持つ 32 ビットのまま使い、逆の行列も 32 ビットで求める。まばたきの直しは、頂点の位置も 32 ビットから
  始める。新しいキーは 64 ビットで計算して、書くときに 32 ビットに丸める。どれも試しと同じ（Blender 5.2.4 の numpy 2.3.4 では、
  `np.array(matrix_world)` と `np.array(vertex.co)` が 32 ビットになる）。
- 出力: `blend`（入力は変えない）、`report`（まばたきのキーごとに、伸ばした頂点・押し出した頂点の数と押し出した最大 mm・戻した瞳の頂点の数。
  新しいキーごとに、動く頂点の数と最大 mm）。

**確かめたこと**（`model.eye_keys`）:
- CI（Blender なし）では、合成の目（試しとは無関係の値）で、まばたきの直し（下まぶたのふちの下まで届く・押し出した頂点が球の面に乗る・
  板より奥の頂点は押し出さない・窓の外と動かない頂点と瞳はそのまま・笑顔の閉じ目は伸ばさない）、瞳のキー（倍率と球の面）、見開きのキー
  （真ん中・下まぶた・縁から離れた所・目の幅の外）、行列の桁、新しいキーの順、工程の受け渡し（代役の Blender）と悪い設定を止めることを確かめた。
- 2026-11-18 に Windows の Blender 5.2.4（VRM Add-on 4.10.5）で、試しで目の板と瞳だけを入れた途中の頭に、`model.eye_rig` → `model.eye_keys`
  の順に試しの値（照合のレシピ。公開しない）でかけ、試しのできあがりの頭と、試しの書き出しの道具で比べた（実測）: 頂点・面・uv・重み・骨・
  形状キー 20 個（直したまばたき 4 つと新しいキー 8 つを含む）・キーの下限・色の絵の 28 個の配列が、1 ビットまで同じ。記録の数（伸ばした・
  押し出した頂点の数と最大 mm、新しいキーの動く頂点の数と最大 mm）も、試しの記録と同じ。違うのは、まだ付けていない VRM の視線と表情だけ。
- はじめの版は逆の行列を 64 ビットで求めていて、まばたきのキーで最大 3.0e-7、新しいキーで 6.0e-8（頭の中の座標。32 ビットの小数の最後の
  桁）の差が出た。試しが 32 ビットの行列の逆を使っていると分かり、そろえた。
- 前の選び方を目の球を基準に直したあとも、照合の頭で同じ 28 個の配列が 1 ビットまで同じ、記録も同じ（この頭では選ぶ頂点が変わらない）。
  CI では、頭と目を一緒に後ろへ 0.5 m 動かしても、直す頂点と直し方が同じことを確かめた（直す前は、まぶたの頂点がどれも外れた）。
- 照合の頭の形状キーを絶対にした写しでは、キーを変える前に止まり、出力を書かない（直す前のコードは、まばたき 4 つを直して新しいキー 8 つを足し、
  成功していた）。止める確かめを足したあとも、照合の頭の結果は同じ。

#### VRM の視線を目の骨で（`vrm.look_at`）と、驚きの表情

目の骨の根もとが目の球の中心にあると、骨を θ 回すと瞳は板の上を 半径 × θ 滑る。視線は「瞳が滑る量（m）」で決め、骨を回す角度は半径で割って
出す（頭や半径が変わっても、決めた量のまま使える）。

- 入力 `blend`（目の骨を球の中心へ移した頭。`model.eye_rig` のあと）・`geometry`（目の形の値。半径は左右の球の平均。左右が違えば報告に両方
  残す）。`armature`（省くと人型の骨の割り当てのあるアーマチュアが 1 つのときそれ）。
- `slide`（`{"horizontal": m, "vertical": m}`。視線の入力がいちばん端のときに、瞳が板の上を滑る量）と `input_max`（視線の入力のいちばん
  大きい角度 °。180 まで）。どちらも既定は無い（頭に合わせて決める。ユウの事前の指示で、親が推奨で決めた。2026-11-18）。
- すること: VRM 1.0 の視線（lookAt）の type を `bone` にし、4 つの範囲（横の内・外、たての下・上）の入力の最大を `input_max`、出力の倍率を
  degrees(`slide` ÷ 半径)（横は `horizontal`、たては `vertical`）にする。VRM Add-on の設定の値を書くだけ。VRM 1.0 でないアーマチュアは止める
  （VRM 0.x の視線は別の所にあり、書いても書き出しに入らない）。
- 出力: `blend`（入力は変えない）、`report`（前と後の type と 4 つの範囲・使った半径・回す角度）。
- 驚きの表情に見開きと縮む瞳を足すのは、今の `vrm.expressions` でする: `binds` の `surprised` に、もとからある割り当て（眉など）と、
  `eye_wide_L`・`eye_wide_R`・`iris_small_L`・`iris_small_R` を並べる（`vrm.expressions` は、挙げた表情の割り当てを外してから、挙げた順に
  入れ直す。もとの割り当ては `check.vrm` か報告の `before` で分かる）。VRM の表情の重みは 0〜1 なので、キーを 1 より大きくする行きすぎは
  VRM には入らない。

**確かめたこと**（`vrm.look_at` と、目を動かせる作りの全部）:
- CI（Blender なし）では、回す角度の計算と 4 つの範囲の並び、工程の受け渡し（代役の Blender。左右の半径が違うとき）と悪い設定を止めることを
  確かめた。
- 2026-11-18 に Windows の Blender 5.2.4（VRM Add-on 4.10.5）で、試しで目の板と瞳だけを入れた途中の頭に、`model.eye_rig` →
  `model.eye_keys` → `vrm.look_at` → `vrm.expressions`（`surprised`）の順に試しの値（照合のレシピ。公開しない）でかけ、試しのできあがりの
  頭と、試しの書き出しの道具で比べた（実測）: 28 個の配列（頂点・面・uv・重み・骨・形状キー 20 個・キーの下限・色の絵）と VRM の設定
  （視線の type と 4 つの範囲・驚きの表情の割り当て 6 つ）が、全部同じ（試しの比べる道具の `exact: true`）。
- 同じ頭のアーマチュアの VRM の版を 0.0 にした写しでは、書く前に止まる（VRM 0.x の視線は別の所にあり、VRM 1.0 の設定を書いても書き出しに
  入らない。止める確かめを足す前は、VRM 1.0 の視線の設定を書いて成功していた）。
- VRM を読む側のアプリで、視線が骨で動くかは確かめていない。

#### 確かめの絵（`render.eye_sheet`）

目を動かせる作りにした頭を、姿勢 × 見る向きで描いて、1 枚に並べる。正面だけでは気づけない、斜めや下から白目や板がのぞく、瞳が板から浮く・
沈む、閉じたまぶたにすき間が残る、などを見るため。合否は決めない。

- 入力 `blend`・`geometry`（目の形の値。視線を骨の角度にする半径は、左右の球の平均）。目の骨は `bones` か VRM の人型の骨の割り当て。
- `poses`（要る）: `[{"name", "gaze": [横 m, たて m], "keys": {名前: 値}}]`。視線は、瞳が板の上を滑る量（横は + がキャラの左＝画面の右、
  たては + が上）で書き、目の骨を（たて ÷ 半径, 0, 横 ÷ 半径）ラジアン（XYZ）回す。キーの名前は、名前 + `_L`・`_R` があれば両方に、無ければ
  その名前に入れる（無い名前は報告の `unknown_keys`）。姿勢ごとに、ほかのキーは 0 に戻す。
- `views`（要る）: `[{"name", "yaw", "pitch", "target", "span", "distance"}]`。狙う点 `target`（世界の座標）を、`yaw`（+ でキャラの左側へ回り
  こむ）と `pitch`（+ で上から。±89° まで）の向きから `distance`（既定 0.5 m）離れて見る。`span` は画面の横に入る幅（m）。
- `paint`（`true` か `{"plate": 色, "iris": 色}`）: 目の板と瞳の面を塗った材質に替えて描く（`true` は板が赤紫・瞳が緑）。顔の面から
  はみ出していれば、目の開きの外に色が出る。目の形の値の `plate_vertices`（目の板の頂点の番号。目の板と瞳を入れる工程が出す）が要る。
- 描き方は試しと同じ: Workbench、光は FLAT、色は絵の色そのまま、裏も描く、影・くぼみ・輪郭線なし、色の変換は Standard、アーマチュアは描かない。
  `size`（既定 [960, 540]）・`background`（既定 [0.40, 0.39, 0.38]）・`aa`（既定 "8"）・`hide`（描かない物体）。並べる側は `cell`（こまの横の画素。
  既定 480。縦は絵と同じ比。こまの縦が枠の 2 画素を引いて残らない `size` と `cell` の組み合わせは、描く前に止める）・`quality`（既定 90）・`font`・
  `font_size`。既定の値は仮の値（試しの描き方に合わせた。ユウの事前の指示で、親が
  推奨で決めた。2026-11-18）。作業用のシーンを開いたまま保存されたファイルでも、アーマチュアのあるシーンで描く。頭の形状キーが絶対（Relative の
  チェックなし）なら、値で姿勢を作れないので止める（`model.eye_keys` と同じ）。入れる値が効くように、また .blend から引き継ぐと絵をすり替え
  うる設定は、この場で（保存しない）`render.face_sheet` と同じに直す: 入れるキーのミュートを外し、範囲を 0 と入れる値が入るまで広げ、形状キーの
  動きとピンを外す。シーケンサー・コンポジター・切り取り・色の補正・出力の形式を決まった値にし、マーカーに結んだカメラを外し、頭の描かない設定を
  外す（描かないコレクションの中にしか無ければ止める）。アーマチュアは姿勢の位置（POSE）にし（静止の姿勢のままだと目の骨を回しても動かない）、
  カメラはセンサーを横で合わせる（`span` は画面の横に入る幅。縦長の絵でも）。アーマチュアの動き（アクション・ドライバー・NLA）は外し（今の
  姿勢のまま止まる）、目の骨の拘束は止める（どちらも入れた視線を上書きする）。報告の `prepared` に、直した物を残す。
- 出力: `sheet`（.jpg か .png）、`report`（描いた絵の一覧・塗った面の数・見つからなかったキー）。描いた絵は工程のフォルダの `views` に残る。

**確かめたこと**（`render.eye_sheet`）:
- CI（Blender なし）では、視線から骨の回し方・キーの名前の広げ方・見る向き・絵の並べ方（こまの比）と、工程の受け渡し（代役の Blender）と悪い
  設定を止めることを確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、目を動かせる作りにした頭（上の照合で試しのできあがりと同じになった物）を、試しの確かめの絵と同じ
  姿勢と向き（照合のレシピ。公開しない）で描き、試しの絵と画素で比べた（実測）: 姿勢 × 向きの一覧 60 枚のうち 56〜58 枚（2 回描いた）、
  はみ出しの絵 48 枚のうち 47 枚が画素まで同じ。残りも 1〜10 画素の差で、2 回描くと違う絵が同じになり別の絵が揺れる（Workbench の描くたびの
  揺れ）。並べた絵を目で見て、視線の向きと、板と瞳の色が目の開きの中だけに出ていることを確かめた。
- 作り直しの照合（試しのできあがりと同じか）は、`check.blend_match` でも `exact`（8 つのメッシュの頂点・面・uv・属性・置き場所と、骨の静止と
  姿勢）。形状キーと VRM の設定は `check.blend_match` の外で、上の照合（試しの書き出しの道具）で同じことを確かめた。
- 描く前の手当て（範囲・シーケンサーなど）を足したあとも、照合の頭の絵は足す前と 108 枚のうち 107 枚が画素まで同じ（1 枚は 1 画素の揺れ。
  この頭で変わった設定はシーケンサーとコンポジターを切る 2 つだけで、素材は無い）。シーケンサーに色の素材を置いた写しでは、足す前は絵が全部
  その色に置き換わり、足したあとは通常と同じ。見開きのキーに範囲の外の 1.5 を入れると、足す前は 1.0 と同じ絵（切られた）、足したあとは 1.5 が
  効いた絵になる（1.0 の絵は足す前と同じ）。アーマチュアを静止の姿勢にして保存した写しでは、POSE にする前は視線を変えても絵が 1 画素も
  変わらず、POSE にしたあとは視線が効く。縦長の絵（[540, 960]）では、センサーを横で合わせたあと、2 つの瞳の間が横長の絵（[960, 540]）の
  0.562 倍（絵の横の比 540/960 と同じ。横に入る幅が `span` のまま）。合わせる前は、この比にならなかった（縦で合わせていた）。どちらの直しの
  あとも、照合の頭の絵は直す前と同じ（揺れの数画素を除く）。目の骨に回りのキーを打った写しでは、動きを外す前は視線を変えても絵が 1 画素も
  変わらず（動きが視線を上書き）、外したあとは視線が効いて、動きの無い頭と画素まで同じ絵になる。照合の頭には動きも目の骨の拘束も無い。

#### 保存した顔リグで、首振りと表情を確かめる

`render.eye_sheet` の `views` はカメラの向きを変える。首を傾けたときの髪やまぶたの変形を調べるには、
保存した `.blend` を開き直し、カメラ・照明・画角を固定して、モデル側の操作で次の絵を作る。

| 操作 | 見比べるもの |
|---|---|
| 頭と表情を固定し、視線だけを変える | リグで視線に連動させた部位（例：虹彩とその反射）を先に挙げる。正面と首を傾けた姿勢のそれぞれで、視線を変える前後の位置を頭の座標系で比べる。顔全体が引かれた疑いがあれば、頭のワールド変換と肩の位置も比べる。 |
| 同じ視線で通常のまばたきと笑顔の閉じ目を切り替える | 斜めから目尻・目頭を拡大し、閉じ切ったコマで白目や反射が露出していないかを見る。タイムラインも同じコマまで再生し、手で閉じたときの形と見比べる。 |
| 口の開きと口角を組み合わせ、表情ごとに並べる | 唇を閉じる表情では合わせ目から歯がのぞかないか、半開きでは歯が唇を突き抜けず口内の奥行きが読めるかを、正面と斜めで見る。口の周りに輪が出たら、境界の頂点位置に加え、両側の材質が参照する肌色・影の頂点属性も照合する。 |
| 首を左上・右上・左下・右下へ動かし、隣り合う姿勢の間を再生する | 毛束を根元から先まで追う。端で隠れた根元が中間で四角くのぞいたり、肩に髪が入って先端だけが点として見えたりする。直した側と反対側も同じカメラで描き直す。 |

肩に黒い点が出たときは、そのコマのカメラから画素を通るレイを飛ばし、最初に当たる面を調べる。
透過を使う毛束では透明な画素にもレイが当たる。元の透過を保ち、毛束と肩を別々の発光色にした比較用の絵で、その画素に見えている部位を確かめる。
肩の面なら、影を一時的に切った絵と比べる。髪の面なら、変形後の毛束と肩を同じ座標系で取り出し、問題の区間で肩の表面に対する位置を調べる。
外向きの法線が確認できる滑らかな部分では、肩の最近点から毛先へのベクトルと法線の内積を取り、局所的に表側か裏側かを符号で区別できる。
調べたコマと部位を控え、補正後は同じ区間をもう一度通す。最後に修正前後のモデルを同じ中立姿勢に戻す。
頂点の対応が保たれた部位では、リグの変形を評価した後の頂点位置を同じ座標系で照合すると、採用済みの形を保てたか分かる。

### 目の動きの台本（`motion.eye_plan`・`render.eye_captions`）

目を動かせる作りにした頭に、視線・まばたき・驚き・笑顔の演技を付ける。視線は「瞳が目の板の上を滑る量（m）」で書き、骨の角度は目の球の半径で
割って出す（頭や半径が変わっても、台本を変えずに使える）。台本は演技の時間 τ（秒）で書き、画面の時間は区間（`sections`）の順に τ をたどる
（同じ τ をゆっくり見せ直す区間も書ける）。

**台本**（`model3d-eye-plan/0.1`。JSON。時刻・速さ・量はどれも書く。既定は無い。頭と演技に合わせて決める値だから。ユウの事前の指示で、親が
推奨で決めた。2026-11-18）:
- `fps`。`gaze`: `events`（`[τ, 横 m, たて m]` の並び。τ の順。横 + はキャラの左、たて + は上）、`saccade`（`base_s`・`per_m_s`・`overshoot`・
  `settle_s`・`settle_decay`）、`circle`（なめらかに回す。`start`・`end`・`h_m`・`v_m`・`ramp_s`、`vertical_ramp`（`"once"` 既定・`"squared"`）。
  無くてよい）、`tremor`（驚いた直後の小さな
  震え。驚きの始めからの `start_s`・`end_s`、`h_m`・`v_m`・`h_hz`・`v_hz`・`phase`。無くてよい）。
- `blink`: `starts`（まばたきの始まりの τ）、`close_s`・`hold_s`・`open_s`・`squash_open_s`・`length_s`、`stretch`（開くときに瞳がたてに伸びて
  戻る。`start_s`・`length_s`・`amount`・`taper`）、`overopen`（まぶたが少し開きすぎて戻る。`start_s`・`length_s`・`amount`）。
- `surprise`（無くてよい）: `start`・`release`・`end`、`squint`（`amount`・`length_s`）、`wide`・`small`（[始め, 立ち上がり, 行きすぎ, 戻り] 秒と
  割合）、`brow`（[始め, 立ち上がり]）。`smile`（無くてよい）: `start`・`ramp_s`・`brow`。
- `lids`（視線につられるまぶた。`up_per_m`・`down_per_m`）、`iris_wide`（横を見ると瞳が横へ伸びる。`amount`・`full_m`）。
- `sections`: `[τ の始め, τ の終わり, 速さ, 向き, 説明]` の並び。区間ごとに (終わり − 始め) ÷ 速さ × fps のコマ（四捨五入）で τ を等分する。
  速さ 0.5 なら、同じ動きを半分の速さで見せ直す。向きと説明は、描く側と字を入れる側が使う。
- 計算の式は `model3d/eye_plan.py` の頭に書いた（視線の飛びは速く動いて少し行きすぎてから止まる、まばたきは閉じて・閉じたまま・開き、瞳は
  つぶれてから開くときにたてへ少し伸びて戻る、驚きは一瞬すくんでから見開き瞳が縮む、など）。返すキーは `blink`・`blink_smile`・
  `mouth_smile`・`brow_up`・`eye_wide`・`iris_small`・`iris_wide`・`iris_squash`（名前 + `_L`・`_R` があれば両方に入れる）。瞳のつぶれの戻りは、
  まぶたの開き（`open_s`）と別に `squash_open_s` の間続く（試しは開き終わった所で打ち切り、`squash_open_s` が長いと瞳が急に元の形へ戻った。
  照合の台本は `squash_open_s` が `open_s` 以下なので、試しと同じ値）。
- なめらかに回す目の入りと出のゆるめ（`ramp_s`）は、既定では横にもたてにも 1 回掛ける。試しの式は、横に 1 回、たてには 2 回掛けていた（たて
  だけゆるめの二乗。ゆるめの間は円が横に押しつぶれる。そうする理由は書かれておらず、誤りに見える）。ユウの答え（2026-11-18）で、直した式を既定に
  し、試しの式は `vertical_ramp: "squared"` で残した。既定と `squared` で違うのは、ゆるめの間のたての視線（と、それにつられるまぶたの見開きの
  キー）だけ。照合は `squared` で取った（照合の台本を既定で計算し直すと、変わるのはゆるめの間のコマのたての視線と見開きだけだった。実測）。

**`motion.eye_plan`**:
- 入力 `blend`（目を動かせる作りにした頭）・`geometry`（目の形の値。半径は左右の球の平均）・`plan`。`start`（台本の最初のコマを置くシーンの
  コマ。既定 1）、`bones`・`armature`。
- シーンの fps と台本の `fps`（キーは台本の fps でコマに置く。違う fps のシーンでは、シーン ÷ 台本 倍の速さで再生される）。ユウの答え
  （2026-11-18）で 3 つの場合にした: 省くと、2 つが違えば止める（2 つの fps と、下の 2 つの意味を言う）。`fps: "set"` は、アーマチュアのある
  シーンを台本の fps にしてから打つ（保存する。シーンのほかの動きの速さも変わる）。`fps: "keep"` は、シーンの fps のまま打つ（コマを 1 枚ずつ
  描いて、台本の fps でまとめるとき）。報告の `scene_fps` に、シーンと台本の fps とどうしたかを残す。
- すること: コマごとに、頭の形状キーの値と目の骨の回転（XYZ。たて ÷ 半径, 0, 横 ÷ 半径）にキーを打つ。形状キーのキーは頭の形状キーの動き、
  目の骨のキーはアーマチュアの動きに入る（踊りの .blend なら、踊りの動きに目の骨の回転を重ねる。目の骨にもうキーがあれば、そのコマは
  上書き）。台本の値が形状キーの範囲（`slider_min`・`slider_max`）を出るなら、範囲を広げる（行きすぎや瞳の伸びで 1 を超えたり負になったり
  する。広げないと Blender が値を範囲に収めてしまう）。描かない。頭の形状キーが絶対（Relative のチェックなし）なら、値のキーで動かせないので
  止める（`model.eye_keys` と同じ）。打つキーをドライバーが上書きする（ドライバーは F カーブのキーより強い）とき、目の骨に止めていない拘束が
  あるとき、打つ形状キーがミュートされているとき、頭が形状キーを 1 つだけ見せる設定（ピン）のとき、アーマチュアが静止の姿勢（REST）のときも
  止める（キーは入っても動かない .blend を書かない。.blend は保存するので、この工程では外さない）。打つキーの F カーブがミュート・ロックされて
  いるか、止めていない修飾子（ノイズなど）があるとき、NLA の帯を編集する状態（tweak）のとき、NLA の層が打つ値を動かしているとき（上のアクション
  に打ったキーが、台本の外のコマでも層の動きを上書きする）、目の骨の回りが XYZ でなく回りのキーかドライバーがあるとき（この工程は回りを XYZ に
  してキーを打つので、クォータニオンなどのもとのキーやドライバーが台本の外のコマでも効かなくなる）、キーを打てないとき（`keyframe_insert` が False。NLA の層の上の
  アクションの混ぜ方や影響で、打つ値を割り戻せないときなど）も止める。組だけのミュートと、層の上の混ぜ方や影響は止めない（Blender 5.2.4 は、
  組をミュートしても中の F カーブを再生し、層の上では打つ値を割り戻してキーにする。実測）。シーンのコマの範囲は、打つコマが入るまで
  広げる（もとの範囲は残す。再生や描画が範囲で止まらないように。報告の `scene_frames`）。
- 出力: `blend`（入力は変えない）、`timeline`（`model3d-eye-timeline/0.1`。コマごとの τ・区間・視線・キーの値と、区間ごとのコマの範囲・速さ・
  向き・説明）、`report`（キーを打った形状キー・広げた範囲・見つからなかったキー・区間）。

**`render.eye_captions`**: 描いたコマ（`frames_dir` の `name`。既定 `f{frame:04d}.png`）に、timeline の区間の説明と題（`title`）を入れて、
別のフォルダ（`out_dir`。既定は工程のフォルダの `frames`）へ書く。入れ方は試しと同じ（基準の幅 1920 で、下に高さ 84・濃さ 150/255 の黒い帯、
説明は 40、題は 26 に縁取り 3。仮の値。`reference_width` などで変えられ、コマの幅に合わせて掛ける）。書体は `font`（説明）と `title_font`
（題。省くと `font`）。省くと Pillow の内蔵の書体（ラテン文字だけ）なので、日本語の説明には書体のファイルを渡す。元のコマは書き換えない
（`out_dir` が `frames_dir` と同じなら止める。`name` はファイルの名前だけで、フォルダを含めると止める。書く前にも、書く所が元のコマと同じ
ファイルにならないかを確かめる）。timeline のコマのファイルが無ければ止める。大きさはコマの幅に合わせるので、とても横長の
コマで帯がコマより高くなる・字がコマの外に出るときは、書く前に止める（`reference_width` を大きくするか、縦の大きいコマにする）。

**確かめたこと**（目の動きの台本）:
- CI（Blender なし）では、合成の台本（どの演技とも関係の無い値）で、視線の飛び（かかる時間・行きすぎ・止まる）、まばたき（閉じ・閉じたまま・
  開いたあとの瞳の伸びとまぶたの開きすぎ）、驚き（すくみ・見開き・ゆるめ）、笑顔、視線につられるまぶた、横で瞳が伸びる、見せ直しの区間の
  コマの数と番号、台本の確かめ、形状キーの範囲の広げ方、字の入れ方（帯・説明・題・大きさ）、工程の受け渡し（代役の Blender）と悪い設定を
  止めることを確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、試しの台本（照合のレシピ。公開しない）を写して照合した（実測）:
  - 台本の計算: 試しが残したコマごとの値（725 コマ。τ・区間・視線・キー 8 つ。試しが書いた桁で丸めた値）と全部同じ。
  - .blend のキー: 目を動かせる作りにした頭（上の照合で試しのできあがりと同じになった物）にかけ、725 コマを動かして読み直すと、形状キーの値
    10,875 個と目の骨の回転が timeline と 32 ビットまで同じ。
  - 描いた絵: 区間ごとに 1 コマ、試しと同じカメラと描き方で、キーから（コマごとに値を入れずに）描くと、8 コマ中 7 コマが試しの絵と画素まで
    同じ（説明の字の所を除いて比べた）。残りの 1 コマと、瞳の横の伸びが 0 をわずかに下回るコマは、瞳のふちだけ違う。試しは
    そのキーの下限を広げずに描いたので、Blender が 0 に収めていた（この工程は台本どおりの値を入れる）。
  - 説明の字: 同じ 7 コマに、試しと同じ書体と題で字を入れると、試しの字入りのコマと絵全体が画素まで同じ。
- 出す前に足した止め（実測）: 頭の形状キーを絶対にした写しでは、足す前は 725 コマに 15 個のキーを打って成功にしていた（値のキーは効かない）。
  `blink_L` に 0 を返すドライバーを付けた写しでは、足す前はキーを打って成功にしていたが、台本でまばたき 1.0 のコマでも評価したあとの値は 0。
  目の骨に回りを留める拘束を付けた写しでは、足す前は視線のキーで回るはずのコマでも、評価したあとの回りはほぼ 0（2e-8 ラジアン）。足したあとは、
  どれも止まる。止めを足したあとも、台本の照合（報告・設定・timeline）と字入りの 7 コマは足す前と同じ。`blink_L` をミュートした写しでは、
  足す前はまばたき 1.0 のコマで左目の頂点の動きが、ミュートしない頭の 2 割ほどしか無く、ピンの写しでは頭のどの頂点もほぼ動かなかった
  （0.001 mm より小さい）。足したあとは、どちらも止まる。
- 照合の頭はシーンが 24 fps・台本が 30 fps（試しはそのままコマを 1 枚ずつ描いていた）。省くと、2 つの fps と set・keep の意味を言って止まる。
  `fps: "keep"`（照合のレシピ）では、報告と timeline が fps の扱いを足す前と同じ。`fps: "set"` では、保存したシーンが 30 fps になり、打った
  キーは keep と同じ。照合の頭のシーンは 1〜250 コマで、台本は 725 コマ。範囲を広げたあとは、保存したシーンが 1〜725 コマ（報告と timeline は
  広げる前と同じ）。アーマチュアを静止の姿勢にした写しでは、止めを足す前は、視線のキーで回るはずのコマでも評価したあとの目の骨の回りがほぼ 0 の
  まま成功にしていた。足したあとは止まる。
- 打つ F カーブ・NLA の層・目の骨の回りの止め（実測。照合の頭の写し）: 打つ形状キーの F カーブをミュートした写し・ロックした写し・ノイズの
  修飾子を付けた写しと、NLA の層の上のアクションを MULTIPLY にした写しでは、止めを足す前は成功にしていたが、まばたき 1.0 のコマで評価した
  あとの値が、ミュート・ロック・MULTIPLY では 0、ノイズでは 1 からずれた（ロックと MULTIPLY ではキーが入らなかった）。目の骨の回りを
  クォータニオンにして台本の前のコマにキーを打った写し・その回りをドライバーで動かした写しと、形状キーを動かす NLA の層を置いた写し（どれも
  台本は 101 コマから置いた）では、止めを足す前は、台本の前のコマでもとの回りと層の値が消えていた。足したあとは、どれも止まる。組だけをミュートした写しと、層の上のアクションの
  影響を 0.5 にした写しは止めず、725 コマで形状キーの値と目の骨の回転が timeline と 32 ビットまで同じ。止めを足したあとも、台本の照合（報告・
  設定・timeline と、.blend のキーの読み直し）は足す前と同じ。
- 動いているところを動画として通しでは見ていない（コマで確かめた）。踊りの .blend に重ねた場合は確かめていない。

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
  元のキーまで全部のコマ（間引いたキーの間の形を変えない）。元のキーが無ければ前後 1 コマ。同じ値にキーを打つほかの破綻の区間の中のコマには打たない
  （報告の `boundary_frames`）。留めのキーは破綻を入れる前に打つ（親の破綻の区間の外で子の破綻を計算するとき、親が元の姿勢になっている）。`world` は骨の行列を入れるので位置と大きさに小さなずれが出るが、キーを打たない位置と大きさは終わりに元の値へ
  戻す（キーが無いと全部のコマに効くため）。
- `world` の軸と `shift` の向きは世界の座標。そのコマのアーマチュアの物体の向きでアーマチュアの中へ直し（物体が動いていても）、`shift` は
  親の今の姿勢を通して骨の位置へ直す（アーマチュアが回っていない・親の無い骨では、試作と同じ式）。
- 同じコマで先に入れた破綻（`local` など）は姿勢の行列に入れてから、`world` を入れる（同じ骨に続けて入れても、先の破綻を入れた素材に `world` を入れたのと同じ）。
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
    どちらも世界の座標で決めたとおり（ずれ 9e-8 以下）で、区間の外は同じ。左の上腕の回りのキーを消した写しで、上腕を回す破綻のすぐ後ろに
    左手を世界の X へ 3 cm 動かす破綻を入れると、左手は世界の X へ 3 cm ちょうど（ずれ 1.2e-9）で、上腕は区間の後ろで元の姿勢。アーマチュアの
    物体に Z のまわりの回転の動き（0° → 90°）を付けた写しでも、世界の X のまわりの回転と世界の X への移動は決めたとおり（ずれ 7e-8 以下）。
    左の上腕に、同じ 3 コマで骨の中の軸のまわりの回り（`local`）と世界の X のまわりの回り（`world`）を続けて入れると、`local` だけを入れた素材に
    `world` を入れたのと同じ（ずれ 1.2e-7 以下。単精度の最後の桁）。
  - 1 回にかかった時間（Blender の起動を含む）: 布を見ないと、354 コマで約 15 秒、954 コマで約 39 秒、720 コマで約 38 秒（そのうち数える所は 6〜10 秒）。
    布も見ると、354 コマで約 40 秒、954 コマで約 96 秒、720 コマで約 59 秒（ほかの重い処理と重なった PC での値）。

**限界**:
- **目に見えるかどうかは測っていない**。測っているのは形の重なりと骨の向き。袖に隠れる・カメラに写らない、は分からない。
- 腕は「骨の上の点と太さ」で見ている（腕のメッシュそのものではない。袖のふくらみは入っていない）。
- 見ていない物: 髪と体、手と道具（刀など）、腕と裾、布の計算で動かす裾（アーマチュアで動かないメッシュ）、顔の形状キー。
- 布として見るのは、揺れものの骨で動くメッシュだけ。体の皮と一体の服は「体」として扱う。布の「小さい所」は頂点の数で決めているので、
  モデルの細かさで変わる。
- 人型の骨の割り当て（VRM）が無いモデルでは動かない。リポジトリ外のキャラクター作成ツールで作った 2 体でしか試していない。
- しきい値は 3 つの動きに合わせて何度も直した値。別の動きで同じように働くかは、まだ分からない。
- 試作は Blender の単精度で計算していた所（骨の行列の逆・腕の点・頭の面までの距離）を、ここは倍精度で計算する。丸めの境の近くの値は、
  最後の桁が試作と違うことがある（上の 1 件）。

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
- 入力 `skip_vertices`（真偽の配列の npz）の頂点は、胴の面として数えない（髪の前に手があるだけで内側と判定しない）。
  鍵がメッシュの名前の配列は、そのメッシュだけに当てる（頂点の数が違えば止まる。`vrm.motion_rig` の `skip` はこの形）。
  名前でない鍵の配列は、すべての OR を、頂点の数が合うメッシュに当てる（前からの形。長さの違う配列があれば止まる）。

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
- 2026-11-16 に、別のキャラ（VRM の骨を付け替えた作業コピー）の 720 コマを Blender 5.2.4 で移し、4.5.15 の結果と骨の行列が
  バイト単位で同じだった（[Blender 5.2 と VRM Add-on](#blender-52-と-vrm-add-on)）。

## 別々に骨へ載せた動きを 1 本に組む

`motion.join_clips` は、別々に骨へ載せた何本かの動き（`motion.retarget` などの .blend。同じキャラ・同じ fps）を、切り替えのコマの表つきで 1 本の並びに組む。
- 何本かの動画から 1 つの踊りを作るとき（2D の完成版が、何本かの生成動画を切り替えながら並べているときなど）に使う。
- 本ごとに骨へ載せてから組む。点をつないでから載せると、腕のならし（`smooth_arms`）などが切り替えをまたいで、切り替えがぼける。
- 入力は、組む本の .blend を何本でも（入力の名前は自由で、`segments` の `clip` で指す）。どの区間にも使わない入力は止める。
- `segments`: 出力のコマを区間に分ける。区間ごとに `clip`（入力の名前）・`out`（出力のコマ [最初, 最後]。1 始まり・両端を含む）・
  `offset_s`（その本の 0 秒目を、出力の何秒目に置くか）。
  - 最初の区間は 1 コマ目から、ほかは前の区間の次のコマから隙間なく続ける。同じ本を何度使ってもよい。
  - 出力のコマ T の元のコマは、その本の最初のキー ＋ round(((T − 1) ÷ fps − `offset_s`) × fps) を、その本のキーの範囲に収めた値。
    範囲の外のコマは、最初か最後の姿勢で止まる（止めたコマの数は報告の `held_start`・`held_end`）。2D の合成で本を並べる式と同じで、
    丸めは Python の round（ちょうど .5 は偶数へ）。
- `fps`（既定は最初の区間の本のシーンの fps）。fps がこれと違う本は止める（本ごとに同じ `fps` で `motion.retarget` する）。
- 動きは、本ごとに F カーブのいちばん多いアクション（`vrm.apply_motion` と同じ選び方）。キーのある F カーブの組（data_path と番号）が
  そろわない本は止める。キーの無い F カーブは読まず、組んだ動きでも書き直さない（そのまま残す。数は報告の `clips` の `empty_curves`）。
  ミュートした F カーブ（ミュートした組の F カーブも）がある本は止める（値は読めるが再生では使われないので、組むと動きが変わる）。
- `align: mean`（既定）: 本ごとに、使うコマの腰（`bone_prefix` + `Hips`）の位置の、世界の左右・前後の平均が原点に来るように、
  腰の位置をずらす（高さは変えない。2D で本ごとに踊り手を画面の中央へ置き直すのと同じ）。`none` でずらさない。
- 最初の区間の本の .blend の動きに、全部のコマのキー（補間は直線）を打ち直し、シーンのコマの範囲を 1〜最後、fps を出力の fps にして、
  別名で保存する。ほかの本のアクションは持ち込まない。元のファイルは変えない。
  - 読んだ値には F カーブの修飾子（ノイズなど）が掛かっているので、打ち直した F カーブの修飾子は外す（二重に掛けない）。
  - 打ち直した動きを、アーマチュアが再生する動きにする（付いていなければ付け、層つきのアクションなら最初のスロットに）。NLA の層は外す
    （`vrm.apply_motion` と同じ。残すと組んだ動きに混ざる）。
- `motion.retarget` の `model3d_points_frames`（各コマが 1 本の points の何コマ目か）は、組んだ動きには合わないので外す
  （`motion.ground_feet` は、組む前の本ごとに掛ける）。シーンの `model3d_join_clip`・`model3d_join_frame` に、コマごとの本の番号
  （報告の `clips` の順）と元のコマを残す。

**出力**:
- `blend`: 組んだ動きの .blend（`motion.twist_split`・`vrm.apply_motion` にそのまま渡せる）。
- `report`: `model3d-motion-join/0.1`。本ごとのキーの範囲と fps、区間ごとの元のコマの範囲と止めたコマの数、切り替えのコマ `cuts`
  （区間の 2 つ目からの最初のコマ）、本ごとの腰のずらし（`offsets`）、`model3d_points_frames` を外したか、動きを付け替えたか
  （`action_bound`）、外した NLA の層と修飾子の数。

**動きをつないだ踊りの知見**（3 作目の VRM のダンスで分かったこと）: 切り替えのコマでは姿勢が飛ぶ。描くときは、切り替えのコマにカットを
置くと、動きの飛びが隠れる。揺れもの（VRM のばね）は、そのまま焼くと切り替えの直後に裾と髪が大きく跳ね上がった。report を
`vrm.bake_springs` の `joined` に渡すと、切り替えのコマごとに揺れものを落ち着かせ直す（[揺れものを焼く](#揺れものを焼くvrmbake_springs)）。

**確かめたこと**:
- CI（Blender なし）では、区間の確かめ・元のコマの表（丸めの式・止めるコマ・同じ本の使い回し）・骨の名前の読み方と、工程の受け渡し
  （代役の Blender）・悪い設定を確かめた。
- 2026-11-17 に Windows の Blender 4.5.15 と 5.2.4 で、3 作目の VRM のダンスの 3 本（各 300 コマ。本ごとに `motion.retarget` した
  .blend）を、リポジトリの外の台本と同じ並びで 954 コマに組んだ。
  - 台本で組んだ .blend と、アクションの F カーブ 527 本のキー・取っ手・補間がバイト単位で同じ。コマごとの本と元のコマの表も同じ。
  - `check.blend_match`（`pose_frames: all`）で、骨 131 本の姿勢が全 954 コマで差 0（`exact`）。4.5.15 で組んでも 5.2.4 で組んでも同じ。
  - 違いは、台本が残していた `model3d_points_frames`（最初の本の 300 コマぶん）を外したことだけ。
  - Blender の実行は 4.5.15 で約 12 秒、5.2.4 で約 7 秒。

## 前腕のねじれを分け直す

`motion.twist_split` は、骨へ移した動き（`motion.retarget` の blend）の前腕のねじれを、肘と手首に半分ずつ持たせ直す。
- 骨の向きを点へ合わせると、前腕が自分の軸のまわりに大きく回り、手がそれを逆に回して打ち消すことがある。そうなると肘と手首で皮膚がねじれてつぶれる。
- ねじれは、骨の回転を「曲がり × 骨の軸（Y）のまわりの回転」に分けたときの、軸のまわりの角度。
- 肘と手首のねじれの合計を −180〜180° に戻し、前腕を軸のまわりに回し直して半分ずつ持たせる。手はその分を逆に回すので、**手の向き・前腕の向き・指先の位置は変わらない**。
- 動きのキーを打ち直すだけで、骨と重みは足さない。上腕のねじれを、骨と重みを足してメッシュの側で付け根から先へ分けるのは `model.twist_bones`（[ねじれを分ける補助の骨](#ねじれを分ける補助の骨modeltwist_bones)）。
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

## 左右の腕どうしの入り込み

手を重ねる振り（両手を体の前で合わせて回す、など）では、動画から読んだ関節の点の左右の手の位置が数 cm ずれ、骨へ載せると、手首や前腕・指が
互いを通り抜ける（3 作目の VRM のダンス）。`motion.push_hands` は「手と体」を見る工程で、「手と手」は見ない。通り抜ける重なりは、
まず元の動画と数で「本来どうすれ違う動きか」を見る（横から撮った振りは、奥行きの差が関節の点に入らない。[推定した 3D の点を整える](#推定した-3d-の点を整える) の「SAM 3D Body の推定の癖」）。

### 腕どうしの入り込みを測る（`check.arm_overlap`）

描かれる形そのもの（骨で変形した後の体のメッシュ。体のモディファイアも効かせる）で、片方の腕の頂点がもう片方の腕の中にあるコマを調べる
（.blend は変えない）。腕を「太さのある棒の集まり」とみなして測ると、袖口のフリルや手のひらの厚みなど、棒で表せていない部分の重なりを見落とした
（棒では重なりが出ないコマで、絵では腕が入り込んでいた）。
- 入力 `blend`（踊りの .blend）。`body`（必須）: 体のメッシュ。
- `arms`（必須）: 2 本の腕 `[{"name", "bones", "forearm", "wrist"}, …]`。体の頂点を、いちばん重みの大きい骨で分け、`bones`（前腕・手・指）の骨が
  いちばん重い頂点をその腕にする。上腕は入れない。`forearm`（`bones` の中の前腕の骨）がいちばん重い頂点は、初期姿勢で、前腕の付け根から
  手首（`wrist` の骨の付け根）へ向かう位置が `sleeve_until`（必須。0〜1 の割合。衣装の袖の長さで決める）より手前なら「袖」、ほかは「肌」（手と指は肌）。
  - いちばん重い骨は、変形に使う骨の頂点グループだけで比べる。`aliases`（`{頂点グループ: 骨}`）の頂点グループは、その骨の重みに足してから比べる
    （`model.twist_bones` で足した骨を、元の骨の仲間にする。上腕の仲間も挙げる。挙げないと、重みを分けた頂点の腕の分け方が変わる）。
  - `all_groups: true` なら、変形に使わない頂点グループ（押し出しの選択など）も比べる（3 作目の台本と同じ数え方）。台本は、袖口の押し出し
    の補正（袖の頂点の選択のグループと Shrinkwrap）を足した後の踊りで、その選択のグループが勝った袖の頂点（3 作目で 1228）を腕から外して数えていた。
- 面は、3 つの頂点が同じ腕のものだけをその腕の面にする（2 つ以上が袖なら袖の面）。
- コマごとに、片方の腕の頂点が、もう片方の腕の面の内側にあるかを調べる。2 つの見方の両方で内側と出た頂点だけを数える:
  1. いちばん近い面（`near`、既定 4 cm 以内）の裏側に `depth`（既定 2 mm）より深くある。
  2. その点から 14 の決まった向きへ伸ばした線が面を奇数回横切る向きが `vote`（既定 0.7）の割合以上ある（閉じた形の中にいる）。
  1 だけだと、爪の先や手のふちのような鋭い角のそばで、外にある点まで裏側と出る（台本の最初の版は、両手が 10 cm 離れたコマで「147 頂点が 39 mm
  入っている」と出た）。2 だけだと、フリルの内側のすき間（袖口と手首の間）にある点まで数える。深さは、いちばん近い面までの距離。
- 初期姿勢でも同じ判定をして、もともと内側と出る頂点（面の向きが乱れている所）は数から除く。
- 頂点の番号と元のメッシュの三角形で測るので、体のモディファイアが頂点や面のつながりを変える（細分化・三角形化など）ときは止める。
  距離の設定は m なので、1 Blender 単位 = 1 m でないシーン（`scale_length` が 1 でない）も止める。体のモディファイアの効き方が表示（ビューポート）と
  描画で違えば、描画にそろえてから測る（`render.stage` の描き方と同じ。.blend は保存しない。そろえたものは報告の `modifiers_synced`）。
- 入り込みを「肌が肌に」「袖が肌に」「肌が袖に」「袖が袖に」の 4 つに分けて、頂点の数といちばん深い量（0.1 mm に丸める）を数える。肌がからむ 3 つの
  どれかで `min_verts`（既定 3）頂点以上・深さ `min_depth_mm`（既定 3）以上のコマを「重なりのあるコマ」に数える。袖どうしだけのコマ（やわらかい物どうし）は別に数える。
- 出力 `report`: 重なりのあるコマの数と区間・肌どうしのコマの数・袖どうしだけのコマの数・深い順のコマ・腕ごとの頂点の数（袖の数、初期姿勢で
  除いた数）。コマごとの値は工程のフォルダの `arm_overlap_raw.json`。`max_frames` を渡したときだけ合否を出す（重なりのあるコマがそれを超えたら不合格）。
- `frames`（[最初, 最後]。省くとシーンの全コマ）、`armature`（省くと最初のアーマチュア）。954 コマで 1 回 数分〜10 分ほど（腕の頂点が多いほどかかる）。
- 数の既定（`near`・`depth`・`vote`・`min_verts`・`min_depth_mm`）は、3 作目で使った値。袖の終わる位置 `sleeve_until` は衣装ごとに違うので、既定を置かない。

**確かめたこと**:
- CI（Blender なし）では、いちばん重い骨の選び方（仲間の重みを足す・同じ重みは先）、面の分け方、線の向き（決まった乱数）、要約と合否、
  工程の受け渡しと悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（腕どうしの入り込みを測る台本）と、同じ .blend の全 954 コマで比べた（実測）。
  - 腕どうしを離す前の踊り: コマごとの 4 つの数と深さが台本と全部同じで、制作の記録とも同じ。重なりのあるコマ 150（肌どうし 126・袖どうしだけ 6）。
    この踊りには選択のグループが無いので、`all_groups` によらず同じ。
  - 腕どうしを離し、袖口と裾を押し出し、ねじれの骨を焼いた後（流れの 6 段目）: `all_groups: true` で台本と全部同じ（重なりのあるコマ 1。7.60 秒、
    肌が肌に 6 頂点・3.5 mm。腕の頂点 左 1808・右 1823）。既定（変形に使う骨のグループだけ）では腕の頂点が 左 2437・右 2422 に戻り、
    重なりのあるコマは同じ 1、袖どうしだけのコマが 0 → 9（袖口のフリルどうしは、押し出しの相手が相手の腕の芯なので重なりうる。やわらかい物どうし）。

### 腕どうしを離す（`motion.separate_hands`）

左右の腕・手・指が互いを通り抜けているコマで、上腕を肩のまわりに少し回して離す（肘から先の曲げと手の形は変えない。`motion.push_hands` と同じ考え方）。
- 入力 `blend`（踊りの .blend。腕の骨の回転は四元数で、上腕の回転は全コマにキーがあること。キーの間を補う動きのままだと、直した回転が前後のコマや切り替えの向こうへ広がるので止める）、任意で `joined`（`motion.join_clips` の report。切り替えのコマ `cuts` を読む）。元のファイルは変えない。
- `bones`（必須）: 骨の名前の型。`sides`（2 つの側の名前。`{side}` に入る）、`upper`・`forearm`・`hand`（`{side}` 入り）、`finger`（`{side}`・`{finger}`・`{part}` 入り）、
  `fingers`（既定 thumb・index・middle・ring・little）、`parts`（既定 proximal・intermediate・distal）、`torso`（胴の骨の並び。あるものだけ）、`head`。
  例: `{"sides": ["L", "R"], "upper": "upper_arm.{side}", "forearm": "lower_arm.{side}", "hand": "hand.{side}", "finger": "{finger}_{part}.{side}"}`。
  Mixamo 式なら `{"sides": ["Left", "Right"], "upper": "mixamorig:{side}Arm", …, "finger": "mixamorig:{side}Hand{finger}{part}", "fingers": ["Thumb", …], "parts": ["1", "2", "3"]}`。
- `body`（必須）: 前腕の太さを測る体のメッシュ。`aliases`: いちばん重い骨を決めるときに仲間にする頂点グループ（`check.arm_overlap` と同じ。変形に使う骨のグループだけで比べる）。
- **測り方**: 片方の腕を、太さのある棒（カプセル）の集まりとみなす。前腕は肘 → 手首を `forearm_bins`（既定 5）に区切り、区切りごとに初期姿勢の体の、前腕が
  いちばん重い頂点の軸からの距離で太さを決める（95% 点。太さが `soft_from` 以上の区切りは袖とみなし `soft_percentile`（既定 75）% 点。
  `soft_radius` を渡すと袖の区切りはその値（芯の太さ。フリルは押し出しで直すので、腕は芯だけで離す）。細い所は `forearm_radius` まで）。
  手のひら（手首 → 2 本目からの指の付け根、太さ `palm_radius`）、指（`finger_radius`）。袖どうしは、太さの和の `soft_factor`（既定 0.6）倍まで
  近づいてよい。左右の棒のすべての組で、いちばん近い 2 点の距離が「太さの和 ＋ `gap`（既定 2 mm）」より小さい分を重なりとし、コマごとにいちばん大きい重なりを取る。
- **直し方**: 左右の前腕の両方に垂直な向きへ、左右の腕を分けて離す（上へ動くほうの腕に `up_share`（既定 0.85）を持たせる。半分ずつにすると、
  下になる腕が裾へ押し込まれた）。重なりの続く区間（`merge` コマ以内はまとめる）ごとに側を 1 つに決め、向きは時間方向にならす。量は前後 ±`dilate`（既定 3）
  コマの最大を取ってから σ `sigma`（既定 3）コマでならし、`relax` 倍する。切り替えのコマ（`cuts`）はまたがない。`passes`（既定 5）回くり返す。
- **すれ違いの手当て**（`detour`、既定 0 回）: くり返しのあとに残る短い重なり（手が相手の腕の中心を 1〜2 コマで通り抜ける所）は、上の向きでは解けない
  （通り抜ける前と後で向きの符号が逆になる）。区間（切り替えのコマをまたぐ区間は、切り替えごとに分ける）ごとに、向きの候補（体の左右・上下・前後とその斜め、通り抜ける向きに垂直な向き、区間の前後で 2 点を結ぶ向き。
  両方の符号）と量（1 cm から 0.5 cm きざみで `detour_max`（既定 10 cm）まで）を計算で試し、「その区間で重なりが無くなる・前後のコマで新しい重なりを作らない・
  手の点（手首・指の付け根・指先）のどれもが、頭や胴（頭は `head_radius`、胴は `torso_radius` の太さの棒とみなす）へ今より `detour_body_loss`（既定 5 mm）より近づかない」を満たす、いちばん小さい量を採る（向きは区間の間ずっと同じ、量だけを前後へなめらかに
  立ち上げる）。満たす候補が無ければ、重なりがいちばん減る候補を採り、記録に「解けていない」と書く。袖どうしの重なりは動かさない。
  腕を肩のまわりに回したあとの位置は計算だけで求まる（Blender で書いた結果との差を報告の `computed_vs_actual_mm` に）。
- **罠（試して採らなかった向きの決め方。台本の記録）**: いちばん近い 2 点を結ぶ向き（棒が真ん中を通り抜けるコマで決まらない）、それを前後のコマから回しながら
  補う向き（向きが数コマで逆になり、手首の加速度が約 3 倍）、体の前後に固定した向き（手が体の前にあると肩を回しても前後へは動かせない）、区間ごとに決めた側を
  くり返しの間変えない形（3 作目で、重なりは 34 → 11 コマに減ったが、腕がほどける所で手首が 24 cm 動き、加速度が 2 倍に跳ねた）。どれも持ち帰っていない。
- 出力: `blend`（上腕の回転のキーを打ち直した .blend）、`report`（`model3d-separate-hands/0.1`。前腕の太さの区切り、直す前・回ごと・すれ違いの手当ての回ごと・
  直した後の重なり（コマの数・区間・いちばん深い量と組）、袖どうしの組を除いて測った直した後の重なり（`after_hard`）、手首が動いた量、
  手首の加速度（直す前と後。切り替えの前後 3 コマを除く。コマが 3 つ未満なら null）、設定）。`after_hard` に重なりが残れば要確認（区間やコマのいちばん大きい組が
  袖どうしでも、手や指の重なりを隠さない）。
- 腕の頂点の重なりを描かれる形で確かめるのは `check.arm_overlap`（上の節）。棒は形を近く表すだけなので、両方を見る。
- 体の太さ（`forearm_radius`・`soft_from`・`palm_radius`・`finger_radius`、`detour` が 1 以上なら `head_radius`・`torso_radius` も。m）は
  体ごとに違うので必須（既定を置かない。腕の太さは `check.body_size` で測れる）。ほかの数の既定は 3 作目で使った値。
- 距離の設定は m なので、1 Blender 単位 = 1 m でないシーン（`scale_length` が 1 でない）は止める。

**確かめたこと**:
- CI（Blender なし）では、棒どうしのいちばん近い 2 点、交差した前腕の重なりと袖どうしの要る離れ、ならし（切り替えをまたがない）、肩のまわりに回したときに腕の形が
  変わらないこと、区間ごとの向き、2 コマだけ通り抜ける手のすれ違いの手当てが重なりを解くこと、工程の受け渡し（`joined` の切り替え）と悪い設定を止めること
  （代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（腕どうしを離す台本）と、流れの 2 段目（揺れものを焼いた踊り、954 コマ）で
  比べた（実測）。台本と同じ設定（値はリポジトリ外のレシピ）でかけると、台本の 3 段目と F カーブ 527 本のキー・取っ手・補間がバイトまで同じ、`check.blend_match` で
  骨 137 本 × 954 コマの姿勢の差 0（`exact`）。報告も、前腕の太さ・直す前・回ごと・すれ違いの手当て（3 回）・直した後・手首が動いた量と加速度まで全部同じ
  （直した後の重なりは 7 コマで、どれも袖どうし）。

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
- 帯の垂れ（脚の形から作る当たり、太ももへの追従、重力）と袖口は、この鎖ばねの操作の対象外。別の作りとして `motion.ribbon_bones` と `motion.sleeve_bones` に実装しており、確認範囲は各節で読む。
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
  - `render.prepare_push` の当たりの写し（`model3d_collider` の印）は、既定から外す。ほかの、材質で隠しただけのメッシュもレンダーに出るものは既定に入るので、そのときは `meshes` を渡す。
  - 材質の枠はすべて塗る（Principled BSDF のない枠は飛ばす）。塗り方の違うメッシュ（セル調・顔・塗らないメッシュ）と分け合う材質は、塗り方ごとに写して付け替えてから書き換える（報告の `copied_materials`）。メッシュをほかの物体と分け合う（リンクした複製の）枠は、物体の側の枠に付け替える（メッシュの側を替えると、塗らない複製の材質も替わるため）。
- **セル調の塗り**（`toon`）:
  - 絵の色（Base Color）を出し、影は決まった向きの光（`light`）と面の向きの内積で 2 段に分ける。境は `threshold` ± `soft` の smoothstep（既定 0.10 ± 0.05）。
  - 影の側は、影の色（`shade`、元の色に掛ける）に、舞台の色を `tint`（既定 0.22）だけ混ぜた色を掛ける。`skin_shade` を渡すと、肌（明るく彩度の低い色）の影だけ別の色にする。光の側は `lit`（既定 0.96）倍。
  - 縁の光: 面が横を向く所（Layer Weight の Facing が `rim_from`〜`rim_to`）に、カメラから見て左は 1 つ目、右は 2 つ目の `rim_colors` を、強さ `rim_gain` でスクリーンで足す。
  - `face_attribute`（既定 `face_near`）の頂点の属性の所は、影を入れず縁の光も入れない。`weak_rim`（既定 `hand` 0.75・`norim` 0.85）の属性の所は縁の光を弱める。
  - 裏の面と `inside_attribute`（既定 `inside`）の所は `inside_color` を掛ける。全部を Emission で出し（既定では舞台の光を受けない。下の `real_light` で、光の当たり方を明るさだけ混ぜられる）、EEVEE が使う材質の出力につなぐ。
  - 元の透け（Principled BSDF の Alpha につないだ出口か、1 未満の値）は、Transparent BSDF と混ぜて残す（髪の房やまつげの切り抜き）。
- **陰影を足す設定**（`toon` の中。省けば上の 2 段の塗りのまま。段 1 の後の陰影の見比べのために足した）:
  - `bands`: 影の段を増やす。`[{"at": 境の内積, "soft": ぼかし, "weight": 重み}, …]`（1〜4 個。重みの和は 1）。`threshold`・`soft` の代わりに使う。
    - 光の当たり方は、境ごとの smoothstep に重みを掛けて足したもの（明るい側が 1、影の側が 0）。その割合で、影の色と光の側の色を混ぜる。
    - 例: 境 0.55・0.10・-0.35 に重み 0.25・0.55・0.2 なら、明るい方から 1・0.75・0.2・0 の 4 段。
  - `face_shadow`（0〜1、既定 0）: `face_attribute` の所にも、影をこの割合だけ入れる。縁の光は入れないまま。
    - 光の当たり方 L（1 が明るい側）を、L + 印 × (1 - `face_shadow`) × (1 - L) にする。印が 1 の所では影の深さが `face_shadow` 倍になり、段の違い（3〜4 段）も残る。
    - 0 のときは今までどおり、印の所を明るい側にする（L と印の大きい方）。
  - `skin_meshes`: 肌の部品の名前（`meshes` の中から）。
    - その部品の頂点に属性 `model3d_skin` を付け、肌（明るく彩度の低い色）を見るのを、その部品の中だけにする。服の淡い色（花の柄など）を肌と読まないため。
    - ほかの物体とメッシュを分け合う部品は止める（印が両方に付くため）。
  - `skin_highlight`: `{"at", "soft", "amount", "color"}`。肌の、光との内積が `at` より大きい所に、`color` を `amount`（0〜1）の強さでスクリーンで足す。`face_attribute` の所は除く。
  - `rim_scale`（既定 1）: 縁の光の強さに掛ける。範囲は `rim_from`・`rim_to` で変える。
  - `real_light`（0〜1、既定 0）: 舞台の光の当たり方（影も含む）を、この割合だけ明るさに混ぜる（光の色まで受ける塗りは [`look.lit`](#舞台の光を受ける塗りlooklit)）。
    - 光の当たり方は、白い Diffuse BSDF を Shader to RGB で読み、白黒の明るさにしたもの（光の色は混ぜない。色のついた光も、明るさとして効くだけ）。明るさの倍率は (1 - `real_light`) + `real_light` × min(`real_light_gain`（既定 1）× 光, `real_light_max`（既定 1.5）)。
    - 舞台の光は強いので、`real_light_gain` で肌の明るい所が 1 前後になるようにそろえる（32 秒の本番の舞台では、光を受けた肌の明るい所が 1.9 前後、後ろの縁の光が当たる所は 8 近く）。上限は、縁の光で白く飛ばないため。
    - Shader to RGB は EEVEE でだけ働く（`render.stage` は EEVEE で描く）。
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
- 陰影を足す設定は、2026-11-07 に Windows の Blender 4.5.15 で、32 秒の本番のキャラで確かめた。
  - 設定を省くと、塗りのノードは前と同じで、本番の 1 コマが画素まで同じだった（207 万 3,600 画素で差 0）。
  - 舞台の光の強さを、光を 1/10 にして（白く飛ばないように）描いて測った。光を受けた肌の明るい所は 1.9 前後（中ほどは 1.4）、後ろの縁の光が当たる所は 8 近く。`real_light_gain` 0.55 と `real_light_max` 1.5 は、ここから決めた。
  - 段（3 段・4 段）、顔の弱い影、肌の部品と影の色とハイライト、縁の光、実際の光を足した 4 通りで、本番の 4 コマを描けた（陰影の見比べ。どれを使うかはまだ決めていない）。
  - CI（Blender なし）では、設定の確かめと既定（省けば今の塗りのまま）を確かめた。
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

## 舞台の光を受ける塗り（`look.lit`）

`look.lit` は、キャラ（blend）の材質を、舞台の光の向きと色を受ける塗りに差し替える。3 作目の VRM のダンスの演出版（リポジトリ外の台本。Blender 5.2 の EEVEE で 1 回で描いた）で、VRM の材質を描く間だけ差し替えた塗りを移した。
- **なぜ要るか**:
  - VRM の材質（VRM Add-on の MToon）は、舞台の光に反応しない。3 作目では、白い光でも赤い光でも逆光でも、同じ色に描かれた。
  - MToon の材質は、描くときに使われる出力がノードグループ（`Mtoon1Material.Mtoon1Output`）の中にある。材質のいちばん外の出力（`Mtoon1Material.CompatibleMaterialOutput`。glTF 用の Principled BSDF につながっている）につなぎ替えても、見た目は変わらなかった（3 作目）。そのため、元の材質は書き換えず、材質ごと新しく作って枠に入れる。
  - `look.toon` の `real_light` は、光の当たり方を明るさとして混ぜるだけで、光の色は混ぜない。場面ごとに光の色を変える演出には、色まで受ける塗りが要る。
- **塗り**: 絵の色 ×（まわりの明るさ ＋ 光の当たり具合を 2 段に分けたもの × 光の色あい）を Emission で出す。
  - 光の当たり具合: 白い Diffuse BSDF を Shader to RGB で読む（舞台の光の向き・色・影を受ける。EEVEE でだけ働く）。RGB のいちばん強い成分 m を `band`（[下, 上]）の smoothstep で 0〜1 にし、`extra`（とても強い光のぶん。m の `from` を `to` へ直線で写し、外は端で止める）を足して、`gain` を掛ける。
  - 光の色あい: Shader to RGB の色を max(m, `floor`) で割った色（いちばん強い成分が 1 になる）。
  - まわりの明るさ: 材質ごとの RGB ノード `model3d_lit_ambient`。描く工程が、コマごとの色 × 材質の印 `model3d_lit_ambient_scale` を入れる。書き換えない描き方では `ambient` の色のまま。
  - `soft`（材質の名前の並び。顔など）の材質は `soft_lit` の値（段をやわらかく、まわりの明るさを多めに）、ほかは `lit` の値。
  - 既定は 3 作目の VRM のダンスで使った値。`lit` は段 0.12〜0.32・明るさ 0.86・まわり 1 倍、`soft_lit` は段 0.02〜0.50・明るさ 0.78・まわり 1.5 倍、`extra` は 1〜6 を 0〜0.30 へ、`floor` は 0.02。`ambient` の既定は 0.2 の灰色。
- **絵の色**（`texture`、既定 `BaseColor`）: 元の材質の、名前に `texture` を含む画像ノード（画像のあるもの。ノードの並びで最初）の画像を、既定の UV で読む。VRM Add-on の MToon では `Mtoon1BaseColorTexture.Image`。
  - 無ければ、Principled BSDF の Base Color に直接つないだ画像ノードの画像。それも無ければ Principled BSDF の Base Color の色。どれも無い材質は止める（`meshes` から外すか、`texture` を変える）。
  - 絵の補間は元のまま。UV の変換（MToon の絵の位置と倍率）・色の掛け率・透け・輪郭線は持ち越さない（3 作目の VRM は、どれも使っていなかった）。
  - `color_attribute`（元の材質の名前 → 色の属性の名前）に書いた材質は、どれより先に、その色の属性（頂点の色。Color Attribute のノードの線形の色）を絵の色にする。頂点の色で塗った小さな部品（Tripo などの生成モデルの当て布）を、絵や Base Color の一色にせずに塗れる。その材質を使うメッシュに、その名前の色の属性が無ければ止める。
- **決まった明るさ**（`unlit`。元の材質の名前 → 倍率）: 書いた材質は、光を受けずに、絵の色 × 倍率を Emission で出すだけにする（ノード 4 つ）。体の中にあって舞台の光が届かず、すき間が黒く見える部品のため（4 作目の 3D のダンスの当て布。どの部品がそうかは、目で見て決める）。
  - まわりの明るさのノードが無いので、描く工程はまわりの明るさを入れない。縁の光も足さない。`soft` と `rim.materials` に同じ材質は書けない（止める）。
  - 材質の印は `model3d_lit`（元の材質の名前）と `model3d_lit_unlit`（倍率）。報告の材質ごとの `unlit`（倍率か null）。色の属性の材質は、報告の `color` が `attribute` で、`attribute` に名前。
- **メッシュ**: `meshes`（既定: アーマチュアで動く、レンダーに出すメッシュ全部。`look.toon` と同じ選び方で、当たりの写しと輪郭線の写しは除く）の、材質の枠をすべて塗る（空の枠は飛ばす）。
  - 材質の無いメッシュは、名指ししたなら止め、既定の選び方なら塗らずに報告の `without_material` に残す。
  - 同じ元の材質は、1 つの塗りを分け合う。元の材質は書き換えないので、塗らないメッシュは元の材質のまま。
  - 塗らない物体とメッシュのデータを分け合う（リンクした複製の）物体は、物体の側の枠に入れる（報告の `object_slots`）。
  - 材質の印は、`model3d_lit`（元の材質の名前）・`model3d_lit_ambient_scale`・`model3d_lit_soft`。印のある .blend にもう一度当てると止める。
- **縁の光**（`rim`。省くと足さない）: 面が横を向く所に、色つきの光を足す（4 作目の 3D のダンスで、参考の動画の輪郭の色つきの光に合わせて、描く工程のフックで足していた物）。
  - 塗り（Emission の色）に、縁の色 × 強さを足す。縁の色は、カメラから見て左の縁が `left`、右の縁が `right`（線形の RGB）。法線をカメラの座標へ直した X を `side`（[下, 上]。既定 [−0.25, 0.25]）の smoothstep で 0（左）〜1（右）にして、2 色を混ぜる。
  - 強さ = smoothstep(`edge`, Layer Weight の Facing（Blend は `blend`。既定 0.5））× smoothstep(`down`, カメラの座標の法線の Y)（既定 [−0.75, −0.15]。真下を向く縁を弱める。あごの下・裾の中）× `gain`（既定 1）× 材質ごとの割合。
  - 材質ごとの割合は `materials`（元の材質の名前 → 割合）、書いていない材質は `amount`（既定 1）。割合 0 の材質には足さない（ノードも足さない）。`left`・`right`・`edge` は必ず書く（作品ごとに決める。既定は持たない）。
  - 左の色・右の色・強さは、全部の材質が見る 1 つのノードグループ `model3d_lit_rim` の中の、名前が `left`・`right`（RGB）と `gain`（Value）のノードにある。コマごとに 1 か所を書き換えれば、全部の材質が変わる。`render.show` のフックからは `import lit_look` して `lit_look.set_rim(bpy, left=…, right=…, gain=…)` を呼ぶ（`render.show` に、光のトラックの値を入れる口はまだ無い）。
  - 縁の光を足した材質の印は `model3d_lit_rim`（割合）。報告の材質ごとの `rim`（割合）と、`rim`（ノードグループの名前と、足した材質の数）。
  - 左右は、カメラの座標の法線の X で分けるので、カメラを傾ける（ロール）と、左右の境も傾く。広い面が横を向く部品（袖口の内側など）では、面いっぱいに乗る（4 作目では、その材質の割合を下げて抑えた）。

**出力**:
- `blend`: 塗りを替えた .blend。
- `report`: `model3d-lit-look/0.1`。メッシュ、材質ごとの元・塗り・柔らかい段・倍率・絵、枠ごとの差し替え、設定。

**確かめたこと**:
- CI（Blender なし）では、設定の重ね方と確かめ、絵の色の出どころの選び方、既定のメッシュの選び方、台本の引数と工程の受け渡し（代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスの踊りの .blend（材質 4 つ・メッシュ 8 つ。顔の材質を `soft`）に当て、リポジトリ外の台本の塗りと比べた（実測）。
  - 4 つの材質とも、ノード 19 個の型と値、つなぎ 23 本、材質の設定、まわりの明るさの倍率が、台本の塗りと同じだった。約 4 秒。
  - 台本の写しの「塗りの差し替え」だけを `look.lit` の材質に置き換えて演出版の 6 コマ（1080×1920・24 標本・動きのぶれ 0.4）を描き、台本の写しのままで描いたコマと比べた。2 コマは画素まで同じ、4 コマは最大 1/255 の差（違う画素 23〜4,535）。
  - 台本の写しのままで同じ 6 コマを 2 回描いても、同じ 4 コマに最大 1/255 の差（違う画素 0〜4,386）が出る。上の差は、描くたびの揺れと同じ大きさ。
- 合成の場面（Blender 5.2.4。アーマチュアで動く球と、動かない球）でも確かめた。
  - 絵の出どころの 3 通り（名前に `BaseColor` を含む絵、Principled BSDF の Base Color につないだ絵、絵の無い Principled BSDF の色）が、どれも塗りになった。
  - 塗る球と材質を分け合う動かない球は、元の材質のまま。メッシュを分け合う 2 つの球（片方だけ動く）は、動く側だけが物体の側の枠で塗られた。
  - メッシュを分け合う 2 つの球（どちらも動く）は、どちらもメッシュの側の枠で塗られた。枠を替える前の材質を先に控えてから替えるため（控えない作りでは、1 つ目で替えた材質を 2 つ目が読み、止まった）。
  - 知らない `soft` の材質と、印のある .blend にもう一度当てるのは止まった。材質の無い動く球は、既定の選び方では塗らずに `without_material` に残り、名指しすると止まった。
  - 空の光を 0 にして、赤い光だけを当てて描くと、塗った所は赤だけ（緑と青は平均 0.1/255）、青い光では青だけ、白い光では絵の色になった。絵をそのまま出す材質（MToon と同じく光に反応しない）は、赤い光でも絵の色のままだった。
- 縁の光（`rim`）:
  - CI（Blender なし）では、ノードの作りを代役で調べた: 足さないとき（`rim` なし・割合 0）の塗りのノード 19 個・つなぎ 23 本が、足す前の `look.lit` と型・設定・値・つなぎまで同じこと。足すときのノード 14 個・つなぎ 17 本（Mix は型の名前 `A_Color`・`B_Color`・`Result_Color` で引く）、ノードグループの中身と `set_rim` の書き換え、設定の確かめ（14 通りを止める）。
  - 2026-11-23 に Windows の Blender 5.2.4 で、合成の場面（灰色の球 2 つ。光なし・空は黒・正射影）を描いて確かめた（実測）。左を赤 [1, 0, 0]、右を青 [0, 0, 1] にすると、正面から見ても横から見ても、カメラから見て左の縁が (248, 89, 89)、右の縁が (88, 88, 247)、下の縁は足す前と同じ (89, 89, 89)、上の縁は 2 色の間 (192, 89, 189)（縁から 3 画素内側。0〜255）。
  - 同じ場面で、`gain` 0、描く前に `set_rim` で強さを 0 に書き換えたもの、割合 0 の球は、足す前と画素まで同じ（違う画素 0）。
  - 4 作目の 3D のダンスの踊り手（材質 15 個）で、塗りの値をそろえて、描く工程のフック（リポジトリ外の台本の写し）の縁の光と比べた（実測）: 15 の材質とも、ノード 33 個・つなぎ 40 本の型・設定・値・つなぎが同じ。見本のコマのカメラ 5 つ（540×960・16 標本、光 2 つ）で描いた絵は、画素まで同じ（違う画素 0）。足す前の塗り（`rim` なし）も、作品の塗った踊り手とノードまで同じだった。
- 色の属性（`color_attribute`）と決まった明るさ（`unlit`）:
  - CI では、代役で、色の属性のノードが絵のノードに替わるだけ（ノード 19 個・つなぎ 23 本のまま）、決まった明るさの材質がノード 4 個・つなぎ 3 本（絵の出どころ 3 通り）で、まわりの明るさのノードと印が無いことを確かめた。設定の確かめ（7 通りを止める）。
  - 2026-11-23 に Blender 5.2.4 で、合成の場面（色の属性で左を赤 (0.8, 0.1, 0.1)・右を緑 (0.1, 0.6, 0.1) に塗った板と、青 (0.1, 0.2, 0.7) の球。まわりの明るさ 0.5）を真上から描いた（実測）。光なしでは、板の左が (170, 63, 63)、右が (63, 149, 63)、球が (63, 89, 160) で、色 × 0.5 の sRGB と同じ。強い光を当てると明るくなり（板の左 (255, 105, 105)）、`unlit` 0.8 の球は、光があってもなくても (80, 111, 197)（色 × 0.8 の sRGB と同じ）で、強い縁の光（強さ 3）も乗らなかった。無い色の属性の名前と、塗らない材質の名前は止まった。
  - 4 作目の 3D のダンスの踊り手で、フックの頂点の色・決まった明るさ（0.8）・縁の光と比べた（実測）: 出力から辿れるノードが 15 の材質とも同じ（当て布はノード 4 個・つなぎ 3 本）。見本のコマのカメラ 5 つで描いた絵は画素まで同じ（違う画素 0。当て布の見える画素は 1 コマ 3,560〜7,221）。
  - フックのこの足しは、塗った .blend から元の材質の印を読むが、塗った .blend には元の材質が残らない（使う物が無いので保存されない）ため、作品の描画では一度も効いていなかった（描いたときの記録に、足したことを知らせる行が無い）。上の比べは、元の材質の印を下ごしらえの .blend から補って回した。`look.lit` は、元の材質を読むのが塗る前なので、この形で抜けることは無い。

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
  - トラックがあれば、その並びで描く（T は書き出す番号、f は動きのコマ。同じ f を続ければ止まり、とばせば速くなる。小数の f は前後のコマの間の時刻を描く。[動きと違う fps で描く](#動きと違う-fps-で描く小数のコマ)）。無ければ、シーンの全コマを T = 1 から描く。
  - ファイルは `f<T>.png`。`only` で T を選ぶ。すでにあるファイルはとばすので、続きから描ける（描き直すときはファイルを消す）。
  - `frames_dir`（既定は工程のフォルダの `frames`）に書く。
  - とばせるのは、同じ入力で描いたコマだけ（[コマのフォルダの入力の控え](#コマのフォルダの入力の控え)）。入力を変えて同じフォルダへ描くと、とばしたコマが前の絵のまま残るので、描く前に止める。
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
    - 当たりの写しと動かす頂点の組は、`render.prepare_push` で作れる（[押し出しを準備する](#押し出しを準備する)）。
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

## 押し出しを準備する

`render.prepare_push`（blend → blend, report）は、`render.stage` の押し出し（`push`）が使う当たりの写しと、押し出す頂点の組を .blend に足す。リポジトリ外の試作で、キャラを組む段階で脚の当たりと帯の垂れの印を作った手順を移した。`look.toon` の前に入れる（試作も、印を付けてから輪郭線を付けた）。

**当たりの写し**（`colliders`: `[{"from": 元のメッシュ, …}]`）:
- 元のメッシュ（脚など）を写し、モディファイアを外す。名前は `name`（既定 `proxy_` + 元の名前）。親と変換は元のまま。
- 世界で `weld_mm`（既定 0.02）以内の頂点をまとめ、穴（付け根やブーツの口）をすべてふさぎ、面の向きをそろえる。
  - まとめる距離は、物体の拡大で直した中の距離にする（軸ごとに拡大が違えば、いちばん大きい拡大で割る。どの向きでも世界で `weld_mm` を超えない）。
  - 体積が負（内向き）なら裏返して外向きにする。
  - 脚の部品は筒なので、そのまま当たりにすると、穴のふちの面の向きで中か外かを読み違え、脚から離れた帯まで引き寄せたため。
- 面を `decimate`（既定 0.25）の割合に減らし、すべての面をなめらかにする。
  - 写しに付いてきた形状キーは、先に外す（キーのあるメッシュは面を減らせないため。元のメッシュは変えない）。写しは骨でだけ動き、元の形状キーの動きは追わない。外したキーの数を報告に載せる。
  - 中か外かは、いちばん近い面の向きで決まる。ふさいだ口の面が平らなままだと、口のふちの近くで向きを読み違えるため（ふちで向きが混ざるようにする）。
- 元の Armature モディファイアと同じアーマチュアと設定（Preserve Volume・頂点の組の絞りとその反転・ビューポートと描画で効くか）で動かす。元と同じに動かないと、押し出しが体からずれた当たりに当てるため（`render.stage` は、描く間だけ各モディファイアのビューポートの効き方を描画の効き方にそろえるので、写しの描画の効き方も元と同じにする）。
- カメラ・影・拡散・光沢・透過・ボリュームには写さない。透明な材質（`proxy_hidden`）も付ける。
- 写しに `model3d_collider` の印を付ける。`look.toon` は、この印の物体を既定のメッシュ選びから外す（写しは `hide_render` を付けないのでレンダーに出る物体と見え、透明な材質には塗りに要る Principled BSDF が無いため）。
- 穴をふさいだ辺の数、体積（世界の L。物体の拡大とシーンの `scale_length` で直す）、裏返したか、面の数を報告に載せる。
- 閉じているかは、押し出しに使う形（間引いた後）で数える。
  - `open_edges`: 間引いた後に開いた辺の数。`open_edges_before_decimate`: 間引く前の数。`nonmanifold_edges`: 間引いた後に 3 つ以上の面が使う辺の数。
  - どれかが 0 でない当たりがあれば、工程は要確認（needs_review）にする（閉じていない当たりでは、押し出しの内外の判定が狂う）。
  - 前は間引く前の数だけを報告し、間引いた後は確かめていなかった（2026-11-08 に直した。試作の 2 本の脚は、間引いた後も開いた辺 0・3 面以上の辺 0）。

**押し出す頂点の組**（`groups`: `[{"mesh", "bones", "top", "front", "back"}]`。1 つのメッシュに 1 つ）:
- 種は、`bones`（`bone_prefix` の後ろの名前）の骨とその子孫の骨に、重みのある頂点。
- 高さ `top`（m。帯の結び目など、上の端）より下で、種とつながった部分を選ぶ。
  - つながりは辺で見る。位置を `weld_mm`（既定 0.01）の格子に丸めて同じになる頂点（継ぎ目）は、同じ点とみなす。
- 重みは高さ z で smoothstep((`top` - z) / `fade`)。上の端で 0、`fade`（m、既定 0.02）下から 1。1/64 に丸め、0 の頂点は組に入れない。
- 頂点の法線がキャラの前（-Y）を向けば `front`、ほかは `back` の組にする（`render.stage` の `push` で、組ごとに離す量を変えられる）。
- 高さと法線は、世界の座標の静止の形で測る（長さは m で、シーンの `scale_length` で直す）。
- 骨はメッシュの Armature モディファイアのアーマチュアから読む。無ければ `armature`（省くと、ファイルにアーマチュアが 1 つならそれ）。
- メッシュをほかの物体と分け合う（リンク複製）なら、先にこの物体だけのメッシュにしてから組を足す。重みはメッシュに付くので、分け合ったままだと、高さの違うほかの物体の重みまで変わるため。分け合っていた物体を報告の `separated_from` に載せる。
- 種の数、つながった部分の数、選んだ頂点の数、組ごとの頂点の数、重みが 0 で外した頂点の数、高さの幅を報告に載せる。

**止まる所**: 元のメッシュ・頂点の組を足すメッシュ・骨が無い。写しの名前の物体や、同じ名前の頂点の組がもうある。種のうち `top` より下のものが無い。

**出力**:
- `blend`: 写しと頂点の組を足した .blend。
- `report`: `model3d-prepare-push/0.1`。上の報告と、設定。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 骨の子孫のたどり方、重みの付け方と丸め方、格子での同じ点のまとめ方、つながった部分の選び方（上の端より上でだけつながった布を分けること、種の無い部分を選ばないこと）、前と後ろの分け方。
  - 設定の確かめ（悪い設定を止めること）と既定、工程の受け渡し（代役の Blender）。
- 2026-11-07 に Windows の Blender 4.5.15 で、リポジトリ外の試作（32 秒の本番の 1 クリップ）と比べた。
  - 試作の部品の段を、帯の印を付けずに組み、この工程で写しと頂点の組を足した。
  - 試作が組んだ .blend と、写し 2 つの頂点の位置・面・面のなめらかさ・頂点の法線・骨の重み・モディファイア・親と行列・見え方・材質、帯の 2 つの組の頂点ごとの重みが、すべて同じだった。
  - 帯の頂点の選び方（骨の重みの種から、上の端より下でつながった部分）は、試作のキャラ専用の座標の箱で選んだ頂点（17,488）と、3 つのクリップで同じだった。

## 骨に沿った閉じた筒で押し出す（Blender の補正）

`render.stage` の `push`（描く直前に、毎コマ Python で頂点を動かす）は、`render.stage` が Blender 5.x で動かないので、VRM の流れ（Blender 5.2）では
使えない。3 作目の VRM のダンスでは、押し出しを Blender のモディファイア（Shrinkwrap の Outside。「相手の形の内側に入った頂点だけを表面へ戻す」）として
.blend に付けた。骨の変形の後ろに付くので、当たっていないコマでは何も変わらず、どの描き方（EEVEE・簡易描画）でも、Blender の版によらず効く。
相手には、体のメッシュでなく、骨に沿った閉じた筒（当たり用の見えない形。骨で動く）を作る。この節の部品は、距離の設定が m なので、
1 Blender 単位 = 1 m でないシーン（`scale_length` が 1 でない）は止める。測る部品は、モディファイアの効き方が表示（ビューポート）と描画で
違えば、描画にそろえてから測る（`render.stage` の描き方と同じ。.blend は保存しない。報告の `modifiers_synced`）。

**罠: Shrinkwrap の Outside の相手は、穴も鋭い角も無い閉じた形にする。** Outside は「いちばん近い面の裏側にある頂点」を内側とみなし、距離に関係なく
面まで動かす。相手の形に穴のふちや鋭い角があると、そのそばの外の点が「裏側」と判定され、遠くの頂点が吸い寄せられて面が破れる。
- 3 作目で、袖口の当たりを前腕と手のひらの別の筒にして端を平らなふたでふさいだら、相手の腕から 20〜30 cm 離れた袖の頂点が相手の手首まで吸い寄せられ、
  肌色や赤のとがった面が腕の間に伸びた（954 コマのうち 152 コマ、最大 38 cm）。直した所の数コマだけを拡大して見ていて、送る前の全コマの検査で見つけた。
- 裾の当たりを体のメッシュそのもの（部品 149 個、穴のふち 3231 辺）にしたら、体から 9〜15 cm 離れた裾の頂点が体の表面まで 10〜15 cm 引き寄せられ、
  表の面が破れて裏地の赤が大きく見えた。体のメッシュをボクセルで作り直して穴をふさぐ形も試したが、中が空洞になり、判定の外れが 1 割残った（どちらも持ち帰っていない）。
- 補正を足したら、全コマで頂点の動いた量を測る（`check.pushout_moves`）。数が大きく出たら、「そういう動きだから」で済ませず、近くから描いて理由を確かめる。

### 袖口を相手の腕の外へ押し出す（`collision.cuff_pushout`）

袖口のフリルは前腕の骨から半径 50〜60 mm あり（3 作目）、手首を交差させる振りでは、腕を離しても相手の腕に入り込む。フリルの太さぶんまで腕を離すと振りが
変わる（手首が最大 17 cm 動いた）。やわらかいフリルは、当たったらつぶれるのが自然なので、腕は動かさず、フリルの頂点だけを相手の腕の外へ押し出す。
- 入力 `blend`（踊りの .blend。体と袖が 1 つのメッシュ）。元のファイルは変えず、別名で保存する。初期姿勢で測って作る。
- `body`（必須）: 体のメッシュ。`arms`（必須）: 2 本の腕 `[{"name", "forearm", "hand", "palm_end"}, …]`（`palm_end` は手のひらの先の骨。中指の付け根など）。
  `aliases`: いちばん重い骨を決めるときに仲間にする頂点グループ（ねじれの骨。`check.arm_overlap` と同じ。変形に使う骨のグループだけで比べる）。
- 腕ごとに、肘 → 手首 → `palm_end` の付け根を通る 1 本の閉じた筒を作る（名前は `proxy`、既定 `ArmProxy.{arm}`）。前腕を 5 つに区切った 6 つの輪のうち、
  肘から `sleeve_until` までは芯の太さ `core_radius`、手首側は体の前腕がいちばん重い頂点から測った太さ（輪の ±0.12 の帯の 90% 点。
  5 頂点に満たなければ肘の側の隣の輪と同じ）、手のひらは `palm_radius`。両端は半球のように丸める（輪を 60°・30° で縮めて先に点を置く）。輪は `segments`（既定 20）角。
  骨に付けて動かす（アーマチュアの子にし、頂点はアーマチュアの空間で作る。Armature）。描かない（`hide_render`）。鏡になったアーマチュア
  （拡大に負の値）は、筒の面の向きが逆になるので止める（`cloth.pushout` の筒も同じ）。
- 体の袖（前腕がいちばん重く、肘から `sleeve_until` までの頂点）に頂点グループ `group`・`near_group`（既定 `cuff_{arm}`・`cuff_near_{arm}`）を付け、
  相手の腕の筒を相手にした Shrinkwrap（Outside、`offset` 既定 2 mm。名前 `modifier`）と、その前に頂点ウェイトの近接（名前 `near_modifier`。相手の筒から
  `gate_near` 以内は重み 1、`gate_far` より遠くは 0）を付ける。判定が外れても、遠くの頂点は動かない。
- 体と衣装の大きさ（`core_radius`・`palm_radius`・`gate_near`・`gate_far` は m、`sleeve_until` は前腕に沿った 0〜1 の割合）は必須（体と衣装ごとに
  違うので既定を置かない）。芯は袖の中の腕の太さ、門はフリルの太さより少し遠く。ほかの数（`offset`・`segments`）の既定は 3 作目で使った値。
- 筒を作ったあと、筒の頂点から 5〜30 cm 離れた点を決まった乱数で置き、Outside と同じ見方で「内側」と判定される点を数える（報告の `far_points_judged_inside`。
  0 でなければ要確認）。同じ名前の物体・頂点グループ・モディファイアがもうあれば止める（2 回かけない）。
- 出力: `blend`、`report`（`model3d-cuff-pushout/0.1`。腕ごとの筒の名前・袖の頂点の数・前腕の長さ・6 つの輪の太さ・筒の頂点の数と体積・判定の確かめ、体のモディファイアの並び）。
- 袖口のフリルどうしは、押し出しの相手が相手の腕の芯なので、外側の部分で重なり合ってよい（やわらかい物どうし）。腕どうしの離し方は `motion.separate_hands`（芯の太さで離す）。

### 押し出しの動きを測る（`check.pushout_moves`）

押し出しの補正が、頂点をしきい値より遠くへ動かしていないかを、コマごとに数で調べる（.blend は変えない）。当たって押し出されただけなら、動く量は相手の形の太さより小さい。
- 入力 `blend`。`checks`（必須）: `[{"mesh", "prefix", "limit_mm"}, …]`。コマごとに、メッシュ `mesh` の、名前が `prefix` で始まるモディファイアを効かせたときと
  外したときの頂点の位置を比べ、`limit_mm` を超えて動いた頂点を数える。0.5 mm より動いた頂点を「動いた」に数える。
  比べる座標は `space`: 既定 `local` はメッシュの中の座標（物体と親の拡大を含まない。3 作目の制作と同じ）、`world` は世界の座標（拡大を含むので、
  mm が実際の長さになる）。拡大のある物体では、`world` の量は `local` の拡大倍になる（3 作目の体と布には拡大があり、`world` で測ると、
  6 段目の動いた量の最大は 袖口 17.1 mm・裾 96.5 mm・垂れ 101.6 mm。制作のしきい値は `local` の量で決めていた）。
  同じメッシュは 1 回だけ挙げる（そのメッシュの調べるモディファイアは一度に外すので、2 つ挙げると動きが混ざる）。
- 調べるメッシュとモディファイアの相手（当たり用の形）は、モディファイアの効き方が表示と描画で違えば描画にそろえる（報告の `modifiers_synced`）。
  ほかは評価しない（速くする）。`frames`（[最初, 最後]。省くとシーンの全コマ）、`step`（既定 1）。
- 出力 `report`: 調べたものごとの、モディファイア・しきい値・動いたコマの数・しきい値を超えたコマの数と最初の 8 コマ（コマ・頂点の数・いちばん大きい量）・
  いちばん大きい量とそのコマ・1 コマに動いた頂点のいちばん多い数。しきい値を超えたコマがあれば不合格（要確認。`strict` なら失敗）。コマごとの値は `pushout_moves_raw.json`。
- しきい値は、相手の形の太さと押し出す距離から決める（当たって押し出されただけなら、それより大きくは動かない。3 作目の値はリポジトリ外のレシピ）。

**確かめたこと**:
- CI（Blender なし）では、筒の輪の向き（直交・縦の軸・前の輪の横の向きを引き継ぐ）と太さの決め方（芯・メッシュの 90% 点・足りないとき）、いちばん重い骨、
  動いた量の数え方（0.5 mm・しきい値・いちばん大きいコマ）と合否、工程の受け渡し（筒の判定の外れは要確認）と悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（袖口の押し出しと、押し出しの動きを測る台本）と比べた（実測）。
  - 流れの 3 段目（腕どうしを離した踊り）に、台本と同じ設定（体と衣装の大きさはリポジトリ外のレシピ）でかけると、台本の 4 段目と `check.blend_match` で `exact`
    （筒 2 つを含む全部の物体、骨 137 本 × 954 コマの姿勢、16 コマの変形した形（袖口の Shrinkwrap が効いた体も））。頂点グループ（筒と体。頂点の中の並びと
    重みのバイトまで）とモディファイアの並びも同じ。報告の数（袖の頂点 左 1354・右 1334、輪の太さ、筒の頂点 242、判定の外れ 0（約 2400 点のうち））も同じ。
  - 流れの 6 段目の全 954 コマで、台本と同じしきい値で測ると、3 つ（袖口・裾・垂れ）とも台本と全部同じ（動いた量の最大は 袖口 11.8 mm・裾 66.6 mm・垂れ 70.0 mm、
    しきい値を超えたコマ 0）。3 作目の台本の記録では、平らなふたの筒の版で袖口が 152 コマ・最大 38 cm 動いていた（上の罠）。

### 布を骨盤と太ももの筒の外へ押し出す（`cloth.pushout`）

揺れものの骨は数本しかないので、骨が体の外にあっても、その間の布の面は体に食い込む（3 作目で、脚を上げたときに黒いパンツと太ももが裾を突き抜けて
斑に見えた）。布に、体の外へ押し出す補正（Shrinkwrap の Outside）を付ける。
- 入力 `blend`（踊りの .blend）。元のファイルは変えず、別名で保存する。初期姿勢で測って作る。
- `body`（必須）: 体のメッシュ。`cloths`（必須）: `[{"mesh", "offsets"}, …]`（`offsets` は押し出す距離（m）の `[外]` か `[内, 外]`）。`axis_bone`（必須）: 縦の軸の骨（腰）。
- **当たりの筒**（名前は `proxy_prefix`（既定 `LowerProxy.`）+ `pelvis`・`thigh.<name>`）: 体の基本の形を、いちばん重い骨（変形に使う骨の頂点グループで比べる）で分けて、
  - 骨盤: `pelvis.bones` がいちばん重い頂点を、股（`pelvis.centre` がいちばん重い真ん中（|x| < 2 cm）の頂点のいちばん低い所）の 1 cm 上から、布のいちばん上の
    5 cm 上まで 2 cm おきに水平に輪切りにする（±1.5 cm の帯。12 頂点に満たない輪は飛ばす）。輪の中心は 5% 点と 95% 点の真ん中。輪は `pelvis.follow`
    （下から並べた骨）のうち、次の骨の付け根より低い骨で動く。
  - 太もも（`thighs` の 1 つごと）: `upper` の付け根から `lower`（膝）の付け根までを、骨に垂直に 8 つ（骨の 0.15 から 1.0 の所。骨の長さの ±7.5%、
    骨から 13 cm 以内）輪切りにする（`bones` がいちばん重い、その側の頂点）。測れない輪は前の輪の太さ。`upper` で動く。
  - 輪ごとに 16 方向の太さ（その方向の頂点の距離の 95% 点。頂点の無い方向は両隣から補い、輪のまわりにならす）を測り、輪から輪へもならす。
    両端は丸める（輪を 60°・30° で縮めて先に点を置く）。骨で動く（Armature）。描かない。
  - 筒のまわりの点の内外の判定（いちばん近い面の裏側か）を、7 本の線が面を奇数回横切る数で確かめる（外れれば要確認）。体が筒からはみ出す量
    （`report_bones`（省くと `pelvis.bones`）がいちばん重い頂点）も報告する。体の形は筒より細かいので、はみ出す分（3 作目で 99% の頂点が 4.3 mm 以内、
    最大 7.2 mm）より押し出す距離を大きくする。
- **層**: `offsets` を `[内, 外]` にした布は、同じメッシュの重なり（裾の表・ひだ・裏地）を、初期姿勢で縦の軸から頂点へ水平に引いた線で層に分ける
  （頂点の外側 `layer_gap`（既定 15 mm）以内に同じメッシュの面があれば覆われている。覆われていて内側に面が無ければいちばん内＝裏地）。
  いちばん外は「外」、間は真ん中、いちばん内は「内」、当て布（頂点グループ `patch_group`（既定 `lining_patch`）の頂点。裏から見える所に足した面）は
  「内」より `patch_inset`（既定 1.5 mm）内側（0.5 mm 未満にはしない）へ押し出す。同じ距離へ押し出すと、体に近い所で表と裏地が同じ所にそろい、裏地が表より外へ出た。
  当て布は、層を数えるときは無い物とする。
- 初期姿勢で、どれかの筒の内側に `attached_depth`（既定 3 mm）より深く（12 cm まで）もぐっている頂点は付け根として動かさない（当て布は、いちばん近い
  元の頂点に合わせる）。層ごとに頂点グループ `group`・`group_mid`・`group_in`・`group_patch`（既定 `pushout` …）を付け、層ごと・筒ごとに Shrinkwrap
  （名前 `modifier`・`modifier_mid` … に `@pelvis` などを付ける）を付ける。同じ名前の物体・頂点グループ・モディファイアがもうあれば止める（2 回かけない）。
- 出力: `blend`、`report`（`model3d-cloth-pushout/0.1`。筒ごとの輪の数・体積・太さ・判定の確かめ、体のはみ出し、布ごとの層の頂点の数と距離・付け根の頂点の数・
  モディファイアの並び）。付けたあとは `check.pushout_moves` を全コマで回す（重なった布の層の順番も全コマで数える）。
- 数の既定（`layer_gap`・`patch_inset`・`attached_depth`）と、筒の輪の取り方（間隔・帯・方向の数・95% 点）は、3 作目で使った値。押し出す距離 `offsets` は
  布と体ごとに違うので必須。ほかの物体と共有しているメッシュ（リンク複製）は止める（頂点グループの重みはメッシュにあるので、全部に効く）。裏地を寄せるときは、形状キーのある布も止める。
- 深くしゃがむような、太ももが大きく上がる姿勢では、裾の骨の数が足りず、押し出しでは直らない乱れが残る（3 作目。揺れものの壁を姿勢でゆるめる
  `vrm.bake_springs` の `_release_walls` と合わせた）。

### 裏地を表から離す（`cloth.lining_gap`）

裏地が表のすぐ裏（3 作目で 0.7〜2 mm）にあると、布が少し曲がるだけで裏地が表より前へ出て、表に裏地の色の小さな四角が見えた（押し出しを付けなくても出る）。
いちばん内の層（裏地）の頂点のうち、すぐ外の面との間が `lining_gap`（m）より狭い物を、見えない内側（縦の軸の向き）へ寄せて、その距離まで離す
（形を書き換える）。モデルの .blend に入れておくと VRM にも入る（3 作目では、モデルに入れてから踊りを作り直した）。
- 入力 `blend`。`cloths`（`cloth.pushout` と同じ。`offsets` を `[内, 外]` にした布だけを手直しする）、`axis_bone`、`lining_gap`（必須）、`layer_gap`・`patch_group`。
  当たりも押し出しの補正も付けない。
- 出力: `blend`、`report`（布ごとの寄せた頂点の数・平均と最大の量（mm））。
- 1 つのメッシュに重なる布の全部の層に効く（3 作目では、裾と同じ指定が垂れの裏の面にも効いた。絵では害は見つかっていない）。踊りの .blend にもかけると、
  2 回分寄る（3 作目で、モデルと踊りの両方にかけると、踊りの側で 187 頂点が平均 0.7 mm 余分に動いた）。モデルに入れたら踊りではかけない。

**確かめたこと**（`cloth.pushout`・`cloth.lining_gap`）:
- CI（Blender なし）では、輪の太さの測り方（丸い輪・楕円・頂点の無い方向の補い）、輪から輪へのならし、層の距離、輪を動かす骨の決め方、工程の受け渡し
  （筒の判定の外れは要確認）と悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（布の押し出しの台本）と比べた（実測）。
  - 流れの 4 段目（袖口まで押し出した踊り）に、台本と同じ設定（距離はリポジトリ外のレシピ）でかけると、台本の 5 段目と `check.blend_match` で `exact`
    （筒 3 つを含む全部の物体、954 コマの骨の姿勢、15 コマの変形した形（押し出しの効いた裾と垂れも））。頂点グループ（頂点の中の並びと重みのバイトまで）と
    モディファイアの並び、報告の数（筒の輪の数・体積・太さ・判定の外れ 0、体のはみ出し、層ごとの頂点の数（裾 1083・578・251・当て布 358）、付け根 216）も同じ。
  - 裏地を離す前のモデルに `cloth.lining_gap` をかけると、台本が作ったモデルと 8 つのメッシュの頂点が全部同じ（裾 229 頂点を平均 4.7 mm・最大 5.7 mm、
    垂れの裏の面 209 頂点を平均 3.2 mm・最大 5.0 mm 寄せた。台本の記録と同じ）。

## 重なった布を手直しして数える

裾の上に重なる垂れ、裾の表と裏地のように、重なった布の順番が入れ替わると、上の布が下の布に切られて見えたり、裏地の色が表に出たりする。
押し出し（上の節）とは別に、布どうしの重なりを手直しし、全コマで数える。この節の部品は、距離の設定が m なので、1 Blender 単位 = 1 m でない
シーンは止める。数える部品は頂点の番号で元のメッシュと結ぶ所があるので、モディファイアが頂点の数を変える布も止める。数える部品は、
モディファイアの効き方が表示と描画で違えば、描画にそろえてから測る（報告の `modifiers_synced`）。

### 上に重なる布を下の布に付ける（`cloth.follow`）

3 作目の VRM のダンスでは、帯のすぐ下の模様のある垂れが、裾とは別の揺れものの骨で動き、揺れものの計算には垂れと裾の当たりが無いので、動くと
垂れが真下へ垂れて、外へ広がった裾の中へ沈んだ（模様が裾に切られ、垂れが見えなくなるコマもあった）。垂れを、裾の表面に付いた布として動かす。
- 入力 `blend`（踊りの .blend かモデルの .blend）。元のファイルは変えず、別名で保存する。
- `follower`（必須）: 上の布。`leader`（必須）: 下の布。`axis_bone`（必須）: 縦の軸の骨（腰）。
- 初期姿勢で、上の布の頂点ごとに、下の布の「いちばん外の面」の頂点（縦の軸から水平に引いた線の外側 `layer_gap`（既定 15 mm）以内に下の布の面が無い頂点）を
  `k`（既定 6）個さがし、その骨の重みを距離で混ぜた値（重み × 1 / (距離² + 1e-6)）で、上の布の骨の重みを置きかえる（間は初期姿勢の形のまま保たれる）。
  上の布自身の揺れものの骨は使わなくなる（その骨と揺れものの設定は消さない。どの頂点も動かさない）。
- 1 頂点に付ける骨は `max_influences`（既定 4）本まで（重みの大きい順に残して合計を 1 に直す）。VRM は 1 頂点 4 本までなので、Blender と VRM で動きが変わらない。
- 骨の重みは、アーマチュアの骨の名前の頂点グループだけを読み・書く。下の布にあって上の布に無い骨の頂点グループは、下の布のグループの並びで足す。上の布がほかの物体とメッシュを共有している（リンク複製）
  ときは止める（重みはメッシュにあるので、全部に効く）。
- 出力: `blend`、`report`（`model3d-cloth-follow/0.1`。置きかえた頂点の数、下の布のいちばん外の面の頂点の数、いちばん遠い近い頂点（mm）、骨の数を切った頂点の数と
  切った重みの割合の最大、置きかえる前と後の骨ごとの頂点の数）。
- モデルにも入れる（3 作目では、踊りで直したあと、同じ手直しをモデルに入れて VRM に書き出した）。モデルに入れたら、踊りの工程ではかけない（かけても同じ）。
- 数の既定（`layer_gap`・`k`）は 3 作目で使った値。`max_influences` の既定 4 は VRM の上限。

### 上の布が下の布の内側に入るコマを数える（`check.cloth_over`）

別々に揺れる布どうしは、初期姿勢で組にした面の平面と比べる数え方では、ずれが大きく、数えすぎた（3 作目で 954 コマ中 953 コマ・深さ 10 cm と出た）。
コマごとに、上の布の頂点から外向きに線を伸ばし、下の布の面に当たるか（＝下の布の内側にいるか）を直接見る（.blend は変えない）。
- 入力 `blend`。`over`（必須）: 上の布。`under`（必須）: 下の布。`axis_bone`（必須）: 縦の軸の骨（外向きは、そのコマの姿勢でこの骨の軸から離れる向き）。
- 上の布の頂点（変形した後）から外向きに `depth`（既定 6 cm）まで線を伸ばし、下の布の面（変形した後。当て布 `patch_group`（既定 `lining_patch`）の面は数えない）に
  当たったら入り込み。深さは当たるまでの距離。`tol`（既定 1 mm）より浅いものは数えない。初期姿勢でも同じ判定をして、もともと内側にある頂点（付け根など）は除く。
- 初期姿勢は、アーマチュアを REST にして評価する。ファイルから動きを読む変形（MESH_CACHE・MESH_SEQUENCE_CACHE。`cloth.smooth` が置き換えた変形など）と
  焼いた物理の計算（CLOTH・SOFT_BODY。`cloth.skirt_proxy` の筒など）は、REST にしても開いたコマの形を返す（そのコマの入り込みを、全部のコマから除いてしまう）
  ので、初期姿勢の判定の間だけ外す。上の布・下の布と、そのモディファイアの相手（裾が貼り付く筒など。その先も）の物を外す（外した物は報告の `rest_caches_off`）。
  焼いていない物理の計算は止める（外すと計算の途中が消え、焼いていないとコマを飛ばして読むたびに形が変わる）。
- 出力 `report`: 入り込んだ頂点の数の 1 コマ平均・最大・`min_verts`（既定 3）以上のコマの数と区間・10 以上・30 以上のコマの数・いちばん深い量。コマごとの値は
  `cloth_over_raw.json`。`max_frames`・`max_mean_verts` を渡したときだけ合否を出す（平均は丸める前の値で比べる。報告の表示は小数 1 桁）。`frames`（[最初, 最後]）。
- 数の既定（`depth`・`tol`・`min_verts`）は 3 作目で使った値。

**確かめたこと**（`cloth.follow`・`check.cloth_over`）:
- CI（Blender なし）では、重みの混ぜ方（近いほど重い・大きい順）と骨の数の切り方（割合・合計を 1 に）、入り込みの要約（平均・区間・合否）、工程の受け渡しと
  悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（垂れを裾に付ける台本と、垂れが裾に入るコマを数える台本）と比べた（実測）。
  - 垂れを裾に付ける前と後の踊り（腕を直す前の動きから、凍結した台本で作り直したもの。954 コマ）で、垂れが裾の内側に入る頂点を `check.cloth_over` で数えると、
    コマごとの数と深さが台本と全部同じで、1 コマ平均 162.5 → 20.0（30 頂点以上のコマは 950 → 152。いちばん多いコマ 389 → 198 頂点）。作り直した踊りも、
    制作の記録と同じ数になった（付けた後は、954 コマのうち 1 コマで深さの最大が 0.1 mm 違うだけ）。
  - 垂れを付ける前のモデルに `cloth.follow` をかけると、台本が作ったモデルと、垂れの頂点ごとの骨の重み（頂点の中の並びと重みのバイトまで）が全部同じ
    （714 頂点を置きかえ、278 頂点で 5 本目以降を切った。切った重みの割合は最大 7.5%。台本の記録と同じ）。`check.blend_match` も `exact`。
    違うのは垂れの頂点グループの並びだけ（台本は足すグループを集合の順で作っていて、並びが実行ごとに変わりうる。こちらは下の布のグループの並び）。

### 裏から見える所に当て布を足す（`cloth.patch_lining`）

3 作目の VRM のダンスでは、裾を下から見上げると、赤い裏地に黒い筋・三角・しみが見えた（しゃがみや脚上げで映る）。調べると、裏地そのものに
切れ目やすき間があり、そこから表の布の裏側が見えている所（大半）と、裏地の面なのに色の絵が黒い所があった。前者は色の絵を塗っても直らない
（塗ると表の色が変わる）。黒く見える面のすぐ内側に、裏地の色の面（当て布）を足して隠す。
- 入力 `blend`（モデルの .blend か踊りの .blend）。元のファイルは変えず、別名で保存する。初期姿勢で決める。
- `object`（必須）: 布のメッシュ。`axis_bone`（必須）: 縦の軸の骨（腰）。`lining_color`（必須）: 裏地の色の決め方 `{"channel": "r"|"g"|"b", "min", "ratio"}`
  （成分 `channel` が `min` 以上で、ほかの 2 つの成分がその `ratio` 倍以下の色。色の値は Blender の画像の画素の値で、浮動小数の絵では線形の値）。`image`: 読む色の絵（省くと、材質のいちばん大きい画像）。色を読むのは、その絵を使う材質の面だけ（ほかの材質の面は選ばない。報告の `faces_other_material`）。
- 布の内側の何か所か（縦の軸の上の 6 つの高さと、3 つの高さで軸から少し外した 4 か所ずつ。外す量はその高さの布の内側までの距離の `view_spread`（既定 0.4）倍）から
  見て、ほかの布の面に隠れずに見える面を「内側から見える面」とする。布より下から見上げる位置は入れない（布のふちの、外からも見える面まで選んでしまうため。
  3 作目で 1444 面を選び、当て布が外から見えた）。
- その面の色の絵を `samples`（既定 30）点（決まった乱数）読み、裏地の色でない点が `min_dark`（既定 0.10）以上ある面を選ぶ。裏地の色だけの内向きの面のうち
  いちばん広い面の uv の真ん中を「裏地の色の 1 点」にする（無ければ止める）。真ん中そのものの色も確かめ、裏地の色でなければ（面の中の小さな暗い所に
  当たったなど）、その面で裏地の色と確かめた点のうち、真ん中にいちばん近い点にする（報告の `lining_uv_from` が `sample`）。
- 選んだ面の頂点を写し、内側（面の向きを内向きにそろえた法線の向き。物体の変換の逆転置で世界の向きにするので、軸ごとに違う拡大でも面に直角）へ
  `offset`（既定 2 mm）寄せて、新しい面を張る。骨の重みは元の頂点と同じ（一緒に動く）。
  向きは内向き。uv は全部「裏地の色の 1 点」（色の絵も材質も変えない）。
- 張る前に、当て布の面の上の点から外（水平〜斜め上、左右に 50° まで）へ線を飛ばし、`cover_reach`（既定 25 cm）以内で元の布の面にも `cover_objects`（体など。
  揺れる布は入れない）にも当たらずに抜ける面は張らない（ひだの折り目で、内側へ寄せた当て布が隣の面を突き抜けて、表に裏地の色の細い線が出たため）。
  `keep_outside_visible: true` なら張る（比べるときだけ）。
- 当て布の頂点に頂点グループ `group`（既定 `lining_patch`。骨の名前ではないので、動きにも VRM の重みにも関わらない）を付ける。`cloth.pushout` は当て布を別の層に
  して裏地より内側へ押し出し、`check.cloth_layers`・`check.cloth_over` は当て布の面を数えない。同じ頂点グループがもうあれば止める。
- 布のふちの下向きの面（外からも見える面）と、外から見えるために張らなかった面のぶんの黒は残る。当て布は 1 色。
- 数の既定（`view_spread`・`samples`・`min_dark`・`offset`・`cover_reach`）と見る位置の数は、3 作目で使った値。裏地の色 `lining_color` は衣装ごとに違うので必須。
  ほかの物体とメッシュを共有している布と、形状キーのある布は止める（面と頂点を足すため）。元の頂点の並びに結び付いたモディファイア（焼いた動きを読む
  MESH_CACHE・MESH_SEQUENCE_CACHE、覚えた頂点で動かす Surface Deform・Mesh Deform・Laplacian Deform、Multires、物理の Cloth・Soft Body、結び付けた形で戻す
  Corrective Smooth）のある布も止める（頂点を足すと働かなくなる。`cloth.smooth` で動きを焼く前の .blend にかける）。
- 出力: `blend`、`report`（`model3d-cloth-patch/0.1`。内側から見える面の数、表の布の裏・暗い裏地として選んだ面の数、外から見えるので張らなかった面の数、
  足した面と頂点の数、前と後の頂点と面の数、裏地の色の uv と色）。
- **試して採らなかった決め方**（3 作目の台本の記録）: 「当て布の裏の 2 cm 以内に元の面があること」（厳しすぎて 434 面のうち 60 面しか残らない）、
  「下から見上げて腰に隠れずに見える面だけ」（ねらった面が外れない）。帯と垂れのすき間から裏地が見えるコマで、黒い筋が消えて赤くなったのを副作用と
  取り違えたが、狙いどおりだった（数が増えたら、先に近くから描いて何が見えているかを確かめる）。

### 布の層の順番を数える（`check.cloth_layers`）

重なった布（裾の表と裏地、その上の垂れなど）の順番が入れ替わっているコマを数える（.blend は変えない）。3 作目では、体の外へ押し出す補正が表も裏地も
同じ距離へそろえたので、重なった所で裏地が表より外へ出て、裾の表に赤い面が見えた（押し出しを層ごとに分けた。`cloth.pushout`）。
- 入力 `blend`（踊りの .blend）。`objects`（必須）: 布のメッシュの並び。`axis_bone`（必須）: 縦の軸の骨。
- 初期姿勢（押し出しの補正 Shrinkwrap は止めて）で、縦の軸から各頂点へ水平に線を引き、線が頂点より内側で布を横切る回数を「層の番号」（いちばん内が 0）とし、
  すぐ外の層を線が横切る点（面と、面の中の位置）と、そこまでの距離を覚える（3 cm まで。`max_gap` がそれより大きければ `max_gap` まで組にする）。
  当て布の面（`patch_group`、既定 `lining_patch`）は層として数えない。初期姿勢はアーマチュアを REST にして評価し、ファイルから動きを読む変形（MESH_CACHE・MESH_SEQUENCE_CACHE。`cloth.smooth` が置き換えた変形など）と
  焼いた物理の計算（CLOTH・SOFT_BODY。`cloth.skirt_proxy` の筒など）は、REST にしても開いたコマの形を返すので、初期姿勢の間だけ外す（布と `body` と、
  そのモディファイアの相手。外した物は報告の `rest_caches_off`。焼いていない物理の計算は止める。`check.cloth_over` と同じ）。
- コマごとに、その面の同じ位置と頂点を比べ、頂点が面の外向きの側へ `tol`（既定 0.5 mm）より出ていたら入れ替わり。1 コマに `min_verts`（既定 3）頂点以上
  あれば、そのコマを数える。
- 調べる頂点を絞る: すぐ外の層までが `max_gap`（既定 15 mm）より遠い頂点は調べない（ひだの別の山など）。`only_innermost`（いちばん内の層だけ）、`pair_from`
  （そのメッシュの頂点だけ）、`pair_to`（すぐ外の面がそのメッシュの頂点だけ）。`patches: "only"` なら当て布の頂点だけを、すぐ外の元の面（当て布が隠している面）
  と比べる。`without`（名前の頭）で、その布のモディファイアを止めて測る（補正を外した形と比べる）。
- 別々に揺れる布どうし（垂れと裾など）は、初期姿勢で組にした面と比べるので数えすぎる（3 作目で 954 コマ中 953 コマ）。その組は `check.cloth_over` で数える。
- `body` を渡すと、初期姿勢での層ごとの体までの距離（10%・50%・90% 点）も報告する（押し出す距離を決める材料）。布と `body` のほかのメッシュは評価しない。
  層を数えなかった頂点（`patches: "ignore"` なら当て布、`"only"` なら元の布）は、`rest_unlayered: "layer0"`（既定。台本と同じ）なら層 0 に入れて数え、
  `"separate"` なら層には入れずに `unlayered` に別に出す。押し出す距離を決めるには `separate` が正しい（層 0 に層を数えなかった頂点が混ざらない）。
  数と扱いは報告の `layers_rest_unlayered`。
- 数の既定（`tol`・`min_verts`・`max_gap`）は 3 作目で使った値。
- 出力 `report`: 入れ替わったコマの数と区間・いちばん多い頂点の数・いちばん大きい量、調べた組の数・距離の分布。コマごとの値は `cloth_layers_raw.json`。
  `max_frames` を渡したときだけ合否を出す。`frames`（[最初, 最後]）。

**確かめたこと**（`cloth.patch_lining`・`check.cloth_layers`）:
- CI（Blender なし）では、裏地の色の決め方（台本の赤の式と同じ・ほかの色）、入れ替わりの要約（区間・合否）、工程の受け渡しと悪い設定を止めること（代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスのリポジトリ外の台本（当て布を足す台本と、層の順番を数える台本）と比べた（実測）。
  - 当て布を足す前のモデルに、台本と同じ設定（裏地の色の決め方と隠す物はリポジトリ外のレシピ）でかけると、台本が作ったモデルと `check.blend_match` で `exact`
    （裾の頂点・面・uv）、頂点グループ（当て布の目印と骨の重み。頂点の中の並びと重みのバイトまで）も全部同じ。報告の数（内側から見える面 1298、表の布の裏 357・
    暗い裏地 77、外から見えるので張らなかった 114、足した面 320・頂点 430、裏地の色の uv と色）も台本の記録と同じ。
  - 当て布を入れた踊り（954 コマ）で、いちばん内の層の裾どうし（入れ替わったコマ 61、最大 59 頂点・13.6 mm）と当て布だけ（100 コマ、最大 66 頂点・16.1 mm）を
    数えると、コマごとの数と量・調べた組の数・距離の分布・層ごとの体までの距離が、台本の記録と全部同じ。

## 要素別に描く

`render.passes` は、キャラを要素に分けて Blender の簡易描画（Workbench）でコマごとに描き、カメラと踊り手の点を控える。光と陰の色・影・にじみなどの重ね合わせは、Blender の外で行う。`render.stage`（EEVEE で舞台ごと一度に描く）と並ぶ 2 つ目の描き方で、2026-11-16 にユウが「2 本立て」に決めた。リポジトリ外の 2 作目の VRM のダンスで、2D の再現版の要素を描いた台本を移した。その制作では、906 コマの要素の描画が約 5 分、重ね合わせが約 2.5 分だった（`render.stage` の EEVEE は 1 コマ約 13.5 秒。絵の作りが違うので、同じ品質の比べではない）。

- **入力**: `blend`（必須）、`track`（必須。`model3d-stage-track/0.1` のコマの並び T・f と、コマごとのカメラ）、`cameras`（任意。`model3d-cameras/0.1`。下の「カメラ」）、`bones`（分身を描くときと、効果の形が点を読むとき（`bursts`・`sparks`・`trails`）は必須。骨の通り道 `model3d-bone-path/0.1`。下の「分身」「効果」）。
- **要素**（`passes`。既定 `["char", "shade", "disc"]`）:

  | 要素 | 描くもの | 背景 | 形式 |
  |---|---|---|---|
  | `room` | 部屋の物体（`room.collection` のコレクションの物体。1 枚の絵から作るときは [箱の部屋](#箱の部屋)）を、色の絵のまま平らな光で | `room.world`（シーンの線形の色。既定は黒） | `format` |
  | `char` | 人物を、色の絵のまま平らな光で | 透け | RGBA の PNG |
  | `shade` | 白い人物に、スタジオの光（`shade.studio_light`・`shade.rotate_z`。世界に固定した向きで、照り返しなし） | 黒 | `format` |
  | `disc` | 踊り手の点の真下（高さ `disc.height`）の床の円（半径 `disc.radius`・頂点 `disc.vertices`）を白で。重ねる側が足もとの影のもとにする | 黒 | `format` |
  | `head` | 顔の物体（`head` に名前を挙げる）を白、ほかの人物の物体を黒で（髪などに隠れた所は黒）。重ねる側が顔の範囲に使う | 黒 | `format` |
  | `fx` | 効果の形（火の粉・はじけ・火花・手の軌跡の炎の帯・炎の輪。毎コマ頂点の色つきの四角形）を頂点の色のまま平らな光で。人物のメッシュも一緒に黒で描く（人物の奥の効果は隠れる）。下の「効果」 | 黒 | `format` |
  | `pool` | 床の光のたまり（中心が明るく外へ暗くなる円盤。頂点の色）を平らな光で。下の「床の光のたまり」 | 黒 | `format` |
  | `rchar`・`rshade`・`rfx` | `mirror.passes` に挙げた `char`・`shade`・`fx` を、カメラを床の面（高さ `mirror.height`。既定 0 m）で反転して描いたもの（床への映り込み。下の「床への映り込み」） | 元の要素と同じ | 元の要素と同じ |
  | `ph<i>`・`rph<i>` | `phantoms.items` の i 番目の分身（骨組みをずらし、踊りの時刻を遅らせた人物）を、`shade` と同じ白い人物とスタジオの光で。`rph<i>` は反転したカメラで（`phantoms.mirror`）。下の「分身」 | 透け | RGBA の PNG |

  - 部屋の物体のうち `hide_render` のものは描かない。人物は、部屋と円のほかの、描画に出る物体（メッシュ・カーブなど。`hide_render` のものは除く）。物体は、アーマチュア（`armature` か最初のアーマチュア）の入ったシーンから選び、そのシーンで描く（作業用のシーンを開いたまま保存されたファイルがある）。
  - `format` は `png`（既定）か `jpeg`（品質 `quality`）。`char` はいつも透けの PNG。
  - どの要素も Workbench で、色の変換は Standard（Look なし）、アンチエイリアスは `antialiasing`（既定 "5"）。影・くぼみの強調・輪郭・透かしは切る。
  - 画像に日時やファイル名の控え（メタデータ）を書かない。同じ描画なら同じバイト列になる。
  - 長さ（カメラ・踊り手の点・円・写る範囲）は m で、シーンの `scale_length` で直す。
- **カメラ**（コマ T ごと）:
  - `cameras` にその T があれば、その行列を `matrix_world` に入れ、記録の `resolution` での焦点距離と主点から、レンズとずらしを決める。受光は縦に合わせる（レンズ = 焦点距離 ÷ 画面の高さ × `camera.sensor_height`、ずらし = (幅 ÷ 2 − 主点の x, 主点の y − 高さ ÷ 2) ÷ 高さ）。記録の `dancer` があれば、それを踊り手の点にする。記録の画面の縦横比が描く大きさと違えば止める。
  - 無ければ、トラックのカメラ（位置・狙う点・レンズ。`render.stage` と同じく、四元数で狙う点へ向ける。ずらしは 0）。レンズの無いカメラは `camera.lens`（既定 35 mm）。
  - どちらにも無いコマがあれば、Blender を起動する前に止める。
  - 行列のカメラは、回転の持ち方を物体の既定（XYZ のオイラー角）のまま入れる（リポジトリ外の台本と同じ）。Blender が回転を分解して持ち直すので、入れた行列と読み戻した行列は少し違う（試作の 906 コマで最大 2.4e-7。読み戻した行列をもう一度入れても、ビットまでは戻らない）。
  - 受光の大きさ `camera.sensor_height`（既定 24 mm）、写る範囲 `camera.clip`（m。既定 [0.1, 1000]）。
- **床への映り込み**（`mirror`。リポジトリ外の 2 作目の VRM のダンスの演出版で、床に踊り手が映る場面を描いた方法を移した）:
  - カメラを床の面で反転すると、その画がそのまま床への映り込みの絵になる（Blender は反転したカメラを受け付ける）。`mirror.passes`（`char`・`shade`）の要素を、そのコマの元の要素を描いた後に、反転したカメラでもう一度描いて `r<要素>` に書く。
  - 反転したカメラの行列は、入れた行列（`cameras` の記録のカメラなら記録の行列、トラックのカメラなら Blender の行列）に、左から鏡の行列（z → 2 × 高さ − z）を掛けたもの。試作と同じく、入れた行列から作る（Blender が持ち直した行列からではない）。
  - 次のコマのカメラを入れるときに、反転は残らない（トラックのカメラも大きさを 1 に戻す）。
  - 重ねる側は `mv dance passes` の `reflection`（[dance › 要素別の描画を重ねる](dance.md#要素別の描画を重ねるmv-dance-passes)）。
- **分身**（`phantoms`。リポジトリ外の 2 作目の VRM のダンスの演出版で、炎の世界に 1〜2 拍遅れて踊る分身を 4 体並べた方法を移した）:
  - `items`: 分身の並び `[{"offset": [x, y], "delay": 秒}]`。骨組み（アーマチュアの物体）を `offset`（m。[x, y, z] でもよい。世界の座標で。骨組みに親があれば、親の向きと大きさを戻して location に足す）だけずらし、動きのコマを `delay` × `fps`（`phantoms.fps`。省くと .blend の fps）だけ前にして描く。描いた後は元の位置と時刻に戻す。
  - `during`: 分身を描く時間 `[[頭, 終わり], …]`（コマの時刻 (T − 1) ÷ fps 秒。境目は `plan.stage` と同じく 1e-6 秒前のコマから。省くといつも）。外のコマは描かず、要素の一覧の `outside` に書く。
  - `mirror`: 床で反転したカメラでも描く（`rph<i>`。反転は `mirror.height` の床で）。カメラは、そのコマの入れた行列（反転も同じ行列から）。
  - 分身と踊り手のカメラからの距離を、骨の通り道（入力 `bones`）の点 `point`（既定 `hips`）を σ `sigma_s`（既定 0.292 秒）・±ceil(`reach` × σ) の標本でならした値で比べる（試作と同じ）。踊りの時刻は (f − `frame0`) ÷ `fps`。分身の点は、遅らせた時刻の点 + `offset`。カメラの位置は、入力 `cameras` の行列（無ければ控え）。分身のほうが近いコマは、要素の一覧の描いたコマに `near: true`（重ねる側が人物の手前に重ねる）と距離 `distance_m` を書く。
  - 縦長の画面は横が狭い（試作では引きでも左右 1.2 m ほど）。分身は横ではなく奥へ並べると画面に入る。
- **効果**（`fx`。リポジトリ外の 2 作目の VRM のダンスの演出版で、手の軌跡の炎の帯・動く向きに伸びる火の粉・火花・足もとの炎の輪を、3D の形として毎コマ描いた方法を移した）:
  - 形は `fx.shapes`（`blender_adapter/fx_shapes.py`。numpy だけで計算し、Blender の中で呼ぶ）。書いた形だけ描く（値は既定に重ねる。既定は試作の値）。形は時刻だけで決まる（乱数の種 `seed` は固定。どのコマから描いても同じ）。長さは m。
    - `embers`（火の粉）: 中心 `centre` のまわりの輪（半径 `radius`）をゆっくり回りながら上り、高さ `height` で生まれ直す粒。動く向きに伸びる筋。
    - `bursts`（はじけ）: `times`（`[[効果の時刻, 強さ], …]`）に、点 `origin`（既定 `chest`。その時刻を踊りの時刻として読む。`hold` より後は `hold` の時刻）から飛び散る粒。
    - `sparks`（火花）: 点 `hands`（既定 `hand_l`・`hand_r`）の通った跡から、少し遅れて舞い上がる粒（手が速いほど明るい）。
    - `trails`（手の軌跡の炎の帯）: 点 `hands` の通り道を `seconds`（既定 0.8 秒）つないだ帯。外側の炎と白っぽい芯の 2 枚。真上から見るときは `steep_weaken` だけ弱める（帯が体に重なって明るい塊になるので）。
    - `ring`（炎の輪）: 中心のまわりの半径 `radius` の、根もとが太く先が細い揺らぐ舌と、床の照り返しの輪。カメラから `near_fade` 以内は消す。
    - 筋（火の粉・はじけ・火花）は、そのコマのカメラを向いた細い四角形。カメラから `near` 以内は描かず、画面の上で大きくなりすぎないよう抑える。
  - コマの値: 効果の時刻は `fx.clock`（コマの時刻 → 効果の時刻のキー。省くとコマの時刻。時間停止で効果だけゆっくり進めるとき）、強さは `fx.levels`（`embers`・`trails`・`sparks`・`ring`・`flare`。数か時刻のキー）、時間の進み方は `fx.rate`（数か時刻のキー。0 で筋が伸びない）。踊りの時刻は (f − `fx.frame0`) ÷ `fx.fps`（省くと .blend の fps）。
  - 点は骨の通り道（入力 `bones`）から読む（`point_track`。点ごとのならし `fx.sigmas` の σ 秒。既定 0 = ならさない）。点の名前は `motion.bone_path` の `points` か `bones`。無い点があれば Blender の前で止める。
  - 描き方: 四角形のメッシュに頂点の色（線形の値）を入れ、Workbench の頂点の色で平らな光。人物のメッシュは、色の属性を足して黒にし、一緒に描く（手前の効果だけが見え、奥の効果は人物に隠れる）。メッシュでない人物の物体（カーブの髪など）は描かない。背景は黒。重ねる側が光として足す。
  - `fx.during`: 描く時間（分身と同じ書き方。外のコマは `outside`）。`mirror.passes` に `fx` を入れると、同じ形を反転したカメラでも描く（`rfx`。形は元のカメラを向いたまま）。
- **床の光のたまり**（`pool`）: 中心 `pool.centre` の、半径 `pool.radii`（m。既定は試作の 0〜45 m の 12 段）の輪を `segments`（既定 72）に分けた円盤（高さ `pool.height`。既定 0.002 m）。頂点の明るさは exp(−r ÷ `falloff`) + `floor` × (1 − exp(−r ÷ `floor_falloff`))（既定 4.5・0.09・25。中心が明るく外へ暗くなり、遠くはほのかに残る）。`pool.during` の外のコマは描かない。重ねる側は `mv dance passes` の `world`（別の世界の床の明るさ）。
- **踊り手の点**: `cameras` の `dancer` があるコマは、それを使う。無いコマは、骨 `dancer.bones`（`bone_prefix` の後ろの名前。既定 `Hips`）の付け根の平均を、そのコマたちの動きのコマ（重ねず小さい順）で、σ `dancer.sigma`（既定 6 コマ）のガウスでならす（`render.stage` の腰のならしと同じ）。アーマチュアは `armature`（省くと最初のアーマチュア）。
- **コマ**: ファイルは `<passes_dir>/<要素>/f<T>.png`（`jpeg` なら `.jpg`）。`passes_dir` の既定は工程のフォルダの `passes`。`only` で描く T を選ぶ（並びに無い T は止める）。描いてある要素のファイルはとばす（続きから描ける。描き直すときはファイルを消す）。とばせるのは同じ入力で描いたファイルだけで、違えば描く前に止める（[コマのフォルダの入力の控え](#コマのフォルダの入力の控え)）。要素を足すだけなら、描いた要素のファイルはそのまま使える。
- **表情**（`expressions`。リポジトリ外の 2 作目の VRM のダンスで、微笑みと閉じ目の顔を時刻で切り替えた方法を移した）:
  - `objects`: 絵を差し替える物体（頭など）。材質の画像ノードを全部、そのコマの表情の絵に差し替える（色の空間は元の絵にそろえる）。
    その材質をほかの物体も使っていれば止める（その物体の絵まで変わるため。材質をその物体だけのものにする）。
  - `images`: `{表情: 絵のパス}`（recipe からの相対パスも可。報告に sha256 を残す）。絵の無い表情は元の絵のまま。
  - `times`: `[[秒, 表情], …]`（秒の小さい順）。コマの時刻 (T − 1) ÷ fps の表情は、その時刻までで最後の名前（最初の秒より前は最初の名前）。
  - `shape_keys`: `{表情: 形状キー}`。その表情の間だけ形状キーを 1 にし、ほかは 0（閉じた目の形 `model.closed_eye_key` など）。同じ形状キーを
    いくつかの表情で分け合ってよい（そのどれかの間は 1）。絶対の形状キー（時間で形を選ぶ）は値で入り切りできないので止める。
  - `values`: `{形状キー: [[秒, 値], …]}`（秒の小さい順で、重ねない）。コマの時刻の値を、キーの間は直線でつないで入れる（最初の秒より前は最初の値、
    最後の秒より後は最後の値。キーの秒ちょうどはその値のまま）。形で作った表情（[表情を形で作る](#表情を形で作るvrm-に入る表情)のまばたき・笑顔の閉じ目・
    口角・眉）の時刻の表に使う。なめらかな開け閉めは、毎コマの値をキーにして渡してよい。`times` が無くてもよい（そのときは `images`・`shape_keys` は使えない）。
    `shape_keys` と同じ形状キーは挙げられない。キーが `objects` に無ければ止める。
  - どの要素にも効く（色の絵は char、形状キーは char・shade・head）。描いたコマの表情は、報告の `expressions`（T ごとの名前と数。`values` があれば
    T ごとに入れた値）。
  - 3 作目の VRM のダンスで分かったこと: カメラと表情を同じ瞬間に合わせると効く（引きから顔へ一気に寄る所で目を閉じて笑い、寄りきってから開く。
    目もとの寄りに切り替わってから開く。開く瞬間が寄りのカットに映るように置く）。
- **出力**:
  - `passes`: `model3d-passes/0.1`。要素ごとに、描いたファイルの T・f・sha256・大きさと、描いていない T（`missing`）。
  - `cameras`: `model3d-cameras/0.1`。並びの全部の T（描いたコマに関係なく）の、Blender が持ち直した行列（位置は m）・レンズ・ずらし・焦点距離・主点と、踊り手の点とカメラからの距離。fps は `fps`（省くとトラックの fps、それも無ければ .blend の fps）。重ねる側が、3D の位置に絵を置くときに使う。入力の `cameras` にもそのまま渡せる（トラックの並びに無い T のカメラは使わない）。
  - `report`: `model3d-pass-render/0.1`。要素ごとに描いた・とばしたコマ、色の変換、人物・部屋・顔の物体、カメラの出どころ（記録・トラックの数と、使わなかった記録の T）、時間、設定。
- EEVEE も合成の API も使わないので、Blender 5.2 でも動く。VRM のキャラは、VRM Add-on を有効にしなくても同じ画になった（下）。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 床への映り込み: 反転した要素の名前と形式、鏡の行列（床の高さ、向きが裏返ること）、元の要素と同じ描き方への切り替え、設定の確かめ、工程の受け渡しと要素の一覧（代役の Blender）。
  - 分身: 要素の名前と描き方、描く時間の境目、踊り手と分身の距離と手前か奥か、設定の確かめ、`bones` が無いと止めること、要素の一覧の `outside` と `near`（代役の Blender）。
  - 効果と床の光のたまり: 効果の形（`fx_shapes`。時刻だけで決まること、形ごとの出し入れ、カメラを向いた筋と近くを描かないこと、炎の輪のカメラの近くの消し方、はじけの時刻、帯の真上からの弱め、時刻のキーの読み方）、床の光のたまりの形と色が試作の式と同じ値であること、描く時間、コマの値（効果の時刻・時間の進み方・強さ・踊りの時刻）、要素ごとの描き方の切り替え（人物のメッシュと効果だけ、メッシュでない人物の物体は描かない）、設定の確かめ、点を読む形があるのに `bones` が無いか点が無いと止めること、要素の一覧の `outside`（代役の Blender）。
  - 設定の重ね方と、悪い設定を止めること。カメラの換算（試作の 1 コマの焦点距離と主点から、Blender が持ったレンズとずらしがビットまで戻る）。ファイルの名前。踊り手の点のならし。要素ごとの物体の出し入れと描き方の切り替え（代役の bpy）。
  - 工程の受け渡し、要素ごとのコマの一覧、カメラの控え、続きから描くこと、カメラの無いコマ・並びに無い `only`・縦横比の違う記録・T の重なった記録を Blender の前で止めること（代役の Blender）。
- 表情は、2026-11-17 に Windows の Blender 5.2.4 で、リポジトリ外の試作の描画の台本の写しを表情の絵つきで回したものと比べた（実測）。採用した踊り
  （閉じ目の形状キー入り）を、試作のカメラの控えの 9 コマ（微笑みと閉じ目の切り替わりの前後・閉じ目の中・遠い場面）で描き、char・shade・head の
  27 枚が試作と画素まで同じ（1 回目は shade の 1 コマが 108 画素違い、描き直すと同じになった。簡易描画の描くたびの揺れ）。
- 形状キーの値の時刻のキー（`values`）は、CI で、直線のつなぎ方（キーの秒ちょうど・前と後）、報告の分け方、名前の表の無い設定の受け渡しと悪い設定を
  止めることを確かめた。2026-11-17 に、3 作目の VRM のダンスの演出版の表情の時刻の表（リポジトリ外の台本。まばたき・笑顔の閉じ目・口角・眉の 9 つの
  形状キー）を毎コマの値のキーにして、入る値が台本の値と全 909 コマで同じことを確かめた（値が 0 でないコマは 851）。Windows の Blender 5.2.4 で、
  表情入りの踊り（9 つの形状キー入り）を 8 コマ描き、報告の入れた値が台本と同じこと、値が全部 0 のコマは `values` なしと char・shade・head が画素まで同じ
  こと、ほかの 7 コマは顔が変わること（目を閉じた笑顔・まばたき・口角・眉）を確かめた（実測）。
- 2026-11-16 に、Windows の Blender 5.2.4 で、リポジトリ外の試作（2 作目の VRM のダンスの 2D の再現版）の要素と比べた（実測）。
  - 基準は、試作の台本の写しを、表情の絵の差し替えなしで回して描いた要素（表情は、モデルの手直しの単位で持ち帰る）。入力は、試作の台本がカメラに入れた行列・焦点距離・主点と、腰の点（どちらも試作の台本の写しで書き出した）。受光 36 mm、写る範囲 0.05〜200 m、`jpeg` の品質 92、アンチエイリアス "5"。
  - 動きのコマがちょうど整数になる 169 コマ（906 コマのうち）で、4 つの要素（char・shade・disc・head）を約 90 秒で描いた（1 要素 0.13 秒）。
  - カメラの控え: 169 コマすべてで、行列・焦点距離・主点が試作の記録とビットまで同じ。
  - disc・head: 169 コマすべてで画素まで同じ。
  - char: 165 コマが画素まで同じ。残りの 4 コマは 1〜7 画素違った。そのうち 2 コマは、試作の台本から部屋を作らずに描いた画と画素まで同じだった（試作の場面には、隠した部屋の物体がある）。
  - shade: 136 コマが画素まで同じ。残りは 12〜181 画素（JPEG で最大 11/255）違った。違ったコマを 1 コマずつ描き直すと、4 コマとも試作と画素まで同じになった。
  - 簡易描画は、同じ入力でも描くたびにわずかに変わる（Blender 5.2.4 の Workbench。この PC の内蔵 GPU で。原因は調べていない）。同じレシピで 169 コマを 2 回描くと、char は 1 コマ（7 画素、最大 13/255）、shade は 34 コマ（合わせて 2,060 画素、JPEG で最大 11/255）違った。disc・head は毎回バイト単位で同じ。試作の台本どうしでも、描く並びを変えると shade に同じ揺れがあった。上の試作との違いは、この揺れの大きさの中に入っている。
  - VRM Add-on を有効にしなくても、同じファイルになった（揺れの分は除く）。
  - Blender 4.5.15 でも動いた（3 コマ）。disc・head は 5.2.4 と画素まで同じ、shade は 0〜51 画素（最大 9/255）、char は約 2 万画素（最大 38/255）違った（版による色の絵の描き方の違いと見ている。調べていない）。
- 床への映り込みは、2026-11-17 に Windows の Blender 5.2.4 で、リポジトリ外の試作（2 作目の VRM のダンスの演出版）の要素と比べた（実測）。
  - 基準は、試作の描画の台本の写しを表情の絵なしで回して描いた要素（870 コマ）。入力は、試作がカメラに入れた行列と焦点距離（控え）。
  - `rchar`（人物の映り込み）: 870 コマすべてで、試作の台本の映り込みの要素と画素まで同じ（真上から・床すれすれ・炎の世界の場面を含む）。
  - 同じ実行で描いた `char`: 847 コマが画素まで同じ。残りの 23 コマは合わせて 90 画素（1 コマ 6 画素以下、最大 55/255）。簡易描画の描くたびの揺れと、試作の場面の隠した部屋の物体のぶん（上の 2D の再現版と同じ大きさ）。
  - 870 コマの `char` と `rchar` で約 6.5 分。
- 分身は、2026-11-17 に同じ条件で比べた（実測）。試作の分身 4 体（左右にずれて奥に並び、1〜2 拍遅れる）を、試作が分身を描いた 218 コマで、反転したカメラでも描いた。
  - 描くコマ: 試作が分身を描いた 218 コマとちょうど同じ（`during` で選んだ）。
  - 手前か奥か（`near`）: 218 コマ × 4 体の 872 件すべてで試作と同じ。距離の差は、試作が mm に丸めて控えた値と 0.5 mm 以内（いちばんきわどいコマでは、分身と踊り手の距離の差が 1.4 mm しかない）。
  - 画素: 8 つの要素（分身 4 体と、その映り込み）× 218 コマの 1,744 枚のうち 1,707 枚が、試作の台本の写しで表情なしに描いた要素と画素まで同じ。残りの 37 枚は合わせて 85 画素（1 枚 6 画素以下、最大 44/255）で、簡易描画の描くたびの揺れの大きさ（陰影の要素と同じ白い人物とスタジオの光の描き方）。
- 効果と床の光のたまりは、2026-11-17 に同じ条件で比べた（実測）。試作の効果の形の設定（はじけの時刻と止める時刻・炎の輪の半径）、コマごとの効果の時刻・強さ・時間の進み方（時間停止の後は 0）をキーに写し、手と胸の点は骨の通り道（胸は 0.292 秒でならす）から読んだ。
  - 効果の形だけ（Blender なし）: 試作の台本の効果の形の関数そのものと 870 コマで比べ、試作の点を渡すと四角形と色がビットまで同じ。点も骨の通り道から読むと、差は最大 2.8e-13 m（float32 に丸めて違う頂点があるのは 1 コマの 1 頂点）。
  - 画素: 870 コマを約 3.4 分で描いた（`fx`・`rfx` と、切り替わった後の 254 コマの `pool`）。`pool` は 254 コマすべて、`fx` は 860 コマ、`rfx` は 865 コマが、試作の台本の写しで表情なしに描いた要素と画素まで同じ。残りの 15 枚は合わせて 1,012 画素（最大 19/255。8/255 を超える画素は 5 つ）。
  - その 15 枚のうち 4 枚を、試作の台本の写しでもう一度描くと、3 枚は私たちの画と画素まで同じ（基準のほうが揺れていた）、1 枚は基準と同じになった。簡易描画の描くたびの揺れ（上の陰影の要素と同じ）。

## コマのフォルダの入力の控え

`render.stage` と `render.passes`（と、`frames_dir` を書いた `render.show`）は、描いてあるコマのファイルをとばすので、止まった描画を続きから描ける。ただし入力（.blend・トラック・設定など）を変えて同じフォルダへ描くと、とばしたコマは前の入力の絵のまま残る。リポジトリ外の 3 作目の VRM のダンスでは、作り直した踊りの下見を同じフォルダへ描き、古いコマが混ざった下見を渡した（自前の描画の台本の一般の注意は [自前の描画スクリプトから乗る](external-renderer.md#描画スクリプトの作り)）。そこで、2 つの工程は、コマのフォルダ（`frames_dir`・`passes_dir`。既定の工程のフォルダも）に入力の控え `model3d_inputs.json`（`model3d-render-inputs/0.1`）を置き、とばしてよいかを確かめる。

- **控えの中身**: 入力のまとまり（`keys`。番号 → 中身）と、コマのファイルごとにどのまとまりで描くか（`files`。フォルダからの相対パス → 番号）。番号は、まとまりの正規の JSON の sha256 の先頭 16 文字。
- **入力のまとまり**: 工程の名前、入力のファイルの sha256（`blend`・`track`、`render.passes` は `cameras` と、効果の点を読むときの `bones`）、設定、描く台本（Blender が読む `blender_adapter` の台本と、それが読む同じフォルダの台本）の sha256、Blender の版（`blender --version`）、`render.passes` は有効にするアドオンの名前と版。
  - 描く絵を変えない `only` は除く。`render.passes` は、どの要素を描くか（`passes`・`mirror.passes`）も除く（要素を足しても、描いた要素のファイルはそのまま使える）。表情の絵（`expressions.images`）はパスでなく中身の sha256 で控える。
  - .blend が読む外の画像など、.blend の中から指す隠れた入力は控えに入らない（変えたら、別のフォルダへ描く）。
- **描く前に**: 並びのコマ（`only` で絞った後。`render.passes` は要素ごと）の控えを、いまの入力のまとまりと比べる。
  - もうファイルがあり、別のまとまりで描いたと控えたコマが 1 つでもあれば止める（誤りに、違うコマの数と名前、違うもの: `.blend`・`トラック`・`設定（変わった設定の名前）`・`描く台本（ファイルの名前）`・`Blender の版（前 → 今）` など）。
  - 控えの無いコマ（この控えより前に描いたコマや、手で置いたコマ。どの入力で描いたか分からない）があっても止める。
  - まだファイルが無くても、別のまとまりで描くと控えたコマがあれば止める。ほかのプロセスが同じフォルダへ別の入力で描いている途中なら、とばしたコマの持ち主が入れ替わるため（途中で止まった描画や、ファイルだけ消したコマでも止まる。そのときは、ほかに描いているプロセスが無いことを確かめてから、コマと控えを消す）。
  - 通れば、確かめたのと同じ待ち合わせの中で、並びのコマを全部いまのまとまりで描く、と控えに書いてから描く（確かめてから控えるまでに、ほかのプロセスが割り込まない。途中で止まっても、描いたコマには控えがある）。別のまとまりで控えたコマの控えは書き換えない。
  - トラックの無い `render.stage`（並びは .blend のシーンで決まる）は、フォルダにもうある `f<T>.png` を全部比べ、描いたコマを描いた後に控える。Blender が途中で止まったときも、止まる前に書いたコマを控えてから止まる（同じ入力なら続きから描ける）。
- **ほかの工程のコマには触らない**: 控えはコマのファイルごとなので、クリップごとに別の工程（別の .blend とトラック）で同じフォルダへ描いて `render.assemble` の `frames_dir` でまとめる使い方（[動画にまとめる](#動画にまとめる)）も、そのまま使える。
- **止まったら**: 別のフォルダへ描くか、そのコマ（と控え）を消してから描く。続きから描くのは、同じ入力のときだけ。
- 観測の `inputs`: 入力のまとまりの番号（`key`）と、とばしてよいと確かめたコマの数（`kept`）。
- 控えを書く間は、フォルダの `.lock` で待ち合わせる（同じフォルダへ別のプロセスが同時に描くとき。10 秒待って取れなければ止める）。

**確かめたこと**: CI（Blender なし）では、描く台本の見つけ方（import の書き方ごと）、控えの読み書き（同じ入力なら残す・違えば違うものを挙げて止める・控えの無いコマで止める・まだ無いコマを別の入力で描くと控えてあれば止める・確かめと同じ待ち合わせの中で控える・クリップごとのまとまりの同居・別の入力の控えを書き換えない・形の違う控えを読まない・`.lock` の待ち合わせ）と、工程の受け渡し（代役の Blender。続きから描く、作り直した .blend と設定の違いで描く前に止める、トラックの無い描画と、その途中で止まった描画の続き、工程のフォルダ、要素を足しても描いた要素をとばす、カメラと設定の違い、表情の絵の中身）を確かめた。

## 箱の部屋

`render.box_room` は、1 枚の背景の絵を、基準のカメラから床と奥の壁に投影して張った「箱の部屋」をキャラの .blend に足す。基準のカメラから見ると絵と同じ画になり、カメラが動くと床と壁がずれて奥行きが出る。リポジトリ外の 2 作目の VRM のダンスで、2D の再現版の部屋（2D の完成版と同じ横長の背景の絵）を作った台本を移した。

- **入力**: `blend`（キャラの .blend）、`image`（背景の絵）。
- **絵の中の仮の部屋**: 基準のカメラが原点で、右 x・下 y・奥 z。カメラは水平に奥を向く。長さの単位は仮のもので、`scale`（既定 1）倍が m。
  - `focal_px`: 基準のカメラの焦点距離（絵の画素）。`center_px`: 主点（[x, y]。水平線の上の消失点）。
  - `camera_height`: 床がカメラのどれだけ下か（仮の単位）。
  - `origin_px`: 世界の原点にする床の点（踊り手の立つ所）の、絵の画素 [x, 床に立つ所の y]。奥行きは focal × camera_height ÷ (y − 主点の y)。
  - `wall_px`: 床と奥の壁の境の、絵の行。壁はそこで床と交わる、絵の面と平行な面。
  - 世界の座標（X 右・Y 奥・Z 上、m）へは ((x − 原点の x) × scale, (z − 原点の z) × scale, (camera_height − y) × scale)。床は Z = 0。
- **形**:
  - 床: 絵の行 `floor.rows`（[最初, 終わり（含まない）, きざみ]。既定は [`wall_px`, 絵の高さ × 2, 24]。既定の最初の行は `wall_px` を小数のまま使い、床の奥の端を壁の下の端に合わせる）で段を切り、`floor.x`（仮の単位の x の並び。既定は試作の 57 列: ±70・±35 と、−13〜13 の 0.5 おき）で列を切った四角形の格子。投影は面の中でまっすぐには補間されないので、奥ほど粗く、手前ほど細かく切る。
  - 壁: 1 枚の四角形（絵の面と平行なので、1 枚でも投影と同じ）。`wall.x`（左右の端。既定 [−70, 70]）と `wall.top`（上の端の y。既定 −45）。
  - 頂点の uv は、基準のカメラで写した絵の位置。絵の外（回り込んで横や上を向いたとき）は、絵の端の色を引き伸ばす。
  - `shell`（既定あり。`null` で無し）: 絵の届かない向きを囲う暗い箱。`location`・`size`（m）、`color`（Workbench の物体の色。シーンの線形の値）。既定は試作の値。
- 物体は `collection`（既定 `model3d_room`。同じ名前のコレクションがあれば止める）に入れ、絵は .blend に詰め込む。元のファイルは変えない。
  コレクションは、アーマチュア（`armature` か最初のアーマチュア。無ければ今のシーン）の入ったシーンに足す（`render.passes` もそのシーンで描く。作業用のシーンを開いたまま保存されたファイルがある）。
- 計算の式と順は試作の台本と同じ（倍精度で計算して、Blender の単精度の頂点と uv に入れる）。
- 出力: `blend`、`report`（`model3d-box-room/0.1`。物体・頂点と面の数・原点と壁の奥行き・基準のカメラの位置（m）・設定）。
- 床に立つ所・壁の行が水平線より上のとき、壁が踊り手より手前のとき、床の段が 2 つに満たないとき、絵を読めないときは、Blender を起動する前に止める。

**確かめたこと**:
- CI（Blender なし）では、床と壁の頂点を基準のカメラで写すと uv の所に来ること、原点・壁・基準のカメラの位置、既定の床の列と段、悪い置き方を Blender の前で止めること、工程の受け渡し（代役の Blender）を確かめた。
- 2026-11-16 に Windows の Blender 5.2.4 で、リポジトリ外の試作（2 作目の VRM のダンスの 2D の再現版。横長の背景の絵 2880×1920、焦点距離 2059 画素、主点 (1425, 795)、カメラの高さ 1.094、`scale` 1.03、床に立つ所 (1440, 1662)、壁の行 958、床の段 [958, 2600, 24]）と比べた（実測）。
  - 頂点 3,937・面 3,809 の部屋と暗い箱を足し、`render.passes` の `room`（`room.world` は試作の [0.02, 0.015, 0.012]）で試作のカメラの 906 コマを描くと、888 コマが試作の部屋の要素と画素まで同じだった（残り 18 コマも最大 2/255。簡易描画の揺れの大きさ）。
  - 部屋のある場面で描くと、char は 887 コマが試作と画素まで同じになった（部屋の無い場面では 882 コマ。試作の場面にも部屋の物体があるため）。disc・head は 906 コマすべてで同じ。
  - 部屋を足す工程は約 4 秒。

## 演出版を 1 回で描く（`render.show`）

`render.show`（blend, cameras [, lights, hooks] → frames, report）は、演出版を Blender 5.x の EEVEE で 1 回でコマごとに PNG に描く。3 作目の VRM のダンスの演出版（リポジトリ外の台本。Blender 5.2 の EEVEE で、舞台・光・カメラ・塗りを 1 回で描いた）の描き方を移した。2 作目の演出版の、要素に分けて描いて外で重ねる描き方（`render.passes` と `mv dance passes`）と並ぶ、もう 1 つの描き方。`render.stage`（4.5 の LED の舞台）は 5.x では止まるので、5.2 で舞台ごと描くときはこちらを使う。

**流れ**（レシピの工程）:
1. `look.lit`: キャラの材質を、舞台の光の向きと色を受ける塗りに差し替える（[舞台の光を受ける塗り](#舞台の光を受ける塗りlooklit)）。
2. `plan.lights`: 光の段取り表から、コマごとの光の値（[光の台本](#光の台本planlights)）。
3. `plan.orbit` か `plan.shots`: コマごとのカメラ（`model3d-cameras/0.1`。絞りとピントは `fstop`・`focus_m`）。
4. `render.show`: 描く。作品ごとの舞台は、フック（下）で作って動かす。
5. `mv dance finish`: 描いたコマに、光のトラックの閃光・暗転と look を掛けて動画にする（docs/dance.md の「完成コマに仕上げを掛ける」）。

**カメラ**（入力 `cameras`。`render.passes` と同じ読み方）:
- 全部のコマを、カメラの位置・向き（四元数）・レンズ（とずらし）のキーにして置く。動きのぶれに、カメラの動きも入る。
  - 向きは、控えの行列から四元数にし、前のコマと内積が負なら符号を返す（間のコマで回り道をさせない）。
  - キーは動きのコマ f に打つ。f は整数で、T の順に増えていく並びにする（小数のコマや止めは描けない。止める）。整数から 1e-6 以内の f は、時刻の計算の丸めの端数とみなして整数にする（`plan.orbit` の 63.00000000000001 など）。
- カットの最後のコマのキー（`cuts` の T の 1 つ前のコマと、最後のコマ）は「次のキーまで止める」にし、ほかは直線でつなぐ。動きのぶれが、カットをまたがない。
- 受光は縦に合わせ、大きさは控えの `sensor_height_mm`（無ければ `camera.sensor_height`。既定 36 mm）。レンズは `lens_mm`（無ければ焦点距離の画素から）、ずらしは `shift`（無ければ主点から）。ずらしのあるコマが 1 つでもあれば、ずらし 0 のコマにもキーを打つ（打たないと、最初のずらしが前のコマへ延びて、写る範囲がずれる）。写る範囲は控えの `clip_m`（無ければ `camera.clip`）。
- 被写界深度: コマに `fstop` と `focus_m` があれば、その f 値とピントの距離（シーンの単位に直す）。無いコマは切る。
- 長さは m で、シーンの `scale_length` で直す。

**動きのぶれ**（`motion_blur`）: `shutter`（シャッターの開き。コマ。既定 0 で無し）、`steps`（分ける回数。既定 1）、`off`（ぶれを切る T。動きのつなぎ目の直前のコマなど）。位置は「コマから後ろ」（コマ T の絵は T から T + `shutter` まで。次のカットの姿を混ぜない）。

**光と塗り**（入力 `lights`。`plan.lights` の光のトラック）:
- `lights`: 光の物体の名前 → `{"energy": 値の名前, "base": W, "color": 値の名前}`。強さは `base` × 値（シーンの単位で直す）、色は値の 3 つの並び。
- `ambient`: 値の名前。`look.lit` の塗りの材質ごとに、値 × 材質の印 `model3d_lit_ambient_scale` を、まわりの明るさの RGB ノードに入れる。
- `world`: 空の色（線形の RGB。省くと .blend の空）。
- `bloom`: Glare の Bloom を合成で（`quality`・`threshold`・`smoothness`・`clamp`・`maximum`・`strength`・`size`。省いた値は Blender の既定。`bloom` を省くと合成を切る）。
- 値はどれも、描く直前にコマごとに入れる（キーにしないので、ぶれの間は止まっている。3 作目の台本と同じ）。

**表情**（`expressions`）: `{"objects": [物体の名前], "values": {形状キー: [[秒, 値], …]}}`。コマの時刻の値を、直線でつないで形状キーに入れる（`render.passes` の `values` と同じ）。
- 入れる値が描くときに効くように、この場で（.blend は保存しない）ミュートを外し、値が範囲の外なら範囲を広げ、ピン（形状キーを 1 つだけ見せる設定）を切り、形状キーのアニメーションとドライバーを外す（`render.face_sheet` と同じ手当て。報告の `key_changes`）。絶対の形状キーと基準のキーは止める。
- 合成の場面（白く光る板と、板を画面の外へ動かす形状キー。ミュート・範囲 0〜0.5・値 0 のアニメーション・ピンの罠つき）で、手当ての前は板が残り（値が効かない）、手当ての後は画面の外へ動いた（Blender 5.2.4）。

**絵の差し替えの表情**（`expressions` の `images` と、`times` か `events`）: 顔が 1 枚の色の絵で描かれたモデル（目も口も絵。形状キーが無い）は、表情を「色の絵をコマごとに差し替える」で作る（4 作目の 3D のダンスの持ち帰り。`render.passes` の `images`・`times` と同じ考え）。
- `images`: `{絵の名前: 絵の道}`（レシピのフォルダからの道か、絶対の道）。`objects` の物体の材質（`materials` を書けば、その材質だけ）の、絵のある画像ノードの絵を差し替える。`""` はもとの絵。絵の色の空間は、もとの絵に合わせる。ほかの物体も使っている材質は止める（その物体の絵まで変わるため。材質を分けるか `materials` で絞る）。ノードグループの中の画像ノードは差し替えない（材質の木の、すぐ下の画像ノードだけ）。
- `times`: `[[秒, 絵の名前], …]`（秒の小さい順）。コマの時刻に、半コマのゆとりを持たせて、時刻までで最後の名前の絵にする（表の秒を丸めて書いても、そのコマで「まだ始まっていない」にならない）。
- `events`（出来事の表。`times` の代わり）: 工程の側で、コマごとの絵の名前にしてから `times` にする（`model3d.face_pictures`）。
  - `mouth`: `[[秒, 口の絵], …]`（次の出来事までの口の絵）。`steps`: `{口の絵: [はさむ絵, …]}`（その口に入るとき、はじめのコマから 1 コマずつはさむ。出るときは逆の順。開ける口の 小 → 中 など）。
  - `eyes`: `[[始まりの秒, 終わりの秒, 目の絵], …]`。`blinks`: `[秒, …]` と `blink`: `[目の絵, …]`（まばたきのコマごとの目の絵。半目 → 閉じ目 → 半目 など。既定は置かない）。
  - 目と口の両方があるコマは、重ねた絵（`目の絵` + `join`（既定 `+`）+ `口の絵`）。`images` に無い絵の組み合わせが 1 コマでもあれば、秒と目と口を並べて止める（重ねた絵を足すか、表を直す）。
  - 秒は、近いコマの番号（`round(秒 × fps)`）に直してから比べる。`fps` は cameras の fps。
- `clock`: `"T"`（既定。時刻は (T − 1) / fps。形状キーの `values` も前と同じ）か `"frame"`（動きのコマ f から (f − `frame0`) / fps。`frame0` 既定 1）。見本のコマ（ばらばらの踊りのコマを T = 1 から並べた物）でも、踊りの時刻の表情になる。
- 報告の `face_pictures`（差し替えた画像ノードの数・T ごとの絵の名前）、観測の `face_pictures`（変わる回数・使う絵・絵の道と sha256）。`frames_dir` の入力の控えにも、絵の sha256 が入る。
- 4 作目の 3D のダンスで分かったこと: 表情ごとに色の絵まるごと（1 枚 18 MB ほど）を持つ。目と口を重ねた絵は数が増えるので、口が開いている間のまばたきは表に書かない（書くと止まる）作りにした。
- 確かめたこと: CI では、はさむ絵の入りと出・まばたきと目の区間と重ねた絵・絵の無い組み合わせで止めること・丸めた秒がそのコマから始まること・半コマのゆとり・時刻の決め方・設定の確かめ・工程の受け渡しを確かめた。
  4 作目の 3D のダンスの表情の出来事の表（リポジトリの外）を、この書き方に直して回すと、制作の台本が作ったコマごとの絵と、全 918 コマで同じだった（絵の変わる所 83 も同じ）。
  2026-11-23 に Windows の Blender 5.2.4 で、絵を貼った板を 3 コマ描くと、`times` の通りに もとの絵 → 赤 → 緑 になり、`clock` `"frame"`・`frame0` と `events` では、決めた動きのコマから赤になった。

**光と影のつなぎ分け**（`links`。Blender の Light Linking）: 幕だけを照らし、踊り手だけが影を落とす光のように、光ごとに照らす物と影を落とす物を分ける。
- 項目は `{"lights": [光の物体の名前], "receivers" か "exclude": [物体の名前], "blockers": [物体の名前]}`。物体の並びの代わりに `"character"` と書くと、踊り手（.blend にもとからある、描画に出るメッシュ。フックの `build` の前に数える）。
  - `receivers`: その光が照らすのは、この物体だけ。`exclude`: その光は、この物体を照らさない（ほかは照らす）。同じ項目に両方は書けない。
  - `blockers`: その光の影を落とすのは、この物体だけ（書かなければ、どの物体も影を落とす）。
  - 1 つの光の、照らす物（`receivers`・`exclude`）と影を落とす物（`blockers`）は、それぞれ 1 つの項目にだけ書く。
- 項目ごと・役ごとにコレクションを作り、光の `receiver_collection`・`blocker_collection` にする（`exclude` は、コレクションの中で照らさない設定）。フックの `build` の後で入れるので、フックが作った光と物体も指せる。報告の `links` に、項目ごとの物体の名前を残す。
- 3 作目の VRM のダンスの演出版では、踊り手の後ろの大きな輪の内側の円板を幕にして、踊り手の前の低い所からの光で、踊り手の影を幕いっぱいに映した。この光は幕だけを照らし（`receivers`）、影を落とすのは踊り手だけ（`blockers`: `"character"`）。ほかの光は幕を照らさない（`exclude`）ので、影が黒いまま残る。
  - Light Linking は、Blender 5.2 の EEVEE でも効いた。
  - 影を映す光は、強すぎると幕の色が白く飛ぶ（赤い幕がピンクになった）。幕の色が残る強さにする。
- 確かめたこと: CI（Blender なし）では、`links` の形の確かめ（止める形 11 通り）、役ごとのコレクションの作り方と照らさない設定、工程への受け渡しを確かめた。
  2026-11-18 に Windows の Blender 5.2.4 で、3 作目の演出版の幕と影の光を、台本と同じくフックの中でつないだ絵と、`links` でつないだ絵を 6 コマで比べた（実測）。
  2 コマは画素まで同じ、4 コマは描くたびの揺れと同じ大きさ（最大 1/255）。つなぎ分けを入れないと、幕に大きな影を映すコマは 207 万 3,600 画素のうち 200 万画素が変わった（差の平均 44/255）。影を映す光の消えているコマは同じ。

**フック**（入力 `hooks`。作品ごとの舞台を作って動かす Python の台本）:
- レシピに入力として書いたときだけ、Blender の中で実行する（台本は Python なので、何でもできる。自分で書いたか確かめた台本だけを渡す）。sha256 を報告と観測に残す。
- `build(ctx)`: .blend を開いて描く設定と空を入れた後、カメラを置く前に 1 回。舞台の物体・材質・光を作る。
- `frame(ctx)`: コマごとに、光・塗り・被写界深度・表情の値を入れた後、描く直前。舞台の値を動かす。
- `ctx`: `bpy`・`scene`・`unit`（`scale_length`）・`settings`・`track`（光の値 `{T: {名前: 値}}`）・`cameras`（`{T: カメラのコマ}`）・`state`（フックが使ってよい辞書）・`config`（フックの値。下）。`frame` では `T`・`f`・`t`（秒）・`values`（そのコマの光の値）・`camera`（そのコマのカメラ。`position` は m）も。
- どちらの関数も省ける。`lights` の光の物体は、フックが作ってもよい（`build` の後で確かめる）。
- フックの値（入力 `hook_config`。任意）: フックが読む作品ごとの値（色・強さ・出来事の表の場所など）の JSON の辞書。`ctx.config` で読める（無ければ空の辞書）。中の鍵はフックの物なので、`render.show` は確かめない。レシピの入力なので、sha256 が実行の記録に残り、描いている間に書き換わったことも分かる（報告と観測の `hooks.config` にも、道と sha256）。`hooks` と組で渡す（`hooks` が無いと止める）。
  - 4 作目の 3D のダンスでは、`render.show` のパラメーターは知らない鍵を止めるので、フックの値を別のファイルにして、環境変数で場所を渡していた。レシピの入力ではないので sha256 が実行の記録に残らず、描くたびに写しを取って手で残した。
  - フックが自分のフォルダの隣のファイル（ほかの台本・出来事の表）を読むと、それは入力にならない（書き換えても気づかない）。値のファイルに書けるものは、`hook_config` に寄せる。
  - 確かめたこと: CI（代役の Blender）では、受け渡し・sha256・`hooks` との組・辞書でない値で止めること・渡さないときは前と同じことを確かめた。2026-11-23 に Windows の Blender 5.2.4 で、`ctx.config` の値で露出を入れるフックを描くと、報告の `view` の露出がその値になり、値のファイルの sha256 が報告の `hooks.config` と実行の記録の入力で同じだった。

**描く設定**: EEVEE（`samples` 既定 64・`raytracing`・`shadows`）、色の変換は `view_transform`（既定 Standard。Look・ガンマ・カーブは無し）、PNG の RGB 8 ビット、`resolution` 既定 1080×1920。
- 露出（`exposure`。段。−32〜32）: 色の変換の Exposure。省くと 0 に決める（前と同じ）。Look・ガンマ・カーブは決め打ちのまま、露出だけを設定にした（フックで入れ直すと効くが、報告に残らなかった）。描いたときの値を報告の `view` に残す。
- 霧（`volumetric`）: `tile_size`（升の大きさ。画素。1・2・4・8・16）・`samples`（1〜256）・`start`・`end`（m ではなくシーンの単位の奥行き。0 より大きく、start < end）・`shadows`（霧の中の影）・`shadow_samples`（1〜128）。書いた鍵だけシーンに入れ、省いた鍵は .blend の値のまま。描いたときの値を報告の `eevee` に残す。霧の場面（ボリュームの材質）が無ければ、絵は変わらない。
  - 霧の升は画素の数なので、画面を 2 倍の大きさにしたら升も 2 倍にすると、同じ細かさのままで、時間が増えにくい（4 作目の 3D のダンスで、1080×1920 の升 8 が、540×960 の升 4 と同じ細かさ。1080×1920 で升 4 → 8 にすると、1 コマ 28.2 → 21.9 秒）。
  - 確かめたこと: CI では設定の確かめ（範囲・列挙・start < end・知らない鍵）と受け渡しを確かめた。2026-11-23 に Windows の Blender 5.2.4 で、合成の場面（270×480）を `exposure` −1 と `volumetric`（升 4・標本 32・end 40・影あり）で描くと、報告の `view` と `eevee` に入れた値が入り、白く飛んでも黒くもない画素（992 画素）の線形の明るさが、露出 0 の絵のちょうど 0.50 倍だった。省いた鍵（start・shadow_samples）は .blend の値のまま。
- 影の領域（`shadow`）: `pool_size`（影の領域の大きさ。MB。16・32・64・128・256・512・1024・1536・2048 のどれか。Blender 5.2.4 の列挙）と `resolution_scale`（影の細かさ。0 より大きく 1 以下）。書いたときだけシーンに入れ（省くと .blend の値のまま。新しいシーンの既定は 512 MB・1.0）、描いたときの値を報告の `eevee` に残す。
- 影の領域のあふれ: EEVEE は、影の領域に入りきらないと、記録に `Shadow buffer full, may result in missing shadows and lower performance. (必要 / 上限)` と書き、そのまま描き続ける（あふれたコマは、描くたびに影が変わる）。描き終えたあと、Blender の記録（工程のフォルダの `blender.log`）からこの行を数え、報告の `shadow_overflow`（行の数・必要がいちばん大きい行の「必要 / 上限」・扱い）と観測に残す。
  - `shadow.overflow`: `"fail"`（既定）なら、1 行でもあれば工程を失敗にする。失敗の文に、行の数・「必要 / 上限」・直し方・描いたコマの場所を書く（コマとその一覧 `frames` は残る）。`"warn"` なら、報告と観測に書くだけで通す。
  - **変わったこと**（4 作目の 3D のダンスの持ち帰り）: 前は、あふれても成功として通っていた。既定を「止める」にしたので、前の作品のレシピを回し直すと止まることがある。止まったら、影の細かさを下げる（下の知見）か、影つきの光を減らすか、前と同じに通すなら `"shadow": {"overflow": "warn"}` と書く。
  - Blender の記録は、終わったあとに標準出力・標準エラーの順で書くので、どのコマであふれたかは行の順からは分からない（数と大きさだけ）。
  - 確かめたこと: CI（代役の Blender）では、行の数え方（数の無い行・いちばん大きい必要）、既定の失敗と失敗の文、`warn`、コマの一覧が失敗でも残ること、設定の確かめを確かめた。2026-11-23 に Windows の Blender 5.2.4 で、合成の場面（床・球 40・影つきの点の光 48。1080×1920・1 標本・2 コマ）を描くと、16 MB で 4 行（必要 / 上限 8439 / 64）、既定の 512 MB で 4 行（8439 / 2048）、2048 MB・細かさ 0.5 で 0 行。16 MB と 512 MB は既定で工程が失敗になり、`warn` では同じ 4 行を書いて通った。報告の `eevee` は入れた値（16・512・2048、1.0・0.5）。上限は 1 MB あたり 4 枚（16 MB → 64、512 MB → 2048）。
- .blend から引き継ぐと絵をすり替えうる設定（シーケンサー・描く範囲の切り取り・画素の縦横比・多視点・透ける背景・表示の装置・出力の色の上書き・マーカーに結んだカメラ）は決まった値にし、.blend と違っていたものを報告の `render_changes` に残す（`render.face_sheet` と同じ）。
- 画像に日時などの控え（メタデータ）を書かない。
- 描くファイルは、工程のフォルダの `frames/f<T>.png`。実行ごとに新しいフォルダに描き、同じ名前のファイルがあっても描き直す（とばさない）。`only` で T を選ぶ。
- 区切って描いて集める・続きから描く（`frames_dir`。レシピのフォルダからの道か、絶対の道）: 書くと、そのフォルダへ描き、もうあるコマはとばす。`only` で区切りを変えて何回も回すと、1 つのフォルダに集まる。途中で止まっても、次は続きから描く。
  - とばしてよいかは、`render.stage` と同じフォルダの入力の控え（`model3d_inputs.json`。[コマのフォルダの入力の控え](#コマのフォルダの入力の控え)）で確かめる。入力のまとまりは、.blend・カメラ・光のトラック・フック（台本と、それが読む同じフォルダの台本）・フックの値・設定（描く絵を変えない `only` と `shadow.overflow` は除く）・描く台本・Blender の版。
  - `render.show` は、1 つのフォルダに 1 つの描画だけを集める: 今回描かないコマでも、フォルダの控えに別の入力のコマがあれば、描く前に止める（区切りごとに入力を変えると、描き直さなかった区切りが前の絵のまま混ざるため。`render.stage` はクリップごとに別の入力で同じフォルダへ描けるので、コマごとに見る）。止まったら、別のフォルダへ描くか、フォルダのコマと控えを消して描き直す。
  - 影の領域があふれた描画のコマ: あふれの知らせは描いた回の Blender の記録にしか出ないので、`frames_dir` に、あふれた描画で描いたコマの控え（`model3d_show_overflow.json`）を置く。続きから描くとき、とばすコマにこの控えがあれば、`shadow.overflow` が `"fail"` なら描く前に止める（そのコマを消して描き直す。`"warn"` で前に通したコマも、`"fail"` に変えれば止まる）。`"warn"` なら通して、報告の `shadow_overflow.reused` に数を書く。あふれずに描き直したコマは、控えから消す。Blender が途中で止まった（落ちた・時間切れ）ときも、書けた記録にあふれがあれば、今回描けたコマを控える。
  - 報告の `skipped_existing`、観測の `skipped_existing` と `inputs`（入力のまとまりの番号と、とばしたコマの数）。`frames` は、今回の並び（`only`）のコマの一覧。
  - 区切って描けるのは、フックがコマをまたいで状態を持たないときだけ（とばしたコマでは、フックの `frame` を呼ばない）。形を時刻と種だけで決めるフックなら、区切っても同じ絵になる。前のコマの状態を使うフック（粒を積み重ねるなど）は、区切ると絵が変わる（道具では見分けない）。

**出力**:
- `frames`: `model3d-frames/0.1`。描いたコマのファイルの sha256 と大きさ、描いていない T。`render.assemble` と `mv dance finish` にそのまま渡せる。
- `report`: `model3d-show-render/0.1`。描いたコマ、カメラのキーと止めたコマ、ぶれたコマ、光、塗りの材質の数、フック、`render_changes`、時間。

**3 作目の VRM のダンスで分かったこと**:
- 光らせていない板（光の量が 0 の発光の面）は、黒い板として残る。消えている場面で、花びらと筋が黒い四角に見えた。光の量が小さいときは透明へ寄せる材質にする（フックの舞台の作り方）。
- 面の光源は、鏡の床に白い円として映る。光源の映り込みを切る（光の `specular_factor` を 0）。
- 床の映り込み（EEVEE の画面の反射）は、画面に見えている物だけが映る。
- にじみ（光のあふれ）は、合成の Glare の Bloom で、描くのと同時に入れる。Blender 5.2 は `scene.compositing_node_group` に合成の部品を置く（`scene.node_tree` は無い）。
- 表情の形状キーを入れた .blend は、描くのが 1 コマ約 4.2 秒から約 5.6 秒になった（1080×1920・24 標本・動きのぶれ 0.4）。
- 動きのぶれを 3 回に分けて描くと（`steps` 3）、速く回り込むカットで輪が 3 重に見えた。3 作目は 1 回のまま。

**4 作目の 3D のダンスで分かったこと**（舞台も 3D の部品で組み、`render.show` で 1 回で描いた作品。リポジトリの外の制作）:
- 大きい画面（1080×1920）で影つきの光を足すと、影の領域があふれた。下書きの大きさ（540×960）では、同じ場面・同じ光でも 0 行だった。
  - 舞台と踊り手だけの場面でも、既定の 512 MB では 4 コマで 25〜73 行（必要 / 上限は最大 3494 / 2048）。1024 MB にすると 0 行。
  - 出来事の光（影つきの点の光）を足すと、1024 MB でも 28 コマで 38 行（最大 6252 / 4096）。影の細かさを 0.5 にすると、試しの 31 コマと本番の全コマで 0 行。
- あふれると、同じコマでも描くたびに影が変わる。同じコマを設定を変えて 7 回描くと、画面の一部の明るさの平均が 2 通り（125 と 159）に割れ、床の踊り手の影も描くたびに違った。全部のコマで描くと、ちらつきになるおそれがある。
- 影の細かさを画面の大きさに合わせる（下書きの 2 倍の大きさなら 0.5 倍）と、下書きと同じ影になり、あふれない。細かさ 0.5 の本番（1080×1920）を半分に縮めた絵と、下書き（540×960・細かさ 1.0）の差は、全コマの平均で 1.58/255（いちばん大きいコマで 4.85）。
- 本番の前に、本番の大きさで要所を試し描きして、この行を数える（下書きの大きさでは出ない）。
- 進め方の段（どの段でも、直す所が 1 つ以上見つかった）: 止め絵の見本 → 小さい画面で要所を毎コマ → 全部のコマの下書き → **本番の大きさで要所の試し描き** → 本番。
  - 本番の大きさの試し描きは、要所（光と出来事の多いコマ・寄り・暗いコマ）を 30 コマほど。見る数は 3 つ: 影の領域のあふれの行数（報告の `shadow_overflow`）、1 コマの秒（報告の `seconds_per_frame`。本番の全部のコマの時間の見積もり）、下書きとの絵の差（本番を下書きの大きさに縮めて比べる。霧の升と影の細かさを画面に合わせれば、平均 1.5/255 ほど）。
  - 描く時間は標本の数にほぼ比例した。8 標本と 24 標本で、輪郭線と髪の縁は見分けがつかなかった。映り込みの計算（`raytracing`）は、入れても時間がほぼ変わらなかった（測っただけで、理由は見ていない）。
- 変えた所だけ描き直すとき: 先に、変えていないコマを 1 つ描き直して、前の版と同じ（差が 1/255 以内。描くたびの揺れ）ことを `check.frames`（か `check.track_match`）で確かめてから、変えた区間だけを描く。同じでなければ、変えていないつもりの入力（.blend・カメラ・光・フック）がどこかで変わっている。
  - 変えた区間は、新しいフォルダへ描く（入力を変えると、前のフォルダの `frames_dir` には描けない。上の「区切って描く」）。2 つのフォルダのコマを 1 本にするときは、使うコマを 1 つのフォルダへ写してから、`render.assemble` にコマの一覧（`model3d-frames/0.1`。1 つの `directory`）を書いて渡す（下の [動画にまとめる](#動画にまとめる) の「外で作ったコマ」）。
  - 4 作目では、最後の 21 コマだけを直して差し替えた。前の版と同じ設定で 1 コマを描き直した差は、平均 0.000・最大 1/255 だった。
- 長い描画を止めるとき: 工程を呼んだ側（作業の道具）を止めても、子のプロセスの Blender は残って描き続けることがある（4 作目で起きた）。止めたら、Blender のプロセスも残っていないか確かめる。

**確かめたこと**:
- CI（Blender なし）では、カメラのキーの決め方（止めるコマ・動きのコマの確かめ・四元数の符号・レンズとずらし・被写界深度の組）、光の強さの単位、版の確かめ、アクションの F カーブの読み方、設定の確かめ、台本の引数と工程の受け渡し（代役の Blender）を確かめた。
- 2026-11-18 に Windows の Blender 5.2.4 で、3 作目の VRM のダンスの演出版（リポジトリ外の台本）と比べた（実測）。
  - 踊りの .blend に `look.lit`、台本の光の関数を写した段取り表から `plan.lights`、台本のカメラの関数から書いたカメラの控え（909 コマ。被写界深度のコマ 194）で、工程だけで `render.show` まで通した。舞台は、台本の舞台の所を写したフック（非公開）で作った。
  - 照合の元は、台本の写しで同じ 6 コマ（1080×1920・24 標本・動きのぶれ 0.4。カットの境で止めるコマと、ぶれを切るコマも台本と同じ）を、同じ並びで描き直したもの。この PC の EEVEE は同じ入力なら同じ絵になるが、同じ台本で 2 回描いても、最大 1/255 の揺れ（違う画素 0〜4,650）が出るコマがあり、まれに数画素だけ 3/255 になる（7 画素）。
  - カメラのキーを止めるコマ（30 のカットの最後）は、台本と同じコマだった。
  - カメラの控えの行列から四元数に戻した `render.show` の絵は、台本と最大 16/255 違った（違う画素は 207 万 3,600 のうち 1.6 万〜3.4 万）。台本の四元数との差は成分で最大 3×10⁻⁷ で、この丸めだけで絵が変わる（[段取りを組む](#段取りを組む) で、カメラの位置が 1 ulp 違うだけで最大 6/255 違ったのと同じ種類）。
  - 確かめのため、台本の四元数をそのまま入れる写し（リポジトリには入れていない）で描くと、6 コマのうち 3 コマが画素まで同じ、3 コマは揺れと同じ大きさ（最大 1/255）だった。光・塗り・カメラのキーと止め・ぶれ・被写界深度・表情・にじみ・フックの舞台は、台本と同じ計算になっている。
  - 表情の手当て（上の「表情」）を足した後も、6 コマは足す前と揺れの範囲で同じだった（この作品の頭の形状キーには、手当てが要らなかった。`key_changes` は空）。
  - 混んだ PC で、1 コマ約 9 秒（6 コマ）。
- 区切って描く・続きから描く（`frames_dir`）: CI（代役の Blender）では、区切りを重ねても描いたコマをとばして 1 つのフォルダに集まること、フック・設定を変えると描かないコマがあっても止まること、`shadow.overflow` だけを変えても止まらないこと、控えの無いコマで止まることを確かめた。
  2026-11-23 に Windows の Blender 5.2.4 で、合成の場面（床・球 40・影つきの点の光 48。270×480・4 標本）を、T 1 だけ描いてから T 1〜2 を同じフォルダへ描くと、T 1 はとばし、2 回に分けて描いたコマは、通しで描いた同じコマとバイト単位で同じだった（2 コマとも）。

## 演出の出来事（`render.show` のフックから呼ぶ部品）

線の束・面を走る枝分かれ・飛び散る粒・光の玉のような「出来事」を、出来事の表（時刻と場所）からコマごとに 3D の光る形にする部品。
`render.show` のフック（[演出版を 1 回で描く](#演出版を-1-回で描くrendershow)）が `build` と `frame` から呼ぶ。リポジトリ外の 4 作目の 3D のダンスの演出の台本（作品の形のまま書いた物）を、作品によらない型に直して移した。

**作りの分け方**: 形は numpy だけで作る（Blender なしで試せる。`blender_adapter/` の中にあり、フックからは `import fx_strips` のように読める。`render.show` は自分のフォルダを `sys.path` に足してから動く）。Blender の側は、四角形をメッシュにして足し算で光る材質を付け、光と物体の属性を書くだけ。
作品の値（色・発光の強さ・光の W・幅と長さ・時刻・種・間隔）は既定に入れない。出来事の表に書く。

### 光る帯と玉の四角形（`fx_strips`）

- `Quads(camera_position)` に、`strip(points, widths, values, flat=False)`（点の列を帯に）と `dot(centre, size, value)`（カメラを向いた四角形 1 つ）でためて、`arrays()` で `(co (N, 4, 3), uv (N, 4, 2), value (N, 4))` を出す。
- 帯の幅の向きは、点ごとに 接線（前後の点の差）× 視線（点からカメラへ）。`flat=True` は面に寝た帯（接線 × 上。床を走る形）。接線と視線が平行なら 接線 × x。
- 四角形の頂点は `[p − s, p + s, q + t, q − t]`。UV の u は幅の向き（足し算で光る材質が、真ん中を白く・縁を色つきにするのに使う）、v は帯の向き。`value` は頂点ごとの強さ。
- `cut(points, fraction)` は、折れ線を根もとから道のりの割合で切る（伸びていく線）。
- 長さがほぼ 0（10⁻¹² より短い）の向きは 0 とみなす（長さ 0 の帯は、幅も 0 の四角形になる。台本の単精度の計算と同じ）。
- `untwist=True` は、幅の向きが前の点と逆を向いたら返す（急に折り返す線で、帯が蝶ネクタイの形にねじれない）。台本はこれをしていなかったので、既定は切ってある。
- 骨の通り道の帯（`model3d.bone_ribbon`）にも、任意の折れ線を受ける `polyline_ribbon_vertices(points, widths, camera_pos)` を足した（同じ幅の向きの計算で、`build_ribbon_topology(n − 1)` の面と UV でメッシュにできる。ねじれは返す）。

**確かめたこと**: CI（Blender なし）では、幅の向きが接線と視線の両方に垂直で、幅が渡した値になること、床に寝た帯が水平なこと、隣の四角形と縁の頂点が同じ所、UV と強さの並び、カメラを向いた四角形の向きと大きさ、ねじれを返すこと、道のりで切ること、`polyline_ribbon_vertices` の形と悪い入力を確かめた。作品の形との照合（帯の頂点の位置）は、帯を使う型の節に書く。

### 出来事の表と時計（`fx_events`）

`Table(spec, path)` は出来事の表（`{"fps", "frame0", "events": [出来事, …]}`）と骨の通り道（`model3d-bone-path/0.1`。骨の点を使うときだけ）を読み、出来事を始まりの順（同じなら表の順）に並べる。出来事の種類と、種類ごとの欄は、形を作る部品（下の節）が読む。表は、どの欄も知らないまま並べるだけ。

- **始まり**: `frame`（動きのコマの番号）か `t`（踊りの秒。コマ f の秒は (f − `frame0`) ÷ `fps`）のどちらか 1 つ。秒は、表を読むときに一度だけコマの番号（`frame0` + t × `fps`）に直し、効くかどうか（`within`）はコマの番号で比べる。形を決める年齢（`age`）は、秒で書いた出来事は (コマの秒 − t)、コマで書いた出来事は (f − frame) ÷ fps。
- **出来事の時刻とコマの時刻を、秒の小数で比べない**: コマの時刻（n ÷ 30 秒）を小数 3 桁に丸めて書くと、0.3 ミリ秒ほど後になり、そのコマでは「まだ始まっていない」になる（4 作目の 3D のダンスでは、1 コマだけの出来事が 1 つ出なかった）。そのコマから効かせたいなら `frame` で書く。秒で書いた始まりがコマの頭の直後（0.05 コマ以内）にあると、`notes` に「1 コマ遅れて効く」と書く。1 コマだけの出来事は、出たかどうかを四角形の数で確かめる。
- **点の書き方**（`point`）: `[x, y, z]`（世界の座標）／`{"point": 名前, "offset": [x, y, z], "frame" か "t": いつ}`（骨の通り道の点。いちばん近い標本に offset を足す。いつを省くと、そのコマ）／`{"camera": [右, 上, 前]}`（そのコマのカメラから見た位置）。
  カメラから見た点へ向かう出来事は、カットの替わり目をまたぐと、前のカメラへ向かって描かれる。カットの頭から始める。
- **乱数**: `rng(*鍵)` は整数だけの鍵で決まる `random.Random`（文字列の種は、実行ごとに変わることがあるので使わない）。形は時刻と種だけで決まる（どのコマから描いても同じ。区切って描ける。変えた所だけ描き直せる）。
- **そのコマだけに出る出来事**: `when`（値の名前）を書いた出来事は、そのコマの値（光のトラックの値など）の大きさが 0.5 以上のコマにだけ出す（`gated`。白黒のコマにだけ出す線など）。
- `tick(f, every)` は、形の細かい揺れを `every` コマごとに替える整数。

**確かめたこと**: CI（Blender なし）では、整数だけの鍵の乱数（文字列・小数・真偽を止める）、始まりをコマの番号で比べること、丸めた秒が 1 コマ遅れることと `notes`、効く間、`when`、点の 3 通りの書き方と範囲の外、悪い表と悪い点を確かめた。
4 作目の 3D のダンスの出来事の表を読むと、`notes` は、足の着地の出来事と、白黒のコマにだけ出す線に出た（どちらも、丸めた秒がコマの頭の 0.010 コマ後にある）。その線は、作品の動画でも、意図したコマに出ず、次のコマにだけ出ていた。

### 折れ線の束（`fx_bundle`）

2 点（`from`・`to`）のあいだを、区間の中点を区間に垂直な向きへずらして折った線（幹）と、幹の折れ目から斜めに出る枝・孫の枝の束。`draw(table, event, f, camera, core, halo, gain, values)` が、そのコマの帯を `core`（と `halo`）の `Quads` にため、光の候補（強さ・位置・色）を返す。

- 大きな形（幹の折れ・枝の出どころ）は種だけで決まり、細かい折れは `every` コマごとに替わる。
- 時刻で、伸びる（`grow` 秒。先が根もとから道のりで進む。枝は、幹の出どころまで届いてから伸びる）→ 光る（`hold`）→ 消える（`fade`。太さが減り、明るさが (1 − k)^1.5 で落ちながらちらつく）。
- `halo`（幅の倍率）を書くと、同じ線を幅の広いにじみの帯にもする（芯と 2 枚。白い地のコマでは、呼ぶ側が `halo` に None を渡して作らない）。
- `branch_start`: 枝と孫の枝の出はじめの決め方。既定の `index` は台本と同じで、出どころの折れ目の番号の割合（幹が届く割合がその値を超えたら枝が出る。孫の枝は親の枝の真ん中の番号）。中点をずらした幹は区間の長さがそろわないので、伸びる間に、先端がまだ届いていない所に枝が出たり、通りすぎてから出たりする。`length` は、描く親の線の道のりの割合で決め、伸びる線の先端が出どころに届いたコマから枝が出る（線の形は同じ）。伸びる間（`grow`）を長く見せる出来事や、カメラの近くで伸びる線では `length` を選ぶとよい。既定を `index` にしたのは、作品の形の照合（下の 25 コマ）を変えないため。CI では、既定が台本の割合のままであることと、`length` で親の線を切った先端が出どころと重なることを確かめた。
- 欄（既定の無い欄は書く）: `from`・`to`・`width`・`jag`・`levels`（[幹を折る回数, 細かく折る回数]）・`forks`・`fork_length`（枝の長さの範囲。根もとと先の距離に対する割合）・`seed`・`grow`・`hold`・`fade`。任意: `every`（既定 1）・`halo`・`gain`・`light`（W）と `light_at`（根もとからの割合）と `colour`・`when`。
- 形の作りの定数（枝の幅の割合・先の細り・開く角度・枝の長さの下限 m など）は `FORM` にある（台本と同じ値。台本でも出来事ごとに変えなかった値なので、出来事の欄にしていない。変えるなら `FORM` を書き換える）。

**確かめたこと**: CI（Blender なし）では、同じコマなら同じ形、幹が根もとから先へ届くこと、枝の出どころが細かい折れで動かないこと、伸びる → 光る → 消える、寿命の外と `when`、にじみの幅と光の候補、足りない欄で止めることを確かめた。
4 作目の 3D のダンスの形の照合の元（台本で作った形を数にした物。25 コマ）のうち、束だけが線とにじみを描く 7 コマで、四角形の数（線 664・にじみ 664）が同じ、頂点の位置の差は最大 2.2 × 10⁻⁶ m、強さの差は最大 5.0 × 10⁻⁷、UV は同じだった（台本は単精度の mathutils で計算し、照合の元は小数 6 桁に丸めてあるので、許しは 1 × 10⁻⁵ にした）。

### 面を走る枝分かれ（`fx_surface`）

根もと（`at`）から `arms` 本の腕が、進みたい向きへ引き戻されながら折れて進み（向きを乱数で足していくだけだと輪を描くので、前の向きのずれを 0.55 だけ残す）、鋭い角度で枝を出す。区間ごとに根もとからの道のり（歩幅の和）を持ち、先端が `speed` m/s で進む。先端が明るく（道のりで `tail` m ごとに e 分の 1）、通った後は `rest` だけ残る。`inward: true` は内向き（外向きの鏡。道のりを先から数え、区間の向きを返すので、枝の先から根もとへ集まる）。

- `Tree(table, event)` が、始まりのコマの根もとの点と種で形を 1 回だけ作る（どのコマから作っても同じ。カメラから見た点は使えない）。`tree.draw(table, f, quads, gain)` が、そのコマの床に寝た帯をためて、光の候補を返す。
- 水平の面（高さ `height`。既定 0）だけ。根もとの点の高さは使わない。
- 欄（既定の無い欄は書く）: `at`・`radius`・`arms`・`step`・`width`・`depth`・`branch`・`seed`・`speed`・`fade`・`tail`・`rest`。任意: `inward`・`height`・`gain`、`light` と `light_height`・`light_decay`・`colour`。
- 効く間は、始まりから radius × 1.8 ÷ speed + fade 秒（radius × 1.3 ÷ speed 秒から fade 秒で消える）。

**確かめたこと**: CI（Blender なし）では、どの区間も前の区間の終わり（同じ道のり）から出て、道のりが根もとから先へ増えること、腕の数、面に寝ること、先端が進むこと、内向きは外向きの鏡（先から始まり、全部に届いた後は同じ区間を逆向きに描く）、寿命の外、カメラから見た根もとを止めること、どのコマから作っても同じ形を確かめた。
4 作目の 3D のダンスの形の照合の元の 25 コマのうち、床の枝分かれのある 9 コマ（外向き・内向き・小さな枝分かれ）で、四角形の数（合わせて 8,447）が同じ、頂点の位置の差は最大 1.7 × 10⁻⁶ m、強さの差は最大 5.3 × 10⁻⁷ だった。
- 道のりは歩幅の和なので、先端の道のりと区間の根もとの道のりが、ちょうど同じになることがある（そのとき長さ 0 の四角形が 1 つできる）。台本と同じ数にするため、秒で書いた出来事の年齢は台本と同じ式（コマの秒 − t）で計算する（コマの番号から計算すると、丸めが 1 ulp 違って、長さ 0 の四角形の数が変わった）。

### 飛び散る粒（`fx_particles`）

出来事の点（`at`。始まりのコマの点。カメラから見た点は使えない）から、向き（標準正規の向きの z に `up` を足して長さ 1）と速さ（`speed` の範囲）を種で決めて飛び散り、空気の抵抗（`drag` 1/s）で遅くなりながら重力（`gravity` m/s²）で落ちる。`floor`（m）より下へは行かない。粒ごとの寿命は `life` × 0.5〜1.6 で、明るさは (1 − 年齢 ÷ 寿命)^0.7。`streak` 秒前の位置からの筋にし、短い間に動いた長さが `size` より短い粒は、カメラを向いた四角形にする。`draw(table, event, f, camera, quads, gain)` は描いた粒の数を返す。

- 欄（既定の無い欄は書く）: `at`・`count`・`speed`・`up`・`life`・`drag`・`gravity`・`streak`・`size`・`seed`。任意: `floor`・`gain`。
- `fx_shapes` の「はじけ」（`render.passes` の効果）とは別に置いた。はじけは、形ごとに作り置いた numpy の乱数を同じ順で引き（火の粉・はじけ・火花・炎の輪で 1 つの並び）、画面の上の速さで筋の長さを決めて色を出す。こちらは、出来事ごとの出どころ・粒ごとの寿命・重力・床の高さを持ち、足し算で光る材質が掛ける強さを出す。はじけに足すと、はじけの乱数の引き方か `render.passes` の絵が変わるので、混ぜていない。

**確かめたこと**: CI（Blender なし）では、寿命の外は 0 で、寿命の短い粒から消えること、床より下へ行かないこと、同じコマなら同じ形を確かめた。
4 作目の 3D のダンスの形の照合の元の 25 コマのうち、粒だけを描く 8 コマで、四角形の数（860）が同じ、頂点の位置の差は最大 1.8 × 10⁻⁶ m、強さの差は最大 5.2 × 10⁻⁷ だった（重力は、台本の 4.9 × 倍率 を、9.8 m/s² × 倍率 として渡した）。

### 溜めて・撃って・余韻（`fx_charge`）

向きを 2 点（根もと `base` → 先 `tip`）で決める 3 段の出来事。`draw(table, event, f, camera, layers, gain, mono)` が、そのコマの段の形を `layers`（`{"orb", "core", "halo", "fine", "motes"}`。どれも `Quads` か None）にため、光の候補の並びを返す。

- 溜め（始まりの `charge` 秒前から）: 先の光の玉が育ち、まわりから短い線が先へ集まり、決まった向きの粒（`motes` 個）が外から内へ流れる。
- 撃つ（始まりから `hold` 秒）: 先の閃光の玉・十字の光条（`flash` 秒）・広がる輪（`ring` 秒。`ring_speed` m/s）。向きへ伸びる芯（`length`・`width` m。`halo` 倍のにじみ）に線が巻きつき（`wraps` 本。毎コマ形が変わる）、枝が跳ねる（`forks` 本）。
- 余韻（その後 `after` 秒）: 先の玉が小さくなり、点の列 `along`（省くと [tip, base]。先・手首・ひじなど）に沿って短い線が残る。
- 細かい部分の大きさ（玉・集まる線・粒・巻きつく線・枝・光条・輪・余韻の線の幅と距離）は、`size`（m）に掛ける割合（`FORM`。台本と同じ値）。光の色 `colour` は 1 つか、段ごと `{"charge", "release", "after"}`。
- `mono`（そのコマの白黒の値）が 0.5 より大きい白い地では、玉とにじみを作らない。大きさが 0.5 より大きければ、光条も作らない（しきいは `fx_blender.mono_frame` と同じ。白い地で十字の光条と輪を重ねると、照準の印に見える）。
- 腕の向きへ撃つ出来事を正面から撮ると、線が体の前を通る。腕の横の手前から撮ると、光が空いた側へ走る。

**確かめたこと**: CI（Blender なし）では、3 段の切り替わり（溜めの前・溜め・撃つ・余韻・終わり）と段ごとの形、撃つ芯の向きと長さ、白黒のコマで玉・にじみ・光条を作らないこと、同じコマなら同じ形を確かめた。
4 作目の 3D のダンスの形の照合の元の 25 コマのうち、この型が描く 10 コマ（1 回目の溜め 2・撃つ 4・余韻 1 と、2 回目の撃つ 3。撃つコマには、白黒のコマにだけ出る束と、ほかの出来事の粒も重なる）で、物体ごとの四角形の数が同じ（線と輪 1,083・芯と枝と光条 1,347・にじみ 65・粒 429・玉 8）、頂点の位置の差は最大 1.7 × 10⁻⁶ m、強さの差は最大 5.3 × 10⁻⁷ だった（size 1 で、台本の大きさと同じになる）。

### 順番に点く・消える部品（`fx_sequence`）

舞台の灯りの列のような部品の並びに、出来事ごとに、順番（`order`）と間隔（`step` 秒）で明るさ（`to`）を入れる。`levels(table, events, count, f)` が、部品 `count` 個のそのコマの明るさを返す（表の始まりの順に、後の出来事が上書きする）。

- `flicker`（秒）: 点きはじめのちらつき（0.25〜1 倍。コマごとに替わる乱数。種は `seed`）。`over`・`surge`: 点いた直後の `over` 秒、明るさを 1 + `surge` 倍から 1 倍へ下げる。
- まだどの出来事も届いていない部品は、いちばん早い出来事の `from`（書かなければ 0。並びに出来事が 1 つも無ければ 1）。
- 始まりは、部品ごとに (始まり + 順位 × `step`) をコマの番号で比べる。ちらつきの乱数を替えるコマは、部品の年齢のコマの数（整数に 10⁻⁶ コマの許しで切り下げる）。
- Blender の側は、明るさを物体の属性に書き（`fx_blender.set_level`）、材質が属性を光の強さに掛ける（`fx_blender.drive_by_object`。1 つの材質を、部品ごとに違う明るさで描ける）。

**確かめたこと**: CI（Blender なし）では、順番と間隔、点きはじめのちらつきの範囲、点いた直後の明るすぎが下がること、後の出来事の上書き、出来事の無い並びを確かめた。
4 作目の 3D のダンスの形の照合の元の 25 コマ（点く・消える・明るすぎのコマを含む）で、部品 18 個の明るさの差は最大 4.3 × 10⁻⁷（照合の元の小数 6 桁の丸め）だった。

### 出来事の光（`fx_lights`）

そのコマに効いている出来事の光の候補（強さ W・位置・色）を集め、`assign(glows, count, floor)` が強い順（同じ強さなら並びの順）に `count` 個の点の光へ割り当てる（`floor` 以下の候補は割り当てず、その光は消す）。出来事が舞台と踊り手を照らす光。

- 影つきの点の光は、数を増やすと EEVEE の影の領域（shadow pool）があふれ、影が付いたり消えたりする。数を決めて、強い物から割り当てる。
- 霧のある舞台では、点の光の `volume_factor` を小さく（0.10 ほど）しないと、霧の中に光の玉が見える。
- 光の台本の値で打つだけの光（窓の外から差す光・拍で打つ光）は、`render.show` の `lights`（光の物体の名前 → 値の名前と W）で足りる（フックで光の物体を作るだけ）。

**確かめたこと**: CI（Blender なし）では、強い順・同じ強さの順・`floor`・数より少ない候補・None の候補を確かめた。
4 作目の 3D のダンスの形の照合の元の 25 コマ（光 3 つ × 25 = 75 のうち、点いている 26）で、点いている光の強さの差は相対で最大 4.1 × 10⁻⁵（照合の元は強さを小数 4 桁に丸めてある）、位置の差は最大 1.6 × 10⁻⁶ m、色は同じ、消えている 49 も同じだった。

### Blender に置く・白黒のコマ（`fx_blender`）

形は作らず、`Quads` の四角形をメッシュにして、足し算で光る材質を付け、光と物体の属性を書く。`bpy` は引数で受け取る（Blender の外でも読める）。

- `additive_material(bpy, name, edge, core, strength, soft)`: 帯の材質。UV の u（幅の向き）で真ん中を `core`、縁を `edge` の色にし、形 × 頂点の強さ（属性 `fx`）× `strength` で光る。透明と足すので、重なると明るくなる（`surface_render_method` は BLENDED）。強さの数のノードの名前は `fx_gain`。
- `disc_material(…, power)`: 光の玉の材質（四角形の真ん中が明るく、縁へ消える）。`flat_material(bpy, name, colour)`: べた塗り（白黒のコマの黒い影など）。
- `layer(bpy, collection, name, material)`: 形を入れる物体（影を落とさない）。`write(bpy, ob, arrays)`: 四角形を、その物体のメッシュに毎コマ作り直す（頂点・UV・属性 `fx`。材質は引き継ぐ）。
- `point_lights(…)`・`apply_lights(lights, assigned, unit)`: 出来事の光（`fx_lights.assign` の結果）を点の光に書く。
- `drive_by_object(bpy, material, socket, attribute)`・`set_level(objects, value, attribute)`: 材質の数の口（光の強さなど）に、物体の属性（既定 `fx_level`）を掛ける。順番に点く部品（`fx_sequence`）の明るさを、1 つの材質のまま部品ごとに変えられる。
- `mono_frame(scene, value, hide, override, background, white, dark, normal)`: 白黒のコマ。`value` が +1 なら `hide` のコレクション（舞台）を描画から外し、ビューレイヤーの材質を `override`（黒いべた塗り）で上書きし、空を `white` にする（白い地に黒い影）。−1 なら外して空を `dark` に（黒い地。踊り手を白く飛ばすのは光の台本の側）。0 なら戻す。

フックの形（`render.show` の `hooks`。作品の値は、この台本の中か、台本が読む表に書く）:

```python
import fx_blender, fx_bundle, fx_events, fx_lights, fx_strips   # render.show が blender_adapter を sys.path に足してある

def build(ctx):
    bpy = ctx.bpy
    fx = bpy.data.collections.new("fx")
    ctx.scene.collection.children.link(fx)
    core = fx_blender.layer(bpy, fx, "fx_core", fx_blender.additive_material(bpy, "fx_core", EDGE, CORE, STRENGTH))
    ctx.state.update(table=fx_events.Table(TABLE, PATH), core=core, lights=fx_blender.point_lights(bpy, fx, "fx_light_", 3, 0.25, 0.3, 0.1))

def frame(ctx):
    s = ctx.state
    quads = fx_strips.Quads(ctx.camera["position"])
    glows = [fx_bundle.draw(s["table"], e, ctx.f, ctx.camera, quads, None, 1.0, ctx.values) for e in s["table"].events if e["kind"] == "bundle"]
    fx_blender.write(ctx.bpy, s["core"], quads.arrays())
    fx_blender.apply_lights(s["lights"], fx_lights.assign(glows, 3, 0.05), ctx.unit)
```

**4 作目の 3D のダンスで分かったこと**:
- 出来事は表（データ）にして、形を時刻と種だけで決める。版ごとの差し替え・変えた所だけの描き直し・区切った描画が、どれも同じ絵になる（描き直したコマの差は最大 1/255）。
- 発光は、にじみ（Bloom）が広がるので、強さ 10 までで足りた（最初は 60 で、画面が白く飛んだ）。
- 白い地のコマは、光の値を 0 にしても、白い空（world）が踊り手を照らして黒くならない。材質の上書きで黒いべた塗りにする。白い地では、にじみと光の玉（足し算の物）を作らない（黒い四角になる）。
- 白黒のコマは、舞台が 1 つのコレクションにまとまっていることを使う（コレクションごと描画から外す）。
- 頭の上を通る弧は「輪（光背）」に見える。
- 2D の重ねで足りる場合: 空の明滅と、画面の上の折れ線は、2D の側（`fx.sky.lightning`）にある。3D に置くのは、物の後ろに隠したい・床に映したい・舞台と踊り手を照らしたい・カメラが動いても場所に付けたいとき。

#### 作品の形との照合

4 作目の 3D のダンスの台本（作品の形のまま書いた物）で作った形を数にした照合の元（本番と同じ出来事の表・カメラ・光の値で、25 コマ。物体ごとの四角形の頂点の位置・UV・強さ、光の位置・強さ・色、順番に点く部品の明るさ）と、この節の型を組んで同じ出来事を作った照合の台本（リポジトリの外）で比べた（Blender なし）。作品の出来事の種類は、束・面の枝分かれ・粒・3 段・順番に点く部品に書き直し、台本の既定の値は照合の台本の中に書いた。

- 25 コマすべてで、6 つの物体の四角形の数が同じだった（合わせて 13,567）。頂点の位置の差は最大 2.2 × 10⁻⁶ m、強さの差は最大 5.3 × 10⁻⁷、UV は同じ。光は、点いている 26 の強さの差が相対で最大 4.1 × 10⁻⁵、位置の差が最大 1.6 × 10⁻⁶ m、消えている 49 も同じ。部品 18 個の明るさの差は最大 4.3 × 10⁻⁷。
- 許しは 1 × 10⁻⁵ にした（台本は単精度の mathutils で計算し、照合の元は小数 6 桁（光の強さは 4 桁）に丸めてある。位置の差 2.2 × 10⁻⁶ m は、単精度の丸め（10 m の所で約 10⁻⁶ m）と 6 桁の丸めの和の大きさ）。乱数は台本と同じ鍵・同じ順に引くので、どの型も、どのコマも同じ形になった。
- 白黒のコマにだけ出す線: 照合の元の 2 回目の白い地のコマには、線が出ていない（台本の表の秒が、コマの秒を小数 3 桁に丸めた値で、コマの秒より 0.3 ミリ秒後にあったため。1 回目は丸めが下向きで、両方のコマに出ている）。照合の台本で同じ秒を書くと、この型でも同じく出ない（照合の元と同じ数）。意図したコマを `frame` で書くと、そのコマに線が出る（線の帯の四角形が 53 → 365。次の黒い地のコマと同じ数）。
- 1 コマの形を作る時間は、多いコマ（四角形 2,658）で約 0.18 秒（numpy。描く時間に比べて小さい）。

**確かめたこと**:
- CI（Blender なし）では、代役の `bpy` で、四角形からメッシュへの頂点・面・UV・属性の渡し方と材質を引き継ぐこと、白黒のコマ（舞台を外す・材質の上書き・空の色と強さ・戻す）、光の書き方（単位）、物体の属性の書き方を確かめた。
- 2026-11-23 に Windows の Blender 5.2.4（`--factory-startup`）で、合成の場面（灰色の壁の舞台のコレクション・踊り手の代わりの箱・発光の板 3 枚・束・面の枝分かれ・粒・3 段）を 320 × 240 で描いて確かめた（実測）。
  - メッシュ: 5 つのコマで、6 つの物体の頂点の数が四角形 × 4、面の数が四角形の数、頂点の位置の差は最大 1.2 × 10⁻⁷ m（単精度）、属性 `fx` の差は最大 5.6 × 10⁻⁸、UV は同じ。材質はどれも BLENDED。四角形の無いコマは空のメッシュ。
  - 物体の属性が材質に届く: 1 つの材質の板 3 枚に `fx_level` 0・0.5・1 を書くと、描いた明るさは 0.000・0.666・0.907（Standard の色の変換。発光 0.8 の 0・0.4・0.8 に当たる）。別のコマで 1・0.25・0 にすると 0.907・0.485・0.001。
  - 白黒のコマ: +1 で舞台のコレクションが描画から外れ、材質の上書きで箱が黒く（0.000）、壁のあった所が白い地（1.000）になった。−1 で黒い地（0.000）に、箱は白いまま（0.907）。0 で戻った（壁 0.584）。白い地のコマでは、にじみと光の玉の四角形が 0。
  - 光: 出来事の光の候補が、強い順に 2 つの点の光へ入った（束と 3 段の光。束が伸びる間は 300 W × 0.45 = 135 W）。

### 出来事の時刻の候補を拾う（`plan.motion_marks`）

`plan.motion_marks`（path → marks）は、骨の通り道（`model3d-bone-path/0.1`）と曲の打音の秒から、出来事を置く時刻の候補を拾う。拾うだけで、どれを出来事にするかは決めない（`check.motion_amount` の仲間の計算だけ）。出すコマの番号は、出来事の表に `frame` でそのまま書ける（秒を丸めて書かない）。

**パラメーター**（どれか 1 つ以上）:
- `landing`: 足の着地（`{"points", "drop", "fall", "still", "window", "gap"}`。全部書く）。点の高さが、前の `window` 標本のいちばん高い所から `drop` m 以上下がっていて、1 つ前の標本の速さが `fall` m/s より速く下がり、1 つ後の標本の速さの大きさが `still` m/s より小さい標本。前の着地から `gap` 秒より近いものは数えない（点ごと）。
- `extremes`: 山と谷（`[{"name", "point", "axis", "kind", "prominence", "gap"}, …]`）。点の位置を `axis` の向きに写した値が、前後 `gap` 秒の中でいちばん大きい（`kind` max）か小さい（min）標本で、その中の反対の端との差が `prominence` m 以上のもの（しゃがみの底は腰の z の min、指先がいちばん前は正面の向きの max）。
- `hits`: 打音の秒の並び → その時刻に届く最初の動きのコマ（コマの頭の直後 0.05 コマ以内の秒は、丸めた秒とみなしてそのコマ）。
- `dance`: 通り道の動きのコマ f の踊りの時刻 (f − `frame0`) ÷ `fps`（既定 1・30）。

**出力**: `marks`（`model3d-motion-marks/0.1`）: `landings`（点・コマ・秒）、`extremes`（名前 → コマ・秒・値）、`hits`（秒・コマ）、`counts`。

**4 作目の 3D のダンスで分かったこと**: 出来事の時刻と場所を、踊りの骨の数（指先・足の着地・しゃがみの底・拳）と曲の打音から決めると、「体が起こしている」ように見える。光の台本だけで鳴らす出来事は、飾りに見えた。着地のしきい（何 cm 下りて止まるか）は、その踊りで目で確かめた値で、踊りごとに測り直す。

**確かめたこと**:
- CI（Blender なし）では、着地（2 つの足・下りて止まる標本）、山と谷（向きに写した値・差のしきい）、打音の最初のコマ（丸めた秒）、悪い設定を確かめた。
- 4 作目の 3D のダンスの骨の通り道（全コマ）で、作品の段取りの台本の着地の検出と同じしきいを渡すと、検出した着地は台本と同じ数で、すべて同じ標本だった。打音などの秒 12 個のうち 11 個は、台本の「届く最初のコマ」と同じ。残りの 1 個は、コマの秒を小数 3 桁に丸めて書いた秒で、台本は 1 コマ後（そのコマに出来事が出なかった）、この操作は意図したコマを出した。

### 画面のどこに映るか（`check.on_screen`）

`check.on_screen`（cameras [, path] → report）は、世界の点が、カメラの控え（`model3d-cameras/0.1`）のコマの画面のどこに映るかを出す。写し方は `plan.shots` の `shot_plan.project` と同じ。

- `points`: `{名前: [x, y, z]}` か `{名前: {"point": 骨の通り道の点, "offset": [x, y, z]}}`（骨の点は、そのコマの動きのコマ f のいちばん近い標本。入力の `path` が要る）。`frames`: コマ T の並び（省くと全部）。`margin`: 画面の幅・高さに対する割合（既定 0）。
- 出す数: コマ・点ごとの x・y（0〜1。0 が左・上）と奥行き（m。負はカメラの後ろ）と、`margin` の内側か。内側に無い点とカメラの後ろの点を数え、全部が内側なら合格（`report_only` で数を出すだけ、`strict` で止める）。
- **縦長の画面は横が狭い**（レンズ 28 mm で、奥行き 8 m の所が ±2.9 m、4.3 m なら ±1.4 m）。出来事を踊り手の真横に置くと画面の外になる。斜め後ろ（奥）へ置き、置く前にこの検査で数を出す。

**確かめたこと**:
- CI（Blender なし）では、画面の真ん中・横の外・カメラの後ろ・骨の点と offset・`margin`、工程の受け渡し（外に出る点があると要確認）、悪い設定と無いコマを確かめた。
- 4 作目の 3D のダンスのカメラの控えで、作品の台本の画面の位置の確かめ（同じ計算を自分で書いた物）と、8 コマ × 5 点（舞台の奥・横・カメラの後ろに来る点を含む）を比べた。画面の中か外かは 40 とも同じで、位置の差は台本が出す桁（x・y は小数 2 桁、奥行きは 1 桁）の丸めの内（最大 0.005）だった。

## 段取りを組む

`plan.stage` は、段取り表（`model3d-stage-plan/0.1`）と光のキー（`model3d-light-keys/0.1`）と骨の通り道（`model3d-bone-path/0.1`）から、`render.stage` のトラックを作る。リポジトリ外の試作で 32 秒の本番のカメラと光を決めた計算を、曲によらない形に移した。曲ごとの時刻と値は、段取り表としてリポジトリの外のレシピに置く。光のキーは、曲の音から `plan.light_keys` で作る。

**流れ**（レシピの工程）:
1. `plan.light_keys`（audio → keys）: 曲の音から、コマごとの帯の強さと拍の格子を書く（下の「光のキー」）。
2. `motion.bone_path`（blend [, track] → path）: 動きのコマごとに、骨の付け根の位置を m で測る。
   - 骨は `bones`（既定は `Hips` と `Head`）に `bone_prefix`（既定 `mixamorig:`）を前に付けた名前。アーマチュアは `armature`（省くと最初のアーマチュア。`render.stage` と同じ）。
   - `track`（描くコマの並び）があればその動きのコマ（小数のコマも。前後のコマの間の時刻で測る）を、無ければ `frame_range` か .blend のシーンの全コマを測る。
   - 骨だけを動かして測る（メッシュの変形は止める）。.blend は保存しない。
   - `points` を書くと、骨から決めた点（顔・胸・手の先など）も測る（下の[骨から決めた点](#骨から決めた点motionbone_path-の-points)）。
3. `plan.stage`（plan, keys, bones [, cut] → track [, report]）: コマごとにカメラ・舞台・光の値を決める。
   - `cut`（T と f だけのトラック）があればその並び、無ければ骨の通り道のコマを T = 1 から順に。
   - コマ T の時刻は (T - 1) / fps 秒（fps は光のキー）。光のキーより後のコマは最後の値を使い、その数を報告する。
4. `render.stage` でそのトラックを描く。

### 骨から決めた点（`motion.bone_path` の `points`）

カメラが狙う点や、手の軌跡のような効果のもとは、骨の付け根そのものではないことが多い（顔は頭の骨の先の少し向こうで少し前、胸は肩と腰の間）。`points` に名前と項の並びを書くと、`motion.bone_path` がコマごとにその点も測り、通り道の `points` に書く（`bones` と同じ形。名前 → 位置の列）。リポジトリ外の 2 作目の VRM のダンスの演出版で、カメラが追う顔・胸・腰と、炎の帯のもとにした両手の先の決め方を移した。

```json
"points": {
  "face": [{"bone": "head", "at": 1.2, "offset": [0, 0, -0.07]}],
  "shoulders": [{"bone": "upper_arm.L"}, {"bone": "upper_arm.R"}],
  "hips": [{"bone": "upper_leg.L"}, {"bone": "upper_leg.R"}],
  "chest": [{"point": "shoulders", "weight": 0.7}, {"point": "hips", "weight": 0.3}],
  "hand_l": [{"bone": "hand.L", "at": 1}]
}
```

- 点は、項の重みつきの平均（Σ `weight` × 項の値 ÷ Σ `weight`。`weight` の既定は 1、0 より大きい数）。
- 骨の項 `{"bone", "at", "offset", "weight"}`: 骨（`bone_prefix` の後ろの名前）の付け根から先へ `at` の割合の所（既定 0 = 付け根、1 = 先。1 より大きければ先の向こう）を、骨の x・y・z 軸（その骨の向き。長さ 1 にする）に沿って `offset`（m。既定 [0, 0, 0]）ずらした点。ちょうど 0 と 1 は付け根と先の値そのもの。
- 点の項 `{"point", "weight"}`: 別の点。書く順は自由で、参照される点を先に測る。輪になった参照・知らない点・知らない鍵は、Blender を起動する前に止める。
- `points` を書くと、`bones` の既定は空になる（付け根も測るなら `bones` も書く）。`bones` と `points` のどちらかに 1 つ以上の列が要る。
- 計算はコマごとに Blender の単精度のベクトルで、試作の台本と同じ順に行う（上の例の顔・胸・腰・手の先は、試作の式と同じ丸めになる）。長さは m で、シーンの `scale_length` で直す。

**確かめたこと**:
- CI（Blender なし）では、項の値（付け根・先・割合・軸に沿ったずらし・長さの単位）、重みつきの平均と点の参照、試作の (左 + 右) ÷ 2 と 肩 × 0.7 + 腰 × 0.3 と同じ丸めになること、設定の確かめ（項の形・知らない鍵・知らない点・輪・offset と重みと割合の値）、`points` があるときの `bones` の既定、契約、工程の受け渡しと、測った通り道に点が無いときに止めること（代役の Blender）を確かめた。
- 2026-11-16 に Windows の Blender 5.2.4 で、リポジトリ外の試作（2 作目の VRM のダンスの演出版）の台本の写しと比べた（実測）。踊りの .blend の、試作と同じ標本の時刻（1/60 秒おきの 1,746 コマ。動きのコマは小数）で、上の例の 5 点（顔・胸・腰・両手の先）を測った。
  - 5 点とも、1,746 コマすべてで試作の台本の値とビットまで同じだった（約 4 秒）。
  - 試作の台本の写しの値を試作と同じ式でならすと、試作が控えたならした通り道と、手の先はビットまで、顔・胸・腰は 1.4e-15 m 以内で同じだった（ならしの畳み込みの丸め。numpy の版の違いと見ている）。

### 骨の軸の位置を測る（`motion.bone_offset`）

`motion.bone_offset`（blend → offsets）は、決めた姿勢（`frame`）で、メッシュの決まった所を、骨の付け根から先へ `at` の割合の所から見た骨の軸（x・y・z。長さ 1）の位置（m）に直す（Blender の中で。変形した後のメッシュ）。出す `points` は、[骨から決めた点](#骨から決めた点motionbone_path-の-points) の `points` にそのまま書ける（頭の飾りの先・目の位置のように、骨の付け根でも先でもない点を、骨に付けて動かす）。

- `targets`: `{名前: {"bone", "at"（既定 0）, "object", "pick", "box"・"index"・"near"}}`。`pick` は `highest`・`lowest`（`box` の中でいちばん高い・低い頂点）、`nearest`（`near` にいちばん近い頂点）、`vertex`（番号 `index`）。`box` は世界の座標（m）の `[[下], [上]]`。
- `bone_prefix`（既定 `mixamorig:`）・`armature`（省くと最初のアーマチュア）・`blender`・`timeout`。
- 出力（`model3d-bone-offset/0.1`）: `points`（名前 → `[{"bone", "at", "offset"}]`）と `measured`（選んだ頂点の番号と世界の座標）。
- 骨の付け根と先と軸は `motion.bone_path` と同じ取り方（世界の行列 × 姿勢の行列）。軸を長さ 1 にしてから解くので、`motion.bone_path` で測り直すと、選んだ頂点の所に戻る（姿勢の行列にわずかな伸び縮みがあっても）。

**確かめたこと**:
- CI（Blender なし）では、骨の項（`bone_term`）の逆になっていること（割合 0・0.4・1、長さの単位、長さ 1 でない軸）、悪い設定、工程の受け渡し（代役の Blender）と、点が足りないときに止めることを確かめた。
- 2026-11-23 に Windows の Blender 5.2.4 で、4 作目の 3D のダンスの踊り手の .blend（作品の台本で頭の上の 2 点を測った物と、SHA-256 が同じ写し）で、台本と同じ選び方（1 コマ目、頭の骨の付け根の左右の帯の中でいちばん高い頂点）を `box` で書いて測った（実測。約 2.6 秒）。
  - 左右とも、台本と同じ頂点を選んだ（世界の座標の差 0）。
  - offset は台本の値と最大 8.5 × 10⁻⁶ m 違った。この .blend の頭の骨の姿勢の行列は、軸の長さが 1 から最大 3.7 × 10⁻⁵ ずれていて、台本は行列の逆（ずれを含む）で、この操作は長さ 1 の軸で解くため。この操作の offset を `motion.bone_path` の決め方（長さ 1 の軸）で足し戻すと、選んだ頂点との差は 1.4 × 10⁻¹⁷ m、台本の offset では 8.5 × 10⁻⁶ m だった。

### 区間ごとの動きの量（`check.motion_amount`）

体の動きが少ない区間は、光もカットも静かにすると、作品の弱い所になる。`check.motion_amount` は、骨の通り道から、区間ごとの動きの量を数で出す。リポジトリ外の 3 作目の VRM のダンスで、前半の見栄えが弱いと言われたあと、前半と後半の動きの量を比べた使い捨ての計算を部品にした（前半は後半より腰も手も動きが少なく、止めの続く振りだった）。

**入力**: `path`（`model3d-bone-path/0.1`。`motion.bone_path` で測る）。点の名前は `points` か `bones` の列の名前で、既定は [骨から決めた点](#骨から決めた点motionbone_path-の-points) の書き方の例と同じ名前（`hips`・`hand_l`・`hand_r`・`foot_l`・`foot_r`・`head_top`・`shoulder_l`・`shoulder_r`）。例: 手と足は骨の先（`"at": 1`）、頭の先は頭の骨の先、肩は上腕の骨の付け根。

**パラメーター**:
- `sections`（要る）: `[{"name": 名前, "from": 時刻, "until": 時刻}, …]`。時刻は秒の数・印（`marks`）の名前・拍 `{"beat": k}`（`grid` が要る）。
- `grid`（任意）: `{"period": 1 拍の秒, "k0_time": 拍 0 の秒}`（`mv audio grid` の `period_s`・`phase_s` から作れる）。あれば止めの拍も数える。
- `dance`: 通り道の動きのコマ f の踊りの時刻 (f − `frame0`) ÷ `fps`（既定 1・30）。
- `speeds`: `{名前: [点の名前, …]}`（既定は `hips`・`hands`（両手）・`feet`（両足））。`rise`: 上下の幅を測る点（既定 `head_top`）。`facing`: `[左の点, 右の点]`（既定は両肩）。
- `holds`: `points`（止めを見る点。既定は `speeds` の `hands`）、`speed`（m/s。既定 0.35）、`min_beats`（既定 2）。

**測り方**:
- 速さ: 隣り合う標本の間の移動の長さ ÷ 時刻の差を、間の真ん中の時刻が区間に入るものだけ平均する。名前を並べた組は、点ごとの平均をさらに平均する（両手なら左右の平均）。
- 上下の幅: 時刻が区間に入る標本の、点の高さのいちばん高い − いちばん低い（m）。
- 体の向き: 左右の点を結ぶ水平の向きを、上から見て反時計回りに 90° 回した向き（0 で −Y の正面、上から見て反時計回りが正。度）。通り道の全体でつなぐ（±180° をまたいでも続ける）。区間の中のいちばん大きい − いちばん小さい（幅）と、最後 − 最初（回った量）。
- 止めの拍: 拍 k の窓 [拍 k, 拍 k + 1) の止めの点の速さの平均が `holds.speed` より小さい拍を「止まっている拍」とし、`min_beats` 拍以上続いた所を止めとして数える（区間に頭が入る拍）。
- 区間の時刻は [from, until)。区間の境目のすぐ後に大きく動く振り（振り返りなど）は、境目のコマ 1 つで向きの幅が変わる。

**出力**: `report`（区間ごとの標本の数・速さ・上下の幅・向きの幅と回った量・止め（止まっている拍の数・止めの並び（頭の拍・拍の数・時刻）・いちばん長い止め・止めの拍の合計）、通り道の全体の同じ値、拍ごとの止めの点の速さの表）。数を出すだけで、合否（どの区間が弱いか）は決めない（`pass` は null。`report_only` で要確認にしない）。

**3 作目の VRM のダンスで分かったこと**: 動きの少ない区間ほど、カメラと光の側で動きを作る（1〜2 拍で向きと大きさを変える切り返し、寄り、ピントの移動、幕に大きな影を映すなどの舞台の出来事）。前半を作り直す前に区間ごとの動きの量を測っていれば、最初の版の割り振りで前半を静かに組まずに済んだ。

**確かめたこと**:
- CI（Blender なし）では、速さ（真ん中の時刻で区間に入れること・組の平均）、上下の幅、体の向き（正面が 0・反時計回りが正・1 回転半をつなぐこと）、止めの拍（拍の窓・しきい値・続いた拍の数）、区間の秒と拍、点が無いとき・標本の無い区間・悪いパラメーター、工程の受け渡し（数を出すだけで要確認、`report_only`）を確かめた。
- 2026-11-18 に、リポジトリ外の 3 作目の VRM のダンスの作業メモの数（前半と後半の 2 区間。使い捨ての計算）と、同じ骨の通り道（凍結の控えと SHA-256 が同じ写し）で比べた（実測。点は腰の骨の付け根、手と足と頭の骨の先、上腕の骨の付け根）。その作品の動きの量・区間の時刻・止めの拍は、ここには書かない（リポジトリ外の照合の結果に置いた）。
  - この部品の区間の取り方では、速さ（腰・両手・両足）はメモと 0.01 m/s 以内、頭の上下の幅は 1 cm 以内で、体の向きの幅は前半が 4°、後半が 26° 違った。
  - メモの計算は、区間の境目のコマを 1 つ前で切っていた。その切り方で測ると、速さ・頭の上下の幅・前半の向きの幅は、メモの桁ですべて同じになった。後半の向きの幅は、試した測り方（両肩・両脚の付け根・両目の向き、つなぐ・つながない、区間の前後）のどれでもメモの値を出せなかった（いちばん近くて 17.5° 違う）。区間の境目のすぐ後から振り返りが始まるので、境目のコマ 1 つで向きの幅が 4〜9° 動く。
  - 止め（既定の 0.35 m/s・2 拍）: 前半に 2 か所・後半に 1 か所が出た。前半の 2 か所は、メモにある前半の止め 5 つ（振りで書いてあり、拍の番号は無い）のうちの 2 つに当たる。ほかの 3 つは、手が小さく動き続けていて（その拍の手の速さは、しきい値の 1.3〜2.3 倍）、この速さでは拾えない。しきい値は仮の値。

### 踊り手を中心にしたカメラ（`plan.orbit`）

`plan.orbit` は、踊り手のまわりの極座標で書いたカメラのキーから、`render.passes` が描くコマごとのカメラを作る。リポジトリ外の 2 作目の VRM のダンスの演出版（3D でしか撮れないカメラの版）で、クレーン・床すれすれ・顔の回り込み・真上からの回転・背中へ回る一周・時間停止の回り込みを決めた計画と計算を移した。`plan.stage` の方位・距離・高さのカメラに、ロールと仰角の向き、区間の中の複数のキー、狙う点と別の回る中心、手持ちの揺れ、踊りの時刻の止めを足したもの。時刻の読み方（秒・印・拍）・区間（`until`）・ならし・寄りと揺れ（`whip`・`push`・`shake`・`beat_push`）は `plan.stage` の部品をそのまま使う。

**流れ**（レシピの工程）:
1. `plan.retime`（→ track）: 点を測る等間隔の標本の並び（例: 24 fps の踊りを 1/60 秒おきに。`fps` 60・`motion_fps` 24）。
2. `motion.bone_path`（blend, track → path）: その並びで、カメラの狙う点を測る（`points`。[骨から決めた点](#骨から決めた点motionbone_path-の-points)）。
3. `plan.orbit`（plan, bones → cameras, track [, report]）: コマごとのカメラとコマの並び。
4. `render.passes`（blend, track, cameras）: そのカメラで要素を描く（[要素別に描く](#要素別に描く)）。

**段取り表**（`model3d-orbit-plan/0.1`。省いた部分は既定の値。既定は試作の値）:

```json
{
  "schema_version": "model3d-orbit-plan/0.1",
  "fps": 30, "frames": 300, "time_offset": 0.5,
  "dance": {"frame0": 1, "fps": 24, "holds": [{"from": 9.6, "at": 9.7}]},
  "centre": "hips",
  "kicks": [{"at": 4.0, "deg": 0.5}],
  "shots": [
    {"name": "顔から引く", "until": 4.0, "handheld": 0.04, "keys": [
      {"az": 20, "dist": 1.2, "height": {"above": 0.05}, "aim": "face", "fov": 32, "roll": -2, "ease": "out"},
      {"az": -10, "dist": 2.4, "height": 1.2, "aim": "chest", "dz": 0.1, "fov": 36, "roll": 1}]},
    {"name": "真上から", "until": 10.5, "keys": [
      {"az": 0, "dist": 0.3, "height": 4.0, "aim": "hips", "fov": 42, "roll": -30},
      {"az": 0, "dist": 0.3, "height": 3.6, "aim": "hips", "fov": 42, "roll": 40}]}
  ]
}
```

- **時刻**: コマ T（1 から `frames` まで）の時刻は `time_offset` + (T − 1) ÷ `fps` 秒。段取り表の時刻（`until`・`kicks` の `at`・`holds` の `from`・`until`）はどれもこの秒で、`plan.stage` と同じく、秒の数・印（`marks`）・拍 `{"beat": k}`（`grid` の `k0_time` + `period` × k）で書ける。境目は 1e-6 秒前のコマから次の側（`plan.stage` と同じ）。
- **区間**（`shots`）: 頭は前の区間の `until`（最初の区間は T = 1 の時刻）。最後の区間だけ `until` を省ける（キーが 1 つのときだけ）。
  - キー（`keys`。1 つ以上）: `az`（方位。度。0 で中心の −Y 側の正面、正で +X 側）・`dist`（中心からの水平の距離 m）・`height`（床からの m か、狙う点からの `{"above": m}`）・`aim`（狙う点の名前）・`dz`（狙う点の高さの上乗せ m）・`fov`（縦の画角。度）・`roll`（傾き。度）・`ease`（次のキーへのつなぎ方）。
  - キーの位置 `u` は区間の中の割合（0〜1。最初は既定 0、最後は既定 1、間のキーは必須）。区間の u = (t − 頭) ÷ (終わり − 頭) で前後のキー a・b を選び、a の `ease` で混ぜる。`ease` は `smooth`（既定。smoothstep）・`linear`・`out`（1 − (1 − u)³）・`in`（u³）・`whip`（0.5 − 0.5 cos(π u^1.15)）。
  - `handheld`: 手持ちの揺れの振れ幅（度。既定 0）。`effects: false` の区間では寄りと揺れを入れない（`plan.stage` と同じ）。
- **点**: `centre`（回る中心。既定 `hips`）と各キーの `aim` は、骨の通り道の `points` か `bones` の名前。通り道の動きのコマ f を踊りの時刻 (f − `dance.frame0`) ÷ `dance.fps` に直し、`smooth.sigma_s`（既定 0.292 秒）のガウス（±ceil(`smooth.reach` × σ) の標本。既定 3。端は端の値で延ばす）でならし、踊りの時刻での値は前後の標本の直線補間（範囲の外は端の標本）。標本は時刻に等間隔であること（違えば止める）。
- **踊りの時刻**: ふつうはコマの時刻。`dance.holds` の区間（`from`〜`until`。`until` を省くとその先ずっと）では `at`（踊りの時刻の秒）に止める（時間停止）。動きのコマは f = `dance.frame0`（既定 1）+ `dance.fps` × 踊りの時刻（ちょうど整数なら整数）。止めた間も、カメラは時刻どおりに動き、狙う点は止めた姿勢のもの。
- **カメラ**（コマごと）:
  - 中心 c・狙う点 a・方位 θ・距離 r・高さ h・画角・ロールを、前後のキーから混ぜる（狙う点の高さ `{"above"}` は、それぞれのキーの狙う点から）。
  - 位置 = (c.x + r sin θ + 揺れの横, c.y − r cos θ, max(h + 揺れの上下, `min_height`))。`min_height` の既定は 0.12 m。寄りと揺れ（`effects`）は `plan.stage` と同じ書き方で、距離・方位・横・上下を動かす。
  - 前 = 狙う点 − 位置（長さ 1）。上の手がかりは、真上（真下）を向くほど、(0, 0, 1) から水平の前 (−sin θ, cos θ, 0) へ寄せる（割合 smoothstep((|前.z| − `up.from`) ÷ `up.span`)。既定 0.6・0.35）。真上から見下ろしても上の向きが回らず、ロールで画を回せる。
  - 右 = 前 × 手がかり、上 = 右 × 前。行列 [右, 上, −前] に、y のまわりに −yaw、x のまわりに pitch、z のまわりに roll + キーのロールの回転を右から掛ける。
  - 手持ちの揺れ（yaw・pitch・roll の度）= `handheld` × Σ `handheld.weights`[m] × sin(2π `handheld.hz`[m] t + 位相)。位相は `handheld.seed` の乱数（既定は試作の 3、[0.9, 2.3, 5.1] Hz、重み [1, 0.6, 0.3]）。
  - 打点の揺れ（`kicks`。`handheld` が 0 より大きい区間でだけ）: `at` から `kick.length` 秒（既定 0.6）、`deg` × exp(−経過 ÷ `kick.tau`) × sin(2π `kick.hz` 経過 + `kick.phase`) × `kick.gain` を yaw・pitch・roll に足す（既定は試作の 0.13 秒、[7.5, 8.3, 6.1] Hz、位相 [0, 1, 2]、[0.6, 1, 0.8]）。
  - 焦点距離 = 画面の高さ ÷ 2 ÷ tan(画角 ÷ 2)（`camera.resolution`。既定 1080×1920）。主点は画面の中央。
- **書いたときだけ効く指定**（リポジトリ外の 3 作目の VRM のダンスの演出版のカメラ（台本の計算）を書けるように足した。省けば上と同じ値）:
  - 仰角 `el`（キー。度、正でカメラが上）: 中心のまわりの円柱の座標でなく、狙う点のまわりの球の座標で置く。`dist` は狙う点からのまっすぐの距離で、位置 = 狙う点 + `dist` × (sin θ cos `el`, −cos θ cos `el`, sin `el`)（+ 揺れの横・上下。高さの下限は同じ）。寄りと揺れの距離（`push`・`beat_push`・`whip`）も、このまっすぐの距離に掛かる。`height` の代わりに書き、区間のキーはどれも `el` か、どれも `height` にそろえる。顔の寄りのように中心（腰）と狙う点が離れるカットで、狙う点からの距離と角度で決めたいときに使う。
  - レンズ `lens`（キー。mm）: 画角 `fov` の代わりに mm で書き、キーの間も mm でつなぐ（焦点距離 = 画面の高さ × `lens` ÷ `camera.sensor_height`。`lens_mm` はその値そのもの）。画角でつなぐと、寄りの途中の大きさが mm でつなぐのと違う（30 → 62 mm の真ん中で、画角でつなぐと 41 mm、mm でつなぐと 46 mm）。
  - つなぎ方 `in2`・`out2`（キーの `ease`）: 2 乗（u²、1 − (1 − u)²）。`in`・`out` は 3 乗のまま。
  - 狙う点のずらし `offset`（キー。[x, y, z] m）: 世界の座標でずらす（`dz` の代わり）。
  - `follow`（キー。0〜1）: 狙う点 = 区間のコマの生の点の平均 × (1 − `follow`) + そのコマの点 × `follow`（区間の中で少しだけ追う。`plan.shots` の `follow` と同じ考え方で、平均の取り方は区間の全部のコマの、ならす前の点）。省けば、そのコマの点。
  - 区間の中だけのならし `smooth.per_shot`（true で）: 点（狙う点・回る中心・ピントの点）を、通り道の全体でなく、区間のコマの並び（そのコマの踊りの時刻の点。標本の時刻にちょうど当たれば標本そのもの）だけでならす（σ は `smooth.sigma_s` × `fps` コマ、±ceil(`smooth.reach` × σ) コマ、端は端の値で延ばす）。区間の外の動きが、カットのつなぎ目の前後のカメラに入らない。
  - 絞りとピント（区間の `fstop`、キーの `focus`）: `fstop`（f 値）のある区間のコマの `cameras` に、`fstop` と `focus_m`（カメラの位置からピントの点までのまっすぐの距離 m）を書く。ピントの点はキーの `focus`（点の名前）を前後のキーで混ぜたもので、キーで点を変えるとピントが手前から奥へ移る（広角の手前の手から顔へ、など）。どちらのキーにも `focus` が無ければ狙う点。ピントの点は `smooth.focus_sigma_s`（省くと `smooth.sigma_s`）でならし、`follow`・`offset` は掛けない。絞りの無い区間のコマには書かない（被写界深度なし）。`render.passes`（簡易描画）はこの 2 つを使わない。
  - 揺れの形 `handheld.form: "orbit"`: 手持ちの揺れを、カメラの向きを回すのでなく、方位・仰角・傾きに足す: 方位 += a (sin(5.1 t + s) + 0.6 sin(11.3 t + 2s))、仰角 += 0.7 a (sin(6.7 t + 1.3 s) + 0.5 sin(13.1 t + s))、傾き += 0.8 a sin(4.3 t + 0.7 s)（a は区間の `handheld`、s は区間の `seed`。省くと区間の番号（0 から））。カメラは狙う点を向いたまま揺れる。仰角 `el` のキーの区間で使う（`height` の区間で振れ幅があれば止める）。`kicks` は効かない。形は 3 作目の VRM のダンスの台本のもの（既定の `rotate` は今の形）。
- **出力**:
  - `cameras`: `model3d-cameras/0.1`。コマごとの行列（m）・焦点距離・主点・`lens_mm`（`camera.sensor_height`。既定 36 mm で換算）・動きのコマ f・踊り手の点（中心の点。`render.passes` が足もとの影の円をその真下に置く）・カメラからの距離。絞りのある区間のコマは `fstop`・`focus_m` も（2 つは組で、どちらかだけは契約が止める）。
  - `track`: `model3d-stage-track/0.1` の T と f だけ（止めた間は同じ f が続く）。
  - `report`（任意）: `model3d-orbit-report/0.1`。区間ごとの T の範囲、止めたコマの数、標本の数と範囲、踊りの時刻が標本の範囲の外だったコマの数（`samples.outside`）。絞りのある区間があれば `dof`（絞りのコマの数・ピントの点・ならし）、`smooth.per_shot` なら `samples.per_shot`。

**確かめたこと**:
- CI（Blender なし）では、点の読み方（秒のガウス・標本の間の補間・端・等間隔でない通り道と無い点を止めること）、キーの選び方とつなぎ方、カメラの位置と向き（正面・横・高さの下限）、ロール、真上から見下ろすときの上の向き、手持ちの揺れと打点の揺れ（試作の式と同じ順・同じ値。振れ幅 0 の区間は打点の揺れも入れない）、踊りの時刻の止め（カメラは動き続ける）、`plan.stage` の寄り、悪い段取り表、出力の契約、工程の受け渡しと再利用を確かめた。
- 2026-11-16 に、リポジトリ外の試作（2 作目の VRM のダンスの演出版。21 区間・870 コマ・打点の揺れ 8 つ・最後の打音の後の時間停止）と比べた（実測）。試作の計画を段取り表に写し（非公開）、[骨から決めた点](#骨から決めた点motionbone_path-の-points)と同じ通り道（1/60 秒おきの 1,746 標本）から作った。
  - カメラの行列: 870 コマすべてで、試作の描画の台本がカメラに入れた行列と最大 2.5e-7 の差（試作は Blender の単精度のベクトルで計算するため。単精度の丸めの範囲）。位置の差は最大 2.4e-7 m、焦点距離の相対の差は 2.2e-16、踊り手までの距離の差は 2.4e-7 m。
  - 踊り手の点を両方のカメラで写した画素の差は最大 0.001 画素。動きのコマ f は 870 コマすべてで試作とビットまで同じ（止めた 27 コマを含む）。
  - そのカメラで `render.passes` の足もとの円と顔の範囲を 12 コマ（どの区間からも。真上からと止めた間を含む）描き、試作のカメラ（控えの行列）で同じ設定で描いた画と比べた。足もとの円は 7 コマ、顔の範囲は 4 コマが画素まで同じ。ほかは縁の画素だけが違った（円は 43〜225 画素、顔は 35〜598 画素。JPEG で最大 94/255）。単精度の丸めの差で、縁の画素の 5 つの標本のうち 1 つが入れ替わり、JPEG の 8×8 の塊に広がった大きさと見ている。試作のカメラで描いた画は、試作の要素と、円が 12 コマ、顔の範囲が 11 コマ画素まで同じ（残りの 1 コマは試作の閉じ目の形のぶん）。
- 書いたときだけ効く指定は、CI で、2 乗のつなぎ方、球の座標の置き方（向き・高さの下限・寄りがまっすぐの距離に掛かること）、mm でつなぐレンズ、キーの書き方をそろえることと契約、世界の座標のずらし、区間の平均と混ぜる狙い、区間の中だけのならし（つなぎ目で混ざらないこと・ガウスの形）、絞りとピント（絞りの無い区間には書かない・ピントが移る・狙う点がピント・`fstop` だけの記録を契約が止める）、揺れの形 orbit（区間の種・省いた種・向きを回さないこと・`height` の区間で止めること）、工程の受け渡しを確かめた。省いたときに今までと同じ値になることは、出力の金型と上の CI のテストで確かめている。
- 2026-11-18 に、リポジトリ外の 3 作目の VRM のダンスの演出版のカメラ（30 区間・909 コマ。台本の写しの計算。凍結の控えと SHA-256 が同じ写し）と比べた（実測）。台本の値を、上の指定で段取り表に写し（非公開。区間のつなぎ方は 2 乗、狙う点とピントの点は骨の重みつきの平均を [骨から決めた点](#骨から決めた点motionbone_path-の-points) と同じ形で通り道の `points` に、拍の寄りは区間ごとの `beat_push`、ならしは区間の中だけ σ 4 コマ ±8・ピントは σ 2 コマ ±4、揺れの形は orbit、上の手がかりは寄せない `up.from` 1）、台本と同じ骨の通り道（1/30 秒おきの 954 コマ）から作った。
  - 位置の差は最大 1.3e-15 m（909 コマのうち 796 コマはビットまで同じ）。向き（台本の位置・狙う点・傾きから同じ式で作った行列）の差は最大 3.3e-16（799 コマはビットまで同じ）。位置がビットまで同じでない 113 コマは、どれも拍の寄りのある区間のコマ（寄りの無い区間の 366 コマは全部ビットまで同じ）。拍からの経過の計算の丸め（台本は余りを取る、`beat_push` は拍の時刻を引く）の差と見ている。
  - レンズは 909 コマすべてで同じ値。絞りは、絞りのある 194 コマすべてで同じ値で、ほかのコマには無い。ピントまでの距離の差は最大 8.9e-16 m（194 コマのうち 180 コマは同じ値）。区間の分かれ目は全部同じ。
  - 動きのコマ f は、T から最大 5.7e-14 ずれたコマがある（踊りと書き出しの fps が同じとき、f = `frame0` + `fps` × 時刻 がちょうど整数にならない丸め。今の式のまま。`render.passes` は、そのコマを前後のコマの間の 5.7e-14 の所で描く）。
  - 骨の通り道を、台本の写しの点でなく、Windows の Blender 5.2.4 の `motion.bone_path` で踊りの .blend から測っても（台本と同じ決め方の点と、重みつきの平均の点の参照。954 コマ）、点は台本の点と腰・胸・両手がビットまで同じ、顔と重みつきの点の差は最大 1.8e-7 m（単精度の丸め）。そこから作ったカメラは、台本の計算と位置の差が最大 4.5e-8 m、レンズと絞りは全コマ同じ、ピントまでの距離の差は最大 7.5e-9 m（model3d の工程だけで通した照合）。
  - 2 作目の VRM のダンスの段取り表（上の 870 コマ）と、`plan.shots` の照合のショット表（906 コマ）から作った出力（cameras・track・report・alt）は、足す前と足した後で同じ中身だった。

### 絵コンテのカメラを 3D に写す（`plan.shots`）

`plan.shots` は、2D の絵コンテのショット表（1 枚の絵の中の仮の部屋に置いたカメラと、追う点を置く画面の画素）を、3D のコマごとのカメラに写す。リポジトリ外の 2 作目の VRM のダンスで、2D の完成版の絵コンテ（2D 版のレンダラの数値）を 3D の再現版のカメラに写した台本の計算を移した。部屋の置き方は [箱の部屋](#箱の部屋)（`render.box_room`）と同じなので、その部屋を描いた .blend と組み合わせると、2D の完成版と同じ構図になる。時刻の読み方・区間・つなぎ方・手持ちの揺れと打点の揺れ・踊りの時刻の止めは `plan.orbit` と同じ。

**流れ**（レシピの工程）: `plan.retime` → `motion.bone_path`（追う点を `points` で測る。例: 顔・胸・腰）→ `plan.shots`（plan, bones → cameras, track [, alt, alt_track, report]）→ `render.passes`（blend, track, cameras）。

**ショット表**（`model3d-shot-plan/0.1`。省いた部分は既定の値。既定は試作の値）:

```json
{
  "schema_version": "model3d-shot-plan/0.1",
  "fps": 30, "frames": 600, "time_offset": 0.5,
  "room": {"focal_px": 2000, "center_px": [540, 800], "camera_height": 1.0, "origin_px": [560, 1700], "scale": 1.0},
  "dance": {"frame0": 1, "fps": 24, "holds": [{"from": 19.0, "at": 19.1}]},
  "kicks": [{"at": 4.0, "deg": 0.5}],
  "wipes": [{"at": 6.0, "seconds": 0.3}],
  "references": [{"name": "客席", "shot": "引き"}],
  "shots": [
    {"name": "寄り", "until": 3.0, "handheld": 0.06, "follow": 0.85, "keys": [
      {"move": [0, 0, 0.1], "roll": -2, "zoom": 2.5, "anchor": "face", "at": [540, 700], "ease": "out"},
      {"move": [0.1, 0, 0.2], "roll": 1, "zoom": 2.0, "anchor": "face", "at": [530, 620]}]},
    {"name": "引き", "until": 6.0, "handheld": 0.05, "fix": {"zoom": 1.1, "py": [0, -40]}, "keys": [
      {"move": [0.1, 0.3, -0.4], "yaw": -3, "pitch": 3, "zoom": 1.0, "anchor": [540, 1000], "at": [540, 1060], "ease": "out"},
      {"move": [0, 0.2, 0.2], "pitch": 2, "zoom": 1.1, "anchor": "chest", "at": [540, 1000]}]},
    {"name": "回り込み", "handheld": 0.03, "orbit": {"keys": [[6.0, 40, 2.5, 2.5], [9.0, -20, 2.5, 0.8]], "aim_z": 1.0, "fov": 48}}
  ]
}
```

- **部屋**（`room`。`render.box_room` と同じ置き方。画素は `camera.resolution` の画面の画素）: 基準のカメラが原点で、右 x・下 y・奥 z。カメラは水平に奥を向き、焦点距離 `focal_px`、主点 `center_px`（水平線の消失点）、床はカメラの `camera_height` 下（仮の単位）。踊り手の立つ所 `origin_px`（[x, 床に立つ所の y]）が世界の原点で、奥行き z_d = `focal_px` × `camera_height` ÷ (y − 主点の y)、x_d = (x − 主点の x) × z_d ÷ `focal_px`。Blender の座標（m）へは ((x − x_d) × `scale`, (z − z_d) × `scale`, (`camera_height` − y) × `scale`)。
- **時刻・区間・止め**: `plan.orbit` と同じ（T の時刻 = `time_offset` + (T − 1) ÷ `fps`、秒の数・印・拍、`until`、`dance.holds`、動きのコマ f = `dance.frame0` + `dance.fps` × 踊りの時刻）。
- **キーのショット**（`keys`）: 区間の u で前後のキー a・b を選び、a の `ease` で混ぜる（`plan.orbit` と同じ）。
  - `move`（基準のカメラからの移動 [右, 下, 奥]。仮の単位）、`yaw`（右へ +）・`pitch`（上へ +）・`roll`（度）、`zoom`（焦点距離 = `zoom` × `focal_px`。既定 1）。
  - 向き: 前 = (sin yaw cos pitch, −sin pitch, cos yaw cos pitch)、右 = (cos yaw, 0, −sin yaw)、下 = 前 × 右。右と下を roll で回し、Blender の向き（右・上・−前）の行列にする。手持ちの揺れは yaw・pitch・roll に足す。
  - `anchor`（追う点）: 骨の通り道の点の名前（`points` か `bones`）か、踊り手の立つ面（奥行き z_d）の上の、基準のカメラの画素 `[x, y]`（決まった点）。名前の点は、区間の頭から終わりまで `follow.samples`（既定 25）点の踊りの時刻で読んで平均し、そこから `follow`（区間の値か `follow.default`。既定 0.8）の割合だけ、いまの踊りの時刻の点へ寄せる（ショットの中で少しだけ追う）。点は `smooth.sigma_s`（既定 0.292 秒）でならす。
  - `at`: 追う点を置く画面の画素。追う点がちょうどここに来るように、主点（レンズのずらし）を解く（2D 版の「主点を解く」と同じ）。キーの a と b の追う点を混ぜてから解く。
- **回り込みのショット**（`orbit`）: `orbit.keys`（`[[時刻, 方位°, 距離 m, 高さ m], …]`）を単調な 3 次の補間（行き過ぎない。範囲の外は端の値）でつなぎ、点 `orbit.centre`（既定 `hips`）のまわりに置いて、高さ `orbit.aim_z`（既定 1 m）の注視点を向く。焦点距離は縦の画角 `orbit.fov`（既定 48°）から。主点は画面の中央。
- **手直し**（`fix`。ショットごと。数か `[頭, 終わり]`（ショットの中の u でまっすぐ変える））: キーのショットは `dx`・`dy`・`dz`（`move` に足す）、`dyaw`・`dpitch`（度）、`zoom`（掛ける）、`px`・`py`（`at` に足す画素）。回り込みは `dyaw`・`radius`（掛ける）・`dz`・`aim_dz`（m）・`zoom`・`px`・`py`（主点をずらす画素）。2D の完成版が、生成動画の写り方のぶん絵コンテの数値からずれているショットを合わせるために使った。
- **つなぎ**（`wipes`）: `at` を中心に `seconds` 秒の間（|t − at| < `seconds` ÷ 2）のコマは、相手のショット（`at` より前のコマは後のショット、後のコマは前のショット。前か後かは、ショットの境目と同じく `at` の 1e-6 秒前のコマから後の側）のカメラも同じ時刻で作る（出力 `alt`。柱が横切る切り替えで、両方のショットを同じ時刻に描くため）。
- **基準のカメラ**（`references`）: ショット `shot`（名前）の `at`（省くとショットの頭）の、揺れなしのカメラを、報告の `references` に名前 `name` で書く（手前の板を置く基準など）。
- **出力**:
  - `cameras`: `model3d-cameras/0.1`。コマごとの行列（m）・焦点距離・主点・`lens_mm`・ずらし・動きのコマ f・踊り手の点（`dancer`。既定 `hips`）・カメラからの距離。
  - `track`: `model3d-stage-track/0.1` の T と f だけ。
  - `alt`（任意。`wipes` が要る）: つなぎの間の相手のショットのカメラ（`model3d-cameras/0.1`。そのコマの T だけ）。
  - `alt_track`（任意。`wipes` が要る）: そのコマだけの並び（`model3d-stage-track/0.1`。`render.passes` の track に `alt` と一緒に渡して、相手の要素を描く）。
  - `report`（任意）: `model3d-shot-report/0.1`。ショットごとの T の範囲と種類、止めたコマの数、標本の数と範囲、追う点を写した画素と狙いの画素の差の最大（`anchor_error_px`。主点を解いた検算）、つなぎの相手、基準のカメラ。

**確かめたこと**:
- CI（Blender なし）では、部屋の置き方（`render.box_room` と同じ式。基準のカメラで踊り手の立つ面の画素が絵どおりに写ること）、追う点がちょうど狙いの画素に来ること（向き・ロール・決まった点と名前の点を混ぜたとき）、追う点の追い方、キーと手直しの混ぜ方、回り込み（行き過ぎない補間・注視点・画角・範囲の外）、踊りの時刻の止め、つなぎの相手のカメラと揺れなしの基準のカメラ、悪い表、出力の契約と工程の受け渡しを確かめた。
- 2026-11-17 に、リポジトリ外の試作（2 作目の VRM のダンスの 2D の再現版。20 ショット（回り込み 2 本を含む）・906 コマ・つなぎ 2 つ・最後の打音の後の止め・手直し 5 ショット）と比べた（実測）。試作の絵コンテの数値と手直しをショット表に写し（非公開）、追う点（顔・胸・腰）は試作と同じ 1/30 秒おきの 847 標本で [骨から決めた点](#骨から決めた点motionbone_path-の-points) を測った（試作は追う点を 1/30 秒おきに測ってならす。1/60 秒おきの通り道で読むと、ならしの標本がずれて腰の点が最大 6.8 mm 違った）。
  - カメラの行列: 906 コマすべてで、試作の台本がカメラに入れた行列と最大 2.0e-7 の差（試作は Blender の単精度のベクトルで計算するため）。レンズの差は最大 3.8e-6 mm、ずらしの差は最大 2.5e-6（画面の高さ 1920 画素で 0.005 画素）、踊り手の点の差は 3.0e-8 m、動きのコマ f は丸めの差だけ（5.7e-14）。追う点を写した画素と狙いの画素の差は 2.3e-13 画素。
  - つなぎの相手のカメラ: 試作が描いた 18 コマとちょうど同じコマで、行列の差は最大 2.9e-7、焦点距離の差は 1.1e-4 画素、主点の差は 2.2e-4 画素。揺れなしの基準のカメラ（手前の板の基準。5 つ）も、行列の差は最大 2.2e-7、主点の差は 9.1e-5 画素以下。
  - そのカメラで `render.passes` の足もとの円と顔の範囲を 12 コマ（どのショットからも。回り込みと止めた間を含む）描き、試作の台本の写しで表情なしに描いた要素と比べた。足もとの円は 11 コマ、顔の範囲は 5 コマが画素まで同じ。ほかは縁の画素だけが違った（36〜210 画素。JPEG で最大 66/255）。`plan.orbit` と同じく、単精度の丸めの差で縁の標本が入れ替わり、JPEG の 8×8 の塊に広がった大きさと見ている。

### 向きの下見（`plan.scout`）

動きごとに、どの向きから撮ると形がいちばん読めるかを、カメラ割りを決める前に並べて見る。`plan.scout` は、拍ごと（か時刻ごと）の姿勢を、踊り手のまわりのいくつかの方位から見るカメラとコマの並びを作る。リポジトリ外の 3 作目の VRM のダンスの演出版で、止めの多い前半の振りを拍ごとに 7 つの方位から描いて並べ、カット割りを作り直した下見の台本の置き方を移した。

**流れ**（レシピの工程）:
1. `motion.bone_path`（blend → path）: 狙う点を測る（既定は腰。`points` の点も使える）。動きのコマ（整数）を測ってあればよい。
2. `plan.scout`（bones → cameras, track [, report]）: 行（拍か時刻）× 列（方位）のコマごとのカメラとコマの並び。
3. `render.passes`（blend, track, cameras）: 小さな画面（`camera.resolution` と同じ縦横比。例 180×320）で、`shade`（白い人物とスタジオの光。形が読みやすい）か `char` を描く。
4. `mv sheet <passes の要素のフォルダ> --out 下見.jpg --fps 1 --every 1 --columns <方位の数>`: 1 行が 1 拍、1 列が 1 方位に並ぶ（セルの字はコマの番号と秒。どの拍・方位かは report の `rows` と `angles`）。

**パラメーター**（省いた値は既定。既定は 3 作目の下見の値）:
- 時刻: `beats`（[最初の拍, 最後の拍]）と `grid`（`{"period": 1 拍の秒, "k0_time": 拍 0 の秒}`。`mv audio grid` の `period_s`・`phase_s` から作れる）か、`times`（秒の並び）。
- `angles`: 方位の並び（度。0 で正面（−Y 側）、正で +X 側。既定 −90・−55・−25・0・25・55・90）。
- `el`（仰角。度、正で上。既定 −4）、`dist`（狙う点からのまっすぐの距離 m。既定 3.1）、`lens`（mm。既定 35）。
- `aim`（狙う点。骨の通り道の `points` か `bones` の名前。既定 hips）、`aim_floor`（狙う点の高さの下限 m。既定 0.75。しゃがんでも狙いが下がりすぎない。null で下限なし）、`dz`（高さの上乗せ m。既定 0.22）。
- `dance`（`frame0` 既定 1・`fps` 既定 30）: 時刻 t の動きのコマ f = `frame0` + round(`fps` × t)（姿勢は整数のコマ）。
- `camera`（`resolution` 既定 1080×1920・`sensor_height` 既定 36 mm）。

**カメラ**: 狙う点 = (点の x, 点の y, max(`aim_floor`, 点の z) + `dz`)（点はそのコマの値。ならさない。通り道にそのコマがあればその値、無ければ前後の標本のコマで直線に補間。標本のコマの間隔はそろっていなくてよく（任意のトラックで `motion.bone_path` を測った通り道も読める）、標本 1 つでもよい。通り道の外は、いちばん近い端の標本の値で、report の外の数に数える）。位置 = 狙う点 + `dist` × (sin az cos `el`, −cos az cos `el`, sin `el`)。狙う点を向き、傾けない。主点は画面の中央。

**出力**: `cameras`（`model3d-cameras/0.1`。コマ T = 1 + 行 × 列の数 + 列（どちらも 0 から）。踊り手の点は、高さを足す前の点）、`track`（T と f。同じ行は同じ f）、`report`（`model3d-scout-report/0.1`。行ごとの拍・時刻・f・T の範囲、方位、通り道の外だったコマの数）。

下見のコマの T はセルの番号で、時刻ではない。`render.passes` の、コマの時刻 (T − 1) ÷ fps で決まる設定（`expressions`・`fx`・`phantoms` の時刻、`pool.during`）は、その拍の時刻に合わないので、下見では使わない（姿勢は f で決まるので合う）。

**3 作目の VRM のダンスで分かったこと**（下見の絵から）: 差し出す腕は、正面からだと縮んで見える（カメラへ向かって伸びる）。かがんで手を回す所は、真横のほうが体の折れが読める。上げた腕と顔の横の手は、正面〜斜め前がよい。

**確かめたこと**:
- CI（Blender なし）では、行と列の並び、動きのコマ、球の座標の位置と向き（狙う点を向き、傾けない）、高さの下限、通り道の読み方（そのコマの標本そのもの・標本の間・外。標本のコマの間隔がそろっていない通り道と、標本 1 つの通り道も）、悪いパラメーター、工程の受け渡しと、出力をそのまま `render.passes` のカメラの確かめに通せることを確かめた。
- 2026-11-18 に、リポジトリ外の 3 作目の VRM のダンスの下見の台本（凍結の控えと SHA-256 が同じ写し）と比べた（実測）。前半の拍 0〜34 を 7 つの方位から見る 245 枚のカメラ（骨の通り道は、台本と同じ式で書き出された腰の骨の頭の位置）。
  - 台本の式を倍精度で写したものと、245 枚の位置と向きがビットまで同じ。動きのコマも 35 拍すべて同じ。
  - 台本は Blender の単精度のベクトルで置くので、それを float32 で写した見積もりとの差は最大 2.6e-7 m（単精度の丸め）。
  - Windows の Blender 5.2.4 で、最初の 3 拍（21 枚）を `render.passes`（`shade` と `char`、180×320）で描き、`mv sheet` で並べた。台本が描いた下見の絵（EEVEE、色の絵のまま）と、同じ拍の同じ姿勢が同じ方位の並びで写っていることを目で見て確かめた（描き方が違うので、画素では比べていない）。

### 見本のコマ（`plan.stills`）

本番を描く前に、塗りと舞台の見本を数枚だけ見る。`plan.stills`（bones → cameras, lights [, track] [, report]）は、決めた踊りのコマを 1 つずつ、決めた位置から見るカメラと、そのコマの光の値を作る。`render.show` の `cameras` と `lights` にそのまま渡せる。リポジトリ外の 4 作目の 3D のダンスで、見本のコマのカメラの控えと光のトラックを書いた台本の置き方を移した。`plan.orbit` は T から時刻を決める（コマが等間隔に並ぶ）ので、ばらばらの踊りのコマを 1 枚ずつ見る見本には使えなかった。

**パラメーター**:
- `shots`（必須。見本の並び）: 見本ごとに `name`（重ならない名前）・`f`（踊りのコマ。整数）・`offset`（足もとからのカメラの位置 [x, y, z] m）・`aim`（狙う点。骨の通り道の `points` か `bones` の名前）・`lens`（mm）は必ず書く。`dz`（狙う点の高さの上乗せ m。既定 0）、`fstop`（絞り。省くと被写界深度なし）、`focus`（ピントの点の名前。省くと狙う点。`fstop` といっしょに書く）、`lights`（そのコマだけの光の値）。
- `lights`: どの見本にも入れる光の値（名前 → 数か [R, G, B]）。`render.show` の `lights`・`ambient` が読む名前を書く。
- `base`（足もとの点。既定 hips）、`fps`（既定 30）、`camera`（`resolution` 既定 1080×1920・`sensor_height` 既定 36 mm）、`clip_m`（[近く, 遠く] m。書けば cameras に入れる）。

**カメラ**: 位置 = `base` の点の真下の床 (x, y, 0) ＋ `offset`。狙う点 = `aim` の点 ＋ (0, 0, `dz`)。点はそのコマの値（ならさない。`plan.scout` と同じ読み方で、通り道の外は端の標本の値にして report の `outside` に数える）。狙う点を向き、傾けない（`plan.scout`・`plan.orbit` と同じ式）。真上か真下を向く見本と、狙う点と同じ位置の見本は止める。焦点距離の画素 = 画面の高さ × `lens` ÷ `sensor_height`、主点は画面の中央。絞りのある見本は `fstop` と `focus_m`（カメラの位置からピントの点までのまっすぐの距離）。

**並び**: 見本を `f` の小さい順に並べ、T = 1 から振る（`render.show` は、T の順に動きのコマが増える並びを描く）。同じ `f` の見本が 2 つあれば止める（同じ姿勢を 2 つの向きから見るときは、隣のコマを使う）。

**出力**: `cameras`（`model3d-cameras/0.1`。踊り手の点は `base` の点）、`lights`（`model3d-light-track/0.1`。`flash` 0・`flash_color` [1, 1, 1]・`fade` 1 に `lights` を重ね、見本の `lights` を重ねる）、`track`（T と f）、`report`（`model3d-stills-report/0.1`。T・f・名前・狙う点までの距離・絞りの有無、使った点、通り道の外の数、光の値の名前）。見本の T は時刻ではないので、T から時刻を決める設定（`render.show` の `expressions` など）は、見本では踊りの時刻に合わない。

**確かめたこと**:
- CI（Blender なし）では、並び（f の順）、足もとからの位置と向きと焦点距離、ピントの点、`plan.orbit` で同じ踊りのコマと位置（腰からの方位・距離・高さ）を書いた 1 コマと行列が合うこと（差 1e-12 より小さい。2 通り）、光の値の重ね方、通り道の外の数、悪いパラメーター 19 通り、工程の受け渡しと再利用を確かめた。
- 2026-11-23 に、4 作目の 3D のダンスの見本の台本（凍結の控えと SHA-256 が同じ写し）と、同じ見本の表と骨の通り道で比べた（実測）: 5 枚のカメラの行列・焦点距離・主点・レンズ・踊り手の点・絞りとピントの距離と、光の値が、台本を今回回した物とも、描いたときの控えとも、ビットまで同じだった。

### 動きと違う fps で描く（小数のコマ）

動きの .blend が 24 fps で、30 fps の動画にしたいときは、`plan.retime` でコマの並びを作る。リポジトリ外の 2 作目の VRM のダンスで、24 fps の踊りを 30 fps で描いた計算を移した（それまでの道具では、動きのコマは整数で、同じコマを繰り返すか飛ばすしかなかった）。

- `plan.retime`（→ track）: パラメーター `fps`（書き出す fps）・`motion_fps`（動きの fps）・`start`（T = 1 の動きのコマ。既定 1）・`frames`（書き出すコマの数）。
  - コマ T（1 から `frames` まで）の動きのコマは f = `start` + (T − 1) × `motion_fps` ÷ `fps`。ちょうど整数なら整数、そうでなければ小数で書く。
  - 出力は T と f だけのトラック（`model3d-stage-track/0.1`。fps は `fps`）。
- 小数の f は、`render.stage`・`render.passes`・`motion.bone_path` が、前のコマ（f を切り捨てた整数）と、そこからの割合（0 以上 1 未満）で Blender の `frame_set(コマ, subframe=割合)` に渡す。骨の動き・揺れもののキー・頂点の動きのファイル（.pc2）は、その時刻の値になる。
- 同じ並びで `motion.bone_path` を測り、`plan.stage` の cut に渡すと、カメラと光を 30 fps で決めたトラックになる（骨の通り道は、cut と同じ小数のコマで測ってあること）。
- 描いたコマの一覧（`model3d-frames/0.1`・`model3d-passes/0.1`）とカメラの控え（`model3d-cameras/0.1`）の f も小数になる。

**確かめたこと**:
- CI（Blender なし）では、小数の f の読み方（ちょうど整数なら整数のまま、丸めの残りは小数のまま）、前のコマと割合への分け方と `frame_set` への渡し方、トラック・`plan.retime` の並び（`start` と fps の比、同じ fps なら 1 コマずつ）、工程の受け渡しと悪いパラメーター、`plan.stage` が小数の cut と骨の通り道を使うこと（整数だけの通り道では止まること）、`motion.bone_path` が小数のコマを測ること（代役の Blender）、契約が小数の f を受けることを確かめた。
- 2026-11-16 に Windows の Blender 5.2.4 で、リポジトリ外の試作（2 作目の VRM のダンス。24 fps の踊りを 30 fps・906 コマで描いた）の要素と比べた（実測）。入力は試作の台本が使った動きのコマ（小数が 737 コマ）とカメラ。基準は、試作の台本の写しを表情なしで回した要素。
  - カメラの控え: 906 コマすべてで、行列・焦点距離・主点が試作の記録とビットまで同じ。
  - disc・head: 906 コマすべてで画素まで同じ（小数のコマの頂点の動き・揺れものも、試作と同じ時刻で描けている）。
  - char: 882 コマが画素まで同じ（残り 24 コマで合わせて 111 画素、最大 25/255）。shade: 783 コマが画素まで同じ（残りは合わせて 6,973 画素、JPEG で最大 30/255）。どちらも、簡易描画の描くたびの揺れと、試作の場面の隠した部屋の物体のぶん（[要素別に描く](#要素別に描く)）。
  - `plan.retime`（fps 30・`motion_fps` 24・`start` 25）の並びは、試作の動きのコマと最大 1.1e-13 違い、7 コマは丸めで「前のコマ＋ほぼ 1 の割合」になる。その 7 コマと 3 コマを描くと、試作のコマ番号で描いたものと char・disc・head はファイルまで同じだった（shade の 1 コマは揺れ）。
- `render.stage` は Windows の Blender 4.5.15 で、別のキャラ（`look.toon` を当てた .blend）を動きのコマ 10・10.8・11.6 で描けた（270×480・1 標本。シーンのコマの範囲は小数のコマを含む整数 [10, 12] にする。画は動きに合わせて変わる）。試作との画素の比べはしていない。

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

**光のキー**（`plan.light_keys`。audio → keys）: リポジトリ外の試作で 32 秒の本番の光のキーを作った解析を移した。
- 曲の音（WAV など）の窓から、コマごとの帯の強さ（low・mid・high。0〜1）と、窓に入る拍を書く。
  - 窓は、音の `start` 秒（既定 0）から `frames` コマ（省くと音の終わりまで）。`fps` の既定は 30。
  - `song_start` は、窓の頭が曲の何秒目か（`start_s` に書く。既定は `start`）。
- `grid`（必須）: 拍の格子 `{"period": 拍の間隔の秒, "k0_time": 拍 0 の時刻の秒}`。時刻は、窓の頭が 0 秒。
  - 曲の拍を測る道具は持たない（2D の完成版などで決めた格子を書く）。
- 解析（既定は試作の値）:
  - 左右を平均した音に、コマ i の時刻 i / fps を中心にして、`n_fft`（既定 2048）標本のハン窓をかけ、パワーにする。
    - 音の外は 0。中心が音の終わりより後のコマは無音にして、その数を報告する（`silent_frames`）。
    - 標本の速さが fps で割り切れるとき（48000 Hz と 30 fps など）は、librosa.stft（中心に合わせ、外は 0）の |·|² と同じ値になる。
    - 割り切れないときも、窓の中心をコマの時刻に合わせる（長い曲でもずれがたまらない）。
  - 帯の強さ: 帯（`bands`）のパワーの和の log10 を、窓の中の `percentiles`（既定 20〜99）で 0〜1 に伸ばす。
    - 帯の既定は、low 30〜130・mid 300〜3000・high 5000〜16000 Hz。
    - 帯に入る周波数が無ければ止まる（標本の速さが低い音など）。
  - low は、立ち上がり（前のコマとの差 × `kick_gain`（既定 3）を 0〜1 に）と、強さ × `low_floor`（既定 0.55）の大きい方。
  - どの帯も、立ち上がりは即座に、下がりは前のコマの `release` 倍まで（既定 low 0.78・mid 0.86・high 0.72）。値は小数 3 桁に丸める。
  - 拍（`beats`）は、格子の拍のうち窓に入る全部（k・時刻・コマ）。`plan.stage` は格子から計算するので、見るためのもの。
- numpy だけで計算する（librosa は要らない）。
  - 音は、soundfile があればそれで読む（libsndfile が読む形式）。
  - 無ければ、PCM の WAV（8・16・24・32 ビット）を標準の wave で読む。値は soundfile と同じ。
- 計算だけで、同じ音と設定なら再利用する。

**確かめたこと**:
- 光のキー（CI。librosa・soundfile なし）では、次を確かめた。
  - 作り物の曲（打音・中音・高音）での帯の強さの形と下がり方。
  - 拍の数え方と、窓の頭と終わりちょうどの拍。窓の中心の取り方。無音のコマ。帯に入る周波数が無いとき。
  - PCM の WAV の読み方（8〜32 ビット）。悪い設定。工程の受け渡しと再利用。作ったキーを `plan.stage` が読めること。
  - librosa・scipy・soundfile がある環境では、窓が scipy のハン窓と、パワーが librosa.stft と、WAV の読み方が soundfile と、同じ値になることも確かめる（値・型・並びまで）。
- 光のキーは、2026-11-07 にリポジトリ外の試作（32 秒の本番の窓の音。48000 Hz・16 ビット・左右）でも確かめた。
  - 試作の光のキー（960 コマの 3 つの帯と、拍）と、全部の値が同じだった。
- 段取り（CI。Blender なし）では、次を確かめた。
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

## 光の台本（`plan.lights`）

`plan.lights`（plan [, keys] → track [, report]）は、光の段取り表（`model3d-light-plan/0.1`）から、コマごとの光の値のトラック（`model3d-light-track/0.1`）を作る。3 作目の VRM のダンスの演出版（リポジトリ外の台本）で、コマ T の関数として光の強さと色・拍で打つ量・閃光・暗転を返した計算を、段取り表と小さな式で書ける形に移した。`plan.stage` が LED の舞台の決まった光の値を作るのに対して、こちらは値の名前も数も段取り表で決める（舞台が作品ごとに違うため）。作品ごとの値は、段取り表としてリポジトリの外のレシピに置く。

**コマ T の値**（時刻 t = (T − 1) / fps 秒）:
1. `values`（値の名前 → 既定の値）を全部求める。
2. t を含む区間（`segments`）の `let`（区間の中の名前。書いた順に求め、後の `let`・`set`・`flashes` で使える）を求め、`set` の値で上書きする。
   - 区間の頭は前の区間の `until`（最初の区間は 0 秒）。最後の区間だけ `until` を省ける。時刻は秒・拍 `{"beat": k}`・`marks` の名前で書く（`plan.stage` と同じ読み方。ちょうど境目のコマは、次の区間）。
   - `set` は `values` と同じ形（数か 3 つの並び）にする。`values` に無い名前は止める。
3. 区間の `flashes` を並べた順に見る。`at` ≤ t < `at` + `length` なら `strength` × exp(−max(t − `at`, 0) / `decay`) を求め、今の `flash` より強ければ `flash` と `flash_color` をそれにする（同じ強さなら先のまま）。
   - 境目は、区間と同じく 1e-6 秒前のコマから届いたとみなす（ちょうど拍に当たるコマで、拍の時刻の丸めのために閃光の頭が 1 コマ遅れないように）。
   - `color` を省くと、`values` の `flash_color` の既定。`decay`・`length` の既定は 0.10 秒・0.6 秒（3 作目の値）。
   - `strength`・`decay`・`length` は数か式（3 つの並びは止める）。`decay` は 0 より大きい数（0 は 0 で割り、負は強まっていく閃光になる。式なら、求めた値をそのコマで確かめて止める）。
- `flash`（閃光の強さ。既定 0）・`flash_color`（閃光の色。既定 [1, 1, 1]）・`fade`（暗転で掛ける割合。既定 1）は、どの段取り表にもある。`flash` と `fade` は 0 以上の数、`flash_color` は 3 つの並び。描く工程と仕上げが読む。

**時刻からコマへ**（4 作目の 3D のダンスで分かったこと）: 光の台本の外で、出来事や表情の時刻をコマに直すときは、「その時刻に届く最初のコマ」T = ⌈t × fps − 10⁻⁶⌉ + 1 にする（コマ T の時刻は (T − 1) / fps。区間の頭を 1e-6 秒のゆとりで読む `plan.lights`・`plan.orbit` と同じ向き）。四捨五入（round）にすると、半分のコマで 1 コマ早まり、光の台本と出来事が 1 コマずれる。
- 時刻を秒の小数で書いた表（小数 3 桁に丸めた秒など）を、コマの時刻とそのまま比べない。丸めた秒がコマの時刻より少しだけ後ろになると、そのコマでは「まだ始まっていない」になる（4 作目では、1 コマだけの出来事が 1 つ抜けた）。表にコマの番号で書くか、比べるときに半コマのゆとりを持たせる（`render.show` の絵の差し替えの表情は、半コマのゆとりで選ぶ）。1 コマだけの出来事は、出たかどうかを数で確かめる。

**値**: 数、3 つの並び（色や位置。要素は数か式）、式（文字列。`colors` の名前も、そのまま式）。

**式**（Python の式の書き方の一部だけ。`ast` で読んで形を確かめ、決めた形のほかは読むときに止める。`eval` は使わない）:
- 名前:
  - `t`（秒）。
  - `low`・`mid`・`high`: 光のキー（`plan.light_keys`）のそのコマの帯の強さ。光のキーより後のコマは最後の値。キーを渡さなければ 0。
  - `hit`: 直前の拍からの減り方 exp(−((t − `k0_time`) mod `period`) / `hit.tau`)。拍で 1、すぐ下がる（`tau` の既定 0.13 秒は 3 作目の値）。
    - 拍の 1e-6 秒前からのコマは、区間と同じく拍に届いたとみなして 1（ちょうど拍に当たるコマで、引き算の丸めのために余りがほぼ 1 拍ぶんになり、拍の頭が落ちないように）。
  - `colors` の名前と、その区間の前に書いた `let` の名前。
- 関数: `beat(k)`（拍 k の時刻 `k0_time` + `period` × k）、`ramp(x, a, b)`（x が a から b へ進む割合を 0〜1 に切って、なめらかに）、`ease(u[, "smooth" | "linear" | "in" | "out"])`（u を 0〜1 に切ってから。smooth は u²(3 − 2u)、in は u²、out は 1 − (1 − u)²）、`mix(a, b, u)`（a + (b − a) × u。数どうしか 3 つの並びどうし）、`max`・`min`（2 つ以上の数）、`exp`、`rand(k)`（下）。
- 決まった乱数 `rand(k)`: コマ T と種 k から決まる 0 以上 1 未満の数（T × 7919 + k を種にした Python の `random.Random` の最初の数）。点きはじめのネオンのちらつき（`"neon": "1 - 0.6 * rand(13)"`）のように、コマごとにばらつく値を式で書ける。
  - コマの番号だけで決まるので、実行ごと・求める順・トラックの長さ・区切って描くかに寄らず同じ値になる。種を変えると、別の並びになる。
  - k は 0〜7918 の整数の定数（式や文字列は読むときに止める。文字列の種は、実行ごとに変わりうるため使わない。7919 より小さいので、別のコマ・別の種と同じ種にならない）。
  - 4 作目の 3D のダンスでは、描く工程のフックが、踊りのコマ f で同じ式のちらつきを掛けていた。光の台本では、書き出すコマ T で引く（T と f がずれている描き方では、同じ式でも別の値になる）。
- 演算: + − × ÷（3 つの並びは要素ごと。並びと数は、掛け算と割り算だけ）、単項の −。`a if 比べる式 else b`（比べる式は if の条件の中だけ。< <= > >=、`0 <= t - x < 0.6` のように続けて書ける）。
- 使えないもの: 属性・添字・ほかの関数・キーワードの引数・べき乗と余り・`and`・`or`・`not`・if の条件の外の比べる式・数と `ease` のつなぎ方のほかの定数。どれも読むときに、式と書いた場所を挙げて止める。
- 計算は Python の浮動小数（倍精度）で、式に書いた順に行う。元の台本と同じ順に書けば、同じ丸めになる。
- 求められない値（0 で割る・exp のあふれ・数と並びの取り違え）は、コマ T と書いた場所を挙げて止める。

**拍の格子と fps とコマの数**: 光のキー（`keys`）を渡すと、その `grid` と `fps` を使う（段取り表にも書くなら同じ値にする。違えば止める）。渡さなければ、段取り表の `grid` と `fps` が要る。コマの数は段取り表の `frames`（省くと光のキーの `frames`）。光のキーの `low`・`mid`・`high` の数が光のキーの `frames` と違えば止める（`plan.stage` と同じ）。

```json
{
  "schema_version": "model3d-light-plan/0.1",
  "colors": {"WHITE": [1.0, 1.0, 1.0], "RED": [1.0, 0.1, 0.1]},
  "values": {"key": 0.0, "key_color": "WHITE", "ring": 1.0, "spin": "t * 4.0"},
  "segments": [
    {"until": {"beat": 8}, "let": {"up": "ramp(t, 0.0, 0.9)"}, "set": {"ring": "1.3 * up"}},
    {"until": {"beat": 32}, "set": {"key": "0.25 + 0.75 * hit", "key_color": "mix(RED, WHITE, hit)"},
     "flashes": [{"at": {"beat": 8}, "strength": 0.5}]},
    {"set": {"key": 1.0, "fade": "1 - ramp(t, 28.0, 29.0)"}}
  ]
}
```

**出力**:
- `track`: `model3d-light-track/0.1`。コマごとの T・区間の番号・値（名前 → 数か 3 つの並び）と、fps・拍の格子。
- `report`: 区間ごとのコマの範囲、値の名前、閃光のあるコマ・暗転のあるコマの数、光のキーより後のコマの数、数の値ごとの最小と最大。

**確かめたこと**:
- CI（Blender なし）では、次を確かめた。
  - 式の読み方（使える形と、使えない形 26 通りを読むときに止めること）と求め方（3 作目の台本の式と同じ値）。3 つの並びの演算と `mix`。
  - 区間・`let`・`set`・閃光（強い方を残す・同じ強さは先のまま・色の既定・長さの境・0 以下の減り方と 3 つの並びを止めること）、暗転と閃光の値の確かめ、拍の `hit`（ちょうど拍のコマで 1）と音の帯、格子と fps とコマの数の読み方、数のそろわない光のキーを止めること。
  - 2 つの契約、工程の受け渡しと再利用（計算だけなので、同じ入力なら使い回す）。
- 2026-11-18 に、3 作目の VRM のダンスの演出版の光の関数（リポジトリ外の台本の写し）を、段取り表（14 の区間）にそのまま写して比べた（実測）。
  - 909 コマ × 40 の値（36,360）が、台本の関数の値とビットまで同じだった。`plan.lights` が書いたトラックを読み戻しても同じ。
  - 閃光のあるコマは 234、暗転のコマは 14。
- `rand`: CI では、`random.Random(T × 7919 + k)` と同じ値になること・コマごとに違うこと・求める順と長さに寄らないこと・種の書き方 9 通りを止めることを確かめた。2026-11-23 に、4 作目の 3D のダンスの描く工程のフック（リポジトリ外の台本の写し）のちらつき（深さ 0.6・種 13）を、代役の場面で 918 コマ回した値と、同じ式を `rand(13)` で書いた段取り表のトラック（T = f）を比べ、918 コマともビットまで同じだった（実測。400 コマで作ったトラックも、頭の 400 コマが同じ）。

## 題字を重ねる

`render.title` は、描いたコマに曲名などの題字を重ね、別のフォルダへ書く。`render.assemble` の前に置く。リポジトリ外の試作で、32 秒の締めに曲名のカードを重ねた台本を移した（2D 完成版の題字と同じ字形・大きさ・光り方・出方）。題字の文字と書体は作品ごとの値なので、リポジトリ外のレシピに書く。

**コマ**: `render.assemble` と同じ決まり（`frames` か、`frames_dir` と `range`）。描いていないコマ・一覧と今のファイルが違うコマ・無いコマがあれば止める。
- `out_dir`: 書き出すフォルダ（既定は工程のフォルダの `frames`）。元のコマのフォルダと同じなら止める（元のコマを書き換えない）。
- 窓の秒（T = 1 が 0 秒）が `start` より前のコマは、そのまま写す（ファイルまで同じ）。`start` からのコマは描き直す（濃さが 0 のコマも）。
- 透けた画素のあるコマ（`render.stage` の `background: "transparent"` など）は止める。
  - 題字を重ねたコマは RGB に書くので、透けが消えるため。先に土台へ重ねてから（`mv overlay` など）、題字を重ねる。
  - アルファがあっても全部 255 なら重ねる（試作のコマは RGBA）。

**出方**:
- `start`（窓の秒）から `fade_in` 秒（既定 0.5）で、なめらかに（3u² − 2u³）浮かび上がる。`fade_in` が 0 なら、`start` から濃さ 1。
- 層ごとの `rise`（画素）: 浮かび上がる間に、その分だけ下から上がる（y = center + rise × (1 − u)）。
- 層ごとの `opacity`（0〜1）: 濃さに掛ける。
- 題字は最後のコマまで出たまま（消すのは `render.assemble` の暗転）。

**層**（`layers`。並べた順に上へ重ねる）: `text`（文字）か `image`（画像のファイル）のどちらか 1 つ。
- `center`: [x, y]。覆い（文字や画像が半分より濃い画素）の範囲の中心を、ここに置く。
- 文字の層:
  - `font`: 書体のファイル（可変の Noto Serif JP など。書体は配らない）。省くと Pillow の内蔵の書体（ラテン文字だけ）。
  - `variation`: 可変の書体の形の名前（"Black" など）。書体に無い形なら、ある形を並べて止める。
  - `size`（画素）、`color`（既定は白）。
  - `stroke`（縁取りの画素）と `stroke_color`（既定 [8, 6, 30]）、`tracking`（字送りに足す量。size の倍数）。
  - 1 字ずつ描く。周りに size × 0.45 + 縁取り × 2 の余白を取る（影と光りが広がる場所）。
- 画像の層: 透けのある PNG など（透けの無い画像は全面が覆い）。
  - 周りに、影と光りのぼかしが届く分（核の長さの半分。sigma の約 4 倍）の透けた余白を足す。
  - 透けの無い画像や、端まで描いた画像にも、外へ影と光りが付く。
- `glow`（任意）: `{"color", "blur", "amount"}`。覆いをぼかした色の光り（影の上、文字の下）。
- `blend`（任意）: 重ね方。既定の `"prototype"` は試作と同じ、`"over"` はふつうの重ね方（下の「計算」）。

**影**: `shadow`（既定 `{"blur": 9, "amount": 0.7}`、null で無し）。覆いをぼかした黒い影。濃さを掛けた後、光りと同じく 0〜1 に切る（`amount` が 1 以下なら切っても同じ値）。

**大きさ**: 大きさ・位置・縁取り・ぼかしは、基準の幅（`reference_width`、既定 1080）での画素で書く。コマの幅に合わせて掛ける。丸めは試作と同じで、大きさは四捨五入、縁取りとぼかしは 1 以上。

**計算**:
- float32 で重ね、0〜255 へは切り捨てで書く（試作と同じ）。そのため、濃さ 0 のコマでも 1/255 ずれる画素がある。
- ぼかしは、OpenCV の GaussianBlur（sigma の指定）と同じ長さと形の核を、numpy で掛ける（端は反射 101。model3d は OpenCV に頼らない）。
- 重ね方（層ごとの `blend`）: 絵の色は、影・光り・文字（か画像）を覆いで重ねた色で、覆いを掛け済み。
  - 既定の `"prototype"` は試作と同じで、その色にもう一度、覆い × 濃さを掛けて重ねる。
    - 半ば透けた画素（文字の縁・光り・影・半透明の画像）は、ふつうの重ね方より暗い（白の覆い 0.6 は、黒の上で 0.36）。
    - 採用版の題字はこの重ね方なので、既定を変えると一致しなくなる。
  - `"over"` は、ふつうの重ね方（色 × 濃さ + 下 × (1 − 覆い × 濃さ)）。白の覆い 0.6 は、黒の上で 0.6。縁と光りが明るくなる。
  - 覆いが 1 か 0 の画素は、どちらでも同じ。

**出力**:
- `frames`: 書いたコマの一覧（`model3d-frames/0.1`）。`render.assemble` の入力 `frames` に渡せる。
- `report`（任意）: `model3d-title/0.1`。中身は次のとおり。
  - 元のコマ（フォルダ・T の範囲・sha256）、書き出し先、start・fade_in・倍率、影
  - 層ごとの書体と sha256・形・大きさ・縁取り・絵の大きさ・覆いの範囲・光り・重ね方（画像の層は余白も）
  - 題字を重ねたコマの数と範囲
  - 題字の文字そのものは書かない（sha256 と字数だけ）。

**確かめたこと**:
- CI では、次を確かめた。
  - ぼかし: 核の長さ（OpenCV と同じ）、端の折り返し（核が並びより長くても）、2 次元の直接の畳み込みとの一致。OpenCV があれば、OpenCV との差が 2e-6 未満。
  - 題字の絵: 1 字ずつの字送りと余白、縁取りで覆いが太ること、影と光りの付き方、覆いの中心合わせ、薄い層と画面の外の層を飛ばすこと、切り捨て。
  - 重ね方: 既定は覆いを二度掛け（白の覆い 0.6 が黒の上で 0.36）、`over` はふつうの重ね方（0.6）。覆いが 1 か 0 の画素は、どちらでも同じ。
  - 影の濃さが 1 を超えても、覆いは 1 まで（下地に負の値を掛けない）。濃さ 1 以下は切らない。
  - 画像の層の余白: 透けの無い画像にも、外へ影と光りが付く。画像の中は画像の色のまま。影も光りも無ければ余白も無い。
  - コマへの重ね方:
    - 前のコマはファイルまで同じ。`start` ちょうどは描き直すだけで、層から遠い所は変わらない。
    - 0.2 秒で浮かび上がる。画像の層も重なる。コマの幅に合わせて大きさが変わる。
    - `blend` は層ごと。`over` の層だけが明るくなり、ほかの層は同じ。
    - 報告に、題字の文字そのものが無い。
  - 書いた一覧から `render.assemble` で動画になること。一覧と今のファイルの照合。悪い設定を止めること。
  - 透けたコマを止めること（最初のコマなら写す前に）。アルファが全部 255 のコマには重ねること（前のコマは RGBA のまま写し、題字のコマは RGB）。
- 2026-11-08 に、リポジトリ外の試作の 32 秒（題字の前の 960 コマ）で確かめた。
  - 試作の題字を重ねたコマと比べると、960 コマのうち 945 コマはファイルまで同じ（写した 906 コマと、題字の 39 コマ）。
  - 残る 15 コマも、違うのは合わせて 17 画素で、どれも 1/255。ぼかしの float32 の和の取り方の違い（OpenCV と numpy で順が違う。差は 5e-7 まで）で、切り捨ての境目にあった画素。
- 2026-11-08 のレビュー（Codex の指摘 4 件）で、`blend` と画像の層の余白を足し、影の覆いを 0〜1 に切り、透けたコマを止めるようにした。
  - 既定の重ね方と文字の層は変えていない。既定の影（濃さ 0.7）は、切っても同じ値。
  - 題字の 54 コマを、直す前と後のコードで同じ設定で書き出すと、ファイルまで同じ。試作の題字との一致は上のまま。

## コマを割って同じ瞬間を見せる（`render.panels`）

`render.panels` は、描いたコマの決めた区間で、画面を上下の段に割り、同じ T を別のカメラで描いたコマを段ごとに入れて、別のフォルダへ書く（同じ瞬間を 2〜3 のカメラで同時に見せる）。`render.assemble` の前に置く。リポジトリ外の 4 作目の 3D のダンスの仕上げの台本（OpenCV で書いたコマ割り）を、numpy の部品にして移した（model3d は OpenCV に頼らない）。2D の側のつなぎ（カット・クロスフェード・黒・フラッシュ・焼け抜け）に、画面を割って並べる重ねは無い。

**流れ**:
1. `render.show`（主のカメラ）: 全部のコマを描く。
2. `render.show`（別のカメラ）: 同じ .blend と光の値で、別の `plan.orbit`（か `plan.shots`）のカメラの控えを使い、割る区間だけを `only` で描く。描いていない T は一覧の `missing` に載る。
3. `render.panels`: 主のコマの一覧を `frames`、別のカメラの一覧を `frames_b`・`frames_c` に渡す。
4. `render.assemble`（か `mv dance finish`）: 書いたコマの一覧をまとめる。

**コマ**:
- 主のコマ: `render.title` と同じ決まり（`frames` か、`frames_dir` と `range`）。描いていないコマ・一覧と今のファイルが違うコマ・無いコマがあれば止める。
- 別のカメラのコマ: 入力 `frames_b`・`frames_c`（`model3d-frames/0.1`。`missing` があってよい）か、パラメーター `frames_b_dir`・`frames_c_dir`（`f<T>.png` のフォルダ。無い T があってよい）。一覧なら、一覧の sha256 と今のファイルが違えば止める。大きさが主のコマと違えば止める。
- `out_dir`: 書き出すフォルダ（既定は工程のフォルダの `frames`）。元のコマのフォルダと同じなら止める。
- 区間の外のコマは、そのまま写す（ファイルまで同じ）。
- **別のカメラのコマが無い T** は、割らずに主のコマをそのまま写し（ファイルまで同じ）、報告の `skipped`（T・区間・無いカメラ）に書く。工程は要確認（`needs_review`）になる（黙って成功にしない）。
- 透けた画素のあるコマと、大きさの違うコマは止める（書くコマは RGB なので）。そのまま写すコマ（区間の外・とばした T）も同じく確かめる。

**区間**（`panels`。区間どうしは重ねない）:
```json
{"range": [41, 54],
 "bands": [{"source": "b", "shift": -0.1}, {"source": "main"}, {"source": "c", "shift": 0.12}],
 "borders": [{"left": 0.3, "right": 0.36, "seed": 1, "steps": 8, "jag": 0.02, "jag_x": 0.01},
             {"left": 0.68, "right": 0.62, "seed": 2, "steps": 8, "jag": 0.02}]}
```
- `range`: 割る T の範囲（主のコマに全部あること）。
- `bands`: 上から順の段（2 つ以上）。`source` は `"main"`（主）・`"b"`・`"c"`、`shift` は下へずらす量（負なら上へ。外から入る所は端の画素を延ばす）。
- `borders`: 上から順の境（段の数 − 1）。いちばん上の段を地にし、境ごとに、その境より下を次の段にする。
  - 折れ線: 左の端の高さ `left`、右の端の高さ `right`、折れの数 `steps`、折れの上下の量の上限 `jag`（ずれ = −`jag`〜`jag` の乱数 × 0.5〜1 の乱数 × 折れごとに入れ替わる ±1。最初の乱数の符号も乱数なので、上下の向きは交互にならず、大きさは 0〜`jag`。台本と同じ式）、横の揺れ `jag_x`（−`jag_x`〜`jag_x` の乱数。既定 0）、種 `seed`（整数）。同じ種なら同じ折れ線。左右の端は、画面の外へ基準の高さの 1/240 だけ出す。
  - 乱数は `random.Random(seed)` で、折れごとに 上下の量 → 上下の割合 → 横の揺れ の順に引く（台本と同じ）。点は整数へ切り捨てる。折れ線の点が左から右へ並ばない（`jag_x` が折れの間より大きい）ときは止める。

**境の線**（`line`。省くと線を描かず、段の境でそのまま切り替わる）:
```json
{"glow_color": [200, 160, 255], "glow_width": [0.02, 0.02], "glow_blur": [0.01, 0.01], "glow_gain": [1.0, 1.0],
 "core_color": [255, 255, 255], "core_width": [0.005, 0.005], "settle": 4}
```
- 幅・ぼかし・強さは `[落ち着いた値, 割れた最初のコマで足す量]`。足す量は、区間で実際に割れた最初のコマで全部、`settle` コマかけて 0 になる（強さ = max(0, 1 − (T − 割れた最初の T) ÷ `settle`)）。区間の頭の T を別のカメラのコマが無くてとばしても、最初に割れたコマで太く明るい線になる（台本は区間の頭から数えた。とばすコマが無ければ同じ）。
- にじみ: 太さ `glow_width` の縁のくっきりした線を `glow_color` で描いてぼかし（`glow_blur` は sigma）、`glow_gain` 倍して足す（0〜255 に切り、切り捨て）。
- 芯: 太さ `core_width` のなめらかな縁の線を `core_color` で重ねる。
- 太さは画素に直して四捨五入する（にじみは 2 以上、芯は 1 以上）。線の半径は (太さ + 1) ÷ 2 の切り捨て（OpenCV の太い線と同じく、奇数の太さは 1 画素太い）。

**大きさ**: 境の高さ・ずらす量・折れの量・線の太さ・ぼかしは、基準の高さ `reference_height`（既定 1。画面の高さの比で書く）での数。コマの高さ ÷ `reference_height` 倍する。台本と同じ数（ある高さの画素）で書くなら、その高さを `reference_height` にする。

**計算**:
- 上下のずらしは、OpenCV 5 の warpAffine（直線の補間・端の画素を延ばす）と同じ形（float32 の位置と重み、偶数への丸め）。整数の画素のずらしは、そのまま写すのと同じ。
- 境から下の範囲は、折れ線を画素の列ごとにつないだ高さ（四捨五入）から下。
- にじみのぼかしは、題字と同じ（OpenCV の GaussianBlur と同じ長さと形の核を numpy で）。
- 芯のなめらかな縁は、線からの距離で濃さを決める（濃さ = 半径 + 1.2 − 距離 を 0〜1 に切る。1.2 は、OpenCV の LINE_AA の縁と、太さ 10〜43 の折れ線で差がいちばん小さくなった幅）。

**出力**:
- `frames`: 書いたコマの一覧（`model3d-frames/0.1`）。`render.assemble`・`mv dance finish` の入力に渡せる。
- `report`（任意）: `model3d-panels/0.1`。主のコマ（フォルダ・T の範囲・sha256）、別のカメラのフォルダとコマの数、倍率、区間ごとの段（ずらす画素）・境（設定と画素の点）・割った T・割らなかった T、線の設定、割れたばかりのコマの太さ・ぼかし・強さ（`fresh`）、`skipped`、写したコマの数。

**確かめたこと**:
- CI（Blender なし）では、合成の単色のコマ（主 赤・b 緑・c 青）で次を確かめた。
  - 3 つ割りで、上の段が c、真ん中が主、下が b の色になる。2 つ割りも同じ。区間の外のコマはファイルまで同じ。
  - 同じ種なら同じ折れ線と同じコマ（一覧の sha256 まで同じ）。種を変えた境だけが変わる。折れ線は台本の式と同じ点。
  - 別のカメラのコマが無い T は、主のコマと同じバイトで、報告の `skipped` に載り、工程が要確認になる。一覧の `missing` の T も同じ。
  - 高さ 960 と 1920（`reference_height` 1）で、境の点と、列ごとの段の切り替わりの高さが、高さの比で合う（2 画素・3 画素以内）。
  - 割れた最初のコマで線が太く明るく、`settle` コマで落ち着く。線から遠い所は段の色のまま。
  - 上下のずらし: 整数は OpenCV の warpAffine と画素まで同じ、小数は最大 1 の差が 5 画素以下（400×300 の乱数の絵）。
  - 書いた一覧から `render.assemble` で動画になること、一覧と今のファイルの照合、大きさの違うコマと透けたコマを止めること、悪い設定を止めること。
- 2026-11-23 に、リポジトリ外の 4 作目の 3D のダンスの仕上げの台本と、同じ入力のコマ（描き直した主のカメラの 28 コマ・1080×1920 と、別のカメラ 2 つのコマ。読むだけ）で比べた（実測。台本の数は照合の台本のレシピに書き、既定にはしていない）。
  - 2 つ割り 14 コマ・3 つ割り 14 コマとも割れ、割らなかったコマは 0。折れ線の点は台本と同じ。
  - 画素まで同じコマは 0。差の平均は 0.007〜0.025/255（2 つ割りの平均 0.009、3 つ割りの平均 0.020）。違う画素は 1 コマ 2.8 万〜10.8 万（207 万 3,600 のうち）で、ほとんどは 1/255。2/255 以上違う画素は 1 コマ 205〜4,347、最大の差は 96/255（奇数の太さの芯の縁）。
  - 違う画素は、境の線から 140 画素の中だけ（外は 28 コマで合わせて 13 画素、どれも 1/255）。線の芯のなめらかな縁（OpenCV の LINE_AA と縁の濃さの決め方が違う）と、にじみ（縁のくっきりした線の縁の画素の違いを、ぼかしが広げて、切り捨てで 1 違う）。段の中の絵とずらしは、画素まで同じ。
  - 1 コマ約 1.6〜1.9 秒（numpy。1080×1920）。

**限界**: 区間の途中で、割り方（段・境）を変えられない（区間を分ける）。境の線は、OpenCV の LINE_AA と縁の画素の濃さが少し違う。カメラを傾けた構図に合わせて境を決める道具は無い（境の高さは構図を見て決める）。

## 目の残像を足す（`render.afterimage`）

`render.afterimage` は、暗くなる直前の明るいコマの像を、暗くなったあとのコマにうすく残す（明るい物を見たあと、目に残る像）。`render.assemble` の前に置く。リポジトリ外の 4 作目の 3D のダンスの仕上げの台本（OpenCV で書いた残像）を、numpy の部品にして移した。2D の側の `afterimage_echo`（[dance](dance.md)。人物の輪郭の残像を、人物の後ろに足す）とは別のもので、こちらは画面全体の像。

**コマ**: `render.title` と同じ決まり（`frames` か、`frames_dir` と `range`。描いていないコマ・一覧と今のファイルが違うコマ・無いコマ・透けた画素のあるコマ・大きさの違うコマは止める。そのまま写すコマも同じく確かめる）。`out_dir` は `render.panels` と同じ。`span` の外のコマと、濃さが `min_amount` 以下のコマは、そのまま写す（ファイルまで同じ）。

**設定**（どれも作品ごとに決める。既定は `min_amount` と `reference_height` だけ）:
- `source`: 明るいコマの T（描いたコマにあること）。
- `span`: 足す T の範囲 `[最初の T, 最後の T]`。`source` より後から始める（明るいコマの後に残る像。`source` と同じか前から始まる範囲は止める）。描いたコマに 1 つもなければ止める。
- `amount`: `span` の最初の T での濃さ。`decay_frames`: 濃さが 1/e になるコマ数（濃さ = `amount` × e^(−(T − 最初の T) ÷ `decay_frames`)）。
- `min_amount`（既定 0）: 濃さがこれ以下のコマには足さない（薄すぎて見えないコマを、描き直さない）。
- `threshold`（0〜1）: 明るさから引く量。これより暗い所には足さない。
- `blur`: ぼかしの sigma（基準の高さ `reference_height`。既定 1 で画面の高さの比）。
- `color`: 足す色（0〜1 の RGB。明るいコマの色の補色など）。

**計算**:
- 明るさ = 0.2126 R + 0.7152 G + 0.0722 B（表示の値のまま。0〜1）をぼかし（題字と同じ。OpenCV の GaussianBlur の float の絵と同じ長さと形の核）、`threshold` を引いて 0〜1 に切り、`color` を掛けた像を、1 回だけ作る。
- コマごとに、像 × 255 × 濃さを足す（0〜255 に切り、切り捨て）。

**出力**:
- `frames`: 書いたコマの一覧（`model3d-frames/0.1`）。`render.assemble`・`mv dance finish` の入力に渡せる。
- `report`（任意）: `model3d-afterimage/0.1`。元のコマ（フォルダ・T の範囲・sha256）、明るいコマの T と sha256、設定、ぼかしの画素、像の明るい画素の数、足した T ごとの濃さ、写したコマの数。

**確かめたこと**:
- CI（Blender なし）では、黒いコマに白い四角のコマの残像を足して、四角の所だけが明るくなること（真ん中の値は (1 − `threshold`) × 色 × 255 の切り捨て）、`decay_frames` コマで濃さが 1/e になること、`min_amount` 以下のコマはファイルまで同じこと、`span` の外のコマはファイルまで同じこと、一覧の照合、悪い設定を止めることを確かめた。
- 2026-11-23 に、リポジトリ外の 4 作目の 3D のダンスの仕上げの台本と、同じ入力のコマ（描き直した主のカメラの 33 コマ・1080×1920。読むだけ）で比べた（実測。台本の数は照合の台本のレシピに書き、既定にはしていない）。
  - 足したコマは台本と同じ 30 コマ（写した 3 コマは画素まで同じ）。
  - 足した 30 コマは、どの画素も差が 1/255 以下。差の平均は 0.001〜0.042/255（濃いコマほど大きい）。台本は 8 ビットの色をぼかしてから明るさにし（ぼかしの核も 8 ビットの絵の長さ）、ここでは明るさ（float）をぼかすので、その丸めの分だけ違う。
  - 1 コマ約 0.4 秒（33 コマで 11 秒。ぼかしは最初の 1 回だけ）。

**限界**: 明るいコマは 1 つ（`source`）。動く明かりの残像（コマごとに像を作り直す）は作らない。像は画面に固定（カメラが動いても、像は動かない。目の残像としては正しいが、ほかの使い方には合わない）。

## 動画にまとめる

`render.assemble` は、`render.stage` が描いたコマ（`f<T>.png`）と曲の音を、1 本の動画にする。リポジトリ外の試作で 32 秒の本番をまとめた書き出しを移した。

**コマ**（どちらか）:
- `frames`（入力）: `render.stage` の描いたコマの一覧か、`render.title` が題字を重ねたコマの一覧（どちらも `model3d-frames/0.1`）。
  - 描いていないコマ（`missing`）があれば止める。
  - 一覧の sha256 と今のファイルが違えば止める（描いた後に変わったコマ）。
- `frames_dir` と `range`（`[最初の T, 最後の T]`）: クリップごとに別の工程で同じフォルダへ描いたコマを、まとめて 1 本にするとき。
  - レシピの入力は 1 つずつ参照するので、複数の一覧はつなげられない。描く工程は `depends_on` で先に並べる。
- 外で作ったコマ（描いたあとに仕上げを掛けたコマ、2 つのフォルダから選んで 1 つのフォルダへ写したコマなど）は、コマの一覧（`model3d-frames/0.1`。T・ファイル・sha256・大きさ）を書いて `frames` に渡すか、1 つのフォルダに `f<T>.png` で置いて `frames_dir` と `range` で渡す。
  - 4 作目の 3D のダンスでは、仕上げを掛けたコマの一覧を自分で書いて渡した。コマごとの sha256 が残るので、あとで描き直したコマと照らせた（本番のコマのファイルが消えたあとも、どのコマが同じかを sha256 で言えた）。一覧を書くなら、`frames_dir` より一覧の方が、あとで照らせる。
- T は 1 ずつ続いていること（とびがあれば止める）。無いコマがあれば止める。

**音とフェード**:
- `audio`（入力）: 曲の音（ffmpeg が読めるもの）。窓（T = 1 が 0 秒）の頭が、曲の `audio_start` 秒（既定 0）。
  - まとめる最初のコマが T = 1 でなければ、その分だけ曲を先から読む。
  - 音は動画の長さで切る（`-shortest`）。
  - 読み始めからの音が映像より短ければ、音の後ろを映像の長さまで無音で足す（`apad=whole_dur`）。`-shortest` は短い方の流れで書き終えるので、足さないと映像のコマまで落ちるため。音の長さは `ffmpeg -i` の表示（0.01 秒きざみ）で読み、0.005 秒より短いときだけ足す。報告の `audio.padded` に載せる。
- `fade_in`・`fade_out`（`{"start": 窓の秒, "duration": 秒}`）: 黒からの、黒へのフェード。窓の秒のまま掛ける。
  - まとめる最初のコマが T = 1 でなければ、コマの時刻を T - 1 コマぶん進めてからフェードを掛け、元に戻す（ffmpeg の `fade` は負の始まりを受け付けないため）。
  - そのため、範囲の頭がフェードの途中なら途中の濃さから続き、範囲より前に終わったフェードは何もしない。戻した後に `fps` でコマの長さを付け直す（`setpts` が長さを 0 にし、最後のコマが落ちるため）。

**書き出し**:
- 色: PNG（RGB）から YUV（4:2:0、TV の範囲）へは BT.709 の式で変え、BT.709 の印（色の行列・原色・伝達特性・範囲）を付ける。印が無いと、HD の再生機は BT.709 として読むので、BT.601 の式で変えた動画は色がずれる。
- H.264（`crf` 既定 18、`preset` 既定 medium）、AAC（`audio_bitrate` 既定 192k）。`fps` は省くと一覧の fps。
- 流れは名指しする（映像は描いたコマ `0:v:0`、音は `1:a:0`）。音の入力に映像の流れがあると（映像つきの曲のファイルなど）、ffmpeg は大きい方の映像を自動で選ぶため。
- ffmpeg は、パラメーター `ffmpeg` → `--tool ffmpeg=PATH` → imageio-ffmpeg → PATH の順に探す。

**出力**:
- `video`: mp4。
- `report`（任意）: `model3d-assemble/0.1`。中身は次のとおり。
  - コマのフォルダ・T の範囲・数・sha256（各コマの sha256 を並べたものの sha256）
  - fps・秒、音の sha256 と読み始め、フェード
  - ffmpeg の版と引数、動画の sha256 と大きさ
  - 書いた動画の長さと流れ（`ffmpeg -i` の表示）

**確かめたこと**:
- CI では、次を確かめた。
  - 秒の書き方、フェードのずらし方、ffmpeg の引数、コマの並びの確かめ、悪い設定を止めること。
  - 後ろの範囲だけをまとめるときのフェード（本物の ffmpeg）。範囲の頭がフェードの途中なら 0.6 の濃さから、前に終わったフェードはそのまま、後で始まるフェードは黒から。コマの数も落ちない。
  - 映像より短い音（1 秒の映像に 0.5 秒の音）でも、10 コマすべてが残り、動画は 1 秒（後ろは無音）。足す前は 5 コマに切れた。
  - 映像（64×96）の入った曲のファイルを音にしても、動画は描いたコマ（32×48）になる。名指しする前は、曲のファイルの映像が動画になった。
  - 本物の ffmpeg（imageio-ffmpeg）での小さな書き出し: BT.709 の印、AAC の音、描いたコマの一覧との照合。
- 2026-11-07 に、リポジトリ外の試作の 32 秒（960 コマ、30.03 秒で止まる曲の窓、最後の 0.6 秒で暗転）で確かめた。
  - 試作と同じ ffmpeg（7.1）で、試作のまとめと**ファイルまで同じ**動画になった（sha256 が一致）。ffmpeg を指定しない既定（imageio-ffmpeg の 7.1）でも同じだった。
  - 960 コマ・32.00 秒、映像と音のどちらも、復号したコマまで同じ。

## 動画の中身を確かめる（`check.video_frames`）

書き出した動画が、いま描いたコマの並びでできているかを確かめる。古い版のコマのまま書き出した、コマが 1 つずれた、別のフォルダのコマを渡した、を見つけるため。リポジトリ外の 3 作目の VRM のダンスで、演出版を書き出すたびに、動画から数コマを取り出してもとのコマと比べた使い捨ての比べを部品にした。

**入力とパラメーター**:
- `video`（入力）: 書き出した動画。`frames`（入力。`render.stage`・`render.title` の描いたコマの一覧 `model3d-frames/0.1`）か、`frames_dir`（コマのフォルダ `f<T>.png`）と `range`（`[最初の T, 最後の T]`）でもとのコマ。一覧なら、`render.assemble` と同じく、一覧の sha256 と今のファイルが違えば止める。
- `first`: 動画の 0 番目のコマの T（既定は、もとのコマの最初の T）。動画の n 番目のコマ（復号した順）を T = `first` + n とする。
- 比べる T: `at`（T の並び）か、もとのコマの最初から最後まで両端を含む `count` 個（既定 8）。
- `offset`（既定 5）、`ratio`（既定 0.5）、`still`（既定 1.0）、`max_mean_abs`（任意）、`ffmpeg`（`render.assemble` と同じ探し方）。

**比べ方**:
- 取り出し: ffmpeg の `select` でコマを取り出し、動画の印の色の式（`bt709`。ほかは `bt601`）と範囲（`tv`。`pc` の印か `yuvj` の画素の形なら `pc`）から RGB に戻す（`render.assemble` が書く動画は BT.709・TV の範囲）。報告の `color` に読んだ式と範囲。動画のコマの数を超える T を頼めば止める。
- 差: 取り出したコマと、もとのコマ T と T ± `offset` の範囲の各コマとの、色の差の平均（R・G・B の 3 つで割る。0〜255）。
- 合う: 範囲の中で差がいちばん小さいのが T で、T との差が T ± `offset` のコマとの差の `ratio` 倍以下。`max_mean_abs` を渡せば、T との差がそれ以下であることも。
- 見分けられない: もとのコマ T と T ± `offset` のコマの差が `still` より小さい（動きの無い所）。合否に数えない。
- 合わないコマがあれば不合格（いちばん近いコマとのずれ `shifts` も出す。1 コマずれた書き出しなら 1）。どれも見分けられなければ未実施。

**出力**: `report`（比べた T、T ごとの差・前後のコマとの差・もとのコマどうしの差・いちばん近い T・合う／合わない／見分けられない、合わない T、ずれ、差の範囲、比べた動画の sha256 `video_sha256`）。
書き出しの検査の表（動画の構造・この報告・最後のコマ・`render.show` の影の領域のあふれと描画の秒）は、2D の側の `mv gate final` が、この報告のファイルを読んで作る（[gate](gate.md#3d-で描いた動画の書き出しの表mv-gate-final)）。

**限界**: 版の違いが画面の小さな範囲だけ（顔の表情だけなど）なら、色の差の平均では見分けられない（下の確かめ）。カメラや場面が変わった版、ずれた書き出し、別の作品のコマは見分けられる。

**確かめたこと**:
- CI では、比べる T の選び方、動画の色の印の読み方（BT.709・TV の範囲と、印の無い pc の範囲の動画）、同じコマの動画が合うこと、1 コマずれた書き出し（ずれ 1 で不合格、`first` を合わせれば合格）、動きは同じでも色の違う別の版のコマで不合格、差の上限、動きの無いコマ（見分けられない）、大きさの違うコマ、悪いパラメーターを、本物の ffmpeg（imageio-ffmpeg）と `render.assemble` と同じ書き方の小さな動画で確かめた。
- 2026-11-18 に、リポジトリ外の 3 作目の VRM のダンスの演出版の動画（1080×1920・909 コマ・BT.709。凍結の物を読むだけ）で確かめた（実測。既定の 8 コマ・前後 5 コマ）。
  - 最後の版の動画と、その版の仕上げたコマ: 8 コマとも合った。同じコマとの差は 0.0〜1.43/255、5 コマ前後のコマとの差は 11.5〜32.8（作業メモの使い捨ての比べは、別の 8 コマで 1.2〜1.9 と 10〜62）。
  - 前半のカメラが違う 1 つ目の版の動画を、最後の版のコマと比べると、作り直した前半の 3 コマ（131・260・390）が合わず、不合格になった。変わっていない最初のカットと後半の 5 コマは合った（2 つの版の台本の値で、カメラが同じだったコマ）。
  - 表情だけが違う 2 つ目の版の動画は、最後の版のコマと 8 コマとも「合う」になった（同じコマとの差は 0.0004〜0.0015 大きいだけ）。上の限界。

## 色の数を、絵の組で比べる（`check.colors`）

`check.colors`（→ report）は、参考の絵（`left`）とこちらの絵（`right`）を同じ物差しで測り、組ごとの平均と、その差（こちら − 参考）を出す。参考の動画の決めた時刻のコマと、こちらの見本のコマのように、時刻の違う止め絵の組で、明るさと色の分布が近いかを数で見るため。リポジトリ外の 4 作目の 3D のダンスで、参考の動画と舞台・塗りの見本を比べた 2 つの台本（絵ごとの明るさと色の分布、決めた所の色の平均）を 1 つにした。
- 並べて目で見るのは `mv sheet --with`（同じ時刻の動画どうし）。1 本の動画の中のコマごとの明るさは `mv inspect`。どちらも、時刻の違う止め絵の組の数は出さない。
- 合否は出さない（数だけ。どこまで近ければよいかは、作品ごとに見て決める）。

**絵**（`left`・`right`。どちらか一方だけでもよい）: 並びの 1 つずつは、絵のパス（文字列）か `{"path": 絵}`、または `{"video": 動画, "at": 秒}`（その時刻に映っているコマ。頭の時刻が「秒 ＋ 1e-6」以下の、いちばん後ろのコマ。コマの時刻は ffmpeg の framemd5 の時間の単位と pts の整数から分数のまま求める（29.97・23.98 のように丸めた fps を掛けると、30000/1001 の動画では 1.001 秒で 1 コマ前を選ぶ）。動画の終わりより後ろの時刻は止める。動画の印の色の式と範囲で RGB に戻す。`check.video_frames` と同じ戻し方）。`name`（既定はファイルの名前。動画は「名前@秒」）、`spots`、`crop` も書ける。パスはレシピのフォルダから。
- `spots`: 決めた所の並び `[{"name", "x", "y", "r"}]`（画素。中心 (x, y) と半径 r の正方形）。パラメーターの `spots` はどの絵にも、絵ごとの `spots` はその絵だけに足す。
- `crop`: [x0, y0, x1, y1]（画素。x1・y1 は含まない）。明るさと色の数を、切り抜いた所だけで測る（字幕の帯を外すなど）。決めた所の座標は、切り抜く前の絵の座標。
- `ffmpeg`・`timeout`（秒。既定 600）: 動画を読むとき。

**数**（どれも表示の値。sRGB の 0〜255 を 255 で割った値で、線形に直さない）:
- 明るさ: 画素ごとに 0.2126 R + 0.7152 G + 0.0722 B。平均（`mean`）と、5 %・50 %・95 % の値（`p05`・`p50`・`p95`。numpy の percentile）。
- 色: R・G・B の平均（`rgb`）、緑÷赤・青÷赤（`g_r`・`b_r`。平均どうしの比。赤の平均は 1e-6 より小さくしない）。
- 彩度（`sat`）: OpenCV の 8 ビットの HSV の S と同じ整数の計算（S = (最大 − 最小) × round(255 × 4096 ÷ 最大) を 4096 で割って丸めた値。最大が 0 なら 0）の平均 ÷ 255。
- 決めた所: R・G・B の平均（0〜255）、いちばん大きい値（`max`）、どれかの色が 254 以上の画素の割合（`clipped`。白飛び）、画素の数。画面の外は切る（全部が外なら止める）。

**出力**: `report`（`model3d-color-stats/0.1`）: 絵ごとの行（名前・大きさ・数・決めた所。動画は時刻・コマの番号・そのコマの頭の時刻 `frame_time`）、組ごとの平均（`average.left`・`average.right`。決めた所は含まない）、差（`difference` = こちら − 参考）。工程の観察にも、平均と差を出す。

**確かめたこと**:
- CI では、数の式（明るさ・百分位・色の比・彩度・決めた所・切り抜き）、OpenCV の HSV の S との一致（cv2 のある環境）、動画の時刻 → コマの番号（30000/1001 と 24000/1001 の時刻の表と、ffmpeg で作った 30000/1001 の動画）、組の平均と差、悪いパラメーター、工程の受け渡し（再利用しない）を、作り物の絵で確かめた。
- 2026-11-23 に、4 作目の 3D のダンスの 2 つの台本（凍結の控えと SHA-256 が同じ写し。OpenCV で読む）と比べた（実測）。
  - 見本のコマ 5 枚（540×960 の PNG）: 明るさの平均・5 %・50 %・95 %、R・G・B の平均、緑÷赤・青÷赤、彩度が、5 枚とも台本の丸め（3 桁。比は 2 桁）で同じ。決めた所 3 か所 × 5 枚の色の平均・いちばん大きい値・白飛びの割合も、15 とも台本の表示の桁で同じ。
  - 完成した動画（1080×1920・BT.709・TV の範囲）の 4 つの時刻のコマ: 明るさの平均が台本より 0.020〜0.030 高く、彩度が 0.031〜0.057 低かった。台本の OpenCV は、動画の印（BT.709）を見ずに BT.601 の式で戻す（同じコマを ffmpeg の BT.601 で戻すと OpenCV との差は平均 1.2/255、BT.709 では 4.0〜5.2/255）。`check.colors` は印の式で戻すので、動画の数は台本と同じにならない（こちらが印どおり）。参考の動画と比べるときは、両方を `check.colors` で測る。

## Orpheus との接続（受け渡し）

1. 3D 側で描いた動画（緑背景か透過の連番から作った動画）を、`model3d handoff RUN <工程 id>/<出力名> handoff.json` で控えにする。
2. dance の spec の `performer.clips[].path` にその動画を書き、spec のどこかに `"x_model3d": {"handoff": "handoff.json"}` のように参照を置く
   （`x_` の鍵は dance が無視するので、`dance-shot/0.2` の契約は変えない）。
3. `mv dance render` の manifest に残る素材の sha256 と、`handoff.json` の `artifact.sha256` を照合する。`mv gate check` はそのまま使える。

既存制作 worker による描画と動画化は `model3d dance-recipe` の経路を使う（[手順](model3d-dance.md)）。
Blender での30秒描画と最終品質の全編出力は、まだこの経路で確かめていない。リポジトリ外の手順では、2026-11-06 に 32 秒を描いた。
汎用 `blender.run_script` を recipe の工程として通す確認も、まだしていない。

## Blender 5.2 安全化ヘルパー（`blender_adapter.blender5_safe`）

Blender 5.2.4 LTS（日本語 UI ロケール環境を含む）の台本で実際に踏んだ不具合を防ぐ共通部品。`bpy` はトップレベルで読まず、関数引数で受け取るため CI の単体テストおよび Blender 外の独立台本読み込み（`test_m3d_blender_scripts.py`）と両立する。

- `init_layered_action_channelbag(bpy, arm, action_name)` / `write_linear_fcurve(...)`: `action_slot` の手動代入（C レベルクラッシュ要因）と新規 Armature の `pose is None` を回避し、`keyframe_insert` でネイティブに Slot / Channelbag を初期化してから `foreach_set("co", ...)` ＋ `fc.update()` で LINEAR カーブを一括書込する（Blender 4.x の従来 `action.fcurves` へもフォールバック）。
- `clear_and_build_world_bg` / `clear_and_build_principled`: `nodes["Background"]` / `nodes["Principled BSDF"]` のような UI ロケール依存の辞書引きを使わず、`nodes.clear()` 後に型名で新規構築する。
- `make_vector_scale` / `make_vector_lerp`: `ShaderNodeVectorMath(SCALE)` の `inputs[1]` 誤結線（`Scale` が既定値 1.0 のまま残る白飛びバグ）を防ぎ必ず `inputs["Scale"]` へ接続する。また `ShaderNodeMix` の同名ソケット重複を避ける明示ベクトル補間を提供する。
- `enforce_quat_continuity(quats)`: `(N, 4)` / `(N, B, 4)` の隣接フレーム四元数内積を非負に揃え、補間・モーションブラーの 360 度フリップを防ぐ。
- `set_blended_alpha(material)`: EEVEE Next では `surface_render_method = "BLENDED"`、旧版では `blend_method = "BLEND"` を設定する。
- Mix ノード（`ShaderNodeMix`）は、同じ名前の入力（`A` など）を型ごとに持つ（Blender 5.2.4 で `A` は 4 つ）。`data_type` を先に決めてから `inputs["A"]` を引けば、使える方（FLOAT → `A_Float`・RGBA → `A_Color`・VECTOR → `A_Vector`）が返る。決める前に引いたソケットは `A_Float` のままで、あとで RGBA にすると使えない側（`enabled` が False）になり、つないでも色が入らない（2026-11-23 に確かめた。4 作目の 3D のダンスの台本は、先に決めてから引いていたので正しく動いた）。

**カメラ視線コリドー遮蔽ガード（`model3d.spatial`）**:
- `camera_corridor_mask` / `camera_corridor_attenuation`: カメラ〜注視点の線分を中心とする円柱コリドー内へ侵入する浮遊破片・床タイルを numpy だけで判定し、`smoothstep` 減衰係数 `[0.0 .. 1.0]` で視界遮蔽を防ぐ（3D 空間および XY 平面投影の両対応）。

**非線形モーションリミックス・分身軌道（`model3d.motion_remix`）**:
- `remix_pose_arrays`: 元モーション配列を非線形フレーズ表で再構成し、拍同期の速度マイクロランプ、バレットタイム減速、逆再生、非連続つなぎ目の全骨四元数 Slerp クロスフェード、連続フレーム符号整合を純 numpy で計算する。
- `apply_additive_bone_euler` / `compute_zero_g_trajectory` / `compute_clone_formation_tracks`: 既存骨格や布押し出し補正を壊さない局所回転上乗せ、無重力浮遊＆空中 360° スピン軌道、3D 分身リグの V 字展開・呼吸・収束スパイラル軌道を提供する。

**骨格軌跡連動 3D 光リボン（`model3d.bone_ribbon`）**:
- `build_ribbon_topology` / `compute_ribbon_vertices`: 骨格の 3D ワールド軌跡 `(N, 3)` とカメラ位置から、移動速度ゲート・カメラ正対ビルボード幅・尾端テーパー付きの動的リボン頂点と Quad UV トポロジーを純 numpy で計算する。
- `polyline_ribbon_vertices`: 任意の折れ線（`(n, 3)`）と節ごとの幅から、同じ帯の頂点 `(n * 2, 3)` を作る（`build_ribbon_topology(n − 1)` でメッシュにできる。幅の向きの計算は [光る帯と玉の四角形](#光る帯と玉の四角形fx_strips) と同じ）。

**確かめたこと**:
- CI では fake `bpy` による Blender 5.2 / 4.x 両系統、日本語ノード名ツリー、`SCALE` ソケット明示結線、四元数連続性の単体テスト、および `test_m3d_import_paths.py` / `test_m3d_blender_scripts.py` を確認。
- 実環境の Blender 5.2.4 LTS（`--factory-startup`）で Armature への Action 生成・F-Curve 評価・World/Principled 構築・`BLENDED` 設定・`make_vector_lerp` 結線のスモーク実行を確認。

## 限界・未検証

- **既存制作 worker の接続は限定的**。元 GLB／分割 GLB／FBX、採用済みのウェイト・法線・動作・補正を再利用する。
  `dance-recipe` は確認済みの配置と CLI 契約に対応し、任意の作品へ自動適応するものではない。素材・preset 自体は Git に入れない。
- **汎用 Blender 観測は、既定のシーン（4.5.15）と VRM のキャラの .blend（5.2.4。アドオンを有効にして）でだけ確かめた。生成器と別メッシュへのリギングは未検証**。既存 worker の再実行とは区別する。
- **別メッシュへの汎用転送（CorrespondenceMap の処理）・揺れ計算の途中状態からの再開・GLB 書き出しは未実装**。既存 VRM 1.0 の揺れのベイクは `vrm.bake_springs` で実装し、2 作目の 720 コマと 3 作目の 954 コマで照合した。動作検査は `check.animation` で実装し、作品群と試験動作で確認したが、しきい値と未使用動作への適用は実験的。部品ラベルの転送は、同じ座標の別メッシュへ面の重心の近さで移す `mesh.transfer_parts` の範囲に限る。
- 面領域は部品の名前と頂点構成に結び付けて持ち、工程の `select` で対象の面だけを加工できるが、**部位の自動分割（髪・顔・衣装を分ける処理）は無い**。
  部品の区別は名前だけで、同じ名前・同じ頂点構成の別のメッシュ（取り違えたファイルなど）は見分けられない。
  面は連結成分・面番号・面集合・箱で人が選び、確認も人が行う。融合した部位を分けるのは形状修正の工程（Blender の手作業と `model3d edit`）。
  構成を変えた後の付け替えは、結合の正確な面の対応だけに対応する（リトポロジーなど対応の無い変更では、面領域を作り直す）。
- 設定画からの生成は、この環境では代役の生成器（輪郭の箱）だけで通した。本物の生成器（TripoSR）はユーザーの環境の記録が根拠で、
  ここでは未検証。多視点の生成器は未接続。
- 貫通の補正は凸の衝突相手で確かめた。凹んだ相手では 1 回の補正で出ないことがあり、`residual_inside` に残る。
- `render.prepare_push` の頂点の組は、上の端より下で種とつながった部分を選ぶ。布が上の端より下でほかの部品（スカートなど）とつながっていると、その部品も入る（試作の帯は、結び目より下では離れた 2 枚）。
  当たりの写しの穴は、開いた辺の輪をすべてふさぐ。面が 3 枚以上集まる辺などで閉じ切らなければ、開いた辺の数を報告するだけで止めない。
- 結合の候補は格子で絞るが、大きなメッシュ（数十万頂点）での速さは未測定。内外の判定は格子で候補の三角形を絞る（総当たりと同じ答え）。
  貫通の補正の行き先（いちばん近い面）を探す所は 点 × 三角形 の総当たり（帯と大腿のような部品どうしを想定）で、全身どうしの補正には
  空間分割（BVH など）が要る（未実装）。
- `check.penetration` は、相手の中に入った頂点を数えるだけ。脚がまるごと布を通り抜ける失敗は数に出ない（`render.part_colors` の絵で見る）。
- 衣装を布で動かす工程（`cloth.skirt_proxy`・`cloth.smooth`）は、1 体（2 作目の VRM のダンスのキャラ。体と裾が別のメッシュ）で
  しか確かめていない。体と服が 1 つのメッシュのキャラでは、島ごとに選ぶ当たりの作り方はそのままでは使えない。筒は腰を通る縦の軸から
  放射状に測るので、腰に巻く形の裾を想定している。布の輪郭線（`look.toon` の `outline`）は付けられない。
- モデルを手直しする工程は、部位を島（辺でつながった頂点の集まり）と頂点グループで選ぶ。体と服が 1 つのメッシュの
  モデルでは、島で部位を分けられないので使えない（場所・色・光線で面を選ぶ方法は入れていない。2026-11-17 ユウ判断）。
  `model.body_shape`・`model.smooth_skin`・`model.clean_skin`・`model.face_shape` 等は 2 作目の分離したメッシュのキャラで確認。
  `model.twist_bones` は 2 作目と 3 作目、`model.neck_junction` は 3 作目で確認した。体と服が一体のモデルへの一般化は未確認。
- 骨で変形した後の一般的な頂点移動 `deltas` を、スキニングの逆変換で基準形へ戻して書く汎用工程は未実装。`model.face_shapes` が基準形で計算した世界座標の変位は、`model.shape_keys` で物体座標へ変換し、相対形状キーに書ける。これは骨の変形を逆算する処理ではない。
- `plan.shots` の回り込みのショットは、試作どおり (0, 0, 1) との外積で右の向きを決めるので、真上・真下から見るカメラは作れない（止める。真上から見下ろすカメラは `plan.orbit` で作る）。2D の絵コンテから写せるのは、1 枚の絵の仮の部屋（`render.box_room` と同じ置き方）に置いたカメラだけ。
- 効果の要素（`render.passes` の `fx`）では、メッシュでない人物の物体（カーブの髪など）は描かない（黒にできないので、その物体の奥の効果は隠れない）。効果の形（`fx_shapes`）は 1 体（2 作目の VRM のダンス）の火の粉・はじけ・火花・帯・輪の 5 つで確かめた。粒の数を大きく増やしたときの速さは測っていない。
- 演出の出来事の部品（`fx_strips`・`fx_events`・`fx_bundle`・`fx_surface`・`fx_particles`・`fx_charge`・`fx_sequence`・`fx_lights`・`fx_blender`）は、1 作（4 作目の 3D のダンス）の形の照合の元（Blender なしの数）と、合成の場面の Blender 5.2.4 で確かめた。作品の描いたコマとの絵の照合はしていない（本番のコマが残っていない）。面を走る枝分かれは水平の面だけ（斜めの面・壁は作っていない）。骨の点は、いちばん近い標本で読む（ならしも補間もしない）。足し算で光る物の描く順番（BLENDED）の乱れと被写界深度との重なりは、作品で見た目で確かめただけ。毎コマ作り直すメッシュに、動きのぶれが正しく付くかは見ていない（作品では切ったまま）。白黒のコマは、舞台が 1 つのコレクションにまとまっていることを使う。
- 要素別の簡易描画（`render.passes`）は、同じ入力でも描くたびに、人物と陰影の要素がわずかに変わる（Blender 5.2.4 の Workbench。[要素別に描く](#要素別に描く)の数字）。表情は頭の絵の差し替えに加え、既存形状キーの時刻キー `values` を直線補間して描ける。3 作目では 909 コマの値と 8 コマの描画を確認した。口の形の自動生成や、音声からの口パク推定は含まない。
- テクスチャへの写しは座標軸に沿った正射影だけ（斜めの視点・透視は無い）。見え隠れは渡したメッシュの中で決め、別の部品の陰は
  `texture.view_parts` の手前の部品の地図を `occlusion` に渡したときだけ見る（地図の画素の中心の奥行きで比べる。同じ部品に溶け込んだ
  髪などは分けられないので、顔の面領域を `select` で選ぶ）。目印の自動検出・枠の自動の決め方（色合わせの枠は手で書く）・
  複数の視点を 1 工程で混ぜる処理は無い（視点ごとに工程を重ねる）。uv を共有する面（左右で同じ uv など）は、見える面と隠れた面の重みを平均する。
