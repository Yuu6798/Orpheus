"""`mv gate flashes`: 完成動画の明滅を、既知のコマに閃光を置いた合成の動画（encode.write_video）で確かめる。

`mv gate check` は gate.json に flash を書いたときだけ検査の行が増え、書かなければ結果が変更前と同じ。
合成素材（1 色で塗ったコマ）での確かめで、実作品の明滅や、規格への適合を確かめるものではない。
"""
import json
import subprocess

import numpy as np
import pytest
from fx_synth import run_cli
from PIL import Image

from mv_pipeline import encode, media
from mv_pipeline.gate import checks as gate
from mv_pipeline.gate import demo, flashes
from mv_pipeline.io import PipelineError, read_json, sha256

FPS = 30
SIZE = (64, 36)
DARK, WHITE = 20, 255
DARK_LEVEL = 0.007               # 灰色 20 の相対輝度（sRGB の伝達特性で線形に戻した値）。白 255 は 1.0
# 1 コマで光って、ゆっくり消える光（灰色の値）。相対輝度は 0.515 から、1 コマあたり 0.07 以下で下がる
FADE = [190, 178, 167, 157, 148, 139, 131, 123, 116, 109, 102, 96, 90, 84, 78, 72, 66, 60, 54, 48, 42, 36, 30, 25, 20]


def _video(path, values, fps=FPS, size=SIZE):
    """コマごとの色（0〜255 の灰色か (R, G, B)）で塗った動画を書く。"""
    frames = [np.full((size[1], size[0], 3), value, np.uint8) for value in values]
    encode.write_video(path, iter(frames), size, fps, 12, len(frames))
    return path


def _strobe(count, at, hold=1, base=DARK, peak=WHITE):
    """base で塗った count コマの、at のコマから hold コマだけを peak にする。"""
    values = [base] * count
    for frame in at:
        values[frame:frame + hold] = [peak] * hold
    return values


def _steps(changes):
    return [(c["frame"], c["direction"]) for c in changes]


# ---- 動画から測る -------------------------------------------------------------------

def test_changes_and_flashes_land_on_the_frames_where_the_light_was_put(tmp_path):
    values = _strobe(60, [10])
    values[25:28] = [WHITE] * 3                                  # 3 コマ続く閃光（25〜27）
    path = _video(tmp_path / "v.mp4", values)
    report = flashes.measure(path)
    assert _steps(report["changes"]) == [(10, "up"), (11, "down"), (25, "up"), (28, "down")]
    assert [c["t"] for c in report["changes"]] == [0.3333, 0.3667, 0.8333, 0.9333]
    assert all(c["amount"] == pytest.approx(1.0 - DARK_LEVEL, abs=0.02) for c in report["changes"])
    # from / to は変化の前後の極値とそのコマ（同じ値が続くときは最後のコマ）。最後の変化の to は動画の終わりまでの極値
    assert [(c["from_frame"], c["to_frame"]) for c in report["changes"]] == [(9, 10), (10, 24), (24, 27), (27, 59)]
    assert report["changes"][0]["from"] == pytest.approx(DARK_LEVEL, abs=0.005) and report["changes"][0]["to"] == pytest.approx(1.0, abs=0.02)
    assert [(f["frame"], f["return_frame"], f["order"]) for f in report["flashes"]] == [(10, 11, "up_down"), (25, 28, "up_down")]
    assert [(f["t"], f["return_t"]) for f in report["flashes"]] == [(0.3333, 0.3667), (0.8333, 0.9333)]
    assert report["counts"] == {"changes": 4, "up": 2, "down": 2, "flashes": 2, "unpaired_changes": 0}
    assert report["max_per_window"] == 2 and report["status"] == "pass"
    assert report["busiest_window"] == {"start_frame": 10, "start_t": 0.3333, "end_t": 1.3333, "flashes": 2, "changes": 4,
                                        "pairs": [[10, 11], [25, 28]]}
    assert report["video"] == {"name": "v.mp4", "sha256": sha256(path), "fps": 30.0, "frames": 60, "size": [64, 36], "matrix_tag": "bt709"}
    # 測り方が報告から分かる: BT.709 として読み、sRGB の伝達特性で線形に戻した相対輝度。変化は直前の極値から delta 以上
    assert report["read"] == {"size": [64, 36], "matrix": "bt709", "transfer": "srgb", "weights": [0.2126, 0.7152, 0.0722],
                              "brightness": "relative_luminance", "change": "delta_from_last_extremum"}
    assert report["thresholds"] == {"delta": 0.1, "max_per_s": 3, "window_s": 1.0} and report["schema_version"] == "gate-flash-report/0.1"
    assert "規格への適合を判定するものではありません" in report["note"]


def test_four_flashes_in_a_second_exceed_the_limit_and_three_do_not(tmp_path):
    four = _video(tmp_path / "four.mp4", _strobe(60, [5, 12, 20, 27]))
    code, result, err = run_cli("gate", "flashes", four)
    assert code == 1 and err == "" and result["status"] == "fail" and result["max_per_window"] == 4 and result["flashes"] == 4
    assert result["busiest_window"]["pairs"] == [[5, 6], [12, 13], [20, 21], [27, 28]]
    out = four.resolve().with_name("four_flash_report")          # 既定の出力先は動画の隣の <動画の名前>_flash_report/
    assert result["out"] == str(out) and result["report"] == str(out / "flash_report.json")
    assert read_json(out / "flash_report.json")["status"] == "fail"
    # 10 コマおきの閃光は、どの 1 秒（30 コマ）にも 3 回。4 つ目（35）は 1 つ目（5）のちょうど 1 秒後で、同じ窓に入らない
    three = _video(tmp_path / "three.mp4", _strobe(90, [5, 15, 25, 35, 45, 55]))
    code, result, _ = run_cli("gate", "flashes", three, "--out", tmp_path / "out")
    assert code == 0 and result["status"] == "pass" and result["max_per_window"] == 3 and result["flashes"] == 6
    assert (tmp_path / "out/flash_report.json").is_file()
    # 上限は引数で変えられる（超えたら不合格。同じなら通る）
    assert run_cli("gate", "flashes", four, "--max-per-s", "4", "--out", tmp_path / "a")[0] == 0
    assert run_cli("gate", "flashes", three, "--max-per-s", "2", "--out", tmp_path / "b")[0] == 1


def test_a_light_that_fades_out_slowly_is_one_flash(tmp_path):
    """1 コマで光り、1 コマあたり delta 未満でゆっくり消える光。山からの下がりが合計で delta に達したコマで「下がり」になる。"""
    path = _video(tmp_path / "v.mp4", [DARK] * 10 + FADE + [DARK] * 10)
    levels, _, _ = flashes.read_levels(path)
    assert max(a - b for a, b in zip(levels[10:], levels[11:])) < 0.08          # どのコマの間の下がりも delta に届かない
    report = flashes.measure(path)
    assert _steps(report["changes"]) == [(10, "up"), (12, "down")]              # 山 0.515 → 0.447 → 0.386（合計 −0.129）
    up, down = report["changes"]
    assert (up["from_frame"], up["to_frame"], down["from_frame"]) == (9, 10, 10) and down["to_frame"] == 44
    assert up["amount"] == pytest.approx(0.515 - DARK_LEVEL, abs=0.02) == down["amount"]     # 大きさは山から谷まで
    assert [(f["frame"], f["return_frame"], f["order"]) for f in report["flashes"]] == [(10, 12, "up_down")]
    assert report["counts"] == {"changes": 2, "up": 1, "down": 1, "flashes": 1, "unpaired_changes": 0} and report["max_per_window"] == 1


def test_a_second_light_rising_while_the_first_fades_makes_two_flashes(tmp_path):
    """1 つ目がゆっくり消える途中（8 コマ後）で 2 つ目が光る。コマ間の差だけを見ると、上がり 2 つで戻りが 1 つも無い形。"""
    path = _video(tmp_path / "v.mp4", [DARK] * 10 + FADE[:8] + FADE + [DARK] * 10)
    levels, _, _ = flashes.read_levels(path)
    falls = [a - b for a, b in zip(levels, levels[1:])]
    assert max(falls) < 0.08 and levels[17] == pytest.approx(0.198, abs=0.01)   # 2 つ目の直前は、まだ暗い地（0.007）に戻っていない
    report = flashes.measure(path)
    assert _steps(report["changes"]) == [(10, "up"), (12, "down"), (18, "up"), (20, "down")]
    assert [(c["from_frame"], c["to_frame"]) for c in report["changes"]] == [(9, 10), (10, 17), (17, 18), (18, 52)]
    assert [(f["frame"], f["return_frame"]) for f in report["flashes"]] == [(10, 12), (18, 20)]
    assert report["counts"]["flashes"] == 2 and report["max_per_window"] == 2 and report["status"] == "pass"


def test_a_scene_change_or_a_one_way_fade_is_not_a_flash(tmp_path):
    report = flashes.measure(_video(tmp_path / "cut.mp4", [DARK] * 15 + [200] * 15))
    assert _steps(report["changes"]) == [(15, "up")] and report["flashes"] == [] and report["max_per_window"] == 0
    assert report["counts"]["unpaired_changes"] == 1 and report["status"] == "pass" and report["busiest_window"] is None
    # 2 秒かけて明るくなるだけのフェード: 谷から delta 離れたコマで上がりが 1 つ。戻らないので明滅は 0 回
    ramp = [int(round(DARK + (230 - DARK) * i / 59)) for i in range(60)]
    fade = flashes.measure(_video(tmp_path / "fade.mp4", [DARK] * 5 + ramp + [230] * 5))
    assert [c["direction"] for c in fade["changes"]] == ["up"] and fade["flashes"] == [] and fade["max_per_window"] == 0
    assert fade["changes"][0]["amount"] == pytest.approx(0.791 - DARK_LEVEL, abs=0.02) and 20 < fade["changes"][0]["frame"] < 40
    # 明るくなって 40 コマ（1 秒以上）後に暗くなる 2 つの切り替えも、明滅にしない
    far = flashes.measure(_video(tmp_path / "far.mp4", [DARK] * 10 + [200] * 40 + [DARK] * 10))
    assert _steps(far["changes"]) == [(10, "up"), (50, "down")] and far["flashes"] == [] and far["counts"]["unpaired_changes"] == 2
    # 1 秒未満で戻れば 1 回（暗 → 明 → 暗）。明 → 暗 → 明 も 1 回
    near = flashes.measure(_video(tmp_path / "near.mp4", [DARK] * 10 + [200] * 20 + [DARK] * 10))
    assert [(f["frame"], f["return_frame"], f["order"]) for f in near["flashes"]] == [(10, 30, "up_down")]
    dip = flashes.measure(_video(tmp_path / "dip.mp4", [200] * 10 + [DARK] * 20 + [200] * 10))
    assert [(f["frame"], f["return_frame"], f["order"]) for f in dip["flashes"]] == [(10, 30, "down_up")] and dip["max_per_window"] == 1


@pytest.mark.parametrize("half", [15, 30])
def test_a_slow_swell_of_a_second_or_more_never_exceeds_the_limit(tmp_path, half):
    """half コマで明るくなり half コマで暗くなる、を 3 回（1 往復が 1 秒か 2 秒）。往復ごとに 1 回と数えるが、どの 1 秒でも 3 回を超えない。"""
    up = [int(round(25 + (229 - 25) * i / half)) for i in range(half)]
    report = flashes.measure(_video(tmp_path / "v.mp4", [25] * 5 + (up + [229] + up[::-1]) * 3 + [25] * 5))
    assert [c["direction"] for c in report["changes"]] == ["up", "down"] * 3 and report["counts"]["flashes"] == 3
    assert report["max_per_window"] == 1 and report["status"] == "pass"


def test_a_rise_over_several_frames_is_one_change(tmp_path):
    values = [DARK] * 5 + [110, 170, 230] + [230] * 5 + [120] * 5
    report = flashes.measure(_video(tmp_path / "v.mp4", values))
    assert _steps(report["changes"]) == [(5, "up"), (13, "down")]               # 3 コマかけた上がりは、delta を越えた最初のコマで 1 つ
    assert (report["changes"][0]["from_frame"], report["changes"][0]["to_frame"]) == (4, 12)
    assert report["changes"][0]["amount"] == pytest.approx(0.791 - DARK_LEVEL, abs=0.02)      # 大きさは谷から山まで（230 は 0.791）
    assert report["changes"][1]["amount"] == pytest.approx(0.791 - 0.188, abs=0.02)           # 120 は 0.188
    assert [(f["frame"], f["return_frame"]) for f in report["flashes"]] == [(5, 13)] and report["max_per_window"] == 1
    assert report["flashes"][0]["amount"] == report["changes"][1]["amount"]        # 明滅の大きさは、2 つの変化の小さい方


def test_brightness_is_the_linear_relative_luminance(tmp_path):
    colours = [(0, 0, 0), (0, 255, 0), (0, 0, 0), (0, 0, 255), (255, 0, 0), (255, 255, 255), (128, 128, 128), (188, 188, 188)]
    path = _video(tmp_path / "v.mp4", [c for c in colours for _ in range(4)])
    levels, info, size = flashes.read_levels(path)
    assert len(levels) == 32 and size == (64, 36) and info["fps"] == 30.0
    # 原色は係数そのまま。灰色 128（表示の値 0.502）は 0.216、188（0.737）は 0.503（ガンマのままの値で測っていない）
    assert levels[::4] == pytest.approx([0.0, 0.7152, 0.0, 0.0722, 0.2126, 1.0, 0.2159, 0.5029], abs=0.012)
    report = flashes.measure(path)
    # 黒 → 青（+0.0722）は delta に届かない。赤で谷（黒）から 0.2126 離れて上がり、白まで上がり続けて 1 つ。128 への下がりで 1 つ
    assert _steps(report["changes"]) == [(4, "up"), (8, "down"), (16, "up"), (24, "down"), (28, "up")]
    assert report["changes"][2]["amount"] == pytest.approx(1.0, abs=0.02)


def test_brightness_is_averaged_after_decoding_each_pixel_to_linear(tmp_path):
    """左半分が黒・右半分が白のコマ。相対輝度の平均は 0.5（表示の値を平均してから線形に戻すと 0.214 になる）。"""
    frame = np.zeros((36, 64, 3), np.uint8)
    frame[:, 32:] = 255
    encode.write_video(tmp_path / "v.mp4", iter([frame] * 4), SIZE, FPS, 12, 4)
    levels, _, _ = flashes.read_levels(tmp_path / "v.mp4")
    assert levels == pytest.approx([0.5] * 4, abs=0.02)


def test_frames_are_read_as_bt709_whatever_the_tag_says(tmp_path):
    """BT.709 の行列で書いて smpte170m（BT.601）の印を付けた動画。印に従って読むと、赤の相対輝度は 0.2126 でなく 0.17 になる。"""
    path = tmp_path / "tagged601.mp4"
    frames = [np.full((36, 64, 3), colour, np.uint8) for colour in [(0, 0, 0)] * 4 + [(255, 0, 0)] * 4]
    subprocess.run([media.ffmpeg(), "-y", "-loglevel", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x36",
                    "-framerate", "30", "-i", "pipe:0",
                    "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=colorspace=smpte170m",
                    "-c:v", "libx264", "-crf", "12", "-colorspace", "smpte170m", str(path)],
                   input=b"".join(frame.tobytes() for frame in frames), check=True, capture_output=True)
    report = flashes.measure(path)
    assert report["video"]["matrix_tag"] == "smpte170m" and report["read"]["matrix"] == "bt709"
    assert report["changes"][0]["to"] == pytest.approx(0.2126, abs=0.012)


def test_the_same_input_gives_the_same_report(tmp_path):
    path = _video(tmp_path / "v.mp4", _strobe(45, [5, 12, 20, 27]))
    first, _ = flashes.run(path, tmp_path / "a")
    second, _ = flashes.run(path, tmp_path / "b")
    one, two = (tmp_path / "a/flash_report.json").read_bytes(), (tmp_path / "b/flash_report.json").read_bytes()
    assert first == second and one == two
    assert str(tmp_path) not in one.decode("utf-8") and json.loads(one) == first       # 絶対パスも時刻も書かない


def test_the_window_is_one_second_of_frames_at_the_fps_of_the_video(tmp_path):
    """24 fps: 1 秒 = 24 コマ。5 と 29 はちょうど 24 コマ離れているので同じ窓に入らず、5 と 27 なら入る。"""
    apart = flashes.measure(_video(tmp_path / "a.mp4", _strobe(48, [5, 13, 21, 29]), fps=24))
    assert apart["video"]["fps"] == 24.0 and apart["max_per_window"] == 3
    close = flashes.measure(_video(tmp_path / "b.mp4", _strobe(48, [5, 13, 21, 27]), fps=24))
    assert close["max_per_window"] == 4 and close["status"] == "fail"
    assert close["changes"][0]["t"] == round(5 / 24, 4) and close["busiest_window"]["end_t"] == round(round(5 / 24, 4) + 1.0, 4)


def test_a_wide_video_is_read_small(tmp_path):
    path = _video(tmp_path / "v.mp4", _strobe(12, [5]), size=(640, 360))
    report = flashes.measure(path)
    assert report["video"]["size"] == [640, 360] and report["read"]["size"] == [320, 180]
    assert _steps(report["changes"]) == [(5, "up"), (6, "down")] and report["changes"][0]["amount"] == pytest.approx(1.0 - DARK_LEVEL, abs=0.02)


# ---- 数え方（動画なし） --------------------------------------------------------------

def _found(levels, delta=0.1):
    return [(c["frame"], c["direction"], c["from_frame"], c["to_frame"], round(c["amount"], 6)) for c in flashes.find_changes(levels, delta)]


def test_a_change_is_delta_away_from_the_last_extremum():
    assert _found([]) == [] and _found([0.5]) == [] and _found([0.5, 0.5, 0.5]) == []
    assert _found([0.0, 0.0999, 0.1998]) == [(2, "up", 0, 2, 0.1998)]         # 1 コマでは届かず、2 コマ目で谷から delta を越える
    assert _found([0.2, 0.3]) == [(1, "up", 0, 1, 0.1)]                       # 0.3 - 0.2 は浮動小数で 0.1 に届かないが、境目は含む
    assert _found([0.3, 0.2]) == [(1, "down", 0, 1, 0.1)] and _found([0.2, 0.2999]) == []
    assert _found([0.0, 0.5, 0.0]) == [(1, "up", 0, 1, 0.5), (2, "down", 1, 2, 0.5)]
    # 数コマかけた上がりは 1 つ（越えたコマは 1、山は同じ値の最後のコマ 4）。下がりは山からの差で見る
    assert _found([0.0, 0.3, 0.6, 0.9, 0.9, 0.5]) == [(1, "up", 0, 4, 0.9), (5, "down", 4, 5, 0.4)]
    # ゆっくり消える光: コマ間の下がりは 0.05 以下でも、山 0.5 から 0.38（−0.12）で下がり。谷は同じ値の最後のコマ
    assert _found([0, 0, .5, .45, .41, .38, .34, .2, 0, 0]) == [(2, "up", 1, 2, 0.5), (5, "down", 2, 9, 0.5)]
    # 最初の変化までは、それまでの最小と最大の両方から見る（下がってから上がる・上がってから下がる）
    assert _found([0.5, 0.45, 0.52, 0.56]) == [(3, "up", 1, 3, 0.11)] and _found([0.5, 0.55, 0.52, 0.44]) == [(3, "down", 1, 3, 0.11)]


def test_wobbles_smaller_than_delta_do_not_reverse_the_direction():
    assert _found([0.5, 0.55, 0.48, 0.56, 0.47]) == []                        # 最小と最大の差が 0.09
    # 上がった後の 0.09 の戻りと再びの上がりは変化にしない（山は 1.0 のコマ）。0.1 戻れば下がり
    assert _found([0.0, 0.9, 0.81, 1.0, 0.91, 1.0, 0.9]) == [(1, "up", 0, 5, 1.0), (6, "down", 5, 6, 0.1)]
    # delta を大きくすれば、小さい明滅は 1 つの上がりにまとまる
    levels = [0.0, 0.5, 0.3, 0.6, 0.35, 0.7]
    assert [c[:2] for c in _found(levels)] == [(1, "up"), (2, "down"), (3, "up"), (4, "down"), (5, "up")]
    assert _found(levels, 0.3) == [(1, "up", 0, 5, 0.7)]


def test_changes_always_alternate_and_each_one_clears_delta():
    rng = np.random.default_rng(11)
    for delta in (0.05, 0.1, 0.3):
        levels = np.clip(np.cumsum(rng.normal(0, 0.06, 600)) % 1.0, 0, 1).tolist()
        changes = flashes.find_changes(levels, delta)
        assert len(changes) > 5
        assert all(a["direction"] != b["direction"] for a, b in zip(changes, changes[1:]))
        for change, following in zip(changes, changes[1:] + [None]):
            sign = 1 if change["direction"] == "up" else -1
            assert sign * (levels[change["frame"]] - change["from"]) >= delta - 1e-9          # 越えたコマは極値から delta 以上
            before = levels[change["from_frame"]:change["frame"]]
            assert all(sign * (v - change["from"]) < delta - 1e-9 for v in before)             # その前のコマは、まだ越えていない
            assert change["from"] == levels[change["from_frame"]] and change["to"] == levels[change["to_frame"]]
            stop = following["frame"] if following else len(levels)
            span = levels[change["frame"]:stop]
            assert change["to"] == (max(span) if sign > 0 else min(span))                      # to は次の変化までの極値
            assert change["amount"] >= delta - 1e-9


def _changes(text):
    """"u5 d8" → 変化の一覧（数え方は向きとコマだけを使う）。"""
    return [{"frame": int(item[1:]), "direction": "up" if item[0] == "u" else "down"} for item in text.split()]


def test_pairs_are_neighbouring_opposite_changes_less_than_a_second_apart():
    assert flashes.pair([], 30) == [] and flashes.pair(_changes("u5"), 30) == []
    assert flashes.pair(_changes("u5 d6 u10 d11"), 30) == [(0, 1), (2, 3)]
    assert flashes.pair(_changes("u5 d6 u10"), 30) == [(0, 1)]                       # 余った 1 つは組にならない
    assert flashes.pair(_changes("u1 u5 d8"), 30) == [(1, 2)]                        # 手で作った同じ向きの並びは、反対向きの隣と組む
    assert flashes.pair(_changes("u1 d30"), 30) == [(0, 1)] and flashes.pair(_changes("u1 d31"), 30) == []
    assert flashes.pair(_changes("u0 d29"), 29.97) == [(0, 1)] and flashes.pair(_changes("u0 d30"), 29.97) == []
    # 1 秒以上離れた 2 つは組にせず、次の近い 2 つを組にする
    assert flashes.pair(_changes("u1 d40 u45 d50"), 30) == [(1, 2)]


def test_a_window_is_paired_on_its_own_so_an_earlier_change_cannot_hide_a_flash():
    """暗くなる切り替え（d1）の後に、0.7 秒で 4 回の閃光。前から順の組は切り替えと最初の上がりを組にして 1 つずつずれ、
    閃光の窓（16〜45）に丸ごと入る組が 3 つになる。窓の中だけで組み直すと 4 つで、これを回数にする。"""
    changes = _changes("d1 u16 d18 u22 d24 u28 d30 u34 d36")
    whole = flashes.pair(changes, 30)
    assert whole == [(0, 1), (2, 3), (4, 5), (6, 7)]
    assert sum(1 for a, b in whole if changes[a]["frame"] >= 16 and changes[b]["frame"] < 46) == 3
    assert flashes.busiest(changes, 30) == (4, 1, [(1, 2), (3, 4), (5, 6), (7, 8)])
    assert flashes.busiest([], 30) == (0, None, []) and flashes.busiest(_changes("u5"), 30) == (0, None, [])
    # 窓は変化のコマから fps コマ未満。4 つ目の戻り（35）が 1 つ目の上がり（5）のちょうど 1 秒後なら、その窓には 3 組しか入らない
    assert flashes.busiest(_changes("u5 d6 u15 d16 u25 d26 u34 d35"), 30)[0] == 3
    assert flashes.busiest(_changes("u5 d6 u15 d16 u25 d26 u33 d34"), 30)[0] == 4
    # 同じ回数なら早い窓
    assert flashes.busiest(_changes("u5 d6 u100 d101"), 30) == (1, 0, [(0, 1)])


def test_the_same_scene_change_before_a_burst_is_counted_from_the_video_too(tmp_path):
    """上の形を動画で: 明るい場面が暗くなり、0.5 秒後から 0.7 秒で 4 回の閃光。どの 1 秒でも最大 4 回で、超過。"""
    values = [200] * 5 + _strobe(60, [15, 21, 27, 33])
    report = flashes.measure(_video(tmp_path / "v.mp4", values))
    assert _steps(report["changes"])[:3] == [(5, "down"), (20, "up"), (21, "down")] and report["counts"]["changes"] == 9
    assert [(f["frame"], f["return_frame"]) for f in report["flashes"]] == [(5, 20), (21, 26), (27, 32), (33, 38)]   # 前から順の組はずれる
    assert report["max_per_window"] == 4 and report["status"] == "fail"
    assert report["busiest_window"]["start_frame"] == 20 and report["busiest_window"]["pairs"] == [[20, 21], [26, 27], [32, 33], [38, 39]]


# ---- しきい値と入力 ------------------------------------------------------------------

def test_settings_fill_defaults_and_refuse_what_they_do_not_know():
    assert flashes.settings() == {"delta": 0.1, "max_per_s": 3} == flashes.settings({})
    assert flashes.settings({"delta": 0.2}) == {"delta": 0.2, "max_per_s": 3}
    assert flashes.settings({"max_per_s": 2.5, "delta": 1}) == {"delta": 1.0, "max_per_s": 2.5}
    assert flashes.settings({"max_per_s": 4.0})["max_per_s"] == 4 and flashes.settings({"max_per_s": 0})["max_per_s"] == 0
    for bad, match in (({"delta": 0}, "delta"), ({"delta": 1.5}, "delta"), ({"delta": "0.1"}, "数値"), ({"delta": True}, "数値"),
                       ({"max_per_s": -1}, "max_per_s"), ({"max_per_s": float("nan")}, "数値"), ({"max_per_sec": 3}, "知らない欄"),
                       (True, "の形"), ([0.1, 3], "の形")):
        with pytest.raises(PipelineError, match=match):
            flashes.settings(bad)


def test_bad_inputs_are_errors(tmp_path):
    with pytest.raises(PipelineError, match="見つかりません"):
        flashes.measure(tmp_path / "none.mp4")
    (tmp_path / "still.png").write_bytes(b"x")
    with pytest.raises(PipelineError, match="画像は測れません"):
        flashes.measure(tmp_path / "still.png")
    path = _video(tmp_path / "v.mp4", [DARK] * 6)
    with pytest.raises(PipelineError, match="delta"):
        flashes.measure(path, delta=0)
    code, result, err = run_cli("gate", "flashes", path, "--delta", "2", "--out", tmp_path / "out")
    assert code == 1 and result is None and "delta" in err and not (tmp_path / "out").exists()
    one = flashes.measure(_video(tmp_path / "one.mp4", [DARK]))      # 1 コマの動画は比べる相手が無く、変化なし
    assert one["video"]["frames"] == 1 and one["changes"] == [] and one["status"] == "pass"


# ---- mv gate check ------------------------------------------------------------------

# 変更前（flash の検査を足す前の main）の `mv gate check` が、下の _demo_config の入力に出した結果。flash を書かない設定では、
# 検査の行・合否・しきい値・報告の欄・出力のファイルがこれと同じでなければならない
BEFORE = {
    "overall": "fail",
    "thresholds": {"lead_min_s": 0.0, "lead_max_s": 0.2, "confirmed_eps_s": 0.001, "jitter_px": 3.0, "seam_pose": 0.05,
                   "hook_overlap_s": 0.1, "overlap_mode": "time", "beat_frames": 1.0, "ssim": 0.98, "confirmed_overrides_measured": True},
    "checks": [
        {"check": "歌詞の時刻", "status": "fail", "summary": "4 行を照合、外れ 2 行", "problems": [
            {"t": 2.667, "text": "demo line two", "detail": "字が歌の +0.413 秒前（許容 0.0〜0.2）"},
            {"t": 7.5, "text": "demo line four", "detail": "字が歌の -0.070 秒前（許容 0.0〜0.2）"}]},
        {"check": "確認済みの時刻", "status": "skip", "summary": "見ていません（confirmed がありません）", "problems": []},
        {"check": "字と顔", "status": "skip", "summary": "見ていません（faces がありません）", "problems": []},
        {"check": "UI 枠", "status": "pass", "summary": "UI 枠に入るコマ 0", "problems": []},
        {"check": "揺れ", "status": "skip", "summary": "見ていません（tracks がありません）", "problems": []},
        {"check": "つなぎ目", "status": "pass", "summary": "切れ目がありません", "problems": []},
        {"check": "字の重なり", "status": "pass", "summary": "入れ替わり 3 回、超過 0", "problems": []},
        {"check": "拍", "status": "pass", "summary": "5 件を拍と照合、ずれ 0", "problems": []},
        {"check": "書き出し", "status": "skip", "summary": "見ていません（video がありません）", "problems": []}],
}
BEFORE_MARKDOWN = (
    "# 品質ゲート: fail\n\ntimeline: `{timeline}`\n\n| 検査 | 結果 | 内容 |\n| --- | --- | --- |\n"
    "| 歌詞の時刻 | 不合格 | 4 行を照合、外れ 2 行 |\n| 確認済みの時刻 | 未実施 | 見ていません（confirmed がありません） |\n"
    "| 字と顔 | 未実施 | 見ていません（faces がありません） |\n| UI 枠 | 合格 | UI 枠に入るコマ 0 |\n"
    "| 揺れ | 未実施 | 見ていません（tracks がありません） |\n| つなぎ目 | 合格 | 切れ目がありません |\n"
    "| 字の重なり | 合格 | 入れ替わり 3 回、超過 0 |\n| 拍 | 合格 | 5 件を拍と照合、ずれ 0 |\n"
    "| 書き出し | 未実施 | 見ていません（video がありません） |\n\n## 歌詞の時刻\n\n"
    "- 2.667 秒 demo line two: 字が歌の +0.413 秒前（許容 0.0〜0.2）\n- 7.500 秒 demo line four: 字が歌の -0.070 秒前（許容 0.0〜0.2）\n")


def _demo_config(root):
    """`mv gate demo` の既定の素材と、手で書いた実測の行（歌い出しの推定を通さず、gate check の入力を固定する）。"""
    demo.create(root)
    (root / "lines").mkdir()
    measured = [{"text": text, "start": start, "method": "vocals_onset"} for text, start in zip(demo.TEXTS, (0.53, 3.08, 5.78, 7.43))]
    (root / "lines/lines_measured.json").write_text(json.dumps(measured), encoding="utf-8")
    return root / "gate.json"


def test_gate_check_without_flash_gives_the_result_it_gave_before(tmp_path, monkeypatch):
    config = _demo_config(tmp_path / "d")
    assert "flash" not in read_json(config)                                   # デモの gate.json は flash を書かない（CI の gate smoke も同じ）
    monkeypatch.setattr(flashes, "measure", lambda *args, **kwargs: pytest.fail("flash を書いていないのに明滅を測りました"))
    report, out = gate.run(config)
    assert list(report) == ["overall", "timeline", "thresholds", "checks"]
    assert {key: report[key] for key in BEFORE} == BEFORE
    assert sorted(p.name for p in out.iterdir()) == ["gate_report.json", "gate_report.md"]
    assert read_json(out / "gate_report.json") == report
    assert (out / "gate_report.md").read_text(encoding="utf-8") == BEFORE_MARKDOWN.format(timeline=report["timeline"])
    code, result, _ = run_cli("gate", "check", config, "--out", tmp_path / "cli")
    assert code == 1 and result["overall"] == "fail"
    assert result["checks"] == [{key: row[key] for key in ("check", "status", "summary")} for row in BEFORE["checks"]]


def _run_dir(root, values, master=None, **timeline):
    """合成の動画と、それを指す最小の timeline・gate.json。(gate.json のパス, 設定の辞書)。"""
    root.mkdir(parents=True, exist_ok=True)
    _video(root / "v.mp4", values)
    seconds = len(values) / FPS
    (root / "timeline.json").write_text(json.dumps({"fps": FPS, "duration_s": seconds, "lyrics": [], "ui_regions": [], **timeline}), encoding="utf-8")
    config = {"timeline": "timeline.json", "video": "v.mp4"}
    if master is not None:
        _video(root / "m.mp4", master)
        config["master"] = "m.mp4"
    (root / "gate.json").write_text(json.dumps(config), encoding="utf-8")
    return root / "gate.json", config


def test_gate_check_adds_the_flash_row_only_when_flash_is_written(tmp_path):
    values = _strobe(60, [5, 15, 25, 35, 45])                   # どの 1 秒にも 3 回まで
    path, config = _run_dir(tmp_path, values, master=values)
    before, out_before = gate.run(path, tmp_path / "before")
    assert len(before["checks"]) == 9 and "flash" not in before and not (out_before / "flash_report.json").exists()
    path.write_text(json.dumps({**config, "flash": {"delta": 0.10, "max_per_s": 3}}), encoding="utf-8")
    after, out_after = gate.run(path, tmp_path / "after")
    assert after["checks"][:9] == before["checks"] and len(after["checks"]) == 10       # 今までの 9 行は同じ。行が 1 つ増えるだけ
    row = after["checks"][9]
    assert row["check"] == "明滅" and row["status"] == "pass" and row["problems"] == []
    assert row["summary"] == "master を測定、変化 10（上がり 5・下がり 5）、明滅 5 回、どの 1 秒でも最大 3 回（上限 3、変化は相対輝度で 0.1 以上）"
    assert after["overall"] == before["overall"] == "pass_with_skips" and after["thresholds"] == before["thresholds"]
    assert after["flash"] == {"delta": 0.1, "max_per_s": 3, "report": "flash_report.json"}
    saved = read_json(out_after / "flash_report.json")
    assert saved == flashes.measure(tmp_path / "m.mp4") and saved["video"]["name"] == "m.mp4"
    assert "| 明滅 | 合格 |" in (out_after / "gate_report.md").read_text(encoding="utf-8")
    # 既定値は {} で使える。上限を下げれば同じ動画が不合格になる
    path.write_text(json.dumps({**config, "flash": {}}), encoding="utf-8")
    assert gate.run(path, tmp_path / "defaults")[0]["checks"][9] == row
    path.write_text(json.dumps({**config, "flash": {"max_per_s": 2}}), encoding="utf-8")
    assert gate.run(path, tmp_path / "strict")[0]["checks"][9]["status"] == "fail"


def test_gate_check_fails_above_the_limit_and_points_at_the_busiest_second(tmp_path):
    values = _strobe(90, [35, 42, 50, 57])                      # 動画の 1.167 秒から 4 回
    path, config = _run_dir(tmp_path, values, duration_s=6.0, interval={"start": 2.0, "duration": 3.0, "frames": 90})
    path.write_text(json.dumps({**config, "flash": {"delta": 0.10, "max_per_s": 3}}), encoding="utf-8")
    code, result, _ = run_cli("gate", "check", path)
    assert code == 1 and result["overall"] == "fail" and result["checks"][9]["status"] == "fail"
    report = read_json(tmp_path / "gate_report/gate_report.json")
    row = report["checks"][9]
    assert row["summary"].startswith("video を測定、変化 8（上がり 4・下がり 4）、明滅 4 回、どの 1 秒でも最大 4 回（上限 3")
    (problem,) = row["problems"]
    # 問題の時刻はショットの時刻（描いた区間の開始 2.0 秒 + 動画のコマ 35 = 1.1666… 秒をミリ秒に切り捨てた 1.166 秒）
    assert problem["t"] == pytest.approx(3.166) and "動画のコマ 35（1.166 秒）からの 1 秒に明滅 4 回" in problem["detail"]
    assert "35-36・42-43・50-51・57-58" in problem["detail"]
    # 静止画は窓の最初の変化のコマ（光ったコマ 35）。四捨五入した 1.167 秒で取ると、次の暗いコマ 36 になる
    assert _still_mean(tmp_path / "gate_report", problem) > 200
    assert read_json(tmp_path / "gate_report/flash_report.json")["status"] == "fail"


def _still_mean(folder, problem):
    with Image.open(folder / problem["still"]) as still:
        return float(np.asarray(still.convert("L")).mean())


def test_gate_check_measures_the_master_and_takes_the_flash_still_from_it(tmp_path):
    """video は灰色 120 のまま、master だけが光る。明滅は master を測り、その静止画も master から取る（ほかの行は video から）。"""
    calm, busy = [120] * 45, _strobe(45, [5, 12, 20, 27])
    line = {"layer": "band", "text": "one", "start": 0.2, "end": 1.0, "fade_in_s": 0.0, "fade_out_s": 0.0, "box": [0.2, 0.07, 0.8, 0.12]}
    path, config = _run_dir(tmp_path / "a", calm, master=busy, lyrics=[line])
    (tmp_path / "a/measured.json").write_text(json.dumps([{"start": 1.0}]), encoding="utf-8")       # 字が歌の 0.8 秒前 → 不合格（0.2 秒に静止画）
    path.write_text(json.dumps({**config, "measured_lines": "measured.json", "flash": {}}), encoding="utf-8")
    report, out = gate.run(path)
    by_name = {row["check"]: row for row in report["checks"]}
    row = by_name["明滅"]
    assert row["status"] == "fail" and row["summary"].startswith("master を測定")
    assert _still_mean(out, row["problems"][0]) > 200                           # master の光ったコマ（5）。video のそのコマは灰色
    assert _still_mean(out, by_name["歌詞の時刻"]["problems"][0]) == pytest.approx(120, abs=6)        # ほかの行は今までどおり video から
    # master だけの設定でも、明滅の静止画は取れる（video が無いので、ほかの行の静止画は無い）
    path.write_text(json.dumps({"timeline": "timeline.json", "master": "m.mp4", "measured_lines": "measured.json", "flash": {}}), encoding="utf-8")
    report, out = gate.run(path, tmp_path / "master-only")
    by_name = {row["check"]: row for row in report["checks"]}
    assert _still_mean(out, by_name["明滅"]["problems"][0]) > 200 and "still" not in by_name["歌詞の時刻"]["problems"][0]
    # flash を書かなければ、master があっても静止画は video からだけ（今までどおり）
    path.write_text(json.dumps({"timeline": "timeline.json", "master": "m.mp4", "measured_lines": "measured.json"}), encoding="utf-8")
    report, out = gate.run(path, tmp_path / "no-flash")
    assert len(report["checks"]) == 9 and sorted(p.name for p in out.iterdir()) == ["gate_report.json", "gate_report.md"]
    # video だけなら video を測る
    path, config = _run_dir(tmp_path / "b", calm)
    path.write_text(json.dumps({**config, "flash": {}}), encoding="utf-8")
    row = gate.run(path)[0]["checks"][9]
    assert row["status"] == "pass" and row["summary"].startswith("video を測定、変化 0（上がり 0・下がり 0）、明滅 0 回")


def test_a_video_that_cannot_be_read_fails_the_flash_row_and_keeps_the_other_rows(tmp_path):
    path, config = _run_dir(tmp_path, [DARK] * 30)
    (tmp_path / "broken.mp4").write_bytes(b"not a video")
    path.write_text(json.dumps({"timeline": "timeline.json", "master": "broken.mp4", "flash": {}}), encoding="utf-8")
    report, out = gate.run(path)
    row = report["checks"][9]
    assert len(report["checks"]) == 10 and row["status"] == "fail" and row["summary"] == "master の明滅を測れません"
    assert "broken.mp4" in row["problems"][0]["detail"] and report["overall"] == "fail"
    assert report["flash"]["report"] is None and not (out / "flash_report.json").exists()


def test_flash_without_a_video_is_a_skip_and_a_bad_flash_is_an_error(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps({"fps": FPS, "duration_s": 1.0, "lyrics": [], "ui_regions": []}), encoding="utf-8")
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json", "flash": {}}), encoding="utf-8")
    report, out = gate.run(tmp_path / "gate.json")
    assert report["checks"][9] == {"check": "明滅", "status": "skip", "summary": "見ていません（master か video がありません）", "problems": []}
    assert report["overall"] == "pass_with_skips" and report["flash"]["report"] is None and not (out / "flash_report.json").exists()
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json", "flash": None}), encoding="utf-8")
    assert len(gate.run(tmp_path / "gate.json")[0]["checks"]) == 9                       # null は「書いていない」
    for bad, match in (({"max_per_sec": 3}, "知らない欄"), (True, 'gate の設定の "flash" は'), ({"delta": 0}, "delta")):
        (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json", "flash": bad}), encoding="utf-8")
        with pytest.raises(PipelineError, match=match):
            gate.run(tmp_path / "gate.json", tmp_path / "bad")
        assert not (tmp_path / "bad").exists()                                           # 設定の誤りは、検査を始める前に止める
