"""効果の形（blender_adapter/fx_shapes.py）: 時刻だけで決まること、形ごとの出し入れ、カメラを向いた筋と近くを描かないこと、炎の輪の
カメラの近くの消し方、はじけの時刻、帯の真上からの弱め、時刻のキーの読み方（Blender なし）。リポジトリ外の試作の台本の関数との
突き合わせ（870 コマで四角形と色がビットまで同じ）は docs/model3d.md。"""
import math

import numpy as np
import pytest

from model3d.blender_adapter import fx_shapes


class Still:
    """動かない点（手は (±0.3, 0, 1.2)、胸は (0, 0, 1.3)）。"""

    def at(self, name, time):
        point = {"hand_l": [0.3, 0.0, 1.2], "hand_r": [-0.3, 0.0, 1.2], "chest": [0.0, 0.0, 1.3]}[name]
        return np.broadcast_to(np.array(point, float), np.shape(time) + (3,)).copy()


class Swing(Still):
    """手が x 方向に 1 秒に 2 m で動く点。"""

    def at(self, name, time):
        base = super().at(name, time)
        if name.startswith("hand"):
            base[..., 0] += 2.0 * np.asarray(time, float)
        return base


def _camera(position, look=(0.0, 0.0, 1.0)):
    """position から look を水平に見るカメラ（右・上・後ろの列と位置）。"""
    forward = np.asarray(look, float) - np.asarray(position, float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    matrix = np.eye(4)
    matrix[:3, 0], matrix[:3, 1], matrix[:3, 2], matrix[:3, 3] = right, up, -forward, position
    return matrix


def _state(tf=3.0, levels=None, camera=None, points=None, rate=1.0, dance_t=None):
    return {"tf": tf, "dance_t": tf if dance_t is None else dance_t, "rate": rate, "camera": _camera([0.0, -6.0, 1.2]) if camera is None else camera,
            "points": points or Still(), "levels": levels or {"embers": 1.0, "trails": 1.0, "sparks": 1.0, "ring": 1.0, "flare": 1.0}}


ALL = {"embers": {}, "bursts": {"times": [[2.5, 1.0]]}, "sparks": {}, "trails": {}, "ring": {}}


def test_shapes_depend_only_on_the_time():
    one, two = fx_shapes.Shapes(ALL), fx_shapes.Shapes(ALL)
    q1, c1 = one.frame(_state(tf=3.0, points=Swing()))
    one.frame(_state(tf=9.0, points=Swing()))                                   # 前のコマを描いても変わらない
    q2, c2 = two.frame(_state(tf=3.0, points=Swing()))
    assert q1.shape[1:] == (4, 3) and c1.shape == q1.shape and len(q1) > 1000
    assert np.array_equal(q1, q2) and np.array_equal(c1, c2)


def test_only_the_named_shapes_are_made_and_levels_switch_them_off():
    embers = fx_shapes.Shapes({"embers": {"count": 50}})
    q, _ = embers.frame(_state())
    assert len(q) == 50
    assert len(embers.frame(_state(levels={"embers": 0.4}))[0]) == 20                   # 強さは数の割合
    assert len(embers.frame(_state(levels={"embers": 0.0}))[0]) == 0
    assert len(fx_shapes.Shapes({}).frame(_state())[0]) == 0
    # 描かない形のぶんも乱数を引くので、火の粉の形は、ほかの形を入れても変わらない
    alone = fx_shapes.Shapes({"embers": {}}).frame(_state())
    mixed = fx_shapes.Shapes(ALL).frame(_state())
    assert np.array_equal(alone[0], mixed[0][:len(alone[0])])


def test_streaks_face_the_camera_and_skip_the_near_ones():
    shapes = fx_shapes.Shapes({"embers": {}})
    camera = _camera([0.0, -6.0, 1.2])
    right, up, at = camera[:3, 0], camera[:3, 1], camera[:3, 3]
    centres = np.array([[0.0, -5.5, 1.2], [0.0, 0.0, 1.0], [1.0, 2.0, 2.0]])           # 1 つ目はカメラから 0.5 m
    velocity = np.array([[0.0, 0.0, 1.0]] * 3)
    quads, colors = shapes.streaks(centres, velocity, np.full(3, 0.01), np.ones((3, 3)), right, up, at, 1.0)
    assert len(quads) == 2                                                                # 0.9 m より近い粒は描かない
    normal = np.cross(right, up)
    assert np.allclose((quads - quads.mean(axis=1, keepdims=True)) @ normal, 0.0)          # どの四角形もカメラの面に平行
    assert np.allclose(colors[:, 0], 0.25) and np.allclose(colors[:, 1], 1.0)              # 筋の後ろは暗い
    stopped, _ = shapes.streaks(centres, velocity, np.full(3, 0.01), np.ones((3, 3)), right, up, at, 0.0)
    length = np.linalg.norm(stopped[:, 1] - stopped[:, 0], axis=1)
    width = np.linalg.norm(stopped[:, 3] - stopped[:, 0], axis=1)
    assert np.allclose(length, width)                                                      # 止まった時間では点（正方形）


def test_ring_flames_fade_near_the_camera():
    shapes = fx_shapes.Shapes({"ring": {"radius": 2.0, "count": 40}})
    inside = _camera([0.0, -2.2, 0.4])                                                      # 輪の手前の舌のすぐそば
    quads, colors = shapes.ring(3.0, 1.0, 1.0, inside[:3, 3])
    flames, glow = quads[:40 * 6], quads[40 * 6:]
    heights = flames.reshape(40, 6, 4, 3)[:, -1, 2, 2] - 0.004
    base = np.stack([2.0 * np.cos(shapes.f_angle), 2.0 * np.sin(shapes.f_angle)], axis=1)
    near = np.linalg.norm(base - inside[:2, 3], axis=1) < 1.6
    assert near.any() and np.allclose(heights[near], 0.0) and (heights[~near] > 0).any()
    assert len(glow) == 2 * 96 and colors.max() > 0


def test_bursts_live_for_their_life_from_each_time():
    shapes = fx_shapes.Shapes({"bursts": {"times": [[2.0, 1.0]], "count": 30, "life": 1.0}})
    camera = _camera([0.0, -6.0, 1.2])
    right, up, at = camera[:3, 0], camera[:3, 1], camera[:3, 3]
    assert len(shapes.bursts(1.99, Still(), right, up, at, 1.0)[0]) == 0
    assert len(shapes.bursts(2.5, Still(), right, up, at, 1.0)[0]) == 30
    assert len(shapes.bursts(3.0, Still(), right, up, at, 1.0)[0]) == 0
    early = shapes.bursts(2.05, Still(), right, up, at, 1.0)[0].mean(axis=(0, 1))
    assert np.allclose(early[:2], 0.0, atol=0.2) and early[2] > 1.3                           # 胸から上へ散る


def test_trails_weaken_when_looking_straight_down():
    shapes = fx_shapes.Shapes({"trails": {}})
    side = shapes.frame(_state(points=Swing(), camera=_camera([0.0, -6.0, 1.2]), levels={"trails": 1.0}))
    top_camera = np.eye(4)
    top_camera[:3, 3] = [0.0, 0.0, 6.0]                                                     # 真下を向くカメラ（後ろの列 +Z）
    top = shapes.frame(_state(points=Swing(), camera=top_camera, levels={"trails": 1.0}))
    assert len(side[0]) == len(top[0]) == 4 * 47
    assert top[1].max() < side[1].max()                                                      # 真上からは帯を 6 割弱める


def test_keys_are_read_like_the_cue_envelope():
    from mv_pipeline.dance import cues
    keys = [[0.0, 0.2], [0.5, 1.0, "smooth"], [1.0, 1.0], [1.0, 0.0], [2.0, 0.5, "step"]]
    for t in (-1.0, 0.0, 0.25, 0.5, 0.75, 0.999, 1.0, 1.5, 2.0, 3.0):
        assert fx_shapes.keys_at(keys, t) == cues.envelope(keys, t)
    assert fx_shapes.keys_at(0.7, 5.0) == 0.7


def test_the_defaults_are_the_prototype_values():
    d = fx_shapes.DEFAULTS
    assert (d["seed"], d["near"], d["embers"]["count"], d["bursts"]["count"], d["sparks"]["count"], d["ring"]["count"]) == (11, 0.9, 900, 320, 170, 460)
    assert d["trails"]["seconds"] == 0.8 and d["trails"]["steep_weaken"] == 0.6 and d["ring"]["near_fade"] == [1.6, 1.6]
    assert math.isclose(fx_shapes.Shapes({"ring": {}}).flame_width[0], 1.0)
    with pytest.raises(KeyError):
        fx_shapes.Shapes({"embers": {}}).s["ring"]
