import subprocess

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline import fx, media  # noqa: E402
from mv_pipeline.fx import compare, probe, render  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

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
