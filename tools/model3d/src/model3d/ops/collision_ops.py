"""衝突の補正: 閉じた衝突相手の中にある頂点を、余裕を取って外へ出す。

- collision.push_out: 閉じた衝突相手の中にある頂点を、余裕を取って外へ出す（メッシュのファイル。Blender なし）。
- collision.cuff_pushout: 袖と袖口のフリルを、相手の腕の、骨に沿った閉じた筒の外へ押し出す補正（Shrinkwrap）を .blend に付ける（Blender の中で）。
- check.pushout_moves: 押し出しの補正が、頂点をしきい値より遠くへ動かしていないかを、コマごとに調べる（Blender の中で）。
"""
from pathlib import Path

import numpy as np

from .. import checks, collide
from ..io import ToolError, atomic_json
from ..mesh import Mesh
from . import StepResult, number, register, require, selected


@register("collision.push_out", inputs=("mesh", "collider"), outputs=("mesh", "deltas", "report"), selectable=("mesh",),
          summary="collider の中にある頂点を面の外へ clearance だけ離す（select で役割の面の頂点だけ）。deltas は頂点ごとの移動量（座標系は工程の space。骨で動かす前の形に戻すのは別工程）")
def push_out(inputs, parameters, outputs, context):
    require(inputs, ("mesh", "collider"), "collision.push_out")
    mesh, collider = Mesh.load(inputs["mesh"]), Mesh.load(inputs["collider"])
    checks.require_same_frame(mesh, collider)   # 単位と軸が同じときだけ。clearance も mesh の単位で読む
    closed = collider.edge_report()
    if not closed["closed"]:
        raise ToolError(f"衝突相手が閉じていません（境界辺 {closed['boundary_edges']}・非多様体辺 {closed['nonmanifold_edges']}）")
    clearance = number(parameters, "clearance", minimum=0)
    chosen = selected(context, "mesh", parameters, "collision.push_out")
    selection = chosen["vertices"] if chosen else parameters.get("selection")
    indices = np.arange(mesh.vertex_count) if selection is None else np.asarray(selection, dtype=np.int64)
    if len(indices) and (indices.min() < 0 or indices.max() >= mesh.vertex_count):
        raise ToolError("selection が無い頂点を指しています")
    moved, report = collide.push_out(mesh.vertices[indices], collider, clearance, parameters.get("directions"),
                                     parameters.get("pad_outside", True))
    fixed = mesh.copy()
    fixed.vertices[indices] = moved
    deltas = (moved - mesh.vertices[indices])
    changed = np.flatnonzero(np.linalg.norm(deltas, axis=1) > 0)
    if "mesh" in outputs:
        fixed.save(outputs["mesh"])
    space = getattr(context, "space", None) or parameters.get("space") or "unknown"   # 工程の宣言が正。勝手に pose と名乗らない
    if "deltas" in outputs:
        atomic_json(outputs["deltas"], {"schema_version": "model3d-deltas/0.1", "topology_sha256": mesh.topology_hash(),
                                        "space": space, "units": mesh.units,
                                        "deltas": [[int(indices[i]), [float(x) for x in deltas[i]]] for i in changed]})
    observation = {"kind": "push_out", **report, "changed": int(len(changed)), "space": space, "topology_sha256": mesh.topology_hash(),
                   "collider_topology_sha256": collider.topology_hash()}
    if "report" in outputs:
        atomic_json(outputs["report"], observation)
    unresolved = []
    if report["residual_inside"]:
        unresolved.append(f"補正の後も {report['residual_inside']} 頂点が中に残っています（凹んだ衝突相手では 1 回の補正で出ないことがある）")
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


CUFF_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "cuff_pushout.py"
MOVES_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "pushout_moves.py"
CUFF_NAMES = ("proxy", "group", "near_group", "near_modifier", "modifier")
# 数の設定: 名前 → (最小, 最小を含まないか, 最大, 整数か)。体と衣装の大きさ（CUFF_SIZES）は既定を置かない。
# ほかの数の既定は 3 作目で使った値（blender_adapter/cuff_pushout.py の DEFAULTS）
CUFF_NUMBERS = {"core_radius": (0.0, True, None, False), "sleeve_until": (0.0, True, 1.0, False), "palm_radius": (0.0, True, None, False),
                "offset": (0.0, False, None, False), "segments": (3, False, None, True), "gate_near": (0.0, False, None, False),
                "gate_far": (0.0, True, None, False)}
CUFF_SIZES = ("core_radius", "sleeve_until", "palm_radius", "gate_near", "gate_far")   # 必須（芯と手のひらの太さ・袖の終わる位置・近接の門）


def cuff_settings(parameters):
    """collision.cuff_pushout のパラメーターを確かめ、台本の settings にする（体と衣装の大きさは必須。省いたほかの数と名前は台本の既定）。"""
    from .arm_ops import COMMON, _armature, _name, _real, _refuse_unknown
    _refuse_unknown(parameters, COMMON | {"body", "arms", "aliases"} | set(CUFF_NUMBERS) | set(CUFF_NAMES), "collision.cuff_pushout")
    arms = parameters.get("arms")
    if not isinstance(arms, list) or len(arms) != 2:
        raise ToolError(f"arms は 2 本の腕 [{{\"name\", \"forearm\", \"hand\", \"palm_end\"}}, …] にしてください: {arms!r}")
    checked = []
    for k, arm in enumerate(arms):
        if not isinstance(arm, dict):
            raise ToolError(f"arms[{k}] は {{\"name\", \"forearm\", \"hand\", \"palm_end\"}} にしてください: {arm!r}")
        _refuse_unknown(arm, ("name", "forearm", "hand", "palm_end"), f"arms[{k}]")
        checked.append({key: _name(arm.get(key), f"arms[{k}].{key}") for key in ("name", "forearm", "hand", "palm_end")})
    if checked[0]["name"] == checked[1]["name"]:
        raise ToolError("arms の 2 本は別の name にしてください")
    aliases = parameters.get("aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v and k != v for k, v in aliases.items()):
        raise ToolError(f"aliases は {{頂点グループの名前: 仲間にする骨の名前}}（別の名前どうし）にしてください: {aliases!r}")
    settings = {"body": _name(parameters.get("body"), "body"), "arms": checked, "aliases": aliases, "armature": _armature(parameters)}
    for key, (minimum, exclusive, maximum, integer) in CUFF_NUMBERS.items():
        if key in parameters:
            if integer and (isinstance(parameters[key], bool) or not isinstance(parameters[key], int)):
                raise ToolError(f"{key} は整数にしてください: {parameters[key]!r}")
            settings[key] = _real(parameters[key], key, minimum, exclusive, maximum)
    missing = [key for key in CUFF_SIZES if key not in settings]
    if missing:
        raise ToolError(f"collision.cuff_pushout には {', '.join(missing)} が要ります（体と衣装ごとに違うので既定は無い。"
                        "太さと門は m、sleeve_until は前腕に沿った 0〜1 の割合）")
    near, far = settings["gate_near"], settings["gate_far"]
    if far <= near:
        raise ToolError(f"gate_far（重み 0 になる距離）は gate_near（重み 1 の距離）より大きくしてください: {near} と {far}")
    for key in CUFF_NAMES:
        if key in parameters:
            value = _name(parameters[key], key)
            if "{arm}" not in value:
                raise ToolError(f"{key} は名前の型（{{arm}} を入れた文字列。腕の name が入る）にしてください: {value!r}")
            settings[key] = value
    return settings


@register("collision.cuff_pushout", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="袖と袖口のフリルが相手の腕に入り込まないよう、当たった所で相手の腕の外へ押し出す補正を付ける（Blender の中で）: 腕ごとに、"
                  "肘 → 手首 → 手のひらの先を通る、鋭い角の無い 1 本の閉じた筒（袖の区切りは芯の太さ core_radius、手首側は体から測った太さ、"
                  "両端は丸める。骨で動き、描かない）を作り、体の袖の頂点（前腕がいちばん重く、肘から sleeve_until まで）に、相手の筒を相手にした"
                  "Shrinkwrap（Outside）と、相手の筒から遠い頂点を止める頂点ウェイトの近接（gate_near〜gate_far）を付ける。腕は動かさない。"
                  "筒から離れた点が内側と判定されないかを報告に出す。元のファイルは変えない")
def cuff_pushout(inputs, parameters, outputs, context):
    from .arm_ops import _outputs, _run
    from ..io import read_json
    require(inputs, ("blend",), "collision.cuff_pushout")
    _outputs(outputs, ("blend", "report"), "collision.cuff_pushout")
    settings = cuff_settings(parameters)
    record = _run(CUFF_SCRIPT, inputs, parameters, outputs, context, settings, "collision.cuff_pushout",
                  ["--out-blend", outputs["blend"], "--report", outputs["report"]])
    report = read_json(outputs["report"])
    arms = report.get("arms") or {}
    observation = {"kind": "cuff_pushout", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "armature": report.get("armature"),
                   "arms": {name: {key: value.get(key) for key in ("proxy", "sleeve_verts", "far_points_judged_inside")}
                            for name, value in arms.items()}}
    # 筒から離れた点が内側と判定されるなら、遠くの頂点を吸い寄せうる（Shrinkwrap の Outside の判定の外れ）
    unresolved = [f"{name}: 筒から離れた点 {value['far_points_judged_inside']} が内側と判定されました（遠くの頂点を吸い寄せうる）"
                  for name, value in arms.items() if value.get("far_points_judged_inside")]
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


def moves_summary(raw):
    """check.pushout_moves の測った値の要約と合否（どれかの調べたもので、しきい値を超えて動いた頂点のあるコマがあれば不合格）。"""
    failures = []
    for key, value in raw["checks"].items():
        over = value["frames_over_limit"]
        if over:
            worst = max(over, key=lambda row: row[2])
            failures.append(f"{key}: しきい値 {value['limit_mm']:g} mm を超えて動いた頂点のあるコマが {len(over)}（最初 {over[0][0]}、"
                            f"いちばん大きいのはコマ {worst[0]} の {worst[2]} mm）")
    frames = raw["frames"]
    return {"check": "pushout_moves", "frames": [frames[0], frames[-1]] if frames else [], "frames_checked": len(frames),
            "space": raw.get("space", "local"),
            "checks": {key: {**value, "frames_over_limit": len(value["frames_over_limit"]), "first_over": value["frames_over_limit"][:8]}
                       for key, value in raw["checks"].items()},
            "pass": not failures, "failures": failures}


@register("check.pushout_moves", inputs=("blend",), outputs=("report",), cacheable=False,
          summary="押し出しの補正（Shrinkwrap の Outside など）が、頂点をおかしな所まで動かしていないかを、コマごとに調べる（Blender の中で。"
                  ".blend は変えない）: checks の 1 つごとに、メッシュ mesh の名前が prefix で始まるモディファイアを効かせたときと外したときの"
                  "頂点の位置を比べ、limit_mm を超えて動いた頂点のあるコマを数える。超えたコマがあれば不合格（要確認）。好みは判定しない")
def check_pushout_moves(inputs, parameters, outputs, context):
    from .arm_ops import COMMON, _frames, _name, _real, _refuse_unknown, _run
    from .check_ops import _finish
    from ..io import read_json
    require(inputs, ("blend",), "check.pushout_moves")
    _refuse_unknown(parameters, COMMON | {"checks", "frames", "step", "space", "strict", "report_only"}, "check.pushout_moves")
    items = parameters.get("checks")
    if not isinstance(items, list) or not items:
        raise ToolError(f"checks は [{{\"mesh\", \"prefix\", \"limit_mm\"}}, …] にしてください: {items!r}")
    checks = []
    for k, item in enumerate(items):
        if not isinstance(item, dict):
            raise ToolError(f"checks[{k}] は {{\"mesh\", \"prefix\", \"limit_mm\"}} にしてください: {item!r}")
        _refuse_unknown(item, ("mesh", "prefix", "limit_mm"), f"checks[{k}]")
        checks.append({"mesh": _name(item.get("mesh"), f"checks[{k}].mesh"), "prefix": _name(item.get("prefix"), f"checks[{k}].prefix"),
                       "limit_mm": _real(item.get("limit_mm"), f"checks[{k}].limit_mm", 0.0, exclusive=True)})
    keys = [c["mesh"] + ":" + c["prefix"] for c in checks]
    if len(set(keys)) != len(keys):
        raise ToolError(f"checks の mesh と prefix の組が重なっています: {keys}")
    meshes = [item["mesh"] for item in checks]
    if len(set(meshes)) != len(meshes):
        raise ToolError("checks の mesh は 1 つずつにしてください（同じメッシュのモディファイアを一度に外して比べるので、2 つ挙げると"
                        "互いの動きが混ざる。まとめて調べたいなら、prefix に共通の頭を選ぶ）")
    step = parameters.get("step", 1)
    if isinstance(step, bool) or not isinstance(step, int) or step < 1:
        raise ToolError(f"step は 1 以上の整数にしてください: {step!r}")
    space = parameters.get("space", "local")
    if space not in ("local", "world"):
        raise ToolError(f"space は local（メッシュの中の座標。物体の拡大を含まない）か world（世界の座標）にしてください: {space!r}")
    settings = {"checks": checks, "frames": _frames(parameters), "step": step, "space": space, "armature": parameters.get("armature")}
    raw_path = Path(context.step_dir) / "pushout_moves_raw.json"
    record = _run(MOVES_SCRIPT, inputs, parameters, outputs, context, settings, "check.pushout_moves", ["--report", str(raw_path)])
    raw = read_json(raw_path)
    report = moves_summary(raw)
    report.update({"blend": str(inputs["blend"]), "blender_version": record["blender_version"], "raw": str(raw_path)})
    return _finish(report, outputs, parameters, "押し出しの動きの検査")
