"""mv dance passes の小さな絵（sprites）: 絵の読み方（8・16 ビット、前乗算、縮めた絵）、1 つの置き方、3D の位置からの 3 枚（遠い・近い・
映り込み）、カメラの控えと位置の読み方、人物の前後の重ね順、spec の確かめ。実作品の要素との比べは docs/dance.md。"""
import hashlib
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import passes, space  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402
from test_dance_passes import H, W, _passes, _spec  # noqa: E402


def _sprite(path, size=(40, 20), depth=np.uint8, color=(255, 0, 0)):
    """赤い四角の絵（前乗算でないアルファ。上半分は半透明）。"""
    high, wide = size
    top = 65535 if depth == np.uint16 else 255
    image = np.zeros((high, wide, 4), depth)
    image[..., 2], image[..., 1], image[..., 0] = (np.array(color) / 255 * top).astype(depth)
    image[..., 3] = top
    image[: high // 2, :, 3] = top // 2
    ok, data = cv2.imencode(".png", image)
    path.write_bytes(data.tobytes())
    return path


def test_sprites_are_read_premultiplied_with_smaller_copies(tmp_path):
    levels = space.sprite_levels(_sprite(tmp_path / "a.png", (100, 52)))
    first = levels[0]
    assert first.dtype == np.float32 and first.shape == (100, 52, 4)
    assert np.allclose(first[60, 10], [1, 0, 0, 1]) and np.allclose(first[10, 10], [127 / 255, 0, 0, 127 / 255])   # 色 × アルファ
    assert [lv.shape[:2] for lv in levels] == [(100, 52), (50, 26), (25, 13), (12, 6)]        # 短い辺が 12 以下になるまで半分ずつ
    deep = space.sprite_levels(_sprite(tmp_path / "b.png", (100, 52), np.uint16))
    assert np.allclose(deep[0][60, 10], [1, 0, 0, 1]) and abs(deep[0][10, 10, 3] - 0.5) < 1e-4
    (tmp_path / "rgb.png").write_bytes(cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes())
    with pytest.raises(ValueError, match="RGBA"):
        space.sprite_levels(tmp_path / "rgb.png")


def test_a_sprite_is_pasted_upright_at_its_size(tmp_path):
    levels = space.sprite_levels(_sprite(tmp_path / "a.png", (40, 20)))
    layer = np.zeros((200, 200, 4), np.float32)
    space.paste(layer, levels, (100.0, 100.0), (100.0, 80.0), 1.0, 0.0)          # 中心 (100, 100)、上端は 20 画素上 → 高さ 40
    cover = layer[..., 3] > 0.25
    ys, xs = np.nonzero(cover)
    assert abs(ys.min() - 80) <= 1 and abs(ys.max() - 119) <= 1 and abs(xs.min() - 90) <= 1 and abs(xs.max() - 109) <= 1
    tilted = np.zeros((200, 200, 4), np.float32)
    space.paste(tilted, levels, (100.0, 100.0), (120.0, 100.0), 1.0, 0.0)        # 上端が右 → 横に寝た絵
    ys, xs = np.nonzero(tilted[..., 3] > 0.25)
    assert xs.max() - xs.min() > ys.max() - ys.min()
    tiny = np.zeros((50, 50, 4), np.float32)
    space.paste(tiny, levels, (10.0, 10.0), (10.0, 9.0), 1.0, 0.0)               # 高さ 2 画素は置かない
    assert not tiny.any()


def _camera():
    """原点から +Y を水平に見るカメラ（焦点距離 100 画素、主点は 200×200 の画面の中心）。踊り手は 5 m 先。"""
    matrix = np.eye(4)
    matrix[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    matrix[:3, 3] = [0.0, 0.0, 1.0]
    return {"matrix": matrix.tolist(), "focal": 100.0, "principal": (100.0, 100.0), "focus": 5.0}


def test_sprites_split_far_and_near_and_reflect_on_the_floor(tmp_path):
    sprites = [space.sprite_levels(_sprite(tmp_path / "a.png", (40, 20)))]
    items = [[0.0, 8.0, 1.0, 1.0, 1.0, 0], [0.5, 3.0, 1.0, 0.5, 1.0, 0], [0.0, 0.1, 1.0, 1.0, 1.0, 0]]   # 遠い・近い・近すぎる
    far, near, mirror = space.sprite_layers((200, 200), _camera(), items, sprites)
    assert far[..., 3].max() > 0.5 and near[..., 3].max() > 0.5
    fy, fx = np.nonzero(far[..., 3] > 0.25)
    ny, nx = np.nonzero(near[..., 3] > 0.25)
    assert abs(fy.mean() - 100) < 2 and nx.mean() > 110                            # 遠い絵は画面の中央、近い絵は右
    my, mx = np.nonzero(mirror[..., 3] > 0.1)
    assert my.min() > 100                                                           # 映り込みは床（地平より下）に
    blurred = space.sprite_layers((200, 200), _camera(), items[:1], sprites, {"focus_blur": 1.0})[0]
    sharp = space.sprite_layers((200, 200), _camera(), items[:1], sprites, {"focus_blur": 0.0})[0]
    assert (blurred[..., 3] > 0.01).sum() > (sharp[..., 3] > 0.01).sum()          # 踊り手から 3 m 奥はぼける
    dim = space.sprite_layers((200, 200), _camera(), items[:1], sprites, {"fade": [1.25, 40.0, 0.45, 0.5]})[0]
    assert dim[..., 0].max() < sharp[..., 0].max()
    empty = space.sprite_layers((200, 200), _camera(), [], sprites)
    assert all(not layer.any() for layer in empty)


def _sprite_spec(tmp_path, items, near_frames=()):
    _passes(tmp_path)
    _sprite(tmp_path / "lamp.png", (40, 20), color=(0, 255, 0))
    camera = np.eye(4)
    camera[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    camera[:3, 3] = [0.0, -5.0, 1.0]
    cameras = {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [W * 10, H * 10],
               "frames": [{"T": T, "matrix": camera.tolist(), "focal_px": 300.0, "principal_px": [W * 5, H * 5], "distance_m": 5.0}
                          for T in (1, 2, 3)]}
    (tmp_path / "cameras.json").write_text(json.dumps(cameras), encoding="utf-8")
    placed = {"schema_version": "dance-sprites/0.1", "unit": "m", "frames": [{"T": T, "items": items} for T in (1, 2)]}
    (tmp_path / "sprites.json").write_text(json.dumps(placed), encoding="utf-8")
    return {"floor_shadow": None, "cameras": "cameras.json",
            "sprites": {"items": "sprites.json", "images": ["lamp.png"], "glow": 0.0, "focus_blur": 0.0, "room_blur": 0.0}}


def test_far_sprites_hide_behind_the_dancer_and_near_ones_cover_it(tmp_path):
    far_dir, near_dir = tmp_path / "far", tmp_path / "near"
    far_dir.mkdir()
    near_dir.mkdir()
    # 人物の四角は画面の中央より少し上（画素 10〜30 行、8〜24 列）。絵を同じ所に、踊り手より奥（8 m）と手前（3 m）に置く
    far = passes.load_spec(_spec(far_dir, **_sprite_spec(far_dir, [[0.0, 3.0, 1.05, 2.0, 1.0, 0]])))
    near = passes.load_spec(_spec(near_dir, **_sprite_spec(near_dir, [[0.0, -2.0, 1.05, 0.8, 1.0, 0]])))
    plan_far, plan_near = passes.load_passes(far_dir / "passes.json"), passes.load_passes(near_dir / "passes.json")
    where_far, where_near = passes.load_space(far, plan_far), passes.load_space(near, plan_near)
    behind = passes.compose(far.data, plan_far, 1, where=where_far).astype(int)
    front = passes.compose(near.data, plan_near, 1, where=where_near).astype(int)
    assert tuple(behind[20, 16]) == (200, 0, 0)                                    # 奥の絵は人物に隠れる
    assert front[20, 16, 1] > 100                                                   # 手前の絵は人物の上に
    assert np.array_equal(passes.compose(far.data, plan_far, 3, where=where_far),
                          passes.compose(passes.load_spec(_spec(far_dir, floor_shadow=None)).data, plan_far, 3))   # 位置の無いコマ


def test_the_sprite_inputs_are_checked(tmp_path):
    parts = _sprite_spec(tmp_path, [[0.0, 3.0, 1.0, 1.0, 1.0, 0]])
    with pytest.raises(PipelineError, match="cameras"):
        passes.load_spec(_spec(tmp_path, **{**parts, "cameras": None}))
    spec = passes.load_spec(_spec(tmp_path, **parts))
    assert spec.data["sprites"]["gain"] == 1.05 and spec.data["sprites"]["fade"] == [1.25, 40.0, 0.45, 1.1]
    plan = passes.load_passes(tmp_path / "passes.json")
    record = json.loads((tmp_path / "cameras.json").read_text(encoding="utf-8"))
    record["frames"] = record["frames"][:2]
    (tmp_path / "cameras.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PipelineError, match="T がありません"):
        passes.load_space(spec, plan)
    record = json.loads((tmp_path / "cameras.json").read_text(encoding="utf-8"))
    record["resolution"] = [W, W]
    (tmp_path / "cameras.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PipelineError, match="縦横比"):
        passes.load_space(spec, plan)
    (tmp_path / "sprites.json").write_text(json.dumps({"schema_version": "dance-sprites/0.1", "unit": "m",
                                                       "frames": [{"T": 1, "items": [[0, 1, 2]]}]}), encoding="utf-8")
    _sprite_spec(tmp_path, [])
    (tmp_path / "sprites.json").write_text(json.dumps({"schema_version": "dance-sprites/0.1", "unit": "m",
                                                       "frames": [{"T": 1, "items": [[0, 1, 2]]}]}), encoding="utf-8")
    with pytest.raises(PipelineError, match="形が違います"):
        passes.load_space(passes.load_spec(_spec(tmp_path, **parts)), plan)
