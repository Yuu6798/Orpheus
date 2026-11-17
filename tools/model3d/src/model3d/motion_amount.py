"""区間ごとの動きの量（check.motion_amount）: 骨の通り道（model3d-bone-path/0.1）から、区間（秒か拍）ごとに、点の動く速さ・頭の上下の幅・
体の向きの幅・止めの拍を数で出す。動きの少ない区間を、カメラ割りと光を決める前に見つけるため。数を出すだけで、どの区間が弱いか
（好み）は決めない。

リポジトリ外の 3 作目の VRM のダンスで、前半の見栄えが弱いと言われたあと、骨の通り道で前半と後半の動きの量を比べた使い捨ての計算を
部品にした（前半は後半より腰も手も動きが少なく、止めが続いていた。カメラと光の側で動きを作って直した）。

- 時刻: 通り道の動きのコマ f の踊りの時刻 t = (f − frame0) ÷ fps。区間の from・until は秒か拍 {"beat": k}（grid の k0_time + period × k）。
- 速さ: 隣り合う標本の間の 移動の長さ ÷ 時刻の差 を、間の真ん中の時刻が [from, until) に入るものだけ平均する。名前を並べた組は、
  点ごとの平均をさらに平均する（両手なら左右の平均）。
- 上下の幅: 点（rise）の高さの いちばん高い − いちばん低い（時刻が [from, until) の標本）。
- 向きの幅: 左右の 2 点（facing。左 → 右）を結ぶ水平の向きから決めた体の前の向き（左 → 右を上から見て反時計回りに 90° 回した向き。
  0 で −Y（正面）、上から見て反時計回りが正）を、通り道の全体でつないで（±180° をまたいでも続ける）、区間の中の いちばん大きい − いちばん
  小さい（幅）と、最後 − 最初（回った量）。
- 止めの拍（grid があるとき）: 拍 k の窓 [拍 k, 拍 k + 1) の中の、止めの点（holds.points）の速さの平均が holds.speed より小さい拍を
  「止まっている拍」とし、続いた拍の数が holds.min_beats 以上の所を止めとして数える（区間の頭の拍が区間に入るもの）。
"""
import math

import numpy as np

from .io import ToolError
from .stage_plan import Clock

DEFAULTS = {"dance": {"frame0": 1.0, "fps": 30.0},
            "speeds": {"hips": ["hips"], "hands": ["hand_l", "hand_r"], "feet": ["foot_l", "foot_r"]},
            "rise": "head_top", "facing": ["shoulder_l", "shoulder_r"],
            "holds": {"points": None, "speed": 0.35, "min_beats": 2}}
EPS = 1e-9


def columns(path):
    return {**path.get("bones", {}), **path.get("points", {})}


def facing_angles(left, right):
    """左右の点の列から、体の前の向き（度。0 で −Y、上から見て反時計回りが正）を、全体でつないだ列。"""
    v = np.asarray(right, float) - np.asarray(left, float)
    forward = np.stack([-v[:, 1], v[:, 0]], axis=1)              # 左 → 右を上から見て反時計回りに 90°（正面を向くと −Y）
    angle = np.arctan2(forward[:, 0], -forward[:, 1])           # −Y から測る
    return np.degrees(np.unwrap(angle))


class Amount:
    def __init__(self, settings, path):
        self.s = settings
        cols = columns(path)
        names = {n for group in settings["speeds"].values() for n in group} | {settings["rise"]} | set(settings["facing"])
        names |= set(settings["holds"]["points"] or [])
        missing = sorted(n for n in names if n not in cols)
        if missing:
            raise ToolError(f"check.motion_amount: 骨の通り道に点がありません: {missing}（あるのは {sorted(cols)}。motion.bone_path の "
                            "points か bones で測ってください）")
        frames = np.asarray(path["frames"], float)
        if len(frames) < 2 or np.any(np.diff(frames) <= 0):
            raise ToolError("check.motion_amount: 骨の通り道の frames は 2 つ以上で、小さい順にしてください")
        dance = settings["dance"]
        self.t = (frames - float(dance["frame0"])) / float(dance["fps"])
        self.mid = (self.t[1:] + self.t[:-1]) / 2
        self.points = {n: np.asarray(cols[n], float) for n in names}
        self.speed = {n: np.linalg.norm(np.diff(p, axis=0), axis=1) / np.diff(self.t) for n, p in self.points.items()}
        self.facing = facing_angles(self.points[settings["facing"][0]], self.points[settings["facing"][1]])
        self.clock = Clock(settings.get("marks"), settings.get("grid") or {"k0_time": 0.0, "period": 1.0})

    def _group_speed(self, names, mask):
        if not mask.any():
            return None
        return float(np.mean([float(np.mean(self.speed[n][mask])) for n in names]))

    def section(self, start, end):
        inside = (self.t >= start - EPS) & (self.t < end - EPS)
        pairs = (self.mid >= start - EPS) & (self.mid < end - EPS)
        out = {"from": start, "until": end, "samples": int(inside.sum()),
               "speeds_m_s": {label: self._group_speed(names, pairs) for label, names in self.s["speeds"].items()}}
        if inside.any():
            z = self.points[self.s["rise"]][inside, 2]
            facing = self.facing[inside]
            out.update(rise_m=float(z.max() - z.min()), facing_range_deg=float(facing.max() - facing.min()),
                       facing_turn_deg=float(facing[-1] - facing[0]))
        else:
            out.update(rise_m=None, facing_range_deg=None, facing_turn_deg=None)
        return out

    def beats(self):
        """拍ごとの止めの点の速さ（grid があるとき）。[{beat, time, hold_speed_m_s, still}]。"""
        grid = self.s.get("grid")
        if grid is None:
            return []
        holds = self.s["holds"]
        names = holds["points"] or self.s["speeds"].get("hands") or next(iter(self.s["speeds"].values()))
        period, k0 = float(grid["period"]), float(grid["k0_time"])
        first = math.ceil((self.t[0] - k0) / period - EPS)
        last = math.floor((self.t[-1] - k0) / period + EPS) - 1
        rows = []
        for k in range(first, last + 1):
            start = k0 + period * k
            speed = self._group_speed(names, (self.mid >= start - EPS) & (self.mid < start + period - EPS))
            rows.append({"beat": k, "time": start, "hold_speed_m_s": speed,
                         "still": speed is not None and speed < float(holds["speed"])})
        return rows

    def holds(self, rows, start, end):
        """区間に頭が入る拍の止め: {still_beats, runs: [{beat, beats, time}], longest, held_beats}。"""
        chosen = [r for r in rows if start - EPS <= r["time"] < end - EPS]
        runs, current = [], []
        for r in chosen + [None]:
            if r is not None and r["still"] and (not current or r["beat"] == current[-1]["beat"] + 1):
                current.append(r)
                continue
            if len(current) >= int(self.s["holds"]["min_beats"]):
                runs.append({"beat": current[0]["beat"], "beats": len(current), "time": current[0]["time"]})
            current = [r] if r is not None and r["still"] else []
        return {"still_beats": sum(1 for r in chosen if r["still"]), "runs": runs,
                "longest": max((run["beats"] for run in runs), default=0), "held_beats": sum(run["beats"] for run in runs)}


def build(settings, path):
    amount = Amount(settings, path)
    rows = amount.beats()
    sections = []
    for n, item in enumerate(settings["sections"]):
        start = amount.clock(item["from"], f"sections[{n}].from")
        end = amount.clock(item["until"], f"sections[{n}].until")
        if end <= start:
            raise ToolError(f"check.motion_amount: sections[{n}] の until（{end:g} 秒）が from（{start:g} 秒）より後ではありません")
        entry = {"name": item.get("name", f"区間 {n + 1}"), **amount.section(start, end)}
        if entry["samples"] == 0:
            raise ToolError(f"check.motion_amount: sections[{n}]（{start:g}〜{end:g} 秒）に通り道の標本がありません")
        if rows:
            entry["holds"] = amount.holds(rows, start, end)
        sections.append(entry)
    whole = amount.section(float(amount.t[0]), float(amount.t[-1]) + 1.0)
    if rows:
        whole["holds"] = amount.holds(rows, float(amount.t[0]), float(amount.t[-1]) + 1.0)
    return {"check": "motion_amount", "status": "done", "pass": None,
            "reason": "測るだけ（区間ごとの動きの量の数。どの区間が弱いかは人が決める）",
            "seconds": [float(amount.t[0]), float(amount.t[-1])], "samples": len(amount.t),
            "settings": {k: settings[k] for k in ("speeds", "rise", "facing", "holds", "dance")}, "grid": settings.get("grid"),
            "sections": sections, "whole": whole, "beats": rows}
