"""mv dance passes の手前の板（cards）と柱が横切るつなぎ（wipes）: 試作の式と同じ値（板の置き方と写し方・揺れ・柱の作り方と入れ替え）、
重ねる順（板はいちばん手前、出す時間、つなぎの相手の画とその板）、spec と入力の確かめ。実作品の要素との比べは docs/dance.md。"""
import hashlib
import json
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import passes, space  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402
from test_dance_passes import H, W, _passes, _spec, _write  # noqa: E402


def _rgba(path, size=(20, 40), color=(0, 255, 0), depth=np.uint8):
    """不透明な四角（右半分は半透明）の RGBA の PNG（前乗算でない）。"""
    high, wide = size
    top = 65535 if depth == np.uint16 else 255
    image = np.zeros((high, wide, 4), depth)
    image[..., 2], image[..., 1], image[..., 0] = (np.array(color) / 255 * top).astype(depth)
    image[..., 3] = top
    image[:, wide // 2:, 3] = top // 2
    path.write_bytes(cv2.imencode(".png", image)[1].tobytes())
    return path


def _view(width=1080, height=1920):
    """原点から +Y を水平に見るカメラ（焦点距離 2000 画素、主点は画面の中心）。"""
    matrix = np.eye(4)
    matrix[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    return {"matrix": matrix.tolist(), "focal_px": 2000.0, "principal_px": [width / 2, height / 2], "resolution": [width, height]}


def _blur(image, sigma):
    return cv2.GaussianBlur(image, (0, 0), sigma) if sigma > 0.2 else image


def test_a_card_is_placed_and_drawn_like_the_prototype():
    reference = _view()
    rng = np.random.default_rng(4)
    rgb = rng.uniform(0, 1, (30, 50, 3)).astype(np.float32)
    alpha = rng.uniform(0, 1, (30, 50)).astype(np.float32)
    corners = space.card_corners(reference, [700.0, 1200.0], 2.0, 400.0, [0.5, 0.25], (50, 30))
    # 試作の式（写し）: 基準のカメラから、奥行き 2 m・幅 400 画素ぶん
    M = np.array(reference["matrix"])
    right, up, forward, position = M[:3, 0], M[:3, 1], -M[:3, 2], M[:3, 3]
    wide = 400.0 * 2.0 / 2000.0
    high = wide * 30 / 50
    anchor = position + 2.0 * (forward + (700 - 540) / 2000 * right - (1200 - 960) / 2000 * up)
    want = [anchor + (u / 50 - 0.5) * wide * right - (v / 30 - 0.25) * high * up for u, v in ((0, 0), (50, 0), (50, 30), (0, 30))]
    assert np.allclose(corners, want)
    u, v, depth = space.project_points(reference, corners)
    assert np.allclose(depth, 2.0) and np.allclose([u[0], v[0]], [700 - 0.5 * 400, 1200 - 0.25 * 400 * 30 / 50])   # 基準のカメラでは at に
    frame = rng.uniform(0, 1, (192, 108, 3)).astype(np.float32)
    small = {**reference, "focal_px": 200.0, "principal_px": [54.0, 96.0], "resolution": [108, 192]}
    got = space.card(frame, rgb, alpha, corners, small, 1.5, 0.8, (108, 192))
    u, v, _ = space.project_points(small, corners)                           # 試作の draw_cards の写し
    target = np.stack([u, v], 1).astype(np.float32)
    transform = cv2.getPerspectiveTransform(np.array([[0, 0], [50, 0], [50, 30], [0, 30]], np.float32), target)
    warped = cv2.warpPerspective(rgb * alpha[..., None], transform, (108, 192), flags=cv2.INTER_LINEAR, borderValue=0)
    cover = cv2.warpPerspective(alpha, transform, (108, 192), flags=cv2.INTER_LINEAR, borderValue=0)
    warped, cover = _blur(warped, 1.5), _blur(cover, 1.5)
    assert np.allclose(got, frame * (1 - cover[..., None]) + warped * 0.8, atol=1e-6)
    behind = space.card_corners(reference, [540.0, 960.0], 2.0, 400.0, [0.5, 0.5], (50, 30)) - np.array([0.0, 3.0, 0.0])
    assert space.card(frame, rgb, alpha, behind, small, 0.0, 1.0, (108, 192)) is frame              # カメラの後ろは重ねない


def test_the_wave_and_the_pillar_follow_the_prototype():
    rng = np.random.default_rng(5)
    rgb = rng.uniform(0, 1, (40, 60, 3)).astype(np.float32)
    alpha = rng.uniform(0, 1, (40, 60)).astype(np.float32)
    got_rgb, got_alpha = space.wave(rgb, alpha, 6.0, 1.3, 260.0)
    xs = np.arange(60, dtype=np.float32)                                       # 試作の客の頭の影の揺れ（写し）
    shift = 6.0 * np.sin(1.3 + xs / 260.0)
    map_x = np.tile(xs, (40, 1))
    map_y = (np.arange(40, dtype=np.float32)[:, None] - shift[None, :]).astype(np.float32)
    assert np.array_equal(got_rgb, cv2.remap(rgb, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
    assert np.array_equal(got_alpha, cv2.remap(alpha, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
    width, height, scale = 108, 192, 0.1
    tall_rgb = rng.uniform(0, 1, (900, 30, 3)).astype(np.float32)
    tall_alpha = rng.uniform(0, 1, (900, 30)).astype(np.float32)
    settings = dict(space.PILLAR)
    image, mask, wide = space.pillar_image(tall_rgb, tall_alpha, (width, height), settings, 0.3, 30.0, scale)
    wide_want = int(round(760 * scale))                                        # 試作の柱の作り方（写し）
    rows = int(math.ceil((height + 40 * scale) / (wide_want / 30)))
    size = (wide_want, int(round(height + 40 * scale)))
    want = cv2.GaussianBlur(cv2.resize(tall_rgb[:rows] * tall_alpha[:rows, :, None], size, interpolation=cv2.INTER_CUBIC), (0, 0), 5 * scale) * 0.62
    want_mask = cv2.GaussianBlur(cv2.resize(tall_alpha[:rows], size, interpolation=cv2.INTER_CUBIC), (0, 0), 5 * scale)
    streak = max(1, int((width + wide_want) / 0.3 / 30 * 0.5))
    assert wide == wide_want and np.array_equal(image, cv2.blur(want, (streak, 1))) and np.array_equal(mask, cv2.blur(want_mask, (streak, 1)))
    for direction in (-1, 1):
        post, cover, centre = space.pillar(image, mask, wide, 0.4, direction, (width, height), settings, scale)
        flipped = (image, mask) if direction < 0 else (image[:, ::-1], mask[:, ::-1])
        travelled = 0.15 * (0.4 * 0.4 * (3 - 2 * 0.4)) + 0.85 * 0.4
        want_centre = (width + wide / 2) - (width + wide) * travelled if direction < 0 else (-wide / 2) + (width + wide) * travelled
        move = np.array([[1, 0, want_centre - wide / 2], [0, 1, -20 * scale]], np.float64)
        assert centre == pytest.approx(want_centre)
        assert np.array_equal(post, cv2.warpAffine(np.ascontiguousarray(flipped[0]), move, (width, height), borderValue=0))
        old, new = rng.uniform(0, 1, (height, width, 3)).astype(np.float32), rng.uniform(0, 1, (height, width, 3)).astype(np.float32)
        side = np.clip(-direction * (np.arange(width, dtype=np.float32) - centre) / (40 * scale) + 0.5, 0, 1)[None, :, None]
        want_frame = (old * (1 - side) + new * side) * (1 - cover[..., None]) + post
        assert np.array_equal(space.wipe(old, new, post, cover, centre, direction, settings, scale), want_frame)


def _cameras(tmp_path, name="cameras.json", Ts=(1, 2, 3)):
    camera = np.eye(4)
    camera[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    camera[:3, 3] = [0.0, -5.0, 1.0]
    record = {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [W * 10, H * 10],
              "frames": [{"T": T, "matrix": camera.tolist(), "focal_px": 300.0, "principal_px": [W * 5, H * 5], "distance_m": 5.0}
                         for T in Ts]}
    (tmp_path / name).write_text(json.dumps(record), encoding="utf-8")
    return name


def test_a_card_is_drawn_in_front_of_everything_in_its_time(tmp_path):
    _passes(tmp_path)
    _rgba(tmp_path / "plate.png", (20, 40), color=(0, 0, 255))
    corners = [[-0.6, -4.0, 1.4], [0.6, -4.0, 1.4], [0.6, -4.0, 0.6], [-0.6, -4.0, 0.6]]           # カメラの 1 m 先に、人物の前を覆う板
    card = {"image": "plate.png", "corners": corners, "during": [[0.0, 1 / 30]], "gain": [[0.0, 1.0], [1 / 30, 0.5]]}
    plan = passes.load_passes(tmp_path / "passes.json")
    spec = passes.load_spec(_spec(tmp_path, floor_shadow=None, cameras=_cameras(tmp_path), cards=[card]))
    where = passes.load_space(spec, plan)
    scene = passes.load_scene(spec, plan)
    first = passes.compose(spec.data, plan, 1, where=where, scene=scene).astype(int)
    assert tuple(first[20, 10]) == (0, 0, 255)                                # 不透明な左半分は板の青（人物の上にも）
    assert first[20, 22, 2] > 0 and first[20, 22, 0] > 0                       # 半透明の右半分は人物と混ざる
    later = passes.compose(spec.data, plan, 2, where=where, scene=scene).astype(int)
    assert tuple(later[20, 16]) == (200, 0, 0)                                 # 出す時間の外は板が無い
    with pytest.raises(PipelineError, match="corners"):
        passes.load_spec(_spec(tmp_path, cards=[{"image": "plate.png"}]))
    with pytest.raises(PipelineError, match="focal_ref"):
        passes.load_spec(_spec(tmp_path, cards=[{**card, "blur": 2.0}]))
    bad = passes.load_spec(_spec(tmp_path, cameras="cameras.json", cards=[{"image": "plate.png", "place": {"reference": "a", "at": [0, 0],
                                                                                                        "depth": 1, "width_px": 10}}]))
    with pytest.raises(PipelineError, match="references"):
        passes.load_scene(bad, plan)


def _alt(tmp_path, Ts):
    """相手の要素（部屋が緑）: とびとびの T だけ。"""
    root = tmp_path / "alt"
    entries = {}
    for kind in ("room", "char", "disc"):
        rows = []
        for T in Ts:
            if kind == "char":
                image = np.zeros((H, W, 4), np.uint8)
            elif kind == "room":
                image = np.zeros((H, W, 3), np.uint8)
                image[..., 1] = 180
            else:
                image = np.zeros((H, W, 3), np.uint8)
            name = f"{kind}/f{T:04d}.png"
            _write(root / name, image)
            data = (root / name).read_bytes()
            rows.append({"T": T, "f": T, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        entries[kind] = {"format": "png", "alpha": kind == "char", "frames": rows, "missing": []}
    manifest = {"schema_version": "model3d-passes/0.1", "directory": str(root), "fps": 30, "resolution": [W, H], "passes": entries}
    (tmp_path / "alt.json").write_text(json.dumps(manifest), encoding="utf-8")
    return "alt.json"


def test_the_wipe_swaps_the_shots_behind_a_moving_pillar(tmp_path):
    _passes(tmp_path)
    pillar = np.zeros((400, 10, 4), np.uint8)
    pillar[..., 3] = 255                                                         # 黒い柱
    (tmp_path / "pillar.png").write_bytes(cv2.imencode(".png", pillar)[1].tobytes())
    wipe = {"at": 1 / 30, "seconds": 0.07, "direction": -1, "passes": _alt(tmp_path, [1, 2, 3]),
            "pillar": {"image": "pillar.png", "width_px": 60, "blur": 1.0, "gain": 1.0, "edge_px": 10}}
    plan = passes.load_passes(tmp_path / "passes.json")
    spec = passes.load_spec(_spec(tmp_path, floor_shadow=None, wipes=[wipe]))
    scene = passes.load_scene(spec, plan)
    frames = [passes.compose(spec.data, plan, T, scene=scene).astype(int) for T in (1, 2, 3)]
    # 1 コマ目（0 秒）: 柱は右から入るところ。左は今の画（灰）、右は相手の画（緑）が見え始める
    assert tuple(frames[0][5, 2]) == (100, 100, 100)
    # 3 コマ目（2/30 秒。つなぎの外）: 今の画のまま
    assert tuple(frames[2][5, 2]) == (100, 100, 100) and tuple(frames[2][5, W - 2]) == (100, 100, 100)
    middle = frames[1]                                                           # at ちょうど: 相手の画が古い画で、柱の右側が今の画
    assert middle[5, W - 1, 1] != middle[5, 0, 1] or middle[5, W - 1, 0] != middle[5, 0, 0]
    gap = {**wipe, "passes": _alt(tmp_path, [1])}
    with pytest.raises(PipelineError, match="相手の要素"):
        passes.load_scene(passes.load_spec(_spec(tmp_path, wipes=[gap])), plan)
    with pytest.raises(PipelineError, match="重なって"):
        passes.load_spec(_spec(tmp_path, wipes=[wipe, {**wipe, "at": 0.05}]))
    _alt(tmp_path, [1, 2, 3])                                                     # 相手の要素を、つなぎの間のコマがそろった一覧に戻す
    lamps = {"items": "sprites.json", "images": ["pillar.png"]}                    # 小さな絵があれば、相手のカメラが要る（Codex の指摘）
    with pytest.raises(PipelineError, match="cameras"):
        passes.load_scene(passes.load_spec(_spec(tmp_path, cameras=_cameras(tmp_path), sprites=lamps, wipes=[wipe])), plan)
    (tmp_path / "sprites.json").write_text(json.dumps({"schema_version": "dance-sprites/0.1", "unit": "m",
                                                        "frames": [{"T": T, "items": []} for T in (1, 2, 3)]}), encoding="utf-8")
    named = {**wipe, "cameras": _cameras(tmp_path, "alt_cameras.json")}
    assert passes.load_scene(passes.load_spec(_spec(tmp_path, cameras="cameras.json", sprites=lamps, wipes=[named])), plan).wipes[0]["where"]
