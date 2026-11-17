"""上に重なる布を下の布に付ける（cloth.follow）と、上の布が下の布の内側に入るコマを数える（check.cloth_over）: 重みの混ぜ方と切り方・要約と合否
（Blender なし）、工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import cloth_follow as cf
from model3d.blender_adapter import cloth_over as co
from model3d.io import ToolError, atomic_json
from model3d.ops.cloth_ops import over_summary


def _mesh(name, kind="MESH"):
    return NS(name=name, type=kind, modifiers=[])


def _modifier(owner, name, kind, show=True, baked=None, **targets):
    modifier = NS(name=name, type=kind, show_viewport=show, id_data=owner, **targets)
    if baked is not None:
        modifier.point_cache = NS(is_baked=baked)
    owner.modifiers.append(modifier)
    return modifier


def test_cached_motion_follows_modifier_targets_and_refuses_unbaked_simulations():
    rig = _mesh("Rig", "ARMATURE")
    proxy, skirt, panels = _mesh("skirt_proxy"), _mesh("Skirt"), _mesh("Panels")
    _modifier(proxy, "follow_hips", "ARMATURE", object=rig)
    cloth = _modifier(proxy, "skirt_cloth", "CLOTH", baked=True)
    _modifier(skirt, "follow_proxy", "SURFACE_DEFORM", target=proxy)      # 裾は筒に貼り付く（筒の焼いた布の計算も外す）
    _modifier(panels, "baked_motion", "MESH_CACHE")
    _modifier(panels, "off", "MESH_CACHE", show=False)                    # 表示で止めてある物は触らない
    found = co.cached_motion([panels, skirt])
    assert [(m.id_data.name, m.name) for m in found] == [("Panels", "baked_motion"), ("skirt_proxy", "skirt_cloth")]
    _modifier(proxy, "back", "SURFACE_DEFORM", target=skirt)              # 互いに指し合っても 1 回ずつ
    assert [m.name for m in co.cached_motion([skirt])] == ["skirt_cloth"]
    cloth.point_cache.is_baked = False
    with pytest.raises(SystemExit, match="焼かれていません"):
        co.cached_motion([skirt])


def test_nearer_leader_vertices_weigh_more():
    weights, cut, share = cf.blend_weights([(0.01, [("skirt_01", 1.0)]), (0.02, [("skirt_02", 1.0)])], 4)
    assert not cut and share == 0.0
    assert weights["skirt_01"] == pytest.approx((1 / (1e-4 + 1e-6)) / (1 / (1e-4 + 1e-6) + 1 / (4e-4 + 1e-6)))
    assert sum(weights.values()) == pytest.approx(1.0) and list(weights) == ["skirt_01", "skirt_02"]   # 大きい順


def test_too_many_bones_are_cut_and_the_rest_rescaled():
    neighbours = [(0.01, [("a", 0.4), ("b", 0.3), ("c", 0.2), ("d", 0.06), ("e", 0.04)])]
    weights, cut, share = cf.blend_weights(neighbours, 4)
    assert cut and list(weights) == ["a", "b", "c", "d"] and share == pytest.approx(0.04)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert cf.blend_weights([(0.01, [("a", 0.0)])], 4) == (None, False, 0.0)          # 重みが無ければ置きかえない


def test_over_summary_counts_frames_runs_and_means():
    raw = {"over": "Panels", "under": "Skirt", "over_verts": 700, "ignored_at_rest": 16,
           "rows": [{"frame": 1, "verts": 0, "max_mm": 0.0}, {"frame": 2, "verts": 5, "max_mm": 4.0}, {"frame": 3, "verts": 40, "max_mm": 12.0},
                    {"frame": 4, "verts": 2, "max_mm": 1.0}, {"frame": 5, "verts": 3, "max_mm": 2.0}]}
    report = over_summary(raw)
    assert report["frames_inside"] == 3 and report["mean_verts_per_frame"] == 10.0 and report["max_verts"] == 40
    assert report["frames_inside_10_verts_or_more"] == 1 and report["frames_inside_30_verts_or_more"] == 1
    assert [run["frames"] for run in report["runs"]] == [[2, 3], [5, 5]] and report["pass"] is None
    judged = over_summary(raw, max_mean_verts=5)
    assert judged["pass"] is False and "平均" in judged["failures"][0]
    assert over_summary(raw, max_frames=3, max_mean_verts=20)["pass"] is True
    assert report["rest_caches_off"] == [] and report["modifiers_synced"] == []


def test_over_summary_judges_the_mean_before_rounding():
    # 100 コマに 1 頂点だけ入る: 平均 0.01 は表示では 0.0 だが、しきい値 0 は超えている
    raw = {"rows": [{"frame": f, "verts": 1 if f == 50 else 0, "max_mm": 2.0 if f == 50 else 0.0} for f in range(1, 101)],
           "rest_caches_off": ["Skirt:baked_motion"]}
    judged = over_summary(raw, max_mean_verts=0)
    assert judged["mean_verts_per_frame"] == 0.0 and judged["pass"] is False and "0.01" in judged["failures"][0]
    assert judged["rest_caches_off"] == ["Skirt:baked_motion"]
    assert over_summary(raw, max_mean_verts=0.01)["pass"] is True


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
script = sys.argv[sys.argv.index("--python") + 1]
if script.endswith("cloth_follow.py"):
    shutil.copyfile(value("--blend"), value("--out-blend"))
    report = {"schema_version": "model3d-cloth-follow/0.1", "follower": settings["follower"], "leader": settings["leader"], "reweighted": 7,
              "verts_cut_to_max_influences": 2, "settings": settings}
else:
    report = {"schema_version": "model3d-cloth-over/0.1", "over": settings["over"], "under": settings["under"], "over_verts": 10,
              "ignored_at_rest": 1, "rows": [{"frame": f, "verts": v, "max_mm": 3.0} for f, v in ((1, 0), (2, 4), (3, 0))], "x_settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def test_follow_and_over_steps(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "model.blend").write_bytes(b"model")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "follow", "inputs": {"model": {"path": "model.blend"}},
                                           "steps": [{"id": "follow", "operation": "cloth.follow", "inputs": {"blend": "$inputs.model"},
                                                      "parameters": {"blender": blender, "follower": "Panels", "leader": "Skirt", "axis_bone": "hips"},
                                                      "outputs": {"blend": "follow.blend", "report": "follow.json"}},
                                                     {"id": "over", "operation": "check.cloth_over", "inputs": {"blend": "$steps.follow.blend"},
                                                      "parameters": {"blender": blender, "over": "Panels", "under": "Skirt", "axis_bone": "hips",
                                                                     "max_frames": 0},
                                                      "outputs": {"report": "over.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    follow = json.loads(Path(result["steps"]["follow"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert follow["settings"] == {"follower": "Panels", "leader": "Skirt", "axis_bone": "hips", "k": 6, "max_influences": 4, "layer_gap": 0.015}
    over = json.loads(Path(result["steps"]["over"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert result["steps"]["over"]["status"] == "needs_review" and over["pass"] is False and over["frames_inside"] == 1


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("operation, parameters, message", [
    ("cloth.follow", {"follower": "Panels", "leader": "Panels", "axis_bone": "hips"}, "別のメッシュ"),
    ("cloth.follow", {"follower": "Panels", "leader": "Skirt"}, "axis_bone"),
    ("cloth.follow", {"follower": "Panels", "leader": "Skirt", "axis_bone": "hips", "k": 0}, "k"),
    ("cloth.follow", {"follower": "Panels", "leader": "Skirt", "axis_bone": "hips", "max_influences": 2.5}, "max_influences"),
    ("cloth.follow", {"follower": "Panels", "leader": "Skirt", "axis_bone": "hips", "bones": []}, "知らない"),
    ("check.cloth_over", {"over": "Panels", "under": "Skirt", "axis_bone": "hips", "depth": 0}, "depth"),
    ("check.cloth_over", {"over": "Panels", "under": "Skirt", "axis_bone": "hips", "frames": [3, 1]}, "frames"),
    ("check.cloth_over", {"over": "Panels", "under": "Skirt", "axis_bone": "hips", "max_frames": -1}, "max_frames"),
])
def test_follow_and_over_reject_bad_settings(tmp_path, operation, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    outputs = {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"} if operation == "cloth.follow" else {"report": tmp_path / "r.json"}
    with pytest.raises(ToolError, match=message):
        get(operation).function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters}, outputs, Context(tmp_path))
