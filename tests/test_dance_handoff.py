"""仕上げの受け渡し口: 層の API・hooks・look・層の静止画・timeline のスキーマ。"""
import hashlib
import json

import jsonschema
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import api, compose  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import look as fxlook  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.fx.color import to_srgb8  # noqa: E402
from mv_pipeline.io import PipelineError, read_json  # noqa: E402
from pathlib import Path  # noqa: E402

TIMELINE_SCHEMA = Path(dance.__file__).with_name("dance_timeline.schema.json")
TIMES = (0.3, 1.0, 1.02, 2.0, 2.5, 2.8)


@pytest.fixture(scope="module")
def shot(tmp_path_factory):
    root = tmp_path_factory.mktemp("handoff")
    code, _, err = run_cli("dance", "demo", root, "--width", "180", "--height", "320", "--duration", "3", "--fps", "12")
    assert code == 0, err
    code, result, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs", "--quiet")
    assert code == 0, err
    return root, result


def write_hooks(root, text):
    path = root / "finish.py"
    path.write_text(text, encoding="utf-8")
    return path


# ---- API -------------------------------------------------------------------------

def test_frame_and_compose_of_layers_are_the_same_bytes(shot):
    root, _ = shot
    ctx = api.prepare(root / "spec.json", out=root / "runs")
    other = api.prepare(dancespec.load_spec(root / "spec.json"), out=root / "runs")
    try:
        for t in TIMES:
            L = api.layers(ctx, t)
            assert (api.compose(ctx, L, t) == api.frame(other, t)).all()
    finally:
        api.close(ctx)
        api.close(other)


def test_layers_have_the_promised_shapes_and_are_not_changed_by_compose(shot):
    root, _ = shot
    ctx = api.prepare(root / "spec.json", out=root / "runs")
    try:
        L = api.layers(ctx, 1.0)
        before = L.stage.copy()
        api.compose(ctx, L, 1.0)
        assert (L.stage == before).all() and L.stage.dtype == np.float32 and L.stage.shape == (320, 180, 3)
        assert L.t == 1.0 and L.frame_index == 12
        name, color, alpha = L.behind[0]
        assert name == "back" and color.shape == (320, 180, 3) and alpha.shape == (320, 180) and alpha.max() > 0
        assert L.performer[0].shape == (320, 180, 3) and L.performer[1].dtype == np.float32
        info = L.performer_info
        assert info["clip"] == 1 and info["blend"] is None and np.shape(info["placement"]) == (2, 3) and np.shape(info["camera"]) == (2, 3)
        assert L.camera["zoom"] >= 1.0 and np.shape(L.camera["matrix"]) == (2, 3)
    finally:
        api.close(ctx)


def test_flashes_are_listed_while_they_are_active(shot):
    root, _ = shot
    ctx = api.prepare(root / "spec.json", out=root / "runs")
    try:
        flash = ctx.spec.data["flashes"][0]
        strength, color, mode = api.layers(ctx, flash["t"]).flashes[0]
        assert strength == pytest.approx(0.22) and mode == "add_display" and color.shape == (3,)
        assert api.layers(ctx, flash["t"] - 0.1).flashes == []
    finally:
        api.close(ctx)


def test_plain_tuples_can_replace_a_text_layer(shot):
    """エージェントが層を差し替えて compose しても、(名前, 色, アルファ) の形なら合成できる。"""
    root, _ = shot
    ctx = api.prepare(root / "spec.json", out=root / "runs")
    try:
        L = api.layers(ctx, 1.0)
        name, color, alpha = L.behind[0]
        L.behind = [(name, color * 0, alpha * 0)]
        replaced = api.compose(ctx, L, 1.0)
        assert (replaced != api.frame(ctx, 1.0)).any()
    finally:
        api.close(ctx)


def test_rng_depends_on_the_seed_and_the_name(shot):
    root, _ = shot
    ctx = api.prepare(root / "spec.json", out=root / "runs")
    try:
        first = api.rng(ctx, "embers").random()
        assert first == api.rng(ctx, "embers").random() and first != api.rng(ctx, "fog").random()
    finally:
        api.close(ctx)


def test_performer_is_available_without_the_other_layers(shot):
    root, _ = shot
    ctx = api.prepare(root / "spec.json", out=root / "runs")
    try:
        assert (api.performer(ctx, 1.0)[1] == api.layers(ctx, 1.0).performer[1]).all()
    finally:
        api.close(ctx)


# ---- hooks -----------------------------------------------------------------------

NOOP = """
def prepare(ctx):
    ctx.x_note = 1
def stage(img, t, ctx, L):
    return img
def behind(img, t, ctx, L):
    return img
def performer(color, alpha, t, ctx, L):
    return color, alpha
def front(img, t, ctx, L):
    return img
def final(img, t, ctx, L):
    return img
"""


def test_doing_nothing_in_hooks_keeps_the_frames(shot, tmp_path):
    root, _ = shot
    hooks = write_hooks(tmp_path, NOOP)
    plain, hooked = api.prepare(root / "spec.json", out=root / "runs"), api.prepare(root / "spec.json", hooks=hooks, out=root / "runs")
    try:
        assert hooked.x_note == 1
        for t in TIMES:
            assert (api.frame(plain, t) == api.frame(hooked, t)).all()
    finally:
        api.close(plain)
        api.close(hooked)


def test_hooks_run_in_order_at_their_places(shot, tmp_path):
    root, _ = shot
    hooks = write_hooks(tmp_path, """
import numpy as np
seen = []
def stage(img, t, ctx, L):
    seen.append("stage")
    return np.zeros_like(img)
def behind(img, t, ctx, L):
    seen.append("behind")
    return img
def performer(color, alpha, t, ctx, L):
    seen.append("performer")
    return color, np.zeros_like(alpha)
def front(img, t, ctx, L):
    seen.append("front")
    return img
def final(img, t, ctx, L):
    seen.append("final")
    return img
""")
    ctx = api.prepare(root / "spec.json", hooks=hooks, out=root / "runs")
    try:
        image = api.frame(ctx, 2.8)
        assert ctx.hooks.module.seen == ["stage", "behind", "performer", "front", "final"]
    finally:
        api.close(ctx)
    assert image.mean() < 40          # ステージと人物が消えて、字だけが残る


def test_hook_results_are_checked(shot, tmp_path):
    root, _ = shot
    hooks = write_hooks(tmp_path, "def stage(img, t, ctx, L):\n    return img[:10]\n")
    ctx = api.prepare(root / "spec.json", hooks=hooks, out=root / "runs")
    try:
        with pytest.raises(PipelineError, match="同じ形"):
            api.frame(ctx, 1.0)
    finally:
        api.close(ctx)


def test_hooks_sha_is_in_the_key_the_manifest_and_the_timeline(shot, tmp_path):
    root, base = shot
    hooks = write_hooks(tmp_path, NOOP)
    code, finished, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs", "--hooks", hooks, "--quiet")
    assert code == 0, err
    assert finished["reused"] is False and finished["run_id"] != base["run_id"]
    run_dir = root / "runs" / finished["run_id"]
    manifest = read_json(run_dir / "manifest.json")
    digest = hashlib.sha256(hooks.read_bytes()).hexdigest()
    assert manifest["hooks"]["sha256"] == digest
    record = read_json(run_dir / "timeline.json")
    assert record["finished"]["hooks"]["sha256"] == digest
    original = read_json(root / "runs" / base["run_id"] / "timeline.json")
    assert original["finished"] is None
    assert {**record, "finished": None} == original      # hooks で時刻と構造は変わらない
    code, again, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs", "--hooks", hooks, "--quiet")
    assert code == 0 and again["reused"] is True, err
    hooks.write_text(NOOP + "\n# changed\n", encoding="utf-8")
    code, changed, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs", "--hooks", hooks, "--quiet")
    assert code == 0 and changed["reused"] is False, err


def test_a_failing_hook_fails_the_run_with_a_traceback_in_the_log(shot, tmp_path):
    root, _ = shot
    hooks = write_hooks(tmp_path, "def final(img, t, ctx, L):\n    raise RuntimeError('boom')\n")
    out = tmp_path / "runs"
    code, _, err = run_cli("dance", "render", root / "spec.json", "--out", out, "--hooks", hooks, "--quiet")
    assert code == 1 and "boom" in err
    run_dir = next(p for p in out.iterdir() if (p / "manifest.json").is_file())
    assert read_json(run_dir / "manifest.json")["status"] == "failed"
    assert "Traceback" in (run_dir / "render.log").read_text(encoding="utf-8")


def test_a_missing_hooks_file_stops_before_rendering(shot, tmp_path):
    root, _ = shot
    code, _, err = run_cli("dance", "render", root / "spec.json", "--out", tmp_path / "runs", "--hooks", tmp_path / "nope.py", "--quiet")
    assert code == 1 and "hooks" in err


def test_a_spec_cannot_name_hooks(tmp_path):
    raw = {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 180, "height": 320}, "duration_s": 2, "stage": {"path": "s.png"},
           "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 2}]}, "hooks": "finish.py"}
    with pytest.raises(PipelineError, match="invalid"):
        dancespec.parse_spec(raw, tmp_path)


# ---- look ------------------------------------------------------------------------

def test_look_is_the_fx_look_on_the_same_linear_frame(shot, tmp_path):
    root, _ = shot
    hooks = write_hooks(tmp_path, """
captured = {}
def final(img, t, ctx, L):
    captured[t] = img.copy()
    return img
""")
    ctx = api.prepare(root / "spec.json", hooks=hooks, out=root / "runs")
    try:
        for t in (0.3, 2.0):
            image = api.frame(ctx, t)
            look = fxlook.prepare(ctx.spec.data["look"], ctx.size, ctx.fps)
            assert (image == fxlook.apply(look, ctx.hooks.module.captured[t], t)).all()
            assert (image != to_srgb8(ctx.hooks.module.captured[t])).any()
    finally:
        api.close(ctx)


def test_look_uses_the_fx_schema_and_normalization(tmp_path):
    raw = {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 180, "height": 320}, "duration_s": 2, "stage": {"path": "s.png"},
           "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 2}]}, "look": {"vignette": {"amount": 0.3}, "grain": {"sigma": 1.5}}}
    data = dancespec.parse_spec(raw, tmp_path).data
    assert data["look"] == fxspec.normalize_look(raw["look"]) and data["look"]["grain"]["frames"] == 6
    with pytest.raises(PipelineError, match="invalid"):
        dancespec.parse_spec({**raw, "look": {"grain": {"sigma": "x"}}}, tmp_path)
    assert any(w.startswith("look") for w in dancespec.parse_spec({**raw, "look": {}}, tmp_path).warnings)


def test_01_has_no_look(tmp_path):
    raw = {"schema_version": dance.SCHEMA_V01, "output": {"width": 180, "height": 320}, "duration_s": 2, "stage": {"path": "s.png"},
           "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 2}]}}
    assert dancespec.parse_spec(raw, tmp_path).data["look"] is None
    with pytest.raises(PipelineError, match="invalid"):
        dancespec.parse_spec({**raw, "look": {}}, tmp_path)


# ---- 層の静止画・timeline のスキーマ -----------------------------------------------------

def test_layer_stills_have_one_row_per_time(shot):
    root, _ = shot
    code, result, err = run_cli("dance", "stills", root / "spec.json", "--times", "1.0", "2.0", "--layers", "--out", root / "sheets")
    assert code == 0, err
    sheet = cv2.imread(result["path"])
    assert sheet.shape[:2] == (640, 900)
    alpha = sheet[:320, 360:540]
    assert (alpha[..., 0] == alpha[..., 1]).all() and alpha.max() > 200 and alpha.min() == 0


def test_timelines_match_their_schema(shot, tmp_path):
    root, result = shot
    schema = json.loads(TIMELINE_SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    assert list(validator.iter_errors(read_json(root / "runs" / result["run_id"] / "timeline.json"))) == []
    code, _, err = run_cli("dance", "demo", tmp_path, "--width", "180", "--height", "320", "--duration", "2", "--fps", "12", "--schema", "0.1")
    assert code == 0, err
    ctx = compose.prepare(dancespec.load_spec(tmp_path / "spec.json"))
    record = compose.timeline_record(ctx)
    ctx.close()
    assert list(validator.iter_errors(json.loads(json.dumps(record)))) == []
    assert list(validator.iter_errors({"schema_version": "dance-timeline/0.2", "fps": 24, "duration_s": 1, "lyrics": [], "ui_regions": []}))


