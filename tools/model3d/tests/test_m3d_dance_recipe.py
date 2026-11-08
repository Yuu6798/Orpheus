"""Private worker connection contracts; fake outputs do not test Blender quality."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from m3d_helpers import parts_npz, run_cli, stand_in_executable, symlinks_available
from model3d import dance_recipe as dance
from model3d import executor


# 再利用ファイルの名前は非公開の worker 一式のもので、preset の reused で渡す。ここでは合成の名前を使う。
REUSED = ("parts_w.npz", "parts_n.npz", "chains.npz", "torso_cut.npy", "atlas.png", "clip/clip_p.blend")


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def stage_setup(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir()
    source = root / "worker_input.txt"
    source.write_text("original")
    preset = dump(root / "preset.json", {"recipe": "upstream", "source_assets": "source"})
    config = {"bundle": str(root), "preset": str(preset), "python": sys.executable, "blender": str(root / "blender"),
              "threads": 2, "frames": "1:3", "selected_frames": [1, 2, 3], "profile": "preview"}
    inventory = {"files": [{"path": str(source), "sha256": dance.digest(source)}], "runtime": {"fake": True}}
    monkeypatch.setattr(dance, "runtime_versions", lambda *args: {"fake": True})
    return config, inventory, root, tmp_path / "step", source


def rebuild_outputs(argv, status="passed", missing=None):
    job = Path(argv[argv.index("--job") + 1])
    dump(job / "execution.json", [{"step": name, "status": status} for name in dance.REBUILD_STAGES])
    dump(job / "provenance.json", {"starts_from_completed_character": False})
    for name in ("char_o.blend", "parts_geo.npz", "parts_n.npz"):
        if name != missing:
            p = job / "models/mv" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"synthetic-test-only")


def test_rebuild_requires_all_stages_and_preserves_outputs(stage_setup, monkeypatch):
    config, inventory, root, work, source = stage_setup
    def fake(argv, **kwargs):
        rebuild_outputs(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(dance.subprocess, "run", fake)
    receipt = dance.run_stage("rebuild", config, inventory, work, {})
    assert receipt["inventory_checked_before_and_after"] is True
    assert receipt["visual_review_required"] is True
    assert (work / "character.blend").read_bytes() == b"synthetic-test-only"
    assert json.loads((work / "provenance.json").read_text())["starts_from_completed_character"] is False
    assert len(json.loads((work / "execution.json").read_text())) == 12
    with pytest.raises(FileExistsError):
        dance.run_stage("rebuild", config, inventory, work, {})


def test_workers_run_in_utf8_mode(stage_setup, monkeypatch):
    config, inventory, root, work, source = stage_setup
    seen = {}
    def fake(argv, **kwargs):
        seen.update(kwargs["env"])
        rebuild_outputs(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(dance.subprocess, "run", fake)
    dance.run_stage("rebuild", config, inventory, work, {})
    assert seen["PYTHONUTF8"] == "1" and seen["PYTHONUNBUFFERED"] == "1"


@pytest.mark.parametrize("defect", ["missing", "failed_stage", "mutated", "nonzero"])
def test_rebuild_refuses_missing_partial_mutated_and_failed_results(stage_setup, monkeypatch, defect):
    config, inventory, root, work, source = stage_setup
    def fake(argv, **kwargs):
        rebuild_outputs(argv, status="failed" if defect == "failed_stage" else "passed",
                        missing="char_o.blend" if defect == "missing" else None)
        if defect == "mutated":
            source.write_text("mutated")
        return subprocess.CompletedProcess(argv, 9 if defect == "nonzero" else 0)
    monkeypatch.setattr(dance.subprocess, "run", fake)
    with pytest.raises((ValueError, RuntimeError)):
        dance.run_stage("rebuild", config, inventory, work, {})
    assert not (work / "stage_receipt.json").exists()
    assert (work / "worker.log").is_file()


def test_changed_source_fails_before_worker_start(stage_setup, monkeypatch):
    config, inventory, root, work, source = stage_setup
    source.write_text("changed before execution")
    monkeypatch.setattr(dance.subprocess, "run", lambda *a, **k: pytest.fail("worker must not start"))
    with pytest.raises(ValueError, match="Pinned input changed"):
        dance.run_stage("rebuild", config, inventory, work, {})


@pytest.mark.parametrize("outputs", [[1, 2], [1, 1, 3], [1, 3, 2]])
def test_render_refuses_missing_duplicate_or_reordered_frames(stage_setup, monkeypatch, outputs):
    config, inventory, root, work, source = stage_setup
    def fake(argv, **kwargs):
        dump(work / "render_manifest.json", {"status": "passed", "config": {"frames": [1, 2, 3]},
             "outputs": [{"frame": frame, "file": "f.png", "sha256": "0" * 64} for frame in outputs]})
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(dance.subprocess, "run", fake)
    with pytest.raises(ValueError, match="output list"):
        dance.run_stage("render", config, inventory, work, {"source_blend": source})


def test_assembler_uses_isolated_job_and_leaves_render_record_unchanged(stage_setup, monkeypatch):
    config, inventory, root, work, source = stage_setup
    render = root / "previous_render"
    render.mkdir()
    frames = []
    for frame in (1, 2, 3):
        file = render / f"f{frame:04d}.png"
        file.write_bytes(b"fake png")
        frames.append({"frame": frame, "file": file.name, "sha256": dance.digest(file)})
    manifest = dump(render / "render_manifest.json", {"status": "passed", "config": {"frames": [1, 2, 3]}, "outputs": frames})
    before = {p.name: p.read_bytes() for p in render.iterdir()}
    linked = symlinks_available(root)
    def fake(argv, **kwargs):
        job = Path(argv[argv.index("--job") + 1])
        assert job == work / "job"
        if linked:
            assert (job / "f0001.png").is_symlink()
        else:   # without symlink privilege (Windows) the assembler hands over a verified copy
            assert not (job / "f0001.png").is_symlink() and dance.digest(job / "f0001.png") == frames[0]["sha256"]
        (job / "invocation.json").write_text("legacy assembler log")
        (work / "dance.mp4").write_bytes(b"fake mp4")
        dump(work / "dance.json", {"frames": 3, "audio": False})
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(dance.subprocess, "run", fake)
    dance.run_stage("assemble", config, inventory, work, {"render_manifest": manifest})
    assert {p.name: p.read_bytes() for p in render.iterdir()} == before


def test_assembler_copies_verified_frames_when_symlinks_are_unavailable(stage_setup, monkeypatch):
    """Windows without symlink privilege raises OSError (WinError 1314); simulated here."""
    config, inventory, root, work, source = stage_setup
    render = root / "previous_render"
    render.mkdir()
    frames = []
    for frame in (1, 2, 3):
        file = render / f"f{frame:04d}.png"
        file.write_bytes(b"fake png %d" % frame)
        frames.append({"frame": frame, "file": file.name, "sha256": dance.digest(file)})
    manifest = dump(render / "render_manifest.json", {"status": "passed", "config": {"frames": [1, 2, 3]}, "outputs": frames})
    before = {p.name: p.read_bytes() for p in render.iterdir()}

    def no_symlink(self, target, target_is_directory=False):
        raise OSError(1314, "A required privilege is not held by the client")

    def fake(argv, **kwargs):
        job = Path(argv[argv.index("--job") + 1])
        for item in frames:
            copied = job / item["file"]
            assert copied.is_file() and not copied.is_symlink()
            assert dance.digest(copied) == item["sha256"]
        (work / "dance.mp4").write_bytes(b"fake mp4")
        dump(work / "dance.json", {"frames": 3, "audio": False})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(Path, "symlink_to", no_symlink)
    monkeypatch.setattr(dance.subprocess, "run", fake)
    dance.run_stage("assemble", config, inventory, work, {"render_manifest": manifest})
    assert {p.name: p.read_bytes() for p in render.iterdir()} == before


def test_worker_runtime_change_is_not_accepted(stage_setup, monkeypatch):
    config, inventory, root, work, source = stage_setup
    monkeypatch.setattr(dance, "runtime_versions", lambda *args: {"fake": "new-version"})
    with pytest.raises(ValueError, match="runtime versions changed"):
        dance.run_stage("rebuild", config, inventory, work, {})


def test_recipe_binds_sources_but_not_completed_character(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir()
    for name in dance.WORKERS:
        (root / name).write_text("# fake private worker")
    for name in REUSED:
        file = root / "upstream/models/mv" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"accepted input")
    script = root / "upstream/scripts/sash_fix.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fake imported worker")
    completed = root / "upstream/models/mv/char_o.blend"
    completed.write_bytes(b"must not be an input")
    source = root / "source/raw.glb"
    source.parent.mkdir()
    source.write_bytes(b"raw")
    dump(source.parent / "manifest.json", [{"name": source.name, "sha256": dance.digest(source)}])
    dump(root / "keys.json", {"frames": 147, "fps": 30})
    preset = dump(root / "preset.json", {"recipe": "upstream", "source_assets": "source", "light_keys": "keys.json",
                                        "render_profiles": {"preview": {}}, "reused": list(REUSED)})
    sheet = root / "sheet.png"
    sheet.write_bytes(b"sheet")
    face = dump(root / "face.json", {"inputs": {"geometry_npz": "old/parts.npz", "normals_npz": "old/normals.npz",
                                               "part_name": "part", "sheet": "sheet.png"}})
    blender = root / "blender"
    blender.write_bytes(b"fake executable")
    # An absolute venv symlink must remain a symlink path in argv/config.
    # Without symlink privilege (Windows, WinError 1314) only that check is skipped; the rest still runs.
    python_link = root / "venv-python"
    if symlinks_available(tmp_path):
        python_link.symlink_to(sys.executable)
    else:
        python_link = Path(sys.executable)
    monkeypatch.setattr(dance, "runtime_versions", lambda *args: {"fake": True})
    result = dance.build(tmp_path / "recipe", root, preset, face, python_link, blender)
    recipe = dance.read(result["recipe"])
    assert str(completed) not in {v["path"] for v in recipe["inputs"].values()}
    assert str(script) in {v["path"] for v in recipe["inputs"].values()}
    assert str(sheet) in {v["path"] for v in recipe["inputs"].values()}
    assert dance.read(tmp_path / "recipe/connector.json")["python"] == str(python_link)
    assert len(recipe["steps"]) == 5
    assert recipe["steps"][1]["parameters"]["review_note"]
    assert all(s["operation"] == "external.run" for s in recipe["steps"])


@pytest.mark.parametrize("frames", ["1", "1,3,4", "1,1", "3:1", "1:147:0", "1:147:4"])
def test_invalid_video_sampling_is_rejected(frames):
    with pytest.raises(ValueError):
        dance.validate_frames(frames, 147)


def test_external_review_note_continues_downstream_but_retains_needs_review(tmp_path):
    steps = []
    for stage in ("projection", "video"):
        parameters = {"config": {"tool": "fake", "version": "1", "argv": [sys.executable, "-c",
                      "from pathlib import Path;import sys;Path(sys.argv[1]).write_text('fake')", "{artifact}"]}}
        if stage == "projection":
            parameters["review_note"] = "Reference correspondence remains unconfirmed"
        steps.append({"id": stage, "operation": "external.run", "parameters": parameters,
                      "outputs": {"artifact": "artifact.txt"},
                      "inputs": {} if stage == "projection" else {"previous": "$steps.projection.artifact"}})
    recipe = dump(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "fake connection",
                                           "inputs": {}, "steps": steps})
    result = executor.run_recipe(recipe)
    assert result["status"] == "needs_review"
    assert result["steps"]["projection"]["status"] == "needs_review"
    assert result["steps"]["video"]["status"] == "succeeded"
    assert result["candidates"] == ["projection"]


COMMON = """import json,sys,hashlib
from pathlib import Path
args = sys.argv[1:]
def arg(name): return args[args.index(name)+1]
def put(path,data):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data))
def emit(path,data):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
"""


def fake_bundle(tmp_path, monkeypatch):
    """Real subprocesses with fake Blender/data, including legacy CLI argv rules."""
    root = tmp_path / "bundle"
    root.mkdir()
    common = COMMON
    (root / "rebuild_recipe.py").write_text(common + f"""
job=Path(arg('--job'))
emit(job/'models/mv/char_o.blend',b'rebuilt from raw')
emit(job/'models/mv/parts_geo.npz',b'new geometry')
emit(job/'models/mv/parts_n.npz',b'new normals')
put(job/'execution.json',[{{'step':n,'status':'passed'}} for n in {list(dance.REBUILD_STAGES)!r}])
put(job/'provenance.json',{{'starts_from_completed_character':False}})
""")
    (root / "face_project.py").write_text(common + """
config=json.loads(Path(arg('--config')).read_text())
assert Path(config['inputs']['geometry_npz']).read_bytes()==b'new geometry'
assert Path(config['inputs']['normals_npz']).read_bytes()==b'new normals'
job=Path(config['output_dir'])
emit(job/'integrated_basecolor.png',b'projected reference')
put(job/'report.json',{'status':'visual_review_required'})
put(job/'integration_report.json',{'outside_exactly_accepted_fix3':True,'inside_exactly_new_projection':True,'source_files_unchanged':True})
""")
    (root / "apply_texture.py").write_text(common + """
source=Path(arg('--blend'));texture=Path(arg('--texture'));out=Path(arg('--out'))
assert source.is_file(), f'missing actual input: {source}'
assert source != out, 'input/output placeholder collision'
assert source.read_bytes()==b'rebuilt from raw'
assert texture.read_bytes()==b'projected reference'
emit(out,b'integrated character')
put(out.with_suffix('.texture_application.json'),{'replacement_texture_packed':True,'input_files_unchanged':True,'replaced_node_count':2})
""")
    (root / "dance_tool.py").write_text(common + """
job=Path(arg('--job'))
if 'render' in args:
 assert Path(arg('--blend')).read_bytes()==b'integrated character'
 frames=[1,4,7];outputs=[]
 for f in frames:
  path=job/f'f{f:04d}.png';emit(path,b'frame'+str(f).encode())
  outputs.append({'frame':f,'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 put(job/'render_manifest.json',{'status':'passed','config':{'frames':frames},'outputs':outputs})
elif 'assemble' in args:
 manifest=json.loads((job/'render_manifest.json').read_text())
 for item in manifest['outputs']:
  assert (job/item['file']).is_file()
 out=Path(arg('--output'));emit(out,b'fake video')
 put(out.with_suffix('.json'),{'frames':3,'audio':False})
else: raise ValueError(args)
""")
    for name in ("run_upstream_blender.py", "render_stage.py"):
        (root / name).write_text("# synthetic worker placeholder")
    for name in REUSED:
        file = root / "upstream/models/mv" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"accepted input")
    script = root / "upstream/scripts/sash_fix.py"
    script.parent.mkdir(parents=True)
    script.write_text("# synthetic imported worker")
    source = root / "source/raw.glb"
    source.parent.mkdir()
    source.write_bytes(b"raw")
    dump(source.parent / "manifest.json", [{"name": source.name, "sha256": dance.digest(source)}])
    dump(root / "keys.json", {"frames": 9, "fps": 30})
    preset = dump(root / "preset.json", {"recipe": "upstream", "source_assets": "source", "light_keys": "keys.json",
                                        "render_profiles": {"preview": {}}, "reused": list(REUSED),
                                        "face_atlas": "atlas.png"})
    (root / "sheet.png").write_bytes(b"sheet")
    face = dump(root / "face.json", {"inputs": {"geometry_npz": "old/parts.npz", "normals_npz": "old/normals.npz",
                                               "part_name": "part", "sheet": "sheet.png"}})
    # Stand-ins are shebang scripts on POSIX and .cmd files on Windows (shebangs cannot start there).
    # The fake python waits for the real one: on Windows os.execv starts a new process and exits at once,
    # so the worker runner would stop waiting before the real python finishes.
    python = stand_in_executable(root, "fake-python",
                                 "import subprocess,sys,json\n"
                                 "if sys.argv[1]=='-c': print(json.dumps({'python':'fake','packages':{'fake':'1'}}))\n"
                                 f"else: sys.exit(subprocess.call([{sys.executable!r},*sys.argv[1:]]))\n")
    blender = stand_in_executable(root, "blender",
                                  "import sys,runpy\n"
                                  "if '--version' in sys.argv: print('Fake Blender 0.1')\n"
                                  "else:\n script=sys.argv[sys.argv.index('--python')+1]\n"
                                  " sys.argv=[script,*sys.argv[sys.argv.index('--')+1:]]\n runpy.run_path(script,run_name='__main__')\n")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        stand_in_executable(bin_dir, name, f"print('Fake {name} 0.1')\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    return root, preset, face, python, blender


def test_public_cli_recipe_runs_all_five_external_stages_without_placeholder_collision(tmp_path, monkeypatch, capsys):
    from model3d.cli import main
    root, preset, face, python, blender = fake_bundle(tmp_path, monkeypatch)
    recipe_dir = tmp_path / "recipe"
    assert main(["dance-recipe", str(recipe_dir), "--bundle", str(root), "--preset", str(preset),
                 "--face-config", str(face), "--python", str(python), "--blender", str(blender),
                 "--frames", "1:9:3"]) == 0
    preparation = json.loads(capsys.readouterr().out)
    recipe = dance.read(preparation["recipe"])
    for stage in recipe["steps"]:
        assert not (set(stage["inputs"]) & set(stage["outputs"])), "external.run shares placeholder names"
    assert main(["run", str(recipe_dir / "recipe.json"), "--quiet"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "needs_review"
    assert list(result["steps"]) == list(dance.STAGES)
    assert result["steps"]["project_reference"]["status"] == "needs_review"
    assert all(v["status"] == "succeeded" for k, v in result["steps"].items() if k != "project_reference")
    applied = dance.read(result["steps"]["apply_texture"]["artifacts"]["receipt"])
    command = applied["commands"][0]
    source = command[command.index("--blend") + 1]
    target = command[command.index("--out") + 1]
    assert source == result["steps"]["rebuild"]["artifacts"]["blend"]
    assert source != target
    assert Path(result["steps"]["assemble"]["artifacts"]["video"]).read_bytes() == b"fake video"
    record = dance.read(Path(result["run_dir"]) / "run_record.json")
    assert record["inputs"]["inventory"]["sha256"] == dance.digest(recipe_dir / "input_inventory.json")
    assert all(s["reused_from"] is None for s in record["steps"])
    assert dance.read(Path(result["run_dir"]).parent / "index.json")["steps"] == {}


def test_bundle_declares_the_video_tools_its_workers_call():
    assert dance.video_tools({}) == ["ffmpeg", "ffprobe"]
    assert dance.video_tools({"video_tools": ["ffmpeg"]}) == ["ffmpeg"]
    for bad in (["ffprobe"], ["ffmpeg", "ffmpeg"], ["ffmpeg", "mencoder"], "ffmpeg"):
        with pytest.raises(ValueError, match="video_tools"):
            dance.video_tools({"video_tools": bad})


def test_only_the_declared_video_tools_are_required(tmp_path, monkeypatch):
    root, preset, face, python, blender = fake_bundle(tmp_path, monkeypatch)
    for probe in (root / "bin").glob("ffprobe*"):
        probe.unlink()
    with pytest.raises(ValueError, match="ffprobe"):
        dance.runtime_versions(python, blender)
    assert "ffprobe" not in dance.runtime_versions(python, blender, ["ffmpeg"])
    setting = dance.read(preset)
    setting["video_tools"] = ["ffmpeg"]
    dump(preset, setting)
    result = dance.build(tmp_path / "recipe", root, preset, face, python, blender, "1:9:3")
    assert dance.read(tmp_path / "recipe/connector.json")["video_tools"] == ["ffmpeg"]
    assert "ffmpeg" in dance.read(result["inventory"])["runtime"]
    assert "ffprobe" not in dance.read(result["inventory"])["runtime"]


def test_noncanonical_private_layout_is_rejected(tmp_path, monkeypatch):
    root, preset, face, python, blender = fake_bundle(tmp_path, monkeypatch)
    (root / "other-upstream").mkdir()
    setting = dance.read(preset)
    setting["recipe"] = "other-upstream"
    dump(preset, setting)
    with pytest.raises(ValueError, match="bundle/upstream"):
        dance.build(tmp_path / "recipe", root, preset, face, python, blender, "1:9:3")


@pytest.mark.parametrize("reused", [None, [], [""], ["parts_w.npz", "parts_w.npz"], ["../keys.json"], ["missing.npz"]],
                         ids=["absent", "empty", "blank", "duplicate", "outside", "missing"])
def test_bundle_preset_must_list_the_reused_files(tmp_path, monkeypatch, reused):
    root, preset, face, python, blender = fake_bundle(tmp_path, monkeypatch)
    setting = dance.read(preset)
    setting.pop("reused") if reused is None else setting.update(reused=reused)
    dump(preset, setting)
    with pytest.raises(ValueError, match="reused"):
        dance.build(tmp_path / "recipe", root, preset, face, python, blender, "1:9:3")
    assert not (tmp_path / "recipe").exists()


@pytest.mark.parametrize("atlas", [None, "sheet.png"], ids=["absent", "not reused"])
def test_model3d_face_config_needs_the_preset_atlas_among_reused_files(tmp_path, monkeypatch, atlas):
    root, preset, _, python, blender = fake_bundle(tmp_path, monkeypatch)
    face = native_face(root)
    setting = dance.read(preset)
    setting.pop("face_atlas") if atlas is None else setting.update(face_atlas=atlas)
    dump(preset, setting)
    with pytest.raises(ValueError, match="face_atlas"):
        dance.build(tmp_path / "recipe", root, preset, face, python, blender, "1:9:3")
    assert not (tmp_path / "recipe").exists()


NATIVE_REBUILD = COMMON + """import shutil
job=Path(arg('--job'))
emit(job/'models/mv/char_o.blend',b'rebuilt from raw')
shutil.copyfile('synthetic/parts.npz',job/'models/mv/parts_geo.npz')
shutil.copyfile('synthetic/normals.npz',job/'models/mv/parts_n.npz')
put(job/'execution.json',[{'step':n,'status':'passed'} for n in STAGES])
put(job/'provenance.json',{'starts_from_completed_character':False})
"""

NATIVE_APPLY = COMMON + """from PIL import Image
source=Path(arg('--blend'));texture=Path(arg('--texture'));out=Path(arg('--out'))
assert source.read_bytes()==b'rebuilt from raw'
with Image.open(texture) as im:
    rgb=im.convert('RGB')
    assert rgb.getpixel((50,20))==(30,30,220) and rgb.getpixel((10,20))==(128,128,128), 'face steps did not paint the masked half'
emit(out,b'integrated character')
put(out.with_suffix('.texture_application.json'),{'replacement_texture_packed':True,'input_files_unchanged':True,'replaced_node_count':2})
"""


def native_face(root):
    """model3d の顔の工程の材料: 合成の板（部品 head）の npz をコピーする rebuild、灰色の atlas、左右 2 色の設定画、private な設定。"""
    import numpy as np
    from PIL import Image
    parts_npz(root / "synthetic")
    (root / "rebuild_recipe.py").write_text(NATIVE_REBUILD.replace("import shutil", "import shutil\nSTAGES=" + repr(list(dance.REBUILD_STAGES))))
    (root / "apply_texture.py").write_text(NATIVE_APPLY)
    Image.fromarray(np.full((64, 64, 3), 128, dtype=np.uint8)).save(root / "upstream/models/mv/atlas.png")
    sheet = np.zeros((128, 128, 4), dtype=np.uint8)   # atlas（64²）と大きさを変え、マスクの大きさを参照画像から取ることを確かめる
    sheet[:, :64, :3], sheet[:, 64:, :3], sheet[..., 3] = (220, 30, 30), (30, 30, 220), 255
    Image.fromarray(sheet).save(root / "sheet.png")
    return dump(root / "face_model3d.json", {
        "schema": "model3d-face-projection/0.1", "part_name": "head", "reference": "sheet.png",
        "mask": {"shapes": [{"polygon": [[64, 0], [128, 0], [128, 128], [64, 128]]}]},
        "projection": {"view": {"from": "-Y", "center": [0.0, 0.0], "span": 2.0, "size": [64, 64]},
                       "alignment": {"model": [[0, 0], [64, 0]], "reference": [[0, 0], [128, 0]]}}})


NATIVE_STEPS = ["rebuild", "face_mesh", "face_mask", "face_projection", "apply_texture", "render", "assemble"]


def test_model3d_face_config_replaces_the_legacy_worker_with_texture_steps(tmp_path, monkeypatch):
    root, preset, _, python, blender = fake_bundle(tmp_path, monkeypatch)
    face = native_face(root)
    (root / dance.LEGACY_FACE_WORKER).unlink()   # この経路は目元投影の worker を使わない
    result = dance.build(tmp_path / "recipe", root, preset, face, python, blender, "1:9:3")
    recipe = dance.read(result["recipe"])
    assert result["stages"] == NATIVE_STEPS
    operations = {s["id"]: s["operation"] for s in recipe["steps"]}
    assert operations["face_mesh"] == "mesh.import_parts" and operations["face_mask"] == "texture.draw_mask"
    assert operations["face_projection"] == "texture.project_reference"
    apply = next(s for s in recipe["steps"] if s["id"] == "apply_texture")
    assert apply["inputs"]["source_texture"] == "$steps.face_projection.texture"
    assert recipe["inputs"]["face_reference"]["path"] == str((root / "sheet.png").resolve())
    assert recipe["inputs"]["face_atlas"]["path"] == str((root / "upstream/models/mv/atlas.png").resolve())
    assert not any(item["path"].endswith(dance.LEGACY_FACE_WORKER) for item in dance.read(result["inventory"])["files"])
    assert not any("review_note" in s["parameters"] for s in recipe["steps"])


BAD_FACE_CONFIGS = {
    "unknown key": lambda c: {**c, "extra": 1},
    "atlas comes from the bundle preset": lambda c: {**c, "atlas": "sheet.png"},
    "bad view": lambda c: {**c, "projection": {**c["projection"], "view": {"from": "top"}}},
    "one landmark": lambda c: {**c, "projection": {**c["projection"], "alignment": {"model": [[0, 0]], "reference": [[0, 0]]}}},
    "misspelled parameter": lambda c: {**c, "projection": {**c["projection"], "min_facng": 0.5}},
    "occlusion part without a map": lambda c: {**c, "projection": {**c["projection"], "occlusion_part": "head"}},
    "misspelled mask key": lambda c: {**c, "mask": {**c["mask"], "feathr": 3.0}},
    "no shapes": lambda c: {**c, "mask": {"shapes": []}},
    "outside the bundle": lambda c: {**c, "reference": "../outside.png"},
    "empty reference": lambda c: {**c, "reference": ""},
    "a folder": lambda c: {**c, "reference": "upstream"},
    "no part": lambda c: {**c, "part_name": ""},
    "unknown schema": lambda c: {**c, "schema": "model3d-face-projection/0.2"},
    "not an object": lambda c: [c],
}


@pytest.mark.parametrize("change", BAD_FACE_CONFIGS.values(), ids=BAD_FACE_CONFIGS.keys())
def test_model3d_face_config_is_checked_before_building(tmp_path, monkeypatch, change):
    root, preset, _, python, blender = fake_bundle(tmp_path, monkeypatch)
    face = native_face(root)
    (tmp_path / "outside.png").write_bytes(b"not inside the bundle")
    dump(face, change(dance.read(face)))
    with pytest.raises(ValueError):
        dance.build(tmp_path / "recipe", root, preset, face, python, blender, "1:9:3")
    assert not (tmp_path / "recipe").exists()


def test_legacy_face_config_without_inputs_is_named(tmp_path, monkeypatch):
    root, preset, face, python, blender = fake_bundle(tmp_path, monkeypatch)
    dump(face, {"part_name": "head"})
    with pytest.raises(ValueError, match="legacy face config needs inputs"):
        dance.build(tmp_path / "recipe", root, preset, face, python, blender, "1:9:3")


def test_model3d_face_config_has_no_external_projection_stage(stage_setup, monkeypatch):
    config, inventory, root, work, source = stage_setup
    config["face_config"] = str(dump(root / "face.json", {"schema": dance.FACE_SCHEMA}))
    monkeypatch.setattr(dance.subprocess, "run", lambda *a, **k: pytest.fail("worker must not start"))
    with pytest.raises(ValueError, match="no external project_reference"):
        dance.run_stage("project_reference", config, inventory, work, {})


def test_public_cli_runs_the_model3d_face_steps_between_rebuild_and_apply(tmp_path, monkeypatch, capsys):
    root, preset, _, python, blender = fake_bundle(tmp_path, monkeypatch)
    face = native_face(root)
    recipe_dir = tmp_path / "recipe"
    code, _, _ = run_cli("dance-recipe", recipe_dir, "--bundle", root, "--preset", preset, "--face-config", face,
                         "--python", python, "--blender", blender, "--frames", "1:9:3")
    assert code == 0
    code, result, _ = run_cli("run", recipe_dir / "recipe.json", "--quiet")
    assert code == 0 and result["status"] == "succeeded", result
    assert list(result["steps"]) == NATIVE_STEPS
    assert result["steps"]["face_projection"]["observations"][0]["painted_texels"] > 0
    mesh = dance.read(result["steps"]["face_mesh"]["artifacts"]["mesh"])
    assert mesh["name"] == "head" and len(mesh["x_normals"]) == 4
    assert Path(result["steps"]["assemble"]["artifacts"]["video"]).read_bytes() == b"fake video"
