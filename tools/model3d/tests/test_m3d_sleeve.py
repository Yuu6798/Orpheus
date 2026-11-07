"""motion.sleeve_bones: 曲げの求め方・遅れのばね（Blender なしで動く部分）と、台本の引数と工程の受け渡し（代役の Blender）を
確かめる。骨を読んで焼き付ける所は本物の Blender が要るので、CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import sleeve_bones
from model3d.io import ToolError, atomic_json


def random_rotations(count, rng):
    q = rng.normal(size=(count, 4))
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q.T
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
                     np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
                     np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1)], -2)


def rot(axis, angle):
    return sleeve_bones.rotvec_matrix(np.asarray(axis, float) * angle)


def prototype_sleeve(A, B, ratio=0.75, fps=30, sub=8, f_sleeve=4.0):
    """リポジトリ外の試作の袖口の計算（曲げと遅れのばね）をそのまま写したもの。A は曲げなし、B は今の前腕（コマ, 3, 3）"""
    ya, yb = A[:, :3, 1], B[:, :3, 1]
    ax_ = np.cross(ya, yb)
    sn = np.linalg.norm(ax_, axis=1, keepdims=True)
    want = np.einsum("fji,fj->fi", A[:, :3, :3], ax_ / np.maximum(sn, 1e-9) * np.arctan2(sn, np.sum(ya * yb, axis=1, keepdims=True)))
    want = want * ratio
    w_ = 2 * np.pi * f_sleeve
    h_ = 1.0 / (fps * sub)
    th, vel = want[0].copy(), np.zeros(3)
    rv = np.zeros_like(want)
    rv[0] = th
    for f in range(1, len(want)):
        for k_ in range(sub):
            tar = want[f - 1] + (want[f] - want[f - 1]) * (k_ + 1) / sub
            vel = vel + h_ * (w_ * w_ * (tar - th) - 2 * 0.45 * w_ * vel)
            th = th + h_ * vel
        rv[f] = th
    return want, rv


def test_bend_and_lag_match_the_prototype():
    rng = np.random.default_rng(3)
    rigid = random_rotations(40, rng)
    turn = np.cumsum(rng.normal(0, 0.15, (40, 3)), axis=0)        # 曲げが少しずつ変わる
    actual = rigid @ sleeve_bones.rotvec_matrix(turn)
    want = sleeve_bones.bend(rigid, actual) * 0.75
    expected_want, expected_rv = prototype_sleeve(rigid, actual)
    np.testing.assert_allclose(want, expected_want, atol=1e-12)
    np.testing.assert_allclose(sleeve_bones.lagged(want, 30), expected_rv, atol=1e-12)


def test_bend_leaves_out_the_twist_about_the_bone():
    rigid = random_rotations(1, np.random.default_rng(5))
    assert np.abs(sleeve_bones.bend(rigid, rigid @ rot([0, 1, 0], 1.2))).max() < 1e-12   # 骨の軸（Y）のまわりのねじれだけ
    bent = sleeve_bones.bend(rigid, rigid @ rot([1, 0, 0], 0.9))
    np.testing.assert_allclose(bent[0], [0.9, 0, 0], atol=1e-12)                       # 曲げなしの向きの中の回転として


def test_lag_starts_on_the_target_follows_late_and_overshoots_a_step():
    want = np.zeros((40, 3))
    want[5:, 0] = 1.0                                              # 6 コマ目で急に曲がる
    rv = sleeve_bones.lagged(want, 30)
    assert rv[0].tolist() == [0.0, 0.0, 0.0] and rv[5, 0] < 1.0     # 遅れて付いて行く
    assert rv[:, 0].max() > 1.0                                    # 減衰比 0.45 では、止まる所で軽く行きすぎる
    assert abs(rv[-1, 0] - 1.0) < 0.02                             # やがて落ち着く
    np.testing.assert_allclose(sleeve_bones.lagged(np.tile([0.3, 0.1, 0], (10, 1)), 30), np.tile([0.3, 0.1, 0], (10, 1)))


def test_rotvec_matrix_and_angle_between():
    rng = np.random.default_rng(8)
    rv = rng.normal(0, 1, (20, 3))
    r = sleeve_bones.rotvec_matrix(rv)
    np.testing.assert_allclose(r @ np.swapaxes(r, 1, 2), np.broadcast_to(np.eye(3), r.shape), atol=1e-12)
    np.testing.assert_allclose(np.einsum("nij,nj->ni", r, rv), rv, atol=1e-12)          # 軸はそのまま
    np.testing.assert_allclose(sleeve_bones.rotvec_matrix(np.zeros(3)), np.eye(3))
    np.testing.assert_allclose(sleeve_bones.angle_between(np.eye(3), rot([0, 0, 1], 0.3)), math.degrees(0.3))
    tiny = (rot([0, 0, 1], 1e-6)).astype(np.float32).astype(float)                    # 単精度でも 0 の近くで粗くならない
    assert sleeve_bones.angle_between(np.eye(3), tiny) < 1e-3


def test_rotation_part_and_stable():
    r = random_rotations(3, np.random.default_rng(9))
    np.testing.assert_allclose(sleeve_bones.rotation_part(r * 1.7), r, atol=1e-12)
    assert sleeve_bones.stable(30, dict(sleeve_bones.DEFAULTS))
    assert not sleeve_bones.stable(30, {**sleeve_bones.DEFAULTS, "f_hz": 60.0, "substeps": 1})


def test_parse_args():
    args = sleeve_bones.parse_args(["--blend", "i.blend", "--out-blend", "o.blend", "--report", "r.json", "--settings", "s.json"])
    assert args.settings == "s.json"


# 代役の Blender: --version に答え、ふだんは -- の後ろの引数を読んで、決まった出力を書き、受け取った引数と設定を報告に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"sleeved")
settings = json.load(open(value("--settings"), encoding="utf-8"))
json.dump({"schema_version": "model3d-sleeve-bones/0.1", "frames": [1, 3], "fps": 30.0,
           "sleeves": [{"bone": s["bone"], "follow": s["follow"], "bend_max_deg": 109.0, "lag_deg": {"median": 3.7, "max": 34.6}}
                       for s in settings["sleeves"]],
           "bake_check_deg": BAKE_CHECK_DEG, "argv": args, "script": script, "settings": settings}, open(value("--report"), "w"))
'''


def blender_stand_in(directory, bake_check_deg=0.0001234):
    return stand_in_executable(directory, "blender", STAND_IN.replace("BAKE_CHECK_DEG", repr(bake_check_deg)))


SLEEVES = [{"bone": "LeftSleeve", "follow": "LeftForeArm"}, {"bone": "RightSleeve", "follow": "RightForeArm"}]


def test_the_operation_writes_the_settings_and_passes_them_to_blender(tmp_path):
    from model3d import executor
    (tmp_path / "dance.blend").write_bytes(b"sprung")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "sleeve",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "sleeve", "operation": "motion.sleeve_bones",
                                                      "inputs": {"blend": "$inputs.dance"},
                                                      "parameters": {"blender": str(blender_stand_in(tmp_path)), "sleeves": SLEEVES,
                                                                     "ratio": 0.6, "substeps": 10},
                                                      "outputs": {"blend": "sleeved.blend", "report": "sleeve.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["sleeve"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["script"]).name == "sleeve_bones.py"
    assert report["settings"] == {"sleeves": SLEEVES, "ratio": 0.6, "f_hz": 4.0, "damping": 0.45, "substeps": 10}
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"sleeved"
    observation = step["observations"][0]
    assert observation["kind"] == "sleeve_bones" and observation["sleeves"][0]["lag_deg"]["max"] == 34.6
    assert observation["bake_check_deg"] == 0.00012                # 報告は丸めない、観察は丸める
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["sleeve"]["reused_from"] is None


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


def call(tmp_path, parameters, outputs=None):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"sprung")
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}
    return get("motion.sleeve_bones").function({"blend": tmp_path / "dance.blend"}, parameters, declared, Context(tmp_path))


def test_the_operation_stops_when_the_baked_sleeve_misses_its_target(tmp_path):
    with pytest.raises(ToolError, match="合いません"):
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path, bake_check_deg=1.5)), "sleeves": SLEEVES})
    with pytest.raises(ToolError, match="合いません"):          # 0.010004 は 4 桁に丸めると 0.01 で通ってしまう
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path, bake_check_deg=0.010004)), "sleeves": SLEEVES})


@pytest.mark.parametrize("parameters, message", [
    ({"sleeves": SLEEVES, "wobble": 1}, "知らないパラメーター"),
    ({}, "sleeves"),
    ({"sleeves": []}, "sleeves"),
    ({"sleeves": {"bone": "LeftSleeve"}}, "sleeves"),
    ({"sleeves": [{"bone": "LeftSleeve"}]}, "sleeves"),
    ({"sleeves": [{"bone": "LeftSleeve", "follow": "LeftForeArm", "ratio": 1}]}, "sleeves"),
    ({"sleeves": [{"bone": "LeftSleeve", "follow": ""}]}, "sleeves"),
    ({"sleeves": SLEEVES + [SLEEVES[0]]}, "重ならず"),
    ({"sleeves": [{"bone": "LeftForeArm", "follow": "LeftForeArm"}]}, "重ならず"),
    ({"sleeves": SLEEVES, "ratio": 1.5}, "ratio"),
    ({"sleeves": SLEEVES, "ratio": -0.1}, "ratio"),
    ({"sleeves": SLEEVES, "f_hz": 0}, "f_hz"),
    ({"sleeves": SLEEVES, "damping": -1}, "damping"),
    ({"sleeves": SLEEVES, "substeps": 0}, "substeps"),
    ({"sleeves": SLEEVES, "substeps": 1.5}, "substeps"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path)), **parameters})


def test_the_operation_needs_blender_and_both_outputs(tmp_path):
    with pytest.raises(ToolError, match="Blender"):
        call(tmp_path, {"sleeves": SLEEVES})
    with pytest.raises(ToolError, match="report"):
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path)), "sleeves": SLEEVES}, {"blend": "o.blend"})
