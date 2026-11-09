"""素材（基準画像・深度・マスク）の読み込みと前処理。描画サイズごとに 1 回だけ行う。

- 基準画像: 出力の縦横比に cover で中央合わせし（余る側を切る）、描画サイズへ縮尺して線形 float32 に。
  camera.frame: source（fx-shot/0.3）なら切らずに元画像の全体を板にする（Assets.plate_size。出力より大きい元画像は、zoom 1 の
  視野が出力と同じ画素になるまで縮める）。マスクと深度も同じ大きさにそろえる。
  切った割合は warnings に残す（黙って切らない）。切り出し範囲は Assets.box（元画像の正規化座標）。
  spec の座標（light.position・radius・emitter.size・camera.center）は**元画像**の正規化座標で、
  to_screen() / width_scale() が box を使って出力画素へ写す（出力の縦横比が変わっても対象に付いてくる）。
- マスク: 輝度（RGBA で alpha が一定でなければ alpha）を 0..1 にし、feather_px でぼかす
  （σ = feather_px/2、出力幅 1920 換算）。基準画像と同じ縦横比（誤差 1% 以内）であること。
- 深度: 8/16bit/float/RGB を 2〜98 パーセンタイルで 0..1 に正規化（外れ値で潰れない）。
  結果は 1 = 手前、0 = 奥（near: white|black で向きを揃える）。ほぼ一定の深度は情報が無いので
  使わない（Assets.depth = None。深度視差も light の depth ゲートも無効になり、警告を残す）。
  fx-shot/0.2 の下ごしらえ（normalize: none・regions・dilate_px・blur_px）があれば、切り出しの前に
  resolution の画素で 正規化 → 向き → regions → 太らせ → ぼかし の順に掛ける（prepare_depth。実制作の 2.5D と同じ順・同じ演算）。
  下ごしらえが無ければ 0.1 と同じ経路（切り出して縮尺 → 正規化）で、値もビット単位で同じ。
"""
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..io import PipelineError, sha256
from . import spec as fxspec
from .color import to_linear

CROP_WARN_FRACTION = 0.005


@dataclass
class Assets:
    size: tuple                      # (W, H) 描画サイズ
    plate_u8: np.ndarray             # uint8 (H, W, 3) sRGB（inpaint 用に保持）
    plate: np.ndarray                # float32 (H, W, 3) 線形
    masks: dict                      # id -> float32 (H, W) 0..1（ぼかし済み）
    depth: np.ndarray | None         # float32 (H, W) 0..1（1 = 手前）
    warnings: list = field(default_factory=list)
    box: tuple = (0.0, 0.0, 1.0, 1.0)   # cover の切り出し (x0, y0, x1, y1)。元画像の正規化座標
    plate_size: tuple | None = None     # camera.frame: source の板の大きさ（元画像の全体）。cover なら None（板は描画サイズ）


def _open(path, role):
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"素材が見つかりません ({role}): {path.name}。spec のあるディレクトリからの相対パスを確認してください")
    try:
        image = Image.open(path)
        image.load()
        return image
    except Exception as error:  # Pillow は形式ごとに別の例外を投げる
        raise PipelineError(f"素材を画像として読めません ({role}: {path.name}): {error}") from error


def cover_box(src_w, src_h, out_w, out_h):
    """cover で中央合わせする切り出し (x0, y0, x1, y1) を元画像の正規化座標 0..1 で返す。"""
    target = out_w / out_h
    if src_w / src_h > target:  # 横が余る
        keep = (src_h * target) / src_w
        return ((1 - keep) / 2, 0.0, (1 + keep) / 2, 1.0)
    keep = (src_w / target) / src_h
    return (0.0, (1 - keep) / 2, 1.0, (1 + keep) / 2)


def _pixel_box(box, size):
    """box を基準画像の画素格子に丸める（実際に切り出す範囲と一致させ、座標変換の誤差を 1 画素未満にする）。"""
    width, height = size
    return (round(box[0] * width) / width, round(box[1] * height) / height,
            round(box[2] * width) / width, round(box[3] * height) / height)


def to_screen(box, u, v):
    """元画像の正規化座標 (u, v) → cover で切った後の画面の正規化座標（0..1 が画面の端）。"""
    return (u - box[0]) / (box[2] - box[0]), (v - box[1]) / (box[3] - box[1])


def width_scale(box):
    """元画像の幅 1.0 が、画面の幅の何倍か（= 1 / 切り出し幅）。半径・size など元画像の幅基準の長さに掛ける。"""
    return 1.0 / (box[2] - box[0])


def _crop_resize(arr, box, size):
    height, width = arr.shape[:2]
    x0, x1 = int(round(box[0] * width)), int(round(box[2] * width))
    y0, y1 = int(round(box[1] * height)), int(round(box[3] * height))
    arr = arr[y0:y1, x0:x1]
    out_w, out_h = size
    if arr.shape[1] == out_w and arr.shape[0] == out_h:
        return np.ascontiguousarray(arr)
    shrinking = arr.shape[1] > out_w or arr.shape[0] > out_h
    return cv2.resize(arr, (out_w, out_h), interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_CUBIC)


def _gray01(image):
    """マスク画像 → float32 0..1。RGBA は alpha が一定でなければ alpha、それ以外は輝度。"""
    if image.mode in ("RGBA", "LA", "PA"):
        alpha = np.asarray(image.convert("RGBA"))[..., 3]
        if alpha.min() != alpha.max():
            return alpha.astype(np.float32) / 255.0
    if image.mode.startswith("I"):
        return np.clip(np.asarray(image, dtype=np.float32) / 65535.0, 0.0, 1.0)
    if image.mode == "F":
        return np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    return np.asarray(image.convert("L")).astype(np.float32) / 255.0


def _raw_values(image):
    """深度画像 → float32（値域はそのまま。正規化は呼び出し側）。"""
    if image.mode.startswith("I") or image.mode == "F":
        return np.asarray(image, dtype=np.float32)
    return np.asarray(image.convert("L")).astype(np.float32)


def _check_aspect(role, image, base_size):
    bw, bh = base_size
    w, h = image.size
    if abs(w * bh - bw * h) > 0.01 * bw * h:
        raise PipelineError(f"{role} の縦横比が基準画像と違います（{w}x{h} と {bw}x{bh}）。同じ縦横比で作り直してください")


def normalize_depth(values, near):
    """深度を 2〜98 パーセンタイルで 0..1 にし、1 = 手前に揃える。平坦なら (0.5 一定, False) を返す。"""
    lo, hi = np.percentile(values, [2, 98])
    if hi - lo < 1e-6:
        return np.full(values.shape, 0.5, dtype=np.float32), False
    out = np.clip((values - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)
    return (out if near == "white" else 1.0 - out).astype(np.float32), True


def _plain_depth(depth):
    """下ごしらえの無い深度か（0.1 と同じ経路で読む）。"""
    return depth["normalize"] == "percentile" and not depth["regions"] and depth["dilate_px"] == 0 and depth["blur_px"] == 0


def _full_scale(image, values):
    """normalize: none で割る値。16 bit（I モード）は 65535、8 bit は 255、float はそのまま（1）。"""
    if image.mode == "F":
        return 1.0
    if image.mode.startswith("I"):
        if float(values.max()) > 65535:
            raise PipelineError("深度画像の値が 16 bit（65535）を超えています。normalize: none は 8 / 16 bit か 0..1 の float の画像で使ってください")
        return 65535.0
    return 255.0


def _int_points(points):
    """多角形の頂点を cv2.fillConvexPoly の整数座標にする（実制作と同じ int32 への切り捨て）。"""
    return points.astype(np.int32)


def region_quad(region, size):
    """regions の 1 つ（正規化済み）の四隅を、size = (w, h) の画素の float32 にする。"""
    width, height = size
    if "box" in region:
        x0, y0, x1, y1 = region["box"]
        points = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    else:
        points = region["quad"]
    return np.array([[x * width, y * height] for x, y in points], dtype=np.float32)


def apply_region(values, quad, mode="plane", value=None, inner=0.92, grow=1.03, blur_px=4.0):
    """quad（画素の四隅）の中の深度を、最小二乗の平面（mode plane）か value に置きかえる。縁は blur_px の σ でなじませる。

    plane: 重心へ inner 倍に縮めた四角形の中の深度に z = a·x + b·y + c を当て、grow 倍に広げた四角形へ書く（実制作の flatten_quad と同じ演算）。
    """
    height, width = values.shape
    center = quad.mean(axis=0)
    if mode == "plane":
        inside = np.zeros((height, width), np.uint8)
        cv2.fillConvexPoly(inside, _int_points(center + (quad - center) * inner), 1)
        ys, xs = np.nonzero(inside)
        if len(xs) < 3:
            raise PipelineError(f"assets.depth.regions: 平面を当てる画素が {len(xs)} 個しかありません（四隅が画像の外か、小さすぎます）")
        design = np.stack([xs, ys, np.ones_like(xs)], 1).astype(np.float64)
        coef, *_ = np.linalg.lstsq(design, values[ys, xs].astype(np.float64), rcond=None)
        yy, xx = np.mgrid[0:height, 0:width]
        target = (coef[0] * xx + coef[1] * yy + coef[2]).astype(np.float32)
    else:
        target = float(value)
    weight = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(weight, _int_points(center + (quad - center) * grow), 255)
    weight = weight.astype(np.float32) / 255.0
    if blur_px > 0:
        weight = cv2.GaussianBlur(weight, (0, 0), blur_px)
    return values * (1 - weight) + target * weight


def prepare_depth(image, depth, base_size, box):
    """0.2 の下ごしらえをした深度（resolution の画素のまま、切り出す前）と、情報があるか。

    順番: 割る（normalize: none）→ 基準画像の大きさへ（resolution: base）→ 伸ばす（normalize: percentile。
    統計は cover で切る範囲の中で取る）→ 向き（near）→ regions → 太らせ（dilate_px）→ ぼかし（blur_px）。
    情報があるかは、下ごしらえの後の cover で切る範囲で見る（一定の深度に regions の value で書いた差を捨てない）。
    percentile は、元の深度に幅が無いと伸ばせないので、そこで情報なしにする。
    """
    values = _raw_values(image)
    if depth["normalize"] == "none":
        values = values / _full_scale(image, values)
    if depth["resolution"] == "base" and (values.shape[1], values.shape[0]) != tuple(base_size):
        values = cv2.resize(values, tuple(base_size), interpolation=cv2.INTER_LINEAR)
    height, width = values.shape
    rows = slice(int(round(box[1] * height)), int(round(box[3] * height)))
    cols = slice(int(round(box[0] * width)), int(round(box[2] * width)))
    if depth["normalize"] == "percentile":
        lo, hi = np.percentile(values[rows, cols], [2, 98])
        if hi - lo < 1e-6:
            return None, False
        values = np.clip((values - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)
    elif float(values.min()) < 0.0 or float(values.max()) > 1.0:
        raise PipelineError(f"深度画像の値が 0..1 の外にあります（{float(values.min()):.3g}〜{float(values.max()):.3g}）。"
                            f"normalize: none は 0..1 の float か 8 / 16 bit の画像で使い、それ以外は percentile にしてください")
    if depth["near"] == "black":
        values = (1.0 - values).astype(np.float32)
    for region in depth["regions"]:
        values = apply_region(values, region_quad(region, (width, height)), region["mode"], region["value"],
                              region["inner"], region["grow"], region["blur_px"])
    if depth["dilate_px"]:
        values = cv2.dilate(values, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (depth["dilate_px"], depth["dilate_px"])))
    if depth["blur_px"] > 0:
        values = cv2.GaussianBlur(values, (0, 0), depth["blur_px"])
    values = np.ascontiguousarray(values, dtype=np.float32)
    seen = values[rows, cols]
    if float(seen.max()) - float(seen.min()) < 1e-6:
        return None, False
    return values, True


def resolve_pivot(pivot, assets):
    """camera.pivot（正規化済み）を数にして (値, 出どころ) を返す。出どころは spec / mask:<id> / box。

    マスクと箱は、描画サイズの深度（下ごしらえ・切り出しの後）の中央値。マスクは 0.5 より大きい画素、箱は元画像の正規化座標。
    深度が無ければ深度視差は無効なので既定の 0.5 を返す（spec の警告が残る）。
    """
    if not isinstance(pivot, dict):
        return float(pivot), "spec"
    if assets.depth is None:
        return 0.5, "spec"
    if "mask" in pivot:
        inside = assets.masks[pivot["mask"]] > 0.5
        if not inside.any():
            raise PipelineError(f"camera.pivot の mask {pivot['mask']!r} が画面の中でほぼ空です（0.5 を超える画素がありません）")
        return float(np.median(assets.depth[inside])), f"mask:{pivot['mask']}"
    height, width = assets.depth.shape
    u0, v0 = to_screen(assets.box, pivot["box"][0], pivot["box"][1])
    u1, v1 = to_screen(assets.box, pivot["box"][2], pivot["box"][3])
    x0, x1 = max(0, int(round(u0 * width))), min(width, int(round(u1 * width)))
    y0, y1 = max(0, int(round(v0 * height))), min(height, int(round(v1 * height)))
    if x1 <= x0 or y1 <= y0:
        raise PipelineError(f"camera.pivot の box {pivot['box']} が画面（cover で切った範囲）の外です")
    return float(np.median(assets.depth[y0:y1, x0:x1])), "box"


def load_assets(spec, size):
    """spec と描画サイズ (W, H) から Assets を作る。"""
    width, height = size
    kpx = width / 1920.0
    data = spec.data
    warnings = []
    base_image = _open(spec.resolve(data["assets"]["base"]), "base")
    base_size = base_image.size
    plate_size = None
    if data["camera"]["frame"] == "source":
        # 元画像の全体が板。出力より大きい元画像は、zoom 1 の視野が出力と同じ画素になるまで縮める（縮める warp で荒れないように）
        box = (0.0, 0.0, 1.0, 1.0)
        cover = max(width / base_size[0], height / base_size[1])
        plate_size = tuple(base_size) if cover >= 1 else (max(1, int(round(base_size[0] * cover))), max(1, int(round(base_size[1] * cover))))
        kpx = kpx / max(width / plate_size[0], height / plate_size[1])        # 出力幅 1920 換算 px → 板の画素
        if cover > 1:
            warnings.append(f"基準画像 {base_size[0]}x{base_size[1]} は、zoom 1 の視野（出力 {width}x{height} の縦横比）でも"
                            f"出力より小さく、{cover:.2f} 倍に拡大されます")
    else:
        box = _pixel_box(cover_box(base_size[0], base_size[1], width, height), base_size)
        cut = 1.0 - (box[2] - box[0]) * (box[3] - box[1])
        if cut > CROP_WARN_FRACTION:
            axis = "左右" if box[0] > 0 else "上下"
            warnings.append(f"基準画像 {base_size[0]}x{base_size[1]} は出力 {width}x{height} と縦横比が違うため、"
                            f"cover で中央に合わせて{axis}を計 {cut * 100:.1f}% 切りました")
        if base_size[0] < width or base_size[1] < height:
            warnings.append(f"基準画像 {base_size[0]}x{base_size[1]} は描画サイズ {width}x{height} より小さく、拡大されます")
    target = plate_size or size
    rgb = np.asarray(base_image.convert("RGB"))
    plate_u8 = _crop_resize(rgb, box, target)
    masks = {}
    for mask_id, mask in data["assets"]["masks"].items():
        image = _open(spec.resolve(mask["path"]), f"mask:{mask_id}")
        _check_aspect(f"mask:{mask_id}", image, base_size)
        values = np.clip(_crop_resize(_gray01(image), box, target), 0.0, 1.0)
        sigma = mask["feather_px"] * kpx / 2.0
        if sigma >= 0.3:
            values = cv2.GaussianBlur(values, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
        masks[mask_id] = np.ascontiguousarray(values, dtype=np.float32)
    depth = None
    depth_spec = data["assets"]["depth"]
    if depth_spec:
        image = _open(spec.resolve(depth_spec["path"]), "depth")
        _check_aspect("depth", image, base_size)
        if _plain_depth(depth_spec):
            values = _crop_resize(_raw_values(image), box, target)
            depth, informative = normalize_depth(values, depth_spec["near"])
        else:
            values, informative = prepare_depth(image, depth_spec, base_size, box)
            # 描画より小さい深度は 3 次補間で拡大するので、段差で 0..1 を行き過ぎる。0.1 の経路（normalize_depth）と同じく切る
            depth = np.ascontiguousarray(np.clip(_crop_resize(values, box, target), 0.0, 1.0), dtype=np.float32) if informative else None
        if not informative:
            depth = None  # 警告と描画を一致させる: 深度視差も light の depth ゲートも無効にする
            warnings.append("深度画像がほぼ一定のため、深度は使いません（深度視差と light の depth 指定も無効）")
    return Assets(size=size, plate_u8=plate_u8, plate=to_linear(plate_u8), masks=masks, depth=depth, warnings=warnings,
                  box=box, plate_size=plate_size)


def describe(spec):
    """manifest 用: 全素材の {役割: {path, sha256, width, height}}（音声は寸法なし）。存在しなければ PipelineError。"""
    out = {}
    for role, rel in fxspec.asset_refs(spec.data).items():
        path = spec.resolve(rel)
        if not path.is_file():
            raise PipelineError(f"素材が見つかりません ({role}): {rel}")
        entry = {"path": rel, "sha256": sha256(path)}
        if role != "audio":
            with Image.open(path) as image:
                entry["width"], entry["height"] = image.size
        out[role] = entry
    return out
