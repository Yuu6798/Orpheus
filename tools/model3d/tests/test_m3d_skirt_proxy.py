"""裾を身代わりの筒の布で動かす（cloth.skirt_proxy）: 筒の形の計算（Blender なし）と、工程の受け渡し（代役の Blender）。
本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import skirt_proxy as sp
from model3d.io import ToolError, atomic_json

NAMES = {"skirt": "Skirt", "body": "Body", "hips_bone": "hips", "leg_groups": ["upper_leg", "lower_leg"]}


def test_merged_needs_the_names_and_keeps_the_defaults():
    settings = sp.merged({**NAMES, "rows": 13})
    assert settings["rows"] == 13 and settings["columns"] == 60 and settings["hem_cut_z"] == 0.0
    assert settings["floor"] == "collide" and settings["preroll"] == 40 and settings["hold"] == 14
    with pytest.raises(ValueError, match="leg_groups"):
        sp.merged({"skirt": "Skirt", "body": "Body", "hips_bone": "hips"})


def test_grid_goes_round_and_down():
    angles, levels = sp.grid_levels(1.0, 0.6, 4, 3)
    assert np.allclose(angles, [0, math.pi / 2, math.pi, 3 * math.pi / 2])
    assert np.allclose(levels, [0.994, 0.799, 0.604])


def test_columns_are_filled_between_the_first_and_the_last_hit():
    inner = np.full((5, 3), np.nan)
    inner[[0, 2, 4], 0] = [0.10, 0.14, 0.18]          # 抜けは上下から補間
    inner[[1, 2], 1] = [0.12, 0.13]                   # 範囲の外（0 段・3〜4 段）は張らない
    inner[2, 2] = 0.2                                 # 1 段だけの列は張らない
    radius, valid = sp.fill_columns(inner)
    assert valid[:, 0].all() and np.allclose(radius[:, 0], [0.10, 0.12, 0.14, 0.16, 0.18])
    assert valid[:, 1].tolist() == [False, True, True, False, False]
    assert not valid[:, 2].any()


def test_runs_go_round_the_ring():
    assert sp.runs_of(np.array([True] * 4)) == [(0, 4)]
    assert sp.runs_of(np.array([False] * 4)) == []
    assert sp.runs_of(np.array([True, False, False, True, True])) == [(3, 3)]       # 3・4・0 の輪
    assert sp.runs_of(np.array([False, True, True, False, True])) == [(1, 2), (4, 1)]


def test_small_gaps_are_closed_and_thin_strips_removed():
    radius = np.tile(np.arange(12, dtype=float), (2, 1))
    valid = np.ones((2, 12), dtype=bool)
    valid[0, 3:5] = False                             # 2 格子のすき間 → 埋める（左右から補間）
    radius[0, 3:5] = np.nan
    valid[1, 1:5] = False                             # 4 格子のすき間は残す
    valid[1, 6:10] = False                            # → 5 は 1 格子だけの帯なので消す
    closed, removed = sp.close_and_trim(radius, valid, close_gap=2, min_strip=1)
    assert closed == 2 and removed == 1
    assert valid[0].all() and np.allclose(radius[0, 3:5], [3, 4])
    assert valid[1].tolist() == [True] + [False] * 9 + [True] * 2
    assert sp.close_and_trim(np.zeros((1, 6)), np.zeros((1, 6), dtype=bool), 10, 1) == (0, 0)   # 全部開いた行は埋めない


def test_slit_is_extended_up_to_the_pinned_rows_in_the_middle_of_the_opening():
    columns, rows = 12, 6
    angles, levels = sp.grid_levels(1.0, 0.5, columns, rows)
    valid = np.ones((rows, columns), dtype=bool)
    valid[3:, 8:10] = False                           # 3 段目から下の 8・9 列が開いている（切れ目）
    cells, angle, top, bottom = sp.slit_cut(valid, angles, levels, pin_rows_front=2)
    assert cells == {(1, 8), (2, 8)}                  # 開いた向きの平均（8.5 列）の列。pin_rows_front - 1 段から切れ目の上まで
    assert angle == pytest.approx(8.5 * 2 * math.pi / columns)
    assert top == levels[1] and bottom == levels[3]
    assert sp.slit_cut(np.ones((rows, columns), dtype=bool), angles, levels, 2) is None
    cells, cuts = sp.extra_cuts([95.0], levels, 2, rows, columns, 0.5)
    assert cells == {(j, 3) for j in range(1, 5)} and cuts == [(3.5 * 2 * math.pi / columns, levels[1], 0.48)]


def test_radius_is_smoothed_toward_the_inner_side_and_kept_off_the_legs():
    rows, columns = 4, 10
    radius = np.full((rows, columns), 0.15)
    radius[1, 4] = 0.30                               # ひだの折り返しを拾った飛び
    valid = np.ones((rows, columns), dtype=bool)
    smoothed = sp.smooth_radius(radius, valid, 25)
    assert abs(smoothed[1, 4] - 0.15) < 1e-12 and np.allclose(smoothed, 0.15)
    clearance = sp.clearance_rows(rows, pin_rows=2, clearance_top=0.003, clearance=0.011)
    assert np.allclose(clearance, [0.003, 0.003, 0.007, 0.011])
    legs = np.zeros((rows, columns))
    legs[3, 0] = 0.145                                # 脚が筒より外へ出る所
    valid[0, 9] = False
    final = sp.proxy_radius(smoothed, valid, legs, clearance)
    assert final[3, 0] == pytest.approx(0.156) and final[2, 0] == pytest.approx(0.15) and np.isnan(final[0, 9])


def test_skirt_vertices_too_close_to_the_legs_are_pushed_out_radially():
    rows, columns = 3, 8
    _, levels = sp.grid_levels(1.0, 0.6, columns, rows)
    legs = np.full((rows, columns), 0.10)
    clearance = np.full(rows, 0.01)
    axis = np.array([0.5, -0.2])
    points = np.array([[0.5 + 0.05, -0.2, 0.8], [0.5, -0.2 + 0.2, 0.7], [0.5, -0.2, 0.9]])
    moved, push, need, radius = sp.push_from_legs(points, axis, levels, legs, clearance)
    assert push.tolist() == [True, False, False]      # 軸の上の点（半径 0）は動かさない
    assert np.allclose(moved[0], [0.5 + 0.11, -0.2, 0.8]) and np.allclose(moved[1:], points[1:])
    assert np.allclose(need, 0.11) and np.allclose(radius, [0.05, 0.2, 0.0])


def test_proxy_mesh_skips_cut_cells_and_fills_the_edges_of_the_opening_with_triangles():
    rows, columns = 3, 4
    angles, levels = sp.grid_levels(1.0, 0.6, columns, rows)
    radius = np.full((rows, columns), 0.2)
    valid = np.ones((rows, columns), dtype=bool)
    valid[2, 1] = False
    radius[2, 1] = np.nan
    points, point_rows, point_columns, faces = sp.proxy_mesh(np.zeros(2), angles, radius, valid, levels, {(0, 3)})
    assert len(points) == 11 and point_rows.count(2) == 3 and point_columns[:4] == [0, 1, 2, 3]
    assert np.allclose(points[1], (0.0, 0.2, levels[0]))
    # 0 段: 4 つの格子のうち (0, 3) は切るので 3 つ × 2、1 段: 角の欠けた 2 つの格子は三角 1 つずつ、残り 2 つは 2 つずつ
    assert len(faces) == 3 * 2 + 2 * 1 + 2 * 2
    assert all(len(set(face)) == 3 for face in faces)


def test_pin_weights_fix_the_top_rows_and_fade_down():
    rows = 6
    angles, _ = sp.grid_levels(1.0, 0.6, 4, rows)
    settings = sp.merged({**NAMES, "pin_rows": 2, "pin_rows_front": 1, "front_zone_deg": [260, 280], "goal_below_pin": 0.05,
                          "goal_hem": 0.0, "goal_curve": 2.0})
    point_rows = [0, 1, 2, 5, 1]
    point_columns = [0, 0, 0, 0, 3]                   # 3 列目は 270°（前）
    weights = sp.pin_weights(point_rows, point_columns, angles, rows, settings)
    assert weights[:2] == [1.0, 1.0] and weights[2] == pytest.approx(0.05) and weights[3] == pytest.approx(0.0)
    assert weights[4] == pytest.approx(0.05 * (1 - 0 / 4) ** 2)        # 前は 1 段だけ固定（1 段目からばね）
    assert sp.pin_weights([1], [3], angles, rows, {**settings, "extend_slit": False}) == [1.0]


def test_collider_keeps_only_large_islands_with_leg_vertices():
    # 島 A（0〜5、脚 4 頂点）・島 B（6〜8、脚 3 頂点）・島 C（9〜11、脚なし）
    edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (6, 7), (7, 8), (9, 10), (10, 11)]
    roots = sp.islands(12, edges)
    assert len(set(roots)) == 3 and len({roots[0], roots[5]}) == 1
    groups = ["upper_leg.L"] * 2 + ["lower_leg.R"] * 2 + ["hips"] * 2 + ["upper_leg.R"] * 3 + ["spine"] * 3
    keep = sp.leg_islands(roots, groups, ["upper_leg", "lower_leg"], min_island=4, min_leg=3)
    assert keep == {roots[0]}                         # B は小さく、C は脚が無い
    assert sp.leg_islands(roots, groups, ["upper_leg", "lower_leg"], min_island=3, min_leg=3) == {roots[0], roots[6]}


def test_preroll_starts_from_the_rest_pose():
    assert sp.rest_value('pose.bones["hips"].rotation_quaternion', 0, -1.0) == -1.0
    assert sp.rest_value('pose.bones["hips"].rotation_quaternion', 2, -1.0) == 0.0
    assert sp.rest_value('pose.bones["hips"].scale', 1, 1.0) == 1.0
    assert sp.rest_value('pose.bones["hips"].location', 1, 1.0) == 0.0


# 代役の Blender: 受け取った設定を写して、報告と .blend を書く
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"cloth")
report = {"schema_version": "model3d-skirt-proxy/0.1", "proxy_verts": 690, "proxy_faces": 1244, "collider_islands": 7,
          "hem_cut": {"cut_at_z": settings.get("hem_cut_z")}, "slit_extended": {"angle_deg": 237.0}, "frames": [-53, 720],
          "baked": settings.get("bake", True), "skirt_verts_pushed_out_at_rest": {"count": 3}, "x_settings": settings,
          "x_argv": sys.argv[1:]}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def _context(tmp_path):
    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path
    return Context()


def test_skirt_proxy_passes_the_settings_to_blender(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "dance.blend").write_bytes(b"dance")
    step = {"id": "cloth", "operation": "cloth.skirt_proxy", "inputs": {"blend": "$inputs.dance"},
            "parameters": {"blender": blender, **NAMES, "hem_cut_z": 0.6, "rows": 13, "extra_cuts_deg": [339],
                           "front_zone_deg": [185, 355], "floor": "clamp", "self_collision": False, "end": 330},
            "outputs": {"blend": "cloth.blend", "report": "cloth.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "cloth",
                                           "inputs": {"dance": {"path": "dance.blend"}}, "steps": [step]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    report = json.loads(Path(result["steps"]["cloth"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    settings = report["x_settings"]
    assert {k: settings[k] for k in NAMES} == NAMES and settings["rows"] == 13 and settings["hem_cut_z"] == 0.6
    assert settings["extra_cuts_deg"] == [339] and settings["floor"] == "clamp" and settings["end"] == 330
    assert "columns" not in settings                   # 省いた値は台本の既定
    argv = report["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "skirt_proxy.py" and "--python-expr" not in argv
    observation = result["steps"]["cloth"]["observations"][0]
    assert observation["kind"] == "skirt_proxy" and observation["proxy_verts"] == 690 and observation["baked"] is True
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["cloth"]["reused_from"] is None


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"skirt": ""}, "skirt"),
    ({"skirt": "Body"}, "別のメッシュ"),
    ({"leg_groups": "upper_leg"}, "leg_groups"),
    ({"leg_groups": []}, "leg_groups"),
    ({"rows": 2}, "rows"),
    ({"rows": 4.5}, "rows"),
    ({"rows": 4, "pin_rows": 4}, "pin_rows"),
    ({"columns": 8, "close_gap": 8}, "close_gap"),
    ({"collider_ratio": 0}, "collider_ratio"),
    ({"collider_ratio": 1.5}, "collider_ratio"),
    ({"inner_percentile": 101}, "inner_percentile"),
    ({"mass": -1}, "mass"),
    ({"extend_slit": "yes"}, "extend_slit"),
    ({"floor": "sticky"}, "floor"),
    ({"front_zone_deg": [355, 185]}, "front_zone_deg"),
    ({"extra_cuts_deg": "339"}, "extra_cuts_deg"),
    ({"end": -1}, "end"),
    ({"bake": 1}, "bake"),
])
def test_skirt_proxy_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("cloth.skirt_proxy").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **NAMES, **parameters},
                                          {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, _context(tmp_path))
