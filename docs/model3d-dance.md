# 既存の3D制作レシピを model3d から実行する

`model3d dance-recipe` は、回収済みの制作 CLI を別プロセスで呼ぶ5工程の RecipeGraph を作る（顔を model3d の工程で写す設定では、目元投影の 1 工程が model3d の 3 工程に替わる）。
元 GLB・分割 GLB・FBX からの再構築、設定画の投影（既存 worker の目元投影か、model3d の顔の工程）、質感の適用、舞台と動作の描画、動画化を
`model3d run` の入力ハッシュ・工程状態・出力記録に接続する。既存のウェイト・法線・動作・補正データを再利用する。
完成済みキャラクターの blend は再構築の入力にしない。

## 準備と実行

この接続は、確認済みの非公開 worker 一式の配置と CLI 契約に対応する。
worker・素材・preset は同梱しない。任意のキャラや任意の Blender スクリプトを自動解析して接続するものではない。
`--bundle` は `rebuild_recipe.py`、`face_project.py`（従来の顔の設定のときだけ）、`apply_texture.py`、`dance_tool.py`、
`render_stage.py`、`run_upstream_blender.py` があるディレクトリ。
その下の `upstream/`、`source_assets/`、参照画像を使用する。素材・出力は Git の外か `projects/` に置く。

```sh
uv sync --locked --extra analysis --extra fx --extra model3d
uv run model3d dance-recipe projects/character-production \
  --bundle /private/dance_tool \
  --preset /private/dance_tool/presets/character.json \
  --face-config /private/dance_tool/presets/reference.json \
  --python /private/recipe-venv/bin/python \
  --blender /opt/blender-4.5.15/blender \
  --frames 1:147:3 --profile preview --threads 4
uv run model3d run projects/character-production/recipe.json
uv run model3d handoff projects/character-production/model3d_runs/RUN_ID \
  assemble/video projects/character-production/handoff.json
```

worker 用 Python は model3d と別環境にする。確認に使う worker の固定依存は
numpy 1.26.4、scipy 1.17.1、Pillow 10.1.0、opencv-python-headless 4.11.0.86。
Blender 4.5.15 LTS、ffmpeg と ffprobe も必要。生成器や有料 API は呼ばない。

worker が再利用する採用済みのファイル（部品の重み・法線・連鎖、atlas、動きの blend など）は、worker 一式の preset の
`reused` に `upstream/models/mv` からの相対パスで並べる（必須。名前は非公開の一式のもので、このリポジトリには書かない）。
どれも `upstream/models/mv` の中のファイルでなければならず、入力一覧にハッシュ付きで載る。

worker が呼ぶ動画ツールは、worker 一式の preset の `video_tools` で宣言できる（既定は `["ffmpeg", "ffprobe"]`）。
ffprobe を呼ばない worker なら `["ffmpeg"]` とし、接続は宣言したツールだけが PATH にあることを確かめて版を記録する。
worker の Python は UTF-8 モード（`PYTHONUTF8=1`）で動かす。encoding を指定しない JSON・テキストの読み込みが、
Windows で ANSI のコードページにならないようにするため。

作成先には空のディレクトリを指定する。`connector.json`、`input_inventory.json`、`recipe.json` ができる。
入力一覧は worker、元資産、再利用する補正データ、設定画、preset、実行ファイルをハッシュ付きで記録する。
実行前後に一致を確認し、Python のパッケージ版・Blender 版・動画ツールの版も記録する。
これは実行ファイルと宣言された入力の追跡であり、OS・ドライバー・全共有ライブラリーを固定するコンテナーではない。

## 工程の入出力

| 工程 | 入力と処理 | 主な出力 |
|---|---|---|
| `rebuild` | 原料と採用済み補正から、既存 worker の12工程を実行 | `character.blend`、部品形状・法線 NPZ、出自・完了記録 |
| `project_reference` | 再構築した形状・法線と設定画で目元を投影。範囲外は既存の補正画像を保持 | 統合テクスチャ、投影・統合報告 |
| `apply_texture` | 再構築モデルの対象画像を置換し、画像を blend に格納 | `character.blend`、画像適用報告 |
| `render` | 既存の動作・舞台・帯補正と選択した設定で描画 | PNG 連番、各画像のハッシュを含む描画 manifest |
| `assemble` | 検証済み連番を動画化 | `dance.mp4`、動画メタデータ |

各工程は `stage_receipt.json` とログも残す。RecipeGraph の出力は各工程直下に置き、
worker の詳細な中間データは `job/` に保持する。動画化は独立した作業場所を使い、完了済み描画工程を変更しない。
連番はその作業場所へシンボリックリンクで渡し、作れない環境（権限の無い Windows など）ではハッシュを照合したコピーにする。

5工程は `external.run` で毎回実行する。新しい run に古い中間結果を混ぜず、失敗した工程で止まる。
（model3d の顔の工程は、入力の sha256 まで同じなら前の run の結果を再利用し、`stage_receipt.json` は残さない。）
入力変更・12工程の欠落・必要な出力の欠落・画像ハッシュ不一致は失敗として記録する。
長時間処理の途中再開機能ではない。

既存 worker の目元投影は推定に基づくため、`project_reference` は `needs_review`、run 全体も `needs_review` になる。
後続の描画と動画化は続行する。人による採用は自動記録しない。
`external.run` の任意パラメーター `review_note` は、この未解決事項を `unresolved` に残すために使う。

## 顔を model3d の工程で写す（`model3d-face-projection/0.1`）

`--face-config` に `"schema": "model3d-face-projection/0.1"` の設定を渡すと、目元投影の worker（`face_project.py`）の代わりに、
model3d の操作で設定画の顔を写す。再構築・質感の適用・描画・動画化はそのまま。この経路では `face_project.py` を使わず、入力一覧にも載せない。
`schema` が違う設定や JSON のオブジェクトでない設定は止める（`schema` の無い設定は従来の worker の設定として読む）。

| 工程 | 操作 | 内容 |
|---|---|---|
| `face_mesh` | `mesh.import_parts` | 再構築した部品形状・法線の NPZ から、`part_name` の部品を読む |
| `face_mask` | `texture.draw_mask` | `mask` の図形を参照画像の大きさで描く（`mask` が無ければこの工程は無い） |
| `face_projection` | `texture.project_reference` | `reference` を `projection` の視点と目印で合わせ、採用済みの atlas（preset の `face_atlas`）へ写す。`overlay` で位置合わせを確かめる |

塗る atlas は、`apply_texture` が置き換える採用済みの画像で、worker 一式の preset の `face_atlas` で名前を指す（`reused` に並べたものの 1 つ。顔の外の画素は採用済みのまま残る）。顔の設定の側では atlas を選べない。
`reference` は worker 一式の中のファイル。どちらも入力一覧にハッシュ付きで載り、recipe の入力 `face_atlas`・`face_reference` になる。
`apply_texture` は `face_projection` の出力を受け取る（従来の設定では `project_reference` の出力）。
部品名・画像・マスク・目印の値はキャラごとの非公開の設定に置き、Git には入れない。値の例（作り物）:

```json
{"schema": "model3d-face-projection/0.1", "part_name": "head_part",
 "reference": "references/head_sheet.png",
 "mask": {"feather": 3.0, "shapes": [{"ellipse": [200, 300, 30, 15], "angle": 8}]},
 "projection": {"view": {"from": "-Y", "center": [0.0, 1.5], "span": 0.3, "size": [1000, 1000]},
                "alignment": {"model": [[400, 600], [600, 600]], "reference": [[200, 300], [300, 300]]}}}
```

`projection` と `mask` は工程と同じ確かめ方（`texture.project_reference`・`texture.draw_mask` の規則）で、recipe を作る前に確かめる。
知らないキー（入れ子の綴りの誤りも）、視点・目印・マスクの誤り、worker 一式の外やフォルダーの `reference` は、長い再構築の前に止まる。
写った画素があれば run は `succeeded` になる（1 画素も写らなければ `needs_review`）。目印を自動検出の値のまま使うときなど、
人の確認が要ることを残すには `projection.review_note`（空でない文字列）を書く。その工程と run は `needs_review` になり、後ろの工程は続く。
採否は `overlay` と描画を見て人が決める。見え隠れは `part_name` の部品だけで決める（目元投影の worker は全部品で決めていた）。
この経路は手前の部品の地図（`texture.view_parts`）を作らないので、`projection.occlusion_part` は recipe を作る前に止める（工程まで進むと、長い再構築の後に止まるため）。
顔の前に別の部品（別部品の前髪・眼鏡など）があるキャラでは、その部品の色を顔へ写すことがある。

2026-11-03 にユーザーの Windows の PC（CPU）で、実素材と非公開の顔の設定を使い、7 工程（2 コマ、1080×1920・64 サンプル）を
約 171 秒で通した。run 全体は `succeeded`。同じ値を手で通した描画と比べ、255 段階で 8 を超える差は 0 画素（最大 7）だった。

## 範囲

今回接続するのは既存原料からの再構築と既存動作の描画。設定画から新しい高品質形状を生成する機能、
別トポロジーへの汎用リギング、多視点融合、30秒の動作検査、最終品質の全編出力は別の課題として残る。
Orpheus には動画と `handoff.json` を渡し、`mv_pipeline` を import しない。

## 実行確認（2026-11-03）

Linux / Blender 4.5.15 LTS / CPU で実素材を使い、生成した RecipeGraph の入口から5工程を実行した。
再構築内部の12工程が完了し、360×640・49フレーム・10fps・4.9秒・無音の MP4 と handoff を出力した。
再構築・画像適用・描画・動画化は `succeeded`、目元投影と run 全体は `needs_review`。
204入力ファイルを宣言し、各工程でハッシュを実行前後に確認した。原料や非公開 preset は公開 Git に含めない。

元r6の完成モデルを比較用に別途回収し、本体・補填14メッシュの頂点・面・UV・意味属性と、元FBX由来の骨・重み配列が一致した。
132骨×147フレームの姿勢行列差は最大 `5.364418e-7`、6フレームの変形サンプル差は最大約 `0.000477 mm`。
衝突用proxyには局所最大約 `2.738 mm` の差が残る。入力・比較基準243ファイルの変更はなかった。

前回の統合モデルによるプレビュー49枚と、今回の復号後RGBを比較した。23枚は全画素一致し、残りを含む全体では
11,289,600画素中36画素に差があった（1枚あたり最大2画素、最大チャンネル差96/255）。
全画素一致とは扱わず、差の原因も特定していない。同じプレビュー条件の比較であり、最終解像度や別環境の再現性の保証ではない。

Python テストは **806 passed / 1 skipped**（新しい接続テスト21件を含む）。skip は従来の2Dダンスで、
この環境の画素比較基準が無いため。`pnpm typecheck` と `pnpm test`（2件）も成功した。
初回実行で見つかった入出力プレースホルダーの同名衝突を修正し、代役 worker を使って
CLI → RecipeGraph → 5つの別プロセスを実行する回帰テストを追加した。実素材も新しい run で最初から通し直した。

既存の `mv demo → render → verify` は、この環境の Node `os.networkInterfaces()` が
`uv_interface_addresses` エラーになるため render で停止し、verify は未実行。
今回の Blender 描画とは別系統であり、既存 Remotion 経路のローカル検証成功とは扱わない。

## Windows の PC での実行確認（2026-11-03）

Windows 11 Home（スマート アプリ コントロールはオン）・Blender 4.5.15 LTS（Windows 版）・CPU で、同じ worker 一式を実素材で通した。
worker の Python は model3d と同じ環境（numpy 2.3.5・scipy 1.18.1・Pillow 13.0.0・opencv-python-headless 5.0.0.93。上の固定版とは違う）、
ffmpeg は imageio-ffmpeg の 7.1。ffprobe は無い。

- 詰まった所: ffprobe が無く、接続の準備で止まった。worker 側はコマ数を ffmpeg で数え、連番のシンボリックリンクを作れなければ
  照合済みのコピーにする版に直し（非公開の一式。Git に入れない）、preset に `video_tools: ["ffmpeg"]` を宣言した。
- 5 工程を約 7.2 分で実行した（再構築の 12 工程は計約 143 秒）。目元投影と run 全体は `needs_review`、ほかは `succeeded`。
  動画は 49 コマ・10 fps・4.9 秒・無音で、ffmpeg で復号したコマ数も 49。
- 再構築したモデルを、採用済みの r6 の完成モデル（上と同じ比較基準）と比べた。メッシュ 27 個の頂点・面・UV・意味属性、
  6 コマの変形後の全頂点（162 件）、132 骨・147 コマの姿勢行列とレスト行列がすべて一致した（差 0）。
- 前回の Linux のプレビュー 49 枚と、今回の PNG を比べた。全画素一致は 0 枚で、画素の 46〜68% に差がある。ただし 255 段階で
  2 を超える差は 9〜25%、8 を超える差は 1% 未満、64 を超える差は 1 枚あたり数画素で、並べた画像と 8 倍の差分では形や配置の違いは
  見えなかった。描画の陰影の細かな差で、Linux のソフトウェア描画との違いによると推定する（原因は特定していない）。
