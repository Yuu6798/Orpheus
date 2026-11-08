""".blend どうしの比べ（check.blend_match と model3d.blend_snapshot）: numpy の比べ（同じ・違いの大きさ・形のつくりの違い・
骨・変形した形・tolerance）と、工程の設定・Blender への渡し方（代役の Blender）。中身を書き出す所は本物の Blender が要るので、
CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import blend_snapshot
from model3d.io import ToolError, atomic_json

EYE = np.eye(4).tolist()


def _description(shift=0.0, uv_shift=0.0, attr=1.0, sizes=(3, 3), extra_object=False, pose_shift=0.0, evaluated_shift=0.0,
                 fps_base=1.0):
    """代役の Blender が書き出しに変える、.blend の中身の説明（JSON）。"""
    objects = [{"name": "body", "co": [[0, 0, 0], [1 + shift, 0, 0], [0, 1, 0], [1, 1, 0]], "loops": [0, 1, 2, 1, 3, 2],
                "sizes": list(sizes), "uv": [[0, 0], [1 + uv_shift, 0], [0, 1], [1, 0], [1, 1], [0, 1]],
                "attributes": {"inside": [0.0, attr, 0.0, 1.0]}, "matrix": EYE}]
    if extra_object:
        objects.append({"name": "hat", "co": [[0, 0, 2]], "loops": [], "sizes": [], "uv": None, "attributes": {}, "matrix": EYE})
    return {"scene": {"start": 1, "end": 3, "fps": 30, "fps_base": fps_base}, "objects": objects, "bones": ["hip", "spine"],
            "rest": [EYE, EYE], "pose_shift": pose_shift, "evaluated_shift": evaluated_shift}


STAND_IN = r'''
import hashlib, json, sys
import numpy as np
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
desc = json.load(open(value("--blend"), encoding="utf-8"))
settings = json.load(open(value("--settings"), encoding="utf-8"))
arrays, meta = {}, {"schema_version": "model3d-blend-snapshot/0.1", "blender": "4.5.15", "scene": desc["scene"], "objects": [],
                    "evaluated": {}, "armature": "rig", "bones": desc["bones"]}
for k, ob in enumerate(desc["objects"]):
    arrays[f"o{k}_co"] = np.array(ob["co"], np.float32).reshape(-1, 3)
    arrays[f"o{k}_loops"] = np.array(ob["loops"], np.int32)
    arrays[f"o{k}_sizes"] = np.array(ob["sizes"], np.int32)
    arrays[f"o{k}_matrix"] = np.array(ob["matrix"], np.float64)
    entry = {"name": ob["name"], "key": f"o{k}", "vertices": len(ob["co"]), "polygons": len(ob["sizes"]), "uv": None, "attributes": {}}
    if ob["uv"] is not None:
        arrays[f"o{k}_uv"] = np.array(ob["uv"], np.float32)
        entry["uv"] = "UVMap"
    for i, (name, values) in enumerate(sorted(ob["attributes"].items())):
        if settings["attributes"] == "all" or name in settings["attributes"]:
            arrays[f"o{k}_a{i}"] = np.array(values, np.float32)
            entry["attributes"][name] = {"key": f"o{k}_a{i}", "domain": "POINT", "type": "FLOAT"}
    meta["objects"].append(entry)
pose_frames = list(range(desc["scene"]["start"], desc["scene"]["end"] + 1)) if settings["pose_frames"] == "all" else (
    settings["pose_frames"] if settings["pose_frames"] is not None else settings["frames"])
meta["pose_frames"] = pose_frames
arrays["rest"] = np.array(desc["rest"], np.float64)
poses = np.array([[np.eye(4)] * len(desc["bones"])] * len(pose_frames), np.float64)
poses[:, 0, 0, 3] += desc["pose_shift"]
arrays["poses"] = poses
if settings["evaluate"]:
    for frame in settings["frames"]:
        meta["evaluated"][str(frame)] = {}
        for k, ob in enumerate(desc["objects"]):
            world = np.array(ob["co"], np.float32).reshape(-1, 3) + np.float32(desc["evaluated_shift"])
            arrays[f"e{frame}_o{k}"] = world
            meta["evaluated"][str(frame)][ob["name"]] = {"key": f"e{frame}_o{k}", "vertices": len(world),
                                                         "sha256": hashlib.sha256(world.tobytes()).hexdigest()}
meta["settings"] = settings
with open(value("--out"), "wb") as stream:
    np.savez(stream, **arrays)
json.dump(meta, open(value("--meta"), "w", encoding="utf-8"))
'''


def _snapshot(tmp_path, side, **changes):
    """代役の Blender と同じ書き出しを、この Python で作って読む（numpy の比べだけを確かめる）。"""
    import subprocess
    import sys
    script = tmp_path / "stand_in.py"
    script.write_text(STAND_IN, encoding="utf-8")
    atomic_json(tmp_path / f"{side}.blend", _description(**changes))
    atomic_json(tmp_path / "settings.json", {"frames": [1, 2], "pose_frames": "all", "evaluate": True, "attributes": "all"})
    npz, meta = tmp_path / f"{side}.npz", tmp_path / f"{side}.json"
    subprocess.run([sys.executable, str(script), "--python", "x", "--", "--blend", str(tmp_path / f"{side}.blend"), "--out", str(npz),
                    "--meta", str(meta), "--settings", str(tmp_path / "settings.json")], check=True)
    return blend_snapshot.load(npz, meta)


def test_identical_files_are_exact_and_pass(tmp_path):
    report = blend_snapshot.compare(_snapshot(tmp_path, "a"), _snapshot(tmp_path, "b"))
    assert report["exact"] and report["pass"] and report["bones"] == 2 and len(report["evaluated_meshes"]) == 2
    body = report["objects"][0]
    assert body["vertices_exact"] and body["max_coordinate_error"] == 0.0 and body["attributes"]["inside"]["identical"]
    assert report["rest_matrix_max_abs_error"] == 0.0 and report["pose_matrix_max_abs_error"] == 0.0


def test_differences_are_measured_and_a_tolerance_decides(tmp_path):
    left = _snapshot(tmp_path, "a")
    right = _snapshot(tmp_path, "b", shift=1e-4, pose_shift=2e-6, evaluated_shift=1e-4)
    report = blend_snapshot.compare(left, right)
    body = report["objects"][0]
    assert not report["exact"] and report["pass"] is False and not body["vertices_exact"]
    assert body["max_coordinate_error"] == pytest.approx(1e-4, rel=1e-3) and report["pose_matrix_max_abs_error"] == pytest.approx(2e-6)
    assert not report["evaluated_meshes"][0]["all_evaluated_vertices_exact"]
    assert report["evaluated_meshes"][0]["sampled_max_coordinate_error"] == pytest.approx(2e-4, rel=1e-3)   # 頂点のずれ + 変形したずれ
    loose = blend_snapshot.compare(left, right, {"coordinate": 3e-4, "matrix": 1e-5})
    assert loose["pass"] is True and not loose["exact"]
    tight = blend_snapshot.compare(left, right, {"coordinate": 1e-5, "matrix": 1e-5})
    assert tight["pass"] is False and any("coordinate" in f for f in tight["failures"])


def test_structure_uv_and_attribute_differences_fail(tmp_path):
    left = _snapshot(tmp_path, "a")
    topology = blend_snapshot.compare(left, _snapshot(tmp_path, "b", sizes=(4, 2), extra_object=True), {"coordinate": 1.0, "matrix": 1.0})
    assert topology["additional_objects"] == ["hat"] and not topology["objects"][0]["polygon_sizes_identical"]
    assert topology["pass"] is False and "形のつくり" in topology["failures"][0]
    values = blend_snapshot.compare(left, _snapshot(tmp_path, "c", uv_shift=0.5, attr=0.5), {"coordinate": 1.0, "matrix": 1.0})
    assert values["pass"] is False and not values["objects"][0]["uv_identical"]
    assert values["objects"][0]["attributes"]["inside"]["max_error"] == 0.5
    allowed = blend_snapshot.compare(left, _snapshot(tmp_path, "d", uv_shift=0.5, attr=0.5), {"value": 0.5})
    assert allowed["pass"] is True                                            # value の幅なら uv と属性の違いも許す


def test_an_attribute_that_moved_domain_or_changed_type_is_a_structural_difference(tmp_path):
    left = _snapshot(tmp_path, "a")
    for key, changed in (("domain", "FACE"), ("type", "INT")):
        right = _snapshot(tmp_path, f"b_{key}")
        right[0]["objects"][0]["attributes"]["inside"][key] = changed          # 値と配列の形は同じまま
        for tolerance in (None, {"coordinate": 1.0, "matrix": 1.0, "value": 1.0}):
            report = blend_snapshot.compare(left, right, tolerance)
            inside = report["objects"][0]["attributes"]["inside"]
            assert not inside["identical"] and inside["kind_identical"] is False and not report["exact"]
            assert report["pass"] is False and "形のつくり" in report["failures"][0]   # 値の幅では許さない
    assert inside["type"] == ["FLOAT", "INT"] and inside["domain"] == ["POINT", "POINT"]


def test_named_meshes_that_are_missing_fail_instead_of_being_skipped(tmp_path):
    left, right = _snapshot(tmp_path, "a"), _snapshot(tmp_path, "b")
    report = blend_snapshot.compare(left, right, requested=["body", "ghost"])            # ghost はどちらにも無い（打ち間違いなど）
    assert report["requested_objects_missing"] == {"left": ["ghost"], "right": ["ghost"]}
    assert not report["exact"] and report["pass"] is False and any("名前を挙げたメッシュがありません" in f for f in report["failures"])
    found = blend_snapshot.compare(left, right, requested=["body"])
    assert found["exact"] and found["pass"] and found["requested_objects_missing"] == {"left": [], "right": []}


def test_the_operation_checks_the_named_meshes(tmp_path):
    from model3d import executor
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    atomic_json(tmp_path / "left.blend", _description())
    atomic_json(tmp_path / "right.blend", _description())
    parameters = {"blender": str(blender), "frames": [1, 3], "pose_frames": "all", "evaluate": True, "attributes": ["inside"],
                  "objects": ["ghost"]}
    step = executor.run_recipe(_recipe(tmp_path, parameters), tmp_path / "runs")["steps"]["match"]
    assert step["status"] == "needs_review" and any("名前を挙げたメッシュ" in u for u in step["unresolved"])


def test_a_different_frame_rate_base_makes_the_scenes_differ(tmp_path):
    report = blend_snapshot.compare(_snapshot(tmp_path, "a"), _snapshot(tmp_path, "b", fps_base=1.001))
    assert not report["scene_identical"] and report["scene"]["right"]["fps_base"] == 1.001       # 再生の速さは fps / fps_base
    assert not report["exact"] and report["pass"] is False and "形のつくり" in report["failures"][0]


def test_bad_snapshots_and_tolerances_are_refused(tmp_path):
    atomic_json(tmp_path / "meta.json", {"schema_version": "other"})
    np.savez(tmp_path / "s.npz", x=np.zeros(1))
    with pytest.raises(ToolError, match="形式"):
        blend_snapshot.load(tmp_path / "s.npz", tmp_path / "meta.json")
    for value in ({}, {"position": 1}, {"coordinate": -1}, {"matrix": True}, [1]):
        with pytest.raises(ToolError, match="tolerance"):
            blend_snapshot.check_tolerance(value)
    assert blend_snapshot.check_tolerance(None) is None and blend_snapshot.check_tolerance({"coordinate": 1}) == {"coordinate": 1.0}


def _recipe(tmp_path, parameters):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "match",
                                           "inputs": {"left": {"path": "left.blend"}, "right": {"path": "right.blend"}},
                                           "steps": [{"id": "match", "operation": "check.blend_match",
                                                      "inputs": {"left": "$inputs.left", "right": "$inputs.right"},
                                                      "parameters": parameters, "outputs": {"report": "match.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_writes_both_snapshots_and_compares_them(tmp_path):
    from model3d import executor
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    atomic_json(tmp_path / "left.blend", _description())
    atomic_json(tmp_path / "right.blend", _description())
    parameters = {"blender": str(blender), "frames": [1, 3], "pose_frames": "all", "evaluate": True, "attributes": ["inside"]}
    result = executor.run_recipe(_recipe(tmp_path, parameters), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["match"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["exact"] and report["settings"]["attributes"] == ["inside"] and report["pose_frames"] == [1, 2, 3]
    observation = step["observations"][0]
    assert observation["check"] == "blend_match" and observation["objects"] == 1 and observation["evaluated_meshes"] == 2
    atomic_json(tmp_path / "right.blend", _description(shift=0.01))
    review = executor.run_recipe(_recipe(tmp_path, parameters), tmp_path / "runs2")
    assert review["steps"]["match"]["status"] == "needs_review"
    strict = executor.run_recipe(_recipe(tmp_path, {**parameters, "strict": True}), tmp_path / "runs3")
    assert strict["status"] == "failed"
    loose = executor.run_recipe(_recipe(tmp_path, {**parameters, "tolerance": {"coordinate": 0.02}}), tmp_path / "runs4")
    assert loose["status"] == "succeeded"


class Context:
    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir
        self.tools = {}


@pytest.mark.parametrize("parameters, message", [
    ({"evaluate": True}, "frames"),
    ({"frames": [1.5]}, "frames"),
    ({"pose_frames": "some"}, "pose_frames"),
    ({"evaluate": "yes", "frames": [1]}, "evaluate"),
    ({"attributes": "most"}, "attributes"),
    ({"objects": "body"}, "objects"),
    ({"armature": ""}, "armature"),
    ({"tolerance": {"coordinate": -1}}, "tolerance"),
    ({"wobble": 1}, "知らないパラメーター"),
    ({}, "Blender の実行ファイル"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    (tmp_path / "left.blend").write_text("{}")
    (tmp_path / "right.blend").write_text("{}")
    with pytest.raises(ToolError, match=message):
        get("check.blend_match").function({"left": tmp_path / "left.blend", "right": tmp_path / "right.blend"}, parameters,
                                          {"report": tmp_path / "r.json"}, Context(tmp_path))
