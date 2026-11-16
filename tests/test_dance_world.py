"""mv dance passes の世界の切り替え（world。fx.burn の焼け抜け）・衝撃（impact）・時刻で変わる灯りの色（figure・rim・bloom のキー）:
試作の式と同じ値（別の世界の背景・衝撃）、焼け抜けが fx.burn と同じ式で踊り手の重心から広がること、部屋の無いコマ、spec の確かめ。"""
import hashlib
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import passes, space  # noqa: E402
from mv_pipeline.fx import burn as fxburn  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402
from test_dance_passes import H, W, _passes, _spec, _write  # noqa: E402


def _blur(image, sigma):
    return cv2.GaussianBlur(image, (0, 0), sigma) if sigma > 0.2 else image


def test_the_other_world_and_the_impact_follow_the_prototype():
    rng = np.random.default_rng(7)
    alpha = rng.uniform(0, 1, (H, W)).astype(np.float32)
    pool = rng.uniform(0, 1, (H, W, 3)).astype(np.float32)
    settings = {**space.WORLD}
    scale = W / 1080
    got = space.other_world(alpha, pool, settings, scale)
    halo = _blur(alpha, 110 * scale)[..., None]                       # 試作の式
    want = np.asarray([0.012, 0.009, 0.018], np.float32) + pool * np.asarray([0.40, 0.15, 0.055], np.float32) \
        + halo * np.array([0.10, 0.04, 0.015], np.float32)
    assert np.array_equal(got, want)
    frame = rng.uniform(0, 1, (H, W, 3)).astype(np.float32)
    total = frame.copy()
    for step in (1, 2, 3):
        zoom = 1 + 0.05 * 0.6 * step
        move = np.array([[zoom, 0, (1 - zoom) * W / 2], [0, zoom, (1 - zoom) * H / 2]], np.float64)
        total += cv2.warpAffine(frame, move, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    assert np.array_equal(space.impact(frame, 0.6), total / 4)
    assert space.impact(frame, 0.01) is frame


def test_the_world_switch_burns_out_from_the_dancer():
    room = np.full((H, W, 3), 0.5, np.float32)
    alpha = np.zeros((H, W), np.float32)
    alpha[10:20, 2:8] = 1.0                                             # 人物は左上
    settings = {**space.WORLD, "halo": {"sigma": 0.0, "color": [0, 0, 0]}}
    burn = {"seed": 0, "scale": 0.08, "soft": 0.035, "char": 0.0, "bias": 1.0, "edge": None}
    assert np.array_equal(space.world(room, alpha, 0.0, None, settings, burn), room)
    dark = space.world(room, alpha, 1.0, None, settings, burn)
    assert np.allclose(dark, [0.012, 0.009, 0.018])
    assert np.allclose(space.world(None, alpha, 0.3, None, settings, burn), dark)            # 部屋が無ければ別の世界
    assert not space.world(None, alpha, 0.0, None, settings, burn).any()                     # 切り替える前で部屋が無ければ黒（試作と同じ）
    half = space.world(room, alpha, 0.5, None, settings, burn)
    near, far = half[15, 5], half[H - 2, W - 2]
    assert near[0] < 0.1 and far[0] > 0.4                               # 踊り手の近くから先に焼ける（bias 1）
    cx, cy = space.centroid(alpha)
    field = fxburn.field({**burn, "from": [cx / W, cy / H]}, (W, H))
    want = fxburn.composite(room * 255, np.broadcast_to(dark, room.shape) * 255, field, 0.5, {**burn, "from": [cx / W, cy / H]}) / 255
    assert np.allclose(half, want)                                      # fx.burn の式そのもの
    assert space.centroid(np.zeros((H, W), np.float32)) == (W / 2, H / 2)


def _with_world(tmp_path):
    """合成の要素で、部屋は T 1・2 だけ（T 3 は outside）、床の光のたまり pool は T 2・3 だけ。"""
    path = _passes(tmp_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["passes"]["room"]["frames"] = manifest["passes"]["room"]["frames"][:2]
    manifest["passes"]["room"]["outside"] = [3]
    rows = []
    for T in (2, 3):
        image = np.full((H, W, 3), 255, np.uint8)
        name = f"pool/f{T:04d}.png"
        _write(tmp_path / "passes" / name, image)
        data = (tmp_path / "passes" / name).read_bytes()
        rows.append({"T": T, "f": T, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest["passes"]["pool"] = {"format": "png", "alpha": False, "frames": rows, "missing": [], "outside": [1]}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_the_world_switch_in_the_composite(tmp_path):
    plan = passes.load_passes(_with_world(tmp_path))
    spec = passes.load_spec(_spec(tmp_path, floor_shadow=None, world={"mix": [[0.0, 0.0], [1 / 30, 0.0], [2 / 30, 1.0]],
                                                                       "halo": {"sigma": 0, "color": [0, 0, 0]}}))
    first = passes.compose(spec.data, plan, 1).astype(int)
    assert tuple(first[2, 2]) == (100, 100, 100)                         # まだ部屋
    last = passes.compose(spec.data, plan, 3).astype(int)               # 部屋の無いコマ: 空 + 床の光のたまり（pool の白 × 色）
    assert tuple(last[2, 2]) == tuple(int(v * 255 + 0.5) for v in np.float32([0.412, 0.159, 0.073]))
    assert tuple(last[20, 16]) == (200, 0, 0)                           # 人物はそのまま
    plain = passes.compose(passes.load_spec(_spec(tmp_path, floor_shadow=None)).data, plan, 3).astype(int)
    assert tuple(plain[2, 2]) == (0, 0, 0)                               # world を書かなければ、部屋の無いコマは黒（今までどおり）


def test_elements_outside_their_window(tmp_path):
    """描く時間の外（outside）のコマ: 顔の範囲が無いコマは顔をならさず、陰影は人物のどのコマにも要る。"""
    path = _passes(tmp_path, kinds=("room", "char", "disc", "shade", "head"))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["passes"]["head"]["frames"] = manifest["passes"]["head"]["frames"][:1]
    manifest["passes"]["head"]["outside"] = [2, 3]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    plan = passes.load_passes(path)
    figure = {"light": [1, 1, 1], "shadow": [0.5, 0.5, 0.5], "face_smooth": 0.5}
    data = passes.load_spec(_spec(tmp_path, floor_shadow=None, figure=figure)).data
    assert tuple(passes.compose(data, plan, 3).astype(int)[20, 16]) == (100, 0, 0)     # 顔の範囲の無いコマも重なる
    manifest["passes"]["shade"]["frames"] = manifest["passes"]["shade"]["frames"][:2]
    manifest["passes"]["shade"]["outside"] = [3]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="どのコマにも shade"):
        passes.render_run(_spec(tmp_path, figure=figure), tmp_path / "runs")


def test_looks_and_strengths_can_change_with_time(tmp_path):
    _passes(tmp_path, kinds=("room", "char", "disc", "shade"))          # 陰影の要素は人物の所で 0（陰の色だけが効く）
    plan = passes.load_passes(tmp_path / "passes.json")

    def frames(**parts):
        data = passes.load_spec(_spec(tmp_path, floor_shadow=None, **parts)).data
        return passes.compose(data, plan, 1).astype(int), passes.compose(data, plan, 2).astype(int), passes.compose(data, plan, 3).astype(int)

    first, middle, last = frames(figure={"light": [1, 1, 1], "shadow": [[0.0, [1, 1, 1]], [2 / 30, [0.5, 0.5, 0.5]]]})
    assert (first[20, 16, 0], middle[20, 16, 0], last[20, 16, 0]) == (200, 150, 100)    # 陰の色が 1 → 0.5 へ（1/30 秒で半分）
    first, middle, last = frames(rim={"rgb": [[0.0, [0, 0, 0]], [2 / 30, [0, 255, 0]]], "strength": [[0.0, 0.0], [2 / 30, 1.0]],
                                      "kernel": 200, "blur": 0, "gradient": None})
    assert first[10, 16, 1] == 0 and 0 < middle[10, 16, 1] < last[10, 16, 1]          # 縁の光が緑で強くなる
    assert last[20, 16, 1] == 0                                                          # 内側は光らない
    first, middle, last = frames(bloom={"threshold": [[0.0, 2.0], [2 / 30, 0.0]], "strength": [[0.0, 0.0], [2 / 30, 0.5]]})
    assert tuple(first[2, 2]) == (100, 100, 100) and last[2, 2, 0] > 100                # にじみ: しきい値が下がり強さが上がる
    for bad in ({"figure": {"light": [[0.0, [1, 1]]], "shadow": [1, 1, 1]}}, {"world": {"sky": [0, 0, 0]}},
                {"impact": {"amount": -1}}, {"world": {"mix": 0, "burn": {"from": [0, 0]}}}):
        with pytest.raises(PipelineError):
            passes.load_spec(_spec(tmp_path, **bad))
    data = passes.load_spec(_spec(tmp_path, world={"mix": 0.5}, impact={"amount": [[0.0, 0.0]]})).data
    assert data["world"]["burn"] == {"seed": 0, "scale": 0.08, "soft": 0.035, "edge": None, "char": 0.0, "bias": 0.0}
    assert data["impact"] == {"amount": [[0.0, 0.0]], "zoom": 0.05, "steps": 3}
