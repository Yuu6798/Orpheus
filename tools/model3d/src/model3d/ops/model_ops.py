"""モデルを手直しする工程（docs/model3d.md の「モデルを手直しする」）。画像から作ったモデルの癖（細い体など）を、部位ごとに測ってから
直す。どれも Blender の中で動かす。Blender は外部の実行ファイルなので再利用しない（毎回実行）。

- model.body_shape: 体つきを部位ごとに広げる（腕・脚は骨の軸のまわり、胴は高さごとの幅と厚み、腕輪は形を保ってずらす）。
- check.body_size: 体の太さを測る（腕・脚の直径、胴の幅と厚み）。測るだけ。
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
        settings["min_points"] = number(parameters, "min_points", minimum=1, integer=True)
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
