"""描いたコマのフォルダの入力の控え（render_inputs・model3d_inputs.json）: render.stage の frames_dir と render.passes の passes_dir で、
とばすコマが前と同じ入力で描いたものかを確かめ、違えば描く前に止めること。控えの読み書き（Blender なし）と、工程の受け渡し（代役の
Blender）を確かめる。リポジトリ外の 3 作目の VRM のダンスで、作り直した踊りの下見に古いコマが残った不具合の種の直し。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import render_inputs
from model3d.io import ToolError, atomic_json
from model3d.ops import render_ops


# ---------- 控えの読み書き ----------

def test_the_scripts_are_the_script_and_the_sibling_scripts_it_imports():
    stage = [p.name for p in render_inputs.script_files(render_ops.RENDER_SCRIPT)]
    passes = [p.name for p in render_inputs.script_files(render_ops.PASS_SCRIPT)]
    assert stage == ["stage_render.py"]
    assert passes == ["fx_shapes.py", "pass_render.py", "point_track.py", "stage_render.py", "vrm_apply_motion.py"]
    assert set(render_inputs.scripts(render_ops.PASS_SCRIPT)) == set(passes)


def test_imports_are_followed_through_every_import_form(tmp_path):
    (tmp_path / "main.py").write_text("import os\nimport a\nfrom b import x\nfrom . import c\nfrom .d import y\n"
                                      "def f():\n    import e\n", encoding="utf-8")
    for name, body in (("a", "import f2\n"), ("b", ""), ("c", ""), ("d", ""), ("e", ""), ("f2", ""), ("unused", "")):
        (tmp_path / f"{name}.py").write_text(body, encoding="utf-8")
    assert [p.name for p in render_inputs.script_files(tmp_path / "main.py")] == ["a.py", "b.py", "c.py", "d.py", "e.py", "f2.py",
                                                                                  "main.py"]


KEY = {"operation": "render.stage", "blend": "b" * 64, "track": None, "settings": {"samples": 24, "resolution": [540, 960]},
       "scripts": {"stage_render.py": "s" * 64}, "blender": "4.5.15"}


def _touch(folder, *names):
    for name in names:
        path = Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")


def test_an_empty_folder_takes_any_inputs_and_the_claim_lists_each_file(tmp_path):
    record, kept = render_inputs.check(tmp_path, KEY, ["f0001.png", "f0002.png"], "test")
    assert kept == 0 and record == {"schema_version": render_inputs.SCHEMA, "keys": {}, "files": {}}
    number = render_inputs.claim(tmp_path, KEY, ["f0002.png", "f0001.png"])
    saved = json.loads((tmp_path / render_inputs.NAME).read_text(encoding="utf-8"))
    assert number == render_inputs.key_id(KEY) and len(number) == 16
    assert saved == {"schema_version": render_inputs.SCHEMA, "keys": {number: KEY}, "files": {"f0001.png": number, "f0002.png": number}}


def test_the_same_inputs_keep_the_frames_that_are_already_there(tmp_path):
    render_inputs.claim(tmp_path, KEY, ["f0001.png", "f0002.png"])
    _touch(tmp_path, "f0001.png")
    assert render_inputs.check(tmp_path, dict(KEY), ["f0001.png", "f0002.png", "f0003.png"], "test")[1] == 1
    assert render_inputs.key_id({**KEY, "settings": {"resolution": [540, 960], "samples": 24}}) == render_inputs.key_id(KEY)


@pytest.mark.parametrize("change, words", [
    ({"blend": "c" * 64}, [".blend"]),
    ({"settings": {"samples": 8, "resolution": [540, 960]}}, ["設定（samples）"]),
    ({"settings": {"samples": 24}}, ["設定（resolution）"]),
    ({"scripts": {"stage_render.py": "t" * 64}}, ["描く台本（stage_render.py）"]),
    ({"blender": "5.2.4"}, ["Blender の版（4.5.15 → 5.2.4）"]),
    ({"track": "d" * 64, "blend": "c" * 64}, [".blend", "トラック"]),
])
def test_other_inputs_stop_before_drawing_and_say_what_changed(tmp_path, change, words):
    render_inputs.claim(tmp_path, KEY, ["f0001.png", "f0002.png"])
    _touch(tmp_path, "f0001.png", "f0002.png")
    with pytest.raises(ToolError, match="別の入力で描いたもの") as error:
        render_inputs.check(tmp_path, {**KEY, **change}, ["f0001.png", "f0002.png"], "render.stage")
    for word in words:
        assert word in str(error.value)
    assert "2 個のコマ（f0001.png, f0002.png）" in str(error.value)


def test_a_frame_without_a_record_stops_because_its_inputs_are_unknown(tmp_path):
    _touch(tmp_path, "f0001.png", "f0007.png")
    with pytest.raises(ToolError, match="控え（model3d_inputs.json）の無いコマが 1 個"):
        render_inputs.check(tmp_path, KEY, ["f0001.png", "f0002.png"], "render.stage")
    assert render_inputs.check(tmp_path, KEY, ["f0002.png", "f0003.png"], "render.stage")[1] == 0     # 描かないコマは見ない


def test_clips_drawn_with_other_inputs_share_the_folder(tmp_path):
    other = {**KEY, "blend": "c" * 64, "track": "e" * 64}
    render_inputs.claim(tmp_path, KEY, ["f0001.png", "f0002.png"])
    render_inputs.claim(tmp_path, other, ["f0003.png", "f0004.png"])
    _touch(tmp_path, "f0001.png", "f0002.png", "f0003.png", "f0004.png")
    assert render_inputs.check(tmp_path, KEY, ["f0001.png", "f0002.png"], "test")[1] == 2
    assert render_inputs.check(tmp_path, other, ["f0003.png", "f0004.png"], "test")[1] == 2
    saved = render_inputs.read(tmp_path)
    assert sorted(saved["keys"]) == sorted([render_inputs.key_id(KEY), render_inputs.key_id(other)])
    with pytest.raises(ToolError, match="控えを書き換えません"):              # 別の入力で控えたコマの控えは書き換えない
        render_inputs.claim(tmp_path, other, ["f0001.png", "f0002.png"])
    assert render_inputs.read(tmp_path) == saved


def test_a_frame_claimed_by_other_inputs_stops_even_before_it_is_drawn(tmp_path):
    other = {**KEY, "blend": "c" * 64}
    render_inputs.check(tmp_path, KEY, ["f0001.png", "f0002.png"], "test", claim=True)   # 確かめて、同じ待ち合わせの中で控える
    assert sorted(render_inputs.read(tmp_path)["files"]) == ["f0001.png", "f0002.png"]
    with pytest.raises(ToolError, match=r"2 個のコマ（f0001.png, f0002.png）は、まだファイルはありませんが、別の入力で描くと控えてあります"
                                         r"（違うもの: \.blend"):
        render_inputs.check(tmp_path, other, ["f0001.png", "f0002.png", "f0003.png"], "render.passes", claim=True)
    assert "f0003.png" not in render_inputs.read(tmp_path)["files"]                     # 止まった方は控えない
    assert render_inputs.check(tmp_path, KEY, ["f0001.png"], "test")[1] == 0              # 同じ入力なら続けられる
    assert render_inputs.check(tmp_path, other, ["f0004.png"], "test", claim=True)[1] == 0  # 重ならないコマは別の入力でもよい


def test_a_record_of_another_kind_is_not_read(tmp_path):
    atomic_json(tmp_path / render_inputs.NAME, {"schema_version": "model3d-other/0.1", "keys": {}, "files": {}})
    with pytest.raises(ToolError, match="読めません"):
        render_inputs.check(tmp_path, KEY, ["f0001.png"], "test")


def test_the_lock_waits_for_another_writer_and_then_gives_up(tmp_path, monkeypatch):
    from model3d.io import directory_lock
    monkeypatch.setattr(render_inputs, "LOCK_WAIT_S", 0.2)
    with directory_lock(tmp_path):
        with pytest.raises(ToolError, match="別のプロセス"):
            render_inputs.claim(tmp_path, KEY, ["f0001.png"])
    assert render_inputs.claim(tmp_path, KEY, ["f0001.png"]) == render_inputs.key_id(KEY)


# ---------- render.stage（代役の Blender） ----------

# 代役の Blender: 並びのコマ（トラックか 1〜3）のうち only のコマに、.blend の中身を書いた PNG を描き（すでにあるファイルはとばす）
STAGE_STAND_IN = r'''
import json, os, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
settings = json.load(open(value("--settings"), encoding="utf-8"))
track = json.load(open(value("--track"), encoding="utf-8")) if value("--track") else None
plan = [(fr["T"], fr["f"]) for fr in track["frames"]] if track else [(1, 1), (2, 2), (3, 3)]
only = set(settings.get("only") or [T for T, _ in plan])
blend = open(value("--blend"), "rb").read()
rendered, existing = [], []
for T, f in plan:
    path = os.path.join(value("--frames-dir"), "f%04d.png" % T)
    if T not in only:
        continue
    if os.path.exists(path):
        existing.append(T)
        continue
    open(path, "wb").write(blend + b" %d" % T)
    rendered.append(T)
    marker = os.path.join(os.path.dirname(os.path.abspath(value("--frames-dir"))), "fail_after_two")
    if len(rendered) == 2 and os.path.exists(marker):          # 2 コマ描いたところで止まる（止まった描画の続きの確かめ）
        os.remove(marker)
        sys.exit(3)
json.dump({"schema_version": "model3d-stage-render/0.1", "planned": [{"T": T, "f": f} for T, f in plan], "rendered": rendered,
           "skipped_existing": existing, "fps": (track or {}).get("fps", 24), "resolution": settings.get("resolution", [1080, 1920]),
           "settings": settings}, open(value("--report"), "w"))
'''


def _track(Ts):
    return {"schema_version": "model3d-stage-track/0.1", "fps": 30, "frames": [{"T": T, "f": T} for T in Ts]}


def _stage_recipe(folder, params, blend=b"dance r1", Ts=(1, 2, 3)):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dance.blend").write_bytes(blend)
    inputs, step_inputs = {"dance": {"path": "dance.blend"}}, {"blend": "$inputs.dance"}
    if Ts is not None:
        atomic_json(folder / "track.json", _track(Ts))
        inputs["track"] = {"path": "track.json"}
        step_inputs["track"] = "$inputs.track"
    atomic_json(folder / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "render", "inputs": inputs,
                                         "steps": [{"id": "render", "operation": "render.stage", "inputs": step_inputs,
                                                    "parameters": params, "outputs": {"frames": "frames.json", "report": "render.json"}}]})
    return folder / "recipe.json"


def _run(recipe, runs):
    from model3d import executor
    return executor.run_recipe(recipe, runs)


def _fails(result, pattern):
    """工程が止まり、誤りの文が pattern に合うこと（レシピの実行は、止まった工程を記録して返す）。"""
    import re
    error = result["steps"]["render"]["error"]
    assert result["status"] == "failed" and re.search(pattern, error), error


def _ok(result):
    """工程が通ったこと。観測を返す。"""
    assert result["status"] == "succeeded", result["steps"]["render"]["error"]
    return result["steps"]["render"]["observations"][0]


def test_render_stage_draws_again_only_with_the_same_inputs(tmp_path):
    frames = tmp_path / "frames"
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAGE_STAND_IN)), "frames_dir": str(frames), "only": [1, 2]}
    first = _ok(_run(_stage_recipe(tmp_path / "a", params), tmp_path / "runs"))
    assert first["rendered"] == 2 and first["inputs"]["kept"] == 0
    saved, number = render_inputs.read(frames), first["inputs"]["key"]
    assert saved["files"] == {"f0001.png": number, "f0002.png": number} and saved["keys"][number]["operation"] == "render.stage"
    assert "only" not in saved["keys"][number]["settings"] and saved["keys"][number]["blender"] == "4.5.15"
    params.pop("only")                                   # 同じ入力で続きから: 描いた 2 コマはとばし、残りの 1 コマだけ描く
    again = _ok(_run(_stage_recipe(tmp_path / "a", params), tmp_path / "runs"))
    assert again["rendered"] == 1 and again["skipped_existing"] == 2 and again["inputs"] == {"key": number, "kept": 2}
    assert sorted(render_inputs.read(frames)["files"]) == ["f0001.png", "f0002.png", "f0003.png"]
    before = {p.name: p.read_bytes() for p in frames.glob("f*.png")}
    remade = _run(_stage_recipe(tmp_path / "a", params, blend=b"dance r2"), tmp_path / "runs")   # 作り直した踊りは、描く前に止める
    _fails(remade, r"3 個のコマ（f0001.png, f0002.png, f0003.png）は、別の入力で描いたもの.*\.blend")
    assert {p.name: p.read_bytes() for p in frames.glob("f*.png")} == before
    _fails(_run(_stage_recipe(tmp_path / "a", {**params, "samples": 8}), tmp_path / "runs"), r"設定（samples）")


def test_render_stage_stops_on_frames_drawn_before_the_record_existed(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "f0002.png").write_bytes(b"old")
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAGE_STAND_IN)), "frames_dir": str(frames)}
    _fails(_run(_stage_recipe(tmp_path / "a", params), tmp_path / "runs"), "控え（model3d_inputs.json）の無いコマが 1 個")
    assert not (frames / "f0001.png").exists()            # Blender を起動する前に止まる


def test_render_stage_clips_with_their_own_inputs_share_one_folder(tmp_path):
    frames = tmp_path / "frames"
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAGE_STAND_IN)), "frames_dir": str(frames)}
    _ok(_run(_stage_recipe(tmp_path / "clip1", params, blend=b"clip 1", Ts=(1, 2)), tmp_path / "runs"))
    _ok(_run(_stage_recipe(tmp_path / "clip2", params, blend=b"clip 2", Ts=(3, 4)), tmp_path / "runs"))
    again = _ok(_run(_stage_recipe(tmp_path / "clip1", params, blend=b"clip 1", Ts=(1, 2)), tmp_path / "runs"))
    assert again["inputs"]["kept"] == 2 and len(render_inputs.read(frames)["keys"]) == 2
    assert (frames / "f0003.png").read_bytes() == b"clip 2 3"


def test_render_stage_without_a_track_records_the_frames_after_drawing(tmp_path):
    frames = tmp_path / "frames"
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAGE_STAND_IN)), "frames_dir": str(frames)}
    assert _ok(_run(_stage_recipe(tmp_path / "a", params, Ts=None), tmp_path / "runs"))["inputs"]["kept"] == 0
    assert sorted(render_inputs.read(frames)["files"]) == ["f0001.png", "f0002.png", "f0003.png"]
    assert _ok(_run(_stage_recipe(tmp_path / "a", params, Ts=None), tmp_path / "runs"))["inputs"]["kept"] == 3
    _fails(_run(_stage_recipe(tmp_path / "a", params, blend=b"dance r2", Ts=None), tmp_path / "runs"), "別の入力で描いたもの")


def test_render_stage_without_a_track_records_the_frames_written_before_a_failure(tmp_path):
    frames = tmp_path / "frames"
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAGE_STAND_IN)), "frames_dir": str(frames)}
    (tmp_path / "fail_after_two").write_bytes(b"")
    _fails(_run(_stage_recipe(tmp_path / "a", params, Ts=None), tmp_path / "runs"), "終了コード 3")
    assert sorted(p.name for p in frames.glob("f*.png")) == ["f0001.png", "f0002.png"]
    assert sorted(render_inputs.read(frames)["files"]) == ["f0001.png", "f0002.png"]        # 止まる前に書いたコマも控える
    again = _ok(_run(_stage_recipe(tmp_path / "a", params, Ts=None), tmp_path / "runs"))  # 同じ入力なら続きから描ける
    assert again["inputs"]["kept"] == 2 and again["rendered"] == 1


def test_render_stage_in_its_own_step_folder_writes_the_record_there(tmp_path):
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAGE_STAND_IN))}
    result = _run(_stage_recipe(tmp_path / "a", params), tmp_path / "runs")
    _ok(result)
    folder = Path(result["steps"]["render"]["artifacts"]["frames"]).parent / "frames"
    assert sorted(render_inputs.read(folder)["files"]) == ["f0001.png", "f0002.png", "f0003.png"]


# ---------- render.passes（代役の Blender） ----------

PASSES_STAND_IN = r'''
import json, os, sys
from model3d.blender_adapter import pass_render
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
settings = pass_render.merged(json.load(open(value("--settings"), encoding="utf-8")))
track = json.load(open(value("--track"), encoding="utf-8"))
plan = [(fr["T"], fr["f"]) for fr in track["frames"]]
only = set(settings["only"] or [T for T, _ in plan])
rendered = {k: [] for k in pass_render.all_kinds(settings)}
existing = {k: [] for k in pass_render.all_kinds(settings)}
for T, f in plan:
    if T not in only:
        continue
    for kind in pass_render.all_kinds(settings):
        path = os.path.join(value("--passes-dir"), pass_render.pass_file(kind, T, settings["format"]))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            existing[kind].append(T)
            continue
        open(path, "wb").write(b"%s %d" % (kind.encode(), T))
        rendered[kind].append(T)
frames = [pass_render.camera_entry(T, f, [[1, 0, 0, 0], [0, 1, 0, -3], [0, 0, 1, 1], [0, 0, 0, 1]], 35.0, (0.0, 0.0), 24.0,
                                   settings["resolution"], 1.0, [0.0, 0.0, 1.0]) for T, f in plan]
json.dump({"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": settings["resolution"], "fps": 30, "frames": frames},
          open(value("--cameras-out"), "w"))
json.dump({"schema_version": "model3d-pass-render/0.1", "planned": [{"T": T, "f": f} for T, f in plan], "rendered": rendered,
           "skipped_existing": existing, "fps": 30, "resolution": settings["resolution"], "camera": {}, "objects": {},
           "settings": settings, "phantoms": {"count": 0, "dance_fps": 24.0}}, open(value("--report"), "w"))
'''


def _cameras(Ts, focal=2000.0):
    return {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [1080, 1920],
            "frames": [{"T": T, "matrix": np.eye(4).tolist(), "focal_px": focal, "principal_px": [540.0, 960.0]} for T in Ts]}


def _passes_recipe(folder, params, cameras=None, image=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dance.blend").write_bytes(b"dance")
    atomic_json(folder / "track.json", _track((1, 2)))
    atomic_json(folder / "cameras_in.json", cameras or _cameras((1, 2)))
    inputs = {"dance": {"path": "dance.blend"}, "track": {"path": "track.json"}, "cameras": {"path": "cameras_in.json"}}
    step_inputs = {"blend": "$inputs.dance", "track": "$inputs.track", "cameras": "$inputs.cameras"}
    if image is not None:
        (folder / "smile.png").write_bytes(image)
    atomic_json(folder / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "passes", "inputs": inputs,
                                         "steps": [{"id": "render", "operation": "render.passes", "inputs": step_inputs,
                                                    "parameters": params,
                                                    "outputs": {"passes": "passes.json", "cameras": "cameras.json",
                                                                "report": "report.json"}}]})
    return folder / "recipe.json"


def test_render_passes_draws_again_only_with_the_same_inputs(tmp_path):
    folder = tmp_path / "passes"
    params = {"blender": str(stand_in_executable(tmp_path, "blender", PASSES_STAND_IN)), "passes_dir": str(folder),
              "passes": ["char", "shade"]}
    first = _ok(_run(_passes_recipe(tmp_path / "a", params), tmp_path / "runs"))
    number = first["inputs"]["key"]
    assert render_inputs.read(folder)["files"] == {f"{k}/f000{T}.png": number for k in ("char", "shade") for T in (1, 2)}
    more = {**params, "passes": ["char", "shade", "disc"]}  # 要素を足しても、描いた要素はそのまま使い、足した要素だけ描く
    again = _ok(_run(_passes_recipe(tmp_path / "a", more), tmp_path / "runs"))
    assert again["inputs"] == {"key": number, "kept": 4}
    assert again["passes"]["disc"]["rendered"] == 2 and again["passes"]["char"]["skipped_existing"] == 2
    moved = _run(_passes_recipe(tmp_path / "a", more, cameras=_cameras((1, 2), focal=2400.0)), tmp_path / "runs")
    _fails(moved, r"6 個のコマ.*別の入力で描いたもの.*カメラ（cameras）")
    _fails(_run(_passes_recipe(tmp_path / "a", {**more, "antialiasing": "8"}), tmp_path / "runs"), r"設定（antialiasing）")


def test_render_passes_records_the_expression_pictures_by_their_contents(tmp_path):
    folder = tmp_path / "passes"
    expressions = {"objects": ["Head"], "images": {"smile": "smile.png"}, "times": [[0.0, "smile"]]}
    params = {"blender": str(stand_in_executable(tmp_path, "blender", PASSES_STAND_IN)), "passes_dir": str(folder),
              "passes": ["char"], "expressions": expressions}
    _ok(_run(_passes_recipe(tmp_path / "a", params, image=b"smile r1"), tmp_path / "runs"))
    kept = _ok(_run(_passes_recipe(tmp_path / "b", params, image=b"smile r1"), tmp_path / "runs"))   # 同じ中身の絵なら、置き場が違っても同じ
    assert kept["inputs"]["kept"] == 2
    _fails(_run(_passes_recipe(tmp_path / "a", params, image=b"smile r2"), tmp_path / "runs"), r"設定（expressions）")
