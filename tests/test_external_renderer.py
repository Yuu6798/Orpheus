"""自前の描画スクリプトから乗る経路（docs/external-renderer.md）。

見本のレンダラ（examples/external/render_frames.py）を、fx legacy で呼んで記録できること、区間を deliver join でつなげること、
書いた色が BT.709 の式で読み戻せることを、合成素材で確かめる。fx extra（opencv）は使わない。
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from fx_synth import run_cli

from mv_pipeline import media

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "examples" / "external" / "render_frames.py"
SIZE, FPS = (96, 54), 12


def example():
    spec = importlib.util.spec_from_file_location("render_frames", RENDERER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"width": SIZE[0], "height": SIZE[1], "fps": FPS}), encoding="utf-8")
    return path


def render(tmp_path, first, last):
    out = tmp_path / "segs" / f"seg_{first:04d}_{last:04d}.mp4"
    out.parent.mkdir(exist_ok=True)
    subprocess.run([sys.executable, str(RENDERER), str(config(tmp_path)), "seg", str(first), str(last), str(out)], check=True)
    return out


def read_bt709(path, index):
    """コマ index を、BT.709・tv の動画として RGB に読む（印に頼らず、式を明示する）。"""
    command = [media.ffmpeg(), "-v", "error", "-i", str(path),
               "-vf", f"select=eq(n\\,{index}),scale=in_color_matrix=bt709:in_range=tv:out_range=pc", "-frames:v", "1",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    data = subprocess.run(command, check=True, capture_output=True).stdout
    return np.frombuffer(data, np.uint8).reshape(SIZE[1], SIZE[0], 3).astype(int)


def test_segments_from_the_example_join_into_a_master(tmp_path):
    render(tmp_path, 0, 6)
    render(tmp_path, 6, 12)
    code, result, _ = run_cli("deliver", "join", tmp_path / "segs", "--out", tmp_path / "master.mp4")
    assert code == 0 and result["technical_pass"] and result["frames"] == 12 and result["segments"] == 2


def test_the_example_runs_under_fx_legacy_with_frame_units(tmp_path):
    code, result, _ = run_cli("fx", "legacy", "--renderer", RENDERER, "--config", config(tmp_path), "--start", 0, "--duration", 0.5,
                              "--units", "frames", "--fps", FPS, "--width", SIZE[0], "--height", SIZE[1], "--out", tmp_path / "runs")
    assert code == 0 and result["status"] == "succeeded" and result["technical_pass"] and result["frames"] == 6


def test_the_written_colours_read_back_with_the_bt709_matrix(tmp_path):
    module = example()
    image = read_bt709(render(tmp_path, 0, 3), 0)
    step = SIZE[0] // len(module.PATCHES)
    for i, colour in enumerate(module.PATCHES):
        middle = image[SIZE[1] // 4, i * step + step // 2]
        assert np.abs(middle - np.array(colour)).max() <= 6, (colour, tuple(middle))


def test_the_same_frame_comes_out_whichever_segment_draws_it(tmp_path):
    """コマ 7 は、0〜9 の区間で描いても 6〜9 の区間で描いても同じ絵（帯の位置で確かめる）。"""
    module = example()
    assert np.array_equal(module.frame(7, *SIZE), module.frame(7, *SIZE))

    def bar(image):
        return int(image[SIZE[1] * 3 // 4].sum(axis=1).argmax())

    whole = read_bt709(render(tmp_path, 0, 9), 7)
    part = read_bt709(render(tmp_path, 6, 9), 1)
    assert abs(bar(whole) - 21) <= 2 and abs(bar(part) - 21) <= 2
    assert np.abs(whole - part).mean() <= 3


def test_an_empty_range_stops_without_writing_a_file(tmp_path):
    """`seg 6 6`（空の区間）は、使い方を出して 0 以外で終わり、0 コマのファイルを残さない。"""
    segs = tmp_path / "segs"
    out = segs / "seg_0006_0006.mp4"
    done = subprocess.run([sys.executable, str(RENDERER), str(config(tmp_path)), "seg", "6", "6", str(out)], capture_output=True)
    assert done.returncode != 0 and b"usage" in done.stderr
    assert not out.exists() and not segs.exists()
