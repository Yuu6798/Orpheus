# 長髪を腕と袖からよける（`vrm.avoid_hair_arm`）

[`vrm.bake_springs`](model3d.md#揺れものを焼くvrmbake_springs) などで動きを焼いた .blend を受け取り、長髪をよける回転を足す。Blender が必要。VRM Add-on の機能を呼ぶ必要がある場合は `addons` と `blender_extensions` を指定できる。

```json
{
  "id": "avoid_hair_arm",
  "operation": "vrm.avoid_hair_arm",
  "inputs": {"blend": "$steps.bake.blend"},
  "parameters": {
    "armature": "Character",
    "chains": [{"object": "Hair", "bones": ["HairRoot", "HairMiddle", "HairTip"],
                "direction": 1, "outward_ratio": -0.2}],
    "colliders": [{"object": "Body", "groups": ["UpperArm.L", "UpperArm.R"]}, {"object": "Sleeves"}],
    "sample_step": 3
  },
  "outputs": {"blend": "hair_adjusted.blend", "report": "hair_arm.json"}
}
```

- `chains` の `bones` は四元数の回転を使う骨を親から子へ 3 本。髪の面は元メッシュの対応する頂点グループで選ぶ。`groups` を指定すると別名や毛先の追加グループも選べる。同じメッシュの面が複数の鎖に当たる場合は、面の頂点で重みが最も大きい鎖へ割り当てる。同じ重みなら `chains` の並び順を使う。
- `direction` は根元をよけるローカル X 回転の符号（既定 `1`、逆は `-1`）。`outward_ratio` はその角度に対するローカル Z 回転の比率（既定 `0`）。中間と毛先の X はそれぞれ根元の `-0.45` 倍と `-0.25` 倍を加えて S 字に垂らす。リグの軸に合わせて方向を指定する。
- `colliders` は腕や袖のメッシュの名前。`groups` を指定したときは、その頂点グループを持つ面だけ使う。選ぶ頂点の重みは `min_weight`（既定 `0.25`）より大きいもの。髪と衝突相手に同じ面を指定すると止まる。
- Blender シーンの開始と終了を含め、`sample_step`（既定 3）の間隔と `extra_frames` で指定したコマで角度を探す。`angle_step_deg`（既定 4°）ずつ `max_angle_deg`（既定 56°）まで試し、交差の最も少ない最初の角度を選ぶ。改善する候補が無ければ 0° にする。
- コマの間を補間し、前後 `envelope_frames`（既定 5）にある角度の最大値を使ってから `smooth_sigma_frames`（既定 5）のガウス平滑化を行う。補正を加える鎖の 3 本に全コマのキーを打ち、再生して確かめる。元の入力ファイルには書かない。
- 計測時は評価済み頂点を `matrix_world` で世界座標へ移す。既知の MToon 輪郭線の Geometry Nodes と、`outline_modifiers`（メッシュ名 → Geometry Nodes モディファイア名の配列）で指定した輪郭線を一時的に隠し、失敗時も `show_viewport` を元に戻す。残るモディファイアが面や頂点の対応を変える場合は止まる。

`report`（`model3d-hair-arm/0.1`）は、全整数コマの焼く前と焼いた後に BVH が検出した三角形の組の数と適用角度を出す。探索したコマの角度とそのときの交差数も `search` に残る。探索するコマを間引いても、補正後の確認は全整数コマで行う。`after_pairs` が残れば `needs_review`。有限個の角度を試すため、すべての交差を解消できるとは限らない。サブフレームの見え方や三角形同士の交差を伴わない完全な包み込みは報告の対象外。

Blender 5.2.4 の合成シーンでは、髪と袖に別々の物体変換を与えた 5 コマの交差が延べ 15 組から 0 組になった。保存したファイルを開き直し、補正した鎖の回転、他の骨の動き、輪郭線の表示設定を確認した。
