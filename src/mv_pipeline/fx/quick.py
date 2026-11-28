"""`mv fx quick IMAGE OUTDIR`（実験的）: 一枚絵から、そのまま描ける fx の一式を既存の部品で組む。

書くもの（OUTDIR）: 元画像の写し（image.<拡張子>）・ショットの spec（shot.json。fx-shot/0.3）・timeline（timeline.json。
fx-timeline/0.2）・hooks（hooks.py。fx.impact・accent の部品）・plan.md（変えるとよい所と次のコマンド）・記録 quick.json と
作業記録の下書き log.md（実行のたびに足す）。`--preview` で半分の大きさのコマの一覧 preview.jpg を 1 枚、`--render` で本番の
動画（`mv fx timeline render` と同じ run）・確認の画像と要約（check.jpg・check.json。quick_check）・検品の設定 inspect.json を足す。

既定の構成（点の無いものは飛ばす）:

    寄り（--focus）→ 線に沿って移る（--path）→ 山場（--impact: 閃光・揺れ・火花・衝撃の輪）→ 全景へ連続で引く → 暗転

--impact が無ければ静かな型（閃光・輪・火花・揺れは使わず、寄りの点にやわらかい光が差して薄れる）。カメラのキーは
camera.curve: smooth（速さが続く曲線）でつなぎ、寄り・移動・引きに緩急を付ける（段の境で止まらず、向きも急に返さない）。
山場の後の引きは、頭の 1 秒ほどで一気に引き、あとはゆっくり全景へ。寄りの点のまわり（顔ほど。--focus-radius）が
画面の端で切れたまま続く秒を段ごとに数え（edge_times）、EDGE_LIMIT_S（0.4 秒）を超える段があれば、線の間の zoom・移る速さ・
画面の取り方を順に変えた形を試し、収まらなければ warnings と plan.md に直し方を書く。

守ること:
- カメラは camera.frame: source・clamp・plate: full（元画像の全体が板で、寄っても元画像の画素から引く）。
- 寄りは元画像の画素を MAX_MAGNIFICATION（1.5）倍まで。超える --zoom はそこで止めて warnings に書く。
- 同じ絵の違う寄りをクロスフェードでつながない（1 本の spec の、途切れないカメラで動かす。つなぎは頭の black だけ）。
- 明滅は山場の閃光の 1 回だけ（1 秒に 3 回以下）。
- マスクも深度も使わない。カメラのキーは、どれも視野が画像の中に収まる所に置く（寄りの段のキーは端から KEY_MARGIN 内側。
  キーの間で曲線が外へ出る所は camera の clamp が止める）。

引数は前の呼び出しの値を引き継ぐ（quick.json の args）。書いた引数だけを置き換え、点は "none" で外す。
手で直したファイルは守る: 引数が前と同じ（か点や大きさの指定なし）なら今のファイルをそのまま使う（--preview・--render だけ
やり直せる）。違う引数で作り直すときに手で直したファイルがあれば止まる（--force で上書き）。
"""
import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np

from ..io import PipelineError, atomic_image, atomic_json, read_json, sha256

RECORD = "quick.json"
RECORD_SCHEMA = "fx-quick/0.1"
SPEC, TIMELINE, HOOKS, PLAN, INSPECT, PREVIEW = "shot.json", "timeline.json", "hooks.py", "plan.md", "inspect.json", "preview.jpg"
MAX_MAGNIFICATION = 1.5         # 寄りで元画像の 1 画素を画面の何画素まで引き伸ばすか
DEFAULT_ZOOM = 2.2              # 寄りの zoom の既定（1 = 全景）。上の上限で止める
MOVE_SHARE = 0.82               # 線に沿って動く間の zoom（寄りから全景へ戻す割合の残り。1 なら寄りのまま）
PUSH_FROM, PUSH_S = 0.78, 3.0   # 寄りの押し込み: PUSH_S 秒あたり zoom をこの割合から寄りの zoom へ（寄りが長ければ頭を引く。押し込みが
                                # 遅いと、平らな絵で `mv inspect` の「止まったコマ」に出る）
FULL_IN_FADE = 0.4              # 全景に着く秒を、終わりから暗転の長さのこの割合だけ前に置く（暗転の途中）
FOCUS_RADIUS = 0.075            # 寄りの点のまわり（顔ほど）の既定の半径（元画像の短い辺に対する割合。--focus-radius）
EDGE_LIMIT_S = 0.4              # 寄りの点のまわりが画面の端で切れたまま続いてよい長さ（秒。超えたら warnings と plan.md に書く）
CUT_SHARE = 0.1                 # 「端で切れている」は、まわりの箱の見える面積がこの割合より多く、横か縦でこの割合より多く外にあるとき
RECOIL_S = 1.0                  # 山場の後の引きの頭: この秒で RECOIL_SHARES のどれかの割合だけ一気に引く（反動）
RECOIL_SHARES = (0.7, 0.8, 0.9)  # 頭で引く割合（zoom は対数で。残りはゆっくり全景へ）。最初が既定
TAIL_KEYS = 4                   # 引きのゆっくりの所を、画像の端に沿わせるときに分ける数
PULL_OK = 0.25                  # 引きの形は、寄りの点のまわりが端で切れるのがこの秒までなら前の形（既定の形）のまま
# カメラは camera.curve: smooth（キーを通って速さが続く曲線）。止まらず、向きも急には返さない:
DRIFT_SPEED = 0.02              # 寄りの間、中心を次の行き先の向きへ流す速さ（画面の対角線の割合/秒。寄りきった所で zoom が止まっても
                                # 動き続けるように。遅いと `mv inspect` の「止まったコマ」に出る）
OPEN_ZOOM = 0.5                 # 頭の zoom の下限: 1 + この割合 ×（寄りの zoom − 1）（寄りが弱い絵でも全景から始めない）
CLOSE_LEAD = 0.985              # 寄りの段の終わりの zoom（寄りの zoom に対する割合）。zoom の山は少し後（PEAK_LAG）に置き、
PEAK_LAG = 0.8                  # 中心が向きを変える所と zoom の山を重ねない（重なるとそこでカメラが止まる）
KEY_MARGIN = 0.012              # 寄りの段の終わりのキーを、視野が画像の端に付く所からこの分（元画像の幅・高さの割合）内側に置く
MOVE_DROP = 0.97                # 線に沿って動く間の zoom の上限（寄りの zoom に対する割合。寄りから少しずつ引き続けて、止まらない）
SETTLE_S, SETTLE_SHARE = 0.4, 0.03   # 山場（か線の終わり）に着いた後も着いた向きへ、この秒で視野の対角線のこの割合だけ流してから引く
SETTLE_ZOOM = 0.97              # その間の zoom の割合（引き続ける）
SHARP_TURN = 100.0              # この角度（度）より鋭く折れ、
SHORT_LEG = 0.25                # 前後の区間の短い方が道のり全体のこの割合より短い点は、カメラの道から落とす
PHASE_NAMES = {"close": "寄り", "move": "移動", "hit": "山場", "pull": "引き"}
# 既定の形で端で切れる時間が長いときに順に試す形（線の間の zoom の割合, 線と山場の画面の取り方, 移る速さ）。最初が既定。
# 絵の変わり方の小さい順: 線の間の zoom → 移る速さ（0.7 は山場の秒を変えずに移る段を 0.7 倍に縮める。寄りが長くなる）→
# 画面の取り方（顔を全部入れる・離して外す。線の点を画面に残したまま中心を寄せるので、線の端を通らないこともある）
LAYOUT_TRIES = tuple((share, framing, pace) for framing in ("near", "include", "exclude") for pace in (1.0, 0.7)
                     for share in (MOVE_SHARE, 1.0, 0.5))
FRAMING_NAMES = {"near": "近い方（入れるか外す）", "include": "顔を全部入れる形", "exclude": "顔を離して外す形"}
EDGE_HINTS = {
    "close": "--focus を絵の内側へ寄せるか、--zoom を下げてください。",
    "move": "--impact（か --path の終わり）を寄りの点から離して顔を早く外すか、近づけて顔を画面に残してください（--impact-at を早めると速く動きます）。",
    "hit": "--impact を動かして、山場の画面に顔が全部入るか全部外れるようにしてください。",
    "pull": "--impact（か --path の終わり）を寄りの点に近づけるか、--zoom を下げてください。",
}
DEFAULT_SECONDS, DEFAULT_FPS = 12.0, 24
OPEN_S = 0.5                    # 頭の黒から上げる秒
FLASH_WINDOW_S = 0.30           # 閃光の窓（impact.flash の既定）
SHAKE_S = 0.5                   # 揺れの長さ（impact.shake の既定）
END_SKIP = 0.2                  # 暗転の終わりのこの秒は、止まったコマを数えない（黒に近い同じ絵が続くのは演出どおり）
SAME_POINT = 0.015              # この距離（元画像の長い辺に対する割合）より近い点は 1 つにまとめる
# 閃光の強さ: 山場の画面の平均の明るさ（0..255）が LUMA_DARK 以下なら FLASH_DARK、LUMA_BRIGHT 以上なら FLASH_BRIGHT（間は直線）。
# 閃光は画を白へ寄せるので、山の所では画の濃淡が (1 − 強さ) 倍になる。明るい絵は明るさがあまり増えずに濃淡だけが減るので弱くする
FLASH_DARK, FLASH_BRIGHT = 0.65, 0.3
LUMA_DARK, LUMA_BRIGHT = 60.0, 180.0
GLINT_FACE = 150.0              # impact.glint の既定の大きさが合う、寄りの点のまわりの半径（素材の幅を 1920 とした画素）
EMBERS_PER_SIDE = 64            # 任意の火の粉の数: 全景で画面に見える絵の下の端の長さが、画面の短い辺 1 つ分あたりの数
QUIET_LIGHT, QUIET_LIGHT_R = 0.12, 2.2   # 静かな型の光の強さと半径（寄りの点のまわりの半径の倍）
EXTENSIONS = {".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpg", ".webp": ".webp"}


# ---- 引数 -------------------------------------------------------------------------------------

CLEAR = "none"                  # 点の引数に書くと、前の呼び出しから引き継いだ点を外す


def parse_point(text, option):
    """"x,y"（元画像の正規化座標 0..1）→ (x, y)。"none" なら CLEAR（引き継いだ点を外す）。"""
    if text is None:
        return None
    if str(text).strip().lower() == CLEAR:
        return CLEAR
    try:
        x, y = (float(v) for v in str(text).split(","))
    except ValueError:
        raise PipelineError(f"{option} は x,y（元画像の正規化座標 0..1。例 0.48,0.30）で書いてください: {text!r}") from None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise PipelineError(f"{option} の x,y は 0..1 にしてください（元画像の幅と高さに対する割合）: {text!r}")
    return (x, y)


def parse_path(text):
    """"x0,y0:x1,y1" → ((x0, y0), (x1, y1))。"none" なら CLEAR。"""
    if text is None:
        return None
    if str(text).strip().lower() == CLEAR:
        return CLEAR
    parts = str(text).split(":")
    if len(parts) != 2:
        raise PipelineError(f"--path は x0,y0:x1,y1（始まりと終わりの点）で書いてください: {text!r}")
    return parse_point(parts[0], "--path"), parse_point(parts[1], "--path")


def parse_size(text):
    """"WxH" → (W, H)。fx の出力は偶数の寸法。"""
    if text is None:
        return None
    try:
        width, height = (int(v) for v in str(text).lower().split("x"))
    except ValueError:
        raise PipelineError(f"--size は 幅x高さ（例 1080x1920）で書いてください: {text!r}") from None
    if width < 64 or height < 64 or width % 2 or height % 2:
        raise PipelineError(f"--size の幅と高さは 64 以上の偶数にしてください: {text!r}")
    return (width, height)


def default_size(image_size):
    """縦の絵なら 1080x1920、横（と正方形）の絵なら 1920x1080。"""
    return (1080, 1920) if image_size[1] > image_size[0] else (1920, 1080)


# ---- 構成 -------------------------------------------------------------------------------------

def _floor4(value):
    """小数 4 桁で切り捨てる（1.9 のような値は 1.9 のまま）。"""
    return math.floor(value * 1e4 + 1e-6) / 1e4


def _distance(a, b, image_size):
    """元画像の画素での距離。"""
    return math.hypot((a[0] - b[0]) * image_size[0], (a[1] - b[1]) * image_size[1])


def _half_view(zoom, cover, size, image_size):
    """zoom の視野の半分の幅と高さ（元画像の正規化座標。frame: source・plate: full）。"""
    scale = cover * zoom
    return size[0] / (2.0 * scale * image_size[0]), size[1] / (2.0 * scale * image_size[1])


def _clamp_center(center, zoom, cover, size, image_size, margin=1e-4):
    """視野（frame: source・plate: full の zoom の視野）が画像の中に収まるように center を寄せる（camera.clamp と同じ式）。
    margin だけ内側に止めるので、キーを小数 4 桁に丸めても外へ出ない。"""
    half_w, half_h = _half_view(zoom, cover, size, image_size)
    clamp = lambda v, half: 0.5 if half >= 0.5 - margin else min(max(v, half + margin), 1.0 - half - margin)  # noqa: E731
    return (clamp(center[0], half_w), clamp(center[1], half_h))


def _movable(zoom, cover, size, image_size, margin=1e-4):
    """zoom でカメラの中心が動ける範囲 (x0, y0, x1, y1)（視野が画像に収まる範囲。幅 0 の向きもある）。"""
    half_w, half_h = _half_view(zoom, cover, size, image_size)
    x0, x1 = (0.5, 0.5) if half_w >= 0.5 - margin else (half_w + margin, 1.0 - half_w - margin)
    y0, y1 = (0.5, 0.5) if half_h >= 0.5 - margin else (half_h + margin, 1.0 - half_h - margin)
    return x0, y0, x1, y1


def _clip(p0, p1, box):
    """線分 p0→p1 のうち box (x0, y0, x1, y1) の中の部分（Liang–Barsky）。中を通らなければ None。"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, p0[0] - box[0]), (dx, box[2] - p0[0]), (-dy, p0[1] - box[1]), (dy, box[3] - p0[1])):
        if abs(p) < 1e-12:
            if q < -1e-12:
                return None
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1 + 1e-12:
            return None
    return (p0[0] + dx * t0, p0[1] + dy * t0), (p0[0] + dx * t1, p0[1] + dy * t1)


def _turn(a, b, c, image_size):
    """点 b での曲がりの角度（度。0 = まっすぐ、180 = 折り返し）。元画像の画素で測る。"""
    u = ((b[0] - a[0]) * image_size[0], (b[1] - a[1]) * image_size[1])
    v = ((c[0] - b[0]) * image_size[0], (c[1] - b[1]) * image_size[1])
    nu, nv = math.hypot(*u), math.hypot(*v)
    if nu < 1e-9 or nv < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv)))))


def focus_box(center, radius, image_size, inset=0.002):
    """寄りの点のまわりの箱 [x0, y0, x1, y1]（元画像の正規化座標。半径は短い辺に対する割合）。画像の外は切り、さらに inset だけ
    内側で切る（カメラは画像の外を写さないので、画像の端にかかる顔は、視野が画像の端まで来ていれば全部見えている）。"""
    r = radius * min(image_size)
    rx, ry = r / image_size[0], r / image_size[1]
    return [max(inset, center[0] - rx), max(inset, center[1] - ry), min(1.0 - inset, center[0] + rx), min(1.0 - inset, center[1] + ry)]


def _view_of(center, zoom, cover, size, image_size):
    half = _half_view(zoom, cover, size, image_size)
    return (center[0] - half[0], center[1] - half[1], center[0] + half[0], center[1] + half[1])


def _contains(view, box, margin=0.0):
    return box[0] >= view[0] + margin and box[2] <= view[2] - margin and box[1] >= view[1] + margin and box[3] <= view[3] - margin


def _apart(view, box):
    return box[2] <= view[0] or box[0] >= view[2] or box[3] <= view[1] or box[1] >= view[3]


def _frame_face(center, zoom, face, target, cover, size, image_size, mode="near", margin=0.01, keep=0.12, away=0.15):
    """線の点や山場の点の画面で寄りの点のまわり（face）が端で切れるなら、その点（target）を画面に残したまま、face が全部入るか
    全部外れる center へ寄せる。mode: near（入れるか外すかの近い方）・include（入れる）・exclude（視野の away の割合だけ離して
    外す）。margin だけ離して収まらなければ、face の大きさの CUT_SHARE / 2 まではみ出す・重なるのを許して探す。できなければ
    center のまま。"""
    half = _half_view(zoom, cover, size, image_size)
    view = _view_of(center, zoom, cover, size, image_size)
    if mode == "near" and not _cut(view, face):
        return center
    box = _movable(zoom, cover, size, image_size)

    def allowed(axis):
        """点を今の余白（keep まで）で画面に残し、画像の中に収まる center の範囲。"""
        h = half[axis]
        room = max(0.0, min(keep * 2 * h, target[axis] - (center[axis] - h), (center[axis] + h) - target[axis]))
        return max(box[axis], target[axis] - h + room), min(box[axis + 2], target[axis] + h - room)

    ranges = [allowed(0), allowed(1)]
    for loose in (False, True):
        pad = [-0.5 * CUT_SHARE * (face[axis + 2] - face[axis]) if loose else margin for axis in (0, 1)]
        options = []
        if mode in ("near", "include"):
            contain = [(max(lo, face[axis + 2] + pad[axis] - half[axis]), min(hi, face[axis] - pad[axis] + half[axis]))
                       for axis, (lo, hi) in enumerate(ranges)]
            if all(lo <= hi + 1e-9 for lo, hi in contain):
                options.append(tuple(min(max(center[axis], lo), hi) for axis, (lo, hi) in enumerate(contain)))
        if mode in ("near", "exclude"):
            gap = 0.0 if mode == "near" else away
            for axis in (0, 1):
                lo, hi = ranges[axis]
                extra = pad[axis] + gap * 2 * half[axis]
                for side_lo, side_hi in ((max(lo, face[axis + 2] + extra + half[axis]), hi),
                                         (lo, min(hi, face[axis] - extra - half[axis]))):
                    if side_lo <= side_hi + 1e-9:
                        moved = list(center)
                        moved[axis] = min(max(center[axis], side_lo), side_hi)
                        other = 1 - axis
                        moved[other] = min(max(center[other], ranges[other][0]), ranges[other][1])
                        options.append(tuple(moved))
        if options:
            return min(options, key=lambda c: _distance(c, center, image_size))
    return center


def visible_share(view, box):
    """box（x0, y0, x1, y1）が view の中にある割合（横, 縦）。面積の割合は 2 つの積。"""
    width = max(0.0, min(view[2], box[2]) - max(view[0], box[0])) / max(1e-12, box[2] - box[0])
    height = max(0.0, min(view[3], box[3]) - max(view[1], box[1])) / max(1e-12, box[3] - box[1])
    return width, height


def _cut(view, box):
    """box が view の端で切れている（見える面積が CUT_SHARE より多く、横か縦で CUT_SHARE より多く外にある）か。
    わずかに切れた・わずかに見えるだけのものは数えない（まわりの箱は顔より少し大きく取る）。"""
    width, height = visible_share(view, box)
    return width * height > CUT_SHARE and min(width, height) < 1.0 - CUT_SHARE


def _curve(waypoints, scale):
    """waypoints [(秒, zoom, 中心), ...] を通る、camera.curve: smooth と同じ曲線（zoom は対数。scale は元画像の大きさ）。"""
    from .timeline import SmoothCurve
    return SmoothCurve([w[0] for w in waypoints], [[math.log(w[1]), w[2][0], w[2][1]] for w in waypoints], path=(1, 2), scale=scale)


def _curve_cut(waypoints, t0, t1, face, cover, size, image_size, fps):
    """waypoints の曲線で t0〜t1 を動くとき、寄りの点のまわりが端で切れたコマが続く、いちばん長い秒（中心は camera と同じく
    視野が画像に収まる所へ寄せる）。"""
    curve = _curve(waypoints, image_size)
    run = best = 0
    for i in range(int(math.floor(t0 * fps)), int(math.ceil(t1 * fps)) + 1):
        u, cx, cy = curve(i / fps)
        zoom = math.exp(u)
        center = _clamp_center((cx, cy), zoom, cover, size, image_size, margin=0.0)
        run = run + 1 if _cut(_view_of(center, zoom, cover, size, image_size), face) else 0
        best = max(best, run)
    return best / fps


def _pull_plan(before, target, end_t, hit, face, cover, size, image_size, fps):
    """引きの段のキー（before の最後のキーから、寄りの点 target の側の全景へ end_t 秒に着く）[(秒, zoom, 中心), ...] と頭で引いた割合。

    山場の後（hit）: 頭の RECOIL_S 秒で一気に引き（反動）、残りはゆっくり全景へ。頭で引く割合（zoom は対数で RECOIL_SHARES）と
    頭の終わりの中心（同じ割合か、寄りの点の側）を試す。山場の無い引き: まず全景へそのまま（静かな型）、次に山場の後と同じ形を
    倍の秒で。前から順に試し、寄りの点のまわりが端で切れるのが PULL_OK 秒までの最初の形（無ければいちばん短い形）を選ぶ。
    顔が画面に入ってくる所を速い所に置くため。
    寄りの点が画像の端の近くで、全景の中心が画像の端に寄せてある（clamp した）ときは、ゆっくりの所に TAIL_KEYS − 1 個のキーを
    足して、どの zoom でも視野を画像の端に付けたまま引く形も試す（キーの間を結ぶと、視野が端から離れて顔の端が切れるため）。"""
    t0, rest_zoom, rest_center = before[-1]
    total = end_t - t0
    final = _clamp_center(target, 1.0, cover, size, image_size)
    lerp = lambda a, b, u: (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)  # noqa: E731
    recoil = min(RECOIL_S if hit else 2.0 * RECOIL_S, 0.4 * total)     # 山場の無い引きは頭もゆっくり

    def tails(mid_t, mid_zoom, mid):
        """mid から全景へ 1 本と、（違えば）キーを足して画像の端に沿わせる形。"""
        plain = [(end_t, 1.0, final)]
        steps = []
        for k in range(1, TAIL_KEYS):
            u = k / TAIL_KEYS
            zoom = _floor4(mid_zoom ** (1.0 - u))
            steps.append((mid_t + (end_t - mid_t) * u, zoom, _clamp_center(lerp(mid, target, u), zoom, cover, size, image_size)))
        if all(_distance(c, lerp(mid, final, k / TAIL_KEYS), image_size) < 1.0 for k, (_, _, c) in enumerate(steps, 1)):
            return [plain]
        return [plain, steps + plain]

    candidates = []
    if not hit:
        gentle = [(end_t, 1.0, final)]
        if _distance(rest_center, final, image_size) < SAME_POINT * max(image_size) and rest_zoom > 1.0:
            # 全景の中心が寄りの点と同じ所: 途中で中心を流してから全景へ（寄りきった所で止まらないように）
            mid_t, mid_zoom = t0 + 0.5 * total, _floor4(math.sqrt(rest_zoom))
            push = (before[-1][2][0] - before[0][2][0], before[-1][2][1] - before[0][2][1])
            mid = _clamp_center((rest_center[0] + push[0], rest_center[1] + push[1]), mid_zoom, cover, size, image_size)
            gentle = [(mid_t, mid_zoom, mid)] + gentle
        candidates.append((gentle, None))
    for share in RECOIL_SHARES:
        for pan in (share, 0.5 * (share + 1.0), 1.0):
            mid_zoom = _floor4(rest_zoom ** (1.0 - share))
            mid = _clamp_center(lerp(rest_center, target, pan), mid_zoom, cover, size, image_size)
            candidates += [([(t0 + recoil, mid_zoom, mid)] + tail, share) for tail in tails(t0 + recoil, mid_zoom, mid)]
    best = None
    for keys, share in candidates:            # 前の形を選ぶ（端で切れるのが PULL_OK 秒までなら、それより短い形を探さない）
        cut = _curve_cut(list(before) + keys, t0, end_t, face, cover, size, image_size, fps)
        if best is None or cut < best[0] - 1e-9:
            best = (cut, keys, share)
        if cut <= PULL_OK:
            break
    return best[1], best[2]


def compose(image_size, size, seconds=DEFAULT_SECONDS, focus=None, path=None, impact=None, zoom=None, impact_at=None,
            focus_radius=FOCUS_RADIUS, fps=DEFAULT_FPS):
    """点と長さから、カメラのキー・各段の秒・hooks の定数・注意を決める（描かない）。

    既定の形で、寄りの点のまわり（顔ほどの半径）が画面の端で切れたまま EDGE_LIMIT_S 秒より長く続く段があれば、LAYOUT_TRIES の
    順（線の間の zoom・移る速さ・線と山場の画面の取り方）に変えた形を試し、収まる最初の形（無ければいちばん短い形）を使う。
    それでも長い段は warnings に直し方を書く。"""
    if not 4.0 <= seconds <= 120.0:
        raise PipelineError(f"--seconds は 4〜120 にしてください: {seconds:g}")
    if impact_at is not None and impact is None:
        raise PipelineError("--impact-at は山場の点 --impact と一緒に書いてください")
    if not 0.0 < focus_radius <= 0.5:
        raise PipelineError(f"--focus-radius は 0 より大きく 0.5 以下にしてください（元画像の短い辺に対する割合）: {focus_radius:g}")
    warnings = []
    cover = max(size[0] / image_size[0], size[1] / image_size[1])      # zoom 1 で元画像の 1 画素が画面の何画素か
    cap = MAX_MAGNIFICATION / cover
    if cap < 1.0:
        warnings.append(f"元画像 {image_size[0]}x{image_size[1]} は {size[0]}x{size[1]} の全景（zoom 1）でも元画像の画素を"
                        f" {cover:.2f} 倍に引き伸ばします（目安は {MAX_MAGNIFICATION:g} 倍まで）。寄りは付けません。"
                        f"--size を小さくするか、大きい画像を使ってください")
        cap = 1.0
    requested = DEFAULT_ZOOM if zoom is None else float(zoom)
    if requested < 1.0:
        raise PipelineError(f"--zoom は 1 以上にしてください（1 = 全景）: {requested:g}")
    if zoom is not None and requested > cap + 1e-9:
        warnings.append(f"--zoom {requested:g} は元画像の画素を {requested * cover:.2f} 倍に引き伸ばすため、"
                        f"{MAX_MAGNIFICATION:g} 倍になる zoom {cap:.2f} に止めました")
    close = _floor4(max(1.0, min(requested, cap)))         # 小数 4 桁で切り捨て（丸めて上限を超えない）
    portrait = image_size[1] > image_size[0]
    default_focus = focus is None and path is None and impact is None
    start = focus or (path[0] if path else None) or impact or ((0.5, 0.36) if portrait else (0.5, 0.45))
    T = float(seconds)
    fade_s = round(min(1.2, max(0.5, 0.08 * T)), 2)
    # 全景に着く秒。暗転の途中に置き、カメラを最後まで止めない（止まった所は `mv inspect` の「止まったコマ」に出る。暗転の明るさの
    # 変化が、引きの終わりのゆっくりした所のコマの差を補う）
    full_t = T - FULL_IN_FADE * fade_s
    base = {"cover": cover, "cap": cap, "close": close, "start": start, "default_focus": default_focus, "fade_s": fade_s,
            "seconds": T, "impact": impact, "line": path, "focus_box": focus_box(start, focus_radius, image_size),
            "focus_radius": focus_radius, "magnification": round(cover * close, 3)}
    tried = []
    for share, framing, pace in LAYOUT_TRIES:
        tried.append(_layout(base, image_size, size, share, framing, path, impact, impact_at, full_t, fps, pace))
        if max(tried[-1]["edge"].values()) <= EDGE_LIMIT_S:
            break
    layout = min(tried, key=lambda item: (max(item["edge"].values()) > EDGE_LIMIT_S, max(item["edge"].values()), tried.index(item)))
    if layout is not tried[0]:
        changes = []
        if layout["share"] != MOVE_SHARE:
            changes.append(f"線の間の zoom を {layout['moving']:.2f} に")
        if layout["pace"] < 1.0:
            changes.append(f"移る段を {layout['pace']:g} 倍に縮めて速く動かす形に")
        if layout["framing"] != "near":
            changes.append(f"線と山場の画面を{FRAMING_NAMES[layout['framing']]}に")
        layout["adjusted"] = f"寄りの点のまわりが端で切れる時間を短くするため、{'、'.join(changes)}しました"
    layout["warnings"] = warnings + layout["warnings"]
    for phase, seconds_cut in layout["edge"].items():
        if seconds_cut > EDGE_LIMIT_S:
            layout["warnings"].append(f"寄りの点のまわり（半径 {focus_radius:g}。顔ほどの大きさ）が、{PHASE_NAMES[phase]}の段で "
                                      f"{seconds_cut:.2f} 秒、画面の端で切れたまま続きます（目安 {EDGE_LIMIT_S:g} 秒まで）。{EDGE_HINTS[phase]}")
    return layout


def _layout(base, image_size, size, share, framing, path, impact, impact_at, full_t, fps, pace=1.0):
    """1 つの形（線の間の zoom の割合 share・線と山場の画面の取り方 framing・移る段の速さ pace）のカメラのキーと段の秒・端で
    切れる時間。キーは camera.curve: smooth でつなぐ（キーを通って速さが続く曲線）。"""
    cover, close, start, T = base["cover"], base["close"], base["start"], base["seconds"]
    moving = _floor4(min(1.0 + share * (close - 1.0), max(1.0, close * MOVE_DROP)))
    near = SAME_POINT * max(image_size)
    warnings = []
    line = list(path) if path else []
    if line:
        # 線のうち、カメラが届く所（その zoom で視野が画像に収まる中心の範囲）だけを追う。届かない端は届く所へ移し、注意に書く
        clipped = _clip(line[0], line[1], _movable(moving, cover, size, image_size))
        moved = clipped if clipped is not None else (_clamp_center(line[0], moving, cover, size, image_size),
                                                      _clamp_center(line[1], moving, cover, size, image_size))
        for name, before, after in (("始まり", line[0], moved[0]), ("終わり", line[1], moved[1])):
            if _distance(before, after, image_size) > near:
                warnings.append(f"--path の{name} {_fmt(before)} は、この寄り（zoom {moving:.2f}）ではカメラの中心が届かないので、"
                                f"線の上の届く所 {_fmt(after)} へ移しました（線の見える所だけを追います）")
        line = list(moved)
    route = [start] + line + ([impact] if impact else [])
    given = [route[0]]
    for point in route[1:]:                 # 同じ点を 2 度書いた所は 1 つに
        if _distance(given[-1], point, image_size) > near:
            given.append(point)
    # カメラの中心は、そのキーの zoom の視野が画像に収まる所へ寄せてから並べる（時間の割り振りも寄せた後の道のりで数える）。
    # 線の点と山場の点では、寄りの点のまわり（face）が画面の端で切れたままにならないように、点を画面に残したまま face が全部入るか
    # 全部外れる所へ寄せる（framing。線が顔の際を通っても、顔を半分切ったまま動き続けない）。寄せると重なる点は 1 つにする
    face = base["focus_box"]
    points, targets = [_clamp_center(given[0], close, cover, size, image_size)], [given[0]]
    for point in given[1:]:
        center = _frame_face(_clamp_center(point, moving, cover, size, image_size), moving, face, point, cover, size, image_size,
                             framing)
        if _distance(points[-1], center, image_size) > near:
            points.append(center)
            targets.append(point)
        elif len(points) > 1:
            points[-1], targets[-1] = center, point      # 後の点（山場）の画面を残す
    # 短い区間で鋭く折れる点は落とす（寄せた端で、0.3 秒ほどの急な折り返しができないように）
    while len(points) >= 3:
        total = sum(_distance(a, b, image_size) for a, b in zip(points, points[1:]))
        sharp = [i for i in range(1, len(points) - 1) if _turn(points[i - 1], points[i], points[i + 1], image_size) > SHARP_TURN
                 and min(_distance(points[i - 1], points[i], image_size), _distance(points[i], points[i + 1], image_size)) < SHORT_LEG * total]
        if not sharp:
            break
        del points[sharp[0]], targets[sharp[0]]
    has_route = len(points) >= 2
    # 線に沿う間の zoom は、寄りから moving へ道のりに比べて少しずつ引く（どの点でも zoom が動いていて、カメラが止まらない）。
    # 点ごとにその zoom で、もう一度画像に収めて顔の切れを避ける
    zooms = [close]
    if has_route:
        lengths = [_distance(a, b, image_size) for a, b in zip(points, points[1:])]
        done = 0.0
        for i, length in enumerate(lengths, 1):
            done += length
            zooms.append(_floor4(math.exp(math.log(close) + (done / sum(lengths)) * (math.log(moving) - math.log(close)))))
            points[i] = _frame_face(_clamp_center(targets[i], zooms[i], cover, size, image_size), zooms[i], face, targets[i], cover,
                                    size, image_size, framing)
    # 段の長さは全体の割合（寄り 25%・線 15%。山場へ移るだけなら 10%）。長い動画では寄りと線を頭打ちにし、残りを引きに回す
    # （移る段が長いと、顔が端で切れたまま遅く動く）
    close_end = min((0.25 if has_route else 0.30) * T, 4.0 if has_route else 5.0)
    route_s = min((0.15 if path else 0.10) * T, 2.2 if path else 1.5) if has_route else 0.0
    hit_t = None
    if impact is not None:
        hit_t = close_end + route_s
        if impact_at is not None:
            earliest, latest = OPEN_S + 0.7, full_t - SETTLE_S - 1.5
            if not earliest <= impact_at <= latest:
                raise PipelineError(f"--impact-at は {earliest:.2f}〜{latest:.2f} 秒にしてください（寄り・引き・暗転の分を残す）: {impact_at:g}")
            if has_route and impact_at < hit_t:     # 早めるときは寄りと移る段を同じ割合で縮める（速く動く）
                share_t = impact_at / hit_t
                close_end, route_s = close_end * share_t, route_s * share_t
            else:                                   # 遅らせるときは寄りを延ばす（移る速さは変えない）
                close_end = impact_at - route_s
            hit_t = float(impact_at)
    route_end = close_end + route_s
    if pace != 1.0 and has_route:           # 山場の秒（線の終わり）を変えずに、移る段を縮める
        route_s *= pace
        close_end = route_end - route_s

    def clamp(center, zoom):
        return _clamp_center(center, zoom, cover, size, image_size)

    def toward(a, b, length_px):
        """a から b の向き（元画像の画素で数える）へ length_px 画素の、正規化座標のずれ。向きが無ければ 0。"""
        dx, dy = (b[0] - a[0]) * image_size[0], (b[1] - a[1]) * image_size[1]
        norm = math.hypot(dx, dy)
        return (0.0, 0.0) if norm < 1e-9 else (dx / norm * length_px / image_size[0], dy / norm * length_px / image_size[1])

    # 頭: 少し引いた所から寄りの点へ押し込みながら、中心を流す（DRIFT_SPEED）。流す向きは次の行き先の反対側から（寄りの点を
    # 通り過ぎて次へ続く）。画像の端で寄せられて流せなければ、次の行き先の側から寄りの点へ流す。zoom の山は寄りの段の終わりの
    # 少し後（線へ移る・引くのが始まってから）に置く: 中心が向きを変える所で zoom も止まると、カメラがそこで止まる
    diag = math.hypot(*size)
    peak = hit_t is None or has_route                     # 山場が寄りの段の終わりに来るとき（山場だけ）は山を後へずらさない
    lead_zoom = _floor4(max(1.0, close * CLOSE_LEAD)) if peak and close > 1.0 else close
    # 寄りの点が画像の端の近く（視野が画像の端に付く所）でも、寄りの段の終わりのキーは端から KEY_MARGIN だけ内側に置く（端に付いた
    # キーへ寄ると、カメラの clamp が中心を端に沿わせ、キーの所で速さが折れる）
    focus_at = _clamp_center(start, lead_zoom, cover, size, image_size, margin=KEY_MARGIN)
    route_times = []
    if has_route:
        lengths = [_distance(a, b, image_size) for a, b in zip(points, points[1:])]
        route_times = [close_end + route_s * sum(lengths[:i + 1]) / sum(lengths) for i in range(len(lengths))]
        route_times[-1] = route_end
    next_t, next_center = (route_times[0], points[1]) if has_route else (full_t, clamp(start, 1.0))
    if _distance(focus_at, next_center, image_size) < near:
        next_center = (focus_at[0] + 0.1, focus_at[1])
    open_zoom = _floor4(max(1.0 + OPEN_ZOOM * (close - 1.0), close * PUSH_FROM ** (close_end / PUSH_S), 1.0))
    drift = toward(focus_at, next_center, DRIFT_SPEED * diag * min(close_end, 4.0) / (cover * close))

    def opening(sign):
        """頭の中心（寄りの点から sign の向きに流した所）。頭の画面に寄りの点のまわりが全部入るところまで流す量を減らす。"""
        for share in (1.0, 0.75, 0.5, 0.25, 0.0):
            center = _clamp_center((focus_at[0] + sign * share * drift[0], focus_at[1] + sign * share * drift[1]), open_zoom, cover,
                                   size, image_size, margin=KEY_MARGIN)
            if not _cut(_view_of(center, open_zoom, cover, size, image_size), face) or share == 0.0:
                return center

    behind, ahead = opening(-1.0), opening(1.0)
    wanted = math.hypot(drift[0] * image_size[0], drift[1] * image_size[1])
    got = lambda c: math.hypot((c[0] - focus_at[0]) * image_size[0], (c[1] - focus_at[1]) * image_size[1])  # noqa: E731
    first = behind if got(behind) >= 0.7 * wanted or got(behind) >= got(ahead) else ahead
    if peak and lead_zoom < close:
        # 寄りの点を通る所（zoom はまだ少し上がる）の後、寄りの段の終わりで zoom の山。中心は次の行き先の向きへ流れ続ける
        lag = min(PEAK_LAG, 0.3 * close_end)
        step = (next_center[0] - focus_at[0], next_center[1] - focus_at[1])
        frac = min(0.06, 0.5 * lag / max(next_t - close_end + lag, 1e-6))
        waypoints = [(0.0, open_zoom, first), (close_end - lag, lead_zoom, focus_at),
                     (close_end, close, _clamp_center((focus_at[0] + step[0] * frac, focus_at[1] + step[1] * frac), close, cover, size,
                                                      image_size, margin=KEY_MARGIN))]
    else:
        waypoints = [(0.0, open_zoom, first), (close_end, lead_zoom, focus_at)]
    for t, point, zoom in zip(route_times, points[1:], zooms[1:]):
        waypoints.append((t, zoom, clamp(point, zoom)))
    recoil_span, hit_view, pull_start = None, None, route_end
    if hit_t is not None:
        hit_view = _view_of(waypoints[-1][2], waypoints[-1][1], cover, size, image_size)
    if hit_t is not None or has_route:
        # 山場（か線の終わり）に着いた後も、着いた向きへ少しだけ流しながら引き始めてから向きを返す（揺れの間。着いた所で止まらない。
        # 向きを返す所では zoom が引き続けている）
        arrive_zoom, arrive, came_from = waypoints[-1][1], waypoints[-1][2], waypoints[-2][2]
        settle_zoom = _floor4(arrive_zoom * SETTLE_ZOOM)
        view_diag = math.hypot(size[0] / (cover * arrive_zoom), size[1] / (cover * arrive_zoom))
        nudge = toward(came_from, arrive, SETTLE_SHARE * view_diag)
        pull_start = route_end + SETTLE_S
        waypoints.append((pull_start, settle_zoom, clamp((arrive[0] + nudge[0], arrive[1] + nudge[1]), settle_zoom)))
    # 引き: 山場の後は頭の RECOIL_S 秒で一気に引き（反動）、あとはゆっくり全景へ。顔が画面に入ってくる所が速い所に来るように、頭の
    # 終わりの所を選ぶ（_pull_plan）。全景は寄りの点の側に寄せる（寄りの点を画面に残す）
    pull, recoil_share = _pull_plan(waypoints, start, full_t, hit_t is not None, face, cover, size, image_size, fps)
    if hit_t is not None and len(pull) > 1:
        recoil_span = [round(pull_start, 3), round(pull[0][0], 3)]
    waypoints += pull
    keys = []
    for t, zoom, center in waypoints:
        zoom = _floor4(zoom)                  # 丸めた zoom で視野を数えてから center を寄せる
        cx, cy = clamp(center, zoom)
        keys.append({"t": round(t, 4), "zoom": zoom, "center": [round(cx, 4), round(cy, 4)]})
    times = [k["t"] for k in keys]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise PipelineError(f"mv fx quick: カメラのキーの秒が昇順になりません（--seconds を長くしてください）: {times}")
    phases = {"close": [0.0, round(close_end, 3)], "move": [round(close_end, 3), round(route_end, 3)] if has_route else None,
              "impact": round(hit_t, 3) if hit_t is not None else None, "pull": [round(pull_start, 3), round(full_t, 3)],
              "fade": [round(T - base["fade_s"], 3), round(T, 3)]}
    layout = {**base, "keys": keys, "phases": phases, "warnings": warnings, "moving": moving, "points": points, "framing": framing,
              "share": share, "pace": pace, "recoil_share": recoil_share, "recoil": recoil_span,
              "hit_view": [round(v, 4) for v in hit_view] if hit_view else None}
    layout["edge"] = edge_times(layout, image_size, size, fps)
    return layout


def camera_path(layout, image_size, size):
    """layout のカメラのキーから、描くときと同じ camera.CameraPath（frame: source・clamp・plate: full）を作る。"""
    from . import camera
    from . import spec as fxspec
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": size[0], "height": size[1]}, "duration_s": layout["seconds"],
           "assets": {"base": "image.png"},
           "camera": {"keys": layout["keys"], "frame": "source", "clamp": True, "plate": "full", "curve": "smooth"}}
    return camera.CameraPath(fxspec.parse_spec(raw, Path(".")).data["camera"], layout["seconds"], size, plate=image_size)


def edge_times(layout, image_size, size, fps=DEFAULT_FPS):
    """寄りの点のまわり（layout["focus_box"]）が画面の端で切れている（_cut）コマが続く、いちばん長い秒を段ごとに返す。続きは、始まったコマの段に数える（段の境をまたいでも切らない）。段は close・move・
    hit（山場の後の止め）・pull。"""
    from . import camera
    path = camera_path(layout, image_size, size)
    phases, total = layout["phases"], int(round(layout["seconds"] * fps))
    bounds = [("close", phases["close"][1])]
    if phases["move"]:
        bounds.append(("move", phases["move"][1]))
    if phases["impact"] is not None:
        bounds.append(("hit", phases["pull"][0]))
    bounds.append(("pull", layout["seconds"] + 1.0))
    result = {name: 0.0 for name, _ in bounds}
    run_start, run_phase = None, None
    for i in range(total + 1):
        t = i / fps
        cut = i < total and _cut((0.0, 0.0, 1.0, 1.0), camera.screen_box(path, t, layout["focus_box"]))
        if cut and run_start is None:
            run_start, run_phase = i, next(name for name, end in bounds if t < end - 1e-9)
        elif not cut and run_start is not None:
            result[run_phase] = max(result[run_phase], round((i - run_start) / fps, 3))
            run_start = None
    return result


# ---- 書くもの ------------------------------------------------------------------------------------

HOOKS_TEMPLATE = '''"""mv fx quick が書いた hooks（uv run mv fx timeline render timeline.json --hooks hooks.py）。

変えてよいのは下の定数と「任意」の行。時刻は timeline の秒、点は元画像の正規化座標（0..1。mv fx guides で読む）。
IMPACT_T はカメラが山場に着く秒（shot.json の camera.keys と同じ）。秒を変えるなら mv fx quick を --impact-at 付きでやり直す。
IMPACT が None（山場の無い絵）なら静かな型: 閃光・輪・火花・揺れは使わず、寄りの点にやわらかい光（LIGHT）が差して薄れるだけ。
mv fx quick をやり直して変わるのは定数の値だけで、ほかの行（「任意」の行も）はどの引数でも同じ文字列。やり直した後に任意の
演出を戻すなら、同じ行の頭の "# " を外す置き換えをもう一度すればよい（秒と大きさは定数の側にある）。
部品は mv_pipeline/fx/impact.py（火花・輪・閃光・揺れ・閃き・暗転）と fire・sky・accent（docs/fx.md の hooks の節）。
"""
from mv_pipeline.fx import accent, fire, impact  # noqa: F401（fire は下の任意の行で使う）

{constants}


def shot(img, t, view):
    if IMPACT is not None:
        point = view.point(*IMPACT)
        if RING > 0:        # 地面を走る輪。山場の点が地面でなければ RING = 0
            impact.ring(img, t, IMPACT_T, point, k=view.px * RING)
        if SPARKS > 0:
            impact.burst(img, t, IMPACT_T, point, k=view.px, n=SPARKS, seed=7)
    elif LIGHT > 0:         # 静かな型: 寄りの点にやわらかい光がゆっくり差し、引くと薄れる（閃光の代わりの控えめな変化）
        accent.warm_light(img, view.base(*SOURCE), t, {{"x": FOCUS[0] * SOURCE[0], "y": FOCUS[1] * SOURCE[1], "rx": LIGHT_R,
                          "ry": LIGHT_R, "amt": [[0.0, 0.0], [LIGHT_T[0], LIGHT], [LIGHT_T[1], 0.4 * LIGHT]], "flick": 0.0,
                          "col": [255, 214, 170]}})
    # 任意: 寄りの点の閃き（FOCUS を目に合わせる。目ごとに呼んでもよい）
    # impact.glint(img, *view.point(*FOCUS), t, GLINT_T, k=view.px * GLINT_K)
    # 任意: 絵の下の端から舞い上がる火の粉（火のある絵に。大きさは画面の短い辺 1080 の画素で書き、画面に合わせる）
    # fire.embers(img, lambda u: view.point(0.1 + 0.8 * u, 0.98), t, 5, EMBERS, 2.5, life=(1.0, 2.4), vy=(100, 300), size=(2.0, 3.6), k=min(img.shape[:2]) / 1080)
    return img


def final(img, t, ctx):
    if IMPACT is not None:
        img = impact.shake(img, t, IMPACT_T, SHAKE)
        impact.flash(img, t, IMPACT_T, FLASH)
    return impact.fade(img, t, END_T, FADE_S)
'''


def _point_text(point):
    return "None" if point is None else f"({point[0]:.4g}, {point[1]:.4g})"


def flash_strength(luma):
    """山場の画面の平均の明るさ luma（0..255。None なら中ほどの 120）→ 閃光の強さ（明るい絵ほど弱い。小数 2 桁）。"""
    luma = 120.0 if luma is None else float(luma)
    x = min(max((luma - LUMA_DARK) / (LUMA_BRIGHT - LUMA_DARK), 0.0), 1.0)
    return round(FLASH_DARK + (FLASH_BRIGHT - FLASH_DARK) * x, 2)


def view_luma(gray, box):
    """元画像の明るさ（uint8 の 2 次元。縮めたものでよい）の、正規化座標の箱 box の中の平均（0..255）。"""
    height, width = gray.shape
    x0, x1 = int(math.floor(box[0] * width)), int(math.ceil(box[2] * width))
    y0, y1 = int(math.floor(box[1] * height)), int(math.ceil(box[3] * height))
    part = gray[max(0, y0):max(y0 + 1, min(height, y1)), max(0, x0):max(x0 + 1, min(width, x1))]
    return float(part.mean()) if part.size else float(gray.mean())


def hooks_values(layout, image_size, size, luma=None):
    """hooks.py の定数（名前 → 値）。閃光は山場の画面の明るさ luma、閃きは寄りの点のまわりの半径、火の粉は画面に合わせる。
    静かな型の光は、zoom がいちばん寄った秒にいちばん強く、全景に着く秒に 4 割まで薄れる。"""
    face = layout["focus_radius"] * min(image_size) * 1920.0 / image_size[0]       # 素材の幅を 1920 とした画素（view.px の単位）
    spawn = 0.8 * layout["cover"] * image_size[0]          # 全景の画面での、火の粉の生まれる線（絵の幅の 0.1〜0.9）の長さ
    peak = max(layout["keys"], key=lambda key: key["zoom"])["t"]
    return {"FLASH": flash_strength(luma), "GLINT_T": round(max(0.6, 0.6 * layout["phases"]["close"][1]), 2),
            "GLINT_K": round(face / GLINT_FACE, 2), "EMBERS": int(min(240, max(16, round(EMBERS_PER_SIDE * spawn / min(size))))),
            "LIGHT": QUIET_LIGHT, "LIGHT_T": (round(max(peak, OPEN_S + 1.0), 2), round(layout["phases"]["pull"][1], 2)),
            "LIGHT_R": round(QUIET_LIGHT_R * layout["focus_radius"] * min(image_size), 1)}


def hooks_source(layout, image_size, size, luma=None):
    phases = layout["phases"]
    values = hooks_values(layout, image_size, size, luma)
    bright = "" if luma is None or layout["impact"] is None else f"山場の画面の明るさ {luma / 255:.2f} に合わせた値で、"
    rows = [("IMPACT", _point_text(layout["impact"]), "山場の点（--impact）。None なら山場の演出なし"),
            ("IMPACT_T", repr(phases["impact"]), "山場の秒"),
            ("FLASH", repr(values["FLASH"]), f"閃光の強さ（0..1。0 で無し。{bright}明るい絵ほど弱い。明滅はこの 1 回だけ）"),
            ("SHAKE", "0.012", "揺れの幅（画面の短い辺の割合。0 で無し）"),
            ("SPARKS", "200", "一斉に飛ぶ火花の数（0 で無し）"),
            ("RING", "1.0", "衝撃の輪の大きさの倍率（0 で無し）"),
            ("FOCUS", _point_text(layout["start"]), "寄りの点（静かな型の光と任意の閃きに使う）"),
            ("GLINT_T, GLINT_K", f"{values['GLINT_T']!r}, {values['GLINT_K']!r}",
             "任意の閃きの秒と大きさの倍率（寄りの点のまわりの半径に合わせた値）"),
            ("EMBERS", repr(values["EMBERS"]), "任意の火の粉の数（全景で画面に入る絵の下の端の長さに合わせた数）"),
            ("LIGHT", repr(values["LIGHT"]), "静かな型（IMPACT が None）の光の強さ（0.3 ほどまで。0 で無し）"),
            ("LIGHT_T, LIGHT_R", f"{values['LIGHT_T']!r}, {values['LIGHT_R']!r}",
             "光がいちばん強い秒と 4 割まで薄れる秒・光の半径（元画像の画素）"),
            ("SOURCE", f"({image_size[0]}, {image_size[1]})", "元画像の大きさ（画素。光の位置と半径の基準）"),
            ("END_T, FADE_S", f"{layout['seconds']!r}, {layout['fade_s']!r}", "終わりの秒と暗転の長さ")]
    width = max(len(f"{name} = {value}") for name, value, _ in rows) + 2
    constants = "\n".join(f"{name} = {value}".ljust(width) + f"# {note}" for name, value, note in rows)
    return HOOKS_TEMPLATE.format(constants=constants)


def spec_data(layout, base, size, fps, image_size):
    return {"schema_version": "fx-shot/0.3", "output": {"width": size[0], "height": size[1], "fps": fps, "crf": 18},
            "duration_s": layout["seconds"], "seed": 7, "assets": {"base": base},
            "camera": {"frame": "source", "clamp": True, "plate": "full", "curve": "smooth", "keys": layout["keys"]},
            "x_quick": {"by": "mv fx quick", "image": list(image_size), "phases": layout["phases"],
                        "zoom_close": round(layout["close"], 4), "magnification_close": layout["magnification"],
                        "magnification_limit": MAX_MAGNIFICATION}}


def timeline_data(layout, size, fps):
    return {"schema_version": "fx-timeline/0.2", "output": {"width": size[0], "height": size[1], "fps": fps, "crf": 18},
            "duration_s": layout["seconds"], "look": {"vignette": {"amount": 0.22}},
            "shots": [{"spec": SPEC, "start": 0, "transition_in": {"type": "black", "duration_s": OPEN_S}}]}


def _fmt(point):
    return f"{point[0]:.2f},{point[1]:.2f}"


def plan_text(layout, image_size, size, fps, args_text, base, values=None):
    p, keys = layout["phases"], layout["keys"]
    values = values or hooks_values(layout, image_size, size)
    quiet = p["impact"] is None
    steps = [f"寄り {p['close'][0]:.1f}–{p['close'][1]:.1f} 秒（{_fmt(layout['start'])}"
             f"{'。点の指定なし: 絵の上寄りの中央' if layout['default_focus'] else ''}・zoom {layout['close']:.2f} = 元画像の画素の"
             f" {layout['magnification']:.2f} 倍）"]
    if p["move"]:
        what = "線に沿って移る" if layout["line"] else "山場へ移る"
        steps.append(f"{what} {p['move'][0]:.1f}–{p['move'][1]:.1f} 秒")
    if not quiet:
        steps.append(f"山場 {p['impact']:.2f} 秒（閃光・揺れ・火花・衝撃の輪）")
    recoil = layout.get("recoil")
    pull_note = f"（頭の {recoil[1] - recoil[0]:.1f} 秒で一気に引き、あとはゆっくり）" if recoil else ""
    steps += [f"全景へ引く {p['pull'][0]:.1f}–{p['pull'][1]:.1f} 秒{pull_note}", f"暗転 {p['fade'][0]:.1f}–{p['fade'][1]:.1f} 秒"]
    image, out = args_text
    edge = "・".join(f"{PHASE_NAMES[name]} {value:.2f}" for name, value in layout["edge"].items())
    caps = zoom_caps(image_size, size)
    kind = ("山場なし（静かな型）: 閃光・輪・火花・揺れは使わず、寄りの点にやわらかい光（LIGHT）が差して薄れる" if quiet
            else "山場あり: 閃光（1 回）・揺れ・火花・衝撃の輪。静かな絵なら --impact none で静かな型にする")
    lines = [
        "# 一枚絵の動画（mv fx quick の計画）", "",
        f"- 絵: {base}（{image_size[0]}x{image_size[1]}）→ {size[0]}x{size[1]}・{fps}fps・{layout['seconds']:g} 秒",
        f"- 型: {kind}",
        "- 構成: " + " → ".join(steps),
        f"- カメラ: shot.json の camera.keys（{len(keys)} 個）を camera.curve: smooth（速さが続く曲線）でつなぐ。止まらず、向きも急に"
        "返さない。frame: source・clamp・plate: full。つなぎは頭の黒から上げる 0.5 秒だけ",
        f"- 寄りの上限（元画像の 1 画素を画面の {MAX_MAGNIFICATION:g} 画素まで）: "
        + "・".join(f"{w}x{h} なら {z:.2f} 倍" for (w, h), z in caps) + f"（今は {size[0]}x{size[1]}）",
    ]
    if caps[0][1] < LOW_ZOOM:
        better = max(caps, key=lambda item: item[1])
        lines.append(f"- 寄りが {LOW_ZOOM:g} 倍に届かない（元画像が小さい）: 強く寄るなら `--size {better[0][0]}x{better[0][1]}`"
                     f"（{better[1]:.2f} 倍。画面は粗くなる）、精細さを取るなら今の大きさのまま（寄りは弱い）。"
                     "何通りも描いて比べなくてよい")
    lines.append(f"- 顔の切れ: 寄りの点のまわり（半径 {layout['focus_radius']:g}）が画面の端で切れたまま続く長さ {edge} 秒"
                 f"（目安 {EDGE_LIMIT_S:g} 秒まで）")
    if layout.get("adjusted"):
        lines.append(f"- 調整: {layout['adjusted']}")
    lines += [f"- 注意: {w}" for w in layout["warnings"]]
    effects = (f"| 5 | 静かな型の光 | hooks.py の LIGHT（0 で無し）・LIGHT_T・LIGHT_R | {values['LIGHT']:g}・{values['LIGHT_T'][0]:g} 秒・"
               f"半径 {values['LIGHT_R']:g} 画素 |" if quiet else
               f"| 5 | 閃光・揺れ・火花・輪 | hooks.py の FLASH・SHAKE・SPARKS・RING（FLASH は山場の画面の明るさに合わせた値。"
               f"山場の点が地面でなければ RING = 0） | {values['FLASH']:g}・0.012・200・1.0 |")
    impact_row = (f"{_fmt(layout['impact'])}・{p['impact']:g} 秒" if not quiet else "なし（山場の無い絵には付けない）")
    lines += ["", "## 変えるとよい所", "",
              "| # | 何を | どこで | 今の値 |", "|---|---|---|---|",
              f"| 1 | 寄りの点（顔・目）とまわりの大きさ | `--focus X,Y`・`--focus-radius R`（座標は `mv fx guides IMAGE --out grid.jpg`"
              f" で読む。R は顔の半径の、短い辺に対する割合） | {_fmt(layout['start'])}・{layout['focus_radius']:g} |",
              f"| 2 | 寄りの強さ | `--zoom`（1 = 全景。上限 {layout['cap']:.2f} で止まる。大きさごとの上限は上） | {layout['close']:.2f} |",
              f"| 3 | 沿って動く線（腕や道など） | `--path X0,Y0:X1,Y1` | {'あり' if layout['line'] else 'なし'} |",
              f"| 4 | 山場の点と秒 | `--impact X,Y`・`--impact-at 秒` | {impact_row} |",
              effects,
              f"| 6 | 暗転・周辺減光 | hooks.py の FADE_S、timeline.json の look.vignette.amount | {layout['fade_s']:g} 秒・0.22 |",
              "| 7 | 任意の演出 | hooks.py の「任意」の行（閃き・火の粉）の頭の `# ` を外す（秒・大きさ・数は GLINT_T・GLINT_K・EMBERS） | 無し |",
              f"| 8 | 長さ・大きさ | `--seconds`・`--size`・`--fps` | {layout['seconds']:g} 秒・{size[0]}x{size[1]}・{fps} |",
              "", "1〜4・8 は mv fx quick を引数を変えてやり直す（カメラと hooks の秒がそろう。引数は前の値を引き継ぐので、変える所だけ書く。点を外すときは none）。5〜7 はファイルを直接直す。",
              "先に引数を決めてからファイルを直す（手で直したファイルがあると、違う引数のやり直しは止まる。--force は上書き）。",
              "やり直しで変わるのは hooks.py の定数の値だけで、任意の行はどの引数でも同じ文字列（--force の後も同じ置き換えで戻せる）。",
              "手で直した後に点や大きさの指定なしで `--preview`・`--render` を付けると、直したファイルのまま描く。", "",
              "## 次のコマンド", "", "```sh",
              f"uv run mv fx quick {image} {out} --preview      # 半分の大きさのコマの一覧 {PREVIEW} を 1 枚見る",
              f"uv run mv fx quick {image} {out} --render       # 本番と確認（check.jpg と要約。明滅・顔の切れ・元画像との差・止まったコマ）",
              f"uv run mv inspect check {out}/{INSPECT} --brief # 任意: 検品（出来事は要確認で、合否ではない）",
              "```", "", f"実行のたびに {LOG} へ記録を足す（作業記録の下書き）。手順と罠は docs/recipes/one-image.md。"]
    return "\n".join(lines) + "\n"


def inspect_frames(layout, fps):
    """検品するコマ [始め, 終わり)。暗転の終わりの END_SKIP 秒（黒に近い同じ絵が続く）は見ない（「止まったコマ」に出るだけなので）。"""
    return [0, int(round((layout["seconds"] - END_SKIP) * fps))]


def inspect_data(layout, video_rel, fps=DEFAULT_FPS):
    p = layout["phases"]
    lights = [[0.0, OPEN_S], p["fade"]]
    moves = []
    if p["move"]:
        moves.append(p["move"])
    if p["impact"] is not None:
        lights.insert(1, [p["impact"], round(p["impact"] + FLASH_WINDOW_S, 3)])
        moves.append([p["impact"], round(p["impact"] + SHAKE_S, 3)])
    if layout.get("recoil"):
        moves.append(layout["recoil"])
    cues = {"lights": lights, **({"moves": moves} if moves else {})}
    return {"schema_version": "inspect/0.1", "foreground": "none",
            "clips": [{"id": "quick", "path": video_rel, "frames": inspect_frames(layout, fps), "cues": cues}],
            "x_quick": "mv fx quick が書いた設定。cues は頭の黒・山場の閃光と揺れ・山場へ移る動き・山場の後の一気の引き・暗転の秒。"
                       f"暗転の終わりの {END_SKIP:g} 秒（黒に近い同じ絵）は見ない"}


# ---- 下見の一覧 ------------------------------------------------------------------------------------

def preview_times(layout, fps, count):
    """下見で見る秒（各段の要所。コマの時刻にそろえる）と短い名前。"""
    p, T = layout["phases"], layout["seconds"]
    picks = [(OPEN_S + 0.1, "close"), (p["close"][1] * 0.65, "close2")]
    if p["impact"] is None or abs(p["impact"] - p["close"][1]) > 0.2:      # 寄りの終わりが山場なら、山場の直前のコマで足りる
        picks.append((p["close"][1], "close"))
    if p["move"]:
        picks.append(((p["move"][0] + p["move"][1]) / 2, "move"))
    if p["impact"] is not None:
        picks += [(p["impact"] - 0.08, "hit-"), (p["impact"] + 0.04, "hit"), (p["impact"] + 0.25, "sparks")]
    pull = p["pull"]
    picks += [(pull[0] + (pull[1] - pull[0]) * 0.4, "pull"), (p["fade"][0] - 0.05, "full"), (T - layout["fade_s"] * 0.5, "fade")]
    while len(picks) < count:          # 足りない分は、いちばん空いた所の真ん中に足す
        ordered = sorted(picks)
        gaps = [(b[0] - a[0], (a[0] + b[0]) / 2) for a, b in zip(ordered, ordered[1:])]
        picks.append((max(gaps)[1], "more"))
    picks = sorted(picks)[:count] if len(picks) <= count else _keep(picks, count)
    frames = []
    for t, name in sorted(picks):
        name = "close" if name == "close2" else name
        index = min(max(int(round(t * fps)), 0), int(round(T * fps)) - 1)
        if index not in [i for i, _ in frames]:
            frames.append((index, name))
    return [(i / fps, name) for i, name in frames]


def _keep(picks, count):
    """多すぎるときは、山場のまわりを残して頭と引きの途中から減らす。"""
    order = {"hit": 0, "sparks": 1, "hit-": 2, "full": 3, "close": 4, "move": 5, "pull": 6, "fade": 7, "close2": 8, "more": 9}
    kept = sorted(picks, key=lambda item: (order.get(item[1], 9), item[0]))[:count]
    return sorted(kept)


def contact_sheet(frames, labels, max_width=1600):
    """コマ（uint8 RGB、同じ大きさ）を左上から並べた 1 枚。縦の絵は 5 列、横の絵は 3 列。字は ASCII。"""
    import cv2
    height, width = frames[0].shape[:2]
    columns = 5 if height > width else 3
    cell_w = max(64, (max_width - 8 * (columns + 1)) // columns)
    cell_h = int(round(height * cell_w / width))
    rows = math.ceil(len(frames) / columns)
    bar = 26
    sheet = np.full((8 + rows * (cell_h + bar + 8), 8 + columns * (cell_w + 8), 3), 18, np.uint8)
    for index, (image, label) in enumerate(zip(frames, labels)):
        row, column = divmod(index, columns)
        x, y = 8 + column * (cell_w + 8), 8 + row * (cell_h + bar + 8)
        sheet[y + bar:y + bar + cell_h, x:x + cell_w] = cv2.resize(image, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        cv2.putText(sheet, label, (x + 2, y + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
    return sheet


def write_preview(out, layout, size, fps, progress=None):
    """半分の大きさで描いたコマ（縦の出力は 10 枚を 5 列、横は 9 枚を 3 列）を、幅 1600 画素までの 1 枚に並べる。"""
    from . import sequence
    from .hooks import Hooks
    times = preview_times(layout, fps, 10 if size[1] > size[0] else 9)
    timeline = sequence.load_timeline(out / TIMELINE)
    ctx = sequence.Context(timeline, 0.5, Hooks(out / HOOKS))
    try:
        frames = [ctx.frame(t) for t, _ in times]
    finally:
        ctx.close()
    sheet = contact_sheet(frames, [f"{t:.2f}s {name}" for t, name in times])
    atomic_image(out / PREVIEW, sheet)
    if progress:
        progress(f"[fx quick] {PREVIEW}: {len(frames)} コマ（半分の大きさ）")
    return {"path": str(out / PREVIEW), "times": [round(t, 3) for t, _ in times]}


# ---- 入口 ------------------------------------------------------------------------------------

def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _edited(out, record):
    """記録のあるファイルのうち、今の中身が記録と違うもの（手で直したもの）。"""
    edited = []
    for name, digest in (record or {}).get("files", {}).items():
        path = out / name
        if path.is_file() and sha256(path) != digest:
            edited.append(name)
    return edited


def _image_gray(image, longest=512):
    """元画像の明るさ（uint8 の 2 次元。長い辺を longest 画素まで縮める。閃光の強さを決めるのに使う）。"""
    from PIL import Image
    with Image.open(image) as handle:
        gray = handle.convert("L")
        gray.thumbnail((longest, longest))
        return np.asarray(gray, np.uint8)


LOG = "log.md"
COMMON_SIZES = {"landscape": ((1920, 1080), (1280, 720)), "portrait": ((1080, 1920), (720, 1280))}
LOW_ZOOM = 1.4                  # 寄りの上限がこれより弱いときは、plan.md に大きさの選び方を 1 行書く


def zoom_caps(image_size, size):
    """出力の大きさごとの寄りの上限（元画像の 1 画素を画面の MAX_MAGNIFICATION 画素まで）[(大きさ, zoom)]。今の大きさと、同じ向きの
    よく使う大きさ。"""
    sizes = [tuple(size)] + [s for s in COMMON_SIZES["portrait" if size[1] > size[0] else "landscape"] if s != tuple(size)]
    return [(s, max(1.0, MAX_MAGNIFICATION / max(s[0] / image_size[0], s[1] / image_size[1]))) for s in sizes]


def _render_progress(progress):
    """描画の進み（コマ数の行）は出さず、始まりと終わりの行だけを流す。"""
    if progress is None:
        return None
    return lambda message: None if "コマ 経過" in str(message) else progress(message)


def _relative(path, out):
    path, out = Path(path), Path(out)
    try:
        return path.resolve().relative_to(out.resolve()).as_posix()
    except ValueError:
        return str(path)


def append_log(out, summary, argv_text, elapsed):
    """OUT/log.md に 1 回分の記録（時刻・引数・一式・注意・所要時間・出力のパス）を足す（作業記録の下書き）。"""
    import datetime
    out = Path(out)
    lines = [f"## {datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}（mv fx quick）", "",
             f"- 引数: {argv_text or '（指定なし。前の引数のまま）'}",
             f"- 一式: {'書いた' if summary['made'] == 'written' else '今のファイルのまま'}（{'・'.join(summary['files'])}）"
             + (f"。手で直したファイル: {'・'.join(summary['edited'])}" if summary.get("edited") else ""),
             f"- 出力: {summary['size'][0]}x{summary['size'][1]}・{summary['fps']}fps・{summary['seconds']:g} 秒・型 {summary['kind']}"]
    lines += [f"- 注意: {w}" for w in summary["warnings"]] or ["- 注意: なし"]
    if summary.get("preview"):
        lines.append(f"- 下見: {_relative(summary['preview'], out)}")
    if summary.get("render"):
        render = summary["render"]
        lines.append(f"- 描画: {_relative(render['video'], out)}（technical_pass={render['technical_pass']}"
                     + ("・前の結果を使った" if render.get("reused") else "") + "）")
    if summary.get("check"):
        from . import quick_check
        lines += [f"- {line}" for line in quick_check.lines(summary["check"], _relative(summary["check"]["image"], out))]
    lines += [f"- 所要: {elapsed:.1f} 秒", ""]
    with open(out / LOG, "a", encoding="utf-8", newline="\n") as handle:
        if handle.tell() == 0:
            handle.write("# 作業記録（mv fx quick が実行のたびに足す下書き。見た目の採否は人が決めて書き足す）\n\n")
        handle.write("\n".join(lines) + "\n")


def summary_text(summary):
    """stdout 用の短い要約（数行。OUT の中のパスは OUT からの相対）。全部の値は --json か quick.json・check.json・log.md。"""
    out = summary["out"]
    p = summary["phases"]
    steps = [f"寄り {p['close'][0]:.1f}–{p['close'][1]:.1f}"]
    if p["move"]:
        steps.append(f"移動 {p['move'][0]:.1f}–{p['move'][1]:.1f}")
    if p["impact"] is not None:
        steps.append(f"山場 {p['impact']:.2f}")
    steps += [f"引き {p['pull'][0]:.1f}–{p['pull'][1]:.1f}", f"暗転 {p['fade'][0]:.1f}–{p['fade'][1]:.1f} 秒"]
    caps = "・".join(f"{w}x{h} {z:.2f}" for (w, h), z in summary["caps"])
    edge = summary["edge"]["seconds"]
    lines = [f"mv fx quick: {out}（{'一式を書いた' if summary['made'] == 'written' else '今の一式のまま'}・"
             f"{summary['size'][0]}x{summary['size'][1]}・{summary['fps']}fps・{summary['seconds']:g} 秒・型 {summary['kind']}）",
             "構成: " + " → ".join(steps),
             f"寄り {summary['zoom']['close']:.2f}（上限 {caps}）・顔の切れ 最長 {max(edge.values()):.2f} 秒（目安 {EDGE_LIMIT_S:g}）"]
    lines += [f"注意: {w}" for w in summary["warnings"]]
    if summary.get("edited"):
        lines.append(f"手で直したファイルのまま: {'・'.join(summary['edited'])}")
    if summary.get("preview"):
        lines.append(f"下見: {_relative(summary['preview'], out)}")
    if summary.get("render"):
        lines.append(f"描画: {_relative(summary['render']['video'], out)}（technical_pass={summary['render']['technical_pass']}）")
    if summary.get("check"):
        from . import quick_check
        lines += quick_check.lines(summary["check"], _relative(summary["check"]["image"], out))
    lines.append(f"{PLAN}（変えるとよい所）・{LOG}（記録）は {out} の中")
    lines += [f"次: {command}" for command in summary["next"]]
    return "\n".join(lines)


def run(image, out, focus=None, path=None, impact=None, size=None, seconds=None, fps=None, zoom=None, impact_at=None,
        preview=False, render=False, force=False, progress=None, image_text=None, out_text=None, focus_radius=None, argv_text=None):
    """一式を書き（か、同じ引数なら今のファイルを使い）、頼まれたら下見と本番を描く。本番の後は確認の画像と要約（quick_check）も
    作る。実行のたびに OUT/log.md へ記録を足す。要約（dict）を返す（短い文は summary_text）。"""
    import time

    from PIL import Image

    began = time.monotonic()
    image, out = Path(image), Path(out)
    if not image.is_file():
        raise PipelineError(f"画像が見つかりません: {image}")
    extension = EXTENSIONS.get(image.suffix.lower())
    if extension is None:
        raise PipelineError(f"画像は {'・'.join(sorted(set(EXTENSIONS)))} にしてください: {image.name}")
    try:
        with Image.open(image) as handle:
            image_size = handle.size
    except Exception as error:       # Pillow は形式ごとに別の例外を投げる
        raise PipelineError(f"画像として読めません: {image.name}: {error}") from error
    out.mkdir(parents=True, exist_ok=True)
    image_digest = sha256(image)
    record_path = out / RECORD
    record = read_json(record_path) if record_path.is_file() else None
    same_image = record is not None and record.get("image_sha256") == image_digest
    # 引数は前の呼び出しの値を引き継ぎ、書いたものだけ置き換える（点は "none" で外す。山場を外せば山場の秒も外す）。絵が違えば引き継がない
    args = dict(record.get("args", {})) if same_image else {}
    given = {"focus": focus, "path": path, "impact": impact, "size": size, "seconds": seconds, "fps": fps, "zoom": zoom,
             "impact_at": impact_at, "focus_radius": focus_radius}
    for name, value in given.items():
        if value is None:
            continue
        if value == CLEAR:
            args.pop(name, None)
            if name == "impact":
                args.pop("impact_at", None)
        else:
            args[name] = [list(v) for v in value] if name == "path" else list(value) if isinstance(value, tuple) else value
    if same_image and args == record.get("args") and not force:
        made = "kept"
    else:
        stray = [name for name in (SPEC, TIMELINE, HOOKS) if (out / name).is_file()] if record is None else _edited(out, record)
        if stray and not force:
            what = "手で直したファイル" if record is not None else "mv fx quick の記録の無いファイル"
            raise PipelineError(f"{out} に{what}があります（{'・'.join(stray)}）。今のファイルで描くなら点や大きさの指定を外して"
                                f"呼び直し、作り直すなら --force を付けてください")
        made = "written"
    point = lambda name: None if args.get(name) is None else tuple(args[name])  # noqa: E731
    line = None if args.get("path") is None else (tuple(args["path"][0]), tuple(args["path"][1]))
    out_size = tuple(args["size"]) if args.get("size") else default_size(image_size)
    out_fps = int(args.get("fps") or DEFAULT_FPS)
    layout = compose(image_size, out_size, float(args.get("seconds") or DEFAULT_SECONDS), point("focus"), line, point("impact"),
                     args.get("zoom"), args.get("impact_at"),
                     FOCUS_RADIUS if args.get("focus_radius") is None else float(args["focus_radius"]), out_fps)
    base = "image" + extension
    if made == "written":
        if not (out / base).is_file() or sha256(out / base) != image_digest:
            shutil.copyfile(image, out / base)
        gray = _image_gray(image)
        luma = view_luma(gray, layout["hit_view"] or [0.0, 0.0, 1.0, 1.0])
        values = hooks_values(layout, image_size, out_size, luma)
        texts = {SPEC: json.dumps(spec_data(layout, base, out_size, out_fps, image_size), ensure_ascii=False, indent=2) + "\n",
                 TIMELINE: json.dumps(timeline_data(layout, out_size, out_fps), ensure_ascii=False, indent=2) + "\n",
                 HOOKS: hooks_source(layout, image_size, out_size, luma),
                 PLAN: plan_text(layout, image_size, out_size, out_fps, (image_text or str(image), out_text or str(out)), base,
                                 values)}
        for name, text in texts.items():
            temporary = out / (name + ".tmp")
            temporary.write_text(text, encoding="utf-8", newline="\n")
            temporary.replace(out / name)
        atomic_json(record_path, {"schema_version": RECORD_SCHEMA, "image_sha256": image_digest, "args": args,
                                  "files": {name: _digest(text) for name, text in texts.items()}})
    from . import sequence
    sequence.load_timeline(out / TIMELINE)          # 書いた（か手で直した）一式が読めることを先に確かめる
    summary = {"out": str(out), "made": made, "args": args, "files": [SPEC, TIMELINE, HOOKS, PLAN], "size": list(out_size), "fps": out_fps,
               "seconds": layout["seconds"], "kind": "山場あり" if layout["impact"] is not None else "山場なし（静かな型）",
               "phases": layout["phases"],
               "zoom": {"close": round(layout["close"], 3), "magnification": layout["magnification"], "limit": MAX_MAGNIFICATION},
               "caps": [[list(s), round(z, 2)] for s, z in zoom_caps(image_size, out_size)],
               "edge": {"seconds": layout["edge"], "limit": EDGE_LIMIT_S, "focus_radius": layout["focus_radius"]},
               **({"adjusted": layout["adjusted"]} if layout.get("adjusted") else {}),
               "warnings": layout["warnings"]}
    if made == "kept" and _edited(out, record):
        summary["edited"] = _edited(out, record)
    image_arg, out_arg = image_text or str(image), out_text or str(out)
    summary["next"] = [f"uv run mv fx quick {image_arg} {out_arg} --render" + ("" if preview else "（下見だけなら --preview）")]
    if preview:
        summary["preview"] = write_preview(out, layout, out_size, out_fps, progress)["path"]
    if render:
        from ..gate import flashes
        from . import quick_check
        result = sequence.render_run(out / TIMELINE, None, 0.0, None, 1.0, False, _render_progress(progress), False, out / HOOKS)
        video = Path(result["video"])
        atomic_json(out / INSPECT, inspect_data(layout, _relative(video, out), out_fps))
        summary["render"] = {"video": result["video"], "technical_pass": result["technical_pass"], "reused": result["reused"],
                             "seconds": result.get("seconds"), "warnings": result["warnings"]}
        report, _ = flashes.run(video)                 # mv gate flashes と同じ（報告は動画の横の *_flash_report）
        check_layout = dict(layout, keys=_shot_keys(out, layout))
        if check_layout["keys"] != layout["keys"]:   # shot.json を手で直したときは、そのカメラで数え直す
            check_layout["edge"] = edge_times(check_layout, image_size, out_size, out_fps)
        summary["check"] = quick_check.run(out, check_layout, image, image_size, out_size, out_fps, video, report)
        summary["next"] = [f"{quick_check.CHECK_IMAGE} を見る（直すなら {PLAN} の表のとおり引数を変えて --render。"
                           f"任意: uv run mv inspect check {out_arg}/{INSPECT} --brief）"]
    summary["elapsed_s"] = round(time.monotonic() - began, 1)
    append_log(out, summary, argv_text, summary["elapsed_s"])
    return summary


def _shot_keys(out, layout):
    """OUT/shot.json のカメラのキー（読めなければ layout のキー）。"""
    try:
        return read_json(Path(out) / SPEC)["camera"]["keys"]
    except (OSError, KeyError, ValueError, PipelineError):
        return layout["keys"]
