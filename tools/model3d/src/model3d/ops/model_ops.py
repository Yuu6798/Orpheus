"""モデルを手直しする工程（docs/model3d.md の「モデルを手直しする」）。画像から作ったモデルの癖（細い体など）を、部位ごとに測ってから
直す。どれも Blender の中で動かす。Blender は外部の実行ファイルなので再利用しない（毎回実行）。

- model.body_shape: 体つきを部位ごとに広げる（腕・脚は骨の軸のまわり、胴は高さごとの幅と厚み、腕輪は形を保ってずらす）。
- check.body_size: 体の太さを測る（腕・脚の直径、胴の幅と厚み）。測るだけ。
- model.twist_bones: ねじれを分ける補助の骨（ねじらない骨）を足し、重みを付け根から先へ振り分ける。
- check.twist: 骨の軸のまわりのねじれの角をコマごとに測る。
- model.recolor_part・model.recolor: 色の絵を、部品の uv の範囲だけ・色相の範囲だけ塗り替える。model.remove_parts: 部品を外す。
- model.smooth_skin・model.clean_skin: 肌の島の色の絵を、頂点の色をならして塗り直す（溝と焼き込まれた明暗・外した部品の下のしみ）。
- model.face_shape: 顔の形を参照の絵の比率へ寄せる（目より下を上げ、あごを細く）。
- model.closed_eye_key: 目を閉じたとき用の形状キー（目の穴をふさぎ、まつ毛を引っ込める）を頭に足す。
"""
import math
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, number, register, require

BODY_SHAPE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "body_shape.py"
BODY_SIZE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "body_size.py"
COMMON = {"blender", "addons", "blender_extensions", "timeout", "armature"}


def _refuse_unknown(values, allowed, where):
    unknown = set(values) - set(allowed)
    if unknown:
        raise ToolError(f"{where} の知らない設定: {sorted(unknown)}（使えるのは {sorted(allowed)}）")


def _name(value, where):
    if not isinstance(value, str) or not value:
        raise ToolError(f"{where} は名前（空でない文字列）にしてください: {value!r}")
    return value


def _names(value, where, allow_empty=False):
    if (not isinstance(value, list) or (not value and not allow_empty) or not all(isinstance(n, str) and n for n in value)
            or len(set(value)) != len(value)):
        raise ToolError(f"{where} は名前（空でない・重ならない文字列）の並びにしてください: {value!r}")
    return value


def _real(value, where, minimum=None, exclusive=False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{where} は有限の数にしてください: {value!r}")
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        raise ToolError(f"{where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    if maximum is not None and value > maximum:
        raise ToolError(f"{where} は {maximum} 以下にしてください: {value!r}")
    return value


def _table(value, where, low=None, high=None):
    from ..blender_adapter.model_edit import check_table
    try:
        return check_table(value, where, low, high)
    except ValueError as error:
        raise ToolError(str(error)) from error


def _armature(parameters):
    armature = parameters.get("armature")
    if armature is not None:
        _name(armature, "armature")
    return armature


def body_shape_settings(parameters):
    """model.body_shape のパラメーターを確かめ、台本の settings にする。"""
    _refuse_unknown(parameters, COMMON | {"body", "limbs", "torso", "bands", "band_shift", "follow_torso"}, "model.body_shape")
    settings = {"body": _name(parameters.get("body"), "body"), "armature": _armature(parameters)}
    limbs = parameters.get("limbs", [])
    if not isinstance(limbs, list):
        raise ToolError(f"limbs は [{{\"bones\": [骨, …], \"profile\": [[位置, 倍率], …]}}, …] にしてください: {limbs!r}")
    seen = set()
    settings["limbs"] = []
    for k, entry in enumerate(limbs):
        if not isinstance(entry, dict):
            raise ToolError(f"limbs[{k}] は {{\"bones\": …, \"profile\": …}} にしてください: {entry!r}")
        _refuse_unknown(entry, ("bones", "profile"), f"limbs[{k}]")
        bones = _names(entry.get("bones"), f"limbs[{k}].bones")
        if seen & set(bones):
            raise ToolError(f"limbs の骨が 2 回出てきます: {sorted(seen & set(bones))}")
        seen |= set(bones)
        settings["limbs"].append({"bones": bones, "profile": _table(entry.get("profile"), f"limbs[{k}].profile", 0.0, 1.0)})
    torso = parameters.get("torso")
    if torso is not None:
        if not isinstance(torso, dict):
            raise ToolError(f"torso は {{\"bones\": …, \"width\": …, \"depth\": …}} にしてください: {torso!r}")
        _refuse_unknown(torso, ("bones", "width", "depth", "levels", "band", "half_width", "min_points", "step"), "torso")
        checked = {"bones": _names(torso.get("bones"), "torso.bones")}
        if seen & set(checked["bones"]):
            raise ToolError(f"torso の骨が limbs にもあります: {sorted(seen & set(checked['bones']))}")
        for key in ("width", "depth"):
            if torso.get(key) is not None:
                checked[key] = _table(torso[key], f"torso.{key}")
        if "levels" in torso:
            levels = torso["levels"]
            if (not isinstance(levels, list) or len(levels) != 3
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in levels)
                    or levels[2] <= 0 or levels[1] <= levels[0]):
                raise ToolError(f"torso.levels は [最初の高さ, 終わり（含まない）, きざみ]（m。きざみは正）にしてください: {levels!r}")
            checked["levels"] = levels
        for key, minimum in (("band", 0.0), ("half_width", 0.0), ("step", 0.0)):
            if key in torso:
                checked[key] = _real(torso[key], f"torso.{key}", minimum, exclusive=True)
        if "min_points" in torso:
            checked["min_points"] = number(torso, "min_points", minimum=1, integer=True)
        settings["torso"] = checked
    bands = parameters.get("bands")
    if bands is not None:
        if not isinstance(bands, dict):
            raise ToolError(f"bands は {{\"bones\": …, \"factor\": …}} にしてください: {bands!r}")
        _refuse_unknown(bands, ("bones", "factor", "max_verts", "share"), "bands")
        checked = {"bones": _names(bands.get("bones"), "bands.bones")}
        if not set(checked["bones"]) <= seen:
            raise ToolError(f"bands の骨は limbs の骨にしてください（腕輪は腕と同じ骨の軸で広げる）: {sorted(set(checked['bones']) - seen)}")
        if "factor" in bands:
            checked["factor"] = _real(bands["factor"], "bands.factor", 0.0, exclusive=True)
        if "max_verts" in bands:
            checked["max_verts"] = number(bands, "max_verts", minimum=1, integer=True)
        if "share" in bands:
            checked["share"] = _real(bands["share"], "bands.share", 0.0, maximum=1.0)
        settings["bands"] = checked
    shift = parameters.get("band_shift")
    if shift is not None:
        if not isinstance(shift, dict):
            raise ToolError(f"band_shift は {{\"pairs\": [[骨, 先の骨], …], \"shift\": …}} にしてください: {shift!r}")
        _refuse_unknown(shift, ("pairs", "shift", "seam_inside", "seam_gap", "keep_from", "stretch_from", "weight_from", "weight_span"),
                        "band_shift")
        if settings.get("bands") is None:
            raise ToolError("band_shift には bands が要ります（動かす腕輪を島で見つけるため）")
        pairs = shift.get("pairs")
        if (not isinstance(pairs, list) or not pairs
                or not all(isinstance(p, list) and len(p) == 2 and all(isinstance(n, str) and n for n in p) and p[0] != p[1] for p in pairs)
                or len({p[0] for p in pairs}) != len(pairs)):
            raise ToolError(f"band_shift.pairs は [[腕輪の骨, その先の骨], …]（重ならない名前）にしてください: {pairs!r}")
        missing = sorted({p[0] for p in pairs} - set(settings["bands"]["bones"]))
        if missing:
            raise ToolError(f"band_shift.pairs の腕輪の骨は bands.bones にしてください: {missing}")
        checked = {"pairs": pairs, "shift": _real(shift.get("shift"), "band_shift.shift")}
        for key in ("seam_inside", "seam_gap"):
            if key in shift:
                checked[key] = _real(shift[key], f"band_shift.{key}", 0.0)
        for key in ("keep_from", "stretch_from", "weight_from"):
            if key in shift:
                checked[key] = _real(shift[key], f"band_shift.{key}", 0.0, maximum=1.0)
        if "weight_span" in shift:
            checked["weight_span"] = _real(shift["weight_span"], "band_shift.weight_span", 0.0, exclusive=True)
        stretch_from, keep_from = checked.get("stretch_from", 0.20), checked.get("keep_from", 0.88)
        if stretch_from >= keep_from:
            raise ToolError(f"band_shift.stretch_from（{stretch_from}）は keep_from（{keep_from}）より小さくしてください")
        settings["band_shift"] = checked
    follow = parameters.get("follow_torso", [])
    settings["follow_torso"] = _names(follow, "follow_torso", allow_empty=True)
    if settings["body"] in settings["follow_torso"]:
        raise ToolError("follow_torso に body を入れないでください（体は頂点グループの重みで広げる）")
    has_torso = bool(settings.get("torso") and (settings["torso"].get("width") or settings["torso"].get("depth")))
    if settings["follow_torso"] and not has_torso:
        raise ToolError("follow_torso には torso の width か depth が要ります（胴と同じ広げ方で付いて行かせる）")
    if not settings["limbs"] and not has_torso and not settings.get("band_shift"):
        raise ToolError("model.body_shape には limbs・torso（width か depth）・band_shift のどれかが要ります（何も変えない）")
    return settings


@register("model.body_shape", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="体つきを部位ごとに広げる（Blender の中で、初期姿勢で）。体のメッシュ（body）の頂点ごとに、頂点グループの重みで"
                  "混ぜる: limbs の骨は骨の軸からの距離を骨に沿った位置ごとの倍率（profile）で、torso の骨は高さごとの中心線から幅と"
                  "厚みを高さごとの別々の倍率で広げ、ほかの骨の所は動かさない。bands（骨 1 本だけにほぼ付いた小さな島。腕輪など）は島ごとに"
                  "1 つの倍率、band_shift で腕輪を骨に沿って動かし、肌の継ぎ目も腕輪の下へ動かす。follow_torso の物体（裾・帯など）は胴と"
                  "同じ広げ方。骨と色の絵は変えない。元のファイルは変えない")
def body_shape(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "model.body_shape")
    settings = body_shape_settings(parameters)
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"model.body_shape には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "model.body_shape")
    addons, extensions = blender_addons(parameters, context)
    settings_path = Path(context.step_dir) / "body_shape_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, BODY_SHAPE_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                     "--out-blend", outputs["blend"], "--report", outputs["report"]],
                                  log=Path(context.step_dir) / "blender.log", timeout=_timeout(parameters), addons=addons,
                                  extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "body_shape", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "objects": {name: {key: value.get(key) for key in ("verts", "moved_mm_max", "bands")}
                               for name, value in (report.get("objects") or {}).items()}}
    return StepResult(observations=[observation])


def body_size_settings(parameters):
    """check.body_size のパラメーターを確かめ、台本の settings にする。"""
    _refuse_unknown(parameters, COMMON | {"body", "limbs", "torso", "window", "min_points", "strict", "report_only"}, "check.body_size")
    settings = {"body": _name(parameters.get("body"), "body"), "armature": _armature(parameters)}
    limbs = parameters.get("limbs", [])
    if not isinstance(limbs, list):
        raise ToolError(f"limbs は [{{\"bone\": 骨, \"at\": [位置, …]}}, …] にしてください: {limbs!r}")
    settings["limbs"] = []
    for k, entry in enumerate(limbs):
        if not isinstance(entry, dict):
            raise ToolError(f"limbs[{k}] は {{\"bone\": …, \"at\": …}} にしてください: {entry!r}")
        _refuse_unknown(entry, ("bone", "at", "groups"), f"limbs[{k}]")
        at = entry.get("at")
        if not isinstance(at, list) or not at:
            raise ToolError(f"limbs[{k}].at は骨に沿った位置（0〜1）の並びにしてください: {at!r}")
        checked = {"bone": _name(entry.get("bone"), f"limbs[{k}].bone"),
                   "at": [_real(v, f"limbs[{k}].at", 0.0, maximum=1.0) for v in at]}
        if "groups" in entry:
            checked["groups"] = _names(entry["groups"], f"limbs[{k}].groups")
        settings["limbs"].append(checked)
    torso = parameters.get("torso")
    if torso is not None:
        if not isinstance(torso, dict):
            raise ToolError(f"torso は {{\"groups\": …, \"heights\": …}} にしてください: {torso!r}")
        _refuse_unknown(torso, ("groups", "heights", "band", "half_width", "min_points"), "torso")
        heights = torso.get("heights")
        if not isinstance(heights, list) or not heights:
            raise ToolError(f"torso.heights は測る高さ（m）の並びにしてください: {heights!r}")
        checked = {"groups": _names(torso.get("groups"), "torso.groups"), "heights": [_real(v, "torso.heights") for v in heights]}
        for key in ("band", "half_width"):
            if key in torso:
                checked[key] = _real(torso[key], f"torso.{key}", 0.0, exclusive=True)
        if "min_points" in torso:
            checked["min_points"] = number(torso, "min_points", minimum=1, integer=True)
        settings["torso"] = checked
    if not settings["limbs"] and settings.get("torso") is None:
        raise ToolError("check.body_size には limbs か torso が要ります（測る所が無い）")
    if "window" in parameters:
        settings["window"] = _real(parameters["window"], "window", 0.0, exclusive=True)
    if "min_points" in parameters:
        from ..blender_adapter.body_size import MIN_LIMB_POINTS
        settings["min_points"] = number(parameters, "min_points", minimum=1, integer=True)
        if settings["min_points"] < MIN_LIMB_POINTS:   # 直径は 2 つの向きで測るので、1〜2 頂点では決まらない
            raise ToolError(f"min_points（腕・脚の直径を測る頂点の数）は {MIN_LIMB_POINTS} 以上にしてください: {settings['min_points']}")
    return settings


@register("check.body_size", inputs=("blend",), outputs=("report",), cacheable=False,
          summary="体の太さを初期姿勢で測る（Blender の中で。.blend は変えない）: 腕・脚は骨に沿った位置ごとの、骨の軸に垂直な広がりの"
                  "大きい 2 つの向きの直径、胴は高さごとの左右の幅と前後の厚み（cm）、背の高さ。頂点はいちばん重い頂点グループで選ぶ。"
                  "測るだけ（合否は出さない。report_only を付けると要確認にしない）")
def body_size(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .check_ops import _finish
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "check.body_size")
    settings = body_size_settings(parameters)
    blender = _blender(parameters, context, "check.body_size")
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path, raw_path = step_dir / "body_size_settings.json", step_dir / "body_size_raw.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, BODY_SIZE_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                    "--report", str(raw_path)],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(raw_path)
    report.update({"check": "body_size", "blend": str(inputs["blend"]), "blender_version": record["blender_version"], "pass": None,
                   "reason": "測っただけ（合否は出さない。report_only で測るだけにできる）", "failures": []})
    return _finish(report, outputs, parameters, "体の太さ")


TWIST_BONES_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "twist_bones.py"
TWIST_MEASURE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "twist_measure.py"


def twist_bones_settings(parameters):
    """model.twist_bones のパラメーターを確かめ、台本の settings にする。"""
    _refuse_unknown(parameters, COMMON | {"bones", "meshes", "rigid_max_verts", "rigid_share"}, "model.twist_bones")
    entries = parameters.get("bones")
    if not isinstance(entries, list) or not entries:
        raise ToolError(f"bones は [{{\"bone\": ねじれる骨, \"aim\": 向く骨, \"name\": 足す骨}}, …] にしてください: {entries!r}")
    bones = []
    for k, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ToolError(f"bones[{k}] は {{\"bone\": …, \"aim\": …}} にしてください: {entry!r}")
        _refuse_unknown(entry, ("bone", "aim", "name"), f"bones[{k}]")
        bone, aim = _name(entry.get("bone"), f"bones[{k}].bone"), _name(entry.get("aim"), f"bones[{k}].aim")
        name = _name(entry.get("name", f"{bone}_swing"), f"bones[{k}].name")
        if len({bone, aim, name}) != 3:
            raise ToolError(f"bones[{k}] の bone・aim・name は別の骨にしてください: {entry!r}")
        bones.append({"bone": bone, "aim": aim, "name": name})
    for key in ("bone", "name"):
        values = [entry[key] for entry in bones]
        if len(set(values)) != len(values):
            raise ToolError(f"bones の {key} が重なっています: {values}")
    if {entry["name"] for entry in bones} & ({entry["bone"] for entry in bones} | {entry["aim"] for entry in bones}):
        raise ToolError("bones の name（足す骨）は、bone・aim の骨と別の名前にしてください")
    settings = {"bones": bones, "armature": _armature(parameters)}
    if parameters.get("meshes") is not None:
        settings["meshes"] = _names(parameters["meshes"], "meshes")
    if "rigid_max_verts" in parameters:
        settings["rigid_max_verts"] = number(parameters, "rigid_max_verts", minimum=0, integer=True)
    if "rigid_share" in parameters:
        settings["rigid_share"] = _real(parameters["rigid_share"], "rigid_share", 0.0, maximum=1.0)
    return settings


@register("model.twist_bones", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="ねじれを分ける補助の骨を足す（Blender の中で）: 骨（bone。上腕など）と同じ位置に、Damped Track で aim の骨を向くだけの"
                  "「ねじらない骨」（name）を足し、メッシュの bone の重みを、骨に沿った位置（付け根 0〜先 1）で元の骨と足した骨に分ける"
                  "（付け根に近いほど足した骨へ）。bone だけにほぼ付いた小さな島（腕輪など）は島の平均の割合。動きは変えない。"
                  "骨の拘束はアプリによって効かないので、動画の中だけで使う。元のファイルは変えない")
def twist_bones(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "model.twist_bones")
    settings = twist_bones_settings(parameters)
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"model.twist_bones には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "model.twist_bones")
    addons, extensions = blender_addons(parameters, context)
    settings_path = Path(context.step_dir) / "twist_bones_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, TWIST_BONES_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                      "--out-blend", outputs["blend"], "--report", outputs["report"]],
                                  log=Path(context.step_dir) / "blender.log", timeout=_timeout(parameters), addons=addons,
                                  extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "twist_bones", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "bones": [entry.get("name") for entry in report.get("bones") or []], "weights": report.get("weights")}
    unresolved = [f"{entry['bone']} の重みを持つメッシュがありません（足した骨は何も動かしません）" for entry in settings["bones"]
                  if not any(entry["bone"] in value for value in (report.get("weights") or {}).values())]
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


def twist_summary(raw, over_deg=(60.0, 90.0), top=8, max_twist_deg=None):
    """check.twist の測った値（骨ごとのコマのねじれの角）の要約と合否（max_twist_deg を渡したときだけ）。"""
    import numpy as np
    frames = raw["frames"]
    bones = {}
    failures = []
    for name, values in raw["twist_deg"].items():
        values = np.asarray(values, dtype=np.float64)
        magnitude = np.abs(values)
        order = np.argsort(-magnitude, kind="stable")[:top]
        bones[name] = {"min_deg": float(values.min()), "max_deg": float(values.max()), "max_abs_deg": float(magnitude.max()),
                       "over": {f"{limit:g}": int((magnitude > limit).sum()) for limit in over_deg},
                       "worst": [[int(frames[i]), float(values[i])] for i in order]}
        if max_twist_deg is not None and magnitude.max() > max_twist_deg:
            failures.append(f"{name}: ねじれが最大 {magnitude.max():.1f}°（しきい値 {max_twist_deg:g}°。超えたコマ "
                            f"{int((magnitude > max_twist_deg).sum())}）")
    report = {"check": "twist", "frames": [frames[0], frames[-1]] if frames else [], "frame_count": len(frames), "bones": bones,
              "thresholds": {} if max_twist_deg is None else {"max_twist_deg": max_twist_deg}}
    if max_twist_deg is None:
        report.update({"pass": None, "reason": "しきい値が無いので測っただけ（report_only で測るだけにできる）", "failures": []})
    else:
        report.update({"pass": not failures, "failures": failures})
    return report


@register("check.twist", inputs=("blend",), outputs=("report",), cacheable=False,
          summary="骨が自分の軸（Y）のまわりにどれだけねじれているかを、コマごとに測る（Blender の中で。骨の回転を振りとねじれに分けた、"
                  "ねじれの角）。骨ごとの最小・最大・over_deg（既定 60° と 90°）を超えたコマの数・大きいコマ。コマは frames（[最初, 最後]）"
                  "か、シーンの全コマ。max_twist_deg を渡したときだけ合否を出す。好みは判定しない")
def check_twist(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .check_ops import _finish
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "check.twist")
    _refuse_unknown(parameters, COMMON | {"bones", "frames", "over_deg", "top", "max_twist_deg", "strict", "report_only"}, "check.twist")
    settings = {"bones": _names(parameters.get("bones"), "bones"), "armature": _armature(parameters)}
    span = parameters.get("frames")
    if span is not None:
        if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in span)
                or span[0] > span[1]):
            raise ToolError(f"frames は [最初のコマ, 最後のコマ]（整数、最初 ≤ 最後）にしてください: {span!r}")
        settings["frames"] = span
    over = parameters.get("over_deg", [60, 90])
    if not isinstance(over, list) or not over:
        raise ToolError(f"over_deg は数えるねじれの角（度）の並びにしてください: {over!r}")
    over = [_real(v, "over_deg", 0.0) for v in over]
    top = number(parameters, "top", 8, minimum=1, integer=True)
    limit = None if parameters.get("max_twist_deg") is None else _real(parameters["max_twist_deg"], "max_twist_deg", 0.0)
    blender = _blender(parameters, context, "check.twist")
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path, raw_path = step_dir / "twist_settings.json", step_dir / "twist_raw.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, TWIST_MEASURE_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                       "--report", str(raw_path)],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    raw = read_json(raw_path)
    report = twist_summary(raw, over, top, limit)
    report.update({"blend": str(inputs["blend"]), "blender_version": record["blender_version"], "armature": raw.get("armature"),
                   "raw": str(raw_path)})
    return _finish(report, outputs, parameters, "ねじれの検査")


RECOLOR_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "recolor_texture.py"


def _run_recolor(mode, inputs, parameters, outputs, context, settings, operation, texture=None):
    """recolor_texture.py を mode で動かし、報告を読む。"""
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    blender = _blender(parameters, context, operation)
    addons, extensions = blender_addons(parameters, context)
    settings_path = Path(context.step_dir) / f"{mode}_settings.json"
    atomic_json(settings_path, settings)
    args = ["--mode", mode, "--blend", inputs["blend"], "--settings", str(settings_path), "--out-blend", outputs["blend"],
            "--report", outputs["report"]]
    if texture is not None:
        args += ["--texture", texture]
    record = launcher.run_blender(blender, RECOLOR_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters), addons=addons, extensions=extensions)
    return record, read_json(outputs["report"])


def _outputs(outputs, names, operation):
    missing = [name for name in names if name not in outputs]
    if missing:
        raise ToolError(f"{operation} には outputs の {', '.join(missing)} が要ります")


@register("model.remove_parts", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="部品（物体）を外す（Blender の中で）: objects に挙げた物体を消し、使う物の無くなったメッシュも消す。骨と揺れものの設定は"
                  "残す（メッシュが無いだけで、動きには影響しない）。シーンに無い名前は要確認。元のファイルは変えない")
def remove_parts(inputs, parameters, outputs, context):
    require(inputs, ("blend",), "model.remove_parts")
    _refuse_unknown(parameters, COMMON - {"armature"} | {"objects"}, "model.remove_parts")
    settings = {"objects": _names(parameters.get("objects"), "objects")}
    _outputs(outputs, ("blend", "report"), "model.remove_parts")
    record, report = _run_recolor("remove", inputs, parameters, outputs, context, settings, "model.remove_parts")
    observation = {"kind": "remove_parts", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "removed": report.get("removed"), "missing": report.get("missing")}
    unresolved = [f"外す物体がありません: {name}" for name in report.get("missing") or []]
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


def _gain(value):
    if (not isinstance(value, list) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in value)):
        raise ToolError(f"gain は赤・緑・青に掛ける倍率（0 以上の有限の数 3 つ）にしてください: {value!r}")
    return value


@register("model.recolor_part", inputs=("blend",), outputs=("blend", "texture", "report"), cacheable=False,
          summary="1 つの部品（object）のメッシュが使う uv の範囲（三角形と、その外の margin 画素）だけ、色の絵に色ごとの倍率 gain を"
                  "掛ける（Blender の中で）。dark_below を渡すと、明るさ（RGB の最大）がそれより暗い画素だけ（+feather でなめらかに 0 へ。"
                  "明るい縁取りの模様などは変えない）。ほかの部品と分け合う色の絵でも、その部品の所だけが変わる。塗り替えた絵は texture の"
                  "PNG に書き、.blend に詰め込んで付け替える。元のファイルは変えない")
def recolor_part(inputs, parameters, outputs, context):
    require(inputs, ("blend",), "model.recolor_part")
    _refuse_unknown(parameters, COMMON - {"armature"} | {"object", "image", "gain", "dark_below", "feather", "margin"}, "model.recolor_part")
    settings = {"object": _name(parameters.get("object"), "object"), "image": None, "gain": _gain(parameters.get("gain")),
                "dark_below": None, "feather": 0.1, "margin": 3.0}
    if parameters.get("image") is not None:
        settings["image"] = _name(parameters["image"], "image")
    if parameters.get("dark_below") is not None:
        settings["dark_below"] = _real(parameters["dark_below"], "dark_below", 0.0, maximum=1.0)
    if "feather" in parameters:
        settings["feather"] = _real(parameters["feather"], "feather", 0.0, exclusive=True)
    if "margin" in parameters:
        settings["margin"] = _real(parameters["margin"], "margin", 0.0)
    _outputs(outputs, ("blend", "texture", "report"), "model.recolor_part")
    if Path(outputs["texture"]).suffix.lower() != ".png":
        raise ToolError(f"outputs の texture は .png にしてください: {Path(outputs['texture']).name}")
    record, report = _run_recolor("part", inputs, parameters, outputs, context, settings, "model.recolor_part", str(outputs["texture"]))
    observation = {"kind": "recolor_part", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"],
                   **{key: report.get(key) for key in ("object", "image", "texels_in_part", "texels_changed")}}
    unresolved = [] if report.get("texels_in_part") else [f"{report.get('object')} の、絵 {report.get('image')} を使う面の uv の範囲に画素がありません"]
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


RULE_KEYS = {"image", "hue_range_deg", "soft", "min_saturation", "hue_shift", "sat_scale", "value_gamma", "value_scale"}


@register("model.recolor", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="色の絵の、色相の範囲だけを補正する（Blender の中で）: rules の 1 つごとに、名前に image を含む絵の、色相 hue_range_deg"
                  "（度。縁は soft 度でなめらかに）で彩度が min_saturation を超える画素の、色相（hue_shift）・彩度（sat_scale）・明るさ"
                  "（value_gamma・value_scale）を変える。範囲の外は変えない。補正した絵を <元の名前>_recolor として詰め込み、付け替える。"
                  "元のファイルは変えない")
def recolor(inputs, parameters, outputs, context):
    require(inputs, ("blend",), "model.recolor")
    _refuse_unknown(parameters, COMMON - {"armature"} | {"rules"}, "model.recolor")
    rules = parameters.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ToolError(f"rules は [{{\"image\": 名前の一部, \"hue_range_deg\": [下, 上], …}}, …] にしてください: {rules!r}")
    checked = []
    for k, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ToolError(f"rules[{k}] は {{\"image\": …, \"hue_range_deg\": …}} にしてください: {rule!r}")
        _refuse_unknown({key for key in rule if not key.startswith("_")}, RULE_KEYS, f"rules[{k}]")
        hue_range = rule.get("hue_range_deg")
        if (not isinstance(hue_range, list) or len(hue_range) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in hue_range)
                or not hue_range[0] < hue_range[1] or hue_range[1] - hue_range[0] > 360):
            raise ToolError(f"rules[{k}].hue_range_deg は [下, 上]（度。下 < 上、幅 360 以下。0 より下も書ける）にしてください: {hue_range!r}")
        entry = {"image": _name(rule.get("image"), f"rules[{k}].image"), "hue_range_deg": hue_range}
        for key, minimum, exclusive in (("soft", 0.0, True), ("min_saturation", 0.0, False), ("sat_scale", 0.0, False),
                                        ("value_gamma", 0.0, True), ("value_scale", 0.0, False)):
            if key in rule:
                entry[key] = _real(rule[key], f"rules[{k}].{key}", minimum, exclusive=exclusive)
        if "hue_shift" in rule:
            entry["hue_shift"] = _real(rule["hue_shift"], f"rules[{k}].hue_shift")
        checked.append(entry)
    _outputs(outputs, ("blend", "report"), "model.recolor")
    record, report = _run_recolor("recolor", inputs, parameters, outputs, context, {"rules": checked}, "model.recolor")
    observation = {"kind": "recolor", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "rules": report.get("rules")}
    return StepResult(observations=[observation])


SKIN_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "skin_repaint.py"


def _range(value, where, low=None, high=None):
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value) or value[0] > value[1]):
        raise ToolError(f"{where} は [下, 上]（有限の数、下 ≤ 上）にしてください: {value!r}")
    if (low is not None and value[0] < low) or (high is not None and value[1] > high):
        raise ToolError(f"{where} は {low}〜{high} の中にしてください: {value!r}")
    return value


def _skin(value):
    if not isinstance(value, dict):
        raise ToolError(f"skin は {{\"hue_deg\": [下, 上], \"saturation\": [下, 上], \"value_min\": 下}} にしてください: {value!r}")
    _refuse_unknown(value, ("hue_deg", "saturation", "value_min"), "skin")
    return {"hue_deg": _range(value.get("hue_deg"), "skin.hue_deg", 0.0, 360.0),
            "saturation": _range(value.get("saturation"), "skin.saturation", 0.0, 1.0),
            "value_min": _real(value.get("value_min"), "skin.value_min", 0.0, maximum=1.0)}


def _run_skin(mode, inputs, parameters, outputs, context, settings, operation):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    _outputs(outputs, ("blend", "texture", "report"), operation)
    if Path(outputs["texture"]).suffix.lower() != ".png":
        raise ToolError(f"outputs の texture は .png にしてください: {Path(outputs['texture']).name}")
    blender = _blender(parameters, context, operation)
    addons, extensions = blender_addons(parameters, context)
    settings_path = Path(context.step_dir) / f"{mode}_skin_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, SKIN_SCRIPT, None, ["--mode", mode, "--blend", inputs["blend"], "--settings", str(settings_path),
                                                               "--out-blend", outputs["blend"], "--texture", str(outputs["texture"]),
                                                               "--report", outputs["report"]],
                                  log=Path(context.step_dir) / "blender.log", timeout=_timeout(parameters), addons=addons,
                                  extensions=extensions)
    return record, read_json(outputs["report"])


@register("model.smooth_skin", inputs=("blend",), outputs=("blend", "texture", "report"), cacheable=False,
          summary="肌の島の、選んだ範囲の溝（形）と、色の絵に焼き込まれた明暗（色）をならす（Blender の中で）。肌色（skin）の頂点のうち、"
                  "seed_z の高さの横を向いた肌をいちばん多く含む島だけを相手にし、高さ z_range・体の中心からの横の距離 x_min〜x_max・"
                  "横を向いた面 facing のなだらかな重みで、色は頂点の色を隣どうしでならして塗り直し、形は shape で縮まないならし方"
                  "（Taubin）をかける。色だけで選ばず島に限るので、色の近い服は崩さない。塗り直した絵は texture の PNG に書いて詰め込む。"
                  "元のファイルは変えない")
def smooth_skin(inputs, parameters, outputs, context):
    require(inputs, ("blend",), "model.smooth_skin")
    _refuse_unknown(parameters, COMMON - {"armature"} | {"body", "image", "skin", "seed_z", "z_range", "z_soft", "x_min", "x_max", "x_soft",
                                                         "facing", "shape", "color_iterations", "paint_margin_px"}, "model.smooth_skin")
    settings = {"body": _name(parameters.get("body"), "body"), "image": _name(parameters.get("image"), "image"),
                "skin": _skin(parameters.get("skin")), "seed_z": _range(parameters.get("seed_z"), "seed_z"),
                "z_range": _range(parameters.get("z_range"), "z_range"),
                "x_min": _real(parameters.get("x_min"), "x_min", 0.0), "x_max": _real(parameters.get("x_max"), "x_max", 0.0),
                "facing": _range(parameters.get("facing"), "facing", 0.0, 1.0)}
    if settings["x_max"] <= settings["x_min"]:
        raise ToolError(f"x_max（{settings['x_max']}）は x_min（{settings['x_min']}）より大きくしてください")
    for key in ("z_soft", "x_soft"):
        if key in parameters:
            settings[key] = _real(parameters[key], key, 0.0, exclusive=True)
    if "color_iterations" in parameters:
        settings["color_iterations"] = number(parameters, "color_iterations", minimum=0, integer=True)
    if "paint_margin_px" in parameters:
        settings["paint_margin_px"] = _real(parameters["paint_margin_px"], "paint_margin_px", 0.0)
    shape = parameters.get("shape")
    if shape is not None:
        if not isinstance(shape, dict):
            raise ToolError(f"shape は {{\"iterations\": …, \"factor\": …, \"inflate\": …}} にしてください: {shape!r}")
        _refuse_unknown(shape, ("iterations", "factor", "inflate", "z_max", "z_soft"), "shape")
        checked = {}
        if "iterations" in shape:
            checked["iterations"] = number(shape, "iterations", minimum=0, integer=True)
        if "factor" in shape:
            checked["factor"] = _real(shape["factor"], "shape.factor", 0.0, exclusive=True, maximum=1.0)
        if "inflate" in shape:
            checked["inflate"] = _real(shape["inflate"], "shape.inflate", maximum=0.0)
        if shape.get("z_max") is not None:
            checked["z_max"] = _real(shape["z_max"], "shape.z_max")
        if "z_soft" in shape:
            checked["z_soft"] = _real(shape["z_soft"], "shape.z_soft", 0.0, exclusive=True)
        settings["shape"] = checked
    record, report = _run_skin("smooth", inputs, parameters, outputs, context, settings, "model.smooth_skin")
    observation = {"kind": "smooth_skin", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"],
                   **{key: report.get(key) for key in ("skin_island_verts", "selected_verts", "shape_moved_mm", "texels_painted")}}
    unresolved = [] if report.get("selected_verts") else ["ならす頂点がありません（範囲の設定を確かめてください）"]
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


@register("model.clean_skin", inputs=("blend",), outputs=("blend", "texture", "report"), cacheable=False,
          summary="外した部品の下の肌のしみを消す（Blender の中で）: island_groups（手など。名前の頭）がいちばん重い頂点を island_min 以上"
                  "含む島の、region_groups（上腕・前腕など。名前の頭）の頂点のうち、肌色（skin）でないもの（しみ）を、まわりのきれいな"
                  "肌の色で外側から順に埋め、範囲の中を軽くならして、色の絵を塗り直す（手は触らない）。塗り直した絵は texture の PNG に"
                  "書いて詰め込む。元のファイルは変えない")
def clean_skin(inputs, parameters, outputs, context):
    require(inputs, ("blend",), "model.clean_skin")
    _refuse_unknown(parameters, COMMON | {"body", "image", "skin", "island_groups", "island_min", "region_groups",
                                           "fill_iterations", "smooth_iterations", "smooth_factor", "paint_margin_px"},
                    "model.clean_skin")
    settings = {"body": _name(parameters.get("body"), "body"), "image": _name(parameters.get("image"), "image"), "armature": _armature(parameters),
                "skin": _skin(parameters.get("skin")), "island_groups": _names(parameters.get("island_groups"), "island_groups"),
                "region_groups": _names(parameters.get("region_groups"), "region_groups")}
    for key in ("island_min", "fill_iterations", "smooth_iterations"):
        if key in parameters:
            settings[key] = number(parameters, key, minimum=1 if key == "island_min" else 0, integer=True)
    if "smooth_factor" in parameters:
        settings["smooth_factor"] = _real(parameters["smooth_factor"], "smooth_factor", 0.0, exclusive=True, maximum=1.0)
    if "paint_margin_px" in parameters:
        settings["paint_margin_px"] = _real(parameters["paint_margin_px"], "paint_margin_px", 0.0)
    record, report = _run_skin("clean", inputs, parameters, outputs, context, settings, "model.clean_skin")
    observation = {"kind": "clean_skin", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"],
                   **{key: report.get(key) for key in ("islands", "region_verts", "dirty_verts", "not_filled", "texels_painted")}}
    unresolved = []
    if not report.get("region_verts"):
        unresolved.append("塗り直す頂点がありません（island_groups・region_groups を確かめてください）")
    if report.get("not_filled"):
        unresolved.append(f"きれいな肌の色で埋めきれない頂点が {report['not_filled']} あります（fill_iterations を増やすか、範囲を確かめてください）")
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


FACE_SHAPE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "face_shape.py"


def face_shape_settings(parameters):
    """model.face_shape のパラメーターを確かめ、台本の settings にする（上下が入れ替わる表は Blender の前で止める）。"""
    from .. import texture
    from ..blender_adapter.face_shape import lift_curve
    _refuse_unknown(parameters, COMMON - {"armature"} | {"head", "view", "lift", "smooth", "depth", "narrow"}, "model.face_shape")
    view = parameters.get("view")
    texture.check_view(view)
    if view["from"] != "-Y":
        raise ToolError(f"model.face_shape の view は正面（from: -Y）にしてください: {view['from']!r}")
    lift = parameters.get("lift")
    if not isinstance(lift, list) or not lift:
        raise ToolError(f"lift は [{{\"vy\": 今の高さ（視点の画素）, \"up\": 上へ動かす量（画素）}}, …] にしてください: {lift!r}")
    anchors = []
    for k, entry in enumerate(lift):
        if not isinstance(entry, dict):
            raise ToolError(f"lift[{k}] は {{\"vy\": …, \"up\": …}} にしてください: {entry!r}")
        _refuse_unknown(entry, ("vy", "up", "name"), f"lift[{k}]")
        anchor = {"vy": _real(entry.get("vy"), f"lift[{k}].vy"), "up": _real(entry.get("up"), f"lift[{k}].up")}
        if "name" in entry:
            anchor["name"] = _name(entry["name"], f"lift[{k}].name")
        anchors.append(anchor)
    if len({a["vy"] for a in anchors}) != len(anchors):
        raise ToolError("lift の vy が重なっています")
    depth = parameters.get("depth")
    if not isinstance(depth, dict):
        raise ToolError(f"depth は {{\"front\": 顔の正面の y, \"back\": 耳のあたりの y}}（m）にしてください: {depth!r}")
    _refuse_unknown(depth, ("front", "back"), "depth")
    front, back = _real(depth.get("front"), "depth.front"), _real(depth.get("back"), "depth.back")
    if front >= back:
        raise ToolError(f"depth.front（{front}）は back（{back}）より手前（小さい y）にしてください")
    settings = {"head": _name(parameters.get("head"), "head"), "view": view, "lift": anchors, "depth": {"front": front, "back": back},
                "smooth": _real(parameters.get("smooth", 10.0), "smooth", 0.0, exclusive=True),
                "narrow": _real(parameters.get("narrow", 0.0), "narrow", 0.0, maximum=0.5)}
    try:
        lift_curve(anchors, settings["smooth"])
    except ValueError as error:
        raise ToolError(str(error)) from error
    return settings


@register("model.face_shape", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="顔の形を参照の絵の比率へ寄せる（Blender の中で）: 頭のメッシュ（head）の頂点を、高さに応じて上へ寄せる。高さは正面の正射影の"
                  "視点（view。texture.project_reference と同じ書き方）の画素で、lift の「今の高さ vy → 上へ動かす量 up」の表をなめらかに"
                  "補間する。顔の正面（depth.front）で効き、耳のあたり（depth.back）で 0（頭の後ろ側と首のつなぎ目は動かさない）。narrow で"
                  "あご先を細く。色の絵は頂点に付いて動く。元のファイルは変えない")
def face_shape(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "model.face_shape")
    settings = face_shape_settings(parameters)
    _outputs(outputs, ("blend", "report"), "model.face_shape")
    blender = _blender(parameters, context, "model.face_shape")
    addons, extensions = blender_addons(parameters, context)
    settings_path = Path(context.step_dir) / "face_shape_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, FACE_SHAPE_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                     "--out-blend", outputs["blend"], "--report", outputs["report"]],
                                  log=Path(context.step_dir) / "blender.log", timeout=_timeout(parameters), addons=addons,
                                  extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "face_shape", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"],
                   **{key: report.get(key) for key in ("head", "vertices", "moved_over_1mm", "max_up_mm", "lowest_z_before", "lowest_z_after")}}
    unresolved = [] if report.get("moved_over_1mm") else ["1 mm 以上動いた頂点がありません（view・lift・depth を確かめてください）"]
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


CLOSED_EYE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "closed_eye_key.py"


@register("model.closed_eye_key", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="目を閉じたとき用の形状キー（key。既定 eyes_closed、値 0）を頭（head）に足す（Blender の中で）: 目のまわりの箱"
                  "（|x| が eye_x、高さ eye_z、y が front_y より手前）の眼球の面を、目の穴の縁に張った膜の位置まで手前に出し（穴をふさぐ）、"
                  "まつ毛の板などの小さな部品は頭の中へ tuck 引っ込める。閉じた目の線は絵で描く（render.passes の expressions で、閉じた目の"
                  "絵の間だけ 1 にする）。元のファイルは変えない")
def closed_eye_key(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "model.closed_eye_key")
    _refuse_unknown(parameters, COMMON - {"armature"} | {"head", "key", "eye_x", "eye_z", "front_y", "eyeball", "rim_min_verts", "membrane",
                                                         "tuck"}, "model.closed_eye_key")
    settings = {"head": _name(parameters.get("head"), "head"), "key": _name(parameters.get("key", "eyes_closed"), "key"),
                "eye_x": _range(parameters.get("eye_x"), "eye_x", 0.0), "eye_z": _range(parameters.get("eye_z"), "eye_z"),
                "front_y": _real(parameters.get("front_y"), "front_y")}
    if settings["eye_x"][0] >= settings["eye_x"][1] or settings["eye_z"][0] >= settings["eye_z"][1]:
        raise ToolError("eye_x・eye_z は [下, 上]（下 < 上）にしてください")
    eyeball = parameters.get("eyeball")
    if eyeball is not None:
        if not isinstance(eyeball, dict):
            raise ToolError(f"eyeball は {{\"min_verts\": …, \"min_height\": …}} にしてください: {eyeball!r}")
        _refuse_unknown(eyeball, ("min_verts", "min_height"), "eyeball")
        checked = {}
        if "min_verts" in eyeball:
            checked["min_verts"] = number(eyeball, "min_verts", minimum=1, integer=True)
        if "min_height" in eyeball:
            checked["min_height"] = _real(eyeball["min_height"], "eyeball.min_height", 0.0)
        settings["eyeball"] = checked
    if "rim_min_verts" in parameters:
        settings["rim_min_verts"] = number(parameters, "rim_min_verts", minimum=3, integer=True)
    for key in ("membrane", "tuck"):
        if key in parameters:
            settings[key] = _real(parameters[key], key, 0.0)
    _outputs(outputs, ("blend", "report"), "model.closed_eye_key")
    blender = _blender(parameters, context, "model.closed_eye_key")
    addons, extensions = blender_addons(parameters, context)
    settings_path = Path(context.step_dir) / "closed_eye_key_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, CLOSED_EYE_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                     "--out-blend", outputs["blend"], "--report", outputs["report"]],
                                  log=Path(context.step_dir) / "blender.log", timeout=_timeout(parameters), addons=addons,
                                  extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "closed_eye_key", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "head": report.get("head"), "key": report.get("key"),
                   "sides": {side: {"rim_verts": value.get("rim_verts"), "parts": [p.get("kind") for p in value.get("parts", [])]}
                             for side, value in (report.get("sides") or {}).items()}}
    return StepResult(observations=[observation])
