"""面領域（役割 → あるメッシュ版の面）: 頂点構成への結び付け・古い対応の検出・工程の select・正確な対応での付け替え。

素材はすべてプログラムで作った合成メッシュ。実キャラの資産は使わない。
"""
import json

import pytest

from m3d_helpers import box, run_cli
from model3d import contracts, demo, executor, recipe, semantic
from model3d.io import ToolError, atomic_json, read_json
from model3d.mesh import Mesh, describe_faces, face_components, submesh, transform, weld


def two_quads(name="pair"):
    """離れた 2 枚の四角形。面 0 と面 1 はどちらも box(2.0)（[-1, 1] の立方体）の中にある。"""
    return Mesh([[0.2, 0, 0], [0.2, 0, 0.4], [0.4, 0, 0.4], [0.4, 0, 0],
                 [-0.4, 0.1, 0], [-0.4, 0.1, 0.4], [-0.2, 0.1, 0.4], [-0.2, 0.1, 0]],
                [[0, 1, 2, 3], [4, 5, 6, 7]], name=name)


def two_boxes():
    """面でつながっていない 2 つの箱（面の構成は box() のまま）。"""
    a, b = box(1.0), box(1.0, center=(3.0, 0.0, 0.0))
    offset = a.vertex_count
    return Mesh(list(a.vertices) + list(b.vertices), a.faces + [[i + offset for i in f] for f in b.faces], name="boxes")


def sash_map(mesh, faces=(0,), parts=()):
    mapping = semantic.build("asset", demo.preset_json(), parts=list(parts))
    semantic.add_region(mapping, "sash", "garment.sash.front", mesh, list(faces), "tester")
    return mapping


def test_face_components_are_ordered_by_first_face_and_described():
    mesh = two_boxes()
    groups = face_components(mesh)
    half = len(box(1.0).faces)
    assert groups == [list(range(half)), list(range(half, 2 * half))]
    summary = describe_faces(mesh, groups[1])
    assert summary["closed"] and summary["faces"] == half and summary["vertices"] == box(1.0).vertex_count
    assert summary["centroid"] == pytest.approx([3.0, 0.0, 0.0]) and summary["area"] == pytest.approx(6.0)
    part, index = submesh(mesh, groups[1])
    assert index["faces"] == groups[1] and part.topology_hash() == box(1.0).topology_hash()


def test_add_region_binds_the_topology_and_validates_the_faces():
    mesh = two_quads()
    mapping = semantic.build("asset", demo.preset_json(), parts=["leg_upper_l_collider"])
    assert "garment.sash.front" in mapping["required_missing"]["parts"]
    entry = semantic.add_region(mapping, "sash", "garment.sash.front", mesh, [0, 0], "tester", note="帯の前", selection={"faces": 1})
    contracts.validate("semantic-map", mapping)
    assert entry == {"role": "garment.sash.front", "part": "pair", "faces": [0], "topology_sha256": mesh.topology_hash(),
                     "basis": "user", "state": "unconfirmed", "note": "帯の前"}
    assert "garment.sash.front" not in mapping["required_missing"]["parts"]   # 面領域で役割が揃った
    assert mapping["history"][-1]["action"] == "add" and mapping["history"][-1]["selection"] == {"faces": 1}
    assert semantic.unconfirmed_roles(mapping) >= {"garment.sash.front"}
    for key, role, faces, message in [("sash", "garment.sash.front", [1], "既にあります"),
                                      ("bad key", "garment.sash.front", [1], "key"),
                                      ("cape", "garment.cape", [1], "知らない部位の役割"),
                                      ("far", "garment.sash.back", [2], "範囲外"),
                                      ("flag", "garment.sash.back", [True], "整数"),
                                      ("empty", "garment.sash.back", [], "0 です")]:
        with pytest.raises(ToolError, match=message):
            semantic.add_region(mapping, key, role, mesh, faces, "tester")
    with pytest.raises(ToolError, match="basis"):
        semantic.add_region(mapping, "other", "garment.sash.back", mesh, [1], "tester", basis="preset")
    semantic.add_region(mapping, "sash", "garment.sash.front", mesh, [1], "tester", replace=True)
    assert mapping["regions"]["sash"]["faces"] == [1]


def test_select_uses_bound_regions_and_named_face_sets_and_refuses_stale_regions():
    mesh = two_quads()
    mapping = sash_map(mesh, parts=["garment_sash_front"])
    chosen = semantic.select(mapping, "garment.sash.front", mesh)
    assert chosen["faces"] == [0] and chosen["vertices"] == [0, 1, 2, 3] and chosen["ignored_stale"] == []
    assert chosen["sources"] == [{"kind": "region", "key": "sash", "state": "unconfirmed", "faces": 1}]
    # 部品の対応（parts）と同じ名前の面集合を持つメッシュでは、その面も選ぶ（結合でメッシュと一緒に付け替わる）
    named = Mesh(mesh.vertices, mesh.faces, name="pair", parts={"garment_sash_front": [1]})
    assert named.topology_hash() == mesh.topology_hash()
    both = semantic.select(mapping, "garment.sash.front", named)
    assert both["faces"] == [0, 1] and [s["kind"] for s in both["sources"]] == ["region", "part"]
    # 構成の違うメッシュ（面が 1 枚多い）には、面番号の範囲に収まっていても当てない
    grown = Mesh(list(mesh.vertices) + [[5, 5, 5]], mesh.faces + [[0, 1, 8]], name="pair")
    with pytest.raises(ToolError, match="古い対応"):
        semantic.select(mapping, "garment.sash.front", grown)
    semantic.confirm(mapping, "regions", "sash", "rejected", "tester")
    with pytest.raises(ToolError, match="当たる面がこのメッシュにありません"):
        semantic.select(mapping, "garment.sash.front", mesh)
    with pytest.raises(ToolError, match="当たる面がこのメッシュにありません"):
        semantic.select(mapping, "hair.front", mesh)


def test_region_status_separates_bound_stale_and_invalid():
    mesh = two_quads()
    mapping = sash_map(mesh)
    mapping["regions"]["old"] = {"role": "hair.front", "part": "pair", "faces": [0], "topology_sha256": "0" * 64,
                                 "basis": "user", "state": "confirmed"}
    mapping["regions"]["loose"] = {"role": "hair.back", "part": "pair", "faces": [0], "basis": "user", "state": "unconfirmed"}
    mapping["regions"]["twin"] = {"role": "hair.side.L", "part": "pair_r", "faces": [0], "topology_sha256": mesh.topology_hash(),
                                  "basis": "user", "state": "confirmed"}
    contracts.validate("semantic-map", mapping)
    status = semantic.region_status(mapping, mesh)
    assert (status["bound"], status["other"], status["stale"], status["invalid"]) == (["sash"], ["twin"], ["old"], ["loose"])
    assert status["regions"]["old"]["status"] == "stale" and "reason" in status["regions"]["old"]


def test_regions_do_not_cross_parts_that_share_a_topology(tmp_path):
    left = two_quads("sleeve_l")
    right = Mesh(left.vertices * [-1.0, 1.0, 1.0], left.faces, name="sleeve_r")   # 左右に写した別の部品。頂点構成は同じ
    assert left.topology_hash() == right.topology_hash()
    mapping = semantic.build("asset", demo.preset_json())
    semantic.add_region(mapping, "cuff_l", "garment.sleeve.L", left, [0], "tester")
    assert semantic.select(mapping, "garment.sleeve.L", left)["faces"] == [0]
    with pytest.raises(ToolError, match="別の部品"):
        semantic.select(mapping, "garment.sleeve.L", right)
    assert semantic.region_status(mapping, right)["other"] == ["cuff_l"]
    # 位置だけを変えた同じ部品（名前を引き継ぐ）と、model3d が書いた OBJ の往復（ファイル名は違う）には当たる
    assert semantic.select(mapping, "garment.sleeve.L", transform(left, translate=[0.1, 0.0, 0.0]))["faces"] == [0]
    left.save(tmp_path / "exported.obj")
    assert semantic.select(mapping, "garment.sleeve.L", Mesh.load(tmp_path / "exported.obj"))["faces"] == [0]
    spaced = Mesh(left.vertices, left.faces, name="袖 L=1")
    spaced.save(tmp_path / "spaced.obj")
    assert Mesh.load(tmp_path / "spaced.obj").name == "袖 L=1"   # 見出しの name は空白・= を含んでも戻る
    with pytest.raises(ToolError, match="名前"):
        semantic.add_region(mapping, "unnamed", "garment.sleeve.R", Mesh(left.vertices, left.faces), [0], "tester")


def test_remap_follows_the_exact_weld_mapping_and_reopens_regions_that_lost_faces():
    # 面 0 は大きな三角形、面 1 は結合で潰れる小さな三角形、面 2 は残る三角形
    mesh = Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 0], [2, 1e-7, 0], [2, 0, 1e-7], [3, 0, 0], [3, 1, 0], [4, 0, 0]],
                [[0, 1, 2], [3, 4, 5], [6, 8, 7]], name="tri")
    mapping = semantic.build("asset", demo.preset_json())
    semantic.add_region(mapping, "keep", "garment.sash.front", mesh, [0, 2], "tester")
    semantic.add_region(mapping, "lose", "garment.sash.back", mesh, [1, 2], "tester")
    for key in ("keep", "lose"):
        semantic.confirm(mapping, "regions", key, "confirmed", "tester")
    mapping["regions"]["elsewhere"] = {"role": "hair.front", "part": "x", "faces": [0], "topology_sha256": "0" * 64,
                                       "basis": "user", "state": "confirmed"}
    welded, report = weld(mesh, 1e-6)
    table = report["vertex_mapping"]
    assert table["face_old_to_new"] == [0, -1, 1] and welded.face_count == 2
    with pytest.raises(ToolError, match="古い対応"):
        semantic.select(mapping, "garment.sash.front", welded)
    result = semantic.remap_regions(mapping, table, "tester")
    contracts.validate("semantic-map", mapping)
    assert result["untouched"] == ["elsewhere"]
    assert result["remapped"]["keep"] == {"faces": 2, "lost": 0, "state": "confirmed"}
    assert result["remapped"]["lose"] == {"faces": 1, "lost": 1, "state": "unconfirmed"}   # 面が消えたので見直しに戻す
    assert mapping["regions"]["keep"]["faces"] == [0, 1] and mapping["regions"]["lose"]["faces"] == [1]
    assert semantic.select(mapping, "garment.sash.front", welded)["faces"] == [0, 1]
    assert mapping["history"][-1]["action"] == "remap"
    for broken, message in [({**table, "face_old_to_new": None}, "面の対応"),
                            ({**table, "face_old_to_new": [0, 0, 1]}, "1 対 1"),
                            ({**table, "face_old_to_new": [0, True, 1]}, "整数")]:
        with pytest.raises(ToolError, match=message):
            semantic.remap_regions(mapping, broken, "tester")


@pytest.fixture
def regions(tmp_path):
    """2 枚の四角形（どちらも箱の中）と箱、面 0 だけを帯の前に当てた対応表。"""
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    box(2.0).save(inputs / "box.json")
    pair = two_quads()
    pair.save(inputs / "pair.json")
    atomic_json(tmp_path / "semantic_map.json", sash_map(pair))

    def write(steps, **extra):
        data = {"schema_version": "model3d-recipe/0.1", "name": "regions", "semantic_map": "semantic_map.json",
                "inputs": {"box": {"path": "inputs/box.json"}, "pair": {"path": "inputs/pair.json"}}, "steps": steps, **extra}
        atomic_json(tmp_path / "recipe.json", data)
        return tmp_path / "recipe.json"

    return tmp_path, write


PUSH = {"id": "push", "operation": "collision.push_out", "inputs": {"mesh": "$inputs.pair", "collider": "$inputs.box"},
        "select": {"mesh": "garment.sash.front"}, "parameters": {"clearance": 0.01},
        "outputs": {"mesh": "fixed.json", "deltas": "deltas.json"}}
REGION_CHECK = {"id": "after", "operation": "check.penetration", "inputs": {"mesh": "$steps.push.mesh", "collider": "$inputs.box"},
                "select": {"mesh": "garment.sash.front"}, "parameters": {"strict": True}, "outputs": {"report": "after.json"}}
WHOLE_CHECK = {"id": "whole", "operation": "check.penetration", "inputs": {"mesh": "$steps.push.mesh", "collider": "$inputs.box"},
               "parameters": {"report_only": True}, "outputs": {"report": "whole.json"}}


def test_select_moves_only_the_region_and_checks_only_the_region(regions):
    root, write = regions
    path = write([PUSH, REGION_CHECK, WHOLE_CHECK])
    first = executor.run_recipe(path, root / "runs")
    assert first["status"] == "needs_review" and first["candidates"] == ["push", "after"]   # 帯の面領域が未確認
    push = first["steps"]["push"]
    assert push["observations"][0]["kind"] == "selection" and push["observations"][0]["faces"] == 1
    assert push["observations"][0]["vertices"] == 4 and push["observations"][0]["sources"][0]["key"] == "sash"
    assert push["observations"][1]["points"] == 4 and push["observations"][1]["inside"] == 4
    fixed = Mesh.load(push["artifacts"]["mesh"])
    original = two_quads()
    assert (fixed.vertices[4:] == original.vertices[4:]).all()   # 面領域の外の頂点は動かさない
    assert (fixed.vertices[:4] != original.vertices[:4]).any(axis=1).all()
    deltas = read_json(push["artifacts"]["deltas"])
    assert sorted(index for index, _ in deltas["deltas"]) == [0, 1, 2, 3]
    after = first["steps"]["after"]["observations"][1]
    assert after["pass"] and after["checked"] == 4 and after["inside"] == 0
    whole = first["steps"]["whole"]["observations"][0]
    assert not whole["pass"] and whole["inside_indices"] == [4, 5, 6, 7] and "checked" not in whole
    record = read_json(root / "runs" / first["run_id"] / "run_record.json")
    assert record["steps"][0]["select"] == {"mesh": "garment.sash.front"}
    contracts.validate("run", record)

    mapping = read_json(root / "semantic_map.json")
    semantic.confirm(mapping, "regions", "sash", "confirmed", "tester")
    atomic_json(root / "semantic_map.json", mapping)
    confirmed = executor.run_recipe(path, root / "runs")
    assert confirmed["status"] == "succeeded" and confirmed["steps"]["push"]["unresolved"] == []
    reused = executor.run_recipe(path, root / "runs")
    assert reused["steps"]["push"]["reused_from"] == confirmed["run_id"]
    assert reused["steps"]["push"]["observations"][0]["kind"] == "selection"


def test_select_refuses_a_stale_region_instead_of_touching_the_whole_mesh(regions):
    root, write = regions
    grown = two_quads()
    grown = Mesh(list(grown.vertices) + [[0.0, 0.5, 0.5]], grown.faces + [[0, 1, 8]], name="pair")
    grown.save(root / "inputs" / "pair.json")   # 面領域を作った後でメッシュの頂点構成が変わった
    result = executor.run_recipe(write([PUSH]), root / "runs")
    assert result["status"] == "failed" and "古い対応" in result["error"]
    assert result["steps"]["push"]["artifacts"] == {}


def test_select_refuses_a_region_of_another_part_with_the_same_topology(regions):
    root, write = regions
    twin = two_quads()
    Mesh(twin.vertices + [0.0, 0.2, 0.0], twin.faces, name="twin").save(root / "inputs" / "twin.json")
    step = dict(PUSH, inputs={"mesh": "$inputs.twin", "collider": "$inputs.box"})
    result = executor.run_recipe(write([step], inputs={"box": {"path": "inputs/box.json"}, "pair": {"path": "inputs/pair.json"},
                                                       "twin": {"path": "inputs/twin.json"}}), root / "runs")
    assert result["status"] == "failed" and "別の部品" in result["error"]
    assert result["steps"]["push"]["artifacts"] == {}


def test_select_is_checked_before_anything_runs(regions):
    root, write = regions
    move = {"id": "move", "operation": "mesh.transform", "inputs": {"mesh": "$inputs.pair"},
                 "select": {"mesh": "garment.sash.front"}, "parameters": {"scale": 2.0}, "outputs": {"mesh": "moved.json"}}
    with pytest.raises(ToolError, match="select（面領域の選択）に対応していません"):
        executor.run_recipe(write([move]), root / "runs")
    with pytest.raises(ToolError, match="工程の入力にありません"):
        executor.run_recipe(write([dict(PUSH, select={"other": "garment.sash.front"})]), root / "runs")
    data = json.loads(write([PUSH]).read_text(encoding="utf-8"))
    del data["semantic_map"]
    atomic_json(root / "recipe.json", data)
    with pytest.raises(ToolError, match="semantic_map"):
        executor.run_recipe(root / "recipe.json", root / "runs")
    assert not (root / "runs").exists()


def test_select_and_a_selection_parameter_are_not_mixed(regions):
    root, write = regions
    result = executor.run_recipe(write([dict(PUSH, parameters={"clearance": 0.01, "selection": [4]})]), root / "runs")
    assert result["status"] == "failed" and "同時に使えません" in result["error"]


def test_select_is_part_of_the_step_key():
    step = {"id": "push", "operation": "collision.push_out", "parameters": {}, "outputs": {"mesh": "m.json"}}
    plain = recipe.step_key(step, {"mesh": "a" * 64}, "f", {"numpy": "x"})
    front = recipe.step_key(dict(step, select={"mesh": "garment.sash.front"}), {"mesh": "a" * 64}, "f", {"numpy": "x"})
    back = recipe.step_key(dict(step, select={"mesh": "garment.sash.back"}), {"mesh": "a" * 64}, "f", {"numpy": "x"})
    assert len({plain, front, back}) == 3


def test_extract_region_writes_the_part_and_its_mapping(regions):
    root, write = regions
    step = {"id": "cut", "operation": "mesh.extract_region", "inputs": {"mesh": "$inputs.pair"},
            "select": {"mesh": "garment.sash.front"}, "outputs": {"mesh": "sash.json", "mapping": "sash_mapping.json"}}
    result = executor.run_recipe(write([step]), root / "runs")
    assert result["status"] == "needs_review"   # 面領域が未確認
    cut = result["steps"]["cut"]
    part = Mesh.load(cut["artifacts"]["mesh"])
    assert part.vertex_count == 4 and part.faces == [[0, 1, 2, 3]] and part.name == "garment.sash.front"
    table = read_json(cut["artifacts"]["mapping"])
    assert table["vertices_new_to_old"] == [0, 1, 2, 3] and table["faces_new_to_old"] == [0]
    assert table["source_topology_sha256"] == two_quads().topology_hash()
    observed = cut["observations"][1]
    assert observed["kind"] == "region" and observed["components"] == 1 and observed["closed"] is False
    plain = {key: value for key, value in step.items() if key != "select"}
    failed = executor.run_recipe(write([plain]), root / "runs")   # select の無い切り出しは工程が止まる
    assert failed["status"] == "failed" and "select" in failed["error"]


def test_cli_binds_checks_previews_and_remaps_regions(tmp_path):
    boxes = two_boxes()
    # 面 0 だけ頂点 0 の写し（継ぎ目の二重頂点）を使う。結合で頂点構成が変わる
    mesh = Mesh(list(boxes.vertices) + [boxes.vertices[0]],
                [[boxes.vertex_count if (k == 0 and i == 0) else i for i in face] for k, face in enumerate(boxes.faces)], name="boxes")
    mesh_path = tmp_path / "boxes.obj"
    mesh.save(mesh_path)
    preset = tmp_path / "preset.json"
    atomic_json(preset, demo.preset_json())
    code, built, _ = run_cli("semantic", "build", preset, tmp_path / "map.json", "--asset-id", "boxes")
    assert code == 0
    code, report, _ = run_cli("semantic", "components", mesh_path, "--out", tmp_path / "components.json",
                              "--obj", tmp_path / "components.obj")
    assert code == 0 and [c["component"] for c in report["components"]] == [0, 1]
    assert not report["components"][0]["closed"] and report["components"][1]["closed"]   # 継ぎ目の側は開いている
    assert set(Mesh.load(tmp_path / "components.obj").parts) == {"component_0", "component_1"}
    half = len(box(1.0).faces)
    code, added, _ = run_cli("semantic", "add-region", tmp_path / "map.json", mesh_path, "--key", "left", "--role", "garment.sash.front",
                             "--component", "0", "--by", "tester", "--basis", "inferred")
    assert code == 0 and added["faces"] == half and added["state"] == "unconfirmed" and added["basis"] == "inferred"
    # 箱で絞る: 成分 1 のうち重心が x >= 3.4 の面（右の箱の +X 側の面）
    code, boxed, _ = run_cli("semantic", "add-region", tmp_path / "map.json", mesh_path, "--key", "right_side", "--role", "garment.sash.back",
                             "--component", "1", "--box", "3.4", "-9", "-9", "9", "9", "9", "--by", "tester")
    assert code == 0 and 0 < boxed["faces"] < half
    (tmp_path / "faces.json").write_text(json.dumps({"faces": [half - 1]}), encoding="utf-8")
    code, listed, _ = run_cli("semantic", "add-region", tmp_path / "map.json", mesh_path, "--key", "one", "--role", "hair.front",
                              "--faces", tmp_path / "faces.json", "--by", "tester")
    assert code == 0 and listed["faces"] == 1
    code, _, err = run_cli("semantic", "add-region", tmp_path / "map.json", mesh_path, "--key", "none", "--role", "hair.back", "--by", "tester")
    assert code == 1 and "選び方" in err
    history = read_json(tmp_path / "map.json")["history"]
    assert history[-1]["selection"]["faces_file"]["sha256"] and history[-2]["selection"]["box"] == [3.4, -9, -9, 9, 9, 9]
    code, status, _ = run_cli("semantic", "check", tmp_path / "map.json", mesh_path)
    assert code == 0 and status["pass"] and status["bound"] == ["left", "one", "right_side"]
    code, preview, _ = run_cli("semantic", "preview", tmp_path / "map.json", mesh_path, tmp_path / "preview.obj")
    assert code == 0 and preview["overlapping_faces"] == 1 and preview["unassigned_faces"] == half - boxed["faces"]
    assert set(Mesh.load(tmp_path / "preview.obj").parts) == {"left", "right_side", "~unassigned"}   # 重なった面は名前順で先の群
    # 頂点構成が変わったメッシュには当たらない → 結合の対応で付け替えると当たる
    moved = Mesh(mesh.vertices, mesh.faces + [[0, 1, 2]], name="boxes")
    moved_path = tmp_path / "moved.obj"
    moved.save(moved_path)
    code, stale, _ = run_cli("semantic", "check", tmp_path / "map.json", moved_path)
    assert code == 1 and not stale["pass"] and len(stale["stale"]) == 3
    welded, report = weld(mesh, 1e-9)
    atomic_json(tmp_path / "mapping.json", report["vertex_mapping"])
    code, remapped, _ = run_cli("semantic", "remap", tmp_path / "map.json", tmp_path / "mapping.json", tmp_path / "map_welded.json",
                                "--by", "tester")
    assert code == 0 and sorted(remapped["remapped"]) == ["left", "one", "right_side"]
    welded.save(tmp_path / "welded.obj")
    code, status, _ = run_cli("semantic", "check", tmp_path / "map_welded.json", tmp_path / "welded.obj")
    assert code == 0 and status["pass"]
    code, _, err = run_cli("semantic", "remap", tmp_path / "map_welded.json", tmp_path / "mapping.json", tmp_path / "again.json",
                           "--by", "tester")
    assert code == 1 and "結び付いた面領域がありません" in err
