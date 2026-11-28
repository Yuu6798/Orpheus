"""一枚絵のレシピで使うコマンドの `--brief`（1〜2 行の要約）。既定の出力（JSON）は変わらない。"""
import contextlib
import io
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline import cli, encode  # noqa: E402


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue()


def video(path, count=24, fps=12, size=(64, 36)):
    frames = [np.full((size[1], size[0], 3), 20 + (200 if i == 10 else 0), np.uint8) for i in range(count)]
    encode.write_video(path, iter(frames), size, fps, 12, count)
    return path


def test_gate_flashes_brief_is_one_line_and_the_default_stays_json(tmp_path):
    clip = video(tmp_path / "v.mp4")
    code, text = run("gate", "flashes", clip, "--brief")
    assert code == 0 and len(text.strip().splitlines()) == 1 and text.startswith("flashes pass: 1 回")
    code, text = run("gate", "flashes", clip)
    assert code == 0 and json.loads(text)["flashes"] == 1


def test_guides_and_sheet_brief_are_one_line(tmp_path):
    image = tmp_path / "art.png"
    cv2.imwrite(str(image), np.full((90, 160, 3), 120, np.uint8))
    code, text = run("fx", "guides", image, "--out", tmp_path / "grid.jpg", "--brief")
    assert code == 0 and len(text.strip().splitlines()) == 1 and "grid.jpg" in text and "目盛り" in text
    code, text = run("fx", "guides", image, "--out", tmp_path / "grid2.jpg")
    assert code == 0 and json.loads(text)["path"].endswith("grid2.jpg")
    clip = video(tmp_path / "v.mp4")
    code, text = run("sheet", clip, "--out", tmp_path / "sheet.jpg", "--count", "4", "--brief")
    assert code == 0 and len(text.strip().splitlines()) == 1 and "sheet.jpg" in text and "4 コマ" in text
    code, text = run("sheet", clip, "--out", tmp_path / "sheet2.jpg", "--count", "4")
    assert code == 0 and json.loads(text)["cells"] == 4


def test_inspect_brief_summarizes_the_result(tmp_path):
    from mv_pipeline.inspection import run as inspect_run
    result = {"overall": "review", "summary": {"events": 2, "by_category": {"continuity": 2}}}
    report_dir = tmp_path / "inspect_report"
    text = inspect_run.brief(result, str(report_dir))
    assert text.splitlines()[0] == "inspect review: 出来事 2（continuity 2）。出来事は要確認で、合否ではない"
    assert text.splitlines()[1] == f"report: {report_dir / 'inspect_report.md'}"
