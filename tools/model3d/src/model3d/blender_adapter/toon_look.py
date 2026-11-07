"""Blender の中で動かす: キャラにセル調の塗りと輪郭線を付ける（描く前の見た目の準備）。

    blender --background --factory-startup --python toon_look.py -- --blend in.blend --out-blend out.blend \
        --report report.json --settings settings.json

settings.json（look.toon が書く）:
- meshes: セル調にするメッシュの名前（既定: アーマチュアで動く、レンダーに出すメッシュ全部。物体とコレクションの hide_render と
  ビューレイヤーでの除外を見る。faces に入れたものは除く）。
- faces: 顔の塗り（光を少し受け、絵の色を直接出す）にするメッシュの名前（既定なし）。
- toon: セル調の数。light（光の向き。面の向きとの内積で影を決める）、threshold・soft（影の境と、そのぼかし）、shade（影の色。
  元の色に掛ける）、skin_shade（肌の影の色。省くと shade と同じ）、tint（影の色に舞台の色を混ぜる割合）、lit（光の当たる側の
  明るさ）、rim_from・rim_to（縁の光が出始める・最大になる面の傾き）、rim_colors（カメラから見て左・右の縁の色）、rim_gain、
  face_attribute（影を入れず縁の光も入れない所の頂点の属性）、weak_rim（縁の光を弱める属性と割合）、inside_attribute と
  inside_color（裏の面と内側を暗くする色）。
  陰影を足す数（省けば 2 段の塗りのまま。ノードも増やさない）: bands（[{at, soft, weight}]。境ごとの smoothstep に重みを掛けて
  足し、段を増やす。threshold・soft の代わり）、face_shadow（face_attribute の所にも入れる影の割合）、skin_meshes（肌の部品。
  頂点に属性 model3d_skin を付け、肌の色を見るのをその部品の中だけにする）、skin_highlight（{at, soft, amount, color}。肌の光の
  向く所に足す色。face_attribute の所は除く）、rim_scale（縁の光の強さに掛ける）、real_light・real_light_gain・real_light_max
  （舞台の光の当たり方を、白い Diffuse BSDF を Shader to RGB で読み、(1 - r) + r × min(gain × 光, max) を明るさに掛ける。
  EEVEE でだけ働く）。
- face: 顔の塗りの数。lit（光を受ける割合）、emit（絵の色を直接出す割合）。
- outline: 輪郭線（反転ハル）の数。enabled、meshes（輪郭線を付けるメッシュ。既定は meshes と同じ）、ratio（メッシュごとの残す
  面の割合。既定 1）、dark（線の明るさ。元の色に掛ける）、
  saturation、width_mm（法線の向きに太らせる量。描くときにカメラの距離で上書きする）、hand_attribute と hand_line（その属性の
  所の線の太さの割合）。

塗り方の違うメッシュ（セル調・顔・塗らないメッシュ）と分け合う材質は、塗り方ごとに写して付け替えてから書き換える（メッシュを
ほかの物体と分け合う枠は、物体の側の枠に付け替える）。
セル調の塗り（リポジトリ外の試作の手順）:
- 絵の色（Principled BSDF の Base Color）を出し、影は決まった向きの光と面の向きの内積で 2 段に分ける（境は smoothstep で
  threshold ± soft）。影の側は、影の色に舞台の色（共有のノードグループ model3d_stage_tone の a）を tint だけ混ぜた色を掛ける。
- 縁の光: 面が横を向く所（Layer Weight の Facing を rim_from〜rim_to）に、カメラから見て左は a、右は b の色を、強さ gain で
  スクリーンで足す。face_attribute の所と、weak_rim の属性の所は弱める。
- 裏の面と inside_attribute の所は inside_color を掛ける。全部を Emission で出す（舞台の光は受けない）。EEVEE が使う材質の
  出力につなぐ。元の透け（Alpha につないだ出口か、1 未満の値）は、Transparent BSDF と混ぜて残す。
- 顔の塗り: Principled BSDF のつやを消し（Roughness 1、Specular 0）、絵の色の lit 倍を受け、絵の色を emit の強さで出す（この
  3 つの入力につないだノードは外す）。
輪郭線（試作の手順）: メッシュの写し（親子付け・変換・モディファイアの並びは元のまま）の重なる頂点を溶かし、ratio まで面を
減らし、手の属性（hand_attribute）のあるメッシュは写しをならしてから（どちらも並びの先頭で元の面に当てる。形状キーがあれば、
頂点と辺だけの仮のメッシュでならしたずれを全部のキーに足す）。材質の枠ごとに、元の材質の写しで、Base Color につながった色
（つなぎ方は元のまま。無ければ色の値）を dark 倍した暗い色を Emission で出し、元の透けを残す線の材質にし、裏の面は描かない。並びの後ろ（骨の変形と、形を変えるモディファイアの後）で、
面を裏返し（ジオメトリノード model3d_flip_faces。名前は model3d_outline_flip の印。Solidify のように面の向きで形が変わる
モディファイアも元と同じ向きで働く）、法線の向きへ width_mm だけ太らせる Displace を足す（名前は model3d_outline_modifier の
印に残す。ふつうは out）。Displace は物体の中の単位で押すので、
物体の拡大とシーンの単位（scale_length）で直した強さにし、その換算（中の単位 1 が何 m か）を model3d_outline_m_per_local の
印に残す。写しは model3d_outline の印を持ち、影を落とさず、元と同じコレクションに入る。元の頂点の並びに結び付いたモディファイア（BOUND）のあるメッシュと、
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
             "inside_attribute": "inside", "inside_color": [0.10, 0.06, 0.16],
             # 陰影を足す設定（省けば上の 2 段の塗りのまま。段 1 の後の見比べ A〜E 用）
             "bands": None, "face_shadow": 0.0, "skin_meshes": None, "skin_highlight": None, "rim_scale": 1.0,
             "real_light": 0.0, "real_light_gain": 1.0, "real_light_max": 1.5},
    "face": {"lit": 0.20, "emit": 0.62},
    "outline": {"enabled": True, "meshes": None, "ratio": {}, "dark": 0.12, "saturation": 1.25, "width_mm": 3.0, "hand_attribute": "hand",
                "hand_line": 0.45},
}
TONE = "model3d_stage_tone"
SKIN_ATTRIBUTE = "model3d_skin"                          # skin_meshes に付ける頂点の属性（1 で肌の部品）


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


def line_sources(nt):
    """線の材質に使う元の色と透け: (色の出口か None, 色の値, 透けの出口か None, 透けの値)。Principled BSDF があれば、
    その Base Color と Alpha（つないだ出口か値）。無ければ最初の絵の色（透けなし）、それも無ければ灰色"""
    bsdfs = [n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"]
    if bsdfs:
        color, alpha = bsdfs[0].inputs["Base Color"], bsdfs[0].inputs["Alpha"]
        return (color.links[0].from_socket if color.links else None, tuple(color.default_value),
                alpha.links[0].from_socket if alpha.links else None, float(alpha.default_value))
    texs = [n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None]
    return texs[0].outputs["Color"] if texs else None, (0.5, 0.5, 0.5, 1.0), None, 1.0


def see_through(nt, shader, alpha, opacity):
    """元の透け（Alpha につないだ出口か、1 未満の値）を残す: Transparent BSDF と透けの割合で混ぜた出口。透けがなければ shader"""
    if alpha is None and opacity >= 1.0:
        return shader
    mix = nt.nodes.new("ShaderNodeMixShader")
    if alpha is not None:
        nt.links.new(alpha, mix.inputs["Fac"])
    else:
        mix.inputs["Fac"].default_value = opacity
    nt.links.new(nt.nodes.new("ShaderNodeBsdfTransparent").outputs[0], mix.inputs[1])
    nt.links.new(shader, mix.inputs[2])
    return mix.outputs[0]


def material_output(nt):
    """EEVEE が描くときに使う材質の出力（無ければ作る）"""
    out = nt.get_output_node("EEVEE")
    return out if out is not None else nt.nodes.new("ShaderNodeOutputMaterial")


def principled(m):
    return m is not None and m.use_nodes and any(n.type == "BSDF_PRINCIPLED" for n in m.node_tree.nodes)


def own_materials(bpy, names, tag):
    """names のメッシュの、Principled BSDF のある材質の並び（重ならない）。ほかのメッシュ（別の塗り方か、塗らないメッシュ）と
    分け合う材質は、この塗り方用に写して付け替える（書き換えがほかのメッシュに及ばないように）。メッシュをほかの物体と分け合う
    （リンクした複製の）枠は、物体の側の枠に付け替える（メッシュの側を替えると、分け合う物体の材質も替わるため）。
    戻り: 材質の並びと、写した材質の名前"""
    group, copies, todo = set(names), {}, []
    for name in names:
        ob = bpy.data.objects[name]
        for slot in ob.material_slots:
            m = slot.material
            if not principled(m):
                continue
            users = {o.name for o in bpy.data.objects if o.type == "MESH" for s_ in o.material_slots if s_.material == m}
            if not users <= group:
                if m.name not in copies:
                    copies[m.name] = m.copy()
                    copies[m.name].name = f"{m.name}.{tag}"
                if slot.link == "DATA" and ob.data.users > 1:
                    slot.link = "OBJECT"
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
    if t["bands"]:                                       # 段を増やす: 境ごとの smoothstep に重みを掛けて足す（重みの和は 1）
        step_ = None
        for b in t["bands"]:
            band = nt.nodes.new("ShaderNodeMapRange")
            band.interpolation_type = "SMOOTHSTEP"
            band.inputs["From Min"].default_value = b["at"] - b["soft"]
            band.inputs["From Max"].default_value = b["at"] + b["soft"]
            nt.links.new(dot.outputs["Value"], band.inputs["Value"])
            part = nt.nodes.new("ShaderNodeMath")
            part.operation = "MULTIPLY"
            part.inputs[1].default_value = b["weight"]
            nt.links.new(band.outputs[0], part.inputs[0])
            if step_ is None:
                step_ = part
            else:
                total = nt.nodes.new("ShaderNodeMath")
                total.operation = "ADD"
                nt.links.new(step_.outputs[0], total.inputs[0])
                nt.links.new(part.outputs[0], total.inputs[1])
                step_ = total
    else:
        step_ = nt.nodes.new("ShaderNodeMapRange")
        step_.interpolation_type = "SMOOTHSTEP"
        step_.inputs["From Min"].default_value = t["threshold"] - t["soft"]
        step_.inputs["From Max"].default_value = t["threshold"] + t["soft"]
        nt.links.new(dot.outputs["Value"], step_.inputs["Value"])
    att = nt.nodes.new("ShaderNodeAttribute")
    att.attribute_type, att.attribute_name = "GEOMETRY", t["face_attribute"] or ""
    face_ = att.outputs["Fac"]
    if t["face_shadow"] > 0:                             # 顔にも、影を face_shadow の割合だけ入れる
        keep_face = nt.nodes.new("ShaderNodeMath")
        keep_face.operation = "MULTIPLY"
        keep_face.inputs[1].default_value = 1.0 - t["face_shadow"]
        nt.links.new(att.outputs["Fac"], keep_face.inputs[0])
        face_ = keep_face.outputs[0]
    lit_ = nt.nodes.new("ShaderNodeMath")
    lit_.operation = "MAXIMUM"
    nt.links.new(step_.outputs[0], lit_.inputs[0])
    nt.links.new(face_, lit_.inputs[1])
    grp = nt.nodes.new("ShaderNodeGroup")
    grp.node_tree = tone
    tint = nt.nodes.new("ShaderNodeMix")                 # 影の色に舞台の色を少し混ぜる
    tint.data_type = "RGBA"
    tint.inputs[0].default_value = t["tint"]
    tint.inputs[6].default_value = (*t["shade"], 1)
    skin = None
    if t["skin_shade"] or t["skin_highlight"]:          # 肌（明るく彩度の低い色。skin_meshes があればその部品の中だけ）
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
        skin = sk_.outputs[0]
        if t["skin_meshes"] is not None:
            mark = nt.nodes.new("ShaderNodeAttribute")
            mark.attribute_type, mark.attribute_name = "GEOMETRY", SKIN_ATTRIBUTE
            only = nt.nodes.new("ShaderNodeMath")
            only.operation = "MULTIPLY"
            nt.links.new(sk_.outputs[0], only.inputs[0])
            nt.links.new(mark.outputs["Fac"], only.inputs[1])
            skin = only.outputs[0]
    if t["skin_shade"]:                                  # 肌の影だけ別の色にする
        smix_ = nt.nodes.new("ShaderNodeMix")
        smix_.data_type = "RGBA"
        nt.links.new(skin, smix_.inputs[0])
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
    shaded = both.outputs[2]
    if t["skin_highlight"]:                              # 肌の光の向く所に、弱いハイライトを足す（顔の印の所は除く）
        h = t["skin_highlight"]
        hl = nt.nodes.new("ShaderNodeMapRange")
        hl.interpolation_type = "SMOOTHSTEP"
        hl.inputs["From Min"].default_value = h["at"] - h["soft"]
        hl.inputs["From Max"].default_value = h["at"] + h["soft"]
        nt.links.new(dot.outputs["Value"], hl.inputs["Value"])
        on_skin = nt.nodes.new("ShaderNodeMath")
        on_skin.operation = "MULTIPLY"
        nt.links.new(hl.outputs[0], on_skin.inputs[0])
        nt.links.new(skin, on_skin.inputs[1])
        off_face = nt.nodes.new("ShaderNodeMath")
        off_face.operation = "SUBTRACT"
        off_face.use_clamp = True
        nt.links.new(on_skin.outputs[0], off_face.inputs[0])
        nt.links.new(att.outputs["Fac"], off_face.inputs[1])
        amount = nt.nodes.new("ShaderNodeMath")
        amount.operation = "MULTIPLY"
        amount.inputs[1].default_value = h["amount"]
        nt.links.new(off_face.outputs[0], amount.inputs[0])
        glow = nt.nodes.new("ShaderNodeMix")
        glow.data_type, glow.blend_type = "RGBA", "SCREEN"
        nt.links.new(amount.outputs[0], glow.inputs[0])
        nt.links.new(shaded, glow.inputs[6])
        glow.inputs[7].default_value = (*h["color"], 1)
        shaded = glow.outputs[2]
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
    rim_ = amt2
    if t["rim_scale"] != 1.0:                            # 縁の光を強める（弱める）
        rim_ = nt.nodes.new("ShaderNodeMath")
        rim_.operation = "MULTIPLY"
        rim_.inputs[1].default_value = t["rim_scale"]
        nt.links.new(amt2.outputs[0], rim_.inputs[0])
    add_ = nt.nodes.new("ShaderNodeMix")
    add_.data_type, add_.blend_type = "RGBA", "SCREEN"
    nt.links.new(rim_.outputs[0], add_.inputs[0])
    nt.links.new(shaded, add_.inputs[6])
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
    final = inside.outputs[2]
    if t["real_light"] > 0:                              # 舞台の光の当たり方（影も）を、real_light の割合だけ明るさに混ぜる
        diffuse = nt.nodes.new("ShaderNodeBsdfDiffuse")
        diffuse.inputs["Color"].default_value = (1, 1, 1, 1)
        to_rgb = nt.nodes.new("ShaderNodeShaderToRGB")   # EEVEE でだけ働く（render.stage は EEVEE）
        nt.links.new(diffuse.outputs[0], to_rgb.inputs[0])
        grey = nt.nodes.new("ShaderNodeRGBToBW")
        nt.links.new(to_rgb.outputs["Color"], grey.inputs[0])
        scaled = nt.nodes.new("ShaderNodeMath")          # gain × 光（後ろの縁の光などで飛ばないよう、max で止める）
        scaled.operation = "MULTIPLY"
        scaled.inputs[1].default_value = t["real_light_gain"]
        nt.links.new(grey.outputs[0], scaled.inputs[0])
        capped = nt.nodes.new("ShaderNodeMath")
        capped.operation = "MINIMUM"
        capped.inputs[1].default_value = t["real_light_max"]
        nt.links.new(scaled.outputs[0], capped.inputs[0])
        lerp = nt.nodes.new("ShaderNodeMath")            # (1 - r) + r × min(gain × 光, max)
        lerp.operation = "MULTIPLY_ADD"
        lerp.inputs[1].default_value = t["real_light"]
        lerp.inputs[2].default_value = 1.0 - t["real_light"]
        nt.links.new(capped.outputs[0], lerp.inputs[0])
        lit_by = nt.nodes.new("ShaderNodeMix")
        lit_by.data_type, lit_by.blend_type = "RGBA", "MULTIPLY"
        lit_by.inputs[0].default_value = 1.0
        nt.links.new(final, lit_by.inputs[6])
        nt.links.new(lerp.outputs[0], lit_by.inputs[7])
        final = lit_by.outputs[2]
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(final, em.inputs["Color"])
    a = bsdf.inputs["Alpha"]                             # 元の透け（髪の房やまつげの切り抜き）は残す
    nt.links.new(see_through(nt, em.outputs[0], a.links[0].from_socket if a.links else None, a.default_value),
                 out.inputs["Surface"])


def mark_skin(bpy, names, meshes):
    """肌の部品（names）の頂点に、属性 SKIN_ATTRIBUTE = 1 を付ける（セル調の塗りが、その部品の中でだけ肌の色を見る）。"""
    for name in names:
        if name not in meshes:
            raise SystemExit(f"skin_meshes の {name} は、セル調にするメッシュ（meshes）にありません")
        me = bpy.data.objects[name].data
        sharing = sorted(o.name for o in bpy.data.objects if o.data == me and o.name != name)
        if sharing:
            raise SystemExit(f"skin_meshes の {name} のメッシュを {sharing} と分け合っているので、肌の印を付けられません")
        attribute = me.attributes.get(SKIN_ATTRIBUTE) or me.attributes.new(SKIN_ATTRIBUTE, "FLOAT", "POINT")
        attribute.data.foreach_set("value", [1.0] * len(me.vertices))
    return list(names)


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


def hull_material(bpy, name, src, o):
    """線の材質: 元の材質 src の写しで、面の出力を、元の色を dark 倍・彩度 saturation 倍した暗い Emission に替える。色のつなぎ方
    （絵・UV・頂点の色など）と透け（Alpha）は元のまま使う（髪の房やまつげの切り抜きに、線の殻を出さない）。裏の面は描かない"""
    if src is not None and src.use_nodes:
        m = src.copy()
        m.name = name
    else:
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        m.node_tree.nodes.clear()
    nt = m.node_tree
    color, value, alpha, opacity = line_sources(nt)
    out = material_output(nt)
    for socket in ("Surface", "Volume", "Displacement"):
        for lk in list(out.inputs[socket].links):
            nt.links.remove(lk)
    em = nt.nodes.new("ShaderNodeEmission")
    hsv = nt.nodes.new("ShaderNodeHueSaturation")
    hsv.inputs["Saturation"].default_value = o["saturation"]
    hsv.inputs["Value"].default_value = o["dark"]
    if color is not None:
        nt.links.new(color, hsv.inputs["Color"])
    else:
        hsv.inputs["Color"].default_value = value
    nt.links.new(hsv.outputs["Color"], em.inputs["Color"])
    nt.links.new(see_through(nt, em.outputs[0], alpha, opacity), out.inputs["Surface"])
    m.use_backface_culling = True
    if hasattr(m, "surface_render_method"):              # 元が半透明の重ね描き（BLENDED）でも、線は切り抜きの描き方にする
        m.surface_render_method = "DITHERED"
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
    sources = [slot.material for slot in ob.material_slots] or [None]   # 材質の枠ごとの、線の材質の元
    new = ob.copy()                                      # 親子付け・変換・モディファイアの並び（骨の変形の設定も）はそのまま写す
    new.data = ob.data.copy()
    new.name = "hull_" + ob.name
    sc.collection.objects.link(new)                      # 作る間は場面の根に置く（モディファイアを当てる操作は、ビューレイヤーにある物体が要る）
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
    for k, src in enumerate(sources):                    # 枠ごとに線の材質に替える（枠を消すと面の材質の番号が 0 に戻るので、中身だけ替える）
        line = hull_material(bpy, "hull_" + ob.name + (f"_{k}" if k else ""), src, o)
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
    homes = [c for c in ob.users_collection if c.library is None] or [sc.collection]
    for c in homes:                                      # 元と同じコレクションに入れる（コレクションでレンダーから隠した
        if new.name not in c.objects:                    # メッシュの写しも隠れ、除外も元と同じになる）
            c.objects.link(new)
    if sc.collection not in homes:
        sc.collection.objects.unlink(new)
    return new


def rendered(ob, view_layers):
    """ob がレンダーに出るか。物体の hide_render と、ob の入ったコレクションまでたどる道（どれか 1 つ）の、コレクションの
    hide_render とビューレイヤーでの除外を見る。使うビューレイヤーのどれかで出れば出る"""
    if ob.hide_render:
        return False

    def walk(layer):
        if layer.exclude or layer.collection.hide_render:
            return False
        return ob.name in layer.collection.objects or any(walk(child) for child in layer.children)

    return any(walk(vl.layer_collection) for vl in view_layers if vl.use)


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
        layers = bpy.context.scene.view_layers           # コレクションでレンダーから隠したメッシュ（着替えの衣装など）は選ばない
        meshes = [o.name for o in bpy.data.objects if deformed(o) and rendered(o, layers) and o.name not in faces]
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
    skin_meshes = mark_skin(bpy, t["skin_meshes"], meshes) if t["skin_meshes"] is not None else []
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
              "outline_hulls": hulls, "tone_group": TONE, "skin_meshes": skin_meshes,
              "settings": settings}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"meshes": len(meshes), "toon_materials": len(done), "hulls": len(hulls)}))


if __name__ == "__main__":
    main()
