"""Blender の中で動かす: 塗りと輪郭線を付けたキャラを、舞台と 4 つのスポットの中で、コマごとに PNG に描く。

    blender --background --factory-startup --python stage_render.py -- --blend in.blend --settings settings.json \
        --frames-dir frames --report report.json [--track track.json]

settings.json（render.stage が書く）:
- resolution（幅・高さ）、samples（EEVEE の 1 画素の標本の数）、raytracing（画面の光線追跡）、bloom（光のにじみ）、
  bone_prefix（腰の骨 <prefix>Hips）、fps（省くとトラックの fps、それも無ければ .blend の値）
- camera: トラックにカメラの無いコマのカメラ。腰の通り道を follow_sigma コマのガウスでならし、その follow 倍の所の高さ
  aim を狙う。方位 az（度。0 で正面。キャラは -Y を向く）、水平の距離 dist、高さ height、レンズ lens、縦の受光の大きさ
  sensor_height（mm）
- outline: 輪郭線の写し（look.toon の model3d_outline の印）の太さを、カメラから腰までの距離で、高さ reference_height の
  画で line_px 画素になるように書き換える。scale は元のメッシュの名前 → 太さの倍率
- stage: enabled（舞台・光・空を作る）、stripe_floor（壁の暗い帯の明るさ。明るい帯の色に掛ける）、stripe_soft（帯の境の幅）
- background: 省くと舞台の空か .blend の空。"transparent" は透けの RGBA の PNG、{"color": [R, G, B]}（0〜255）はその色の背景
  （緑抜き用。透けで描いて合成で敷くので、背景の色で人物を照らさない。ディザーは 0）。どちらも stage.enabled を false にする
- push: 描く直前の押し出しの並び。各要素は meshes（動かすメッシュ。outlines が true なら、その輪郭線の写しも）、colliders
  （当たり。閉じたメッシュ）、groups（動かす頂点の組の名前と離す量 offset_mm。並べた順で最初に重みのある組の量）、smooth
  （動かす量をならす回数）。コマごとに、組の頂点のうち当たりの中か近くにある所を、当たりの面から offset_mm だけ外へ出す。
  中か外かは 5 本の光線が当たりの面と奇数回交わる向きの多さで決める。動かす量は面のつながりでならし、組の重みを掛け、骨で
  動かす前の差に直してシェイプキー model3d_push に書く（リポジトリ外の試作の帯の直しと同じ手順）
- only: 描くコマの T（省くと全部）
track.json（model3d-stage-track/0.1）: 描くコマの並びと、コマごとのカメラ・舞台・光・塗りの値。省くと、.blend のシーンの
全コマを T = 1 から順に、既定のカメラと動かない光で描く。

舞台と光（リポジトリ外の試作と同じ組み方）:
- 床: 40 m の板。暗い Principled（Base 0.006・0.006・0.01、Roughness 0.24、Specular 0.6）で、EEVEE の画面の反射を受ける。
- LED の壁（y = 7 m、幅 32 m・高さ 7 m・厚さ 0.1 m）: 物体の座標の Wave（帯）を Color Ramp（0.66〜0.76）で帯にし、暗い帯と
  明るい帯の色を混ぜて Emission で出す。模様は scroll だけ横にずれる。stage の led は横の帯（Scale 1.6・Distortion 1.5）、
  led_diag は斜めの帯（Scale 3.0・Distortion 6.0）、void は壁・塔・トラスを描かない。
- 上のトラスと両脇の塔（金属の Principled）、光の柱 8 本（細い側を上にした長さ 12 m の円錐。発光と透明を足した半透明で、
  先へ向かって、また縁ほど暗い）。
- スポット 4 つ（どれも (0, 0, 1) を向き、ぼかし 0.5）: 主光（0.8, -5, 5）2600 W・影の半径 1.2 m、補助光（-1.5, -4, 1.2）
  650 W・影なし、左右の輪郭光（-4, 4.5, 5.5）・（4, 4.5, 5.5）。
- 空: ほぼ黒（0.002, 0.002, 0.004）。EEVEE、色の変換は Standard（Look なし）、合成で Glare の Bloom（質 High）。
長さ（トラックのカメラ、既定のカメラの距離と高さ、舞台）は m で、シーンの scale_length（1 Blender 単位が何 m か）で直す。
舞台のメッシュは 1/scale_length 倍の親 model3d_stage_root の下に m のまま作り、光は強さを 1/scale_length² 倍、影の半径を
1/scale_length 倍にする。カメラの写る範囲と画面の反射の厚みも直す（単位によらず同じに見えるように）。
コマごと: 動きのコマ f へ進め、トラックの値（壁の模様・明るさ・色、輪郭光、光の柱、塗りの共有グループ、カメラ）を入れ、
輪郭線の太さを書き換えて描く。書き出すファイルがすでにあるコマはとばす（続きから描ける）。
**検証**: 本物の Blender 4.5.15 で、リポジトリ外の試作と同じキャラ・同じ値から、同じ画素になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

DEFAULTS = {
    "resolution": [1080, 1920], "samples": 64, "raytracing": True, "bloom": True, "bone_prefix": "mixamorig:", "fps": None,
    "camera": {"sensor_height": 24.0, "lens": 35.0, "az": 0.0, "dist": 3.6, "height": 1.1, "aim": 0.95, "follow": 0.6,
               "follow_sigma": 6.0},
    "outline": {"line_px": 2.4, "reference_height": 1920, "scale": {}},
    "stage": {"enabled": True, "stripe_floor": 0.02, "stripe_soft": 0.0},
    "background": None,
    "push": [],
    "only": None,
}
TONE = "model3d_stage_tone"
WAVE = {"led": ("X", 1.5, 1.6), "led_diag": ("DIAGONAL", 6.0, 3.0), "void": ("X", 1.5, 1.6)}   # 帯の向き・Distortion・Scale
VIOLET, PINK = (0.30, 0.09, 1.0), (1.0, 0.05, 0.45)       # 塗りの共有グループが無いときの、既定の縁の色
BEAMS = 8
PUSH_RAYS = ((1, 0.13, 0.07), (-0.11, 1, 0.05), (0.06, -0.09, 1), (-1, -0.08, -0.12), (0.1, -1, -0.07))   # 中か外かを調べる光線
PUSH_MARGIN_M = 0.02      # 当たりの箱をこれだけ広げた中の頂点だけを調べる
PUSH_STEP_M = 1e-5        # 光線が面を越えて次の面を探すときに進める長さ
PUSH_MOVED_M = 0.001      # 報告で「動かした」と数える量
PUSH_KEY = "model3d_push"


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="stage_render.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--track")
    return ap.parse_args(argv)


def merged(settings):
    """既定の数に settings を重ねる（camera・outline・stage は中の鍵ごと）。"""
    out = {**DEFAULTS, **{k: v for k, v in settings.items() if k not in ("camera", "outline", "stage")}}
    for key in ("camera", "outline", "stage"):
        out[key] = {**DEFAULTS[key], **settings.get(key, {})}
    return out


def frame_name(T):
    return f"f{int(T):04d}.png"


def frame_plan(track, frame_start, frame_end):
    """描くコマの並び [(T, f, そのコマの値)]。トラックがあればその順、無ければシーンの全コマを T = 1 から。"""
    if track is not None:
        return [(int(fr["T"]), int(fr["f"]), fr) for fr in track["frames"]]
    return [(k + 1, f, {}) for k, f in enumerate(range(frame_start, frame_end + 1))]


def gauss_smooth(values, sigma):
    """(n, k) の列を、列ごとに σ のガウス（±round(2σ)、端は端の値で延ばす）でならす（試作と同じ）。"""
    values = np.asarray(values, float)
    r = int(round(2 * sigma))
    w = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    w /= w.sum()
    return np.stack([np.convolve(np.pad(values[:, k], r, mode="edge"), w, mode="valid") for k in range(values.shape[1])],
                    axis=1)


def orbit(target, az_deg, dist, height):
    """狙う点 target から、方位 az（度。0 で -Y 側の正面、正で +X 側）・水平の距離 dist・高さ height のカメラの位置。"""
    r = math.radians(az_deg)
    return (target[0] + math.sin(r) * dist, target[1] - math.cos(r) * dist, height)


def outline_width(distance, lens, sensor_height, line_px, reference_height, res_y):
    """カメラから distance（m）の所で、高さ reference_height の画で line_px 画素になる線の太さ（m）。
    試作と同じ順に計算する（結果は解像度によらない）。"""
    px_per_m = res_y / 2 / (sensor_height / 2 / lens * distance)
    return line_px * (res_y / reference_height) / px_per_m


def light_energy(watts, unit):
    """m で決めた光の強さ（W）を、1 Blender 単位が unit m のシーンで同じ明るさにする強さ。光は Blender 単位の距離の 2 乗で
    弱まるので、1/unit² 倍にする。"""
    return watts / unit ** 2


def hull_strength(width_bu, unit, per, scale):
    """輪郭線の写しの Displace の強さ（中の単位。外へ太らせるので負）。width_bu は Blender 単位の太さ。unit（シーンの
    scale_length。1 Blender 単位が何 m か）で m に直してから、写しの換算 per（中の単位 1 が何 m か）で割る。"""
    return -(width_bu * unit / per) * scale


def tone_values(spec, initial):
    """そのコマの塗りの共有グループの値: .blend の最初の値に、トラックの値を重ねる（省いた値は、前のコマでなく最初の値に戻る）。"""
    return {**initial, **spec.get("tone", {})}


def static_values(tone_colors, stripe_floor):
    """トラックに無いコマの、動かない光と壁の値（色は塗りの共有グループの a・b）。"""
    a, b = (list(c) for c in tone_colors)
    return {"stage": "led",
            "wall": {"strength": 1.0, "color": a, "dark": [v * stripe_floor for v in a], "scroll": 0.0},
            "rims": [{"energy": 1500.0, "color": a}, {"energy": 1500.0, "color": b}],
            "beams": {"gain": 0.9, "rotations": [[math.radians(-18.0), math.radians(-12.0 if j < BEAMS // 2 else 12.0), 0.0]
                                                 for j in range(BEAMS)],
                      "colors": [b if j % 2 == 0 else a for j in range(BEAMS)]}}


def frame_values(spec, static, stripe_floor):
    """そのコマの舞台と光の値: 動かない値に、トラックの値を部分ごとに重ねる。暗い帯の色を省いて明るい帯の色を渡すと、
    明るい帯の色 × stripe_floor にする。"""
    out = {"stage": spec.get("stage", static["stage"])}
    wall = {**static["wall"], **spec.get("wall", {})}
    if "color" in spec.get("wall", {}) and "dark" not in spec["wall"]:
        wall["dark"] = [v * stripe_floor for v in wall["color"]]
    out["wall"] = wall
    out["rims"] = [{**s, **t} for s, t in zip(static["rims"], spec.get("rims", [{}, {}]))]
    out["beams"] = {**static["beams"], **spec.get("beams", {})}
    return out


def push_weights(vertex_groups, groups):
    """頂点の {組の名前: 重み} と、組の並び [(名前, 離す量), ...] から (動かす割合, 離す量)。割合は組の重みの最大、離す量は
    並べた順で最初に重みのある組の量。どの組にも重みが無ければ None。"""
    w = max(vertex_groups.get(name, 0.0) for name, _ in groups)
    if w <= 0:
        return None
    return w, next(offset for name, offset in groups if vertex_groups.get(name, 0.0) > 0)


def clearly_outside(p, q, normal, dist, offset):
    """当たりの面のいちばん近い点 q から、面の外側の向きにはっきり離れている頂点 p（光線で調べなくてよい）。"""
    return dist > offset and (p - q) @ normal / max(dist, 1e-9) > 0.5


def push_correction(p, q, dist, offset, inside):
    """頂点 p を、当たりの面のいちばん近い点 q から offset だけ外へ出す動き。中なら面を越えて外へ、外でも offset より近ければ
    離す。どちらでもなければ None。"""
    if inside:
        u = (q - p) / max(dist, 1e-9)
    elif dist < offset:
        u = (p - q) / max(dist, 1e-9)
    else:
        return None
    return q + u * offset - p


def spread_push(need, edges, iterations):
    """動かす量を、面のつながり（edges は頂点の組の番号の対）でならす。iterations 回、隣の平均と半々に混ぜ、各頂点の要る
    向きの量は下回らないようにする。戻り: (ならした動き, 要った量の大きさ)。"""
    need = np.asarray(need, float)
    mag = np.linalg.norm(need, axis=1)
    dirs = need / np.maximum(mag[:, None], 1e-9)
    d = need.copy()
    if len(edges) and mag.max(initial=0.0) > 0:
        deg = np.bincount(edges.ravel(), minlength=len(need)).astype(float)
        for _ in range(iterations):
            acc = np.zeros_like(d)
            np.add.at(acc, edges[:, 0], d[edges[:, 1]])
            np.add.at(acc, edges[:, 1], d[edges[:, 0]])
            avg = np.where(deg[:, None] > 0, acc / np.maximum(deg[:, None], 1), d)
            d = 0.5 * d + 0.5 * avg
            short = np.maximum(mag - (d * dirs).sum(1), 0)
            d = d + dirs * short[:, None]
    return d, mag


def vertex_count_problem(name, base, evaluated, modifiers, frame=None):
    """押し出しは、元のメッシュの頂点の番号で、評価した（モディファイアをかけた）形の頂点を読む。頂点の数が変わると
    番号がずれるので、そのときの説明（変わらなければ None）。modifiers はビューポートで効いているモディファイアの名前。"""
    if base == evaluated:
        return None
    when = f"コマ {frame} で、" if frame is not None else ""
    return (f"push: {when}{name} のモディファイア（{', '.join(modifiers) or 'なし'}）で頂点の数が変わります（{base} → {evaluated}）。"
            "押し出しは頂点の並びが変わらないメッシュにだけ使えます。Mask・Decimate・Subdivision などは、先に適用するか、"
            "ビューポートで切ってください（測るのはビューポートの形）")


def blend_bone_rotations(weights, bone_rots):
    """頂点ごとの骨の重み（行の和が 1。骨の重みの無い頂点は 0 の行）と、骨ごとの 3×3 の動きから、頂点ごとの 3×3（重みで
    混ぜた線形の骨の動き）。骨の重みの無い頂点と、骨の無いときは、骨で動かないので単位行列にする。"""
    weights = np.asarray(weights, float)
    out = np.tile(np.eye(3), (len(weights), 1, 1))
    if len(weights) and len(bone_rots):
        rigged = weights.sum(1) > 0
        out[rigged] = np.einsum("vb,bij->vij", weights[rigged], np.asarray(bone_rots, float))
    return out


def weld_groups(points, tol=1e-5):
    """同じ位置の頂点（UV の継ぎ目で分かれた頂点）の組の番号。"""
    _, inverse = np.unique(np.round(np.asarray(points, float) / tol).astype(np.int64), axis=0, return_inverse=True)
    return inverse.ravel()


def weld_mean(d, groups):
    """同じ位置の頂点の動きを、その組の平均にそろえる（継ぎ目で裂けないように）。"""
    acc = np.zeros((groups.max() + 1, 3))
    np.add.at(acc, groups, d)
    return (acc / np.bincount(groups).astype(float)[:, None])[groups]


# ---------- ここから下は Blender の中でだけ動く ----------

def build_stage(bpy, stage, unit=1.0):
    """舞台（床・LED の壁・トラスと塔・光の柱）と光と空を作る。戻り: コマごとに書き換える物。
    大きさと置き場所は m で決めてある。メッシュは 1/unit 倍の親 model3d_stage_root の下に m のまま作り（模様は物体の中の座標で
    決まるので変わらない）、光は Blender 単位の場所に置いて、強さを 1/unit² 倍・影の半径を 1/unit 倍にする（距離の 2 乗で弱まる
    ため）。unit はシーンの scale_length（1 Blender 単位が何 m か）。"""
    from mathutils import Vector
    sc = bpy.context.scene
    coll = bpy.data.collections.new("model3d_stage")
    sc.collection.children.link(coll)
    vl = bpy.context.view_layer
    vl.active_layer_collection = vl.layer_collection.children[coll.name]
    root = bpy.data.objects.new("model3d_stage_root", None)
    coll.objects.link(root)
    root.scale = (1.0 / unit,) * 3

    def emit_mat(name, blend=False):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        em = nt.nodes.new("ShaderNodeEmission")
        if blend:
            add = nt.nodes.new("ShaderNodeAddShader")
            tr = nt.nodes.new("ShaderNodeBsdfTransparent")
            nt.links.new(em.outputs[0], add.inputs[0])
            nt.links.new(tr.outputs[0], add.inputs[1])
            nt.links.new(add.outputs[0], out.inputs[0])
            m.surface_render_method = "BLENDED"
        else:
            nt.links.new(em.outputs[0], out.inputs[0])
        return m, em

    def box(name, loc, size, mat):
        bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
        o = bpy.context.object
        o.name, o.scale = name, size
        bpy.ops.object.transform_apply(scale=True)
        o.data.materials.append(mat)
        o.parent = root
        return o

    def principled(name, values):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        b = [n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"][0]
        for key, value in values.items():
            b.inputs[key].default_value = value
        return m

    bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, 0))
    floor = bpy.context.object
    floor.name = "stage_floor"
    floor.parent = root
    floor.data.materials.append(principled("stage_floor", {"Base Color": (0.006, 0.006, 0.01, 1), "Roughness": 0.24,
                                                           "Specular IOR Level": 0.6}))
    wall_m, wall_e = emit_mat("stage_led")
    nt = wall_m.node_tree
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    wv = nt.nodes.new("ShaderNodeTexWave")
    wv.bands_direction = "X"
    wv.inputs["Scale"].default_value = 1.6
    wv.inputs["Distortion"].default_value = 1.5
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.66 - stage["stripe_soft"]
    ramp.color_ramp.elements[1].position = 0.76 + stage["stripe_soft"]
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    nt.links.new(tc.outputs["Object"], mp.inputs[0])
    nt.links.new(mp.outputs[0], wv.inputs["Vector"])
    nt.links.new(wv.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs[0], mix.inputs[0])          # Factor
    nt.links.new(mix.outputs[2], wall_e.inputs["Color"])  # Result（色）
    walls = [box("stage_wall", (0, 7.0, 3.5), (32.0, 0.1, 7.0), wall_m)]
    tm = principled("stage_truss", {"Base Color": (0.05, 0.05, 0.06, 1), "Metallic": 0.8, "Roughness": 0.4})
    walls += [box("stage_tower", (x, 6.5, 3.8), (0.4, 0.4, 7.6), tm) for x in (-16.5, 16.5)]
    walls += [box("stage_truss", (0, y, 7.6), (33.4, 0.4, 0.4), tm) for y in (6.5, 0.5)]
    walls += [box("stage_truss_side", (x, 3.5, 7.6), (0.4, 6.4, 0.4), tm) for x in (-16.5, -6.0, 6.0, 16.5)]
    beams = []
    for i in range(BEAMS):
        bpy.ops.mesh.primitive_cone_add(vertices=24, radius1=1.0, radius2=0.05, depth=12.0, location=(0, 0, -6.0),
                                        end_fill_type="NOTHING")
        c = bpy.context.object
        bpy.ops.object.transform_apply(location=True)    # 器具（細い側）を原点にする
        c.name = f"stage_beam{i}"
        c.location = (-9.8 + i * 2.8, 6.2, 7.4)
        c.rotation_mode = "XYZ"
        c.parent = root
        m, em = emit_mat(f"stage_beam{i}", blend=True)
        n2 = m.node_tree
        gc = n2.nodes.new("ShaderNodeTexCoord")
        sep = n2.nodes.new("ShaderNodeSeparateXYZ")
        mr = n2.nodes.new("ShaderNodeMapRange")
        mr.inputs["From Min"].default_value = -7.5
        mr.inputs["From Max"].default_value = 0.0
        gain = n2.nodes.new("ShaderNodeMath")
        gain.operation = "MULTIPLY"
        n2.links.new(gc.outputs["Object"], sep.inputs[0])
        n2.links.new(sep.outputs["Z"], mr.inputs["Value"])
        sq = n2.nodes.new("ShaderNodeMath")              # 先へ向かって早く消える
        sq.operation = "POWER"
        sq.inputs[1].default_value = 1.6
        n2.links.new(mr.outputs[0], sq.inputs[0])
        lw = n2.nodes.new("ShaderNodeLayerWeight")       # 円錐の縁をやわらかくする
        lw.inputs["Blend"].default_value = 0.5
        inv = n2.nodes.new("ShaderNodeMath")
        inv.operation = "POWER"
        inv.inputs[1].default_value = 3.0
        one = n2.nodes.new("ShaderNodeMath")
        one.operation = "SUBTRACT"
        one.inputs[0].default_value = 1.0
        n2.links.new(lw.outputs["Facing"], one.inputs[1])
        n2.links.new(one.outputs[0], inv.inputs[0])
        both = n2.nodes.new("ShaderNodeMath")
        both.operation = "MULTIPLY"
        n2.links.new(sq.outputs[0], both.inputs[0])
        n2.links.new(inv.outputs[0], both.inputs[1])
        n2.links.new(both.outputs[0], gain.inputs[0])
        n2.links.new(gain.outputs[0], em.inputs["Strength"])
        c.data.materials.append(m)
        beams.append((c, em, gain))

    def spot(name, loc, energy, color, size):
        d = bpy.data.lights.new(name, "SPOT")
        d.energy, d.color, d.spot_size, d.spot_blend = light_energy(energy, unit), color, size, 0.5
        d.shadow_soft_size = d.shadow_soft_size / unit
        o = bpy.data.objects.new(name, d)
        coll.objects.link(o)
        o.location = Vector(loc) / unit
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = (Vector((0, 0, 1.0)) - Vector(loc)).to_track_quat("-Z", "Y")
        return d

    key = spot("stage_key", (0.8, -5.0, 5.0), 2600, (1.0, 0.97, 0.95), 0.7)
    fill = spot("stage_fill", (-1.5, -4.0, 1.2), 500, (0.8, 0.8, 1.0), 1.2)
    rim_a = spot("stage_rimA", (-4.0, 4.5, 5.5), 1500, VIOLET, 0.9)
    rim_b = spot("stage_rimB", (4.0, 4.5, 5.5), 1500, PINK, 0.9)
    key.shadow_soft_size = 1.2 / unit                   # 硬い影が脚や顔の形の歪みに見えるので、やわらかくする
    fill.energy, fill.use_shadow = light_energy(650, unit), False
    world = bpy.data.worlds.new("model3d_world")
    sc.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.002, 0.002, 0.004, 1)
    vl.active_layer_collection = vl.layer_collection
    return {"wall_emission": wall_e, "wall_mix": mix, "wall_mapping": mp, "wall_wave": wv, "walls": walls, "beams": beams,
            "rims": (rim_a, rim_b), "unit": unit}


def set_stage(handles, kind):
    for o in handles["walls"]:
        o.hide_render = (kind == "void")
    wave = handles["wall_wave"]
    wave.bands_direction, distortion, scale = WAVE[kind]
    wave.inputs["Distortion"].default_value = distortion
    wave.inputs["Scale"].default_value = scale


def set_lights(handles, values):
    w = values["wall"]
    handles["wall_emission"].inputs["Strength"].default_value = w["strength"]
    handles["wall_mix"].inputs[6].default_value = (*w["dark"], 1)       # A（暗い帯）
    handles["wall_mix"].inputs[7].default_value = (*w["color"], 1)      # B（明るい帯）
    handles["wall_mapping"].inputs["Location"].default_value = (w["scroll"], 0, 0)
    for light, rim in zip(handles["rims"], values["rims"]):
        light.energy = light_energy(rim["energy"], handles["unit"])
        light.color = rim["color"]
    b = values["beams"]
    for (c, em, gain), rotation, color in zip(handles["beams"], b["rotations"], b["colors"]):
        c.rotation_euler = rotation
        gain.inputs[1].default_value = b["gain"]
        em.inputs["Color"].default_value = (*color, 1)


def apply_render(bpy, settings):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE_NEXT"
    sc.eevee.taa_render_samples = settings["samples"]
    sc.eevee.use_raytracing = settings["raytracing"]
    sc.eevee.ray_tracing_method = "SCREEN"
    sc.render.resolution_x, sc.render.resolution_y = settings["resolution"]
    sc.render.resolution_percentage = 100
    sc.render.use_motion_blur = False                   # キーフレームを使わず、描く前に値を入れるので、動きのぶれは使えない
    background = settings.get("background")
    sc.render.film_transparent = background is not None    # 色の背景も、透けで描いてから合成で敷く（空の色で人物を照らさない）
    image = sc.render.image_settings
    image.file_format, image.color_mode, image.color_depth, image.compression = "PNG", "RGB", "8", 15
    if background == "transparent":
        image.color_mode = "RGBA"
    sc.view_settings.view_transform, sc.view_settings.look = "Standard", "None"
    color = background.get("color") if isinstance(background, dict) else None
    if color is not None:
        sc.render.dither_intensity = 0.0                 # 背景の色を画素まで決まった値にする（緑抜きのキーの色）
    sc.use_nodes = bool(settings["bloom"]) or color is not None
    if sc.use_nodes:
        tree = sc.node_tree
        tree.nodes.clear()
        rl = tree.nodes.new("CompositorNodeRLayers")
        comp = tree.nodes.new("CompositorNodeComposite")
        out = rl.outputs["Image"]
        if settings["bloom"]:
            gl = tree.nodes.new("CompositorNodeGlare")
            gl.glare_type, gl.quality = "BLOOM", "HIGH"
            tree.links.new(out, gl.inputs["Image"])
            out = gl.outputs["Image"]
        if color is not None:
            bg = tree.nodes.new("CompositorNodeRGB")
            bg.outputs[0].default_value = (*srgb_to_linear(color), 1.0)
            over = tree.nodes.new("CompositorNodeAlphaOver")
            tree.links.new(bg.outputs[0], over.inputs[1])
            tree.links.new(out, over.inputs[2])
            out = over.outputs["Image"]
        tree.links.new(out, comp.inputs["Image"])


def srgb_to_linear(color):
    """0〜255 の sRGB の色を、合成の線形の値に（Standard の表示で同じ 0〜255 に戻る）。"""
    out = []
    for v in color:
        c = v / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def hip_path(bpy, arm, hips, frames, sigma):
    """動きのコマごとの、ならした腰の位置（世界）。骨だけを動かして測る（部品の変形は止めて速くする。後で戻す）"""
    sc = bpy.context.scene
    mods = [(m, m.show_viewport) for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers]
    for m, _ in mods:
        m.show_viewport = False
    points = []
    for f in frames:
        sc.frame_set(f)
        points.append(np.array(arm.matrix_world @ hips.head))
    for m, shown in mods:
        m.show_viewport = shown
    return {f: p for f, p in zip(frames, gauss_smooth(points, sigma))}


def viewport_modifiers(ob):
    """ビューポートで効いているモディファイアの「名前（種類）」。"""
    return [f"{m.name}（{m.type}）" for m in ob.modifiers if m.show_viewport]


def evaluated_points(bpy, ob):
    """評価した（モディファイアをかけた）メッシュの頂点の世界の座標と、面の頂点の並び。"""
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    co = np.zeros(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    polys = [tuple(p.vertices) for p in me.polygons]
    ev.to_mesh_clear()
    M = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3], polys


def inside_mesh(tree, p, rays, step):
    """点 p が閉じたメッシュ（BVH の tree）の中か。光線ごとに面と交わる数を数え、奇数の向きが 3 本以上なら中。"""
    from mathutils import Vector
    votes = 0
    for d in rays:
        hits, o = 0, Vector(p)
        for _ in range(12):
            loc, _, _, _ = tree.ray_cast(o, d)
            if loc is None:
                break
            hits += 1
            o = loc + d * step
        votes += hits % 2
    return votes >= 3


def open_edges(ob):
    """面 1 枚にしか使われない辺の数（0 なら閉じている）。"""
    count = {}
    for poly in ob.data.polygons:
        for key in poly.edge_keys:
            count[key] = count.get(key, 0) + 1
    return sum(1 for v in count.values() if v == 1)


class PushTarget:
    """押し出すメッシュ 1 つ: 組に重みのある頂点、その割合と離す量（Blender 単位）、骨の重み、面のつながり、継ぎ目の組、
    シェイプキー。arm はメッシュを動かすアーマチュア（Armature モディファイアが無ければ None。骨で動かない）。"""

    def __init__(self, ob, arm, groups, key):
        self.ob, self.arm = ob, arm
        me = ob.data
        names = {vg.index: vg.name for vg in ob.vertex_groups}
        deform = {b.name for b in arm.data.bones if b.use_deform} if arm is not None else set()
        idx, wt, off, dw = [], [], [], []
        for v in me.vertices:
            gs = {names[g.group]: g.weight for g in v.groups}
            picked = push_weights(gs, groups)
            if picked is None:
                continue
            idx.append(v.index)
            wt.append(picked[0])
            off.append(picked[1])
            dw.append({k: x for k, x in gs.items() if k in deform and x > 0})
        self.idx, self.w, self.off = np.array(idx, int), np.array(wt), np.array(off)
        self.bones = sorted({b for d in dw for b in d})
        column = {b: i for i, b in enumerate(self.bones)}
        self.W = np.zeros((len(idx), len(self.bones)))
        for r, d in enumerate(dw):
            for b, x in d.items():
                self.W[r, column[b]] = x
        self.W /= np.maximum(self.W.sum(1, keepdims=True), 1e-9)
        pos = {v: i for i, v in enumerate(idx)}
        edges = [(pos[a], pos[b]) for a, b in (tuple(e.vertices) for e in me.edges) if a in pos and b in pos]
        self.edges = np.array(edges) if edges else np.zeros((0, 2), int)
        co = np.zeros(len(me.vertices) * 3)
        me.vertices.foreach_get("co", co)
        self.basis = co.reshape(-1, 3)
        self.weld = weld_groups(self.basis[self.idx]) if len(idx) else np.zeros(0, int)
        self.key = None
        if not len(idx):                                 # 動かす頂点が無ければ、シェイプキーも足さない
            return
        if me.shape_keys is None:
            ob.shape_key_add(name="Basis", from_mix=False)
        blocks = me.shape_keys.key_blocks
        self.key = blocks[key] if key in blocks else ob.shape_key_add(name=key, from_mix=False)
        self.key.value = 1.0

    def deform_rot(self):
        """頂点ごとに、骨で動かす前の形の差を、動かした後の世界の差に直す 3×3 行列（骨の重みで混ぜた骨の動き）。骨の重みの
        無い頂点と、アーマチュアで動かないメッシュは、物体の行列だけ。"""
        O = np.array(self.ob.matrix_world)[:3, :3]
        if self.arm is None or not self.bones:
            return np.tile(O, (len(self.idx), 1, 1))
        A = np.array(self.arm.matrix_world)[:3, :3]
        Mb = np.array([np.array(self.arm.pose.bones[b].matrix @ self.arm.data.bones[b].matrix_local.inverted())[:3, :3]
                       for b in self.bones])
        return A @ blend_bone_rotations(self.W, Mb) @ np.linalg.inv(A) @ O


class Push:
    """押し出し 1 つ（当たりと、動かすメッシュの並び）。apply() で今のコマを直す。"""

    def __init__(self, bpy, targets, colliders, smooth, unit):
        from mathutils import Vector
        self.bpy, self.targets, self.colliders, self.smooth, self.unit = bpy, targets, colliders, smooth, unit
        self.rays = [Vector(d).normalized() for d in PUSH_RAYS]

    def apply(self, reset=True):
        """戻り: メッシュの名前 → (動かした頂点の数, 要った最大の量 mm)。reset が false なら、直す前の形へ戻すのは呼ぶ側
        （apply_pushes）がすでにした。"""
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree
        bpy, unit = self.bpy, self.unit
        if reset:
            reset_pushes(bpy, [self])                     # 直す前の形で測る
        margin, step = PUSH_MARGIN_M / unit, PUSH_STEP_M / unit
        trees = []
        for collider in self.colliders:
            co, polys = evaluated_points(bpy, collider)
            trees.append((BVHTree.FromPolygons([tuple(c) for c in co], polys), co.min(0) - margin, co.max(0) + margin))
        stats = {}
        for t in self.targets:
            co, _ = evaluated_points(bpy, t.ob)
            problem = vertex_count_problem(t.ob.name, len(t.basis), len(co), viewport_modifiers(t.ob),
                                           bpy.context.scene.frame_current)
            if problem:                                   # 頂点の番号がずれたまま測らない（コマごとに変わることもある）
                raise SystemExit(problem)
            p = co[t.idx]
            need = np.zeros_like(p)
            for tree, lo, hi in trees:
                for k in np.where(np.all((p > lo) & (p < hi), axis=1))[0]:
                    q, n, _, dist = tree.find_nearest(Vector(p[k]))
                    if q is None:
                        continue
                    q, n = np.array(q), np.array(n)
                    if clearly_outside(p[k], q, n, dist, t.off[k]):
                        continue
                    v = push_correction(p[k], q, dist, t.off[k], inside_mesh(tree, p[k], self.rays, step))
                    if v is not None and np.linalg.norm(v) > np.linalg.norm(need[k]):
                        need[k] = v
            d, mag = spread_push(need, t.edges, self.smooth)
            d = weld_mean(d, t.weld) * t.w[:, None]
            rest = np.einsum("vij,vj->vi", np.linalg.inv(t.deform_rot()), d)
            kco = t.basis.copy()
            kco[t.idx] += rest
            t.key.data.foreach_set("co", kco.ravel())
            t.key.value = 1.0
            stats[t.ob.name] = (int((mag > PUSH_MOVED_M / unit).sum()), round(float(mag.max(initial=0.0) * unit * 1000), 1))
        bpy.context.view_layer.update()
        return stats


def reset_pushes(bpy, pushes):
    """押し出しのシェイプキーを全部 0 にして、直す前の形に戻す。"""
    for push in pushes:
        for t in push.targets:
            t.key.value = 0.0
    bpy.context.view_layer.update()


def apply_pushes(bpy, pushes):
    """今のコマの押し出し: 先に全部の押し出しを直す前の形に戻し（ある押し出しの当たりが別の押し出しで動かすメッシュでも、
    前のコマの直しで測らない）、並べた順に当てる。戻り: メッシュの名前 → (動かした頂点の数, 要った最大の量 mm)。"""
    reset_pushes(bpy, pushes)
    moved = {}
    for push in pushes:
        moved.update(push.apply(reset=False))
    return moved


def make_pushes(bpy, specs, unit):
    """設定の push から、押し出しの並びと、報告に載せる中身。動かすメッシュの骨の動きは、その Armature モディファイアの
    アーマチュア（無ければ骨で動かない）で直す。"""
    pushes, notes = [], []
    for n, spec in enumerate(specs):
        groups = [(g["name"], g["offset_mm"] / 1000.0 / unit) for g in spec["groups"]]
        group_names = [name for name, _ in groups]
        colliders = []
        for name in spec["colliders"]:
            ob = bpy.data.objects.get(name)
            if ob is None or ob.type != "MESH":
                raise SystemExit(f"push[{n}] の当たりのメッシュがありません: {name}")
            colliders.append(ob)
        targets, skipped = [], []
        for name in spec["meshes"]:
            ob = bpy.data.objects.get(name)
            if ob is None or ob.type != "MESH":
                raise SystemExit(f"push[{n}] の動かすメッシュがありません: {name}")
            objects = [(ob, True)]
            if spec.get("outlines", True):
                objects += [(o, False) for o in bpy.data.objects if o.type == "MESH" and o.get("model3d_outline") == name]
            for o, named in objects:
                problem = vertex_count_problem(o.name, len(o.data.vertices), len(evaluated_points(bpy, o)[0]),
                                               viewport_modifiers(o))
                if problem:                               # 描き始める前に止める
                    raise SystemExit(problem)
                modifier = next((m for m in o.modifiers if m.type == "ARMATURE" and m.object is not None
                                 and m.show_viewport and m.show_render), None)
                target = PushTarget(o, modifier.object if modifier else None, groups, PUSH_KEY)
                if not len(target.idx):
                    if named:
                        raise SystemExit(f"push[{n}]: {o.name} に、{group_names} の組の重みのある頂点がありません")
                    skipped.append(o.name)
                    continue
                targets.append(target)
        pushes.append(Push(bpy, targets, colliders, int(spec.get("smooth", 6)), unit))
        notes.append({"targets": [{"object": t.ob.name, "vertices": int(len(t.idx)), "armature": t.arm.name if t.arm else None,
                                   "without_bones": int((t.W.sum(1) <= 0).sum()) if t.arm else int(len(t.idx))}
                                  for t in targets], "skipped": skipped,
                      "colliders": [{"object": c.name, "open_edges": open_edges(c)} for c in colliders],
                      "groups": [{"name": g["name"], "offset_mm": g["offset_mm"]} for g in spec["groups"]],
                      "smooth": int(spec.get("smooth", 6))})
    return pushes, notes


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    track = None
    if a.track:
        with open(a.track, encoding="utf-8") as stream:
            track = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    sc = bpy.context.scene
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise SystemExit("アーマチュアがありません")
    arm = armatures[0]
    hips_name = settings["bone_prefix"] + "Hips"
    if hips_name not in arm.pose.bones:
        raise SystemExit(f"腰の骨がありません（bone_prefix を確かめてください）: {hips_name}")
    hips = arm.pose.bones[hips_name]
    plan = frame_plan(track, sc.frame_start, sc.frame_end)
    fps = settings["fps"] or (track or {}).get("fps") or sc.render.fps / sc.render.fps_base
    if float(fps).is_integer():
        sc.render.fps, sc.render.fps_base = int(fps), 1.0
    else:
        sc.render.fps = int(math.ceil(fps))
        sc.render.fps_base = sc.render.fps / fps
    action_frames = [f for _, f, _ in plan]
    sc.frame_start, sc.frame_end = min(action_frames), max(action_frames)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    stage, cam_s, line = settings["stage"], settings["camera"], settings["outline"]
    unit = float(sc.unit_settings.scale_length)          # 1 Blender 単位が何 m か（トラックと設定の長さは m）
    handles = build_stage(bpy, stage, unit) if stage["enabled"] else None
    apply_render(bpy, settings)
    trace = sc.eevee.ray_tracing_options                 # 画面の反射の厚みは Blender 単位なので、m のまま同じに見えるよう直す
    trace.screen_trace_thickness = trace.screen_trace_thickness / unit
    tone = bpy.data.node_groups.get(TONE)
    tone_initial = ({"a": list(tone.nodes["a"].outputs[0].default_value)[:3], "b": list(tone.nodes["b"].outputs[0].default_value)[:3],
                     "gain": tone.nodes["gain"].outputs[0].default_value} if tone is not None else None)
    static = static_values((tone_initial["a"], tone_initial["b"]) if tone is not None else (VIOLET, PINK), stage["stripe_floor"])
    cam_d = bpy.data.cameras.new("model3d_cam")
    cam_d.sensor_fit, cam_d.sensor_height, cam_d.lens = "VERTICAL", cam_s["sensor_height"], cam_s["lens"]
    cam = bpy.data.objects.new("model3d_cam", cam_d)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.rotation_mode = "QUATERNION"
    cam_d.clip_start, cam_d.clip_end = cam_d.clip_start / unit, cam_d.clip_end / unit   # 写る範囲も m のまま
    needs_path = any("camera" not in spec for _, _, spec in plan)
    path = hip_path(bpy, arm, hips, sorted(set(action_frames)), cam_s["follow_sigma"]) if needs_path else {}
    hulls = []
    for o in bpy.data.objects:                           # 輪郭線の写し（look.toon）: 太らせる Displace と、中の単位の換算
        if o.type == "MESH" and o.get("model3d_outline"):
            mod = o.modifiers.get(o.get("model3d_outline_modifier", "out"))
            if mod is not None:
                hulls.append((o, mod, float(o.get("model3d_outline_m_per_local", 1.0)),
                              float(line["scale"].get(o["model3d_outline"], 1.0))))
    pushes, push_notes = make_pushes(bpy, settings["push"], unit) if settings["push"] else ([], [])
    push_frames = []
    only = set(settings["only"]) if settings["only"] else None
    os.makedirs(a.frames_dir, exist_ok=True)
    res_y = settings["resolution"][1]
    rendered, existing, distances, widths, from_track = [], [], [], [], 0
    began = time.time()
    for T, f, spec in plan:
        file = os.path.join(a.frames_dir, frame_name(T))
        if only is not None and T not in only:
            continue
        if os.path.exists(file):
            existing.append(T)
            continue
        t0 = time.time()
        sc.frame_set(f)
        if handles is not None:
            values = frame_values(spec, static, stage["stripe_floor"])
            set_stage(handles, values["stage"])
            set_lights(handles, values)
        if tone is not None:                             # 毎コマ入れ直す（前のコマの値を引き継がない）
            values_t = tone_values(spec, tone_initial)
            tone.nodes["a"].outputs[0].default_value = (*values_t["a"], 1)
            tone.nodes["b"].outputs[0].default_value = (*values_t["b"], 1)
            tone.nodes["gain"].outputs[0].default_value = values_t["gain"]
        if "camera" in spec:
            loc, target = Vector(spec["camera"]["location"]) / unit, Vector(spec["camera"]["target"]) / unit   # m → Blender 単位
            cam_d.lens = spec["camera"].get("lens", cam_s["lens"])
            from_track += 1
        else:
            hip = path[f]
            target = Vector((hip[0] * cam_s["follow"], hip[1] * cam_s["follow"], cam_s["aim"] / unit))   # 腰は Blender 単位
            loc = Vector(orbit(target, cam_s["az"], cam_s["dist"] / unit, cam_s["height"] / unit))
            cam_d.lens = cam_s["lens"]
        cam.location = loc
        cam.rotation_quaternion = (target - loc).to_track_quat("-Z", "Y")
        d = (loc - arm.matrix_world @ hips.head).length    # Blender 単位
        distances.append(d * unit)
        if hulls:
            wid = outline_width(d, cam_d.lens, cam_d.sensor_height, line["line_px"], line["reference_height"], res_y)
            widths.append(wid * unit)
            for _, mod, per, scale in hulls:
                mod.strength = hull_strength(wid, unit, per, scale)
        bpy.context.view_layer.update()
        if pushes:                                       # 描く直前に押し出す（輪郭線の太さを入れた後の形で測る）
            moved = apply_pushes(bpy, pushes)
            push_frames.append({"T": T, "f": f, "objects": {k: list(v) for k, v in moved.items()}})
        sc.render.filepath = file
        bpy.ops.render.render(write_still=True)
        rendered.append(T)
        print(f"T {T} f {f} {time.time() - t0:.1f}s", flush=True)
    seconds = time.time() - began

    def span(values, k=1.0):
        return {"min": round(min(values) * k, 4), "max": round(max(values) * k, 4)} if values else None

    report = {"schema_version": "model3d-stage-render/0.1", "blender": bpy.app.version_string,
              "planned": [{"T": T, "f": f} for T, f, _ in plan], "rendered": rendered, "skipped_existing": existing,
              "fps": fps, "resolution": settings["resolution"], "scale_length": unit, "engine": sc.render.engine,
              "samples": settings["samples"],
              "color": {"view_transform": sc.view_settings.view_transform, "look": sc.view_settings.look,
                        "exposure": sc.view_settings.exposure, "gamma": sc.view_settings.gamma,
                        "display": sc.display_settings.display_device},
              "camera": {"from_track": from_track, "default": len(rendered) - from_track, "distance_to_hips_m": span(distances)},
              "outline": {"hulls": len(hulls), "width_mm": span(widths, 1000.0)},
              "push": ({"pushes": push_notes, "frames": push_frames,
                        "max_mm": {name: max(fr["objects"][name][1] for fr in push_frames)
                                   for name in (push_frames[0]["objects"] if push_frames else {})}} if pushes else None),
              "stage": bool(stage["enabled"]), "background": settings.get("background"), "seconds": round(seconds, 1),
              "seconds_per_frame": round(seconds / len(rendered), 2) if rendered else None, "settings": settings}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"planned": len(plan), "rendered": len(rendered), "skipped_existing": len(existing)}))


if __name__ == "__main__":
    main()
