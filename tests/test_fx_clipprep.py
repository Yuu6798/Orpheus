"""生成した動画の下ごしらえ（fx/clipprep.py、`mv fx clips`）: 揺れ止めと色のゲイン。

式は実制作の 2 つの台本の関数を写したもの（private_*）と合成の配列で比べる。ならしは scipy の gaussian_filter1d と比べる。
"""
import contextlib
import io
import json
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.fx import clipprep, render  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

MW, MH = 640, 360


# ---- 実制作の台本の関数（写し） ------------------------------------------------------------------

def private_gray_small(fr):
    g = cv2.cvtColor(cv2.resize(fr, (MW, MH), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).astype(np.float32)
    return cv2.GaussianBlur(g, (0, 0), 1.0)


def private_measure(frames, bg_mask):
    tpl = private_gray_small(frames[0])
    warp = np.eye(2, 3, dtype=np.float32)
    out = [warp.copy()]
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
    for i in range(1, len(frames)):
        try:
            _, warp = cv2.findTransformECC(tpl, private_gray_small(frames[i]), warp.copy(), cv2.MOTION_EUCLIDEAN, crit, bg_mask, 5)
        except cv2.error:
            pass
        out.append(warp.copy())
    return np.array(out)


def private_params(warps):
    th = np.arctan2(warps[:, 1, 0], warps[:, 0, 0])
    return warps[:, 0, 2].astype(np.float64), warps[:, 1, 2].astype(np.float64), th.astype(np.float64)


def private_smooth(tx, ty, th, sigma=1.5):
    from scipy.ndimage import gaussian_filter1d
    s = [gaussian_filter1d(a, sigma, mode="nearest") for a in (tx, ty, th)]
    return [a - a[0] for a in s]


def private_mat(tx, ty, th, scale):
    c, s = np.cos(th), np.sin(th)
    return np.float32([[c, -s, tx * scale], [s, c, ty * scale]])


def private_inner_rect(M, w, h):
    A, t = M[:, :2].astype(np.float64), M[:, 2].astype(np.float64)
    Ai = np.linalg.inv(A)
    c = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)
    p = (Ai @ (c - t).T).T
    x0 = max(p[0, 0], p[3, 0], 0.0)
    x1 = min(p[1, 0], p[2, 0], float(w))
    y0 = max(p[0, 1], p[1, 1], 0.0)
    y1 = min(p[2, 1], p[3, 1], float(h))
    return [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]


def private_bg(mask):
    m = cv2.resize(mask, (MW, MH), interpolation=cv2.INTER_NEAREST) > 127
    m = cv2.dilate(m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)))
    return np.where(m > 0, 0, 255).astype(np.uint8)


def private_color_fit(f0, still):
    s = cv2.resize(still, (480, 270), interpolation=cv2.INTER_AREA).astype(np.float32)
    v = cv2.resize(f0, (480, 270), interpolation=cv2.INTER_AREA).astype(np.float32)
    fit = []
    for c in range(3):
        A = np.stack([v[..., c].ravel(), np.ones(v[..., c].size, np.float32)], 1)
        g, o = np.linalg.lstsq(A, s[..., c].ravel(), rcond=None)[0]
        fit.append((float(g), float(o)))
    vf = np.stack([v[..., c] * fit[c][0] + fit[c][1] for c in range(3)], -1)
    before, after = float(np.abs(s - v).mean()), float(np.abs(s - vf).mean())
    ok = after < before - 0.5 and all(0.85 <= g <= 1.15 for g, _ in fit)
    return fit, round(before, 2), round(after, 2), ok


def private_lut(fit):
    return np.stack([np.clip(np.arange(256) * g + o + 0.0, 0, 255) for g, o in fit], 1).astype(np.uint8)


# ---- 合成の揺れる動画 -------------------------------------------------------------------------

W, H = 320, 180


def texture(seed=1):
    rng = np.random.default_rng(seed)
    big = cv2.GaussianBlur(rng.integers(0, 256, (H * 2, W * 2, 3), dtype=np.uint8), (0, 0), 3)
    return cv2.normalize(big, None, 0, 255, cv2.NORM_MINMAX)


def shaky_frames(count=16, amplitude=3.0, seed=1):
    big = texture(seed)
    frames = []
    for k in range(count):
        dx, dy, deg = amplitude * math.sin(k / 3.0), 0.7 * amplitude * math.cos(k / 4.0), 0.4 * math.sin(k / 5.0)
        m = cv2.getRotationMatrix2D((W, H), deg, 1.0)
        m[0, 2] += dx - W / 2
        m[1, 2] += dy - H / 2
        frames.append(cv2.warpAffine(big, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT))
    return frames


def test_gaussian_smooth_equals_scipy_bit_for_bit():
    gaussian_filter1d = pytest.importorskip("scipy.ndimage").gaussian_filter1d
    rng = np.random.default_rng(7)
    for n, sigma in ((81, 1.5), (5, 1.5), (3, 2.5), (40, 0.7), (121, 3.0), (2, 1.5)):
        values = rng.normal(0, 10, n)
        assert np.array_equal(clipprep.gaussian_smooth(values, sigma), gaussian_filter1d(values, sigma, mode="nearest")), (n, sigma)
    assert np.array_equal(clipprep.gaussian_smooth([1.0, 2.0], 0), [1.0, 2.0])


def test_measure_smooth_matrix_and_rect_equal_the_private_code():
    pytest.importorskip("scipy")
    frames = [cv2.resize(f, (1280, 720), interpolation=cv2.INTER_LINEAR) for f in shaky_frames(12)]
    mask = np.zeros((720, 1280), np.uint8)
    cv2.circle(mask, (700, 380), 140, 255, -1)
    background = clipprep.background_mask(mask, (MW, MH), 25)
    assert np.array_equal(background, private_bg(mask))
    size = clipprep.measure_size(1280, 720)
    assert size == (MW, MH) and clipprep.measure_size(1920, 1080) == (MW, MH)
    for bg in (None, background):
        warps, failed = clipprep.measure((clipprep.gray_small(f, size) for f in frames), bg)
        theirs = private_measure(frames, bg)
        assert np.array_equal(warps, theirs) and failed == []
        mine = clipprep.smooth(*clipprep.params(warps), 1.5)
        expected = private_smooth(*private_params(theirs))
        assert all(np.array_equal(a, b) for a, b in zip(mine, expected))
        for i in range(len(frames)):
            m = clipprep.matrix(mine[0][i], mine[1][i], mine[2][i], 2.0)
            assert np.array_equal(m, private_mat(expected[0][i], expected[1][i], expected[2][i], 2.0))
            assert clipprep.inner_rect(m, 1280, 720) == private_inner_rect(m, 1280, 720)
            expected_frame = frames[i] if i == 0 else cv2.warpAffine(frames[i], m, (1280, 720), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                                                                     borderMode=cv2.BORDER_REPLICATE)
            assert np.array_equal(clipprep.stabilized(frames[i], i, m), expected_frame)


def test_color_fit_and_lut_equal_the_private_code():
    rng = np.random.default_rng(3)
    first = cv2.resize(cv2.GaussianBlur(rng.integers(0, 256, (90, 160, 3), dtype=np.uint8), (0, 0), 2), (1280, 720))
    for gain, offset in ((1.06, 3.0), (0.97, -2.0), (1.3, 0.0), (1.0, 0.2)):
        still = np.clip(first.astype(np.float32) * gain + offset + rng.normal(0, 1.5, first.shape), 0, 255).astype(np.uint8)
        mine = clipprep.color_fit(first, still)
        assert mine == private_color_fit(first, still)
        assert np.array_equal(clipprep.color_lut(mine[0]), private_lut(mine[0]))
    lut = clipprep.color_lut([(1.08, 4.0), (1.0, 0.0), (0.9, -1.0)])
    frame = rng.integers(0, 256, (4, 5, 3), dtype=np.uint8)
    assert np.array_equal(clipprep.apply_lut(frame, lut), np.stack([lut[..., c][frame[..., c]] for c in range(3)], -1))


def test_color_fit_recovers_a_gain_and_refuses_out_of_range_or_small_gains():
    rng = np.random.default_rng(5)
    first = cv2.GaussianBlur(rng.integers(30, 220, (270, 480, 3), dtype=np.uint8), (0, 0), 2)
    still = np.clip(first.astype(np.float32) * np.float32([1.08, 1.0, 0.95]) + np.float32([4, 0, -3]), 0, 255).astype(np.uint8)
    fit, before, after, use = clipprep.color_fit(first, still)
    assert use and after < before and [round(g, 2) for g, _ in fit] == [1.08, 1.0, 0.95]
    strong = np.clip(first.astype(np.float32) * 1.3, 0, 255).astype(np.uint8)
    assert not clipprep.color_fit(first, strong)[3]                                   # 倍率が 0.85〜1.15 の外
    assert clipprep.color_fit(first, strong, gain_range=[0.85, 1.4])[3]
    assert not clipprep.color_fit(first, np.clip(first.astype(np.float32) + 0.3, 0, 255).astype(np.uint8))[3]   # ほとんど縮まない


def test_stabilizing_brings_frames_back_onto_the_first():
    frames = shaky_frames(16)
    size = clipprep.measure_size(W, H)
    warps, failed = clipprep.measure((clipprep.gray_small(f, size) for f in frames), None)
    sx, sy, sth = clipprep.smooth(*clipprep.params(warps), 0)
    scale = W / size[0]
    inner = (slice(20, H - 20), slice(30, W - 30))
    before = [np.abs(f[inner].astype(np.int16) - frames[0][inner]).mean() for f in frames[1:]]
    after = [np.abs(clipprep.stabilized(f, i, clipprep.matrix(sx[i], sy[i], sth[i], scale))[inner].astype(np.int16) - frames[0][inner]).mean()
             for i, f in enumerate(frames) if i > 0]
    assert failed == [] and max(after) < 0.35 * min(before), (before, after)


def plan_dict(**clip):
    entry = {"id": "a", "source": "clip.mp4", "out": "out/a.mp4", "stabilize": {}}
    entry.update(clip)
    return {"schema_version": "fx-clips/0.1", "clips": [entry]}


@pytest.mark.parametrize("change, message", [
    (lambda p: p.update(schema_version="fx-clips/0.2"), "schema_version"),
    (lambda p: p["clips"].append(dict(p["clips"][0])), "重複"),
    (lambda p: p["clips"][0].pop("stabilize"), "stabilize も color もありません"),
    (lambda p: (p["clips"][0].pop("stabilize"), p["clips"][0].update(color={"reference": "still.png"}, measure_from="clip.mp4")),
     "measure_from は stabilize"),
    (lambda p: p["clips"][0].update(color={"reference": "still.png", "gain_range": [1.1, 0.9]}), "gain_range"),
    (lambda p: p["clips"][0].update(id="a b"), "正しくありません"),
    (lambda p: p["clips"][0]["stabilize"].update(dilate_px=-1), "正しくありません"),
])
def test_plan_errors_name_the_cause(tmp_path, change, message):
    plan = plan_dict()
    change(plan)
    with pytest.raises(PipelineError, match=message):
        clipprep.parse_plan(json.loads(json.dumps(plan)), tmp_path)


@pytest.mark.parametrize("out, message", [("clip.mp4", "入力（clip.mp4）と同じ"), ("clips_record.json", "記録"), ("plan.json", "入力（計画）と同じ")])
def test_outputs_never_overwrite_inputs(tmp_path, out, message):
    path = tmp_path / "plan.json"
    plan = plan_dict(out=out)
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(PipelineError, match=message):
        clipprep.output_targets(clipprep.parse_plan(plan, tmp_path), tmp_path, path, tmp_path)


def run_cli(*argv):
    from mv_pipeline import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def write_clip(path, frames, fps=16):
    render.write_video(path, iter(frames), (frames[0].shape[1], frames[0].shape[0]), fps, 14, len(frames))


def test_cli_prepares_records_reuses_and_protects(tmp_path):
    frames = shaky_frames(12)
    write_clip(tmp_path / "clip.mp4", frames)
    still = np.clip(frames[0].astype(np.float32) * 1.07 + 3, 0, 255).astype(np.uint8)
    cv2.imencode(".png", cv2.cvtColor(still, cv2.COLOR_RGB2BGR))[1].tofile(str(tmp_path / "still.png"))
    mask = np.zeros((H, W), np.uint8)
    cv2.rectangle(mask, (130, 60), (190, 130), 255, -1)
    cv2.imencode(".png", mask)[1].tofile(str(tmp_path / "mask.png"))
    plan = plan_dict(stabilize={"mask": "mask.png", "dilate_px": 9}, color={"reference": "still.png"})
    plan["clips"].append({"id": "b", "source": "clip.mp4", "out": "out/b.mp4", "color": {"reference": "still.png"}})
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    code, out, err = run_cli("fx", "clips", tmp_path / "plan.json")
    summary = json.loads(out)
    assert code == 0 and summary["written"] == ["a", "b"], err
    record = json.loads((tmp_path / "clips_record.json").read_text(encoding="utf-8"))
    a = record["clips"]["a"]
    assert a["source"]["frames"] == 12 and a["source"]["size"] == [W, H] and a["out"]["frames"] == 12
    assert a["stabilize"]["measure_size"] == [640, 360] and a["stabilize"]["mask"]["path"] == "mask.png" and len(a["stabilize"]["valid"]) == 12
    assert a["stabilize"]["tx"][0] == 0 and a["color"]["used"] and record["clips"]["b"]["stabilize"] is None
    from mv_pipeline.dance.clips import Source
    source = Source(tmp_path / "out" / "a.mp4")
    assert source.count() == 12 and source.fps == 16 and (source.width, source.height) == (W, H)
    source.close()
    code, out, _ = run_cli("fx", "clips", tmp_path / "plan.json")
    assert code == 0 and json.loads(out)["written"] == []                         # 同じ入力は作り直さない
    code, out, _ = run_cli("fx", "clips", tmp_path / "plan.json", "--only", "b")
    assert code == 0 and json.loads(out)["written"] == []
    (tmp_path / "out" / "b.mp4").write_bytes(b"edited by hand")
    code, _, err = run_cli("fx", "clips", tmp_path / "plan.json", "--only", "b")
    assert code == 1 and "--force" in err                                            # 記録と違う出力は上書きしない
    code, out, _ = run_cli("fx", "clips", tmp_path / "plan.json", "--only", "b", "--force")
    assert code == 0 and json.loads(out)["written"] == ["b"]
    code, _, err = run_cli("fx", "clips", tmp_path / "plan.json", "--only", "zz")
    assert code == 1 and "計画に無いクリップ" in err


def test_measure_from_needs_the_same_frame_count(tmp_path):
    frames = shaky_frames(10)
    write_clip(tmp_path / "clip.mp4", frames)
    write_clip(tmp_path / "small.mp4", [cv2.resize(f, (160, 90)) for f in frames[:8]])
    plan = plan_dict(measure_from="small.mp4")
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(PipelineError, match="コマ数 8 が source の 10 と違います"):
        clipprep.make_clips(tmp_path / "plan.json")
    write_clip(tmp_path / "small.mp4", [cv2.resize(f, (160, 90)) for f in frames])
    result = clipprep.make_clips(tmp_path / "plan.json")
    record = json.loads((tmp_path / "clips_record.json").read_text(encoding="utf-8"))["clips"]["a"]
    assert result["written"] == ["a"] and record["measure_from"]["path"] == "small.mp4" and record["stabilize"]["scale"] == W / 640
