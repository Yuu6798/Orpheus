"""fx の look（色調整・周辺減光・粒状感）のテスト。

参照実装（legacy_*）は、実制作レンダラの式を fx の実装を呼ばずに**独立に**書き写したもの（式・順番・既定値。合成フレームで
fx の出力と突き合わせる）。spec の検証と look.py の純関数は cv2 なしで走り、render / CLI を通す層は opencv が前提
（`--extra fx` で同期していない環境ではその層だけ skip される）。
"""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from fx_synth import FULL, make_project, run_cli, spec_dict
from mv_pipeline import fx
from mv_pipeline.fx import color
from mv_pipeline.fx import look as fxlook
from mv_pipeline.fx import spec as fxspec
from mv_pipeline.io import PipelineError, read_json

KEYS = [[0.0, 0.85, [0.95, 1.0, 1.07], 0.12],
        {"t": 3.0, "sat": 1.12, "gain": [1.06, 0.94, 1.08], "con": 0.18},
        [6.0, 0.55, [0.98, 0.98, 1.0], 0.10]]
LOOK = {"grade": {"keys": KEYS, "blend_s": 1.2, "time_offset_s": 0.0},
        "vignette": {"amount": 0.38, "start": 0.55, "power": 1.6, "floor": 0.55},
        "grain": {"sigma": 2.0, "size_px": 2, "frames": 6, "seed": 7}}
LEGACY_KEYS = [[0.0, 0.85, [0.95, 1.0, 1.07], 0.12], [3.0, 1.12, [1.06, 0.94, 1.08], 0.18],
               [6.0, 0.55, [0.98, 0.98, 1.0], 0.10]]                       # KEYS を実制作の配列形だけで書いたもの
SHORT_LOOK = {"grade": {"keys": [[0.0, 0.85, [0.95, 1.0, 1.07], 0.12],
                                 {"t": 1.5, "sat": 1.12, "gain": [1.06, 0.94, 1.08], "con": 0.18}], "blend_s": 1.0},
              "vignette": {}, "grain": {}}


def parse(tmp_path, data):
    return fxspec.parse_spec(data, tmp_path)


def look_of(tmp_path, look):
    return parse(tmp_path, spec_dict(look=look)).data["look"]


# ---- 実制作の式を独立に書き写した参照実装 --------------------------------------------

LUMW = np.array([0.299, 0.587, 0.114], np.float32)


def legacy_ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def legacy_grade_at(keys, blend, t):
    """keys = 実制作の配列形 [[t, sat, [r,g,b], con], ...]。区間 [a, b) は b の blend 秒前から b へ smoothstep。
    最初のキーより前は最初のキー（実制作の癖を直した点）、最後のキー以降は最後のキー。"""
    if t < keys[0][0]:
        return keys[0][1], np.array(keys[0][2]), keys[0][3]
    for j in range(len(keys) - 1):
        a, b = keys[j], keys[j + 1]
        if a[0] <= t < b[0]:
            f = legacy_ease((t - (b[0] - blend)) / blend)
            return (a[1] + (b[1] - a[1]) * f, np.array(a[2]) + (np.array(b[2]) - np.array(a[2])) * f,
                    a[3] + (b[3] - a[3]) * f)
    return keys[-1][1], np.array(keys[-1][2]), keys[-1][3]


def legacy_apply_grade(f, sat, gain, con):
    """f = 0..255 の float32 RGB。"""
    x = f / 255.0
    lum = (x * LUMW).sum(-1, keepdims=True)
    x = lum + (x - lum) * sat
    x = np.clip(x * gain.astype(np.float32), 0, 1)
    x = x * x * (3 - 2 * x) * con + x * (1 - con)
    return x * 255.0


def legacy_vig(W, H, amount=0.38, start=0.55, power=1.6, floor=0.55):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    return np.clip(1.0 - amount * np.clip(r - start, 0, None) ** power, floor, 1.0)[..., None].astype(np.float32)


def legacy_grain(W, H, seed=7, sigma=2.0, size=2, frames=6):
    rng = np.random.default_rng(seed)
    return [rng.normal(0, sigma, (H // size, W // size, 1)).astype(np.float32).repeat(size, 0).repeat(size, 1)[:H, :W]
            for _ in range(frames)]


def legacy_frame(f, t, keys, blend, vig, grain, fps, offset=0.0):
    """f = 0..255 の float32 (H, W, 3)。実制作の `apply_grade(...) * VIG + GRAIN[n % len]` と切り捨て。"""
    tau = t + offset
    out = legacy_apply_grade(f, *legacy_grade_at(keys, blend, tau)) * vig + grain[round(tau * fps) % len(grain)]
    return np.clip(out, 0, 255).astype(np.uint8)


def prepared_look(tmp_path, look, size, fps):
    return fxlook.prepare(look_of(tmp_path, look), size, fps)


def random_frame(w, h, seed=3):
    """0..255 の float32 RGB（実制作が受け取る形）と、それを /255 した表示用の値。"""
    f = np.random.default_rng(seed).random((h, w, 3), dtype=np.float32) * np.float32(255.0)
    return f, f / 255.0


# ---- 実制作との一致 ---------------------------------------------------------------

@pytest.mark.parametrize("t", [-1.0, 0.0, 1.9, 2.4, 3.0, 4.9, 5.5, 7.0])
@pytest.mark.parametrize("band_pixels", [1 << 15, 160 * 7])        # 一括と、帯に分けた計算（行の端数あり）
def test_finish_matches_the_production_formulas_exactly(tmp_path, monkeypatch, t, band_pixels):
    monkeypatch.setattr(fxlook, "_BAND_PIXELS", band_pixels)
    w, h, fps = 160, 90, 24.0
    f, display = random_frame(w, h)
    got = fxlook.finish(prepared_look(tmp_path, LOOK, (w, h), fps), display, t)
    want = legacy_frame(f, t, LEGACY_KEYS, 1.2, legacy_vig(w, h), legacy_grain(w, h), fps)
    assert got.dtype == np.uint8 and got.shape == (h, w, 3)
    assert np.array_equal(got, want)


def test_finish_matches_with_non_default_values_and_a_time_offset(tmp_path):
    """縦画面向けの値（周辺減光 0.34・下限 0.58、粒状感 σ1.8）、blend_s=0.6、grain の seed・枚数・粒の大きさ、time_offset_s。"""
    w, h, fps = 160, 96, 30.0                       # 実制作の粒は割り切れる寸法でしか組めないので、size_px=4 で割り切れる高さ
    f, display = random_frame(w, h, seed=11)
    look = {"grade": {"keys": [[100.0, 1.0, [1.04, 1.0, 0.98], 0.10], [110.0, 0.55, [0.98, 0.98, 1.0], 0.10]],
                      "blend_s": 0.6, "time_offset_s": 100.0},
            "vignette": {"amount": 0.34, "start": 0.55, "power": 1.6, "floor": 0.58},
            "grain": {"sigma": 1.8, "size_px": 4, "frames": 5, "seed": 12}}
    keys = [[100.0, 1.0, [1.04, 1.0, 0.98], 0.10], [110.0, 0.55, [0.98, 0.98, 1.0], 0.10]]
    state = prepared_look(tmp_path, look, (w, h), fps)
    for t in (0.0, 3.3, 9.5, 9.8, 12.0):
        want = legacy_frame(f, t, keys, 0.6, legacy_vig(w, h, 0.34, 0.55, 1.6, 0.58),
                            legacy_grain(w, h, 12, 1.8, 4, 5), fps, offset=100.0)
        assert np.array_equal(fxlook.finish(state, display, t), want), t


def test_apply_from_linear_matches_the_production_formulas_within_one_level(tmp_path):
    """線形 → float の sRGB 符号化 → look の全経路。符号化の丸め誤差(≈1e-7)で切り捨ての境目の画素が 1 だけずれうる。"""
    w, h, fps = 160, 90, 24.0
    u8 = np.random.default_rng(5).integers(0, 256, (h, w, 3), dtype=np.uint8)
    state = prepared_look(tmp_path, LOOK, (w, h), fps)
    for t in (0.5, 2.4, 7.0):
        want = legacy_frame(u8.astype(np.float32), t, LEGACY_KEYS, 1.2, legacy_vig(w, h), legacy_grain(w, h), fps)
        got = fxlook.apply(state, color.to_linear(u8), t)
        diff = np.abs(got.astype(int) - want.astype(int))
        assert diff.max() <= 1 and (diff > 0).mean() < 0.02, (t, diff.max(), (diff > 0).mean())


def test_only_the_present_children_are_applied(tmp_path):
    w, h, fps = 160, 90, 24.0
    _, display = random_frame(w, h, seed=2)
    scaled, vig, grain = display * np.float32(255.0), legacy_vig(w, h), legacy_grain(w, h)
    only_vig = fxlook.finish(prepared_look(tmp_path, {"vignette": {}}, (w, h), fps), display, 0.0)
    assert np.array_equal(only_vig, np.clip(scaled * vig, 0, 255).astype(np.uint8))
    only_grain = fxlook.finish(prepared_look(tmp_path, {"grain": {}}, (w, h), fps), display, 0.0)
    assert np.array_equal(only_grain, np.clip(scaled + grain[0], 0, 255).astype(np.uint8))
    none = fxlook.finish(prepared_look(tmp_path, {}, (w, h), fps), display, 0.0)
    assert np.array_equal(none, np.clip(scaled, 0, 255).astype(np.uint8))                          # 丸めず切り捨て


def test_finish_never_modifies_its_input(tmp_path):
    w, h = 160, 90
    _, display = random_frame(w, h)
    before = display.copy()
    state = prepared_look(tmp_path, LOOK, (w, h), 24.0)
    fxlook.finish(state, display, 1.0)
    fxlook.apply(state, display, 1.0)
    assert np.array_equal(display, before)


# ---- grade: キーの補間 ------------------------------------------------------------

def grade_at(tmp_path, keys, tau, blend_s=1.2):
    grade = look_of(tmp_path, {"grade": {"keys": keys, "blend_s": blend_s}})["grade"]
    return fxlook.grade_params(grade, tau)


def test_key_blend_toward_62_4_follows_the_production_example(tmp_path):
    """キー 62.4 に向かう例: 61.2 で f=0、61.5 で 0.156、61.8 で 0.5、62.1 で 0.844、62.4 で 1（sat 1→2、gain 1→3、con 0→1 で f を読む）。"""
    keys = [[0.0, 1.0, [1, 1, 1], 0.0], [62.4, 2.0, [3, 3, 3], 1.0]]
    for tau, f in ((61.2, 0.0), (61.5, 0.15625), (61.8, 0.5), (62.1, 0.84375), (62.4, 1.0), (100.0, 1.0)):
        sat, gain, con = grade_at(tmp_path, keys, tau)
        assert sat == pytest.approx(1 + f, abs=1e-9) and con == pytest.approx(f, abs=1e-9), tau
        assert gain == pytest.approx([1 + 2 * f] * 3, abs=1e-9), tau
    assert grade_at(tmp_path, keys, 30.0) == (1.0, [1.0, 1.0, 1.0], 0.0)        # 区間の頭は保持


def test_blend_is_continuous_at_each_key_and_holds_the_interval_start(tmp_path):
    keys = [[0.0, 1.0, [1, 1, 1], 0.0], [10.0, 2.0, [2, 2, 2], 0.5], [20.0, 0.5, [1, 1, 1], 0.2]]
    assert grade_at(tmp_path, keys, 10.0 - 1e-9)[0] == pytest.approx(2.0, abs=1e-6)       # b の直前に b へ届く
    assert grade_at(tmp_path, keys, 10.0)[0] == 2.0                                        # b ちょうどで b（次の区間の頭）
    assert grade_at(tmp_path, keys, 15.0)[0] == 2.0                                        # 保持
    assert grade_at(tmp_path, keys, 19.4)[0] == pytest.approx(1.25, abs=1e-9)             # 20 の 0.6 秒前 = 半分
    assert grade_at(tmp_path, keys, 20.0)[0] == 0.5 and grade_at(tmp_path, keys, 99.0)[0] == 0.5


def test_before_the_first_key_is_the_first_key_not_the_last(tmp_path):
    keys = [[10.0, 0.4, [1, 1, 1], 0.1], [20.0, 2.0, [2, 2, 2], 0.9]]
    assert grade_at(tmp_path, keys, 0.0) == (0.4, [1.0, 1.0, 1.0], 0.1)
    assert grade_at(tmp_path, keys, 9.99)[0] == 0.4
    assert grade_at(tmp_path, [[10.0, 0.4, [1, 1, 1], 0.1]], 50.0)[0] == 0.4              # キー 1 個は常にそれ


def test_zero_blend_is_a_step_at_the_next_key(tmp_path):
    keys = [[0.0, 1.0, [1, 1, 1], 0.0], [5.0, 2.0, [1, 1, 1], 0.0]]
    assert grade_at(tmp_path, keys, 4.999, blend_s=0)[0] == 1.0
    assert grade_at(tmp_path, keys, 5.0, blend_s=0)[0] == 2.0


def test_time_offset_shifts_the_evaluation_time(tmp_path):
    w, h = 160, 90
    _, display = random_frame(w, h, seed=8)
    keys = [[0.0, 0.5, [1, 1, 1], 0.0], [100.0, 1.5, [1.2, 1, 0.8], 0.4]]
    shifted = prepared_look(tmp_path, {"grade": {"keys": keys, "time_offset_s": 99.0}, "grain": {}}, (w, h), 24.0)
    plain = prepared_look(tmp_path, {"grade": {"keys": keys}, "grain": {}}, (w, h), 24.0)
    assert np.array_equal(fxlook.finish(shifted, display, 0.5), fxlook.finish(plain, display, 99.5))
    assert not np.array_equal(fxlook.finish(shifted, display, 0.5), fxlook.finish(plain, display, 0.5))


def test_grade_is_neutral_for_neutral_keys_and_con_is_an_s_curve():
    keys = [{"t": 0.0, "sat": 1.0, "gain": [1.0, 1.0, 1.0], "con": 0.0}]
    x = np.linspace(0, 1, 11, dtype=np.float32)[None, :, None] * np.ones((1, 1, 3), np.float32)
    neutral = fxlook._grade(x, 1.0, keys[0]["gain"], 0.0)
    assert np.allclose(neutral, x, atol=5e-7)
    full = fxlook._grade(x, 1.0, [1.0, 1.0, 1.0], 1.0)
    assert np.allclose(full, x * x * (3 - 2 * x), atol=1e-6) and full[0, 5, 0] == pytest.approx(0.5, abs=1e-6)
    assert np.allclose(fxlook._grade(x, 0.0, [1, 1, 1], 0.0)[..., 0], x[..., 0], atol=1e-6)    # 無彩色の入力は彩度 0 でも同じ


# ---- vignette ---------------------------------------------------------------------

DEFAULT_VIG = {"amount": 0.38, "start": 0.55, "power": 1.6, "floor": 0.55}


def test_vignette_values_at_known_distances():
    vig = fxlook.vignette_map((160, 90), DEFAULT_VIG)
    assert vig.shape == (90, 160, 1) and vig.dtype == np.float32
    assert vig[45, 0, 0] == pytest.approx(0.894, abs=1e-3)               # 左辺の中点 r=1.0
    assert vig[0, 0, 0] == pytest.approx(0.699, abs=1e-3)                # 角 r=√2
    assert vig[45, 80, 0] == 1.0 and vig[45, 40, 0] == 1.0               # 中心と r=0.5 (< start) は減光なし
    assert vig[0, 80, 0] == pytest.approx(0.894, abs=1e-3)               # 上辺の中点も r=1.0（各軸を自軸の半幅で割る楕円）
    assert vig.min() >= 0.55 and vig.max() <= 1.0


def test_vignette_floor_start_and_power_apply():
    floored = fxlook.vignette_map((160, 90), {"amount": 2.0, "start": 0.0, "power": 1.0, "floor": 0.8})
    assert floored.min() == pytest.approx(0.8) and floored[0, 0, 0] == pytest.approx(0.8)
    assert floored[45, 80, 0] == 1.0                                      # 中心は r=0 で減光なし
    late = fxlook.vignette_map((160, 90), {"amount": 0.38, "start": 1.2, "power": 1.6, "floor": 0.55})
    assert late[45, 0, 0] == 1.0 and late[0, 0, 0] < 1.0                  # 辺の中点(r=1)はまだ減光前、角(√2)は減光中
    gentle = fxlook.vignette_map((160, 90), {**DEFAULT_VIG, "power": 0.5})
    assert gentle[0, 0, 0] < fxlook.vignette_map((160, 90), DEFAULT_VIG)[0, 0, 0]       # 1 未満の乗は同じ距離でより暗い


def test_vignette_follows_the_output_aspect_and_is_the_same_for_every_channel(tmp_path):
    wide, tall = fxlook.vignette_map((160, 90), DEFAULT_VIG), fxlook.vignette_map((90, 160), DEFAULT_VIG)
    assert tall[80, 0, 0] == pytest.approx(0.894, abs=1e-3) and tall[0, 0, 0] == pytest.approx(wide[0, 0, 0], abs=1e-6)
    flat = np.full((90, 160, 3), 0.5, np.float32)
    out = fxlook.finish(prepared_look(tmp_path, {"vignette": {}}, (160, 90), 24.0), flat, 0.0)
    assert (out[..., 0] == out[..., 1]).all() and (out[..., 1] == out[..., 2]).all()
    assert out[45, 80, 0] == 127 and out[0, 0, 0] == int(127.5 * 0.699)


# ---- grain ------------------------------------------------------------------------

GRAIN = {"sigma": 4.0, "size_px": 2, "frames": 6, "seed": 7}


def grain_out(tmp_path, n, params=GRAIN, fps=12.0, size=(160, 90)):
    flat = np.full((size[1], size[0], 3), 0.5, np.float32)
    return fxlook.finish(prepared_look(tmp_path, {"grain": params}, size, fps), flat, n / fps)


def test_grain_cycles_with_the_frame_number(tmp_path):
    state = prepared_look(tmp_path, {"grain": GRAIN}, (160, 90), 12.0)
    flat = np.full((90, 160, 3), 0.5, np.float32)
    frames = [fxlook.finish(state, flat, n / 12.0) for n in range(14)]
    for n in range(8):
        assert np.array_equal(frames[n], frames[n + 6]), n               # n と n+frames は同じ粒
    assert len({f.tobytes() for f in frames[:6]}) == 6                   # 6 枚はどれも違う
    assert not np.array_equal(frames[0], frames[1])


def test_grain_is_deterministic_monochrome_two_pixel_blocks_with_the_requested_sigma(tmp_path):
    a, b = grain_out(tmp_path, 3), grain_out(tmp_path, 3)
    assert np.array_equal(a, b)
    assert (a[..., 0] == a[..., 1]).all() and (a[..., 1] == a[..., 2]).all()          # 3ch 同値（モノクロ）
    assert (a[0::2, 0::2] == a[1::2, 0::2]).all() and (a[0::2, 0::2] == a[0::2, 1::2]).all() \
        and (a[0::2, 0::2] == a[1::2, 1::2]).all()                                     # 2px 角のブロック
    assert len(np.unique(a[..., 0])) > 5                                                 # 平坦な入力に粒が乗っている
    pattern = fxlook.grain_frames((160, 90), GRAIN)[3]
    assert pattern.shape == (90, 160, 1) and pattern.dtype == np.float32
    assert float(pattern.mean()) == pytest.approx(0.0, abs=0.3) and float(pattern.std()) == pytest.approx(4.0, rel=0.06)


def test_grain_size_and_remainder_fill_the_frame_by_repeating_the_edge():
    patterns = fxlook.grain_frames((160, 90), {**GRAIN, "size_px": 3, "frames": 2})        # 160 = 53 粒 × 3 + 1
    assert [p.shape for p in patterns] == [(90, 160, 1)] * 2
    p = patterns[0]
    assert (p[:, 156:159] == p[:, 156:157]).all() and (p[:, 159] == p[:, 158]).all()      # 端数の列は端の列の反復
    q = fxlook.grain_frames((100, 50), {**GRAIN, "size_px": 8, "frames": 1})[0]               # 100 = 12×8+4、50 = 6×8+2
    assert q.shape == (50, 100, 1) and (q[48:] == q[47]).all() and (q[:, 96:] == q[:, 95:96]).all()
    one = fxlook.grain_frames((160, 90), {**GRAIN, "size_px": 1, "frames": 1})[0]
    assert len(np.unique(one)) > 5000                                      # 1px はブロックにならない


def test_grain_depends_only_on_its_own_seed_and_sigma_zero_is_disabled(tmp_path):
    a = fxlook.grain_frames((160, 90), GRAIN)
    assert all(np.array_equal(x, y) for x, y in zip(a, fxlook.grain_frames((160, 90), GRAIN)))
    other = fxlook.grain_frames((160, 90), {**GRAIN, "seed": 8})
    assert not np.array_equal(a[0], other[0])
    state = prepared_look(tmp_path, {"grain": {**GRAIN, "sigma": 0.0}, "vignette": {"amount": 0.0}}, (160, 90), 12.0)
    assert state.grain is None and state.vignette is None


# ---- color: float の sRGB 符号化 ----------------------------------------------------

def test_to_srgb_float_is_the_exact_transfer_function_and_clips():
    u8 = np.arange(256, dtype=np.uint8)
    enc = color.to_srgb_float(color.to_linear(u8))
    assert enc.dtype == np.float32 and np.allclose(enc * 255.0, u8, atol=2e-3)
    assert np.array_equal(color.to_srgb_float(np.array([-1.0, 0.0, 1.0, 2.0], np.float32)), np.array([0, 0, 1, 1], np.float32))
    low = np.float32(0.002)
    assert color.to_srgb_float(np.array([low]))[0] == pytest.approx(0.002 * 12.92, rel=1e-6)          # 線形区間
    high = np.float32(0.2159)
    assert color.to_srgb_float(np.array([high]))[0] == pytest.approx(1.055 * 0.2159 ** (1 / 2.4) - 0.055, rel=1e-6)
    x = np.linspace(0, 1, 101, dtype=np.float32)
    assert (np.diff(color.to_srgb_float(x)) > 0).all()                                                  # 単調


def test_pure_white_and_black_survive_the_encode_and_the_truncation(tmp_path):
    """float32 で 1.055·1−0.055 は 0.99999994。純白が切り捨てで 254 に落ちないこと（端点は厳密）。"""
    state = prepared_look(tmp_path, {"grain": {"sigma": 0.0}}, (16, 16), 24.0)           # 他は無効 = 符号化と切り捨てだけ
    assert (fxlook.apply(state, np.ones((16, 16, 3), np.float32), 0.0) == 255).all()
    assert (fxlook.apply(state, np.zeros((16, 16, 3), np.float32), 0.0) == 0).all()
    assert (fxlook.apply(state, np.full((16, 16, 3), 5.0, np.float32), 0.0) == 255).all()          # 1 超は切る


# ---- spec: 正規化と検証 --------------------------------------------------------------

def test_look_is_absent_by_default_and_every_child_is_optional(tmp_path):
    assert parse(tmp_path, spec_dict()).data["look"] is None
    assert not [w for w in parse(tmp_path, spec_dict()).warnings if w.startswith("look")]
    only_vig = look_of(tmp_path, {"vignette": {}})
    assert only_vig == {"grade": None, "grain": None, "vignette": DEFAULT_VIG}
    only_grain = look_of(tmp_path, {"grain": {}})
    assert only_grain["grain"] == {"sigma": 2.0, "size_px": 2, "frames": 6, "seed": 7} and only_grain["grade"] is None
    grade = look_of(tmp_path, {"grade": {"keys": [[0, 1, [1, 1, 1], 0]]}})["grade"]
    assert grade["blend_s"] == 1.2 and grade["time_offset_s"] == 0.0


def test_array_and_object_key_forms_normalize_identically(tmp_path):
    arrays = [[0.0, 0.85, [0.95, 1.0, 1.07], 0.12], [3.0, 1.12, [1.06, 0.94, 1.08], 0.18]]
    objects = [{"t": 0.0, "sat": 0.85, "gain": [0.95, 1.0, 1.07], "con": 0.12},
               {"t": 3.0, "sat": 1.12, "gain": [1.06, 0.94, 1.08], "con": 0.18}]
    mixed = [arrays[0], objects[1]]
    forms = [parse(tmp_path, spec_dict(look={"grade": {"keys": k}})) for k in (arrays, objects, mixed)]
    assert len({fxspec.canonical_json(s.data) for s in forms}) == 1
    assert forms[0].data["look"]["grade"]["keys"][0] == {"t": 0.0, "sat": 0.85, "gain": [0.95, 1.0, 1.07], "con": 0.12}
    ints = parse(tmp_path, spec_dict(look={"grade": {"keys": [[0, 1, [1, 1, 1], 0], [3, 1, [2, 1, 1], 1]]}}))
    floats = parse(tmp_path, spec_dict(look={"grade": {"keys": [[0.0, 1.0, [1.0, 1.0, 1.0], 0.0], [3.0, 1.0, [2.0, 1.0, 1.0], 1.0]]}}))
    assert fxspec.canonical_json(ints.data) == fxspec.canonical_json(floats.data)        # 1 と 1.0 は同じ正規形・同じ key


def test_object_form_leaves_omitted_fields_neutral(tmp_path):
    key = look_of(tmp_path, {"grade": {"keys": [{"t": 2}]}})["grade"]["keys"][0]
    assert key == {"t": 2.0, "sat": 1.0, "gain": [1.0, 1.0, 1.0], "con": 0.0}


def test_integer_fields_accept_whole_floats_and_become_ints(tmp_path):
    whole = look_of(tmp_path, {"grain": {"size_px": 2.0, "frames": 6.0, "seed": 7.0}})["grain"]
    assert [type(whole[k]) for k in ("size_px", "frames", "seed")] == [int] * 3
    assert fxspec.canonical_json(whole) == fxspec.canonical_json(look_of(tmp_path, {"grain": {}})["grain"])
    for bad in ({"size_px": 2.5}, {"frames": 6.5}, {"seed": 7.5}):
        with pytest.raises(PipelineError, match="invalid"):
            look_of(tmp_path, {"grain": bad})


BAD_VALUES = [
    {"grade": {"keys": [[0, 3.1, [1, 1, 1], 0]]}}, {"grade": {"keys": [[0, -0.1, [1, 1, 1], 0]]}},
    {"grade": {"keys": [[0, 1, [4.1, 1, 1], 0]]}}, {"grade": {"keys": [[0, 1, [1, -0.1, 1], 0]]}},
    {"grade": {"keys": [[0, 1, [1, 1, 1], 1.1]]}}, {"grade": {"keys": [[0, 1, [1, 1, 1], -0.1]]}},
    {"grade": {"keys": [{"t": 0, "sat": 3.1}]}}, {"grade": {"keys": [{"t": 0, "gain": [1, 1, 4.1]}]}},
    {"grade": {"keys": [{"t": 0, "con": 2}]}}, {"grade": {"keys": [{"sat": 1}]}},
    {"grade": {"keys": [[0, 1, [1, 1], 0]]}}, {"grade": {"keys": [[0, 1, [1, 1, 1]]]}},
    {"grade": {"keys": [[0, 1, [1, 1, 1], 0, 9]]}}, {"grade": {"keys": ["red"]}},
    {"grade": {"keys": [[0, 1, [1, 1, 1], 0]], "blend_s": -0.1}}, {"grade": {"keys": [[0, 1, [1, 1, 1], 0]], "blend_s": 10.1}},
    {"grade": {"keys": []}}, {"grade": {}}, {"grade": {"keys": [[0, 1, [1, 1, 1], 0]], "time_offset_s": "10"}},
    {"vignette": {"amount": 2.1}}, {"vignette": {"amount": -0.1}}, {"vignette": {"start": 2.1}}, {"vignette": {"start": -1}},
    {"vignette": {"power": 0.05}}, {"vignette": {"power": 8.1}}, {"vignette": {"floor": 1.1}}, {"vignette": {"floor": -0.1}},
    {"grain": {"sigma": 50.1}}, {"grain": {"sigma": -1}}, {"grain": {"size_px": 0}}, {"grain": {"size_px": 9}},
    {"grain": {"frames": 0}}, {"grain": {"frames": 65}}, {"grain": {"seed": -1}},
    [], "night", {"grade": []}, {"vignette": 0.3},
]


@pytest.mark.parametrize("bad", BAD_VALUES, ids=[json.dumps(b)[:70] for b in BAD_VALUES])
def test_out_of_range_and_malformed_look_is_rejected(tmp_path, bad):
    with pytest.raises(PipelineError, match="invalid"):
        look_of(tmp_path, bad)


def test_range_edges_are_accepted(tmp_path):
    ok = {"grade": {"keys": [[-5, 0, [0, 0, 0], 0], [5, 3, [4, 4, 4], 1]], "blend_s": 10, "time_offset_s": -1e6},
          "vignette": {"amount": 2, "start": 2, "power": 8, "floor": 1},
          "grain": {"sigma": 50, "size_px": 8, "frames": 64, "seed": 0}}
    assert look_of(tmp_path, ok)["grade"]["time_offset_s"] == -1e6
    assert look_of(tmp_path, {"vignette": {"amount": 0, "power": 0.1, "floor": 0}, "grain": {"sigma": 0, "size_px": 1, "frames": 1}})


def test_keys_must_ascend_strictly(tmp_path):
    for keys in ([[3, 1, [1, 1, 1], 0], [1, 1, [1, 1, 1], 0]], [[1, 1, [1, 1, 1], 0], [1, 1, [1, 1, 1], 0]],
                 [{"t": 0}, {"t": 5}, {"t": 4}]):
        with pytest.raises(PipelineError, match="look.grade.keys"):
            look_of(tmp_path, {"grade": {"keys": keys}})


def test_unknown_keys_are_rejected_at_every_level_of_look(tmp_path):
    cases = [{"colour": {}}, {"grade": {"keys": [[0, 1, [1, 1, 1], 0]], "blend": 1}},
             {"grade": {"keys": [{"t": 0, "satt": 1}]}}, {"vignette": {"strength": 0.3}}, {"grain": {"sigmaa": 2}}]
    for look in cases:
        with pytest.raises(PipelineError, match="未知の鍵"):
            look_of(tmp_path, look)


def test_x_prefixed_keys_are_allowed_everywhere_in_look_and_kept(tmp_path):
    look = {"x_note": "夜", "grade": {"x_src": "a", "keys": [{"t": 0, "x_beat": 4}]}, "vignette": {"x_v": 1}, "grain": {"x_g": [1]}}
    data = look_of(tmp_path, look)
    assert data["x_note"] == "夜" and data["grade"]["x_src"] == "a" and data["grade"]["keys"][0]["x_beat"] == 4
    assert data["vignette"]["x_v"] == 1 and data["grain"]["x_g"] == [1]


def test_look_warnings_for_dead_or_stepping_settings(tmp_path):
    empty = parse(tmp_path, spec_dict(look={}))
    assert any(w.startswith("look") and "何も変わりません" in w for w in empty.warnings)
    stepping = parse(tmp_path, spec_dict(look={"grade": {"keys": [[0, 1, [1, 1, 1], 0], [1, 1, [1, 1, 1], 0], [9, 1, [1, 1, 1], 0]]}}))
    assert len([w for w in stepping.warnings if w.startswith("look.grade")]) == 1 and "段差" in stepping.warnings[-1]
    clean = parse(tmp_path, spec_dict(look=LOOK))
    assert not [w for w in clean.warnings if w.startswith("look")]
    off = fxspec.without_look(stepping)
    assert off.data["look"] is None and not [w for w in off.warnings if w.startswith("look")]
    assert off.sha256 == stepping.sha256 and off.path == stepping.path


def test_without_look_changes_the_canonical_form_only_when_a_look_exists(tmp_path):
    with_look = parse(tmp_path, spec_dict(look=LOOK))
    plain = parse(tmp_path, spec_dict())
    assert fxspec.canonical_json(fxspec.without_look(with_look).data) == fxspec.canonical_json(plain.data)
    assert fxspec.canonical_json(with_look.data) != fxspec.canonical_json(plain.data)
    assert fxspec.canonical_json(fxspec.without_look(plain).data) == fxspec.canonical_json(plain.data)


def test_schema_documents_look_and_stays_valid():
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(fxspec.schema())
    assert "look" in fxspec.schema()["properties"] and fx.SCHEMA_VERSION == "fx-shot/0.1"          # 版番号は据え置き


# ---- render（opencv が要る） ----------------------------------------------------------

@pytest.fixture
def render():
    pytest.importorskip("cv2")
    from mv_pipeline.fx import render as module
    return module


def build(render, tmp_path, width=160, height=90, **changes):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return render.prepare(fxspec.load_spec(make_project(tmp_path, width, height, **changes)))


def one_light(**over):
    lt = copy.deepcopy(FULL["lights"][0])
    lt["intensity"], lt["flicker"] = 1.0, {"amount": 0.0, "hz": 5.0}
    lt.update(over)
    return [lt]


def digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def test_a_spec_without_look_takes_the_old_to_srgb8_path_byte_for_byte(render, tmp_path):
    ctx = build(render, tmp_path)
    assert ctx.look is None and ctx.spec.data["look"] is None
    for variant in fx.VARIANTS:
        for t in (0.0, 0.7, 1.99):
            use_layers = ctx.has_layers and variant in ("layers", "both")
            active = render.light.levels(ctx.lights, t) if ctx.has_lights and variant in ("light", "both") else []
            linear = render._layered(ctx, t, active) if use_layers else render._flat(ctx, t, active)
            assert np.array_equal(render.frame(ctx, t, variant), color.to_srgb8(linear)), (variant, t)
    assert "look" not in ctx.variant_notes()["both"]["features"]


def test_look_changes_the_picture_and_is_recorded_in_every_variants_features(render, tmp_path):
    plain, styled = build(render, tmp_path / "a"), build(render, tmp_path / "b", look=SHORT_LOOK)
    assert styled.look is not None
    for variant in fx.VARIANTS:
        assert not np.array_equal(render.frame(plain, 1.0, variant), render.frame(styled, 1.0, variant))
        assert "look" in styled.variant_notes()[variant]["features"]
        assert styled.variant_notes()[variant]["empty"] == plain.variant_notes()[variant]["empty"]     # empty の意味は変わらない


def test_all_four_variants_get_the_same_look_and_the_same_grain(render, tmp_path):
    ctx = build(render, tmp_path, layers=None, lights=None, look=SHORT_LOOK)             # 機能が無ければ 4 版は同じ映像
    for t in (0.0, 0.5, 1.5):
        frames = [render.frame(ctx, t, v) for v in fx.VARIANTS]
        assert all(np.array_equal(frames[0], f) for f in frames[1:])
    a = build(render, tmp_path / "s7", look=SHORT_LOOK, seed=7)
    b = build(render, tmp_path / "s8", look=SHORT_LOOK, seed=8)                          # spec の seed は粒に混ざらない
    assert all(np.array_equal(x, y) for x, y in zip(a.look.grain, b.look.grain))
    c = build(render, tmp_path / "g8", look={**SHORT_LOOK, "grain": {"seed": 8}})
    assert not np.array_equal(a.look.grain[0], c.look.grain[0])


def test_look_does_not_touch_the_cached_plate_even_when_the_frame_aliases_it(render, tmp_path):
    ctx = build(render, tmp_path, camera=None, layers=None, lights=None, look=SHORT_LOOK)
    plate = ctx.assets.plate
    assert render._flat(ctx, 0.0, []) is plate                    # 静止カメラ・光なしの linear は plate そのもの（罠の前提）
    before = plate.copy()
    plate.flags.writeable = False                                 # その場で書き換えれば ValueError になる
    first = render.frame(ctx, 0.5, "base")
    second = render.frame(ctx, 0.5, "base")
    assert np.array_equal(plate, before) and np.array_equal(first, second)


def test_locality_holds_with_a_look_pixels_outside_receivers_and_emitter_stay_bit_identical(render, tmp_path):
    ctx = build(render, tmp_path, camera=None, lights=one_light(), look=LOOK)
    width, height = ctx.size
    masks = ctx.assets.masks
    ys, xs = np.mgrid[0:height, 0:width]
    near_emitter = np.hypot(xs + 0.5 - 0.64 * width, ys + 0.5 - 0.42 * height) <= 4 * 0.03 * width + 1.0
    allowed = (masks["subject"] > 0) | (masks["ground"] > 0) | near_emitter
    diff = (render.frame(ctx, 1.0, "light") != render.frame(ctx, 1.0, "base")).any(axis=2)
    assert diff.sum() > 500 and (~allowed).sum() > 3000
    assert not (diff & ~allowed).any()


def test_zero_intensity_light_still_equals_base_with_a_look(render, tmp_path):
    ctx = build(render, tmp_path, lights=one_light(intensity=0.0), look=LOOK)
    for t in (0.0, 0.7, 2.0):
        assert np.array_equal(render.frame(ctx, t, "light"), render.frame(ctx, t, "base"))


def test_look_keeps_frames_deterministic_order_independent_and_the_context_unchanged(render, tmp_path):
    import cv2
    path = make_project(tmp_path, look=LOOK)
    times = (0.0, 0.5, 1.25, 1.99)
    snapshot = lambda c: {(v, t): digest(render.frame(c, t, v)) for v in fx.VARIANTS for t in times}  # noqa: E731
    ctx = render.prepare(fxspec.load_spec(path))
    state = lambda: [digest(np.ascontiguousarray(a)) for a in (ctx.assets.plate, ctx.look.vignette, *ctx.look.grain)]  # noqa: E731
    before = state()
    first = snapshot(ctx)
    backward = {(v, t): digest(render.frame(ctx, t, v)) for v in reversed(fx.VARIANTS) for t in reversed(times)}
    assert first == backward and state() == before
    assert first == snapshot(render.prepare(fxspec.load_spec(path)))
    threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        assert first == snapshot(render.prepare(fxspec.load_spec(path)))
    finally:
        cv2.setNumThreads(threads)
    assert len(set(first.values())) > 12


def test_vignette_and_grain_are_built_once_in_prepare_not_per_frame(render, tmp_path, monkeypatch):
    ctx = build(render, tmp_path, look=LOOK)

    def boom(*args, **kwargs):
        raise AssertionError("フレームごとに作り直している")
    monkeypatch.setattr(fxlook, "vignette_map", boom)
    monkeypatch.setattr(fxlook, "grain_frames", boom)
    for t in (0.0, 0.5, 1.0):
        assert render.frame(ctx, t, "both").shape == (90, 160, 3)


def test_output_size_changes_the_vignette_but_not_the_grain_block_size(render, tmp_path):
    spec = fxspec.load_spec(make_project(tmp_path, look=LOOK))
    small, large = render.prepare(spec, scale=0.5), render.prepare(spec, scale=2.0)
    assert small.look.vignette.shape == (small.size[1], small.size[0], 1) and large.look.grain[0].shape[:2] == (large.size[1], large.size[0])
    assert (large.look.grain[0][0::2, 0::2] == large.look.grain[0][1::2, 1::2]).all()                  # 粒は常に 2 出力画素角


# ---- CLI: --no-look / demo --look -----------------------------------------------------

def sheet_pixels(result):
    return np.asarray(Image.open(result["path"]))


def test_stills_no_look_equals_a_spec_without_look_and_the_key_separates_the_two(tmp_path):
    pytest.importorskip("cv2")
    path = make_project(tmp_path, look=SHORT_LOOK)
    plain = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k != "look"}
    plain_path = tmp_path / "plain.json"
    plain_path.write_text(json.dumps(plain), encoding="utf-8")
    args = ("--times", "0.5", "1.5", "--variants", "base,both")
    code, styled, err = run_cli("fx", "stills", path, *args, "--out", tmp_path / "a")
    assert code == 0, err
    code, off, err = run_cli("fx", "stills", path, *args, "--no-look", "--out", tmp_path / "b")
    assert code == 0, err
    code, absent, err = run_cli("fx", "stills", plain_path, *args, "--no-look", "--out", tmp_path / "c")
    assert code == 0, err
    assert (styled["look"], off["look"], absent["look"]) == ("applied", "disabled", "absent")
    assert np.array_equal(sheet_pixels(off), sheet_pixels(absent))                                  # --no-look の出力 = look を外した spec の出力
    assert not np.array_equal(sheet_pixels(styled), sheet_pixels(off))
    names = [Path(r["path"]).name for r in (styled, off, absent)]
    assert names[0] != names[1] and names[1] == names[2]                                            # key が look の有無で分かれる


def test_render_keys_and_manifest_record_applied_disabled_and_absent(tmp_path):
    pytest.importorskip("cv2")
    from mv_pipeline.fx import assets as fxassets
    from mv_pipeline.fx import runs
    path = make_project(tmp_path, look=SHORT_LOOK)
    sp = fxspec.load_spec(path)
    libraries = {"python": "3", "numpy": "2", "opencv": "4", "pillow": "13", "ffmpeg": "x"}
    key = lambda s: runs.render_key(s, fxassets.describe(s), fx.VARIANTS, 0.0, 2.0, 24, (160, 90), 1.0, True, libraries)  # noqa: E731
    assert key(sp) != key(fxspec.without_look(sp))
    assert key(runs.resolve_look(sp, True)[0]) == key(fxspec.without_look(sp))
    changed = parse(tmp_path, {**json.loads(path.read_text(encoding="utf-8")),
                               "look": {**SHORT_LOOK, "vignette": {"amount": 0.4}}})
    assert key(changed) != key(sp)
    assert runs.resolve_look(sp)[1] == "applied" and runs.resolve_look(sp, True)[1] == "disabled"
    plain = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k != "look"}
    plain_path = tmp_path / "plain.json"
    plain_path.write_text(json.dumps(plain), encoding="utf-8")
    plain_sp = fxspec.load_spec(plain_path)
    assert runs.resolve_look(plain_sp, True)[1] == "absent" and key(runs.resolve_look(plain_sp, True)[0]) == key(plain_sp)
    assert key(plain_sp) == key(fxspec.without_look(sp))                                            # 外した spec = look の無い spec

    out = tmp_path / "runs"
    args = ("--variants", "base", "--duration", "0.25", "--out", out)
    code, applied, err = run_cli("fx", "render", path, *args)
    assert code == 0 and applied["technical_pass"] is True and applied["look"] == "applied", err
    code, disabled, err = run_cli("fx", "render", path, *args, "--no-look")
    assert code == 0 and disabled["look"] == "disabled" and disabled["run_id"] != applied["run_id"] and not disabled["reused"], err
    assert "--no-look" in err
    code, absent, err = run_cli("fx", "render", plain_path, *args)                                  # look の無い spec は --no-look の run と同じ key
    assert code == 0 and absent["reused"] is True and absent["run_id"] == disabled["run_id"] and absent["look"] == "absent"
    manifests = {name: read_json(out / r["run_id"] / "manifest.json") for name, r in (("applied", applied), ("disabled", disabled))}
    assert manifests["applied"]["look"] == "applied" and manifests["disabled"]["look"] == "disabled"
    assert manifests["applied"]["key"] != manifests["disabled"]["key"]
    assert "look" in manifests["applied"]["variants"]["base"]["features"] and "look" not in manifests["disabled"]["variants"]["base"]["features"]
    assert manifests["applied"]["spec"]["normalized_sha256"] != manifests["disabled"]["spec"]["normalized_sha256"]
    plain_run = run_cli("fx", "render", plain_path, "--variants", "base", "--duration", "0.25", "--out", tmp_path / "plain_runs")[1]
    assert read_json(tmp_path / "plain_runs" / plain_run["run_id"] / "manifest.json")["look"] == "absent"


def test_demo_look_flag_adds_a_look_example_and_the_default_demo_spec_has_none(tmp_path):
    pytest.importorskip("cv2")
    for name, flags in (("plain", ()), ("styled", ("--look",))):
        code, result, err = run_cli("fx", "demo", tmp_path / name, "--width", "320", "--height", "180", "--duration", "1.0",
                                    "--fps", "12", *flags)
        assert code == 0 and result["files"][-1] == "spec.json", err
    plain = json.loads((tmp_path / "plain" / "spec.json").read_text(encoding="utf-8"))
    styled = json.loads((tmp_path / "styled" / "spec.json").read_text(encoding="utf-8"))
    assert "look" not in plain and fxspec.load_spec(tmp_path / "plain" / "spec.json").data["look"] is None
    assert {k: v for k, v in styled.items() if k != "look"} == plain                                # 既定のデモ spec は変えない
    sp = fxspec.load_spec(tmp_path / "styled" / "spec.json")
    assert not sp.warnings and sp.data["look"]["grade"] and sp.data["look"]["vignette"] and sp.data["look"]["grain"]


def test_e2e_demo_look_renders_with_technical_pass_and_records_the_look(tmp_path):
    pytest.importorskip("cv2")
    assert run_cli("fx", "demo", tmp_path, "--width", "320", "--height", "180", "--duration", "1.0", "--fps", "12", "--look")[0] == 0
    code, result, err = run_cli("fx", "render", tmp_path / "spec.json", "--out", tmp_path / "runs")
    assert code == 0, err
    assert result["status"] == "succeeded" and result["technical_pass"] is True and result["look"] == "applied"
    assert all(v["technical_pass"] for v in result["variants"].values()) and result["grid"]
    manifest = read_json(tmp_path / "runs" / result["run_id"] / "manifest.json")
    assert manifest["look"] == "applied" and manifest["warnings"] == []
    assert all("look" in item["features"] for item in manifest["variants"].values())
    assert manifest["variants"]["both"]["checks"]["failures"] == [] and manifest["grid"]["technical_pass"] is True
