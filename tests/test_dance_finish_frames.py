"""mv dance finish: 完成コマの一覧と光のトラックの読み方・1 コマの仕上げ（mv dance passes の finish_frame を呼ぶ）・run の受け渡しを、
合成の小さなコマで確かめる。3 作目の VRM のダンスの演出版の仕上げたコマとの比べは docs/dance.md の「完成コマに仕上げを掛ける」。"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import finish_frames  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

W, H = 32, 48


def _write(path, rgb):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(".png", np.ascontiguousarray(rgb[..., ::-1]))
    path.write_bytes(data.tobytes())


def _frames(tmp_path, count=4, value=128, fps=30, first=1):
    """合成の完成コマ（灰色の一様な画）と一覧（model3d-frames/0.1）。"""
    root = tmp_path / "frames"
    rows = []
    for T in range(first, first + count):
        name = f"f{T:04d}.png"
        _write(root / name, np.full((H, W, 3), value, np.uint8))
        data = (root / name).read_bytes()
        rows.append({"T": T, "f": T, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = {"schema_version": "model3d-frames/0.1", "directory": str(root), "fps": fps, "resolution": [W, H], "frames": rows,
                "missing": []}
    (tmp_path / "frames.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path / "frames.json"


def _track(tmp_path, values, fps=30):
    frames = [{"T": T, "values": {"flash": 0.0, "flash_color": [1.0, 1.0, 1.0], "fade": 1.0, **v}} for T, v in values.items()]
    track = {"schema_version": "model3d-light-track/0.1", "fps": fps, "frames": frames}
    (tmp_path / "lights.json").write_text(json.dumps(track), encoding="utf-8")
    return tmp_path / "lights.json"


def _spec(tmp_path, **parts):
    spec = {"schema_version": "dance-finish/0.1", "frames": "frames.json", **parts}
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    return tmp_path / "spec.json"


def test_the_light_values_become_the_finish_of_mv_dance_passes():
    assert finish_frames.finish_values(None) == {"impact": None, "bloom": None, "flash": None, "level": 1.0}
    calm = finish_frames.finish_values({"flash": 0.0, "flash_color": [1.0, 0.5, 0.0], "fade": 0.5})
    assert calm["flash"] is None and calm["level"] == 0.5
    lit = finish_frames.finish_values({"flash": 0.4, "flash_color": [1.0, 0.5, 0.0], "fade": 1.0})
    assert lit["flash"] == {"keys": [[0.0, 0.4]], "color": [255.0, 127.5, 0.0]} and lit["level"] == 1.0


def test_one_frame_is_finished_with_the_flash_the_fade_and_the_look():
    from mv_pipeline.dance.passes_layers import finish_frame
    from mv_pipeline.fx import fire
    from mv_pipeline.fx import look as fxlook
    from mv_pipeline.fx import spec as fxspec
    grey = np.full((H, W, 3), 0.5, np.float32)
    plain = finish_frames.finish_one(grey, None, 1, 30)
    assert plain.dtype == np.uint8 and (plain == 128).all()                        # 何も掛けない（0.5 × 255 + 0.5 の切り捨て）
    faded = finish_frames.finish_one(grey, {"flash": 0.0, "flash_color": [1, 1, 1], "fade": 0.5}, 1, 30)
    assert (faded == 64).all()                                                     # 暗転は掛け算
    values = {"flash": 0.4, "flash_color": [1.0, 0.2, 0.2], "fade": 1.0}
    flashed = finish_frames.finish_one(grey, values, 1, 30)
    want = fire.screen(grey * np.float32(255.0), np.asarray([255.0, 51.0, 51.0], np.float32) * np.float32(0.4)) / np.float32(255.0)
    assert np.array_equal(flashed, (np.clip(want, 0, 1) * 255 + 0.5).astype(np.uint8)) and flashed[0, 0, 0] > flashed[0, 0, 1] > 128
    look = fxlook.prepare(fxspec.normalize_look({"vignette": {"amount": 0.5}}), (W, H), 30)
    shaded = finish_frames.finish_one(grey, values, 1, 30, look)
    assert shaded[0, 0, 0] < shaded[H // 2, W // 2, 0]                             # 周辺の減光は look で
    data = finish_frames.finish_values(values)
    from types import SimpleNamespace
    assert np.array_equal(shaded, finish_frame(data, SimpleNamespace(fps=30, size=(W, H)), 1, grey, look))


def test_the_frame_list_must_be_whole_and_unchanged(tmp_path):
    path = _frames(tmp_path, count=3)
    fps, size, files = finish_frames.load_frames(path)
    assert fps == 30 and size == (W, H) and [T for T, _ in files] == [1, 2, 3]
    record = json.loads(path.read_text(encoding="utf-8"))
    for change, message in [({"schema_version": "model3d-passes/0.1"}, "model3d-frames"), ({"missing": [4]}, "描いていない"),
                            ({"frames": record["frames"] + [record["frames"][0]]}, "重なって"),
                            ({"frames": [record["frames"][0], record["frames"][2]]}, "とんで"), ({"frames": []}, "コマがありません")]:
        (tmp_path / "bad.json").write_text(json.dumps({**record, **change}), encoding="utf-8")
        with pytest.raises(PipelineError, match=message):
            finish_frames.load_frames(tmp_path / "bad.json")
    (tmp_path / "frames" / "f0003.png").unlink()                                   # 一覧にあるのにファイルが無い
    with pytest.raises(PipelineError, match="ファイルがありません"):
        finish_frames.load_frames(path)
    _write(tmp_path / "frames" / "f0003.png", np.zeros((H, W, 3), np.uint8))       # 描いた後に変わったコマ
    with pytest.raises(PipelineError, match="sha256"):
        finish_frames.load_frames(path)


def test_the_light_track_must_cover_the_frames(tmp_path):
    path = _track(tmp_path, {1: {}, 2: {"flash": 0.3}})
    values, fps = finish_frames.load_track(path, [1, 2])
    assert fps == 30 and values[2] == {"flash": 0.3, "flash_color": [1.0, 1.0, 1.0], "fade": 1.0}
    with pytest.raises(PipelineError, match="T \\[3\\]"):
        finish_frames.load_track(path, [1, 2, 3])
    with pytest.raises(PipelineError, match="flash・flash_color・fade"):
        finish_frames.load_track(_track(tmp_path, {1: {"fade": -1.0}}), [1])
    (tmp_path / "other.json").write_text(json.dumps({"schema_version": "model3d-stage-track/0.1", "frames": []}), encoding="utf-8")
    with pytest.raises(PipelineError, match="model3d-light-track"):
        finish_frames.load_track(tmp_path / "other.json", [1])


def _write_bgra(path, bgra):
    ok, data = cv2.imencode(".png", np.ascontiguousarray(bgra))
    path.write_bytes(data.tobytes())
    return path


def test_transparent_frames_are_refused_and_opaque_alpha_is_dropped(tmp_path):
    """透けた画素のあるコマは止める（そのまま読むとアルファが黙って消え、透けた所が不透明になるため）。全部不透明なアルファは
    除いて読み、cv2 の IMREAD_COLOR で読んだのと同じ値。灰色・16 ビットのコマも今までと同じ読み方。"""
    from mv_pipeline.dance.passes_scene import read_image
    rgb = np.random.default_rng(0).integers(0, 256, (H, W, 3), dtype=np.uint8)
    _write(tmp_path / "rgb.png", rgb)
    bgra = np.dstack([rgb[..., ::-1], np.full((H, W), 255, np.uint8)])
    opaque = _write_bgra(tmp_path / "opaque.png", bgra)
    plain = finish_frames.read_frame(tmp_path / "rgb.png", 1)
    assert np.array_equal(plain, read_image(tmp_path / "rgb.png")) and np.array_equal(plain, rgb.astype(np.float32) / 255)
    assert np.array_equal(finish_frames.read_frame(opaque, 1), plain) and np.array_equal(read_image(opaque), plain)
    bgra[0, 0, 3] = 254                                                            # 1 画素だけ、少し透ける
    with pytest.raises(PipelineError, match="T 7 に透けた画素"):
        finish_frames.read_frame(_write_bgra(tmp_path / "clear.png", bgra), 7)
    grey16 = (np.arange(H * W, dtype=np.uint16).reshape(H, W) * 37)
    deep = _write_bgra(tmp_path / "grey16.png", grey16)
    assert np.array_equal(finish_frames.read_frame(deep, 1), read_image(deep))


def test_the_run_stops_on_a_transparent_frame(tmp_path):
    path = _frames(tmp_path, count=2)
    frame = _write_bgra(tmp_path / "frames" / "f0002.png", np.zeros((H, W, 4), np.uint8))   # 透けた背景（render.stage など）
    record = json.loads(path.read_text(encoding="utf-8"))
    data = frame.read_bytes()
    record["frames"][1].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PipelineError, match="T 2 に透けた画素"):
        finish_frames.render_run(_spec(tmp_path), out=tmp_path / "runs", quiet=True)
    _write_bgra(tmp_path / "frames" / "f0001.png", np.zeros((H, W, 4), np.uint8))           # 最初のコマなら run を作る前に止める
    record["frames"][0].update(sha256=hashlib.sha256((tmp_path / "frames" / "f0001.png").read_bytes()).hexdigest(),
                               bytes=(tmp_path / "frames" / "f0001.png").stat().st_size)
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PipelineError, match="T 1 に透けた画素"):
        finish_frames.render_run(_spec(tmp_path), out=tmp_path / "runs2", quiet=True)
    assert not (tmp_path / "runs2").exists()


def test_the_run_writes_the_finished_video_and_frames(tmp_path):
    _frames(tmp_path, count=4)
    _track(tmp_path, {1: {}, 2: {"flash": 0.8}, 3: {}, 4: {"fade": 0.25}})
    spec = _spec(tmp_path, lights="lights.json", output={"crf": 20, "frames": True}, look={"vignette": {"amount": 0.2}})
    result = finish_frames.render_run(spec, out=tmp_path / "runs", quiet=True)
    assert result["status"] == "succeeded" and result["technical_pass"] is True and not result["reused"]
    run = Path(result["run_dir"])
    names = sorted(p.name for p in (run / "frames").iterdir())
    assert (run / "dance.mp4").is_file() and names == [f"f{T:04d}.png" for T in range(1, 5)]
    centre = [cv2.imread(str(run / "frames" / name))[H // 2, W // 2, 0] for name in names]
    assert centre[1] > centre[0] == centre[2] > centre[3]                          # 閃光で明るく、暗転で暗く
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["finish"] == {"flash_frames": 1, "fade_frames": 1} and manifest["interval"]["T"] == [1, 4]
    again = finish_frames.render_run(spec, out=tmp_path / "runs", quiet=True)
    assert again["reused"] and again["run_id"] == result["run_id"]


def test_the_frames_and_the_track_must_agree(tmp_path):
    _frames(tmp_path, count=2)
    _track(tmp_path, {1: {}, 2: {}}, fps=24)
    with pytest.raises(PipelineError, match="fps"):
        finish_frames.render_run(_spec(tmp_path, lights="lights.json"), out=tmp_path / "runs", quiet=True)
    with pytest.raises(PipelineError, match="schema_version"):
        finish_frames.parse_spec({"schema_version": "dance-passes/0.1"}, tmp_path)
    with pytest.raises(PipelineError, match="invalid"):
        finish_frames.parse_spec({"schema_version": "dance-finish/0.1", "frames": "frames.json", "wobble": 1}, tmp_path)


def test_the_finish_command_hands_its_options_to_the_run(tmp_path, capsys):
    """mv dance finish: --out・--quiet が render_run に渡り、結果は stdout の JSON に出る。"""
    from mv_pipeline import cli
    _frames(tmp_path, count=2)
    spec, out = _spec(tmp_path), tmp_path / "runs"
    assert cli.main(["dance", "finish", str(spec), "--out", str(out), "--quiet"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "succeeded" and result["technical_pass"] is True and Path(result["run_dir"]).parent == out.resolve()
    assert cli.main(["dance", "finish", str(spec), "--out", str(out), "--force", "--quiet"]) == 0
    assert not json.loads(capsys.readouterr().out)["reused"]
    assert cli.main(["dance", "finish", str(tmp_path / "missing.json"), "--quiet"]) == 1
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err.startswith("Error:")
