"""袖口の押し出し（collision.cuff_pushout）と押し出しの動きの検査（check.pushout_moves）: 筒の輪の向きと太さ・いちばん重い骨・
動いた量の数え方と合否（Blender なし）、工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import cuff_pushout as cp
from model3d.blender_adapter import pushout_moves as pm
from model3d.io import ToolError, atomic_json
from model3d.ops.collision_ops import moves_summary


def test_ring_frame_is_square_and_follows_the_hint():
    axis, side, up = cp.frame(np.array([0.3, -0.1, 0.0]))
    for a, b in ((axis, side), (axis, up), (side, up)):
        assert float(a @ b) == pytest.approx(0.0, abs=1e-9)
    assert np.linalg.norm(side) == pytest.approx(1.0) and np.linalg.norm(up) == pytest.approx(1.0)
    vertical = cp.frame(np.array([0.0, 0.0, 2.0]))[1]                       # 縦の軸でも横の向きが決まる
    assert np.linalg.norm(vertical) == pytest.approx(1.0)
    new_axis, kept, _ = cp.frame(np.array([1.0, 0.1, 0.0]), side)          # 前の輪の横の向きを、新しい軸に垂直に直して引き継ぐ（ねじれない）
    projected = side - new_axis * float(side @ new_axis)
    assert kept == pytest.approx(projected / np.linalg.norm(projected)) and float(kept @ side) > 0


def test_sleeve_rings_use_the_core_and_the_wrist_rings_use_the_mesh():
    t = np.concatenate([np.full(20, 0.7), np.full(20, 0.9), np.full(3, 1.0)])
    radial = np.concatenate([np.linspace(0.02, 0.03, 20), np.linspace(0.018, 0.022, 20), [0.05, 0.05, 0.05]])
    radii = cp.ring_radii(t, radial, core=0.04, sleeve_until=0.6)
    assert radii[:3] == [0.04, 0.04, 0.04]                                  # 肘から 0.6 までは芯の太さ
    assert radii[3] == pytest.approx(float(np.percentile(radial[:20], 90)))  # 0.6 の輪: ±0.12 の帯で 0.6 より先の頂点
    near_wrist = radial[(t >= 1.0 - 0.12) & (t < 1.12)]
    assert radii[5] == pytest.approx(float(np.percentile(near_wrist, 90)))
    assert cp.ring_radii(np.array([0.1]), np.array([0.01]), 0.03, 0.6)[3:] == [0.03, 0.03, 0.03]   # 頂点が足りなければ肘の側の隣の輪と同じ


def test_heaviest_bone_counts_aliases_with_their_bone():
    aliases = {"lower_arm_twist.L": "lower_arm.L"}
    assert cp.heaviest([("hand.L", 0.5), ("lower_arm.L", 0.3), ("lower_arm_twist.L", 0.2)], aliases) == "hand.L"   # 同じ重みは先
    assert cp.heaviest([("hand.L", 0.45), ("lower_arm.L", 0.3), ("lower_arm_twist.L", 0.25)], aliases) == "lower_arm.L"
    assert cp.heaviest([], aliases) == ""


def test_moves_are_counted_per_frame_against_the_limit():
    record = {"frames_moved": 0, "frames_over_limit": [], "max_move_mm": 0.0, "max_move_frame": None, "max_moved_verts": 0}
    pm.tally(record, 5, np.array([0.0, 0.0004, 0.002, 0.05]), 0.045)
    pm.tally(record, 6, np.zeros(4), 0.045)
    pm.tally(record, 7, np.array([0.01, 0.02, 0.0, 0.0]), 0.045)
    assert record["frames_moved"] == 2 and record["max_moved_verts"] == 2      # 0.5 mm 以下は動いていない
    assert record["frames_over_limit"] == [[5, 1, 50.0]] and record["max_move_mm"] == 50.0 and record["max_move_frame"] == 5
    raw = {"frames": [5, 6, 7], "checks": {"Body:cuff_": {**record, "modifiers": ["cuff_out_L"], "limit_mm": 45.0}}}
    report = moves_summary(raw)
    assert report["pass"] is False and "Body:cuff_" in report["failures"][0] and report["checks"]["Body:cuff_"]["frames_over_limit"] == 1
    raw["checks"]["Body:cuff_"]["frames_over_limit"] = []
    assert moves_summary(raw)["pass"] is True


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
if script.endswith("cuff_pushout.py"):
    shutil.copyfile(value("--blend"), value("--out-blend"))
    arms = {a["name"]: {"proxy": "ArmProxy." + a["name"], "sleeve_verts": 9, "far_points_judged_inside": 2 if a["name"] == "R" else 0}
            for a in settings["arms"]}
    report = {"schema_version": "model3d-cuff-pushout/0.1", "armature": "Rig", "arms": arms, "settings": settings}
else:
    record = {"modifiers": ["m"], "limit_mm": 45.0, "frames_moved": 1, "frames_over_limit": [], "max_move_mm": 3.2, "max_move_frame": 2,
              "max_moved_verts": 4}
    report = {"schema_version": "model3d-pushout-moves/0.1", "frames": [1, 2, 3],
              "checks": {c["mesh"] + ":" + c["prefix"]: dict(record) for c in settings["checks"]}, "x_settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''

ARMS = [{"name": "L", "forearm": "lower_arm.L", "hand": "hand.L", "palm_end": "middle_proximal.L"},
        {"name": "R", "forearm": "lower_arm.R", "hand": "hand.R", "palm_end": "middle_proximal.R"}]
SIZES = {"core_radius": 0.035, "sleeve_until": 0.5, "palm_radius": 0.018, "gate_near": 0.04, "gate_far": 0.055}   # 体と衣装の大きさ（合成の値。工程では必須）


def run(tmp_path, steps):
    from model3d import executor
    (tmp_path / "dance.blend").write_bytes(b"dance")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "cuff", "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": steps})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_cuff_and_moves_steps(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = run(tmp_path, [{"id": "cuff", "operation": "collision.cuff_pushout", "inputs": {"blend": "$inputs.dance"},
                             "parameters": {"blender": blender, "body": "Body", "arms": ARMS, **SIZES},
                             "outputs": {"blend": "cuff.blend", "report": "cuff.json"}},
                            {"id": "moves", "operation": "check.pushout_moves", "inputs": {"blend": "$steps.cuff.blend"},
                             "parameters": {"blender": blender, "checks": [{"mesh": "Body", "prefix": "cuff_", "limit_mm": 45}], "space": "world"},
                             "outputs": {"report": "moves.json"}}])
    cuff = result["steps"]["cuff"]
    assert cuff["status"] == "needs_review" and any("R" in u and "内側" in u for u in cuff["unresolved"])   # 筒の判定の外れは要確認
    sent = json.loads(Path(cuff["artifacts"]["report"]).read_text(encoding="utf-8"))["settings"]
    assert {key: sent[key] for key in SIZES} == SIZES and sent["arms"][0]["palm_end"] == "middle_proximal.L" and "offset" not in sent   # 省いた数は台本の既定
    moves = json.loads(Path(result["steps"]["moves"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert result["steps"]["moves"]["status"] == "succeeded" and moves["pass"] is True and moves["checks"]["Body:cuff_"]["max_move_mm"] == 3.2
    assert json.loads(Path(moves["raw"]).read_text(encoding="utf-8"))["x_settings"]["space"] == "world"


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({"arms": ARMS, **SIZES}, "body"),
    ({"body": "Body", "arms": ARMS[:1]}, "2 本"),
    ({"body": "Body", "arms": [ARMS[0], {**ARMS[1], "name": "L"}]}, "別の name"),
    ({"body": "Body", "arms": [ARMS[0], {k: v for k, v in ARMS[1].items() if k != "palm_end"}]}, "palm_end"),
    ({"body": "Body", "arms": ARMS, **SIZES, "gate_near": 0.06, "gate_far": 0.05}, "gate_far"),
    ({"body": "Body", "arms": ARMS, **SIZES, "segments": 2}, "segments"),
    ({"body": "Body", "arms": ARMS, **SIZES, "proxy": "ArmProxy"}, "{arm}"),
    ({"body": "Body", "arms": ARMS}, "core_radius"),                     # 体と衣装の大きさは既定が無い
    ({"body": "Body", "arms": ARMS, **{k: v for k, v in SIZES.items() if k != "gate_far"}}, "gate_far"),
    ({"body": "Body", "arms": ARMS, "mode": "mesh"}, "知らない"),
])
def test_cuff_pushout_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("collision.cuff_pushout").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                               {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({}, "checks"),
    ({"checks": [{"mesh": "Body", "prefix": "cuff_"}]}, "limit_mm"),
    ({"checks": [{"mesh": "Body", "prefix": "cuff_", "limit_mm": 0}]}, "limit_mm"),
    ({"checks": [{"mesh": "Body", "prefix": "cuff_", "limit_mm": 45}], "space": "object"}, "space"),
    ({"checks": [{"mesh": "Body", "prefix": "cuff_", "limit_mm": 45}] * 2}, "重なって"),
    ({"checks": [{"mesh": "Body", "prefix": "cuff_out", "limit_mm": 45}, {"mesh": "Body", "prefix": "cuff_near", "limit_mm": 45}]},
     "1 つずつ"),                                                        # 同じメッシュを 2 回挙げると、外したときの動きが混ざる
    ({"checks": [{"mesh": "Body", "prefix": "cuff_", "limit_mm": 45}], "step": 0}, "step"),
])
def test_pushout_moves_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("check.pushout_moves").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                            {"report": tmp_path / "r.json"}, Context(tmp_path))
