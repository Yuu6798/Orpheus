"""render.passes の分身（phantoms）: 要素の名前・描き方・描く時間・踊り手との距離と手前か奥か・設定の確かめ（Blender なし）と、
工程の受け渡し・要素の一覧（描く時間の外のコマ・near）（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md。"""
import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import contracts
from model3d.blender_adapter import pass_render
from model3d.io import ToolError, atomic_json
from model3d.ops import render_ops
from test_m3d_passes import STAND_IN, _cameras, _fake_bpy, _recipe, _track


class Line:
    """腰が x 方向に 1 秒に 1 m 進む点（高さ 1 m）。"""

    def at(self, name, time):
        time = np.asarray(time, float)
        return np.stack([time, np.zeros_like(time), np.ones_like(time)], axis=-1)


def test_phantom_kinds_are_named_by_index():
    settings = pass_render.merged({"passes": ["char"], "phantoms": {"items": [{"offset": [1, 2], "delay": 0.5}] * 2, "mirror": True}})
    assert pass_render.phantom_kinds(settings) == ["ph0", "ph1", "rph0", "rph1"]
    assert pass_render.all_kinds(settings) == ["char", "ph0", "ph1", "rph0", "rph1"]
    assert [pass_render.phantom_index(k) for k in ("ph0", "rph12", "char", "rchar", "phx")] == [0, 12, None, None, None]
    assert pass_render.base_kind("rph1") == "ph" and pass_render.base_kind("ph0") == "ph"
    assert pass_render.pass_file("rph1", 3, "jpeg") == "rph1/f0003.png"                       # 分身はいつも透けの PNG
    assert pass_render.phantom_kinds(pass_render.merged({})) == []
    # 反転したカメラで描く元の要素の写しは rchar・rshade だけ（分身の rph<i> は分身の描き方で 1 回だけ描く）
    assert [k for k in ("rchar", "rshade", "rph0", "ph0", "room") if pass_render.is_mirrored(k)] == ["rchar", "rshade"]


def test_phantoms_are_drawn_white_with_the_studio_light_on_clear():
    bpy = _fake_bpy()
    body = type("O", (), {"name": "Body", "hide_render": True, "color": None})()
    handles = {"character": [body], "room": [], "disc": None, "world": type("W", (), {"color": None})()}
    pass_render.set_pass(bpy, "ph0", handles, pass_render.merged({"format": "jpeg", "shade": {"rotate_z": 90.0}}))
    sc = bpy.context.scene
    sh = sc.display.shading
    assert not body.hide_render and (sh.light, sh.color_type, sh.single_color) == ("STUDIO", "SINGLE", (1.0, 1.0, 1.0))
    assert sh.use_world_space_lighting and not sh.show_specular_highlight and sh.studiolight_rotate_z == pytest.approx(np.pi / 2)
    assert sc.render.film_transparent and (sc.render.image_settings.file_format, sc.render.image_settings.color_mode) == ("PNG", "RGBA")


def test_the_window_and_the_order_of_the_phantoms():
    assert pass_render.within(5.0, None)
    during = [[1.0, 2.0], [3.0, 4.0]]
    assert [pass_render.within(t, during) for t in (0.9, 1.0 - 1e-7, 1.5, 2.0 - 1e-7, 2.5, 3.0, 3.99)] == \
        [False, True, True, False, False, True, True]                         # 境目は 1e-6 秒前のコマから（plan.stage と同じ）
    items = [{"offset": [0.0, 2.0], "delay": 0.0}, {"offset": [0.0, -2.0], "delay": 0.5}]
    main, order = pass_render.phantom_distances(Line(), "hips", 1.0, items, [1.0, -3.0, 1.0])
    assert main == pytest.approx(3.0)
    assert order[0] == {"i": 0, "distance_m": pytest.approx(5.0), "near": False}                    # 奥へ 2 m
    assert order[1]["near"] and order[1]["distance_m"] == pytest.approx(np.hypot(0.5, 1.0))          # 手前へ 2 m、0.5 秒前の位置


def test_the_phantom_settings_are_checked():
    s = render_ops._pass_settings({"phantoms": {"items": [{"offset": [0.9, 1.2], "delay": 0.6}], "during": [[20.5, 28.0]],
                                                "mirror": True, "point": "hips", "sigma_s": 0.3, "fps": 24}})
    assert s["phantoms"] == {"items": [{"offset": [0.9, 1.2], "delay": 0.6}], "during": [[20.5, 28.0]], "mirror": True,
                             "point": "hips", "sigma_s": 0.3, "fps": 24.0}
    for ghosts, word in [({"items": {"offset": [0, 1]}}, "items"), ({"items": [{"delay": 1}]}, "offset"),
                         ({"items": [{"offset": [0]}]}, "offset"), ({"items": [{"offset": [0, 1], "delay": -1}]}, "delay"),
                         ({"items": [{"offset": [0, 1], "turn": 1}]}, "offset"), ({"during": [[2, 1]]}, "during"),
                         ({"during": []}, "during"), ({"mirror": "yes"}, "mirror"), ({"sigma_s": -1}, "sigma_s"),
                         ({"glow": 1}, "phantoms")]:
        with pytest.raises(ToolError, match=word):
            render_ops._pass_settings({"phantoms": ghosts})


def _bones(count=200, rate=60.0):
    frames = 1.0 + np.arange(count) / rate * 24.0
    hips = [[t, 0.0, 1.0] for t in np.arange(count) / rate]
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": frames.tolist(), "bones": {}, "points": {"hips": hips}}


def test_the_operation_draws_phantoms_in_their_window_and_marks_the_near_ones(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    track = _track([{"T": T, "f": 1 + 0.8 * (T - 1)} for T in range(1, 6)])        # 30 fps、動きは 24 fps
    params = {"blender": blender, "passes": ["char"], "passes_dir": "out_passes",
              "phantoms": {"items": [{"offset": [0.0, 2.0], "delay": 0.0}, {"offset": [0.0, 0.0, -0.8], "delay": 0.0}],
                           "during": [[0.05, 0.12]], "mirror": True, "sigma_s": 0.0}}
    recipe = _recipe(tmp_path, params, track, _cameras(range(1, 6)))
    import json
    data = json.loads(recipe.read_text(encoding="utf-8"))
    with pytest.raises(ToolError, match="bones"):
        render_ops.render_passes({"blend": tmp_path / "dance.blend", "track": tmp_path / "track.json",
                                  "cameras": tmp_path / "cameras_in.json"}, params,
                                 {"passes": tmp_path / "p.json", "cameras": tmp_path / "c.json", "report": tmp_path / "r.json"}, None)
    atomic_json(tmp_path / "bones.json", _bones())
    data["inputs"]["bones"] = {"path": "bones.json"}
    data["steps"][0]["inputs"]["bones"] = "$inputs.bones"
    atomic_json(recipe, data)
    result = executor.run_recipe(recipe, tmp_path / "runs")
    step = result["steps"]["passes"]
    assert result["status"] == "succeeded", step.get("error")
    manifest = contracts.load("passes", step["artifacts"]["passes"])
    assert list(manifest["passes"]) == ["char", "ph0", "ph1", "rph0", "rph1"]
    ph0, ph1 = manifest["passes"]["ph0"], manifest["passes"]["ph1"]
    assert [fr["T"] for fr in ph0["frames"]] == [3, 4] and ph0["outside"] == [1, 2, 5] and ph0["missing"] == []
    # カメラは原点。踊り手は (t, 0, 1)。分身 0 は奥へ 2 m（遠い）、分身 1 は 0.8 m 下（カメラに近い）
    assert [fr["near"] for fr in ph0["frames"]] == [False, False] and [fr["near"] for fr in ph1["frames"]] == [True, True]
    assert ph0["frames"][0]["distance_m"] == pytest.approx(np.sqrt((2 / 30) ** 2 + 4.0 + 1.0))
    assert manifest["passes"]["rph1"]["alpha"] and "near" in manifest["passes"]["rph1"]["frames"][0]
