"""層の公開 API（0.x の間は互換を保つ）。仕上げのエージェントや外部のスクリプトはここから下書きの層を受け取る。

    from mv_pipeline.dance import api
    ctx = api.prepare("spec.json", scale=1.0, hooks=None)   # Spec も可
    L = api.layers(ctx, t)              # FrameLayers（hooks を掛ける前の層）
    img = api.compose(ctx, L, t)        # uint8 RGB。api.frame(ctx, t) と同じバイト列
    rng = api.rng(ctx, "名前")           # spec の seed と名前から決まる乱数
    api.close(ctx)

FrameLayers の中身はすべて float32・線形・出力の大きさで、色は前乗算。合成の順は
stage → behind → performer → flashes → front → look → uint8。
"""
from pathlib import Path

from ..fx import timeline
from . import compose as _compose
from . import spec as _spec
from .compose import FrameLayers  # noqa: F401
from .hooks import Hooks


def prepare(spec, scale=1.0, hooks=None, out=None):
    """spec は spec.json のパスか Spec。hooks は hooks ファイルのパス。out は fit の解析のキャッシュを置く場所。"""
    loaded = _spec.load_spec(spec) if isinstance(spec, (str, Path)) else spec
    return _compose.prepare(loaded, scale, out, None if hooks is None else Hooks(hooks))


def layers(ctx, t):
    return _compose.layers(ctx, t)


def compose(ctx, frame_layers, t):
    return _compose.compose_layers(ctx, frame_layers, t)


def frame(ctx, t):
    return _compose.frame(ctx, t)


def performer(ctx, t):
    """人物の (前乗算の線形色, アルファ)。人物のいない時刻は None。字やステージを作らないので layers より速い。"""
    return _compose.performer_at(ctx, t)


def zoom(ctx, t):
    """時刻 t のカメラの倍率（seams を含む）。"""
    return ctx.camera.view(ctx.camera.state(t)).total_zoom


def body_height(ctx):
    """fit の体の高さ（画面の画素）。mode body でなければ None。"""
    fit = ctx.spec.data["performer"]["fit"]
    return fit["body_height"] * ctx.size[1] if fit and fit["mode"] == "body" else None


def rng(ctx, name):
    """spec の seed と名前から決まる乱数生成器。"""
    return timeline.seeded_rng(ctx.spec.data["seed"], name)


def close(ctx):
    ctx.close()
