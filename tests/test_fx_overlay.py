"""`mv overlay`: 透過レイヤー（PNG 連番・アルファつき動画）を土台に重ねて書き出す。合成素材だけで確かめる。"""
import json
import subprocess
import wave

import numpy as np
import pytest
from fx_synth import run_cli
from PIL import Image

from mv_pipeline import media
from mv_pipeline.fx import overlay, probe
from mv_pipeline.io import PipelineError, read_json, sha256

SIZE = (64, 48)
FPS = 12
N = 6


def _base_image(path, color=(40, 80, 160)):
    Image.new("RGB", SIZE, color).save(path)


def _layer_frames(folder, count=N, alpha=128, color=(255, 0, 0), box=(16, 12, 48, 36)):
    """半透明の赤い箱（straight α）。箱の外はアルファ 0 で色は黒（色を持たない）。"""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        rgba = np.zeros((SIZE[1], SIZE[0], 4), np.uint8)
        x0, y0, x1, y1 = box
        rgba[y0:y1, x0:x1] = (*color, alpha)
        Image.fromarray(rgba, "RGBA").save(folder / f"frame_{i:04d}.png")


def _audio(path, seconds=N / FPS):
    rate = 48000
    t = np.arange(int(rate * seconds)) / rate
    pcm = (0.2 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(np.repeat(pcm, 2).tobytes())


def _first_frame_rgb(video):
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-i", str(video), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(SIZE[1], SIZE[0], 3)


def test_composite_is_straight_alpha_in_srgb():
    under = np.full((2, 2, 3), 100, np.uint8)
    rgba = np.zeros((2, 2, 4), np.uint8)
    rgba[0, 0] = (200, 0, 0, 255)      # 不透明: レイヤーの色
    rgba[0, 1] = (200, 0, 0, 128)      # 半透明: 線形化せずに混ぜる（200·128/255 + 100·127/255 ≈ 150）
    rgba[1, 0] = (255, 255, 255, 0)    # アルファ 0: 色を持たず、下が残る
    out = overlay.composite(under, rgba)
    assert tuple(out[0, 0]) == (200, 0, 0) and tuple(out[1, 0]) == (100, 100, 100) and tuple(out[1, 1]) == (100, 100, 100)
    assert abs(int(out[0, 1][0]) - 150) <= 1 and abs(int(out[0, 1][1]) - 50) <= 1


def test_image_base_with_a_png_sequence_and_audio_passes_and_records_every_frame(tmp_path):
    _base_image(tmp_path / "base.png")
    _layer_frames(tmp_path / "layer")
    _audio(tmp_path / "song.wav")
    result = overlay.run_overlay(tmp_path / "base.png", [tmp_path / "layer"], tmp_path / "song.wav", tmp_path / "runs", fps=FPS, quiet=True)
    assert result["status"] == "succeeded" and result["technical_pass"] is True and result["frames"] == N and result["reused"] is False
    run_dir = tmp_path / "runs" / result["run_id"]
    manifest = read_json(run_dir / "manifest.json")
    record = manifest["overlay"]
    assert record["schema_version"] == "fx-overlay/0.2" and record["base"]["kind"] == "image" and record["base"]["frames"] is None
    assert [f["name"] for f in record["layers"][0]["files"]] == [f"frame_{i:04d}.png" for i in range(N)]
    assert all(f["sha256"] == sha256(tmp_path / "layer" / f["name"]) for f in record["layers"][0]["files"])
    assert record["audio"]["sha256"] == sha256(tmp_path / "song.wav") and record["settings"] == {
        "fps": float(FPS), "crf": 18, "size": list(SIZE), "frames": N, "blend": "srgb"}
    assert record["layers"][0]["options"]["alpha"] == "straight" and record["layers"][0]["options"]["style"] is None   # 0.2 はレイヤーごと
    checks = manifest["variants"]["overlay"]["checks"]
    assert checks["pass"] and checks["video"]["matrix"] == "bt709" and checks["audio"]["decoded_s"] == pytest.approx(N / FPS, abs=0.11)
    frame = _first_frame_rgb(run_dir / "overlay.mp4")
    outside, inside = frame[2, 2].astype(int), frame[24, 32].astype(int)
    assert np.abs(outside - np.array([40, 80, 160])).max() <= 6            # 箱の外は土台の色（圧縮の誤差だけ）
    assert inside[0] > 120 and inside[2] < 110                              # 箱の中は赤が混ざる
    index = read_json(tmp_path / "runs" / "index.json")
    assert index["runs"][manifest["key"]]["run_id"] == result["run_id"]
    again = overlay.run_overlay(tmp_path / "base.png", [tmp_path / "layer"], tmp_path / "song.wav", tmp_path / "runs", fps=FPS, quiet=True)
    assert again["reused"] is True and again["run_id"] == result["run_id"]


def test_mismatched_frame_counts_sizes_and_missing_fps_are_errors(tmp_path):
    _base_image(tmp_path / "base.png")
    _layer_frames(tmp_path / "a")
    _layer_frames(tmp_path / "b", count=N - 1)
    with pytest.raises(PipelineError, match="--fps"):
        overlay.run_overlay(tmp_path / "base.png", [tmp_path / "a"], out=tmp_path / "runs")
    with pytest.raises(PipelineError, match="コマ数が合いません"):
        overlay.run_overlay(tmp_path / "base.png", [tmp_path / "a", tmp_path / "b"], out=tmp_path / "runs", fps=FPS)
    small = tmp_path / "small"
    small.mkdir()
    for i in range(N):
        Image.new("RGBA", (32, 24), (0, 0, 0, 0)).save(small / f"f{i}.png")
    with pytest.raises(PipelineError, match="寸法が合いません"):
        overlay.run_overlay(tmp_path / "base.png", [small], out=tmp_path / "runs", fps=FPS)
    with pytest.raises(PipelineError, match="--layer か --layers を指定"):
        overlay.run_overlay(tmp_path / "base.png", [], out=tmp_path / "runs", fps=FPS)
    assert not (tmp_path / "runs").exists()      # 入力の検証で止まった分は run を作らない


def test_video_base_and_alpha_video_layer(tmp_path):
    """土台が動画なら fps とコマ数は動画から。レイヤーは PNG 連番を ffmpeg で rgba の動画にしたもの。"""
    _layer_frames(tmp_path / "layer")
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-framerate", str(FPS), "-i", str(tmp_path / "layer" / "frame_%04d.png"),
                    "-c:v", "png", "-pix_fmt", "rgba", str(tmp_path / "layer.mov")], check=True)
    _base_image(tmp_path / "base.png", (10, 200, 30))
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(tmp_path / "base.png"),
                    "-frames:v", str(N), "-pix_fmt", "yuv420p", "-c:v", "libx264", str(tmp_path / "base.mp4")], check=True)
    result = overlay.run_overlay(tmp_path / "base.mp4", [tmp_path / "layer.mov"], out=tmp_path / "runs", quiet=True)
    assert result["technical_pass"] is True and result["frames"] == N and result["fps"] == pytest.approx(FPS)
    record = read_json(tmp_path / "runs" / result["run_id"] / "manifest.json")["overlay"]
    assert record["base"]["kind"] == "video" and record["base"]["frames"] == N and record["layers"][0]["kind"] == "video"
    assert record["layers"][0]["sha256"] == sha256(tmp_path / "layer.mov") and record["audio"] is None
    with pytest.raises(PipelineError, match="fps"):
        overlay.run_overlay(tmp_path / "base.mp4", [tmp_path / "layer.mov"], out=tmp_path / "runs", fps=FPS + 1)
    with pytest.raises(PipelineError, match="アルファがありません"):
        overlay.run_overlay(tmp_path / "base.mp4", [tmp_path / "base.mp4"], out=tmp_path / "runs")


def test_sequence_order_is_numeric_and_opaque_sequences_are_rejected(tmp_path):
    folder = tmp_path / "unpadded"
    folder.mkdir()
    for i in range(12):
        rgba = np.zeros((SIZE[1], SIZE[0], 4), np.uint8)
        rgba[0, 0] = (i, 0, 0, 255)                        # コマ番号を左上の画素に入れる
        Image.fromarray(rgba, "RGBA").save(folder / f"frame{i}.png")
    layer = overlay.describe_layer(folder)
    assert [f["name"] for f in layer["files"]] == [f"frame{i}.png" for i in range(12)]
    _base_image(tmp_path / "base.png")
    frames = list(overlay.frames({"kind": "image", "path": str(tmp_path / "base.png"), "size": list(SIZE), "frames": None}, [layer], 12))
    assert [int(f[0, 0][0]) for f in frames] == list(range(12))
    opaque = tmp_path / "opaque"
    opaque.mkdir()
    Image.new("RGB", SIZE, (1, 2, 3)).save(opaque / "f0.png")
    with pytest.raises(PipelineError, match="アルファがありません"):
        overlay.describe_layer(opaque)


def test_alpha_pix_fmt_is_checked_by_format_not_by_spelling():
    assert all(overlay.has_alpha_pix_fmt(f) for f in ("rgba", "bgra", "yuva420p", "gbrap", "ya8", "rgba64le"))
    assert not any(overlay.has_alpha_pix_fmt(f) for f in ("gray", "yuv420p", "rgb24", "gbrp", None, ""))


def test_layers_stack_bottom_to_top(tmp_path):
    _base_image(tmp_path / "base.png", (0, 0, 0))
    _layer_frames(tmp_path / "red", alpha=255, color=(255, 0, 0))
    _layer_frames(tmp_path / "blue", alpha=255, color=(0, 0, 255), box=(16, 12, 32, 36))
    result = overlay.run_overlay(tmp_path / "base.png", [tmp_path / "red", tmp_path / "blue"], out=tmp_path / "runs", fps=FPS, crf=10, quiet=True)
    frame = _first_frame_rgb(tmp_path / "runs" / result["run_id"] / "overlay.mp4").astype(int)
    assert frame[24, 20][2] > 200 and frame[24, 20][0] < 40      # 左半分は上の青
    assert frame[24, 40][0] > 200 and frame[24, 40][2] < 40      # 右半分は下の赤


def test_cli_runs_and_a_failed_check_leaves_a_failed_run(tmp_path, monkeypatch):
    _base_image(tmp_path / "base.png")
    _layer_frames(tmp_path / "layer")
    code, result, err = run_cli("overlay", "--base", tmp_path / "base.png", "--layer", tmp_path / "layer", "--fps", str(FPS),
                                "--out", tmp_path / "runs", "--quiet")
    assert code == 0, err
    assert result["technical_pass"] is True and result["layers"] == 1 and result["audio"] is False
    original = probe.check_video
    monkeypatch.setattr(probe, "check_video", lambda *a, **k: {**original(*a, **k), "pass": False, "failures": ["テスト"]})
    with pytest.raises(PipelineError, match="技術検査"):
        overlay.run_overlay(tmp_path / "base.png", [tmp_path / "layer"], out=tmp_path / "runs", fps=FPS, force=True, quiet=True)
    manifests = [read_json(p) for p in (tmp_path / "runs").glob("*/manifest.json")]
    assert sorted(m["status"] for m in manifests) == ["failed", "succeeded"]
    assert json.loads(json.dumps(manifests[0]["overlay"]))["schema_version"] == "fx-overlay/0.2"
