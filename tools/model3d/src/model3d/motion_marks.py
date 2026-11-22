"""出来事の時刻の候補を、踊りの骨の通り道と曲の打音から拾う（plan.motion_marks の計算。Blender は使わない）。

リポジトリ外の 4 作目の 3D のダンスの演出で、出来事の表を書く前に、足の着地（下りて止まるコマ）と、打音の時刻が届く最初のコマを
拾った計算を、作品によらない形にした。高さの山と谷（しゃがみの底など）・向きに沿った山（指先がいちばん前など）も拾えるようにした。
拾うだけで、どれを出来事にするかは決めない。出すコマの番号は、出来事の表に frame でそのまま書ける（秒を丸めて書かない）。

- 着地: 点の高さが、前の window 標本のいちばん高い所から drop m 以上下がっていて、1 つ前の標本の速さが fall m/s より速く下がり、
  1 つ後の標本の速さの大きさが still m/s より小さい標本。前の着地から gap 秒より近いものは数えない（点ごと）。
- 山と谷: 点の位置を axis の向きに写した値が、前後 gap 秒の中でいちばん大きい（max）か小さい（min）標本で、その中のいちばん
  小さい（大きい）値との差が prominence m 以上のもの。
- 打音: 秒の並びを、その時刻に届く最初の動きのコマにする（コマの頭の直後 0.05 コマ以内の秒は、丸めた秒とみなしてそのコマ）。
"""
import math

import numpy as np

from .io import ToolError

NEAR_HEAD = 0.05     # コマの頭の直後とみなす幅（コマ）。秒を小数 3 桁に丸めた時刻は、30 fps でコマの頭から 0.015 コマ以内


def _column(path, name):
    columns = {**path.get("bones", {}), **path.get("points", {})}
    if name not in columns:
        raise ToolError(f"骨の通り道に点がありません: {name}（あるのは {sorted(columns)}）")
    values = np.asarray(columns[name], float)
    if values.shape != (len(path["frames"]), 3):
        raise ToolError(f"骨の通り道の {name} の数（{len(values)}）が frames（{len(path['frames'])}）と違います")
    return values


def _spacing(path):
    frames = np.asarray(path["frames"], float)
    if len(frames) < 3:
        raise ToolError("骨の通り道の標本が 3 つより少ないので、拾えません")
    steps = np.diff(frames)
    if steps.min() <= 0 or steps.max() - steps.min() > 1e-6:
        raise ToolError("骨の通り道の標本が、コマに等間隔で増えていません")
    return frames, float(steps[0])


def first_frame(t, fps, frame0):
    """秒 t が届く最初の動きのコマの番号（整数）。"""
    return int(math.ceil(frame0 + t * fps - NEAR_HEAD))


def landings(path, names, fps, frame0, drop, fall, still, window, gap):
    frames, step = _spacing(path)
    rate = fps / step                                                     # 1 秒あたりの標本の数
    out = []
    for name in names:
        z = _column(path, name)[:, 2]
        v = np.gradient(z) * rate
        last = None
        for i in range(window, len(z) - 1):
            if z[i - window:i].max() - z[i] > drop and v[i - 1] < -fall and abs(v[i + 1]) < still and (last is None or (i - last) / rate > gap):
                out.append({"point": name, "frame": float(frames[i]), "t": (float(frames[i]) - frame0) / fps})
                last = i
    return sorted(out, key=lambda m: (m["t"], m["point"]))


def extremes(path, spec, fps, frame0):
    frames, step = _spacing(path)
    rate = fps / step
    axis = np.asarray(spec["axis"], float)
    axis = axis / np.linalg.norm(axis)
    value = _column(path, spec["point"]) @ axis
    sign = 1.0 if spec["kind"] == "max" else -1.0
    reach = max(1, int(round(spec["gap"] * rate)))
    out = []
    for i in range(len(value)):
        lo, hi = max(0, i - reach), min(len(value), i + reach + 1)
        near = sign * value[lo:hi]
        if sign * value[i] < near.max() or (out and frames[i] - out[-1]["frame"] <= reach * step):
            continue
        if sign * value[i] - near.min() >= spec["prominence"]:
            out.append({"frame": float(frames[i]), "t": (float(frames[i]) - frame0) / fps, "value": float(value[i])})
    return out


def build(settings, path):
    fps, frame0 = settings["dance"]["fps"], settings["dance"]["frame0"]
    report = {"schema_version": "model3d-motion-marks/0.1", "fps": fps, "frame0": frame0}
    if settings.get("landing"):
        s = settings["landing"]
        report["landings"] = landings(path, s["points"], fps, frame0, s["drop"], s["fall"], s["still"], s["window"], s["gap"])
    if settings.get("extremes"):
        report["extremes"] = {spec["name"]: extremes(path, spec, fps, frame0) for spec in settings["extremes"]}
    if settings.get("hits") is not None:
        report["hits"] = [{"t": t, "frame": first_frame(t, fps, frame0)} for t in settings["hits"]]
    report["counts"] = {"landings": len(report.get("landings", [])), "extremes": {k: len(v) for k, v in report.get("extremes", {}).items()},
                        "hits": len(report.get("hits", []))}
    return report
