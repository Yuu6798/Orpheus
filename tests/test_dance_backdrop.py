"""dance-shot/0.3 のステージの口: hooks の backdrop（カメラの前）、spec の cues（曲の出来事の時刻）、cues.envelope（強さの曲線）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from PIL import Image  # noqa: E402

from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import api, compose, cues, demo, plan  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "dance" / "backdrop_hooks.py"


def base(**extra):
    raw = {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 180, "height": 320, "fps": 12}, "duration_s": 2.0,
           "stage": {"path": "stage.png"}, "performer": {"clips": [{"path": "end.png", "start": 0, "end": 2}]},
           "beats": {"bpm": 120, "count": 8}}
    raw.update(extra)
    return raw


# ---- envelope ---------------------------------------------------------------------------

def test_envelope_joins_keys_linearly_smoothly_or_in_steps():
    keys = [(1.0, 0.2), (2.0, 0.6), (3.0, 1.0, "smooth"), (4.0, 0.0, "step"), (4.0, 0.5, "step")]
    assert cues.envelope(keys, 0.0) == 0.2 and cues.envelope(keys, 1.5) == pytest.approx(0.4)
    assert cues.envelope(keys, 2.25) == pytest.approx(0.6 + 0.4 * cues.smoothstep(0.25))
    assert cues.envelope(keys, 3.9) == 1.0 and cues.envelope(keys, 4.0) == 0.5 and cues.envelope(keys, 9.0) == 0.5
    with pytest.raises(ValueError, match="昇順"):
        cues.envelope([(2.0, 0.0), (1.0, 1.0)], 1.5)
    with pytest.raises(ValueError, match="つなぎ方"):
        cues.envelope([(0.0, 0.0), (1.0, 1.0, "cubic")], 0.5)


def _stage_level(s, quiet, pickup, back):
    """制作のステージの明るさの式（写し）: 休みの頭から 0.06 秒で 0.25 へ、戻りの合図から次の打ちまで 0.25 → 0.6、打ちで 1。"""
    if s < quiet:
        return 1.0
    if s < pickup:
        return 1.0 - 0.75 * min(1.0, (s - quiet) / 0.06)
    if s < back:
        return 0.25 + 0.35 * (s - pickup) / (back - pickup)
    return 1.0


def _energy(s, build, drop):
    """制作のステージの勢いの式（写し）。"""
    smooth = lambda u: (lambda v: v * v * (3 - 2 * v))(min(1.0, max(0.0, u)))  # noqa: E731
    if s < build:
        return 0.30 + 0.05 * smooth(s / build)
    if s < drop:
        return 0.35 + 0.35 * smooth((s - build) / (drop - build))
    return 1.0


def test_envelope_writes_the_production_stage_curves():
    quiet, pickup, back, build, drop = 6.25, 6.83, 7.4, 3.1, 4.95
    level = [(quiet, 1.0), (quiet + 0.06, 0.25), (pickup, 0.25), (back, 0.60), (back, 1.0, "step")]
    energy = [(0.0, 0.30), (build, 0.35, "smooth"), (drop, 0.70, "smooth"), (drop, 1.0, "step")]
    for j in range(-12, 120):
        s = j / 12
        assert cues.envelope(level, s) == pytest.approx(_stage_level(s, quiet, pickup, back), abs=1e-12)
        assert cues.envelope(energy, s) == pytest.approx(_energy(s, build, drop), abs=1e-12)


def test_first_and_between():
    when = {"drop": [1.5, 3.0], "none": []}
    assert cues.first(when, "drop") == 1.5 and cues.first(when, "none") is None and cues.first(when, "x", 9.0) == 9.0
    assert cues.between(1.0, 1.0, 2.0) and not cues.between(2.0, 1.0, 2.0) and cues.between(5.0, 1.0, None)


# ---- spec の cues --------------------------------------------------------------------------

def test_cues_resolve_times_and_refs_into_sorted_lists(tmp_path):
    data = dancespec.parse_spec(base(cues={"drop": {"beat": 2}, "hits": [1.5, {"frame": 6}, {"bar": 1}], "end": 1.9}), tmp_path).data
    assert data["cues"] == {"drop": [1.0], "hits": [0.5, 1.5, 2.0], "end": [1.9]}
    assert plan.build(data).count("## cues") == 1 and "hits" in plan.build(data)
    with pytest.raises(PipelineError, match="invalid"):
        dancespec.parse_spec(base(schema_version=dance.SCHEMA_V02, cues={"drop": 1.0}), tmp_path)
    assert dancespec.parse_spec(base(), tmp_path).data["cues"] == {}


# ---- backdrop -----------------------------------------------------------------------------

@pytest.fixture
def shot(tmp_path):
    stage = np.zeros((320, 180, 3), np.uint8)
    stage[::20] = 120                                                                 # 横じまのステージ（カメラで動くのが見える）
    Image.fromarray(stage).save(tmp_path / "stage.png")
    Image.fromarray(np.full((96, 64, 3), (0, 255, 0), np.uint8)).save(tmp_path / "end.png")     # 人物は全部抜ける
    raw = base(cues={"seam": 0.5, "end_card": 1.5}, camera={"keys": [{"t": 0, "zoom": 1.0}, {"t": 2.0, "zoom": 1.4, "center": [0.4, 0.6]}]})
    (tmp_path / "spec.json").write_text(json.dumps(raw), encoding="utf-8")
    hook = tmp_path / "backdrop.py"
    hook.write_text("import numpy as np\n"
                    "def backdrop(img, t, ctx):\n"
                    "    out = img.copy()\n"
                    "    out[150:170, 80:100] = (1.0, 0.0, 0.0)\n"
                    "    return out\n", encoding="utf-8")
    return tmp_path


def test_backdrop_is_drawn_before_the_camera(shot):
    ctx = api.prepare(shot / "spec.json", hooks=shot / "backdrop.py", out=shot / "runs")
    try:
        t = 1.5
        L = api.layers(ctx, t)
        before = ctx.stage.at(t).copy()
        before[150:170, 80:100] = (1.0, 0.0, 0.0)
        state = ctx.camera.state(t)
        expected = compose._warp_stage(ctx, before, ctx.camera.matrix(state, ctx.spec.data["stage"]["parallax"]))
        assert np.array_equal(L.stage, expected)                                    # 描いた四角もカメラで寄る
        red = (L.stage[..., 0] > 0.5) & (L.stage[..., 1] < 0.1)
        assert red.sum() > 400 * 1.2                                                  # 20×20 より大きく写る（寄りの分）
        assert not np.array_equal(ctx.stage.at(t)[150:170, 80:100], before[150:170, 80:100])   # 素材の配列は書き換えない
    finally:
        api.close(ctx)


def test_without_a_backdrop_hook_the_stage_is_the_same(shot):
    plain = api.prepare(shot / "spec.json", out=shot / "runs")
    other = shot / "other.py"
    other.write_text("def final(img, t, ctx, L):\n    return img\n", encoding="utf-8")
    hooked = api.prepare(shot / "spec.json", hooks=other, out=shot / "runs")
    try:
        assert np.array_equal(api.layers(plain, 1.0).stage, api.layers(hooked, 1.0).stage)
    finally:
        api.close(plain)
        api.close(hooked)


def test_a_backdrop_must_keep_the_shape(shot):
    bad = shot / "bad.py"
    bad.write_text("def backdrop(img, t, ctx):\n    return img[:10]\n", encoding="utf-8")
    ctx = api.prepare(shot / "spec.json", hooks=bad, out=shot / "runs")
    try:
        with pytest.raises(PipelineError, match="backdrop"):
            api.layers(ctx, 0.5)
    finally:
        api.close(ctx)


def test_the_timeline_carries_the_cues_only_when_there_are_some(shot):
    ctx = api.prepare(shot / "spec.json", out=shot / "runs")
    try:
        assert compose.timeline_record(ctx)["cues"] == {"seam": [0.5], "end_card": [1.5]} and api.cues(ctx)["seam"] == [0.5]
    finally:
        api.close(ctx)
    raw = json.loads((shot / "spec.json").read_text(encoding="utf-8"))
    del raw["cues"]
    (shot / "plain.json").write_text(json.dumps(raw), encoding="utf-8")
    ctx = api.prepare(shot / "plain.json", out=shot / "runs")
    try:
        assert "cues" not in compose.timeline_record(ctx)
    finally:
        api.close(ctx)


def test_the_example_backdrop_follows_the_demo_cues(tmp_path):
    demo.create(tmp_path, 180, 320, 3.0, 12)
    when = dancespec.load_spec(tmp_path / "spec.json").data["cues"]
    assert set(when) == {"drop", "seam", "end_card"}
    ctx = api.prepare(tmp_path / "spec.json", hooks=EXAMPLE, out=tmp_path / "runs")
    plain = api.prepare(tmp_path / "spec.json", out=tmp_path / "runs")
    try:
        lift = {}
        for t in (when["seam"][0] - 0.25, when["seam"][0] + 0.1, when["end_card"][0]):
            added = api.layers(ctx, t).stage - api.layers(plain, t).stage
            lift[t] = float(added.max())
        early, dim, back = lift.values()
        assert early > 0 and dim < early and back >= dim and math.isfinite(back)          # seam で暗く、end_card までに戻る
    finally:
        api.close(ctx)
        api.close(plain)
