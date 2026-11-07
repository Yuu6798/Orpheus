"""Blender の中で動かす: キャラにセル調の塗りと輪郭線を付ける（描く前の見た目の準備）。

    blender --background --factory-startup --python toon_look.py -- --blend in.blend --out-blend out.blend \
        --report report.json --settings settings.json

settings.json（look.toon が書く）:
- meshes: セル調にするメッシュの名前（既定: アーマチュアで動く、レンダーに出すメッシュ全部。faces に入れたものは除く）。
- faces: 顔の塗り（光を少し受け、絵の色を直接出す）にするメッシュの名前（既定なし）。
- toon: セル調の数。light（光の向き。面の向きとの内積で影を決める）、threshold・soft（影の境と、そのぼかし）、shade（影の色。
  元の色に掛ける）、skin_shade（肌の影の色。省くと shade と同じ）、tint（影の色に舞台の色を混ぜる割合）、lit（光の当たる側の
  明るさ）、rim_from・rim_to（縁の光が出始める・最大になる面の傾き）、rim_colors（カメラから見て左・右の縁の色）、rim_gain、
  face_attribute（影を入れず縁の光も入れない所の頂点の属性）、weak_rim（縁の光を弱める属性と割合）、inside_attribute と
  inside_color（裏の面と内側を暗くする色）。
- face: 顔の塗りの数。lit（光を受ける割合）、emit（絵の色を直接出す割合）。
- outline: 輪郭線（反転ハル）の数。enabled、meshes（輪郭線を付けるメッシュ。既定は meshes と同じ）、ratio（メッシュごとの残す
  面の割合。既定 1）、dark（線の明るさ。元の色に掛ける）、
  saturation、width_mm（法線の向きに太らせる量。描くときにカメラの距離で上書きする）、hand_attribute と hand_line（その属性の
  所の線の太さの割合）。

塗り方の違うメッシュ（セル調・顔・塗らないメッシュ）と分け合う材質は、塗り方ごとに写して付け替えてから書き換える。
セル調の塗り（リポジトリ外の試作の手順）:
- 絵の色（Principled BSDF の Base Color）を出し、影は決まった向きの光と面の向きの内積で 2 段に分ける（境は smoothstep で
  threshold ± soft）。影の側は、影の色に舞台の色（共有のノードグループ model3d_stage_tone の a）を tint だけ混ぜた色を掛ける。
- 縁の光: 面が横を向く所（Layer Weight の Facing を rim_from〜rim_to）に、カメラから見て左は a、右は b の色を、強さ gain で
  スクリーンで足す。face_attribute の所と、weak_rim の属性の所は弱める。
- 裏の面と inside_attribute の所は inside_color を掛ける。全部を Emission で出す（舞台の光は受けない）。EEVEE が使う材質の
  出力につなぐ。
- 顔の塗り: Principled BSDF のつやを消し（Roughness 1、Specular 0）、絵の色の lit 倍を受け、絵の色を emit の強さで出す（この
  3 つの入力につないだノードは外す）。
輪郭線（試作の手順）: メッシュの写し（親子付け・変換・モディファイアの並びは元のまま）の重なる頂点を溶かし、ratio まで面を
減らし、手の属性（hand_attribute）のあるメッシュは写しをならしてから（どちらも並びの先頭で元の面に当てる。形状キーがあれば、
頂点と辺だけの仮のメッシュでならしたずれを全部のキーに足す）。材質の枠ごとに、Base Color をさかのぼった絵（無ければ色）を
dark 倍した暗い色を Emission で出す線の材質にし、裏の面は描かない。並びの後ろ（骨の変形と、形を変えるモディファイアの後）で、
面を裏返し（ジオメトリノード model3d_flip_faces。名前は model3d_outline_flip の印。Solidify のように面の向きで形が変わる
モディファイアも元と同じ向きで働く）、法線の向きへ width_mm だけ太らせる Displace を足す（名前は model3d_outline_modifier の
印に残す。ふつうは out）。Displace は物体の中の単位で押すので、
物体の拡大とシーンの単位（scale_length）で直した強さにし、その換算（中の単位 1 が何 m か）を model3d_outline_m_per_local の
印に残す。写しは model3d_outline の印を持ち、影を落とさない。元の頂点の並びに結び付いたモディファイア（BOUND）のあるメッシュと、
形状キーのあるメッシュの面を減らすことは止める（写しでは働かないため）。
共有のノードグループ model3d_stage_tone（a・b・gain）は、描く工程がコマごとに書き換える。
**検証**: 本物の Blender 4.5.15 で、リポジトリ外の試作と同じキャラから、同じ輪郭線の写しと同じ描画になることを確かめた
（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

DEFAULTS = {
    "meshes": None, "faces": [],
    "toon": {"light": [0.8, -5.0, 4.0], "threshold": 0.10, "soft": 0.05, "shade": [0.66, 0.52, 0.72], "skin_shade": None,
             "tint": 0.22, "lit": 0.96, "rim_from": 0.60, "rim_to": 0.72, "rim_colors": [[0.30, 0.09, 1.0], [1.0, 0.05, 0.45]],
             "rim_gain": 0.35, "face_attribute": "face_near",
             "weak_rim": [{"attribute": "hand", "scale": 0.75}, {"attribute": "norim", "scale": 0.85}],
             "inside_attribute": "inside", "inside_color": [0.10, 0.06, 0.16]},
    "face": {"lit": 0.20, "emit": 0.62},
    "outline": {"enabled": True, "meshes": None, "ratio": {}, "dark": 0.12, "saturation": 1.25, "width_mm": 3.0, "hand_attribute": "hand",
                "hand_line": 0.45},
}
TONE = "model3d_stage_tone"


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="toon_look.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--settings", required=True)
    return ap.parse_args(argv)


def merged(settings):
    """既定の数に settings を重ねる（toon・face・outline は中の鍵ごと）。"""
    out = {**DEFAULTS, **{k: v for k, v in settings.items() if k not in ("toon", "face", "outline")}}
    for key in ("toon", "face", "outline"):
        out[key] = {**DEFAULTS[key], **settings.get(key, {})}
    return out


def tone_group(bpy, colors, gain):
    """舞台の色（a・b）と縁の光の強さ（gain）を、全部のメッシュで分け合うノードグループ"""
    tone = bpy.data.node_groups.get(TONE) or bpy.data.node_groups.new(TONE, "ShaderNodeTree")
    tone.nodes.clear()
    tone.interface.clear()
    tone.interface.new_socket("a", in_out="OUTPUT", socket_type="NodeSocketColor")
    tone.interface.new_socket("b", in_out="OUTPUT", socket_type="NodeSocketColor")
    tone.interface.new_socket("gain", in_out="OUTPUT", socket_type="NodeSocketFloat")
    out = tone.nodes.new("NodeGroupOutput")
    a, b, g = tone.nodes.new("ShaderNodeRGB"), tone.nodes.new("ShaderNodeRGB"), tone.nodes.new("ShaderNodeValue")
    a.name, b.name, g.name = "a", "b", "gain"
    a.outputs[0].default_value = (*colors[0], 1)
    b.outputs[0].default_value = (*colors[1], 1)
    g.outputs[0].default_value = gain
    tone.links.new(a.outputs[0], out.inputs[0])
    tone.links.new(b.outputs[0], out.inputs[1])
    tone.links.new(g.outputs[0], out.inputs[2])
    return tone


def base_color(nt, bsdf):
    """Principled BSDF の絵の色の出どころ（つないだ絵があればそれ、無ければ色の値の RGB）"""
    if bsdf.inputs["Base Color"].links:
        return bsdf.inputs["Base Color"].links[0].from_socket
    rgb = nt.nodes.new("ShaderNodeRGB")
    rgb.outputs[0].default_value = tuple(bsdf.inputs["Base Color"].default_value)
    return rgb.outputs[0]


def base_image(bsdf):
    """Principled BSDF の Base Color の出どころをさかのぼって、最初に見つかる絵（無ければ None）"""
    todo, seen = [lk.from_node for lk in bsdf.inputs["Base Color"].links], set()
    while todo:
        n = todo.pop(0)
        if n.name in seen:
            continue
        seen.add(n.name)
        if n.type == "TEX_IMAGE" and n.image is not None:
            return n.image
        todo.extend(lk.from_node for s in n.inputs for lk in s.links)
    return None


def hull_source(m):
    """輪郭線の色の出どころ: Base Color をさかのぼった絵か、絵が無ければ Base Color の値（Principled BSDF が無ければ最初の絵）"""
    image, color = None, (0.5, 0.5, 0.5, 1.0)
    if m is not None and m.use_nodes:
        bsdfs = [n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"]
        if bsdfs:
            image, color = base_image(bsdfs[0]), tuple(bsdfs[0].inputs["Base Color"].default_value)
        else:
            texs = [n for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None]
            image = texs[0].image if texs else None
    return image, color


def material_output(nt):
    """EEVEE が描くときに使う材質の出力（無ければ作る）"""
    out = nt.get_output_node("EEVEE")
    return out if out is not None else nt.nodes.new("ShaderNodeOutputMaterial")


def principled(m):
    return m is not None and m.use_nodes and any(n.type == "BSDF_PRINCIPLED" for n in m.node_tree.nodes)


def own_materials(bpy, names, tag):
    """names のメッシュの、Principled BSDF のある材質の並び（重ならない）。ほかのメッシュ（別の塗り方か、塗らないメッシュ）と
    分け合う材質は、この塗り方用に写して付け替える（書き換えがほかのメッシュに及ばないように）。戻り: 材質の並びと、写した材質の名前"""
    group, copies, todo = set(names), {}, []
    for name in names:
        for slot in bpy.data.objects[name].material_slots:
            m = slot.material
            if not principled(m):
                continue
            users = {o.name for o in bpy.data.objects if o.type == "MESH" for s_ in o.material_slots if s_.material == m}
            if not users <= group:
                if m.name not in copies:
                    copies[m.name] = m.copy()
                    copies[m.name].name = f"{m.name}.{tag}"
                slot.material = copies[m.name]
                m = copies[m.name]
            if m not in todo:
                todo.append(m)
    return todo, sorted(copies)


def toon_material(bpy, m, tone, t):
    """セル調の塗り（試作の toon_material と同じ組み方）"""
    from mathutils import Vector
    nt = m.node_tree
    bsdf = [n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"][0]
    out = material_output(nt)
    src = base_color(nt, bsdf)
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    dot = nt.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = Vector(t["light"]).normalized()
    nt.links.new(geo.outputs["Normal"], dot.inputs[0])
    step_ = nt.nodes.new("ShaderNodeMapRange")
    step_.interpolation_type = "SMOOTHSTEP"
    step_.inputs["From Min"].default_value = t["threshold"] - t["soft"]
    step_.inputs["From Max"].default_value = t["threshold"] + t["soft"]
    nt.links.new(dot.outputs["Value"], step_.inputs["Value"])
    att = nt.nodes.new("ShaderNodeAttribute")
    att.attribute_type, att.attribute_name = "GEOMETRY", t["face_attribute"] or ""
    lit_ = nt.nodes.new("ShaderNodeMath")
    lit_.operation = "MAXIMUM"
    nt.links.new(step_.outputs[0], lit_.inputs[0])
    nt.links.new(att.outputs["Fac"], lit_.inputs[1])
    grp = nt.nodes.new("ShaderNodeGroup")
    grp.node_tree = tone
    tint = nt.nodes.new("ShaderNodeMix")                 # 影の色に舞台の色を少し混ぜる
    tint.data_type = "RGBA"
    tint.inputs[0].default_value = t["tint"]
    tint.inputs[6].default_value = (*t["shade"], 1)
    if t["skin_shade"]:                                  # 肌（明るく彩度の低い色）の影だけ別の色にする
        hsv_ = nt.nodes.new("ShaderNodeSeparateColor")
        hsv_.mode = "HSV"
        nt.links.new(src, hsv_.inputs[0])
        sv_ = nt.nodes.new("ShaderNodeMapRange")
        sv_.interpolation_type = "SMOOTHSTEP"
        sv_.inputs["From Min"].default_value, sv_.inputs["From Max"].default_value = 0.62, 0.74
        nt.links.new(hsv_.outputs[2], sv_.inputs["Value"])
        ss_ = nt.nodes.new("ShaderNodeMapRange")
        ss_.interpolation_type = "SMOOTHSTEP"
        ss_.inputs["From Min"].default_value, ss_.inputs["From Max"].default_value = 0.42, 0.28
        nt.links.new(hsv_.outputs[1], ss_.inputs["Value"])
        sk_ = nt.nodes.new("ShaderNodeMath")
        sk_.operation = "MULTIPLY"
        nt.links.new(sv_.outputs[0], sk_.inputs[0])
        nt.links.new(ss_.outputs[0], sk_.inputs[1])
        smix_ = nt.nodes.new("ShaderNodeMix")
        smix_.data_type = "RGBA"
        nt.links.new(sk_.outputs[0], smix_.inputs[0])
        smix_.inputs[6].default_value = (*t["shade"], 1)
        smix_.inputs[7].default_value = (*t["skin_shade"], 1)
        nt.links.new(smix_.outputs[2], tint.inputs[6])
    nt.links.new(grp.outputs["a"], tint.inputs[7])
    dark = nt.nodes.new("ShaderNodeMix")
    dark.data_type, dark.blend_type = "RGBA", "MULTIPLY"
    dark.inputs[0].default_value = 1.0
    nt.links.new(src, dark.inputs[6])
    nt.links.new(tint.outputs[2], dark.inputs[7])
    bright = nt.nodes.new("ShaderNodeMix")
    bright.data_type, bright.blend_type = "RGBA", "MULTIPLY"
    bright.inputs[0].default_value = 1.0
    bright.inputs[7].default_value = (t["lit"], t["lit"], t["lit"], 1)
    nt.links.new(src, bright.inputs[6])
    both = nt.nodes.new("ShaderNodeMix")
    both.data_type = "RGBA"
    nt.links.new(lit_.outputs[0], both.inputs[0])
    nt.links.new(dark.outputs[2], both.inputs[6])
    nt.links.new(bright.outputs[2], both.inputs[7])
    # 縁の光：カメラから見て左は a の色、右は b の色
    lw = nt.nodes.new("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.5
    edge = nt.nodes.new("ShaderNodeMapRange")
    edge.interpolation_type = "SMOOTHSTEP"
    edge.inputs["From Min"].default_value = t["rim_from"]
    edge.inputs["From Max"].default_value = t["rim_to"]
    nt.links.new(lw.outputs["Facing"], edge.inputs["Value"])
    vt = nt.nodes.new("ShaderNodeVectorTransform")
    vt.vector_type, vt.convert_from, vt.convert_to = "NORMAL", "WORLD", "CAMERA"
    nt.links.new(geo.outputs["Normal"], vt.inputs[0])
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(vt.outputs[0], sep.inputs[0])
    side_ = nt.nodes.new("ShaderNodeMapRange")
    side_.inputs["From Min"].default_value = -0.2
    side_.inputs["From Max"].default_value = 0.2
    nt.links.new(sep.outputs["X"], side_.inputs["Value"])
    col = nt.nodes.new("ShaderNodeMix")
    col.data_type = "RGBA"
    nt.links.new(side_.outputs[0], col.inputs[0])
    nt.links.new(grp.outputs["a"], col.inputs[6])
    nt.links.new(grp.outputs["b"], col.inputs[7])
    weak = None                                          # 縁の光を弱める所（手は細く、指が白く飛ぶ。暗い髪束は点々と光る）
    for w in t["weak_rim"]:
        attw = nt.nodes.new("ShaderNodeAttribute")
        attw.attribute_type, attw.attribute_name = "GEOMETRY", w["attribute"]
        scale = nt.nodes.new("ShaderNodeMath")
        scale.operation = "MULTIPLY"
        scale.inputs[1].default_value = w["scale"]
        nt.links.new(attw.outputs["Fac"], scale.inputs[0])
        if weak is None:
            weak = scale
        else:
            mx = nt.nodes.new("ShaderNodeMath")
            mx.operation = "MAXIMUM"
            nt.links.new(weak.outputs[0], mx.inputs[0])
            nt.links.new(scale.outputs[0], mx.inputs[1])
            weak = mx
    both_ = nt.nodes.new("ShaderNodeMath")
    both_.operation = "MAXIMUM"
    nt.links.new(att.outputs["Fac"], both_.inputs[0])
    if weak is not None:
        nt.links.new(weak.outputs[0], both_.inputs[1])
    else:
        both_.inputs[1].default_value = 0.0
    keep_ = nt.nodes.new("ShaderNodeMath")               # 顔には縁の光を入れない
    keep_.operation = "SUBTRACT"
    keep_.inputs[0].default_value = 1.0
    nt.links.new(both_.outputs[0], keep_.inputs[1])
    amt = nt.nodes.new("ShaderNodeMath")
    amt.operation = "MULTIPLY"
    nt.links.new(edge.outputs[0], amt.inputs[0])
    nt.links.new(grp.outputs["gain"], amt.inputs[1])
    amt2 = nt.nodes.new("ShaderNodeMath")
    amt2.operation = "MULTIPLY"
    nt.links.new(amt.outputs[0], amt2.inputs[0])
    nt.links.new(keep_.outputs[0], amt2.inputs[1])
    add_ = nt.nodes.new("ShaderNodeMix")
    add_.data_type, add_.blend_type = "RGBA", "SCREEN"
    nt.links.new(amt2.outputs[0], add_.inputs[0])
    nt.links.new(both.outputs[2], add_.inputs[6])
    nt.links.new(col.outputs[2], add_.inputs[7])
    # 裏の面と内側は暗くする（袖やスカートの内側。裏の絵には塗り直しの跡がある）
    inside = nt.nodes.new("ShaderNodeMix")
    inside.data_type, inside.blend_type = "RGBA", "MULTIPLY"
    inside.inputs[7].default_value = (*t["inside_color"], 1)
    att2 = nt.nodes.new("ShaderNodeAttribute")
    att2.attribute_type, att2.attribute_name = "GEOMETRY", t["inside_attribute"] or ""
    in_ = nt.nodes.new("ShaderNodeMath")
    in_.operation = "MAXIMUM"
    nt.links.new(geo.outputs["Backfacing"], in_.inputs[0])
    nt.links.new(att2.outputs["Fac"], in_.inputs[1])
    nt.links.new(in_.outputs[0], inside.inputs[0])
    nt.links.new(add_.outputs[2], inside.inputs[6])
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(inside.outputs[2], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])


def face_material(m, f):
    """顔の塗り（試作と同じ）: つやを消し、絵の色の lit 倍を受け、絵の色を emit の強さで出す（絵に影が描いてあるので）"""
    nt = m.node_tree
    for n in list(nt.nodes):
        if n.type != "BSDF_PRINCIPLED":
            continue
        for name, value in (("Roughness", 1.0), ("Specular IOR Level", 0.0), ("Emission Strength", f["emit"])):
            for lk in list(n.inputs[name].links):          # つないだノードがあると値が効かないので外す
                nt.links.remove(lk)
            n.inputs[name].default_value = value
        src = base_color(nt, n)
        hsv = nt.nodes.new("ShaderNodeHueSaturation")
        hsv.inputs["Value"].default_value = f["lit"]
        nt.links.new(src, hsv.inputs["Color"])
        nt.links.new(hsv.outputs["Color"], n.inputs["Base Color"])
        nt.links.new(src, n.inputs["Emission Color"])


def hull_material(bpy, name, image, color, o):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    hsv = nt.nodes.new("ShaderNodeHueSaturation")
    hsv.inputs["Saturation"].default_value = o["saturation"]
    hsv.inputs["Value"].default_value = o["dark"]
    if image is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        nt.links.new(tex.outputs["Color"], hsv.inputs["Color"])
    else:
        hsv.inputs["Color"].default_value = color
    nt.links.new(hsv.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    m.use_backface_culling = True
    return m


def metres_per_local(matrix_world, scale_length):
    """物体の中の単位 1 が何 m か（物体の拡大 × シーンの scale_length。拡大が軸ごとに違えばその平均）。"""
    import numpy as np
    scale = float(np.linalg.norm(np.asarray(matrix_world, float)[:3, :3], axis=0).mean())
    return scale * float(scale_length)


# 元の頂点の並びに結び付いた（覚えた頂点や、焼いた動きを読む）モディファイア。写しの頂点を溶かし面を減らすと働かない
BOUND = {"SURFACE_DEFORM", "MESH_DEFORM", "LAPLACIANDEFORM", "MULTIRES", "MESH_CACHE", "MESH_SEQUENCE_CACHE", "SOFT_BODY", "CLOTH"}


def bound_modifiers(ob):
    """ob のモディファイアのうち、輪郭線の写しでは働かないもの（BOUND と、結び付けた形で戻す Corrective Smooth）の名前"""
    return [m.name for m in ob.modifiers
            if m.type in BOUND or (m.type == "CORRECTIVE_SMOOTH" and getattr(m, "rest_source", "") == "BIND")]


FLIP = "model3d_flip_faces"


def flip_group(bpy):
    """面を裏返すだけのジオメトリノード（全部の写しで分け合う）"""
    group = bpy.data.node_groups.get(FLIP)
    if group is None:
        group = bpy.data.node_groups.new(FLIP, "GeometryNodeTree")
        group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        flip = group.nodes.new("GeometryNodeFlipFaces")
        group.links.new(group.nodes.new("NodeGroupInput").outputs[0], flip.inputs["Mesh"])
        group.links.new(flip.outputs["Mesh"], group.nodes.new("NodeGroupOutput").inputs[0])
    return group


def apply_first(bpy, ob, mod):
    """mod を並びの先頭に動かして当てる（写したモディファイアより先に、元の面に当てる）"""
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
    bpy.ops.object.modifier_apply(modifier=mod.name)


def smooth_hull(bpy, new):
    """写し全体をならす（Smooth、強さ 0.6・6 回）。形状キーがあると Blender はモディファイアを当てられないので、同じ頂点と辺だけの
    仮のメッシュ（Smooth は辺だけを使う）で基本の形（Basis）をならし、そのずれを全部のキーと頂点に足す（キーごとの動きはそのまま）"""
    import numpy as np

    def smooth(ob):
        sm = ob.modifiers.new("smooth", "SMOOTH")
        sm.factor, sm.iterations = 0.6, 6
        sm.vertex_group = ""
        apply_first(bpy, ob, sm)

    me = new.data
    if me.shape_keys is None:
        smooth(new)
        return
    n = len(me.vertices)
    basis, after = np.zeros(n * 3), np.zeros(n * 3)
    me.shape_keys.reference_key.data.foreach_get("co", basis)
    edges = np.zeros(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", edges)
    flat_mesh = bpy.data.meshes.new("model3d_smooth")
    flat_mesh.vertices.add(n)
    flat_mesh.vertices.foreach_set("co", basis)
    flat_mesh.edges.add(len(me.edges))
    flat_mesh.edges.foreach_set("vertices", edges)
    flat_mesh.update()
    flat = bpy.data.objects.new("model3d_smooth", flat_mesh)
    bpy.context.scene.collection.objects.link(flat)
    smooth(flat)
    flat.data.vertices.foreach_get("co", after)
    flat_mesh = flat.data
    bpy.data.objects.remove(flat)
    bpy.data.meshes.remove(flat_mesh)
    shift = after - basis
    for kb in me.shape_keys.key_blocks:
        co = np.zeros(n * 3)
        kb.data.foreach_get("co", co)
        kb.data.foreach_set("co", co + shift)
    me.vertices.foreach_set("co", after)
    me.update()
    bpy.context.view_layer.objects.active = new


def outline_hull(bpy, ob, o):
    """輪郭線の写し（試作の outline.py と同じ作り方。写しは元の親子付け・変換・モディファイアの並びを持つ）"""
    import bmesh
    sc = bpy.context.scene
    sources = [hull_source(slot.material) for slot in ob.material_slots] or [hull_source(None)]   # 材質の枠ごとの線の色
    new = ob.copy()                                      # 親子付け・変換・モディファイアの並び（骨の変形の設定も）はそのまま写す
    new.data = ob.data.copy()
    new.name = "hull_" + ob.name
    sc.collection.objects.link(new)
    me = new.data
    if "custom_normal" in me.attributes:
        me.attributes.remove(me.attributes["custom_normal"])
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=2e-5)
    bm.to_mesh(me)
    bm.free()
    ratio = float(o["ratio"].get(ob.name, 1.0))
    if ratio < 1.0:
        dec = new.modifiers.new("dec", "DECIMATE")
        dec.ratio = ratio
        apply_first(bpy, new, dec)
    hand = o["hand_attribute"]
    if hand and hand in me.attributes:                   # 手首から先は面の細かな凹凸から線が点々と覗くので、写しだけならす
        smooth_hull(bpy, new)
    for k, (image, color) in enumerate(sources):         # 枠ごとに線の材質に替える（枠を消すと面の材質の番号が 0 に戻るので、中身だけ替える）
        line = hull_material(bpy, "hull_" + ob.name + (f"_{k}" if k else ""), image, color, o)
        if k < len(new.material_slots):
            new.material_slots[k].material = line        # 物体に付けた材質の枠でも、写しの側だけが替わる
        else:
            me.materials.append(line)
    bpy.context.view_layer.update()
    # Displace は物体の中の単位で押し出すので、mm の太さを、物体の拡大とシーンの単位で中の単位に直す（描く工程も同じ数で直す）
    per = metres_per_local(new.matrix_world, sc.unit_settings.scale_length)
    new["model3d_outline_m_per_local"] = per
    flip = new.modifiers.new("flip", "NODES")             # 面は写したモディファイアの後で裏返す（Solidify のように面の向きで
    flip.node_group = flip_group(bpy)                    # 形が変わるモディファイアも、元と同じ向きで働かせる）
    new["model3d_outline_flip"] = flip.name
    m2 = new.modifiers.new("out", "DISPLACE")             # 並びの最後（骨の変形や形を変えるモディファイアの後）に太らせる
    m2.direction, m2.mid_level, m2.strength = "NORMAL", 0.0, -(o["width_mm"] / 1000) / per
    new["model3d_outline_modifier"] = m2.name
    if hand and hand in me.attributes:
        hv = [0.0] * len(me.vertices)
        me.attributes[hand].data.foreach_get("value", hv)
        g = new.vertex_groups.new(name="line")           # 元に同じ名前の組があれば line.001 などになる
        for k, h in enumerate(hv):
            g.add([k], 1.0 - (1.0 - o["hand_line"]) * min(max(h, 0.0), 1.0), "REPLACE")
        m2.vertex_group = g.name
    new.visible_shadow = False
    new["model3d_outline"] = ob.name
    return new


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise SystemExit("アーマチュアがありません")
    arm = armatures[0]
    if any(o.get("model3d_outline") for o in bpy.data.objects):
        raise SystemExit("この .blend には、もう輪郭線の写しがあります（look.toon は 1 回だけ当ててください）")

    def deformed(o):
        return o.type == "MESH" and (o.parent == arm or any(m.type == "ARMATURE" and m.object == arm for m in o.modifiers))

    faces = list(settings["faces"])
    if settings["meshes"] is None:
        meshes = [o.name for o in bpy.data.objects if deformed(o) and not o.hide_render and o.name not in faces]
    else:
        meshes = list(settings["meshes"])
    for name in meshes + faces:
        o = bpy.data.objects.get(name)
        if o is None or o.type != "MESH":
            raise SystemExit(f"メッシュがありません: {name}")
        if not any(principled(slot.material) for slot in o.material_slots):
            raise SystemExit(f"{name} の材質に Principled BSDF がありません")
    lined = meshes if settings["outline"]["meshes"] is None else list(settings["outline"]["meshes"])
    for name in lined:
        if bpy.data.objects.get(name) is None or bpy.data.objects[name].type != "MESH":
            raise SystemExit(f"輪郭線を付けるメッシュがありません: {name}")
        if not settings["outline"]["enabled"]:
            continue
        bound = bound_modifiers(bpy.data.objects[name])
        if bound:
            raise SystemExit(f"{name} の {bound} は元の頂点の並びに結び付いているので、輪郭線の写し（頂点を溶かし、面を減らす）"
                             "では働きません。先にそのモディファイアを当てるか、outline.meshes から外してください")
        if bpy.data.objects[name].data.shape_keys and float(settings["outline"]["ratio"].get(name, 1.0)) < 1.0:
            raise SystemExit(f"{name} には形状キーがあるので、輪郭線の写しの面を減らせません（outline.ratio から外してください）")
    if set(meshes) & set(faces):
        raise SystemExit(f"同じメッシュを meshes と faces の両方に入れています: {sorted(set(meshes) & set(faces))}")
    t, f, o = settings["toon"], settings["face"], settings["outline"]
    tone = tone_group(bpy, t["rim_colors"], t["rim_gain"])
    toon_list, toon_copies = own_materials(bpy, meshes, "toon")      # 塗り方ごとに材質を分けてから書き換える
    face_list, face_copies = own_materials(bpy, faces, "face")
    for m in toon_list:
        toon_material(bpy, m, tone, t)
    for m in face_list:                                  # 顔は全部の材質の枠を塗る
        face_material(m, f)
    done, face_done = [m.name for m in toon_list], [m.name for m in face_list]
    hulls = []
    if o["enabled"]:
        bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.object and bpy.context.object.mode != "OBJECT" else None
        for name in lined:
            h = outline_hull(bpy, bpy.data.objects[name], o)
            hulls.append({"mesh": name, "hull": h.name, "triangles": sum(len(p.vertices) - 2 for p in h.data.polygons),
                          "ratio": float(o["ratio"].get(name, 1.0))})
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    report = {"schema_version": "model3d-toon-look/0.1", "blender": bpy.app.version_string, "meshes": meshes, "faces": faces,
              "toon_materials": done, "face_materials": face_done, "copied_materials": toon_copies + face_copies,
              "outline_hulls": hulls, "tone_group": TONE,
              "settings": settings}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"meshes": len(meshes), "toon_materials": len(done), "hulls": len(hulls)}))


if __name__ == "__main__":
    main()
