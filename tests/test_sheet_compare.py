"""mv sheet の版どうしの見比べ: 同じ時刻のコマを動画ごとに 1 段ずつ並べたシート（--with）と、左右に並べた動画（.mp4 の --out）を、
色でコマ番号の分かる合成の動画で確かめる（閲覧の道具。合否は出さない）。"""
import json

import numpy as np
import pytest

from mv_pipeline import encode, sheet
from mv_pipeline.inspection import video
from mv_pipeline.io import PipelineError


def _clip(path, frames, fps, size, base, audio=True, audio_s=None):
    """コマ i の色が (base, 10·i, 0) の動画（BT.709）。"""
    width, height = size

    def images():
        for i in range(frames):
            image = np.zeros((height, width, 3), np.uint8)
            image[:] = (base, min(255, 10 * i), 0)
            yield image
    plan = {"silent": True, "duration": audio_s or frames / fps} if audio else None
    encode.write_video(path, images(), size, fps, 10, frames, plan)
    return path


def test_rows_put_each_video_on_its_own_row(tmp_path):
    a = _clip(tmp_path / "before.mp4", 12, 12, (64, 48), 200)
    b = _clip(tmp_path / "after.mp4", 12, 12, (64, 48), 40)
    result = sheet.build_rows([a, b], tmp_path / "rows.png", at=[0.25, 0.5], labels=["before", "after"], cell_width=64)
    assert result["rows"] == 2 and result["columns"] == 2 and result["missing"] == 0
    record = json.loads((tmp_path / "rows.json").read_text(encoding="utf-8"))
    assert [(c["row"], c["label"], c["frame"]) for c in record["cells"]] == [(0, "before", 3), (0, "before", 6), (1, "after", 3), (1, "after", 6)]
    from PIL import Image
    image = np.asarray(Image.open(tmp_path / "rows.png"))
    assert image.shape[1] == 2 * 64
    top, bottom = image[image.shape[0] // 4 + 8, 10], image[3 * image.shape[0] // 4 + 8, 10]
    assert top[0] > 150 and bottom[0] < 90                       # 上の段は 1 本目、下の段は 2 本目
    every = sheet.build_rows([a, b], tmp_path / "every.png", every=0.5, cell_width=64)
    assert every["columns"] == 2                                  # 1 本目の長さ（1 秒）で 0・0.5 秒
    with pytest.raises(PipelineError, match="--labels の数"):
        sheet.build_rows([a, b], tmp_path / "bad.png", at=[0.1], labels=["one"])
    with pytest.raises(PipelineError, match="時刻"):
        sheet.build_rows([a, b], tmp_path / "bad.png")


def test_side_by_side_video_lines_up_the_times(tmp_path):
    a = _clip(tmp_path / "a.mp4", 24, 24, (64, 48), 200)
    b = _clip(tmp_path / "b.mp4", 9, 12, (32, 24), 40, audio=False)     # 12 fps・0.75 秒・半分の大きさ
    result = sheet.side_by_side([a, b], tmp_path / "side.mp4", labels=None, audio=1, crf=10)
    assert result["size"] == [32 + 32, 24] and result["fps"] == 24 and result["frames"] == 18 and result["audio"] == 1
    info = video.describe(tmp_path / "side.mp4")
    decoded = list(video.frames(tmp_path / "side.mp4", None, info))
    assert len(decoded) == 18
    _, frame = decoded[10]
    left, right = frame[12, 16].astype(int), frame[12, 32 + 16].astype(int)
    assert abs(left[0] - 200) < 12 and abs(left[1] - 100) < 12     # 1 本目のコマ 10
    assert abs(right[0] - 40) < 12 and abs(right[1] - 50) < 12     # 2 本目の時刻 10/24 秒 → コマ 5
    record = json.loads((tmp_path / "side.json").read_text(encoding="utf-8"))
    assert record["frames"] == 18 and [p["size"] for p in record["panels"]] == [[32, 24], [32, 24]]   # 低い方の高さにそろえる
    quiet = sheet.side_by_side([b, a], tmp_path / "quiet.mp4", audio=0, labels=["b", "a"])
    assert quiet["audio"] == 0
    with pytest.raises(PipelineError, match="音がありません"):
        sheet.side_by_side([b, a], tmp_path / "noaudio.mp4", audio=1)
    with pytest.raises(PipelineError, match="2 本以上"):
        sheet.side_by_side([a], tmp_path / "one.mp4")
    with pytest.raises(PipelineError, match=".mp4"):
        sheet.side_by_side([a, b], tmp_path / "side.png")


def test_a_short_audio_track_is_padded_to_the_video(tmp_path):
    from mv_pipeline.fx import probe
    a = _clip(tmp_path / "a.mp4", 24, 24, (32, 24), 200, audio_s=0.5)     # 1 秒の絵に 0.5 秒の音
    b = _clip(tmp_path / "b.mp4", 24, 24, (32, 24), 40, audio=False)
    result = sheet.side_by_side([a, b], tmp_path / "side.mp4", audio=1)
    checks = probe.check_video(tmp_path / "side.mp4", frames=24, fps=24, audio=True, audio_s=1.0, bt709=True)
    assert result["frames"] == 24 and checks["pass"], checks["failures"]
