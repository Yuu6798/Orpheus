"""mv dance passes の床への映り込み（reflection）と効果の時計（clock）: 映り込みの重ね方・水鏡のゆらし方（リポジトリ外の試作の式と同じ
値）、色のキー、spec の確かめ、合成の小さな要素での重ね。実作品の要素との比べは docs/dance.md の「要素別の描画を重ねる」。"""
import hashlib
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import passes, space  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402
from test_dance_passes import H, W, _passes, _spec, _write  # noqa: E402


def _blur(image, sigma):
    return cv2.GaussianBlur(image, (0, 0), sigma) if sigma > 0.2 else image


def test_the_mirror_adds_blurred_tinted_parts_in_order():
    rng = np.random.default_rng(4)
    rgba = rng.uniform(0, 1, (H, W, 4)).astype(np.float32)
    fx = rng.uniform(0, 1, (H, W, 3)).astype(np.float32)
    light, strength = np.array([0.96, 0.775, 0.665], np.float32), 0.16
    got = space.mirror((W, H), [(rgba[..., :3] * rgba[..., 3:4], 2.5, [light, strength]), (fx, 3.0, [0.35, 0.75])])
    want = np.zeros((H, W, 3), np.float32)                     # 試作の式: 0 から、人物 → 効果の順に足す
    want += _blur(rgba[..., :3] * rgba[..., 3:4], 2.5) * light * strength
    want += _blur(fx, 3.0) * 0.35 * 0.75
    assert got.dtype == np.float32 and np.array_equal(got, want)
    assert np.array_equal(space.mirror((W, H), []), np.zeros((H, W, 3), np.float32))


def test_the_ripple_matches_the_prototype_formula():
    rng = np.random.default_rng(5)
    image = rng.uniform(0, 1, (H, W, 3)).astype(np.float32)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    w, clock, scale = 0.6, 29.3, W / 1080
    shift = 3.0 * scale * w * np.sin(ys / (15.0 * scale) + clock * 2.5) \
        + 0.5 * 3.0 * scale * w * np.sin(ys / (15.0 * scale * 2.7) - clock * 1.7 + xs * 0.004)
    want = cv2.remap(image, xs + shift.astype(np.float32), ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    assert np.array_equal(space.ripple(image, w, clock, None, scale), want)
    assert space.ripple(image, 0.0, clock) is image                                  # 強さ 0 はそのまま
    moved = space.ripple(image, 1.0, 0.0, {"px": 0.0})
    assert np.array_equal(moved, image)                                              # 幅 0 ならずれない


def test_colors_and_color_keys_give_values_by_time():
    assert np.array_equal(passes.color_at([1.0, 0.5, 0.25], 3.0), np.float32([1.0, 0.5, 0.25]))
    keys = passes._color([[0.0, [1.0, 1.0, 1.0]], [1.0, [0.0, 0.5, 1.0]], [2.0, [0.0, 0.5, 1.0]], [2.0, [1.0, 0.0, 0.0]]], "tint")
    assert np.allclose(passes.color_at(keys, 0.5), [0.5, 0.75, 1.0])
    assert np.array_equal(passes.color_at(keys, 2.0), np.float32([1.0, 0.0, 0.0]))     # 同じ時刻のキーで跳ぶ
    assert np.array_equal(passes.color_at(keys, -1.0), np.float32([1.0, 1.0, 1.0]))
    with pytest.raises(PipelineError, match="昇順"):
        passes._color([[1.0, [1, 1, 1]], [0.0, [0, 0, 0]]], "tint")


def test_the_reflection_spec_is_filled_and_checked(tmp_path):
    _passes(tmp_path)
    spec = passes.load_spec(_spec(tmp_path, reflection={"char": {}, "ripple": {"amount": [[0.0, 0.0], [1.0, 1.0]]}},
                                  clock=[[0.0, 1.0], [10.0, 11.0]]))
    data = spec.data
    assert data["reflection"]["char"] == {"strength": 0.16, "blur": 2.5, "tint": [1.0, 1.0, 1.0]}
    assert data["reflection"]["ripple"] == {"amount": [[0.0, 0.0], [1.0, 1.0]], "px": 3.0, "wave_px": 15.0, "speed": [2.5, 1.7]}
    assert passes.clock_at(data, 5.0) == pytest.approx(6.0) and passes.clock_at({"clock": None}, 5.0) == 5.0
    assert passes.load_spec(_spec(tmp_path)).data["reflection"] is None                  # 書かなければ今までどおり
    for bad in ({"reflection": {"char": {"tint": [1, 1]}}}, {"reflection": {"char": {"strength": -1}}},
                {"reflection": {"glass": {}}}, {"reflection": {"ripple": {"wave_px": 0}}}, {"clock": [[1.0, 0.0], [0.0, 1.0]]}):
        with pytest.raises(PipelineError):
            passes.load_spec(_spec(tmp_path, **bad))


def _with_reflection(tmp_path):
    """合成の要素に、床で反転したカメラの人物 rchar（人物の赤い四角を床の下へ返した所）を足す。"""
    path = _passes(tmp_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = tmp_path / "passes"
    rows = []
    for T in (1, 2, 3):
        image = np.zeros((H, W, 4), np.uint8)
        image[32:44, 8:24] = (0, 0, 200, 255)
        name = f"rchar/f{T:04d}.png"
        _write(root / name, image)
        data = (root / name).read_bytes()
        rows.append({"T": T, "f": T, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest["passes"]["rchar"] = {"format": "png", "alpha": True, "frames": rows, "missing": []}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_the_reflection_is_added_to_the_room(tmp_path):
    plan = passes.load_passes(_with_reflection(tmp_path))
    plain = passes.compose(passes.load_spec(_spec(tmp_path, floor_shadow=None)).data, plan, 1).astype(int)
    lit = passes.load_spec(_spec(tmp_path, floor_shadow=None, reflection={"char": {"strength": 0.5, "blur": 0, "tint": [1, 0, 0]}}))
    frame = passes.compose(lit.data, plan, 1).astype(int)
    assert tuple(plain[38, 16]) == (100, 100, 100)
    assert frame[38, 16, 0] > 100 and tuple(frame[38, 16, 1:]) == (100, 100)          # 映り込みの所だけ、赤が足される
    assert np.array_equal(frame[:30], plain[:30])                                     # 人物と部屋の上は変わらない
    rippled = passes.load_spec(_spec(tmp_path, floor_shadow=None, clock=[[0.0, 3.0]],
                                     reflection={"char": {"strength": 0.5, "blur": 0, "tint": [1, 0, 0]},
                                                 "ripple": {"amount": 1.0, "px": 200.0}}))
    moved = passes.compose(rippled.data, plan, 1).astype(int)
    assert not np.array_equal(moved[32:44], frame[32:44]) and np.array_equal(moved[:30], frame[:30])


def test_the_reflection_needs_the_mirrored_pass(tmp_path):
    _passes(tmp_path)
    spec = _spec(tmp_path, reflection={"char": {}})
    with pytest.raises(PipelineError, match="rchar"):
        passes.render_run(spec, tmp_path / "runs")
