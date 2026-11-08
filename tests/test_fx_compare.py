import subprocess

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import make_project, run_cli  # noqa: E402
from mv_pipeline import fx, media  # noqa: E402
from mv_pipeline.fx import compare, probe, render, runs  # noqa: E402
from mv_pipeline.io import PipelineError, read_json  # noqa: E402

SIZE = (160, 90)
COLORS = {"base": (200, 40, 40), "layers": (40, 200, 40), "light": (40, 40, 200), "both": (200, 200, 40)}


def solid_video(path, rgb, frames=6, size=SIZE, fps=12):
    image = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    image[:] = rgb
    render.write_video(path, (image for _ in range(frames)), size, fps, 18, frames)
    return path


def decode(path, size=SIZE):
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, size[1], size[0], 3)


def quadrant(frame, variant):
    col, row = compare.SLOTS[variant]
    w, h = SIZE[0] // 2, SIZE[1] // 2
    return frame[row * h:(row + 1) * h, col * w:(col + 1) * w]


def test_slots_are_fixed_by_variant_not_by_what_was_rendered():
    assert compare.SLOTS == {"base": (0, 0), "layers": (1, 0), "light": (0, 1), "both": (1, 1)}
    assert list(compare.SLOTS) == list(fx.VARIANTS) and compare.tile_size((1920, 1080)) == (960, 540)


def test_grid_places_each_variant_in_its_quadrant_with_a_label(tmp_path):
    sources = {v: solid_video(tmp_path / f"{v}.mp4", rgb) for v, rgb in COLORS.items()}
    out = tmp_path / "grid.mp4"
    result = compare.write_grid(out, sources, {}, SIZE, 12, 18, 6)
    assert result["frames"] == 6 and out.is_file() and not list(tmp_path.glob("*.part*"))
    frames = decode(out)
    assert frames.shape == (6, 90, 160, 3)
    for variant, rgb in COLORS.items():
        tile = quadrant(frames[3], variant)
        body = tile[24:-4, 6:-4]                                                            # ラベルの帯の下・縁の彩度のにじみを除く
        assert np.abs(body.astype(int) - np.array(rgb)).max() <= 8, variant
        assert (np.abs(tile[:16, :40].astype(int) - np.array(rgb)).max(axis=2) > 40).any()  # 左上のラベル（白字・黒帯）


def test_grid_with_a_subset_keeps_the_slots_and_leaves_the_rest_black(tmp_path):
    sources = {v: solid_video(tmp_path / f"{v}.mp4", COLORS[v]) for v in ("base", "both")}
    out = tmp_path / "grid.mp4"
    compare.write_grid(out, sources, {}, SIZE, 12, 18, 6)
    frame = decode(out)[2]
    assert np.abs(quadrant(frame, "both")[24:-4, 6:-4].astype(int) - np.array(COLORS["both"])).max() <= 8
    for empty in ("layers", "light"):
        assert quadrant(frame, empty)[6:-6, 6:-6].max() <= 20                               # 黒（隣の色のにじみは縁だけ）


def test_label_marks_variants_that_equal_base_because_the_spec_lacks_the_feature():
    assert compare.label_for("light", {"empty": True}) == "light [no-op]"
    assert compare.label_for("light", {"empty": False}) == "light" and compare.label_for("light") == "light"
    assert compare.label_for("base", {"empty": True}) == "base"                               # base 自身は基準なので付けない


def test_grid_uses_bt709_for_the_decode_so_colors_match_the_variants(tmp_path):
    """タグ任せにせず行列を明示している。飽和色が bt601 の取り違えで 20 以上ずれない。"""
    sources = {v: solid_video(tmp_path / f"{v}.mp4", rgb) for v, rgb in COLORS.items()}
    compare.write_grid(tmp_path / "grid.mp4", sources, {}, SIZE, 12, 10, 6)
    tile = quadrant(decode(tmp_path / "grid.mp4")[3], "layers")[24:-4, 6:-4].astype(int)
    assert np.abs(tile - np.array(COLORS["layers"])).max() <= 8


def test_grid_does_not_depend_on_the_color_tags_of_its_sources(tmp_path):
    """タグの無い（bt709 で符号化しただけの）動画でも、行列を明示して復号するので色が合う。"""
    sources = {}
    for variant, rgb in COLORS.items():
        image = np.zeros((SIZE[1], SIZE[0], 3), dtype=np.uint8)
        image[:] = rgb
        sources[variant] = tmp_path / f"{variant}.mp4"
        subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "160x90",
                        "-framerate", "12", "-i", "pipe:0", "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                        "-c:v", "libx264", "-crf", "10", "-frames:v", "6",
                        "-bsf:v", "h264_metadata=colour_primaries=2:transfer_characteristics=2:matrix_coefficients=2",   # タグを「不明」にする（ffmpeg 7 は自動でタグを付けるため）
                        str(sources[variant])],
                       input=np.repeat(image[None], 6, axis=0).tobytes(), check=True)
        assert probe.inspect(sources[variant])["video"]["matrix"] is None                        # 本当にタグが無い
    compare.write_grid(tmp_path / "grid.mp4", sources, {}, SIZE, 12, 10, 6)
    frame = decode(tmp_path / "grid.mp4")[3]
    for variant, rgb in COLORS.items():
        assert np.abs(quadrant(frame, variant)[24:-4, 6:-4].astype(int) - np.array(rgb)).max() <= 10, variant


def test_the_decoders_are_stopped_when_writing_the_grid_fails(tmp_path, monkeypatch):
    sources = {v: solid_video(tmp_path / f"{v}.mp4", rgb) for v, rgb in COLORS.items()}
    closed, real = [], compare.grid_frames

    def watched(*args):
        try:
            yield from real(*args)
        finally:
            closed.append(True)

    monkeypatch.setattr(compare, "grid_frames", watched)
    bogus = {"path": tmp_path / "nope.wav", "start": 0.0, "duration": 0.5}
    try:
        compare.write_grid(tmp_path / "grid.mp4", sources, {}, SIZE, 12, 18, 6, audio=bogus)
    except PipelineError:
        assert closed == [True]                       # 例外の最中（トレースバックがまだジェネレータを握っている間）に閉じている
    else:
        pytest.fail("write_grid should fail with a missing audio file")
    assert not (tmp_path / "grid.mp4").exists()


def test_a_broken_source_fails_the_grid_without_leaving_a_file(tmp_path):
    sources = {v: solid_video(tmp_path / f"{v}.mp4", rgb) for v, rgb in COLORS.items()}
    sources["light"].write_bytes(b"not a video")
    out = tmp_path / "grid.mp4"
    with pytest.raises(PipelineError, match="light.mp4"):
        compare.write_grid(out, sources, {}, SIZE, 12, 18, 6)
    assert not out.exists() and not list(tmp_path.glob("*.part*"))


def test_a_source_with_too_few_frames_fails_the_grid(tmp_path):
    sources = {v: solid_video(tmp_path / f"{v}.mp4", rgb, frames=6 if v != "both" else 3) for v, rgb in COLORS.items()}
    with pytest.raises(PipelineError, match="both.mp4"):
        compare.write_grid(tmp_path / "grid.mp4", sources, {}, SIZE, 12, 18, 6)
    assert not (tmp_path / "grid.mp4").exists()


def test_unknown_variant_is_rejected(tmp_path):
    with pytest.raises(PipelineError, match="unknown variant"):
        compare.write_grid(tmp_path / "g.mp4", {"fancy": tmp_path / "x.mp4"}, {}, SIZE, 12, 18, 6)


def test_stamp_blends_inside_the_image_and_clips_at_the_edge():
    image = np.full((20, 30, 3), 100, dtype=np.uint8)
    patch = compare.label_patch("abc", 90)
    out = compare.stamp(image.copy(), patch, 0, 0)
    assert out.shape == image.shape and (out != image).any()
    assert np.array_equal(compare.stamp(image.copy(), patch, 29, 19)[:19], image[:19])      # 端では切れても落ちない
    assert np.array_equal(compare.stamp(image.copy(), patch, 40, 0), image)                 # 完全に外なら何もしない


def test_save_png_atomic_leaves_no_temporary_file(tmp_path):
    compare.save_png_atomic(np.zeros((4, 4, 3), dtype=np.uint8), tmp_path / "sub" / "a.png")
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["a.png"]


# ---- 基準の動画と並べた比較動画（mv fx compare） -------------------------------------------

def test_the_compare_layout_matches_the_production_one():
    layout = compare.compare_layout((1920, 1080))
    assert layout["tiles"] == {"reference": (0, 0, 960, 540), "both": (960, 0, 960, 540), "base": (0, 584, 640, 360),
                               "layers": (640, 584, 640, 360), "light": (1280, 584, 640, 360)}
    assert layout["footer"] == (24, 944) and layout["margin"] == 28
    portrait = compare.compare_layout((1080, 1920))["tiles"]
    assert portrait["both"] == (540, 0, 540, 960) and portrait["light"] == (720, 1040, 360, 640)


def counting_video(path, frames=30, size=(160, 90), fps=12):
    """コマ i を灰色 8·i で塗った動画（基準の動画の代わり）。"""
    render.write_video(path, (np.full((size[1], size[0], 3), 8 * i, np.uint8) for i in range(frames)), size, fps, 10, frames)
    return path


@pytest.fixture(scope="module")
def finished_run(tmp_path_factory):
    folder = tmp_path_factory.mktemp("compare")
    spec_path = make_project(folder, 480, 270)
    return folder, runs.render_run(spec_path, grid=False)


def frame_of(path, index, size):
    return decode(path, size)[index].astype(int)


def test_compare_places_the_reference_frames_next_to_the_variants(finished_run):
    folder, run = finished_run
    reference = counting_video(folder / "ref.mp4")
    result = runs.compare_run(run["run_dir"], reference, offset=0.25, label="published")
    assert result["technical_pass"] and result["frames"] == 24 and result["reference"]["first_frame"] == 3   # 0.25·12
    record = read_json(result["record"])
    assert record["schema_version"] == "fx-compare/0.1" and record["reference"]["first_frame"] == 3
    assert set(record["run"]["variants"]) == set(fx.VARIANTS) and record["output"]["technical_pass"]
    frames = decode(result["path"], (480, 270)).astype(int)
    for i in (0, 10, 20):
        tile = frames[i][30:130, 10:230]                                                       # 基準の枠（ラベルの帯の下）
        assert np.abs(tile - 8 * (3 + i)).max() <= 6, i
    x, y, w, h = compare.compare_layout((480, 270))["tiles"]["base"]
    reader = compare._Reader(run["variants"]["base"]["path"], (w, h), 24)
    try:
        expected = [reader.read().astype(int) for _ in range(24)]
    finally:
        reader.close()
    assert np.abs(frames[12][y + 24:y + h, x:x + w] - expected[12][24:]).mean() < 3            # base の枠は base.mp4 のその時刻


def test_the_reference_fps_must_match_within_the_rounding_of_the_reading():
    assert runs.same_fps(24.0, 24) and runs.same_fps(23.98, 24000 / 1001) and runs.same_fps(29.97, 30000 / 1001)   # 丸めの幅は同じ
    assert not runs.same_fps(23.98, 24) and not runs.same_fps(59.94, 60) and not runs.same_fps(11.99, 12)          # 本当に違う fps


def test_matching_rates_count_the_reference_with_the_unrounded_run_fps(finished_run, monkeypatch):
    from mv_pipeline.inspection import video
    folder, run = finished_run
    reference = counting_video(folder / "ref_offset.mp4")
    read = video.describe
    monkeypatch.setattr(video, "describe", lambda path: {**read(path), "fps": 12.004})        # 小数 2 桁の読みで 12 と同じとみなす幅
    starts, decode_frames = [], video.frames

    def frames(path, size, info, start=0, end=None):
        starts.append(start)
        return decode_frames(path, size, info, start=0, end=end - start)                   # 短い動画の先頭を読む（start だけを見る）

    monkeypatch.setattr(video, "frames", frames)
    result = runs.compare_run(run["run_dir"], reference, offset=125.0)
    assert starts == [1500] and result["reference"]["first_frame"] == 1500                # 125·12。読んだ 12.004 で数えると 1501
    assert read_json(result["record"])["reference"]["fps_used"] == 12.0


def test_the_stills_reference_counts_with_the_spec_fps_when_the_rates_match(tmp_path, monkeypatch):
    from mv_pipeline.inspection import video
    reference = counting_video(tmp_path / "ref.mp4")
    read = video.describe
    monkeypatch.setattr(video, "describe", lambda path: {**read(path), "fps": 12.004})
    monkeypatch.setattr(video, "grab", lambda path, indices, info, size=None, total=None:
                        {i: np.zeros((size[1], size[0], 3), np.uint8) for i in indices})
    same = runs.reference_cells(reference, [125.0], 0.0, (160, 90), "ref", fps=12)
    assert same["frames_used"] == [1500] and same["fps_used"] == 12.0                     # spec と同じ fps なら丸めていない fps で
    other = runs.reference_cells(reference, [125.0], 0.0, (160, 90), "ref", fps=24)
    assert other["frames_used"] == [1501] and other["fps_used"] == 12.004                 # 違う fps なら読んだ fps で


def test_compare_refuses_mismatched_or_short_references(finished_run):
    folder, run = finished_run
    with pytest.raises(PipelineError, match="fps"):
        runs.compare_run(run["run_dir"], counting_video(folder / "ref24.mp4", fps=24))
    near = counting_video(folder / "ref11988.mp4", fps=12000 / 1001)                           # ffmpeg は 11.99 fps と出す
    with pytest.raises(PipelineError, match="fps"):                                           # 1 対 1 に並べると、だんだんずれる
        runs.compare_run(run["run_dir"], near)
    short = counting_video(folder / "short.mp4", frames=30)
    out = folder / "short_compare.mp4"
    with pytest.raises(PipelineError, match="コマ目で終わりました"):
        runs.compare_run(run["run_dir"], short, offset=1.5, out=out)                           # 18 コマ目から 24 コマは無い
    assert not out.exists()
    with pytest.raises(PipelineError, match="ASCII"):
        runs.compare_run(run["run_dir"], short, label="公開版")
    with pytest.raises(PipelineError, match="manifest"):
        runs.compare_run(folder, short)


def test_compare_refuses_a_run_whose_outputs_changed(tmp_path):
    spec_path = make_project(tmp_path, 160, 90)
    run = runs.render_run(spec_path, variants="base", grid=False)
    with open(run["variants"]["base"]["path"], "ab") as stream:
        stream.write(b"x")
    with pytest.raises(PipelineError, match="manifest の記録と違います"):
        runs.compare_run(run["run_dir"], counting_video(tmp_path / "ref.mp4"))


def test_the_compare_command(finished_run):
    folder, run = finished_run
    code, result, err = run_cli("fx", "compare", run["run_dir"], "--reference", counting_video(folder / "cli_ref.mp4"),
                                "--out", folder / "cli_compare.mp4")
    assert code == 0, err
    assert result["path"].endswith("cli_compare.mp4") and (folder / "cli_compare.json").is_file()
