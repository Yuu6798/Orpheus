"""意味部位（役割）の語彙と、preset から作る SemanticMap。

共通処理は `garment.sash.front` や `leg.upper.L` のような役割名だけを参照する。部品番号（`part_3` など）や特定サービスの
骨名（`mixamorig:LeftUpLeg` など）は preset で役割に対応付ける。対応の根拠（preset / user / inferred）と確認状態
（confirmed / unconfirmed / rejected）を持ち、確実でない対応は利用者が直す。
"""
from .io import ToolError, now, sha256

SIDES = ("L", "R", "C")

def _SIDED(base):
    return (base, f"{base}.L", f"{base}.R")


PART_ROLES = frozenset(
    ["body", "body.head", "body.neck", "body.torso", "body.torso.upper", "body.torso.lower", "body.hips",
     *_SIDED("body.arm.upper")[1:], *_SIDED("body.arm.lower")[1:], *_SIDED("body.hand")[1:],
     *_SIDED("body.leg.upper")[1:], *_SIDED("body.leg.lower")[1:], *_SIDED("body.foot")[1:],
     "face", "face.eye.L", "face.eye.R", "face.mouth", "face.brow.L", "face.brow.R",
     "hair", "hair.front", "hair.back", "hair.side.L", "hair.side.R", "hair.tail",
     "garment", "garment.top", "garment.bottom", "garment.skirt", "garment.sleeve.L", "garment.sleeve.R",
     "garment.sash", "garment.sash.front", "garment.sash.back", "garment.sash.knot", "garment.collar", "garment.belt",
     "accessory", "collider", "collider.leg.upper.L", "collider.leg.upper.R", "collider.torso"])

BONE_ROLES = frozenset(
    ["root", "hips", "spine", "spine.1", "spine.2", "chest", "neck", "head", "head.top",
     "shoulder.L", "shoulder.R", "arm.upper.L", "arm.upper.R", "arm.lower.L", "arm.lower.R", "hand.L", "hand.R",
     "leg.upper.L", "leg.upper.R", "leg.lower.L", "leg.lower.R", "foot.L", "foot.R", "toe.L", "toe.R",
     "jaw", "eye.L", "eye.R", "cuff.L", "cuff.R", "sash.1", "sash.2", "sash.3", "sash.4",
     "hair.1", "hair.2", "hair.3", "hair.4"])

HUMANOID_REQUIRED_BONES = ("hips", "spine", "head", "arm.upper.L", "arm.upper.R", "leg.upper.L", "leg.upper.R")


def is_part_role(role):
    return isinstance(role, str) and (role in PART_ROLES or role.startswith("x."))


def is_bone_role(role):
    return isinstance(role, str) and (role in BONE_ROLES or role.startswith("x."))


def side_of(role):
    if role.endswith(".L"):
        return "L"
    if role.endswith(".R"):
        return "R"
    return "C"


def normalize_bone_name(name):
    """名前空間の接頭辞（`mixamorig:Hips` の `mixamorig:`）を外す。"""
    return name.split(":", 1)[1] if ":" in name else name


def check_preset(preset, where=None):
    unknown_parts = sorted(r for r in preset["parts"].values() if not is_part_role(r))
    unknown_bones = sorted(r for r in preset["bones"].values() if not is_bone_role(r))
    unknown_required = sorted(r for r in preset["required_roles"].get("parts", []) if not is_part_role(r)) + \
        sorted(r for r in preset["required_roles"].get("bones", []) if not is_bone_role(r))
    problems = []
    if unknown_parts:
        problems.append(f"知らない部位の役割: {', '.join(unknown_parts)}")
    if unknown_bones:
        problems.append(f"知らない骨の役割: {', '.join(unknown_bones)}")
    if unknown_required:
        problems.append(f"required_roles に知らない役割: {', '.join(unknown_required)}")
    if problems:
        raise ToolError(f"preset{'（' + str(where) + '）' if where else ''}: " + "; ".join(problems) +
                        "。語彙は model3d/semantic.py の PART_ROLES / BONE_ROLES（拡張は x. で始める）")
    return preset


def load_preset(path):
    from . import contracts
    return check_preset(contracts.load("preset", path), path)


def _lookup(table, name):
    if name in table:
        return table[name], "preset"
    plain = normalize_bone_name(name)
    for key, role in table.items():
        if normalize_bone_name(key) == plain:
            return role, "preset"
    return None, None


def build(asset_id, preset, parts=(), bones=(), preset_path=None):
    """preset の対応表で、部品 ID と骨名を役割に対応付けた SemanticMap。対応できないものは unmapped に残す。

    bones は名前の列か、{"name", "parent", "rest_matrix"} の列。state は全て unconfirmed（確認は `confirm`）。
    """
    from . import contracts
    check_preset(preset)
    result = {"schema_version": "model3d-semantic-map/0.1", "asset_id": asset_id, "parts": {}, "bones": {}, "regions": {},
              "unmapped": {"parts": [], "bones": []}, "required_missing": {"parts": [], "bones": []}, "history": []}
    if preset_path:
        result["preset"] = {"path": str(preset_path), "sha256": sha256(preset_path)}
    for part in parts:
        role = preset["parts"].get(part)
        if role is None:
            result["unmapped"]["parts"].append(part)
            continue
        result["parts"][part] = {"role": role, "side": side_of(role), "basis": "preset", "state": "unconfirmed"}
    for bone in bones:
        info = bone if isinstance(bone, dict) else {"name": bone}
        name = info["name"]
        role, basis = _lookup(preset["bones"], name)
        if role is None:
            result["unmapped"]["bones"].append(name)
            continue
        entry = {"role": role, "side": side_of(role), "parent": info.get("parent"), "basis": basis, "state": "unconfirmed"}
        if info.get("rest_matrix") is not None:
            entry["rest_matrix"] = info["rest_matrix"]
        result["bones"][name] = entry
    present_parts = {v["role"] for v in result["parts"].values()}
    present_bones = {v["role"] for v in result["bones"].values()}
    result["required_missing"]["parts"] = sorted(r for r in preset["required_roles"].get("parts", []) if r not in present_parts)
    result["required_missing"]["bones"] = sorted(r for r in preset["required_roles"].get("bones", []) if r not in present_bones)
    return contracts.validate("semantic-map", result)


def confirm(semantic_map, kind, key, state, by, note=None):
    """人の確認。kind は parts / bones / regions、state は confirmed / rejected / unconfirmed。根拠は user になる。"""
    if kind not in ("parts", "bones", "regions"):
        raise ToolError(f"kind は parts / bones / regions です: {kind!r}")
    if state not in ("confirmed", "rejected", "unconfirmed"):
        raise ToolError(f"state は confirmed / rejected / unconfirmed です: {state!r}")
    if key not in semantic_map[kind]:
        raise ToolError(f"{kind} に {key!r} がありません")
    entry = semantic_map[kind][key]
    entry["state"] = state
    entry["basis"] = "user"
    if note:
        entry["note"] = note
    semantic_map.setdefault("history", []).append({"at": now(), "by": by, "kind": kind, "key": key, "state": state, "note": note})
    return semantic_map


def roles_present(semantic_map, confirmed_only=False):
    """対応済みの役割の集合（parts と bones）。rejected は数えない。"""
    def keep(entry):
        return entry["state"] == "confirmed" if confirmed_only else entry["state"] != "rejected"
    return {"parts": {v["role"] for v in semantic_map["parts"].values() if keep(v)} |
                     {v["role"] for v in semantic_map["regions"].values() if keep(v)},
            "bones": {v["role"] for v in semantic_map["bones"].values() if keep(v)}}
