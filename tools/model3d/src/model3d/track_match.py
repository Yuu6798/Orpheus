"""2 つの台本の値を、同じコマ T どうしで比べる（check.track_match）。変えたコマだけ描き直すとき、前の版のコマを写してよいのは、
台本がそのコマに渡す値（カメラ・光・塗り・動きのコマ）が前の版と同じコマだけ。どのコマが同じで、どのコマを描き直すかを数で出す。

リポジトリ外の 3 作目の VRM のダンスで、演出版の前半だけを作り直したとき、前の版と今の版の台本の値（カメラと光）が後半の全部のコマで
同じことを確かめてから、後半は前の版のコマを写した（全部のコマを描くより 22 分ほど短く済んだ）使い捨ての比べを部品にした。

- 比べるもの: コマの並びの記録 2 つ（model3d-cameras/0.1 か model3d-stage-track/0.1。左と右は同じ種類）。T ごとに、T のほかの欄を
  全部たどって（入れ子の辞書と並び。欄の名前は "camera.location[1]" の形）、数は差の大きさ、数でない値は同じかを見る。ignore の欄
  （名前の頭が一致する欄も）は見ない。
- 同じ: どの欄も、数は差が tolerance 以下（既定 0 = ちょうど同じ）、数でない値は同じで、欄の有無も同じ。
- 左は前の版（コマを写す元）、右は今の版（これから描く台本）。右にしか無い T（今の版で足したコマ）は描き直すコマに入れる。
  左にしか無い T（今の版で消えたコマ）は描かないので、描き直すコマには入れず、only_left に並べる。
- 記録の頭の欄（画面の大きさ・fps・受光など。notes と x_ の欄は見ない）が違えば、全部のコマを描き直すコマにする（コマの値の意味が変わる）。
"""
import math

from .io import ToolError

KINDS = ("cameras", "stage-track")


def flatten(value, prefix=""):
    """入れ子の値を {欄の名前: 値}（数・文字列・真偽・None）に。"""
    out = {}
    if isinstance(value, dict):
        for key in sorted(value):
            out.update(flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for k, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{k}]"))
    else:
        out[prefix] = value
    return out


def _ignored(name, ignore):
    return any(name == item or name.startswith(item + ".") or name.startswith(item + "[") for item in ignore)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def compare_frame(left, right, ignore, tolerance):
    """1 コマの比べ: (同じか, 欄ごとの数の差の最大 {名前: 差}, 違う欄の名前の並び)。"""
    a = {k: v for k, v in flatten({k: v for k, v in left.items() if k != "T"}).items() if not _ignored(k, ignore)}
    b = {k: v for k, v in flatten({k: v for k, v in right.items() if k != "T"}).items() if not _ignored(k, ignore)}
    diffs, differing = {}, []
    for name in sorted(set(a) | set(b)):
        if name not in a or name not in b:
            differing.append(name)
            continue
        x, y = a[name], b[name]
        if _number(x) and _number(y):
            d = abs(float(x) - float(y))
            if math.isnan(d):
                d = math.inf
            diffs[name] = d
            if d > tolerance:
                differing.append(name)
        elif x != y:
            differing.append(name)
    return not differing, diffs, differing


def ranges(Ts):
    """T の並びを、続いた範囲 [[最初, 最後], …] に。"""
    out = []
    for T in sorted(Ts):
        if out and T == out[-1][1] + 1:
            out[-1][1] = T
        else:
            out.append([T, T])
    return out


def _group(name):
    """欄の名前のまとまり（"matrix[0][3]" → "matrix"、"camera.location[1]" → "camera.location"）。"""
    return name.split("[", 1)[0]


def compare(left, right, ignore=(), tolerance=0.0, top=5):
    """2 つの記録（読んだ JSON。left は前の版、right は今の版）の比べ。報告の辞書。"""
    by_left = {fr["T"]: fr for fr in left["frames"]}
    by_right = {fr["T"]: fr for fr in right["frames"]}
    for side, frames, mapping in (("左", left["frames"], by_left), ("右", right["frames"], by_right)):
        if len(mapping) != len(frames):
            raise ToolError(f"check.track_match: {side}の記録の T が重なっています")
    # 記録の頭の欄（画面の大きさ・fps・受光など）が違えば、コマの値が同じでも意味が変わる（同じ焦点距離の画素でも画角が違う）ので、
    # 全部のコマを描き直すコマにする（notes と x_ の欄は見ない）
    head = {k: v for k, v in left.items() if k != "frames" and k != "notes" and not k.startswith("x_")}
    head_right = {k: v for k, v in right.items() if k != "frames" and k != "notes" and not k.startswith("x_")}
    _, _, head_differing = compare_frame(head, head_right, ignore, 0.0)
    same, redo, worst, rows = [], [], {}, []
    for T in sorted(set(by_left) | set(by_right)):
        if T not in by_right:
            # 今の版で消えたコマは描かない。描き直すコマ（only にそのまま渡す並び）に入れると、今の版のトラックに無い T で止まる
            rows.append({"T": T, "same": False, "only": "left"})
            continue
        if T not in by_left:
            redo.append(T)
            rows.append({"T": T, "same": False, "only": "right"})
            continue
        equal, diffs, differing = compare_frame(by_left[T], by_right[T], ignore, tolerance)
        if head_differing:
            equal, differing = False, differing + ["(記録の頭) " + n for n in head_differing]
        (same if equal else redo).append(T)
        for name, d in diffs.items():
            group = _group(name)
            if d > worst.get(group, {"max": -1.0})["max"]:
                worst[group] = {"max": d, "T": T}
        rows.append({"T": T, "same": equal, "differing": sorted({_group(n) for n in differing}),
                     "max_diff": max(diffs.values(), default=0.0)})
    largest = sorted((r for r in rows if not r["same"] and "max_diff" in r), key=lambda r: -r["max_diff"])[:top]
    return {"check": "track_match", "status": "done", "pass": None,
            "reason": "測るだけ（同じ台本の値のコマと、描き直すコマを並べる。写すかは人が決める）",
            "frames": len(rows), "same_frames": len(same), "redo_frames": len(redo),
            "same": ranges(same), "redo": ranges(redo), "redo_T": redo,
            "only_left": [r["T"] for r in rows if r.get("only") == "left"],
            "only_right": [r["T"] for r in rows if r.get("only") == "right"],
            "tolerance": tolerance, "ignore": list(ignore), "header_differing": head_differing,
            "max_diff_by_field": {k: worst[k] for k in sorted(worst)}, "largest": largest, "rows": rows}
