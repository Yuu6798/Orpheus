"""look: 最終フレームの色調整（grade）・周辺減光（vignette）・粒状感（grain）。

実制作レンダラと**同じ式・同じ順番・同じ既定値**で、表示用の値（ガンマ符号化した 0..1）の上で掛ける。線形光ではない:
実制作の設定値（grade_keys）をそのまま移せることを優先した、見た目の仕上げの層。順番:

    合成済みの線形 RGB（光・レイヤー・ソフトニーまで済み）→ float の sRGB 符号化（color.to_srgb_float）
    → grade（彩度 → RGB ゲイン → クリップ → S 字）→ ×255 → vignette（乗算）→ grain（加算）
    → [0, 255] に切って uint8（**切り捨て**。実制作と同じ）

- 全 4 版に同じ関数を掛ける（版で分岐しない・版を seed に混ぜない）。画素ごとの純関数で、近傍も画面統計も見ない
  ので、「受光マスク外の画素は light 無しとビット一致」の保証は look があっても保たれる。
- 周辺減光と粒状感は**出力画面基準**（cover の切り出しやカメラに引きずられない）。形は prepare で 1 回だけ作り、
  フレームごとの仕事は読むだけ（Look は不変。frame は乱数を引かず、ctx も linear も書き換えない）。
- spec に look が無ければ呼ばれない（render.frame は従来の to_srgb8 の経路を通り、出力は従来とバイト単位で同じ）。

評価時刻 τ = 時刻 t + grade.time_offset_s。grade のキーの t と、grain のコマ番号 round(τ·fps) は τ で数える
（実制作の grade_keys は曲の絶対秒なので、区間の曲上の開始秒を time_offset_s に入れるとそのまま移せる）。
"""
from dataclasses import dataclass

import numpy as np

from . import timeline
from .color import to_srgb_float

LUMA = (0.299, 0.587, 0.114)    # Rec.601。彩度の基準は各画素自身の輝度
_LUMA32 = tuple(np.float32(w) for w in LUMA)
_BAND_PIXELS = 1 << 15          # 帯 1 枚の画素数の目安（行数 = これ // 幅）


@dataclass
class Look:
    """prepare が作る不変データ。grade は正規化済み spec の辞書（keys・blend_s・time_offset_s）、なければ None。
    vignette = float32 (H, W, 1) の乗算ゲイン、grain = float32 (H, W, 1) の list（frames 枚）。それぞれ無効なら None。"""
    grade: object
    vignette: object
    grain: object
    fps: float
    time_offset_s: float


def vignette_map(size, params):
    """周辺減光の乗算ゲイン float32 (H, W, 1)。size = (W, H)、params = amount・start・power・floor。

    r = √(((x−W/2)/(W/2))² + ((y−H/2)/(H/2))²)（各軸を自軸の半幅で割った楕円距離。画素は整数座標のまま。
    辺の中点が 1、角が √2）、ゲイン = clip(1 − amount·max(r−start, 0)^power, floor, 1)。3ch に同じ値を掛ける。
    """
    width, height = size
    xx = np.arange(width, dtype=np.float32)[None, :]
    yy = np.arange(height, dtype=np.float32)[:, None]
    r = np.sqrt(((xx - width / 2) / (width / 2)) ** 2 + ((yy - height / 2) / (height / 2)) ** 2)
    gain = np.clip(1.0 - params["amount"] * np.clip(r - params["start"], 0, None) ** params["power"],
                   params["floor"], 1.0)
    return gain[..., None].astype(np.float32)


def grain_frames(size, params):
    """粒状感のパターン frames 枚（float32 (H, W, 1)、単位は 0..255）。

    default_rng(seed) から (H//size_px, W//size_px, 1) の正規乱数（平均 0・標準偏差 sigma）を順に引き、各軸 size_px 倍の
    最近傍反復で H×W に広げる（割り切れない端数は端の行・列を反復して埋める）。1ch なので R・G・B に同じ値が乗る
    （モノクロ。輝度依存なし）。seed は look.grain.seed だけで決まり、版にも spec の seed にも依らない。
    """
    width, height = size
    step = params["size_px"]
    rng = np.random.default_rng(params["seed"])
    patterns = []
    for _ in range(params["frames"]):
        small = rng.normal(0, params["sigma"], (max(1, height // step), max(1, width // step), 1)).astype(np.float32)
        big = small.repeat(step, 0).repeat(step, 1)[:height, :width]
        short_y, short_x = height - big.shape[0], width - big.shape[1]
        if short_y or short_x:
            big = np.pad(big, ((0, short_y), (0, short_x), (0, 0)), mode="edge")
        patterns.append(np.ascontiguousarray(big))
    return patterns


def prepare(look, size, fps):
    """正規化済みの look（spec.data["look"]）から Look を作る。描画サイズごとに 1 回。"""
    grade, vignette, grain = look["grade"], look["vignette"], look["grain"]
    return Look(grade=grade,
                vignette=vignette_map(size, vignette) if vignette is not None and vignette["amount"] > 0 else None,
                grain=grain_frames(size, grain) if grain is not None and grain["sigma"] > 0 else None,
                fps=float(fps), time_offset_s=grade["time_offset_s"] if grade is not None else 0.0)


def grade_params(grade, tau):
    """評価時刻 tau の (sat, gain [r, g, b], con)。

    - tau が最初のキーより前なら最初のキー、最後のキー以降は最後のキー（外挿しない。
      実制作は最初のキーより前だと最後のキーになる癖があるが、ここは直してある）。
    - 区間 [a, b) は a を保持し、b の時刻の blend_s 秒前から b へ移る: f = smoothstep(clamp((tau − (b.t − blend_s))/blend_s))。
      sat・gain の各 ch・con を同じ f で線形補間する。b の時刻ちょうどに b の値になる。blend_s = 0 なら段差。
    - キー間隔が blend_s より短いと区間の頭で f > 0 になり、前の区間の終わりと段差ができる（spec 検証が警告する）。
    """
    keys = grade["keys"]
    if tau < keys[0]["t"]:
        a = b = keys[0]
        f = 0.0
    elif tau >= keys[-1]["t"]:
        a = b = keys[-1]
        f = 0.0
    else:
        a, b = next((a, b) for a, b in zip(keys, keys[1:]) if a["t"] <= tau < b["t"])
        blend = grade["blend_s"]
        f = timeline.ease("in_out", (tau - (b["t"] - blend)) / blend) if blend > 0 else 0.0
    return (a["sat"] + (b["sat"] - a["sat"]) * f,
            [x + (y - x) * f for x, y in zip(a["gain"], b["gain"])],
            a["con"] + (b["con"] - a["con"]) * f)


def _grade(x, sat, gain, con):
    """x（float32 (H, W, 3)、0..1）に彩度 → RGB ゲイン → クリップ → S 字を掛けた新しい配列（0..1）。x は変更しない。

    lum = Rec.601 の重み和、x = lum + (x − lum)·sat、x = clip(x·gain, 0, 1)、x = x·x·(3 − 2x)·con + x·(1 − con)。
    sat > 1 で 0..1 の外に出た値は、ゲインの直後のクリップで初めて切る。con = 0 で恒等、1 で完全な smoothstep。
    """
    lum = x[..., 0:1] * _LUMA32[0] + x[..., 1:2] * _LUMA32[1] + x[..., 2:3] * _LUMA32[2]
    y = x - lum
    y *= sat
    y += lum
    y *= np.asarray(gain, dtype=np.float32)
    np.clip(y, 0.0, 1.0, out=y)
    curve = y * y
    ramp = y * 2.0
    np.subtract(3.0, ramp, out=ramp)
    curve *= ramp
    curve *= con
    y *= 1.0 - con
    y += curve
    return y


def _band(look, x, y0, y1, grade, grain):
    """行 y0:y1 の表示用の値 x（float32 (h, W, 3)、0..1）に grade → ×255 → vignette → grain を掛け、切り捨てた uint8 を返す。
    x は変更しない。"""
    if grade is not None:
        out = _grade(x, *grade)
        out *= 255.0
    else:
        out = x * 255.0
    if look.vignette is not None:
        out *= look.vignette[y0:y1]
    if grain is not None:
        out += grain[y0:y1]
    np.clip(out, 0.0, 255.0, out=out)
    return out.astype(np.uint8)


def _run(look, source, t, encode):
    """source（(H, W, 3)）を行の帯ごとに encode で表示用の float32 にして look を掛け、uint8 (H, W, 3) にする。

    どの段も画素ごとの演算なので、帯に分けても結果は一括と bit 単位で同じ。帯に分けるのは、25 MB 級の一時配列を
    何枚も作らず（メモリ）、キャッシュに載る大きさで一連の演算を回す（速度。1080p で 3 割ほど速い）ため。
    grade の補間とコマ番号はフレームごとに 1 回だけ決める。
    """
    tau = t + look.time_offset_s
    grade = grade_params(look.grade, tau) if look.grade is not None else None
    grain = look.grain[round(tau * look.fps) % len(look.grain)] if look.grain is not None else None
    height, width = source.shape[:2]
    rows = max(1, _BAND_PIXELS // width)
    out = np.empty(source.shape[:2] + (3,), dtype=np.uint8)
    for y0 in range(0, height, rows):
        y1 = min(height, y0 + rows)
        out[y0:y1] = _band(look, encode(source[y0:y1]), y0, y1, grade, grain)
    return out


def finish(look, display, t):
    """表示用の値 display（float32 (H, W, 3)、ガンマ符号化・0..1）に look を掛けて uint8 (H, W, 3) を返す。

    grade → ×255 → vignette（乗算）→ grain（加算、コマは round(τ·fps) % frames）→ 切り捨て。display は変更しない。
    """
    return _run(look, display, t, lambda band: np.asarray(band, dtype=np.float32))


def apply(look, linear, t):
    """時刻 t の線形 RGB（float32 (H, W, 3)）に look を掛けて uint8 (H, W, 3) を返す（sRGB 符号化 → finish と同じ）。

    linear は変更しない（静止カメラ・光なしの linear は ctx.assets.plate そのものでありうるので、絶対にその場で書き換えない）。
    """
    return _run(look, linear, t, to_srgb_float)
