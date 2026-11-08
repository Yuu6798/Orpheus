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


FRAMES_PARAMETERS = {"left_dir", "right_dir", "range", "top", "amplify", "view_frame", "strict", "report_only",
                     *checks.FRAME_THRESHOLDS}


def frame_sides(inputs, parameters, recipe_dir):
    """左右のコマの並び: 入力 left / right（描いたコマの一覧）か、パラメーター left_dir / right_dir（コマのフォルダ）。range で T を絞る。"""
    from pathlib import Path
    from ..io import resolve_path
    sides, sources = {}, {}
    for side in ("left", "right"):
        given, folder = inputs.get(side), parameters.get(f"{side}_dir")
        if (given is None) == (folder is None):
            raise ToolError(f"{side} は、入力 {side}（描いたコマの一覧 model3d-frames/0.1）か、パラメーター {side}_dir（コマのフォルダ）の"
                            "どちらか 1 つにしてください")
        source = Path(given) if given is not None else resolve_path(recipe_dir, folder)
        if folder is not None and not source.is_dir():
            raise ToolError(f"{side}_dir のフォルダがありません: {source}")
        sides[side], sources[side] = checks.frame_files(source), str(source)
    span = parameters.get("range")
    if span is not None:
        if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in span)
                or span[0] > span[1]):
            raise ToolError(f"range は [最初の T, 最後の T]（1 以上の整数、最初 ≤ 最後）にしてください: {span!r}")
        sides = {side: {T: path for T, path in files.items() if span[0] <= T <= span[1]} for side, files in sides.items()}
    if not sides["left"]:
        raise ToolError(f"左にコマがありません（f<T>.png）: {sources['left']}")
    return sides, sources


@register("check.frames", inputs=("left", "right"), outputs=("report", "view"), cacheable=False,
          summary="2 つのコマの並び（描いたコマの一覧か、コマのフォルダ）を、同じ T どうしで画素で比べる: 同じコマの数・違う画素の数・"
                  "いちばん大きい値の差・色の差の平均・透けの違い・差の大きさごとの画素の数・違いの大きいコマ。片方にしか無いコマと大きさの"
                  "違うコマも並べる。view で [左 | 右 | 差 × amplify] の PNG。しきい値（コマごとの max_differing_pixels・"
                  "max_channel_diff・max_mean_abs）を渡したときだけ合否を出す。コマは隠れた入力なので再利用しない")
def check_frames(inputs, parameters, outputs, context):
    from . import number
    unknown = set(parameters) - FRAMES_PARAMETERS
    if unknown:
        raise ToolError(f"check.frames の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(FRAMES_PARAMETERS)}）")
    sides, sources = frame_sides(inputs, parameters, context.recipe_dir)
    thresholds = {key: number(parameters, key, minimum=0) for key in checks.FRAME_THRESHOLDS if parameters.get(key) is not None}
    top = number(parameters, "top", 5, minimum=1, integer=True)
    amplify = number(parameters, "amplify", 8, minimum=0, exclusive=True)
    report = checks.compare_frames(sides["left"], sides["right"], thresholds, top)
    report.update(left=sources["left"], right=sources["right"])
    if "view" in outputs:
        T = parameters.get("view_frame")
        if T is None:
            T = report["worst_frames"][0]["T"] if report["worst_frames"] else None
        if T is None or T not in sides["left"] or T not in sides["right"]:
            raise ToolError(f"view に並べるコマがありません（view_frame {T!r} が左右の両方に無い）")
        report["view"] = {"T": T, **checks.frame_view(sides["left"][T], sides["right"][T], outputs["view"], amplify)}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    summary = {k: v for k, v in report.items() if k != "rows"}
    return _finish(summary, {k: v for k, v in outputs.items() if k != "report"}, parameters, "コマの比べ")
