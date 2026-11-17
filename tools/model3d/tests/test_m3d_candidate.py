"""check.candidate: 合成の箱の T ポーズの体で、切り口の輪とかたまり・太さ・切り口の表・確認用の絵（Blender なし）と、工程の受け渡しと
悪い設定（代役の Blender）を確かめる。3 作目の VRM のダンスの候補での突き合わせ（台本と同じ数・同じ絵）は docs/model3d.md
（リポジトリ外の素材なので CI には無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from m3d_helpers import stand_in_executable
from model3d import mesh_slices
from model3d.io import ToolError


def _box(x0, x1, y0, y1, z0, z1):
    V = np.array([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], dtype=np.float64)
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    T = [(a, b, c) for a, b, c, d in faces] + [(a, c, d) for a, b, c, d in faces]
    return V, np.array(T, dtype=np.int64)


def _join(*boxes):
    verts, tris, offset = [], [], 0
    for V, T in boxes:
        verts.append(V)
        tris.append(T + offset)
        offset += len(V)
    return np.concatenate(verts), np.concatenate(tris)


def _body():
    """箱でできた T ポーズの体: 脚 2 本（幅 0.10、0〜0.9 m）、腰（幅 0.30、0.9〜1.1）、くびれ（幅 0.24、1.1〜1.2）、胸（幅 0.32、1.2〜1.45）、
    腕（左右に 0.8 m まで、1.40〜1.45）。"""
    return _join(_box(-0.14, -0.04, -0.06, 0.06, 0.0, 0.9), _box(0.04, 0.14, -0.06, 0.06, 0.0, 0.9),
                 _box(-0.15, 0.15, -0.1, 0.1, 0.9, 1.1), _box(-0.12, 0.12, -0.09, 0.09, 1.1, 1.2),
                 _box(-0.16, 0.16, -0.11, 0.11, 1.2, 1.45), _box(-0.8, 0.8, -0.04, 0.04, 1.40, 1.45))


def test_sections_follow_the_faces_and_clusters_merge_overlapping_loops():
    V, T = _box(0, 1, 0, 2, 0, 3)
    loops = mesh_slices.section_loops(V, T, 1.5)
    assert len(loops) == 1 and len(loops[0]) == 8                       # 縦の 4 辺と、側面の対角線 4 本
    assert loops[0][:, 0].min() == 0 and loops[0][:, 0].max() == 1 and np.ptp(loops[0][:, 1]) == 2
    V, T = _join(_box(0, 1, 0, 1, 0, 1), _box(0.5, 2, 0, 1, 0, 1), _box(3, 4, 0, 1, 0, 1))
    assert mesh_slices.clusters(V, T, 0.5, 0.01) == [(0.0, 2.0, 0.0, 1.0), (3.0, 4.0, 0.0, 1.0)]
    assert mesh_slices.clusters(V, T, 0.5, 1.5) == [(0.0, 4.0, 0.0, 1.0)]          # すき間 1 は gap 1.5 でまとまる


def test_the_candidate_is_measured_on_the_floor_to_arm_scale():
    V, T = _body()
    assert "thigh" not in mesh_slices.measure_candidate(V, T)["rows"]          # 太ももの高さに既定は無い
    m = mesh_slices.measure_candidate(V, T, thigh_at=0.4)
    assert (m["floor_z"], m["top_z"], m["centre_x"]) == (0.0, 1.45, 0.0)
    assert m["arm_z"] == pytest.approx(1.425) and m["S"] == pytest.approx(1.425)
    assert m["rows"]["waist"]["at"] == pytest.approx(0.80) and m["rows"]["waist"]["width"] == pytest.approx(0.24 / 1.425)
    assert m["rows"]["waist"]["depth"] == pytest.approx(0.18 / 1.425)
    assert m["rows"]["thigh"]["at"] == 0.4 and m["rows"]["thigh"]["width"] == pytest.approx(0.10 / 1.425)
    assert m["rows"]["thigh"]["depth"] == pytest.approx(0.12 / 1.425)
    assert m["rows"]["knee"]["at"] == pytest.approx(0.34) and m["rows"]["calf"]["at"] == pytest.approx(0.33)
    U = 0.80 * 1.425
    assert m["by_U"]["U"] == pytest.approx(U) and m["by_U"]["waist"]["width"] == pytest.approx(0.24 / U)
    assert m["by_U"]["thigh"]["width"] == pytest.approx(0.10 / U) and m["by_U"]["hem_h"] == pytest.approx(0.785, abs=0.005)
    widths = {round(p["at"], 2): p["torso_width"] for p in m["profile"]}
    assert widths[0.7] == pytest.approx(0.30 / 1.425) and widths[0.95] == pytest.approx(0.32 / 1.425)   # 腰の箱と胸の箱


def test_legs_hidden_above_the_ankles_give_only_the_hem():
    """足首までの長い裾: 脚が見えるのは U の 0.09 まで（足首を探す 0.10〜0.30 に届かない）。足首より上の脚の段は出さず、裾の高さだけ。"""
    V, T = _join(_box(-0.14, -0.04, -0.06, 0.06, 0.0, 0.105), _box(0.04, 0.14, -0.06, 0.06, 0.0, 0.105),
                 _box(-0.15, 0.15, -0.1, 0.1, 0.105, 1.1), _box(-0.12, 0.12, -0.09, 0.09, 1.1, 1.2),
                 _box(-0.16, 0.16, -0.11, 0.11, 1.2, 1.45), _box(-0.8, 0.8, -0.04, 0.04, 1.40, 1.45))
    by_U = mesh_slices.measure_candidate(V, T)["by_U"]
    assert by_U["hem_h"] == pytest.approx(0.09) and not {"ankle", "calf", "knee", "thigh"} & set(by_U)


def test_slice_rows_and_the_check_picture(tmp_path):
    V, T = _body()
    rows = mesh_slices.slice_rows(V, T, [0.3, 0.9])
    assert [len(r) for r in rows.values()] == [2, 1]                       # 脚 2 本、胴 1 つ（腕の高さより下）
    left = rows["0.300"][0]
    assert left[:3] == pytest.approx([-0.14 / 1.45, -0.04 / 1.45, 0.12 / 1.45]) and left[3] == 8
    m = mesh_slices.measure_candidate(V, T)
    mesh_slices.check_picture(V, m, tmp_path / "check.png")
    picture = np.asarray(Image.open(tmp_path / "check.png"))
    assert picture.shape[1] == 700 and (picture == [26, 77, 255]).all(axis=2).any()       # 腕の高さの青い線


def test_bodies_that_cannot_be_measured_are_refused():
    V, T = _box(-1, 1, -1, 1, 0, 1)
    with pytest.raises(ToolError, match="高さが 0"):
        mesh_slices.measure_candidate(V * [1, 1, 0], T)                     # 平らで高さが 0
    plate = (np.array([[-2, -0.1, 0], [2, -0.1, 0], [2, 0.1, 0], [-2, 0.1, 0]], float), np.array([[0, 1, 2], [0, 2, 3]]))
    V, T = _join(plate, _box(-0.2, 0.2, -0.1, 0.1, 0.0, 1.5))            # 横にいちばん遠い頂点たちが床の高さ（腕が無い）
    with pytest.raises(ToolError, match="腕の先"):
        mesh_slices.measure_candidate(V, T)
    with pytest.raises(ToolError, match="メッシュ"):
        mesh_slices.measure_candidate(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64))


STAND_IN = r'''
import json, sys
from pathlib import Path
import numpy as np
from PIL import Image
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
data = np.load(Path(value("--model")).with_suffix(".npz"))
with open(value("--mesh"), "wb") as stream:
    np.savez(stream, V=data["V"], T=data["T"], names=np.array(["body"]))
if "--views" in args:
    folder = Path(value("--views"))
    folder.mkdir(parents=True, exist_ok=True)
    width, height = (int(v) for v in value("--size").split("x"))
    names = ["front", "side", "back", "oblique" if value("--kind") == "head" else "below"]
    for k, name in enumerate(names):
        Image.new("RGB", (width, height), (40 * k, 200, 200)).save(folder / (name + ".png"))
print("CANDIDATE_MESH", " ".join(args))
'''


class Context:
    tools, recipe_dir = {}, None

    def __init__(self, step_dir):
        self.step_dir = step_dir


def _candidate(tmp_path):
    V, T = _body()
    np.savez(tmp_path / "candidate.npz", V=V, T=T)
    (tmp_path / "candidate.fbx").write_bytes(b"fbx")
    return tmp_path / "candidate.fbx"


def test_candidate_operation_measures_and_lays_out_the_views(tmp_path):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    outputs = {"report": tmp_path / "r.json", "views": tmp_path / "views.png", "check": tmp_path / "check.png"}
    result = get("check.candidate").function({"model": _candidate(tmp_path)}, {"blender": str(blender), "slices": [0.3], "size": [40, 60],
                                                                                "thigh_at": 0.4}, outputs, Context(tmp_path))
    report = json.loads(outputs["report"].read_text(encoding="utf-8"))
    assert report["kind"] == "body" and report["verts"] == 48 and report["body"]["rows"]["waist"]["at"] == pytest.approx(0.8)
    assert report["body"]["rows"]["thigh"]["at"] == 0.4
    assert report["views"] == ["front.png", "side.png", "back.png", "below.png"] and len(report["slices"]["0.300"]) == 2
    assert Image.open(outputs["views"]).size == (4 * 40 + 3 * 4, 60) and Image.open(outputs["check"]).width == 700
    assert result.review is True
    head = get("check.candidate").function({"model": _candidate(tmp_path)}, {"blender": str(blender), "kind": "head", "report_only": True},
                                           {"report": tmp_path / "h.json", "views": tmp_path / "h.png"}, Context(tmp_path))
    report = json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))
    assert head.review is False and "body" not in report and report["views"][-1] == "oblique.png"


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"kind": "hand"}, None, "kind"),
    ({"kind": "head"}, {"report": "r.json", "check": "c.png"}, "check"),
    ({"slices": [1.5]}, None, "slices"),
    ({"slices": "0.3"}, None, "slices"),
    ({"size": [8, 8]}, None, "size"),
    ({"gap": -1}, None, "gap"),
    ({"thigh_at": 1.5}, None, "thigh_at"),
    ({"thigh_at": 0}, None, "thigh_at"),
    ({"kind": "head", "thigh_at": 0.4}, None, "thigh_at"),
    ({"wobble": 1}, None, "知らないパラメーター"),
])
def test_candidate_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    declared = {k: tmp_path / v for k, v in (outputs or {"report": "r.json"}).items()}
    with pytest.raises(ToolError, match=message):
        get("check.candidate").function({"model": _candidate(tmp_path)}, {"blender": str(blender), **parameters}, declared, Context(tmp_path))


def test_candidate_needs_a_model_file_blender_can_read(tmp_path):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "candidate.stl").write_bytes(b"stl")
    with pytest.raises(ToolError, match=".fbx"):
        get("check.candidate").function({"model": tmp_path / "candidate.stl"}, {"blender": str(blender)}, {"report": tmp_path / "r.json"},
                                        Context(tmp_path))
