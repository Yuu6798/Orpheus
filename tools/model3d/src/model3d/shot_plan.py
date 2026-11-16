"""画面の上の点で決めるカメラ（plan.shots）: 2D の絵コンテのショット表（model3d-shot-plan/0.1）を、1 枚の絵の「仮の部屋」
（render.box_room と同じ置き方）から、コマごとの 3D のカメラ（model3d-cameras/0.1。行列・焦点距離・主点・踊り手の点）と、
コマの並び（model3d-stage-track/0.1。T と動きのコマ f）に写す。計算だけで、Blender は使わない。

リポジトリ外の 2 作目の VRM のダンスで、2D の完成版の絵コンテの数値（2D 版のレンダラが絵の中の仮の部屋に置いたカメラ）を、3D の再現版の
カメラに写した台本の計算を移した。時刻の読み方（秒の数・印・拍）・区間（until）・つなぎ方（ease）・手持ちの揺れと打点の揺れ・踊りの
時刻の止めは plan.orbit と同じ。

部屋（room。render.box_room と同じ置き方。長さは仮の単位で、scale 倍が m）: 基準のカメラが原点で、右 x・下 y・奥 z。カメラは水平に奥を
向き、焦点距離 focal_px（画面の画素。画面は camera.resolution）、主点 center_px（画面の画素。水平線の消失点）。床はカメラの
camera_height 下。踊り手の立つ所 origin_px（画面の画素 [x, 床に立つ所の y]）の奥行き z_d = focal × camera_height ÷ (y − 主点の y)、
x_d = (x − 主点の x) × z_d ÷ focal。Blender の座標（X 右・Y 奥・Z 上、m）へは ((x − x_d) × scale, (z − z_d) × scale,
(camera_height − y) × scale)。

時刻: コマ T の t = time_offset + (T − 1) ÷ fps 秒。区間（shots）の頭は前の区間の until（最初は T = 1 の時刻）。踊りの時刻は t
（dance.holds の区間では止めた時刻）、動きのコマ f = dance.frame0 + dance.fps × 踊りの時刻。

ショット（区間の中の u = (t − 頭) ÷ (終わり − 頭) を 0〜1 に切り、前後のキー a・b を a の ease で混ぜた w）:
- キー: move（部屋の座標の移動 [右, 下, 奥]）、yaw（右へ +）・pitch（上へ +）・roll（度）、zoom（焦点距離 = zoom × focal_px）、
  anchor（追う点の名前か、踊り手の立つ面の上の画素 [x, y]）と at（それを置く画面の画素）、ease。
- 追う点（名前）: 点の通り道（smooth.sigma_s でならす）を、区間の頭から終わりまで follow.samples 点の踊りの時刻で読んで平均し、そこから
  follow（区間の follow か follow.default）の割合だけ、いまの踊りの時刻の点へ寄せる（ショットの中で少しだけ追う）。
- 揺れ: 手持ちの揺れ（区間の handheld が振れ幅。kicks が打点）を yaw・pitch・roll に足す（plan.orbit と同じ式）。
- fix（区間ごとの手直し。数か [頭, 終わり]（u でまっすぐ変える））: dx・dy・dz（move に足す）、dyaw・dpitch（度）、zoom（掛ける）、
  px・py（at に足す画素）。
- 向き: 前 = (sin yaw cos pitch, −sin pitch, cos yaw cos pitch)、右 = (cos yaw, 0, −sin yaw)、下 = 前 × 右。右と下を roll で回し、
  Blender の向き（右・上・−前）の行列にする。位置は move を Blender の座標に直したもの。
- 主点: 追う点がちょうど at の画素に来るように、主点（レンズのずらし）を解く（2D 版の「主点を解く」と同じ）。
回り込み（orbit のある区間）: orbit.keys（[[時刻, 方位°, 距離 m, 高さ m], …]。単調な 3 次の補間（行き過ぎない）でつなぎ、範囲の外は端）で、
点 orbit.centre（既定 hips）のまわりに置き、高さ orbit.aim_z の注視点を向く。焦点距離は縦の画角 orbit.fov × fix.zoom、主点は画面の中心 +
fix.px・py。fix.dyaw・radius（掛ける）・dz・aim_dz も使える。
"""
import math

import numpy as np

from .blender_adapter.point_track import PathError, Samples
from .io import ToolError
from .orbit_plan import EASE, handheld, rotation
from .stage_plan import EPS, Clock, find, inside, spans

DEFAULTS = {
    "time_offset": 0.0, "dancer": "hips",
    "smooth": {"sigma_s": 0.292, "reach": 3.0},
    "follow": {"samples": 25, "default": 0.8},
    "handheld": {"seed": 3, "hz": [0.9, 2.3, 5.1], "weights": [1.0, 0.6, 0.3]},
    "kick": {"tau": 0.13, "length": 0.6, "hz": [7.5, 8.3, 6.1], "phase": [0.0, 1.0, 2.0], "gain": [0.6, 1.0, 0.8]},
    "camera": {"resolution": [1080, 1920], "sensor_height": 36.0},
}
SECTIONS = ("smooth", "follow", "handheld", "kick", "camera")
FIX = ("dx", "dy", "dz", "dyaw", "dpitch", "zoom", "px", "py")
ORBIT_FIX = ("dyaw", "radius", "dz", "aim_dz", "zoom", "px", "py")
NEXT = 1e-3            # つなぎの最中に、相手の区間を選ぶときの時刻のずらし（試作と同じ）


def merged(plan):
    out = {**DEFAULTS, **{k: v for k, v in plan.items() if k not in SECTIONS}}
    for key in SECTIONS:
        out[key] = {**DEFAULTS[key], **plan.get(key, {})}
    return out


def lerp(a, b, w):
    return a + (b - a) * w


def pchip(xs, ys, x):
    """単調な 3 次の補間（行き過ぎない。Fritsch–Carlson の傾き。リポジトリ外の試作と同じ式と順）。"""
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    for k in range(1, n - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    m[0], m[-1] = d[0], d[-1]
    i = 0
    while i < n - 2 and x > xs[i + 1]:
        i += 1
    t = (x - xs[i]) / h[i]
    h00, h10, h01, h11 = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t, -2 * t**3 + 3 * t**2, t**3 - t**2
    return h00 * ys[i] + h10 * h[i] * m[i] + h01 * ys[i + 1] + h11 * h[i] * m[i + 1]


def fixed(fix, name, u, default):
    """手直しの値（数か [頭, 終わり] を u でまっすぐ変えたもの）。"""
    value = fix.get(name, default)
    if isinstance(value, list):
        return value[0] + (value[1] - value[0]) * u
    return value


class Room:
    """絵の中の仮の部屋（render.box_room と同じ置き方）。"""

    def __init__(self, room):
        self.f = float(room["focal_px"])
        self.cx, self.cy = (float(v) for v in room["center_px"])
        self.hc = float(room["camera_height"])
        self.k = float(room.get("scale", 1.0))
        ox, oy = (float(v) for v in room["origin_px"])
        if not oy > self.cy:
            raise ToolError(f"room.origin_px の y（{oy:g}）は主点の y（{self.cy:g}）より下（大きい値）にしてください（床に立つ所）")
        self.z_d = self.f * self.hc / (oy - self.cy)
        self.x_d = (ox - self.cx) * self.z_d / self.f

    def world(self, point):
        """部屋の座標（右・下・奥）→ Blender の座標（m）。"""
        return np.array([(point[0] - self.x_d) * self.k, (point[2] - self.z_d) * self.k, (self.hc - point[1]) * self.k])

    def on_plane(self, pixel):
        """踊り手の立つ面（奥行き z_d）の上の、基準のカメラの画素 → Blender の座標。"""
        z = self.z_d
        return self.world(((pixel[0] - self.cx) * z / self.f, (pixel[1] - self.cy) * z / self.f, z))


def direction(v):
    """部屋の向き（右・下・奥）→ Blender の向き（右・奥・上）。"""
    return np.array([v[0], v[2], -v[1]])


class Shots:
    def __init__(self, plan):
        p = merged(plan)
        self.p = p
        self.fps = float(p["fps"])
        self.frames = int(p["frames"])
        self.offset = float(p["time_offset"])
        self.clock = Clock(p.get("marks"), p.get("grid") or {"k0_time": 0.0, "period": 1.0})
        self.room = Room(p["room"])
        self.width, self.height = (float(v) for v in p["camera"]["resolution"])
        self.shots = p["shots"]
        self.spans = spans(self.shots, self.clock, "shots", start=self.offset)
        for n, (shot, (start, end)) in enumerate(zip(self.shots, self.spans)):
            if "orbit" in shot:
                keys = shot["orbit"]["keys"]
                times = [self.clock(k[0], f"shots[{n}].orbit.keys[{i}]") for i, k in enumerate(keys)]
                if len(keys) < 2 or any(b <= a for a, b in zip(times, times[1:])):
                    raise ToolError(f"shots[{n}].orbit.keys は時刻の昇順に 2 つ以上にしてください: {times}")
                continue
            keys = shot.get("keys") or []
            if not keys:
                raise ToolError(f"shots[{n}] には keys（カメラのキー）か orbit（回り込み）が要ります")
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
        self.phases = np.random.default_rng(int(p["handheld"]["seed"])).uniform(0, 2 * np.pi, (3, 3))
        self.wipes = [(self.clock(w["at"], f"wipes[{n}].at"), float(w["seconds"])) for n, w in enumerate(p.get("wipes", []))]
        names = {p["dancer"]}
        for shot in self.shots:
            if "orbit" in shot:
                names.add(shot["orbit"].get("centre", "hips"))
            for key in shot.get("keys") or []:
                if isinstance(key["anchor"], str):
                    names.add(key["anchor"])
        self.names = sorted(names)
        self._means = {}

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
            if inside(t, start, end):
                return at, True
        return t, False

    def shot_at(self, t):
        n = find(self.spans, t)
        if n is None:
            raise ToolError(f"{t:.4f} 秒がショット（shots）のどれにも入りません（最後のショットの until を省くか延ばしてください）")
        return n

    def position_in(self, n, t):
        start, end = self.spans[n]
        return 0.0 if math.isinf(end) else min(max((t - start) / (end - start), 0.0), 1.0)

    def keys_at(self, n, t):
        """ショット n の、時刻 t での前後のキーと混ぜる割合 w（キー a の ease）。"""
        keys = self.shots[n]["keys"]
        if len(keys) == 1:
            return keys[0], keys[0], 0.0
        u = self.position_in(n, t)
        us = [self._u(key, k, len(keys)) for k, key in enumerate(keys)]
        i = 0
        while i < len(keys) - 2 and u > us[i + 1]:
            i += 1
        a, b = keys[i], keys[i + 1]
        return a, b, EASE[a.get("ease", "smooth")]((u - us[i]) / max(1e-9, us[i + 1] - us[i]))

    def tracked(self, n, name, t, samples):
        """追う点: ショットの中の平均から、follow の割合だけいまの点へ寄せたもの。"""
        if (n, name) not in self._means:
            start, end = self.spans[n]
            if math.isinf(end):
                end = start
            times = np.linspace(start, end, int(self.p["follow"]["samples"]))
            points = [samples.at(name, self.dance_time(float(x))[0]) for x in times]
            self._means[(n, name)] = sum(points[1:], points[0]) / len(points)
        mean = self._means[(n, name)]
        follow = float(self.shots[n].get("follow", self.p["follow"]["default"]))
        return mean + follow * (samples.at(name, self.dance_time(t)[0]) - mean)

    def anchor(self, key, n, t, samples):
        if isinstance(key["anchor"], str):
            return self.tracked(n, key["anchor"], t, samples)
        return self.room.on_plane(key["anchor"])

    def camera(self, t, n, samples, shake=True):
        """ショット n の時刻 t のカメラ: (4×4 の行列, 焦点距離, 主点, 追う点（回り込みは None）, 狙いの画素（同じ））。"""
        shot = self.shots[n]
        fix = shot.get("fix", {})
        u = self.position_in(n, t)
        jitter = handheld(t, float(shot.get("handheld", 0.0)), self.phases, self.kicks, self.p["handheld"], self.p["kick"]) if shake \
            else [0.0, 0.0, 0.0]
        if "orbit" in shot:
            return self._orbit(shot, t, u, fix, jitter, samples)
        a, b, w = self.keys_at(n, t)
        move = [lerp(float(a["move"][i]), float(b["move"][i]), w) for i in range(3)]
        move = [move[0] + fixed(fix, "dx", u, 0.0), move[1] + fixed(fix, "dy", u, 0.0), move[2] + fixed(fix, "dz", u, 0.0)]
        yaw = math.radians(lerp(float(a.get("yaw", 0.0)), float(b.get("yaw", 0.0)), w) + jitter[0] + fixed(fix, "dyaw", u, 0.0))
        pitch = math.radians(lerp(float(a.get("pitch", 0.0)), float(b.get("pitch", 0.0)), w) + jitter[1] + fixed(fix, "dpitch", u, 0.0))
        roll = math.radians(lerp(float(a.get("roll", 0.0)), float(b.get("roll", 0.0)), w) + jitter[2])
        zoom = lerp(float(a.get("zoom", 1.0)), float(b.get("zoom", 1.0)), w) * fixed(fix, "zoom", u, 1.0)
        forward = np.array([math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)])
        right = np.array([math.cos(yaw), 0.0, -math.sin(yaw)])
        down = np.cross(forward, right)
        right2 = math.cos(roll) * right + math.sin(roll) * down
        down2 = -math.sin(roll) * right + math.cos(roll) * down
        position = self.room.world(move)
        right_b, up_b, forward_b = direction(right2), -direction(down2), direction(forward)
        matrix = np.eye(4)
        matrix[:3, 0], matrix[:3, 1], matrix[:3, 2], matrix[:3, 3] = right_b, up_b, -forward_b, position
        focal = zoom * self.room.f
        anchor = lerp(self.anchor(a, n, t, samples), self.anchor(b, n, t, samples), w)
        target = (lerp(float(a["at"][0]), float(b["at"][0]), w) + fixed(fix, "px", u, 0.0),
                  lerp(float(a["at"][1]), float(b["at"][1]), w) + fixed(fix, "py", u, 0.0))
        relative = anchor - position
        depth = float(relative @ forward_b)
        if depth <= 1e-9:
            raise ToolError(f"{t:.4f} 秒: 追う点がカメラの後ろにあります（shots[{n}] の move・yaw・pitch を確かめてください）")
        u0, v0 = focal * float(relative @ right_b) / depth, -focal * float(relative @ up_b) / depth
        principal = (target[0] - u0, target[1] - v0)
        return matrix, focal, principal, anchor, target

    def _orbit(self, shot, t, u, fix, jitter, samples):
        orbit = shot["orbit"]
        keys = orbit["keys"]
        times = [self.clock(k[0], "orbit.keys") for k in keys]
        x = min(max(t, times[0]), times[-1])
        theta = math.radians(pchip(times, [float(k[1]) for k in keys], x))
        radius = pchip(times, [float(k[2]) for k in keys], x)
        high = pchip(times, [float(k[3]) for k in keys], x)
        theta += math.radians(fixed(fix, "dyaw", u, 0.0))
        radius *= fixed(fix, "radius", u, 1.0)
        centre = samples.at(orbit.get("centre", "hips"), self.dance_time(t)[0])
        position = np.array([centre[0] + radius * math.sin(theta), centre[1] - radius * math.cos(theta), high + fixed(fix, "dz", u, 0.0)])
        aim = np.array([centre[0], centre[1], float(orbit.get("aim_z", 1.0)) + fixed(fix, "aim_dz", u, 0.0)])
        forward = aim - position
        forward = forward / np.linalg.norm(forward)
        right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
        if float(np.linalg.norm(right)) < 1e-9:
            raise ToolError(f"{t:.4f} 秒: 回り込みのカメラが注視点の真上か真下にあり、右の向きが決まりません")
        right = right / np.linalg.norm(right)
        up = np.cross(right, forward)
        basis = np.stack([right, up, -forward], axis=1) @ rotation("y", -jitter[0]) @ rotation("x", jitter[1]) @ rotation("z", jitter[2])
        matrix = np.eye(4)
        matrix[:3, :3], matrix[:3, 3] = basis, position
        focal = self.height / 2 / math.tan(math.radians(float(orbit.get("fov", 48.0))) / 2) * fixed(fix, "zoom", u, 1.0)
        principal = (self.width / 2 + fixed(fix, "px", u, 0.0), self.height / 2 + fixed(fix, "py", u, 0.0))
        return matrix, focal, principal, None, None

    def entry(self, T, f, matrix, focal, principal, dancer):
        position = matrix[:3, 3]
        return {"T": T, "f": f, "matrix": [[float(v) for v in row] for row in matrix], "focal_px": float(focal),
                "principal_px": [float(principal[0]), float(principal[1])],
                "lens_mm": float(focal / self.height * self.p["camera"]["sensor_height"]),
                "shift": [float((self.width / 2 - principal[0]) / self.height), float((principal[1] - self.height / 2) / self.height)],
                "dancer": [float(v) for v in dancer], "distance_m": float(np.linalg.norm(np.asarray(dancer) - position))}


def project(matrix, focal, principal, point):
    """点を、カメラ（行列・焦点距離・主点）で写した画素（x 右・y 下）。"""
    matrix = np.asarray(matrix, float)
    relative = np.asarray(point, float) - matrix[:3, 3]
    depth = -float(relative @ matrix[:3, 2])
    return (principal[0] + focal * float(relative @ matrix[:3, 0]) / depth, principal[1] - focal * float(relative @ matrix[:3, 1]) / depth)


def read_points(path, names, frame0, fps, sigma_s, reach):
    try:
        return Samples(path, {name: sigma_s for name in names}, frame0, fps, reach)
    except PathError as error:
        raise ToolError(str(error)) from error


def build(plan, path):
    """(cameras, track, alt, report)。cameras は model3d-cameras/0.1（並びの全部の T）、alt は wipes の間の相手のショットのカメラ
    （model3d-cameras/0.1。wipes が無ければ None）、track は model3d-stage-track/0.1（T と f だけ）。report の alt_track は、相手のカメラの
    コマだけの並び（render.passes で相手の要素を描くトラック）。"""
    shots = Shots(plan)
    p = shots.p
    smooth = p["smooth"]
    samples = read_points(path, shots.names, shots.frame0, shots.dance_fps, float(smooth["sigma_s"]), float(smooth["reach"]))
    width, height = (int(v) for v in p["camera"]["resolution"])
    entries, rows, alt, hits, worst, held, outside = [], [], [], [], 0.0, 0, 0
    for T in range(1, shots.frames + 1):
        t = shots.time(T)
        n = shots.shot_at(t)
        dance_t, frozen = shots.dance_time(t)
        held += frozen
        outside += samples.outside(dance_t)
        f = shots.frame0 + shots.dance_fps * dance_t
        f = round(f) if abs(f - round(f)) < 1e-9 else f
        dancer = samples.at(p["dancer"], dance_t)
        matrix, focal, principal, anchor, target = shots.camera(t, n, samples)
        if anchor is not None:
            seen = project(matrix, focal, principal, anchor)
            worst = max(worst, math.hypot(seen[0] - target[0], seen[1] - target[1]))
        entries.append(shots.entry(T, f, matrix, focal, principal, dancer))
        rows.append({"T": T, "f": f})
        hits.append(n)
        for at, seconds in shots.wipes:                  # つなぎの最中は、相手のショットのカメラも同じ時刻で
            if abs(t - at) < seconds / 2:                  # at の前か後かは、ショットの境目と同じく EPS 秒前のコマから後の側
                other = shots.shot_at(at + NEXT if t < at - EPS else at - NEXT)
                m2, f2, p2, _, _ = shots.camera(t, other, samples)
                alt.append({**shots.entry(T, f, m2, f2, p2, dancer), "shot": other})
                break
    cameras = {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [width, height], "fps": shots.fps,
               "sensor_height_mm": float(p["camera"]["sensor_height"]), "frames": entries}
    alt_cameras = None if not shots.wipes else {
        "schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [width, height], "fps": shots.fps,
        "sensor_height_mm": float(p["camera"]["sensor_height"]), "frames": [{k: v for k, v in e.items() if k != "shot"} for e in alt]}
    track = {"schema_version": "model3d-stage-track/0.1", "fps": shots.fps, "frames": rows}
    references = {}
    for n, ref in enumerate(p.get("references", [])):     # 揺れなしの、ショットの頭（か at）のカメラ（手前の板を置く基準など）
        index = next((k for k, s in enumerate(shots.shots) if s.get("name") == ref["shot"]), None)
        if index is None:
            raise ToolError(f"references[{n}] のショット {ref['shot']!r} がありません（shots の name）")
        at = shots.clock(ref["at"], f"references[{n}].at") if "at" in ref else shots.spans[index][0]
        matrix, focal, principal, _, _ = shots.camera(at, index, samples, shake=False)
        references[ref["name"]] = {"shot": ref["shot"], "time": at, "matrix": [[float(v) for v in row] for row in matrix],
                                   "focal_px": float(focal), "principal_px": [float(principal[0]), float(principal[1])]}
    spans_out = []
    for n, (start, end) in enumerate(shots.spans):
        Ts = [T for T, k in zip(range(1, shots.frames + 1), hits) if k == n]
        item = {"index": n, "kind": "orbit" if "orbit" in shots.shots[n] else "keys", "from": start,
                "until": None if math.isinf(end) else end, "T": [Ts[0], Ts[-1]] if Ts else None, "frames": len(Ts)}
        if shots.shots[n].get("name"):
            item["name"] = shots.shots[n]["name"]
        spans_out.append(item)
    report = {"schema_version": "model3d-shot-report/0.1", "frames": shots.frames, "fps": shots.fps,
              "seconds": [shots.time(1), shots.time(shots.frames)], "points": shots.names, "dancer": p["dancer"],
              "room": {"origin_depth": shots.room.z_d, "origin_x": shots.room.x_d, "scale": shots.room.k},
              "samples": {"count": samples.count, "per_second": samples.rate, "dance_seconds": list(samples.span),
                          "sigma_s": float(smooth["sigma_s"]), "outside": outside},
              "anchor_error_px": worst, "shots": spans_out, "held_frames": int(held),
              "alt": [{"T": e["T"], "shot": e["shot"]} for e in alt], "references": references,
              "alt_track": None if not shots.wipes else {"schema_version": "model3d-stage-track/0.1", "fps": shots.fps,
                                                         "frames": [{"T": e["T"], "f": e["f"]} for e in alt]},
              "f": [rows[0]["f"], rows[-1]["f"]]}
    return cameras, track, alt_cameras, report
