"""motion.ribbon_bones: 帯の垂れの計算・脚の当たりの作り方・太ももを混ぜる狙い（Blender なしで動く部分）と、台本の引数と
工程の受け渡し（代役の Blender）を確かめる。骨を読んで焼き付ける所は本物の Blender が要るので、CI では確かめない
（docs/model3d.md の手元の確認）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import spring_bones as sb
from model3d.io import ToolError, atomic_json


def prototype_ribbon(nodes, Ttar, cols, HIP_X, HIP_FWD, HIP_UP, fps=30, sub=8, f_root=4.0, f_tip=1.8, damp=0.35, gravity=1.0,
                     lane=0.8, over=0.5, root_move=0.025, inelastic=1.0, interp=1):
    """リポジトリ外の試作の simulate_ribbon（既定の押し方）を、狙いの位置を受け取る形にして写したもの"""
    F = len(Ttar)
    C, N = nodes.shape[:2]
    dt = 1.0 / (fps * sub)
    L = np.linalg.norm(np.diff(nodes, axis=1), axis=2)
    dirs = np.diff(Ttar, axis=2)
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)

    def rest_limit(nodes, col):
        ab = col["qb"] - col["qa"]
        t = np.clip(((nodes - col["qa"]) @ ab) / (ab @ ab), 0, 1)
        return np.linalg.norm(nodes - (col["qa"] + t[..., None] * ab), axis=-1)

    rest_cap = [rest_limit(nodes, col) for col in cols]
    s = np.linspace(0, 1, N)[1:]
    w0 = 2 * np.pi * f_root * (f_tip / f_root) ** s
    kk, drag = w0 ** 2, 2 * damp * w0 * dt
    grav = np.array([0.0, 0.0, -9.8 * gravity])

    def col_at(col, f_lo, f_hi, u):
        a0 = col["pa"][f_lo] + (col["pa"][f_hi] - col["pa"][f_lo]) * u
        return a0, col["pb"][f_lo] + (col["pb"][f_hi] - col["pb"][f_lo]) * u - a0

    def collide(x, i, geo, hx, hf, hu):
        for (a0, ab), dcap, col in zip(geo, rest_cap, cols):
            t = np.clip(((x - a0) @ ab) / (ab @ ab), 0, 1)
            q = a0 + t[:, None] * ab
            rc = 0.98 * np.minimum(col["ra"] + t * (col["rb"] - col["ra"]), dcap[:, i])
            d = x - q
            ln = np.linalg.norm(d, axis=1)
            m = ln < rc
            if not m.any():
                continue
            u = d / np.maximum(ln[:, None], 1e-6)
            u = u - (u @ hx)[:, None] * hx * lane
            axn = ab / np.linalg.norm(ab)
            pref = (hf + hu) - ((hf + hu) @ axn) * axn
            pref /= max(np.linalg.norm(pref), 1e-9)
            u = u - (u @ axn)[:, None] * axn
            u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-9)
            u = u + over * pref
            u = u - (u @ axn)[:, None] * axn
            u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-9)
            dpar = d - (d @ (ab / np.linalg.norm(ab)))[:, None] * (ab / np.linalg.norm(ab))
            b_ = (dpar * u).sum(1)
            c_ = (dpar * dpar).sum(1) - rc * rc
            s_ = -b_ + np.sqrt(np.maximum(b_ * b_ - c_, 0))
            x[m] = x[m] + u[m] * s_[m, None]
        return x

    def advance(x, xo, f_lo, f_hi, u):
        root = Ttar[f_lo, :, 0] + (Ttar[f_hi, :, 0] - Ttar[f_lo, :, 0]) * u
        dr = dirs[f_lo] + (dirs[f_hi] - dirs[f_lo]) * u
        dr /= np.linalg.norm(dr, axis=-1, keepdims=True)
        geo = [col_at(col, f_lo, f_hi, u if interp else 1.0) for col in cols]
        hx = HIP_X[f_lo] + (HIP_X[f_hi] - HIP_X[f_lo]) * u
        hx /= np.linalg.norm(hx)
        hf = HIP_FWD[f_lo] + (HIP_FWD[f_hi] - HIP_FWD[f_lo]) * u
        hf /= np.linalg.norm(hf)
        hu = HIP_UP[f_lo] + (HIP_UP[f_hi] - HIP_UP[f_lo]) * u
        hu /= np.linalg.norm(hu)
        xn = x.copy()
        prev = x.copy()
        pushed = collide(root.copy(), 0, geo, hx, hf, hu)
        mv = pushed - root
        ln_ = np.linalg.norm(mv, axis=1, keepdims=True)
        xn[:, 0] = root + mv * np.minimum(1.0, root_move / np.maximum(ln_, 1e-9))
        for i in range(1, N):
            want = xn[:, i - 1] + L[:, i - 1, None] * dr[:, i - 1]
            p = x[:, i] + (x[:, i] - xo[:, i]) * (1 - drag[i - 1]) + dt * dt * (kk[i - 1] * (want - x[:, i]) + grav)
            dd = p - xn[:, i - 1]
            p = xn[:, i - 1] + dd * (L[:, i - 1, None] / np.maximum(np.linalg.norm(dd, axis=1, keepdims=True), 1e-9))
            p0 = p.copy()
            for _ in range(3):
                p = collide(p, i, geo, hx, hf, hu)
                dd = p - xn[:, i - 1]
                p = xn[:, i - 1] + dd * (L[:, i - 1, None] / np.maximum(np.linalg.norm(dd, axis=1, keepdims=True), 1e-9))
            xn[:, i] = p
            prev[:, i] = x[:, i] + (p - p0) * inelastic
        return xn, prev

    x = Ttar[0].copy()
    xo = x.copy()
    for _ in range(fps * sub):
        x, xo = advance(x, xo, 0, 0, 0.0)
    SIM = np.zeros((F, C, N, 3))
    SIM[0] = x
    for f in range(1, F):
        for k_ in range(sub):
            x, xo = advance(x, xo, f - 1, f, (k_ + 1) / sub)
        SIM[f] = x
    return SIM


def ribbon(frames=30, lift=0.15):
    """腰の高さから前に垂れる帯（6 節）と、太ももが前へ上がってくる当たり（コマで前へ出るカプセル）"""
    nodes = np.zeros((1, 6, 3))
    nodes[0, :, 1] = -0.2
    nodes[0, :, 2] = 1.0 - 0.06 * np.arange(6)
    targets = np.repeat(nodes[None], frames, axis=0)
    swing = lift * np.sin(np.linspace(0, np.pi, frames))
    a = np.stack([np.zeros(frames), -0.05 - swing, np.full(frames, 0.85)], 1)
    b = np.stack([np.zeros(frames), -0.05 - 2 * swing, np.full(frames, 0.55 + 2 * swing)], 1)
    thigh = {"pa": a, "pb": b, "ra": 0.07, "rb": 0.06, "qa": a[0], "qb": b[0]}
    hips = {"x": np.tile([1.0, 0, 0], (frames, 1)), "fwd": np.tile([0, -1.0, 0], (frames, 1)), "up": np.tile([0, 0, 1.0], (frames, 1))}
    return nodes, targets, thigh, hips


def test_simulate_ribbon_matches_the_prototype_where_capsules_continue():
    nodes, targets, thigh, hips = ribbon()
    joined = {**thigh, "open_a": True, "open_b": True}                  # 両端が継ぎ目なら、試作と同じ解き方
    sim = sb.simulate_ribbon(nodes, targets, [joined], hips)
    expected = prototype_ribbon(nodes, targets, [thigh], hips["x"], hips["fwd"], hips["up"])
    np.testing.assert_array_equal(sim, expected)


def end_cap_push(open_end):
    """端から軸の向きに 97 mm 先の節（半径 100 mm のカプセル、実際の半径は 98 mm）を、1 刻みで押した量（m）"""
    a, b = np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, 0.6])
    cap = {"pa": np.stack([a, a]), "pb": np.stack([b, b]), "ra": 0.1, "rb": 0.1, "qa": a, "qb": b,
           "open_a": False, "open_b": open_end}
    nodes = np.array([[[0.3, 0.0, 1.0], [0.3, 0.0, 0.94]]])            # 静止では当たりから離れている
    targets = np.repeat(np.array([[[0.0, 0.0, 0.563], [0.0, 0.0, 0.503]]])[None], 2, axis=0)   # 2 つ目の節は端の 97 mm 先
    hips = {"x": np.tile([1.0, 0, 0], (2, 1)), "fwd": np.tile([0, -1.0, 0], (2, 1)), "up": np.tile([0, 0, 1.0], (2, 1))}
    sim = sb.simulate_ribbon(nodes, targets, [cap], hips, gravity=0.0, lane=0.0, root_move=0.0, settle=0.0, substeps=1)
    return np.linalg.norm(sim[1, 0, 1] - targets[1, 0, 1])


def test_a_free_capsule_end_is_solved_as_a_sphere():
    free, joined = end_cap_push(False), end_cap_push(True)
    # 1 回の押しは、端では球の面までの約 14 mm、継ぎ目では円柱として 98 mm。押した後に長さの戻しで回り込むので、
    # 1 コマの後の動きは、それぞれ約 30 mm と約 77 mm になる
    assert free < 0.04 and joined > 0.06


def test_a_ribbon_keeps_its_lengths_hangs_from_its_root_and_droops_under_gravity():
    nodes, targets, _, hips = ribbon()
    targets = targets.copy()
    targets[..., 1] = -0.2 - 0.06 * np.arange(6)                        # 狙いの形は前へ水平に伸びる帯
    targets[..., 2] = 1.0
    nodes = targets[0].copy()
    sim = sb.simulate_ribbon(nodes, targets, [], hips)
    np.testing.assert_allclose(np.linalg.norm(np.diff(sim, axis=2), axis=3), 0.06, atol=1e-12)
    np.testing.assert_allclose(sim[:, :, 0], targets[:, :, 0], atol=1e-12)
    assert sim[-1, 0, -1, 2] < 0.98                                    # 重力で先が下がる
    flat = sb.simulate_ribbon(nodes, targets, [], hips, gravity=0.0)
    np.testing.assert_allclose(flat, targets, atol=1e-9)               # 重力が無ければ狙いの形のまま


def test_a_raised_thigh_pushes_the_ribbon_out_in_front():
    nodes, targets, thigh, hips = ribbon()
    free = sb.simulate_ribbon(nodes, targets, [], hips)
    held = sb.simulate_ribbon(nodes, targets, [thigh], hips)
    inside_free = sb.surface_clear(free, [thigh])[:, :, 1:].min()
    inside_held = sb.surface_clear(held, [thigh])[:, :, 1:].min()
    assert inside_free < -0.02 and inside_held > inside_free            # 当たりがあると、中に入る深さが浅い
    assert held[:, 0, -1, 1].min() < free[:, 0, -1, 1].min()            # 先は前（-y）へ乗り越える


def test_a_ribbon_on_a_capsule_axis_still_gets_out_with_no_front_bias():
    a, b = np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, 0.6])        # 縦のカプセル
    cap = {"pa": np.tile(a, (10, 1)), "pb": np.tile(b, (10, 1)), "ra": 0.05, "rb": 0.05, "qa": a, "qb": b}
    nodes = np.array([[[0.3, 0.0, 1.06], [0.3, 0.0, 1.0], [0.3, 0.0, 0.94]]])   # 静止では当たりから離れている
    targets = np.repeat(np.array([[[0.0, 0.0, 1.06], [0.0, 0.0, 1.0], [0.0, 0.0, 0.94]]])[None], 10, axis=0)   # 狙いは軸の上
    hips = {"x": np.tile([1.0, 0, 0], (10, 1)), "fwd": np.tile([0, -1.0, 0], (10, 1)), "up": np.tile([0, 0, 1.0], (10, 1))}
    kw = dict(gravity=0.0, over=0.0, root_move=0.0, settle=0.0)
    sim = sb.simulate_ribbon(nodes, targets, [cap], hips, **kw)
    stuck = prototype_ribbon(nodes, targets, [cap], hips["x"], hips["fwd"], hips["up"], gravity=0.0, over=0.0, root_move=0.0)
    off_axis = lambda s_, n: np.hypot(s_[-1, 0, n, 0], s_[-1, 0, n, 1])
    assert max(off_axis(stuck, 1), off_axis(stuck, 2)) < 1e-9           # 試作の式では押す向きが 0 で、軸の上に残る
    assert sb.surface_clear(sim[-1:], [cap])[0, 0, 1] > -0.002          # 1 つ目の節は当たりの面まで出る
    assert sim[-1, 0, 1, 1] < 0 and off_axis(sim, 2) > 0.01             # 腰の前（-y）へ。2 つ目も軸から離れる
    # （この場面は狙いが当たりの中を通る無理な形なので、2 つ目は当たりの中に少し残る）


def test_fit_capsules_measures_slices_of_a_tube_around_the_bones():
    rng = np.random.default_rng(1)
    heads = [np.array([0.0, 0, 1.0]), np.array([0.0, 0, 0.6]), np.array([0.0, 0, 0.2])]
    t = rng.random(20000)
    angle = rng.random(20000) * 2 * np.pi
    z = 1.0 - 0.8 * t
    radius = np.where(z > 0.6, 0.09, 0.06)                              # 太ももは太く、すねは細い
    verts = np.stack([0.01 + radius * np.cos(angle), radius * np.sin(angle), z], 1)   # 中心は骨から 1 cm ずれている
    points = sb.fit_capsules(verts, heads, [[0.25, 0.75], [0.5]])
    assert [j for j, _, _ in points] == [0, 0, 0, 1]                    # 付け根まで伸ばした点と、3 つの輪切り
    np.testing.assert_allclose(points[0][1], [0.01, 0, 1.0], atol=3e-3)
    np.testing.assert_allclose(points[1][1], [0.01, 0, 0.9], atol=3e-3)
    assert points[1][2] == pytest.approx(0.09, abs=4e-3) and points[3][2] == pytest.approx(0.06, abs=4e-3)   # 乱数の中心のずれのぶん
    assert len(sb.fit_capsules(verts[:30], heads, [[0.25, 0.75], [0.5]])) == 0   # 頂点の少ない輪切りは飛ばす
    assert [j for j, _, _ in sb.fit_capsules(verts, heads, [[0.25], [0.5]], extend_first=False)] == [0, 1]


def test_capsules_from_fit_move_each_end_with_its_bone():
    points = [(0, np.array([0.0, 0, 1.0]), 0.09), (0, np.array([0.0, 0, 0.9]), 0.08), (1, np.array([0.0, 0, 0.4]), 0.06)]
    still, lifted = np.tile(np.eye(4), (2, 1, 1)), np.tile(np.eye(4), (2, 1, 1))
    lifted[:, 2, 3] = 0.1
    caps = sb.capsules_from_fit(points, [still, lifted], 0.02)
    assert len(caps) == 2 and caps[0]["ra"] == pytest.approx(0.11) and caps[1]["rb"] == pytest.approx(0.08)
    np.testing.assert_allclose(caps[1]["pa"], [[0, 0, 0.9]] * 2)
    np.testing.assert_allclose(caps[1]["pb"], [[0, 0, 0.5]] * 2)         # 2 本目の骨は 0.1 m 上がった
    np.testing.assert_allclose(caps[1]["qb"], [0, 0, 0.4])


def prototype_skin_follow(REL, w, p, thigh, wmax, z0, span):
    """リポジトリ外の試作の skin_follow（重みは外から）をそのまま写したもの"""
    m = np.einsum("fbij,nb->fnij", REL, w)
    wb = np.clip((z0 - p[:, 2]) / span, 0, 1) * wmax
    m = m * (1 - wb)[None, :, None, None] + thigh[:, None] * wb[None, :, None, None]
    x = np.einsum("fnij,nj->fni", m, np.c_[p, np.ones(len(p))])[..., :3]
    u, _, vt = np.linalg.svd(m[..., :3, :3])
    return x, u @ vt


def test_the_thigh_blend_matches_the_prototype():
    rng = np.random.default_rng(2)
    moves = np.tile(np.eye(4), (6, 3, 1, 1))
    moves[..., :3, 3] = rng.normal(0, 0.05, (6, 3, 3))
    thigh = np.tile(np.eye(4), (6, 1, 1))
    thigh[:, :3, 3] = rng.normal(0, 0.1, (6, 3))
    points = np.stack([np.zeros(8), np.full(8, -0.2), np.linspace(1.0, 0.6, 8)], 1)
    weights = sb.height_weights(points[:, 2], [1.4, 1.0, 0.7])
    extra_w = sb.lower_weights(points[:, 2], 0.6, 0.985, 0.25)
    got = sb.blend_follow(moves, weights, points, thigh, extra_w)
    expected = prototype_skin_follow(moves, weights, points, thigh, 0.6, 0.985, 0.25)
    np.testing.assert_array_equal(got[0], expected[0])
    np.testing.assert_array_equal(got[1], expected[1])
    np.testing.assert_allclose(sb.lower_weights([1.0, 0.985, 0.86, 0.7], 0.6, 0.985, 0.25), [0, 0, 0.3, 0.6])


def test_uniform_scale_reads_one_factor_or_refuses_uneven_scale():
    assert sb.uniform_scale(np.eye(4)) == 1.0
    turned = np.eye(4)
    turned[:3, :3] = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]]) * 0.01   # 回転と、FBX によくある 0.01 倍
    assert sb.uniform_scale(turned) == pytest.approx(0.01)
    assert sb.uniform_scale(np.diag([1.0, 2.0, 1.0, 1.0])) is None


def test_hip_axes_turn_with_the_hips():
    turn = np.array([[[0.0, -1, 0], [1, 0, 0], [0, 0, 1]]])             # 上から見て 90° 回る
    axes = sb.hip_axes(turn)
    np.testing.assert_allclose(axes["x"], [[0, 1, 0]])
    np.testing.assert_allclose(axes["fwd"], [[1, 0, 0]])
    np.testing.assert_allclose(axes["up"], [[0, 0, 1]])


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
open(value("--out-blend"), "wb").write(b"ribboned")
settings = json.load(open(value("--settings"), encoding="utf-8"))
json.dump({"schema_version": "model3d-spring-bones/0.1", "frames": [1, 3], "fps": 30.0,
           "chains": [{"root": n, "bones": 5, "parent": "Hips"} for n in settings["chains"]], "colliders": 14,
           "ribbons": [{"root": n, "motion": {}, "clearance_mm": {"min": -26.2, "frames_under_3mm": 76}} for n in settings["chains"]],
           "spring_stretch_mm": {"max": 0.0, "min": 0.0}, "bake_check": {"direction_deg": 0.0002, "root_mm": 0.00008},
           "bones_vs_springs_mm": 0.0, "argv": args, "script": script, "settings": settings}, open(value("--report"), "w"))
'''

P = "mixamorig:"
FOLLOW = [{"bone": P + "Spine", "height_m": 0.96}, {"bone": P + "Hips", "height_m": 0.70}]
FITTED = [{"bones": [P + "LeftUpLeg", P + "LeftLeg", P + "LeftFoot"], "slices": [[0.25, 0.45], [0.2, 0.5]], "objects": ["leg L"]}]


def test_the_operation_writes_ribbon_settings_and_uses_the_spring_script(tmp_path):
    from model3d import executor
    (tmp_path / "dance.blend").write_bytes(b"sprung")
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), "chains": ["SashA0", "SashB0"], "follow": FOLLOW,
              "thigh": {"bone": P + "RightUpLeg", "weight": 0.6, "from_height_m": 0.985, "span_m": 0.25}, "fitted": FITTED}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "ribbon",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "ribbon", "operation": "motion.ribbon_bones",
                                                      "inputs": {"blend": "$inputs.dance"}, "parameters": params,
                                                      "outputs": {"blend": "ribboned.blend", "report": "ribbon.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["ribbon"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["script"]).name == "spring_bones.py"
    settings = report["settings"]
    assert settings["model"] == "ribbon" and settings["chains"] == ["SashA0", "SashB0"] and settings["fitted"] == FITTED
    assert (settings["f_root_hz"], settings["f_tip_hz"], settings["damping"]) == (4.0, 1.8, 0.35)
    assert (settings["fit_margin_mm"], settings["hips"], settings["gravity"], settings["lane"], settings["over"]) == (22.0, P + "Hips", 1.0, 0.8, 0.5)
    assert (settings["root_move_mm"], settings["inelastic"], settings["interp"]) == (25.0, 1.0, True)
    assert settings["thigh"] == {"bone": P + "RightUpLeg", "weight": 0.6, "from_height_m": 0.985, "span_m": 0.25}
    observation = step["observations"][0]
    assert observation["kind"] == "ribbon_bones" and observation["colliders"] == 14 and observation["chains"] == 2
    assert observation["ribbons"][0]["clearance_mm"]["frames_under_3mm"] == 76
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["ribbon"]["reused_from"] is None


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


def call(tmp_path, parameters):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"sprung")
    declared = {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    return get("motion.ribbon_bones").function({"blend": tmp_path / "dance.blend"}, {"blender": blender, **parameters}, declared,
                                               Context(tmp_path))


THIGH = {"bone": P + "RightUpLeg", "weight": 0.6, "from_height_m": 0.985, "span_m": 0.25}


@pytest.mark.parametrize("parameters, message", [
    ({"chains": ["SashA0"], "links": [[0, 1]]}, "知らないパラメーター"),
    ({"chains": ["SashA0"], "thigh": THIGH}, "follow と一緒"),
    ({"chains": ["SashA0"], "follow": FOLLOW, "thigh": {**THIGH, "weight": 1.5}}, "weight"),
    ({"chains": ["SashA0"], "follow": FOLLOW, "thigh": {**THIGH, "span_m": 0}}, "span_m"),
    ({"chains": ["SashA0"], "follow": FOLLOW, "thigh": {"bone": P + "RightUpLeg"}}, "thigh"),
    ({"chains": ["SashA0"], "fitted": [{"bones": [P + "LeftUpLeg"], "slices": []}]}, "fitted"),
    ({"chains": ["SashA0"], "fitted": [{"bones": [P + "LeftUpLeg", P + "LeftLeg"], "slices": [[0.2], [0.5]]}]}, "fitted"),
    ({"chains": ["SashA0"], "fitted": [{"bones": [P + "LeftUpLeg", P + "LeftLeg"], "slices": [[1.2]]}]}, "fitted"),
    ({"chains": ["SashA0"], "fitted": [{"bones": [P + "LeftUpLeg", P + "LeftLeg"], "slices": [[0.2]], "objects": []}]}, "fitted"),
    ({"chains": ["SashA0"], "fitted": [{"bones": [P + "LeftUpLeg", P + "LeftLeg"], "slices": [[0.2]], "extend_first": 1}]}, "fitted"),
    ({"chains": ["SashA0"], "fitted": [{"bones": [P + "LeftUpLeg", P + "LeftLeg"], "slices": [[0.2]], "mesh": "x"}]}, "fitted"),
    ({"chains": ["SashA0"], "hips": ""}, "hips"),
    ({"chains": ["SashA0"], "lane": 1.5}, "lane"),
    ({"chains": ["SashA0"], "inelastic": -0.5}, "inelastic"),
    ({"chains": ["SashA0"], "interp": "yes"}, "interp"),
    ({"chains": ["SashA0"], "gravity": -1}, "gravity"),
    ({"chains": ["SashA0"], "fit_margin_mm": -1}, "fit_margin_mm"),
    ({"chains": ["SashA0"], "root_move_mm": -1}, "root_move_mm"),
    ({}, "chains"),
])
def test_the_operation_rejects_bad_ribbon_settings(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters)
