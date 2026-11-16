"""布の動きをならして頂点の動きのファイルに置き換える（cloth.smooth）: ならし方と PC2 の形（Blender なし）、工程の受け渡し
（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import cloth_smooth as cs
from model3d.blender_adapter import jitter_sample
from model3d.io import ToolError, atomic_json


def _bones(count):
    """コマごとの骨の行列: 速さの変わる回転と、まっすぐ進む動き。"""
    out = []
    for f in range(count):
        angle = 0.05 * f * f
        c, s = math.cos(angle), math.sin(angle)
        out.append([[c, -s, 0, 0.2 * f], [s, c, 0, 0.1], [0, 0, 1, 1.0], [0, 0, 0, 1]])
    return np.array(out)


def _world(local, bones):
    return np.einsum("fij,fvj->fvi", bones[:, :3, :3], local) + bones[:, None, :3, 3]


def test_gaussian_kernel_reaches_three_sigmas():
    kernel, reach = cs.gaussian_kernel(1.2)
    assert reach == 4 and len(kernel) == 9 and kernel.sum() == pytest.approx(1.0)
    assert kernel[4] == kernel.max() and np.allclose(kernel, kernel[::-1])


def test_smoothing_keeps_what_moves_with_the_bone_and_removes_a_one_frame_jump():
    frames, count = 12, 3
    bones = _bones(frames)
    local = np.tile(np.array([[0.1, 0.0, -0.4], [0.0, 0.15, -0.5], [-0.1, 0.0, -0.6]]), (frames, 1, 1))
    still, summary = cs.smooth_in_bone_space(_world(local, bones), bones, 1.2, 3)
    assert np.allclose(still, _world(local, bones), atol=1e-12)        # 骨に付いて動く所はそのまま
    assert summary["moved_mm"]["max"] == pytest.approx(0.0, abs=1e-6)
    jumped = local.copy()
    jumped[5, 1, 2] += 0.03                                             # 1 コマだけ 30 mm 跳ぶ
    result, summary = cs.smooth_in_bone_space(_world(jumped, bones), bones, 1.2, 3)
    assert np.allclose(result, _world(local, bones), atol=1e-12)        # 中央値で消える
    assert summary["jitter_mm_before"]["mean"] > 0 and summary["jitter_mm_after"]["mean"] == pytest.approx(0.0, abs=1e-6)
    assert summary["moved_mm"]["max"] == pytest.approx(30.0, abs=0.05)
    gauss_only, _ = cs.smooth_in_bone_space(_world(jumped, bones), bones, 1.2, 1)    # 中央値なしでは、ならしても残る
    back = np.einsum("fij,fvj->fvi", np.linalg.inv(bones)[:, :3, :3], gauss_only) + np.linalg.inv(bones)[:, None, :3, 3]
    assert 0.001 < abs(back[5, 1, 2] - local[5, 1, 2]) < 0.03


def test_smoothing_extends_the_ends_with_the_end_values():
    bones = np.tile(np.eye(4), (6, 1, 1))
    track = np.zeros((6, 1, 3))
    track[:, 0, 0] = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]                     # 最後のコマだけ動く（端は端の値で延ばす）
    result, _ = cs.smooth_in_bone_space(track, bones, 1.0, 1)
    kernel, reach = cs.gaussian_kernel(1.0)
    assert result[-1, 0, 0] == pytest.approx(kernel[reach:].sum())
    assert result[0, 0, 0] == pytest.approx(0.0)


def test_jitter_is_the_same_formula_as_the_check():
    rng = np.random.default_rng(3)
    track = rng.normal(size=(7, 4, 3))
    assert np.array_equal(cs.jitter(track), jitter_sample.offsets(track))


def test_pc2_bytes_have_the_point_cache_header():
    points = np.arange(2 * 3 * 3, dtype=float).reshape(2, 3, 3) / 7
    data = cs.pc2_bytes(points, -53)
    assert len(data) == 32 + 2 * 3 * 3 * 4
    assert data[:12] == b"POINTCACHE2\x00" and struct.unpack("<iiffi", data[12:32]) == (1, 3, -53.0, 1.0, 2)
    back, start, step = cs.read_pc2(data)
    assert start == -53.0 and step == 1.0 and np.array_equal(back, points.astype("<f4"))
    with pytest.raises(ValueError, match="PC2"):
        cs.read_pc2(b"X" * 32)
    with pytest.raises(ValueError, match="大きさ"):
        cs.read_pc2(data[:-4])


def test_object_space_uses_each_frames_matrix_when_the_object_moves():
    rng = np.random.default_rng(5)
    local = rng.normal(size=(4, 6, 3))
    still = np.tile(np.array([[1, 0, 0, 0.5], [0, 0, -1, 0.2], [0, 1, 0, 1.0], [0, 0, 0, 1.0]]), (4, 1, 1))
    world = np.einsum("fij,fvj->fvi", still[:, :3, :3], local) + still[:, None, :3, 3]
    inverses = np.linalg.inv(still)
    back, moves = cs.to_object_space(world, inverses)
    assert moves is False and np.array_equal(back, world @ inverses[0][:3, :3].T + inverses[0][:3, 3])   # 台本と同じ 1 つの行列
    assert np.allclose(back, local)
    moving = still.copy()
    moving[:, 0, 3] += np.arange(4) * 0.3                                  # 親が動いて、物体そのものが動く
    world = np.einsum("fij,fvj->fvi", moving[:, :3, :3], local) + moving[:, None, :3, 3]
    back, moves = cs.to_object_space(world, np.linalg.inv(moving))
    assert moves is True and np.allclose(back, local)                     # コマごとの行列で直すと、物体の中の座標に戻る
    wrong = world @ np.linalg.inv(moving)[-1][:3, :3].T + np.linalg.inv(moving)[-1][:3, 3]
    assert not np.allclose(wrong, local)                                   # 最後のコマの行列だけで直すとずれる


def test_topology_changes_are_refused():
    assert cs.topology_problem("Skirt", 2137, 2137) is None
    assert "Subdivision" in cs.topology_problem("Skirt", 2137, 8548) and "2137 → 変形の後 8548" in cs.topology_problem(
        "Skirt", 2137, 8548)


def test_cache_paths_are_relative_to_the_blend(tmp_path):
    blend = tmp_path / "run" / "smooth.blend"
    assert cs.relative_to_blend(tmp_path / "run" / "smooth_skirt.pc2", blend) == "//smooth_skirt.pc2"
    assert cs.relative_to_blend(tmp_path / "run" / "cache" / "a.pc2", blend) == "//cache/a.pc2"
    assert cs.relative_to_blend(tmp_path / "other" / "a.pc2", blend) is None


# 代役の Blender: 受け取った設定と引数を写し、報告と .blend と .pc2 を書く
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"smooth")
open(value("--proxy-cache"), "wb").write(b"proxy")
part = {"object": "skirt_proxy", "verts": 690, "jitter_mm_before": {"mean": 5.4}, "jitter_mm_after": {"mean": 0.8}, "moved_mm": {}}
report = {"schema_version": "model3d-cloth-smooth/0.1", "frames": [-53, 720], "proxy": part, "x_settings": settings,
          "x_argv": sys.argv[1:]}
if "--cache" in args:
    open(value("--cache"), "wb").write(b"detail")
    report["detail"] = {**part, "object": settings["detail"]}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def test_cloth_smooth_passes_the_settings_and_writes_the_caches(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "cloth.blend").write_bytes(b"cloth")
    step = {"id": "smooth", "operation": "cloth.smooth", "inputs": {"blend": "$inputs.cloth"},
            "parameters": {"blender": blender, "detail": "Skirt", "bone_prefix": "", "bone": "hips", "sigma": 1.5, "median": 5},
            "outputs": {"blend": "smooth.blend", "report": "smooth.json", "proxy_cache": "smooth_skirt_proxy.pc2",
                        "cache": "smooth_skirt.pc2"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "smooth",
                                           "inputs": {"cloth": {"path": "cloth.blend"}}, "steps": [step]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    artifacts = result["steps"]["smooth"]["artifacts"]
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"] == {"proxy": "skirt_proxy", "bone": "hips", "sigma": 1.5, "detail_sigma": 0.8, "median": 5,
                                    "detail": "Skirt"}
    argv = report["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "cloth_smooth.py"
    tail = argv[argv.index("--") + 1:]
    assert Path(tail[tail.index("--proxy-cache") + 1]) == Path(artifacts["proxy_cache"])
    assert Path(tail[tail.index("--cache") + 1]) == Path(artifacts["cache"])
    observation = result["steps"]["smooth"]["observations"][0]
    assert observation["kind"] == "cloth_smooth" and observation["detail"]["object"] == "Skirt"
    # 細かい裾をならさない（detail_sigma 0）なら、cache は書かない
    step["parameters"] = {"blender": blender, "detail_sigma": 0}
    step["outputs"] = {"blend": "smooth.blend", "report": "smooth.json", "proxy_cache": "smooth_skirt_proxy.pc2"}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "smooth",
                                           "inputs": {"cloth": {"path": "cloth.blend"}}, "steps": [step]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    report = json.loads(Path(result["steps"]["smooth"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert "--cache" not in report["x_argv"] and report["x_settings"]["bone"] == "mixamorig:Hips" and "detail" not in report


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"wobble": 1}, None, "知らないパラメーター"),
    ({"median": 2}, None, "奇数"),
    ({"median": 0}, None, "median"),
    ({"sigma": 0}, None, "sigma"),
    ({"detail_sigma": -1}, None, "detail_sigma"),
    ({}, None, "detail"),
    ({"detail": "skirt_proxy"}, None, "別のメッシュ"),
    ({"detail_sigma": 0, "detail": "Skirt"}, None, "detail_sigma"),
    ({"detail": "Skirt"}, {"blend": "o.blend", "report": "r.json", "proxy_cache": "p.pc2"}, "cache"),
    ({"detail_sigma": 0}, {"blend": "o.blend", "report": "r.json", "proxy_cache": "p.pc2", "cache": "c.pc2"}, "detail_sigma > 0"),
    ({"detail": "Skirt"}, {"blend": "o.blend", "report": "r.json", "proxy_cache": "p.bin", "cache": "c.pc2"}, ".pc2"),
    ({"detail": "Skirt"}, {"blend": "o.blend", "report": "r.json", "proxy_cache": "sub/p.pc2", "cache": "c.pc2"}, "同じフォルダ"),
    ({"detail": "Skirt", "bone": ""}, None, "bone"),
    ({"detail": "Skirt", "proxy": ""}, None, "proxy"),
])
def test_cloth_smooth_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "cloth.blend").write_bytes(b"cloth")
    declared = outputs or {"blend": "o.blend", "report": "r.json", "proxy_cache": "p.pc2", "cache": "c.pc2"}

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get("cloth.smooth").function({"blend": tmp_path / "cloth.blend"}, {"blender": str(blender), **parameters},
                                     {k: tmp_path / v for k, v in declared.items()}, Context())
