"""検査の操作。合否は report に残し、不合格は needs_review（strict なら失敗、report_only なら測るだけ）にする。
未実施（材料が無い）は合格にしない: needs_review に残し、strict なら失敗にする。"""
from .. import checks, semantic
from ..io import ToolError, atomic_json, read_json
from ..mesh import Mesh
from . import StepResult, register, require, selected


def _finish(report, outputs, parameters, label):
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    passed = report.get("pass")
    if parameters.get("report_only"):   # 測るだけ（補正前の値を残すなど）。合否は report に残り、工程の状態は変えない
        return StepResult(observations=[report])
    if passed is None:
        if parameters.get("strict"):   # 未実施は合格ではない。strict なら止める
            raise ToolError(f"{label}: 未実施（{report.get('reason')}）は strict では不合格です")
        return StepResult(observations=[report], unresolved=[f"{label}: 未実施（{report.get('reason')}）"], review=True)
    if passed:
        return StepResult(observations=[report])
    failures = report.get("failures") or [f"{label}: 不合格"]
    if parameters.get("strict"):
        raise ToolError(f"{label} が不合格です: " + "; ".join(str(f) for f in failures))
    return StepResult(observations=[report], unresolved=[f"{label}: {f}" for f in failures], review=True)


@register("check.structure", inputs=("rig",), outputs=("report",),
          summary="骨・ウェイトを検査し、任意の入力 mesh と頂点数・topology を照合する。required_bone_roles は preset か配列")
def check_structure(inputs, parameters, outputs, context):
    require(inputs, ("rig",), "check.structure")
    from .. import contracts
    rig = contracts.validate("rig", read_json(inputs["rig"]), inputs["rig"])
    required = parameters.get("required_bone_roles")
    if required == "preset":
        if context.preset is None:
            raise ToolError("required_bone_roles: preset には preset が要ります")
        required = context.preset["required_roles"].get("bones", [])
    elif required == "humanoid":
        required = list(semantic.HUMANOID_REQUIRED_BONES)
    mesh = Mesh.load(inputs["mesh"]) if "mesh" in inputs else None
    report = checks.structure(rig, context.semantic_map, required, parameters.get("tolerance", 1e-3), mesh=mesh)
    result = _finish(report, outputs, parameters, "構造の検査")
    if required and context.semantic_map is not None:
        unconfirmed = {v["role"] for v in context.semantic_map["bones"].values() if v["state"] == "unconfirmed"}
        for role in sorted(set(required) & unconfirmed):
            result.unresolved.append(f"構造の検査: 必須骨の役割 {role} の対応が未確認です")
            result.review = True
    return result


@register("check.penetration", inputs=("mesh", "collider"), outputs=("report",), selectable=("mesh",),
          summary="mesh の頂点（select で役割の面の頂点だけ）が閉じた collider の中にないことを検査する")
def check_penetration(inputs, parameters, outputs, context):
    require(inputs, ("mesh", "collider"), "check.penetration")
    chosen = selected(context, "mesh", parameters, "check.penetration")
    report = checks.penetration(Mesh.load(inputs["mesh"]), Mesh.load(inputs["collider"]), parameters.get("directions"),
                                vertices=chosen["vertices"] if chosen else None)
    if report["inside"]:
        report["failures"] = [f"{report['inside']} 頂点が中にあります（最大の深さ {report['max_depth']:.4g}）"]
    return _finish(report, outputs, parameters, "貫通の検査")


@register("check.continuity", inputs=("track",), outputs=("report",),
          summary="コマごとの点の列で、前のコマからの移動が max_jump を超えないことを検査する")
def check_continuity(inputs, parameters, outputs, context):
    require(inputs, ("track",), "check.continuity")
    from . import number
    report = checks.continuity(read_json(inputs["track"]), number(parameters, "max_jump", minimum=0, exclusive=True))
    if report.get("jumps"):
        report["failures"] = [f"コマ {j['frame']}（t={j['t']}）で {j['max_move']:.4g} 動いています" for j in report["jumps"]]
    return _finish(report, outputs, parameters, "連続性の検査")


MOTION_PARAMETERS = {"fps", "bend_min_deg", "contact_height", "skate_speed", "floor_tolerance", "frames", "top",
                     "strict", "report_only", *checks.MOTION_THRESHOLDS}


@register("check.motion", inputs=("points",), outputs=("report",),
          summary="motion.prepare_keypoints の points（npz）の動きを数で測る: 膝とつま先の食い違い・接地中の足の滑り・"
                  "床へのめり込み・1 コマの移動・骨の長さのぶれ。しきい値（max_swivel_deg など）を渡したときだけ合否を出す。好みは判定しない")
def check_motion(inputs, parameters, outputs, context):
    require(inputs, ("points",), "check.motion")
    import numpy as np
    from . import number
    unknown = set(parameters) - MOTION_PARAMETERS
    if unknown:
        raise ToolError(f"check.motion の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(MOTION_PARAMETERS)}）")
    try:
        with np.load(inputs["points"], allow_pickle=False) as data:
            points = data["points"]
            stance = data["stance"] if "stance" in data.files else None
    except (OSError, KeyError, ValueError) as error:
        raise ToolError(f"points の npz を読めません（points が要ります）: {error}") from error
    frames = parameters.get("frames")
    if frames is not None:
        if not (isinstance(frames, list) and len(frames) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in frames)):
            raise ToolError(f"frames は [開始, 終了) の整数 2 つにしてください: {frames!r}")
        frames = tuple(frames)
    thresholds = {key: number(parameters, key, minimum=0) for key in checks.MOTION_THRESHOLDS if parameters.get(key) is not None}
    report = checks.motion(points, stance, fps=number(parameters, "fps", 30.0, minimum=0, exclusive=True),
                           bend_min_deg=number(parameters, "bend_min_deg", 25.0, minimum=0),
                           contact_height=number(parameters, "contact_height", 0.03, minimum=0, exclusive=True),
                           skate_speed=number(parameters, "skate_speed", 0.2, minimum=0, exclusive=True),
                           floor_tolerance=number(parameters, "floor_tolerance", 0.005, minimum=0),
                           thresholds=thresholds, frames=frames, top=number(parameters, "top", 8, minimum=1, integer=True))
    return _finish(report, outputs, parameters, "動きの検査")


UV_STRETCH_PARAMETERS = {"texture_size", "threshold", "max_over", "max_degenerate", "strict", "report_only"}


@register("check.uv_stretch", inputs=("mesh", "texture"), outputs=("report",),
          summary="uv の伸び（三角形の、テクスチャの画素での周長 / 3D の周長を中央値で割った値）を測る: 百分位・threshold（既定 8）を"
                  "超える三角形の数・3D で潰れた三角形の数・三角形の uv の幅（画素）。テクスチャの大きさは入力 texture の画像か、"
                  "パラメーター texture_size（[幅, 高さ] か 1 つの数）。しきい値（max_over・max_degenerate）を渡したときだけ合否を出す")
def check_uv_stretch(inputs, parameters, outputs, context):
    from . import number
    require(inputs, ("mesh",), "check.uv_stretch")
    unknown = set(parameters) - UV_STRETCH_PARAMETERS
    if unknown:
        raise ToolError(f"check.uv_stretch の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(UV_STRETCH_PARAMETERS)}）")
    size = parameters.get("texture_size")
    if inputs.get("texture") is not None:
        if size is not None:
            raise ToolError("texture と texture_size は一緒に使えません（大きさはどちらか 1 つで決める）")
        from PIL import Image
        with Image.open(inputs["texture"]) as image:
            width, height = image.size
    elif size is None:
        raise ToolError("check.uv_stretch には、テクスチャの大きさ（入力 texture か、パラメーター texture_size）が要ります")
    else:
        values = size if isinstance(size, list) else [size, size]
        if (len(values) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in values)):
            raise ToolError(f"texture_size は [幅, 高さ] か 1 つの正の整数にしてください: {size!r}")
        width, height = values
    threshold = number(parameters, "threshold", 8.0, minimum=0, exclusive=True)
    thresholds = {key: number(parameters, key, minimum=0) for key in checks.UV_STRETCH_THRESHOLDS if parameters.get(key) is not None}
    report = checks.uv_stretch(Mesh.load(inputs["mesh"]), width, height, threshold, thresholds)
    return _finish(report, outputs, parameters, "uv の伸びの検査")
