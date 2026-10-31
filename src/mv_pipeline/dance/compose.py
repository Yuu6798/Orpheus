"""1 フレームの合成。

    ctx = prepare(spec, scale=1.0)        # ステージ・字・人物の配置。描画サイズごとに 1 回
    img = frame(ctx, t)                   # uint8 (H, W, 3) RGB。同じ ctx・t なら同じバイト列

重ね順は 背景ステージ → 後ろの字 → 人物 → フラッシュ → 帯の字。カメラはステージと人物に掛かり、字には掛からない。
合成は線形光の float32 で行い、最後に 1 回だけ sRGB の uint8 へ戻す。
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from ..fx import timeline
from ..fx.color import color_to_linear, to_linear, to_srgb8
from ..io import PipelineError
from . import key as dancekey
from . import lyrics as dancelyrics
from .clips import Timeline


@dataclass
class Context:
    spec: object
    size: tuple
    stage: np.ndarray
    clips: Timeline
    place: dict
    lines: list
    warnings: list = field(default_factory=list)
    flashes: list = field(default_factory=list)

    @property
    def fps(self):
        return self.spec.data["output"]["fps"]

    @property
    def crf(self):
        return self.spec.data["output"]["crf"]

    @property
    def duration(self):
        return self.spec.data["duration_s"]

    def audio_path(self):
        audio = self.spec.data["audio"]
        return self.spec.resolve(audio["path"]) if audio else None

    def close(self):
        self.clips.close()


def scaled_size(spec, scale=1.0):
    out = spec.data["output"]
    if not 0 < scale <= 4:
        raise PipelineError(f"scale は 0 より大きく 4 以下にしてください: {scale}")
    even = lambda v: max(16, 2 * int(round(v * scale / 2)))  # noqa: E731
    return even(out["width"]), even(out["height"])


def _cover(image, size):
    """uint8 RGB を size へ cover で中央に合わせる。余る側を切る。"""
    width, height = size
    h, w = image.shape[:2]
    scale = max(width / w, height / h)
    new = (max(width, int(math.ceil(w * scale))), max(height, int(math.ceil(h * scale))))
    resized = cv2.resize(image, new, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    x0, y0 = (new[0] - width) // 2, (new[1] - height) // 2
    return resized[y0:y0 + height, x0:x0 + width]


def _load_stage(spec, size):
    path = spec.resolve(spec.data["stage"]["path"])
    if not path.is_file():
        raise PipelineError(f"ステージの画像が見つかりません: {spec.data['stage']['path']}")
    with Image.open(path) as image:
        return to_linear(_cover(np.asarray(image.convert("RGB")), size))


def _placement(spec, source, size):
    """人物の縮尺と、縮尺後の左上の出力座標。height は元動画の高さが画面の高さの何倍かを表す。"""
    place = spec.data["performer"]["placement"]
    width, height = size
    scale = place["height"] * height / source.height
    new = (max(2, int(round(source.width * scale))), max(2, int(round(source.height * scale))))
    x = place["anchor"][0] * width - place["pivot"][0] * new[0]
    y = place["anchor"][1] * height - place["pivot"][1] * new[1]
    return {"scale": scale, "size": new, "x": x, "y": y}


def prepare(spec, scale=1.0):
    size = scaled_size(spec, scale)
    stage = _load_stage(spec, size)
    clips = Timeline(spec)
    try:
        place = {path: _placement(spec, source, size) for path, source in clips.sources.items()}
        lines, text_warnings = dancelyrics.prepare(spec.data, spec.resolve, size)
    except BaseException:
        clips.close()
        raise
    warnings = list(spec.warnings) + text_warnings
    sizes = {(s.width, s.height) for s in clips.sources.values()}
    if len(sizes) > 1:
        warnings.append(f"performer: 人物動画の寸法がそろっていません {sorted(sizes)}（切れ目で人物の大きさが変わることがあります）")
    flashes = [{**f, "linear": color_to_linear(f["color"])} for f in spec.data["flashes"]]
    return Context(spec, size, stage, clips, place, lines, warnings, flashes)


# ---- カメラ ------------------------------------------------------------------

def camera_state(camera, t):
    """時刻 t の (zoom, center, rotate_deg)。seams は切れ目の時刻で最大になる「回転しながら寄って引く」動き。"""
    state = timeline.interp(camera["keys"], t, ["zoom", "center", "rotate_deg"])
    zoom, rotate = state["zoom"], state["rotate_deg"]
    for seam in camera["seams"]:
        u = abs(t - seam["t"]) / seam["span_s"]
        if u < 1.0:
            lift = 1.0 - float(timeline.ease("in_out", u))
            zoom *= 1.0 + (seam["zoom"] - 1.0) * lift
            rotate += seam["rotate_deg"] * lift
    return zoom, state["center"], rotate


def camera_matrix(zoom, center, rotate_deg, size):
    """出力画素 → 出力画素の 2×3 行列。center（正規化）の点が画面の中央に来る。zoom=1・center=中央・回転 0 で恒等。"""
    width, height = size
    angle = math.radians(rotate_deg)
    a, b = zoom * math.cos(angle), zoom * math.sin(angle)
    px, py = center[0] * width - 0.5, center[1] * height - 0.5
    return np.array([[a, -b, width / 2.0 - 0.5 - (a * px - b * py)],
                     [b, a, height / 2.0 - 0.5 - (b * px + a * py)]], dtype=np.float64)


def _is_identity(matrix):
    return bool(np.all(np.abs(matrix - np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float64)) < 1e-9))


def _performer(ctx, t, matrix):
    """人物の (前乗算の線形色, アルファ) を画面の大きさで返す。人物のいない時刻は None。"""
    found = ctx.clips.frame_at(t)
    if found is None:
        return None
    index, image = found
    place = ctx.place[ctx.clips.clips[index]["path"]]
    color, alpha = dancekey.cutout(image, ctx.spec.data["performer"]["key"])
    method = cv2.INTER_AREA if place["scale"] < 1 else cv2.INTER_LINEAR
    color = cv2.resize(color, place["size"], interpolation=method)
    alpha = cv2.resize(alpha, place["size"], interpolation=method)
    full = np.vstack([matrix, [0.0, 0.0, 1.0]]) @ np.array([[1, 0, place["x"]], [0, 1, place["y"]], [0, 0, 1]], dtype=np.float64)
    warp = full[:2].astype(np.float32)
    color = cv2.warpAffine(color, warp, ctx.size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    alpha = cv2.warpAffine(alpha, warp, ctx.size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return color, alpha


def frame(ctx, t):
    """時刻 t の 1 フレームを uint8 (H, W, 3) RGB で返す。"""
    dt = 1.0 / ctx.fps
    zoom, center, rotate = camera_state(ctx.spec.data["camera"], t)
    matrix = camera_matrix(zoom, center, rotate, ctx.size)
    if _is_identity(matrix):
        out = ctx.stage.copy()
    else:
        out = cv2.warpAffine(ctx.stage, matrix.astype(np.float32), ctx.size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    for line in ctx.lines:
        if line.layer == "back":
            dancelyrics.draw(out, line, t, dt)
    person = _performer(ctx, t, matrix)
    if person is not None:
        color, alpha = person
        out = color + out * (1.0 - alpha[..., None])
    for flash in ctx.flashes:
        age = t - flash["t"]
        if 0 <= age < 5 * flash["decay_s"]:
            out = out + (flash["linear"] - out) * float(flash["strength"] * math.exp(-age / flash["decay_s"]))
    for line in ctx.lines:
        if line.layer == "band":
            dancelyrics.draw(out, line, t, dt)
    return to_srgb8(out)


def timeline_record(ctx):
    """run に残す timeline.json の中身。外部の検査道具はこれを読む（描画の入力ではない）。"""
    data, (width, height) = ctx.spec.data, ctx.size
    norm = lambda b: [round(b[0] / width, 5), round(b[1] / height, 5), round(b[2] / width, 5), round(b[3] / height, 5)]  # noqa: E731
    return {
        "schema_version": "dance-timeline/0.1",
        "size": [width, height], "fps": ctx.fps, "duration_s": ctx.duration,
        "lyrics": [{"layer": line.layer, "text": line.text, "start": line.start, "end": line.end,
                    "fade_in_s": line.style["fade_in_s"], "fade_out_s": line.style["fade_out_s"],
                    "box_px": line.box_px, "box": norm(line.box_px)} for line in ctx.lines],
        "clips": [{"path": c["path"], "start": c["start"], "end": c["end"]} for c in data["performer"]["clips"]],
        "seams": ctx.clips.seams(),
        "flashes": [f["t"] for f in data["flashes"]],
        "ui_regions": data["lyrics"]["ui_regions"],
    }
