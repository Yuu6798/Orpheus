"""縦の歌詞の配置と描画。

層は 2 つ: back = 人物の後ろの大きな字（フック）、band = 上の帯の字。どちらも画面に固定で、カメラの動きには乗らない
（読みやすさのためと、字の位置が時刻によらず 1 つに決まって UI 枠を確かめやすいため）。
強調は色だけで行う（大きさも位置も変えない）。words の t が来たら、その語が accent_color に変わる。t が無ければ最初から。

字は箱に収まるように縮める。描いた字のインクの範囲（box_px）を timeline.json に残す。
"""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..fx.color import color_to_linear
from ..io import PipelineError

BASE_WIDTH = 1080.0   # size_px・stroke_px・MIN_FONT_PX は幅 1080 の画面での値
MIN_FONT_PX = 8
OVERFLOW_SLACK_PX = 2 # 箱からこれ以上はみ出したら「収まらない」とする


@dataclass
class Run:
    x: int
    y: int
    fill: np.ndarray            # float32 (h, w) 0..1
    stroke: object              # 同じ寸法の float32、または None
    accent_at: object           # float（その時刻から accent の色）/ None（強調しない）


@dataclass
class Line:
    layer: str
    text: str
    start: float
    end: float
    box_px: list
    runs: list
    style: dict
    overflow: bool = False      # 最小の大きさでも箱に収まらない（box_px は画面内に切った値なので、欠けはこの印で分かる）

    def fade(self, t, dt):
        """時刻 t の濃さ 0..1。start の最初のフレームから見え、end の直前まで残る。"""
        if not self.start <= t < self.end:
            return 0.0
        fade_in, fade_out = self.style["fade_in_s"], self.style["fade_out_s"]
        rise = 1.0 if fade_in <= 0 else min(1.0, (t - self.start + dt) / fade_in)
        fall = 1.0 if fade_out <= 0 else min(1.0, (self.end - t) / fade_out)
        return min(rise, fall)


def _font(lyrics_spec, resolve, pixels):
    pixels = max(1, int(round(pixels)))
    if lyrics_spec["font"]:
        path = resolve(lyrics_spec["font"])
        if not path.is_file():
            raise PipelineError(f"lyrics.font が見つかりません: {lyrics_spec['font']}")
        return ImageFont.truetype(str(path), pixels, index=lyrics_spec["font_index"])
    return ImageFont.load_default(pixels)


def _fit(lyrics_spec, resolve, text, pixels, box_w, box_h, stroke, minimum):
    """箱に収まる最大のフォント（size_px から 5% ずつ縮める）。minimum まで縮めても収まらなければ minimum のフォントを返す。"""
    while True:
        font = _font(lyrics_spec, resolve, pixels)
        x0, y0, x1, y1 = font.getbbox(text, stroke_width=stroke)
        if (x1 - x0 <= box_w and y1 - y0 <= box_h) or pixels <= minimum:
            return font
        pixels *= 0.95


def _mask(font, text, width, height, x, baseline, stroke=0):
    image = Image.new("L", (width, height), 0)
    ImageDraw.Draw(image).text((x, baseline), text, font=font, fill=255, anchor="ls", stroke_width=stroke)
    return np.asarray(image, dtype=np.float32) / 255.0


def layout(line, lyrics_spec, resolve, size):
    """1 行を箱の中央に配置して Line を作る（語ごとの Run に分ける）。"""
    width, height = size
    style = lyrics_spec[line["layer"]]
    scale = width / BASE_WIDTH
    stroke = int(round(style["stroke_px"] * scale))
    x0, y0, x1, y1 = style["box"]
    box = (x0 * width, y0 * height, x1 * width, y1 * height)
    font = _fit(lyrics_spec, resolve, line["text"], style["size_px"] * scale, box[2] - box[0], box[3] - box[1], stroke,
                MIN_FONT_PX * scale)   # 最小も画面に比例させる（--scale の下見で、本番と同じ行が収まるように）
    ink = font.getbbox(line["text"], anchor="ls", stroke_width=stroke)
    origin_x = int(round((box[0] + box[2]) / 2 - (ink[0] + ink[2]) / 2))
    baseline = int(round((box[1] + box[3]) / 2 - (ink[1] + ink[3]) / 2))
    words = line["words"] or [{"text": line["text"], "t": None, "accent": False}]
    # 行全体を 1 回で描いてから語の境目の列で切る。語ごとに描くと字詰めや合字が変わり、強調で字の位置が動くため
    pad = stroke + 2
    w, h = ink[2] - ink[0] + 2 * pad, ink[3] - ink[1] + 2 * pad
    local = (pad - ink[0], pad - ink[1])
    fill = _mask(font, line["text"], w, h, *local)
    edge = _mask(font, line["text"], w, h, *local, stroke=stroke) if stroke else None
    runs, left, prefix = [], 0, ""
    for index, word in enumerate(words):
        prefix += word["text"]
        right = w if index == len(words) - 1 else min(w, local[0] + int(round(font.getlength(prefix))))
        if right > left:
            accent_at = (word["t"] if word["t"] is not None else line["start"]) if word["accent"] else None
            runs.append(Run(origin_x + ink[0] - pad + left, baseline + ink[1] - pad, fill[:, left:right],
                            None if edge is None else edge[:, left:right], accent_at))
            left = right
    # getbbox は字の送り幅を含むので、実際のインクの範囲で箱の中央に合わせ直す
    ink = _ink_extent(runs)
    shift_x = int(round((box[0] + box[2]) / 2 - (ink[0] + ink[2]) / 2))
    shift_y = int(round((box[1] + box[3]) / 2 - (ink[1] + ink[3]) / 2))
    for run in runs:
        run.x += shift_x
        run.y += shift_y
    ink = _ink_extent(runs)
    overflow = (ink[0] < box[0] - OVERFLOW_SLACK_PX or ink[1] < box[1] - OVERFLOW_SLACK_PX
                or ink[2] > box[2] + OVERFLOW_SLACK_PX or ink[3] > box[3] + OVERFLOW_SLACK_PX)
    box_px = [max(0, ink[0]), max(0, ink[1]), min(width, ink[2]), min(height, ink[3])]
    return Line(line["layer"], line["text"], line["start"], line["end"], box_px, runs, style, overflow)


def _ink_extent(runs):
    """実際に描かれる画素の範囲 [x0, y0, x1, y1]（画面に切らない値）。"""
    boxes = []
    for run in runs:
        mask = run.stroke if run.stroke is not None else run.fill
        ys, xs = np.nonzero(mask > 0.02)
        if len(xs):
            boxes.append((run.x + int(xs.min()), run.y + int(ys.min()), run.x + int(xs.max()) + 1, run.y + int(ys.max()) + 1))
    if not boxes:
        return [0, 0, 0, 0]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _blit(canvas, mask, x, y, color):
    """canvas（線形 float32）に color を mask の濃さで重ねる。はみ出す分は切る。"""
    h, w = canvas.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + mask.shape[1]), min(h, y + mask.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    alpha = mask[y0 - y:y1 - y, x0 - x:x1 - x, None]
    region = canvas[y0:y1, x0:x1]
    region += (color - region) * alpha


def draw(canvas, line, t, dt):
    """canvas に 1 行を描く（その場で書き換える）。見えない時刻なら何もしない。"""
    strength = line.fade(t, dt) * line.style["opacity"]
    if strength <= 0:
        return
    base = color_to_linear(line.style["color"])
    accent = color_to_linear(line.style["accent_color"])
    edge = color_to_linear(line.style["stroke_color"])
    for run in line.runs:
        if run.stroke is not None:
            _blit(canvas, run.stroke * strength, run.x, run.y, edge)
        color = accent if run.accent_at is not None and t >= run.accent_at else base
        _blit(canvas, run.fill * strength, run.x, run.y, color)


def overlaps(box_px, region, size):
    """box_px（画素）と region の box（正規化）が重なる面積が正か。"""
    width, height = size
    x0, y0, x1, y1 = region["box"]
    return min(box_px[2], x1 * width) > max(box_px[0], x0 * width) and min(box_px[3], y1 * height) > max(box_px[1], y0 * height)


def prepare(data, resolve, size):
    """(Line の列, warnings)。UI 枠に重なる行と、箱に収まらない行は warnings に残す（描画は止めない）。"""
    spec_lyrics = data["lyrics"]
    lines, warnings = [], []
    for item in spec_lyrics["lines"]:
        line = layout(item, spec_lyrics, resolve, size)
        lines.append(line)
        if line.overflow:
            warnings.append(f"lyrics: {line.text!r}（{line.start:g} 秒）が最小の大きさでも {line.layer} の箱に収まりません")
        for region in spec_lyrics["ui_regions"]:
            if overlaps(line.box_px, region, size):
                warnings.append(f"lyrics: {line.text!r}（{line.start:g} 秒）が UI 枠 {region['name']!r} に重なります")
    return lines, warnings
