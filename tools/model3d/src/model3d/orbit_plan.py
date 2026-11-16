"""踊り手を中心にした極座標のカメラ（plan.orbit）: カメラの段取り表（model3d-orbit-plan/0.1）と、骨から決めた点の通り道
（model3d-bone-path/0.1 の points か bones）から、コマごとのカメラ（model3d-cameras/0.1。行列・焦点距離・主点・踊り手の点）と、
コマの並び（model3d-stage-track/0.1。T と動きのコマ f）を作る。計算だけで、Blender は使わない。

リポジトリ外の 2 作目の VRM のダンスの演出版（3D ならではのカメラ）の計画と描画の台本の計算を移した。plan.stage と同じ時刻の
読み方（秒の数・印・拍。stage_plan.Clock）・区間（until）・ならし（stage_plan.gauss）・寄りと揺れ（whip・push・shake・beat_push。
stage_plan.Camera）を使い、足りない所（狙う点と別の回る中心、区間の中の複数のキーとつなぎ方、ロール、画角、真上から見るときの
上の向き、手持ちの揺れと打点の揺れ、踊りの時刻の止め）を足した。

時刻: コマ T（1 から frames まで）の時刻は t = time_offset + (T − 1) / fps 秒（time_offset で曲の秒にそろえられる）。段取り表の時刻は
どれもこの t。区間の頭は前の区間の until（最初の区間は T = 1 の時刻）。踊りの時刻は t（holds の区間では止めた時刻）で、動きのコマは
f = dance.frame0 + dance.fps × 踊りの時刻。

点: 通り道の点（bones か points の列）を、動きのコマ f → 踊りの時刻 (f − frame0) / fps に直し、smooth.sigma_s 秒のガウス
（±ceil(reach × σ) の標本。端は端の値で延ばす）でならす。踊りの時刻での値は、前後の標本の直線補間（範囲の外は端の標本）。
標本は時刻に等間隔であること（plan.retime のトラックで motion.bone_path を測る）。

カメラ（コマごと。shots の区間の中の u = (t − 頭) / (終わり − 頭) で、前後のキー a・b を ease で混ぜる w）:
  中心 c = centre の点、狙う点 a = (aim の点 + (0, 0, dz)) を a・b で混ぜたもの、方位 θ・距離 r・高さ h・画角・ロールも混ぜる。
  高さは m の数か、狙う点からの高さ {"above": m}。位置 = (c.x + r sin θ + 揺れの横, c.y − r cos θ, max(h + 揺れの上下, min_height))。
  前 = 狙う点 − 位置（長さ 1）。上の手がかり = (0, 0, 1) と水平の前 (−sin θ, cos θ, 0) を、s = smoothstep((|前.z| − up.from) / up.span)
  で混ぜたもの（真上から見下ろすときに上の向きが回らない）。右 = 前 × 手がかり、上 = 右 × 前。行列 = [右, 上, −前] に、手持ちの揺れの
  回り（y のまわりに −yaw、x のまわりに pitch、z のまわりに roll + キーのロール）を右から掛ける。焦点距離 = 画面の高さ ÷ 2 ÷ tan(画角 ÷ 2)。
"""
import math

import numpy as np

from .blender_adapter.point_track import PathError, Samples
from .io import ToolError
from .stage_plan import Camera, Clock, ease as plan_ease, find, inside, spans

EASE = {
    "smooth": lambda u: plan_ease(u, "smooth"),
    "linear": lambda u: plan_ease(u, "linear"),
    "out": lambda u: 1 - (1 - min(1.0, max(0.0, u))) ** 3,
    "in": lambda u: min(1.0, max(0.0, u)) ** 3,
    "whip": lambda u: 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)) ** 1.15),
}
DEFAULTS = {
    "time_offset": 0.0, "centre": "hips", "min_height": 0.12,
    "smooth": {"sigma_s": 0.292, "reach": 3.0},
    "up": {"from": 0.6, "span": 0.35},
    "handheld": {"seed": 3, "hz": [0.9, 2.3, 5.1], "weights": [1.0, 0.6, 0.3]},
    "kick": {"tau": 0.13, "length": 0.6, "hz": [7.5, 8.3, 6.1], "phase": [0.0, 1.0, 2.0], "gain": [0.6, 1.0, 0.8]},
    "camera": {"resolution": [1080, 1920], "sensor_height": 36.0},
}
SECTIONS = ("smooth", "up", "handheld", "kick", "camera")


def merged(plan):
    out = {**DEFAULTS, **{k: v for k, v in plan.items() if k not in SECTIONS}}
    for key in SECTIONS:
        out[key] = {**DEFAULTS[key], **plan.get(key, {})}
    return out


def lerp(a, b, w):
    return a + (b - a) * w


def smoothstep(v):
    v = min(1.0, max(0.0, v))
    return v * v * (3 - 2 * v)


def rotation(axis, degrees):
    """軸 axis（"x"・"y"・"z"）のまわりに degrees 度回す 3×3 の行列（右手系。Blender の Matrix.Rotation と同じ向き）。"""
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    if axis == "x":
        return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])
    if axis == "y":
        return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


class Orbit:
    def __init__(self, plan):
        p = merged(plan)
        self.p = p
        self.fps = float(p["fps"])
        self.frames = int(p["frames"])
        self.offset = float(p["time_offset"])
        self.clock = Clock(p.get("marks"), p.get("grid") or {"k0_time": 0.0, "period": 1.0})
        if p.get("grid") is None and _uses_beats(plan):
            raise ToolError("段取り表に拍 {\"beat\": k} がありますが、grid（拍の格子）がありません")
        self.shots = p["shots"]
        self.spans = spans(self.shots, self.clock, "shots", start=self.offset)   # 最初の区間の頭は T = 1 の時刻（u をそこから数える）
        for n, (shot, (start, end)) in enumerate(zip(self.shots, self.spans)):
            keys = shot["keys"]
            if len(keys) > 1 and math.isinf(end):
                raise ToolError(f"shots[{n}] はキーが 2 つ以上なので until が要ります（区間の中の割合でキーを混ぜるため）")
            us = [self._u(key, k, len(keys)) for k, key in enumerate(keys)]
            if any(b < a for a, b in zip(us, us[1:])) or us[0] < 0 or us[-1] > 1:
                raise ToolError(f"shots[{n}] のキーの u は 0〜1 の昇順にしてください: {us}")
        dance = p["dance"]
        self.frame0, self.dance_fps = float(dance.get("frame0", 1.0)), float(dance["fps"])
        self.holds = []
        for n, hold in enumerate(dance.get("holds", [])):
            start = self.clock(hold["from"], f"dance.holds[{n}].from")
            end = self.clock(hold["until"], f"dance.holds[{n}].until") if "until" in hold else math.inf
            if end <= start:
                raise ToolError(f"dance.holds[{n}] の until（{end:g} 秒）が from（{start:g} 秒）より後ではありません")
            self.holds.append((start, end, float(hold["at"])))
        self.kicks = [(self.clock(k["at"], f"kicks[{n}].at"), float(k["deg"])) for n, k in enumerate(p.get("kicks", []))]
        phases = np.random.default_rng(int(p["handheld"]["seed"])).uniform(0, 2 * np.pi, (3, 3))
        self.phases = phases
        self.effects = Camera({"camera": {"effects": p.get("effects", [])}}, self.clock) if p.get("effects") else None
        self.names = sorted({p["centre"]} | {key["aim"] for shot in self.shots for key in shot["keys"]})

    @staticmethod
    def _u(key, k, count):
        if "u" in key:
            return float(key["u"])
        if k == 0:
            return 0.0
        if k == count - 1:
            return 1.0
        raise ToolError("区間の中のキー（最初と最後のほか）には u（区間の中の割合 0〜1）が要ります")

    def time(self, T):
        return self.offset + (T - 1) / self.fps

    def dance_time(self, t):
        for start, end, at in self.holds:
            if inside(t, start, end):                    # 境目は plan.stage と同じく、EPS 秒前のコマから止める側
                return at, True
        return t, False

    def motion_frame(self, dance_t):
        f = self.frame0 + dance_t * self.dance_fps
        return int(f) if float(f).is_integer() else f

    def keys_at(self, n, t):
        """区間 n の、時刻 t での前後のキーと混ぜる割合 w（キー a の ease。試作と同じ選び方）。"""
        shot = self.shots[n]
        start, end = self.spans[n]
        keys = shot["keys"]
        if len(keys) == 1:
            return keys[0], keys[0], 0.0
        u = min(max((t - start) / (end - start), 0.0), 1.0)
        us = [self._u(key, k, len(keys)) for k, key in enumerate(keys)]
        i = 0
        while i < len(keys) - 2 and u > us[i + 1]:
            i += 1
        a, b = keys[i], keys[i + 1]
        return a, b, EASE[a.get("ease", "smooth")]((u - us[i]) / max(1e-9, us[i + 1] - us[i]))

    def jitter(self, t, amplitude):
        """手持ちの揺れ（yaw・pitch・roll の度）。区間の振れ幅 amplitude が 0 なら、打点の揺れも入れない（試作と同じ）。"""
        hand, kick = self.p["handheld"], self.p["kick"]
        values = []
        for j in range(3):
            total = None
            for m in range(3):          # 試作の式と同じ順: s0 + 0.6 s1 + 0.3 s2（重み 1 は掛けない）
                term = math.sin(2 * math.pi * hand["hz"][m] * t + self.phases[j, m])
                term = term if hand["weights"][m] == 1.0 else hand["weights"][m] * term
                total = term if total is None else total + term
            values.append(amplitude * total)
        if amplitude > 0:
            for at, strength in self.kicks:
                dt = t - at
                if 0 <= dt < kick["length"]:
                    k = strength * math.exp(-dt / kick["tau"])
                    for j in range(3):
                        values[j] += k * math.sin(2 * math.pi * kick["hz"][j] * dt + kick["phase"][j]) * kick["gain"][j]
        return values

    def camera(self, T, samples):
        """コマ T のカメラ: (4×4 の行列, 焦点距離, 踊り手の点, 動きのコマ f, 区間の番号, 止めたか)。"""
        t = self.time(T)
        n = find(self.spans, t)
        if n is None:
            raise ToolError(f"{t:.4f} 秒がカメラの区間（shots）のどれにも入りません（最後の区間の until を省くか延ばしてください）")
        shot = self.shots[n]
        a, b, w = self.keys_at(n, t)
        dance_t, held = self.dance_time(t)
        centre = samples.at(self.p["centre"], dance_t)
        aim_a = samples.at(a["aim"], dance_t) + np.array([0.0, 0.0, float(a.get("dz", 0.0))])
        aim_b = samples.at(b["aim"], dance_t) + np.array([0.0, 0.0, float(b.get("dz", 0.0))])
        aim = lerp(aim_a, aim_b, w)
        az, radius = lerp(float(a["az"]), float(b["az"]), w), lerp(float(a["dist"]), float(b["dist"]), w)
        high_a = _height(a["height"], aim_a)
        high_b = _height(b["height"], aim_b)
        height = lerp(high_a, high_b, w)
        sx = sz = 0.0
        if self.effects is not None and shot.get("effects", True):
            for effect in self.effects.effects:
                radius, az, sx, sz = self.effects._apply(effect, t, radius, az, sx, sz)
        theta = math.radians(az)
        position = np.array([centre[0] + radius * math.sin(theta) + sx, centre[1] - radius * math.cos(theta),
                             max(height + sz, float(self.p["min_height"]))])
        forward = aim - position
        length = float(np.linalg.norm(forward))
        if length < 1e-9:
            raise ToolError(f"T {T}: カメラの位置が狙う点と同じです（距離・高さを確かめてください）")
        forward = forward / length
        flat = np.array([-math.sin(theta), math.cos(theta), 0.0])
        steep = smoothstep((abs(forward[2]) - self.p["up"]["from"]) / self.p["up"]["span"])
        hint = np.array([0.0, 0.0, 1.0]) * (1 - steep) + flat * steep
        hint = hint / np.linalg.norm(hint)
        right = np.cross(forward, hint)
        if float(np.linalg.norm(right)) < 1e-9:
            raise ToolError(f"T {T}: 前の向きと上の手がかりが重なり、カメラの右の向きが決まりません（up を確かめてください）")
        right = right / np.linalg.norm(right)
        up = np.cross(right, forward)
        yaw, pitch, roll = self.jitter(t, float(shot.get("handheld", 0.0)))
        basis = np.stack([right, up, -forward], axis=1)
        basis = basis @ rotation("y", -yaw) @ rotation("x", pitch) @ rotation("z", roll + lerp(float(a.get("roll", 0.0)), float(b.get("roll", 0.0)), w))
        matrix = np.eye(4)
        matrix[:3, :3] = basis
        matrix[:3, 3] = position
        fov = lerp(float(a["fov"]), float(b["fov"]), w)
        focal = self.p["camera"]["resolution"][1] / 2 / math.tan(math.radians(fov) / 2)
        return matrix, focal, centre, self.motion_frame(dance_t), n, held


def read_points(path, names, frame0, fps, sigma_s, reach):
    """カメラが使う点（どれも同じ σ でならす）を、踊りの時刻で読む Samples。"""
    try:
        return Samples(path, {name: sigma_s for name in names}, frame0, fps, reach)
    except PathError as error:
        raise ToolError(str(error)) from error


def _height(height, aim):
    return float(aim[2]) + float(height["above"]) if isinstance(height, dict) else float(height)


def _uses_beats(value):
    if isinstance(value, dict):
        if set(value) == {"beat"}:
            return True
        return any(_uses_beats(v) for v in value.values())
    if isinstance(value, list):
        return any(_uses_beats(v) for v in value)
    return False


def build(plan, path):
    """(cameras, track, report)。cameras は model3d-cameras/0.1、track は model3d-stage-track/0.1（T と f だけ）。"""
    orbit = Orbit(plan)
    p = orbit.p
    smooth = p["smooth"]
    samples = read_points(path, orbit.names, orbit.frame0, orbit.dance_fps, float(smooth["sigma_s"]), float(smooth["reach"]))
    width, height = p["camera"]["resolution"]
    entries, rows, hits, held, outside = [], [], [], [], 0
    for T in range(1, orbit.frames + 1):
        matrix, focal, dancer, f, n, frozen = orbit.camera(T, samples)
        dance_t, _ = orbit.dance_time(orbit.time(T))
        outside += samples.outside(dance_t)
        position = matrix[:3, 3]
        entries.append({"T": T, "f": f, "matrix": [[float(v) for v in row] for row in matrix], "focal_px": float(focal),
                        "principal_px": [width / 2, height / 2], "lens_mm": float(focal / height * p["camera"]["sensor_height"]),
                        "shift": [0.0, 0.0], "dancer": [float(v) for v in dancer],
                        "distance_m": float(np.linalg.norm(np.asarray(dancer) - position))})
        rows.append({"T": T, "f": f})
        hits.append(n)
        held.append(frozen)
    cameras = {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [int(width), int(height)], "fps": orbit.fps,
               "sensor_height_mm": float(p["camera"]["sensor_height"]), "frames": entries}
    track = {"schema_version": "model3d-stage-track/0.1", "fps": orbit.fps, "frames": rows}
    shots = []
    for n, (start, end) in enumerate(orbit.spans):
        Ts = [T for T, k in zip(range(1, orbit.frames + 1), hits) if k == n]
        item = {"index": n, "from": start, "until": None if math.isinf(end) else end, "T": [Ts[0], Ts[-1]] if Ts else None,
                "frames": len(Ts)}
        if orbit.shots[n].get("name"):
            item["name"] = orbit.shots[n]["name"]
        shots.append(item)
    report = {"schema_version": "model3d-orbit-report/0.1", "frames": orbit.frames, "fps": orbit.fps,
              "seconds": [orbit.time(1), orbit.time(orbit.frames)], "points": orbit.names, "centre": p["centre"],
              "samples": {"count": samples.count, "per_second": samples.rate, "dance_seconds": list(samples.span),
                          "sigma_s": float(smooth["sigma_s"]), "outside": outside},
              "shots": shots, "held_frames": int(sum(held)),
              "f": [rows[0]["f"], rows[-1]["f"]]}
    return cameras, track, report
