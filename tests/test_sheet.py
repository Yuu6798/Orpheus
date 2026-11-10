"""`mv sheet`: 指定した時刻・等間隔の時刻・timeline の出来事のコマを 1 枚に並べる（閲覧用。合否なし）。合成の連番と動画で確かめる。"""
import hashlib
import io
import subprocess

import numpy as np
import pytest
from fx_synth import run_cli
from PIL import Image

from mv_pipeline import encode, media, sheet, validate
from mv_pipeline.io import PipelineError, atomic_image, atomic_json, read_json

W, H, FPS, N = 64, 48, 24, 24
VW, VH, BLOCK = 96, 64, 16
PNG_SIGNATURE, JPEG_START = b"\x89PNG\r\n\x1a\n", b"\xff\xd8"


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
    with pytest.raises(PipelineError, match="どれかで指定"):
        sheet.build(tmp_path / "frames", tmp_path / "x.png", fps=FPS)
    atomic_json(tmp_path / "timeline.json", {"fps": FPS, "flashes": [0.5]})
    for extra in ({}, {"every": 0.5}):                                   # --at と --events は、--every があっても一緒には使えない
        with pytest.raises(PipelineError, match="どちらか一方"):
            sheet.build(tmp_path / "frames", tmp_path / "x.png", at=[0.0], events_path=tmp_path / "timeline.json", **extra)
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


# ---- 等間隔の時刻（--every・--count） ---------------------------------------------------------

def _numbered(i):
    """コマ i の絵。16 画素の升目で、升 (bx, by) の値は 20 + 4·i + 40·((bx + 2·by) % 4)。コマ番号も場所も画素値で読める。"""
    by, bx = np.mgrid[0:VH, 0:VW] // BLOCK
    gray = (20 + 4 * i + 40 * ((bx + 2 * by) % 4)).astype(np.uint8)
    return np.repeat(gray[..., None], 3, axis=2)


def _numbered_video(path):
    encode.write_video(path, (_numbered(i) for i in range(N)), (VW, VH), FPS, 10, N)
    return path


def _pictures(path, record, picture_height):
    """シートの各セルの絵（字の帯の下）を、並べた順に返す。picture_height は絵の高さ。"""
    with Image.open(path) as image:
        pixels = np.asarray(image.convert("RGB"))
    width, columns = record["cell_width"], record["columns"]
    height = pixels.shape[0] // -(-len(record["cells"]) // columns)
    band = height - picture_height
    return [pixels[(c // columns) * height + band:(c // columns + 1) * height, (c % columns) * width:(c % columns + 1) * width]
            for c in range(len(record["cells"]))]


def _frame_number(picture):
    """原寸のコマの絵の左上の升（値 20 + 4·i）からコマ番号を読む。"""
    return int(round((float(picture[2:BLOCK - 2, 2:BLOCK - 2].mean()) - 20) / 4))


def _cells(path):
    return [(c["t"], c["frame"], c["event"]) for c in read_json(path)["cells"]]


def test_every_and_count_pick_evenly_spaced_frames(tmp_path):
    video = _numbered_video(tmp_path / "v.mp4")
    result = sheet.build(video, tmp_path / "e.png", every=0.25, cell_width=VW)
    record = read_json(tmp_path / "e.json")
    assert [(c["t"], c["frame"]) for c in record["cells"]] == [(0.0, 0), (0.25, 6), (0.5, 12), (0.75, 18)]     # 長さ（1.0 秒）ちょうどは入らない
    assert {(c["event"], c["event_ascii"]) for c in record["cells"]} == {("等間隔", "every")}
    assert record["every"] == 0.25 and record["count"] is None and result["missing"] == 0 and record["missing"] == []
    assert [_frame_number(p) for p in _pictures(tmp_path / "e.png", record, VH)] == [0, 6, 12, 18]             # 絵もそのコマ
    sheet.build(video, tmp_path / "c.png", count=4, cell_width=VW)
    record = read_json(tmp_path / "c.json")
    assert [(c["t"], c["frame"]) for c in record["cells"]] == [(0.125, 3), (0.375, 9), (0.625, 15), (0.875, 21)]   # 4 等分の各区間の中央
    assert {(c["event"], c["event_ascii"]) for c in record["cells"]} == {("等分", "count")} and record["count"] == 4 and record["every"] is None
    assert [_frame_number(p) for p in _pictures(tmp_path / "c.png", record, VH)] == [3, 9, 15, 21]
    # 最後の半コマの時刻（0.99 秒。いちばん近いコマは 24 = 動画の外）は、動画の中の時刻なので最後のコマにする
    sheet.build(video, tmp_path / "l.png", every=0.33, cell_width=VW)
    record = read_json(tmp_path / "l.json")
    assert [c["frame"] for c in record["cells"]] == [0, 8, 16, 23] and record["missing"] == []
    assert [c["t"] for c in record["cells"]] == pytest.approx([0.0, 0.33, 0.66, 0.99])
    assert sheet.build(video, tmp_path / "o.png", every=5.0, cell_width=VW)["cells"] == 1                       # 長さより長い間隔は 0 秒だけ


def test_spaced_times_join_the_given_times_in_time_order_without_repeating_a_frame(tmp_path):
    video = _numbered_video(tmp_path / "v.mp4")
    sheet.build(video, tmp_path / "a.png", at=[0.9, 0.5], every=0.25, count=3, cell_width=VW)
    record = read_json(tmp_path / "a.json")
    assert [c["frame"] for c in record["cells"]] == [0, 4, 6, 12, 18, 20, 22]                                  # 時刻順。コマ 12 は 1 つだけ
    assert [c["event"] for c in record["cells"]] == ["等間隔", "等分", "等間隔", "指定", "等間隔", "等分", "指定"]   # 重なったコマは --at が残る
    assert [c["t"] for c in record["cells"]] == sorted(c["t"] for c in record["cells"])
    assert [_frame_number(p) for p in _pictures(tmp_path / "a.png", record, VH)] == [0, 4, 6, 12, 18, 20, 22]
    sheet.build(video, tmp_path / "b.png", every=0.5, count=1, cell_width=VW)                                 # --every と --count が同じコマなら --every
    assert _cells(tmp_path / "b.json") == [(0.0, 0, "等間隔"), (0.5, 12, "等間隔")]


def test_spaced_times_join_the_timeline_events(tmp_path):
    _sequence(tmp_path / "frames")
    timeline = {"fps": FPS, "duration_s": N / FPS, "ui_regions": [], "seams": [0.25], "flashes": [0.75, 5.0],
                "lyrics": [{"layer": "band", "text": "one", "start": 0.0, "end": 0.5, "box": [0, 0, 1, 1]}]}
    atomic_json(tmp_path / "timeline.json", timeline)
    result = sheet.build(tmp_path / "frames", tmp_path / "s.png", events_path=tmp_path / "timeline.json", every=0.25, backgrounds=("white",), cell_width=64)
    assert _cells(tmp_path / "s.json") == [(0.0, 0, "行の開始: one"), (0.25, 6, "切れ目"), (0.5, 12, "等間隔"), (0.75, 18, "フラッシュ")]
    assert result["missing"] == 1 and read_json(tmp_path / "s.json")["missing"][0]["t"] == 5.0               # 動画の外の出来事は今までどおり missing
    # 同じコマの出来事 2 つは今までどおり両方並べる。譲るのは等間隔・等分の時刻だけ
    atomic_json(tmp_path / "same.json", {**timeline, "seams": [0.5], "flashes": [0.5], "lyrics": []})
    sheet.build(tmp_path / "frames", tmp_path / "t.png", events_path=tmp_path / "same.json", every=0.5, backgrounds=("white",), cell_width=64)
    assert _cells(tmp_path / "t.json") == [(0.0, 0, "等間隔"), (0.5, 12, "フラッシュ"), (0.5, 12, "切れ目")]
    # 一部の区間だけ描いた run: 等間隔の時刻も timeline の時刻（interval.start から）で書く
    partial = {**timeline, "interval": {"start": 10.0, "duration": N / FPS, "frames": N}, "seams": [], "flashes": [10.5],
               "lyrics": [{"layer": "band", "text": "late", "start": 10.0, "end": 10.5, "box": [0, 0, 1, 1]}]}
    atomic_json(tmp_path / "partial.json", partial)
    sheet.build(tmp_path / "frames", tmp_path / "p.png", events_path=tmp_path / "partial.json", every=0.25, backgrounds=("white",), cell_width=64)
    assert _cells(tmp_path / "p.json") == [(10.0, 0, "行の開始: late"), (10.25, 6, "等間隔"), (10.5, 12, "フラッシュ"), (10.75, 18, "等間隔")]
    # 出来事の無い timeline でも、等間隔の時刻があれば並べる
    atomic_json(tmp_path / "empty.json", {"fps": FPS})
    assert sheet.build(tmp_path / "frames", tmp_path / "e.png", events_path=tmp_path / "empty.json", count=2, backgrounds=("white",), cell_width=64)["cells"] == 2
    with pytest.raises(PipelineError, match="並べる時刻がありません"):
        sheet.build(tmp_path / "frames", tmp_path / "x.png", events_path=tmp_path / "empty.json", backgrounds=("white",))


def test_spaced_times_never_repeat_a_frame_and_bad_steps_are_refused(tmp_path):
    _sequence(tmp_path / "frames")
    options = {"fps": FPS, "backgrounds": ("white",), "cell_width": 32}
    for name, extra in (("tiny", {"every": 1e-9}), ("many", {"count": 10 ** 12}), ("double", {"count": 2 * N})):     # 1 コマより細かい間隔でも、回るのはコマ数の程度
        assert sheet.build(tmp_path / "frames", tmp_path / f"{name}.png", **extra, **options)["cells"] == N
        assert [c["frame"] for c in read_json(tmp_path / f"{name}.json")["cells"]] == list(range(N))
    for fps, frames, every, count in ((24, 24, 0.01, 30), (29.97, 100, 0.04, 99), (30, 7, 1 / 30, 7), (12, 1, 0.5, 3)):
        naive, seen, k = [], set(), 0                                         # 1 つずつ数えた時刻と同じ（飛ばしても、最初の時刻が残る）
        while k * every < frames / fps - 1e-9:
            frame = min(validate.frame_at(k * every, fps), frames - 1)
            naive += [] if frame in seen else [(k * every, frame)]
            seen.add(frame)
            k += 1
        assert [(t, frame) for t, frame, _, _ in sheet.spaced_times(fps, frames, every=every)] == naive
        middles = [(k + 0.5) * (frames / fps) / count for k in range(count)]
        picked = sheet.spaced_times(fps, frames, count=count)
        assert [frame for _, frame, _, _ in picked] == sorted({min(validate.frame_at(t, fps), frames - 1) for t in middles})
        assert all(t in middles for t, _, _, _ in picked)
    for bad in (0, -1.0, float("nan"), float("inf")):
        with pytest.raises(PipelineError, match="--every"):
            sheet.build(tmp_path / "frames", tmp_path / "x.png", every=bad, **options)
    for bad in (0, -2, 2.5):
        with pytest.raises(PipelineError, match="--count"):
            sheet.build(tmp_path / "frames", tmp_path / "x.png", count=bad, **options)
    for extra in ({"every": 5e-324}, {"count": 10 ** 400}):                    # 浮動小数で数えられない値も、エラーの文で止める
        with pytest.raises(PipelineError, match="桁外れ"):
            sheet.build(tmp_path / "frames", tmp_path / "x.png", **extra, **options)
    with pytest.raises(PipelineError, match="正の fps"):
        sheet.build(tmp_path / "frames", tmp_path / "x.png", every=0.5, **{**options, "fps": 0})
    assert not (tmp_path / "x.png").exists()


# ---- 原寸の切り出し（--crop） -----------------------------------------------------------------

def test_crop_shows_the_region_at_its_native_size(tmp_path):
    video = _numbered_video(tmp_path / "v.mp4")
    box = [24, 16, 72, 64]
    result = sheet.build(video, tmp_path / "c.png", at=[0.25, 0.5], crop=(0.25, 0.25, 0.75, 1.0), columns=1)
    record = read_json(tmp_path / "c.json")
    assert [c["crop"] for c in record["cells"]] == [box, box]
    assert record["crop"] == {"units": "norm", "box": [0.25, 0.25, 0.75, 1.0], "pixels": box, "native": True}
    band = max(14, int(round(48 * sheet.LABEL_HEIGHT_SHARE)))
    assert record["cell_width"] == 48 and result["size"] == [48, 2 * (48 + band)]              # セルの絵の大きさ = 切り出した画素数（縮めない）
    for picture, frame in zip(_pictures(tmp_path / "c.png", record, 48), (6, 12)):
        assert picture.shape == (48, 48, 3)
        assert np.abs(picture.astype(int) - _numbered(frame)[16:64, 24:72]).max() <= 8         # 元のコマの同じ範囲（符号化の誤差の範囲）
        assert np.abs(picture.astype(int) - _numbered(frame)[16:64, 25:73]).max() >= 32        # 1 画素ずれた範囲とは合わない（升の境目で 40 違う）
    # 画素で指定しても同じ絵。コマの sha256 は切り出す前のコマ全体のまま
    sheet.build(video, tmp_path / "p.png", at=[0.25, 0.5], crop=(24, 16, 72, 64), units="px", columns=1)
    other = read_json(tmp_path / "p.json")
    assert (tmp_path / "p.png").read_bytes() == (tmp_path / "c.png").read_bytes() and other["crop"]["units"] == "px"
    sheet.build(video, tmp_path / "f.png", at=[0.25, 0.5], cell_width=VW)
    assert [c["sha256"] for c in other["cells"]] == [c["sha256"] for c in read_json(tmp_path / "f.json")["cells"]]
    assert read_json(tmp_path / "f.json")["crop"] is None and read_json(tmp_path / "f.json")["cells"][0]["crop"] is None
    # --cell-width を明示したときだけ、その幅に合わせる
    result = sheet.build(video, tmp_path / "w.png", at=[0.25], crop=(0.25, 0.25, 0.75, 1.0), cell_width=96, columns=1)
    record = read_json(tmp_path / "w.json")
    assert record["cell_width"] == 96 and result["size"][0] == 96 and record["crop"]["native"] is False and record["cells"][0]["crop"] == box
    picture = _pictures(tmp_path / "w.png", record, 96)[0]
    assert np.abs(picture[::2, ::2].astype(int) - _numbered(6)[16:64, 24:72]).max() <= 8       # 2 倍に広げた同じ範囲


def test_crop_of_a_transparent_sequence_is_exact_and_bad_regions_are_refused(tmp_path):
    _sequence(tmp_path / "frames")
    sheet.build(tmp_path / "frames", tmp_path / "s.png", at=[0.5], fps=FPS, crop=(4, 6, 44, 36), units="px", backgrounds=("white", "black"))
    record = read_json(tmp_path / "s.json")
    with Image.open(tmp_path / "frames" / "f_0012.png") as image:
        region = np.asarray(image)[6:36, 4:44]
    white, black = _pictures(tmp_path / "s.png", record, 30)
    assert np.array_equal(white, sheet.over(region, (255, 255, 255))) and np.array_equal(black, sheet.over(region, (0, 0, 0)))
    assert record["cell_width"] == 40 and [c["crop"] for c in record["cells"]] == [[4, 6, 44, 36]] * 2
    assert record["cells"][0]["sha256"] == hashlib.sha256((tmp_path / "frames" / "f_0012.png").read_bytes()).hexdigest()
    refused = [((0.5, 0.2, 0.4, 0.6), "norm"), ((0.2, 0.6, 0.8, 0.5), "norm"),                 # 範囲が逆
               ((0.0, 0.0, 1.2, 1.0), "norm"), ((-0.1, 0.0, 0.5, 1.0), "norm"), ((0, 0, W + 1, H), "px"),   # コマの外
               ((0.2, 0.2, 0.2, 0.6), "norm"), ((10, 10, 30, 10), "px"),                       # 面積 0
               ((0.5, 0.5, 0.501, 0.9), "norm"), ((10.2, 0, 10.8, 20), "px"),                  # 1 画素より小さい
               ((float("nan"), 0.0, 1.0, 1.0), "norm"), ((0.0, 0.0, 1.0, 1.0), "cm")]
    for crop, units in refused:
        with pytest.raises(PipelineError, match="--crop|--units"):
            sheet.build(tmp_path / "frames", tmp_path / "x.png", at=[0.5], fps=FPS, crop=crop, units=units)
    assert not (tmp_path / "x.png").exists() and not (tmp_path / "x.json").exists()
    assert sheet.crop_box((0.0, 0.0, 1.0, 1.0), "norm", (W, H)) == [0, 0, W, H] and sheet.crop_box((0, 0, 1, 1), "px", (W, H)) == [0, 0, 1, 1]


# ---- 形式（--out の拡張子） -------------------------------------------------------------------

def test_the_extension_of_out_picks_png_or_jpeg(tmp_path):
    _sequence(tmp_path / "frames", transparent=False)
    for name, start, kind in (("a.png", PNG_SIGNATURE, "png"), ("b.jpg", JPEG_START, "jpeg"), ("c.jpeg", JPEG_START, "jpeg"), ("d.JPG", JPEG_START, "jpeg")):
        result = sheet.build(tmp_path / "frames", tmp_path / name, at=[0.0, 0.5], fps=FPS, cell_width=64)
        assert (tmp_path / name).read_bytes().startswith(start) and result["format"] == kind
        assert result["json"] == str((tmp_path / name).with_suffix(".json")) and read_json(result["json"])["format"] == kind   # 隣の .json の名前は今までどおり
        with Image.open(tmp_path / name) as image:
            assert image.format == kind.upper() and list(image.size) == result["size"]
    with Image.open(tmp_path / "a.png") as image:                             # JPEG は同じ絵の品質 90
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
    assert (tmp_path / "b.jpg").read_bytes() == buffer.getvalue()
    for name in ("x.webp", "x.gif", "x", "x.png.bak"):
        with pytest.raises(PipelineError, match="拡張子"):
            sheet.build(tmp_path / "frames", tmp_path / name, at=[0.0], fps=FPS, cell_width=64)
        assert not (tmp_path / name).exists() and not (tmp_path / name).with_suffix(".json").exists()
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(PipelineError, match="JPEG"):                          # JPEG の 1 辺の上限。書きかけも残さない
        atomic_image(tmp_path / "wide.jpg", np.zeros((1, 65536, 3), np.uint8))
    assert not (tmp_path / "wide.jpg").exists()


def test_cli_takes_the_spacing_the_crop_and_the_format(tmp_path):
    _sequence(tmp_path / "frames")
    code, result, err = run_cli("sheet", tmp_path / "frames", "--out", tmp_path / "s.jpg", "--every", "0.5", "--count", "2", "--fps", str(FPS),
                                "--crop", "0.25", "0", "0.75", "1", "--backgrounds", "white")
    assert code == 0, err
    record = read_json(tmp_path / "s.json")
    assert result["format"] == "jpeg" and (tmp_path / "s.jpg").read_bytes().startswith(JPEG_START)
    assert [c["frame"] for c in record["cells"]] == [0, 6, 12, 18] and record["cell_width"] == 32 and record["cells"][0]["crop"] == [16, 0, 48, H]
    code, result, err = run_cli("sheet", tmp_path / "frames", "--out", tmp_path / "p.png", "--at", "0.5", "--fps", str(FPS),
                                "--crop", "8", "0", "40", "48", "--units", "px", "--cell-width", "64", "--backgrounds", "white")
    assert code == 0, err
    assert result["size"][0] == 64 and read_json(tmp_path / "p.json")["crop"] == {"units": "px", "box": [8.0, 0.0, 40.0, 48.0], "pixels": [8, 0, 40, 48],
                                                                                 "native": False}
    for bad, word in ((["--every", "0"], "--every"), (["--count", "0"], "--count"), (["--every", "1", "--out", tmp_path / "x.gif"], "拡張子"),
                      (["--every", "1", "--crop", "0.5", "0", "0.5", "1"], "--crop")):
        code, _, err = run_cli("sheet", tmp_path / "frames", "--out", tmp_path / "bad.png", "--fps", str(FPS), *bad)
        assert code == 1 and word in err
