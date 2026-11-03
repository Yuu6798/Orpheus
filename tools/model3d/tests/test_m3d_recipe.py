"""レシピの実行器: 依存順・参照・key・再利用・失敗・needs_review・記録。"""
import json
import sys

import pytest

from m3d_helpers import box, run_cli
from model3d import contracts, executor, recipe
from model3d.io import ToolError, atomic_json, read_json, sha256


@pytest.fixture
def workspace(tmp_path):
    """箱と帯（箱に入った点を持つ）だけの小さな recipe。"""
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    box(2.0).save(inputs / "box.json")
    from model3d.mesh import Mesh
    strip = Mesh([[0.5, 0, 0], [0.5, 0, 0.5], [1.5, 0, 0.5], [1.5, 0, 0]], [[0, 1, 2, 3]], name="strip")
    strip.save(inputs / "strip.json")

    def write(steps, **extra):
        data = {"schema_version": "model3d-recipe/0.1", "name": "small", "inputs": {"box": {"path": "inputs/box.json"},
                "strip": {"path": "inputs/strip.json"}}, "steps": steps, **extra}
        atomic_json(tmp_path / "recipe.json", data)
        return tmp_path / "recipe.json"

    return tmp_path, write


PUSH = {"id": "push", "operation": "collision.push_out", "inputs": {"mesh": "$inputs.strip", "collider": "$inputs.box"},
        "parameters": {"clearance": 0.01}, "outputs": {"mesh": "fixed.json", "report": "push.json"}, "preconditions": ["closed:collider"]}
AFTER = {"id": "after", "operation": "check.penetration", "inputs": {"mesh": "$steps.push.mesh", "collider": "$inputs.box"},
         "parameters": {"strict": True}, "outputs": {"report": "after.json"}}


def test_order_steps_resolves_references_and_rejects_cycles_and_unknowns():
    steps = [{"id": "b", "operation": "x.y", "inputs": {"m": "$steps.a.mesh"}, "outputs": {"mesh": "b.json"}},
             {"id": "a", "operation": "x.y", "inputs": {"m": "$inputs.box"}, "outputs": {"mesh": "a.json"}}]
    data = {"schema_version": "model3d-recipe/0.1", "name": "n", "inputs": {"box": {"path": "p"}}, "steps": steps}
    assert [s["id"] for s in recipe.order_steps(data)] == ["a", "b"]
    data["steps"][1]["depends_on"] = ["b"]
    with pytest.raises(ToolError, match="循環"):
        recipe.order_steps(data)
    data["steps"][1]["depends_on"] = []
    data["steps"][0]["inputs"]["m"] = "$steps.a.nope"
    with pytest.raises(ToolError, match="無い出力"):
        recipe.order_steps(data)
    data["steps"][0]["inputs"]["m"] = "$inputs.nope"
    with pytest.raises(ToolError, match="無い入力"):
        recipe.order_steps(data)
    data["steps"][0]["inputs"]["m"] = "$inputs.box"
    data["steps"].append(dict(data["steps"][0]))
    with pytest.raises(ToolError, match="重なって"):
        recipe.order_steps(data)
    with pytest.raises(ToolError, match="参照"):
        recipe.parse_ref("inputs.box")


def test_run_writes_a_valid_record_reuses_succeeded_steps_and_force_reruns(workspace):
    root, write = workspace
    path = write([PUSH, AFTER])
    first = executor.run_recipe(path, root / "runs")
    assert first["status"] == "succeeded" and first["candidates"] == []
    record = contracts.load("run", root / "runs" / first["run_id"] / "run_record.json")
    assert [s["status"] for s in record["steps"]] == ["succeeded", "succeeded"]
    assert record["steps"][0]["observations"][0]["inside"] == 2 and record["steps"][1]["observations"][0]["inside"] == 0
    assert set(record["inputs"]) == {"box", "strip"} and record["environment"]["model3d"]["fingerprint"]
    fixed = root / "runs" / first["run_id"] / "push" / "fixed.json"
    assert fixed.is_file() and record["steps"][0]["artifacts"]["mesh"]["sha256"] == sha256(fixed)
    index = read_json(root / "runs" / "index.json")
    assert len(index["steps"]) == 2
    second = executor.run_recipe(path, root / "runs")
    assert second["status"] == "succeeded"
    assert all(s["reused_from"] == first["run_id"] for s in second["steps"].values())
    assert sha256(root / "runs" / second["run_id"] / "push" / "fixed.json") == sha256(fixed)
    third = executor.run_recipe(path, root / "runs", force=True)
    assert all(s["reused_from"] is None for s in third["steps"].values())
    assert (root / "runs" / first["run_id"] / "run.log").read_text(encoding="utf-8").count("\n") >= 3


def test_changed_parameters_or_inputs_change_the_key_and_are_not_reused(workspace):
    root, write = workspace
    path = write([PUSH])
    first = executor.run_recipe(path, root / "runs")
    changed = dict(PUSH, parameters={"clearance": 0.02})
    second = executor.run_recipe(write([changed]), root / "runs")
    assert second["steps"]["push"]["reused_from"] is None
    assert second["steps"]["push"]["observations"][0]["clearance"] == 0.02
    box(2.2).save(root / "inputs" / "box.json")
    third = executor.run_recipe(write([PUSH]), root / "runs")
    assert third["steps"]["push"]["reused_from"] is None and first["run_id"] != third["run_id"]


def test_tampered_cached_artifact_is_not_reused(workspace):
    root, write = workspace
    path = write([PUSH])
    first = executor.run_recipe(path, root / "runs")
    (root / "runs" / first["run_id"] / "push" / "fixed.json").write_text("{}", encoding="utf-8")
    second = executor.run_recipe(path, root / "runs")
    assert second["steps"]["push"]["reused_from"] is None and second["status"] == "succeeded"


def test_declared_input_hash_mismatch_stops_before_running(workspace):
    root, write = workspace
    path = write([PUSH])
    data = read_json(path)
    data["inputs"]["box"]["sha256"] = "0" * 64
    atomic_json(path, data)
    with pytest.raises(ToolError, match="ハッシュが宣言と違います"):
        executor.run_recipe(path, root / "runs")


def test_failed_step_stops_the_recipe_and_keeps_the_record(workspace):
    root, write = workspace
    strict_before = {"id": "before", "operation": "check.penetration", "inputs": {"mesh": "$inputs.strip", "collider": "$inputs.box"},
                     "parameters": {"strict": True}, "outputs": {"report": "before.json"}}
    path = write([strict_before, PUSH])
    result = executor.run_recipe(path, root / "runs")
    assert result["status"] == "failed" and "不合格" in result["error"]
    record = contracts.load("run", root / "runs" / result["run_id"] / "run_record.json")
    assert [s["status"] for s in record["steps"]] == ["failed", "pending"]
    assert (root / "runs" / result["run_id"] / "before" / "before.json").is_file()   # 検査の報告は失敗でも残る
    assert read_json(root / "runs" / "index.json")["steps"] == {}


def test_needs_review_keeps_candidates_out_of_the_cache(workspace):
    root, write = workspace
    lenient = {"id": "before", "operation": "check.penetration", "inputs": {"mesh": "$inputs.strip", "collider": "$inputs.box"},
               "parameters": {}, "outputs": {"report": "before.json"}}
    measure = dict(lenient, id="measure", parameters={"report_only": True}, outputs={"report": "measure.json"})
    path = write([lenient, measure, PUSH])
    result = executor.run_recipe(path, root / "runs")
    assert result["status"] == "needs_review" and result["candidates"] == ["before"]
    assert result["steps"]["before"]["status"] == "needs_review" and result["steps"]["before"]["unresolved"]
    assert result["steps"]["measure"]["status"] == "succeeded" and result["steps"]["measure"]["observations"][0]["inside"] == 2
    index = read_json(root / "runs" / "index.json")
    assert {v["step"] for v in index["steps"].values()} == {"measure", "push"}


def test_preconditions_and_roles_are_enforced(workspace, tmp_path):
    root, write = workspace
    from model3d import demo, semantic
    preset = demo.preset_json()
    atomic_json(root / "preset.json", preset)
    semantic_map = semantic.build("asset", preset, parts=["garment_sash_front"], bones=["mixamorig:Hips"], preset_path=root / "preset.json")
    atomic_json(root / "semantic_map.json", semantic_map)
    with_role = dict(PUSH, applies_to=["garment.sash.front"], preconditions=["closed:collider", "role:garment.sash.front", "units:m", "帯の面が確定していること"])
    result = executor.run_recipe(write([with_role], preset="preset.json", semantic_map="semantic_map.json"), root / "runs")
    assert result["status"] == "needs_review" and result["warnings"] and "未確認" in result["warnings"][0]
    assert result["candidates"] == ["push"] and len(result["steps"]["push"]["unresolved"]) == 1
    missing = dict(with_role, applies_to=["garment.sash.back"])
    result = executor.run_recipe(write([missing], preset="preset.json", semantic_map="semantic_map.json"), root / "runs")
    assert result["status"] == "failed" and "garment.sash.back" in result["error"]
    without_map = dict(PUSH, applies_to=["garment.sash.front"])
    result = executor.run_recipe(write([without_map]), root / "runs")
    assert result["status"] == "needs_review" and "SemanticMap" in result["steps"]["push"]["unresolved"][0]
    bad_kind = dict(PUSH, preconditions=["weird:thing"])
    result = executor.run_recipe(write([bad_kind]), root / "runs")
    assert result["status"] == "failed" and "知らない前提" in result["error"]
    open_collider = dict(PUSH, inputs={"mesh": "$inputs.box", "collider": "$inputs.strip"})
    result = executor.run_recipe(write([open_collider]), root / "runs")
    assert result["status"] == "failed" and "閉じていません" in result["error"]


@pytest.mark.parametrize("role_declaration", [
    {"applies_to": ["garment.sash.front"]},
    {"preconditions": ["role:garment.sash.front"]},
    {"applies_to": ["garment.sash.front"], "preconditions": ["role:garment.sash.front"]},
])
def test_used_unconfirmed_roles_remain_candidates_until_confirmed(workspace, role_declaration):
    from model3d import demo, semantic
    root, write = workspace
    mapping = semantic.build("asset", demo.preset_json(), parts=["garment_sash_front"], bones=["mixamorig:Hips"])
    atomic_json(root / "semantic_map.json", mapping)
    path = write([dict(PUSH, **role_declaration), AFTER], semantic_map="semantic_map.json")
    first = executor.run_recipe(path, root / "runs")
    second = executor.run_recipe(path, root / "runs")
    for result in (first, second):
        assert result["status"] == "needs_review" and result["candidates"] == ["push"]
        step = result["steps"]["push"]
        assert step["status"] == "needs_review" and step["reused_from"] is None
        assert len(step["unresolved"]) == 1 and "garment.sash.front" in step["unresolved"][0]
        # 検査は候補を調べられるが、その合格で元工程や run の未確認を消さない。
        assert result["steps"]["after"]["status"] == "succeeded"
    index = read_json(root / "runs" / "index.json")
    assert {item["step"] for item in index["steps"].values()} == {"after"}

    semantic.confirm(mapping, "parts", "garment_sash_front", "confirmed", "tester")
    atomic_json(root / "semantic_map.json", mapping)
    confirmed = executor.run_recipe(path, root / "runs")
    assert confirmed["status"] == "succeeded" and confirmed["candidates"] == []
    assert confirmed["steps"]["push"]["unresolved"] == []
    assert confirmed["warnings"]   # 使用しない hips は未確認のまま。加工を妨げない。
    reused = executor.run_recipe(path, root / "runs")
    assert reused["steps"]["push"]["reused_from"] == confirmed["run_id"]


def test_unscoped_steps_are_not_blocked_by_unrelated_unconfirmed_roles(workspace):
    from model3d import demo, semantic
    root, write = workspace
    mapping = semantic.build("asset", demo.preset_json(), parts=["garment_sash_front"], bones=["mixamorig:Hips"])
    atomic_json(root / "semantic_map.json", mapping)
    result = executor.run_recipe(write([PUSH], semantic_map="semantic_map.json"), root / "runs")
    assert result["status"] == "succeeded" and result["warnings"]
    assert result["steps"]["push"]["unresolved"] == []


def test_preset_or_semantic_map_changes_invalidate_reuse(workspace):
    """check.structure は preset（必須の骨）と対応表を読む。どちらかが変われば key が変わり、古い報告を再利用しない。"""
    root, write = workspace
    from model3d import demo, semantic
    preset = demo.preset_json()
    atomic_json(root / "preset.json", preset)
    rig = demo.rig_json(demo.sash_mesh().vertex_count)   # ウェイトの頂点番号は帯の頂点数が前提
    atomic_json(root / "inputs" / "rig.json", rig)
    semantic_map = semantic.build("asset", preset, bones=[{"name": b["name"], "parent": b["parent"]} for b in rig["bones"]],
                                  preset_path=root / "preset.json")
    for name in semantic_map["bones"]:
        semantic.confirm(semantic_map, "bones", name, "confirmed", "tester")
    atomic_json(root / "semantic_map.json", semantic_map)
    check = {"id": "structure", "operation": "check.structure", "inputs": {"rig": "$inputs.rig"},
             "parameters": {"required_bone_roles": "preset"}, "outputs": {"report": "structure.json"}}
    data = read_json(write([check], preset="preset.json", semantic_map="semantic_map.json"))
    data["inputs"]["rig"] = {"path": "inputs/rig.json"}
    atomic_json(root / "recipe.json", data)
    first = executor.run_recipe(root / "recipe.json", root / "runs")
    assert first["status"] == "succeeded"
    preset["required_roles"]["bones"].append("toe.L")          # リグに無い骨を必須にする
    atomic_json(root / "preset.json", preset)
    second = executor.run_recipe(root / "recipe.json", root / "runs")
    assert second["steps"]["structure"]["reused_from"] is None and second["status"] == "needs_review"
    assert "toe.L" in second["steps"]["structure"]["unresolved"][0]
    preset["required_roles"]["bones"].remove("toe.L")
    atomic_json(root / "preset.json", preset)
    third = executor.run_recipe(root / "recipe.json", root / "runs")
    assert third["steps"]["structure"]["reused_from"] == first["run_id"]
    semantic.confirm(semantic_map, "bones", "mixamorig:Hips", "rejected", "tester")
    atomic_json(root / "semantic_map.json", semantic_map)
    fourth = executor.run_recipe(root / "recipe.json", root / "runs")
    assert fourth["steps"]["structure"]["reused_from"] is None and fourth["status"] == "needs_review"


def test_push_out_deltas_carry_the_step_space(workspace):
    root, write = workspace
    rest = dict(PUSH, space="rest", outputs={"mesh": "fixed.json", "deltas": "deltas.json"})
    result = executor.run_recipe(write([rest]), root / "runs")
    assert result["status"] == "succeeded"
    assert read_json(result["steps"]["push"]["artifacts"]["deltas"])["space"] == "rest"
    assert result["steps"]["push"]["observations"][0]["space"] == "rest"
    record = contracts.load("run", root / "runs" / result["run_id"] / "run_record.json")
    assert record["steps"][0]["space"] == "rest"
    unknown = dict(PUSH, outputs={"mesh": "fixed.json", "deltas": "deltas.json"})
    result = executor.run_recipe(write([unknown]), root / "runs")
    assert read_json(result["steps"]["push"]["artifacts"]["deltas"])["space"] == "unknown"


def test_missing_declared_output_fails_the_step(workspace):
    root, write = workspace
    step = dict(PUSH, outputs={"mesh": "fixed.json", "extra": "never.json"})
    result = executor.run_recipe(write([step]), root / "runs")
    assert result["status"] == "failed" and ("never.json" in result["error"] or "extra" in result["error"])


def test_unknown_operation_is_rejected_before_anything_runs(workspace):
    root, write = workspace
    with pytest.raises(ToolError, match="知らない操作"):
        executor.run_recipe(write([dict(PUSH, operation="mesh.nope")]), root / "runs")
    assert not (root / "runs").exists() or not list((root / "runs").glob("*/run_record.json"))


def test_orphan_running_record_is_closed_on_next_lock(workspace):
    root, write = workspace
    path = write([PUSH])
    first = executor.run_recipe(path, root / "runs")
    record_path = root / "runs" / first["run_id"] / "run_record.json"
    record = read_json(record_path)
    record["status"] = "running"
    record["steps"][0]["status"] = "running"
    atomic_json(record_path, record)
    executor.run_recipe(path, root / "runs")
    closed = read_json(record_path)
    assert closed["status"] == "failed" and "interrupted" in closed["error"] and closed["steps"][0]["status"] == "failed"


def test_accept_and_edit_are_recorded_separately_from_technical_status(workspace):
    root, write = workspace
    result = executor.run_recipe(write([PUSH]), root / "runs")
    run_dir = root / "runs" / result["run_id"]
    executor.accept(run_dir, "accepted", "tester", "合成素材なので仮")
    executor.note_edit(run_dir, "tester", "帯の 3 頂点を手で外へ出した", 12.5)
    record = contracts.load("run", run_dir / "run_record.json")
    assert record["status"] == "succeeded" and record["adopted"]["decision"] == "accepted"
    assert record["human_edits"][0]["minutes"] == 12.5
    with pytest.raises(ToolError):
        executor.accept(run_dir, "maybe", "tester")


def test_interrupted_step_is_recorded_as_failed(workspace, monkeypatch):
    from model3d import ops
    ops.load_all()

    def interrupt(inputs, parameters, outputs, context):
        raise KeyboardInterrupt

    monkeypatch.setitem(ops.OPERATIONS, "test.interrupt", ops.Operation("test.interrupt", interrupt, (), ""))
    root, write = workspace
    path = write([dict(PUSH, id="stop", operation="test.interrupt", outputs={"mesh": "never.json"}), dict(PUSH, inputs={"mesh": "$steps.stop.mesh", "collider": "$inputs.box"})])
    with pytest.raises(KeyboardInterrupt):
        executor.run_recipe(path, root / "runs")
    record_path = next((root / "runs").glob("*/run_record.json"))
    record = contracts.load("run", record_path)
    assert record["status"] == "failed" and [s["status"] for s in record["steps"]] == ["failed", "pending"]
    assert "interrupted" in record["steps"][0]["error"] and record["steps"][0]["finished_at"]


def test_execute_interface_rejects_wrong_return(monkeypatch, tmp_path):
    from model3d import ops
    ops.load_all()
    monkeypatch.setitem(ops.OPERATIONS, "test.bad", ops.Operation("test.bad", lambda i, p, o, c: None, (), ""))
    context = executor.Context(tmp_path, tmp_path, tmp_path)
    with pytest.raises(ToolError, match="StepResult"):
        executor.execute("test.bad", {}, {}, context, {})
