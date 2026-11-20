"""顔の見切れ（`mv gate faces` と `mv gate check` の "face_cut"）。素材はすべて合成（左右で色の違う横長の画像と、横に動く縦の窓）。

縦 90×160 の出力に、横 320×180 の元画像を frame: source・zoom 1 で写す。窓の幅は元画像の高さいっぱいの 9:16 = 0.31640625（正規化座標）で、
中心を 0.5 から 0.2 へ等速で動かすと、x 0.45〜0.55 の枠は右の辺から画面の外へ出ていき、最後は全部外になる。
"""
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.gate import checks, faces as gatefaces  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

WINDOW = 180 * 9 / 16 / 320          # zoom 1 の窓の幅（元画像の正規化座標）
FACE = [0.45, 0.3, 0.55, 0.6]
FPS, DURATION = 12, 1.0


def scene(tmp_path, pan_to=0.2, extra_faces=None):
    image = np.zeros((180, 320, 3), np.uint8)
    image[:, :160] = (200, 30, 30)
    image[:, 160:] = (30, 30, 200)
    cv2.imencode(".png", image)[1].tofile(str(tmp_path / "base.png"))
    spec = {"schema_version": "fx-shot/0.3", "output": {"width": 90, "height": 160, "fps": FPS}, "duration_s": DURATION,
            "assets": {"base": "base.png"},
            "camera": {"keys": [{"t": 0, "zoom": 1.0, "center": [0.5, 0.5]}, {"t": DURATION, "zoom": 1.0, "center": [pan_to, 0.5]}],
                       "frame": "source"},
            "faces": [{"box": FACE}] + (extra_faces or [])}
    (tmp_path / "a.json").write_text(json.dumps(spec), encoding="utf-8")
    timeline = {"schema_version": "fx-timeline/0.2", "output": {"width": 90, "height": 160, "fps": FPS}, "duration_s": DURATION,
                "shots": [{"spec": "a.json", "start": 0}]}
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(timeline), encoding="utf-8")
    return path


def expected_visible(t, pan_to=0.2, box=FACE):
    """窓の中心を等速で動かしたときの、枠の横の幅のうち窓に入っている割合（縦は全部入る）。"""
    center = 0.5 + (pan_to - 0.5) * t / DURATION
    left, right = center - WINDOW / 2, center + WINDOW / 2
    return max(0.0, min(box[2], right) - max(box[0], left)) / (box[2] - box[0])


def test_projection_counts_frames_where_the_face_leaves_the_frame(tmp_path):
    path = scene(tmp_path)
    timeline, interval, frames = gatefaces.project(path)
    assert interval == (0.0, 1.0, FPS, 12) and len(frames) == 12
    records = gatefaces.faces_records(frames, FPS)
    assert len(records) == 12 and all(r["role"] == "main" and r["estimated"] is True for r in records)
    got = [r["visible"] for r in records]
    want = [expected_visible(i / FPS) for i in range(12)]
    assert max(abs(a - b) for a, b in zip(got, want)) < 0.005           # 写し方の画素の端の扱いの差まで
    assert got[0] == 1.0 and all(a >= b for a, b in zip(got, got[1:]))   # 離れていくほど減る
    gone = [r for r in records if r["visible"] == 0]
    assert gone and all(r["box"][0] == r["box"][2] for r in gone)     # 全部外の枠は面積 0 の箱で残す（字とは交わらない）
    rep = gatefaces.report(timeline, interval, frames)
    row = rep["shots"][0]
    assert rep["frames_with_cut"] == sum(v < 1 - 1e-6 for v in got) == row["cut_frames"]
    assert row["min_visible"] == 0 and row["sides"] == ["right"] and row["first_cut_t"] == pytest.approx(min(
        i / FPS for i, v in enumerate(got) if v < 1 - 1e-6))
    gate_row = checks.check_face_cut({"fps": FPS, "duration_s": 1.0}, records, checks.face_cut_settings({}, "x"))
    assert f"切れたコマ {rep['frames_with_cut']}（" in gate_row["summary"]   # 報告と mv gate check が同じコマを数える


def test_faces_match_the_render_for_the_boxes_inside_the_frame(tmp_path):
    from mv_pipeline.fx import sequence
    path = scene(tmp_path, pan_to=0.45)
    summary = sequence.render_run(path, quiet=True)
    rendered = json.loads(open(summary["faces"], encoding="utf-8").read())
    result = gatefaces.write(path, tmp_path / "gf")
    mine = json.loads((tmp_path / "gf" / "faces.json").read_text(encoding="utf-8"))
    assert [{k: v for k, v in r.items() if k != "visible"} for r in mine if r["visible"] > 0] == rendered
    assert result["frames"] == 12 and result["boxes"] == 12 and result["frames_with_cut"] == 0 and result["cut_shots"] == []


def test_min_visible_and_extra_boxes(tmp_path):
    path = scene(tmp_path, pan_to=0.35)
    extra = tmp_path / "boxes.json"
    extra.write_text(json.dumps({"schema_version": "gate-boxes/0.1", "boxes": [
        {"shot": "a.json", "box": [0.40, 0.7, 0.44, 0.8], "label": "手"},
        {"shot": 0, "box": [0.50, 0.1, 0.52, 0.2], "role": "main", "estimated": False}]}), encoding="utf-8")
    timeline, interval, frames = gatefaces.project(path, extra_path=extra)
    records = gatefaces.faces_records(frames, FPS)
    assert len(records) == 36 and sum(r.get("label") == "手" for r in records) == 12
    hands = [r for r in records if r.get("label") == "手"]
    assert all(r["role"] == "other" and r["estimated"] is True for r in hands)
    strict = gatefaces.report(timeline, interval, frames, 1.0)
    loose = gatefaces.report(timeline, interval, frames, 0.5)
    assert strict["frames_with_cut"] > loose["frames_with_cut"] >= 0
    with_other = gatefaces.report(timeline, interval, frames, 1.0, roles=("main", "other"))
    assert with_other["frames_with_cut"] >= strict["frames_with_cut"]
    bad = tmp_path / "bad.json"
    for boxes, message in (([{"shot": "z.json", "box": [0, 0, 1, 1]}], "ショットにありません"),
                           ([{"shot": 3, "box": [0, 0, 1, 1]}], "番号"),
                           ([{"shot": 0, "box": [0.5, 0, 0.4, 1]}], "x0 < x1"),
                           ([{"shot": 0, "box": [0, 0, 1, 1], "role": "hand"}], "role"),
                           ([{"shot": 0, "box": [0, 0, 1, 1], "colour": 1}], "知らない鍵")):
        bad.write_text(json.dumps({"schema_version": "gate-boxes/0.1", "boxes": boxes}), encoding="utf-8")
        with pytest.raises(PipelineError, match=message):
            gatefaces.project(path, extra_path=bad)
    bad.write_text(json.dumps({"boxes": []}), encoding="utf-8")
    with pytest.raises(PipelineError, match="gate-boxes/0.1"):
        gatefaces.project(path, extra_path=bad)
    with pytest.raises(PipelineError, match="min-visible"):
        gatefaces.write(path, tmp_path / "x", min_visible=0)
    with pytest.raises(PipelineError, match="収まりません"):
        gatefaces.project(path, start=0.5, duration=1.0)


def test_gate_check_face_cut_row(tmp_path):
    record = {"fps": 12, "duration_s": 1.0}
    faces = [{"start": i / 12, "end": (i + 1) / 12, "box": [0.1, 0.1, 0.2, 0.2], "role": "main", "estimated": True,
              "visible": 1.0 if i < 4 else 0.5} for i in range(12)]
    faces += [{"start": i / 12, "end": (i + 1) / 12, "box": [0.5, 0.5, 0.6, 0.6], "role": "other", "estimated": True,
               "visible": 0.2, "label": "手"} for i in range(12)]
    settings = checks.face_cut_settings({}, "x")
    row = checks.check_face_cut(record, faces, settings)
    assert row["status"] == "warn" and "切れたコマ 8" in row["summary"] and len(row["problems"]) == 1
    assert row["problems"][0]["t"] == pytest.approx(4 / 12) and "8 コマ" in row["problems"][0]["detail"] and "0.50" in row["problems"][0]["detail"]
    both = checks.check_face_cut(record, faces, checks.face_cut_settings({"roles": ["main", "other"]}, "x"))
    assert [p["text"] for p in both["problems"]] == ["手", "主役の顔"]
    assert checks.check_face_cut(record, faces, checks.face_cut_settings({"min_visible": 0.4}, "x"))["status"] == "pass"
    windowed = checks.check_face_cut({"fps": 12, "duration_s": 1.0, "interval": {"start": 0.0, "duration": 4 / 12}}, faces, settings)
    assert windowed["status"] == "pass"                                    # 描いた区間の外の枠は見ない
    assert checks.check_face_cut(record, [{k: v for k, v in f.items() if k != "visible"} for f in faces], settings)["status"] == "skip"
    assert checks.check_face_cut(record, None, settings)["status"] == "skip"
    for value, message in (([], "辞書"), ({"min_visible": 0}, "min_visible"), ({"roles": ["hand"]}, "roles"), ({"x": 1}, "知らない鍵")):
        with pytest.raises(PipelineError, match=message):
            checks.face_cut_settings(value, "x")


def test_gate_run_adds_the_row_only_when_configured(tmp_path):
    from mv_pipeline.fx import sequence
    path = scene(tmp_path)
    summary = sequence.render_run(path, quiet=True)
    gatefaces.write(path, tmp_path / "gf")
    base = {"timeline": str(next((tmp_path / "fx_runs").glob("*/timeline.json"))), "faces": str(tmp_path / "gf" / "faces.json")}
    config = tmp_path / "gate.json"
    config.write_text(json.dumps(base), encoding="utf-8")
    plain, _ = checks.run(config, tmp_path / "r1")
    assert checks.FACE_CUT_CHECK not in [c["check"] for c in plain["checks"]] and "face_cut" not in plain
    config.write_text(json.dumps({**base, "video": summary["video"], "face_cut": {"min_visible": 0.9}}), encoding="utf-8")
    report, out = checks.run(config, tmp_path / "r2")
    row = next(c for c in report["checks"] if c["check"] == checks.FACE_CUT_CHECK)
    assert row["status"] == "warn" and report["face_cut"] == {"min_visible": 0.9, "roles": ["main"]}
    assert row["problems"][0].get("still")                                 # 問題のコマの静止画（video から）
    assert (out / "gate_report.md").read_text(encoding="utf-8").count(checks.FACE_CUT_CHECK) >= 2


def test_cli_gate_faces(tmp_path):
    import contextlib
    import io

    from mv_pipeline import cli
    path = scene(tmp_path)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["gate", "faces", str(path), "--out", str(tmp_path / "gf"), "--start", "0.25", "--duration", "0.5",
                         "--min-visible", "0.95"])
    result = json.loads(out.getvalue())
    assert code == 0 and result["frames"] == 6 and result["min_visible"] == 0.95
    rep = json.loads((tmp_path / "gf" / "faces_report.json").read_text(encoding="utf-8"))
    assert rep["schema_version"] == "gate-faces-report/0.1" and rep["interval"]["start"] == 0.25 and rep["faces"] == "faces.json"
