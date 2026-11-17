"""Blender の中で動かす: 組み立てたモデル（体・頭・髪が別の物体の .blend）の頭身を測り、頭の大きさと位置の提案を出し、頭と髪を
メモリの中で動かして測り直す（check.assembly）。.blend は保存しない（元のファイルは変えない）。

    blender --background --factory-startup model.blend --python assembly.py -- --settings settings.json --report raw.json [--views DIR]

測るのはメッシュの基本の形を物体の置き場所で世界の座標にしたもの（形状キー・モディファイアは使わない。絵は評価した形で描く）。
settings: body・head・hair（物体の名前の並び。名前の物体とその子孫の、描かれるメッシュ（物体とコレクションの描かない設定と、
ビューレイヤーの除外を見る。ground_feet.rendered）を、シーンの並び順に集める）、pivot（頭と髪を
大きくする中心。省くと頭のメッシュの箱の中心）、reference・anchor・place（assembly_measure.proposal）、bust（胸から上の絵の幅と、
頭の真ん中から下げる量。m）。動かすのは、頭と髪の名前の物体のうち、祖先が名前に入っていない物（子は親について動く）。
--views があれば、今の配置（now_*）と動かした後（after_*）を、Workbench で正射影で描く（色の絵そのままと粘土）。
**検証**: Windows の Blender 5.2.4 で、3 作目の VRM のダンスの組み立てで、リポジトリ外の台本と同じ数になり、絵は、全身の正面と後ろ
（台本は手の先が切れていた。こちらは横の範囲にも合わせる）と簡易描画の揺れ（粘土の絵の数画素）のほかは同じになることを確かめた
（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from . import assembly_measure
    from .candidate_mesh import world_mesh
    from .face_sheet import DISPLAY_FIXED, IMAGE_FIXED, RENDER_FIXED, VIEW_FIXED, setting_changes
    from .ground_feet import rendered
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import assembly_measure
    from candidate_mesh import world_mesh
    from face_sheet import DISPLAY_FIXED, IMAGE_FIXED, RENDER_FIXED, VIEW_FIXED, setting_changes
    from ground_feet import rendered

CLAY = (0.78, 0.78, 0.8)
BACKGROUND = (0.55, 0.55, 0.58)
GROUPS = ("body", "head", "hair")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="assembly.py")
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--views", default="")
    return ap.parse_args(argv)


def collect(scene, names, shown):
    """names の物体と、その子孫のメッシュのうち、shown（描かれるか）が真の物（シーンの並び順）。"""
    named, found = set(names), []
    for ob in scene.objects:
        if ob.type != "MESH" or not shown(ob):
            continue
        parent = ob
        while parent is not None and parent.name not in named:
            parent = parent.parent
        if parent is not None:
            found.append(ob)
    return found


def movers(scene, names):
    """動かす物体: names のうち、祖先が names に入っていない物（シーンの並び順）。"""
    named, found = set(names), []
    for ob in scene.objects:
        if ob.name not in named:
            continue
        parent = ob.parent
        while parent is not None and parent.name not in named:
            parent = parent.parent
        if parent is None:
            found.append(ob)
    return found


def check_groups(groups, moved):
    """体のメッシュが頭・髪と重ならず、動かす物体の子孫でないこと（頭と髪だけを動かすため）。問題があればその説明。"""
    body = {ob.name for ob in groups["body"]}
    shared = sorted(body & {ob.name for ob in groups["head"] + groups["hair"]})
    if shared:
        return f"体のメッシュが、頭か髪の名前の物体の子孫にもなっています: {shared}"
    names = {ob.name for ob in moved}
    for ob in groups["body"]:
        parent = ob
        while parent is not None:
            if parent.name in names:
                return f"体のメッシュ {ob.name} が、動かす物体 {parent.name}（頭か髪）の子孫です"
            parent = parent.parent
    return None


def base_colour_active(bpy):
    """材質ごとに、いちばん大きい画像のノードを選んだ状態にする（Workbench の色の絵の描き方は、選んだ画像を使う。保存しない）。"""
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        best = None
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                area = node.image.size[0] * node.image.size[1]
                if best is None or area > best[0]:
                    best = (area, node)
        if best:
            mat.node_tree.nodes.active = best[1]


def setup_render(bpy, scene):
    """Workbench の描き方にし、.blend から引き継ぐと出てくる絵をすり替えうる描く設定（シーケンサー・コンポジター・描く範囲の切り取り・
    画素の縦横比・多視点・透ける背景・ぶれ・色の補正・表示の装置・出力の形式）を render.face_sheet と同じ値にする。マーカーに結んだ
    カメラも外す。カメラと、.blend と違っていた設定（render_changes）を返す。"""
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.show_shadows = False
    shading.show_backface_culling = False
    groups = (("display", scene.display_settings, DISPLAY_FIXED), ("render", scene.render, RENDER_FIXED),
              ("image", scene.render.image_settings, IMAGE_FIXED))
    changes = {key: setting_changes({name: getattr(target, name) for name in fixed}, fixed) for key, target, fixed in groups}
    for key, target, _ in groups:
        for name, (_, value) in changes[key].items():
            setattr(target, name, value)
    if scene.world is None:
        scene.world = bpy.data.worlds.new("model3d_assembly_world")
    scene.world.use_nodes = False
    scene.world.color = BACKGROUND
    scene.view_settings.view_transform = "Standard"      # 表示の装置を決めてから（選べる変換は装置ごと）
    changes["view"] = setting_changes({name: getattr(scene.view_settings, name) for name in VIEW_FIXED}, VIEW_FIXED)
    for name, (_, value) in changes["view"].items():
        setattr(scene.view_settings, name, value)
    changes["marker_cameras"] = [marker.name for marker in scene.timeline_markers if marker.camera is not None]
    for marker in scene.timeline_markers:                # マーカーに結んだカメラは、描くときにカメラを差し替える
        marker.camera = None
    camera_data = bpy.data.cameras.new("model3d_assembly_camera")
    camera_data.type = "ORTHO"
    camera = bpy.data.objects.new("model3d_assembly_camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    return camera, changes


def render(bpy, scene, camera, folder, name, direction, centre, ortho, res, mode):
    from mathutils import Vector
    shading = scene.display.shading
    if mode == "tex":
        shading.light, shading.color_type, shading.show_cavity = "FLAT", "TEXTURE", False
    else:
        shading.light, shading.color_type, shading.show_cavity = "STUDIO", "SINGLE", True
        shading.single_color = CLAY
        shading.cavity_type = "BOTH"
    d = Vector(direction).normalized()
    camera.location = Vector(centre) + d * 10
    camera.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    camera.data.ortho_scale = ortho
    camera.data.clip_start, camera.data.clip_end = 0.1, 50
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.filepath = os.path.join(folder, name + ".png")
    bpy.ops.render.render(write_still=True)
    return {"file": name + ".png", "direction": list(direction), "centre": list(centre), "ortho": ortho, "res": list(res),
            "ppm": max(res) / ortho, "mode": mode}


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Matrix, Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    scene = bpy.context.scene
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:
        raise SystemExit(f"シーンの単位の倍率（scale_length）が 1 ではありません（{scene.unit_settings.scale_length}）。長さを m で測るため 1 にしてください")
    missing = sorted({name for key in GROUPS for name in settings[key] if scene.objects.get(name) is None})
    if missing:
        raise SystemExit(f"シーンに無い物体: {missing}")
    groups = {key: collect(scene, settings[key], lambda ob: rendered(ob, scene.view_layers)) for key in GROUPS}
    for key, found in groups.items():
        if not any(len(ob.data.vertices) for ob in found):
            raise SystemExit(f"{key} の名前の物体とその子孫に、描くメッシュがありません: {settings[key]}")
    moved = movers(scene, settings["head"] + settings["hair"])
    problem = check_groups(groups, moved)
    if problem:
        raise SystemExit(problem)

    def measured():
        try:
            return assembly_measure.measure(*(world_mesh(groups[key]) for key in GROUPS))
        except ValueError as error:
            raise SystemExit(str(error)) from None

    now = measured()
    now["objects"] = {key: [ob.name for ob in found] for key, found in groups.items()}
    if settings.get("pivot") is not None:
        pivot, pivot_from = Vector(settings["pivot"]), "parameter"
    else:
        V = world_mesh(groups["head"])[0]
        pivot, pivot_from = Vector(((V.min(axis=0) + V.max(axis=0)) / 2).tolist()), "head box centre"
    try:
        plan = assembly_measure.proposal(now, [pivot.x, pivot.y, pivot.z], settings.get("reference"), settings.get("anchor"),
                                         settings.get("place"))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    plan["pivot_from"] = pivot_from
    report = {"schema_version": "model3d-assembly-raw/0.1", "blend": bpy.data.filepath, "blender_version": bpy.app.version_string,
              "reference": settings.get("reference"), "anchor": settings.get("anchor"), "place": settings.get("place") or {},
              "now": now, "proposal": plan, "moved": [ob.name for ob in moved]}
    if report["reference"]:
        ref = report["reference"]
        ref["heads"] = ref["top_U"] / (ref["top_U"] - ref["chin_U"])
    bust_size, bust_drop = settings["bust"]
    folder = os.path.abspath(a.views) if a.views else ""
    camera = None
    if folder:
        os.makedirs(folder, exist_ok=True)
        base_colour_active(bpy)
        camera, report["render_changes"] = setup_render(bpy, scene)
        report["views"] = {name: render(bpy, scene, camera, folder, name, *rest)
                           for name, *rest in assembly_measure.view_plan(now, "now", bust_size, bust_drop)}
    # メモリの中で頭と髪を動かす（保存しない）
    move = Matrix.Translation(Vector(plan["to"])) @ Matrix.Scale(plan["scale"], 4) @ Matrix.Translation(-pivot)
    for ob in moved:
        ob.matrix_world = move @ ob.matrix_world
    bpy.context.view_layer.update()
    after = measured()
    after["head"].pop("profile")
    report["after"] = after
    if folder:
        report["views"].update({name: render(bpy, scene, camera, folder, name, *rest)
                                for name, *rest in assembly_measure.view_plan(after, "after", bust_size, bust_drop)})
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print("ASSEMBLY_MEASURED", json.dumps({"now": now["ratio"], "proposal": plan, "after": after["ratio"]}))


if __name__ == "__main__":
    main()
