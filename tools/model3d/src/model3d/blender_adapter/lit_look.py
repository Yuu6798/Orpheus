"""Blender の中で動かす: キャラの材質を、舞台の光の向きと色を受ける塗りに差し替える（描く前の見た目の準備）。

    blender --background --factory-startup --python lit_look.py -- --blend in.blend --out-blend out.blend \
        --report report.json --settings settings.json

settings.json（look.lit が書く。DEFAULTS に重ねる）:
- meshes: 塗りを替えるメッシュの名前（既定: アーマチュアで動く、レンダーに出すメッシュ全部。look.toon と同じ選び方で、
  render.prepare_push の当たりの写しと look.toon の輪郭線の写しは除く）。材質の無いメッシュは、名指ししたなら止め、既定の選び方
  なら報告の without_material に残す（塗らない）
- soft: 柔らかい段にする材質の名前（元の材質の名前。顔など。既定なし）
- texture: 絵の色の画像ノードの名前に含まれる文字（既定 "BaseColor"。VRM Add-on の MToon は Mtoon1BaseColorTexture.Image）
- lit・soft_lit: 段（band: [下, 上]。光の強さを smoothstep で 0〜1 にする範囲）、段の明るさ（gain）、まわりの明るさの倍率（ambient）。
  soft の材質は soft_lit、ほかは lit
- extra: とても強い光のぶん（光の強さの from: [下, 上] を to: [下, 上] へ直線で写し、外は端で止める）
- floor: 光の色あいを、光のいちばん強い成分で割るときの下限
- ambient: まわりの明るさの最初の色（描く工程がコマごとに書き換える。書き換えない描き方では、この色のまま）
- rim: 縁の光（省くと足さない）。left・right（カメラから見て左と右の縁の色。線形の RGB）と edge（[下, 上]。面が横を向く度合い
  Layer Weight の Facing を smoothstep で 0〜1 にする範囲）は必ず書く。gain（強さ。既定 1）、amount（材質ごとの割合の既定。
  既定 1）、materials（元の材質の名前 → 割合。0 の材質には足さない）、side（[下, 上]。カメラの座標の法線の X を 0〜1 にする範囲。
  既定 [-0.25, 0.25]）、down（[下, 上]。同じく Y。真下を向く縁を弱める。既定 [-0.75, -0.15]）、blend（Layer Weight の Blend。既定 0.5）
塗り（リポジトリ外の試作 3 作目の VRM のダンスの演出版と同じ組み方）:
- 絵の色 ×（まわりの明るさ ＋ 光の当たり具合を 2 段に分けたもの × 光の色あい）を Emission で出す。
- 光の当たり具合: 白い Diffuse BSDF を Shader to RGB で読み（舞台の光の向き・色・影を受ける。EEVEE でだけ働く）、RGB のいちばん
  強い成分 m を求め、smoothstep(band) と extra を足して gain を掛ける。光の色あい: Shader to RGB の色 ÷ max(m, floor)。
- まわりの明るさ: RGB ノード model3d_lit_ambient。材質の印 model3d_lit_ambient_scale（lit・soft_lit の ambient）を掛けて、描く工程
  （render.show）がコマごとに入れる。材質の印 model3d_lit は元の材質の名前。
- 縁の光（rim を書いたときだけ）: Emission の色に、縁の色 × 強さを足す。縁の色は、カメラの座標の法線の X を side の smoothstep で
  0（左）〜1（右）にして left と right を混ぜた色。強さは smoothstep(edge, Facing) × smoothstep(down, 法線の Y) × gain × 割合。
  left・right・gain は、全部の材質が見る 1 つのノードグループ model3d_lit_rim の中の、名前が left・right・gain の RGB と Value の
  ノードにある。コマごとに書き換えるときは set_rim（フックからは import lit_look）。材質の印 model3d_lit_rim は割合。
- 絵の色: 元の材質の、名前に texture を含む画像ノード（ノードの並びで最初の、画像のあるもの）の画像を、既定の UV で読む（絵の
  補間は元のまま。UV の変換・色の掛け率・透けは持ち越さない）。無ければ、Principled BSDF の Base Color に直接つないだ画像
  ノード。それも無ければ Principled BSDF の Base Color の色。どれも無い材質は止める。
- 元の材質は書き換えず、材質ごと新しく作って枠に入れる（VRM の MToon は出力がノードグループの中にあり、外の出力につないでも
  使われない）。同じ元の材質は 1 つの塗りを分け合う。塗らないメッシュとメッシュのデータを分け合う物体は、物体の側の枠に入れる。
  裏の面を描かない設定は元の材質のまま。
**検証**: 本物の Blender 5.2.4 で、試作の台本と同じノードの組み方・値になり、同じ画になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from . import toon_look
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import toon_look

DEFAULTS = {
    "meshes": None, "soft": [], "texture": "BaseColor",
    "lit": {"band": [0.12, 0.32], "gain": 0.86, "ambient": 1.0},
    "soft_lit": {"band": [0.02, 0.50], "gain": 0.78, "ambient": 1.5},
    "extra": {"from": [1.0, 6.0], "to": [0.0, 0.30]},
    "floor": 0.02,
    "ambient": [0.2, 0.2, 0.2],
}
SECTIONS = ("lit", "soft_lit", "extra")
MARK = "model3d_lit"                                     # 塗りの材質の印（値は元の材質の名前）
SCALE_MARK = "model3d_lit_ambient_scale"                 # まわりの明るさに掛ける倍率
SOFT_MARK = "model3d_lit_soft"
AMBIENT = "model3d_lit_ambient"                          # まわりの明るさの RGB ノードの名前
OUTLINE_MARK = "model3d_outline"                         # look.toon の輪郭線の写しの印
RIM_GROUP = "model3d_lit_rim"                            # 縁の光の値（左の色・右の色・強さ）のノードグループの名前
RIM_MARK = "model3d_lit_rim"                             # 縁の光を足した材質の印（値は割合）
RIM_DEFAULTS = {"gain": 1.0, "amount": 1.0, "materials": {}, "side": [-0.25, 0.25], "down": [-0.75, -0.15], "blend": 0.5}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="lit_look.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--settings", required=True)
    return ap.parse_args(argv)


def merged(settings):
    """既定の数に settings を重ねる（lit・soft_lit・extra は中の鍵ごと）。"""
    out = {**DEFAULTS, **{k: v for k, v in settings.items() if k not in SECTIONS}}
    for key in SECTIONS:
        out[key] = {**DEFAULTS[key], **settings.get(key, {})}
    return out


def color_source(nodes, texture):
    """絵の色の出どころ: ("image", 画像ノード) か ("color", RGBA) か None。nodes は材質のノードの並び（並びの順に探す）。
    名前に texture を含む、画像のある画像ノード → Principled BSDF の Base Color に直接つないだ、画像のある画像ノード →
    Principled BSDF の Base Color の色。"""
    for node in nodes:
        if node.type == "TEX_IMAGE" and node.image is not None and texture in node.name:
            return "image", node
    principled = [n for n in nodes if n.type == "BSDF_PRINCIPLED"]
    for bsdf in principled:
        for link in bsdf.inputs["Base Color"].links:
            if link.from_node.type == "TEX_IMAGE" and link.from_node.image is not None:
                return "image", link.from_node
    if principled:
        return "color", tuple(principled[0].inputs["Base Color"].default_value)
    return None


def default_meshes(bpy):
    """既定のメッシュ: アーマチュアで動く、レンダーに出すメッシュ（当たりの写しと輪郭線の写しは除く）。look.toon と同じ選び方。"""
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise SystemExit("アーマチュアがありません（meshes を渡すと、アーマチュアの無い .blend にも使えます）")
    arm = armatures[0]

    def deformed(o):
        return o.type == "MESH" and (o.parent == arm or any(m.type == "ARMATURE" and m.object == arm for m in o.modifiers))

    layers = bpy.context.scene.view_layers
    return [o.name for o in bpy.data.objects
            if deformed(o) and toon_look.rendered(o, layers) and not toon_look.is_collider(o) and not o.get(OUTLINE_MARK)]


def new_material(bpy, name):
    """ノードを使う空の材質（Blender 5.x でも 4.x でも）。"""
    m = bpy.data.materials.new(name)
    if m.node_tree is None or not getattr(m, "use_nodes", True):
        m.use_nodes = True
    return m


def rim_settings(s):
    """設定の rim に既定を足したもの（rim が無ければ None）。"""
    if s.get("rim") is None:
        return None
    return {**RIM_DEFAULTS, **s["rim"]}


def rim_control(bpy, rim):
    """縁の光の値のノードグループ（全部の材質が見る）: 出力 left・right（色）・gain（数）。中のノードの名前も left・right・gain。"""
    if bpy.data.node_groups.get(RIM_GROUP) is not None:
        raise SystemExit(f"この .blend には、もう縁の光のノードグループ {RIM_GROUP} があります")
    group = bpy.data.node_groups.new(RIM_GROUP, "ShaderNodeTree")
    out = group.nodes.new("NodeGroupOutput")
    for name, kind, default in (("left", "NodeSocketColor", (*rim["left"], 1)), ("right", "NodeSocketColor", (*rim["right"], 1)),
                                ("gain", "NodeSocketFloat", float(rim["gain"]))):
        group.interface.new_socket(name, in_out="OUTPUT", socket_type=kind)
        node = group.nodes.new("ShaderNodeRGB" if kind == "NodeSocketColor" else "ShaderNodeValue")
        node.name = node.label = name
        node.outputs[0].default_value = default
        group.links.new(node.outputs[0], out.inputs[name])
    return group


def set_rim(bpy, left=None, right=None, gain=None):
    """縁の光の値を書き換える（全部の材質に効く）。描く工程やフックが、コマごとに呼ぶ。left・right は 3 つの数、gain は数。"""
    group = bpy.data.node_groups.get(RIM_GROUP)
    if group is None:
        raise SystemExit(f"縁の光のノードグループ {RIM_GROUP} がありません（look.lit の rim で作ります）")
    for name, value in (("left", left), ("right", right)):
        if value is not None:
            group.nodes[name].outputs[0].default_value = (*[float(v) for v in value][:3], 1.0)
    if gain is not None:
        group.nodes["gain"].outputs[0].default_value = float(gain)


def _socket(sockets, identifier):
    """型ごとに同じ名前のソケットがあるノード（Mix）から、型の名前 identifier で 1 つ引く。"""
    for socket in sockets:
        if socket.identifier == identifier:
            return socket
    raise SystemExit(f"ソケット {identifier} がありません")


def add_rim(tree, source, group, amount, rim):
    """縁の光の色 × 強さを source（Emission へ入れる色）に足した出力を返す（4 作目の 3D のダンスのフックと同じ組み方）。"""
    new, link = tree.nodes.new, tree.links.new

    def smooth(value, band):
        node = new("ShaderNodeMapRange")
        node.interpolation_type = "SMOOTHSTEP"
        node.inputs["From Min"].default_value, node.inputs["From Max"].default_value = (float(v) for v in band)
        link(value, node.inputs["Value"])
        return node.outputs[0]

    def multiply(a, b):
        node = new("ShaderNodeMath")
        node.operation = "MULTIPLY"
        for socket, value in zip((node.inputs[0], node.inputs[1]), (a, b)):
            if isinstance(value, (int, float)):
                socket.default_value = float(value)
            else:
                link(value, socket)
        return node.outputs[0]

    control = new("ShaderNodeGroup")
    control.node_tree = group
    weight = new("ShaderNodeLayerWeight")
    weight.inputs["Blend"].default_value = float(rim["blend"])
    edge = smooth(weight.outputs["Facing"], rim["edge"])               # 面が横を向く所
    geometry = new("ShaderNodeNewGeometry")
    to_camera = new("ShaderNodeVectorTransform")
    to_camera.vector_type, to_camera.convert_from, to_camera.convert_to = "NORMAL", "WORLD", "CAMERA"
    link(geometry.outputs["Normal"], to_camera.inputs[0])
    xyz = new("ShaderNodeSeparateXYZ")
    link(to_camera.outputs[0], xyz.inputs[0])
    side = smooth(xyz.outputs["X"], rim["side"])                       # 0 = カメラから見て左、1 = 右
    upward = smooth(xyz.outputs["Y"], rim["down"])                     # 真下を向く縁は弱める（あごの下、裾の中）
    colour = new("ShaderNodeMix")
    colour.data_type = "RGBA"                                          # 型を決めてから、型の名前でソケットを引く
    link(side, _socket(colour.inputs, "Factor_Float"))
    link(control.outputs["left"], _socket(colour.inputs, "A_Color"))
    link(control.outputs["right"], _socket(colour.inputs, "B_Color"))
    strength = multiply(multiply(edge, upward), multiply(control.outputs["gain"], float(amount)))
    scaled = new("ShaderNodeVectorMath")
    scaled.operation = "SCALE"
    link(_socket(colour.outputs, "Result_Color"), scaled.inputs[0])
    link(strength, scaled.inputs["Scale"])
    total = new("ShaderNodeVectorMath")
    total.operation = "ADD"
    link(source, total.inputs[0])
    link(scaled.outputs[0], total.inputs[1])
    return total.outputs[0]


def lit_material(bpy, material, source, soft, s, rim_group=None, rim_amount=0.0):
    """光を受ける塗りの材質（試作の stage_skin と同じ組み方・同じ値）。rim_group（縁の光のノードグループ）と rim_amount（割合）が
    あれば、縁の光を足す（割合 0 なら足さない）。戻り: 新しい材質。"""
    shade = s["soft_lit"] if soft else s["lit"]
    skin = new_material(bpy, material.name + ".lit")
    skin.use_backface_culling = material.use_backface_culling
    tree = skin.node_tree
    tree.nodes.clear()
    new = tree.nodes.new
    out = new("ShaderNodeOutputMaterial")
    kind, value = source
    if kind == "image":
        image = new("ShaderNodeTexImage")                # UV は既定の UV
        image.image = value.image
        image.interpolation = value.interpolation
        color = image.outputs["Color"]
    else:
        image = new("ShaderNodeRGB")
        image.outputs[0].default_value = value
        color = image.outputs[0]
    diffuse = new("ShaderNodeBsdfDiffuse")
    diffuse.inputs["Color"].default_value = (1, 1, 1, 1)
    to_rgb = new("ShaderNodeShaderToRGB")
    separate = new("ShaderNodeSeparateColor")
    max_rg, max_rgb, safe, inverse = new("ShaderNodeMath"), new("ShaderNodeMath"), new("ShaderNodeMath"), new("ShaderNodeMath")
    max_rg.operation = max_rgb.operation = safe.operation = "MAXIMUM"
    inverse.operation = "DIVIDE"
    band = new("ShaderNodeMapRange")
    band.interpolation_type = "SMOOTHSTEP"
    band.inputs["From Min"].default_value, band.inputs["From Max"].default_value = shade["band"]
    extra = new("ShaderNodeMapRange")                    # とても強い光のぶんを少し足す
    extra.inputs["From Min"].default_value, extra.inputs["From Max"].default_value = s["extra"]["from"]
    extra.inputs["To Min"].default_value, extra.inputs["To Max"].default_value = s["extra"]["to"]
    level = new("ShaderNodeMath")
    level.operation = "ADD"
    gain = new("ShaderNodeMath")
    gain.operation = "MULTIPLY"
    gain.inputs[1].default_value = shade["gain"]
    hue, lit, total, colour = new("ShaderNodeVectorMath"), new("ShaderNodeVectorMath"), new("ShaderNodeVectorMath"), new("ShaderNodeVectorMath")
    hue.operation = lit.operation = "SCALE"
    total.operation = "ADD"
    colour.operation = "MULTIPLY"
    ambient = new("ShaderNodeRGB")
    ambient.name = ambient.label = AMBIENT
    ambient.outputs[0].default_value = (*s["ambient"], 1)
    emission = new("ShaderNodeEmission")
    link = tree.links.new
    link(diffuse.outputs[0], to_rgb.inputs[0])
    link(to_rgb.outputs["Color"], separate.inputs[0])
    link(separate.outputs[0], max_rg.inputs[0])
    link(separate.outputs[1], max_rg.inputs[1])
    link(max_rg.outputs[0], max_rgb.inputs[0])
    link(separate.outputs[2], max_rgb.inputs[1])
    link(max_rgb.outputs[0], band.inputs["Value"])
    link(max_rgb.outputs[0], extra.inputs["Value"])
    link(band.outputs[0], level.inputs[0])
    link(extra.outputs[0], level.inputs[1])
    link(level.outputs[0], gain.inputs[0])
    link(max_rgb.outputs[0], safe.inputs[0])
    safe.inputs[1].default_value = s["floor"]
    inverse.inputs[0].default_value = 1.0
    link(safe.outputs[0], inverse.inputs[1])
    link(to_rgb.outputs["Color"], hue.inputs[0])         # 光の色あい（いちばん強い成分で割る）
    link(inverse.outputs[0], hue.inputs["Scale"])
    link(hue.outputs[0], lit.inputs[0])
    link(gain.outputs[0], lit.inputs["Scale"])
    link(lit.outputs[0], total.inputs[0])
    link(ambient.outputs[0], total.inputs[1])
    link(color, colour.inputs[0])
    link(total.outputs[0], colour.inputs[1])
    painted = colour.outputs[0]
    if rim_group is not None and rim_amount > 0:
        painted = add_rim(tree, painted, rim_group, rim_amount, rim_settings(s))
    link(painted, emission.inputs["Color"])
    link(emission.outputs[0], out.inputs["Surface"])
    skin[MARK] = material.name
    skin[SCALE_MARK] = float(shade["ambient"])
    skin[SOFT_MARK] = bool(soft)
    if rim_group is not None and rim_amount > 0:
        skin[RIM_MARK] = float(rim_amount)
    return skin


def swap_slots(objects, meshes, lit_for):
    """meshes の物体の材質の枠を、lit_for(元の材質) が返す塗りの材質に替える。objects は全部の物体（bpy.data.objects）。
    替える前の枠ごとの材質を先に控えてから替える（選んだ 2 つの物体がメッシュを分け合うと、1 つ目で替えた材質が 2 つ目の枠にも
    見えるため）。塗らない物体とメッシュのデータを分け合う枠は、物体の側の枠に入れる（分け合う物体の材質を替えないため）。
    戻り: (替えた枠の並び, 物体の側の枠にした枠の「物体/番号」)。空の枠は飛ばす。"""
    by_name, group = {o.name: o for o in objects}, set(meshes)
    original = {(name, index): slot.material for name in meshes for index, slot in enumerate(by_name[name].material_slots)}
    slots, moved = [], []
    for name in meshes:
        ob = by_name[name]
        for index, slot in enumerate(ob.material_slots):
            m = original[(name, index)]
            if m is None:
                continue
            lit = lit_for(m)
            sharing = {o.name for o in objects if o.data == ob.data} - group
            if slot.link == "DATA" and sharing:
                slot.link = "OBJECT"
                moved.append(f"{ob.name}/{index}")
            slot.material = lit
            slots.append({"object": ob.name, "slot": index, "source": m.name, "material": lit.name})
    return slots, moved


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    if any(m.get(MARK) is not None for m in bpy.data.materials):
        raise SystemExit("この .blend には、もう光を受ける塗りの材質があります（look.lit は 1 回だけ当ててください）")
    meshes = default_meshes(bpy) if settings["meshes"] is None else list(settings["meshes"])
    if not meshes:
        raise SystemExit("塗りを替えるメッシュがありません")
    for name in meshes:
        o = bpy.data.objects.get(name)
        if o is None or o.type != "MESH":
            raise SystemExit(f"メッシュがありません: {name}")
    bare = [name for name in meshes if not any(s.material for s in bpy.data.objects[name].material_slots)]
    if bare and settings["meshes"] is not None:          # 名指ししたメッシュに材質が無ければ、塗れないので止める
        raise SystemExit(f"meshes の {bare} に材質がありません（塗りを替えられません）")
    sources = {}                                         # 元の材質の名前 → 絵の色の出どころ
    for name in meshes:
        for slot in bpy.data.objects[name].material_slots:
            m = slot.material
            if m is None or m.name in sources:
                continue
            found = color_source(list(m.node_tree.nodes), settings["texture"]) if m.node_tree is not None else None
            if found is None:
                raise SystemExit(f"{name} の材質 {m.name} に絵の色がありません（名前に {settings['texture']!r} を含む画像ノード、"
                                 "Principled BSDF の Base Color につないだ画像ノード、Principled BSDF のどれも無い）。"
                                 "meshes から外すか、texture を変えてください")
            sources[m.name] = found
    unknown = sorted(set(settings["soft"]) - set(sources))
    if unknown:
        raise SystemExit(f"soft の材質が、塗りを替えるメッシュにありません: {unknown}（あるのは {sorted(sources)}）")
    rim = rim_settings(settings)
    if rim is not None:
        unknown = sorted(set(rim["materials"]) - set(sources))
        if unknown:
            raise SystemExit(f"rim.materials の材質が、塗りを替えるメッシュにありません: {unknown}（あるのは {sorted(sources)}）")
    rim_group = rim_control(bpy, rim) if rim is not None else None
    made, amounts = {}, {}

    def lit_for(m):
        if m.name not in made:
            amounts[m.name] = float(rim["materials"].get(m.name, rim["amount"])) if rim is not None else 0.0
            made[m.name] = lit_material(bpy, m, sources[m.name], m.name in settings["soft"], settings, rim_group, amounts[m.name])
        return made[m.name]

    slots, moved = swap_slots(list(bpy.data.objects), meshes, lit_for)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    materials = []
    for source, skin in made.items():
        kind, value = sources[source]
        entry = {"source": source, "material": skin.name, "soft": bool(skin[SOFT_MARK]),
                 "ambient_scale": skin[SCALE_MARK], "color": kind,
                 "texture_node": value.name if kind == "image" else None,
                 "image": value.image.name if kind == "image" else None}
        if rim is not None:
            entry["rim"] = amounts[source]
        materials.append(entry)
    report = {"schema_version": "model3d-lit-look/0.1", "blender": bpy.app.version_string, "meshes": meshes,
              "materials": materials, "slots": slots, "object_slots": moved, "without_material": bare, "settings": settings}
    if rim is not None:
        report["rim"] = {"group": rim_group.name, "materials": sum(1 for a in amounts.values() if a > 0)}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"meshes": len(meshes), "materials": len(materials), "soft": sum(1 for m in materials if m["soft"])}))


if __name__ == "__main__":
    main()
