"""1 フレームの合成。

    ctx = prepare(spec, scale=1.0)        # ステージ・字・人物の配置。描画サイズごとに 1 回
    img = frame(ctx, t)                   # uint8 (H, W, 3) RGB。同じ ctx・t なら同じバイト列

重ね順は 背景ステージ → 後ろの字 → 人物 → フラッシュ → 帯の字。カメラはステージと人物に掛かり、字には掛からない。
カメラの式は camera.py。ブレは seams の窓の中だけで、カメラに乗る層ごとに変換をずらして平均する。
合成は線形光の float32 で行い、最後に 1 回だけ sRGB の uint8 へ戻す。
"""
import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..fx import timeline
from ..fx.color import color_to_linear, from_srgb_float, to_linear, to_srgb8, to_srgb_float
from ..io import PipelineError, sha256
from . import SCHEMA_V01
from . import fit as dancefit
from . import key as dancekey
from . import lyrics as dancelyrics
from .camera import Camera, camera_matrix, is_identity
from .clips import Source, Timeline

FRAME_FIELDS = ["t", "clip", "source_frame", "blend", "zoom", "cx", "cy", "rotate", "shake_x", "shake_y", "blur"]
PLACEMENT_FIELDS = ["p_a", "p_b", "p_tx", "p_c", "p_d", "p_ty"]


@dataclass
class Context:
    spec: object
    size: tuple
    stage: object                    # Stage
    clips: Timeline
    place: list                      # clip ごとの配置（_placement）
    lines: list
    warnings: list = field(default_factory=list)
    flashes: list = field(default_factory=list)
    analysis: list = field(default_factory=list)     # fit の解析の記録（clip ごと。fit を使わない clip は含まない）
    stills: dict = field(default_factory=dict)       # 静止画クリップの切り抜き（clip 番号 -> 色・アルファ）
    camera: object = None            # camera.Camera

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
        self.stage.close()


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


class Stage:
    """ステージ。静止画か動画で、at(t) は線形 float32 (H, W, 3)。読み出しは人物と同じく前方向だけ。"""

    def __init__(self, spec, size):
        stage = spec.data["stage"]
        self.config, self.size, self.source, self.index, self.image = stage, size, None, None, None
        path = spec.resolve(stage["path"])
        if stage["kind"] == "image":
            if not path.is_file():
                raise PipelineError(f"ステージの画像が見つかりません: {stage['path']}")
            with Image.open(path) as image:
                self.image = to_linear(_cover(np.asarray(image.convert("RGB")), size))
        else:
            if not path.is_file():
                raise PipelineError(f"ステージの動画が見つかりません: {stage['path']}")
            self.source = Source(path)
            self.frames = self.source.count()

    def frame_number(self, t):
        """ショット時刻 t のステージのフレーム番号。loop なら折り返し、loop でなければ最後を保つ。"""
        number = int(math.floor((self.config["source_start"] + t) * self.source.fps + 1e-6))
        return number % self.frames if self.config["loop"] else max(0, min(number, self.frames - 1))

    def at(self, t):
        if self.source is None:
            return self.image
        number = self.frame_number(t)
        if number != self.index:
            self.index, self.image = number, to_linear(_cover(self.source.read(number), self.size))
        return self.image

    def close(self):
        if self.source is not None:
            self.source.close()


def _placement(spec, clip, source, size, analysis=None):
    """1 クリップの縮尺と、縮尺後の左上の出力座標。height は元動画の高さが画面の高さの何倍かを表す。

    analysis があれば fit で合わせる。xs・ys は元のフレームごとの左上（固定なら None で x・y を使う）。"""
    performer = spec.data["performer"]
    place, fit = performer["placement"], performer["fit"]
    width, height = size
    scale = place["height"] * height / source.height
    if analysis is not None and fit["mode"] == "body":
        scale = fit["body_height"] * height / analysis["p90_height"]
    scale *= clip["placement"]["scale"]
    new = (max(2, int(round(source.width * scale))), max(2, int(round(source.height * scale))))
    flip = place["flip_x"] != clip["placement"]["flip_x"]
    pivot_x = 1.0 - place["pivot"][0] if flip else place["pivot"][0]    # 反転しても元動画の同じ点を anchor に合わせる
    x = place["anchor"][0] * width - pivot_x * new[0]
    y = place["anchor"][1] * height - place["pivot"][1] * new[1]
    xs = ys = None
    if analysis is not None:
        sx, sy = new[0] / source.width, new[1] / source.height
        center = lambda value: place["anchor"][0] * width - sx * (source.width - value if flip else value)  # noqa: E731
        if fit["x"] == "median":
            x = center(analysis["median_cx"])
        elif fit["x"] == "track":
            xs = center(np.asarray(analysis["cx"], dtype=np.float64))
        if fit["ground"] == "median":
            y = place["anchor"][1] * height - sy * analysis["median_foot"]
        elif fit["ground"] == "lock":
            ys = place["anchor"][1] * height - sy * np.asarray(analysis["ground"], dtype=np.float64)
    dx, dy = clip["placement"]["offset"][0] * width, clip["placement"]["offset"][1] * height
    return {"scale": scale, "size": new, "x": x + dx, "y": y + dy, "flip": flip, "source": (source.width, source.height),
            "xs": None if xs is None else xs + dx, "ys": None if ys is None else ys + dy}


def _origin(place, number):
    """元のフレーム番号 number のときの、縮尺後の左上の出力座標。"""
    x = place["x"] if place["xs"] is None else float(place["xs"][min(number, len(place["xs"]) - 1)])
    y = place["y"] if place["ys"] is None else float(place["ys"][min(number, len(place["ys"]) - 1)])
    return x, y


def placement_matrix(place, number):
    """元動画の画素 → カメラの前の画面の画素の 2×3（左右反転を含む）。"""
    x, y = _origin(place, number)
    sx, sy = place["size"][0] / place["source"][0], place["size"][1] / place["source"][1]
    if place["flip"]:
        return [[-sx, 0.0, x + place["size"][0]], [0.0, sy, y]]
    return [[sx, 0.0, x], [0.0, sy, y]]


def _fit_analysis(spec, clips, out):
    """{path: (解析の結果, キャッシュのパス)}。fit を使わない spec では空。"""
    performer = spec.data["performer"]
    fit = performer["fit"]
    if fit is None or (fit["mode"] == "frame" and fit["x"] == "none" and fit["ground"] == "none"):
        return {}
    found = {}
    for clip in performer["clips"]:
        if clip["fit"] and clip["path"] not in found:
            found[clip["path"]] = dancefit.load(clips.sources[clip["path"]], sha256(spec.resolve(clip["path"])),
                                                performer["key"], fit, out)
    return found


def prepare(spec, scale=1.0, out=None):
    """out は fit の解析のキャッシュを置く場所（既定は spec の隣の dance_runs）。"""
    size = scaled_size(spec, scale)
    stage = Stage(spec, size)
    clips = Timeline(spec)
    try:
        analysis = _fit_analysis(spec, clips, Path(out) if out else spec.base_dir / "dance_runs")
        place = [_placement(spec, clip, clips.sources[clip["path"]], size, analysis[clip["path"]][0] if clip["fit"] and clip["path"] in analysis else None)
                 for clip in clips.clips]
        lines, text_warnings = dancelyrics.prepare(spec.data, spec.resolve, size)
    except BaseException:
        clips.close()
        stage.close()
        raise
    warnings = list(spec.warnings) + text_warnings
    sizes = {(s.width, s.height) for s in clips.sources.values()}
    if len(sizes) > 1 and not analysis:
        warnings.append(f"performer: 人物動画の寸法がそろっていません {sorted(sizes)}（切れ目で人物の大きさが変わることがあります）")
    flashes = [{**f, "linear": color_to_linear(f["color"]), "display": np.asarray(f["color"], dtype=np.float32) / 255.0}
               for f in spec.data["flashes"]]
    records = [dancefit.record(path, *found) for path, found in analysis.items()]
    camera = Camera(spec.data["camera"], size, spec.data["output"]["fps"])
    return Context(spec, size, stage, clips, place, lines, warnings, flashes, records, camera=camera)


# ---- 1 フレーム ---------------------------------------------------------------

_is_identity = is_identity


def camera_state(camera, t, size=(16, 16), fps=24.0):
    """時刻 t の (zoom, center, rotate_deg)。seams まで含めた値（アクセント・揺れは含む）。"""
    state = Camera(camera, size, fps).state(t)
    return state.zoom * state.seam_zoom, state.center, state.rotate + state.seam_rotate


def _warp_stage(ctx, image, matrix):
    return cv2.warpAffine(image, matrix.astype(np.float32), ctx.size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _stage_view(ctx, t, state, samples):
    """カメラ・視差・ブレを掛けたステージ。書き換えてよい新しい配列を返す。"""
    image, parallax = ctx.stage.at(t), ctx.spec.data["stage"]["parallax"]
    if samples:
        return sum(_warp_stage(ctx, image, ctx.camera.matrix(item, parallax)) for item in samples) / np.float32(len(samples))
    matrix = ctx.camera.matrix(state, parallax)
    return image.copy() if is_identity(matrix) else _warp_stage(ctx, image, matrix)


def _cut(ctx, index, image):
    """clip index の 1 枚を緑抜きして配置の大きさにした (前乗算の線形色, アルファ)。静止画は 1 回だけ作る。"""
    if index in ctx.stills:
        return ctx.stills[index]
    place = ctx.place[index]
    color, alpha = dancekey.cutout(image, ctx.spec.data["performer"]["key"])
    method = cv2.INTER_AREA if place["scale"] < 1 else cv2.INTER_LINEAR
    color = cv2.resize(color, place["size"], interpolation=method)
    alpha = cv2.resize(alpha, place["size"], interpolation=method)
    if place["flip"]:
        color, alpha = color[:, ::-1].copy(), alpha[:, ::-1].copy()
    if ctx.clips.clips[index]["kind"] == "image":
        ctx.stills[index] = color, alpha
    return color, alpha


def _warped(ctx, color, alpha, x, y, matrix):
    full = np.vstack([matrix, [0.0, 0.0, 1.0]]) @ np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], dtype=np.float64)
    warp = full[:2].astype(np.float32)
    color = cv2.warpAffine(color, warp, ctx.size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    alpha = cv2.warpAffine(alpha, warp, ctx.size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return color, alpha


def _placed(ctx, shown, matrices):
    """1 つの素材を画面に置く。matrices が複数ならブレで、前乗算の色とアルファを平均する。"""
    color, alpha = _cut(ctx, shown.index, shown.image)
    x, y = _origin(ctx.place[shown.index], shown.frame)
    if len(matrices) == 1:
        return _warped(ctx, color, alpha, x, y, matrices[0])
    placed = [_warped(ctx, color, alpha, x, y, matrix) for matrix in matrices]
    count = np.float32(len(placed))
    return sum(item[0] for item in placed) / count, sum(item[1] for item in placed) / count


def _performer(ctx, t, matrix, samples=()):
    """人物の (前乗算の線形色, アルファ) を画面の大きさで返す。人物のいない時刻は None。blend の窓では 2 本を重ねる。"""
    shown = ctx.clips.shown(t)
    if not shown:
        return None
    matrices = [ctx.camera.matrix(item) for item in samples] or [matrix]
    if len(shown) == 1:
        return _placed(ctx, shown[0], matrices)
    (old_color, old_alpha), (new_color, new_alpha) = [_placed(ctx, item, matrices) for item in shown]
    weight = np.float32(shown[1].weight)
    return old_color + (new_color - old_color) * weight, old_alpha + (new_alpha - old_alpha) * weight


def _flash(out, flash, t):
    age = t - flash["t"]
    if not 0 <= age < 5 * flash["decay_s"]:
        return out
    strength = float(flash["strength"] * math.exp(-age / flash["decay_s"]))
    if flash["mode"] == "add_display":
        return from_srgb_float(to_srgb_float(out) + flash["display"] * np.float32(strength))
    return out + (flash["linear"] - out) * strength


def frame(ctx, t):
    """時刻 t の 1 フレームを uint8 (H, W, 3) RGB で返す。"""
    dt = 1.0 / ctx.fps
    state = ctx.camera.state(t)
    samples = ctx.camera.blur_states(t)
    out = _stage_view(ctx, t, state, samples)
    for line in ctx.lines:
        if line.layer == "back":
            dancelyrics.draw(out, line, t, dt)
    person = _performer(ctx, t, ctx.camera.matrix(state), samples)
    if person is not None:
        color, alpha = person
        out = color + out * (1.0 - alpha[..., None])
    for flash in ctx.flashes:
        out = _flash(out, flash, t)
    for line in ctx.lines:
        if line.layer == "band":
            dancelyrics.draw(out, line, t, dt)
    return to_srgb8(out)


def _frame_row(ctx, t, fitted):
    """timeline の frames の 1 行。hooks に関わらず spec だけから決まる値。"""
    shown = ctx.clips.mapping(t)
    state = ctx.camera.state(t)
    zoom, center, rotate, moved, seam_zoom, seam_rotate = ctx.camera.view(state)
    last = shown[-1] if shown else None
    row = [round(t, 5), None if last is None else last[0], None if last is None else last[1],
           None if len(shown) < 2 else round(last[2], 5), round(zoom * seam_zoom, 6), round(center[0], 6), round(center[1], 6),
           round(rotate + seam_rotate, 5), round(moved[0], 3), round(moved[1], 3), len(ctx.camera.blur_states(t))]
    if fitted:
        matrix = placement_matrix(ctx.place[last[0]], last[1]) if last else None
        row += [round(v, 4) for r in matrix for v in r] if matrix else [None] * len(PLACEMENT_FIELDS)
    return row


def timeline_record(ctx, start=0.0, duration=None, frames=None):
    """run に残す timeline.json の中身。`mv gate check` はこれを読む（描画の入力ではない）。"""
    data, (width, height) = ctx.spec.data, ctx.size
    norm = lambda b: [round(b[0] / width, 5), round(b[1] / height, 5), round(b[2] / width, 5), round(b[3] / height, 5)]  # noqa: E731
    length = ctx.duration - start if duration is None else duration
    count = timeline.frame_count(ctx.duration - start, ctx.fps) if frames is None else frames
    record = {
        "schema_version": "dance-timeline/0.1",
        "size": [width, height], "fps": ctx.fps, "duration_s": ctx.duration,
        "interval": {"start": start, "duration": length, "frames": count},
        "lyrics": [{"layer": line.layer, "text": line.text, "start": line.start, "end": line.end,
                    "fade_in_s": line.style["fade_in_s"], "fade_out_s": line.style["fade_out_s"],
                    "box_px": line.box_px, "box": norm(line.box_px), "overflow": line.overflow} for line in ctx.lines],
        "clips": [{"path": c["path"], "start": c["start"], "end": c["end"]} for c in data["performer"]["clips"]],
        "seams": ctx.clips.seams(),
        "flashes": [f["t"] for f in data["flashes"]],
        "ui_regions": data["lyrics"]["ui_regions"],
    }
    if data["source_schema"] == SCHEMA_V01:
        return record
    clips = data["performer"]["clips"]
    fitted = bool(ctx.analysis)
    audio = data["audio"]
    record.update({
        "schema_version": "dance-timeline/0.2",
        "beats": None if data["beats"] is None else {"beats": data["beats"]["beats"], "downbeats": data["beats"]["downbeats"]},
        "clips": [{"path": c["path"], "start": c["start"], "end": c["end"], "kind": c["kind"], "transition_in": c["transition_in"],
                   "beat_exempt": c["beat_exempt"]} for c in clips],
        "seam_info": [{"t": t, "from": left, "to": right, "kind": clips[right]["transition_in"]["type"], "beat_exempt": clips[right]["beat_exempt"]}
                      for t, left, right in ctx.clips.seam_list()],
        "frames_fields": FRAME_FIELDS + (PLACEMENT_FIELDS if fitted else []),
        "frames": [_frame_row(ctx, timeline.frame_time(i, ctx.fps, start), fitted) for i in range(count)],
        "analysis": ctx.analysis,
        "camera_seams": [{k: seam[k] for k in ("t", "span_s", "pre_s", "post_s", "zoom", "rotate_deg", "rotate_post_deg", "pivot", "blur")}
                         for seam in data["camera"]["seams"]],
        "accents": data["camera"]["accents"],
        "flash_info": [{k: f[k] for k in ("t", "strength", "decay_s", "mode", "beat_exempt")} for f in data["flashes"]],
        "audio": None if audio is None else {"fit": audio["fit"], "end_s": audio["end_s"]},
        "finished": None,
    })
    return record
