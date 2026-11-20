"""Blender 5.2+ の台本で踏みがちな罠を避けるための共通安全化ヘルパー。

実制作（Blender 5.2.4 LTS・日本語 UI 環境）で確認した以下の落とし穴を構造的に防ぐ:
1. Layered Action API: `action_slot` を手動代入すると C 側で EXCEPTION_ACCESS_VIOLATION を起こす場合があり、
   新規 Armature は `view_layer.update()` 前に `arm.pose` が None になる。
   → `arm.animation_data.action = new_act` のあと `keyframe_insert` でネイティブに Slot / Channelbag を初期化し、
   `foreach_set("co", ...)` + `fc.update()` で書き込む（Blender 4.x の従来 `fcurves` にもフォールバック）。
2. UI ロケール依存: `nodes["Background"]` や `nodes["Principled BSDF"]` は日本語ロケールで
   `"背景"` / `"プリンシプルBSDF"` になり KeyError を起こす。
   → `nodes.clear()` してから型名（`ShaderNodeBackground` 等）で新規生成する。
3. ソケット誤結線: `ShaderNodeVectorMath` の `operation="SCALE"` は `inputs[1]` が未使用の Vector B で
   `inputs["Scale"]` が既定値 1.0 のまま残るため全画素白飛びの原因になる。また `ShaderNodeMix` の
   `inputs["A"]` は型ごとに同名ソケットが重複する。
   → `inputs["Scale"]` 明示接続と、明示ベクトル補間 `A*(1-f) + B*f` のビルダーを提供する。
4. 四元数フリップ: カメラや骨の四元数符号が前後コマで反転すると補間・モーションブラーが 360 度回り道する。
   → `enforce_quat_continuity` で隣接フレームの内積を非負に揃える。

`bpy` はトップレベルで import しない（CI や `model3d` 単体テスト・台本単体読み込みと両立する）。
"""
import numpy as np


def enforce_quat_continuity(quats):
    """四元数列（shape `(N, 4)` または `(N, B, 4)`）の隣接フレーム符号を揃え、内積が負になる反転を無くす。"""
    arr = np.array(quats, dtype=np.float64, copy=True)
    if arr.ndim not in (2, 3) or arr.shape[-1] != 4:
        raise ValueError(f"quats の形は (N, 4) または (N, B, 4) にしてください: {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError("quats に非有限の値（NaN / Inf）が含まれています")
    if arr.shape[0] <= 1:
        return arr
    if arr.ndim == 2:
        for i in range(1, arr.shape[0]):
            if float(np.dot(arr[i - 1], arr[i])) < 0.0:
                arr[i] = -arr[i]
    else:
        for i in range(1, arr.shape[0]):
            dots = np.sum(arr[i - 1] * arr[i], axis=-1)
            flip = dots < 0.0
            if np.any(flip):
                arr[i, flip] = -arr[i, flip]
    return arr


def init_layered_action_channelbag(bpy, arm, action_name):
    """Armature に新しい Action を安全に割り当て、fcurves を持つコンテナ（Channelbag または Action）を返す。

    Blender 5.2 の Layered Action API では `action_slot` を手動代入せず、`keyframe_insert` で
    ネイティブに Slot・Layer・Strip・Channelbag を生成させてから種カーブを除去する。
    """
    if getattr(arm, "pose", None) is None:
        bpy.context.view_layer.update()
    if getattr(arm, "pose", None) is None or not arm.pose.bones:
        raise RuntimeError(f"Armature {getattr(arm, 'name', arm)!r} に pose.bones がありません")

    if getattr(arm, "animation_data", None) is None:
        arm.animation_data_create()

    new_act = bpy.data.actions.new(name=action_name)
    arm.animation_data.action = new_act

    # ネイティブに Slot / Layer / Strip / Channelbag を初期化させる
    seed_bone = arm.pose.bones[0]
    seed_bone.keyframe_insert(data_path="rotation_quaternion", frame=1)

    layers = getattr(new_act, "layers", None)
    if layers:
        container = layers[0].strips[0].channelbags[0]
    else:
        container = new_act

    for fc in list(container.fcurves):
        container.fcurves.remove(fc)
    return new_act, container


def write_linear_fcurve(container, data_path, index, frames, values):
    """1 本の F-Curve を LINEAR 補間で一括書き込みし、`fc.update()` まで実行する。"""
    f_arr = np.asarray(frames, dtype=np.float32).reshape(-1)
    v_arr = np.asarray(values, dtype=np.float32).reshape(-1)
    if len(f_arr) != len(v_arr):
        raise ValueError(f"frames ({len(f_arr)}) と values ({len(v_arr)}) の長さが一致しません")
    if len(f_arr) == 0:
        raise ValueError("空のカーブは書けません")
    if not np.isfinite(f_arr).all() or not np.isfinite(v_arr).all():
        raise ValueError("frames または values に非有限の値（NaN / Inf）が含まれています")

    fc = container.fcurves.new(data_path=data_path, index=int(index))
    fc.keyframe_points.add(len(f_arr))
    co = np.empty(len(f_arr) * 2, dtype=np.float32)
    co[0::2] = f_arr
    co[1::2] = v_arr
    fc.keyframe_points.foreach_set("co", co)
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
    fc.update()
    return fc


def _rgba(color):
    vals = tuple(float(c) for c in color)
    if len(vals) == 3:
        return (*vals, 1.0)
    if len(vals) == 4:
        return vals
    raise ValueError(f"色は 3 要素または 4 要素にしてください: {color!r}")


def _ensure_node_tree(owner):
    """Blender 5.2+ で非推奨の `use_nodes` 参照を避けつつ確実に `node_tree` を取得する。"""
    nt = getattr(owner, "node_tree", None)
    if nt is None and hasattr(owner, "use_nodes"):
        owner.use_nodes = True
        nt = getattr(owner, "node_tree", None)
    if nt is None:
        raise RuntimeError(f"{owner!r} の node_tree を取得できません")
    return nt


def clear_and_build_world_bg(world, color=(0.0, 0.0, 0.0, 1.0), strength=1.0):
    """UI ロケールに依存せず World の背景ノードを構築する（`nodes["Background"]` を使わない）。"""
    nt = _ensure_node_tree(world)
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs[0].default_value = _rgba(color)
    bg.inputs[1].default_value = float(strength)
    nt.links.new(bg.outputs[0], out.inputs[0])
    return bg, out


def clear_and_build_principled(
    material,
    base_color=(0.015, 0.017, 0.028, 1.0),
    metallic=0.0,
    roughness=0.5,
    coat_weight=None,
    coat_roughness=None,
):
    """UI ロケールに依存せず Principled BSDF マテリアルを構築する（`nodes["Principled BSDF"]` を使わない）。"""
    nt = _ensure_node_tree(material)
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = _rgba(base_color)
    bsdf.inputs["Metallic"].default_value = float(metallic)
    bsdf.inputs["Roughness"].default_value = float(roughness)
    if coat_weight is not None and "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = float(coat_weight)
    if coat_roughness is not None and "Coat Roughness" in bsdf.inputs:
        bsdf.inputs["Coat Roughness"].default_value = float(coat_roughness)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return bsdf, out


def make_vector_scale(node_tree, vec_socket, scale_socket):
    """`ShaderNodeVectorMath(SCALE)` を安全に結線する（`inputs[1]` 誤結線を防ぎ必ず `inputs["Scale"]` へ繋ぐ）。"""
    node = node_tree.nodes.new("ShaderNodeVectorMath")
    node.operation = "SCALE"
    node_tree.links.new(vec_socket, node.inputs[0])
    node_tree.links.new(scale_socket, node.inputs["Scale"])
    return node.outputs[0]


def make_vector_lerp(node_tree, vec_a, vec_b, factor_socket):
    """`ShaderNodeMix` の同名ソケット曖昧さを避け、明示ベクトル演算 `A*(1-f) + B*f` で補間する。"""
    inv = node_tree.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    node_tree.links.new(factor_socket, inv.inputs[1])

    part_a = make_vector_scale(node_tree, vec_a, inv.outputs[0])
    part_b = make_vector_scale(node_tree, vec_b, factor_socket)

    add = node_tree.nodes.new("ShaderNodeVectorMath")
    add.operation = "ADD"
    node_tree.links.new(part_a, add.inputs[0])
    node_tree.links.new(part_b, add.inputs[1])
    return add.outputs[0]


def set_blended_alpha(material):
    """EEVEE Next（Blender 4.2+ / 5.2+）でディザなしの滑らかな透過描画を有効にする。"""
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "BLENDED"
    elif hasattr(material, "blend_method"):
        material.blend_method = "BLEND"
    return material
