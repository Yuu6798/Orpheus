"""mv dance passes の分身（phantoms）: 炎の色の分身の足し方（リポジトリ外の試作の式と同じ値）、分身の映り込み、一覧の描く時間の外の
コマと near、人物の前後の重ね順、spec の確かめ。実作品の要素との比べは docs/dance.md の「要素別の描画を重ねる」。"""
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


def _mix(a, b, w):
    return np.asarray(a, np.float32) * (1 - w) + np.asarray(b, np.float32) * w


def test_a_phantom_glows_in_flame_colors_like_the_prototype():
    rng = np.random.default_rng(6)
    target = rng.uniform(0, 1, (H, W, 3)).astype(np.float32)
    picture = rng.uniform(0, 1, (H, W, 4)).astype(np.float32)
    dark, hot = [0.75, 0.16, 0.03], [1.0, 0.78, 0.36]
    strength, scale = 0.4, W / 1080
    got = space.phantom(target, picture, strength, None, scale)
    solid = picture[..., 3]                                              # 試作の式
    outline = np.clip(solid - _blur(solid, 9 * scale), 0, 1) * 2.0
    cover = (solid * (0.38 + 1.1 * outline) * strength)[..., None]
    body = _mix(dark, hot, np.clip(picture[..., :3].mean(axis=2), 0, 1)[..., None]) * cover
    want = target + body + _blur(body, 14 * scale) * 0.7
    assert got.dtype == np.float32 and np.array_equal(got, want)
    glow = space.phantom_glow(picture, dark, hot)
    assert np.array_equal(glow, _mix(dark, hot, np.clip(picture[..., :3].mean(axis=2), 0, 1)[..., None]) * picture[..., 3:4])


def _with_phantoms(tmp_path, near):
    """合成の要素に、分身 ph0（左上の白い四角。T 2・3 だけ。near は引数）と、その映り込み rph0 を足す。"""
    path = _passes(tmp_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = tmp_path / "passes"
    for kind, box in (("ph0", (slice(14, 26), slice(12, 30))), ("rph0", (slice(36, 44), slice(12, 30)))):
        rows = []
        for T in (2, 3):
            image = np.zeros((H, W, 4), np.uint8)
            image[box] = (255, 255, 255, 255)
            name = f"{kind}/f{T:04d}.png"
            _write(root / name, image)
            data = (root / name).read_bytes()
            rows.append({"T": T, "f": T, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "near": near})
        manifest["passes"][kind] = {"format": "png", "alpha": True, "frames": rows, "missing": [], "outside": [1]}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_the_list_keeps_the_window_and_the_near_flags(tmp_path):
    plan = passes.load_passes(_with_phantoms(tmp_path, near=True))
    assert plan.phantoms() == [0] and sorted(plan.files["ph0"]) == [2, 3] and plan.near["ph0"] == {2: True, 3: True}
    manifest = json.loads((tmp_path / "passes.json").read_text(encoding="utf-8"))
    manifest["passes"]["ph0"]["outside"] = [1, 2]                         # 描いたコマと重なる
    (tmp_path / "passes.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="outside"):
        passes.load_passes(tmp_path / "passes.json")
    manifest["passes"]["ph0"]["outside"] = []                             # 足りない
    (tmp_path / "passes.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="そろっていません"):
        passes.load_passes(tmp_path / "passes.json")
    manifest["passes"]["ghost"] = manifest["passes"].pop("ph0")
    (tmp_path / "passes.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="知らない要素"):
        passes.load_passes(tmp_path / "passes.json")


def test_far_phantoms_go_behind_the_dancer_and_near_ones_in_front(tmp_path):
    far = tmp_path / "far"
    near = tmp_path / "near"
    far.mkdir()
    near.mkdir()
    spec = {"floor_shadow": None, "phantoms": {"strength": 1.0, "glow": 0, "edge_blur": 0}}
    far_plan = passes.load_passes(_with_phantoms(far, near=False))
    near_plan = passes.load_passes(_with_phantoms(near, near=True))
    behind = passes.compose(passes.load_spec(_spec(far, **spec)).data, far_plan, 2).astype(int)
    front = passes.compose(passes.load_spec(_spec(near, **spec)).data, near_plan, 2).astype(int)
    plain = passes.compose(passes.load_spec(_spec(far, floor_shadow=None)).data, far_plan, 2).astype(int)
    assert tuple(behind[20, 16]) == tuple(plain[20, 16]) == (200, 0, 0)          # 人物の四角の中: 奥の分身は隠れる
    assert front[20, 16, 1] > 0                                                   # 手前の分身は人物の上に光る
    assert behind[15, 28, 1] > plain[15, 28, 1]                                   # 人物の外では、奥の分身も光る
    first = passes.compose(passes.load_spec(_spec(far, **spec)).data, far_plan, 1)
    assert np.array_equal(first, passes.compose(passes.load_spec(_spec(far, floor_shadow=None)).data, far_plan, 1))   # 描く時間の外


def test_the_mirrored_phantoms_join_the_reflection(tmp_path):
    plan = passes.load_passes(_with_phantoms(tmp_path, near=False))
    spec = passes.load_spec(_spec(tmp_path, floor_shadow=None, phantoms={"strength": 0.5},
                                  reflection={"phantoms": {"blur": 0, "gain": 0.45}}))
    mirror = passes.reflection_layer(spec.data, plan, 2)
    assert np.allclose(mirror[40, 16], np.float32([1.0, 0.78, 0.36]) * 0.5 * 0.45)    # 白い分身 → 熱い炎の色 × 強さ × gain
    assert np.array_equal(passes.reflection_layer(spec.data, plan, 1), np.zeros((H, W, 3), np.float32))


def test_the_phantom_spec_is_filled_and_needs_the_phantom_passes(tmp_path):
    _passes(tmp_path)
    data = passes.load_spec(_spec(tmp_path, phantoms={}, reflection={"phantoms": {}})).data
    assert data["phantoms"] == {"strength": 0.62, "dark": [0.75, 0.16, 0.03], "hot": [1.0, 0.78, 0.36], "fill": 0.38, "edge": 1.1,
                                "edge_blur": 9.0, "edge_gain": 2.0, "glow": 14.0, "glow_gain": 0.7}
    assert data["reflection"]["phantoms"] == {"blur": 3.0, "gain": 0.45}
    with pytest.raises(PipelineError):
        passes.load_spec(_spec(tmp_path, phantoms={"hot": [1, 1]}))
    with pytest.raises(PipelineError, match="ph<i>"):
        passes.render_run(_spec(tmp_path, phantoms={}), tmp_path / "runs")
