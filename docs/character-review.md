# キャラクターの横顔・動き・配布モデルを比べる

モデルを直したあと、修正前と同じポーズを同じ画角から開くためのローカル検査。Blender の原本と配布 GLB の差から、確認したいコマを選ぶこともできる。両コマンドとも `model3d` extra と Blender が必要。比較ページはブラウザでローカルに開く。

## 立ち絵と修正前後を並べる

外見の基準には向きごとの立ち絵を、振付の基準には元の動画を指定する。`left` はキャラクターの左側。右側にしかない髪飾りも見分けられるよう、左右の絵は個別に指定する。未指定の向きは空欄になる。複数の向きを収めた設定画は、該当する向きを切り出して渡す。

```sh
uv run model3d character-review views.json --blender /path/to/blender --out review
```

`views.json` の例。相対パスの基準は設定ファイルのあるディレクトリ。骨名・メッシュ名は各版に合わせる。

```json
{
  "before": {"path": "before.blend", "armature": "Rig", "left": "upper_arm.L", "right": "upper_arm.R", "anchor": "chest"},
  "after":  {"path": "after.blend",  "armature": "Rig", "left": "upper_arm.L", "right": "upper_arm.R", "anchor": "chest"},
  "references": {"left-front": "art/lf.png", "left": "art/l.png", "left-back": "art/lb.png",
                 "right-front": "art/rf.png", "right": "art/r.png", "right-back": "art/rb.png"},
  "motion_reference": "motion.mp4",
  "scale": 0.8, "offset_z": 0.05, "size": 512,
  "events": [
    {"id": "standing", "label": "立ち姿", "frame": 1, "rest": true},
    {"id": "blink", "label": "まばたき", "region": "目と口",
     "peak": {"object": "Face", "shape_key": "Blink", "range": [1, 24]},
     "disable": {"object": "Face", "shape_key": "Blink"}},
    {"id": "turn", "label": "ターン", "region": "左前腕", "focus_view": "left", "frame": 36,
     "framing": {"anchor": "forearm.L", "scale": 0.45}}
  ]
}
```

- 3D の2列は左右それぞれ斜め前45°、真横90°、斜め後ろ135°を描く。Z が上のリグを使い、左右の骨の位置から身体の向きを求める。修正前のカメラの動きを両リグのレスト座標に合わせるので、修正後の腕のずれも見比べられる。`scale` は正射影の画角の幅（メートル）、`offset_z` は注視点の高さの調整（メートル）。同じ開始フレーム・fps と同じ振付を用意する。場面のライティングと画面合成はレビュー用のライティングに置き換える。
- 関連するメッシュを限定するには各モデルに `"meshes": ["Body", "Coat"]` を追加する。`rest` はアーマチュアをレスト姿勢にする。表情を含め、その他のアニメーションの評価時刻は指定したフレーム。
- 顔や前腕だけを拡大するにはイベントの `framing` で `anchor`（修正前モデルの骨名）、`scale`、`offset_z` を個別に指定する。修正前で決めた注視点と倍率を修正後にも使うため、修正で手の位置がずれた場合も見える。
- 腕組み・ターン・腕上げは振付を見て `frame` を選ぶ。`peak` は修正後の指定区間を評価し、表情値が最大となる最初のコマを選ぶ。区間内で値が一定なら範囲を変える。閉眼時にまつ毛が口に出るような問題は、目と口がともに映る画角で対照画像を確認する。
- `disable` は修正後の同じ姿勢で1項目を切った列を足す。形状キーのほか、`{"object":"Body","modifier":"Outline"}` または `{"object":"OutlineMesh","hide":true}` を指定できる。`focus_view` は特定の向きの比較行へ直接移動するリンクになる。
- 別の検査で測った値はイベントの `measurements` に `[{"label":"手と衣装の交差","value":8,"unit":"三角形ペア"}]` の形式で添えられる。ページには入力した測定値として表示する。交差が衣装の内側に隠れる場合もあるため、指定時刻の写真で輪郭や指先への影響を確認する。

標準出力の `index` に各イベントのシーン開始からの秒数、フレーム、部位、画像へのリンクが並ぶ。立ち絵は同梱し、振付動画には元のローカルファイルへのリンクを貼る。各回の画像とカメラ行列（`views/cameras.json`）は個別のフォルダに保存する。

## 実際の配布 GLB を読み戻す

```sh
uv run model3d character-roundtrip portable.json --blender /path/to/blender --out roundtrip.json
```

```json
{
  "native": "after.blend", "delivered": "performance.glb",
  "frames": [1, 12, 36, 48], "native_start": 1, "delivered_start": 0,
  "sections": [{
    "label": "左前腕", "at": 0.5, "half_width": 0.1,
    "native": {"mesh": "Arm.L", "bone": "forearm.L", "tip": "hand.L"},
    "delivered": {"mesh": "Arm.L", "bone": "forearm.L", "tip": "hand.L"}
  }],
  "expressions": [{
    "label": "まばたき",
    "native": {"mesh": "Face", "key": "Blink"}, "delivered": {"mesh": "Face", "key": "Blink"}
  }]
}
```

原本の各 `frame` を秒に変換し、単一のアニメーションを含む GLB を空の Blender シーンに読み込んで同じ時刻を評価する。原本は保存時のアクティブシーンを優先し、そこに指定メッシュが揃わない場合は全メッシュが揃う唯一のシーンを使う。`native_start` に対応する配布側のフレームを `delivered_start` に指定する。GLB 内のキーが 0 秒より後に始まる場合、例えば最初のキーが 0.1875 秒で読み込みが 24 fps なら `4.5` になる。読み込みには原本の Blender の整数 fps を使い、元の時計の fps_base は秒への変換で反映する。測定時刻がクリップ全体の時間範囲を外れると停止する。個別のチャンネルが先に終わる場合は GLB に従って末尾の値を保つ。

レスト時の骨の長さを単位に、`at` が示す位置の前後 `half_width`、半径 `max_radius`（既定 0.65）以内の、指定頂点グループに重みを持つ頂点を選ぶ。ねじり用の骨などを含めるには各側のバインドに `"groups":["forearm.L","twist.L"]` を足す。各モデルで同じ選び方をし、選んだ頂点をアニメーション中も追う。骨軸に垂直な面に投影した重心からの平均距離をレスト骨長で割り、全体の単位や大きさの違いを吸収する。

レポートにはコマ別の値、原本に対する配布版の断面比、形状キーの差（配布版 − 原本）、測定したコマのうち差の大きいコマと秒が残る。表情には相対形状キーの評価済みウェイトを使い、ミュート時は 0 とする。Basis、絶対形状キー、形状キーのピン留めプレビューは対象外。絶対的な正しさや動作全体の無貫通は判定しない。形状キーの値が一致していても、実際に瞳が隠れるかは比較画像で確認する。読み戻しに使った Blender の版、原本のシーン名、両入力の SHA-256 も記録する。

その結果から同じ部位を開くには、`views.json` に `"roundtrip_report": "roundtrip.json"` と次のイベントを足す。修正後モデルがレポートを測った原本と同じ内容であることを確認してから撮影する。

```json
{"id":"forearm-volume","label":"配布時の太さを確認","region":"左前腕","focus_view":"left",
 "finding":{"kind":"sections","label":"左前腕"}}
```

この比較ページの画像は修正前後の **Blender 原本**。レポートに測定シーンが記録されていれば修正後の描画に使う。GLB の結果は同じコマに付く測定メモで確認できる。`kind: "expressions"` で表情の差からも選べる。比較ページから全測定値の JSON を開ける。JSON の秒数は読み戻しの同期起点 `native_start` を基準にする。比較に使った原本の名前、SHA-256 と Blender の版は `sources.json` に残る。

## RecipeGraph から使う

`check.character_roundtrip` は `spec` を入力、`report` を出力とする。`review.character_views` も同じ名前を使い、出力 JSON の `index` が比較ページを指す。前段の結果は視点比較の入力 `"roundtrip": "$steps.measure.report"` に直接渡せる。どちらも `model3d run ... --tool blender=/path/to/blender` で使用する。画像や外部モデルも参照するため毎回実行する。

```json
{
  "schema_version": "model3d-recipe/0.1", "name": "キャラクターの配布確認",
  "inputs": {"delivery": {"path": "portable.json"}, "views": {"path": "views.json"}},
  "steps": [
    {"id": "measure", "operation": "check.character_roundtrip",
     "inputs": {"spec": "$inputs.delivery"}, "outputs": {"report": "measurement.json"}},
    {"id": "review", "operation": "review.character_views",
     "inputs": {"spec": "$inputs.views", "roundtrip": "$steps.measure.report"}, "outputs": {"report": "review.json"}}
  ]
}
```

すでに描画してある動画同士を並べる場合は [同時刻の比較シート](sheet.md) を使える。作品固有の骨名や演技の時刻は各作品の設定に置く。
