# presets

キャラ（資産）ごとの対応表と値。**実キャラの preset は Git に入れない**（キャラ資産・動画・重みと同じく別に保持し、
AssetManifest と RunRecord に sha256 で参照を残す）。ここには合成素材のデモ用だけを置く。

- `example_humanoid.json`: `model3d demo` が書くものと同じ。部品 ID → 役割、Mixamo 風の骨名 → 骨の役割、役割ごとの値
  （帯の余裕 4 mm・減衰 7 mm は仮の値）、必須の役割。
- 役割の語彙は `src/model3d/semantic.py`（`PART_ROLES` / `BONE_ROLES`。拡張は `x.` で始める）。
- 他の体格では身長比から初期値を作り、局所の衣装の厚みと形を見て調整する。単純な比例で妥当になるとは仮定しない。
