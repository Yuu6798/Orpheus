import hashlib
import subprocess
import wave

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import make_project, spec_dict, write_spec  # noqa: E402
from mv_pipeline import fx, media  # noqa: E402
from mv_pipeline.fx import render, timeline  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

TIMES = (0.0, 0.5, 1.25, 1.99)


def digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def snapshot(ctx):
    return {v: {t: digest(render.frame(ctx, t, v)) for t in TIMES} for v in fx.VARIANTS}


def test_same_spec_and_seed_give_identical_bytes_for_every_variant_and_time(tmp_path):
    path = make_project(tmp_path)
    first = snapshot(render.prepare(fxspec.load_spec(path)))
    second = snapshot(render.prepare(fxspec.load_spec(path)))
    assert first == second
    assert len({h for per in first.values() for h in per.values()}) > 12      # 版も時刻も実際に違う絵になっている


def test_thread_count_does_not_change_the_pixels(tmp_path):
    path = make_project(tmp_path)
    ctx = render.prepare(fxspec.load_spec(path))
    many = snapshot(ctx)
    threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        single = snapshot(render.prepare(fxspec.load_spec(path)))
    finally:
        cv2.setNumThreads(threads)
    assert many == single


def test_frame_is_a_pure_function_of_context_time_and_variant(tmp_path):
    ctx = render.prepare(fxspec.load_spec(make_project(tmp_path)))
    state = lambda: [hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest() for a in (  # noqa: E731
        ctx.assets.plate, ctx.background, ctx.hole, ctx.assets.depth, ctx.assets.masks["subject"],
        ctx.planes[0].premult, ctx.stack[0].texture, ctx.lights[0].receivers[0].diffuse_w)]
    before = state()
    forward = {(v, t): digest(render.frame(ctx, t, v)) for v in fx.VARIANTS for t in TIMES}
    backward = {(v, t): digest(render.frame(ctx, t, v)) for v in reversed(fx.VARIANTS) for t in reversed(TIMES)}
    assert forward == backward and state() == before


def test_seed_changes_the_particles_and_fog_but_not_the_plain_base(tmp_path):
    a = render.prepare(fxspec.load_spec(make_project(tmp_path, seed=7)))
    b = render.prepare(fxspec.load_spec(make_project(tmp_path, seed=8)))
    assert digest(render.frame(a, 1.0, "base")) == digest(render.frame(b, 1.0, "base"))
    assert digest(render.frame(a, 1.0, "layers")) != digest(render.frame(b, 1.0, "layers"))


def test_scale_gives_even_sizes_and_matching_frames(tmp_path):
    spec = fxspec.load_spec(make_project(tmp_path, 160, 90))
    for scale in (0.5, 1.0, 2.0):
        ctx = render.prepare(spec, scale=scale)
        w, h = ctx.size
        assert w % 2 == 0 and h % 2 == 0 and abs(w - 160 * scale) <= 1 and abs(h - 90 * scale) <= 1
        assert render.frame(ctx, 1.0, "both").shape == (h, w, 3)
    with pytest.raises(PipelineError, match="scale"):
        render.scaled_size(spec, 0)


def test_unknown_variant_and_missing_files_are_reported(tmp_path):
    ctx = render.prepare(fxspec.load_spec(make_project(tmp_path)))
    with pytest.raises(PipelineError, match="variant"):
        render.frame(ctx, 0.0, "fancy")
    (tmp_path / "mask_subject.png").unlink()
    with pytest.raises(PipelineError, match="mask:subject"):
        render.prepare(fxspec.load_spec(tmp_path / "spec.json"))


def test_aspect_mismatch_is_cropped_and_recorded_not_silent(tmp_path):
    make_project(tmp_path, 160, 90)
    data = spec_dict()
    data["output"].update(width=120, height=90)               # 4:3 の出力に 16:9 の素材
    write_spec(tmp_path, data)
    ctx = render.prepare(fxspec.load_spec(tmp_path / "spec.json"))
    assert any("縦横比" in w and "25.0%" in w for w in ctx.warnings)
    assert render.frame(ctx, 0.0, "base").shape == (90, 120, 3)
    bad_mask = tmp_path / "mask_subject.png"
    from PIL import Image
    Image.new("L", (90, 90), 255).save(bad_mask)
    with pytest.raises(PipelineError, match="縦横比"):
        render.prepare(fxspec.load_spec(tmp_path / "spec.json"))


def test_depth_is_normalized_by_percentile_and_oriented(tmp_path):
    from mv_pipeline.fx.assets import normalize_depth
    values = np.tile(np.linspace(0, 100, 100, dtype=np.float32), (4, 1))
    values[0, 0] = 1e6                                         # 外れ値があっても勾配は潰れない
    white, informative = normalize_depth(values, "white")
    black, _ = normalize_depth(values, "black")
    assert informative and 0.45 < white[1, 50] < 0.55 and white.min() == 0 and white.max() == 1
    assert np.allclose(black, 1 - white)
    flat, informative = normalize_depth(np.full((4, 4), 7, dtype=np.float32), "white")
    assert not informative and (flat == 0.5).all()


# ---- ffmpeg への書き出し ----------------------------------------------------

def ffmpeg_info(path):
    return subprocess.run([media.ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr


def decode(path, size):
    out = subprocess.run([media.ffmpeg(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(-1, size[1], size[0], 3)


def write_ramp_wav(path, seconds=4.0, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    samples = ((0.1 + 0.2 * t) * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")      # 振幅が時刻に比例
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(np.stack([samples, samples], axis=1).tobytes())


def test_render_variant_writes_exact_frames_with_bt709_tags_and_matching_colors(tmp_path):
    ctx = render.prepare(fxspec.load_spec(make_project(tmp_path)))
    out = tmp_path / "runs" / "both.mp4"
    result = render.render_variant(ctx, "both", out, start=0.0, duration=1.0, log_path=tmp_path / "runs" / "both.log")
    assert result["frames"] == 12 == timeline.frame_count(1.0, ctx.fps) and out.is_file()
    info = ffmpeg_info(out)
    assert "h264" in info and "160x90" in info and "yuv420p(tv, bt709" in info and "12 fps" in info
    assert "Audio:" not in info
    frames = decode(out, ctx.size)
    assert len(frames) == 12
    expected = render.frame(ctx, 0.5, "both").astype(int)
    assert np.abs(frames[6].astype(int) - expected).mean() < 4          # 4:2:0・CRF の誤差内（行列の取り違えなら大きくずれる）
    assert not list(out.parent.glob("*.part*"))
    assert "libx264" in (tmp_path / "runs" / "both.log").read_text()


def test_saturated_primaries_survive_the_bt709_round_trip(tmp_path):
    size = (160, 90)
    image = np.zeros((90, 160, 3), dtype=np.uint8)
    quadrants = [((slice(0, 45), slice(0, 80)), (255, 0, 0)), ((slice(0, 45), slice(80, 160)), (0, 255, 0)),
                 ((slice(45, 90), slice(0, 80)), (0, 0, 255)), ((slice(45, 90), slice(80, 160)), (255, 200, 0))]
    for (rows, cols), rgb in quadrants:
        image[rows, cols] = rgb
    out = tmp_path / "primaries.mp4"
    render.write_video(out, (image for _ in range(12)), size, 12, 10, 12)
    decoded = decode(out, size)[5].astype(int)
    for (rows, cols), rgb in quadrants:
        inner = decoded[rows, cols][12:-12, 12:-12]
        assert np.abs(inner - np.array(rgb)).max() <= 6          # 行列やレンジの取り違えなら 20 以上ずれる


def test_start_offset_and_audio_are_cut_from_offset_plus_start_with_fades(tmp_path):
    make_project(tmp_path)
    write_ramp_wav(tmp_path / "audio.wav")
    data = spec_dict()
    data["audio"] = {"path": "audio.wav", "offset_s": 1.0}
    write_spec(tmp_path, data)
    ctx = render.prepare(fxspec.load_spec(tmp_path / "spec.json"))
    out = tmp_path / "with_audio.mp4"
    render.render_variant(ctx, "light", out, start=0.5, duration=1.0)
    info = ffmpeg_info(out)
    assert "Audio: aac" in info and "48000 Hz" in info and "Duration: 00:00:01.0" in info
    raw = subprocess.run([media.ffmpeg(), "-v", "error", "-i", str(out), "-vn", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, dtype="<i2").astype(float) / 32768
    assert abs(len(audio) - 48000) < 2200
    rms = lambda a, b: float(np.sqrt(np.mean(audio[int(a * 48000):int(b * 48000)] ** 2)))  # noqa: E731
    expected = (0.1 + 0.2 * (1.0 + 0.5 + 0.5)) / np.sqrt(2)             # 出力 0.5 秒 = 音源 2.0 秒の振幅
    assert rms(0.4, 0.6) == pytest.approx(expected, rel=0.15)
    assert rms(0.0, 0.01) < 0.35 * rms(0.4, 0.6)                         # 頭 0.02 秒のフェードイン
    assert rms(0.97, 1.0) < 0.1 * rms(0.4, 0.6)                          # 終わり 0.25 秒のフェードアウト


def test_same_input_gives_the_same_mp4_bytes(tmp_path):
    ctx = render.prepare(fxspec.load_spec(make_project(tmp_path)))
    for name in ("a", "b"):
        render.render_variant(ctx, "both", tmp_path / f"{name}.mp4", duration=1.0)
    assert (tmp_path / "a.mp4").read_bytes() == (tmp_path / "b.mp4").read_bytes()


def test_failed_write_never_replaces_or_leaves_a_file(tmp_path):
    ctx = render.prepare(fxspec.load_spec(make_project(tmp_path)))
    out = tmp_path / "keep.mp4"
    out.write_bytes(b"previous good output")
    frames = lambda n: (render.frame(ctx, i / 12, "base") for i in range(n))  # noqa: E731
    bogus = {"path": tmp_path / "nope.wav", "start": 0.0, "duration": 1.0}
    with pytest.raises(PipelineError, match="ffmpeg"):
        render.write_video(out, frames(12), ctx.size, 12, 23, 12, audio=bogus, log_path=tmp_path / "w.log")
    with pytest.raises(PipelineError, match="フレーム数"):
        render.write_video(out, frames(5), ctx.size, 12, 23, 12)
    with pytest.raises(PipelineError, match="expected uint8"):
        render.write_video(out, iter([np.zeros((3, 3, 3), dtype=np.uint8)]), ctx.size, 12, 23, 1)
    with pytest.raises(PipelineError, match="expected uint8"):
        render.write_video(out, iter([np.zeros((90, 160, 3), dtype=np.float32)]), ctx.size, 12, 23, 1)
    assert out.read_bytes() == b"previous good output" and not list(tmp_path.glob("*.part*"))
    assert "nope.wav" in (tmp_path / "w.log").read_text()


def test_missing_audio_file_is_reported_before_rendering(tmp_path):
    make_project(tmp_path)
    data = spec_dict()
    data["audio"] = {"path": "gone.wav"}
    write_spec(tmp_path, data)
    ctx = render.prepare(fxspec.load_spec(tmp_path / "spec.json"))
    with pytest.raises(PipelineError, match="gone.wav"):
        render.render_variant(ctx, "base", tmp_path / "x.mp4", duration=0.5)
    assert not (tmp_path / "x.mp4").exists()


def test_library_versions_report_what_was_used(tmp_path):
    versions = render.library_versions()
    assert set(versions) == {"python", "numpy", "opencv", "pillow", "ffmpeg"}
    assert versions["numpy"] == np.__version__ and versions["ffmpeg"].startswith("ffmpeg version")
