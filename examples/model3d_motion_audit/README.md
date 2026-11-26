# VRM の動きを演出前に見る

最終の動きが入った `.blend` から固定カメラで無演出の確認動画を描き、参照動画と横に並べる。気になる時刻では、正面・左右斜め・真横のシートも作る。

```sh
uv sync --locked --extra model3d
uv run python examples/model3d_motion_audit/run.py projects/dance/final.blend projects/dance/ref.mp4 \
  --blender /opt/blender/blender --out projects/dance/audit \
  --motion-fps 24 --fps 30 --start 1 --frames 120 --at 0.5 2 3.5 \
  --armature Armature --hips-bone 'mixamorig:Hips'
```

- 参照動画の頭を `.blend` の `--start` と揃える。`--motion-fps` は `.blend` のモーションの fps、`--fps` は確認動画の fps。
- `--location` と `--target` は固定カメラの位置と注視点（世界座標、m）。`--resolution` と `--lens` も変更できる。全身が入る画で確認する。
- `--at` は監査区間の頭からの秒数。シートは時刻が行、方位が列の順で、方位は正面・左斜め・右斜め・右真横。
- `--out` に新しいフォルダを指定する。`comparison.mp4` と `angles.png` ができる。`audit.json` に比較したコマ数と下見の記録が残る。参照が短ければ、並んでいる間だけを比較する。

描画には `render.passes` の白い人物パスを使う。視点の下見は `motion.bone_path` → `plan.scout`、動画化と並べ方は `render.assemble` と `mv sheet` と同じ処理を使う。
確認用の簡易描画なので、材質の見え方、エフェクト、画角による遮りは、本番カメラの素材も描いて確認する。
