"""見た目の準備: 骨へ移した動きのキャラに、描く前のセル調の塗りと輪郭線を付ける（Blender の中で）。"""
from pathlib import Path

from ..io import ToolError, atomic_json
from . import StepResult, number, register, require

LOOK_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "toon_look.py"
LOOK_PARAMETERS = {"blender", "meshes", "faces", "toon", "face", "outline", "timeout"}
TOON_KEYS = {"light", "threshold", "soft", "shade", "skin_shade", "tint", "lit", "rim_from", "rim_to", "rim_colors", "rim_gain",
             "face_attribute", "weak_rim", "inside_attribute", "inside_color"}
FACE_KEYS = {"lit", "emit"}
OUTLINE_KEYS = {"enabled", "meshes", "ratio", "dark", "saturation", "width_mm", "hand_attribute", "hand_line"}


def _names(value, key, allow_none=True):
    if value is None and allow_none:
        return None
    if not isinstance(value, list) or not all(isinstance(n, str) and n for n in value) or len(set(value)) != len(value):
        raise ToolError(f"{key} はメッシュの名前（空でない・重ならない文字列）の配列にしてください: {value!r}")
    return value


def _vector(value, key, size=3, lower=None, upper=None, nonzero=False):
    if (not isinstance(value, list) or len(value) != size
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)):
        raise ToolError(f"{key} は {size} 個の数の配列にしてください: {value!r}")
    if (lower is not None and min(value) < lower) or (upper is not None and max(value) > upper):
        raise ToolError(f"{key} の数は {lower}〜{upper} にしてください: {value!r}")
    if nonzero and not any(value):
        raise ToolError(f"{key} は 0 でない向きにしてください: {value!r}")
    return [float(x) for x in value]


def _attribute(value, key):
    if value is not None and not (isinstance(value, str) and value):
        raise ToolError(f"{key} は頂点の属性の名前か null にしてください: {value!r}")
    return value


def _section(parameters, key, allowed):
    section = parameters.get(key, {})
    if not isinstance(section, dict):
        raise ToolError(f"{key} は辞書にしてください: {section!r}")
    unknown = set(section) - allowed
    if unknown:
        raise ToolError(f"{key} の知らない鍵: {sorted(unknown)}（使えるのは {sorted(allowed)}）")
    return section


def _look_settings(parameters):
    meshes = _names(parameters.get("meshes"), "meshes")
    faces = _names(parameters.get("faces", []), "faces", allow_none=False)
    if meshes is not None and set(meshes) & set(faces):
        raise ToolError(f"同じメッシュを meshes と faces の両方に入れています: {sorted(set(meshes) & set(faces))}")
    toon = _section(parameters, "toon", TOON_KEYS)
    t = {}
    if "light" in toon:
        t["light"] = _vector(toon["light"], "toon.light", nonzero=True)
    for k in ("shade", "inside_color"):
        if k in toon:
            t[k] = _vector(toon[k], "toon." + k, lower=0, upper=1)
    if "skin_shade" in toon:
        t["skin_shade"] = None if toon["skin_shade"] is None else _vector(toon["skin_shade"], "toon.skin_shade", lower=0, upper=1)
    if "rim_colors" in toon:
        colors = toon["rim_colors"]
        if not isinstance(colors, list) or len(colors) != 2:
            raise ToolError(f"toon.rim_colors は左右 2 つの色の配列にしてください: {colors!r}")
        t["rim_colors"] = [_vector(c, "toon.rim_colors", lower=0) for c in colors]
    for k in ("threshold", "rim_from", "rim_to"):
        if k in toon:
            t[k] = float(number(toon, k))
    for k in ("soft", "lit", "rim_gain"):
        if k in toon:
            t[k] = float(number(toon, k, minimum=0))
    if "tint" in toon:
        t["tint"] = float(number(toon, "tint", minimum=0))
        if t["tint"] > 1:
            raise ToolError(f"toon.tint は 0〜1 にしてください: {t['tint']!r}")
    if t.get("rim_from", 0.60) >= t.get("rim_to", 0.72):
        raise ToolError("toon.rim_from は rim_to より小さくしてください")
    for k in ("face_attribute", "inside_attribute"):
        if k in toon:
            t[k] = _attribute(toon[k], "toon." + k)
    if "weak_rim" in toon:
        weak = toon["weak_rim"]
        if not isinstance(weak, list) or not all(isinstance(w, dict) and set(w) == {"attribute", "scale"} for w in weak):
            raise ToolError(f"toon.weak_rim は {{attribute: 属性, scale: 0〜1}} の配列にしてください: {weak!r}")
        t["weak_rim"] = []
        for w in weak:
            scale = float(number(w, "scale", minimum=0))
            if scale > 1 or not (isinstance(w["attribute"], str) and w["attribute"]):
                raise ToolError(f"toon.weak_rim の各項目は {{attribute: 属性, scale: 0〜1}} にしてください: {w!r}")
            t["weak_rim"].append({"attribute": w["attribute"], "scale": scale})
    face = _section(parameters, "face", FACE_KEYS)
    f = {k: float(number(face, k, minimum=0)) for k in FACE_KEYS if k in face}
    outline = _section(parameters, "outline", OUTLINE_KEYS)
    o = {}
    if "enabled" in outline:
        if not isinstance(outline["enabled"], bool):
            raise ToolError(f"outline.enabled は true か false: {outline['enabled']!r}")
        o["enabled"] = outline["enabled"]
    if "meshes" in outline:
        o["meshes"] = _names(outline["meshes"], "outline.meshes")
    if "ratio" in outline:
        ratio = outline["ratio"]
        if not isinstance(ratio, dict) or not all(isinstance(k, str) and k and isinstance(v, (int, float))
                                                  and not isinstance(v, bool) and 0 < v <= 1 for k, v in ratio.items()):
            raise ToolError(f"outline.ratio は {{メッシュの名前: 残す面の割合（0 より大きく 1 以下）}} にしてください: {ratio!r}")
        o["ratio"] = {k: float(v) for k, v in ratio.items()}
    for k in ("dark", "saturation"):
        if k in outline:
            o[k] = float(number(outline, k, minimum=0))
    if "width_mm" in outline:
        o["width_mm"] = float(number(outline, "width_mm", minimum=0, exclusive=True))
    if "hand_attribute" in outline:
        o["hand_attribute"] = _attribute(outline["hand_attribute"], "outline.hand_attribute")
    if "hand_line" in outline:
        o["hand_line"] = float(number(outline, "hand_line", minimum=0))
        if o["hand_line"] > 1:
            raise ToolError(f"outline.hand_line は 0〜1 にしてください: {o['hand_line']!r}")
    return {"meshes": meshes, "faces": faces, "toon": t, "face": f, "outline": o}


@register("look.toon", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="骨へ移した動きのキャラ（blend）に、描く前のセル調の塗りと輪郭線を付ける（Blender の中で）。体は決まった向きの光で"
                  "影を 2 段に分け、影の色に舞台の色を混ぜ、縁の光を足して Emission で出す（舞台の光は受けない）。顔（faces）は"
                  "光を少し受けて絵の色を直接出す。輪郭線は、面を減らして裏返した写しを骨の変形の後に太らせる反転ハル。"
                  "舞台の色と縁の光の強さは共有のノードグループ model3d_stage_tone に置く（描く工程がコマごとに書き換える）。"
                  "Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def toon_look(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "look.toon")
    unknown = set(parameters) - LOOK_PARAMETERS
    if unknown:
        raise ToolError(f"look.toon の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(LOOK_PARAMETERS)}）")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"look.toon には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "look.toon")
    settings_path = Path(context.step_dir) / "look_settings.json"
    atomic_json(settings_path, _look_settings(parameters))
    args = ["--blend", inputs["blend"], "--out-blend", outputs["blend"], "--report", outputs["report"],
            "--settings", str(settings_path)]
    record = launcher.run_blender(blender, LOOK_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    hulls = report.get("outline_hulls") or []
    observation = {"kind": "toon_look", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "meshes": len(report.get("meshes") or []),
                   "toon_materials": len(report.get("toon_materials") or []),
                   "face_materials": len(report.get("face_materials") or []), "outline_hulls": len(hulls),
                   "outline_triangles": sum(h.get("triangles", 0) for h in hulls)}
    return StepResult(observations=[observation])
