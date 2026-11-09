"""静止画の固定の手直し（窓灯を消す・暗くする・赤の彩度を落とす・描かれた小さな火の粉を消す）。実験的（docs/fx.md）。

描画の前に、元画像に 1 回だけ掛ける（実制作は素材を読み込むときに掛ける）。手直しした画像を別の名前で書き出し、spec の
assets.base にする。実制作の演出の部品と同じ式・同じ順で書いてあり、同じ入力なら同じ画になる。

    from mv_pipeline.fx import retouch
    out = retouch.static_edit(rgb, [{"op": "desat", "poly": [[0, 0], [400, 0], [400, 300]], "amt": 0.6}], base=(1672, 941))

ops の各要素（poly は区域の多角形。基準座標 base の画素で書き、画像の幅 / base の幅 倍して画像に合わせる）:

    erase    区域の中の暖色の明るい所（r > b + warm かつ輝度 > lum。窓灯など）を、grow 回太らせて周りの色で埋める（inpaint）
    dim      同じ所を周りの色へ寄せて暗くする（factor は元の色を残す割合）
    specks   区域の中の小さな暖色の点（面積 max_area 以下。描かれた火の粉）を周りの色で埋める
    desat    区域（ぼかした多角形）の中の赤の彩度を amt だけ落とし、明るさを amt·darken だけ落とす
"""
import cv2
import numpy as np

from ..io import PipelineError

OPS = ("erase", "dim", "specks", "desat")


def poly_mask(shape, poly, k):
    """多角形 poly（基準座標）を k 倍して塗った uint8 のマスク（0 か 255。大きさは shape の縦横）。"""
    m = np.zeros(shape[:2], np.uint8)
    q = (np.array(poly, np.float32) * k).astype(np.int32)
    cv2.fillPoly(m, [q], 255)
    return m


def static_edit(im, ops, base=(1672, 941)):
    """im（uint8 RGB）に ops を順に掛けた画（uint8 RGB。im は書き換えない）。"""
    for number, e in enumerate(ops):
        if e.get("op") not in OPS:
            raise PipelineError(f"fx retouch: ops[{number}] の op は {', '.join(OPS)} のどれかにしてください: {e.get('op')!r}")
    k = im.shape[1] / float(base[0])
    out = im.copy()
    for e in ops:
        op = e["op"]
        if op in ("erase", "dim", "specks"):
            reg = poly_mask(im.shape, e["poly"], k) > 0
            f = out.astype(np.float32)
            r, b = f[..., 0], f[..., 2]
            lum = f.mean(-1)
            warm = (r > b + e.get("warm", 25)) & (lum > e.get("lum", 70)) & reg
            m = warm.astype(np.uint8) * 255
            if op == "specks":
                n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
                keep = np.zeros_like(m)
                for i in range(1, n):
                    if st[i, cv2.CC_STAT_AREA] <= e.get("max_area", 120) * k * k:
                        keep[lab == i] = 255
                m = keep
            m = cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=int(e.get("grow", 2)))
            if op == "dim":
                a = cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), 1.5)[..., None]
                fac = e.get("factor", 0.15)
                tgt = cv2.inpaint(out, m, 3, cv2.INPAINT_TELEA).astype(np.float32)
                mix = tgt * (1 - fac) + out.astype(np.float32) * fac
                out = np.clip(out * (1 - a) + mix * a, 0, 255).astype(np.uint8)
            else:
                out = cv2.inpaint(out, m, 4, cv2.INPAINT_TELEA)
        else:
            reg = cv2.GaussianBlur(poly_mask(im.shape, e["poly"], k).astype(np.float32) / 255.0, (0, 0), 6 * k)
            hsv = cv2.cvtColor(out, cv2.COLOR_RGB2HSV).astype(np.float32)
            red = ((hsv[..., 0] < 12) | (hsv[..., 0] > 165)) & (hsv[..., 1] > 60)
            a = reg * red * e.get("amt", 0.6)
            hsv[..., 1] *= (1 - a)
            hsv[..., 2] *= (1 - a * e.get("darken", 0.25))
            out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2RGB)
    return out
