"""fx-timeline の 1 コマの計画（画を描かずに決まる。実制作の式との比較にも使う）と、重ね方（blend）・仕上げ（finish）。
mv_pipeline.fx.sequence から以前の名前で読める。cv2 は関数の中でだけ読む。"""
import bisect
import math

import numpy as np

from .sequence_spec import ease


def shot_index(data, t):
    """時刻 t のショットの番号（start ≤ t の最後のショット。0 秒より前は最初のショット）。"""
    return max(0, bisect.bisect_right([shot["start"] for shot in data["shots"]], t) - 1)


def plan_frame(data, t):
    """時刻 t の ([(ショットの番号, ショットの中の時刻, 重み)], 黒の倍率, フラッシュ (量, 色) か None)。

    burn の間は xfade と同じく前と今の 2 つを返す。重みは進み p の (1 − p, p) で、画素ごとの混ぜ方は burn_at の設定で決まる
    （blend に burn を渡す。渡さなければ重みのままの直線の重ねになる）。"""
    index = shot_index(data, t)
    shot = data["shots"][index]
    local = lambda i: data["shots"][i]["offset_s"] + (t - data["shots"][i]["start"])  # noqa: E731
    layers = [(index, local(index), 1.0)]
    transition, since = shot["transition_in"], t - shot["start"]
    dim, flash = 1.0, None
    burning = burn_at(data, t)
    if burning is not None:
        layers = [(index - 1, local(index - 1), 1 - burning[2]), (index, local(index), burning[2])]
    elif transition["type"] == "xfade" and index > 0 and t < shot["start"] + transition["duration_s"]:
        a = ease(since / transition["duration_s"])
        layers = [(index - 1, local(index - 1), 1 - a), (index, local(index), a)]
    elif transition["type"] == "black" and t < shot["start"] + transition["duration_s"]:
        dim = ease(since / transition["duration_s"])
    if transition["type"] == "flash" and since < transition["window_s"]:
        flash = (transition["strength"] * math.exp(-since * transition["rate"]), transition["color"])
    return layers, dim, flash


def burn_at(data, t):
    """時刻 t が burn の窓の中なら (ショットの番号, burn の設定, 進み p)。外なら None。窓は xfade と同じ t < start + duration_s で、
    p = (t − start)/duration_s（直線で 0 から 1 へ。xfade と違い smoothstep は掛けない）。"""
    index = shot_index(data, t)
    shot = data["shots"][index]
    transition = shot["transition_in"]
    if transition["type"] != "burn" or index == 0 or not t < shot["start"] + transition["duration_s"]:
        return None
    return index, transition, min(max((t - shot["start"]) / transition["duration_s"], 0.0), 1.0)


def video_positions(video, local_t, fps):
    """動画のショットの中の時刻 local_t（t − start）のコマ位置と重み [(位置, 重み)]。式と計算の順は実制作と同じ。"""
    loop = video["loop"]
    if loop is None:
        return [((video["offset_s"] + local_t * video["speed"]) * fps, 1.0)]
    tau = local_t + loop["start_s"]
    first = _loop_once(tau, loop, video["speed"], fps)
    if loop["mix"] <= 0:
        return first
    second = _loop_once(tau * loop["mix_speed"] + loop["mix_offset_s"], loop, video["speed"], fps)
    return [(p, w * (1 - loop["mix"])) for p, w in first] + [(p, w * loop["mix"]) for p, w in second]


def _loop_once(t, loop, speed, fps):
    a, b = loop["frames"]
    crossfade = loop["crossfade_frames"]
    period = b - a - crossfade
    p = (t * fps * speed) % period
    if p < crossfade:
        w = p / crossfade
        return [(a + p, w), (a + period + p, 1 - w)]
    return [(a + p, 1.0)]


def blend(layers, dim, flash, burn=None):
    """plan_frame の重みで、ショットの画（表示の値 0..255）をつなぐ。layers は [(画, 重み)]。float32 を返す（まだ切らない）。
    burn は焼け抜けの間だけ渡す (burn の設定, 進み p, 画と同じ大きさの場)。あれば 2 つの画を、重みでなく焼け抜けの式で混ぜる。"""
    if len(layers) == 2 and burn is not None:
        from . import burn as fxburn
        image = fxburn.composite(layers[0][0], layers[1][0], burn[2], burn[1], burn[0])
    elif len(layers) == 2:
        (before, weight_before), (now, weight_now) = layers
        image = before.astype(np.float32) * weight_before + now.astype(np.float32) * weight_now
    else:
        image = layers[0][0].astype(np.float32)
    if dim != 1.0:
        image = image * dim
    if flash is not None:
        amount, color = flash
        image = image + (np.asarray(color, np.float32) - image) * amount
    return image


def finish(image, look=None, t=0.0):
    """つないだ画に timeline の look（fx の look.Look か None）を掛け、0〜255 に切って切り捨てた uint8 にする。"""
    if look is None:
        return np.clip(image, 0, 255).astype(np.uint8)
    from . import look as fxlook
    return fxlook.finish(look, np.clip(image, 0, 255) / np.float32(255.0), t)
