"""別々に骨へ載せた動きを 1 本に組む工程（motion.join_clips）: 区間の確かめと元のコマの表（Blender なし）と、工程の受け渡し（代役の
Blender）を確かめる。本物の Blender 4.5.15・5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import join_clips
from model3d.io import ToolError, atomic_json


def _segments():
    return [{"clip": "walk", "out": [1, 30], "offset_s": 0.5}, {"clip": "spin", "out": [31, 60], "offset_s": 1.0},
            {"clip": "walk", "out": [61, 72], "offset_s": 2.0}]


def test_segments_must_tile_the_output_from_frame_one():
    assert join_clips.check_segments(_segments(), {"walk", "spin"}) == _segments()
    assert join_clips.clip_order(_segments()) == ["walk", "spin"]           # 最初が打ち直す .blend の本
    assert join_clips.cut_frames(_segments()) == [31, 61]                   # 区間の 2 つ目からの最初のコマ
    for bad, message in (
            ([], "1 つ以上"), ("walk", "1 つ以上"),
            ([{"clip": "walk", "out": [2, 30], "offset_s": 0}], "1 コマ目から"),                       # 1 から始めない
            ([{"clip": "walk", "out": [1, 30], "offset_s": 0}, {"clip": "spin", "out": [32, 40], "offset_s": 0}], "31 コマ目から"),
            ([{"clip": "walk", "out": [1, 30], "offset_s": 0}, {"clip": "spin", "out": [30, 40], "offset_s": 0}], "31 コマ目から"),
            ([{"clip": "walk", "out": [5, 1], "offset_s": 0}], "最初 ≤ 最後"), ([{"clip": "walk", "out": [1.0, 3], "offset_s": 0}], "整数"),
            ([{"clip": "walk", "out": [1, 3], "offset_s": float("nan")}], "有限"), ([{"clip": "walk", "out": [1, 3], "offset_s": True}], "有限"),
            ([{"clip": "", "out": [1, 3], "offset_s": 0}], "clip"), ([{"clip": "walk", "out": [1, 3]}], "offset_s: 秒"),
            ([{"clip": "walk", "out": [1, 3], "offset_s": 0, "speed": 2}], "offset_s: 秒")):
        with pytest.raises(ValueError, match=message):
            join_clips.check_segments(bad)
    with pytest.raises(ValueError, match="無い本"):
        join_clips.check_segments(_segments(), {"walk"})
    with pytest.raises(ValueError, match="使わない本.*extra"):
        join_clips.check_segments(_segments(), {"walk", "spin", "extra"})


def test_source_table_places_each_clip_and_holds_outside_its_keys():
    # walk のキーは 1〜48（24 fps で 2 秒）、spin のキーは 101〜124（1 秒）
    table, report = join_clips.source_table(_segments(), {"walk": (1, 48), "spin": (101, 124)}, 24.0)
    assert len(table) == 72 and [index for index, _ in table] == [0] * 30 + [1] * 30 + [2] * 12
    sources = [source for _, source in table]
    # walk の 0 秒目を出力の 0.5 秒目（13 コマ目）に置く: 1〜12 コマ目は最初の姿勢で止まり、13 コマ目からキーの 1, 2, …
    assert sources[:14] == [1] * 13 + [2]
    assert sources[29] == 1 + 17                                            # 30 コマ目 = 1.208 秒 → walk の 0.708 秒 = 17 コマ目
    # spin の 0 秒目を 1.0 秒目（25 コマ目）に置く: 31 コマ目は spin の 6 コマ目、キーの 24 コマを超えたら最後の姿勢
    assert sources[30] == 101 + 6 and sources[48] == 124 and sources[59] == 124
    # 同じ本を 2 度使ってよい（walk を 2.0 秒目に置き直す）
    assert sources[60:62] == [1 + 12, 1 + 13]
    assert report[0] == {"clip": "walk", "out": [1, 30], "offset_s": 0.5, "source": [1, 18], "held_start": 12, "held_end": 0}
    assert report[1]["held_start"] == 0 and report[1]["held_end"] == 12 and report[1]["source"] == [107, 124]
    assert report[2]["source"] == [13, 24]


def test_source_table_uses_the_private_script_rounding():
    # 出力のコマ（0 始まり j）の元のコマ = round((j ÷ fps − offset_s) × fps)。Python の round（ちょうど .5 は偶数へ）と浮動小数の
    # 端数まで、2D の合成と試作の台本と同じ式にしてある（式の順を変えると、切り替えの前後でコマが 1 つずれることがある）
    for fps, offset in ((30.0, 0.075), (30.0, 0.05), (24.0, 1.0 / 48.0), (29.97, 3.3)):
        segments = [{"clip": "c", "out": [1, 200], "offset_s": offset}]
        table, _ = join_clips.source_table(segments, {"c": (0, 999)}, fps)
        expected = [min(max(int(round((j / fps - offset) * fps)), 0), 999) for j in range(200)]
        assert [source for _, source in table] == expected, (fps, offset)


def test_key_range_skips_curves_without_keys():
    from types import SimpleNamespace as N

    def curve(*frames):
        return N(keyframe_points=[N(co=(float(frame), 0.0)) for frame in frames])

    curves = [curve(1, 24, 48), curve(), curve(5, 52), curve()]
    assert join_clips.keyed_curves(curves) == [curves[0], curves[2]]          # キーの無い F カーブは読まず、書き直さない
    assert join_clips.key_range(curves) == (1, 52)
    assert join_clips.key_range([curve(0.4, 99.6)]) == (0, 100)              # 小数のコマは丸める
    with pytest.raises(SystemExit, match="キーがありません"):
        join_clips.key_range([curve(), curve()])


class _Removable(list):
    """remove を持つ並び（Blender の modifiers・nla_tracks の代わり）。"""


def test_rewritten_curves_lose_their_modifiers():
    from types import SimpleNamespace as N
    noisy = N(modifiers=_Removable(["NOISE", "GENERATOR"]))
    plain = N(modifiers=_Removable())
    assert join_clips.strip_modifiers([noisy, plain]) == 2                  # 読んだ値には掛かっているので、二重に掛けない
    assert noisy.modifiers == [] and join_clips.strip_modifiers([noisy, plain]) == 0


def test_bind_action_makes_the_armature_play_the_rewritten_action():
    from types import SimpleNamespace as N

    class Animation:
        def __init__(self, action, tracks=()):
            self.action, self.action_slot, self.nla_tracks = action, None, _Removable(tracks)

    joined, other = N(name="joined", slots=["OBJECT"]), N(name="other", slots=[])
    # 付いていない動き（F カーブのいちばん多いアクションでない動き）が付いていれば、付け替えて最初のスロットに。NLA の層は外す
    animation = Animation(other, tracks=["walk", "idle"])
    armature = N(animation_data_create=lambda: animation)
    assert join_clips.bind_action(armature, joined) == (True, 2)
    assert animation.action is joined and animation.action_slot == "OBJECT" and animation.nla_tracks == []
    # もう付いていれば、そのまま（スロットにも触らない）
    animation = Animation(joined)
    animation.action_slot = "KEEP"
    assert join_clips.bind_action(N(animation_data_create=lambda: animation), joined) == (False, 0)
    assert animation.action_slot == "KEEP"
    # 動きの無いアーマチュア・スロットの無い古い形式のアクション
    animation = Animation(None)
    assert join_clips.bind_action(N(animation_data_create=lambda: animation), other) == (True, 0)
    assert animation.action is other and animation.action_slot is None


def test_muted_curves_are_named():
    from types import SimpleNamespace as N
    curves = [N(data_path='pose.bones["a"].location', array_index=0, mute=False, group=None),
              N(data_path='pose.bones["a"].location', array_index=1, mute=True, group=None),
              N(data_path='pose.bones["b"].rotation_quaternion', array_index=2, mute=False, group=N(mute=True)),
              N(data_path='pose.bones["c"].rotation_quaternion', array_index=0, mute=False, group=N(mute=False))]
    assert join_clips.muted_curves(curves) == ['pose.bones["a"].location[1]', 'pose.bones["b"].rotation_quaternion[2]']
    assert join_clips.muted_curves(curves[:1]) == []


def test_bone_channel_reads_escaped_bone_names():
    assert join_clips.bone_channel('pose.bones["mixamorig:Hips"].location') == ("mixamorig:Hips", ".location")
    assert join_clips.bone_channel('pose.bones["a\\"]b\\\\c"].rotation_quaternion') == ('a"]b\\c', ".rotation_quaternion")
    assert join_clips.bone_channel('["model3d_points_frames"]') is None
    assert join_clips.bone_channel('pose.bones["broken') is None


# 代役の Blender: 設定を読み、出力を書いて、受け取った argv と設定を報告に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 4.5.15 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"joined")
json.dump({"schema_version": "model3d-motion-join/0.1", "frames": [1, 72], "fps": 24.0, "cuts": [31, 61],
           "clips": [{"name": "walk"}, {"name": "spin"}],
           "segments": [{"clip": "walk", "held_start": 12, "held_end": 0}, {"clip": "spin", "held_start": 0, "held_end": 12},
                        {"clip": "walk", "held_start": 0, "held_end": 0}],
           "points_frames_removed": True, "action_bound": False, "nla_tracks_removed": 0, "modifiers_removed": 3,
           "argv": sys.argv[1:], "settings": settings}, open(value("--report"), "w"))
'''


def _recipe(tmp_path, parameters, inputs=("walk", "spin")):
    for name in inputs:
        (tmp_path / f"{name}.blend").write_bytes(name.encode())
    step = {"id": "join", "operation": "motion.join_clips", "inputs": {name: f"$inputs.{name}" for name in inputs},
            "parameters": parameters, "outputs": {"blend": "joined.blend", "report": "join.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "join",
                                           "inputs": {name: {"path": f"{name}.blend"} for name in inputs}, "steps": [step]})
    return tmp_path / "recipe.json"


def test_join_clips_passes_the_clips_and_the_segments_to_blender(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    recipe = _recipe(tmp_path, {"blender": blender, "segments": _segments(), "bone_prefix": "rig:", "align": "none"})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["join"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["argv"][report["argv"].index("--python") + 1]).name == "join_clips.py"
    assert "--factory-startup" in report["argv"] and "--python-expr" not in report["argv"]          # アドオンは要らない
    settings = report["settings"]
    assert {name: Path(path).name for name, path in settings["clips"].items()} == {"spin": "spin.blend", "walk": "walk.blend"}
    assert settings["segments"] == _segments() and settings["hips"] == "rig:Hips" and settings["align"] == "none"
    assert settings["fps"] is None and settings["armature"] == ""
    tail = report["argv"][report["argv"].index("--") + 1:]
    assert Path(tail[tail.index("--out-blend") + 1]) == Path(step["artifacts"]["blend"])
    observation = step["observations"][0]
    assert observation["kind"] == "join_clips" and observation["cuts"] == [31, 61] and observation["clips"] == ["walk", "spin"]
    assert observation["held"] == [[12, 0], [0, 12], [0, 0]] and observation["points_frames_removed"] is True
    assert observation["action_bound"] is False and observation["nla_tracks_removed"] == 0 and observation["modifiers_removed"] == 3
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["join"]["reused_from"] is None     # 毎回実行する
    # fps と armature を渡すと、そのまま設定に入る
    recipe = _recipe(tmp_path, {"blender": blender, "segments": _segments(), "fps": 24, "armature": "Rig"})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    settings = json.loads(Path(result["steps"]["join"]["artifacts"]["report"]).read_text(encoding="utf-8"))["settings"]
    assert settings["fps"] == 24.0 and settings["armature"] == "Rig" and settings["hips"] == "mixamorig:Hips"
    assert settings["align"] == "mean"


@pytest.mark.parametrize("parameters, inputs, message", [
    ({"wobble": 1}, ("walk", "spin"), "知らないパラメーター"),
    ({}, ("walk", "spin"), "segments"),
    ({"segments": [{"clip": "walk", "out": [1, 9], "offset_s": 0}]}, ("walk", "spin"), "使わない本"),
    ({"segments": [{"clip": "jump", "out": [1, 9], "offset_s": 0}]}, ("walk",), "無い本"),
    ({"segments": [{"clip": "walk", "out": [0, 9], "offset_s": 0}]}, ("walk",), "1 コマ目から"),
    ({"segments": [{"clip": "walk", "out": [1, 9], "offset_s": 0}], "fps": 0}, ("walk",), "fps"),
    ({"segments": [{"clip": "walk", "out": [1, 9], "offset_s": 0}], "align": "median"}, ("walk",), "align"),
    ({"segments": [{"clip": "walk", "out": [1, 9], "offset_s": 0}], "armature": 3}, ("walk",), "armature"),
    ({"segments": [{"clip": "walk", "out": [1, 9], "offset_s": 0}], "bone_prefix": 3}, ("walk",), "bone_prefix"),
    ({"segments": [{"clip": "walk", "out": [1, 9], "offset_s": 0}]}, (), "1 つ以上挙げて"),
    ({"segments": [{"clip": "walk", "out": [1, 9], "offset_s": 0}], "blender": None}, ("walk",), "Blender"),
])
def test_join_clips_rejects_bad_settings(tmp_path, parameters, inputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    parameters = {"blender": str(blender), **parameters}
    if parameters["blender"] is None:
        del parameters["blender"]
    for name in inputs:
        (tmp_path / f"{name}.blend").write_bytes(b"blend")

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get("motion.join_clips").function({name: tmp_path / f"{name}.blend" for name in inputs}, parameters,
                                          {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context())
    with pytest.raises(ToolError, match="report"):                         # 出力がそろっていなければ止まる
        get("motion.join_clips").function({"walk": tmp_path / "walk.blend"}, {"blender": str(blender)},
                                          {"blend": tmp_path / "o.blend"}, Context())
