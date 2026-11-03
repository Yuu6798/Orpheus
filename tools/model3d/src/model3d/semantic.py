"""意味部位（役割）の語彙と、preset から作る SemanticMap。

共通処理は `garment.sash.front` や `leg.upper.L` のような役割名だけを参照する。部品番号（`part_3` など）や特定サービスの
骨名（`mixamorig:LeftUpLeg` など）は preset で役割に対応付ける。対応の根拠（preset / user / inferred）と確認状態
（confirmed / unconfirmed / rejected）を持ち、確実でない対応は利用者が直す。

部位分けの無いメッシュ（生成器の出力など）は、面領域（regions）で役割を面に結び付ける。面番号はその部品のそのメッシュ版で
だけ意味を持つので、面領域は部品の名前（part。メッシュの name）と頂点構成のハッシュ（topology_sha256）の両方に結び付ける。
名前の違うメッシュ（頂点構成が同じ左右の部品など）には当てず、構成の違うメッシュには古い対応として当てない。
構成を変えた工程の後は、その工程が書いた正確な面の対応で付け替える。近い面を探して付け替えることはしない。
"""
import re

from .io import ToolError, now, sha256
from .mesh import face_vertices

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


def unconfirmed_roles(semantic_map):
    """未確認の対応を含む役割。同じ役割に確認済みの対応があっても、残る未確認を隠さない。"""
    return {entry["role"] for kind in ("parts", "bones", "regions")
            for entry in semantic_map[kind].values() if entry["state"] == "unconfirmed"}


# ---- 面領域（役割 → あるメッシュ版の面）--------------------------------------------------------------

REGION_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")   # OBJ の g 名にもそのまま使える文字だけ


def face_list(mesh, faces):
    """面番号の列を、重複を除いた昇順の整数に直す。範囲外・整数でない値・空は止める。"""
    chosen = set()
    for value in faces:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolError(f"面番号は整数にしてください: {value!r}")
        if not 0 <= value < mesh.face_count:
            raise ToolError(f"面番号 {value} がメッシュの面（{mesh.face_count} 面）の範囲外です")
        chosen.add(value)
    if not chosen:
        raise ToolError("面領域の面が 0 です")
    return sorted(chosen)


def add_region(semantic_map, key, role, mesh, faces, by, basis="user", note=None, replace=False, selection=None):
    """面領域を足す。面番号はこのメッシュの名前（part）と頂点構成（topology_sha256）に結び付け、状態は unconfirmed（確認は confirm）。

    名前の無いメッシュは部品を区別できないので受け付けない。selection は面の選び方の記録（履歴に残す）。
    """
    if not isinstance(key, str) or not REGION_KEY.match(key):
        raise ToolError(f"面領域の key は英数字と _ . - だけにしてください: {key!r}")
    if not is_part_role(role):
        raise ToolError(f"知らない部位の役割です: {role!r}（語彙は model3d/semantic.py の PART_ROLES。拡張は x. で始める）")
    if basis not in ("user", "inferred"):
        raise ToolError(f"basis は user か inferred です: {basis!r}")
    if key in semantic_map["regions"] and not replace:
        raise ToolError(f"面領域 {key} は既にあります（置き換えるときは replace）")
    if not mesh.name:
        raise ToolError("面領域を作るメッシュに名前（name）がありません。部品は名前で区別するので、JSON なら name を付けてください"
                        "（OBJ はファイル名か、model3d が書いた見出しの名前になる）")
    chosen = face_list(mesh, faces)
    entry = {"role": role, "part": mesh.name, "faces": chosen, "topology_sha256": mesh.topology_hash(),
             "basis": basis, "state": "unconfirmed"}
    if note:
        entry["note"] = note
    semantic_map["regions"][key] = entry
    missing = semantic_map.get("required_missing", {})
    if role in missing.get("parts", []):
        missing["parts"] = [r for r in missing["parts"] if r != role]
    semantic_map.setdefault("history", []).append(
        {"at": now(), "by": by, "kind": "regions", "key": key, "action": "add", "role": role, "faces": len(chosen),
         "topology_sha256": entry["topology_sha256"], "state": "unconfirmed", "note": note,
         **({"selection": selection} if selection else {})})
    return entry


def region_status(semantic_map, mesh):
    """各面領域をこのメッシュ版に当てられるか。bound（同じ部品・同じ頂点構成で面番号が範囲内）・other（別の部品の面領域）・
    stale（同じ部品で頂点構成が違う＝古い対応）・invalid（頂点構成の記録が無いか、面が 0 か範囲外）。"""
    topology = mesh.topology_hash()
    rows, groups = {}, {"bound": [], "other": [], "stale": [], "invalid": []}
    for key, entry in sorted(semantic_map["regions"].items()):
        recorded = entry.get("topology_sha256")
        if entry["part"] != mesh.name:
            status, reason = "other", f"別の部品 {entry['part']} の面領域です（このメッシュは {mesh.name}）"
        elif not recorded:
            status, reason = "invalid", "頂点構成（topology_sha256）が記録されていません"
        elif recorded != topology:
            status, reason = "stale", f"別の頂点構成 {recorded[:12]}… に結び付いています"
        elif not entry["faces"] or max(entry["faces"]) >= mesh.face_count:
            status, reason = "invalid", "面が 0 か、メッシュに無い面を指しています"
        else:
            status, reason = "bound", None
        rows[key] = {"role": entry["role"], "state": entry["state"], "faces": len(entry["faces"]), "status": status,
                     **({"reason": reason} if reason else {})}
        groups[status].append(key)
    return {"topology_sha256": topology, "faces": mesh.face_count, "regions": rows, **groups}


def select(semantic_map, role, mesh):
    """役割 role に当たる、このメッシュの面と頂点（工程の select が使う）。

    使うのは、このメッシュの名前（part）と頂点構成に結び付いた面領域と、対応表の部品（parts）のうち role に当てた部品 ID と
    同じ名前のメッシュの面集合（mesh.parts。結合などでメッシュと一緒に付け替わる）。rejected の対応は使わない。当たる面が無ければ
    止める。別の部品の面領域は、頂点構成が同じでも当てない。同じ部品で頂点構成の違う面領域しか無いときは古い対応として止め、
    加工対象を推測しない。
    """
    topology = mesh.topology_hash()
    faces, sources, stale, other = set(), [], [], []
    for key, entry in sorted(semantic_map["regions"].items()):
        if entry["role"] != role or entry["state"] == "rejected":
            continue
        if entry["part"] != mesh.name:
            other.append(key)
            continue
        if entry.get("topology_sha256") != topology:
            stale.append(key)
            continue
        if entry["faces"] and max(entry["faces"]) >= mesh.face_count:
            raise ToolError(f"面領域 {key} がメッシュに無い面を指しています")
        faces.update(entry["faces"])
        sources.append({"kind": "region", "key": key, "state": entry["state"], "faces": len(entry["faces"])})
    for part_id, entry in sorted(semantic_map["parts"].items()):
        if entry["role"] != role or entry["state"] == "rejected" or part_id not in mesh.parts:
            continue
        faces.update(mesh.parts[part_id])
        sources.append({"kind": "part", "key": part_id, "state": entry["state"], "faces": len(mesh.parts[part_id])})
    if not sources:
        if stale:
            raise ToolError(f"役割 {role} の面領域（{', '.join(stale)}）は別の頂点構成のメッシュに結び付いています（古い対応）。"
                            "頂点構成を変えた工程の面の対応で付け替える（model3d semantic remap）か、このメッシュで作り直してください")
        if other:
            parts = sorted({semantic_map["regions"][key]["part"] for key in other})
            raise ToolError(f"役割 {role} の面領域（{', '.join(other)}）は別の部品（{', '.join(parts)}）のもので、"
                            f"このメッシュ（{mesh.name}）には当てません。工程の入力が正しい部品か確かめてください")
        raise ToolError(f"役割 {role} に当たる面がこのメッシュにありません（このメッシュ版の面領域も、対応表の部品と同じ名前の面集合も無い）")
    if not faces:
        raise ToolError(f"役割 {role} の面が 0 です")
    chosen = sorted(faces)
    return {"role": role, "topology_sha256": topology, "faces": chosen, "vertices": face_vertices(mesh, chosen),
            "sources": sources, "ignored_stale": stale, "ignored_other_part": other}


def remap_regions(semantic_map, mapping, by):
    """頂点構成を変えた工程の正確な面の対応（mesh.weld の mapping の face_old_to_new。消えた面は -1）で、source の頂点構成に
    結び付いた面領域を target へ付け替える。面が消えた領域は unconfirmed に戻す（rejected は rejected のまま）。"""
    source, target = mapping.get("source_topology_sha256"), mapping.get("target_topology_sha256")
    table = mapping.get("face_old_to_new")
    if not isinstance(source, str) or not isinstance(target, str) or not isinstance(table, list):
        raise ToolError("面の対応（source_topology_sha256・target_topology_sha256・face_old_to_new）がありません。"
                        "mesh.weld の mapping を渡してください")
    if any(isinstance(k, bool) or not isinstance(k, int) or k < -1 for k in table):
        raise ToolError("face_old_to_new は整数（消えた面は -1）の列にしてください")
    kept = [k for k in table if k >= 0]
    if len(set(kept)) != len(kept):
        raise ToolError("face_old_to_new が 2 つの面を同じ面へ写しています（正確な 1 対 1 の対応ではありません）")
    report = {"source_topology_sha256": source, "target_topology_sha256": target, "remapped": {}, "untouched": []}
    for key, entry in sorted(semantic_map["regions"].items()):
        if entry.get("topology_sha256") != source:
            report["untouched"].append(key)
            continue
        if any(k >= len(table) for k in entry["faces"]):
            raise ToolError(f"面領域 {key} が面の対応の範囲（{len(table)} 面）の外の面を指しています")
        lost = sum(1 for k in entry["faces"] if table[k] < 0)
        entry["faces"] = sorted(table[k] for k in entry["faces"] if table[k] >= 0)
        entry["topology_sha256"] = target
        if lost and entry["state"] == "confirmed":
            entry["state"] = "unconfirmed"
        report["remapped"][key] = {"faces": len(entry["faces"]), "lost": lost, "state": entry["state"]}
        semantic_map.setdefault("history", []).append(
            {"at": now(), "by": by, "kind": "regions", "key": key, "action": "remap", "from": source, "to": target,
             "faces": len(entry["faces"]), "lost": lost, "state": entry["state"]})
    return report
