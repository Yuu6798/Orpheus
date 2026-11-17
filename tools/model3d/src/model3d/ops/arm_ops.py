"""腕の手直しと検査の工程（docs/model3d.md）。リポジトリ外の 3 作目の VRM のダンスで、腕を直した台本を工程にしたもの。どれも Blender の
中で動かす。Blender は外部の実行ファイルなので再利用しない（毎回実行）。

- motion.bake_roll: 手の回転の一部を写す骨（model.twist_bones の roll）の回転を、拘束に頼らず正確な値でキーに焼く（拘束は止める）。
- check.arm_pinch: 腕の関節のまわりのメッシュが、ねじれで絞ったように細くなっていないかをコマごとに測る（太さの比）。
- check.arm_overlap: 左右の腕のメッシュが互いの中に入り込んでいるコマを、変形した後の形で調べる。
- motion.separate_hands: 左右の腕・手・指が互いを通り抜けているコマで、上腕を肩のまわりに少し回して離す。
"""
import math
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, number, register, require

BAKE_ROLL_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "bake_roll.py"
ARM_PINCH_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "arm_pinch.py"
COMMON = {"blender", "addons", "blender_extensions", "timeout", "armature"}


def _name(value, where):
    if not isinstance(value, str) or not value:
        raise ToolError(f"{where} は名前（空でない文字列）にしてください: {value!r}")
    return value


def _real(value, where, minimum=None, exclusive=False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{where} は有限の数にしてください: {value!r}")
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        raise ToolError(f"{where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    if maximum is not None and value > maximum:
        raise ToolError(f"{where} は {maximum} 以下にしてください: {value!r}")
    return value


def _frames(parameters):
    span = parameters.get("frames")
    if span is None:
        return None
    if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in span)
            or span[0] > span[1]):
        raise ToolError(f"frames は [最初のコマ, 最後のコマ]（整数、最初 ≤ 最後）にしてください: {span!r}")
    return span


def _refuse_unknown(values, allowed, where):
    unknown = set(values) - set(allowed)
    if unknown:
        raise ToolError(f"{where} の知らない設定: {sorted(unknown)}（使えるのは {sorted(allowed)}）")


def _names(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(n, str) and n for n in value) or len(set(value)) != len(value):
        raise ToolError(f"{where} は名前（空でない・重ならない文字列）の並びにしてください: {value!r}")
    return value


def _armature(parameters):
    armature = parameters.get("armature")
    if armature is not None and (not isinstance(armature, str) or not armature):
        raise ToolError(f"armature は名前（空でない文字列）にしてください: {armature!r}")
    return armature


def _outputs(outputs, names, operation):
    missing = [name for name in names if name not in outputs]
    if missing:
        raise ToolError(f"{operation} には outputs の {', '.join(missing)} が要ります")


def _run(script, inputs, parameters, outputs, context, settings, operation, extra=()):
    """台本を settings で動かす（settings は工程のフォルダの JSON に書いて渡す）。戻り: 起動の記録。"""
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    blender = _blender(parameters, context, operation)
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path = step_dir / f"{operation.split('.')[-1]}_settings.json"
    atomic_json(settings_path, settings)
    return launcher.run_blender(blender, script, None, ["--blend", inputs["blend"], "--settings", str(settings_path), *extra],
                                log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)


@register("motion.bake_roll", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="手の回転の一部を写す骨（model.twist_bones の roll。Copy Rotation の Y だけ・Add・ローカルどうし）の回転を、拘束に頼らず"
                  "正確な値でコマごとのキーに焼く（Blender の中で）: 写す骨の回転の、軸 Y のまわりのねじれ（VRM の roll と同じ決め方）に"
                  "影響を掛けて打ち、拘束は止める。bones を省くと、止めていない roll の形の Copy Rotation がある骨を全部。止める前の拘束との"
                  "差を報告に出す。踊りの .blend だけに使う（止めた拘束は VRM に書き出されない）。元のファイルは変えない")
def bake_roll(inputs, parameters, outputs, context):
    require(inputs, ("blend",), "motion.bake_roll")
    _refuse_unknown(parameters, COMMON | {"bones"}, "motion.bake_roll")
    _outputs(outputs, ("blend", "report"), "motion.bake_roll")
    settings = {"bones": _names(parameters["bones"], "bones") if parameters.get("bones") is not None else [],
                "armature": _armature(parameters)}
    record = _run(BAKE_ROLL_SCRIPT, inputs, parameters, outputs, context, settings, "motion.bake_roll",
                  ["--out-blend", outputs["blend"], "--report", outputs["report"]])
    report = read_json(outputs["report"])
    observation = {"kind": "bake_roll", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "bones": {name: {key: value.get(key) for key in ("from", "largest_twist_deg", "constraint_was_off_by_deg")}
                             for name, value in (report.get("bones") or {}).items()}}
    return StepResult(observations=[observation])


RING_KEYS = ("name", "at", "axis", "offset", "band", "radius", "groups")


def pinch_settings(parameters):
    """check.arm_pinch のパラメーターを確かめ、台本の settings にする。"""
    _refuse_unknown(parameters, COMMON | {"body", "rings", "frames", "min_weight", "thin", "min_ratio", "strict", "report_only"},
                    "check.arm_pinch")
    rings = parameters.get("rings")
    if not isinstance(rings, list) or not rings:
        raise ToolError("rings は [{\"name\", \"at\", \"axis\", \"offset\", \"band\", \"radius\", \"groups\"}, …] にしてください: "
                        f"{rings!r}")
    checked = []
    for k, ring in enumerate(rings):
        if not isinstance(ring, dict):
            raise ToolError(f"rings[{k}] は {{\"name\", \"at\", \"axis\", …}} にしてください: {ring!r}")
        _refuse_unknown(ring, RING_KEYS, f"rings[{k}]")
        checked.append({"name": _name(ring.get("name"), f"rings[{k}].name"), "at": _name(ring.get("at"), f"rings[{k}].at"),
                        "axis": _name(ring.get("axis"), f"rings[{k}].axis"),
                        "offset": _real(ring.get("offset", 0.0), f"rings[{k}].offset"),
                        "band": _real(ring.get("band"), f"rings[{k}].band", 0.0, exclusive=True),
                        "radius": _real(ring.get("radius"), f"rings[{k}].radius", 0.0, exclusive=True),
                        "groups": _names(ring.get("groups"), f"rings[{k}].groups")})
    names = [ring["name"] for ring in checked]
    if len(set(names)) != len(names):
        raise ToolError(f"rings の name が重なっています: {names}")
    return {"body": _name(parameters.get("body"), "body"), "rings": checked, "frames": _frames(parameters),
            "armature": _armature(parameters),
            "min_weight": _real(parameters.get("min_weight", 0.9), "min_weight", 0.0, maximum=1.0)}


def pinch_summary(raw, thin=0.75, min_ratio=None):
    """check.arm_pinch の測った値（輪ごとのコマの太さの比）の要約と合否（min_ratio を渡したときだけ）。数の丸めは台本と同じ。"""
    import numpy as np
    frames = raw["frames"]
    rings, failures, unmeasured = {}, [], []
    for name, ring in raw["rings"].items():
        values = np.asarray(ring["ratio"], dtype=np.float64)
        worst = int(np.argmin(values))
        rings[name] = {"verts": ring["verts"], "rest_spread_mm": ring.get("rest_spread_mm"), "min": round(float(values.min()), 3),
                       "min_at_frame": int(frames[worst]), "p05": round(float(np.percentile(values, 5)), 3),
                       "median": round(float(np.median(values)), 3), "frames_thin": int((values < thin).sum()),
                       "frames_below_half": int((values < 0.5).sum())}
        if ring.get("rest_spread_mm") is None:
            unmeasured.append(name)
        if min_ratio is not None and values.min() < min_ratio:
            failures.append(f"{name}: いちばん細いコマ {frames[worst]} で初期姿勢の {values.min():.3f} 倍（しきい値 {min_ratio:g}。"
                            f"下回ったコマ {int((values < min_ratio).sum())}）")
    report = {"check": "arm_pinch", "frames": [frames[0], frames[-1]] if frames else [], "frame_count": len(frames), "thin_below": thin,
              "rings": rings, "unmeasured": unmeasured, "thresholds": {} if min_ratio is None else {"min_ratio": min_ratio}}
    if min_ratio is None:
        report.update({"pass": None, "reason": "しきい値が無いので測っただけ（report_only で測るだけにできる）", "failures": []})
    else:
        report.update({"pass": not failures, "failures": failures})
    return report


@register("check.arm_pinch", inputs=("blend",), outputs=("report",), cacheable=False,
          summary="腕の関節のまわり（肩の付け根・肘・手首など）のメッシュが、ねじれで絞ったように細くなっていないかをコマごとに測る（Blender の"
                  "中で。.blend は変えない）: rings の輪ごとに、初期姿勢で骨に沿った帯の頂点を選び、コマごとの太さ（骨の軸に垂直な、重心からの"
                  "距離の平均）を初期姿勢で割った比。輪ごとの最小・5% 点・中央値・thin（既定 0.75）より細いコマの数・半分より細いコマの数。"
                  "体の Armature 以外のモディファイアは止めて骨の変形だけを見る。min_ratio を渡したときだけ合否を出す。好みは判定しない")
def check_arm_pinch(inputs, parameters, outputs, context):
    from .check_ops import _finish
    require(inputs, ("blend",), "check.arm_pinch")
    settings = pinch_settings(parameters)
    thin = _real(parameters.get("thin", 0.75), "thin", 0.0, exclusive=True, maximum=1.0)
    limit = None if parameters.get("min_ratio") is None else _real(parameters["min_ratio"], "min_ratio", 0.0, exclusive=True, maximum=1.0)
    step_dir = Path(context.step_dir)
    raw_path = step_dir / "arm_pinch_raw.json"
    record = _run(ARM_PINCH_SCRIPT, inputs, parameters, outputs, context, settings, "check.arm_pinch", ["--report", str(raw_path)])
    raw = read_json(raw_path)
    report = pinch_summary(raw, thin, limit)
    report.update({"blend": str(inputs["blend"]), "blender_version": record["blender_version"], "armature": raw.get("armature"),
                   "body": raw.get("body"), "modifiers_off": raw.get("modifiers_off"), "raw": str(raw_path)})
    return _finish(report, outputs, parameters, "腕の関節の細りの検査")


ARM_OVERLAP_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "arm_overlap.py"
HARD_KINDS = ("skin_in_skin", "sleeve_in_skin", "skin_in_sleeve")      # 肌がからむ入り込み（袖どうしは、やわらかい物どうしなので別に数える）


def overlap_settings(parameters):
    """check.arm_overlap のパラメーターを確かめ、台本の settings にする。"""
    _refuse_unknown(parameters, COMMON | {"body", "arms", "aliases", "all_groups", "frames", "sleeve_until", "depth", "near", "vote",
                                          "min_verts", "min_depth_mm", "max_frames", "strict", "report_only"}, "check.arm_overlap")
    arms = parameters.get("arms")
    if not isinstance(arms, list) or len(arms) != 2:
        raise ToolError(f"arms は 2 本の腕 [{{\"name\", \"bones\", \"forearm\", \"wrist\"}}, …] にしてください: {arms!r}")
    checked = []
    for k, arm in enumerate(arms):
        if not isinstance(arm, dict):
            raise ToolError(f"arms[{k}] は {{\"name\", \"bones\", \"forearm\", \"wrist\"}} にしてください: {arm!r}")
        _refuse_unknown(arm, ("name", "bones", "forearm", "wrist"), f"arms[{k}]")
        entry = {"name": _name(arm.get("name"), f"arms[{k}].name"), "bones": _names(arm.get("bones"), f"arms[{k}].bones"),
                 "forearm": _name(arm.get("forearm"), f"arms[{k}].forearm"), "wrist": _name(arm.get("wrist"), f"arms[{k}].wrist")}
        if entry["forearm"] not in entry["bones"]:
            raise ToolError(f"arms[{k}].forearm（{entry['forearm']}）を bones に入れてください（袖と肌を分けるのは前腕がいちばん重い頂点）")
        checked.append(entry)
    if checked[0]["name"] == checked[1]["name"] or set(checked[0]["bones"]) & set(checked[1]["bones"]):
        raise ToolError("arms の 2 本は、別の name と、重ならない bones にしてください")
    aliases = parameters.get("aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v and k != v for k, v in aliases.items()):
        raise ToolError(f"aliases は {{頂点グループの名前: 仲間にする骨の名前}}（別の名前どうし）にしてください: {aliases!r}")
    all_groups = parameters.get("all_groups", False)
    if not isinstance(all_groups, bool):
        raise ToolError(f"all_groups は true か false にしてください: {all_groups!r}")
    return {"body": _name(parameters.get("body"), "body"), "arms": checked, "aliases": aliases, "all_groups": all_groups,
            "frames": _frames(parameters), "armature": _armature(parameters),
            "sleeve_until": _real(parameters.get("sleeve_until"), "sleeve_until（袖の終わる位置の割合。衣装ごとに渡す）", 0.0,
                                  exclusive=True, maximum=1.0),
            "depth": _real(parameters.get("depth", 0.002), "depth", 0.0),
            "near": _real(parameters.get("near", 0.04), "near", 0.0, exclusive=True),
            "vote": _real(parameters.get("vote", 0.7), "vote", 0.0, exclusive=True, maximum=1.0)}


def overlap_summary(raw, min_verts=3, min_depth_mm=3.0, max_frames=None):
    """check.arm_overlap の測った値（コマごとの 4 つの入り込みの数と深さ）の要約と合否（max_frames を渡したときだけ）。
    重なりのあるコマ: 肌がからむ 3 つのどれかで、min_verts 頂点以上・深さ min_depth_mm 以上（深さは 0.1 mm に丸めた値。台本と同じ）。"""
    rows = raw["rows"]

    def bad(row, keys):
        return any(row[key][0] >= min_verts and row[key][1] >= min_depth_mm for key in keys)

    hard = [row for row in rows if bad(row, HARD_KINDS)]
    runs = []
    for row in hard:
        if runs and row["frame"] - runs[-1][1] <= 1:
            runs[-1][1] = row["frame"]
        else:
            runs.append([row["frame"], row["frame"]])
    worst = sorted(([row["frame"]] + [row[key] for key in HARD_KINDS] for row in hard), key=lambda item: -max(v[1] for v in item[1:]))[:12]
    report = {"check": "arm_overlap", "frames": [rows[0]["frame"], rows[-1]["frame"]] if rows else [], "frames_checked": len(rows),
              "arms": raw.get("arms"), "frames_overlapping": len(hard), "frames_skin_in_skin": sum(1 for row in rows if bad(row, ("skin_in_skin",))),
              "frames_sleeve_only": sum(1 for row in rows if bad(row, ("sleeve_in_sleeve",)) and not bad(row, HARD_KINDS)),
              "runs": runs, "worst": worst, "limits": {"min_verts": min_verts, "min_depth_mm": min_depth_mm},
              "thresholds": {} if max_frames is None else {"max_frames": max_frames}}
    if max_frames is None:
        report.update({"pass": None, "reason": "しきい値が無いので測っただけ（report_only で測るだけにできる）", "failures": []})
    else:
        failures = [] if len(hard) <= max_frames else [f"腕どうしが入り込んでいるコマが {len(hard)}（しきい値 {max_frames}。最初の区間 {runs[0]}）"]
        report.update({"pass": not failures, "failures": failures})
    return report


@register("check.arm_overlap", inputs=("blend",), outputs=("report",), cacheable=False,
          summary="左右の腕（前腕・袖口・手・指）のメッシュが互いの中に入り込んでいるコマを、変形した後の形（体のモディファイアも効かせる）で"
                  "調べる（Blender の中で。.blend は変えない）: 体の頂点をいちばん重い骨（変形に使う骨の頂点グループで比べる。all_groups で"
                  "全部のグループ）で 2 本の腕に分け（aliases でねじれの骨を元の骨の仲間に）、前腕の頂点は肘から sleeve_until までを袖にする。いちばん近い面の裏側にあり、14 の向きの線が面を奇数回横切る向きが"
                  "vote 以上の頂点を入り込みとし、肌が肌に・袖が肌に・肌が袖に・袖が袖にの 4 つに分けて数える（初期姿勢で内側の頂点は除く）。"
                  "重なりのあるコマの数と区間。max_frames を渡したときだけ合否を出す。好みは判定しない")
def check_arm_overlap(inputs, parameters, outputs, context):
    from .check_ops import _finish
    require(inputs, ("blend",), "check.arm_overlap")
    settings = overlap_settings(parameters)
    min_verts = number(parameters, "min_verts", 3, minimum=1, integer=True)
    min_depth = _real(parameters.get("min_depth_mm", 3.0), "min_depth_mm", 0.0)
    limit = None if parameters.get("max_frames") is None else number(parameters, "max_frames", minimum=0, integer=True)
    raw_path = Path(context.step_dir) / "arm_overlap_raw.json"
    record = _run(ARM_OVERLAP_SCRIPT, inputs, parameters, outputs, context, settings, "check.arm_overlap", ["--report", str(raw_path)])
    raw = read_json(raw_path)
    report = overlap_summary(raw, min_verts, min_depth, limit)
    report.update({"blend": str(inputs["blend"]), "blender_version": record["blender_version"], "armature": raw.get("armature"),
                   "body": raw.get("body"), "raw": str(raw_path)})
    return _finish(report, outputs, parameters, "腕どうしの入り込みの検査")


SEPARATE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "separate_hands.py"
JOIN_SCHEMA = "model3d-motion-join/0.1"
# 数の設定: 名前 → (最小, 最小を含まないか, 最大, 整数か)。体の太さ（SEPARATE_SIZES・DETOUR_SIZES）は体ごとに違うので既定を置かない。
# ほかの数の既定は 3 作目で使った値（blender_adapter/separate_hands.py の DEFAULTS）
SEPARATE_NUMBERS = {
    "passes": (1, False, None, True), "forearm_radius": (0.0, True, None, False), "forearm_bins": (1, False, None, True),
    "soft_from": (0.0, True, None, False), "soft_factor": (0.0, False, None, False), "soft_percentile": (0.0, False, 100.0, False),
    "soft_radius": (0.0, False, None, False), "palm_radius": (0.0, True, None, False), "finger_radius": (0.0, True, None, False),
    "gap": (0.0, False, None, False), "dilate": (0, False, None, True), "sigma": (0.0, True, None, False), "merge": (0, False, None, True),
    "relax": (0.0, True, None, False), "up_share": (0.0, False, 1.0, False), "threshold": (0.0, False, None, False),
    "detour": (0, False, None, True), "detour_max": (0.0, True, None, False), "detour_body_loss": (0.0, False, None, False),
    "head_radius": (0.0, True, None, False), "torso_radius": (0.0, True, None, False),
}
SEPARATE_SIZES = ("forearm_radius", "soft_from", "palm_radius", "finger_radius")   # 必須（前腕の細い所・袖とみなす太さ・手のひら・指）
DETOUR_SIZES = ("head_radius", "torso_radius")                                    # detour のときだけ必須（手を頭と胴から離す）


def _template(bones, key, fields):
    value = bones.get(key)
    if not isinstance(value, str) or not value or any("{" + field + "}" not in value for field in fields):
        raise ToolError(f"bones.{key} は骨の名前の型（{'・'.join('{' + f + '}' for f in fields)} を入れた文字列）にしてください: {value!r}")
    return value


def bone_templates(bones):
    """motion.separate_hands の bones（骨の名前の型）を確かめ、既定を埋める。"""
    from ..blender_adapter.separate_hands import FINGERS, PARTS
    if not isinstance(bones, dict):
        raise ToolError("bones は {\"sides\", \"upper\", \"forearm\", \"hand\", \"finger\", …}（骨の名前の型）にしてください: "
                        f"{bones!r}")
    _refuse_unknown(bones, ("sides", "upper", "forearm", "hand", "finger", "fingers", "parts", "torso", "head"), "bones")
    sides = bones.get("sides")
    if not isinstance(sides, list) or len(sides) != 2 or not all(isinstance(v, str) and v for v in sides) or sides[0] == sides[1]:
        raise ToolError(f"bones.sides は 2 つの別の側の名前（{{side}} に入る。例 [\"L\", \"R\"]）にしてください: {sides!r}")
    out = {"sides": sides, **{key: _template(bones, key, ("side",)) for key in ("upper", "forearm", "hand")},
           "finger": _template(bones, "finger", ("side", "finger", "part")),
           "fingers": _names(bones.get("fingers", list(FINGERS)), "bones.fingers"), "parts": _names(bones.get("parts", list(PARTS)), "bones.parts"),
           "torso": _names(bones["torso"], "bones.torso") if bones.get("torso") else [], "head": None}
    if len(out["fingers"]) < 2:
        raise ToolError(f"bones.fingers は 2 本以上にしてください（手のひらの棒は 2 本目の指から）: {out['fingers']!r}")
    if bones.get("head") is not None:
        out["head"] = _name(bones["head"], "bones.head")
    return out


def _separate_cuts(parameters, inputs):
    """切り替えのコマ: パラメーターの cuts か、入力 joined（motion.join_clips の report）の cuts。両方は使えない。"""
    cuts = parameters.get("cuts")
    if "joined" in inputs:
        if cuts is not None:
            raise ToolError("motion.separate_hands: 切り替えのコマは、パラメーターの cuts か入力の joined のどちらかで渡してください")
        report = read_json(inputs["joined"])
        if not isinstance(report, dict) or report.get("schema_version") != JOIN_SCHEMA:
            raise ToolError(f"motion.separate_hands の joined は motion.join_clips の report（{JOIN_SCHEMA}）にしてください")
        cuts, where = report.get("cuts"), "joined の cuts"
    else:
        cuts, where = ([] if cuts is None else cuts), "cuts"
    if (not isinstance(cuts, list) or not all(isinstance(cut, int) and not isinstance(cut, bool) and cut >= 1 for cut in cuts)
            or len(set(cuts)) != len(cuts)):
        raise ToolError(f"{where} は切り替えのコマ（Blender のコマ番号。1 以上の整数で、重ならない）の並びにしてください: {cuts!r}")
    return sorted(cuts)


def separate_settings(parameters, inputs):
    """motion.separate_hands のパラメーターを確かめ、台本の settings にする（体の太さは必須。省いたほかの数は台本の既定）。"""
    _refuse_unknown(parameters, COMMON | {"body", "bones", "aliases", "cuts"} | set(SEPARATE_NUMBERS), "motion.separate_hands")
    if parameters.get("bones") is None:
        raise ToolError("motion.separate_hands には bones（骨の名前の型）が要ります")
    aliases = parameters.get("aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v and k != v for k, v in aliases.items()):
        raise ToolError(f"aliases は {{頂点グループの名前: 仲間にする骨の名前}}（別の名前どうし）にしてください: {aliases!r}")
    settings = {"body": _name(parameters.get("body"), "body"), "bones": bone_templates(parameters["bones"]), "aliases": aliases,
                "cuts": _separate_cuts(parameters, inputs), "armature": _armature(parameters)}
    for key, (minimum, exclusive, maximum, integer) in SEPARATE_NUMBERS.items():
        if key in parameters:
            value = parameters[key]
            if integer and (isinstance(value, bool) or not isinstance(value, int)):
                raise ToolError(f"{key} は整数にしてください: {value!r}")
            settings[key] = _real(value, key, minimum, exclusive, maximum)
    missing = [key for key in SEPARATE_SIZES + (DETOUR_SIZES if settings.get("detour", 0) > 0 else ()) if key not in settings]
    if missing:
        raise ToolError(f"motion.separate_hands には体の太さ {', '.join(missing)}（m）が要ります（体ごとに違うので既定は無い。"
                        "腕の太さは check.body_size で測れる。head_radius・torso_radius は detour のときだけ）")
    return settings


@register("motion.separate_hands", inputs=("blend", "joined"), outputs=("blend", "report"), cacheable=False,
          summary="左右の腕・手・指が互いを通り抜けているコマを測り、上腕を肩のまわりに少し回して離す（Blender の中で）: 腕を太さのある棒の"
                  "集まり（前腕は体のメッシュから測った区切りごとの太さ、手のひら・指）とみなし、左右の棒の重なりを、前腕の両方に垂直な向きへ、"
                  "区間ごとに側を決めて離す（上へ動くほうの腕に up_share。量は前後に広げてならす。切り替えのコマ cuts か joined はまたがない）。"
                  "passes 回くり返し、残った短い重なりは detour 回まで、すれ違う向きと量を計算で探す。骨の名前は bones の型。上腕の回転のキー"
                  "だけを打ち直す（肘から先は変えない）。元のファイルは変えない")
def separate_hands(inputs, parameters, outputs, context):
    require(inputs, ("blend",), "motion.separate_hands")
    _outputs(outputs, ("blend", "report"), "motion.separate_hands")
    settings = separate_settings(parameters, inputs)
    record = _run(SEPARATE_SCRIPT, inputs, parameters, outputs, context, settings, "motion.separate_hands",
                  ["--out-blend", outputs["blend"], "--report", outputs["report"]])
    report = read_json(outputs["report"])
    observation = {"kind": "separate_hands", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "armature": report.get("armature"),
                   "before": (report.get("before") or {}).get("frames_over"), "after": (report.get("after") or {}).get("frames_over"),
                   "wrist_moved": report.get("wrist_moved"), "detours": len(report.get("detours") or [])}
    # 残った重なりのうち、袖どうし（やわらかい物どうし。押し出しで直す）でないものは要確認
    unresolved = [f"腕どうしの重なりが残っています: コマ {run['frames']}（{run['pair']}、{run['max_overlap_mm']} mm）"
                  for run in (report.get("after") or {}).get("runs", []) if str(run.get("pair")).count("(袖)") < 2]
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))
