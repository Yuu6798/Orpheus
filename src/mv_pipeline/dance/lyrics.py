"""歌詞の配置と描画。

層（lyrics.layers）は z = behind（人物の後ろ）か front（フラッシュの後）に属し、描く順は z ごとに書いた順。既定の層は
back = 人物の後ろの大きな字（フック）、band = 上の帯の字。字は画面に固定で、follow_camera の行だけカメラに乗る。

配置は行全体で 1 回だけ決め、時刻で動かさない（箱が 1 つに決まって UI 枠を確かめやすいため）。enter・exit の動きは配置の上に
掛けるだけで、timeline の箱は動きの無い位置を指す。強調は色だけで行う。words の t が来たら、その語が accent_color（語の color が
あればその色）に変わる。t が無ければ最初から。reveal "word" では語は t まで見えない。

words の row で行を縦に積める。全 row を同じ大きさにし、箱の幅と高さに収まる最大の大きさにする。row の頭の空白は詰める。

字は箱に収まるように縮める。描いた字のインクの範囲（box_px）を timeline.json に残す。
"""
import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..fx import timeline
from ..fx.color import color_to_linear
from ..io import PipelineError

BASE_WIDTH = 1080.0   # size_px・stroke_px・rise_px・radius_px・MIN_FONT_PX は幅 1080 の画面での値
MIN_FONT_PX = 8
OVERFLOW_SLACK_PX = 2 # 箱からこれ以上はみ出したら「収まらない」とする
STAGGER = ("left", "center", "right")


@dataclass
class Run:
    x: int
    y: int
    fill: np.ndarray            # float32 (h, w) 0..1
    stroke: object              # 同じ寸法の float32、または None
    accent_at: object           # float（その時刻から accent の色）/ None（強調しない）
    accent: np.ndarray          # 強調の色（線形）
    word: int = 0               # words の番号
    appear: float = 0.0         # 見え始める時刻
    glow: object = None         # ぼかした字。float32。fill より glow_pad だけ大きい
    glow_pad: int = 0


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
    sung: bool = True
    words: tuple = ()           # timeline に残す語 [{text, t, row, box_px}]
    pixel: float = 1.0          # 幅 1080 の 1 画素が今の画面の何画素か

    def __post_init__(self):
        self.base = color_to_linear(self.style["color"])
        self.edge = color_to_linear(self.style["stroke_color"])
        glow = self.style["glow"]
        self.glow = None if glow is None else color_to_linear(glow["color"]) * np.float32(glow["strength"])
        ink = _ink_extent(self.runs)
        self.center = ((ink[0] + ink[2]) / 2.0, (ink[1] + ink[3]) / 2.0)

    @property
    def follow_camera(self):
        return self.style["follow_camera"]

    def fade(self, t, dt, appear=None):
        """時刻 t の濃さ 0..1。start の最初のフレームから見え、end の直前まで残る。exit があれば fade_out の代わりになる。"""
        appear = self.start if appear is None else appear
        if not appear <= t < self.end:
            return 0.0
        fade_in, fade_out = self.style["fade_in_s"], self.fall_s
        rise = 1.0 if fade_in <= 0 else min(1.0, (t - appear + dt) / fade_in)
        fall = 1.0 if fade_out <= 0 else min(1.0, (self.end - t) / fade_out)
        return min(rise, fall)

    @property
    def fall_s(self):
        leave = self.style["exit"]
        return self.style["fade_out_s"] if leave is None else leave["dur_s"]

    def move(self, run, t, dt):
        """語の動きを (倍率, tx, ty) で返す（q → 倍率·q + (tx, ty)）。動かない時刻は None。"""
        come, leave = self.style["enter"], self.style["exit"]
        scale, rise, grow = 1.0, 0.0, 1.0
        if come is not None:
            u = (t - run.appear + dt) / come["dur_s"]
            if u < 1.0:
                eased = float(timeline.ease("out", u))
                scale = come["scale_from"] + (1.0 - come["scale_from"]) * eased
                rise = come["rise_px"] * self.pixel * (1.0 - eased)
        if leave is not None and self.end - t < leave["dur_s"]:
            grow = 1.0 + (leave["scale_to"] - 1.0) * float(timeline.ease("in", 1.0 - (self.end - t) / leave["dur_s"]))
        if scale == 1.0 and rise == 0.0 and grow == 1.0:
            return None
        word = _ink_extent([run])
        cx, cy = (word[0] + word[2]) / 2.0, (word[1] + word[3]) / 2.0
        return (grow * scale, grow * cx * (1.0 - scale) + self.center[0] * (1.0 - grow),
                grow * (cy * (1.0 - scale) - rise) + self.center[1] * (1.0 - grow))


def _font(fonts, name, resolve, pixels):
    pixels = max(1, int(round(pixels)))
    entry = fonts.get(name) if name else None
    if entry:
        path = resolve(entry["path"])
        if not path.is_file():
            raise PipelineError(f"lyrics.font が見つかりません: {entry['path']}")
        return ImageFont.truetype(str(path), pixels, index=entry["index"])
    return ImageFont.load_default(pixels)


def _fit(fonts, name, resolve, rows, pixels, box_w, box_h, stroke, minimum, gap_em):
    """全 row が箱に収まる最大のフォント（size_px から 5% ずつ縮める）。minimum まで縮めても収まらなければ minimum のフォントを返す。"""
    while True:
        font = _font(fonts, name, resolve, pixels)
        sizes = [font.getbbox(text, stroke_width=stroke) for text in rows]
        tall = max(y1 - y0 for _, y0, _, y1 in sizes)
        block = len(rows) * tall + (len(rows) - 1) * gap_em * pixels
        if (all(x1 - x0 <= box_w for x0, _, x1, _ in sizes) and block <= box_h) or pixels <= minimum:
            return font, tall, pixels
        pixels *= 0.95


def _mask(font, text, width, height, x, baseline, stroke=0):
    image = Image.new("L", (width, height), 0)
    ImageDraw.Draw(image).text((x, baseline), text, font=font, fill=255, anchor="ls", stroke_width=stroke)
    return np.asarray(image, dtype=np.float32) / 255.0


def _grouped(item):
    """[(row の文字列, [(words の番号, 語)])]。row の頭の空白は詰める。"""
    words = item["words"] or [{"text": item["text"], "t": None, "accent": False, "color": None, "row": 0}]
    rows = {}
    for index, word in enumerate(words):
        rows.setdefault(word["row"], []).append((index, word))
    grouped = []
    for number in sorted(rows):
        members, head = [], True
        for index, word in rows[number]:
            text = word["text"].lstrip() if head else word["text"]
            head = head and not text
            members.append((index, {**word, "text": text}))
        grouped.append(("".join(word["text"] for _, word in members), members))
    return grouped


def _row_runs(font, text, members, stroke, item, style, accent):
    """1 つの row を語ごとの Run に分ける。座標は baseline と行頭が (0, 0) の位置。"""
    ink = font.getbbox(text, anchor="ls", stroke_width=stroke)
    # 行全体を 1 回で描いてから語の境目の列で切る。語ごとに描くと字詰めや合字が変わり、強調で字の位置が動くため
    pad = stroke + 2
    w, h = ink[2] - ink[0] + 2 * pad, ink[3] - ink[1] + 2 * pad
    local = (pad - ink[0], pad - ink[1])
    fill = _mask(font, text, w, h, *local)
    edge = _mask(font, text, w, h, *local, stroke=stroke) if stroke else None
    runs, left, prefix = [], 0, ""
    for position, (index, word) in enumerate(members):
        prefix += word["text"]
        right = w if position == len(members) - 1 else min(w, local[0] + int(round(font.getlength(prefix))))
        if right > left:
            accent_at = (word["t"] if word["t"] is not None else item["start"]) if word["accent"] else None
            color = accent if word["color"] is None else color_to_linear(word["color"])
            appear = word["t"] if style["reveal"] == "word" and word["t"] is not None else item["start"]
            runs.append(Run(ink[0] - pad + left, ink[1] - pad, fill[:, left:right], None if edge is None else edge[:, left:right],
                            accent_at, color, index, appear))
            left = right
    return runs, ink


def _align(row, count):
    return STAGGER[row % len(STAGGER)] if count == "stagger" else count


def _target(align, box, ink):
    """row のインク ink の左右の中央を置く x。"""
    half = (ink[2] - ink[0]) / 2
    return {"left": box[0] + half, "right": box[2] - half}.get(align, (box[0] + box[2]) / 2)


def _add_glow(run, glow, scale):
    radius = glow["radius_px"] * scale
    run.glow_pad = int(math.ceil(3 * radius)) + 1
    padded = np.pad(run.fill, run.glow_pad)
    run.glow = cv2.GaussianBlur(padded, (0, 0), max(0.1, radius))


def layout(item, style, fonts, resolve, size):
    """1 行を箱の中に配置して Line を作る（語ごとの Run に分ける）。style は層の設定に行の上書きを重ねたもの。"""
    width, height = size
    scale = width / BASE_WIDTH
    stroke = int(round(style["stroke_px"] * scale))
    x0, y0, x1, y1 = style["box"]
    box = (x0 * width, y0 * height, x1 * width, y1 * height)
    grouped = _grouped(item)
    gap_em = style["rows"]["gap_em"] if len(grouped) > 1 else 0.0
    font, tall, pixels = _fit(fonts, style["font"], resolve, [text for text, _ in grouped], style["size_px"] * scale, box[2] - box[0],
                              box[3] - box[1], stroke, MIN_FONT_PX * scale, gap_em)   # 最小も画面に比例させる（--scale の下見で、本番と同じ行が収まるように）
    accent = color_to_linear(style["accent_color"])
    pitch = tall + gap_em * pixels
    block = len(grouped) * tall + (len(grouped) - 1) * gap_em * pixels
    runs, rows = [], []
    for number, (text, members) in enumerate(grouped):
        row, ink = _row_runs(font, text, members, stroke, item, style, accent)
        align = _align(number, style["rows"]["align"])
        origin_x = int(round(_target(align, box, ink) - (ink[0] + ink[2]) / 2))
        if len(grouped) == 1:
            baseline = int(round((box[1] + box[3]) / 2 - (ink[1] + ink[3]) / 2))
        else:
            baseline = int(round((box[1] + box[3]) / 2 - block / 2 + tall + number * pitch))
        for run in row:
            run.x += origin_x
            run.y += baseline
        # getbbox は字の送り幅を含むので、実際のインクの範囲で箱に合わせ直す
        ink = _ink_extent(row)
        shift = int(round(_target(align, box, ink) - (ink[0] + ink[2]) / 2))
        for run in row:
            run.x += shift
        rows.append(row)
        runs += row
    ink = _ink_extent(runs)
    shift_y = int(round((box[1] + box[3]) / 2 - (ink[1] + ink[3]) / 2))
    for run in runs:
        run.y += shift_y
    ink = _ink_extent(runs)
    overflow = (ink[0] < box[0] - OVERFLOW_SLACK_PX or ink[1] < box[1] - OVERFLOW_SLACK_PX
                or ink[2] > box[2] + OVERFLOW_SLACK_PX or ink[3] > box[3] + OVERFLOW_SLACK_PX)
    box_px = [max(0, ink[0]), max(0, ink[1]), min(width, ink[2]), min(height, ink[3])]
    if style["glow"] is not None:
        for run in runs:
            _add_glow(run, style["glow"], scale)
    words = [{"text": word["text"], "t": word["t"], "row": word["row"],
              "box_px": _clipped(_ink_extent([run for run in runs if run.word == index]), size)}
             for row in grouped for index, word in row[1]]
    return Line(item["layer"], item["text"], item["start"], item["end"], box_px, runs, style, overflow,
                style["sung"] if item["sung"] is None else item["sung"], tuple(words), scale)


def _clipped(ink, size):
    return [max(0, ink[0]), max(0, ink[1]), min(size[0], ink[2]), min(size[1], ink[3])]


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


def _blit(canvas, mask, x, y, color, coverage=None):
    """canvas（線形 float32）に color を mask の濃さで重ねる。はみ出す分は切る。coverage があればアルファも重ねる。"""
    h, w = canvas.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + mask.shape[1]), min(h, y + mask.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    alpha = mask[y0 - y:y1 - y, x0 - x:x1 - x, None]
    region = canvas[y0:y1, x0:x1]
    region += (color - region) * alpha
    if coverage is not None:
        covered = coverage[y0:y1, x0:x1]
        covered += (1.0 - covered) * alpha[..., 0]


def _add(canvas, mask, x, y, color):
    """canvas に color × mask を足す（線形光）。"""
    h, w = canvas.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + mask.shape[1]), min(h, y + mask.shape[0])
    if x1 > x0 and y1 > y0:
        canvas[y0:y1, x0:x1] += color * mask[y0 - y:y1 - y, x0 - x:x1 - x, None]


def _moved(mask, x, y, move):
    """mask（左上 (x, y)）を move で動かした (mask, x, y)。"""
    if move is None:
        return mask, x, y
    scale, tx, ty = move
    h, w = mask.shape
    left, top = int(math.floor(scale * x + tx)) - 1, int(math.floor(scale * y + ty)) - 1
    size = (int(math.ceil(w * scale)) + 3, int(math.ceil(h * scale)) + 3)
    matrix = np.array([[scale, 0.0, scale * x + tx - left], [0.0, scale, scale * y + ty - top]], dtype=np.float32)
    return cv2.warpAffine(mask, matrix, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0), left, top


def draw(canvas, line, t, dt, coverage=None):
    """canvas に 1 行を描く（その場で書き換える）。見えない時刻なら何もしない。coverage（float32 (H, W)）があればアルファも描く。"""
    for run in line.runs:
        strength = line.fade(t, dt, run.appear) * line.style["opacity"]
        if strength <= 0:
            continue
        move = line.move(run, t, dt)
        if run.glow is not None:
            _add(canvas, *_moved(run.glow, run.x - run.glow_pad, run.y - run.glow_pad, move), line.glow * np.float32(strength))
        if run.stroke is not None:
            _blit(canvas, *_moved(run.stroke * strength, run.x, run.y, move), line.edge, coverage)
        color = run.accent if run.accent_at is not None and t >= run.accent_at else line.base
        _blit(canvas, *_moved(run.fill * strength, run.x, run.y, move), color, coverage)


def overlaps(box_px, region, size):
    """box_px（画素）と region の box（正規化）が重なる面積が正か。"""
    width, height = size
    x0, y0, x1, y1 = region["box"]
    return min(box_px[2], x1 * width) > max(box_px[0], x0 * width) and min(box_px[3], y1 * height) > max(box_px[1], y0 * height)


def prepare(data, resolve, size):
    """(Line の列, warnings)。UI 枠に重なる行と、箱に収まらない行は warnings に残す（描画は止めない）。"""
    spec_lyrics = data["lyrics"]
    layers = {layer["name"]: layer for layer in spec_lyrics["layers"]}
    lines, warnings = [], []
    for item in spec_lyrics["lines"]:
        style = {**layers[item["layer"]], **item["style"]}
        style["font"] = style["font"] or spec_lyrics["font"]
        line = layout(item, style, spec_lyrics["fonts"], resolve, size)
        lines.append(line)
        if line.overflow:
            warnings.append(f"lyrics: {line.text!r}（{line.start:g} 秒）が最小の大きさでも {line.layer} の箱に収まりません")
        for region in spec_lyrics["ui_regions"]:
            if overlaps(line.box_px, region, size):
                warnings.append(f"lyrics: {line.text!r}（{line.start:g} 秒）が UI 枠 {region['name']!r} に重なります")
    return lines, warnings
