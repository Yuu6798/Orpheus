import json
import shutil
import subprocess
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import make_project, run_cli, spec_dict, write_spec  # noqa: E402
from mv_pipeline import fx, media  # noqa: E402
from mv_pipeline.fx import compare, probe, render, runs  # noqa: E402
from mv_pipeline.fx import assets as fxassets  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.io import PipelineError, read_json, sha256  # noqa: E402


@pytest.fixture(scope="module")
def shot(tmp_path_factory):
    """デモ素材（320x180・1.5 秒・12fps）と、その 1 回目の fx render の結果。"""
    root = tmp_path_factory.mktemp("shot")
    assert run_cli("fx", "demo", root, "--width", "320", "--height", "180", "--duration", "1.5", "--fps", "12")[0] == 0
    code, result, err = run_cli("fx", "render", root / "spec.json", "--out", root / "runs")
    assert code == 0, err
    return root, result


def test_render_writes_four_variants_a_grid_and_a_complete_manifest(shot):
    root, result = shot
    run_dir = root / "runs" / result["run_id"]
    assert result["reused"] is False and result["status"] == "succeeded" and result["technical_pass"] is True
    assert result["interval"] == {"start": 0.0, "duration": 1.5, "fps": 12.0, "frames": 18} and result["size"] == [320, 180]
    assert set(result["variants"]) == set(fx.VARIANTS) and result["grid"] == str(run_dir / "grid.mp4")
    assert sorted(p.name for p in run_dir.iterdir()) == sorted(
        ["manifest.json", "spec.json", "render.log", "grid.mp4"] + [f"{v}.mp4" for v in fx.VARIANTS])
    manifest = read_json(run_dir / "manifest.json")
    spec_file = root / "spec.json"
    assert manifest["schema_version"] == "fx-run/0.1" and manifest["status"] == "succeeded" and manifest["error"] is None
    assert manifest["run_id"].endswith("-" + manifest["key"][:8]) and len(manifest["run_id"]) == len("20261024T120000Z-") + 8
    assert manifest["run_id"][8] == "T" and manifest["run_id"][15] == "Z"
    assert manifest["spec"]["sha256"] == sha256(spec_file) == sha256(run_dir / "spec.json")
    sp = fxspec.load_spec(spec_file)
    assert manifest["spec"]["normalized_sha256"] and manifest["seed"] == 7
    assert {r: a["sha256"] for r, a in manifest["assets"].items()} == {r: a["sha256"] for r, a in fxassets.describe(sp).items()}
    assert manifest["engine"] == {"name": "fx", "version": fx.VERSION, "schema_version": fx.SCHEMA_VERSION,
                                  "fingerprint": fx.fingerprint()}
    assert {"python", "numpy", "opencv", "pillow", "ffmpeg"} <= set(manifest["libraries"])
    assert manifest["interval"] == result["interval"] and manifest["output"]["size"] == [320, 180]
    for item in manifest["variants"].values():
        video = run_dir / item["path"]
        assert item["frames"] == 18 and item["technical_pass"] is True and item["sha256"] == sha256(video)
        assert item["checks"]["failures"] == [] and item["checks"]["audio"]["decoded_s"] == pytest.approx(1.5, abs=0.11)
        assert item["empty"] is False and "camera" in item["features"]
    assert "light" in manifest["variants"]["both"]["features"] and "layers" in manifest["variants"]["layers"]["features"]
    assert manifest["grid"]["variants"] == list(fx.VARIANTS) and manifest["grid"]["technical_pass"] is True
    assert "render done" in (run_dir / "render.log").read_text() and "libx264" in (run_dir / "render.log").read_text()
    index = read_json(root / "runs" / "index.json")
    assert index["runs"][manifest["key"]]["run_id"] == result["run_id"]


def test_every_output_is_bt709_tv_with_matching_size_fps_and_audio(shot):
    root, result = shot
    for item in list(result["variants"].values()) + [{"path": result["grid"]}]:
        info = probe.inspect(item["path"])
        video = info["video"]
        assert (video["width"], video["height"], video["fps"], video["pix_fmt"]) == (320, 180, 12.0, "yuv420p")
        assert (video["range"], video["matrix"], video["primaries"], video["transfer"]) == ("tv", "bt709", "bt709", "bt709")
        assert info["audio"] == {"codec": "aac", "sample_rate": 48000, "channels": 2}
        assert probe.decode(item["path"], "v")["frames"] == 18 and probe.decode(item["path"], "v")["ok"]


def test_second_render_reuses_the_run_and_force_renders_again(shot):
    root, first = shot
    before = sorted(p.name for p in (root / "runs").iterdir())
    code, again, err = run_cli("fx", "render", root / "spec.json", "--out", root / "runs")
    assert code == 0 and again["reused"] is True and again["run_id"] == first["run_id"] and "再利用" in err
    assert sorted(p.name for p in (root / "runs").iterdir()) == before                # 何も作らない
    assert again["variants"] == first["variants"] and again["grid"] == first["grid"]
    code, forced, _ = run_cli("fx", "render", root / "spec.json", "--out", root / "runs", "--force")
    assert code == 0 and forced["reused"] is False and forced["run_id"] != first["run_id"]
    assert (root / "runs" / first["run_id"] / "manifest.json").is_file()              # 過去の run は残る
    assert read_json(root / "runs" / "index.json")["runs"][read_json(
        root / "runs" / forced["run_id"] / "manifest.json")["key"]]["run_id"] == forced["run_id"]
    code, third, _ = run_cli("fx", "render", root / "spec.json", "--out", root / "runs")
    assert third["reused"] is True and third["run_id"] == forced["run_id"]


def test_reuse_is_refused_when_an_output_changed_or_vanished(tmp_path):
    root = tmp_path
    run_cli("fx", "demo", root, "--width", "160", "--height", "90", "--duration", "0.5", "--fps", "12")
    args = ("fx", "render", root / "spec.json", "--out", root / "runs", "--variants", "base,both")
    first = run_cli(*args)[1]
    run_dir = root / "runs" / first["run_id"]
    (run_dir / "both.mp4").write_bytes(b"tampered")
    second = run_cli(*args)[1]
    assert second["reused"] is False and second["run_id"] != first["run_id"] and second["technical_pass"] is True
    shutil.rmtree(root / "runs" / second["run_id"])
    third = run_cli(*args)[1]
    assert third["reused"] is False and third["run_id"] != first["run_id"] and (root / "runs" / third["run_id"]).is_dir()


# ---- key ---------------------------------------------------------------------

def key_of(tmp_path, data=None, *, variants=fx.VARIANTS, start=0.0, duration=2.0, size=(160, 90), scale=1.0, grid=True,
           libraries=None, mutate=None):
    path = make_project(tmp_path) if data is None else write_spec(tmp_path, data)
    if mutate:
        mutate(tmp_path)
    sp = fxspec.load_spec(path)
    libraries = libraries or {"python": "3", "numpy": "2", "opencv": "4", "pillow": "13", "ffmpeg": "x"}
    return runs.render_key(sp, fxassets.describe(sp), variants, start, duration, round(duration * 12), size, scale, grid, libraries)


def test_key_is_stable_and_changes_with_every_render_input(tmp_path, monkeypatch):
    base = key_of(tmp_path)
    assert base == key_of(tmp_path) and len(base) == 64
    changed = {}
    changed["seed"] = key_of(tmp_path, spec_dict(seed=8))
    changed["x_key"] = key_of(tmp_path, {**spec_dict(), "x_note": "試行"})                  # 描画に使わない鍵でも安全側に倒す
    changed["fps"] = key_of(tmp_path, {**spec_dict(), "output": {**spec_dict()["output"], "fps": 24}})
    changed["light"] = key_of(tmp_path, {**spec_dict(), "lights": []})
    changed["variants"] = key_of(tmp_path, variants=("base", "both"))
    changed["start"] = key_of(tmp_path, start=0.5)
    changed["duration"] = key_of(tmp_path, duration=1.5)
    changed["size"] = key_of(tmp_path, size=(80, 46))
    changed["scale"] = key_of(tmp_path, scale=0.5)
    changed["grid"] = key_of(tmp_path, grid=False)
    changed["libraries"] = key_of(tmp_path, libraries={"python": "3", "numpy": "2", "opencv": "5", "pillow": "13", "ffmpeg": "x"})
    for label, value in changed.items():
        assert value != base, label
    assert len(set(changed.values())) == len(changed)
    monkeypatch.setattr(runs, "fingerprint", lambda: "0" * 64)                              # fx のコードが変わった
    assert key_of(tmp_path) != base


def test_key_changes_when_an_asset_pixel_or_the_audio_changes(tmp_path):
    base = key_of(tmp_path)

    def touch_mask(directory):
        from PIL import Image
        image = Image.open(directory / "mask_subject.png")
        image.putpixel((1, 1), 255)
        image.save(directory / "mask_subject.png")

    assert key_of(tmp_path, mutate=touch_mask) != base
    data = spec_dict()
    data["audio"] = {"path": "audio.wav"}
    (tmp_path / "audio.wav").write_bytes(b"RIFF0000WAVE")
    first = key_of(tmp_path, data)
    (tmp_path / "audio.wav").write_bytes(b"RIFF0001WAVE")
    assert key_of(tmp_path, data) != first


# ---- 失敗・中断・技術検査 -----------------------------------------------------

@pytest.fixture
def small(tmp_path):
    run_cli("fx", "demo", tmp_path, "--width", "160", "--height", "90", "--duration", "0.5", "--fps", "12")
    return tmp_path


def snapshot(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file() and p.name != ".lock"}


def test_failed_render_never_damages_earlier_runs_or_the_index(small, monkeypatch):
    out = small / "runs"
    good = run_cli("fx", "render", small / "spec.json", "--out", out)[1]
    before = snapshot(out)
    real = render.render_variant

    def explode(ctx, variant, *args, **kwargs):
        if variant == "light":
            raise PipelineError("ffmpeg が途中で終了しました（テスト）")
        return real(ctx, variant, *args, **kwargs)

    monkeypatch.setattr(render, "render_variant", explode)
    code, result, err = run_cli("fx", "render", small / "spec.json", "--out", out, "--force")
    assert code == 1 and result is None and "テスト" in err
    after = snapshot(out)
    assert {k: v for k, v in after.items() if k.split("/")[0] == good["run_id"] or k == "index.json"} == \
        {k: v for k, v in before.items()}                                                    # 過去の run と index はバイト一致
    new = [d for d in out.iterdir() if d.is_dir() and d.name != good["run_id"]]
    assert len(new) == 1
    failed = read_json(new[0] / "manifest.json")
    assert failed["status"] == "failed" and "テスト" in failed["error"] and failed["finished_at"]
    assert set(failed["variants"]) == {"base", "layers"}                                     # 進んだところまで記録される
    assert not list(new[0].glob("*.part*")) and not (new[0] / "light.mp4").exists()
    monkeypatch.undo()
    assert run_cli("fx", "render", small / "spec.json", "--out", out)[1]["run_id"] == good["run_id"]   # 良い run は再利用される


def test_technical_failure_is_recorded_as_failed_and_not_indexed(small, monkeypatch):
    out = small / "runs"
    real = probe.check_video

    def strict(path, **expect):
        result = real(path, **expect)
        if str(path).endswith("both.mp4"):
            return {**result, "pass": False, "failures": ["テスト用の不合格"]}
        return result

    monkeypatch.setattr(probe, "check_video", strict)
    code, _, err = run_cli("fx", "render", small / "spec.json", "--out", out, "--variants", "base,both", "--no-grid")
    assert code == 1 and "技術検査に失敗" in err and "テスト用の不合格" in err
    (run_dir,) = [d for d in out.iterdir() if d.is_dir()]
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["status"] == "failed" and manifest["technical_pass"] is False
    assert manifest["variants"]["base"]["technical_pass"] is True and manifest["variants"]["both"]["technical_pass"] is False
    assert not (out / "index.json").exists()
    monkeypatch.undo()
    again = run_cli("fx", "render", small / "spec.json", "--out", out, "--variants", "base,both", "--no-grid")[1]
    assert again["reused"] is False and again["technical_pass"] is True


def test_run_left_running_by_a_killed_process_is_closed_as_failed(small):
    out = small / "runs"
    stale = out / "20260101T000000Z-deadbeef"
    stale.mkdir(parents=True)
    (stale / "manifest.json").write_text(json.dumps({"run_id": stale.name, "status": "running", "error": None}))
    done = out / "20260101T000001Z-feedface"
    done.mkdir()
    (done / "manifest.json").write_text(json.dumps({"run_id": done.name, "status": "succeeded", "error": None}))
    assert run_cli("fx", "render", small / "spec.json", "--out", out, "--variants", "base")[0] == 0
    closed = read_json(stale / "manifest.json")
    assert closed["status"] == "failed" and "interrupted" in closed["error"] and closed["finished_at"]
    assert read_json(done / "manifest.json")["status"] == "succeeded"


def test_two_renders_into_one_out_dir_cannot_overlap(small):
    from mv_pipeline.runner import project_lock
    out = small / "runs"
    with project_lock(out):
        code, _, err = run_cli("fx", "render", small / "spec.json", "--out", out)
    assert code == 1 and "already running" in err


# ---- 版の部分指定・機能なし・区間 ---------------------------------------------

def test_subset_of_variants_without_grid_and_with_a_start_offset(small):
    out = small / "runs"
    code, result, _ = run_cli("fx", "render", small / "spec.json", "--out", out, "--variants", "both,base", "--no-grid",
                              "--start", "0.25", "--duration", "0.25")
    assert code == 0 and list(result["variants"]) == ["base", "both"] and result["grid"] is None     # 版は常に base, … の順
    assert result["interval"] == {"start": 0.25, "duration": 0.25, "fps": 12.0, "frames": 3}
    manifest = read_json(out / result["run_id"] / "manifest.json")
    assert manifest["grid"] is None and manifest["variants"]["both"]["frames"] == 3
    assert not (out / result["run_id"] / "grid.mp4").exists()
    one = run_cli("fx", "render", small / "spec.json", "--out", out, "--variants", "base")[1]
    assert one["grid"] is None                                                               # 1 版だけならグリッドは作らない


def test_start_and_duration_cut_the_same_pictures_as_the_full_shot(small):
    """区間を切って描いても、同じショット時刻は同じ絵（start 付きでも時刻がずれない）。"""
    ctx = render.prepare(fxspec.load_spec(small / "spec.json"))
    out = small / "runs"
    result = run_cli("fx", "render", small / "spec.json", "--out", out, "--variants", "both", "--no-grid",
                     "--start", "0.25", "--duration", "0.25")[1]
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-i", result["variants"]["both"]["path"],
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    decoded = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 90, 160, 3)
    expected = render.frame(ctx, 0.25 + 1 / 12, "both").astype(int)
    assert len(decoded) == 3 and np.abs(decoded[1].astype(int) - expected).mean() < 4


def test_features_missing_from_the_spec_are_recorded_not_hidden(tmp_path):
    path = make_project(tmp_path, layers=None, lights=None, audio=None)
    result = run_cli("fx", "render", path, "--out", tmp_path / "runs", "--duration", "0.5")[1]
    manifest = read_json(tmp_path / "runs" / result["run_id"] / "manifest.json")
    for name in ("layers", "light", "both"):
        item = manifest["variants"][name]
        assert item["empty"] is True and item["technical_pass"] is True and (tmp_path / "runs" / result["run_id"] / f"{name}.mp4").is_file()
    assert "layers が無い" in manifest["variants"]["layers"]["note"] and "lights が無い" in manifest["variants"]["light"]["note"]
    assert manifest["variants"]["base"]["empty"] is False
    assert manifest["variants"]["base"]["checks"]["audio"] is None                          # 音声なしの spec は音声なしの MP4


def test_interval_scale_and_variant_arguments_are_validated(small):
    spec = small / "spec.json"
    for argv, text in ((("--start", "0.4", "--duration", "0.3"), "超えています"), (("--duration", "0"), "0 以下"),
                       (("--duration", "-1"), "0 以下"), (("--start", "-1"), "0 以上"), (("--start", "0.5"), "0 以下"),
                       (("--scale", "0"), "scale"), (("--scale", "9"), "scale"), (("--variants", "base,fancy"), "fancy"),
                       (("--variants", ","), "指定なし")):
        code, _, err = run_cli("fx", "render", spec, "--out", small / "runs", *argv)
        assert code == 1 and text in err, (argv, err)
    assert not (small / "runs").exists() or not [d for d in (small / "runs").iterdir() if d.is_dir()]   # 検査で落ちたら run を作らない


def test_scale_changes_the_rendered_size(small):
    result = run_cli("fx", "render", small / "spec.json", "--out", small / "runs", "--scale", "0.5", "--variants", "base,light")[1]
    assert result["size"] == [80, 44]                                                        # 90×0.5=45 は偶数 44 に丸める
    for path in (result["variants"]["base"]["path"], result["grid"]):
        info = probe.inspect(path)["video"]
        assert (info["width"], info["height"]) == (80, 44)


def test_warnings_from_spec_and_assets_reach_the_manifest(tmp_path):
    data = spec_dict(depth=None)
    data["assets"].pop("depth")
    data["lights"][0].pop("depth")
    data["camera"]["parallax_px"] = 5                                                          # 深度が無いのに視差を指定
    path = make_project(tmp_path, assets=data["assets"], lights=data["lights"], camera=data["camera"])
    result = run_cli("fx", "render", path, "--out", tmp_path / "runs", "--variants", "base", "--duration", "0.25")[1]
    assert any("assets.depth がない" in w for w in result["warnings"])
    assert read_json(tmp_path / "runs" / result["run_id"] / "manifest.json")["warnings"] == result["warnings"]


# ---- stills ------------------------------------------------------------------

def test_stills_sheet_has_rows_for_times_and_columns_for_variants(small):
    code, result, _ = run_cli("fx", "stills", small / "spec.json", "--times", "0", "0.25", "--out", small / "stills")
    assert code == 0 and result["variants"] == list(fx.VARIANTS) and result["times"] == [0.0, 0.25]
    from PIL import Image
    sheet = np.asarray(Image.open(result["path"]).convert("RGB"))
    gap, (w, h) = compare.GAP, (160, 90)
    assert result["cell"] == [w, h] and sheet.shape == (2 * h + 3 * gap, 4 * w + 5 * gap, 3)
    assert result["sheet"] == [sheet.shape[1], sheet.shape[0]]
    ctx = render.prepare(fxspec.load_spec(small / "spec.json"))
    for r, t in enumerate((0.0, 0.25)):
        for c, variant in enumerate(fx.VARIANTS):
            y, x = gap + r * (h + gap), gap + c * (w + gap)
            below_label = sheet[y + 24:y + h, x:x + w]
            assert np.array_equal(below_label, render.frame(ctx, t, variant)[24:])           # 圧縮なしの frame() そのまま
            assert not np.array_equal(sheet[y:y + 16, x:x + 60], render.frame(ctx, t, variant)[:16, :60])   # 左上にラベル
    assert (sheet[:gap] == compare.SHEET_BACKGROUND).all()
    again = run_cli("fx", "stills", small / "spec.json", "--times", "0", "0.25", "--out", small / "stills")[1]
    assert again["path"] == result["path"]                                                    # 同じ入力は同じ名前
    other = run_cli("fx", "stills", small / "spec.json", "--times", "0", "0.4", "--variants", "base,both",
                    "--out", small / "stills")[1]
    assert other["path"] != result["path"] and other["sheet"][0] == 2 * w + 3 * gap
    assert sorted(p.suffix for p in (small / "stills").iterdir()) == [".png", ".png"]         # 一時ファイルを残さない


def test_stills_rejects_times_outside_the_shot(small):
    for times in (("-0.1",), ("9",)):
        code, _, err = run_cli("fx", "stills", small / "spec.json", "--times", *times, "--out", small / "stills")
        assert code == 1 and "外です" in err


# ---- accept ------------------------------------------------------------------

def manifest_key(run_dir):
    return read_json(run_dir / "manifest.json")["key"]


def test_accept_appends_and_keeps_existing_lines(shot):
    root, result = shot
    run_dir = root / "runs" / result["run_id"]
    log = root / "runs" / "acceptance.jsonl"
    code, first, _ = run_cli("fx", "accept", run_dir, "--variant", "both", "--decision", "accepted", "--by", "reviewer", "--note", "炎の当たりが良い")
    assert code == 0 and log.is_file()
    line1 = log.read_bytes()
    code, _, _ = run_cli("fx", "accept", run_dir, "--variant", "layers", "--decision", "hold", "--by", "reviewer")
    code, _, _ = run_cli("fx", "accept", run_dir, "--variant", "both", "--decision", "rejected", "--by", "reviewer2", "--note", "やはり強い")
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert log.read_bytes().startswith(line1) and len(lines) == 3                             # 追記のみ。1 行目は不変
    assert [(x["variant"], x["decision"]) for x in lines] == [("both", "accepted"), ("layers", "hold"), ("both", "rejected")]
    assert lines[0]["note"] == "炎の当たりが良い" and lines[0]["by"] == "reviewer" and lines[1]["note"] is None
    assert lines[0]["at"].endswith("+00:00") and lines[0]["run_id"] == result["run_id"]
    assert lines[0]["video_sha256"] == sha256(run_dir / "both.mp4") and lines[0]["key"] == manifest_key(run_dir)
    assert read_json(run_dir / "manifest.json")["technical_pass"] is True
    assert "accept" not in (run_dir / "manifest.json").read_text() and sorted(p.name for p in run_dir.iterdir()).count("acceptance.jsonl") == 0


def test_accept_refuses_unknown_variants_bad_input_and_replaced_files(shot, tmp_path):
    root, result = shot
    run_dir = tmp_path / "copy"
    shutil.copytree(root / "runs" / result["run_id"], run_dir)
    out = lambda *extra: run_cli("fx", "accept", run_dir, *extra)  # noqa: E731
    assert "ありません" in out("--variant", "nope", "--decision", "hold", "--by", "x")[2]
    assert "判定者" in out("--variant", "both", "--decision", "hold", "--by", "  ")[2]
    with pytest.raises(SystemExit):                                                          # CLI は argparse の choices で止める
        out("--variant", "both", "--decision", "maybe", "--by", "x")
    with pytest.raises(PipelineError, match="decision"):
        runs.accept(run_dir, "both", "maybe", "x")
    assert "manifest.json がない" in run_cli("fx", "accept", tmp_path, "--variant", "both", "--decision", "hold", "--by", "x")[2]
    manifest = read_json(run_dir / "manifest.json")
    manifest["variants"]["light"]["technical_pass"] = False
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    assert "技術検査に通っていない" in out("--variant", "light", "--decision", "accepted", "--by", "x")[2]
    assert out("--variant", "light", "--decision", "rejected", "--by", "x")[0] == 0              # 不合格の版を rejected にはできる
    (run_dir / "both.mp4").write_bytes(b"replaced")
    assert "manifest の記録と違う" in out("--variant", "both", "--decision", "accepted", "--by", "x")[2]
    lines = (tmp_path / "acceptance.jsonl").read_text(encoding="utf-8").splitlines()
    assert [(json.loads(x)["variant"], json.loads(x)["decision"]) for x in lines] == [("light", "rejected")]   # 断った判定は残らない


def test_accept_does_not_glue_onto_a_truncated_last_line(shot, tmp_path):
    root, result = shot
    run_dir = tmp_path / "copy"
    shutil.copytree(root / "runs" / result["run_id"], run_dir)
    log = tmp_path / "acceptance.jsonl"
    log.write_bytes(b'{"broken": ')
    assert run_cli("fx", "accept", run_dir, "--variant", "base", "--decision", "hold", "--by", "x")[0] == 0
    first, second = log.read_text(encoding="utf-8").split("\n")[:2]
    assert first == '{"broken": ' and json.loads(second)["variant"] == "base"


# ---- CLI ---------------------------------------------------------------------

def test_fx_commands_take_no_project_and_report_errors_with_exit_code_1(tmp_path):
    from mv_pipeline import cli
    args = cli.parser().parse_args(["fx", "render", str(tmp_path / "spec.json")])
    assert args.command == "fx" and not hasattr(args, "project")
    code, result, err = run_cli("fx", "render", tmp_path / "missing.json")
    assert code == 1 and result is None and err.startswith("Error:") and "spec not found" in err
    assert not (tmp_path / "fx_runs").exists() and not (tmp_path / "missing.json").exists()
    with pytest.raises(SystemExit):
        run_cli("fx", "render", tmp_path / "x.json", "--bogus")


def test_missing_opencv_gives_an_actionable_message_and_accept_still_works(shot, tmp_path, monkeypatch):
    root, result = shot
    monkeypatch.setitem(sys.modules, "cv2", None)
    code, _, err = run_cli("fx", "render", root / "spec.json", "--out", tmp_path / "runs")
    assert code == 1 and "--extra fx" in err and "opencv" in err
    code, _, err = run_cli("fx", "stills", root / "spec.json", "--times", "0")
    assert code == 1 and "--extra fx" in err
    run_dir = tmp_path / "copy"
    shutil.copytree(root / "runs" / result["run_id"], run_dir)
    assert run_cli("fx", "accept", run_dir, "--variant", "base", "--decision", "hold", "--by", "x")[0] == 0   # cv2 不要


def test_existing_commands_are_unchanged_by_the_fx_group(tmp_path):
    from mv_pipeline import cli
    choices = cli.parser()._subparsers._group_actions[0].choices
    assert {"init", "analyze", "render", "verify", "demo", "run-adapter", "fx"} <= set(choices)
    assert "project" in {a.dest for a in choices["render"]._actions} and "dir" not in {a.dest for a in choices["render"]._actions}
    code, _, err = run_cli("status", tmp_path / "no-such-project")
    assert code == 1 and "Error:" in err


# ---- 再利用の守り・run_id・グリッドの検査 -------------------------------------

def test_reusable_refuses_entries_that_are_not_good_successful_runs(small):
    out = small / "runs"
    good = run_cli("fx", "render", small / "spec.json", "--out", out, "--variants", "base,both")[1]
    run_dir = out / good["run_id"]
    manifest = read_json(run_dir / "manifest.json")
    key = manifest["key"]
    index = runs.load_index(out)
    assert runs.reusable(out, index, key)["run_id"] == good["run_id"]
    assert runs.reusable(out, index, "0" * 64) is None                                       # index に無い key

    def with_manifest(change):
        tampered = json.loads(json.dumps(manifest))
        change(tampered)
        (run_dir / "manifest.json").write_text(json.dumps(tampered), encoding="utf-8")
        try:
            return runs.reusable(out, index, key)
        finally:
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert with_manifest(lambda m: m.update(status="failed")) is None                       # 失敗した run は再利用しない
    assert with_manifest(lambda m: m.update(status="running")) is None
    assert with_manifest(lambda m: m.update(technical_pass=False)) is None
    assert with_manifest(lambda m: m["variants"]["base"].update(technical_pass=False)) is None      # 版単位の不合格
    assert with_manifest(lambda m: m["grid"].update(technical_pass=False)) is None
    assert with_manifest(lambda m: m.update(key="f" * 64)) is None                           # 別の key の run
    assert runs.reusable(out, index, key)["run_id"] == good["run_id"]
    index["runs"][key]["run_id"] = "../outside"                                              # index が出力先の外を指しても読まない
    assert runs.reusable(out, index, key) is None


def test_run_ids_are_unique_even_for_the_same_key_in_the_same_second(tmp_path, monkeypatch):
    monkeypatch.setattr(runs.time, "strftime", lambda *args: "20261024T120000Z")
    key = "ab" * 32
    ids = [runs.Run(tmp_path, key, "render", "render.log").run_id for _ in range(3)]
    assert ids == ["20261024T120000Z-abababab", "20261024T120000Z-abababab-2", "20261024T120000Z-abababab-3"]
    assert all((tmp_path / i / "manifest.json").is_file() for i in ids)


def test_a_failing_grid_fails_the_run_even_when_every_variant_passed(small, monkeypatch):
    real = probe.check_video

    def strict(path, **expect):
        result = real(path, **expect)
        return {**result, "pass": False, "failures": ["グリッドだけ不合格"]} if str(path).endswith("grid.mp4") else result

    monkeypatch.setattr(probe, "check_video", strict)
    code, _, err = run_cli("fx", "render", small / "spec.json", "--out", small / "runs", "--variants", "base,both")
    assert code == 1 and "グリッドだけ不合格" in err
    (run_dir,) = [d for d in (small / "runs").iterdir() if d.is_dir()]
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["status"] == "failed" and manifest["technical_pass"] is False and manifest["error"]
    assert manifest["variants"]["base"]["technical_pass"] is True and not (small / "runs" / "index.json").exists()


def test_stills_name_changes_with_the_times_alone(small):
    a = run_cli("fx", "stills", small / "spec.json", "--times", "0", "0.25", "--variants", "base", "--out", small / "s")[1]
    b = run_cli("fx", "stills", small / "spec.json", "--times", "0", "0.3", "--variants", "base", "--out", small / "s")[1]
    c = run_cli("fx", "stills", small / "spec.json", "--times", "0", "0.25", "--variants", "base", "--scale", "0.5", "--out", small / "s")[1]
    assert len({a["path"], b["path"], c["path"]}) == 3
