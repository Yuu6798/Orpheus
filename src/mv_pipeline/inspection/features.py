"""コマごとの特徴。1 回の復号で、解析の大きさ（既定の幅 320）に縮めたコマから測る。

- 画面: 明るさの平均、前のコマとの差、差分と照合に使う小さな灰色の画（解析の大きさの 1/4）。
- 人物（前景がある時だけ）: 緑抜き（dance の key と同じ式）か固定の箱で切り抜いた輪郭。面積・囲み・重心・塊の数・
  囲みにそろえた 32×32 の輪郭・内側の画素の配色（頭・上半身・下半身の帯ごとにも）。
- 動き: 前のコマからの密なオプティカルフロー（Farneback。解析の大きさの半分で測る）の大きさの p95 と、大きく動いた所の
  向きと範囲。人物があれば人物のまわりだけを見て、体の高さで割る。
- 設定画: 人物の内側の画素と設定画の主な色との重なり（reference.frame_stats）。

ここで測るのは数値だけで、異常かどうかは決めない（detect が信号にし、人が見て決める）。
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..dance import key as dancekey
from . import reference, video
from .detect import BANDS, bhattacharyya

SMALL = 4                 # 差分を比べる小さな画は解析の大きさの 1/SMALL
SHAPE = 32                # 輪郭を囲みにそろえて比べる大きさ
BAND_NAMES = ("head", "upper", "lower")
GRAY_BINS, HUE_BINS, SAT_BINS, VAL_BINS, LOW_SAT = 8, 16, 3, 3, 40
BINS = GRAY_BINS + HUE_BINS * SAT_BINS * VAL_BINS
HUE_SHIFT = 6             # 赤（色相 0）が色相の箱の中央に来るようにずらす（OpenCV の色相は 0..179）
MIN_PRESENT = 0.004       # 人物がいるとみなす面積（画面の割合）
SPECK = 0.0005            # これより小さい塊は雑音として捨てる（画面の割合）
COMPONENT = (0.0015, 0.02)  # 数える塊: 画面の 0.15% 以上で、いちばん大きい塊の 2% 以上（離れた手ほどの大きさ）


def color_bins(hsv):
    """OpenCV の HSV（uint8、色相 0..179）の画素 (M, 3) → 配色の箱の番号 (M,)。彩度の低い画素は明るさだけで 8 つに分ける。"""
    h, s, v = (hsv[:, c].astype(np.int32) for c in range(3))
    gray = np.minimum(v * GRAY_BINS // 256, GRAY_BINS - 1)
    hue = np.minimum(((h + HUE_SHIFT) % 180) * HUE_BINS // 180, HUE_BINS - 1)
    sat = np.minimum(np.maximum(s - LOW_SAT, 0) * SAT_BINS // (256 - LOW_SAT), SAT_BINS - 1)
    val = np.minimum(v * VAL_BINS // 256, VAL_BINS - 1)
    return np.where(s < LOW_SAT, gray, GRAY_BINS + (hue * SAT_BINS + sat) * VAL_BINS + val)


def histogram(bins):
    """箱の番号 → 合計 1 の配色の分布。画素が無ければ None。"""
    if bins.size == 0:
        return None
    counts = np.bincount(bins, minlength=BINS).astype(np.float64)
    return counts / counts.sum()


def mask_of(rgb, foreground):
    """人物の切り抜き（bool (h, w)）。前景を書いていなければ None。"""
    if not foreground or foreground["kind"] == "none":
        return None
    height, width = rgb.shape[:2]
    if foreground["kind"] == "box":
        x0, y0, x1, y1 = foreground["box"]
        mask = np.zeros((height, width), bool)
        mask[int(round(y0 * height)):int(round(y1 * height)), int(round(x0 * width)):int(round(x1 * width))] = True
        return mask
    alpha = dancekey.alpha_of(rgb, foreground["key"])
    mask = (alpha > 0.5).astype(np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))) > 0


@dataclass
class Silhouette:
    mask: np.ndarray            # 雑音を除いた bool (h, w)
    area: float
    bbox: list                  # 正規化 [x0, y0, x1, y1]（右と下は端を含まない）
    centroid: list
    components: int
    extra_box: list | None      # いちばん大きい塊のほかの塊の囲み
    shape: np.ndarray           # 囲みにそろえた (SHAPE, SHAPE) bool


def silhouette(mask):
    height, width = mask.shape
    total = float(height * width)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= SPECK * total]
    if not keep:
        return None
    clean = np.isin(labels, keep)
    largest = max(stats[i, cv2.CC_STAT_AREA] for i in keep)
    big = sorted((i for i in keep if stats[i, cv2.CC_STAT_AREA] >= max(COMPONENT[0] * total, COMPONENT[1] * largest)),
                 key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    rows, cols = np.nonzero(clean)
    y0, y1, x0, x1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    extra = None
    if len(big) > 1:
        boxes = [(stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP], stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH],
                  stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT]) for i in big[1:]]
        extra = [min(b[0] for b in boxes) / width, min(b[1] for b in boxes) / height,
                 max(b[2] for b in boxes) / width, max(b[3] for b in boxes) / height]
    crop = clean[y0:y1, x0:x1].astype(np.uint8) * 255
    shape = cv2.resize(crop, (SHAPE, SHAPE), interpolation=cv2.INTER_AREA) > 127
    return Silhouette(mask=clean, area=float(clean.sum()) / total, bbox=[x0 / width, y0 / height, x1 / width, y1 / height],
                      centroid=[float(cols.mean() + 0.5) / width, float(rows.mean() + 0.5) / height],
                      components=len(big), extra_box=extra, shape=shape)


def _interior(mask):
    """色を測る内側の画素（縁を 2 画素削る。細くて消えるときは切り抜きのまま）。"""
    inner = cv2.erode(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=2) > 0
    return inner if inner.sum() >= 16 else mask


def band_rows(bbox, height):
    """人物の囲みを頭・上半身・下半身に分けた行の範囲 [(r0, r1), …]。"""
    top, bottom = bbox[1] * height, bbox[3] * height
    span = bottom - top
    return [(int(round(top + a * span)), int(round(top + b * span))) for a, b in BANDS]


@dataclass
class Features:
    first: int
    fps: float
    size: tuple
    small: tuple
    has_foreground: bool
    luma: np.ndarray
    fdiff: np.ndarray
    gray: np.ndarray            # (n, hs, ws) uint8
    masks: np.ndarray | None    # (n, hs, ws) bool
    present: np.ndarray
    area: np.ndarray
    bbox: np.ndarray
    centroid: np.ndarray
    body: np.ndarray
    components: np.ndarray
    extra_box: list
    shape: np.ndarray
    hist: list
    band_hist: list
    flow: np.ndarray
    flow_vec: np.ndarray
    flow_box: list
    ref: list = field(default_factory=list)      # コマごとの reference.frame_stats（設定画が無ければ空）

    @property
    def count(self):
        return len(self.luma)

    def frame(self, i):
        """内部の番号 i → 元動画のコマ番号。"""
        return self.first + int(i)

    def region(self, i, j):
        """コマ i と j の人物の外（少し広げる）。人物が無い・外が狭すぎる（画面の 10% 未満）ときは画面全体。"""
        if self.masks is None:
            return None
        union = (self.masks[i] | self.masks[j]).astype(np.uint8)
        outside = cv2.dilate(union, np.ones((3, 3), np.uint8)) == 0
        return outside if outside.mean() >= 0.1 else None

    def diffs(self, i, j):
        """(差の平均, 構造の差)。構造の差は、それぞれの平均を引いてから比べる（明るさだけの変化を除く）。0..1。"""
        a, b = self.gray[i].astype(np.float32), self.gray[j].astype(np.float32)
        where = self.region(i, j)
        if where is not None:
            a, b = a[where], b[where]
        plain = float(np.mean(np.abs(a - b))) / 255.0
        structure = float(np.mean(np.abs((a - a.mean()) - (b - b.mean())))) / 255.0
        return plain, structure

    def shape_change(self, i, j):
        """1 − IoU（囲みにそろえた輪郭）。どちらかに人物がいなければ nan。"""
        if not (self.present[i] and self.present[j]):
            return math.nan
        a, b = self.shape[i], self.shape[j]
        union = np.logical_or(a, b).sum()
        return 1.0 - float(np.logical_and(a, b).sum()) / union if union else math.nan

    def area_change(self, i, j):
        if not (self.present[i] and self.present[j]):
            return math.nan
        return abs(math.log(self.area[j] / self.area[i]))

    def centroid_change(self, i, j):
        """重心の移動（体の高さで割る）。"""
        if not (self.present[i] and self.present[j]):
            return math.nan
        body = max(1e-6, 0.5 * (self.body[i] + self.body[j]))
        dx = (self.centroid[j, 0] - self.centroid[i, 0]) * self.size[0] / self.size[1]   # 横も縦と同じ長さの単位にする
        return math.hypot(dx, self.centroid[j, 1] - self.centroid[i, 1]) / body

    def palette_change(self, i, j):
        return bhattacharyya(self.hist[i], self.hist[j])

    def change_box(self, i, j):
        """コマ i と j で変わった所の囲み（正規化）。輪郭があれば輪郭の差、無ければ明るさの差。"""
        if self.masks is not None and (self.present[i] or self.present[j]):
            changed = self.masks[i] ^ self.masks[j]
        else:
            changed = np.abs(self.gray[i].astype(np.int16) - self.gray[j].astype(np.int16)) > 25
        changed = cv2.morphologyEx(changed.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) > 0
        rows, cols = np.nonzero(changed)
        if not len(rows):
            return None
        hs, ws = changed.shape
        return [round(cols.min() / ws, 4), round(rows.min() / hs, 4), round((cols.max() + 1) / ws, 4), round((rows.max() + 1) / hs, 4)]


def _flow(previous, gray, region, scale_px):
    """(動きの大きさの p95, 大きく動いた所の平均の向き [dx, dy], 大きく動いた所の囲み)。長さは scale_px で割る。"""
    winsize = max(15, int(round(15 * gray.shape[1] / 160)) | 1)     # 窓が小さいと平らな塗りの所で値が暴れる（合成素材で確かめた）
    flow = cv2.calcOpticalFlowFarneback(previous, gray, None, 0.5, 3, winsize, 3, 5, 1.2, 0)
    magnitude = np.hypot(flow[..., 0], flow[..., 1])
    values = magnitude[region] if region is not None else magnitude.ravel()
    if values.size < 16:
        return math.nan, [0.0, 0.0], None
    p90, p95 = np.percentile(values, [90, 95])
    strong = magnitude >= max(p90, 1e-3)
    if region is not None:
        strong &= region
    vector = [float(flow[..., 0][strong].mean()) / scale_px, float(flow[..., 1][strong].mean()) / scale_px] if strong.any() else [0.0, 0.0]
    rows, cols = np.nonzero(strong & (magnitude >= 0.5 * values.max()))
    height, width = gray.shape
    box = [cols.min() / width, rows.min() / height, (cols.max() + 1) / width, (rows.max() + 1) / height] if len(rows) else None
    return float(p95) / scale_px, vector, box


def extract(path, info, size, foreground, start, end, palettes=(), thresholds=None, progress=None):
    """[start, end) のコマを読んで Features を返す。palettes は reference.prepare の設定画（無ければ設定画の照合をしない）。"""
    width, height = size
    small = (max(8, width // SMALL), max(8, height // SMALL))
    rows = {name: [] for name in ("luma", "fdiff", "gray", "mask", "present", "area", "bbox", "centroid", "body", "components",
                                  "extra_box", "shape", "hist", "band_hist", "flow", "flow_vec", "flow_box", "ref")}
    flow_size = (max(16, width // 2), max(16, height // 2))
    previous_gray, previous_half, previous_half_mask = None, None, None
    if video.is_image(path):
        source = [(0, video.resize(video.load_rgb(path), size))]
    else:
        source = video.frames(path, size, info, start, end)
    for index, rgb in source:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        rows["luma"].append(float(gray.mean()) / 255.0)
        rows["fdiff"].append(math.nan if previous_gray is None else float(np.mean(np.abs(gray.astype(np.int16) - previous_gray))) / 255.0)
        rows["gray"].append(cv2.resize(gray, small, interpolation=cv2.INTER_AREA))
        raw = mask_of(rgb, foreground)
        person = silhouette(raw) if raw is not None else None
        present = person is not None and person.area >= MIN_PRESENT
        mask = person.mask if person is not None else (np.zeros((height, width), bool) if raw is not None else None)
        rows["mask"].append(None if mask is None else cv2.resize(mask.astype(np.uint8) * 255, small, interpolation=cv2.INTER_AREA) > 127)
        rows["present"].append(present)
        rows["area"].append(person.area if person else 0.0)
        rows["bbox"].append(person.bbox if present else [math.nan] * 4)
        rows["centroid"].append(person.centroid if present else [math.nan] * 2)
        rows["body"].append(person.bbox[3] - person.bbox[1] if present else math.nan)
        rows["components"].append(person.components if present else 0)
        rows["extra_box"].append(person.extra_box if present else None)
        rows["shape"].append(person.shape if present else np.zeros((SHAPE, SHAPE), bool))
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        if mask is None:
            inner = np.ones((height, width), bool)
            bands = [(int(round(a * height)), int(round(b * height))) for a, b in BANDS]
        elif present:
            inner = _interior(mask)
            bands = band_rows(person.bbox, height)
        else:
            inner, bands = None, None
        if inner is None:
            rows["hist"].append(None)
            rows["band_hist"].append([None, None, None])
        else:
            bins = np.full((height, width), -1, np.int32)
            bins[inner] = color_bins(hsv[inner])
            rows["hist"].append(histogram(bins[inner]))
            rows["band_hist"].append([histogram(bins[r0:r1][bins[r0:r1] >= 0]) for r0, r1 in bands])
        if palettes and mask is not None:
            rows["ref"].append(reference.frame_stats(rgb, inner if present else None, palettes, thresholds))
        half = cv2.resize(gray, flow_size, interpolation=cv2.INTER_AREA)          # 動きは解析の大きさの半分で測る（速さのため）
        half_mask = None if mask is None else cv2.resize(mask.astype(np.uint8), flow_size, interpolation=cv2.INTER_NEAREST) > 0
        if previous_gray is None:
            rows["flow"].append(math.nan)
            rows["flow_vec"].append([0.0, 0.0])
            rows["flow_box"].append(None)
        else:
            region = None
            if half_mask is not None:
                both = (half_mask | previous_half_mask).astype(np.uint8)
                region = cv2.dilate(both, np.ones((3, 3), np.uint8)) > 0 if both.any() else None
            scale_px = (rows["body"][-1] if present else 1.0) * flow_size[1]
            if region is None and half_mask is not None:
                rows["flow"].append(math.nan)
                rows["flow_vec"].append([0.0, 0.0])
                rows["flow_box"].append(None)
            else:
                value, vector, box = _flow(previous_half, half, region, max(scale_px, 4.0))
                rows["flow"].append(value)
                rows["flow_vec"].append(vector)
                rows["flow_box"].append(box)
        previous_gray, previous_half, previous_half_mask = gray, half, half_mask
        if progress:
            progress(index)
    if not rows["luma"]:
        return None
    has_mask = rows["mask"][0] is not None
    return Features(first=int(start), fps=float(info["fps"] or 0.0), size=(width, height), small=small, has_foreground=has_mask,
                    luma=np.array(rows["luma"]), fdiff=np.array(rows["fdiff"]), gray=np.stack(rows["gray"]),
                    masks=np.stack(rows["mask"]) if has_mask else None, present=np.array(rows["present"], bool),
                    area=np.array(rows["area"]), bbox=np.array(rows["bbox"], float), centroid=np.array(rows["centroid"], float),
                    body=np.array(rows["body"], float), components=np.array(rows["components"], int), extra_box=rows["extra_box"],
                    shape=np.stack(rows["shape"]), hist=rows["hist"], band_hist=rows["band_hist"], flow=np.array(rows["flow"], float),
                    flow_vec=np.array(rows["flow_vec"], float), flow_box=rows["flow_box"], ref=rows["ref"])
