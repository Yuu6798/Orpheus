"""まばたきのキーの直しと目の新しいキー（model.eye_keys）: まばたきの直し・瞳のキー・見開きのキーの計算（Blender なし）、
工程の受け渡し（代役の Blender）と悪い設定を止めること。本物の Blender 5.2.4 での照合は docs/model3d.md（CI には Blender が無い）。
例の目と量は、どの頭とも関係の無い値。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import eye_keys, eye_math
from model3d.io import atomic_json
from model3d.ops.eye_ops import KEY_NAMES

CENTRE = np.array([0.0, 0.096, 1.4])                          # 球の手前の面は、目の真ん中で y = -0.004
EYE = {"sphere": {"centre": CENTRE.tolist(), "radius": 0.1, "normal": [0.0, -1.0, 0.0]}, "pupil": [0.0, 1.4],
       "iris": {"a": 0.005, "b": 0.004}, "rim": {"x": [-0.01, 0.0, 0.01], "upper": [1.404, 1.406, 1.404], "lower": [1.396, 1.394, 1.396]},
       "window": [-0.02, 0.02, 1.38, 1.42], "iris_vertices": [5, 7], "lid_vertices": [0, 1, 2, 3]}
CFG = {"lid_front": 0.0003, "lid_margin": 0.001, "close_below": 0.0005, "close_reach": 0.8, "close_max_scale": 1.5, "wide_up": 0.003, "wide_down": 0.001,
       "iris_small": 0.5, "iris_lift": 0.0005, "iris_stretch": [1.2, 0.9], "iris_squash": [1.15, 0.7],
       "side_fade": 0.005, "bump_power": 1.0, "upper_fall": [0.001, 0.008], "lower_fall": 0.005}
IDENTITY = eye_math.Frame(np.eye(4))


def test_frame_keeps_the_matrix_in_32_bits():
    m = np.eye(4)
    m[:3, :3] = [[0.6, 0, 0], [0, 0, -0.6], [0, 0.6, 0]]                       # 90° 回って 0.6 倍、上へ 1.2 m
    m[:3, 3] = [0.0, 0.05, 1.2]
    frame = eye_math.Frame(m)
    assert frame.a.dtype == np.float32 and frame.inverse.dtype == np.float32   # 逆の行列も 32 ビットで求める（試しと同じ）
    p = np.array([[0.1, 0.2, 0.3], [-0.05, 0.4, 0.7]])
    assert frame.world(p).dtype == np.float64 and frame.world(p.astype(np.float32)).dtype == np.float32
    assert np.allclose(frame.world(p)[0], m[:3, :3] @ p[0] + m[:3, 3], atol=1e-7)
    assert np.allclose(frame.local(frame.world(p)), p, atol=1e-6)


def _blink():
    """まばたきの例: 0 上まぶた（手前）、1 押し出される頂点、2 板より奥の頂点、3 窓の外、4 動かない、5 瞳、6 上がる頂点。"""
    base = np.array([[0.0, -0.006, 1.406], [0.005, -0.0052, 1.405], [0.008, -0.0035, 1.402], [0.03, -0.006, 1.40],
                     [0.0, -0.006, 1.40], [0.0, -0.0045, 1.40], [-0.005, -0.0052, 1.395]])
    key = base.copy()
    key[0, 2] -= 0.8 * 0.012                     # 開き 12 mm の 8 割まで下りる
    key[1] = [0.005, -0.0047, 1.400]             # 下りながら奥へ
    key[2, 2] -= 0.003
    key[3, 2] -= 0.005
    key[5, 2] -= 0.002
    key[6, 2] += 0.002
    return base.astype(np.float32), key.astype(np.float32)


def test_blink_fix_reaches_below_the_lower_lid_and_keeps_lids_in_front():
    base, key = _blink()
    fixed, info = eye_math.fix_blink(base, key, IDENTITY, EYE, CFG, True, skip=[5])
    assert info["scaled_vertices"] == 3 and info["pushed_in_front"] == 1 and info["pushed_max_mm"] > 0
    # 0: 上まぶたは、下まぶたのふち（1.394）より close_below 下まで届く
    assert np.allclose(fixed[0], [0.0, -0.006, 1.394 - 0.0005], atol=1e-6)
    # 1: 伸ばしたうえで、板より lid_margin 手前の球の面（半径 0.101）へ押し出す
    h = 0.010
    k = (h + 0.0005) / (0.8 * h)
    stretched = base[1] + (key[1] - base[1]) * k
    assert np.linalg.norm(fixed[1] - CENTRE) == pytest.approx(0.101, abs=1e-6)
    assert np.allclose((fixed[1] - CENTRE) / 0.101, (stretched - CENTRE) / np.linalg.norm(stretched - CENTRE), atol=1e-5)
    # 2: もとが板より奥の頂点は、伸ばすが押し出さない
    assert fixed[2][2] == pytest.approx(1.402 - 0.003 * (0.0088 + 0.0005) / (0.8 * 0.0088), abs=1e-6)   # x = 8 mm で開きは 8.8 mm
    # 3〜6: 窓の外・動かない頂点・瞳（skip）・上がる頂点はそのまま
    assert np.array_equal(fixed[[3, 4, 5]], key[[3, 4, 5]]) and np.allclose(fixed[6], key[6], atol=1e-7)


def test_smile_blink_is_not_stretched():
    base, key = _blink()
    fixed, info = eye_math.fix_blink(base, key, IDENTITY, EYE, CFG, False, skip=[5])
    assert info["scaled_vertices"] == 0 and info["pushed_in_front"] == 1
    assert np.allclose(fixed[0], key[0], atol=1e-7)
    assert np.linalg.norm(fixed[1] - CENTRE) == pytest.approx(0.101, abs=1e-6)


def _sphere_point(x, z, radius):
    return [x, float(eye_math.sphere_y(CENTRE, radius, x, z)), z]


def test_iris_key_scales_round_the_pupil_and_stays_on_the_sphere():
    base_w = np.array([[0.0, -0.006, 1.406]] * 5 + [_sphere_point(0.004, 1.403, 0.1005)] + [[0.01, -0.006, 1.41]]
                      + [_sphere_point(-0.002, 1.398, 0.1005)])
    p = eye_math.iris_key(base_w, EYE, 0.5, 0.5, CFG["iris_lift"])
    assert np.allclose(p[5, [0, 2]], [0.002, 1.4015]) and np.allclose(p[7, [0, 2]], [-0.001, 1.399])
    assert np.allclose(np.linalg.norm(p[[5, 7]] - CENTRE, axis=1), 0.1005)
    others = [0, 1, 2, 3, 4, 6]
    assert np.array_equal(p[others], base_w[others])
    stretched = eye_math.iris_key(base_w, EYE, 1.2, 0.9, CFG["iris_lift"])
    assert stretched[5, 0] == pytest.approx(0.0048) and stretched[5, 2] == pytest.approx(1.4027)


def test_wide_key_lifts_the_upper_lid_most_in_the_middle():
    base_w = np.array([[0.0, -0.006, 1.406], [0.0, -0.006, 1.394], [0.005, -0.006, 1.410], [0.0125, -0.006, 1.404], [0.0, -0.006, 1.40]])
    dz = eye_math.wide_key(base_w, EYE, CFG)[:, 2] - base_w[:, 2]
    assert dz[0] == pytest.approx(0.003)                                       # 上まぶた: 目の幅の真ん中で wide_up
    assert dz[1] == pytest.approx(-0.001)                                      # 下まぶた: wide_down 下へ
    assert dz[2] == pytest.approx(0.003 * math.sin(0.75 * math.pi) * 0.5)    # 縁から 5 mm 上: 減らし始め 1 mm・幅 8 mm で半分
    assert abs(dz[3]) < 1e-9 and dz[4] == 0.0                                 # 目の幅の外と、まぶたでない頂点は動かない


def test_new_keys_come_in_the_trial_order():
    plan = eye_keys.new_key_plan(KEY_NAMES, CFG)
    assert plan == [("iris_small_", 0.5, 0.5, 0.0), ("iris_wide_", 1.2, 0.9, 0.0), ("iris_squash_", 1.15, 0.7, -1.0)]
    assert eye_keys.SIDES == ("R", "L")                                        # 右目・左目の順に足す


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
shutil.copyfile(value("--blend"), value("--out"))
names = settings["names"]
new_keys = {prefix + side: {"moved_vertices": 2, "max_mm": 1.0} for side in ("R", "L") for prefix in names.values()}
blink = {settings["blink"][side]: {"scaled_vertices": 1, "pushed_in_front": 0, "pushed_max_mm": 0.0, "iris_reset": 0} for side in ("R", "L")}
json.dump({"blender": "5.2.4", "object": "Head", "blink": blink, "new_keys": new_keys, "x_settings": settings, "x_argv": sys.argv[1:]},
          open(value("--report"), "w", encoding="utf-8"))
'''


def _geometry():
    return {"schema_version": eye_math.SCHEMA, "object": "Head", "eyes": {"R": EYE, "L": {**EYE, "iris_vertices": [8, 9]}}}


def _recipe(tmp_path, parameters, data=None):
    (tmp_path / "head.blend").write_bytes(b"blend")
    atomic_json(tmp_path / "eyes.json", data or _geometry())
    step = {"id": "keys", "operation": "model.eye_keys", "inputs": {"blend": "$inputs.head", "geometry": "$inputs.eyes"},
            "parameters": parameters, "outputs": {"blend": "keys.blend", "report": "keys.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "keys",
                                           "inputs": {"head": {"path": "head.blend"}, "eyes": {"path": "eyes.json"}}, "steps": [step]})
    return tmp_path / "recipe.json"


def test_eye_keys_step_passes_the_amounts(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **CFG}), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["keys"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    settings = report["x_settings"]
    assert settings["cfg"] == CFG and settings["names"] == KEY_NAMES
    assert settings["blink"] == {"R": "blink_R", "L": "blink_L"} and settings["smile"] == {"R": "blink_smile_R", "L": "blink_smile_L"}
    assert report["blender_version"] == "5.2.4" and len(step["observations"][0]["new_keys"]) == 8
    argv = report["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "eye_keys.py" and "vrm" not in " ".join(argv)   # VRM Add-on は要らない
    names = {"wide": "wide_", "small": "small_", "stretch": "stretch_", "squash": "squash_"}
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **CFG, "smile": False, "names": names,
                                                    "blink": {"R": "close_R", "L": "close_L"}}), tmp_path / "runs")
    settings = json.loads(Path(result["steps"]["keys"]["artifacts"]["report"]).read_text(encoding="utf-8"))["x_settings"]
    assert settings["smile"] is None and settings["names"] == names and settings["blink"] == {"R": "close_R", "L": "close_L"}


def _without(key):
    return {k: v for k, v in CFG.items() if k != key}


@pytest.mark.parametrize("parameters, data, message", [
    (_without("lid_margin"), None, "lid_margin が要ります"), (_without("side_fade"), None, "side_fade が要ります"),
    (_without("iris_squash"), None, "iris_squash"), ({**CFG, "close_reach": 1.2}, None, "close_reach"),
    ({**CFG, "close_max_scale": 0.9}, None, "close_max_scale"), ({**CFG, "wide_up": -0.001}, None, "wide_up"),
    ({**CFG, "iris_stretch": [1.2]}, None, "iris_stretch"), ({**CFG, "upper_fall": [0.002, 0.0]}, None, "upper_fall"),
    ({**CFG, "names": {"wide": "a_", "small": "a_", "stretch": "b_", "squash": "c_"}}, None, "重なって"),
    ({**CFG, "names": {"wide": "a_"}}, None, "names"), ({**CFG, "blink": {"R": "blink_R"}}, None, "blink"),
    ({**CFG, "blink": {"R": "blink_smile_R", "L": "blink_L"}}, None, "同じキーを 2 回"),
    (CFG, {**_geometry(), "eyes": {**_geometry()["eyes"], "L": {k: v for k, v in EYE.items() if k != "lid_vertices"}}}, "lid_vertices"),
    ({**CFG, "spin": 1}, None, "知らないパラメーター"), (CFG, {"schema_version": "other"}, "目の形の値")])
def test_eye_keys_refuses_bad_settings(tmp_path, parameters, data, message):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **parameters}, data), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["keys"]["error"]
