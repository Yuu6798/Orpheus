"""段取り: 段取り表（model3d-stage-plan/0.1）・光のキー（model3d-light-keys/0.1）・骨の通り道（model3d-bone-path/0.1）から、
render.stage が描くトラック（model3d-stage-track/0.1）を作る。計算だけで、Blender は使わない（plan.stage が呼ぶ）。

コマ T（1 から）の時刻は t = (T - 1) / fps 秒（fps は光のキー）。段取り表の時刻は、秒の数・印（marks）の名前・拍 {"beat": k}
（拍の格子 k0_time + period × k）のどれかで書く。長さは m、角度は度、レンズは mm。省いた部分は下の既定の値
（リポジトリの外の試作の値）。

カメラ（camera）:
- 区間（shots）ごとに、方位 az（0 で正面、正で +X 側。キャラは -Y を向く）・狙う点からの水平の距離 dist・高さ height・
  レンズ lens・狙う点 target を決める（省いた値は base）。区間の頭は前の区間の until（最初は 0 秒）で、最後の区間だけ
  until を省ける（その先ずっと）。
- to を書くと、区間の頭から over 秒（省くと区間の長さ）かけて、to の値まで ease（smooth: なめらか / linear）で動く。
- 狙う点は、腰の通り道の hips 倍の所の高さ z（{"hips": 0.6, "z": 0.95}。0 に近いほど腰の揺れを追わない）、頭から上へ
  head m（{"head": -0.12}）、決まった点（{"point": [x, y, z]}）のどれか。高さも頭からの高さ {"head": 0.03} で書ける。
  骨の通り道は、描くコマの動きのコマ f（重ねず小さい順）の並びのまま smooth のガウスでならす（既定は腰 σ 6 コマ、
  頭 σ 1.5 コマ・幅 ±6 コマ）。
- effects（区間の effects が false の間は入れない）: whip（つなぎ目で寄りながら回り込み、戻る）・push（短く寄る）・
  shake（揺れ）・beat_push（拍ごとに寄る。every の倍数の拍は strong、ほかは weak）。
- カメラの位置 = 狙う点 + (sin(az) × dist + 揺れの横, -cos(az) × dist)、高さ = height + 揺れの上下。

舞台（stage）: 区間ごとの壁の模様（led / led_diag / void）と、壁の明るさに掛ける fade（[頭, 終わり]）。

光（lights）:
- palette: 区間ごとの縁の色 a・b。壁と左の輪郭光と塗りの a は a、右の輪郭光と塗りの b は b、光の柱は j が奇数なら a。
- level: 舞台の光の強さ（segments の最初に当たる区間の値、どれにも当たらなければ default）。
- 信号: low・mid・high は光のキーのそのコマの値、hit は直前の拍から hit.frames コマで 1 → 0、punch は直前の punch.at
  から punch.frames コマで 1 → 0。
- 明るさ = (base + Σ 係数 × 信号) × (level が true なら舞台の光の強さ) × 落とす割合（dims の最初に当たる区間）。
  壁はさらに舞台の fade と、レンズが closeup.lens 以上なら closeup.scale を掛ける。塗りの縁は scale を掛ける。
- 壁の模様の横のずれ = scroll × T。光の柱 j（0〜7。0〜3 は左）の向きは、phase からの拍 b で、下向きに
  tilt.base + tilt.swing × sin(2π b / tilt.period + j × tilt.step) 度、横に
  (j < 4 なら -1、ほかは 1) × pan.spread + pan.swing × sin(2π b / pan.period + j × pan.step) 度。
"""
import math

import numpy as np

from .io import ToolError

SIGNALS = ("low", "mid", "high", "hit", "punch")
BEAMS = 8
VIEW_KEYS = ("az", "dist", "height", "lens", "target")
CAMERA_BASE = {"az": 0.0, "dist": 3.6, "height": 1.1, "lens": 35.0, "target": {"hips": 0.6, "z": 0.95}}
SMOOTH = {"hips": {"sigma": 6.0}, "head": {"sigma": 1.5, "radius": 6}}
EFFECTS = {
    "whip": {"before": 0.3, "hold": 0.15, "after": 0.5, "zoom": 0.35, "turn": [6.0, 10.0], "ease": "smooth"},
    "push": {"amount": 0.06, "tau": 0.12, "length": 0.4},
    "shake": {"tau": 0.18, "length": 0.6, "x": [0.05, 95.0], "z": [0.035, 80.0]},
    "beat_push": {"strong": 0.045, "weak": 0.022, "every": 4, "tau": 0.12},
}
PALETTE = [{"a": [0.30, 0.09, 1.0], "b": [1.0, 0.05, 0.45]}]      # render.stage の既定の縁の色と同じ
LIGHTS = {
    "hit": {"frames": 9.0},
    "punch": {"at": [], "frames": 12.0},
    "wall": {"base": 0.12, "low": 0.9, "punch": 1.2, "level": True, "scroll": -0.04},
    "rims": [{"base": 900.0, "mid": 1800.0, "punch": 1500.0, "level": True},
             {"base": 900.0, "high": 1800.0, "punch": 1500.0, "level": True}],
    "beams": {"base": 0.9, "hit": 2.0, "high": 1.2, "level": True, "phase": 0.0,
              "tilt": {"base": 18.0, "swing": 12.0, "period": 8.0, "step": 1.0},
              "pan": {"spread": 12.0, "swing": 24.0, "period": 4.0, "step": 0.6}},
    "tone": {"base": 0.35, "mid": 0.5, "punch": 0.7, "level": False, "scale": 1.0},
}


def ease(u, kind="smooth"):
    """0〜1 に収めた u。smooth は u²(3 - 2u)（試作と同じ）。"""
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u) if kind == "smooth" else u


def gauss(values, sigma, radius=None):
    """(n, k) の列を、列ごとに σ のガウス（±radius コマ、省くと ±round(2σ)。端は端の値で延ばす）でならす（試作と同じ）。"""
    values = np.asarray(values, float)
    r = int(round(2 * sigma)) if radius is None else int(radius)
    w = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    w /= w.sum()
    return np.stack([np.convolve(np.pad(values[:, k], r, mode="edge"), w, mode="valid") for k in range(values.shape[1])],
                    axis=1)


def since(events, t):
    """t から見て直前の出来事からの秒（試作と同じく 1e-6 秒だけ先の出来事も含める）。無ければ 99。"""
    d = [t - e for e in events if t >= e - 1e-6]
    return min(d) if d else 99.0


def lerp(a, b, u):
    return a + (b - a) * u


class Clock:
    """段取り表の時刻（秒の数・印の名前・拍 {"beat": k}）を秒に直す。"""

    def __init__(self, marks, grid):
        self.grid = grid
        self.marks = {name: self._plain(value) for name, value in (marks or {}).items()}

    def beat(self, k):
        return self.grid["k0_time"] + self.grid["period"] * k

    def _plain(self, value):
        return self.beat(value["beat"]) if isinstance(value, dict) else float(value)

    def __call__(self, value, where):
        if isinstance(value, str):
            if value not in self.marks:
                raise ToolError(f"{where} の印 {value!r} が marks にありません（あるのは {sorted(self.marks)}）")
            return self.marks[value]
        return self._plain(value)

    def many(self, value, where):
        return [self(v, f"{where}[{n}]") for n, v in enumerate(value)] if isinstance(value, list) else [self(value, where)]


def spans(parts, clock, where):
    """区間の並びの [頭, 終わり)（秒）。頭は前の区間の until（最初は 0 秒）。最後の区間だけ until を省ける（その先ずっと）。"""
    out, start = [], 0.0
    for n, part in enumerate(parts):
        if "until" in part:
            end = clock(part["until"], f"{where}[{n}].until")
        elif n == len(parts) - 1:
            end = math.inf
        else:
            raise ToolError(f"{where}[{n}] に until が要ります（省けるのは最後の区間だけ）")
        if end <= start:
            raise ToolError(f"{where}[{n}] の until（{end:g} 秒）が区間の頭（{start:g} 秒）より後ではありません")
        out.append((start, end))
        start = end
    return out


def find(span_list, t):
    """t を含む区間の番号（無ければ None）。"""
    for n, (start, end) in enumerate(span_list):
        if start <= t < end:
            return n
    return None


def window(item, clock, where, need_end=True):
    """{"from", "until"} の [頭, 終わり)（秒）。until を省くとその先ずっと。"""
    start = clock(item["from"], f"{where}.from")
    end = clock(item["until"], f"{where}.until") if "until" in item else (None if need_end else math.inf)
    if end is None:
        raise ToolError(f"{where} に until が要ります")
    if end <= start:
        raise ToolError(f"{where} の until（{end:g} 秒）が from（{start:g} 秒）より後ではありません")
    return start, end


def view(base, values):
    return {**base, **{k: values[k] for k in VIEW_KEYS if k in values}}


def uses(views, kind):
    """カメラの値のどれかが kind（hips / head）の骨を使うか。"""
    for v in views:
        if kind in v.get("target", {}):
            return True
        if kind == "head" and isinstance(v.get("height"), dict):
            return True
    return False


class Camera:
    def __init__(self, plan, clock):
        cam = plan.get("camera", {})
        self.clock = clock
        self.base = view(CAMERA_BASE, cam.get("base", {}))
        self.smooth = {k: cam.get("smooth", {}).get(k, SMOOTH[k]) for k in SMOOTH}
        self.shots = cam.get("shots") or [{}]
        self.spans = spans(self.shots, clock, "camera.shots")
        self.moves = []
        for n, (shot, (start, end)) in enumerate(zip(self.shots, self.spans)):
            first = view(self.base, shot)
            if "to" not in shot:
                if "over" in shot or "ease" in shot:
                    raise ToolError(f"camera.shots[{n}] の over・ease は to と一緒に書いてください")
                self.moves.append((first, None, None, None))
                continue
            over = shot.get("over", end - start)
            if math.isinf(over):
                raise ToolError(f"camera.shots[{n}] は最後の区間で until が無いので、to には over（秒）が要ります")
            self.moves.append((first, view(first, shot["to"]), over, shot.get("ease", "smooth")))
        self.effects = [self._effect(e, n) for n, e in enumerate(cam.get("effects", []))]
        views = [v for move in self.moves for v in move[:2] if v is not None]
        self.needs = {kind for kind in ("hips", "head") if uses(views, kind)}

    def _effect(self, effect, n):
        where = f"camera.effects[{n}]"
        e = {**EFFECTS[effect["kind"]], **effect}
        if e["kind"] == "whip":
            e["at"] = self.clock(e["at"], f"{where}.at")
        elif e["kind"] in ("push", "shake"):
            e["at"] = self.clock.many(e["at"], f"{where}.at")
        else:
            e["from"], e["until"] = window(e, self.clock, where)
        return e

    def paths(self, bones, frames, names):
        """描くコマの動きのコマ（重ねず小さい順）の並びで、腰と頭の通り道をならす。"""
        out = {}
        for kind in sorted(self.needs):
            name = names[kind]
            if name not in bones["bones"]:
                raise ToolError(f"骨の通り道に {name} がありません（段取り表のカメラが {kind} を使います。bone_prefix を確かめてください）")
            row = dict(zip(bones["frames"], bones["bones"][name]))
            missing = [f for f in frames if f not in row]
            if missing:
                raise ToolError(f"骨の通り道に、描くコマの動きのコマがありません: {missing[:10]}")
            spec = self.smooth[kind]
            points = np.array([row[f] for f in frames], float)
            smooth = gauss(points, spec["sigma"], spec.get("radius"))
            if "z" in spec:
                smooth[:, 2] = gauss(points[:, 2:3], spec["z"]["sigma"], spec["z"].get("radius"))[:, 0]
            out[kind] = {f: smooth[k] for k, f in enumerate(frames)}
        return out

    @staticmethod
    def _aim(target, f, paths):
        if "hips" in target:
            p = paths["hips"][f]
            return [p[0] * target["hips"], p[1] * target["hips"], float(target["z"])]
        if "head" in target:
            p = paths["head"][f]
            return [float(p[0]), float(p[1]), float(p[2]) + target["head"]]
        return [float(v) for v in target["point"]]

    @staticmethod
    def _height(height, f, paths):
        return float(paths["head"][f][2]) + height["head"] if isinstance(height, dict) else float(height)

    def at(self, t, f, paths):
        """コマ（t 秒、動きのコマ f）のカメラ: (位置, 狙う点, レンズ, 区間の番号)。"""
        n = find(self.spans, t)
        if n is None:
            raise ToolError(f"{t:.4f} 秒がカメラの区間（camera.shots）のどれにも入りません（最後の区間の until を省くか延ばしてください）")
        first, last, over, kind = self.moves[n]
        target, height = self._aim(first["target"], f, paths), self._height(first["height"], f, paths)
        az, dist, lens = float(first["az"]), float(first["dist"]), float(first["lens"])
        if last is not None:
            u = ease((t - self.spans[n][0]) / over, kind)
            target = [lerp(a, b, u) for a, b in zip(target, self._aim(last["target"], f, paths))]
            height = lerp(height, self._height(last["height"], f, paths), u)
            az, dist, lens = lerp(az, float(last["az"]), u), lerp(dist, float(last["dist"]), u), lerp(lens, float(last["lens"]), u)
        sx = sz = 0.0
        if self.shots[n].get("effects", True):
            for e in self.effects:
                dist, az, sx, sz = self._apply(e, t, dist, az, sx, sz)
        r = math.radians(az)
        target = [float(v) for v in target]
        location = [target[0] + (math.sin(r) * dist + sx), target[1] + (-math.cos(r) * dist), float(height) + sz]
        return location, target, lens, n

    def _apply(self, e, t, dist, az, sx, sz):
        kind = e["kind"]
        if kind == "whip":
            at, before, hold, after, zoom = e["at"], e["before"], e["hold"], e["after"], e["zoom"]
            t1, t2 = e["turn"]
            if at - before <= t < at:                    # 寄りながら回る
                u = ease((t - (at - before)) / before, e["ease"])
                dist *= 1 - zoom * u
                az += t1 * u
            elif at <= t < at + hold + after:
                if t < at + hold:                        # 寄ったまま回り続ける
                    az += t1 + (t2 - t1) * (t - at) / hold
                    dist *= 1 - zoom
                else:                                    # 戻る
                    u = ease((t - at - hold) / after, e["ease"])
                    az += t2 * (1 - u)
                    dist *= (1 - zoom) + zoom * u
        elif kind == "push":
            for at in e["at"]:
                d = t - at
                if 0 <= d < e["length"]:
                    dist *= 1 - e["amount"] * math.exp(-d / e["tau"])
        elif kind == "shake":
            for at in e["at"]:
                d = t - at
                if 0 <= d < e["length"]:
                    sx += e["x"][0] * math.exp(-d / e["tau"]) * math.sin(d * e["x"][1])
                    sz += e["z"][0] * math.exp(-d / e["tau"]) * math.cos(d * e["z"][1])
        elif e["from"] <= t < e["until"]:                # beat_push
            k = math.floor((t - self.clock.grid["k0_time"]) / self.clock.grid["period"])
            d = t - self.clock.beat(k)
            dist *= 1 - (e["strong"] if k % e["every"] == 0 else e["weak"]) * math.exp(-d / e["tau"])
        return dist, az, sx, sz


class Lights:
    def __init__(self, plan, clock, keys):
        lights = plan.get("lights", {})
        self.clock, self.keys = clock, keys
        self.fps = float(keys["fps"])
        self.colors = plan.get("colors", {})
        palette = lights.get("palette") or PALETTE
        self.palette = [(self._color(p["a"], f"lights.palette[{n}].a"), self._color(p["b"], f"lights.palette[{n}].b"))
                        for n, p in enumerate(palette)]
        self.palette_spans = spans(palette, clock, "lights.palette")
        stage = plan.get("stage") or [{"kind": "led"}]
        self.stage = stage
        self.stage_spans = spans(stage, clock, "stage")
        for n, (part, (start, end)) in enumerate(zip(stage, self.stage_spans)):
            if "fade" in part and math.isinf(end):
                raise ToolError(f"stage[{n}] は最後の区間で until が無いので、fade（変わる割合）は使えません")
        level = lights.get("level", {})
        self.level_default = float(level.get("default", 1.0))
        self.levels = []
        for n, seg in enumerate(level.get("segments", [])):
            ramp = isinstance(seg["value"], list)
            start, end = window(seg, clock, f"lights.level.segments[{n}]", need_end=ramp)
            self.levels.append((start, end, seg["value"], seg.get("ease", "smooth")))
        self.hit_frames = float({**LIGHTS["hit"], **lights.get("hit", {})}["frames"])
        punch = {**LIGHTS["punch"], **lights.get("punch", {})}
        self.punch_frames = float(punch["frames"])
        self.accents = [clock(v, f"lights.punch.at[{n}]") for n, v in enumerate(punch["at"])]
        self.dims = [(*window(d, clock, f"lights.dims[{n}]"), d) for n, d in enumerate(lights.get("dims", []))]
        self.wall = {**LIGHTS["wall"], **lights.get("wall", {})}
        rims = lights.get("rims") or [{}, {}]
        self.rims = [{**LIGHTS["rims"][k], **rims[k]} for k in range(2)]
        self.beams = {**LIGHTS["beams"], **lights.get("beams", {})}
        for key in ("tilt", "pan"):
            self.beams[key] = {**LIGHTS["beams"][key], **lights.get("beams", {}).get(key, {})}
        self.phase = clock(self.beams["phase"], "lights.beams.phase")
        self.tone = {**LIGHTS["tone"], **lights.get("tone", {})}
        grid = keys["grid"]
        end = keys["frames"] / self.fps                   # 窓に入る拍（光のキーを作る道具と同じ数え方）
        k = math.ceil(-grid["k0_time"] / grid["period"])
        self.beats = []
        while clock.beat(k) < end:
            self.beats.append(clock.beat(k))
            k += 1
            if len(self.beats) > 10 * keys["frames"]:
                raise ToolError(f"光のキーの拍の格子の period（{grid['period']}）が短すぎます（1 コマに 10 拍より多い）")

    def _color(self, value, where):
        if isinstance(value, str):
            if value not in self.colors:
                raise ToolError(f"{where} の色 {value!r} が colors にありません（あるのは {sorted(self.colors)}）")
            return [float(v) for v in self.colors[value]]
        return [float(v) for v in value]

    def level(self, t):
        for start, end, value, kind in self.levels:
            if start <= t < end:
                if isinstance(value, list):
                    return value[0] + (value[1] - value[0]) * ease((t - start) / (end - start), kind)
                return float(value)
        return self.level_default

    def dim(self, t):
        for start, end, d in self.dims:
            if start <= t < end:
                return d
        return {}

    @staticmethod
    def amount(formula, signals):
        total = float(formula.get("base", 0.0))
        for key, coef in formula.items():
            if key in SIGNALS:
                total = total + coef * signals[key]
        return total

    @staticmethod
    def factors(formula, key, lv, dim):
        """(舞台の光の強さの代わり, 掛ける割合)。"""
        level = lv if formula.get("level", True) else 1.0
        value = dim.get(key)
        if isinstance(value, dict):
            return value.get("level", level), value.get("scale", 1.0)
        return level, (1.0 if value is None else value)

    def at(self, T, t, lens):
        """コマ T（t 秒、レンズ lens）の舞台と光の値。"""
        i = min(T - 1, len(self.keys["low"]) - 1)
        signals = {"low": float(self.keys["low"][i]), "mid": float(self.keys["mid"][i]), "high": float(self.keys["high"][i]),
                   "hit": max(0.0, 1.0 - since(self.beats, t) * self.fps / self.hit_frames),
                   "punch": max(0.0, 1.0 - since(self.accents, t) * self.fps / self.punch_frames)}
        n = find(self.palette_spans, t)
        if n is None:
            raise ToolError(f"{t:.4f} 秒が色の区間（lights.palette）のどれにも入りません")
        a, b = self.palette[n]
        s = find(self.stage_spans, t)
        if s is None:
            raise ToolError(f"{t:.4f} 秒が舞台の区間（stage）のどれにも入りません")
        part = self.stage[s]
        fade = 1.0
        if "fade" in part:
            start, end = self.stage_spans[s]
            fade = lerp(part["fade"][0], part["fade"][1], ease((t - start) / (end - start), part.get("ease", "smooth")))
        lv, dim = self.level(t), self.dim(t)

        def value(formula, key):
            level, scale = self.factors(formula, key, lv, dim)
            out = self.amount(formula, signals) * level * scale
            if out < 0:
                raise ToolError(f"T {T} の {key} の明るさが負になります（{out:g}）。段取り表の係数を確かめてください")
            return out

        strength = value(self.wall, "wall") * fade
        closeup = self.wall.get("closeup")
        if closeup and lens >= closeup["lens"]:
            strength *= closeup["scale"]
        rims = [{"energy": value(self.rims[0], "rims"), "color": a}, {"energy": value(self.rims[1], "rims"), "color": b}]
        beat = (t - self.phase) / self.clock.grid["period"]
        tilt, pan = self.beams["tilt"], self.beams["pan"]
        rotations = []
        for j in range(BEAMS):
            side = -1 if j < BEAMS // 2 else 1
            down = tilt["base"] + tilt["swing"] * math.sin(2 * math.pi * beat / tilt["period"] + j * tilt["step"])
            across = side * pan["spread"] + pan["swing"] * math.sin(2 * math.pi * beat / pan["period"] + j * pan["step"])
            rotations.append([math.radians(-down), math.radians(across), 0.0])
        beams = {"gain": value(self.beams, "beams"), "rotations": rotations, "colors": [a if j % 2 else b for j in range(BEAMS)]}
        tone = {"a": a, "b": b, "gain": value(self.tone, "tone") * self.tone["scale"]}
        wall = {"strength": strength, "color": a, "scroll": self.wall["scroll"] * T}
        return {"stage": part["kind"], "tone": tone, "wall": wall, "rims": rims, "beams": beams}, n, s


def _ranges(span_list, hits, names, rows):
    out = []
    for n, (start, end) in enumerate(span_list):
        Ts = [T for T, k in zip(rows, hits) if k == n]
        item = {"index": n, "from": start, "until": None if math.isinf(end) else end,
                "T": [Ts[0], Ts[-1]] if Ts else None, "frames": len(Ts)}
        if names[n]:
            item["name"] = names[n]
        out.append(item)
    return out


def build_track(plan, keys, bones, cut=None, bone_prefix="mixamorig:"):
    """トラック（model3d-stage-track/0.1）と報告。cut（T と f だけのトラック）が無ければ、骨の通り道のコマを T = 1 から順に描く。"""
    for band in ("low", "mid", "high"):
        if len(keys[band]) != keys["frames"]:
            raise ToolError(f"光のキーの {band} の数（{len(keys[band])}）が frames（{keys['frames']}）と違います")
    for name, column in bones["bones"].items():
        if len(column) != len(bones["frames"]):
            raise ToolError(f"骨の通り道の {name} の数（{len(column)}）が frames（{len(bones['frames'])}）と違います")
    if len(set(bones["frames"])) != len(bones["frames"]):
        raise ToolError("骨の通り道の frames が重なっています")
    fps = float(keys["fps"])
    if cut is not None and cut.get("fps") is not None and float(cut["fps"]) != fps:
        raise ToolError(f"描くコマの並び（cut）の fps（{cut['fps']}）が光のキーの fps（{keys['fps']}）と違います")
    rows = ([(int(fr["T"]), int(fr["f"])) for fr in cut["frames"]] if cut is not None
            else [(k + 1, int(f)) for k, f in enumerate(bones["frames"])])
    clock = Clock(plan.get("marks"), keys["grid"])
    camera, lights = Camera(plan, clock), Lights(plan, clock, keys)
    names = {"hips": bone_prefix + "Hips", "head": bone_prefix + "Head"}
    paths = camera.paths(bones, sorted({f for _, f in rows}), names)
    frames, shot_hits, stage_hits, palette_hits = [], [], [], []
    for T, f in rows:
        t = (T - 1) / fps
        location, target, lens, n = camera.at(t, f, paths)
        values, p, s = lights.at(T, t, lens)
        frames.append({"T": T, "f": f, "camera": {"location": location, "target": target, "lens": lens}, **values})
        shot_hits.append(n)
        stage_hits.append(s)
        palette_hits.append(p)
    track = {"schema_version": "model3d-stage-track/0.1", "fps": fps, "frames": frames}
    Ts = [T for T, _ in rows]
    report = {
        "schema_version": "model3d-stage-plan-report/0.1", "fps": fps, "frames": len(rows), "T": [min(Ts), max(Ts)],
        "seconds": [(min(Ts) - 1) / fps, (max(Ts) - 1) / fps], "action_frames": [min(f for _, f in rows), max(f for _, f in rows)],
        "bones": {kind: names[kind] for kind in sorted(camera.needs)},
        "keys": {"frames": keys["frames"], "beats": len(lights.beats), "beyond": sum(1 for T in Ts if T > len(keys["low"]))},
        "shots": _ranges(camera.spans, shot_hits, [s.get("name") for s in camera.shots], Ts),
        "stage": [{**item, "kind": lights.stage[item["index"]]["kind"]}
                  for item in _ranges(lights.stage_spans, stage_hits, [None] * len(lights.stage_spans), Ts)],
        "palette": _ranges(lights.palette_spans, palette_hits, [None] * len(lights.palette_spans), Ts),
    }
    return track, report
