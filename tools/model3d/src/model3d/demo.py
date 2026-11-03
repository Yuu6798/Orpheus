"""合成素材のデモ。実キャラの素材は使わず、プログラムで作った部品で一気通貫（押し出し → 結合 → 貫通の検査 → 補正 → 検査 → 書き出し）を通す。

- 衝突相手: 八角柱（左の大腿の代役。役割 collider.leg.upper.L）。
- 帯: 2 枚の板を継ぎ目で重ねた薄い面（役割 garment.sash.front）。継ぎ目の頂点は二重で、中ほどの 3 列は大腿の中に入れてある。
- 袖: 六角柱をねじって押し出す（役割 garment.sleeve.L。ねじりの確認用）。
- リグ: Mixamo 風の骨名と、帯の頂点のウェイト（上端だけ hips と半分ずつ）。
合成素材の検査が通ることは、実素材で同じ処理が成立することを保証しない（docs/model3d.md）。
"""
import math
from pathlib import Path

from . import contracts, generate, semantic
from .io import atomic_json
from .mesh import Mesh

SASH_ROWS = 7
SASH_Z0, SASH_DZ = 0.05, 0.05
SASH_COLUMNS = (-0.03, -0.015, 0.0)   # 左の板。右の板は符号を反転（継ぎ目 x=0 は二重）
SASH_Y_OUT, SASH_Y_NEAR, SASH_Y_IN = -0.09, -0.075, -0.05
INSIDE_ROWS = (2, 3, 4)
NEAR_ROWS = (1, 5)


def regular_polygon(sides, radius):
    return [[round(radius * math.cos(2 * math.pi * k / sides), 12), round(radius * math.sin(2 * math.pi * k / sides), 12)]
            for k in range(sides)]


def sash_mesh():
    vertices, uv = [], []
    for panel, sign in (("left", 1.0), ("right", -1.0)):
        for row in range(SASH_ROWS):
            z = SASH_Z0 + SASH_DZ * row
            y = SASH_Y_IN if row in INSIDE_ROWS else SASH_Y_NEAR if row in NEAR_ROWS else SASH_Y_OUT
            for column in (SASH_COLUMNS if sign > 0 else tuple(-c for c in reversed(SASH_COLUMNS))):
                vertices.append([column, y, z])
                uv.append([0.5 + column / 0.06, row / (SASH_ROWS - 1)])
    faces = []
    per_panel = SASH_ROWS * len(SASH_COLUMNS)
    for panel in range(2):
        base = panel * per_panel
        for row in range(SASH_ROWS - 1):
            for column in range(len(SASH_COLUMNS) - 1):
                a = base + row * len(SASH_COLUMNS) + column
                b, c, d = a + 1, a + 1 + len(SASH_COLUMNS), a + len(SASH_COLUMNS)
                faces.append([a, b, c, d])
    return Mesh(vertices, faces, uv, name="sash_front", parts={"garment_sash_front": list(range(len(faces)))})


def rig_json(vertex_count):
    bones = [("mixamorig:Hips", None), ("mixamorig:Spine", "mixamorig:Hips"), ("mixamorig:Head", "mixamorig:Spine"),
             ("mixamorig:LeftArm", "mixamorig:Spine"), ("mixamorig:RightArm", "mixamorig:Spine"),
             ("mixamorig:LeftUpLeg", "mixamorig:Hips"), ("mixamorig:LeftLeg", "mixamorig:LeftUpLeg"),
             ("mixamorig:LeftFoot", "mixamorig:LeftLeg"), ("mixamorig:RightUpLeg", "mixamorig:Hips")]
    per_panel = SASH_ROWS * len(SASH_COLUMNS)
    top_row = {panel * per_panel + (SASH_ROWS - 1) * len(SASH_COLUMNS) + c for panel in range(2) for c in range(len(SASH_COLUMNS))}
    leg = [[i, 0.5 if i in top_row else 1.0] for i in range(vertex_count)]
    hips = [[i, 0.5] for i in sorted(top_row)]
    return {"schema_version": "model3d-rig/0.1", "name": "demo_rig",
            "bones": [{"name": n, "parent": p, "head": [0.0, 0.0, 0.0], "tail": [0.0, 0.0, 0.1],
                       "rest_matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]} for n, p in bones],
            "weights": {"vertex_count": vertex_count, "groups": {"mixamorig:LeftUpLeg": leg, "mixamorig:Hips": hips}}}


def preset_json():
    return {"schema_version": "model3d-preset/0.1", "name": "example_humanoid",
            "description": "合成素材のデモ用。実キャラの preset は Git の外に置く。",
            "body": {"height": 1.6, "units": "m"},
            "parts": {"garment_sash_front": "garment.sash.front", "leg_upper_l_collider": "collider.leg.upper.L",
                      "sleeve_l": "garment.sleeve.L"},
            "bones": {"mixamorig:Hips": "hips", "mixamorig:Spine": "spine", "mixamorig:Head": "head",
                      "mixamorig:LeftArm": "arm.upper.L", "mixamorig:RightArm": "arm.upper.R",
                      "mixamorig:LeftUpLeg": "leg.upper.L", "mixamorig:RightUpLeg": "leg.upper.R",
                      "mixamorig:LeftLeg": "leg.lower.L", "mixamorig:LeftFoot": "foot.L"},
            "parameters": {"garment.sash.front": {"clearance": 0.004, "falloff": 0.007, "units": "m"}},
            "required_roles": {"parts": ["garment.sash.front", "collider.leg.upper.L"],
                               "bones": list(semantic.HUMANOID_REQUIRED_BONES)}}


def recipe_json(clearance):
    return {
        "schema_version": "model3d-recipe/0.1", "name": "demo-sash-collision",
        "description": "合成素材: 大腿の代役に入った帯を外へ出し、検査して書き出す",
        "preset": "preset.json", "semantic_map": "semantic_map.json", "scope": "synthetic parts only",
        "inputs": {"profile_leg": {"path": "inputs/profile_leg.json", "kind": "json"},
                   "profile_sleeve": {"path": "inputs/profile_sleeve.json", "kind": "json"},
                   "sash": {"path": "inputs/sash.json", "kind": "mesh"},
                   "rig": {"path": "inputs/rig.json", "kind": "json"}},
        "steps": [
            {"id": "leg", "operation": "mesh.extrude_profile", "inputs": {"profile": "$inputs.profile_leg"},
             "parameters": {"height": 0.4, "name": "leg_upper_l_collider"}, "outputs": {"mesh": "collider.json"},
             "applies_to": ["collider.leg.upper.L"], "space": "rest",
             "evidence": [{"source": "artifact", "ref": "JSCAD extrudeLinear reconstruction 2026-11-03 (synthetic parts)", "state": "observed"}]},
            {"id": "sleeve", "operation": "mesh.extrude_profile", "inputs": {"profile": "$inputs.profile_sleeve"},
             "parameters": {"height": 0.25, "twist_deg": 20, "steps": 5, "name": "sleeve_l"}, "outputs": {"mesh": "sleeve.json"},
             "applies_to": ["garment.sleeve.L"], "space": "rest"},
            {"id": "weld", "operation": "mesh.weld", "inputs": {"mesh": "$inputs.sash"}, "parameters": {"distance": 1e-6},
             "outputs": {"mesh": "sash_welded.json"}, "applies_to": ["garment.sash.front"],
             "notes": "UV の継ぎ目の二重頂点を 1 つにする（2026-11-02 の記録）"},
            {"id": "before", "operation": "check.penetration", "inputs": {"mesh": "$steps.weld.mesh", "collider": "$steps.leg.mesh"},
             "parameters": {"report_only": True, "x_expect": "inside > 0"}, "outputs": {"report": "penetration_before.json"},
             "preconditions": ["closed:collider"], "notes": "補正前の測定。report_only なので合否で止めない"},
            {"id": "push", "operation": "collision.push_out", "inputs": {"mesh": "$steps.weld.mesh", "collider": "$steps.leg.mesh"},
             "parameters": {"clearance": clearance, "pad_outside": True},
             "outputs": {"mesh": "sash_fixed.json", "deltas": "sash_deltas.json", "report": "push_out.json"},
             "preconditions": ["closed:collider", "role:garment.sash.front", "units:m"], "applies_to": ["garment.sash.front"],
             "space": "pose", "evidence": [{"source": "record", "ref": "2026-11-02 session: ray parity inside test, push out, no velocity", "state": "observed"}]},
            {"id": "after", "operation": "check.penetration", "inputs": {"mesh": "$steps.push.mesh", "collider": "$steps.leg.mesh"},
             "parameters": {"strict": True}, "outputs": {"report": "penetration_after.json"}, "preconditions": ["closed:collider"]},
            {"id": "structure", "operation": "check.structure", "inputs": {"rig": "$inputs.rig"},
             "parameters": {"required_bone_roles": "preset", "tolerance": 0.001}, "outputs": {"report": "structure.json"}},
            {"id": "export", "operation": "mesh.export_obj", "inputs": {"mesh": "$steps.push.mesh"}, "outputs": {"obj": "sash_fixed.obj"}},
            {"id": "export_collider", "operation": "mesh.export_obj", "inputs": {"mesh": "$steps.leg.mesh"}, "outputs": {"obj": "collider.obj"}},
        ],
    }


def recipe_generate_json(resolution):
    return {
        "schema_version": "model3d-recipe/0.1", "name": "demo-initial-model",
        "description": "合成の設定画 1 枚から、代役の生成器で初期モデルを作り、検査して書き出す（status は needs_review）",
        "scope": "synthetic sheet and a stand-in backend; not a generator",
        "inputs": {"sheet": {"path": "inputs/sheet_front.png", "kind": "image"}},
        "steps": [
            {"id": "prepare", "operation": "generate.prepare_reference", "inputs": {"image": "$inputs.sheet"},
             "parameters": {"foreground_ratio": 0.85}, "outputs": {"prepared": "sheet_prepared.png", "metadata": "prepare.json"},
             "notes": "前処理だけ。生成器に渡す PNG と条件を人が見られる"},
            {"id": "generate", "operation": "generate.initial_model", "inputs": {"image": "$inputs.sheet"},
             "parameters": {"backend": "generator.json", "foreground_ratio": 0.85, "resolution": resolution, "density_threshold": 25.0},
             "outputs": {"mesh": "initial_model.json", "record": "initial_model_record.json", "prepared": "prepared.png"},
             "notes": "生成が終わっただけでは採用しない（常に needs_review）。本物の生成器は backends/README.md",
             "evidence": [{"source": "record", "ref": "2026-11-03 設定画から初期モデルを生成する機能の検証（単一視点・TripoSR・CPU）", "state": "observed"}]},
            {"id": "inspect", "operation": "generate.inspect_initial_model", "inputs": {"mesh": "$steps.generate.mesh"},
             "outputs": {"report": "inspect.json"}},
            {"id": "export", "operation": "mesh.export_obj", "inputs": {"mesh": "$steps.generate.mesh"}, "outputs": {"obj": "initial_model.obj"}},
        ],
    }


def create(directory, clearance=0.004, resolution=64):
    """合成素材・preset・対応表・リグ・recipe と、設定画からの生成の recipe を directory に書く。"""
    root = Path(directory).resolve()
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    atomic_json(inputs / "profile_leg.json", {"points": regular_polygon(8, 0.07), "units": "m"})
    atomic_json(inputs / "profile_sleeve.json", {"points": regular_polygon(6, 0.03), "units": "m"})
    sash = sash_mesh()
    sash.save(inputs / "sash.json")
    atomic_json(inputs / "rig.json", contracts.validate("rig", rig_json(sash.vertex_count)))
    preset = contracts.validate("preset", preset_json())
    atomic_json(root / "preset.json", preset)
    rig = rig_json(sash.vertex_count)
    semantic_map = semantic.build("demo-asset", preset, parts=list(preset["parts"]),
                                  bones=[{"name": b["name"], "parent": b["parent"]} for b in rig["bones"]],
                                  preset_path=root / "preset.json")
    atomic_json(root / "semantic_map.json", semantic_map)
    recipe = contracts.validate("recipe", recipe_json(clearance))
    atomic_json(root / "recipe.json", recipe)
    generate.save_rgba(generate.synthetic_sheet(), inputs / "sheet_front.png")
    atomic_json(root / "generator.json", generate.standin_config())
    atomic_json(root / "recipe_generate.json", contracts.validate("recipe", recipe_generate_json(resolution)))
    return {"dir": str(root), "recipe": str(root / "recipe.json"), "recipe_generate": str(root / "recipe_generate.json"),
            "preset": str(root / "preset.json"), "semantic_map": str(root / "semantic_map.json"),
            "sash": {"vertices": sash.vertex_count, "faces": sash.face_count},
            "expected": {"inside_before": len(INSIDE_ROWS) * (2 * len(SASH_COLUMNS) - 1), "inside_after": 0,
                         "welded_vertices": sash.vertex_count - SASH_ROWS,
                         "generate_status": "needs_review", "generate_candidates": ["generate"]}}
