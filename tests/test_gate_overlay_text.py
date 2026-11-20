"""重ねの字と顔（`mv gate check` の "overlay"）。素材はすべて合成（横に動く字の四角の連番と、手で書いた枠）。

出力 48×24・12 fps。描いた区間は 1.0〜2.0 秒（12 コマ）で、連番は曲全体の番号（first_frame 12）。
字の四角は 行 4〜9・列 i〜i+7（i はコマの番号）で、アルファ 200。
"""
import json

import numpy as np
import pytest
from PIL import Image

from mv_pipeline.gate import checks, overlay_text  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

W, H, FPS = 48, 24, 12
RECORD = {"schema_version": "dance-timeline/0.1", "size": [W, H], "fps": FPS, "duration_s": 3.0, "clips": [], "seams": [], "flashes": [],
          "interval": {"start": 1.0, "duration": 1.0, "frames": 12}, "lyrics": [], "ui_regions": []}
ROWS = (4, 10)


def text_frame(i, value=200):
    rgba = np.zeros((H, W, 4), np.uint8)
    rgba[ROWS[0]:ROWS[1], i:i + 8] = (255, 255, 255, value)
    return rgba


def frames_dir(tmp_path, name="telop", skip=(), value=200, first=12, count=12):
    folder = tmp_path / name
    folder.mkdir()
    for i in range(count):
        if i in skip:
            continue
        Image.fromarray(text_frame(i, value)).save(folder / f"f{first + i:05d}.png")
    return folder


def layers_file(tmp_path, layers, name="layers.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"schema_version": "fx-overlay-layers/0.1", "layers": layers}), encoding="utf-8")
    return path


def face(box, start=1.0, end=2.0, role="main", estimated=False, label=None):
    item = {"start": start, "end": end, "box": box, "role": role, "estimated": estimated}
    if label:
        item["label"] = label
    return item


BOX = [16 / W, 0.0, 24 / W, 0.5]          # 列 16〜23・行 0〜11


def expected(i, box_cols=(16, 24), rows=(0, 12)):
    """コマ i の字の四角（列 i〜i+7・行 4〜9）と箱の重なりの画素数。"""
    cols = max(0, min(i + 8, box_cols[1]) - max(i, box_cols[0]))
    return cols * max(0, min(ROWS[1], rows[1]) - max(ROWS[0], rows[0]))


def test_counts_text_pixels_inside_the_boxes(tmp_path):
    frames_dir(tmp_path)
    layers_file(tmp_path, [{"path": "telop", "first_frame": 12, "missing": "transparent"}])
    opts = overlay_text.settings({"layers": "layers.json"}, "x")
    row, report = overlay_text.check(RECORD, [face(BOX)], opts, tmp_path)
    want = [expected(i) for i in range(12)]
    hit = [i for i, n in enumerate(want) if n]
    assert row["status"] == "fail"                                        # 実測の主役の顔に重なる
    assert len(report["overlaps"]) == 1 and report["boxes_checked"] == 12 and report["frames_with_boxes"] == 12
    group = report["overlaps"][0]
    assert group["frames"] == len(hit) and group["max_px"] == max(want)
    assert group["first_t"] == pytest.approx(1.0 + hit[0] / FPS) and group["last_t"] == pytest.approx(1.0 + hit[-1] / FPS)
    assert group["rect"] == [16, 0, 24, 12]
    assert row["problems"][0]["text"] == "主役の顔" and f"{len(hit)} コマ" in row["problems"][0]["detail"]
    assert "実測の主役の顔 1 件" in row["summary"]
    estimated, _ = overlay_text.check(RECORD, [face(BOX, estimated=True)], opts, tmp_path)
    other, _ = overlay_text.check(RECORD, [face(BOX, role="other", label="手")], opts, tmp_path)
    assert estimated["status"] == "warn" and "推定の枠" in estimated["problems"][0]["detail"]
    assert other["status"] == "warn" and other["problems"][0]["text"] == "手"
    main_only = overlay_text.settings({"layers": "layers.json", "roles": ["main"]}, "x")
    assert overlay_text.check(RECORD, [face(BOX, role="other")], main_only, tmp_path)[0]["status"] == "pass"


def test_box_edges_take_the_partly_covered_pixels(tmp_path):
    frames_dir(tmp_path)
    layers_file(tmp_path, [{"path": "telop", "first_frame": 12, "missing": "transparent"}])
    opts = overlay_text.settings({"layers": "layers.json"}, "x")
    box = [15.5 / W, 9.5 / H, 16.2 / W, 1.0]                                # 列 15〜16・行 9〜23（端の画素も数える）
    _, report = overlay_text.check(RECORD, [face(box)], opts, tmp_path)
    want = [expected(i, (15, 17), (9, 24)) for i in range(12)]
    assert report["overlaps"][0]["max_px"] == max(want) and report["overlaps"][0]["frames"] == sum(1 for n in want if n)
    assert overlay_text._pixel_rect([0.0, 0.0, 1.0, 1.0], W, H) == (0, 0, W, H)
    assert overlay_text._pixel_rect([0.5, 0.5, 0.5, 0.5], W, H) == (24, 12, 24, 12)       # 面積 0 の箱は数えない


def test_alpha_threshold_opacity_and_missing_frames(tmp_path):
    frames_dir(tmp_path, value=27, skip=(13 - 12, 14 - 12))
    layers_file(tmp_path, [{"path": "telop", "first_frame": 12, "missing": "transparent"}])
    loose = overlay_text.settings({"layers": "layers.json"}, "x")
    _, report = overlay_text.check(RECORD, [face(BOX)], loose, tmp_path)
    # 27 > 26 で字。コマ 1・2（番号 13・14）は無い（透明）ので、重なりは 2 つに分かれない（その 2 コマは字が箱の外）
    assert sum(g["frames"] for g in report["overlaps"]) == sum(1 for i in range(12) if expected(i) and i not in (1, 2))
    strict = overlay_text.settings({"layers": "layers.json", "alpha": 27}, "x")
    assert overlay_text.check(RECORD, [face(BOX)], strict, tmp_path)[0]["status"] == "pass"     # 27 は 27 より大きくない
    layers_file(tmp_path, [{"path": "telop", "first_frame": 12, "missing": "transparent", "opacity": 0.5}], "half.json")
    half = overlay_text.settings({"layers": "half.json"}, "x")
    assert overlay_text.check(RECORD, [face(BOX)], half, tmp_path)[0]["status"] == "pass"       # 27 × 0.5 は字ではない


def test_gaps_split_the_overlaps(tmp_path):
    frames_dir(tmp_path, skip=(10,))
    layers_file(tmp_path, [{"path": "telop", "first_frame": 12, "missing": "transparent"}])
    opts = overlay_text.settings({"layers": "layers.json"}, "x")
    _, report = overlay_text.check(RECORD, [face(BOX)], opts, tmp_path)
    assert [(g["first_t"], g["last_t"]) for g in report["overlaps"]] == [
        (pytest.approx(1.0 + 9 / FPS), pytest.approx(1.0 + 9 / FPS)), (pytest.approx(1.0 + 11 / FPS), pytest.approx(1.0 + 11 / FPS))]


def test_timed_card_counts_only_while_shown(tmp_path):
    card = np.zeros((H, W, 4), np.uint8)
    card[:, 16:24] = (255, 255, 255, 255)
    Image.fromarray(card).save(tmp_path / "card.png")
    layers_file(tmp_path, [{"image": "card.png", "start_s": 0.25, "end_s": 0.5}])     # 出力の 0 コマ目からの秒
    opts = overlay_text.settings({"layers": "layers.json"}, "x")
    _, report = overlay_text.check(RECORD, [face(BOX)], opts, tmp_path)
    group = report["overlaps"][0]
    assert (group["first_t"], group["last_t"]) == (pytest.approx(1.25), pytest.approx(1.5 - 1 / FPS))
    assert group["max_px"] == 8 * 12


def test_per_frame_faces_and_the_window(tmp_path):
    frames_dir(tmp_path)
    layers_file(tmp_path, [{"path": "telop", "first_frame": 12, "missing": "transparent"}])
    opts = overlay_text.settings({"layers": "layers.json"}, "x")
    per_frame = [face(BOX, round(1.0 + i / FPS, 6), round(1.0 + (i + 1) / FPS, 6)) for i in range(12)]
    per_frame += [face(BOX, 0.0, 1.0), face(BOX, 2.0, 3.0)]                 # 描いた区間の外の枠は見ない
    _, report = overlay_text.check(RECORD, per_frame, opts, tmp_path)
    assert report["boxes_checked"] == 12 and report["overlaps"][0]["frames"] == sum(1 for i in range(12) if expected(i))


def test_layers_must_fit_the_interval(tmp_path):
    frames_dir(tmp_path, first=0, count=10)
    layers_file(tmp_path, [{"path": "telop"}])                                 # first_frame が無い連番は、描いた区間と同じコマ数
    opts = overlay_text.settings({"layers": "layers.json"}, "x")
    with pytest.raises(PipelineError, match="コマ数"):
        overlay_text.check(RECORD, [face(BOX)], opts, tmp_path)
    assert overlay_text.check(RECORD, None, opts, tmp_path)[0]["status"] == "skip"
    for value, message in (([], "辞書"), ({}, "layers"), ({"layers": "a.json", "alpha": 0.1}, "alpha"),
                           ({"layers": "a.json", "alpha": 255}, "alpha"), ({"layers": "a.json", "roles": ["hand"]}, "roles"),
                           ({"layers": "a.json", "x": 1}, "知らない鍵")):
        with pytest.raises(PipelineError, match=message):
            overlay_text.settings(value, "x")


def test_gate_run_adds_the_row_only_when_configured(tmp_path):
    frames_dir(tmp_path)
    layers_file(tmp_path, [{"path": "telop", "first_frame": 12, "missing": "transparent"}])
    (tmp_path / "timeline.json").write_text(json.dumps(RECORD), encoding="utf-8")
    (tmp_path / "faces.json").write_text(json.dumps([face(BOX, estimated=True)]), encoding="utf-8")
    config = tmp_path / "gate.json"
    base = {"timeline": "timeline.json", "faces": "faces.json"}
    config.write_text(json.dumps(base), encoding="utf-8")
    plain, out = checks.run(config, tmp_path / "r1")
    assert overlay_text.OVERLAY_CHECK not in [c["check"] for c in plain["checks"]] and "overlay" not in plain
    assert not (out / overlay_text.REPORT_NAME).exists()
    config.write_text(json.dumps({**base, "overlay": {"layers": "layers.json"}}), encoding="utf-8")
    report, out = checks.run(config, tmp_path / "r2")
    row = next(c for c in report["checks"] if c["check"] == overlay_text.OVERLAY_CHECK)
    assert row["status"] == "warn" and report["overall"] != "fail"
    assert report["overlay"] == {"layers": "layers.json", "alpha": 26, "roles": ["main", "other"], "report": overlay_text.REPORT_NAME}
    saved = json.loads((out / overlay_text.REPORT_NAME).read_text(encoding="utf-8"))
    assert saved["schema_version"] == overlay_text.REPORT_SCHEMA and saved["overlaps"][0]["frames"] == sum(1 for i in range(12) if expected(i))
    assert (out / "gate_report.md").read_text(encoding="utf-8").count(overlay_text.OVERLAY_CHECK) >= 2


def test_alpha_video_layer_counts_the_same_as_the_frames(tmp_path):
    import subprocess

    from mv_pipeline import media
    folder = frames_dir(tmp_path, first=0)
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-framerate", str(FPS), "-start_number", "0", "-i", str(folder / "f%05d.png"),
                    "-c:v", "png", "-pix_fmt", "rgba", str(tmp_path / "layer.mov")], check=True)
    layers_file(tmp_path, [{"path": "telop"}], "frames.json")
    layers_file(tmp_path, [{"path": "layer.mov"}], "video.json")
    faces = [face(BOX), face([0.0, 0.0, 0.3, 1.0], 1.5, 1.75, role="other", label="手")]
    got = {name: overlay_text.check(RECORD, faces, overlay_text.settings({"layers": f"{name}.json"}, "x"), tmp_path)[1]["overlaps"]
           for name in ("frames", "video")}
    assert got["video"] == got["frames"] and len(got["frames"]) == 2
