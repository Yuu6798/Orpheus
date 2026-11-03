# JSCAD の原本出力（押し出し・ねじりの照合用）

`cases.json` は、2026-11-03 の再構成の実験で、**無変更の JSCAD 配布コード**（jscad/OpenJSCAD.org、commit `0e2c05bb…`、MIT）の
`extrudeLinear` が作った合成の試験部品（6 件）と、不正な steps の拒否（1 件）。キャラの資産ではない。
テストは `model3d.mesh.extrude_profile` の出力を、三角形の数・頂点集合・bounds・面積・体積（許容差 1e-12）で照合する。
三角形の分割の一致や JSON のバイト一致は求めない（同じ実験の比較規約と同じ）。
