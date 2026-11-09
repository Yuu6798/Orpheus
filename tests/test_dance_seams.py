"""dance-shot/0.3 のつなぎ: clips[].shift（切れ目の前後のずらし）と transition_in dissolve（切れ目を中心にした線形の重ね）。"""
import json
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from PIL import Image  # noqa: E402

from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import api, clips, compose, plan  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import render as fxrender  # noqa: E402
from mv_pipeline.fx import timeline  # noqa: E402
from mv_pipeline.gate.checks import _validate_timeline  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

GREEN = (0, 255, 0)
FPS = 30


def base(clips_, schema=dance.SCHEMA_VERSION, duration=2.0):
    return {"schema_version": schema, "output": {"width": 180, "height": 320, "fps": FPS}, "duration_s": duration,
            "stage": {"path": "stage.png"}, "performer": {"clips": clips_, "key": {"color": [0, 255, 0], "choke_px": 0, "feather_px": 0},
                                                          "placement": {"anchor": [0.5, 0.9], "height": 0.6}}}


def two(transition=None, **shift):
    first = {"path": "a.mp4", "start": 0, "end": 1.0}
    second = {"path": "b.mp4", "start": 1.0, "end": 2.0}
    if transition:
        second["transition_in"] = transition
    for key, value in shift.items():
        (first if key == "a" else second)["shift"] = value
    return [first, second]


# ---- 書き方の検査 ---------------------------------------------------------------------

@pytest.mark.parametrize("clips_, message", [
    ([{"path": "a.mp4", "start": 0, "end": 1, "transition_in": {"type": "dissolve", "frames": 4}}], "最初の clip"),
    ([{"path": "a.mp4", "start": 0, "end": 0.9}, {"path": "b.mp4", "start": 1.0, "end": 2.0, "transition_in": {"type": "dissolve", "frames": 4}}],
     "隙間なく"),
    (two({"type": "dissolve", "frames": 70}), "窓が収まりません"),
    ([{"path": "a.mp4", "start": 0, "end": 1.0}, {"path": "b.mp4", "start": 1.0, "end": 1.1, "transition_in": {"type": "dissolve", "frames": 4}},
      {"path": "c.mp4", "start": 1.1, "end": 2.0, "transition_in": {"type": "dissolve", "frames": 4}}], "窓が収まりません"),
    (two(a=[{"t": 1.0, "ease": 1.0, "offset": [0.1, 0]}]), "ease は t と違う"),
])
def test_dissolves_and_shifts_are_checked(tmp_path, clips_, message):
    with pytest.raises(PipelineError, match=message):
        dancespec.parse_spec(base(clips_), tmp_path)


def test_a_blend_window_and_the_next_dissolve_must_not_overlap(tmp_path):
    def clips_(blend):
        return [{"path": "a.mp4", "start": 0, "end": 0.8},
                {"path": "b.mp4", "start": 0.8, "end": 1.4, "transition_in": {"type": "blend", "frames": blend}},
                {"path": "c.mp4", "start": 1.4, "end": 2.0, "transition_in": {"type": "dissolve", "frames": 8}}]
    with pytest.raises(PipelineError, match="blend の窓"):
        dancespec.parse_spec(base(clips_(15)), tmp_path)          # 0.5 秒 + 次の前半 0.13 秒 > 0.6 秒（前のクリップが途中で消える）
    assert dancespec.parse_spec(base(clips_(12)), tmp_path)      # 0.4 秒 + 0.13 秒 は収まる
    long_blend = [{"path": "a.mp4", "start": 0, "end": 1.0},
                  {"path": "b.mp4", "start": 1.0, "end": 1.2, "transition_in": {"type": "blend", "frames": 12}}]
    assert dancespec.parse_spec(base(long_blend), tmp_path)       # 次に dissolve が無ければ今までどおり（0.2 と同じ）


@pytest.mark.parametrize("clips_", [two({"type": "dissolve", "frames": 4}), two(a=[{"t": 1.0, "ease": 0.5}])])
def test_a_02_spec_cannot_dissolve_or_shift(tmp_path, clips_):
    with pytest.raises(PipelineError, match="invalid"):
        dancespec.parse_spec(base(clips_, schema=dance.SCHEMA_V02), tmp_path)


# ---- 重みの式（非公開の制作の写し） -------------------------------------------------------

def _pieces_at(cuts, xfs, j):
    """制作の pieces_at（写し）: ディゾルブは切れ目の前後 xf 秒で線形、それ以外は s >= t で切り替える。"""
    s = j / FPS
    for i, (t, xf) in enumerate(zip(cuts, xfs)):
        if xf > 0 and t - xf <= s < t + xf:
            w = (s - (t - xf)) / (2 * xf)
            return [(i, 1 - w), (i + 1, w)]
    return [(sum(1 for t in cuts if s >= t), 1.0)]


class Stub:
    fps, width, height = 30.0, 64, 96

    def count(self):
        return 90

    def close(self):
        pass


def test_dissolve_weights_follow_the_production_formula(tmp_path, monkeypatch):
    monkeypatch.setattr(clips.Timeline, "_source", staticmethod(lambda spec, clip: Stub()))
    cuts, frames = [0.61, 1.333, 2.2], [4, 6, 3]
    clips_ = [{"path": f"{n}.mp4", "start": 0 if i == 0 else cuts[i - 1], "end": cuts[i] if i < 3 else 3.0} for i, n in enumerate("abcd")]
    for clip, count in zip(clips_[1:], frames):
        clip["transition_in"] = {"type": "dissolve", "frames": count}
    tl = clips.Timeline(dancespec.parse_spec(base(clips_, duration=3.0), tmp_path))
    xfs = [count / (2 * FPS) for count in frames]
    for j in range(90):
        mine = [(index, weight) for index, _, weight in tl.mapping(timeline.frame_time(j, FPS))]
        assert mine == _pieces_at(cuts, xfs, j)


def _shift_at(shifts, s):
    """制作の shift_at（写し）: t で全部、ease で 0。smoothstep。複数は足す。"""
    smooth = lambda u: (lambda v: v * v * (3 - 2 * v))(min(1.0, max(0.0, u)))  # noqa: E731
    dx = dy = ds = 0.0
    for sh in shifts:
        t, e = sh["t"], sh["ease"]
        w = (1.0 if s >= t else smooth((s - e) / (t - e))) if e < t else (1.0 if s <= t else 1.0 - smooth((s - t) / (e - t)))
        dx += sh["dx"] * w
        dy += sh["dy"] * w
        ds += sh["ds"] * w
    return dx, dy, ds


def test_shift_weights_follow_the_production_formula(tmp_path):
    shifts = [{"t": 1.0, "ease": 0.4, "dx": -0.04, "dy": 0.01, "ds": 0.05}, {"t": 0.7, "ease": 0.95, "dx": 0.02, "dy": 0.0, "ds": -0.03}]
    clip = dancespec.parse_spec(base(two(a=[{"t": s["t"], "ease": s["ease"], "offset": [s["dx"], s["dy"]], "scale": s["ds"]} for s in shifts])),
                                tmp_path).data["performer"]["clips"][0]
    for j in range(60):
        assert compose.shift_at(clip, j / FPS) == _shift_at(shifts, j / FPS)


# ---- 描画 ----------------------------------------------------------------------------

def person(left=30, width=20, top=20, bottom=91):
    image = np.full((96, 64, 3), GREEN, np.uint8)
    image[top:bottom + 1, left:left + width] = (200, 60, 120)
    return image


@pytest.fixture
def media(tmp_path):
    Image.fromarray(np.full((320, 180, 3), 40, np.uint8)).save(tmp_path / "stage.png")
    for name, left in (("a", 22), ("b", 30)):
        frames = [person(left=left) for _ in range(60)]
        fxrender.write_video(tmp_path / f"{name}.mp4", iter(frames), (64, 96), FPS, 0, len(frames))
    return tmp_path


def _alpha(tmp_path, clips_, t, name):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(base(clips_)), encoding="utf-8")
    ctx = api.prepare(path, out=tmp_path / "runs")
    try:
        L = api.layers(ctx, t)
        return L.performer[1], L.performer_info
    finally:
        api.close(ctx)


def _box(alpha):
    ys, xs = np.nonzero(alpha > 0.5)
    return xs.min(), xs.max(), ys.min(), ys.max()


def test_an_offset_moves_the_performer_on_the_screen(media):
    plain, info = _alpha(media, two(), 0.5, "plain")
    moved, moved_info = _alpha(media, two(a=[{"t": 1.0, "ease": 0.2, "offset": [0.1, -0.05]}]), 0.5, "moved")
    w = compose.smoothstep((0.5 - 0.2) / (1.0 - 0.2))
    (x0, x1, y0, y1), (u0, u1, v0, v1) = _box(plain), _box(moved)
    assert abs((u0 - x0) - 0.1 * 180 * w) <= 1 and abs((v1 - y1) + 0.05 * 320 * w) <= 1
    assert moved_info["placement"][0][2] == pytest.approx(info["placement"][0][2] + 0.1 * 180 * w)


def test_a_scale_shift_keeps_the_feet_on_the_floor(media):
    plain, _ = _alpha(media, two(), 1.5, "plain")
    grown, _ = _alpha(media, two(b=[{"t": 1.0, "ease": 1.9, "scale": 0.3}]), 1.0, "grown")
    (x0, x1, y0, y1), (u0, u1, v0, v1) = _box(plain), _box(grown)
    assert abs(v1 - y1) <= 1                                                       # 足元は動かない
    assert (v1 - v0) / (y1 - y0) == pytest.approx(1.3, abs=0.03) and abs((u0 + u1) - (x0 + x1)) <= 2


def test_a_scale_shift_needs_a_measurable_performer(media):
    """足元の列を作れない動画（人物がどのコマにも写っていない）に scale の shift を書くと、中心が無いので止める（offset だけなら描ける）。"""
    empty = [np.full((96, 64, 3), GREEN, np.uint8) for _ in range(60)]
    fxrender.write_video(media / "b.mp4", iter(empty), (64, 96), FPS, 0, len(empty))
    path = media / "empty.json"
    path.write_text(json.dumps(base(two(b=[{"t": 1.0, "ease": 1.9, "scale": 0.3}]))), encoding="utf-8")
    with pytest.raises(PipelineError, match="shift の scale"):
        api.prepare(path, out=media / "runs")
    path.write_text(json.dumps(base(two(b=[{"t": 1.0, "ease": 1.9, "offset": [0.1, 0]}]))), encoding="utf-8")
    api.close(api.prepare(path, out=media / "runs"))


def test_a_dissolve_overlaps_both_clips_around_the_seam(media):
    window = two({"type": "dissolve", "frames": 4})
    half, info = _alpha(media, window, 1.0, "half")                    # 窓の真ん中は 0.5 ずつ
    first, _ = _alpha(media, two(), 0.9, "first")
    second, _ = _alpha(media, two(), 1.0, "second")
    assert info["blend"] == pytest.approx(0.5)                                       # 制作の式と同じく 0.4999999999999999
    assert np.allclose(half, (first + second) / 2, atol=1e-5)
    early, early_info = _alpha(media, window, 1.0 - 1 / FPS, "early")                 # 切れ目の前から新しい側が見える
    assert early_info["clip"] == 1 and early_info["blend"] == pytest.approx(0.25) and early.max() > 0


def test_the_timeline_records_a_dissolve_seam(media):
    path = media / "spec.json"
    path.write_text(json.dumps(base(two({"type": "dissolve", "frames": 4}))), encoding="utf-8")
    ctx = api.prepare(path, out=media / "runs")
    try:
        record = compose.timeline_record(ctx)
    finally:
        api.close(ctx)
    assert record["seam_info"][0]["kind"] == "dissolve" and record["seam_info"][0]["t"] == 1.0
    _validate_timeline(record)
    blends = [row for row in record["frames"] if row[3] is not None]
    assert [round(row[3], 6) for row in blends] == [0.0, 0.25, 0.5, 0.75]


def test_plan_lists_shifts_and_dissolves(tmp_path):
    data = dancespec.parse_spec(base(two({"type": "dissolve", "frames": 4}, a=[{"t": 1.0, "ease": 0.5, "offset": [-0.03, 0], "scale": 0.1}])),
                                tmp_path).data
    text = plan.build(data)
    assert "dissolve 4f" in text and "## shift" in text and "-0.03, 0" in text and "0.1" in text
    assert math.isclose(data["performer"]["clips"][0]["shift"][0]["ease"], 0.5)
