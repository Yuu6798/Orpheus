"""Blender の中で動かす: 裾を「身代わりの筒」の布で動かす（cloth.skirt_proxy）。細かい裾は筒に貼り付けて付いて行かせる。

    blender --background --factory-startup --python skirt_proxy.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ身代わりか（リポジトリ外の 2 作目の VRM のダンスで分かったこと）:
  細かい裾（ひだの彫り込まれたメッシュ）に直接布の計算をかけると、体全体を相手にして 330 コマで 45 分たっても終わらず、相手を軽く
  するとひだが潰れて破れた紙のようになった。そこで、裾の内側の面をなぞった、なめらかで目の粗い筒（身代わり）を作り、布として計算するのは
  筒だけにする。細かい裾は筒の面に貼り付けて（Surface Deform）、ひだの形のまま付いて行かせる。

前提: 裾の骨は動いていない .blend（vrm.bake_springs の no_spring で裾の揺れものを外したもの）。1 Blender 単位 = 1 m のシーン。
やること（どれも初期姿勢で測ってから）:
  0. hem_cut_z > 0 なら、裾を床からその高さで水平に切り落とす（丈を詰める）。
  1. 裾の内側の面までの距離を、腰を通る縦の軸から放射状に測る（高さ rows × 向き columns の格子）。切れ目（裾が当たらない向き）には
     筒の面を張らない。細いすき間は埋め、細い帯は消し、周りの値でならす。
  2. 体（body）から、脚の頂点グループ（leg_groups の名前の頭）の頂点を持つ大きな島（脚・骨盤）だけを取り出し、足首より下を除いて、
     面を減らした当たり判定の相手（skirt_collider）にする。小さな島（腰の飾りなど）は入らない。
  3. 細かい裾の頂点のうち、脚に近すぎるものを外へ出す（clearance）。
  4. extend_slit なら、前の切れ目を帯の下まで伸ばす（筒と細かい裾の両方に切り込み）。extra_cuts_deg の向きにも切り込みを足せる。
  5. 筒を作る。腰の骨に付け、上の数段は固定、下へ行くほど弱く「元の形へ戻るばね」を効かせる。細かい裾を筒に貼り付ける（骨の変形は外す）。
  6. 初期姿勢から最初のコマの姿勢へ移る助走（preroll コマ）と落ち着かせるコマ（hold）を、骨の動きのキーとして前に足し、
     床（skirt_floor）も置いて、布を計算して焼く。
長さの定数（上の端から 0.12 m を軸に使う・段の端 6 mm と 4 mm・光線の届く 1.2 m と 0.6 m・切り込みの幅 5 cm）は台本と同じ。

報告は model3d-skirt-proxy/0.1（リポジトリ外の台本の SKIRT_PROXY の行と同じ項目）。
**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスで、リポジトリの外の台本と同じ形と布の動きになることを確かめた
（docs/model3d.md）。CI には Blender が無い（形の計算はテストする）。
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .vrm_apply_motion import use_scene_of
    from .vrm_bake_springs import fcurve_bags
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vrm_apply_motion import use_scene_of
    from vrm_bake_springs import fcurve_bags

PROXY, COLLIDER, FLOOR = "skirt_proxy", "skirt_collider", "skirt_floor"
# 既定の値は、リポジトリ外の 2 作目の VRM のダンスで決めた値（理由は docs/model3d.md の表）
DEFAULTS = {
    "columns": 60, "rows": 26, "close_gap": 3, "min_strip": 3, "inner_percentile": 25,
    "clearance_top": 0.003, "clearance": 0.011,
    "collider_min_island": 150, "collider_min_leg_vertices": 5, "collider_ratio": 0.5, "collider_cut_below": 0.16,
    "collider_outer": 0.005, "collider_inner": 0.006, "friction": 2.0, "distance_min": 0.003,
    "quality": 12, "collision_quality": 5, "mass": 0.15, "air_damping": 3.0, "gravity": 1.0,
    "tension": 20.0, "compression": 20.0, "shear": 8.0, "bending": 8.0,
    "tension_damping": 8.0, "compression_damping": 8.0, "shear_damping": 8.0, "bending_damping": 2.0,
    "pin_rows": 4, "extend_slit": True, "pin_rows_front": 3, "front_zone_deg": [185.0, 355.0], "extra_cuts_deg": [],
    "hem_cut_z": 0.0, "goal_below_pin": 0.05, "goal_hem": 0.0, "goal_curve": 2.0, "pin_stiffness": 20.0,
    "smooth_iterations": 6, "smooth_factor": 0.5, "bind_falloff": 2.5, "preroll": 40, "hold": 14,
    "floor": "collide", "floor_offset": 0.006, "floor_friction": 2.0, "self_collision": False, "pushout_after": 0.003,
}
NAMES = ("skirt", "body", "hips_bone", "leg_groups")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="skirt_proxy.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def merged(settings):
    """既定の値に settings を重ねる（名前の 4 つ skirt・body・hips_bone・leg_groups は settings に要る）。"""
    missing = [name for name in NAMES if name not in settings]
    if missing:
        raise ValueError(f"設定に {', '.join(missing)} が要ります")
    return {**DEFAULTS, **settings}


# ---- 形の計算（Blender なし。リポジトリ外の台本と同じ式と順。テストする） ----

def grid_levels(z_top, z_bottom, columns, rows):
    """筒の格子の向き（ラジアン。0 が +X、反時計回り）と高さ（上から下へ）。"""
    angles = np.arange(columns) * (2 * math.pi / columns)
    levels = np.linspace(z_top - 0.006, z_bottom + 0.004, rows)
    return angles, levels


def fill_columns(inner):
    """列（向き）ごとに、当たった範囲（いちばん上〜いちばん下の段）の中の抜けを上下から補間で埋める。範囲の外（切れ目・裾の下）は
    張らない。当たりが 2 段に満たない列は張らない。戻り: (半径, 張る格子)。"""
    rows, columns = inner.shape
    valid = ~np.isnan(inner)
    radius = inner.copy()
    for i in range(columns):
        hit_rows = np.flatnonzero(valid[:, i])
        if len(hit_rows) < 2:
            valid[:, i] = False
            continue
        first, last = hit_rows[0], hit_rows[-1]
        span = np.arange(first, last + 1)
        radius[span, i] = np.interp(span, hit_rows, inner[hit_rows, i])
        valid[span, i] = True
    return radius, valid


def runs_of(mask):
    """輪になった 1 行の中の、True が続く区間を (開始, 長さ) で返す。"""
    count = len(mask)
    if mask.all():
        return [(0, count)]
    if not mask.any():
        return []
    start = next(i for i in range(count) if mask[i] and not mask[i - 1])
    found = []
    i = start
    while True:
        length = 0
        while mask[(i + length) % count]:
            length += 1
        found.append((i % count, length))
        i += length
        while not mask[i % count]:
            i += 1
        if i % count == start:
            break
    return found


def close_and_trim(radius, valid, close_gap, min_strip):
    """行ごとに、close_gap 以下の幅のすき間は左右から補間で埋め、min_strip 以下の幅の帯は消す（細い帯は布の計算でばたつき、
    しわのもとになる）。radius と valid をその場で直し、(埋めた格子の数, 消した格子の数) を返す。"""
    rows, columns = valid.shape
    closed, removed = 0, 0
    for j in range(rows):
        for begin, length in runs_of(~valid[j]):
            if length <= close_gap and valid[j].any():
                left, right = radius[j, (begin - 1) % columns], radius[j, (begin + length) % columns]
                for k in range(length):
                    t = (k + 1) / (length + 1)
                    radius[j, (begin + k) % columns] = left * (1 - t) + right * t
                    valid[j, (begin + k) % columns] = True
                closed += length
        for begin, length in runs_of(valid[j]):
            if length <= min_strip:
                for k in range(length):
                    valid[j, (begin + k) % columns] = False
                removed += length
    return closed, removed


def slit_cut(valid, angles, levels, pin_rows_front):
    """前の切れ目を上へ伸ばす切り込み: 切れ目のいちばん上の段（pin_rows_front 段目から下で、開いた格子が 2 つ以上ある最初の段）の、
    開いている向きの平均の列に、pin_rows_front − 1 段目からその段の上まで。
    戻り: (切る格子の集合, 向き, 上端の高さ, 下端の高さ)。その段が無ければ None。"""
    rows, columns = valid.shape
    apex = next((j for j in range(pin_rows_front, rows) if (~valid[j]).sum() >= 2), None)
    if apex is None:
        return None
    step_angle = 2 * math.pi / columns
    open_angles = angles[np.flatnonzero(~valid[apex])]
    centre = math.atan2(np.sin(open_angles).mean(), np.cos(open_angles).mean()) % (2 * math.pi)
    cut_column = int(centre // step_angle)
    cells = {(j, cut_column) for j in range(pin_rows_front - 1, apex)}
    return cells, (cut_column + 0.5) * step_angle, float(levels[pin_rows_front - 1]), float(levels[apex])


def extra_cuts(degrees_list, levels, pin_rows_front, rows, columns, z_bottom):
    """追加の切り込み（帯の下から裾まで）の格子と、細かい裾を切る (向き, 上端, 下端) の並び。"""
    step_angle = 2 * math.pi / columns
    cells, cuts = set(), []
    for degrees in degrees_list:
        column = int(math.radians(degrees) // step_angle) % columns
        for j in range(pin_rows_front - 1, rows - 1):
            cells.add((j, column))
        cuts.append(((column + 0.5) * step_angle, float(levels[pin_rows_front - 1]), float(z_bottom) - 0.02))
    return cells, cuts


def smooth_radius(radius, valid, percentile):
    """とび出た値をならす（周り 3 段 × 5 列の、張る格子の値の百分位 percentile。ひだの内側への折り返しを拾った所を周りに合わせる）。
    その後、上下左右との平均で 2 回なめらかにする。"""
    rows, columns = valid.shape
    smoothed = radius.copy()
    for j in range(rows):
        for i in range(columns):
            if not valid[j, i]:
                continue
            near = [radius[jj, ii % columns] for jj in (j - 1, j, j + 1) for ii in (i - 2, i - 1, i, i + 1, i + 2)
                    if 0 <= jj < rows and valid[jj, ii % columns]]
            smoothed[j, i] = float(np.percentile(near, percentile))
    for _ in range(2):
        blurred = smoothed.copy()
        for j in range(rows):
            for i in range(columns):
                if not valid[j, i]:
                    continue
                near = [smoothed[jj, ii % columns] for jj, ii in ((j, i), (j - 1, i), (j + 1, i), (j, i - 1), (j, i + 1))
                        if 0 <= jj < rows and valid[jj, ii % columns]]
                blurred[j, i] = float(np.mean(near))
        smoothed = blurred
    return smoothed


def clearance_rows(rows, pin_rows, clearance_top, clearance):
    """段ごとの脚からの離し方: 腰に固定する段は clearance_top、その下 2 段で clearance へ移る。"""
    ramp = np.clip((np.arange(rows) - (pin_rows - 1)) / 2.0, 0.0, 1.0)
    return clearance_top + (clearance - clearance_top) * ramp


def proxy_radius(smoothed, valid, legs, clearance_of_row):
    """筒の半径: ならした内側の面と、脚のいちばん外側＋離し方の、大きい方。張らない格子は NaN。"""
    return np.where(valid, np.maximum(smoothed, np.where(legs > 0, legs + clearance_of_row[:, None], 0.0)), np.nan)


def push_from_legs(points, axis, levels, legs, clearance_of_row):
    """細かい裾の頂点（世界の座標）のうち、軸から見て脚＋離し方より内側のものを、放射の向きに外へ出す（近い 4 つの格子の大きい方の
    脚を使う。安全側）。戻り: (動かした頂点, 動かしたか, 要る半径, 頂点の半径)。"""
    rows, columns = legs.shape
    relative = points[:, :2] - axis
    vertex_radius = np.hypot(relative[:, 0], relative[:, 1])
    vertex_angle = np.mod(np.arctan2(relative[:, 1], relative[:, 0]), 2 * math.pi)
    column_float = vertex_angle / (2 * math.pi / columns)
    row_float = np.clip((levels[0] - points[:, 2]) / (levels[0] - levels[-1]) * (rows - 1), 0, rows - 1)
    i0 = np.floor(column_float).astype(int) % columns
    i1 = (i0 + 1) % columns
    j0 = np.floor(row_float).astype(int)
    j1 = np.minimum(j0 + 1, rows - 1)
    leg_here = np.maximum.reduce([legs[j0, i0], legs[j0, i1], legs[j1, i0], legs[j1, i1]])
    clearance_here = clearance_of_row[j0] + (clearance_of_row[j1] - clearance_of_row[j0]) * (row_float - j0)
    need = np.where(leg_here > 0, leg_here + clearance_here, 0.0)
    push = (vertex_radius < need) & (vertex_radius > 1e-6)
    scale = np.where(push, need / np.maximum(vertex_radius, 1e-6), 1.0)
    moved = points.copy()
    moved[:, :2] = axis + relative * scale[:, None]
    return moved, push, need, vertex_radius


def proxy_mesh(axis, angles, radius, valid, levels, cut_cells):
    """筒の頂点（世界の座標）・その段と列・三角形の面。張る格子（valid）に頂点を置き、切る格子には面を張らず、切れ目の縁は
    三角で詰める。"""
    rows, columns = radius.shape
    index_of = -np.ones((rows, columns), dtype=int)
    points, point_rows, point_columns = [], [], []
    for j in range(rows):
        for i in range(columns):
            if valid[j, i]:
                index_of[j, i] = len(points)
                points.append((axis[0] + math.cos(angles[i]) * radius[j, i], axis[1] + math.sin(angles[i]) * radius[j, i], levels[j]))
                point_rows.append(j)
                point_columns.append(i)
    faces = []
    for j in range(rows - 1):
        for i in range(columns):
            a, b, c, d = index_of[j, i], index_of[j, (i + 1) % columns], index_of[j + 1, (i + 1) % columns], index_of[j + 1, i]
            if (j, i) in cut_cells:
                continue
            if min(a, b, c, d) >= 0:
                faces.append((a, d, c))
                faces.append((a, c, b))
            elif min(a, b, d) >= 0:
                faces.append((a, d, b))
            elif min(b, c, d) >= 0:
                faces.append((d, c, b))
            elif min(a, b, c) >= 0:
                faces.append((a, c, b))
            elif min(a, c, d) >= 0:
                faces.append((a, d, c))
    return points, point_rows, point_columns, faces


def pin_weights(point_rows, point_columns, angles, rows, settings):
    """筒の頂点ごとの「元の形へ戻るばね」の重み（腰に固定する段は 1、その下は goal_below_pin から goal_hem へ goal_curve の曲線で
    弱める）。extend_slit なら、前側（front_zone_deg の向き）は pin_rows_front 段だけ固定する。"""
    front_from, front_to = (math.radians(v) for v in settings["front_zone_deg"])
    weights = []
    for row, column in zip(point_rows, point_columns):
        in_front = settings["extend_slit"] and front_from <= angles[column] <= front_to
        pin_rows = settings["pin_rows_front"] if in_front else settings["pin_rows"]
        if row < pin_rows:
            weight = 1.0
        else:
            t = (row - pin_rows) / max(1, rows - 1 - pin_rows)
            weight = settings["goal_hem"] + (settings["goal_below_pin"] - settings["goal_hem"]) * (1.0 - t) ** settings["goal_curve"]
        weights.append(weight)
    return weights


def islands(count, edges):
    """辺でつながった頂点の島（頂点ごとの根の番号）。"""
    parent = list(range(count))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for u, v in edges:
        a, b = find(u), find(v)
        if a != b:
            parent[a] = b
    return [find(index) for index in range(count)]


def leg_islands(roots, top_groups, prefixes, min_island, min_leg):
    """当たりの相手に残す島: 頂点が min_island 以上で、いちばん重みの大きい頂点グループの名前が prefixes のどれかで始まる頂点を
    min_leg 以上持つ島。"""
    sizes = Counter(roots)
    leg_count = Counter(root for root, name in zip(roots, top_groups) if name.startswith(tuple(prefixes)))
    return {root for root, count in sizes.items() if count >= min_island and leg_count[root] >= min_leg}


def rest_value(data_path, array_index, sign):
    """助走の始まりのキーの値（骨の基本の姿勢）: 回転の四元数は w だけ sign（最初のコマの w の符号）、大きさは 1、ほかは 0。"""
    if data_path.endswith("rotation_quaternion"):
        return sign if array_index == 0 else 0.0
    if data_path.endswith("scale"):
        return 1.0
    return 0.0


# ---- Blender の中 ----

def world_points(obj):
    matrix = np.array(obj.matrix_world)
    local = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", local)
    local = local.reshape(-1, 3)
    return local @ matrix[:3, :3].T + matrix[:3, 3]


def main(argv=None):
    import bmesh  # Blender の中でだけ使える
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        try:
            settings = merged(json.load(stream))
        except ValueError as error:
            raise SystemExit(str(error)) from error

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"
                 and (not settings.get("armature") or obj.name == settings["armature"])]
    if not armatures:
        raise SystemExit(f"アーマチュアがありません{'（' + settings['armature'] + '）' if settings.get('armature') else ''}")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:
        raise SystemExit(f"1 Blender 単位 = 1 m のシーンだけを扱います（scale_length {scene.unit_settings.scale_length}）")
    for key in ("skirt", "body"):
        obj = scene.objects.get(settings[key])
        if obj is None or obj.type != "MESH":
            raise SystemExit(f"{key} のメッシュがシーンにありません: {settings[key]}")
    if settings["hips_bone"] not in armature.data.bones:
        raise SystemExit(f"{armature.name} に骨 {settings['hips_bone']} がありません")
    for name in (PROXY, COLLIDER, FLOOR):
        if name in bpy.data.objects:
            raise SystemExit(f"{name} がもうあります（cloth.skirt_proxy を 2 回かけない）")
    if armature.animation_data is None or armature.animation_data.action is None:
        raise SystemExit(f"{armature.name} に動き（アクション）がありません")
    skirt, body = scene.objects[settings["skirt"]], scene.objects[settings["body"]]
    end = settings.get("end") or scene.frame_end
    spring = getattr(getattr(armature.data, "vrm_addon_extension", None), "spring_bone1", None)
    if spring is not None and hasattr(spring, "enable_animation"):
        spring.enable_animation = False                 # 焼いている間に、VRM Add-on の揺れものの計算が骨を動かさないように
    armature.data.pose_position = "REST"                # ここから先の測定と貼り付けは初期姿勢で行う
    bpy.context.view_layer.update()

    hem_report = None
    if settings["hem_cut_z"] > 0:
        # 裾を水平に切り落とす（細かい裾のメッシュそのものを短くする。筒はこのあと短い裾から作られる）
        world = np.array(skirt.matrix_world)
        inverse_world = np.array(skirt.matrix_world.inverted())
        before = len(skirt.data.vertices)
        bm = bmesh.new()
        bm.from_mesh(skirt.data)
        plane_point = Vector(inverse_world[:3, :3] @ np.array([0.0, 0.0, settings["hem_cut_z"]]) + inverse_world[:3, 3])
        plane_normal = Vector(world[:3, :3].T @ np.array([0.0, 0.0, 1.0]))
        bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces), dist=1e-6,
                               plane_co=plane_point, plane_no=plane_normal, clear_inner=True, clear_outer=False)
        bm.to_mesh(skirt.data)
        bm.free()
        skirt.data.update()
        hem_report = {"cut_at_z": settings["hem_cut_z"], "verts_before_after": [before, len(skirt.data.vertices)]}

    def tree_of(points, mesh):
        return BVHTree.FromPolygons([Vector(p) for p in points], [tuple(polygon.vertices) for polygon in mesh.polygons])

    def hits_along(tree, origin, direction, reach=1.2):
        """origin から direction へ進んで当たる距離を、近い順にすべて返す。"""
        found = []
        travelled = 0.0
        start = Vector(origin)
        while travelled < reach:
            location, _, _, distance = tree.ray_cast(start, direction, reach - travelled)
            if location is None:
                break
            travelled += distance
            found.append(travelled)
            start = location + direction * 1e-4
            travelled += 1e-4
        return found

    # ---- 1. 裾の内側の面を測る ----
    skirt_points = world_points(skirt)
    skirt_tree = tree_of(skirt_points, skirt.data)
    z_top, z_bottom = skirt_points[:, 2].max(), skirt_points[:, 2].min()
    upper = skirt_points[skirt_points[:, 2] > z_top - 0.12]
    axis = np.array([upper[:, 0].mean(), upper[:, 1].mean()])
    columns, rows = settings["columns"], settings["rows"]
    angles, levels = grid_levels(z_top, z_bottom, columns, rows)
    directions = [Vector((math.cos(angle), math.sin(angle), 0.0)) for angle in angles]
    inner = np.full((rows, columns), np.nan)
    for j, z in enumerate(levels):
        for i, direction in enumerate(directions):
            found = hits_along(skirt_tree, (axis[0], axis[1], z), direction)
            if found:
                inner[j, i] = found[0]

    # ---- 2. 当たり判定の相手（脚の頂点グループの頂点を持つ大きな島だけ） ----
    names = {group.index: group.name for group in body.vertex_groups}
    top_groups = [names[max(vertex.groups, key=lambda item: item.weight).group] if len(vertex.groups) else ""
                  for vertex in body.data.vertices]
    roots = islands(len(body.data.vertices), [tuple(edge.vertices) for edge in body.data.edges])
    keep_roots = leg_islands(roots, top_groups, settings["leg_groups"], settings["collider_min_island"],
                             settings["collider_min_leg_vertices"])
    if not keep_roots:
        raise SystemExit(f"当たりの相手にする脚の島がありません（leg_groups {settings['leg_groups']} の頂点グループの頂点を "
                         f"{settings['collider_min_leg_vertices']} 以上持つ、{settings['collider_min_island']} 頂点以上の島）")
    collider = body.copy()
    collider.data = body.data.copy()
    collider.name = COLLIDER
    for collection in body.users_collection:
        collection.objects.link(collider)
    bm = bmesh.new()
    bm.from_mesh(collider.data)
    bm.verts.ensure_lookup_table()
    body_height = world_points(body)[:, 2]
    bmesh.ops.delete(bm, geom=[vertex for vertex in bm.verts if roots[vertex.index] not in keep_roots
                               or body_height[vertex.index] < settings["collider_cut_below"]], context="VERTS")
    bm.to_mesh(collider.data)
    bm.free()
    faces_before = len(collider.data.polygons)
    if settings["collider_ratio"] < 1.0:
        decimate = collider.modifiers.new("decimate", "DECIMATE")
        decimate.ratio = settings["collider_ratio"]
        with bpy.context.temp_override(object=collider, active_object=collider, selected_objects=[collider]):
            bpy.ops.object.modifier_move_to_index(modifier=decimate.name, index=0)
            bpy.ops.object.modifier_apply(modifier=decimate.name)
    for modifier in list(collider.modifiers):
        if modifier.type != "ARMATURE":
            collider.modifiers.remove(modifier)
    collider.hide_render = True
    collider.display_type = "WIRE"
    collider.modifiers.new("Collision", "COLLISION")
    collider.collision.thickness_outer = settings["collider_outer"]
    collider.collision.thickness_inner = settings["collider_inner"]
    collider.collision.cloth_friction = settings["friction"]
    removed_collisions = []
    for obj in bpy.data.objects:                      # ほかの物には当たり判定を残さない
        if obj is not collider and obj.type == "MESH":
            for modifier in list(obj.modifiers):
                if modifier.type == "COLLISION":
                    obj.modifiers.remove(modifier)
                    removed_collisions.append(obj.name)
    collider_tree = tree_of(world_points(collider), collider.data)
    legs = np.zeros((rows, columns))                  # 軸から見た、脚のいちばん外側までの距離（当たらなければ 0）
    for j, z in enumerate(levels):
        for i, direction in enumerate(directions):
            found = hits_along(collider_tree, (axis[0], axis[1], z), direction, reach=0.6)
            if found:
                legs[j, i] = found[-1]

    # ---- 筒の半径を決める ----
    radius, valid = fill_columns(inner)
    closed, removed = close_and_trim(radius, valid, settings["close_gap"], settings["min_strip"])
    cut_cells, cut_report = set(), None
    if settings["extend_slit"]:
        slit = slit_cut(valid, angles, levels, settings["pin_rows_front"])
        if slit is not None:
            cut_cells, cut_angle, cut_top, cut_bottom = slit
            cut_report = {"angle_deg": round(math.degrees(cut_angle), 1), "from_z": round(cut_top, 3), "to_z": round(cut_bottom, 3)}
    extra_cells, extra = extra_cuts(settings["extra_cuts_deg"], levels, settings["pin_rows_front"], rows, columns, z_bottom)
    cut_cells |= extra_cells
    smoothed = smooth_radius(radius, valid, settings["inner_percentile"])
    clearance_of_row = clearance_rows(rows, settings["pin_rows"], settings["clearance_top"], settings["clearance"])
    radius = proxy_radius(smoothed, valid, legs, clearance_of_row)

    # ---- 3. 細かい裾の頂点を、初期姿勢で脚の外へ出す ----
    moved, push, need, vertex_radius = push_from_legs(skirt_points, axis, levels, legs, clearance_of_row)
    inverse = np.array(skirt.matrix_world.inverted())
    local = moved @ inverse[:3, :3].T + inverse[:3, 3]
    skirt.data.vertices.foreach_set("co", local.reshape(-1))
    skirt.data.update()
    pushed = {"count": int(push.sum()), "max_mm": round(float((need - vertex_radius)[push].max() * 1000), 1) if push.any() else 0.0}

    def cut_detail(angle, high, low):
        """細かい裾に、軸から angle の向きへ出る縦の面で切り込みを入れる（高さ low〜high の範囲。幅 0 の切れ目）。"""
        world = np.array(skirt.matrix_world)
        inverse_world = np.array(skirt.matrix_world.inverted())
        bm = bmesh.new()
        bm.from_mesh(skirt.data)
        direction = np.array([math.cos(angle), math.sin(angle)])

        def world_of(vertex):
            return world[:3, :3] @ np.array(vertex.co) + world[:3, 3]

        def near_cut(point, bottom, top):
            offset = point[:2] - axis
            along = float(offset @ direction)
            across = abs(float(offset[0] * direction[1] - offset[1] * direction[0]))
            return along > 0.03 and across < 0.05 and bottom <= point[2] <= top

        region = [face for face in bm.faces if any(near_cut(world_of(v), low - 0.03, high + 0.01) for v in face.verts)]
        geometry = set(region)
        for face in region:
            geometry.update(face.edges)
            geometry.update(face.verts)
        plane_point = Vector(inverse_world[:3, :3] @ np.array([axis[0], axis[1], 0.0]) + inverse_world[:3, 3])
        plane_normal = Vector(world[:3, :3].T @ np.array([-direction[1], direction[0], 0.0]))
        result = bmesh.ops.bisect_plane(bm, geom=list(geometry), dist=1e-6, plane_co=plane_point, plane_no=plane_normal,
                                        clear_inner=False, clear_outer=False)
        cut_edges = [item for item in result["geom_cut"] if isinstance(item, bmesh.types.BMEdge)
                     and all(near_cut(world_of(v), low - 0.03, high) for v in item.verts)]
        bmesh.ops.split_edges(bm, edges=cut_edges)
        bm.to_mesh(skirt.data)
        bm.free()
        skirt.data.update()
        return len(cut_edges)

    if cut_report:
        cut_report["edges_split"] = cut_detail(cut_angle, cut_top, cut_bottom)
    extra_report = [{"angle_deg": round(math.degrees(angle), 1), "from_z": round(high, 3), "to_z": round(low, 3),
                     "edges_split": cut_detail(angle, high, low)} for angle, high, low in extra]

    # ---- 4. 身代わりの筒 ----
    proxy_points, proxy_rows, proxy_columns, faces = proxy_mesh(axis, angles, radius, valid, levels, cut_cells)
    mesh = bpy.data.meshes.new(PROXY)
    mesh.from_pydata(proxy_points, [], faces)
    mesh.update()
    bm = bmesh.new()                                   # どの面にも使われていない頂点を消す
    bm.from_mesh(mesh)
    loose = [vertex for vertex in bm.verts if not vertex.link_faces]
    loose_index = {vertex.index for vertex in loose}
    proxy_rows = [row for index, row in enumerate(proxy_rows) if index not in loose_index]
    proxy_columns = [column for index, column in enumerate(proxy_columns) if index not in loose_index]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    proxy = bpy.data.objects.new(PROXY, mesh)
    for collection in skirt.users_collection:
        collection.objects.link(proxy)
    proxy.hide_render = True
    proxy.display_type = "WIRE"
    follow = proxy.vertex_groups.new(name=settings["hips_bone"])
    follow.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
    pin = proxy.vertex_groups.new(name="pin")
    for index, weight in enumerate(pin_weights(proxy_rows, proxy_columns, angles, rows, settings)):
        pin.add([index], weight, "REPLACE")
    floor = None
    if settings["floor"] in ("collide", "clamp"):
        bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 0, 0))
        floor = bpy.context.object
        floor.name = FLOOR
        floor.hide_render = True
        floor.display_type = "WIRE"
        if settings["floor"] == "collide":
            floor.modifiers.new("Collision", "COLLISION")
            floor.collision.thickness_outer = 0.004
            floor.collision.cloth_friction = settings["floor_friction"]

    def clamp_to_floor(obj, offset):
        """床より下へ行った頂点を、床の高さ（＋offset）へ戻す。床より上の頂点は動かさない。"""
        modifier = obj.modifiers.new("keep_above_floor", "SHRINKWRAP")
        modifier.target = floor
        modifier.wrap_method = "NEAREST_SURFACEPOINT"
        modifier.wrap_mode = "OUTSIDE"
        modifier.offset = offset
        return modifier

    proxy_armature = proxy.modifiers.new("follow_hips", "ARMATURE")
    proxy_armature.object = armature
    cloth = proxy.modifiers.new("skirt_cloth", "CLOTH")
    cloth.show_viewport = cloth.show_render = False      # 貼り付けが済むまで止めておく
    if settings["floor"] == "clamp":
        clamp_to_floor(proxy, settings["floor_offset"])
    if settings["smooth_iterations"] > 0:
        smooth = proxy.modifiers.new("smooth_wrinkles", "SMOOTH")
        smooth.iterations = settings["smooth_iterations"]
        smooth.factor = settings["smooth_factor"]
    bpy.context.view_layer.update()

    # ---- 5. 細かい裾を筒に貼り付ける ----
    for modifier in list(skirt.modifiers):
        if modifier.type in {"ARMATURE", "CLOTH", "SHRINKWRAP", "SURFACE_DEFORM"}:
            skirt.modifiers.remove(modifier)
    surface = skirt.modifiers.new("follow_proxy", "SURFACE_DEFORM")
    surface.target = proxy
    surface.falloff = settings["bind_falloff"]
    bpy.context.view_layer.objects.active = skirt
    with bpy.context.temp_override(object=skirt, active_object=skirt, selected_objects=[skirt]):
        bpy.ops.object.surfacedeform_bind(modifier=surface.name)
    bpy.context.view_layer.update()
    bpy.context.evaluated_depsgraph_get().update()
    if not surface.is_bound:
        raise SystemExit("細かい裾を筒に貼り付けられませんでした（Surface Deform の bind に失敗）")
    if floor is not None:
        clamp_to_floor(skirt, settings["floor_offset"] * 0.5)
    if settings["pushout_after"] > 0:
        pushout = skirt.modifiers.new("pushout_body", "SHRINKWRAP")
        pushout.target = collider
        pushout.wrap_method = "NEAREST_SURFACEPOINT"
        pushout.wrap_mode = "OUTSIDE"
        pushout.offset = settings["pushout_after"]

    # ---- 6. 助走をつけて、布を計算する ----
    first_frame = scene.frame_start
    action = armature.animation_data.action
    start = first_frame - settings["preroll"] - settings["hold"]
    hold_from = first_frame - settings["hold"]
    sign_of = {}
    curves = [curve for bag in fcurve_bags(action) for curve in bag.fcurves if curve.data_path.startswith('pose.bones["')]
    for curve in curves:
        if curve.data_path.endswith("rotation_quaternion") and curve.array_index == 0:
            sign_of[curve.data_path] = -1.0 if curve.evaluate(first_frame) < 0 else 1.0
    for curve in curves:
        value = curve.evaluate(first_frame)
        for frame, key_value in ((hold_from, value), (start, rest_value(curve.data_path, curve.array_index,
                                                                         sign_of.get(curve.data_path, 1.0)))):
            point = curve.keyframe_points.insert(frame=frame, value=key_value, options={"FAST"})
            point.interpolation = "LINEAR"
        curve.update()

    cloth.show_viewport = cloth.show_render = True
    c = cloth.settings
    c.quality = settings["quality"]
    c.mass = settings["mass"]
    c.air_damping = settings["air_damping"]
    c.tension_stiffness, c.compression_stiffness = settings["tension"], settings["compression"]
    c.shear_stiffness, c.bending_stiffness = settings["shear"], settings["bending"]
    c.tension_damping, c.compression_damping = settings["tension_damping"], settings["compression_damping"]
    c.shear_damping, c.bending_damping = settings["shear_damping"], settings["bending_damping"]
    c.vertex_group_mass = "pin"
    c.pin_stiffness = settings["pin_stiffness"]
    c.effector_weights.gravity = settings["gravity"]
    cc = cloth.collision_settings
    cc.use_collision = True
    cc.collision_quality = settings["collision_quality"]
    cc.distance_min = settings["distance_min"]
    cc.use_self_collision = settings["self_collision"]
    cloth.point_cache.frame_start = start
    cloth.point_cache.frame_end = end
    armature.data.pose_position = "POSE"
    bpy.context.view_layer.update()

    report = {"schema_version": "model3d-skirt-proxy/0.1", "blender": bpy.app.version_string, "armature": armature.name,
              "axis_xy": [round(float(v), 4) for v in axis], "skirt_z": [round(float(z_bottom), 3), round(float(z_top), 3)],
              "grid": [rows, columns], "proxy_verts": len(mesh.vertices), "proxy_faces": len(mesh.polygons),
              "open_cells_by_row": [int((~valid[j]).sum()) for j in range(rows)], "gap_cells_closed": closed,
              "strip_cells_removed": removed, "collider_islands": len(keep_roots),
              "collider_faces_before_after": [faces_before, len(collider.data.polygons)],
              "removed_collisions": sorted(set(removed_collisions)), "skirt_verts_pushed_out_at_rest": pushed,
              "hem_cut": hem_report, "slit_extended": cut_report, "extra_cuts": extra_report, "bound": surface.is_bound,
              "frames": [start, end], "preroll_keys": [start, hold_from], "preroll_curves": len(curves), "baked": False}
    if settings.get("bake", True):
        started = time.time()
        bpy.context.view_layer.objects.active = proxy
        with bpy.context.temp_override(scene=scene, active_object=proxy, object=proxy, point_cache=cloth.point_cache):
            bpy.ops.ptcache.bake(bake=True)
        seconds = time.time() - started
        report.update({"baked": cloth.point_cache.is_baked, "seconds": round(seconds, 1),
                       "seconds_per_frame": round(seconds / max(1, end - start + 1), 2)})
        if not cloth.point_cache.is_baked:
            raise SystemExit("布の計算を焼けませんでした")
    scene.frame_set(first_frame)
    out = os.path.abspath(a.out_blend)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    report["settings"] = settings
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"proxy_verts": report["proxy_verts"], "baked": report["baked"], "out_blend": out}))


if __name__ == "__main__":
    main()
