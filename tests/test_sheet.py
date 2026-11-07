"""`mv sheet`: 指定した時刻か timeline の出来事のコマを 1 枚に並べる（閲覧用。合否なし）。合成の連番と動画で確かめる。"""
import subprocess

import numpy as np
import pytest
from fx_synth import run_cli
from PIL import Image

from mv_pipeline import media, sheet
from mv_pipeline.io import PipelineError, atomic_json, read_json

W, H, FPS, N = 64, 48, 24, 24


def _sequence(folder, transparent=True):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(N):
        if transparent:
            rgba = np.zeros((H, W, 4), np.uint8)
            rgba[10:30, 8:8 + i * 2 + 2] = (255, 40, 40, 200)      # コマごとに伸びる半透明の赤
            Image.fromarray(rgba, "RGBA").save(folder / f"f_{i:04d}.png")
        else:
            rgb = np.full((H, W, 3), (i * 10, 20, 60), np.uint8)
            Image.fromarray(rgb, "RGB").save(folder / f"f_{i:04d}.png")


def test_transparent_sequence_is_laid_over_each_background_with_labels(tmp_path):
    _sequence(tmp_path / "frames")
    result = sheet.build(tmp_path / "frames", tmp_path / "sheet.png", at=[0.0, 0.5], fps=FPS, cell_width=64)
    assert result["cells"] == 6 and result["rows"] == 2 and result["transparent"] is True and result["missing"] == 0
    image = np.asarray(Image.open(tmp_path / "sheet.png").convert("RGB"))
    assert image.shape[1] == 3 * 64 and image.shape[0] == result["size"][1]
    record = read_json(tmp_path / "sheet.json")
    assert [c["background"] for c in record["cells"]] == ["white", "black", "gray"] * 2
    assert [c["frame"] for c in record["cells"]][::3] == [0, 12] and record["cells"][3]["event"] == "指定" and record["cells"][3]["event_ascii"] == "given"
    assert record["cells"][3]["sha256"] == __import__("hashlib").sha256((tmp_path / "frames" / "f_0012.png").read_bytes()).hexdigest()
    assert record["labels"] in ("full", "ascii") and record["fps_origin"] == "--fps"
    band = record["size"][1] // 2 - 1
    row2 = image[record["size"][1] // 2:, :, :]      # 2 行目（0.5 秒 = コマ 12）
    label_band = max(14, int(round(H * 64 / W * 0.11)))
    white_cell = row2[label_band + 2:label_band + 8, 2:6]       # 白の背景の、字の外の画素
    black_cell = row2[label_band + 2:label_band + 8, 64 + 2:64 + 6]
    assert white_cell.min() > 240 and black_cell.max() < 15 and band > 0


def test_events_come_from_the_timeline_and_out_of_range_times_are_listed(tmp_path):
    _sequence(tmp_path / "frames")
    timeline = {"fps": FPS, "duration_s": N / FPS, "ui_regions": [], "seams": [0.25], "flashes": [0.75, 5.0],
                "lyrics": [{"layer": "band", "text": "one", "start": 0.0, "end": 0.5, "box": [0, 0, 1, 1]}]}
    atomic_json(tmp_path / "timeline.json", timeline)
    result = sheet.build(tmp_path / "frames", tmp_path / "s.png", events_path=tmp_path / "timeline.json", backgrounds=("white",), cell_width=64)
    record = read_json(tmp_path / "s.json")
    assert [c["event"] for c in record["cells"]] == ["行の開始: one", "切れ目", "フラッシュ"] and result["missing"] == 1
    assert [c["event_ascii"] for c in record["cells"]] == ["line 1", "seam", "flash"]
    assert record["missing"][0]["t"] == 5.0 and record["events"]["kinds"] == list(sheet.KINDS) and record["fps"] == FPS and record["fps_origin"] == "timeline"
    result = sheet.build(tmp_path / "frames", tmp_path / "t.png", events_path=tmp_path / "timeline.json", backgrounds=("white",), kinds=("flashes",))
    assert result["cells"] == 1
    # 一部の区間だけ描いた run: timeline の時刻はショットの時刻で、動画は interval.start から始まる
    partial = {**timeline, "interval": {"start": 10.0, "duration": N / FPS, "frames": N}, "seams": [], "flashes": [10.5],
               "lyrics": [{"layer": "band", "text": "late", "start": 10.0, "end": 10.5, "box": [0, 0, 1, 1]}]}
    atomic_json(tmp_path / "partial.json", partial)
    sheet.build(tmp_path / "frames", tmp_path / "p.png", events_path=tmp_path / "partial.json", backgrounds=("white",))
    record = read_json(tmp_path / "p.json")
    assert [(c["t"], c["frame"]) for c in record["cells"]] == [(10.0, 0), (10.5, 12)] and record["events"]["interval_start"] == 10.0


def test_opaque_video_wraps_times_into_columns(tmp_path):
    _sequence(tmp_path / "frames", transparent=False)
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-framerate", str(FPS), "-i", str(tmp_path / "frames" / "f_%04d.png"),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "10", str(tmp_path / "v.mp4")], check=True)
    result = sheet.build(tmp_path / "v.mp4", tmp_path / "v.png", at=[0.0, 0.25, 0.5, 0.75, 0.95], columns=2, cell_width=64)
    assert result["cells"] == 5 and result["rows"] == 3 and result["transparent"] is False
    record = read_json(tmp_path / "v.json")
    assert [c["frame"] for c in record["cells"]] == [0, 6, 12, 18, 23] and record["source"]["kind"] == "video" and "files" not in record["source"]
    assert record["size"] == [128, record["size"][1]] and all(len(c["sha256"]) == 64 for c in record["cells"])
    with pytest.raises(PipelineError, match="--fps"):
        sheet.build(tmp_path / "v.mp4", tmp_path / "x.png", at=[0.0], fps=FPS + 1)
    assert sheet.build(tmp_path / "v.mp4", tmp_path / "y.png", at=[0.0], fps=FPS * 1.001, cell_width=64)["cells"] == 1   # 1% 未満の差は通す


def test_arguments_are_checked(tmp_path):
    _sequence(tmp_path / "frames")
    with pytest.raises(PipelineError, match="どちらか一方"):
        sheet.build(tmp_path / "frames", tmp_path / "x.png", fps=FPS)
    with pytest.raises(PipelineError, match="--fps"):
        sheet.build(tmp_path / "frames", tmp_path / "x.png", at=[0.0])
    with pytest.raises(PipelineError, match="--backgrounds"):
        sheet.build(tmp_path / "frames", tmp_path / "x.png", at=[0.0], fps=FPS, backgrounds=("pink",))
    with pytest.raises(PipelineError, match="どの時刻も"):
        sheet.build(tmp_path / "frames", tmp_path / "x.png", at=[9.0], fps=FPS)


def test_unpadded_names_palette_without_transparency_and_uneven_sizes(tmp_path):
    folder = tmp_path / "unpadded"
    folder.mkdir()
    for i in range(12):
        rgba = np.zeros((H, W, 4), np.uint8)
        rgba[0, 0] = (i, 0, 0, 255)
        Image.fromarray(rgba, "RGBA").save(folder / f"frame{i}.png")
    source = sheet.describe_source(folder, fps=FPS)
    assert [p.name for p in source["files"]] == [f"frame{i}.png" for i in range(12)]
    assert int(sheet._load_frames(source, [10])[10][0][0, 0][0]) == 10
    palette = tmp_path / "palette"
    palette.mkdir()
    Image.new("P", (W, H)).save(palette / "f0.png")
    assert sheet.describe_source(palette, fps=FPS)["transparent"] is False
    keyed = tmp_path / "keyed"
    keyed.mkdir()
    Image.new("RGB", (W, H), (1, 2, 3)).save(keyed / "f0.png", transparency=(1, 2, 3))       # tRNS の透明色
    assert sheet.describe_source(keyed, fps=FPS)["transparent"] is True
    uneven = tmp_path / "uneven"
    uneven.mkdir()
    Image.new("RGBA", (W, H)).save(uneven / "f0.png")
    Image.new("RGBA", (W, H + 8)).save(uneven / "f1.png")
    with pytest.raises(PipelineError, match="寸法がそろっていません"):
        sheet.build(uneven, tmp_path / "u.png", at=[0.0, 1 / FPS], fps=FPS, cell_width=64)
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    Image.new("RGB", (W, H)).save(mixed / "f0.png")
    Image.new("RGBA", (W, H)).save(mixed / "f1.png")
    with pytest.raises(PipelineError, match="透過の有無がそろっていません"):
        sheet.build(mixed, tmp_path / "m.png", at=[0.0, 1 / FPS], fps=FPS, cell_width=64)


def test_labels_fall_back_to_ascii_without_a_cjk_font(tmp_path, monkeypatch):
    _sequence(tmp_path / "frames")
    monkeypatch.setattr(sheet, "FONT_CANDIDATES", ())
    result = sheet.build(tmp_path / "frames", tmp_path / "s.png", at=[0.0], fps=FPS, backgrounds=("white",), cell_width=64)
    assert result["labels"] == "ascii" and read_json(tmp_path / "s.json")["label_font"] is None
    with pytest.raises(PipelineError, match="--font"):
        sheet.build(tmp_path / "frames", tmp_path / "t.png", at=[0.0], fps=FPS, font=tmp_path / "missing.ttf")


def test_cli(tmp_path):
    _sequence(tmp_path / "frames")
    code, result, err = run_cli("sheet", tmp_path / "frames", "--out", tmp_path / "out" / "sheet.png", "--at", "0", "0.5", "--fps", str(FPS),
                                "--backgrounds", "white,black", "--cell-width", "64")
    assert code == 0, err
    assert result["cells"] == 4 and (tmp_path / "out" / "sheet.png").is_file() and (tmp_path / "out" / "sheet.json").is_file()
