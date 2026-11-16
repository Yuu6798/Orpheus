"""mv dance passes: 要素別の描画（model3d の render.passes）の重ね方を、合成の小さな要素で確かめる（区間とキーの値・一覧の確かめ・
重ねる順・run の受け渡し）。実作品の要素との比べは docs/dance.md の「要素別の描画を重ねる」。"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import finish, passes  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

W, H = 32, 48


def _write(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(".png", image)
    path.write_bytes(data.tobytes())


def _passes(tmp_path, frames=3, kinds=("room", "char", "disc"), gap=False):
    """合成の要素: 部屋は灰色、人物は真ん中の赤い四角（透け）、円は人物の下の白い帯。"""
    root = tmp_path / "passes"
    entries = {}
    Ts = [1, 2, 4] if gap else list(range(1, frames + 1))
    for kind in kinds:
        rows = []
        for T in Ts:
            if kind == "char":
                image = np.zeros((H, W, 4), np.uint8)
                image[10:30, 8:24] = (0, 0, 200, 255)          # BGRA の赤
            elif kind == "room":
                image = np.full((H, W, 3), 100, np.uint8)
            else:
                image = np.zeros((H, W, 3), np.uint8)
                image[32:40, 4:28] = 255
            name = f"{kind}/f{T:04d}.png"
            _write(root / name, image)
            data = (root / name).read_bytes()
            rows.append({"T": T, "f": T, "file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        entries[kind] = {"format": "png", "alpha": kind == "char", "frames": rows, "missing": []}
    manifest = {"schema_version": "model3d-passes/0.1", "directory": str(root), "fps": 30, "resolution": [W, H], "passes": entries}
    (tmp_path / "passes.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path / "passes.json"


def _spec(tmp_path, **parts):
    spec = {"schema_version": "dance-passes/0.1", "passes": "passes.json", **parts}
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    return tmp_path / "spec.json"


def test_segments_and_keys_give_values_by_time():
    segments = passes._segments([{"until": 1.0, "sigma": 3.0}, {"until": 2.0, "sigma": 0.8}, {"sigma": 5.0}], "room.blur")
    assert [passes.segment_value(segments, t) for t in (0.0, 0.999, 1.0, 1.5, 2.0, 9.0)] == [3.0, 3.0, 0.8, 0.8, 5.0, 5.0]
    assert passes.segment_value(passes._segments(2.5, "x"), 7.0) == 2.5
    with pytest.raises(PipelineError, match="until が要ります"):
        passes._segments([{"sigma": 1.0}, {"sigma": 2.0}], "room.blur")
    with pytest.raises(PipelineError, match="前の区間より後"):
        passes._segments([{"until": 2.0, "sigma": 1.0}, {"until": 1.0, "sigma": 2.0}], "room.blur")
    keys = [[0.0, 0.2], [0.1, 0.8], [1.0, 1.0], [2.0, 0.0, "smooth"]]
    assert passes.level_at(keys, 0.05) == pytest.approx(0.5) and passes.level_at(keys, 1.5) == pytest.approx(0.5)
    assert passes.level_at(0.7, 3.0) == 0.7
    flash = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.14], [1.1, 0.0]]          # 同じ時刻のキーで跳ぶ（閃光の立ち上がり）
    assert passes.level_at(flash, 0.99) == 0.0 and passes.level_at(flash, 1.0) == pytest.approx(0.14)
    assert passes.level_at(flash, 1.05) == pytest.approx(0.07)


def test_the_spec_is_checked_and_filled(tmp_path):
    _passes(tmp_path)
    spec = passes.load_spec(_spec(tmp_path, rim={"rgb": [255, 180, 110], "strength": 0.9, "band": "blur"}))
    data = spec.data
    assert data["output"] == {"crf": 18, "frames": False} and data["floor_shadow"] == passes.FLOOR_SHADOW
    assert data["rim"]["band"] == "blur" and data["rim"]["kernel"] == 7.0 and data["bloom"] is None and data["look"] is None
    assert passes.load_spec(_spec(tmp_path, floor_shadow=None)).data["floor_shadow"] is None
    for bad, word in (({"room": {"blur": -1}}, "invalid"), ({"rim": {"rgb": [1, 2, 3]}}, "invalid"),
                      ({"level": [[1.0, 1.0], [0.5, 0.0]]}, "昇順"), ({"flash": {"keys": [[0, 1, "jump"]]}}, "invalid"),
                      ({"passes": "../outside.json"}, None)):
        with pytest.raises(PipelineError, match=word):
            passes.load_spec(_spec(tmp_path, **bad))


def test_the_passes_list_must_line_up(tmp_path):
    plan = passes.load_passes(_passes(tmp_path))
    assert plan.frames == [1, 2, 3] and plan.size == (W, H) and set(plan.files) == {"room", "char", "disc"}
    with pytest.raises(PipelineError, match="1 ずつ"):
        passes.load_passes(_passes(tmp_path / "gap", gap=True))
    with pytest.raises(PipelineError, match="char"):
        passes.load_passes(_passes(tmp_path / "nochar", kinds=("room",)))
    path = _passes(tmp_path / "changed")
    (tmp_path / "changed" / "passes" / "room" / "f0002.png").write_bytes(b"changed")
    with pytest.raises(PipelineError, match="変わっています"):
        passes.load_passes(path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["passes"]["room"]["missing"] = [3]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PipelineError, match="描いていない"):
        passes.load_passes(path, verify=False)


def test_the_layers_are_put_together_in_order(tmp_path):
    _passes(tmp_path)
    plain = passes.load_spec(_spec(tmp_path, floor_shadow=None))
    plan = passes.load_passes(tmp_path / "passes.json")
    frame = passes.compose(plain.data, plan, 1).astype(int)
    assert tuple(frame[20, 16]) == (200, 0, 0) and tuple(frame[2, 2]) == (100, 100, 100)          # 人物の上と、部屋だけの所
    shadowed = passes.compose(passes.load_spec(_spec(tmp_path)).data, plan, 1).astype(int)
    assert shadowed[36, 16, 0] < 100 and tuple(shadowed[20, 16]) == (200, 0, 0)                    # 円の所だけ部屋が暗い
    dim = passes.compose(passes.load_spec(_spec(tmp_path, floor_shadow=None, room={"level": 0.5}, level=[[0.0, 0.5]])).data, plan, 1)
    assert tuple(dim[2, 2].astype(int)) == (25, 25, 25) and tuple(dim[20, 16].astype(int)) == (100, 0, 0)
    flash = passes.load_spec(_spec(tmp_path, floor_shadow=None, flash={"keys": [[0.0, 0.5]], "color": [255, 255, 255]}))
    lit = passes.compose(flash.data, plan, 1).astype(int)
    assert tuple(lit[2, 2]) == (178, 178, 178)                     # スクリーン合成: 100 + (255 − 100) × 0.5
    blurred = passes.load_spec(_spec(tmp_path, floor_shadow=None, room={"blur": [{"until": 0.04, "sigma": 0.0}, {"sigma": 4.0}]}))
    assert np.array_equal(passes.compose(blurred.data, plan, 1), frame)                         # T 1（0 秒）はぼかさない
    rim = passes.load_spec(_spec(tmp_path, floor_shadow=None, rim={"rgb": [0, 255, 0], "strength": 1.0, "kernel": 200, "blur": 0, "gradient": None}))
    ring = passes.compose(rim.data, plan, 1).astype(int)                # 幅 32 の画面なので、幅 1080 の 200 画素 ≒ 6 画素
    assert ring[10, 16, 1] > 0 and ring[20, 16, 1] == 0           # 縁のすぐ内側だけに光が足される


def test_the_blur_band_of_the_rim_light():
    alpha = np.zeros((40, 40), np.float32)
    alpha[10:30, 10:30] = 1.0
    fg = np.zeros((40, 40, 3), np.float32)
    out = finish.rim_light(fg, alpha, [255, 255, 255], 1.0, kernel=3, blur=0, gradient=None, clip=False, band="blur")
    want = np.clip(alpha - cv2.GaussianBlur(alpha, (0, 0), 3), 0, 1)
    assert np.allclose(out[..., 0], want) and 0.35 < out[10, 20, 0] < 0.55 and out[20, 20, 0] < 0.01
    eroded = finish.rim_light(fg, alpha, [255, 255, 255], 1.0, kernel=3, blur=0, gradient=None, clip=False)
    assert eroded[10, 20, 0] == pytest.approx(1.0) and eroded[12, 20, 0] == 0.0    # 既定（削った帯）は変わらない
    assert finish.blur(alpha, 0.1) is alpha and not np.array_equal(finish.blur(alpha, 1.0), alpha)


def test_the_run_writes_the_video_and_frames(tmp_path):
    _passes(tmp_path, frames=4)
    spec = _spec(tmp_path, output={"crf": 20, "frames": True}, bloom={"threshold": 0.3, "ceiling": None},
                 look={"vignette": {"amount": 0.2}})
    result = passes.render_run(spec, out=tmp_path / "runs", quiet=True)
    assert result["status"] == "succeeded" and result["technical_pass"] is True and not result["reused"]
    run = Path(result["run_dir"])
    assert (run / "dance.mp4").is_file() and sorted(p.name for p in (run / "frames").iterdir()) == [f"f{T:04d}.png" for T in range(1, 5)]
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["passes"] == ["char", "disc", "room"] and manifest["interval"]["T"] == [1, 4]
    assert manifest["variants"]["dance"]["checks"]["pass"] is True
    again = passes.render_run(spec, out=tmp_path / "runs", quiet=True)
    assert again["reused"] and again["run_id"] == result["run_id"]
    from mv_pipeline.dance import VARIANT
    from mv_pipeline.fx import runs as fxruns
    fxruns.accept(run, VARIANT, "hold", "test", None)
    assert "hold" in (tmp_path / "runs" / "acceptance.jsonl").read_text(encoding="utf-8")


def test_every_pass_must_have_the_listed_size(tmp_path):
    _passes(tmp_path)
    plan = passes.load_passes(tmp_path / "passes.json")
    _write(tmp_path / "passes" / "room" / "f0001.png", np.full((H // 2, W // 2, 3), 100, np.uint8))
    plan.files["room"][1] = tmp_path / "passes" / "room" / "f0001.png"
    with pytest.raises(PipelineError, match="room のコマ T 1 の大きさ"):
        passes.compose(passes.load_spec(_spec(tmp_path)).data, plan, 1)


def test_a_short_song_is_padded_to_the_video(tmp_path):
    import wave
    with wave.open(str(tmp_path / "song.wav"), "wb") as song:      # 0.05 秒の音
        song.setnchannels(1)
        song.setsampwidth(2)
        song.setframerate(48000)
        song.writeframes(bytes(2 * 2400))
    warnings = []
    plan = passes.audio_plan(tmp_path / "song.wav", 0.0, 0.1, warnings)
    assert plan["pad"] is True and plan["length"] == pytest.approx(0.05, abs=0.011) and warnings
    assert passes.audio_plan(tmp_path / "song.wav", 0.0, 0.04, []) == {"path": tmp_path / "song.wav", "start": 0.0, "duration": 0.04}
    with pytest.raises(PipelineError, match="読み始め"):
        passes.audio_plan(tmp_path / "song.wav", 1.0, 0.1, [])
    _passes(tmp_path, frames=6)
    spec = _spec(tmp_path, audio={"path": "song.wav"})
    result = passes.render_run(spec, out=tmp_path / "runs", quiet=True)           # 6 コマ（0.2 秒）に 0.05 秒の音: 無音で足して通す
    assert result["status"] == "succeeded" and result["technical_pass"] is True and any("無音" in w for w in result["warnings"])


def test_the_audio_starts_at_the_first_listed_T(tmp_path, monkeypatch):
    _passes(tmp_path, frames=3)
    manifest = json.loads((tmp_path / "passes.json").read_text(encoding="utf-8"))
    for entry in manifest["passes"].values():                       # T 31〜33 の一覧（30 fps なら 1 秒目から）
        for fr in entry["frames"]:
            fr["T"] += 30
    (tmp_path / "passes.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "song.wav").write_bytes(b"")
    seen = {}

    def plan(path, start, seconds, warnings):
        seen["start"], seen["seconds"] = start, seconds
        return None
    monkeypatch.setattr(passes, "audio_plan", plan)
    result = passes.render_run(_spec(tmp_path, audio={"path": "song.wav", "offset_s": 0.5}), out=tmp_path / "runs", quiet=True)
    assert result["status"] == "succeeded" and seen["start"] == pytest.approx(1.5) and seen["seconds"] == pytest.approx(0.1)


def test_look_grade_keys_must_be_in_order(tmp_path):
    _passes(tmp_path)
    bad = {"grade": {"keys": [{"t": 0, "sat": 1, "gain": [1, 1, 1], "con": 0}, {"t": 2, "sat": 1, "gain": [1, 1, 1], "con": 0},
                              {"t": 1, "sat": 1, "gain": [1, 1, 1], "con": 0}]}}
    with pytest.raises(PipelineError, match="look.grade.keys"):
        passes.load_spec(_spec(tmp_path, look=bad))
    empty = passes.load_spec(_spec(tmp_path, look={}))
    assert any(w.startswith("look") for w in empty.warnings)
