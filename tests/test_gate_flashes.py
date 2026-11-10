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
STEP = (WHITE - DARK) / 255      # 暗い地から白への変化の大きさ（明るさ 0〜1）


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
    return [(c["frame"], c["end_frame"], c["direction"]) for c in changes]


# ---- 動画から測る -------------------------------------------------------------------

def test_changes_and_flashes_land_on_the_frames_where_the_light_was_put(tmp_path):
    values = _strobe(60, [10])
    values[25:28] = [WHITE] * 3                                  # 3 コマ続く閃光（25〜27）
    path = _video(tmp_path / "v.mp4", values)
    report = flashes.measure(path)
    assert _steps(report["changes"]) == [(10, 10, "up"), (11, 11, "down"), (25, 25, "up"), (28, 28, "down")]
    assert [c["t"] for c in report["changes"]] == [0.3333, 0.3667, 0.8333, 0.9333]
    assert all(c["amount"] == pytest.approx(STEP, abs=0.02) for c in report["changes"])
    assert report["changes"][0]["from"] == pytest.approx(DARK / 255, abs=0.02) and report["changes"][0]["to"] == pytest.approx(1.0, abs=0.02)
    assert [(f["frame"], f["return_frame"], f["order"]) for f in report["flashes"]] == [(10, 11, "up_down"), (25, 28, "up_down")]
    assert [(f["t"], f["return_t"]) for f in report["flashes"]] == [(0.3333, 0.3667), (0.8333, 0.9333)]
    assert report["counts"] == {"changes": 4, "up": 2, "down": 2, "flashes": 2, "unpaired_changes": 0}
    assert report["max_per_window"] == 2 and report["status"] == "pass"
    assert report["busiest_window"] == {"start_frame": 10, "start_t": 0.3333, "end_t": 1.3333, "flashes": 2, "changes": 4,
                                        "pairs": [[10, 11], [25, 28]]}
    assert report["video"] == {"name": "v.mp4", "sha256": sha256(path), "fps": 30.0, "frames": 60, "size": [64, 36], "matrix_tag": "bt709"}
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


def test_a_slow_fade_over_a_second_is_not_a_flash(tmp_path):
    """15 コマで明るくなり 15 コマで暗くなる（1 秒）。振れ幅は 0.8 あるが、1 コマあたり 0.053 で delta に届かない。"""
    up = [int(round(25 + (229 - 25) * i / 15)) for i in range(15)]
    values = [25] * 5 + (up + [229] + up[::-1]) * 2 + [25] * 5
    report = flashes.measure(_video(tmp_path / "v.mp4", values))
    assert report["counts"]["changes"] == 0 and report["flashes"] == [] and report["max_per_window"] == 0
    assert report["busiest_window"] is None and report["status"] == "pass"
    # 同じ明暗でも、delta を 1 コマあたりの変化より小さくすれば変化として数える（しきい値で決まることの確かめ）
    assert flashes.measure(tmp_path / "v.mp4", delta=0.04)["counts"]["flashes"] == 2


def test_a_single_scene_change_is_not_a_flash(tmp_path):
    report = flashes.measure(_video(tmp_path / "cut.mp4", [DARK] * 15 + [200] * 15))
    assert _steps(report["changes"]) == [(15, 15, "up")] and report["flashes"] == [] and report["max_per_window"] == 0
    assert report["counts"]["unpaired_changes"] == 1 and report["status"] == "pass"
    # 明るくなって 40 コマ（1 秒以上）後に暗くなる 2 つの切り替えも、明滅にしない
    far = flashes.measure(_video(tmp_path / "far.mp4", [DARK] * 10 + [200] * 40 + [DARK] * 10))
    assert _steps(far["changes"]) == [(10, 10, "up"), (50, 50, "down")] and far["flashes"] == [] and far["counts"]["unpaired_changes"] == 2
    # 1 秒未満で戻れば 1 回（暗 → 明 → 暗）。明 → 暗 → 明 も 1 回
    near = flashes.measure(_video(tmp_path / "near.mp4", [DARK] * 10 + [200] * 20 + [DARK] * 10))
    assert [(f["frame"], f["return_frame"], f["order"]) for f in near["flashes"]] == [(10, 30, "up_down")]
    dip = flashes.measure(_video(tmp_path / "dip.mp4", [200] * 10 + [DARK] * 20 + [200] * 10))
    assert [(f["frame"], f["return_frame"], f["order"]) for f in dip["flashes"]] == [(10, 30, "down_up")] and dip["max_per_window"] == 1


def test_a_rise_over_several_frames_is_one_change(tmp_path):
    values = [DARK] * 5 + [90, 160, 230] + [230] * 5 + [120] * 5
    report = flashes.measure(_video(tmp_path / "v.mp4", values))
    assert _steps(report["changes"]) == [(5, 7, "up"), (13, 13, "down")]
    assert report["changes"][0]["amount"] == pytest.approx((230 - DARK) / 255, abs=0.02)
    assert report["changes"][1]["amount"] == pytest.approx((230 - 120) / 255, abs=0.02)
    assert [(f["frame"], f["return_frame"]) for f in report["flashes"]] == [(5, 13)] and report["max_per_window"] == 1
    assert report["flashes"][0]["amount"] == report["changes"][1]["amount"]        # 明滅の大きさは、2 つの変化の小さい方


def test_brightness_is_the_bt709_luma_of_the_display_values(tmp_path):
    colours = [(0, 0, 0), (0, 255, 0), (0, 0, 0), (0, 0, 255), (255, 0, 0), (255, 255, 255)]
    path = _video(tmp_path / "v.mp4", [c for c in colours for _ in range(4)])
    levels, info, size = flashes.read_levels(path)
    assert len(levels) == 24 and size == (64, 36) and info["fps"] == 30.0
    assert levels[::4] == pytest.approx([0.0, 0.7152, 0.0, 0.0722, 0.2126, 1.0], abs=0.02)
    report = flashes.measure(path)
    # 黒 → 青（+0.0722）は delta に届かない。青 → 赤（+0.14）と赤 → 白は 4 コマ離れているので別の変化
    assert _steps(report["changes"]) == [(4, 4, "up"), (8, 8, "down"), (16, 16, "up"), (20, 20, "up")]
    assert report["changes"][2]["amount"] == pytest.approx(0.2126 - 0.0722, abs=0.02)


def test_frames_are_read_as_bt709_whatever_the_tag_says(tmp_path):
    """BT.709 の行列で書いて smpte170m（BT.601）の印を付けた動画。印に従って読むと緑は 0.733・赤は 0.194 になる。"""
    path = tmp_path / "tagged601.mp4"
    frames = [np.full((36, 64, 3), colour, np.uint8) for colour in [(0, 0, 0)] * 4 + [(0, 255, 0)] * 4 + [(255, 0, 0)] * 4]
    subprocess.run([media.ffmpeg(), "-y", "-loglevel", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x36",
                    "-framerate", "30", "-i", "pipe:0",
                    "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=colorspace=smpte170m",
                    "-c:v", "libx264", "-crf", "12", "-colorspace", "smpte170m", str(path)],
                   input=b"".join(frame.tobytes() for frame in frames), check=True, capture_output=True)
    report = flashes.measure(path)
    assert report["video"]["matrix_tag"] == "smpte170m" and report["read"]["matrix"] == "bt709"
    assert report["changes"][0]["to"] == pytest.approx(0.7152, abs=0.008) and report["changes"][1]["to"] == pytest.approx(0.2126, abs=0.008)


def test_a_light_that_fades_out_slowly_is_only_a_rise(tmp_path):
    """限界の確かめ: 1 コマで上がり、1 コマあたり delta 未満でゆっくり消える光は、戻りが変化にならず明滅に数えない。"""
    decay = [int(round(DARK + 110 * 0.8 ** k)) for k in range(20)]      # +0.43 上がり、最初の戻りは 0.086
    report = flashes.measure(_video(tmp_path / "v.mp4", [DARK] * 10 + decay + [DARK] * 10))
    assert _steps(report["changes"]) == [(10, 10, "up")] and report["flashes"] == []
    assert report["counts"] == {"changes": 1, "up": 1, "down": 0, "flashes": 0, "unpaired_changes": 1}


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


# ---- 数え方（動画なし） --------------------------------------------------------------

def test_a_change_needs_delta_between_two_consecutive_frames():
    assert flashes.find_changes([], 0.1) == [] and flashes.find_changes([0.5], 0.1) == []
    assert flashes.find_changes([0.0, 0.0999, 0.1998], 0.1) == []                    # 2 コマで 0.2 でも、1 コマあたりは届かない
    assert _steps(flashes.find_changes([0.2, 0.3], 0.1)) == [(1, 1, "up")]           # 0.3 - 0.2 は浮動小数で 0.1 に届かないが、境目は含む
    assert _steps(flashes.find_changes([0.3, 0.2], 0.1)) == [(1, 1, "down")]
    assert _steps(flashes.find_changes([0.0, 0.5, 0.0], 0.1)) == [(1, 1, "up"), (2, 2, "down")]


def test_consecutive_changes_in_one_direction_are_merged_and_sized_by_their_sum():
    (change,) = flashes.find_changes([0.0, 0.3, 0.6, 0.9, 0.9], 0.1)
    assert (change["frame"], change["end_frame"], change["direction"]) == (1, 3, "up")
    assert (change["from"], change["to"]) == (0.0, 0.9) and change["amount"] == pytest.approx(0.9)
    # 間に delta 未満のコマが挟まると、続いていないので 2 つの変化（まとめるのは続くコマだけ）
    two = flashes.find_changes([0.0, 0.3, 0.35, 0.65], 0.1)
    assert _steps(two) == [(1, 1, "up"), (3, 3, "up")] and [round(c["amount"], 6) for c in two] == [0.3, 0.3]


def _changes(text):
    """"u5 d8" → 変化の一覧（数え方は向きと始まりのコマだけを使う）。"""
    return [{"frame": int(item[1:]), "direction": "up" if item[0] == "u" else "down"} for item in text.split()]


def test_pairs_are_neighbouring_opposite_changes_less_than_a_second_apart():
    assert flashes.pair([], 30) == [] and flashes.pair(_changes("u5"), 30) == []
    assert flashes.pair(_changes("u5 d6 u10 d11"), 30) == [(0, 1), (2, 3)]
    assert flashes.pair(_changes("u5 d6 u10"), 30) == [(0, 1)]                       # 余った 1 つは組にならない
    assert flashes.pair(_changes("u1 u5 d8"), 30) == [(1, 2)]                        # 同じ向きが続けば、近い方と組む
    assert flashes.pair(_changes("u1 d30"), 30) == [(0, 1)] and flashes.pair(_changes("u1 d31"), 30) == []
    assert flashes.pair(_changes("u0 d29"), 29.97) == [(0, 1)] and flashes.pair(_changes("u0 d30"), 29.97) == []


def test_a_window_is_paired_on_its_own_so_an_earlier_change_cannot_hide_a_flash():
    """暗くなる切り替え（d1）の後に、0.7 秒で 4 回の閃光。前から順の組は切り替えと最初の上がりを組にして 1 つずつずれ、
    閃光の窓（16〜45）に丸ごと入る組が 3 つになる。窓の中だけで組み直すと 4 つで、これを回数にする。"""
    changes = _changes("d1 u16 d18 u22 d24 u28 d30 u34 d36")
    whole = flashes.pair(changes, 30)
    assert whole == [(0, 1), (2, 3), (4, 5), (6, 7)]
    assert sum(1 for a, b in whole if changes[a]["frame"] >= 16 and changes[b]["frame"] < 46) == 3
    assert flashes.busiest(changes, 30) == (4, 1, [(1, 2), (3, 4), (5, 6), (7, 8)])
    assert flashes.busiest([], 30) == (0, None, []) and flashes.busiest(_changes("u5"), 30) == (0, None, [])
    # 窓は始まりのコマから fps コマ未満。4 つ目の戻り（35）が 1 つ目の上がり（5）のちょうど 1 秒後なら、その窓には 3 組しか入らない
    assert flashes.busiest(_changes("u5 d6 u15 d16 u25 d26 u34 d35"), 30)[0] == 3
    assert flashes.busiest(_changes("u5 d6 u15 d16 u25 d26 u33 d34"), 30)[0] == 4
    # 同じ回数なら早い窓
    assert flashes.busiest(_changes("u5 d6 u100 d101"), 30) == (1, 0, [(0, 1)])


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
    assert row["summary"] == "master を測定、変化 10（上がり 5・下がり 5）、明滅 5 回、どの 1 秒でも最大 3 回（上限 3、変化の大きさ 0.1 以上）"
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
    with Image.open(tmp_path / "gate_report" / problem["still"]) as still:
        assert np.asarray(still.convert("L")).mean() > 200
    assert read_json(tmp_path / "gate_report/flash_report.json")["status"] == "fail"


def test_gate_check_measures_the_master_when_there_is_one(tmp_path):
    calm, busy = [DARK] * 45, _strobe(45, [5, 12, 20, 27])
    path, config = _run_dir(tmp_path / "a", calm, master=busy)
    path.write_text(json.dumps({**config, "flash": {}}), encoding="utf-8")
    row = gate.run(path)[0]["checks"][9]
    assert row["status"] == "fail" and row["summary"].startswith("master を測定")
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
