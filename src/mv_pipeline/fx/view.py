"""hooks に渡すショットの見え方（実制作の演出の部品の Geo に当たる）。素材の座標 → 画面の画素。

View はショットのその時刻の camera（parallax 1 の層。深度視差は入れない）で写す。座標は画面の画素で、画素の中心が整数
（cv2 の描画と同じ）。素材の座標は 2 通りで書ける:

- 正規化座標（0..1）: view.point(u, v)。view.scale は素材の幅 1 が画面の何画素か。view.px は素材の幅を 1920 とした
  1 画素が画面の何画素か（決まった画素の既定値を持つ部品（火の粉の速さ・大きさなど）に渡す倍率。fx の「1920 幅換算」と同じ考え）。
- 基準座標（実制作の素材の座標系。例えば 1672×941）: geo = view.base(1672, 941) で geo.pt(x, y)・geo.pts(P)・geo.k（基準の 1 が
  画面の何画素か）。実制作の演出の設定（位置と長さが基準座標の画素）をそのまま写せる。

点・倍率は Python の float（実制作の Geo と同じ型。numpy の float64 の数だと、numpy 2 では float32 の画の計算が float64 に上がる）。
view.depth() は spec の深度（assets.depth。fx の前処理の後で 1 = 手前）を同じ camera で画面へ写したもの（深度が無ければ None）。

    def shot(img, t, view):                      # fx-timeline の hooks
        geo = view.base(1672, 941)
        X, Y = geo.pt(822, 668)
        fire.candle(img, X, Y, t, 40 * geo.k, seed=5)
        return img
"""
import numpy as np

from .assets import to_screen

NOMINAL_WIDTH = 1920.0      # 正規化座標で書くときに、素材の幅を何画素とみなすか（view.px。決まった画素の既定値の基準）


class View:
    """ショット index の、ショットの中の時刻 t の見え方。kind は spec か video、name は spec か動画のパス。
    depth は板の大きさの深度（spec の assets.depth。無ければ None）。"""

    def __init__(self, path, box, plate, size, t, index=0, kind="spec", name="", depth=None):
        self.path, self.box, self.size, self.t = path, tuple(box), tuple(size), t
        self.plate = tuple(plate) if plate is not None else tuple(size)
        self.index, self.kind, self.name = index, kind, name
        self.matrix = path.matrix(t, 1.0).astype(np.float64)        # 板の画素 → 画面の画素
        self.scale = float(self.matrix[0, 0] * self.plate[0] / (self.box[2] - self.box[0]))
        self.px = self.scale / NOMINAL_WIDTH                        # 素材の幅を 1920 とした 1 画素 → 画面の画素
        self._depth, self._screen_depth = depth, None

    def point(self, u, v):
        """素材の正規化座標 (u, v) → 画面の画素の座標 (X, Y)（Python の float）。"""
        pu, pv = to_screen(self.box, u, v)
        return (float(self.matrix[0, 0] * (pu * self.plate[0] - 0.5) + self.matrix[0, 2]),
                float(self.matrix[1, 1] * (pv * self.plate[1] - 0.5) + self.matrix[1, 2]))

    def depth(self):
        """画面の深度（出力の大きさの float32、1 = 手前・0 = 奥。端の画素を伸ばす）。spec に深度が無ければ None。"""
        if self._depth is None:
            return None
        if self._screen_depth is None:
            from . import camera
            depth = np.asarray(self._depth, np.float32)
            out = camera.warp(depth, self.path.matrix(self.t, 1.0), self.size)
            self._screen_depth = out.copy() if out is depth else out      # 恒等の写しでも素材の深度そのものは渡さない
        return self._screen_depth

    def points(self, uv):
        return np.array([self.point(u, v) for u, v in uv], np.float32)

    def base(self, width, height):
        """基準座標（width×height）で書いた位置と長さを写す道具。"""
        return BaseView(self, width, height)

    def blade(self, points, w0, w1, base=None):
        """刃（根元 → 切っ先の点列と、根元・切っ先の幅）を画面の fire.Blade にする。base = (幅, 高さ) なら点・幅・炎の高さは
        基準座標の画素、無ければ点は正規化座標で幅と高さは素材の幅の割合。火の粉の速さ・大きさ（Blade.px）は、基準座標の刃なら
        基準座標の画素（実制作と同じ）、正規化座標の刃なら素材の幅を 1920 とした画素（view.px）で数える。"""
        from .fire import Blade
        if base is not None:
            geo = self.base(*base)
            screen_points, k, px = geo.pts(points), geo.k, geo.k
        else:
            screen_points, k, px = self.points(points), self.scale, self.px
        s = np.linspace(0, 1, len(screen_points))
        return Blade(points=screen_points, s=s, widths=(w0 + (w1 - w0) * s) * k, k=k, px=px)

    def warp(self, array):
        """素材の座標の配列（元画像の全体。大きさは問わない）を、camera で画面へ写した float32（端の画素を伸ばす）。"""
        from . import camera
        from .assets import _crop_resize
        plate = _crop_resize(np.asarray(array, np.float32), self.box, self.plate)
        return camera.warp(plate, self.path.matrix(self.t, 1.0), self.size)


class BaseView:
    """基準座標（実制作の素材の座標系）の位置と長さを画面へ写す。pt・pts・k は実制作の Geo と同じ使い方。"""

    def __init__(self, view, width, height):
        self.view, self.width, self.height = view, float(width), float(height)
        self.k = view.scale / self.width            # 基準座標の 1 が画面の何画素か

    def pt(self, x, y):
        return self.view.point(x / self.width, y / self.height)

    def pts(self, points):
        return np.array([self.pt(x, y) for x, y in points], np.float32)
