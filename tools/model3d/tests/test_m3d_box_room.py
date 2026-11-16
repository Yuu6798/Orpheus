"""render.box_room: 絵を基準のカメラから床と奥の壁に投影した箱の部屋の形と uv（Blender なしで動く部分）と、工程の受け渡し
（代役の Blender）。本物の Blender で描く所は CI では確かめない（docs/model3d.md の「箱の部屋」の手元の確認）。"""
import json
from pathlib import Path

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import box_room
from model3d.io import ToolError, atomic_json
from model3d.ops import render_ops

ROOM = {"focal_px": 1000.0, "center_px": [500.0, 400.0], "camera_height": 1.0, "origin_px": [600.0, 900.0], "wall_px": 500.0}


def _project(point, settings, size):
    """Blender の座標の点を、基準のカメラ（仮の部屋の原点。奥 +Y・上 +Z）で絵の画素へ写す。"""
    s = box_room.merged(settings)
    f, (cx, cy), hc, k = s["focal_px"], s["center_px"], s["camera_height"], s["scale"]
    z_d = f * hc / (s["origin_px"][1] - cy)
    x_d = (s["origin_px"][0] - cx) * z_d / f
    x, z, y = point[0] / k + x_d, point[1] / k + z_d, hc - point[2] / k
    return (f * x / z + cx) / size[0], 1.0 - (f * y / z + cy) / size[1]


def test_the_floor_and_wall_project_back_onto_the_picture():
    settings = {**ROOM, "scale": 1.5, "floor": {"rows": [500, 1200, 50], "x": [-4.0, 0.0, 4.0]}, "wall": {"x": [-6.0, 6.0], "top": -3.0}}
    verts, faces, uvs, info = box_room.room_geometry(box_room.merged(settings), (1000, 800))
    assert info["floor_rows"] == 14 and info["floor_columns"] == 3 and len(verts) == 14 * 3 + 4
    assert len(faces) == 13 * 2 + 1 and all(len(face) == 4 for face in faces)
    for v, uv in zip(verts, uvs):                                    # どの頂点も、基準のカメラで写すとその uv の所に来る
        assert _project(v, settings, (1000, 800)) == pytest.approx(uv)
    floor = verts[:-4]
    assert all(v[2] == pytest.approx(0.0) for v in floor)            # 床は Z = 0
    assert info["origin_depth"] == pytest.approx(1000 * 1.0 / 500) and info["wall_depth"] == pytest.approx(1000 / 100)
    assert verts[-4][1] == pytest.approx((10.0 - 2.0) * 1.5)          # 壁は原点から (壁の奥行き − 原点の奥行き) × scale 奥
    assert verts[-2][2] == pytest.approx((1.0 + 3.0) * 1.5)           # 壁の上の端
    assert info["camera_m"] == pytest.approx([-(100 * 2.0 / 1000) * 1.5, -2.0 * 1.5, 1.5])
    origin_uv = _project((0.0, 0.0, 0.0), settings, (1000, 800))
    assert origin_uv == pytest.approx((0.6, 1 - 900 / 800))           # 世界の原点は絵の origin_px


def test_the_defaults_match_the_production_room():
    s = box_room.merged(ROOM)
    assert s["floor"]["x"][:3] == [-70.0, -35.0, -13.0] and s["floor"]["x"][-3:] == [13.0, 35.0, 70.0] and len(s["floor"]["x"]) == 57
    assert s["wall"] == {"x": [-70.0, 70.0], "top": -45.0} and s["shell"]["size"] == [40.0, 44.0, 20.0]
    verts, _, _, info = box_room.room_geometry(s, (1000, 800))
    assert info["floor_rows"] == len(range(500, 1600, 24))           # 既定の床の段は、絵の高さの 2 倍の行まで
    assert box_room.merged({**ROOM, "shell": None})["shell"] is None


def test_bad_rooms_are_stopped_before_blender():
    assert box_room.room_problem(box_room.merged(ROOM), None) is None
    for change, word in (({"origin_px": [600, 300]}, "origin_px"), ({"wall_px": 350}, "wall_px"), ({"wall_px": 950}, "wall_px"),
                         ({"floor": {"rows": [300, 900, 10]}}, "floor.rows")):
        assert word in box_room.room_problem(box_room.merged({**ROOM, **change}), None)
    for params, word in (({}, "focal_px"), ({**ROOM, "focal_px": 0}, "focal_px"), ({**ROOM, "center_px": [1]}, "center_px"),
                         ({**ROOM, "floor": {"rows": [500, 520, 24]}}, "floor.rows"), ({**ROOM, "floor": {"x": [1, 0]}}, "floor.x"),
                         ({**ROOM, "wall": {"x": [2, 1]}}, "wall.x"), ({**ROOM, "wall": {"top": 2}}, "wall.top"),
                         ({**ROOM, "shell": {"size": [1, 0, 1]}}, "shell.size"), ({**ROOM, "wall_px": 950}, "wall_px"),
                         ({**ROOM, "collection": ""}, "collection"), ({**ROOM, "floor": {"step": 1}}, "floor")):
        with pytest.raises(ToolError, match=word):
            render_ops._box_settings(params)


STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
value = lambda flag: args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"room " + open(value("--blend"), "rb").read())
json.dump({"schema_version": "model3d-box-room/0.1", "collection": settings.get("collection", "model3d_room"),
           "objects": ["model3d_room", "model3d_room_shell"], "vertices": 10, "faces": 4, "wall_depth": 10.0,
           "camera_m": [0, -2, 1], "image": open(value("--image"), "rb").read().decode(), "settings": settings},
          open(value("--report"), "w"))
'''


def test_the_operation_adds_the_room_to_a_copy(tmp_path):
    from model3d import executor
    (tmp_path / "dance.blend").write_bytes(b"dance")
    (tmp_path / "room.png").write_bytes(b"picture")
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), **ROOM, "scale": 1.03, "collection": "set"}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "room",
                                           "inputs": {"dance": {"path": "dance.blend"}, "room": {"path": "room.png"}},
                                           "steps": [{"id": "room", "operation": "render.box_room",
                                                      "inputs": {"blend": "$inputs.dance", "image": "$inputs.room"},
                                                      "parameters": params, "outputs": {"blend": "room.blend", "report": "room.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    step = result["steps"]["room"]
    assert result["status"] == "succeeded", step.get("error")
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"room dance"
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["image"] == "picture" and report["settings"]["scale"] == 1.03 and report["settings"]["collection"] == "set"
    obs = step["observations"][0]
    assert obs["kind"] == "box_room" and obs["collection"] == "set" and obs["objects"] == ["model3d_room", "model3d_room_shell"]
