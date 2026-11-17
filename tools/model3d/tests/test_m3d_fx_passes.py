"""render.passes の効果の要素（fx・反転した rfx）と床の光のたまり（pool）: 床の光のたまりの形と色（試作の式と同じ値）、描く時間、
効果の値の読み方（効果の時刻・時間の進み方・強さのキー、踊りの時刻）、要素ごとの描き方の切り替え、設定の確かめ、代役の Blender での
一覧の outside と骨の通り道の受け渡し（Blender なし）。"""
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import contracts
from model3d.blender_adapter import fx_shapes, pass_render
from model3d.io import ToolError, atomic_json
from model3d.ops import render_ops
from test_m3d_passes import STAND_IN, _cameras, _fake_bpy, _recipe, _track


def test_the_pool_follows_the_prototype():
    quads, colors = pass_render.pool_quads(pass_render.DEFAULTS["pool"])
    radii = np.array([0.0, 0.5, 1.0, 1.6, 2.4, 3.5, 5.0, 7.0, 10.0, 15.0, 24.0, 45.0])      # 試作の台本の式（写し）
    angles = np.linspace(0, 2 * math.pi, 73)
    want_q, want_c = [], []
    for j in range(len(radii) - 1):
        for i in range(72):
            corners = [(radii[j], angles[i]), (radii[j + 1], angles[i]), (radii[j + 1], angles[i + 1]), (radii[j], angles[i + 1])]
            want_q.append([(r * math.cos(a), r * math.sin(a), 0.002) for r, a in corners])
            want_c.append([[math.exp(-r / 4.5) + 0.09 * (1 - math.exp(-r / 25.0))] * 3 for r, _ in corners])
    assert quads.shape == (11 * 72, 4, 3) and np.array_equal(quads, np.array(want_q)) and np.array_equal(colors, np.array(want_c))
    assert colors[0, 0, 0] == 1.0 and colors[-1, 1, 0] < 0.1                     # 中心が明るく、外へ暗くなる
    moved, _ = pass_render.pool_quads({**pass_render.DEFAULTS["pool"], "centre": [1.0, -2.0], "height": 0.01})
    assert np.allclose(moved[..., :2], quads[..., :2] + [1.0, -2.0]) and np.all(moved[..., 2] == 0.01)


def test_the_windows_points_and_values_of_a_frame():
    s = pass_render.merged({"passes": ["char", "fx", "pool"], "mirror": {"passes": ["fx"]},
                            "fx": {"shapes": {"embers": {}, "bursts": {"origin": "chest"}, "trails": {}}, "during": [[1.0, 2.0]]},
                            "pool": {"during": [[1.5, 9.0]]}})
    assert pass_render.all_kinds(s) == ["char", "fx", "pool", "rfx"]
    assert pass_render.base_kind("rfx") == "fx" and pass_render.is_mirrored("rfx") and pass_render.extension("rfx", "jpeg") == "jpg"
    drawn = {k: [t for t in (0.5, 1.0, 1.6, 2.0) if pass_render.drawn_at(k, t, s)] for k in pass_render.all_kinds(s)}
    assert drawn == {"char": [0.5, 1.0, 1.6, 2.0], "fx": [1.0, 1.6], "rfx": [1.0, 1.6], "pool": [1.6, 2.0]}
    assert pass_render.fx_points(s) == ["chest", "hand_l", "hand_r"]
    assert pass_render.fx_points(pass_render.merged({"fx": {"shapes": {"embers": {}, "ring": {}}}})) == []
    fx = pass_render.merged({"fx": {"clock": [[0.0, 0.0], [1.0, 1.0], [2.0, 1.06]], "rate": [[0.0, 1.0], [1.0, 1.0], [1.0, 0.0, "step"]],
                                    "levels": {"embers": [[0.0, 0.0], [2.0, 1.0]], "ring": 0.5}, "frame0": 1.0}})["fx"]
    camera = np.eye(4)
    state = pass_render.fx_state(fx, 1.5, 49.0, 24.0, camera, "points")
    assert state["tf"] == pytest.approx(1.03) and state["rate"] == 0.0 and state["dance_t"] == 2.0
    assert state["levels"] == {"embers": 0.75, "ring": 0.5} and state["camera"] is camera and state["points"] == "points"
    plain = pass_render.fx_state(pass_render.DEFAULTS["fx"], 0.4, 1.0, 24.0, camera, None)
    assert plain["tf"] == 0.4 and plain["rate"] == 1.0 and plain["levels"] == {}           # 効果の時刻はコマの時刻のまま


def test_effects_are_drawn_flat_with_vertex_colours_and_a_black_character():
    bpy = _fake_bpy()
    body = SimpleNamespace(name="Body", type="MESH", hide_render=True, color=None)
    hair = SimpleNamespace(name="Hair", type="CURVES", hide_render=True, color=None)
    floor, disc = SimpleNamespace(name="floor", hide_render=False), SimpleNamespace(name="disc", hide_render=False)
    effect, pool = SimpleNamespace(name="fx", hide_render=False), SimpleNamespace(name="pool", hide_render=False)
    world = SimpleNamespace(color=None)
    handles = {"character": [body, hair], "room": [floor], "disc": disc, "world": world, "fx": effect, "pool": pool}
    settings = pass_render.merged({"passes": ["char", "disc", "fx", "pool"], "format": "jpeg", "quality": 92})
    sc = bpy.context.scene
    seen = {}
    for kind in ("fx", "pool", "rfx", "char", "disc"):
        pass_render.set_pass(bpy, kind, handles, settings)
        sh = sc.display.shading
        seen[kind] = {"body": not body.hide_render, "fx": not effect.hide_render, "pool": not pool.hide_render, "disc": not disc.hide_render,
                      "floor": not floor.hide_render, "light": sh.light, "color": sh.color_type, "transparent": sc.render.film_transparent,
                      "world": world.color, "format": sc.render.image_settings.file_format, "hair": not hair.hide_render}
    flat = {"light": "FLAT", "color": "VERTEX", "transparent": False, "world": (0.0, 0.0, 0.0), "format": "JPEG", "disc": False,
            "floor": False}
    assert seen["fx"] == {**flat, "body": True, "fx": True, "pool": False, "hair": False}   # 人物のメッシュ（黒）と効果
    assert seen["rfx"] == seen["fx"]                                                    # 反転したカメラでも同じ描き方
    assert seen["pool"] == {**flat, "body": False, "fx": False, "pool": True, "hair": False}
    assert seen["char"]["hair"] and seen["char"]["body"]                                  # ほかの要素では描く
    assert not seen["char"]["fx"] and not seen["char"]["pool"] and seen["char"]["color"] == "TEXTURE"
    assert seen["disc"]["disc"] and not seen["disc"]["fx"] and seen["disc"]["color"] == "SINGLE"


def test_the_effect_settings_are_checked():
    s = render_ops._pass_settings({"passes": ["char", "fx", "pool"],
                                   "fx": {"shapes": {"embers": {"count": 10}, "ring": {}}, "levels": {"embers": [[0, 0], [1, 1, "smooth"]]},
                                          "clock": [[0, 0], [2, 2.1]], "rate": 1, "during": [[0, 3]], "fps": 24, "sigmas": {"chest": 0.292}},
                                   "pool": {"centre": [0, 1], "radii": [0, 1, 4], "segments": 12, "falloff": 3, "during": [[2, 5]]}})
    assert s["fx"] == {"shapes": {"embers": {"count": 10}, "ring": {}}, "levels": {"embers": [[0.0, 0.0], [1.0, 1.0, "smooth"]]},
                       "clock": [[0.0, 0.0], [2.0, 2.1]], "rate": 1.0, "during": [[0.0, 3.0]], "fps": 24.0, "sigmas": {"chest": 0.292}}
    assert s["pool"] == {"centre": [0.0, 1.0], "radii": [0.0, 1.0, 4.0], "segments": 12, "falloff": 3.0, "during": [[2.0, 5.0]]}
    bad = [({"passes": ["fx"]}, "fx.shapes"), ({"passes": ["char"], "mirror": {"passes": ["fx"]}}, "fx.shapes"),
           ({"fx": {"shapes": {"smoke": {}}}}, "知らない欄"), ({"fx": {"shapes": {"embers": {"colour": 1}}}}, "embers"),
           ({"fx": {"shapes": {"ring": 2}}}, "ring"), ({"fx": {"levels": {"glow": 1}}}, "fx.levels"),
           ({"fx": {"levels": {"embers": -1}}}, "fx.levels.embers"), ({"fx": {"levels": {"ring": [[1, 0], [0, 1]]}}}, "fx.levels.ring"),
           ({"fx": {"clock": 1.0}}, "fx.clock"), ({"fx": {"rate": [[0, 1, "jump"]]}}, "fx.rate"), ({"fx": {"during": [[2, 1]]}}, "fx.during"),
           ({"fx": {"sigmas": {"chest": -1}}}, "fx.sigmas"), ({"fx": {"glow": 1}}, "fx"), ({"pool": {"radii": [1, 0.5]}}, "pool.radii"),
           ({"pool": {"segments": 2}}, "segments"), ({"pool": {"falloff": 0}}, "falloff"), ({"pool": {"centre": [0]}}, "pool.centre"),
           ({"pool": {"during": []}}, "pool.during"), ({"pool": {"size": 1}}, "pool")]
    for params, word in bad:
        with pytest.raises(ToolError, match=word):
            render_ops._pass_settings(params)


def _bones(points=("chest", "hand_l", "hand_r"), count=100, rate=60.0):
    frames = 1.0 + np.arange(count) / rate * 24.0
    track = [[0.0, 0.0, 1.0]] * count
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": frames.tolist(), "bones": {},
            "points": {name: track for name in points}}


def test_the_operation_draws_effects_and_the_pool_in_their_windows(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    track = _track([{"T": T, "f": 1 + 0.8 * (T - 1)} for T in range(1, 6)])
    params = {"blender": blender, "passes": ["char", "fx", "pool"], "mirror": {"passes": ["fx"]}, "passes_dir": "out_passes",
              "format": "jpeg", "fx": {"shapes": {"sparks": {}, "ring": {}}, "during": [[0.05, 0.12]]}, "pool": {"during": [[0.1, 1.0]]}}
    recipe = _recipe(tmp_path, params, track, _cameras(range(1, 6)))
    outputs = {"passes": tmp_path / "p.json", "cameras": tmp_path / "c.json", "report": tmp_path / "r.json"}
    with pytest.raises(ToolError, match="hand_l, hand_r"):                         # 火花は手の点を読む: 骨の通り道が要る
        render_ops.render_passes({"blend": tmp_path / "dance.blend", "track": tmp_path / "track.json"}, params, outputs, None)
    atomic_json(tmp_path / "chest_only.json", _bones(points=("chest",)))
    with pytest.raises(ToolError, match="無い点"):
        render_ops.render_passes({"blend": tmp_path / "dance.blend", "track": tmp_path / "track.json",
                                  "bones": tmp_path / "chest_only.json"}, params, outputs, None)
    data = json.loads(recipe.read_text(encoding="utf-8"))
    atomic_json(tmp_path / "bones.json", _bones())
    data["inputs"]["bones"] = {"path": "bones.json"}
    data["steps"][0]["inputs"]["bones"] = "$inputs.bones"
    atomic_json(recipe, data)
    result = executor.run_recipe(recipe, tmp_path / "runs")
    step = result["steps"]["passes"]
    assert result["status"] == "succeeded", step.get("error")
    manifest = contracts.load("passes", step["artifacts"]["passes"])
    assert list(manifest["passes"]) == ["char", "fx", "pool", "rfx"]
    fx, rfx, pool = (manifest["passes"][k] for k in ("fx", "rfx", "pool"))
    assert [fr["T"] for fr in fx["frames"]] == [3, 4] and fx["outside"] == [1, 2, 5] and fx["missing"] == []
    assert [fr["T"] for fr in rfx["frames"]] == [3, 4] and rfx["outside"] == [1, 2, 5]
    assert [fr["T"] for fr in pool["frames"]] == [4, 5] and pool["outside"] == [1, 2, 3]
    assert (fx["format"], fx["alpha"], fx["frames"][0]["file"]) == ("jpeg", False, "fx/f0003.jpg")
    assert "outside" not in manifest["passes"]["char"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert "--bones" in report["argv"]                                                 # 点を読む形があれば、Blender に骨の通り道を渡す
    data["steps"][0]["parameters"] = {**params, "fx": {"shapes": {"embers": {}, "ring": {}}}}
    atomic_json(recipe, data)
    same_folder = executor.run_recipe(recipe, tmp_path / "runs2")                    # 設定を変えて同じフォルダへは、描く前に止まる
    assert same_folder["status"] == "failed" and "別の入力で描いたもの" in same_folder["error"]
    data["steps"][0]["parameters"]["passes_dir"] = "out_passes2"
    atomic_json(recipe, data)
    again = executor.run_recipe(recipe, tmp_path / "runs2")
    assert again["status"] == "succeeded"
    report2 = json.loads(Path(again["steps"]["passes"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert "--bones" not in report2["argv"]


def test_the_shapes_module_is_read_by_the_renderer():
    assert pass_render.fx_shapes is fx_shapes and set(fx_shapes.SHAPES) == {"embers", "bursts", "sparks", "trails", "ring"}
