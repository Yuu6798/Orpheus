"""頭の候補の目の作りを調べる（check.eyes）: 合成の頭（穴の開いた肌の板・奥の白目の扇・浮いた瞳・まつ毛）で、部品の一覧・扇・
絵の中の場所・絵の大きさ・工程の受け渡しと悪い設定を確かめる（Blender なし）。本物の頭での照合は docs/model3d.md。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import eye_check
from model3d.io import atomic_json

EYES = (-0.035, 0.035)
EYE_Z = 1.30


def _head():
    """頭の代わり: y = 0 の肌の板（目のまわりは穴）と、目ごとに奥の扇（中心 1 頂点 + 輪 16。輪は肌に辺でつながる）・浮いた瞳（円盤）・
    まつ毛（細い帯）。uv は部品ごとに絵の別の所（扇は白、瞳は茶、まつ毛は黒、肌は桃色）。"""
    co, tris, edges, uv_of = [], [], [], []

    def add(point, uv):
        co.append(point)
        uv_of.append(uv)
        return len(co) - 1

    xs, zs = np.arange(-0.09, 0.0901, 0.01), np.arange(1.25, 1.4001, 0.01)
    grid = {}

    def node(i, j):                                                       # 使う格子の点だけを作る
        if (i, j) not in grid:
            x, z = xs[i], zs[j]
            grid[i, j] = add((x, 0.0, z), (0.05 + 0.4 * (x + 0.09) / 0.18, 0.05 + 0.4 * (z - 1.25) / 0.15))
        return grid[i, j]

    for i in range(len(xs) - 1):
        for j in range(len(zs) - 1):
            cx, cz = (xs[i] + xs[i + 1]) / 2, (zs[j] + zs[j + 1]) / 2
            if min(math.hypot(cx - e, cz - EYE_Z) for e in EYES) < 0.018:
                continue                                                  # 目のまわりは穴
            a, b, c, d = node(i, j), node(i + 1, j), node(i + 1, j + 1), node(i, j + 1)
            tris += [(a, b, c), (a, c, d)]
    for ex in EYES:
        centre = add((ex, 0.012, EYE_Z), (0.65, 0.65))                     # 扇の中心（奥）
        ring = [add((ex + 0.015 * math.cos(2 * math.pi * k / 16), 0.0, EYE_Z + 0.015 * math.sin(2 * math.pi * k / 16)),
                    (0.6 + 0.05 * math.cos(2 * math.pi * k / 16) + 0.05, 0.6 + 0.05 * math.sin(2 * math.pi * k / 16) + 0.05)) for k in range(16)]
        tris += [(centre, ring[k], ring[(k + 1) % 16]) for k in range(16)]
        for r in ring:                                                    # 輪を肌の板にいちばん近い頂点でつなぐ（辺だけ）
            p = np.array(co[r])
            nearest = min(grid.values(), key=lambda g: np.linalg.norm(np.array(co[g]) - p))
            edges.append((r, nearest))
        hub = add((ex, -0.004, EYE_Z), (0.85, 0.85))                       # 瞳（円盤。肌より少し前）
        rim = [add((ex + 0.006 * math.cos(2 * math.pi * k / 12), -0.004, EYE_Z + 0.006 * math.sin(2 * math.pi * k / 12)),
                   (0.85 + 0.03 * math.cos(2 * math.pi * k / 12), 0.85 + 0.03 * math.sin(2 * math.pi * k / 12))) for k in range(12)]
        tris += [(hub, rim[k], rim[(k + 1) % 12]) for k in range(12)]
        lash = [add((ex + dx, -0.006, EYE_Z + 0.014 + dz), (0.95 + 0.01 * (dx > 0), 0.95 + 0.01 * (dz > 0)))
                for dx in (-0.012, 0.012) for dz in (0.0, 0.002)]
        tris += [(lash[0], lash[2], lash[3]), (lash[0], lash[3], lash[1])]
    tris = np.array(tris, dtype=np.int64)
    for a, b, c in tris:
        edges += [(a, b), (b, c), (c, a)]
    edges = np.unique(np.sort(np.array(edges, dtype=np.int64), axis=1), axis=0)
    tri_loops = np.arange(len(tris) * 3, dtype=np.int64).reshape(-1, 3)
    uv = np.array(uv_of, dtype=float)[tris.reshape(-1)]
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    image[:] = (230, 180, 170)                                            # 肌
    image[200 - 150:200 - 100, 100:150] = (250, 250, 250)                 # 扇（uv 0.5〜0.75）
    image[0:40, 160:200] = (120, 60, 30)                                  # 瞳（uv 0.8〜1）
    image[0:12, 186:200] = (20, 20, 20)                                   # まつ毛
    return np.array(co, dtype=float), edges, tris, tri_loops, uv, image


CO, EDGES, TRIS, TRI_LOOPS, UV, IMAGE = _head()
SETTINGS = {"resolution": 0.0005, "section_pixel": 0.0002, "top_pixel": 0.001, "margin": 0.005, "front_y": 0.03,
            "window": {"x": [-0.07, 0.07], "z": [1.27, 1.33], "y": 0.02},
            "eyes": {"find": {"x": [0.01, 0.068], "z": [1.28, 1.32], "front_y": 0.02}}}


def test_parts_fans_and_texture_places():
    report, material = eye_check.survey(CO, EDGES, TRIS, SETTINGS, UV, TRI_LOOPS, IMAGE)
    assert report["schema_version"] == "model3d-eye-check/0.1" and report["texture"] == {"size": [200, 200]}
    parts = {row["island"]: row for row in report["parts"]}
    assert report["islands"] == 5 and parts[0]["vertices"] == len(CO) - 2 * 13 - 2 * 4  # 肌（板と扇）・瞳 2・まつ毛 2
    small = [row for row in report["parts"] if row["island"] != 0]
    iris = [row for row in small if row["vertices"] == 13]
    assert len(iris) == 2 and {round(row["centre"][0], 3) for row in iris} == {-0.035, 0.035}
    assert iris[0]["colour"] == [120, 60, 30] and iris[0]["px"][0] == 12 and iris[0]["uv_min"][0] == pytest.approx(0.82, abs=1e-4)
    for side, sign in (("R", -1.0), ("L", 1.0)):
        eye = report["eyes"][side]
        assert eye["found"] and eye["valence"] == 16 and eye["fan_tris"] == 16 and eye["ring"] == 16
        assert eye["centre_co"] == [round(sign * 0.035, 4), 0.012, EYE_Z]
        assert eye["cone_depth_mm"] == pytest.approx(12.0, abs=0.01)                      # 扇の中心は輪より 12 mm 奥
        assert eye["ring_z"] == [round(EYE_Z - 0.015, 4), round(EYE_Z + 0.015, 4)]
        ring_parts = [parts[k] for k in eye["parts_in_ring"]]
        assert any(p["vertices"] == 13 for p in ring_parts)                              # 瞳は輪の中、まつ毛は輪の上の外
        assert eye["fan_uv_min"][0] == pytest.approx(0.6, abs=1e-3)
    assert material["fan_tri"].sum() == 32


def test_eyes_without_a_fan_are_reported():
    settings = {**SETTINGS, "eyes": {"find": {"x": [0.01, 0.068], "z": [1.0, 1.1], "front_y": 0.02}}}
    report, _ = eye_check.survey(CO, EDGES, TRIS, settings)
    assert report["eyes"]["R"] == {"found": False, "reason": "探す箱（eyes.find）に肌の頂点がありません"}
    assert "colour" not in report["parts"][0] and "uv_min" not in report["parts"][1]
    drawn = eye_check.images(eye_check.survey(CO, EDGES, TRIS, settings)[1], settings, report)
    assert "sections" not in drawn and set(drawn) == {"front", "parts", "top"}


def test_images_have_the_window_size():
    report, material = eye_check.survey(CO, EDGES, TRIS, SETTINGS, UV, TRI_LOOPS, IMAGE)
    drawn = eye_check.images(material, SETTINGS, report, IMAGE)
    assert drawn["front"].size == (int(0.14 / 0.0005), int(0.06 / 0.0005))
    front = np.asarray(drawn["front"])
    assert tuple(front[int((1.33 - EYE_Z) / 0.0005), int((0.035 + 0.07) / 0.0005)]) == (120, 60, 30)   # 目の真ん中は瞳の色
    parts = np.asarray(drawn["parts"])
    assert tuple(parts[2, 2]) == tuple(eye_check.palette(5)[0])                    # 肌は灰色
    scale = 1.0 / SETTINGS["top_pixel"]                                            # 上から見た断面は、頭の幅と奥行き + 1 cm ずつ
    width = int(((CO[:, 0].max() + 0.01) - (CO[:, 0].min() - 0.01)) * scale)
    depth = int(((CO[:, 1].max() + 0.01) - (CO[:, 1].min() - 0.01)) * scale)
    assert drawn["sections"].size[0] > 0 and drawn["top"].size == (width, depth)
    with_bones = {**SETTINGS, "bones": [{"name": "eye.L", "head": [0.035, 0.15, EYE_Z], "tail": [0.035, 0.0, EYE_Z]}]}
    plain = np.asarray(drawn["top"])
    boned = np.asarray(eye_check.images(material, with_bones, report, IMAGE)["top"])
    assert (boned != plain).any()                                                   # 目の骨の軸を描く


def _recipe(tmp_path, parameters, outputs=None, texture=True):
    np.savez(tmp_path / "head.npz", co=CO, edges=EDGES, tris=TRIS, tri_loops=TRI_LOOPS, uv=UV)
    Image.fromarray(IMAGE).save(tmp_path / "head.png")
    inputs = {"head": {"path": "head.npz"}, **({"texture": {"path": "head.png"}} if texture else {})}
    step = {"id": "eyes", "operation": "check.eyes", "inputs": {"mesh": "$inputs.head", **({"texture": "$inputs.texture"} if texture else {})},
            "parameters": parameters, "outputs": {"report": "eyes.json", **(outputs or {})}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "eyes", "inputs": inputs, "steps": [step]})
    return tmp_path / "recipe.json"


PARAMETERS = {"front_y": 0.03, "window": {"x": [-0.07, 0.07], "z": [1.27, 1.33], "y": 0.02},
              "eyes": {"find": {"x": [0.01, 0.068], "z": [1.28, 1.32], "front_y": 0.02}}, "resolution": 0.0005, "section_pixel": 0.0002}


def test_check_eyes_step_writes_the_report_and_images(tmp_path):
    from model3d import executor
    outputs = {"front": "front.png", "parts": "parts.png", "sections": "sections.png", "top": "top.png"}
    result = executor.run_recipe(_recipe(tmp_path, PARAMETERS, outputs), tmp_path / "runs")
    assert result["status"] == "succeeded", result                                # 合否は決めない（要確認にしない）
    step = result["steps"]["eyes"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["eyes"]["L"]["fan_tris"] == 16 and report["settings"]["window"]["x"] == [-0.07, 0.07]
    assert step["observations"][0]["eyes"]["R"] == {"fan_tris": 16, "valence": 16, "parts_in_ring": 1}
    for key in outputs:
        assert Image.open(step["artifacts"][key]).size[0] > 0
    again = executor.run_recipe(_recipe(tmp_path, PARAMETERS, outputs), tmp_path / "runs")
    assert again["steps"]["eyes"]["reused_from"] is not None                     # numpy だけなので、同じ入力なら再利用する
    plain = executor.run_recipe(_recipe(tmp_path, PARAMETERS, texture=False), tmp_path / "runs")
    assert plain["status"] == "succeeded"


@pytest.mark.parametrize("change, message", [
    ({"front_y": None}, "front_y"), ({"window": {"x": [0.1, -0.1], "z": [1.4, 1.6], "y": 0}}, "window.x"),
    ({"window": {"x": [-0.1, 0.1], "z": [1.4, 1.6]}}, "window"), ({"eyes": {"find": {"x": [0, 1]}}}, "eyes.find"),
    ({"sections": {"x": "0.03"}}, "sections.x"), ({"top": {"z": "high"}}, "top.z"), ({"bones": [{"head": [0, 0]}]}, "bones"),
    ({"resolution": 0}, "resolution"), ({"colour": 1}, "知らないパラメーター")])
def test_check_eyes_refuses_bad_settings(tmp_path, change, message):
    from model3d import executor
    parameters = {key: value for key, value in {**PARAMETERS, **change}.items() if value is not None}
    result = executor.run_recipe(_recipe(tmp_path, parameters), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["eyes"]["error"]


def test_check_eyes_refuses_images_that_are_not_png(tmp_path):
    from model3d import executor
    result = executor.run_recipe(_recipe(tmp_path, PARAMETERS, {"front": "front.jpg"}), tmp_path / "runs")
    assert result["status"] == "failed" and ".png" in result["steps"]["eyes"]["error"]
