"""VRM のキャラを model3d の工程で動かす（Blender 5.2 と VRM Add-on。docs/model3d.md の「VRM のキャラを動かす」）。

どの工程も Blender の中で VRM Add-on を使うので、既定で bl_ext.user_default.vrm を有効にして起動する（addons で変えられる。
拡張機能のフォルダは blender_extensions か --tool blender_extensions=DIR）。Blender は外部の実行ファイルなので再利用しない（毎回実行）。
"""
from pathlib import Path

from ..io import ToolError, read_json
from . import StepResult, number, register, require
from .blender_ops import blender_addons
from .motion_ops import _blender, _bone_prefix, _timeout

VRM_ADDON = "bl_ext.user_default.vrm"
MOTION_RIG_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "vrm_motion_rig.py"
MOTION_RIG_PARAMETERS = {"blender", "addons", "blender_extensions", "bone_prefix", "armature", "skip_meshes", "timeout"}


def _names(parameters, key):
    value = parameters.get(key, [])
    if not isinstance(value, list) or not all(isinstance(name, str) and name.strip() and "," not in name for name in value) \
            or len(set(value)) != len(value):
        raise ToolError(f"{key} は名前（空でない・カンマを含まない・重ならない文字列）の並びにしてください: {value!r}")
    return value


def _check(parameters, allowed, outputs, required, operation):
    """工程に共通の確かめ（知らないパラメーター・要る出力・armature の型）。armature（空なら ""）を返す。"""
    unknown = set(parameters) - allowed
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(allowed)}）")
    missing = [name for name in required if name not in outputs]
    if missing:
        raise ToolError(f"{operation} には outputs の {', '.join(missing)} が要ります")
    armature = parameters.get("armature", "")
    if not isinstance(armature, str):
        raise ToolError(f"armature はアーマチュアの名前（文字列）にしてください: {armature!r}")
    return armature


@register("vrm.motion_rig", inputs=("character",), outputs=("blend", "bone_map", "report", "skip"), cacheable=False,
          summary="VRM のキャラの .blend（VRM Add-on の人型の骨の割り当て入り）から、骨の名前を Mixamo 式（bone_prefix）に付け替えた"
                  "作業コピー（blend）と、元の名前 → 付け替えた名前の対応表（bone_map）を作る（Blender の中で）。付け替えるのは "
                  "model3d が役割を知っている骨だけで、目・あごは付け替えない。motion.retarget が要る骨が無ければ止まる。"
                  "skip_meshes を挙げると、motion.retarget の skip_vertices に渡す npz（skip）も書く。元のファイルは変えない")
def motion_rig(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    require(inputs, ("character",), "vrm.motion_rig")
    armature = _check(parameters, MOTION_RIG_PARAMETERS, outputs, ("blend", "bone_map", "report"), "vrm.motion_rig")
    skip_meshes = _names(parameters, "skip_meshes")
    if bool(skip_meshes) != ("skip" in outputs):
        raise ToolError("vrm.motion_rig: skip_meshes と outputs の skip は一緒に書いてください")
    blender = _blender(parameters, context, "vrm.motion_rig")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    args = ["--character", inputs["character"], "--out-blend", outputs["blend"], "--bone-map", outputs["bone_map"],
            "--report", outputs["report"], "--bone-prefix", _bone_prefix(parameters)]
    if armature:
        args += ["--armature", armature]
    if skip_meshes:
        args += ["--skip-meshes", ",".join(skip_meshes), "--skip", outputs["skip"]]
    record = launcher.run_blender(blender, MOTION_RIG_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "vrm_motion_rig", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "mapped": report.get("mapped"), "renamed": report.get("renamed"), "unmapped": sorted(report.get("unmapped") or {}),
                   "removed_scenes": report.get("removed_scenes")}
    return StepResult(observations=[observation])


APPLY_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "vrm_apply_motion.py"
APPLY_PARAMETERS = {"blender", "addons", "blender_extensions", "fps", "armature", "timeout"}


@register("vrm.apply_motion", inputs=("character", "motion", "bone_map"), outputs=("blend", "report"), cacheable=False,
          summary="作業コピー（vrm.motion_rig）で付けた動き（motion の .blend。motion.retarget などの出力）を、元の VRM のキャラの "
                  ".blend（character）へ戻す（Blender の中で）。骨の名前は bone_map の逆で元へ戻し、キャラに無い骨の動きがあれば止まる。"
                  "コマの範囲は動きのキーの範囲、fps は motion の .blend のシーン（fps で上書き可）。VRM の設定と材質はキャラのまま")
def apply_motion(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    require(inputs, ("character", "motion", "bone_map"), "vrm.apply_motion")
    armature = _check(parameters, APPLY_PARAMETERS, outputs, ("blend", "report"), "vrm.apply_motion")
    fps = None if parameters.get("fps") is None else number(parameters, "fps", minimum=0, exclusive=True)
    blender = _blender(parameters, context, "vrm.apply_motion")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    args = ["--character", inputs["character"], "--motion", inputs["motion"], "--bone-map", inputs["bone_map"],
            "--out-blend", outputs["blend"], "--report", outputs["report"]]
    if fps is not None:
        args += ["--fps", repr(float(fps))]
    if armature:
        args += ["--armature", armature]
    record = launcher.run_blender(blender, APPLY_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "vrm_apply_motion", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "curves": report.get("curves"), "curves_renamed": report.get("curves_renamed"), "frames": report.get("frames"),
                   "fps": report.get("fps")}
    return StepResult(observations=[observation])


BAKE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "vrm_bake_springs.py"
BAKE_PARAMETERS = {"blender", "addons", "blender_extensions", "settle_s", "rate", "no_spring", "armature", "timeout", "cuts",
                   "cut_settle_s", "carry", "carry_bone", "carry_skip"}
JOIN_SCHEMA = "model3d-motion-join/0.1"


def _carry(parameters):
    """運ぶ区間（[最初, 最後] の並び。両端を含む）を「a:b,c:d」にする。運ぶ骨と運ばない揺れものは、区間と一緒のときだけ。"""
    spans = parameters.get("carry", [])
    if not isinstance(spans, list) or not all(
            isinstance(span, list) and len(span) == 2 and all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in span)
            and span[0] <= span[1] for span in spans):
        raise ToolError(f"carry は運ぶ区間 [最初, 最後]（Blender のコマ番号。1 以上の整数、最初 ≤ 最後）の並びにしてください: {spans!r}")
    bone = parameters.get("carry_bone", "")
    if not isinstance(bone, str):
        raise ToolError(f"carry_bone は骨の名前（文字列）にしてください: {bone!r}")
    skip = _names(parameters, "carry_skip")
    if (bone or skip) and not spans:
        raise ToolError("vrm.bake_springs: carry_bone・carry_skip は carry（運ぶ区間）と一緒に使ってください")
    return ",".join(f"{first}:{last}" for first, last in spans), bone, skip


def _cuts(parameters, inputs):
    """切り替えのコマ: パラメーターの cuts か、入力 joined（motion.join_clips の report）の cuts。両方は使えない。
    → (切り替えのコマ, joined の組んだ動きのコマの範囲 [最初, 最後]。joined でなければ None)"""
    cuts, frames = parameters.get("cuts"), None
    if "joined" in inputs:
        if cuts is not None:
            raise ToolError("vrm.bake_springs: 切り替えのコマは、パラメーターの cuts か入力の joined のどちらかで渡してください（両方は使えない）")
        report = read_json(inputs["joined"])
        if not isinstance(report, dict) or report.get("schema_version") != JOIN_SCHEMA:
            raise ToolError(f"vrm.bake_springs の joined は motion.join_clips の report（{JOIN_SCHEMA}）にしてください")
        cuts, frames, where = report.get("cuts"), report.get("frames"), "joined の cuts"
        if not (isinstance(frames, list) and len(frames) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in frames)
                and frames[0] <= frames[1]):
            raise ToolError(f"vrm.bake_springs の joined の frames（組んだ動きのコマの範囲 [最初, 最後]）が読めません: {frames!r}")
    else:
        cuts, where = ([] if cuts is None else cuts), "cuts"
    if (not isinstance(cuts, list) or not all(isinstance(cut, int) and not isinstance(cut, bool) and cut >= 1 for cut in cuts)
            or len(set(cuts)) != len(cuts)):
        raise ToolError(f"{where} は切り替えのコマ（Blender のコマ番号。1 以上の整数で、重ならない）の並びにしてください: {cuts!r}")
    return sorted(cuts), frames


@register("vrm.bake_springs", inputs=("blend", "overrides", "joined"), outputs=("blend", "report"), cacheable=False,
          summary="動きを付けた VRM のキャラ（blend）の揺れもの（VRM 1.0 のばね）を、VRM Add-on の計算を 1/rate 秒（既定 1/60）刻みで"
                  "進めて、コマごとの骨の回転のキーに焼く（Blender の中で）。最初のコマで settle_s 秒（既定 2）落ち着かせる。"
                  "切り替えのコマ（cuts か、joined＝motion.join_clips の report の cuts）では、揺れものを初期に戻し、そのコマの姿勢で"
                  "cut_settle_s 秒（既定は settle_s と同じ）落ち着かせ直す。carry の区間では、揺れものの節の位置を carry_bone（既定は人型の"
                  "hips）の動きで運び、急な動きの勢いを伝えない（carry_skip の揺れものは運ばない）。overrides（JSON）で硬さ・重力・当たり判定を"
                  "この .blend の中だけ上書きし（_release_walls で、姿勢に応じて壁の球を小さくし、その間だけ見張りの当たりを立てる。VRM には"
                  "入らない）、no_spring に挙げた文字を名前に含む揺れものは動かさない。元のファイルは変えない")
def bake_springs(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher, vrm_bake_springs
    require(inputs, ("blend",), "vrm.bake_springs")
    armature = _check(parameters, BAKE_PARAMETERS, outputs, ("blend", "report"), "vrm.bake_springs")
    settle = number(parameters, "settle_s", 2.0, minimum=0)
    rate = number(parameters, "rate", 60.0, minimum=0, exclusive=True)
    no_spring = _names(parameters, "no_spring")
    cuts, joined_frames = _cuts(parameters, inputs)
    cut_settle = number(parameters, "cut_settle_s", settle, minimum=0)
    if parameters.get("cut_settle_s") is not None and not cuts and "joined" not in inputs:
        raise ToolError("vrm.bake_springs: cut_settle_s は切り替えのコマ（cuts か joined）と一緒に使ってください")
    carry, carry_bone, carry_skip = _carry(parameters)
    if "overrides" in inputs:
        try:
            vrm_bake_springs.check_overrides(read_json(inputs["overrides"]))
        except ValueError as error:
            raise ToolError(f"vrm.bake_springs の overrides: {error}") from error
    blender = _blender(parameters, context, "vrm.bake_springs")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    # 揺れものの計算を読むアドオン（挙げたもののうち、名前の最後が vrm のもの）
    module = next((name for name in addons if name.rsplit(".", 1)[-1] == "vrm"), VRM_ADDON)
    args = ["--blend", inputs["blend"], "--out-blend", outputs["blend"], "--report", outputs["report"],
            "--settle", repr(float(settle)), "--rate", repr(float(rate)), "--addon-module", module]
    if "overrides" in inputs:
        args += ["--overrides", inputs["overrides"]]
    if no_spring:
        args += ["--no-spring", ",".join(no_spring)]
    if armature:
        args += ["--armature", armature]
    if cuts:
        args += ["--cuts", ",".join(str(cut) for cut in cuts), "--cut-settle", repr(float(cut_settle))]
    if joined_frames is not None:                         # 組んだ動きの報告と、焼く .blend のコマの範囲が同じかを台本が確かめる
        args += ["--joined-frames", f"{joined_frames[0]}:{joined_frames[1]}"]
    if carry:
        args += ["--carry", carry]
        if carry_bone:
            args += ["--carry-bone", carry_bone]
        if carry_skip:
            args += ["--carry-skip", ",".join(carry_skip)]
    record = launcher.run_blender(blender, BAKE_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "vrm_bake_springs", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "frames": report.get("frames"), "joints": report.get("joints"), "still_joints": report.get("still_joints"),
                   "steps": report.get("steps"), "overridden": sorted(report.get("overridden") or {})}
    if cuts:
        observation["cuts"] = report.get("cuts")
    if carry:
        observation["carried_frames"] = report.get("carried_frames")
        observation["carry_bone"] = report.get("carry_bone")
    if report.get("released_walls"):
        observation["released_walls"] = [{key: item.get(key) for key in ("springs", "walls", "guards", "frames_released", "spans")}
                                         for item in report["released_walls"]]
    return StepResult(observations=[observation])


EXPRESSIONS_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "vrm_expressions.py"
EXPRESSIONS_PARAMETERS = {"blender", "addons", "blender_extensions", "object", "binds", "armature", "timeout"}


def expression_binds(binds):
    """vrm.expressions の binds（{VRM の表情の名前: {形状キー: 重み}}）を確かめる。名前は VRM 1.0 の決まった表情だけ、重みは 0〜1。"""
    import math
    from ..blender_adapter.vrm_expressions import PRESETS
    if not isinstance(binds, dict) or not binds:
        raise ToolError(f"binds は {{VRM の表情の名前: {{形状キー: 重み}}}} にしてください: {binds!r}")
    checked = {}
    for name, values in binds.items():
        if name not in PRESETS:
            raise ToolError(f"binds の表情 {name!r} は VRM 1.0 の決まった表情ではありません（使えるのは {sorted(PRESETS)}）")
        if not isinstance(values, dict):
            raise ToolError(f"binds.{name} は {{形状キー: 重み}}（空の {{}} は割り当てを外す）にしてください: {values!r}")
        for key, weight in values.items():
            if not isinstance(key, str) or not key:
                raise ToolError(f"binds.{name} の形状キーの名前は空でない文字列にしてください: {key!r}")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 0 <= weight <= 1:
                raise ToolError(f"binds.{name}.{key} の重みは 0〜1 の数にしてください: {weight!r}")
        checked[name] = {key: float(weight) for key, weight in values.items()}
    return checked


@register("vrm.expressions", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="VRM の表情（VRM 1.0 の決まった表情。happy・blink・blinkLeft など）に、物体（object）の形状キーを割り当てる（Blender の中で"
                  " VRM Add-on で）: binds の表情ごとに今の割り当てを外し、{形状キー: 重み} を挙げた順に割り当てる（空の {} は外すだけ）。"
                  "挙げない表情は変えない。元のファイルは変えない")
def expressions(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import atomic_json
    require(inputs, ("blend",), "vrm.expressions")
    armature = _check(parameters, EXPRESSIONS_PARAMETERS, outputs, ("blend", "report"), "vrm.expressions")
    obj = parameters.get("object")
    if not isinstance(obj, str) or not obj:
        raise ToolError(f"object は形状キーを持つメッシュの名前（空でない文字列）にしてください: {obj!r}")
    settings = {"object": obj, "binds": expression_binds(parameters.get("binds")), "armature": armature}
    blender = _blender(parameters, context, "vrm.expressions")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    atomic_json(step_dir / "vrm_expressions_settings.json", settings)
    record = launcher.run_blender(blender, EXPRESSIONS_SCRIPT, None, ["--blend", inputs["blend"], "--settings",
                                                                      step_dir / "vrm_expressions_settings.json", "--out-blend",
                                                                      outputs["blend"], "--report", outputs["report"]],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "vrm_expressions", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "object": report.get("object"),
                   "changed": report.get("changed"), "bound": report.get("bound"), "name_only": report.get("name_only")}
    return StepResult(observations=[observation])


MATERIAL_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "vrm_materials_mtoon.py"
MATERIAL_PARAMETERS = {"materials", "alpha_modes", "merge_materials", "merge_images", "shade_from_base",
                       "reference_material", "base_color_factors", "shade_color_factors",
                       "blender", "addons", "blender_extensions", "timeout"}


def mtoon_material_settings(parameters):
    materials = _names(parameters, "materials")
    settings = {"materials": materials, "shade_from_base": parameters.get("shade_from_base", False)}
    if not isinstance(settings["shade_from_base"], bool):
        raise ToolError("shade_from_base は true か false にしてください")
    reference = parameters.get("reference_material")
    if reference is not None and (not materials or not isinstance(reference, str) or not reference.strip()):
        raise ToolError("reference_material は変換する materials と一緒に、手本の材質名を指定してください")
    settings["reference_material"] = reference
    for key, length in (("base_color_factors", 4), ("shade_color_factors", 3)):
        factors = parameters.get(key, {})
        if not isinstance(factors, dict) or factors.keys() - set(materials):
            raise ToolError(f"{key} には materials の材質名を指定してください")
        for components in factors.values():
            if (not isinstance(components, list) or len(components) != length
                    or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in components)):
                raise ToolError(f"{key} の値は 0〜1 の数値 {length} 個にしてください")
        settings[key] = factors
    for key in ("alpha_modes", "merge_materials", "merge_images"):
        mapping = parameters.get(key, {})
        if not isinstance(mapping, dict) or any(not isinstance(name, str) or not name.strip()
                                               for pair in mapping.items() for name in pair):
            raise ToolError(f"{key} は空でない名前同士の対応を書いてください")
        if key == "alpha_modes":
            if set(mapping.values()) - {"OPAQUE", "MASK", "BLEND"}:
                raise ToolError("alpha_modes の値は OPAQUE、MASK、BLEND のいずれかにしてください")
        elif mapping.keys() & mapping.values():
            raise ToolError(f"{key} の統合元と統合先は別の名前にしてください（連鎖も不可）")
        settings[key] = mapping
    if not any(settings[key] for key in ("materials", "alpha_modes", "merge_materials", "merge_images")):
        raise ToolError("変換、透過の変更、または重複の統合を指定してください")
    if settings["shade_from_base"] and not materials:
        raise ToolError("shade_from_base は変換する materials と一緒に指定してください")
    return settings


@register("vrm.materials_mtoon", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="指定した Principled 材質を VRM Add-on で MToon に変換し、指定した材質の透過型を変える。"
                  "名前で示した材質や画像の対は、設定・つなぎ方や画素が同じことを確かめてから統合する。元の .blend は変えない")
def materials_mtoon(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import atomic_json
    operation = "vrm.materials_mtoon"
    require(inputs, ("blend",), operation)
    _check(parameters, MATERIAL_PARAMETERS, outputs, ("blend", "report"), operation)
    settings = mtoon_material_settings(parameters)
    blender = _blender(parameters, context, operation)
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "vrm_material_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", settings_path,
            "--out-blend", outputs["blend"], "--report", outputs["report"]]
    record = launcher.run_blender(blender, MATERIAL_SCRIPT, None, args, log=step_dir / "blender.log",
                                  timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    return StepResult(observations=[{"kind": "vrm_materials_mtoon", "blender_version": record["blender_version"],
                                     "addons": record.get("addons"), "converted": len(report["converted"]),
                                     "alpha_changed": len(report["alpha_changed"]),
                                     "materials_merged": len(report["materials_merged"]),
                                     "images_merged": len(report["images_merged"])}])


EXPORT_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "vrm_export.py"
EXPORT_PARAMETERS = {"blender", "addons", "blender_extensions", "jpeg_quality", "armature", "strict", "timeout"}


@register("vrm.export", inputs=("blend", "meta_from"), outputs=("vrm", "blend", "report", "check"), cacheable=False,
          summary="VRM のキャラの .blend（blend）を、骨を基本の姿勢に戻して VRM 1.0 として書き出す（Blender の中で VRM Add-on で）。"
                  "meta_from（元の VRM）があれば、メタの名前・版・作者を写す（.blend に残らないことがあるため）。jpeg_quality（1〜100）で"
                  "材質の画像を JPEG にして埋め込み直す。書き出した VRM は check.vrm と同じ検査にかけ、check に残す。元のファイルは変えない")
def export(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..blender_adapter.vrm_export import left_out
    from ..io import atomic_json
    from .. import vrm
    require(inputs, ("blend",), "vrm.export")
    armature = _check(parameters, EXPORT_PARAMETERS, outputs, ("vrm", "blend", "report"), "vrm.export")
    quality = number(parameters, "jpeg_quality", 0, minimum=0, integer=True)
    if quality > 100:
        raise ToolError(f"jpeg_quality は 0〜100 にしてください: {quality}")
    blender = _blender(parameters, context, "vrm.export")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    args = ["--blend", inputs["blend"], "--out-blend", outputs["blend"], "--out-vrm", outputs["vrm"], "--report", outputs["report"]]
    if "meta_from" in inputs:
        meta = vrm.meta_of(Path(inputs["meta_from"]).read_bytes(), str(inputs["meta_from"]))
        atomic_json(step_dir / "meta.json", meta)
        args += ["--meta", step_dir / "meta.json"]
    if quality:
        args += ["--jpeg-quality", str(quality), "--textures", step_dir / "textures"]
    if armature:
        args += ["--armature", armature]
    record = launcher.run_blender(blender, EXPORT_SCRIPT, None, args, log=step_dir / "blender.log",
                                  timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    checked = vrm.inspect_file(outputs["vrm"])
    if "check" in outputs:
        atomic_json(outputs["check"], checked)
    observation = {"kind": "vrm_export", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "addons": record.get("addons"), "armature": report.get("armature"),
                   "vrm_bytes": report.get("vrm_bytes"), "meta_copied": report.get("meta_copied"),
                   "textures": len(report.get("textures") or []), "check_pass": checked["pass"],
                   "human_bones": checked["human_bones"], "springs": len(checked["springs"]),
                   "alpha_dropped": report.get("alpha_dropped", [])}
    # 骨の拘束: 書き出した数と、.blend の骨の拘束のうち書き出されなかったもの（合否には使わない。IK など VRM に要らない拘束もある）
    exported = (checked.get("structure") or {}).get("node_constraints") or []
    observation["constraints_exported"] = len(exported)
    not_exported, unmatched = left_out(report.get("bone_constraints") or [], exported)
    observation["constraints_not_exported"] = not_exported
    observation["constraints_unmatched"] = unmatched      # VRM にあって .blend に同じ形・相手・重みの拘束が見つからないもの（ふつうは無い）
    # 透過を使う材質の絵を JPEG にしたもの（透過が消える）は、検査の不合格と同じく要確認にする
    problems = [f"VRM の検査: {f}" for f in checked["failures"]]
    problems += [f"透過を使う材質の絵を JPEG にしました（透過が消える）: {name}" for name in observation["alpha_dropped"]]
    if not problems:
        return StepResult(observations=[observation])
    if parameters.get("strict"):
        raise ToolError("vrm.export: " + "; ".join(problems))
    return StepResult(observations=[observation], unresolved=problems, review=True)


@register("check.vrm", inputs=("vrm",), outputs=("report",),
          summary="VRM 1.0 のファイルの中身を読む（Blender なし）: メタ・人型の骨の数と必須の骨・メッシュ・材質（MToon の数）・画像・"
                  "揺れもの・当たり判定・表情の割り当て（メッシュごとの形の名前と、表情ごとの形と重み）。作りの一覧 structure（primitive ごとの"
                  "頂点と材質・骨の拘束・人型でない関節・ねじれらしい骨・揺れものの鎖ごとの値・当たり判定・材質の色の掛け率と MToon の値・画像の"
                  "大きさ・視線・メッシュごとの骨の重み）。合否は、VRM 1.0 の拡張があるか・人型の必須の骨・メタの名前と作者とライセンスの URL・"
                  "たどった参照の番号が範囲の中か。見た目は判定しない。不合格は needs_review（strict なら失敗、report_only なら測るだけ）")
def check_vrm(inputs, parameters, outputs, context):
    from .. import vrm
    from .check_ops import _finish
    require(inputs, ("vrm",), "check.vrm")
    unknown = set(parameters) - {"strict", "report_only"}
    if unknown:
        raise ToolError(f"check.vrm の知らないパラメーター: {sorted(unknown)}（使えるのは report_only・strict）")
    return _finish(vrm.inspect_file(inputs["vrm"]), outputs, parameters, "VRM の検査")


@register("check.vrm_roll", inputs=("vrm",), outputs=("report",),
          summary="VRM 1.0 の roll 拘束を GLB の基本姿勢から検算する（Blender なし）。"
                  "元 node の軸まわりの回転と倒しを組み合わせ、重みを掛けたねじれからの最大のずれと、倒しただけのねじれを測る",
          capability={"aliases": ["VRM roll 拘束", "ねじれの検算"],
                      "network_mode": "never", "external_process_mode": "never",
                      "parameters": {"status": "docs", "schema": None}})
def check_vrm_roll(inputs, parameters, outputs, context):
    from .. import vrm_roll
    from .check_ops import _finish
    operation = "check.vrm_roll"
    require(inputs, ("vrm",), operation)
    allowed = {"twists_deg", "swings_deg", "tolerance_deg", "strict", "report_only"}
    _check(parameters, allowed, outputs, (), operation)
    for key in ("strict", "report_only"):
        if key in parameters and type(parameters[key]) is not bool:
            raise ToolError(f"{key} は true または false にしてください")
    return _finish(vrm_roll.inspect_file(inputs["vrm"], parameters), outputs, parameters, "VRM の roll 検算")


@register("vrm.avoid_hair_arm", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="腕や袖と交差する長髪をよけ、先の骨を重力方向へ戻す補正を焼く。"
                  "評価済みメッシュの世界座標で調べ、平滑化と焼き付けの後の交差も全コマで数える",
          capability={"aliases": ["長髪と腕や袖の衝突回避", "hair arm sleeve collision"],
                      "docs": "docs/model3d-hair-arm.md", "parameters": {"status": "docs", "schema": None}})
def avoid_hair_arm(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher, vrm_avoid_hair_arm
    from ..io import atomic_json

    operation = "vrm.avoid_hair_arm"
    controls = {"blender", "addons", "blender_extensions", "armature", "timeout"}
    require(inputs, ("blend",), operation)
    armature = _check(parameters, controls | vrm_avoid_hair_arm.SETTINGS, outputs, ("blend", "report"), operation)
    settings = {key: value for key, value in parameters.items() if key not in controls}
    try:
        vrm_avoid_hair_arm.check_settings(settings)
    except ValueError as error:
        raise ToolError(f"{operation}: {error}") from error
    blender = _blender(parameters, context, operation)
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "hair_arm_settings.json"
    atomic_json(settings_path, {**settings, "armature": armature})
    script = Path(vrm_avoid_hair_arm.__file__).resolve()
    args = ["--blend", inputs["blend"], "--settings", settings_path, "--out-blend", outputs["blend"], "--report", outputs["report"]]
    record = launcher.run_blender(blender, script, None, args, log=step_dir / "blender.log", timeout=_timeout(parameters),
                                  addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    if not isinstance(report, dict) or report.get("schema_version") != vrm_avoid_hair_arm.SCHEMA:
        raise ToolError(f"{operation} の報告の形式が違います")
    fields = ("armature", "frames", "before_pairs", "after_pairs")
    observation = {"kind": "vrm_avoid_hair_arm", "blender_version": record["blender_version"], "seconds": record["seconds"],
                   **{key: report[key] for key in fields}}
    remaining = report["after_pairs"]
    unresolved = [f"{operation}: 交差する三角形の組が補正前の延べ {report['before_pairs']} 件から {remaining} 件になりました"] if remaining else []
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))
