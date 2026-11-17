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

リポジトリ外の 3 作目の VRM のダンスの演出版のカメラ（台本の camera_at）を書けるように、どれも書いたときだけ効く指定を足した（省けば
今までと同じ値）:
  - キーの el（仰角。度）: 狙う点のまわりの球の座標で置く。dist は狙う点からのまっすぐの距離で、位置 = 狙う点 + dist × (sin θ cos el,
    −cos θ cos el, sin el)（+ 揺れの横・上下）。寄りと揺れ（effects）の距離も、このまっすぐの距離に掛ける。
  - キーの lens（mm）: 画角の代わりにレンズを混ぜる（焦点距離 = 画面の高さ × lens ÷ camera.sensor_height）。
  - ease の in2・out2: 2 乗のつなぎ方（u²・1 − (1 − u)²）。
  - キーの offset（[x, y, z] m）: 狙う点を世界の座標でずらす（dz の代わり）。
  - キーの follow（0〜1）: 狙う点 = 区間のコマの生の点の平均 × (1 − follow) + その時刻の点 × follow（区間の中で少しだけ追う）。
  - smooth.per_shot: 点を、通り道の全体でなく区間のコマの並びだけでならす（端は端の値で延ばす。σ は sigma_s × fps コマ、
    ±ceil(reach × σ) コマ）。区間の外の動きを、つなぎ目の前後のカメラに入れない。
  - 区間の fstop（f 値）とキーの focus（ピントの点の名前）: 絞りのある区間のコマの cameras に fstop と focus_m（カメラの位置から
    ピントの点までのまっすぐの距離 m）を書く。focus はキーの間で混ぜる（ピントが手前から奥へ移る）。どちらのキーにも focus が
    無ければ、ピントは狙う点。ピントの点は smooth.focus_sigma_s（省くと sigma_s）でならす（follow・offset は掛けない）。
  - handheld.form: orbit（rotate の代わり）: 手持ちの揺れを、カメラの向きを回すのでなく、方位・仰角・傾きを決まった正弦の和で
    動かす（区間の seed。省くと区間の番号）。方位 += a (sin(5.1 t + s) + 0.6 sin(11.3 t + 2 s))、仰角 += 0.7 a (sin(6.7 t + 1.3 s)
    + 0.5 sin(13.1 t + s))、傾き += 0.8 a sin(4.3 t + 0.7 s)（a は区間の handheld）。仰角 el のキーの区間で使う。kicks は効かない。
"""
import math

import numpy as np

from .blender_adapter.point_track import PathError, Samples
from .io import ToolError
from .stage_plan import Camera, Clock, _ranges, ease as plan_ease, find, inside, smoothstep, spans  # noqa: F401（smoothstep は以前の名前）


def _clamped(u):
    return min(1.0, max(0.0, u))


def _in2(u):
    u = _clamped(u)
    return u * u


def _out2(u):
    u = _clamped(u)
    return 1 - (1 - u) * (1 - u)


EASE = {
    "smooth": lambda u: plan_ease(u, "smooth"),
    "linear": lambda u: plan_ease(u, "linear"),
    "out": lambda u: 1 - (1 - min(1.0, max(0.0, u))) ** 3,
    "in": lambda u: min(1.0, max(0.0, u)) ** 3,
    "whip": lambda u: 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)) ** 1.15),
    "in2": _in2,
    "out2": _out2,
}
DEFAULTS = {
    "time_offset": 0.0, "centre": "hips", "min_height": 0.12,
    "smooth": {"sigma_s": 0.292, "reach": 3.0, "per_shot": False, "focus_sigma_s": None},
    "up": {"from": 0.6, "span": 0.35},
    "handheld": {"seed": 3, "hz": [0.9, 2.3, 5.1], "weights": [1.0, 0.6, 0.3], "form": "rotate"},
    "kick": {"tau": 0.13, "length": 0.6, "hz": [7.5, 8.3, 6.1], "phase": [0.0, 1.0, 2.0], "gain": [0.6, 1.0, 0.8]},
    "camera": {"resolution": [1080, 1920], "sensor_height": 36.0},
}
SNAP = 1e-9            # 点を読む時刻が標本の時刻からこの標本の数より近ければ、その標本の値そのもの（区間の生の点）
SECTIONS = ("smooth", "up", "handheld", "kick", "camera")


def merged(plan, defaults=DEFAULTS, sections=SECTIONS):
    """表に既定（defaults）を足したもの。sections の節は、節の中の名前ごとに既定と混ぜる。plan.shots も自分の既定で使う。"""
    out = {**defaults, **{k: v for k, v in plan.items() if k not in sections}}
    for key in sections:
        out[key] = {**defaults[key], **plan.get(key, {})}
    return out


def lerp(a, b, w):
    return a + (b - a) * w


def rotation(axis, degrees):
    """軸 axis（"x"・"y"・"z"）のまわりに degrees 度回す 3×3 の行列（右手系。Blender の Matrix.Rotation と同じ向き）。"""
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    if axis == "x":
        return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])
    if axis == "y":
        return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def handheld(t, amplitude, phases, kicks, hand, kick):
    """手持ちの揺れ（yaw・pitch・roll の度）: 振れ幅 amplitude × Σ weights[m] × sin(2π hz[m] t + 位相)（位相は handheld.seed の乱数）と、
    打点 kicks（[(時刻, 度)]）から length 秒の揺れ。振れ幅が 0 なら、打点の揺れも入れない（試作と同じ）。plan.orbit と plan.shots が使う。"""
    values = []
    for j in range(3):
        total = None
        for m in range(3):          # 試作の式と同じ順: s0 + 0.6 s1 + 0.3 s2（重み 1 は掛けない）
            term = math.sin(2 * math.pi * hand["hz"][m] * t + phases[j, m])
            term = term if hand["weights"][m] == 1.0 else hand["weights"][m] * term
            total = term if total is None else total + term
        values.append(amplitude * total)
    if amplitude > 0:
        for at, strength in kicks:
            dt = t - at
            if 0 <= dt < kick["length"]:
                k = strength * math.exp(-dt / kick["tau"])
                for j in range(3):
                    values[j] += k * math.sin(2 * math.pi * kick["hz"][j] * dt + kick["phase"][j]) * kick["gain"][j]
    return values


def orbit_shake(t, amplitude, seed):
    """揺れの形 orbit（3 作目の VRM のダンスの台本の形）: 方位・仰角・傾きに足す度。"""
    az = amplitude * (math.sin(t * 5.1 + seed) + 0.6 * math.sin(t * 11.3 + 2 * seed))
    el = amplitude * 0.7 * (math.sin(t * 6.7 + 1.3 * seed) + 0.5 * math.sin(t * 13.1 + seed))
    roll = amplitude * 0.8 * math.sin(t * 4.3 + 0.7 * seed)
    return az, el, roll


def exact_at(samples, name, time):
    """点 name の踊りの時刻 time での値。時刻が標本の時刻にちょうど当たれば（SNAP の中）、その標本の値そのもの（補間の丸めを入れない）。"""
    position = (time - samples.start) * samples.rate
    nearest = int(round(position))
    if abs(position - nearest) < SNAP and 0 <= nearest < samples.count:
        return samples.columns[name][nearest]
    return samples.at(name, time)


def key_u(key, k, count):
    """区間の中のキー k（count 個のうち）の割合 u。書いていなければ、最初のキーは 0、最後のキーは 1。plan.orbit と plan.shots が使う。"""
    if "u" in key:
        return float(key["u"])
    if k == 0:
        return 0.0
    if k == count - 1:
        return 1.0
    raise ToolError("区間の中のキー（最初と最後のほか）には u（区間の中の割合 0〜1）が要ります")


def check_keys(n, keys, end):
    """区間 n（終わり end 秒）のキーの並びを確かめる: 2 つ以上なら until が要り、u は 0〜1 の昇順。"""
    if len(keys) > 1 and math.isinf(end):
        raise ToolError(f"shots[{n}] はキーが 2 つ以上なので until が要ります（区間の中の割合でキーを混ぜるため）")
    us = [key_u(key, k, len(keys)) for k, key in enumerate(keys)]
    if any(b < a for a, b in zip(us, us[1:])) or us[0] < 0 or us[-1] > 1:
        raise ToolError(f"shots[{n}] のキーの u は 0〜1 の昇順にしてください: {us}")


def blend_keys(keys, u):
    """区間の中の割合 u での前後のキー a・b と、混ぜる割合 w（キー a の ease。試作と同じ選び方）。キーは 2 つ以上。"""
    us = [key_u(key, k, len(keys)) for k, key in enumerate(keys)]
    i = 0
    while i < len(keys) - 2 and u > us[i + 1]:
        i += 1
    a, b = keys[i], keys[i + 1]
    return a, b, EASE[a.get("ease", "smooth")]((u - us[i]) / max(1e-9, us[i + 1] - us[i]))


def dance_holds(clock, dance):
    """踊りの時刻の止め [(頭, 終わり, 止める踊りの時刻)]（dance.holds。until を省くと、その先ずっと）。"""
    holds = []
    for n, hold in enumerate(dance.get("holds", [])):
        start = clock(hold["from"], f"dance.holds[{n}].from")
        end = clock(hold["until"], f"dance.holds[{n}].until") if "until" in hold else math.inf
        if end <= start:
            raise ToolError(f"dance.holds[{n}] の until（{end:g} 秒）が from（{start:g} 秒）より後ではありません")
        holds.append((start, end, float(hold["at"])))
    return holds


def held_time(holds, t):
    """時刻 t の踊りの時刻と、止めたか。"""
    for start, end, at in holds:
        if inside(t, start, end):                    # 境目は plan.stage と同じく、EPS 秒前のコマから止める側
            return at, True
    return t, False


class Keyed:
    """plan.orbit と plan.shots に共通の読み方: コマの時刻、踊りの時刻と止め、手持ちの揺れの打点と位相、区間の中のキーの割合。"""

    _u = staticmethod(key_u)

    def _read_timing(self, p):
        """表（既定を足したもの）と、コマの fps・数・時刻のずらし・時刻の読み方。"""
        self.p = p
        self.fps = float(p["fps"])
        self.frames = int(p["frames"])
        self.offset = float(p["time_offset"])
        self.clock = Clock(p.get("marks"), p.get("grid") or {"k0_time": 0.0, "period": 1.0})

    def _read_dance(self, p):
        """踊りの時刻（動きのコマへの直し方と止め）と、手持ちの揺れの打点と位相。"""
        dance = p["dance"]
        self.frame0, self.dance_fps = float(dance.get("frame0", 1.0)), float(dance["fps"])
        self.holds = dance_holds(self.clock, dance)
        self.kicks = [(self.clock(k["at"], f"kicks[{n}].at"), float(k["deg"])) for n, k in enumerate(p.get("kicks", []))]
        self.phases = np.random.default_rng(int(p["handheld"]["seed"])).uniform(0, 2 * np.pi, (3, 3))

    def time(self, T):
        return self.offset + (T - 1) / self.fps

    def dance_time(self, t):
        return held_time(self.holds, t)


class Orbit(Keyed):
    def __init__(self, plan):
        self._read_timing(merged(plan))
        p = self.p
        beat_push = any(effect.get("kind") == "beat_push" for effect in p.get("effects", []))
        if p.get("grid") is None and (_uses_beats(plan) or beat_push):   # 光のキーの無い plan.orbit には、ほかに拍の出どころが無い
            raise ToolError("段取り表に拍 {\"beat\": k} か beat_push（拍ごとの寄り）がありますが、grid（拍の格子）がありません")
        self.shots = p["shots"]
        self.spans = spans(self.shots, self.clock, "shots", start=self.offset)   # 最初の区間の頭は T = 1 の時刻（u をそこから数える）
        self.form = p["handheld"]["form"]
        for n, (shot, (start, end)) in enumerate(zip(self.shots, self.spans)):
            check_keys(n, shot["keys"], end)
            _check_modes(n, shot, self.form)
        self._read_dance(p)
        self.effects = Camera({"camera": {"effects": p.get("effects", [])}}, self.clock) if p.get("effects") else None
        self.names = sorted({p["centre"]} | {key["aim"] for shot in self.shots for key in shot["keys"]})
        smooth = p["smooth"]
        self.per_shot = bool(smooth["per_shot"])
        self.reach = float(smooth["reach"])
        self.sigma_frames = float(smooth["sigma_s"]) * self.fps
        focus_sigma = smooth["focus_sigma_s"] if smooth["focus_sigma_s"] is not None else smooth["sigma_s"]
        self.focus_sigma_s = float(focus_sigma)
        self.focus_sigma_frames = self.focus_sigma_s * self.fps
        dof = [shot for shot in self.shots if shot.get("fstop") is not None]
        self.focus_names = sorted({key.get("focus", key["aim"]) for shot in dof for key in shot["keys"]})
        followed = {key["aim"] for shot in self.shots for key in shot["keys"] if "follow" in key}
        self.raw_names = sorted((set(self.names) | set(self.focus_names)) if self.per_shot else followed)
        self._shot_T = None
        self._series = {}

    def shot_frames(self, n):
        """区間 n に入るコマ T の並び（camera と同じ境目の決め方）。"""
        if self._shot_T is None:
            self._shot_T = {}
            for T in range(1, self.frames + 1):
                self._shot_T.setdefault(find(self.spans, self.time(T)), []).append(T)
        return self._shot_T.get(n, [])

    def _shot_series(self, n, name, sigma, raw):
        """区間 n のコマの並びの生の点（raw から）と、区間の中だけでならした点（σ コマ。±ceil(reach × σ)。端は端の値で延ばす）と、生の
        点の平均。(最初の T, ならした点, 平均)。"""
        key = (n, name, sigma)
        if key not in self._series:
            from .blender_adapter.stage_render import gauss_smooth
            Ts = self.shot_frames(n)
            values = np.stack([exact_at(raw, name, self.dance_time(self.time(T))[0]) for T in Ts])
            smooth = gauss_smooth(values, sigma, int(math.ceil(self.reach * sigma))) if sigma > 0 else values
            self._series[key] = (Ts[0], smooth, values.mean(axis=0))
        return self._series[key]

    def _point(self, key, n, T, dance_t, samples, points):
        """キーの狙う点（ならした点。follow があれば区間の生の点の平均と混ぜ、offset か dz を足す）。"""
        name = key["aim"]
        if self.per_shot:
            first, smooth, mean = self._shot_series(n, name, self.sigma_frames, _need(points, "raw"))
            live = smooth[T - first]
        else:
            live = samples.at(name, dance_t)
            if "follow" in key:
                mean = self._shot_series(n, name, 0.0, _need(points, "raw"))[2]
        if "follow" in key:
            follow = float(key["follow"])
            live = mean * (1 - follow) + live * follow
        if "offset" in key:
            return live + np.array([float(v) for v in key["offset"]])
        return live + np.array([0.0, 0.0, float(key.get("dz", 0.0))])

    def _focus_point(self, key, n, T, dance_t, points):
        """キーのピントの点（focus か、無ければ狙う点の名前の点を、ピントのならしで。follow・offset は掛けない）。"""
        name = key.get("focus", key["aim"])
        if self.per_shot:
            first, smooth, _ = self._shot_series(n, name, self.focus_sigma_frames, _need(points, "raw"))
            return smooth[T - first]
        return _need(points, "focus").at(name, dance_t)

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
        return blend_keys(keys, min(max((t - start) / (end - start), 0.0), 1.0))

    def jitter(self, t, amplitude):
        """手持ちの揺れ（yaw・pitch・roll の度。handheld）。"""
        return handheld(t, amplitude, self.phases, self.kicks, self.p["handheld"], self.p["kick"])

    def camera(self, T, samples, points=None):
        """コマ T のカメラ: (4×4 の行列, 焦点距離, 踊り手の点, 動きのコマ f, 区間の番号, 止めたか)。"""
        out = self.frame(T, samples, points)
        return out["matrix"], out["focal"], out["centre"], out["f"], out["shot"], out["held"]

    def frame(self, T, samples, points=None):
        """コマ T のカメラ（辞書）: matrix・focal・centre・f・shot・held と、lens（キーの lens で混ぜたときの mm。ほかは None）・
        dof（絞りのある区間なら (f 値, ピントまでの距離 m)。ほかは None）。points は、生の点（"raw"）とピントの点（"focus"）の
        Samples（follow・smooth.per_shot・絞りのときに要る。build が作る）。"""
        t = self.time(T)
        n = find(self.spans, t)
        if n is None:
            raise ToolError(f"{t:.4f} 秒がカメラの区間（shots）のどれにも入りません（最後の区間の until を省くか延ばしてください）")
        shot = self.shots[n]
        a, b, w = self.keys_at(n, t)
        dance_t, held = self.dance_time(t)
        centre = samples.at(self.p["centre"], dance_t)
        aim_a = self._point(a, n, T, dance_t, samples, points)
        aim_b = self._point(b, n, T, dance_t, samples, points)
        aim = lerp(aim_a, aim_b, w)
        az, radius = lerp(float(a["az"]), float(b["az"]), w), lerp(float(a["dist"]), float(b["dist"]), w)
        spherical = "el" in a
        if spherical:
            el = lerp(float(a["el"]), float(b["el"]), w)
        else:
            high_a = _height(a["height"], aim_a)
            high_b = _height(b["height"], aim_b)
            height = lerp(high_a, high_b, w)
        sx = sz = 0.0
        if self.effects is not None and shot.get("effects", True):
            for effect in self.effects.effects:
                radius, az, sx, sz = self.effects._apply(effect, t, radius, az, sx, sz)
        key_roll = lerp(float(a.get("roll", 0.0)), float(b.get("roll", 0.0)), w)
        amplitude = float(shot.get("handheld", 0.0))
        if self.form == "orbit":
            yaw = pitch = None
            roll = 0.0
            if amplitude > 0:
                d_az, d_el, roll = orbit_shake(t, amplitude, shot.get("seed", n))
                az, el = az + d_az, el + d_el
        else:
            yaw, pitch, roll = self.jitter(t, amplitude)
        theta = math.radians(az)
        if spherical:
            phi = math.radians(el)
            position = aim + np.array([math.sin(theta) * math.cos(phi), -math.cos(theta) * math.cos(phi), math.sin(phi)]) * radius
            if sx or sz:
                position = position + np.array([sx, 0.0, sz])
            if position[2] < float(self.p["min_height"]):
                position[2] = float(self.p["min_height"])
        else:
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
        basis = np.stack([right, up, -forward], axis=1)
        if yaw is not None:                              # 揺れの形 orbit は向きを回さない（方位・仰角・傾きで揺らした）
            basis = basis @ rotation("y", -yaw) @ rotation("x", pitch)
        basis = basis @ rotation("z", roll + key_roll)
        matrix = np.eye(4)
        matrix[:3, :3] = basis
        matrix[:3, 3] = position
        lens = None
        if "lens" in a:
            lens = lerp(float(a["lens"]), float(b["lens"]), w)
            focal = self.p["camera"]["resolution"][1] * lens / float(self.p["camera"]["sensor_height"])
        else:
            fov = lerp(float(a["fov"]), float(b["fov"]), w)
            focal = self.p["camera"]["resolution"][1] / 2 / math.tan(math.radians(fov) / 2)
        dof = None
        if shot.get("fstop") is not None:
            if "focus" in a or "focus" in b:
                point = lerp(self._focus_point(a, n, T, dance_t, points), self._focus_point(b, n, T, dance_t, points), w)
            else:
                point = aim
            dof = (float(shot["fstop"]), float(np.linalg.norm(point - position)))
        return {"matrix": matrix, "focal": focal, "centre": centre, "f": self.motion_frame(dance_t), "shot": n, "held": held,
                "lens": lens, "dof": dof}


def _need(points, kind):
    """build が作った点の読み方（raw: 生の点、focus: ピントの点）。無ければ止める（camera を直接呼んだとき）。"""
    if not points or points.get(kind) is None:
        raise ToolError(f"plan.orbit: {'follow・smooth.per_shot' if kind == 'raw' else 'ピントの点'}には、点の読み方 {kind} が要ります"
                        "（build が作る）")
    return points[kind]


def _check_modes(n, shot, form):
    """区間のキーの書き方をそろえる: 高さは height か el、画角は fov か lens、狙う点のずらしは dz か offset。揺れの形 orbit は el の区間で。"""
    keys = shot["keys"]
    for one, other, what in (("height", "el", "高さ"), ("fov", "lens", "画角"), ("dz", "offset", "狙う点のずらし")):
        for k, key in enumerate(keys):
            if one in key and other in key:
                raise ToolError(f"shots[{n}].keys[{k}] の{what}は {one} か {other} のどちらか 1 つにしてください")
        if what != "狙う点のずらし" and len({one in key for key in keys}) > 1:
            raise ToolError(f"shots[{n}] のキーの{what}は、どれも {one} か、どれも {other} にそろえてください（混ぜるため）")
    if form == "orbit" and float(shot.get("handheld", 0.0)) > 0 and "el" not in keys[0]:
        raise ToolError(f"shots[{n}]: 揺れの形 orbit（handheld.form）は、仰角 el のキーの区間で使ってください（方位と仰角を揺らすため）")


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
    points = {"raw": read_points(path, orbit.raw_names, orbit.frame0, orbit.dance_fps, 0.0, float(smooth["reach"]))
              if orbit.raw_names else None,
              "focus": read_points(path, orbit.focus_names, orbit.frame0, orbit.dance_fps, orbit.focus_sigma_s, float(smooth["reach"]))
              if orbit.focus_names and not orbit.per_shot else None}
    width, height = p["camera"]["resolution"]
    entries, rows, hits, held, outside, dof_frames = [], [], [], [], 0, 0
    for T in range(1, orbit.frames + 1):
        out = orbit.frame(T, samples, points)
        matrix, focal, dancer, f, n, frozen = out["matrix"], out["focal"], out["centre"], out["f"], out["shot"], out["held"]
        dance_t, _ = orbit.dance_time(orbit.time(T))
        outside += samples.outside(dance_t)
        position = matrix[:3, 3]
        lens_mm = out["lens"] if out["lens"] is not None else focal / height * p["camera"]["sensor_height"]
        entry = {"T": T, "f": f, "matrix": [[float(v) for v in row] for row in matrix], "focal_px": float(focal),
                 "principal_px": [width / 2, height / 2], "lens_mm": float(lens_mm),
                 "shift": [0.0, 0.0], "dancer": [float(v) for v in dancer],
                 "distance_m": float(np.linalg.norm(np.asarray(dancer) - position))}
        if out["dof"] is not None:
            entry["fstop"], entry["focus_m"] = out["dof"]
            dof_frames += 1
        entries.append(entry)
        rows.append({"T": T, "f": f})
        hits.append(n)
        held.append(frozen)
    cameras = {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [int(width), int(height)], "fps": orbit.fps,
               "sensor_height_mm": float(p["camera"]["sensor_height"]), "frames": entries}
    track = {"schema_version": "model3d-stage-track/0.1", "fps": orbit.fps, "frames": rows}
    shots = _ranges(orbit.spans, hits, [shot.get("name") for shot in orbit.shots], range(1, orbit.frames + 1))   # plan.stage と同じ報告
    report = {"schema_version": "model3d-orbit-report/0.1", "frames": orbit.frames, "fps": orbit.fps,
              "seconds": [orbit.time(1), orbit.time(orbit.frames)], "points": orbit.names, "centre": p["centre"],
              "samples": {"count": samples.count, "per_second": samples.rate, "dance_seconds": list(samples.span),
                          "sigma_s": float(smooth["sigma_s"]), "outside": outside},
              "shots": shots, "held_frames": int(sum(held)),
              "f": [rows[0]["f"], rows[-1]["f"]]}
    if orbit.focus_names:                                # 絞りのある区間（無ければ今までと同じ報告）
        report["dof"] = {"frames": dof_frames, "focus_points": orbit.focus_names, "focus_sigma_s": orbit.focus_sigma_s}
    if orbit.per_shot:
        report["samples"]["per_shot"] = True
    return cameras, track, report
