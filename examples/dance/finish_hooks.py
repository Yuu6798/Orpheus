"""dance の仕上げの見本（`mv dance render spec.json --hooks examples/dance/finish_hooks.py`。stills でも使える）。

`mv_pipeline.dance.finish` の部品で、次の 3 つを掛ける。
- ステージ: ブルーム。
- 人物の後ろ: 後光・接地の影・床の反射。
- 人物: シャープと輪郭の光（拍の脈で強める）。

部品は表示の値で計算するので、`display` / `straight` で表示の値にしてから掛け、`linear` / `premultiplied` で戻す。
床の線は `performer_info["body"]` の足元の点（hooks を使うと足元の列が作られる）から取る。色と強さは見本の値なので、作品ごとに変える。
"""
from mv_pipeline.dance import finish

RIM = (235, 235, 255)        # 輪郭の光の色（RGB。作品ごとに変える）


def _unit(ctx):
    """長さの既定は 1080 幅の出力の画素なので、出力の幅で掛ける。"""
    return ctx.size[0] / 1080


def stage(img, t, ctx, L):
    return finish.linear(finish.stage_bloom(finish.display(img), unit=_unit(ctx)))


def behind(img, t, ctx, L):
    if L.performer is None:
        return img
    color, alpha = L.performer
    body = L.performer_info["body"]
    floor = body["feet"][1] if body else 0.86 * ctx.size[1]
    unit = _unit(ctx)
    shown = finish.halo(finish.display(img), alpha, unit=unit)
    shown = finish.contact_shadow(shown, alpha, floor, unit=unit)
    shown = finish.floor_reflection(shown, finish.straight(color, alpha), alpha, floor, unit=unit)
    return finish.linear(shown)


def performer(color, alpha, t, ctx, L):
    unit = _unit(ctx)
    beats = ctx.spec.data["beats"]
    pulse = finish.beat_pulse(beats["beats"], t, downbeats=beats["downbeats"]) if beats else 0.0
    fg = finish.sharpen(finish.straight(color, alpha), unit=unit)
    fg = finish.rim_light(fg, alpha, RIM, 0.30 + 0.45 * pulse, unit=unit)
    return finish.premultiplied(fg, alpha), alpha
