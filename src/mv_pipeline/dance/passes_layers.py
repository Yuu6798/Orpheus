"""mv dance passes のコマごとの重ね方: 小さな絵・映り込み・分身・効果・板の層、つなぎ（ワイプ）、仕上げ（finish_frame）と 1 コマの
合成（compose）。mv_pipeline.dance.passes から以前の名前で読める。"""
import numpy as np

from ..io import PipelineError
from . import space
from .passes_spec import clock_at, color_at, level_at, segment_value
from .passes_scene import card_layer, read_image


def sprite_layers(data, plan, T, where):
    """コマ T の小さな絵の 3 枚（遠い・近い・映り込み）。sprites が無ければ None。"""
    if data["sprites"] is None or where is None:
        return None
    t = (T - 1) / plan.fps
    return space.sprite_layers(plan.size, where.cameras[T], where.items.get(T, ()), where.sprites, data["sprites"],
                               segment_value(data["room"]["blur"], t), plan.size[0] / 1080)


def reflection_layer(data, plan, T, layers=None):
    """床への映り込みの画（部屋に足す前。表示の値）。reflection が無ければ None。床で反転したカメラで描いた要素（と小さな絵の映り込み
    layers[2]）を、ぼかして色と強さを掛けて足し合わせ（space.mirror。人物 → 効果 → 分身 → 小さな絵の順）、水鏡ならゆらす（space.ripple）。"""
    reflection = data["reflection"]
    if reflection is None:
        return None
    t = (T - 1) / plan.fps
    unit = plan.size[0] / 1080
    parts = []
    if reflection["char"] is not None and T in plan.files.get("rchar", {}):      # 描く時間の外のコマ（outside）には足さない
        mirrored = _same_size(read_image(plan.files["rchar"][T], alpha=True), plan, "rchar", T)
        char = reflection["char"]
        parts.append((mirrored[..., :3] * mirrored[..., 3:4], char["blur"], [color_at(char["tint"], t), level_at(char["strength"], t)]))
    flames = reflection["fx"]
    if flames is not None and T in plan.files.get("rfx", {}):     # 効果の映り込み（ぼかして gain と強さを掛ける）
        picture = _same_size(read_image(plan.files["rfx"][T]), plan, "rfx", T)
        parts.append((picture, flames["blur"], [flames["gain"], level_at(flames["strength"], t)]))
    ghosts, glow = data["phantoms"], reflection["phantoms"]
    if ghosts is not None and glow is not None:       # 分身の映り込み（炎の色 × アルファをぼかして、分身の強さと gain を掛ける）
        strength = level_at(ghosts["strength"], t)
        for i in plan.phantoms():
            kind = f"rph{i}"
            if T in plan.files.get(kind, {}):
                picture = _same_size(read_image(plan.files[kind][T], alpha=True), plan, kind, T)
                parts.append((space.phantom_glow(picture, ghosts["dark"], ghosts["hot"]), glow["blur"], [strength, glow["gain"]]))
    lights = reflection["sprites"]
    if lights is not None and layers is not None:     # 小さな絵の映り込み（床で上下を返した位置に置いたもの。ぼかさない）
        parts.append((layers[2][..., :3], 0.0, [lights["strength"], level_at(lights["gain"], t)]))
    mirror = space.mirror(plan.size, parts, unit)
    ripple = reflection["ripple"]
    if ripple is not None:
        mirror = space.ripple(mirror, level_at(ripple["amount"], t), clock_at(data, t), ripple, unit)
    return mirror


def phantom_layer(data, plan, T, image, near):
    """分身（ph<i>。一覧の near が手前か奥か）を、炎の色の光として image に足す（space.phantom。番号の小さい順）。"""
    ghosts = data["phantoms"]
    if ghosts is None:
        return image
    t = (T - 1) / plan.fps
    unit = plan.size[0] / 1080
    strength = level_at(ghosts["strength"], t)
    for i in plan.phantoms():
        kind = f"ph{i}"
        if T in plan.files[kind] and plan.near[kind][T] == near:
            picture = _same_size(read_image(plan.files[kind][T], alpha=True), plan, kind, T)
            image = space.phantom(image, picture, strength, ghosts, unit)
    return image


def layers(data, plan, T, where=None, scene=None, select=None):
    """コマ T の重ねる層（部屋から手前の板まで。衝撃・にじみ・閃光・明るさ・look の前の、表示の値の float32）。
    select は手前の板を選ぶ時刻（省くとコマの時刻。つなぎの相手の画では、相手のショットの側の時刻）。"""
    from . import finish

    t = (T - 1) / plan.fps
    width, height = plan.size
    unit = width / 1080
    files = plan.files
    character = _same_size(read_image(files["char"][T], alpha=True), plan, "char", T)
    has_room = T in files.get("room", {})               # 部屋は描く時間の外（outside）のコマには無い（別の世界に切り替わった後など）
    room = _same_size(read_image(files["room"][T]), plan, "room", T) if has_room else np.zeros((height, width, 3), np.float32)
    room = finish.blur(room, segment_value(data["room"]["blur"], t), unit=unit)
    room = room * np.float32(level_at(data["room"]["level"], t))
    shadow = data["floor_shadow"]
    if shadow is not None and T in files.get("disc", {}):
        disc = _same_size(read_image(files["disc"][T]), plan, "disc", T).mean(axis=2)
        room = finish.halo(room, disc, strength=shadow["strength"], gain=shadow["gain"], sigma=shadow["sigma"], down=shadow["down"],
                           unit=unit)
    other = data["world"]
    mix = 0.0 if other is None else level_at(other["mix"], t)
    if mix > 0:                                          # 部屋から別の世界へ（踊り手のまわりから焼け抜ける。fx.burn）
        pool = _same_size(read_image(files["pool"][T]), plan, "pool", T) if T in files.get("pool", {}) else None
        room = space.world(room if has_room else None, character[..., 3], mix, pool, other, other["burn"], unit)
    layers = sprite_layers(data, plan, T, where)
    mirror = reflection_layer(data, plan, T, layers)
    if mirror is not None:
        room = room + mirror
    lights = data["sprites"]
    if layers is not None:                                          # 遠い小さな絵（踊り手より遠い）
        room = space.over_glow(room, layers[0], lights["glow"], lights["glow_blur"], unit)
    room = phantom_layer(data, plan, T, room, near=False)          # 奥の分身（踊り手より遠い）
    alpha = character[..., 3]
    figure = character[..., :3]
    tint = data["figure"]
    if tint is not None:
        shade = _same_size(read_image(files["shade"][T]), plan, "shade", T).mean(axis=2)
        if tint["face_smooth"] > 0 and T in files.get("head", {}):
            head = _same_size(read_image(files["head"][T]), plan, "head", T).mean(axis=2)
            shade = finish.face_shade(shade, head, tint["face_smooth"], unit=unit)
        figure = finish.light_tint(figure, shade, color_at(tint["light"], t), color_at(tint["shadow"], t), *tint["shade_range"])
    rim = data["rim"]
    if rim is not None:
        figure = finish.rim_light(figure, alpha, color_at(rim["rgb"], t), level_at(rim["strength"], t), kernel=rim["kernel"], shape=rim["shape"],
                                  blur=rim["blur"], gradient=rim["gradient"], clip=rim["clip"], unit=unit, band=rim["band"])
    frame = finish.over(figure, alpha, room)
    frame = phantom_layer(data, plan, T, frame, near=True)         # 手前の分身
    if layers is not None:                                          # 近い小さな絵
        frame = space.over_glow(frame, layers[1], lights["glow"], lights["glow_blur"], unit)
    lit = data["effects"]
    if lit is not None and T in files.get("fx", {}):                 # 効果（光として足す。奥の効果は、人物の黒で隠れている）
        picture = _same_size(read_image(files["fx"][T]), plan, "fx", T)
        frame = space.effects(frame, picture, level_at(lit["gain"], t), level_at(lit["glow"], t), lit, unit)
    return card_layer(data, scene, plan, T, where, frame, t if select is None else select)   # 手前の板（いちばん手前）


NEXT = 1e-3            # つなぎの最中に、相手のショットの側を選ぶ時刻のずらし（plan.shots と同じ）


def wipe_at(scene, t):
    """時刻 t がつなぎの間（|t − at| < seconds ÷ 2）なら、そのつなぎ（無ければ None）。"""
    for wipe in (scene.wipes if scene is not None else []):
        if abs(t - wipe["at"]) < wipe["seconds"] / 2:
            return wipe
    return None


def wipe_layer(data, plan, T, frame, scene):
    """つなぎの間のコマ: 相手の要素で相手の画を重ね（手前の板は相手のショットの側の時刻で選ぶ）、柱が通り過ぎた側を新しい画にして、
    柱を重ねる（space.pillar・space.wipe）。at より前は今の画が古い画、at から後は相手の画が古い画（at の 1e-6 秒前のコマから後の側）。"""
    t = (T - 1) / plan.fps
    wipe = wipe_at(scene, t)
    if wipe is None:
        return frame
    unit = plan.size[0] / 1080
    at = wipe["at"]
    before = t < at - 1e-6                               # at の前か後か（plan.shots と同じく、EPS 秒前のコマから後の側）
    other = layers(data, wipe["plan"], T, wipe["where"], scene, at + NEXT if before else at - NEXT)
    old, new = (frame, other) if before else (other, frame)
    if wipe["image"] is None:
        wipe["image"] = space.pillar_image(wipe["rgb"], wipe["alpha"], plan.size, wipe["pillar"], wipe["seconds"], plan.fps, unit)
    image, mask, wide = wipe["image"]
    progress = (t - at + wipe["seconds"] / 2) / wipe["seconds"]
    post, cover, centre = space.pillar(image, mask, wide, progress, wipe["direction"], plan.size, wipe["pillar"], unit)
    return space.wipe(old, new, post, cover, centre, wipe["direction"], wipe["pillar"], unit)


def finish_frame(data, plan, T, frame, look=None):
    """衝撃・にじみ・閃光・明るさ・look（上の順の 4e〜8）を掛けて、uint8 (H, W, 3) RGB を返す。"""
    from ..fx import fire
    from ..fx import look as fxlook
    from . import finish

    t = (T - 1) / plan.fps
    unit = plan.size[0] / 1080
    hit = data["impact"]
    if hit is not None:                                              # 衝撃（奥へ吸い込まれる筋）
        frame = space.impact(frame, level_at(hit["amount"], t), hit["zoom"], hit["steps"])
    bloom = data["bloom"]
    if bloom is not None:
        frame = finish.stage_bloom(frame, threshold=level_at(bloom["threshold"], t), ceiling=bloom["ceiling"], sigma=bloom["sigma"],
                                   strength=level_at(bloom["strength"], t), down=bloom["down"], unit=unit)
    flash = data["flash"]
    if flash is not None:
        strength = level_at(flash["keys"], t)
        if strength > 0:
            add = np.asarray(flash["color"], np.float32) * np.float32(strength)
            frame = fire.screen(frame * np.float32(255.0), add) / np.float32(255.0)
    frame = frame * np.float32(level_at(data["level"], t))
    if look is not None:
        return fxlook.finish(look, np.ascontiguousarray(frame, dtype=np.float32), t)
    return (np.clip(frame, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def compose(data, plan, T, look=None, where=None, scene=None):
    """コマ T を重ねて uint8 (H, W, 3) RGB を返す（表示の値で計算する。上の順）。"""
    frame = layers(data, plan, T, where, scene)
    frame = wipe_layer(data, plan, T, frame, scene)
    return finish_frame(data, plan, T, frame, look)


def _same_size(image, plan, kind, T):
    width, height = plan.size
    if image.shape[:2] != (height, width):
        raise PipelineError(f"{kind} のコマ T {T} の大きさ（{image.shape[1]}×{image.shape[0]}）が一覧（{width}×{height}）と違います")
    return image
