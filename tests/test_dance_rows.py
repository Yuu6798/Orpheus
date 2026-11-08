"""performer.rows: fit を使わないクリップでも、人物の足元と中心の列を hooks と層の API に渡す（DANCE-HOOK-1）。"""
import json
import warnings

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from PIL import Image  # noqa: E402

from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import api, clips, compose, fit  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import render as fxrender  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

KEY = {"color": [0.0, 255.0, 0.0], "tolerance": 0.22, "softness": 0.12, "spill": 1.0, "choke_px": 0.0, "feather_px": 0.0}
GREEN = (0, 255, 0)
W, H = 80, 120


def person(bottom, left=30, width=20, height=60):
    """緑の背景に箱の人物。動画の色は 2×2 で間引かれる（4:2:0）ので、下端は奇数の行にする（偶数だと 1 行下まで色がにじむ）。"""
    image = np.full((H, W, 3), GREEN, np.uint8)
    image[bottom - height + 1:bottom + 1, left:left + width] = (200, 60, 120)
    return image


def write_video(path, frames, fps=12):
    fxrender.write_video(path, iter(frames), (frames[0].shape[1], frames[0].shape[0]), fps, 0, len(frames))


def spec(tmp_path, **performer):
    Image.fromarray(np.full((320, 180, 3), 40, np.uint8)).save(tmp_path / "stage.png")
    raw = {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 180, "height": 320, "fps": 12}, "duration_s": 2.0,
           "stage": {"path": "stage.png"},
           "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 1}, {"path": "end.png", "start": 1, "end": 2}],
                         "key": {"color": [0, 255, 0], "choke_px": 0, "feather_px": 0}, **performer}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


# ---- 測定と式 ------------------------------------------------------------------------

def test_measure_rows_takes_the_lowest_pixel_row_and_the_median_x(tmp_path):
    frames = [person(91), person(95, left=10, width=11), np.full((H, W, 3), GREEN, np.uint8), person(91)]
    write_video(tmp_path / "a.mp4", frames)
    measured = fit.measure_rows(clips.Source(tmp_path / "a.mp4"), KEY, 1.0, skip=[[3, 4]])
    assert measured["frames"] == 4 and measured["bottom"] == [91.0, 95.0, None, None]
    assert measured["cx"][:2] == [39.5, 15.0] and measured["cx"][2:] == [None, None]
    half = fit.measure_rows(clips.Source(tmp_path / "a.mp4"), KEY, 0.5)
    assert half["bottom"][0] in (88.0, 90.0, 92.0) and half["scale"] == 0.5    # 縮小の画素の行 × 2（元動画の画素）


def _production_stats(bottoms, centers, used, k=10, box=9):
    """非公開の制作の stats() の式（写し）: 欠けは前後で埋め、使う範囲の外は端で止め、前後 k の最大を box コマの平均でならす。"""
    b = np.array([np.nan if v is None else v for v in bottoms], dtype=np.float64)
    ok = ~np.isnan(b)
    b = np.interp(np.arange(len(b)), np.where(ok)[0], b[ok])
    i0, i1 = used
    b[:i0] = b[i0]
    b[i1 + 1:] = b[i1]
    cxs = [np.nan if v is None else v for v in centers][i0:i1 + 1]
    g = np.array([b[max(0, i - k):i + k + 1].max() for i in range(len(b))])
    g = np.convolve(np.pad(g, box // 2, mode="edge"), np.ones(box) / box, mode="valid")
    return g, float(np.nanmedian(cxs))


@pytest.mark.parametrize("used, k, box", [((0, 59), 10, 9), ((7, 41), 10, 9), ((0, 59), 3, 5), ((12, 12), 0, 1)])
def test_rows_follow_the_production_formula(used, k, box):
    rng = np.random.default_rng(5)
    bottoms = [None if rng.random() < 0.1 else float(rng.integers(500, 900)) for _ in range(60)]
    bottoms[0] = 700.0
    centers = [None if v is None else float(rng.integers(300, 700)) for v in bottoms]
    got = fit.rows_for({"frames": 60, "bottom": bottoms, "cx": centers}, used, {"hold_frames": k, "box_frames": box})
    ground, cx_med = _production_stats(bottoms, centers, used, k, box)
    assert np.array_equal(got["ground"], ground) and got["cx_med"] == cx_med and got["used"] == list(used)
    assert not np.isnan(got["bottom"]).any() and len(got["cx"]) == 60


def test_a_used_range_without_any_measurement_still_gives_a_finite_centre():
    measured = {"frames": 5, "bottom": [700.0, None, None, 720.0, 710.0], "cx": [40.0, None, None, 60.0, 50.0]}   # 使う範囲を画像で差し替えた
    with warnings.catch_warnings():
        warnings.simplefilter("error")                                                       # All-NaN の警告も出さない
        got = fit.rows_for(measured, (1, 2), {"hold_frames": 0, "box_frames": 1})
    assert np.isfinite(got["cx_med"]) and got["cx_med"] == float(np.median(got["cx"][1:3]))   # 前後で埋めた値の中央値
    assert np.isfinite(got["ground"]).all() and np.isfinite(got["cx"]).all()
    assert fit.rows_for(measured, (0, 4), {"hold_frames": 0, "box_frames": 1})["cx_med"] == 50.0   # 写ったコマがあれば今までどおり


# ---- spec ----------------------------------------------------------------------------

def test_rows_settings_default_to_the_production_values_and_box_is_odd(tmp_path):
    data = dancespec.load_spec(spec(tmp_path)).data["performer"]["rows"]
    assert data == {"analysis_scale": 0.5, "hold_frames": 10, "box_frames": 9}
    with pytest.raises(PipelineError, match="奇数"):
        dancespec.load_spec(spec(tmp_path, rows={"box_frames": 8}))
    raw = json.loads(spec(tmp_path, rows={"hold_frames": 4}).read_text(encoding="utf-8"))
    raw["schema_version"] = dance.SCHEMA_V02
    with pytest.raises(PipelineError, match="invalid"):
        dancespec.parse_spec(raw, tmp_path)


# ---- 層の API と hooks ------------------------------------------------------------------

@pytest.fixture
def shot(tmp_path):
    frames = [person(101 - (6 if i in (4, 5) else 0), left=30 + i) for i in range(12)]          # 4・5 コマ目は跳ぶ
    write_video(tmp_path / "a.mp4", frames)
    Image.fromarray(person(110)).save(tmp_path / "end.png")
    return spec(tmp_path, rows={"analysis_scale": 1.0, "hold_frames": 2, "box_frames": 1})


def test_rows_are_made_only_with_hooks_or_on_request(shot, tmp_path):
    ctx = api.prepare(shot, out=tmp_path / "runs")
    try:
        assert ctx.rows is None and api.layers(ctx, 0.25).performer_info["body"] is None
    finally:
        api.close(ctx)
    ctx = api.prepare(shot, out=tmp_path / "runs", rows=True)
    try:
        assert set(ctx.rows) == {"a.mp4", "end.png"} and ctx.rows["end.png"]["frames"] == 1
        rows = ctx.rows["a.mp4"]
        assert rows["used"] == [0, 11] and list(rows["bottom"][3:7]) == [101.0, 95.0, 95.0, 101.0]
        assert list(rows["ground"][3:7]) == [101.0, 101.0, 101.0, 101.0]                         # 跳んでも足元の列は床に残る
        assert (tmp_path / "runs" / rows["file"]).is_file()
    finally:
        api.close(ctx)


def test_body_points_land_on_the_drawn_performer(shot, tmp_path):
    raw = json.loads(shot.read_text(encoding="utf-8"))
    raw["camera"] = {"keys": [{"t": 0, "zoom": 1.0}, {"t": 1.0, "zoom": 1.3, "center": [0.45, 0.6]}]}
    raw["performer"]["placement"] = {"anchor": [0.4, 0.9], "height": 0.8, "flip_x": True}
    shot.write_text(json.dumps(raw), encoding="utf-8")
    ctx = api.prepare(shot, out=tmp_path / "runs", rows=True)
    try:
        for t in (0.0, 0.5, 0.75):
            L = api.layers(ctx, t)
            body = L.performer_info["body"]
            ys, xs = np.nonzero(L.performer[1] > 0.5)
            near = 0.75 * body["scale"]                                           # 元の 1 画素の 3/4 以内（縮尺と補間の分）
            assert abs(body["bottom"][1] - ys.max()) <= near                      # このコマの最下の行が画面の最下の行に来る
            assert abs(body["bottom"][0] - np.median(xs)) <= near
            assert body["source"]["frame"] == L.performer_info["source_frame"]
            assert body["scale"] == pytest.approx(ctx.place[0]["scale"] * L.camera["zoom"], rel=0.02)
    finally:
        api.close(ctx)


def test_a_hook_can_mark_the_feet(shot, tmp_path):
    hook = tmp_path / "finish.py"
    hook.write_text(
        "def final(img, t, ctx, L):\n"
        "    body = L.performer_info and L.performer_info['body']\n"
        "    if body is None:\n"
        "        return img\n"
        "    x, y = (int(round(v)) for v in body['feet'])\n"
        "    out = img.copy()\n"
        "    out[y - 1:y + 2, x - 1:x + 2] = (1.0, 0.0, 0.0)\n"
        "    return out\n", encoding="utf-8")
    ctx = api.prepare(shot, hooks=hook, out=tmp_path / "runs")
    try:
        assert ctx.rows is not None
        x, y = (int(round(v)) for v in api.layers(ctx, 0.25).performer_info["body"]["feet"])
        image = api.frame(ctx, 0.25)
        assert tuple(image[y, x]) == (255, 0, 0)
    finally:
        api.close(ctx)


def test_rows_are_cached_by_content_and_settings(shot, tmp_path):
    first = api.prepare(shot, out=tmp_path / "runs", rows=True)
    path = tmp_path / "runs" / first.rows["a.mp4"]["file"]
    api.close(first)
    stamp = path.stat().st_mtime_ns
    second = api.prepare(shot, out=tmp_path / "runs", rows=True)
    try:
        assert path.stat().st_mtime_ns == stamp and second.rows["a.mp4"]["file"] == first.rows["a.mp4"]["file"]
    finally:
        api.close(second)
    assert fit.rows_digest(KEY, 0.5) != fit.rows_digest(KEY, 0.5, [[2, 3]]) != fit.rows_digest(KEY, 1.0)
    assert len(path.name) == len("0123456789abcdef-rows-0123456789abcdef.json")
    stale = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**stale, "settings_sha256": "0" * 64, "bottom": [1.0] * stale["frames"]}), encoding="utf-8")
    third = api.prepare(shot, out=tmp_path / "runs", rows=True)                           # 名前が同じでも中身が違えば測り直す
    try:
        assert third.rows["a.mp4"]["bottom"][0] != 1.0 and json.loads(path.read_text(encoding="utf-8"))["settings_sha256"] != "0" * 64
    finally:
        api.close(third)


def test_frames_the_shot_never_shows_do_not_pull_the_ground(tmp_path):
    frames = [person(61) if i < 4 else person(101) for i in range(12)]                    # 最初の 4 コマは高い所（使わない）
    write_video(tmp_path / "a.mp4", frames)
    Image.fromarray(person(110)).save(tmp_path / "end.png")
    path = spec(tmp_path, rows={"analysis_scale": 1.0})
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["performer"]["clips"][0]["source_start"] = 6 / 12                                # 元のコマ 6 から使う
    path.write_text(json.dumps(raw), encoding="utf-8")
    ctx = compose.prepare(dancespec.load_spec(path), out=tmp_path / "runs", rows=True)
    try:
        rows = ctx.rows["a.mp4"]
        assert rows["used"][0] == 6 and set(np.round(rows["ground"], 6)) == {101.0}
    finally:
        ctx.close()
