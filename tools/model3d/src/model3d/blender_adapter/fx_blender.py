"""出来事の形を Blender に置く側（render.show のフックが Blender の中で `import fx_blender` で読む）。形は作らない（fx_strips の
四角形をメッシュにするだけ）。bpy は引数で受け取る（Blender の外でも読める。CI は代役で試す）。

リポジトリ外の 4 作目の 3D のダンスの演出の台本の、メッシュの書き方・足し算で光る材質・光・順番に点く部品の明るさの渡し方・
白黒のコマを移した。

- `additive_material(bpy, name, edge, core, strength, soft)`: 帯の材質。UV の u（幅の向き）で、真ん中を core の色、縁を edge の色にし、
  形 × 頂点の強さ（属性 fx）× strength で光る。透明と足すので、重なると明るくなる（`surface_render_method` BLENDED）。
- `disc_material(bpy, name, edge, core, strength, power)`: 光の玉の材質（四角形の真ん中が明るく、縁へ消える）。
- `flat_material(bpy, name, colour)`: べた塗り（白黒のコマの黒い影など。ビューレイヤーの材質の上書きに使う）。
- `layer(bpy, collection, name, material)`: 形を入れる物体（影を落とさない）。`write(bpy, ob, arrays)`: 四角形をその物体のメッシュにする。
- `point_lights(bpy, collection, prefix, count, ...)` と `apply_lights(lights, assigned, unit)`: 出来事の光（fx_lights.assign の結果）を点の光に書く。
- `drive_by_object(bpy, material, socket, attribute)`: 材質の数の口（光の強さなど）に、物体の属性を掛ける。`set_level(objects, value, attribute)` で書く。
- `mono_frame(scene, value, ...)`: 白黒のコマ（+1 = 白い地に黒い影、−1 = 黒い地、0 = ふだん）。
"""
FX = "fx"            # 頂点の強さの属性の名前
LEVEL = "fx_level"   # 物体ごとの明るさの属性の名前（材質が読む）


def _math(tree, operation, a, b=None):
    node = tree.nodes.new("ShaderNodeMath")
    node.operation = operation
    for socket, value in zip(node.inputs, (a, b)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            tree.links.new(value, socket)
    return node.outputs[0]


def _smooth(tree, value, low, high, smooth=True):
    node = tree.nodes.new("ShaderNodeMapRange")
    if smooth:
        node.interpolation_type = "SMOOTHSTEP"
    node.inputs["From Min"].default_value, node.inputs["From Max"].default_value = low, high
    tree.links.new(value, node.inputs["Value"])
    return node.outputs[0]


def _glow(bpy, name, shape, white, edge, core, strength, attribute):
    """shape(tree, uv) → 形、white(tree, …) → 芯の割合 から、足し算で光る材質を組む。強さの数のノードの名前は fx_gain。"""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.surface_render_method = "BLENDED"
    material.use_backface_culling = False
    tree = material.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    coord = tree.nodes.new("ShaderNodeTexCoord")
    form, inside = shape(tree, coord.outputs["UV"])
    colour = tree.nodes.new("ShaderNodeMix")
    colour.data_type = "RGBA"                                            # 型を決めてから A・B を引く（同じ名前の口が型ごとにある）
    tree.links.new(white(tree, inside), colour.inputs["Factor"])
    colour.inputs["A"].default_value, colour.inputs["B"].default_value = (*edge, 1), (*core, 1)
    attr = tree.nodes.new("ShaderNodeAttribute")
    attr.attribute_type, attr.attribute_name = "GEOMETRY", attribute
    gain = tree.nodes.new("ShaderNodeValue")
    gain.name, gain.outputs[0].default_value = "fx_gain", strength
    emission = tree.nodes.new("ShaderNodeEmission")
    tree.links.new(colour.outputs["Result"], emission.inputs["Color"])
    tree.links.new(_math(tree, "MULTIPLY", _math(tree, "MULTIPLY", form, attr.outputs["Fac"]), gain.outputs[0]), emission.inputs["Strength"])
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    add = tree.nodes.new("ShaderNodeAddShader")
    tree.links.new(transparent.outputs[0], add.inputs[0])
    tree.links.new(emission.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], out.inputs["Surface"])
    return material


def additive_material(bpy, name, edge, core, strength, soft=1.0, attribute=FX):
    """帯の材質（u: 0〜1 の真ん中が芯）。soft は形の指数（大きいほど芯へ寄る）。"""
    def shape(tree, uv):
        xyz = tree.nodes.new("ShaderNodeSeparateXYZ")
        tree.links.new(uv, xyz.inputs[0])
        across = _math(tree, "ABSOLUTE", _math(tree, "SUBTRACT", _math(tree, "MULTIPLY", xyz.outputs["X"], 2.0), 1.0))   # 0 = 真ん中、1 = 縁
        inside = _math(tree, "SUBTRACT", 1.0, across)
        return _math(tree, "POWER", _smooth(tree, inside, 0.0, 1.0), soft), inside
    return _glow(bpy, name, shape, lambda tree, inside: _smooth(tree, inside, 0.50, 0.90), edge, core, strength, attribute)


def disc_material(bpy, name, edge, core, strength, power=2.0, attribute=FX):
    """光の玉の材質（四角形の真ん中 1 → 縁 0 の power 乗）。"""
    def shape(tree, uv):
        centre = tree.nodes.new("ShaderNodeVectorMath")
        centre.operation = "SUBTRACT"
        centre.inputs[1].default_value = (0.5, 0.5, 0.0)
        tree.links.new(uv, centre.inputs[0])
        length = tree.nodes.new("ShaderNodeVectorMath")
        length.operation = "LENGTH"
        tree.links.new(centre.outputs[0], length.inputs[0])
        inside = _smooth(tree, length.outputs["Value"], 0.5, 0.0, smooth=False)
        return _math(tree, "POWER", inside, power), inside
    return _glow(bpy, name, shape, lambda tree, inside: _smooth(tree, inside, 0.55, 0.92), edge, core, strength, attribute)


def flat_material(bpy, name, colour):
    """べた塗り（光を受けない、決まった色）。"""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.use_backface_culling = False
    tree = material.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*colour, 1)
    tree.links.new(emission.outputs[0], out.inputs["Surface"])
    return material


def layer(bpy, collection, name, material):
    """形を入れる物体（空のメッシュ。影を落とさない）。"""
    ob = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    ob.data.materials.append(material)
    ob.visible_shadow = False
    collection.objects.link(ob)
    return ob


def write(bpy, ob, arrays, attribute=FX):
    """fx_strips.Quads.arrays() の四角形を、物体のメッシュにする（毎コマ作り直す。材質は引き継ぐ）。四角形の数を返す。"""
    co, uv, value = arrays
    count = len(co)
    mesh = bpy.data.meshes.new(ob.name)
    if count:
        mesh.from_pydata([tuple(v) for v in co.reshape(-1, 3).tolist()], [], [(4 * i, 4 * i + 1, 4 * i + 2, 4 * i + 3) for i in range(count)])
        mesh.uv_layers.new(name="UVMap").data.foreach_set("uv", uv.reshape(-1).tolist())
        mesh.attributes.new(attribute, "FLOAT", "POINT").data.foreach_set("value", value.reshape(-1).tolist())
    for material in ob.data.materials:
        mesh.materials.append(material)
    old = ob.data
    ob.data = mesh
    bpy.data.meshes.remove(old)
    return count


def point_lights(bpy, collection, prefix, count, soft_size, specular, volume, shadow=True):
    """出来事の光の点の光（強さ 0 で置く）。"""
    lights = []
    for k in range(int(count)):
        data = bpy.data.lights.new(f"{prefix}{k}", "POINT")
        data.energy, data.shadow_soft_size, data.use_shadow = 0.0, soft_size, shadow
        data.specular_factor, data.volume_factor = specular, volume
        ob = bpy.data.objects.new(f"{prefix}{k}", data)
        collection.objects.link(ob)
        lights.append(ob)
    return lights


def apply_lights(lights, assigned, unit=1.0):
    """fx_lights.assign の結果を書く（None の光は強さ 0。位置と色は前のまま）。強さは m の W を、1 単位が unit m のシーンに直す。"""
    for light, item in zip(lights, assigned):
        if item is None:
            light.data.energy = 0.0
            continue
        energy, position, colour = item
        light.location = tuple(v / unit for v in position)
        light.data.energy, light.data.color = energy / unit ** 2, tuple(colour)


def drive_by_object(bpy, material, socket, attribute=LEVEL):
    """材質の数の口 socket（光の強さなど）へ、いまの値 × 物体の属性 attribute を入れる。いまの値を持つ数のノードを返す
    （その default_value を変えれば、元の強さを変えられる）。口に線がつながっていれば、その線の元 × 属性にする。"""
    tree = material.node_tree
    attr = tree.nodes.new("ShaderNodeAttribute")
    attr.attribute_type, attr.attribute_name = "OBJECT", attribute
    multiply = tree.nodes.new("ShaderNodeMath")
    multiply.operation = "MULTIPLY"
    base = None
    if socket.is_linked:
        tree.links.new(socket.links[0].from_socket, multiply.inputs[0])
    else:
        base = tree.nodes.new("ShaderNodeValue")
        base.outputs[0].default_value = socket.default_value
        tree.links.new(base.outputs[0], multiply.inputs[0])
    tree.links.new(attr.outputs["Fac"], multiply.inputs[1])
    tree.links.new(multiply.outputs[0], socket)
    return base


def set_level(objects, value, attribute=LEVEL):
    """物体ごとの明るさの属性を書く（変わったときだけ。描き直しの印を付ける）。"""
    for ob in objects:
        if abs(float(ob.get(attribute, 1.0)) - value) > 1e-9 or attribute not in ob:
            ob[attribute] = float(value)
            ob.update_tag()


def mono_frame(scene, value, hide=(), override=None, background=None, white=((1.0, 1.0, 1.0), 1.0), dark=((0.0, 0.0, 0.0), 1.0),
               normal=None, layer=0):
    """白黒のコマ。value が 0.5 より大きいと白い地（hide のコレクションを描画から外し、ビューレイヤーの材質を override で上書きし、
    空を white の色と強さに）、−0.5 より小さいと黒い地（外して、空を dark に）、そのほかはふだん（normal の色と強さに戻す）。
    background は空の Background ノード（無ければ空は変えない）。白い地では、空の光が物を照らすので、光を 0 にしても黒くならない
    （材質の上書きで黒くする）。足し算で光る物（にじみ・光の玉）は、白い地で黒い四角になるので、形を作る側で作らない。"""
    if background is not None and normal is None:
        raise ValueError("background を渡すときは、normal（ふだんの空の色と強さ）も渡してください")
    on = abs(value) > 0.5
    for collection in hide:
        if collection.hide_render != on:
            collection.hide_render = on
    view_layer = scene.view_layers[layer]
    wanted = override if value > 0.5 else None
    if view_layer.material_override != wanted:
        view_layer.material_override = wanted
    if background is not None:
        colour, strength = white if value > 0.5 else dark if value < -0.5 else normal
        background.inputs[0].default_value = (*colour, 1)
        background.inputs[1].default_value = strength
