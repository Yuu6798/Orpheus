"""render.passes の床への映り込み（mirror）: 反転したカメラで描く要素の名前・形式・鏡の行列・設定の確かめ・要素ごとの切り替え（Blender
なし）と、工程の受け渡しと要素の一覧（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md。"""
import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import contracts
from model3d.blender_adapter import pass_render
from model3d.io import ToolError
from model3d.ops import render_ops
from test_m3d_passes import STAND_IN, _cameras, _fake_bpy, _recipe, _track


def test_mirrored_kinds_are_named_and_written_like_their_source():
    settings = pass_render.merged({"passes": ["char", "shade", "disc"], "mirror": {"passes": ["char", "shade"]}})
    assert pass_render.all_kinds(settings) == ["char", "shade", "disc", "rchar", "rshade"]
    assert pass_render.base_kind("rchar") == "char" and pass_render.base_kind("rshade") == "shade"
    assert pass_render.base_kind("room") == "room" and pass_render.base_kind("rdisc") == "rdisc"     # 反転できる要素だけ
    assert pass_render.pass_file("rchar", 7, "jpeg") == "rchar/f0007.png"                            # 人物の映り込みも透けの PNG
    assert pass_render.pass_file("rshade", 7, "jpeg") == "rshade/f0007.jpg"
    assert pass_render.merged({})["mirror"] == {"passes": [], "height": 0.0}
    assert pass_render.all_kinds(pass_render.merged({"passes": ["char"]})) == ["char"]                # 書かなければ今までどおり


def test_the_mirror_matrix_flips_the_camera_across_the_floor():
    flip = np.array(pass_render.mirror_values(0.0))
    assert np.array_equal(flip, np.diag([1.0, 1.0, -1.0, 1.0]))                       # 床が 0 なら、試作の鏡の行列そのもの
    camera = np.eye(4)
    camera[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]                                 # 前（−Z）が +Y の水平のカメラ
    camera[:3, 3] = [0.5, -3.0, 1.2]
    mirrored = np.array(pass_render.mirror_values(0.1)) @ camera
    assert np.allclose(mirrored[:3, 3], [0.5, -3.0, 2 * 0.1 - 1.2])                   # 床の面（0.1 m）の下へ
    assert np.allclose(mirrored[:3, :3], np.diag([1, 1, -1]) @ camera[:3, :3])
    assert np.linalg.det(mirrored[:3, :3]) == pytest.approx(-1.0)                       # 向きが裏返った（鏡の）カメラ


def test_a_mirrored_pass_is_drawn_like_its_source():
    bpy = _fake_bpy()
    body = type("O", (), {"name": "Body", "hide_render": False, "color": None})()
    handles = {"character": [body], "room": [], "disc": None, "world": type("W", (), {"color": None})()}
    settings = pass_render.merged({"passes": ["char", "shade"], "format": "jpeg", "mirror": {"passes": ["char", "shade"]}})
    seen = {}
    for kind in ("char", "rchar", "shade", "rshade"):
        pass_render.set_pass(bpy, kind, handles, settings)
        sc = bpy.context.scene
        seen[kind] = (sc.display.shading.light, sc.display.shading.color_type, sc.render.film_transparent,
                      sc.render.image_settings.file_format, sc.render.image_settings.color_mode, body.hide_render)
    assert seen["rchar"] == seen["char"] == ("FLAT", "TEXTURE", True, "PNG", "RGBA", False)
    assert seen["rshade"] == seen["shade"] and seen["shade"][:3] == ("STUDIO", "SINGLE", False)


def test_the_mirror_settings_are_checked():
    s = render_ops._pass_settings({"passes": ["char", "shade"], "mirror": {"passes": ["char"], "height": 0.02}})
    assert s["mirror"] == {"passes": ["char"], "height": 0.02}
    assert render_ops._pass_settings({})["mirror"] == {}
    for mirror, word in [({"passes": ["disc"]}, "mirror.passes"), ({"passes": ["char", "char"]}, "mirror.passes"),
                         ({"passes": "char"}, "mirror.passes"), ({"height": "floor"}, "height"), ({"tilt": 1}, "mirror")]:
        with pytest.raises(ToolError, match=word):
            render_ops._pass_settings({"mirror": mirror})


def test_the_operation_lists_the_mirrored_passes(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    track = _track([{"T": 1, "f": 25}, {"T": 2, "f": 26}])
    params = {"blender": blender, "passes": ["char", "disc"], "format": "jpeg", "mirror": {"passes": ["char", "shade"]},
              "passes_dir": "out_passes"}
    result = executor.run_recipe(_recipe(tmp_path, params, track, _cameras([1, 2])), tmp_path / "runs")
    step = result["steps"]["passes"]
    assert result["status"] == "succeeded", step.get("error")
    manifest = contracts.load("passes", step["artifacts"]["passes"])
    assert list(manifest["passes"]) == ["char", "disc", "rchar", "rshade"]
    rchar, rshade = manifest["passes"]["rchar"], manifest["passes"]["rshade"]
    assert (rchar["format"], rchar["alpha"], rshade["format"], rshade["alpha"]) == ("png", True, "jpeg", False)
    assert [fr["file"] for fr in rchar["frames"]] == ["rchar/f0001.png", "rchar/f0002.png"]
    assert (tmp_path / "out_passes" / "rshade" / "f0002.jpg").read_bytes() == b"rshade 2"
    assert step["observations"][0]["passes"]["rchar"] == {"rendered": 2, "skipped_existing": 0, "frames": 2, "missing": 0}
