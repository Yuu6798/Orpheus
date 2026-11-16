"""mv dance passes の効果（effects。model3d の render.passes の fx）と効果の映り込み（reflection.fx。rfx）: 試作の式と同じ値、
重ねる順（近い小さな絵の後・衝撃の前、映り込みは人物の次）、描く時間の外のコマ、要素の無い一覧を止めること、spec の既定。"""
import hashlib
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import finish, passes, space  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402
from test_dance_passes import H, W, _passes, _spec, _write  # noqa: E402


def _blur(image, sigma):
    return cv2.GaussianBlur(image, (0, 0), sigma) if sigma > 0.2 else image


def _add(path, kind, images, outside=()):
    """一覧 path に要素 kind を足す（images は {T: BGR の uint8}）。"""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent / "passes"
    rows = []
    for T, image in images.items():
        name = f"{kind}/f{T:04d}.png"
        _write(root / name, image)
        data = (root / name).read_bytes()
        rows.append({"T": T, "f": T, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest["passes"][kind] = {"format": "png", "alpha": False, "frames": rows, "missing": [], **({"outside": list(outside)} if outside else {})}
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_the_effects_follow_the_prototype():
    rng = np.random.default_rng(5)
    frame = rng.uniform(0, 1, (H, W, 3)).astype(np.float32)
    picture = rng.uniform(0, 1, (H, W, 3)).astype(np.float32)
    scale = W / 1080
    soft = _blur(picture, 1.0 * scale)                                   # 試作の式（写し）
    want = frame + soft * 0.9 + _blur(soft, 12 * scale) * 0.32
    assert np.array_equal(space.effects(frame, picture, 0.9, 0.32, space.EFFECTS, scale), want)
    wide = {**space.EFFECTS, "blur": 60.0, "glow_blur": 400.0}
    soft = _blur(picture, 60.0 * scale)
    assert np.array_equal(space.effects(frame, picture, 0.5, 0.1, wide, scale), frame + soft * 0.5 + _blur(soft, 400.0 * scale) * 0.1)


def test_the_effects_in_the_composite_and_the_reflection(tmp_path):
    path = _passes(tmp_path)
    spark = np.zeros((H, W, 3), np.uint8)
    spark[2:6, 2:6] = (0, 0, 255)                                        # BGR の赤い光（人物の外）
    _add(path, "fx", {2: spark, 3: spark}, outside=[1])
    below = np.zeros((H, W, 3), np.uint8)
    below[40:44, 10:20] = (0, 255, 0)                                    # 床の下の緑（映り込み）
    _add(path, "rfx", {1: below, 2: below, 3: below})
    plan = passes.load_passes(path)
    plain = passes.load_spec(_spec(tmp_path, floor_shadow=None)).data
    lit = passes.load_spec(_spec(tmp_path, floor_shadow=None, effects={"gain": 1.0, "glow": 0.0, "blur": 0})).data
    for T in (1, 2):
        a, b = passes.compose(plain, plan, T).astype(int), passes.compose(lit, plan, T).astype(int)
        if T == 1:
            assert np.array_equal(a, b)                                  # 描く時間の外のコマは足さない
        else:
            assert tuple(b[3, 3]) == (255, 100, 100) and tuple(a[3, 3]) == (100, 100, 100)
            assert np.array_equal(a[20, 16], b[20, 16])                 # 人物の所は変わらない
    mirrored = passes.load_spec(_spec(tmp_path, floor_shadow=None, reflection={"fx": {"blur": 0, "gain": 0.5, "strength": [[0.0, 0.0], [2 / 30, 1.0]]}})).data
    first, last = passes.reflection_layer(mirrored, plan, 1), passes.reflection_layer(mirrored, plan, 3)
    assert not first.any()                                               # 強さ 0
    assert last[41, 15, 1] == pytest.approx(0.5) and last[41, 15, 0] == 0 and last[2, 2].sum() == 0
    data = passes.load_spec(_spec(tmp_path, effects={}, reflection={"fx": {}})).data
    assert data["effects"] == {"gain": 0.9, "glow": 0.32, "blur": 1.0, "glow_blur": 12.0}
    assert data["reflection"]["fx"] == {"blur": 3.0, "gain": 0.35, "strength": 1.0}


def test_the_reflection_adds_the_effects_after_the_character(tmp_path):
    path = _passes(tmp_path)
    figure = np.zeros((H, W, 4), np.uint8)
    figure[36:44, 8:24] = (255, 0, 0, 255)
    _add(path, "rfx", {T: np.full((H, W, 3), 30, np.uint8) for T in (1, 2, 3)})
    rows = []
    for T in (1, 2, 3):
        name = f"rchar/f{T:04d}.png"
        _write(tmp_path / "passes" / name, figure)
        data = (tmp_path / "passes" / name).read_bytes()
        rows.append({"T": T, "f": T, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["passes"]["rchar"] = {"format": "png", "alpha": True, "frames": rows, "missing": []}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = passes.load_passes(path)
    spec = passes.load_spec(_spec(tmp_path, reflection={"char": {"strength": 0.5, "blur": 1.0}, "fx": {"blur": 2.0, "gain": 0.35, "strength": 0.7}})).data
    got = passes.reflection_layer(spec, plan, 2)
    char = passes.read_image(plan.files["rchar"][2], alpha=True)
    flames = passes.read_image(plan.files["rfx"][2])
    unit = W / 1080
    want = np.zeros((H, W, 3), np.float32)
    want += finish.blur(char[..., :3] * char[..., 3:4], 1.0, unit) * np.asarray([1.0, 1.0, 1.0], np.float32) * 0.5
    want += finish.blur(flames, 2.0, unit) * 0.35 * 0.7                  # 人物 → 効果の順に、ぼかして gain と強さを掛けて足す
    assert np.array_equal(got, want)


def test_the_effects_need_their_passes(tmp_path):
    _passes(tmp_path)
    for parts, word in (({"effects": {}}, "fx"), ({"reflection": {"fx": {}}}, "rfx")):
        with pytest.raises(PipelineError, match=word):
            passes.render_run(_spec(tmp_path, **parts), tmp_path / "runs")
    for bad in ({"effects": {"gain": -1}}, {"effects": {"halo": 1}}, {"reflection": {"fx": {"blur": -1}}}):
        with pytest.raises(PipelineError):
            passes.load_spec(_spec(tmp_path, **bad))
