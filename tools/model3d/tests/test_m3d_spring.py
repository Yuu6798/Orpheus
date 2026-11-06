"""motion.spring_bones: ばねの計算と骨の向きの組み立て（Blender なしで動く部分）、台本の引数と工程の受け渡し
（代役の Blender）を確かめる。鎖をたどる所と骨に焼き付ける所は本物の Blender が要るので、CI では確かめない
（docs/model3d.md の手元の確認）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import spring_bones
from model3d.io import ToolError, atomic_json


def prototype_simulate(nodes, Ttar, cols, links, fps=30, sub=8, f_root=3.2, f_tip=1.1, damp=0.30, bend=0.12, push=0.3):
    """リポジトリ外の試作の simulate を、狙いの位置を受け取る形にして、そのまま写したもの"""
    F = len(Ttar)
    dt = 1.0 / (fps * sub)
    C, N = nodes.shape[:2]
    L = np.linalg.norm(np.diff(nodes, axis=1), axis=2)

    def rest_limit(nodes, col):
        ab = col["qb"] - col["qa"]
        t = np.clip(((nodes - col["qa"]) @ ab) / (ab @ ab), 0, 1)
        return np.linalg.norm(nodes - (col["qa"] + t[..., None] * ab), axis=-1)

    rest_cap = [rest_limit(nodes, col) for col in cols]
    s = np.linspace(0, 1, N)[None, :]
    w0 = 2 * np.pi * f_root * (f_tip / f_root) ** s
    kk = (w0 ** 2)[..., None]
    cc = (2 * damp * w0)[..., None]

    def step(x, v, tar, tar_v):
        acc = kk * (tar - x) + cc * (tar_v - v)
        v = v + dt * acc
        x_new = x + dt * v
        x_new[:, 0] = tar[:, 0]
        off = x_new - tar
        x_new[:, 1:-1] += bend * (0.5 * (off[:, :-2] + off[:, 2:]) - off[:, 1:-1])
        x_new[:, -1] += bend * (off[:, -2] - off[:, -1])
        for _ in range(2):
            for i in range(N - 1):
                d = x_new[:, i + 1] - x_new[:, i]
                ln = np.linalg.norm(d, axis=1, keepdims=True)
                x_new[:, i + 1] = x_new[:, i] + d * (L[:, i:i + 1] / np.maximum(ln, 1e-6))
        for c0, c1 in links:
            d0 = np.linalg.norm(nodes[c1] - nodes[c0], axis=1)
            d = x_new[c1] - x_new[c0]
            ln = np.linalg.norm(d, axis=1)
            over = np.maximum(ln - d0 * 1.25, 0) / np.maximum(ln, 1e-6)
            x_new[c0, 1:] += (0.5 * over[:, None] * d)[1:]
            x_new[c1, 1:] -= (0.5 * over[:, None] * d)[1:]
        return x_new, (x_new - x) / dt

    def collide(x, f):
        hits = 0
        for col, dcap in zip(cols, rest_cap):
            a0, ab = col["pa"][f], col["pb"][f] - col["pa"][f]
            t = np.clip(((x - a0) @ ab) / (ab @ ab), 0, 1)
            q = a0 + t[..., None] * ab
            rc = 0.98 * np.minimum(col["ra"] + t * (col["rb"] - col["ra"]), dcap)
            d = x - q
            ln = np.linalg.norm(d, axis=2)
            m = ln < rc
            m[:, 0] = False
            if m.any():
                x[m] += push * (q[m] + d[m] / np.maximum(ln[m, None], 1e-6) * rc[m, None] - x[m])
                hits += int(m.sum())
        return x, hits

    x = Ttar[0].copy()
    v = np.zeros_like(x)
    for _ in range(fps * sub):
        x, v = step(x, v, Ttar[0], np.zeros_like(x))
    SIM = np.zeros((F, C, N, 3))
    SIM[0] = x
    total_hits = 0
    for f in range(1, F):
        tv = (Ttar[f] - Ttar[f - 1]) * fps
        for k_ in range(sub):
            tar = Ttar[f - 1] + (Ttar[f] - Ttar[f - 1]) * (k_ + 1) / sub
            x, v = step(x, v, tar, tv)
            x, h = collide(x, f)
            total_hits += h
        SIM[f] = x
    return SIM, total_hits


def hanging_chains(chains=3, count=6, frames=40, swing=0.15, seed=0):
    """頭の高さから下へ垂れた鎖と、左右に揺れる狙いの位置（揺れの幅 swing m）。"""
    rng = np.random.default_rng(seed)
    nodes = np.zeros((chains, count, 3))
    for c in range(chains):
        nodes[c, :, 0] = 0.05 * c + rng.normal(0, 0.002, count)
        nodes[c, :, 2] = 1.5 - 0.08 * np.arange(count)
    shift = swing * np.sin(np.linspace(0, 3 * np.pi, frames))
    targets = np.repeat(nodes[None], frames, axis=0)
    targets[..., 0] += shift[:, None, None]
    return nodes, targets


def capsule(frames, a, b, radius):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return {"pa": np.tile(a, (frames, 1)), "pb": np.tile(b, (frames, 1)), "ra": radius, "rb": radius, "qa": a, "qb": b}


def test_simulate_matches_the_prototype():
    nodes, targets = hanging_chains()
    cols = [capsule(len(targets), [0.12, 0.0, 1.0], [0.12, 0.0, 1.4], 0.05)]
    links = ((0, 1), (1, 2))
    sim, hits = spring_bones.simulate(nodes, targets, cols, links)
    expected, expected_hits = prototype_simulate(nodes, targets, cols, links)
    np.testing.assert_array_equal(sim, expected)
    assert hits == expected_hits and hits > 0


def test_the_root_stays_on_its_target_and_the_lengths_are_kept():
    nodes, targets = hanging_chains()
    sim, _ = spring_bones.simulate(nodes, targets)
    np.testing.assert_allclose(sim[:, :, 0], targets[:, :, 0], atol=1e-12)   # コマの終わりの狙いは a + (b − a) で作るので、誤差の分だけ
    np.testing.assert_allclose(np.linalg.norm(np.diff(sim, axis=2), axis=3),
                               np.broadcast_to(np.linalg.norm(np.diff(nodes, axis=1), axis=2), sim.shape[:3][:1] + (3, 5)),
                               atol=1e-12)


def test_a_still_target_is_left_alone_and_a_moving_one_drags_the_tip_behind():
    nodes, targets = hanging_chains(swing=0.0)
    still, _ = spring_bones.simulate(nodes, targets)
    assert np.abs(still - targets).max() < 1e-9
    nodes, targets = hanging_chains(swing=0.15)
    moving, _ = spring_bones.simulate(nodes, targets)
    offset = np.linalg.norm(moving - targets, axis=3).mean(axis=(0, 1))
    assert offset[0] < 1e-12 and offset[-1] > offset[1] > 0      # 根元は狙いのまま、毛先ほど遅れる


def test_a_collider_keeps_the_chain_out_of_it():
    nodes, targets = hanging_chains(chains=1, swing=0.15)
    axis = ([0.1, 0.0, 1.0], [0.1, 0.0, 1.4])
    free, _ = spring_bones.simulate(nodes, targets)
    held, hits = spring_bones.simulate(nodes, targets, [capsule(len(targets), *axis, 0.04)])

    def closest(sim):
        return np.abs(sim[:, 0, 1:, 0] - 0.1).min()               # カプセルの軸（x=0.1）までの近さ
    assert hits > 0 and closest(held) > closest(free)


def test_a_collider_on_one_bone_is_a_sphere_and_still_pushes():
    nodes, targets = hanging_chains(chains=1, swing=0.15)
    centre = [0.1, 0.0, 1.2]
    free, _ = spring_bones.simulate(nodes, targets)
    held, hits = spring_bones.simulate(nodes, targets, [capsule(len(targets), centre, centre, 0.05)])   # from と to が同じ
    assert np.isfinite(held).all() and hits > 0

    def closest(sim):
        return np.linalg.norm(sim[:, 0, 1:] - centre, axis=-1).min()
    assert closest(held) > closest(free)
    assert spring_bones.along_axis(np.zeros((4, 3)), np.ones(3), np.zeros(3)).tolist() == [0.0] * 4
    assert np.isfinite(spring_bones.rest_limit(nodes, capsule(1, centre, centre, 0.05))).all()


def test_links_keep_neighbouring_chains_together():
    nodes, targets = hanging_chains(chains=2, swing=0.0)
    targets = targets.copy()
    targets[:, 1, :, 0] += np.linspace(0, 0.3, len(targets))[:, None]   # 2 本目だけが離れていく
    apart, _ = spring_bones.simulate(nodes, targets)
    linked, _ = spring_bones.simulate(nodes, targets, links=((0, 1),))
    gap = lambda sim: np.linalg.norm(sim[-1, 1, 1:] - sim[-1, 0, 1:], axis=1).max()
    assert gap(linked) < gap(apart)


def test_minimal_rotation_turns_one_direction_onto_the_other():
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=(50, 3)), rng.normal(size=(50, 3))
    r = spring_bones.minimal_rotation(a, b)
    np.testing.assert_allclose(np.einsum("nij,nj->ni", r, a / np.linalg.norm(a, axis=1, keepdims=True)),
                               b / np.linalg.norm(b, axis=1, keepdims=True), atol=1e-12)
    np.testing.assert_allclose(r @ np.swapaxes(r, 1, 2), np.broadcast_to(np.eye(3), r.shape), atol=1e-12)
    np.testing.assert_allclose(spring_bones.minimal_rotation(a[:1], a[:1])[0], np.eye(3), atol=1e-12)


def random_rotations(count, rng):
    q = rng.normal(size=(count, 4))
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q.T
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
                     np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
                     np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1)], -2)


def test_chain_basis_rebuilds_the_bone_directions_through_the_chain():
    rng = np.random.default_rng(4)
    frames, bones = 7, 4
    parent = random_rotations(frames, rng)
    relation = random_rotations(bones, rng)
    target = random_rotations(frames * bones, rng).reshape(frames, bones, 3, 3)
    directions = rng.normal(size=(frames, bones, 3))
    basis = spring_bones.chain_basis(parent, relation, target, directions)
    above = parent
    for j in range(bones):                                         # 親から順に組むと、骨の Y 軸が毛の向きになる
        pose = above @ relation[j] @ basis[:, j]
        np.testing.assert_allclose(pose[:, :, 1], directions[:, j] / np.linalg.norm(directions[:, j], axis=1, keepdims=True),
                                   atol=1e-12)
        np.testing.assert_allclose(pose, spring_bones.minimal_rotation(target[:, j, :, 1], directions[:, j]) @ target[:, j],
                                   atol=1e-12)
        above = pose


def test_rotation_part_drops_scale_and_mirroring():
    rng = np.random.default_rng(6)
    r = random_rotations(5, rng)
    np.testing.assert_allclose(spring_bones.rotation_part(r * 2.5), r, atol=1e-12)
    mirrored = spring_bones.rotation_part(r @ np.diag([1.0, 1.0, -1.0]))
    np.testing.assert_allclose(np.linalg.det(mirrored), 1.0, atol=1e-12)


def test_stable_refuses_springs_too_fast_for_the_step():
    settings = dict(spring_bones.DEFAULTS)
    assert spring_bones.stable(30, settings)
    assert not spring_bones.stable(30, {**settings, "f_root_hz": 100.0, "substeps": 1})
    assert not spring_bones.stable(30, {**settings, "damping": 20.0})


def test_height_weights_blend_between_neighbouring_bones_and_clamp_at_the_ends():
    weights = spring_bones.height_weights(np.array([1.5, 1.4, 1.3, 1.0, 0.5]), [1.46, 1.38, 1.24, 0.70])
    np.testing.assert_allclose(weights.sum(1), 1.0)
    np.testing.assert_allclose(weights[0], [1, 0, 0, 0])            # いちばん上より上は端の骨だけ
    np.testing.assert_allclose(weights[1], [0.25, 0.75, 0, 0])
    np.testing.assert_allclose(weights[4], [0, 0, 0, 1])


def prototype_skin_point(REL, w, p):
    """リポジトリ外の試作の skin_point（重みは外から）をそのまま写したもの"""
    h = np.c_[p, np.ones(len(p))]
    m = np.einsum("fbij,nb->fnij", REL, w)
    x = np.einsum("fnij,nj->fni", m, h)[..., :3]
    u, _, vt = np.linalg.svd(m[..., :3, :3])
    return x, u @ vt


def test_blend_follow_matches_the_prototype():
    rng = np.random.default_rng(8)
    moves = np.tile(np.eye(4), (5, 3, 1, 1))
    moves[..., :3, :3] = random_rotations(15, rng).reshape(5, 3, 3, 3)
    moves[..., :3, 3] = rng.normal(0, 0.05, (5, 3, 3))
    points = rng.normal(0, 0.3, (12, 3)) + [0, 0, 1.2]
    weights = spring_bones.height_weights(points[:, 2], [1.4, 1.1, 0.8])
    got, expected = spring_bones.blend_follow(moves, weights, points), prototype_skin_point(moves, weights, points)
    np.testing.assert_array_equal(got[0], expected[0])
    np.testing.assert_array_equal(got[1], expected[1])


def test_root_location_puts_the_root_head_where_asked():
    rng = np.random.default_rng(9)
    follow = np.tile(np.eye(4), (4, 1, 1))
    follow[:, :3, :3] = random_rotations(4, rng)
    follow[:, :3, 3] = rng.normal(0, 0.1, (4, 3))
    rest_root = np.eye(4)
    rest_root[:3, :3] = random_rotations(1, rng)[0]
    rest_root[:3, 3] = [0.02, 0.05, 1.5]
    heads = rng.normal(0, 0.1, (4, 3)) + [0, 0, 1.5]
    loc = spring_bones.root_location(follow, rest_root, heads)
    for k in range(4):                                             # 親の今 × 静止 × 位置のキー の原点が head になる
        basis = np.eye(4)
        basis[:3, 3] = loc[k]
        np.testing.assert_allclose((follow[k] @ rest_root @ basis)[:3, 3], heads[k], atol=1e-12)


def test_motion_stats_by_node():
    nodes, targets = hanging_chains()
    sim, _ = spring_bones.simulate(nodes, targets)
    stats = spring_bones.motion_stats(sim, targets)
    assert len(stats["offset_cm_median_by_node"]) == 6 and stats["offset_cm_median_by_node"][0] == 0.0
    assert stats["step_cm"]["max"] >= stats["step_cm"]["p99"] > 0


def test_parse_args():
    args = spring_bones.parse_args(["--blend", "in.blend", "--out-blend", "o.blend", "--report", "r.json", "--settings", "s.json"])
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
open(value("--out-blend"), "wb").write(b"sprung")
settings = json.load(open(value("--settings"), encoding="utf-8"))
json.dump({"schema_version": "model3d-spring-bones/0.1", "frames": [1, 3], "fps": 30.0,
           "chains": [{"root": n, "bones": 7, "parent": "Head"} for n in settings["chains"]], "collision_pushes": 12,
           "motion": {"offset_cm_median_by_node": [0.0, 1.7]}, "spring_stretch_mm": {"max": 12.3, "min": -8.7},
           "bake_check": {"direction_deg": DIRECTION_DEG, "root_mm": ROOT_MM}, "bones_vs_springs_mm": 14.0,
           "argv": args, "script": script, "settings": settings}, open(value("--report"), "w"))
'''


def blender_stand_in(directory, direction_deg=0.000123456, root_mm=0.00002):
    return stand_in_executable(directory, "blender", STAND_IN.replace("DIRECTION_DEG", repr(direction_deg)).replace("ROOT_MM", repr(root_mm)))


def make_recipe(tmp_path, parameters, outputs=None):
    (tmp_path / "dance.blend").write_bytes(b"grounded")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "spring",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "spring", "operation": "motion.spring_bones",
                                                      "inputs": {"blend": "$inputs.dance"}, "parameters": parameters,
                                                      "outputs": outputs or {"blend": "sprung.blend", "report": "spring.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_writes_the_settings_and_passes_them_to_blender(tmp_path):
    from model3d import executor
    recipe = make_recipe(tmp_path, {"blender": str(blender_stand_in(tmp_path)), "chains": ["HairA0", "HairB0"],
                                    "colliders": [{"from": "Spine", "to": "Spine2", "radius_mm": 130}], "links": [[0, 1]],
                                    "target": "animation", "f_root_hz": 3, "substeps": 10})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["spring"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["script"]).name == "spring_bones.py" and Path(report["argv"][report["argv"].index("--blend") + 1]).name == "dance.blend"
    assert report["settings"] == {"chains": ["HairA0", "HairB0"], "colliders": [{"from": "Spine", "to": "Spine2", "radius_mm": 130.0}],
                                  "links": [[0, 1]], "target": "animation", "follow": [], "f_root_hz": 3.0, "f_tip_hz": 1.1, "damping": 0.3,
                                  "bend": 0.12, "substeps": 10, "push": 0.3, "settle_s": 1.0}
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"sprung"
    observation = step["observations"][0]
    assert observation["kind"] == "spring_bones" and observation["chains"] == 2 and observation["bones_per_chain"] == 7
    assert observation["collision_pushes"] == 12 and observation["bake_check"]["direction_deg"] == 0.00012   # 観察は丸める
    assert observation["spring_stretch_mm"]["max"] == 12.3 and observation["bones_vs_springs_mm"] == 14.0
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["spring"]["reused_from"] is None   # 毎回実行する


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


def call(tmp_path, parameters, outputs=None):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"grounded")
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}
    return get("motion.spring_bones").function({"blend": tmp_path / "dance.blend"}, parameters, declared, Context(tmp_path))


def test_the_operation_stops_when_the_baked_bones_miss_the_springs(tmp_path):
    with pytest.raises(ToolError, match="direction_deg"):
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path, direction_deg=2.5)), "chains": ["HairA0"]})
    with pytest.raises(ToolError, match="root_mm"):             # 0.10004 は 4 桁に丸めると 0.1 で通ってしまう
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path, root_mm=0.10004)), "chains": ["HairA0"]})


def test_stretched_springs_alone_do_not_stop_the_operation(tmp_path):
    result = call(tmp_path, {"blender": str(blender_stand_in(tmp_path)), "chains": ["HairA0"]})   # 節の位置の差 14 mm は報告だけ
    assert result.observations[0]["bones_vs_springs_mm"] == 14.0


@pytest.mark.parametrize("parameters, message", [
    ({"chains": ["HairA0"], "wobble": 1}, "知らないパラメーター"),
    ({}, "chains"),
    ({"chains": []}, "chains"),
    ({"chains": "HairA0"}, "chains"),
    ({"chains": ["HairA0", "HairA0"]}, "chains"),
    ({"chains": ["HairA0", 3]}, "chains"),
    ({"chains": ["HairA0"], "colliders": {"from": "Spine"}}, "colliders"),
    ({"chains": ["HairA0"], "colliders": [{"from": "Spine", "to": "Spine2"}]}, "colliders"),
    ({"chains": ["HairA0"], "colliders": [{"from": "Spine", "to": "Spine2", "radius_mm": 0}]}, "radius_mm"),
    ({"chains": ["HairA0"], "colliders": [{"from": "Spine", "to": "Spine2", "radius_mm": 10, "extra": 1}]}, "colliders"),
    ({"chains": ["HairA0", "HairB0"], "links": [[0, 2]]}, "links"),
    ({"chains": ["HairA0", "HairB0"], "links": [[1, 1]]}, "links"),
    ({"chains": ["HairA0", "HairB0"], "links": [[0, True]]}, "links"),
    ({"chains": ["HairA0", "HairB0"], "links": [[0, 1, 1]]}, "links"),
    ({"chains": ["HairA0"], "target": "pose"}, "target"),
    ({"chains": ["HairA0"], "follow": {"bone": "Head"}}, "follow"),
    ({"chains": ["HairA0"], "follow": [{"bone": "Head"}]}, "follow"),
    ({"chains": ["HairA0"], "follow": [{"bone": "Head", "height_m": "high"}]}, "height_m"),
    ({"chains": ["HairA0"], "follow": [{"bone": "Head", "height_m": 1.4}, {"bone": "Neck", "height_m": 1.4}]}, "height_m"),
    ({"chains": ["HairA0"], "target": "animation", "follow": [{"bone": "Head", "height_m": 1.4}]}, "follow"),
    ({"chains": ["HairA0"], "bend": 1.5}, "bend"),
    ({"chains": ["HairA0"], "push": 0}, "push"),
    ({"chains": ["HairA0"], "push": 2}, "push"),
    ({"chains": ["HairA0"], "substeps": 0}, "substeps"),
    ({"chains": ["HairA0"], "substeps": 2.5}, "substeps"),
    ({"chains": ["HairA0"], "f_root_hz": 0}, "f_root_hz"),
    ({"chains": ["HairA0"], "damping": -1}, "damping"),
    ({"chains": ["HairA0"], "settle_s": -1}, "settle_s"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path)), **parameters})


def test_the_operation_needs_blender_and_both_outputs(tmp_path):
    with pytest.raises(ToolError, match="Blender"):
        call(tmp_path, {"chains": ["HairA0"]})
    with pytest.raises(ToolError, match="report"):
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path)), "chains": ["HairA0"]}, {"blend": "o.blend"})
