"""check.assembly: 合成の箱の組み立て（T ポーズの体・鼻の出た頭・髪）で、頭身の測り方・提案・描く絵の並び（Blender なし）と、
物体の集め方・工程の受け渡し・絵・悪い設定（代役の Blender）を確かめる。3 作目の VRM のダンスの組み立てでの突き合わせ（台本と同じ数・
同じ絵）は docs/model3d.md（リポジトリ外の素材なので CI には無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import assembly, assembly_measure
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


def _parts():
    """体（脚・腰・くびれ（幅 0.24、1.1〜1.2 m）・胸・腕（1.40〜1.45 m）・首（幅 0.08、1.45〜1.5 m））、頭（0.2 m の箱に、前へ 3 cm
    出た鼻（1.56〜1.58 m））、髪（てっぺん 1.75 m）。"""
    body = _join(_box(-0.14, -0.04, -0.06, 0.06, 0.0, 0.9), _box(0.04, 0.14, -0.06, 0.06, 0.0, 0.9),
                 _box(-0.15, 0.15, -0.1, 0.1, 0.9, 1.1), _box(-0.12, 0.12, -0.09, 0.09, 1.1, 1.2),
                 _box(-0.16, 0.16, -0.11, 0.11, 1.2, 1.45), _box(-0.8, 0.8, -0.04, 0.04, 1.40, 1.45),
                 _box(-0.04, 0.04, -0.04, 0.04, 1.45, 1.5))
    head = _join(_box(-0.1, 0.1, -0.1, 0.1, 1.5, 1.7), _box(-0.01, 0.01, -0.13, -0.09, 1.56, 1.58))
    hair = _box(-0.11, 0.11, -0.11, 0.11, 1.62, 1.75)
    return body, head, hair


def _centre(part):
    """頭の箱の中心（頭と髪を大きくする中心）。"""
    return [float(v) for v in (part[0].min(axis=0) + part[0].max(axis=0)) / 2]


def _moved(part, plan):
    V, T = part
    return np.array(plan["to"]) + plan["scale"] * (V - np.array(plan["pivot"])), T


def test_the_assembly_is_measured_on_the_feet_to_waist_scale():
    body, head, hair = _parts()
    m = assembly_measure.measure(body, head, hair)
    b = m["body"]
    assert (b["floor_z"], b["top_z"], b["centre_x"]) == (0.0, 1.5, 0.0) and b["arm_z"] == pytest.approx(1.425)
    assert b["waist_z"] == pytest.approx(0.80 * 1.425) and b["U"] == pytest.approx(0.80 * 1.425)
    assert (b["waist_width"], b["waist_depth"]) == (pytest.approx(0.24), pytest.approx(0.18))
    assert b["neck"][0] == {"below_top": 0.003, "width": pytest.approx(0.08), "depth": pytest.approx(0.08), "cx": pytest.approx(0.0),
                            "cy": pytest.approx(0.0)}
    h = m["head"]
    assert (h["bottom_z"], h["top_z"], h["face_x"]) == (1.5, 1.7, 0.0)
    assert h["nose"] == {"z": pytest.approx(1.56), "y": pytest.approx(-0.13)}          # 下 2/3 でいちばん前
    assert h["chin"] == {"z": pytest.approx(1.5), "y": pytest.approx(-0.1)}            # 鼻より下で、前下 45° にいちばん出た所
    assert h["width_at_nose"] == pytest.approx(0.2) and h["profile"][0][0] == pytest.approx(1.5)
    assert m["hair"]["top_centre_z"] == pytest.approx(1.75) and m["hair"]["max_z"] == 1.75
    r = m["ratio"]
    assert r["heads"] == pytest.approx(7.0) and r["head_length"] == pytest.approx(0.25)
    assert r["top_U"] == pytest.approx(1.75 / 1.14) and r["chin_U"] == pytest.approx(1.5 / 1.14)
    assert r["chin_above_body_top"] == pytest.approx(0.0) and r["head_bottom_below_body_top"] == pytest.approx(0.0)


@pytest.mark.parametrize("reference, anchor, heads, chin", [
    ({"top_U": 1.5, "chin_U": 1.3}, None, 1.5 / 0.2, 1.3 * 1.14),
    (None, {"z": 1.45, "gap": 0.1, "heads": 7.0}, 7.0, 1.45 + 0.1 * 1.45 / 5.9),
    ({"top_U": 1.5, "chin_U": 1.3}, {"z": 1.45, "gap": 0.1, "heads": 7.0}, 7.0, 1.45 + 0.1 * 1.45 / 5.9),   # 両方なら目印
])
def test_the_proposal_puts_the_head_where_the_reference_or_the_anchor_says(reference, anchor, heads, chin):
    body, head, hair = _parts()
    now = assembly_measure.measure(body, head, hair)
    pivot = _centre(head)                                                  # 鼻の分だけ前（y が −）に寄った箱の中心
    plan = assembly_measure.proposal(now, pivot, reference, anchor, {"y": 0.02})
    assert plan["from"] == ("anchor on the body + heads" if anchor else "reference ratio (U)")
    assert plan["target_chin_z"] == pytest.approx(chin) and plan["to"][:2] == [pivot[0], pytest.approx(pivot[1] + 0.02)]
    after = assembly_measure.measure(body, _moved(head, plan), _moved(hair, plan))
    assert after["head"]["chin"]["z"] == pytest.approx(chin) and after["ratio"]["heads"] == pytest.approx(heads)
    assert after["ratio"]["head_length"] == pytest.approx(plan["target_head_length"])
    if anchor is None:
        assert (after["ratio"]["top_U"], after["ratio"]["chin_U"]) == (pytest.approx(1.5), pytest.approx(1.3))


def test_a_placement_by_hand_and_proposals_that_cannot_be_made():
    body, head, hair = _parts()
    now, pivot = assembly_measure.measure(body, head, hair), _centre(head)
    plan = assembly_measure.proposal(now, pivot, place={"scale": 2.0, "z": 1.4, "y": -0.01})
    assert (plan["from"], plan["scale"], plan["target_head_length"]) == ("manual", 2.0, None)
    assert plan["to"] == [pivot[0], pytest.approx(pivot[1] - 0.01), 1.4] and plan["pivot"] == pivot
    plan = assembly_measure.proposal(now, pivot, {"top_U": 1.5, "chin_U": 1.3}, place={"z": 1.45})
    assert plan["from"] == "reference ratio (U)" and plan["to"][2] == 1.45 and plan["scale"] == pytest.approx(0.912)
    with pytest.raises(ValueError, match="reference か anchor"):
        assembly_measure.proposal(now, pivot, place={"scale": 2.0})
    with pytest.raises(ValueError, match="正になりません"):
        assembly_measure.proposal(now, pivot, anchor={"z": -0.5, "gap": 0.1, "heads": 7.0})


def test_assemblies_that_cannot_be_measured_are_refused():
    body, head, hair = _parts()
    with pytest.raises(ValueError, match="頭の高さが 0"):
        assembly_measure.measure(body, (head[0] * [1, 1, 0] + [0, 0, 1.6], head[1]), hair)
    with pytest.raises(ValueError, match="髪の"):
        assembly_measure.measure(body, head, (hair[0] + [0.5, 0, 0], hair[1]))           # 顔の真ん中の切り口に髪が無い
    plate = (np.array([[-2, -0.1, 0], [2, -0.1, 0], [2, 0.1, 0], [-2, 0.1, 0]], float), np.array([[0, 1, 2], [0, 2, 3]]))
    with pytest.raises(ValueError, match="腕の先"):
        assembly_measure.measure(_join(plate, _box(-0.2, 0.2, -0.1, 0.1, 0.0, 1.5)), head, hair)
    with pytest.raises(ValueError, match="あご"):
        assembly_measure.measure(body, _box(-0.1, 0.1, -0.1, 0.1, 1.5, 1.7), hair)              # 鼻の無い箱: 鼻先がいちばん下になる


def test_the_views_and_their_rows():
    m = assembly_measure.measure(*_parts())
    plan = assembly_measure.view_plan(m, "now", 0.62, 0.10)
    names = [p[0] for p in plan]
    assert names == ["now_front_tex", "now_side_tex", "now_bust_front_tex", "now_bust_side_tex", "now_front_clay", "now_side_clay",
                     "now_bust_front_clay", "now_bust_side_clay", "now_back_tex", "now_bust_oblique_tex"]
    front = dict(zip(("name", "direction", "centre", "ortho", "res", "mode"), plan[0]))
    assert m["reach"] == {"x": pytest.approx(0.8), "y": pytest.approx(0.13)}       # 腕の先と、鼻の先
    assert front["centre"] == (0.0, 0.0, pytest.approx(1.75 / 2)) and front["res"] == (1000, 1500)
    assert front["ortho"] * 1000 / 1500 == pytest.approx(2 * 0.8 * 1.06)           # 縦長の絵: 横の範囲に腕の先まで入るよう広げる
    side = dict(zip(("name", "direction", "centre", "ortho", "res", "mode"), plan[1]))
    assert side["ortho"] == pytest.approx(1.75 * 1.06)                              # 横から: 奥行きは入るので、高さの 1.06 倍
    bust = dict(zip(("name", "direction", "centre", "ortho", "res", "mode"), plan[2]))
    assert bust["centre"][2] == pytest.approx((1.75 + 1.5) / 2 - 0.10) and bust["ortho"] == 0.62 and bust["res"] == (1100, 1100)
    view = {"centre": list(side["centre"]), "res": list(side["res"]), "ppm": 1500 / side["ortho"]}
    assert assembly_measure.view_row(view, 1.75 / 2) == 750 and assembly_measure.view_row(view, 0.0) == pytest.approx(750 + 750 / 1.06)
    assert set(assembly_measure.landmarks(m)) == {"hair top", "chin", "nose", "body top", "head bottom", "waist", "floor"}


class _Ob:
    def __init__(self, name, kind="MESH", parent=None, hidden=False):
        self.name, self.type, self.parent, self.hide_render = name, kind, parent, hidden


class _Scene:
    def __init__(self, objects):
        self.objects = objects


def test_objects_are_collected_by_name_and_only_the_top_named_ones_move():
    body_root, head_root = _Ob("body", "EMPTY"), _Ob("head", "EMPTY")
    obs = [body_root, _Ob("Body", parent=body_root), _Ob("Skirt", parent=body_root), _Ob("Hidden", parent=body_root, hidden=True),
           head_root, _Ob("Face", parent=head_root), _Ob("Hair"), _Ob("Tassel", parent=None)]
    scene = _Scene(obs)
    assert [o.name for o in assembly.collect(scene, ["body"])] == ["Body", "Skirt"]          # 描く物のメッシュだけ、シーンの並び順
    assert [o.name for o in assembly.collect(scene, ["head", "Face"])] == ["Face"]
    assert [o.name for o in assembly.movers(scene, ["head", "Face", "Hair"])] == ["head", "Hair"]   # 子は親について動く
    groups = {"body": assembly.collect(scene, ["body"]), "head": assembly.collect(scene, ["head"]), "hair": assembly.collect(scene, ["Hair"])}
    assert assembly.check_groups(groups, assembly.movers(scene, ["head", "Hair"])) is None
    groups["hair"] = assembly.collect(scene, ["Skirt"])
    assert "子孫にも" in assembly.check_groups(groups, assembly.movers(scene, ["head"]))
    body_root.parent = head_root                                                            # 体が頭の子なら、頭と一緒に動いてしまう
    groups["hair"] = assembly.collect(scene, ["Hair"])
    assert "動かす物体 head" in assembly.check_groups(groups, assembly.movers(scene, ["head", "Hair"]))


STAND_IN = r'''
import json, sys
from pathlib import Path
from PIL import Image
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
blend = next(a for a in sys.argv[1:sys.argv.index("--")] if a.endswith(".blend"))
raw = json.loads(Path(blend).with_suffix(".json").read_text(encoding="utf-8"))
if "--views" in args:
    folder = Path(value("--views"))
    folder.mkdir(parents=True, exist_ok=True)
    for name, view in raw["views"].items():
        Image.new("RGB", tuple(view["res"]), (120, 120, 130)).save(folder / view["file"])
else:
    raw.pop("views")
Path(value("--report")).write_text(json.dumps(raw), encoding="utf-8")
print("ASSEMBLY_STAND_IN", " ".join(sys.argv[1:]))
'''


class Context:
    tools, recipe_dir = {}, None

    def __init__(self, step_dir):
        self.step_dir = step_dir


def _blend(tmp_path):
    """代役の Blender が返す測りの結果（合成の組み立てを、この部品で測った値）と、.blend の代わりのファイル。絵は小さくする。"""
    body, head, hair = _parts()
    now = assembly_measure.measure(body, head, hair)
    plan = assembly_measure.proposal(now, _centre(head), {"top_U": 1.5, "chin_U": 1.3})
    after = assembly_measure.measure(body, _moved(head, plan), _moved(hair, plan))
    after["head"].pop("profile")
    views = {}
    for tag, m in (("now", now), ("after", after)):
        for name, direction, centre, ortho, res, mode in assembly_measure.view_plan(m, tag, 0.62, 0.10):
            res = (40, 60) if res[0] != res[1] else (44, 44)
            views[name] = {"file": name + ".png", "direction": list(direction), "centre": list(centre), "ortho": ortho, "res": list(res),
                           "ppm": max(res) / ortho, "mode": mode}
    raw = {"schema_version": "model3d-assembly-raw/0.1", "now": now, "proposal": plan, "after": after, "views": views,
           "moved": ["head", "hair"]}
    (tmp_path / "assembly.json").write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / "assembly.blend").write_bytes(b"blend")
    return tmp_path / "assembly.blend"


def _illustration(tmp_path):
    rgba = np.zeros((200, 100, 4), np.uint8)
    rgba[20:190, 40:60] = (200, 160, 140, 255)
    Image.fromarray(rgba).save(tmp_path / "illustration.png")
    return tmp_path / "illustration.png"


ROWS = {"top": 20, "chin": 50, "waist": 110, "sole": 190}


def test_assembly_operation_passes_the_settings_and_draws_the_sheets(tmp_path):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    outputs = {"report": tmp_path / "r.json", "views": tmp_path / "views.png", "bust": tmp_path / "bust.png", "compare": tmp_path / "c.png"}
    result = get("check.assembly").function(
        {"blend": _blend(tmp_path), "illustration": _illustration(tmp_path)},
        {"blender": str(blender), "body": ["body"], "head": ["head"], "hair": ["hair"], "reference": {"top_U": 1.5, "chin_U": 1.3},
         "illustration_rows": ROWS}, outputs, Context(tmp_path))
    settings = json.loads((tmp_path / "assembly_settings.json").read_text(encoding="utf-8"))
    assert settings == {"body": ["body"], "head": ["head"], "hair": ["hair"], "reference": {"top_U": 1.5, "chin_U": 1.3}, "anchor": None,
                        "place": {}, "pivot": None, "bust": [0.62, 0.1]}
    report = json.loads(outputs["report"].read_text(encoding="utf-8"))
    assert report["check"] == "assembly" and report["proposal"]["from"] == "reference ratio (U)"
    assert report["now"]["ratio"]["heads"] == pytest.approx(7.0) and report["after"]["ratio"]["heads"] == pytest.approx(7.5)
    assert Image.open(outputs["views"]).size == (round((5 * 40 + 4 * 8) / 2), round((2 * (40 + 60) + 8) / 2))
    assert Image.open(outputs["bust"]).size == (round((5 * 44 + 4 * 8) / 2), round((2 * (40 + 44) + 8) / 2))
    placed = report["compare"]["placed"]
    view = report["views"]["now_front_tex"]
    sole, waist = (assembly_measure.view_row(view, report["now"]["body"][key]) for key in ("floor_z", "waist_z"))
    assert [p["view"] for p in placed] == ["now_front_tex", "after_front_tex"] and placed[0]["scale"] == pytest.approx((sole - waist) / 80)
    assert outputs["compare"].is_file() and result.review is True
    numbers = get("check.assembly").function(
        {"blend": _blend(tmp_path)}, {"blender": str(blender), "body": ["body"], "head": ["head"], "hair": ["hair"],
                                      "place": {"scale": 2.0, "z": 1.4}, "bust": {"drop": 0.2}, "report_only": True},
        {"report": tmp_path / "n.json"}, Context(tmp_path))
    assert numbers.review is False and "views" not in json.loads((tmp_path / "n.json").read_text(encoding="utf-8"))
    assert json.loads((tmp_path / "assembly_settings.json").read_text(encoding="utf-8"))["bust"] == [0.62, 0.2]


NAMES = {"body": ["body"], "head": ["head"], "hair": ["hair"]}
REFERENCE = {"reference": {"top_U": 1.5, "chin_U": 1.3}}


@pytest.mark.parametrize("parameters, outputs, inputs, message", [
    ({"head": ["head"], "hair": ["hair"], **REFERENCE}, None, None, "body"),
    ({**NAMES, "reference": {"top_U": 1.2, "chin_U": 1.3}}, None, None, "reference"),
    ({**NAMES, "reference": {"top_U": 1.5}}, None, None, "reference"),
    ({**NAMES, "anchor": {"z": 1.4, "gap": 0.1, "heads": 1.05}}, None, None, "anchor"),
    ({**NAMES, "place": {"scale": 0, "z": 1.4}}, None, None, "scale"),
    ({**NAMES, "place": {"scale": 2.0}}, None, None, "reference か anchor"),
    ({**NAMES, "place": {"turn": 2.0}}, None, None, "place"),
    ({**NAMES, **REFERENCE, "pivot": [0, 0]}, None, None, "pivot"),
    ({**NAMES, **REFERENCE, "bust": {"size": 0}}, None, None, "bust"),
    ({**NAMES, **REFERENCE, "bust": {"width": 1}}, None, None, "bust"),
    ({**NAMES, **REFERENCE, "sheet_scale": 2}, None, None, "sheet_scale"),
    ({**NAMES, **REFERENCE, "wobble": 1}, None, None, "知らないパラメーター"),
    ({**NAMES, **REFERENCE, "illustration_rows": ROWS}, {"report": "r.json", "compare": "c.png"}, None, "illustration"),
    ({**NAMES, **REFERENCE, "illustration_rows": {**ROWS, "chin": 10}}, {"report": "r.json", "compare": "c.png"}, "illustration",
     "illustration_rows"),
    ({**NAMES, **REFERENCE, "illustration_rows": ROWS}, None, None, "compare"),
    ({**NAMES, **REFERENCE}, {"views": "v.png"}, None, "report"),
])
def test_assembly_rejects_bad_settings(tmp_path, parameters, outputs, inputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    declared = {k: tmp_path / v for k, v in (outputs or {"report": "r.json"}).items()}
    given = {"blend": _blend(tmp_path)}
    if inputs:
        given["illustration"] = _illustration(tmp_path)
    with pytest.raises(ToolError, match=message):
        get("check.assembly").function(given, {"blender": str(blender), **parameters}, declared, Context(tmp_path))
