"""`mv fx quick`（一枚絵 → spec・timeline・hooks・plan.md）: 構成・寄りの上限・クロスフェードなし・明滅の数・手で直したファイルの扱い・
顔が端で切れる時間・山場の後の引き・閃光と閃きと火の粉の大きさ。"""
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import fx_synth  # noqa: E402
from mv_pipeline.fx import camera, quick, sequence  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402


def picture(path, width, height, seed=0):
    """なめらかなむらに細い線を足した絵（寄りのぼけと止まったコマが分かる程度の細かさ）。"""
    rng = np.random.default_rng(seed)
    base = cv2.GaussianBlur(rng.uniform(0, 255, (height, width, 3)).astype(np.float32), (0, 0), 9)
    image = np.clip((base - base.mean()) * 3 + 110, 0, 255).astype(np.uint8)
    for x in range(0, width, 37):
        cv2.line(image, (x, 0), (x, height - 1), (210, 200, 180), 1)
    cv2.imencode(path.suffix, image)[1].tofile(str(path))
    return path


def camera_path(layout, image_size, size):
    raw = {"keys": layout["keys"], "frame": "source", "clamp": False, "plate": "full"}
    return camera.CameraPath(fxspec.parse_spec({"schema_version": "fx-shot/0.3", "output": {"width": size[0], "height": size[1]},
                                                "duration_s": layout["seconds"], "assets": {"base": "b.png"}, "camera": raw}, ".")
                             .data["camera"], layout["seconds"], size, plate=image_size)


LANDSCAPE = dict(focus=(0.30, 0.35), path=((0.20, 0.70), (0.70, 0.40)), impact=(0.72, 0.38))


@pytest.mark.parametrize("image_size, size, points", [
    ((1600, 900), (1920, 1080), LANDSCAPE),
    ((3200, 1800), (1920, 1080), LANDSCAPE),
    ((1200, 1800), (1080, 1920), dict(focus=(0.55, 0.22), path=((0.35, 0.40), (0.62, 0.66)), impact=(0.64, 0.70))),
    ((1200, 1800), (1080, 1920), {}),
    ((2048, 1152), (1920, 1080), dict(impact=(0.9, 0.95))),
    ((3000, 2000), (1920, 1080), dict(focus=(0.05, 0.1), path=((0.1, 0.1), (0.95, 0.9)))),
    ((900, 1600), (1080, 1920), dict(focus=(0.5, 0.3), impact=(0.5, 0.8))),
])
def test_layout_keeps_the_view_inside_and_the_close_up_within_the_pixel_limit(image_size, size, points):
    layout = quick.compose(image_size, size, 12.0, **points)
    times = [key["t"] for key in layout["keys"]]
    assert times == sorted(set(times)) and times[0] == 0.0
    path = camera_path(layout, image_size, size)
    assert path.out_of_bounds(samples=241) is None                         # どのキーも、キーの間も視野は画像の中（clamp で曲がらない）
    cover = max(size[0] / image_size[0], size[1] / image_size[1])
    worst = max(path.at(i / 24)[0] for i in range(12 * 24 + 1)) * cover
    assert worst <= quick.MAX_MAGNIFICATION + 1e-6 or (cover > quick.MAX_MAGNIFICATION and worst == pytest.approx(cover))
    assert layout["phases"]["pull"][1] > layout["phases"]["fade"][0]       # 全景に着くのは暗転の途中（最後まで止めない）


def test_layout_caps_the_zoom_and_says_so():
    layout = quick.compose((1200, 1800), (1080, 1920), zoom=2.6, focus=(0.5, 0.3))
    assert layout["close"] == pytest.approx(1.5 / max(1080 / 1200, 1920 / 1800), abs=1e-4)
    assert layout["magnification"] == pytest.approx(1.5, abs=1e-3)
    assert any("1.5 倍" in w and "2.6" in w for w in layout["warnings"])
    assert quick.compose((1200, 1800), (1080, 1920), focus=(0.5, 0.3))["warnings"] == []     # 既定の寄りを止めても注意は出さない
    small = quick.compose((600, 900), (1080, 1920))
    assert small["close"] == 1.0 and "--size" in small["warnings"][0]


def test_layout_orders_the_steps_and_moves_the_climax():
    full = quick.compose((3200, 1800), (1920, 1080), 12.0, **LANDSCAPE)
    phases = full["phases"]
    assert phases["close"][1] == phases["move"][0] < phases["move"][1] == phases["impact"] < phases["pull"][0]
    assert len(full["points"]) == 4                                                    # 寄り → 線の始まり → 線の終わり → 山場
    assert full["points"][0] == LANDSCAPE["focus"]
    hit = quick._view_of(full["points"][-1], full["moving"], full["cover"], (1920, 1080), (3200, 1800))
    assert hit[0] < LANDSCAPE["impact"][0] < hit[2] and hit[1] < LANDSCAPE["impact"][1] < hit[3]   # 山場の点は山場の画面の中
    later = quick.compose((3200, 1800), (1920, 1080), 12.0, impact_at=6.0, **LANDSCAPE)
    arrive = [key["t"] for key in later["keys"]].index(6.0)
    assert later["phases"]["impact"] == 6.0 and later["keys"][arrive + 1]["t"] == 6.0 + quick.SETTLE_S
    moving = (lambda p: p["move"][1] - p["move"][0])
    assert moving(later["phases"]) == pytest.approx(moving(phases), abs=1e-3)          # 遅らせても移る速さは同じ（寄りが延びる）
    sooner = quick.compose((3200, 1800), (1920, 1080), 12.0, impact_at=3.0, **LANDSCAPE)
    assert moving(sooner["phases"]) < moving(phases)                                   # 早めると速く動く
    alone = quick.compose((1200, 1800), (1080, 1920), impact=(0.5, 0.8))
    assert alone["phases"]["move"] is None and alone["phases"]["impact"] == alone["phases"]["close"][1]
    plain = quick.compose((1920, 1080), (1920, 1080))
    assert plain["default_focus"] and plain["phases"]["impact"] is None and plain["start"] == (0.5, 0.45)
    with pytest.raises(PipelineError, match="--impact-at"):
        quick.compose((1200, 1800), (1080, 1920), impact_at=5.0)
    with pytest.raises(PipelineError, match="--seconds"):
        quick.compose((1200, 1800), (1080, 1920), 2.0)
    with pytest.raises(PipelineError, match="--impact-at"):
        quick.compose((1200, 1800), (1080, 1920), impact=(0.5, 0.8), impact_at=11.5)


def test_arguments_are_checked():
    assert quick.parse_point("0.4,0.25", "--focus") == (0.4, 0.25)
    assert quick.parse_path("0.1,0.2:0.3,0.9") == ((0.1, 0.2), (0.3, 0.9))
    assert quick.parse_size("1080x1920") == (1080, 1920)
    for bad, call in (("0.4", lambda v: quick.parse_point(v, "--focus")), ("1.2,0.3", lambda v: quick.parse_point(v, "--impact")),
                      ("0.1,0.2", quick.parse_path), ("1080x1921", quick.parse_size), ("big", quick.parse_size)):
        with pytest.raises(PipelineError):
            call(bad)
    assert quick.default_size((1200, 1800)) == (1080, 1920) and quick.default_size((1024, 1024)) == (1920, 1080)


def test_the_set_is_one_shot_without_crossfades_and_the_hooks_load(tmp_path):
    image = picture(tmp_path / "art.png", 600, 900)
    summary = quick.run(image, tmp_path / "out", focus=(0.4, 0.25), path=((0.35, 0.5), (0.6, 0.8)), impact=(0.62, 0.82),
                        size=(216, 384), seconds=6.0, fps=12)
    out = tmp_path / "out"
    assert summary["made"] == "written" and all((out / name).is_file() for name in ("shot.json", "timeline.json", "hooks.py", "plan.md"))
    timeline = sequence.load_timeline(out / "timeline.json")
    shots = timeline.data["shots"]
    assert len(shots) == 1 and shots[0]["transition_in"]["type"] == "black"          # 同じ絵の寄りをクロスフェードでつながない
    spec = json.loads((out / "shot.json").read_text(encoding="utf-8"))
    assert spec["camera"]["frame"] == "source" and spec["camera"]["plate"] == "full" and spec["camera"]["clamp"] is True
    assert spec["assets"] == {"base": "image.png"} and (out / "image.png").read_bytes() == image.read_bytes()
    source = (out / "hooks.py").read_text(encoding="utf-8")
    assert "IMPACT = (0.62, 0.82)" in source and "IMPACT_T = " in source
    compile(source, "hooks.py", "exec")
    enabled = source.replace("    # impact.glint", "    impact.glint").replace("    # fire.embers", "    fire.embers")
    (out / "hooks_all.py").write_text(enabled, encoding="utf-8")                     # 任意の行も動く
    from mv_pipeline.fx.hooks import Hooks
    ctx = sequence.Context(timeline, 1.0, Hooks(out / "hooks_all.py"))
    try:
        frames = [ctx.frame(t) for t in (0.2, 1.3, summary["phases"]["impact"] + 0.1, 5.9)]
    finally:
        ctx.close()
    assert all(frame.shape == (384, 216, 3) for frame in frames)
    plan = (out / "plan.md").read_text(encoding="utf-8")
    assert "変えるとよい所" in plan and "--preview" in plan and "docs/recipes/one-image.md" in plan


def test_render_preview_and_one_flash(tmp_path):
    from mv_pipeline.gate import flashes
    image = picture(tmp_path / "art.jpg", 1600, 900, seed=4)
    summary = quick.run(image, tmp_path / "q", impact=(0.6, 0.7), size=(256, 144), seconds=5.0, fps=12, preview=True, render=True)
    out = tmp_path / "q"
    assert summary["render"]["technical_pass"] is True and (out / "preview.jpg").is_file()
    sheet = cv2.imread(str(out / "preview.jpg"))
    assert sheet is not None and sheet.shape[1] <= 1600
    report = flashes.measure(summary["render"]["video"])
    assert report["max_per_window"] <= 3 and report["counts"]["flashes"] <= 1          # 明滅は山場の 1 回まで
    inspect = json.loads((out / "inspect.json").read_text(encoding="utf-8"))
    clip = inspect["clips"][0]
    assert (out / clip["path"]).is_file() and clip["cues"]["lights"][1][0] == summary["phases"]["impact"]
    from mv_pipeline.inspection import config as inspect_config
    inspect_config.load(out / "inspect.json")                                          # 検品の設定として読める
    check = summary["check"]                                                           # 描いた後の確認は 1 回で出る
    assert (out / "check.jpg").is_file() and json.loads((out / "check.json").read_text(encoding="utf-8")) == check
    assert check["flashes"]["status"] == "pass" and check["flashes"]["flashes"] <= 1 and check["frozen"] == []
    assert check["magnification_max"] <= quick.MAX_MAGNIFICATION + 1e-3 and set(check["difference"]) == {"head", "closest", "impact", "end"}
    assert check["edge"]["seconds"] == max(summary["edge"]["seconds"].values())
    sheet = cv2.imread(str(out / "check.jpg"))
    assert sheet is not None and sheet.shape[1] <= 1600
    text = quick.summary_text(summary)
    assert "check.jpg" in text and "明滅 pass" in text and "止まったコマ なし" in text and len(text.encode("utf-8")) < 1500
    log = (out / "log.md").read_text(encoding="utf-8")
    assert log.count("## ") == 1 and "描画: fx_runs/" in log and "所要:" in log


def test_hand_edits_are_kept_or_protected(tmp_path):
    image = picture(tmp_path / "art.png", 600, 900)
    out = tmp_path / "out"
    quick.run(image, out, focus=(0.5, 0.3), size=(216, 384), seconds=6.0, fps=12)
    hooks = out / "hooks.py"
    hooks.write_text(hooks.read_text(encoding="utf-8").replace("SHAKE = 0.012", "SHAKE = 0.02"), encoding="utf-8")
    kept = quick.run(image, out)                                                       # 指定なし: 今のファイルのまま
    assert kept["made"] == "kept" and kept["edited"] == ["hooks.py"] and "SHAKE = 0.02" in hooks.read_text(encoding="utf-8")
    assert quick.run(image, out, focus=(0.5, 0.3), size=(216, 384), seconds=6.0, fps=12)["made"] == "kept"   # 同じ引数
    with pytest.raises(PipelineError, match="手で直したファイル.*hooks.py"):
        quick.run(image, out, focus=(0.4, 0.3), size=(216, 384), seconds=6.0, fps=12)
    rewritten = quick.run(image, out, focus=(0.4, 0.3), size=(216, 384), seconds=6.0, fps=12, force=True)
    assert rewritten["made"] == "written" and "SHAKE = 0.012" in hooks.read_text(encoding="utf-8")
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "shot.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PipelineError, match="記録の無いファイル"):
        quick.run(image, stray)


def test_arguments_carry_over_and_none_removes_a_point(tmp_path):
    image = picture(tmp_path / "art.png", 600, 900)
    out = tmp_path / "out"
    first = quick.run(image, out, focus=(0.5, 0.3), path=((0.5, 0.4), (0.5, 0.8)), impact=(0.5, 0.85), impact_at=3.0,
                      size=(216, 384), seconds=6.0, fps=12)
    assert first["phases"]["impact"] == 3.0
    zoomed = quick.run(image, out, zoom=1.2)                                           # 変える所だけ書く
    assert zoomed["made"] == "written" and zoomed["args"]["focus"] == [0.5, 0.3] and zoomed["args"]["zoom"] == 1.2
    assert zoomed["phases"]["move"] is not None and zoomed["size"] == [216, 384]
    plain = quick.run(image, out, path=quick.parse_path("none"), impact=quick.parse_point("none", "--impact"))
    assert "path" not in plain["args"] and "impact" not in plain["args"] and "impact_at" not in plain["args"]
    assert plain["phases"]["move"] is None and plain["phases"]["impact"] is None and "IMPACT = None" in (out / "hooks.py").read_text("utf-8")
    other = picture(tmp_path / "other.png", 1600, 900, seed=5)
    fresh = quick.run(other, out, force=True)                                          # 絵が違えば引き継がない
    assert fresh["args"] == {} and fresh["size"] == [1920, 1080]


def test_cli_quick_prints_a_short_summary(tmp_path):
    image = picture(tmp_path / "art.png", 640, 360)
    code, result, _ = fx_synth.run_cli("fx", "quick", image, tmp_path / "o", "--size", "320x180", "--seconds", "5", "--fps", "12",
                                       "--focus", "0.3,0.4", "--zoom", "9", "--json")
    assert code == 0 and result["made"] == "written" and result["size"] == [320, 180]
    assert any("1.5 倍" in w for w in result["warnings"]) and len(json.dumps(result, ensure_ascii=False)) < 2500
    import io
    import contextlib
    from mv_pipeline import cli
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert cli.main(["fx", "quick", str(image), str(tmp_path / "o"), "--zoom", "1.2"]) == 0
    text = stdout.getvalue()                                                           # 既定は数行の文（JSON は --json）
    assert text.startswith("mv fx quick: ") and len(text.splitlines()) <= 10 and len(text.encode("utf-8")) < 1200
    log = (tmp_path / "o" / "log.md").read_text(encoding="utf-8")
    assert log.count("## ") == 2 and "- 引数: --zoom 1.2" in log                    # 実行のたびに作業記録へ足す
    code, _, err = fx_synth.run_cli("fx", "quick", image, tmp_path / "o2", "--focus", "2,2")
    assert code == 1 and "0..1" in err


# 既定の形で、寄りの点のまわり（顔ほど）が画面の端で切れたまま続く時間（縦の絵・横の絵・縦横の違う出力、いくつかの点の位置）
FACE_CASES = [
    ((2000, 3000), (1080, 1920), dict(focus=(0.40, 0.22), impact=(0.62, 0.78))),
    ((2000, 3000), (1080, 1920), dict(focus=(0.62, 0.30), impact=(0.30, 0.70))),
    ((1200, 1800), (1080, 1920), dict(focus=(0.50, 0.20), path=((0.50, 0.40), (0.70, 0.65)), impact=(0.70, 0.80))),
    ((1200, 1800), (1080, 1920), dict(focus=(0.35, 0.25), path=((0.25, 0.55), (0.75, 0.60)))),
    ((1200, 1800), (1080, 1920), {}),
    ((3200, 1800), (1920, 1080), LANDSCAPE),
    ((3200, 1800), (1920, 1080), dict(focus=(0.70, 0.30), impact=(0.25, 0.75))),
    ((1920, 1080), (1920, 1080), dict(focus=(0.50, 0.30), impact=(0.80, 0.70))),
    ((2400, 1350), (1920, 1080), dict(focus=(0.20, 0.40), path=((0.30, 0.60), (0.85, 0.55)), impact=(0.85, 0.30))),
    ((3200, 1800), (1080, 1920), dict(focus=(0.45, 0.30), impact=(0.60, 0.80))),
]


@pytest.mark.parametrize("image_size, size, points", FACE_CASES)
def test_default_layouts_keep_the_face_cut_short(image_size, size, points):
    layout = quick.compose(image_size, size, 12.0, **points)
    assert max(layout["edge"].values()) <= quick.EDGE_LIMIT_S
    assert not any("切れたまま" in w for w in layout["warnings"])
    assert quick.camera_path(layout, image_size, size).out_of_bounds(samples=241) is None


def test_edge_time_counts_the_cut_frames_of_each_step():
    plain = quick.compose((1200, 1800), (1080, 1920), 12.0)
    assert plain["edge"] == {"close": 0.0, "pull": 0.0}
    view, face = (0.0, 0.0, 1.0, 1.0), (0.9, 0.4, 1.3, 0.6)                          # 横の 3/4 が画面の外
    assert quick._cut(view, face) and not quick._cut(view, (0.97, 0.4, 1.3, 0.6))   # 見えるのが 1 割より少なければ数えない
    assert not quick._cut(view, (-0.01, 0.4, 0.39, 0.6))                              # 外に出たのが 1 割より少なければ数えない


def test_a_face_that_stays_cut_is_warned_with_a_fix(tmp_path):
    layout = quick.compose((1200, 1800), (1080, 1920), 12.0, focus=(0.5, 0.3), focus_radius=0.35)
    assert layout["edge"]["close"] > quick.EDGE_LIMIT_S
    assert any("寄りの段" in w and "--zoom" in w for w in layout["warnings"])
    with pytest.raises(PipelineError, match="--focus-radius"):
        quick.compose((1200, 1800), (1080, 1920), focus_radius=0.0)
    image = picture(tmp_path / "art.png", 600, 900)
    summary = quick.run(image, tmp_path / "out", focus=(0.5, 0.3), size=(216, 384), seconds=6.0, fps=12, focus_radius=0.35)
    plan = (tmp_path / "out" / "plan.md").read_text(encoding="utf-8")
    assert summary["edge"]["seconds"]["close"] > quick.EDGE_LIMIT_S and summary["edge"]["limit"] == quick.EDGE_LIMIT_S
    assert "顔の切れ" in plan and "切れたまま" in plan and "--focus-radius" in plan
    assert quick.run(image, tmp_path / "out", zoom=1.2)["args"]["focus_radius"] == 0.35          # 引数は引き継ぐ


def test_the_pull_after_the_climax_recoils_fast_then_slows_down():
    layout = quick.compose((2000, 3000), (1080, 1920), 12.0, focus=(0.40, 0.22), impact=(0.62, 0.78))
    keys, (start, end) = layout["keys"], layout["recoil"]
    times = [key["t"] for key in keys]
    hold, recoil = keys[times.index(start)], keys[times.index(start) + 1]
    assert start == layout["phases"]["pull"][0] and recoil["t"] == pytest.approx(end, abs=1e-3)
    assert 0.9 <= end - start <= quick.RECOIL_S + 1e-9
    fast = math.log(hold["zoom"] / recoil["zoom"]) / (end - start)                    # 頭の 1 秒ほどで zoom の 7 割ほど（対数で）
    slow = math.log(recoil["zoom"]) / (layout["phases"]["pull"][1] - end)            # 残りはゆっくり全景へ
    assert math.log(hold["zoom"] / recoil["zoom"]) >= 0.65 * math.log(hold["zoom"]) and slow < 0.25 * fast
    assert layout["recoil"] in quick.inspect_data(layout, "v.mp4")["clips"][0]["cues"]["moves"]   # 検品の cues にも入れる


def test_a_path_start_out_of_reach_moves_onto_the_line_without_a_sharp_turn():
    image_size = (3200, 1800)
    line = ((0.04, 0.92), (0.60, 0.55))
    layout = quick.compose(image_size, (1920, 1080), 12.0, focus=(0.35, 0.30), path=line, impact=(0.70, 0.50))
    assert any("--path の始まり 0.04,0.92" in w and "移しました" in w for w in layout["warnings"])
    moved = layout["points"][1]
    cross = (moved[0] - line[0][0]) * (line[1][1] - line[0][1]) - (moved[1] - line[0][1]) * (line[1][0] - line[0][0])
    assert abs(cross) < 1e-6 and line[0][0] < moved[0] < line[1][0]                  # 線の上の、届く所へ
    move = layout["phases"]["move"]
    route = [key for key in layout["keys"] if move[0] <= key["t"] <= move[1]]
    for a, b, c in zip(route, route[1:], route[2:]):                                  # 短い区間の急な折り返しを作らない
        sharp = quick._turn(a["center"], b["center"], c["center"], image_size) > quick.SHARP_TURN
        assert not sharp or min(b["t"] - a["t"], c["t"] - b["t"]) >= 0.35


def test_the_flash_is_weaker_on_bright_pictures(tmp_path):
    assert quick.flash_strength(30) == quick.FLASH_DARK and quick.flash_strength(230) == quick.FLASH_BRIGHT
    assert quick.flash_strength(60) > quick.flash_strength(120) > quick.flash_strength(180)
    for name, level in (("dark", 40), ("bright", 215)):
        cv2.imwrite(str(tmp_path / f"{name}.png"), np.full((900, 600, 3), level, np.uint8))
        quick.run(tmp_path / f"{name}.png", tmp_path / name, focus=(0.5, 0.3), impact=(0.5, 0.8), size=(216, 384), seconds=6.0, fps=12)

    def flash(name):
        return float(re.search(r"^FLASH = ([0-9.]+)", (tmp_path / name / "hooks.py").read_text(encoding="utf-8"), re.M).group(1))

    assert flash("dark") >= 0.6 and flash("bright") <= 0.35
    assert f"{flash('bright'):g}・0.012" in (tmp_path / "bright" / "plan.md").read_text(encoding="utf-8")


def test_hooks_sizes_follow_the_face_and_the_screen_and_only_constants_change():
    points = dict(focus=(0.40, 0.22), impact=(0.62, 0.78))
    portrait = quick.compose((2000, 3000), (1080, 1920), 12.0, **points)
    values = quick.hooks_values(portrait, (2000, 3000), (1080, 1920))
    bigger = quick.compose((2000, 3000), (1080, 1920), 12.0, focus_radius=0.15, **points)
    assert quick.hooks_values(bigger, (2000, 3000), (1080, 1920))["GLINT_K"] == pytest.approx(2 * values["GLINT_K"], abs=0.011)
    landscape = quick.compose((3200, 1800), (1920, 1080), 12.0, **LANDSCAPE)
    assert quick.hooks_values(landscape, (3200, 1800), (1920, 1080))["EMBERS"] > values["EMBERS"]   # 横の画面は下の端が長い
    later = quick.compose((2000, 3000), (1080, 1920), 12.0, impact_at=6.5, **points)
    a = quick.hooks_source(portrait, (2000, 3000), (1080, 1920), 90.0)
    b = quick.hooks_source(later, (2000, 3000), (1080, 1920), 200.0)

    def body(text):
        return [line for line in text.splitlines() if not re.match(r"^[A-Z_][A-Z_, ]* = ", line)]

    assert a != b and body(a) == body(b)                                               # やり直しで変わるのは定数の値だけ
    assert "    # impact.glint(img, *view.point(*FOCUS), t, GLINT_T, k=view.px * GLINT_K)" in body(a)
    assert any(line.startswith("    # fire.embers(") and "EMBERS" in line for line in body(a))


def screen_speeds(layout, image_size, size, fps=24):
    """コマごとのカメラの速さ（画面の対角線の割合/秒。zoom は画面の対角線の 1/4 の所の動き、中心は画面での動きで数える）。"""
    path = quick.camera_path(layout, image_size, size)
    cover, diag = max(size[0] / image_size[0], size[1] / image_size[1]), math.hypot(*size)
    states = [path.at(i / fps) for i in range(int(round(layout["seconds"] * fps)) + 1)]
    out = []
    for (z0, x0, y0), (z1, x1, y1) in zip(states, states[1:]):
        zoom_px = abs(math.log(z1 / z0)) * 0.25 * diag
        center_px = math.hypot((x1 - x0) * image_size[0], (y1 - y0) * image_size[1]) * cover * z1
        out.append(math.hypot(zoom_px, center_px) * fps / diag)
    return np.array(out)


@pytest.mark.parametrize("image_size, size, points", [
    ((1600, 900), (1920, 1080), dict(focus=(0.62, 0.33))),
    ((1200, 1800), (1080, 1920), {}),
    ((1600, 900), (1920, 1080), dict(focus=(0.62, 0.33), impact=(0.62, 0.88))),
    ((2000, 3000), (1080, 1920), dict(focus=(0.40, 0.22), impact=(0.62, 0.78))),
    ((3200, 1800), (1920, 1080), dict(focus=(0.30, 0.35), path=((0.30, 0.60), (0.65, 0.55)))),
    ((2000, 3000), (1080, 1920), dict(focus=(0.50, 0.25), path=((0.30, 0.50), (0.70, 0.80)))),
])
def test_the_camera_eases_without_stops_or_sudden_turns(image_size, size, points):
    layout = quick.compose(image_size, size, 12.0, **points)
    speeds = screen_speeds(layout, image_size, size)
    p, fps = layout["phases"], 24
    for i in range(int(quick.OPEN_S * fps), len(speeds) - 1):
        t = (i + 1) / fps
        if (p["impact"] is not None and p["impact"] - 0.1 <= t <= p["impact"] + 0.7) or t > p["pull"][1] - 0.05:
            continue                                     # 山場の揺れの間（閃光と揺れが動く）と、全景に着いた後（暗転の中）は見ない
        around = speeds[max(0, i - 12):i + 13]
        assert abs(speeds[i + 1] - speeds[i]) <= 0.4 * around.max()            # 速さは 1 コマで急に変わらない（緩急は曲線で）
        assert speeds[i] >= 0.015 or speeds[i] >= 0.2 * float(np.median(around))   # 動いている所の近くで止まらない
    assert speeds[int(quick.OPEN_S * fps):int(p["pull"][1] * fps) - 2].min() > 0.001   # 全景に着くまでカメラは動き続ける


def test_the_quiet_type_uses_a_soft_light_instead_of_the_climax(tmp_path):
    image = picture(tmp_path / "art.png", 1600, 900, seed=2)
    out = tmp_path / "quiet"
    summary = quick.run(image, out, focus=(0.6, 0.35), size=(320, 180), seconds=5.0, fps=12, render=True)
    source = (out / "hooks.py").read_text(encoding="utf-8")
    assert "IMPACT = None" in source and re.search(r"^LIGHT = 0\.\d+", source, re.M) and summary["kind"].startswith("山場なし")
    assert summary["check"]["flashes"]["flashes"] == 0 and summary["check"]["frozen"] == []      # 閃光も止まったコマもない
    from mv_pipeline.fx.hooks import Hooks
    timeline = sequence.load_timeline(out / "timeline.json")
    peak = float(re.search(r"^LIGHT_T, LIGHT_R = \(([0-9.]+),", source, re.M).group(1))
    lit, plain = sequence.Context(timeline, 1.0, Hooks(out / "hooks.py")), sequence.Context(timeline, 1.0, None)
    try:
        a, b = lit.frame(peak), plain.frame(peak)
    finally:
        lit.close()
        plain.close()
    view = quick.camera_path(quick.compose((1600, 900), (320, 180), 5.0, (0.6, 0.35)), (1600, 900), (320, 180))
    from mv_pipeline.fx.view import View
    x, y = View(view, (0, 0, 1, 1), (1600, 900), (320, 180), peak).point(0.6, 0.35)
    near = (slice(max(0, int(y) - 6), int(y) + 6), slice(max(0, int(x) - 6), int(x) + 6))
    assert 2.0 < float(a[near].mean()) - float(b[near].mean()) < 40.0                         # 寄りの点が少し明るくなる（控えめ）
    plan = (out / "plan.md").read_text(encoding="utf-8")
    assert "山場なし（静かな型）" in plan and "LIGHT" in plan and "山場の無い絵には付けない" in plan


def test_plan_lists_the_zoom_limit_per_size_and_how_to_choose(tmp_path):
    small = quick.compose((1600, 900), (1920, 1080), 12.0, focus=(0.5, 0.4))
    plan = quick.plan_text(small, (1600, 900), (1920, 1080), 24, ("IMG", "OUT"), "image.png")
    assert "1920x1080 なら 1.25 倍・1280x720 なら 1.88 倍" in plan
    assert "1.4 倍に届かない" in plan and "--size 1280x720" in plan and "何通りも描いて比べなくてよい" in plan
    large = quick.compose((3200, 1800), (1920, 1080), 12.0, focus=(0.5, 0.4))
    plan = quick.plan_text(large, (3200, 1800), (1920, 1080), 24, ("IMG", "OUT"), "image.png")
    assert "1920x1080 なら 2.50 倍" in plan and "届かない" not in plan
    assert quick.zoom_caps((1080, 1920), (1080, 1920))[1] == ((720, 1280), pytest.approx(1.5 / (1280 / 1920)))


def test_frozen_frames_follow_the_inspect_rule():
    from mv_pipeline.fx import quick_check
    moving = [math.nan] + [0.004] * 30
    assert quick_check.frozen(moving, 24) == []
    stopped = [math.nan] + [0.004] * 12 + [0.0001] * 4 + [0.004] * 12          # 動いている所で 4 コマ同じ絵
    assert quick_check.frozen(stopped, 24) == [(pytest.approx(12 / 24, abs=1e-3), pytest.approx(16 / 24, abs=1e-3))]
    slow = [math.nan] + [0.001] * 12 + [0.0001] * 4 + [0.001] * 12             # 全体がゆっくりなら止まったとは読まない
    assert quick_check.frozen(slow, 24) == []


def test_a_picture_too_small_to_zoom_keeps_every_key_at_zoom_one_or_more(tmp_path):
    # 寄れない絵（寄りが zoom 1 で止まる）に山場や線を付けても、カメラのキーは schema の下限（zoom 1）を割らない
    image = picture(tmp_path / "small.png", 600, 900)
    for name, points in (("impact", dict(impact=(0.5, 0.8))),
                         ("all", dict(zoom=1.0, focus=(0.5, 0.3), path=((0.4, 0.5), (0.6, 0.8)), impact=(0.6, 0.85)))):
        quick.run(image, tmp_path / name, **points)
        keys = json.loads((tmp_path / name / quick.SPEC).read_text(encoding="utf-8"))["camera"]["keys"]
        assert min(key["zoom"] for key in keys) >= 1.0


@pytest.mark.parametrize(("edited_output", "render_size", "render_fps", "inspect_end"), [
    ({"fps": 6}, (256, 144), 6, 29),
    ({}, (256, 144), 24, 115),
    ({"fps": 23.976}, (256, 144), 23.976, 115),
    ({"fps": 0.5}, (256, 144), 0.5, 2),
    ({"fps": 12, "width": 295, "height": 167}, (296, 168), 12, 58),
])
def test_render_checks_with_the_output_of_an_edited_timeline(tmp_path, edited_output, render_size, render_fps, inspect_end):
    image = picture(tmp_path / "art.jpg", 640, 360, seed=4)
    out = tmp_path / "q"
    quick.run(image, out, impact=(0.6, 0.7), size=(256, 144), seconds=5.0, fps=30)
    timeline = json.loads((out / quick.TIMELINE).read_text(encoding="utf-8"))
    timeline["output"].pop("fps")
    timeline["output"].update(edited_output)
    (out / quick.TIMELINE).write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = quick.run(image, out, render=True)
    assert summary["render"]["technical_pass"] and summary["check"]
    manifest = json.loads(Path(summary["render"]["video"]).with_name("manifest.json").read_text(encoding="utf-8"))
    assert manifest["output"]["size"] == list(render_size)
    assert manifest["interval"]["fps"] == render_fps
    inspect = json.loads((out / quick.INSPECT).read_text(encoding="utf-8"))
    assert inspect["clips"][0]["frames"] == [0, inspect_end]


def test_render_checks_at_least_one_frame_for_a_short_edited_timeline(tmp_path):
    image = picture(tmp_path / "art.jpg", 320, 180)
    out = tmp_path / "q"
    quick.run(image, out, size=(160, 90), seconds=5.0, fps=12)
    timeline = json.loads((out / quick.TIMELINE).read_text(encoding="utf-8"))
    timeline["duration_s"] = 0.02
    timeline["shots"][0]["transition_in"] = {"type": "cut"}
    (out / quick.TIMELINE).write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = quick.run(image, out, render=True)
    assert summary["render"]["technical_pass"]
    assert set(summary["check"]["times"].values()) == {0.0}
    inspect = json.loads((out / quick.INSPECT).read_text(encoding="utf-8"))
    assert inspect["clips"][0]["frames"] == [0, 1]
    reused = quick.run(image, out, render=True)
    assert reused["render"]["reused"] and reused["check"]["times"] == summary["check"]["times"]
