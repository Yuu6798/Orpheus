"""Blender の中で動かす: 1 枚の背景の絵を、基準のカメラから床と奥の壁に投影して張った「箱の部屋」を .blend に足す
（docs/model3d.md の「箱の部屋」）。基準のカメラから見ると絵と同じ画になり、カメラが動くと床と壁がずれて奥行きが出る。

    blender --background --factory-startup --python box_room.py -- --blend in.blend --image room.png --settings settings.json \
        --out-blend out.blend --report report.json

部屋の座標（絵の中の仮の部屋）: 基準のカメラが原点で、右 x・下 y・奥 z。カメラは水平に奥（+z）を向き、焦点距離 focal_px（絵の画素）、
主点 center_px（水平線の消失点）。床はカメラの camera_height 下（y = camera_height）。長さの単位は仮のもので、scale 倍が m。
- 原点にする床の点（踊り手の立つ所）: 絵の画素 origin_px（x と、床に立つ所の y）。その奥行き z = focal × camera_height ÷ (y − 主点の y)。
- 奥の壁: 絵の行 wall_px で床と交わる、絵の面と平行な壁（奥行き z = focal × camera_height ÷ (wall_px − 主点の y)）。
- Blender の座標（X 右・Y 奥・Z 上、m）へは ((x − 原点の x) × scale, (z − 原点の z) × scale, (camera_height − y) × scale)。
- 床のメッシュ: 絵の行 floor.rows（[最初, 終わり（含まない）, きざみ]。既定は [wall_px, 絵の高さ × 2, 24]）で段を切り（奥ほど粗い）、
  floor.x（仮の部屋の x）で列を切る。
  頂点の uv は、基準のカメラで写した絵の画素。絵の外（回り込んで横や上を向いたとき）は、絵の端の色を引き伸ばす（EXTEND）。
- 壁は 1 枚（絵の面と平行なので、1 枚でも投影と同じ）。wall.x（左右の端）と wall.top（上の端の y）。
- shell があれば、部屋を暗い色の箱で囲う（絵の届かない向き）。位置と大きさは m、色は Workbench の物体の色（シーンの線形）。
物体は collection（既定 model3d_room）に入れ、render.passes の room.collection で描く。絵は .blend に詰め込む。
コレクションは、アーマチュア（armature か最初のアーマチュア。無ければ今のシーン）の入ったシーンに足す（render.passes と同じシーン）。
リポジトリ外の試作の台本（2 作目の VRM のダンスの下見）と同じ式・同じ順で計算する（倍精度で計算して、Blender の単精度に入れる）。
"""
import argparse
import json
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vrm_apply_motion import use_scene_of

DEFAULTS = {
    "scale": 1.0,
    "floor": {"rows": None, "x": [-70.0, -35.0] + [-13.0 + 0.5 * k for k in range(53)] + [35.0, 70.0]},
    "wall": {"x": [-70.0, 70.0], "top": -45.0},
    "shell": {"location": [0.0, 4.0, 9.9], "size": [40.0, 44.0, 20.0], "color": [0.012, 0.007, 0.005]},
    "collection": "model3d_room", "armature": None,
}


def merged(settings):
    out = {**DEFAULTS, **{k: v for k, v in settings.items() if k not in ("floor", "wall", "shell")}}
    for key in ("floor", "wall"):
        out[key] = {**DEFAULTS[key], **settings.get(key, {})}
    out["shell"] = None if settings.get("shell", DEFAULTS["shell"]) is None else {**DEFAULTS["shell"], **settings.get("shell", {})}
    return out


def room_geometry(settings, image_size):
    """床と壁のメッシュ（頂点は Blender の座標の m、uv は 0〜1、面は四角形）と、置き方の数（原点の奥行き・壁の奥行きなど）。

    計算の式と順は、リポジトリ外の試作の台本と同じ（倍精度）。"""
    f = float(settings["focal_px"])
    cx, cy = (float(v) for v in settings["center_px"])
    hc = float(settings["camera_height"])
    k = float(settings["scale"])
    width, height = image_size
    ox, oy = (float(v) for v in settings["origin_px"])
    z_d = f * hc / (oy - cy)
    x_d = (ox - cx) * z_d / f
    z_w = f * hc / (float(settings["wall_px"]) - cy)

    def to_b(point):
        return ((point[0] - x_d) * k, (point[2] - z_d) * k, (hc - point[1]) * k)

    def uv_of(point):
        return ((f * point[0] / point[2] + cx) / width, 1.0 - (f * point[1] / point[2] + cy) / height)

    rows = floor_rows(settings, height)
    xs = [float(v) for v in settings["floor"]["x"]]
    verts, faces, uvs = [], [], []
    for py in range(int(rows[0]), int(rows[1]), int(rows[2])):
        z = f * hc / (py - cy)
        for x in xs:
            point = (x, hc, z)
            verts.append(to_b(point))
            uvs.append(uv_of(point))
    columns = len(xs)
    count = len(range(int(rows[0]), int(rows[1]), int(rows[2])))
    for j in range(count - 1):
        for i in range(columns - 1):
            a = j * columns + i
            faces.append((a, a + 1, a + columns + 1, a + columns))
    base = len(verts)
    left, right = (float(v) for v in settings["wall"]["x"])
    top = float(settings["wall"]["top"])
    for x, y in ((left, hc), (right, hc), (right, top), (left, top)):
        point = (x, y, z_w)
        verts.append(to_b(point))
        uvs.append(uv_of(point))
    faces.append((base, base + 1, base + 2, base + 3))
    info = {"origin_depth": z_d, "origin_x": x_d, "wall_depth": z_w, "camera_m": list(to_b((0.0, 0.0, 0.0))),
            "floor_rows": count, "floor_columns": columns}
    return verts, faces, uvs, info


def floor_rows(settings, height):
    """床の段の行 [最初, 終わり（含まない）, きざみ]。既定は [wall_px, 絵の高さ × 2, 24]（絵の下の端の先まで）。"""
    return settings["floor"]["rows"] or [int(settings["wall_px"]), 2 * int(height), 24]


def room_problem(settings, image_size):
    """置き方が成り立たないとき（床に立つ所・壁の行が水平線より上、壁が踊り手より手前、床の段が 2 つに満たない）の説明。
    image_size（幅, 高さ）は、既定の床の段を数えるのに使う。"""
    cy = float(settings["center_px"][1])
    if float(settings["origin_px"][1]) <= cy:
        return "origin_px の y（床に立つ所）は、主点の y（水平線）より下（大きい値）にしてください"
    if float(settings["wall_px"]) <= cy:
        return "wall_px（床と壁の境の行）は、主点の y（水平線）より下にしてください"
    if float(settings["wall_px"]) >= float(settings["origin_px"][1]):
        return "wall_px（床と壁の境の行）は、床に立つ所（origin_px の y）より上（小さい値）にしてください（壁が踊り手より手前になる）"
    rows = floor_rows(settings, image_size[1])
    if float(rows[0]) <= cy:
        return "floor.rows の最初の行は、主点の y（水平線）より下にしてください"
    if len(range(int(rows[0]), int(rows[1]), int(rows[2]))) < 2:
        return f"床の段が 2 つに満たない（行 {rows}）。floor.rows を、終わりの行が最初の行よりきざみの 2 倍以上先になるようにしてください"
    return None


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="box_room.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    # 部屋は、踊り手（アーマチュア）の入ったシーンに足す（render.passes もそのシーンで描く。作業用のシーンを開いたまま保存されたファイルがある）
    if settings["armature"]:
        arm = bpy.data.objects.get(settings["armature"])
        if arm is None or arm.type != "ARMATURE":
            raise SystemExit(f"アーマチュアの物体がありません: {settings['armature']}")
    else:
        arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    sc = use_scene_of(bpy, arm) if arm is not None else bpy.context.scene
    if bpy.data.collections.get(settings["collection"]) is not None:
        raise SystemExit(f"同じ名前のコレクションがもうあります: {settings['collection']}")
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    unit = float(sc.unit_settings.scale_length)
    image = bpy.data.images.load(os.path.abspath(a.image))
    image.pack()
    problem = room_problem(settings, tuple(image.size))
    if problem:
        raise SystemExit(problem)
    verts, faces, uvs, info = room_geometry(settings, tuple(image.size))
    coll = bpy.data.collections.new(settings["collection"])
    sc.collection.children.link(coll)

    material = bpy.data.materials.new("model3d_room")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.extension = "EXTEND"
    material.node_tree.links.new(texture.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    nodes.active = texture                               # 簡易描画（Workbench の色の絵）が読む画像
    mesh = bpy.data.meshes.new("model3d_room")
    mesh.from_pydata([Vector(v) / unit for v in verts], [], faces)
    layer = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        layer.data[loop.index].uv = uvs[loop.vertex_index]
    mesh.materials.append(material)
    mesh.update()
    room = bpy.data.objects.new("model3d_room", mesh)
    coll.objects.link(room)
    objects = [room.name]
    if settings["shell"] is not None:
        dark = bpy.data.materials.new("model3d_room_shell")
        dark.diffuse_color = (*settings["shell"]["color"], 1.0)
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=tuple(v / unit for v in settings["shell"]["location"]))
        shell = bpy.context.object
        shell.name = "model3d_room_shell"
        shell.scale = tuple(v / unit for v in settings["shell"]["size"])
        shell.data.materials.append(dark)
        for other in list(shell.users_collection):
            other.objects.unlink(shell)
        coll.objects.link(shell)
        objects.append(shell.name)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    report = {"schema_version": "model3d-box-room/0.1", "blender": bpy.app.version_string, "collection": coll.name, "scene": sc.name,
              "objects": objects, "image": {"size": list(image.size)}, "vertices": len(verts), "faces": len(faces),
              "scale_length": unit, **info, "settings": settings}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"objects": objects, "vertices": len(verts)}))


if __name__ == "__main__":
    main()
