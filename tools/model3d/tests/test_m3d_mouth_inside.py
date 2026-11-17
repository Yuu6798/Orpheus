"""口の中の色を塗る（texture.mouth_inside）: 合成の口（顔の面・その奥の唇の裏の袋・歯と舌の部品）で、塗る所と触らない所（Blender なし）と、
工程の受け渡しと悪い設定。3 作目の VRM のダンスの頭と絵での突き合わせは docs/model3d.md（リポジトリ外の素材なので CI には無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import mouth
from model3d.io import ToolError, atomic_json

SIZE = 64


def quad_grid(x0, x1, z0, z1, y, nx, nz):
    xs, zs = np.linspace(x0, x1, nx + 1), np.linspace(z0, z1, nz + 1)
    co = np.array([[x, y, z] for z in zs for x in xs])
    tris = []
    for j in range(nz):
        for i in range(nx):
            a, b, c, d = j * (nx + 1) + i, j * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i
            tris += [(a, b, c), (a, c, d)]
    return co, np.array(tris)


BOXES = [(0.02, 0.02, 0.45, 0.95), (0.55, 0.05, 0.95, 0.30), (0.55, 0.40, 0.95, 0.60), (0.55, 0.70, 0.95, 0.95)]   # u0, v0, u1, v1


def synthetic_mouth(boxes=BOXES):
    """顔の面（y −0.06）・唇の裏の袋（y −0.05、顔の面と 1 本の辺でつながる＝肌）・歯（y −0.055）・舌（y −0.05〜−0.03）。
    uv: 顔の面は絵の左半分、袋・歯・舌はそれぞれ右半分の別の四角（boxes）に置く。"""
    pieces = [quad_grid(-0.04, 0.04, 1.43, 1.48, -0.06, 8, 5), quad_grid(-0.02, 0.02, 1.446, 1.454, -0.05, 4, 1),
              quad_grid(-0.01, 0.01, 1.450, 1.454, -0.055, 2, 1)]
    tongue_co = np.array([[-0.008, -0.05, 1.447], [0.008, -0.05, 1.447], [0.008, -0.03, 1.447], [-0.008, -0.03, 1.447]])
    pieces.append((tongue_co, np.array([(0, 1, 2), (0, 2, 3)])))
    co, tris, uv, edges, start = [], [], [], set(), 0
    for (points, faces), (u0, v0, u1, v1) in zip(pieces, boxes):
        lo, hi = points.min(axis=0), points.max(axis=0)
        span = np.where(hi - lo > 0, hi - lo, 1.0)
        for face in faces:
            for v in face:
                p = (points[v] - lo) / span
                uv.append([u0 + (u1 - u0) * p[0], v0 + (v1 - v0) * (p[2] if span[2] != 1.0 else p[1])])
            tris.append([start + v for v in face])
            for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
                edges.add(tuple(sorted((start + a, start + b))))
        co.extend(points.tolist())
        start += len(points)
    edges.add((0, 54))                                                  # 袋は顔の面とつながる（肌の島）
    tris = np.array(tris, dtype=np.int64)
    return (np.array(co), np.array(sorted(edges), dtype=np.int64), tris, np.arange(tris.size, dtype=np.int64).reshape(-1, 3),
            np.array(uv, dtype=float), boxes)


SETTINGS = {"front_y": -0.02, "resolution": 0.0005, "window": {"half_width": 0.035, "z": [1.432, 1.478]}, "zone": {"half_width": 0.03},
            "parts": {"half_width": 0.025, "z": [1.44, 1.47], "front_y": -0.02, "max_size": 0.045, "front_limit": -0.058},
            "tongue": {"back_y": -0.035, "centre": 0.003}}


def pixel(box, u, v):
    """uv の四角 box の中の割合 (u, v) の画素の (行, 列)。"""
    u0, v0, u1, v1 = box
    return int((1 - (v0 + (v1 - v0) * v)) * SIZE), int((u0 + (u1 - u0) * u) * SIZE)


def test_dilate_grows_by_the_four_neighbours():
    mask = np.zeros((7, 7), dtype=bool)
    mask[3, 3] = True
    grown = mouth.dilate(mask, 2)
    assert grown.sum() == 13 and grown[1, 3] and grown[3, 5] and not grown[1, 2]       # ひし形（4 近傍を 2 回）
    edge = np.zeros((3, 3), dtype=bool)
    edge[0, 0] = True
    assert mouth.dilate(edge, 1).sum() == 3                                              # 絵の外は無いもの


def test_the_inside_of_the_mouth_is_painted_and_the_face_is_not():
    co, edges, tris, tri_loops, uv, boxes = synthetic_mouth()
    image = np.full((SIZE, SIZE, 3), 200, dtype=np.uint8)
    painted, report = mouth.paint_inside(co, edges, tris, tri_loops, uv, image, SETTINGS)
    assert report["pocket_triangles"] == 8 and report["part_triangles"] == 4 and report["tongue_triangles"] == 2
    assert report["parts"] == [6, 4] and report["tongue_parts"] == [4]                   # 歯と舌（大きい島の順）
    face, pocket, teeth, tongue = boxes
    assert painted[pixel(pocket, 0.5, 0.5)].tolist() == [92, 26, 30]                     # 袋と歯は暗い赤
    assert painted[pixel(teeth, 0.5, 0.5)].tolist() == [92, 26, 30]
    assert painted[pixel(tongue, 0.5, 0.5)].tolist() == [168, 72, 74]                    # 舌は明るい赤
    assert (painted[pixel(face, 0.5, 0.5)] == 200).all()                                 # 外から見える顔の面は触らない
    r0, c0 = int((1 - face[3]) * SIZE), int(face[0] * SIZE)
    r1, c1 = int((1 - face[1]) * SIZE), int(face[2] * SIZE)
    assert (painted[r0:r1, c0:c1] == 200).all()
    assert report["painted_pixels"] == int((painted != 200).any(axis=2).sum())
    custom, _ = mouth.paint_inside(co, edges, tris, tri_loops, uv, image, {**SETTINGS, "inside_color": [10, 0, 0], "grow": 0})
    assert custom[pixel(pocket, 0.5, 0.5)].tolist() == [10, 0, 0]
    assert report["uv_outside_triangles"] == 0 and report["alpha_raised_pixels"] == 0


def test_overlapping_paint_is_counted_once():
    near = BOXES[:3] + [(0.55, 0.62, 0.95, 0.95)]                      # 舌の四角を歯の四角のすぐ上へ（ふくらませた所が重なる）
    co, edges, tris, tri_loops, uv, boxes = synthetic_mouth(near)
    image = np.full((SIZE, SIZE, 3), 200, dtype=np.uint8)
    painted, report = mouth.paint_inside(co, edges, tris, tri_loops, uv, image, SETTINGS)
    assert report["painted_pixels"] == int((painted != 200).any(axis=2).sum())          # 重なりは 1 回だけ
    tongue = (painted == [168, 72, 74]).all(axis=2)
    rows = np.where(tongue.any(axis=1))[0]
    assert rows.max() > int((1 - boxes[3][1]) * SIZE)                    # 舌の色が四角の下の端より外（歯との間）まで広がっている


def _tongue_uv_shifted():
    """舌の 2 つの三角形（最後の 2 つ）の uv を、絵の外（u + 1）へずらしたもの。"""
    co, edges, tris, tri_loops, uv, boxes = synthetic_mouth()
    shifted = uv.copy()
    shifted[tri_loops[-2:].ravel(), 0] += 1.0
    return co, edges, tris, tri_loops, shifted, boxes


def test_uv_outside_the_picture_is_not_painted_and_is_counted():
    co, edges, tris, tri_loops, uv, boxes = _tongue_uv_shifted()
    image = np.full((SIZE, SIZE, 3), 200, dtype=np.uint8)
    painted, report = mouth.paint_inside(co, edges, tris, tri_loops, uv, image, SETTINGS)
    assert report["tongue_triangles"] == 2 and report["uv_outside_triangles"] == 2
    assert painted[pixel(boxes[3], 0.5, 0.5)].tolist() == [200, 200, 200]               # 絵の外の所は塗らない（繰り返しの uv は確かめる）


# ---------- 工程 ----------

def _write_inputs(tmp_path, alpha=False):
    co, edges, tris, tri_loops, uv, _ = synthetic_mouth()
    np.savez_compressed(tmp_path / "head_mesh.npz", co=co, edges=edges, tris=tris, tri_loops=tri_loops, uv=uv)
    Image.new("RGBA" if alpha else "RGB", (SIZE, SIZE), (200, 200, 200, 128) if alpha else (200, 200, 200)).save(tmp_path / "head.png")


def test_mouth_inside_operation_writes_a_png_and_keeps_alpha(tmp_path):
    from model3d import executor
    _write_inputs(tmp_path, alpha=True)
    atomic_json(tmp_path / "recipe.json", {
        "schema_version": "model3d-recipe/0.1", "name": "mouth", "inputs": {"mesh": {"path": "head_mesh.npz"}, "texture": {"path": "head.png"}},
        "steps": [{"id": "mouth", "operation": "texture.mouth_inside", "inputs": {"mesh": "$inputs.mesh", "texture": "$inputs.texture"},
                   "parameters": SETTINGS, "outputs": {"texture": "mouth.png", "report": "mouth.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["mouth"]
    painted = np.asarray(Image.open(step["artifacts"]["texture"]))
    changed = (painted[..., :3] != 200).any(axis=2)
    assert painted.shape == (SIZE, SIZE, 4) and changed.any()
    assert (painted[~changed, 3] == 128).all() and (painted[changed, 3] == 255).all()     # 透けは残し、塗った画素だけ不透明に
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["alpha_kept"] is True and report["painted_pixels"] > 0 and report["schema_version"] == "model3d-mouth-inside/0.1"
    assert report["alpha_raised_pixels"] == int(changed.sum()) and step["unresolved"] == []
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["mouth"].get("reused_from")   # 同じ入力なら再利用


def _with(**changes):
    return {**SETTINGS, **changes}


@pytest.mark.parametrize("parameters, outputs, message", [
    ({k: v for k, v in SETTINGS.items() if k != "parts"}, None, "parts"),
    (_with(tongue={"back_y": -0.035}), None, "tongue に centre"),
    (_with(inside_color=[300, 0, 0]), None, "inside_color"),
    (_with(grow=-1), None, "grow"),
    (_with(window={"half_width": 0.03, "z": [1.47, 1.43]}), None, "window.z"),
    (_with(zone={"half_width": 0.04}), None, r"window\.half_width（0\.035）以下"),               # 窓の外の頂点は比べられない
    (_with(colour=1), None, "知らない設定"),
    (SETTINGS, {"texture": "m.jpg", "report": "r.json"}, ".png"),
    (SETTINGS, {"texture": "m.png"}, "report"),
])
def test_mouth_inside_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    _write_inputs(tmp_path)
    declared = {k: tmp_path / v for k, v in (outputs or {"texture": "m.png", "report": "r.json"}).items()}
    with pytest.raises(ToolError, match=message):
        get("texture.mouth_inside").function({"mesh": tmp_path / "head_mesh.npz", "texture": tmp_path / "head.png"}, parameters, declared,
                                             Context())


def test_mouth_inside_needs_uv_and_triangle_corners(tmp_path):
    from model3d.ops import get

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    co, edges, tris, tri_loops, uv, _ = synthetic_mouth()
    np.savez(tmp_path / "no_uv.npz", co=co, edges=edges, tris=tris, tri_loops=tri_loops)
    Image.new("RGB", (SIZE, SIZE)).save(tmp_path / "head.png")
    run = get("texture.mouth_inside").function
    outputs = {"texture": tmp_path / "m.png", "report": tmp_path / "r.json"}
    with pytest.raises(ToolError, match="uv"):
        run({"mesh": tmp_path / "no_uv.npz", "texture": tmp_path / "head.png"}, SETTINGS, outputs, Context())
    np.savez(tmp_path / "short_uv.npz", co=co, edges=edges, tris=tris, tri_loops=tri_loops, uv=uv[:5])
    with pytest.raises(ToolError, match="面の角"):
        run({"mesh": tmp_path / "short_uv.npz", "texture": tmp_path / "head.png"}, SETTINGS, outputs, Context())


def test_mouth_inside_keeps_a_colour_key_transparency(tmp_path):
    from model3d.ops import get

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    _write_inputs(tmp_path)
    Image.new("RGB", (SIZE, SIZE), (200, 200, 200)).save(tmp_path / "keyed.png", transparency=(200, 200, 200))   # 透明色キー（tRNS）
    outputs = {"texture": tmp_path / "m.png", "report": tmp_path / "r.json"}
    get("texture.mouth_inside").function({"mesh": tmp_path / "head_mesh.npz", "texture": tmp_path / "keyed.png"}, SETTINGS, outputs, Context())
    painted = np.asarray(Image.open(outputs["texture"]))
    changed = (painted[..., :3] != 200).any(axis=2)
    assert painted.shape == (SIZE, SIZE, 4) and changed.any()
    assert (painted[~changed, 3] == 0).all() and (painted[changed, 3] == 255).all()       # 透けは残し、塗った画素だけ不透明に


def test_mouth_inside_refuses_16_bit_textures_and_flags_uv_outside(tmp_path):
    from model3d.ops import get

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    run = get("texture.mouth_inside").function
    outputs = {"texture": tmp_path / "m.png", "report": tmp_path / "r.json"}
    _write_inputs(tmp_path)
    Image.fromarray(np.full((SIZE, SIZE), 50000, dtype=np.uint16)).save(tmp_path / "gray16.png")
    with pytest.raises(ToolError, match="8 ビット"):                                       # 色に変えると 255 で切られて白くなる
        run({"mesh": tmp_path / "head_mesh.npz", "texture": tmp_path / "gray16.png"}, SETTINGS, outputs, Context())
    co, edges, tris, tri_loops, uv, _ = _tongue_uv_shifted()
    np.savez(tmp_path / "shifted.npz", co=co, edges=edges, tris=tris, tri_loops=tri_loops, uv=uv)
    result = run({"mesh": tmp_path / "shifted.npz", "texture": tmp_path / "head.png"}, SETTINGS, outputs, Context())
    assert result.review and "0〜1 の外" in result.unresolved[0]                             # 塗った画素はあるが、要確認
