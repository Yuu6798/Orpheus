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
          summary="mesh の頂点（select で役割の面の頂点だけ）が閉じた collider の中にないことを検査する。内外の判定の候補の三角形は、"
                  "光線の向きに垂直な面の格子で絞る（search: grid。all で総当たり。答えは同じ）。中に入った頂点を数えるだけなので、"
                  "脚がまるごと布を通り抜ける失敗は拾えない（render.part_colors の色分けの絵で見る）")
def check_penetration(inputs, parameters, outputs, context):
    require(inputs, ("mesh", "collider"), "check.penetration")
    chosen = selected(context, "mesh", parameters, "check.penetration")
    search = parameters.get("search", "grid")
    if search not in ("grid", "all"):
        raise ToolError(f"check.penetration の search は grid か all にしてください: {search!r}")
    report = checks.penetration(Mesh.load(inputs["mesh"]), Mesh.load(inputs["collider"]), parameters.get("directions"),
                                vertices=chosen["vertices"] if chosen else None, search=search)
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


JITTER_PARAMETERS = {"objects", "bone", "bone_prefix", "armature", "frames", "jump_mm", "toggle", "toggle_types", "top", "space",
                     "blender", "addons", "blender_extensions", "timeout", "strict", "report_only", *checks.JITTER_THRESHOLDS}
JITTER_SCRIPT = __import__("pathlib").Path(__file__).resolve().parents[1] / "blender_adapter" / "jitter_sample.py"


def _jitter_frames(inputs, parameters):
    """測るコマの並び [[T, f], …] と出どころ。入力 track（小数のコマも）か、パラメーター frames（[最初, 最後] の整数）。
    どちらも無ければ None（Blender の中でシーンの全コマ）。"""
    span = parameters.get("frames")
    if inputs.get("track") is not None:
        if span is not None:
            raise ToolError("check.jitter: 入力 track とパラメーター frames は一緒に使えません（コマの並びはどちらか 1 つで決める）")
        from .render_ops import _track
        track = _track(inputs["track"])
        f = [float(fr["f"]) for fr in track["frames"]]
        steps = [b - a for a, b in zip(f, f[1:])]
        # 前後のコマの中間と比べるので、動きのコマは等間隔に増えること（plan.retime の並びの丸めの揺れ 1e-13 ほどは許す）
        if steps and (min(steps) <= 0 or max(steps) - min(steps) > 1e-6 * max(1.0, abs(steps[0]))):
            raise ToolError("check.jitter: track の動きのコマ f は、等間隔に増える並びにしてください（前後のコマの中間と比べるため）")
        return [[int(fr["T"]), fr["f"]] for fr in track["frames"]], "track"
    if span is None:
        return None, "scene"
    if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in span)
            or span[0] > span[1]):
        raise ToolError(f"frames は [最初のコマ, 最後のコマ]（整数、最初 ≤ 最後）にしてください: {span!r}")
    return [[k + 1, f] for k, f in enumerate(range(span[0], span[1] + 1))], "range"


@register("check.jitter", inputs=("blend", "track"), outputs=("report",), cacheable=False,
          summary="布などのメッシュ（objects）のちらつきを測る: コマごとに頂点を骨（bone）から見た座標で読み（Blender の中で）、"
                  "前後のコマの中間からのずれ（2 階差分の半分）の平均・99 百分位・最大と、jump_mm（既定 4）を超えた割合、ずれの大きい"
                  "コマと頂点を出す。体の動きそのものは骨から見ると動かないので、布の震えや 1 コマだけの跳びが残る。コマは入力 track "
                  "（小数のコマも）か frames（[最初, 最後]）、無ければシーンの全コマ。toggle で変形を 1 つずつ切って測り直し、どの変形が"
                  "ちらつきを作るかを見分ける。しきい値（max_mean_mm など）を渡したときだけ合否を出す。好みは判定しない。"
                  "Blender は外部の実行ファイルなので再利用しない")
def check_jitter(inputs, parameters, outputs, context):
    from pathlib import Path
    from ..blender_adapter import launcher
    from ..blender_adapter.jitter_sample import SPACES, TOGGLE_TYPES
    from ..io import read_json
    from . import number
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _bone_prefix, _timeout
    require(inputs, ("blend",), "check.jitter")
    unknown = set(parameters) - JITTER_PARAMETERS
    if unknown:
        raise ToolError(f"check.jitter の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(JITTER_PARAMETERS)}）")
    objects = parameters.get("objects")
    if (not isinstance(objects, list) or not objects or not all(isinstance(o, str) and o for o in objects)
            or len(set(objects)) != len(objects)):
        raise ToolError(f"objects は測るメッシュの名前（空でない・重ならない文字列）の並びにしてください: {objects!r}")
    bone = parameters.get("bone", "Hips")
    if not isinstance(bone, str) or not bone:
        raise ToolError(f"bone は骨の名前（bone_prefix の後ろ）にしてください: {bone!r}")
    armature = parameters.get("armature")
    if armature is not None and (not isinstance(armature, str) or not armature):
        raise ToolError(f"armature はアーマチュアの名前にしてください: {armature!r}")
    toggle = parameters.get("toggle", False)
    if not isinstance(toggle, bool):
        raise ToolError(f"toggle は true か false にしてください: {toggle!r}")
    space = parameters.get("space", "bone")
    if space not in SPACES:
        raise ToolError(f"space は {' か '.join(SPACES)} にしてください（bone: 骨の行列をそのまま逆に。rigid: 回転と位置だけ）: {space!r}")
    kinds = parameters.get("toggle_types", list(TOGGLE_TYPES))
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) and k for k in kinds):
        raise ToolError(f"toggle_types はモディファイアの種類（例 \"CLOTH\"）の並びにしてください: {kinds!r}")
    frames, source = _jitter_frames(inputs, parameters)
    if frames is not None and len(frames) < 3:
        raise ToolError(f"check.jitter には 3 コマ以上が要ります（前後のコマの中間と比べるため）: {len(frames)} コマ")
    thresholds = {key: number(parameters, key, minimum=0) for key in checks.JITTER_THRESHOLDS if parameters.get(key) is not None}
    jump = number(parameters, "jump_mm", 4.0, minimum=0, exclusive=True)
    settings = {"objects": objects, "bone": _bone_prefix(parameters) + bone, "armature": armature, "frames": frames,
                "frames_source": source, "jump_m": jump / 1000, "toggle": toggle, "toggle_types": kinds,
                "top": number(parameters, "top", 8, minimum=1, integer=True), "space": space}
    blender = _blender(parameters, context, "check.jitter")
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path, raw_path = step_dir / "jitter_settings.json", step_dir / "jitter_raw.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, JITTER_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                  "--report", str(raw_path)],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = checks.jitter_verdict(read_json(raw_path), thresholds)
    report.update(blend=str(inputs["blend"]), blender_version=record["blender_version"])
    result = _finish(report, outputs, parameters, "ちらつきの検査")
    warnings = report.get("warnings") or []
    if warnings and not parameters.get("report_only"):   # 骨の大きさで長さが変わっているなど、数の読み方に要る注意
        if parameters.get("strict"):
            raise ToolError("ちらつきの検査: " + "; ".join(warnings))
        result.unresolved.extend(f"ちらつきの検査: {w}" for w in warnings)
        result.review = True
    return result


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


FRAMES_PARAMETERS = {"left_dir", "right_dir", "range", "top", "amplify", "view_frame", "strict", "report_only", "zone",
                     *checks.FRAME_THRESHOLDS}


def frame_zone(value):
    """check.frames の zone（数える範囲の内・外を分けて数える）を確かめる。"""
    import math
    if not isinstance(value, dict):
        raise ToolError(f"zone は {{\"boxes\": [[x0, y0, x1, y1], …], \"threshold\": 数, \"metres_per_pixel\": 数}} にしてください: {value!r}")
    unknown = set(value) - {"boxes", "threshold", "metres_per_pixel"}
    if unknown:
        raise ToolError(f"zone の知らないキー: {sorted(unknown)}（使えるのは boxes・threshold・metres_per_pixel）")
    boxes = value.get("boxes")
    if not isinstance(boxes, list) or not boxes:
        raise ToolError(f"zone.boxes は数える範囲の枠 [x0, y0, x1, y1]（画素。右と下は含まない）の並びにしてください: {boxes!r}")
    for k, box in enumerate(boxes):
        if (not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, int) and not isinstance(v, bool) for v in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise ToolError(f"zone.boxes[{k}] は [x0, y0, x1, y1]（整数、x0 < x1、y0 < y1）にしてください: {box!r}")
    checked = {"boxes": boxes, "threshold": 0}
    for key, required_positive in (("threshold", False), ("metres_per_pixel", True)):
        if value.get(key) is None:
            continue
        v = value[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or (required_positive and v == 0):
            raise ToolError(f"zone.{key} は {'正' if required_positive else '0 以上'}の数にしてください: {v!r}")
        checked[key] = v
    return checked


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
                  "違うコマも並べる。view で [左 | 右 | 差 × amplify] の PNG。zone（数える範囲の枠と差のしきい）で、変わった画素を"
                  "範囲の内と外に分けて数える（外が 0 でなければはみ出し。枠は mm でも）。しきい値（コマごとの max_differing_pixels・"
                  "max_channel_diff・max_mean_abs・max_outside_pixels）を渡したときだけ合否を出す。コマは隠れた入力なので再利用しない")
def check_frames(inputs, parameters, outputs, context):
    from . import number
    unknown = set(parameters) - FRAMES_PARAMETERS
    if unknown:
        raise ToolError(f"check.frames の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(FRAMES_PARAMETERS)}）")
    sides, sources = frame_sides(inputs, parameters, context.recipe_dir)
    thresholds = {key: number(parameters, key, minimum=0) for key in checks.FRAME_THRESHOLDS if parameters.get(key) is not None}
    top = number(parameters, "top", 5, minimum=1, integer=True)
    amplify = number(parameters, "amplify", 8, minimum=0, exclusive=True)
    zone = frame_zone(parameters["zone"]) if parameters.get("zone") is not None else None
    if "max_outside_pixels" in thresholds and zone is None:
        raise ToolError("max_outside_pixels（数える範囲の外の数の上限）は zone と一緒に使ってください")
    report = checks.compare_frames(sides["left"], sides["right"], thresholds, top, zone=zone)
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


BLEND_MATCH_PARAMETERS = {"frames", "pose_frames", "evaluate", "attributes", "samples", "objects", "armature", "tolerance",
                          "blender", "timeout", "strict", "report_only"}
SNAPSHOT_SCRIPT = __import__("pathlib").Path(__file__).resolve().parents[1] / "blender_adapter" / "snapshot_blend.py"


def _int_list(value, key):
    if not isinstance(value, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise ToolError(f"{key} はコマの番号（整数）の並びにしてください: {value!r}")
    return value


@register("check.blend_match", inputs=("left", "right"), outputs=("report",), cacheable=False,
          summary="2 つの .blend を比べる: 物体の有無・シーン・メッシュごとの頂点（物体の中の座標）・面のつなぎ・uv・属性・置き場所、"
                  "骨の名前・静止・姿勢（pose_frames。all は全部のコマ）、evaluate なら frames の変形した世界の形（sha256 と等間隔の頂点）。"
                  "Blender で中身を書き出し、numpy で比べる。全部が同じかを exact に、違いの大きさを max_* に出す。tolerance（coordinate・"
                  "matrix・value）を渡せばその中かで、渡さなければ全部が同じかで合否を出す。Blender は外部の実行ファイルなので再利用しない")
def check_blend_match(inputs, parameters, outputs, context):
    from pathlib import Path
    from .. import blend_snapshot
    from ..blender_adapter import launcher
    from . import number
    from .motion_ops import _blender, _timeout
    require(inputs, ("left", "right"), "check.blend_match")
    unknown = set(parameters) - BLEND_MATCH_PARAMETERS
    if unknown:
        raise ToolError(f"check.blend_match の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(BLEND_MATCH_PARAMETERS)}）")
    frames = _int_list(parameters.get("frames", []), "frames")
    pose_frames = parameters.get("pose_frames")
    if pose_frames is not None and pose_frames != "all":
        pose_frames = _int_list(pose_frames, "pose_frames")
    evaluate = parameters.get("evaluate", False)
    if not isinstance(evaluate, bool):
        raise ToolError(f"evaluate は true か false にしてください: {evaluate!r}")
    if evaluate and not frames:
        raise ToolError("evaluate には、変形した形を測るコマ frames が要ります")
    attributes = parameters.get("attributes", "all")
    if attributes != "all" and (not isinstance(attributes, list) or not all(isinstance(a, str) for a in attributes)):
        raise ToolError(f"attributes は属性の名前の並びか \"all\" にしてください: {attributes!r}")
    objects = parameters.get("objects")
    if objects is not None and (not isinstance(objects, list) or not all(isinstance(o, str) for o in objects)):
        raise ToolError(f"objects はメッシュの名前の並びにしてください: {objects!r}")
    armature = parameters.get("armature")
    if armature is not None and (not isinstance(armature, str) or not armature):
        raise ToolError(f"armature はアーマチュアの名前にしてください: {armature!r}")
    tolerance = blend_snapshot.check_tolerance(parameters.get("tolerance"))
    samples = number(parameters, "samples", 512, minimum=1, integer=True)
    blender = _blender(parameters, context, "check.blend_match")
    settings = {"frames": frames, "pose_frames": pose_frames, "evaluate": evaluate, "attributes": attributes, "samples": samples,
                "objects": objects, "armature": armature}
    settings_path = Path(context.step_dir) / "snapshot_settings.json"
    atomic_json(settings_path, settings)
    snapshots, records = {}, {}
    for side in ("left", "right"):
        npz, meta = blend_snapshot.snapshot_paths(context.step_dir, side)
        records[side] = launcher.run_blender(blender, SNAPSHOT_SCRIPT, None,
                                             ["--blend", inputs[side], "--out", str(npz), "--meta", str(meta), "--settings", str(settings_path)],
                                             log=Path(context.step_dir) / f"blender_{side}.log", timeout=_timeout(parameters))
        snapshots[side] = blend_snapshot.load(npz, meta)
    report = blend_snapshot.compare(snapshots["left"], snapshots["right"], tolerance, requested=objects,
                                    requested_attributes=attributes if isinstance(attributes, list) else None)
    report.update(left=str(inputs["left"]), right=str(inputs["right"]), frames=frames, settings=settings,
                  blender_version=records["left"]["blender_version"])
    summary = {k: v for k, v in report.items() if k not in ("objects", "evaluated_meshes", "pose_frames")}
    summary["objects"] = len(report["objects"])
    summary["evaluated_meshes"] = len(report["evaluated_meshes"])
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    return _finish(summary, {k: v for k, v in outputs.items() if k != "report"}, parameters, ".blend の比べ")
