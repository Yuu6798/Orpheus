# SAM 3D Body のコマ数をそろえる

`motion.resample_keypoints` は、SAM 3D Body の MHR70 の npz を等間隔の別のフレームレートにそろえる工程。
動画を描くフレームレートと点を抽出したフレームレートが異なるとき、[点の下ごしらえ](model3d.md#推定した-3d-の点を整える) の前に置く。

```json
{
  "id": "resample",
  "operation": "motion.resample_keypoints",
  "inputs": {"keypoints": "$inputs.motion"},
  "parameters": {"target_fps": 30},
  "outputs": {"keypoints": "keypoints-30fps.npz", "report": "resample.json"}
}
```

- 入力の fps は npz の `fps` から読む。古い npz に `fps` が無いときは `source_fps` を渡す。両方があるときは一致させる。可変フレームレートの動画は点を抽出する前にそろえる。
- コマ数は元のコマ数 × 新旧 fps の比を最も近い整数に丸め、最低 1 コマにする。点とカメラの並進、および入力にあれば 2D の点と枠を PCHIP（各成分の単調性を保つ三次補間）で補間する。入力末尾より後のコマには最後の値を置く。
- `valid` は補間に使う両方が真なら真、`used_full_image_box` はどちらかが真なら真。補間区間の両端が有効でも、三次補間の傾きに無効な隣接コマが使われる区間は両端のみで線形補間する。これらの鍵が無い場合は足さない。ほかのコマごとの配列は最も近い入力コマから取り、`cam_int_fixed` とスカラーはそのまま保つ。
- `t` と `frame_index` は出力側のコマにそろえる。時刻は入力の最初の値から始める。入力に時刻が無い場合は 0 秒から始める。点や枠に欠損値や非有限値がある場合はエラーとして止める。
- 次の工程では `motion.prepare_keypoints` の `inputs.keypoints` に `$steps.resample.keypoints` を渡す。任意の `report` に、変換前後のコマ数・fps・入力にあるフラグの件数を記録する。

## 実素材確認

実素材確認（2026-11-27）: 実ダンス動画から抽出した SAM 3D Body MHR70 npz（300 コマ・30 fps・有効 300 コマ）へ `target_fps: 60`（600 コマ・間隔 0.016667 秒）および `target_fps: 24`（240 コマ・間隔 0.041667 秒）を実行。独立の NumPy 検算で、60 fps 出力の偶数コマと元 30 fps の 3D 点誤差 0.0 m、PCHIP 補間中間コマのオーバーシュート 0.0 m、`cam_int_fixed` のビット一致を確認し、そのまま後段の `motion.prepare_keypoints`（600 コマ・70 点）へ接続できることを確かめた。
