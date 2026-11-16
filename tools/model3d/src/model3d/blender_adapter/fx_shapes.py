"""効果の形（3D の四角形の集まりと頂点の色）を作る。numpy だけを使う（render.passes の fx の要素が Blender の中で呼ぶ。CI でも試せる）。

リポジトリ外の 2 作目の VRM のダンスの演出版の描画の台本で、毎コマ頂点の色つきのメッシュとして描いた効果の形の計算を、同じ式・同じ順・
同じ乱数の引き方で移した。形は時刻だけで決まる（乱数の種は固定。どのコマから描いても同じ）。長さは m、時刻は秒。

    shapes = Shapes(settings)                 乱数の配列を作り置く（種 seed と数から。台本と同じ順に引く）
    quads, colors = shapes.frame(state)       そのコマの四角形 (N, 4, 3) と頂点の色 (N, 4, 3)（線形の値。光として足す色）

state（そのコマの値）: tf（効果の時刻）・dance_t（踊りの時刻）・rate（時間の進み方。止めた間は 0）・camera（4×4 の行列。位置と右・上の
向き）・levels（embers・trails・sparks・ring・flare の強さ）・points（point_track.Samples。点を踊りの時刻で読む）。

形（settings の名前。省いた形は描かない。値は既定に重ねる。既定は試作の値）:
- embers（火の粉）: 中心 centre のまわりの輪（半径 radius）をゆっくり回りながら上り、height で生まれ直す粒。動く向きに伸びる筋。
- bursts（はじけ）: times の時刻に、点 origin（その時刻の姿勢。hold より後は hold の時刻）から飛び散る粒。
- sparks（火花）: 点 hands の通った跡から、少し遅れて舞い上がる粒（手が速いほど明るい）。
- trails（炎の帯）: 点 hands の通り道を seconds 秒つないだ帯。外側の炎と白っぽい芯の 2 枚。真上から見るときは steep_weaken だけ弱める。
- ring（炎の輪）: 中心のまわりの半径 radius の、根もとが太く先が細い揺らぐ舌と、床の照り返しの輪。カメラから near_fade 以内は消す。
筋（火の粉・はじけ・火花）はカメラを向いた細い四角形で、カメラから near 以内は描かず、画面の上で大きくなりすぎないよう抑える。
"""
import math

import numpy as np

UP = np.array([0.0, 0.0, 1.0])
EMPTY = (np.zeros((0, 4, 3)), np.zeros((0, 4, 3)))
DEFAULTS = {
    "seed": 11, "near": 0.9, "size_per_m": 0.003, "length_per_m": 0.02, "length_max": 0.07, "streak": 0.03, "tail": 0.25,
    "embers": {"count": 900, "centre": [0.0, 0.0], "radius": [0.3, 7.5], "omega": 0.3, "height": 6.0, "rise": [0.25, 0.9],
               "size": [0.004, 0.009], "color": [1.0, 0.55, 0.16]},
    "bursts": {"count": 320, "origin": "chest", "times": [], "speed": [1.5, 5.0], "size": [0.005, 0.011], "life": 2.4,
               "drag": 2.2, "lift": 0.5, "fade": 0.9, "color": [1.0, 0.66, 0.24], "hold": None},
    "sparks": {"count": 170, "hands": ["hand_l", "hand_r"], "life": 0.9, "spread": 0.28, "lift": 0.45, "gravity": 0.6,
               "size": [0.004, 0.008], "color": [1.0, 0.72, 0.3]},
    "trails": {"hands": ["hand_l", "hand_r"], "phase": [0.0, 2.1], "samples": 48, "seconds": 0.8, "steep_weaken": 0.6,
               "steep": [0.6, 0.35]},
    "ring": {"count": 460, "centre": [0.0, 0.0], "radius": 2.25, "steps": 6, "near_fade": [1.6, 1.6], "glow": 0.4,
             "glow_segments": 96, "glow_color": [0.9, 0.32, 0.07], "flare_cap": 1.8},
}
SHAPES = ("embers", "bursts", "sparks", "trails", "ring")
TRAIL_COLORS = np.array([[1.0, 0.92, 0.62], [1.0, 0.55, 0.12], [0.85, 0.2, 0.03], [0.25, 0.03, 0.0], [0.0, 0.0, 0.0]])
TRAIL_COLOR_AT = np.array([0.0, 0.25, 0.55, 0.85, 1.0])
FLAME_COLOR_AT = [0.0, 0.25, 0.5, 0.8, 1.0]
FLAME_COLUMNS = ([1.0, 1.0, 0.95, 0.45, 0.0], [0.9, 0.62, 0.32, 0.06, 0.0], [0.6, 0.18, 0.05, 0.0, 0.0])


def merged(settings):
    out = {k: v for k, v in DEFAULTS.items() if k not in SHAPES}
    out.update({k: v for k, v in settings.items() if k not in SHAPES})
    for name in SHAPES:
        if settings.get(name) is not None:
            out[name] = {**DEFAULTS[name], **settings[name]}
    return out


def smoothstep(v):
    v = min(1.0, max(0.0, v))
    return v * v * (3 - 2 * v)


class Shapes:
    def __init__(self, settings):
        s = merged(settings)
        self.s = s
        rng = np.random.default_rng(int(s["seed"]))       # 台本と同じ順に引く（火の粉 → はじけ → 火花 → 炎の輪。描かない形のぶんも引く）
        e = {**DEFAULTS["embers"], **(s.get("embers") or {})}
        n = int(e["count"])
        self.e_r = np.sqrt(rng.uniform(e["radius"][0] ** 2, e["radius"][1] ** 2, n))
        self.e_phi, self.e_omega = rng.uniform(0, 2 * math.pi, n), rng.uniform(-e["omega"], e["omega"], n)
        self.e_z0, self.e_vz = rng.uniform(0, e["height"], n), rng.uniform(e["rise"][0], e["rise"][1], n)
        self.e_size, self.e_phase = rng.uniform(e["size"][0], e["size"][1], n), rng.uniform(0, 2 * math.pi, n)
        b = {**DEFAULTS["bursts"], **(s.get("bursts") or {})}
        n = int(b["count"])
        self.b_dir = rng.normal(size=(n, 3))
        self.b_dir[:, 2] = np.abs(self.b_dir[:, 2]) * 0.8 + 0.1
        self.b_dir /= np.linalg.norm(self.b_dir, axis=1, keepdims=True)
        self.b_speed, self.b_size = rng.uniform(b["speed"][0], b["speed"][1], n), rng.uniform(b["size"][0], b["size"][1], n)
        p = {**DEFAULTS["sparks"], **(s.get("sparks") or {})}
        n, hands = int(p["count"]), len(p["hands"])
        self.s_phase = rng.uniform(0, p["life"], (hands, n))
        self.s_velocity = rng.normal(size=(hands, n, 3)) * p["spread"] + np.array([0.0, 0.0, p["lift"]])
        self.s_size = rng.uniform(p["size"][0], p["size"][1], (hands, n))
        r = {**DEFAULTS["ring"], **(s.get("ring") or {})}
        n = int(r["count"])
        self.f_angle = np.linspace(0, 2 * math.pi, n, endpoint=False) + rng.uniform(-0.01, 0.01, n)
        self.f_radius = r["radius"] + rng.uniform(-0.08, 0.08, n)
        self.f_w, self.f_phase, self.f_phase2 = rng.uniform(1.2, 2.6, n), rng.uniform(0, 2 * math.pi, n), rng.uniform(0, 2 * math.pi, n)
        self.f_tall = np.where(rng.uniform(0, 1, n) > 0.82, rng.uniform(1.5, 2.3, n), rng.uniform(0.6, 1.1, n))     # ときどき背の高い舌
        self.f_half = rng.uniform(0.022, 0.05, n)
        steps = int(r["steps"])
        self.flame_s = np.linspace(0.0, 1.0, steps + 1)
        self.flame_width = (1 - self.flame_s) ** 0.75 * (1 + 0.45 * np.sin(math.pi * self.flame_s))     # 少し上がふくらんで、先で閉じる
        self.flame_colors = np.stack([np.interp(self.flame_s, FLAME_COLOR_AT, column) for column in FLAME_COLUMNS], axis=1)
        t = {**DEFAULTS["trails"], **(s.get("trails") or {})}
        self.trail_age = np.linspace(0.0, 1.0, int(t["samples"]))

    # ---- 筋（カメラを向いた細い四角形） ----
    def streaks(self, centres, velocities, sizes, colors, right, up, camera_at, rate):
        s = self.s
        distance = np.linalg.norm(centres - camera_at, axis=1)
        keep = distance > s["near"]
        centres, velocities, colors, distance = centres[keep], velocities[keep], colors[keep], distance[keep]
        half = np.minimum(sizes[keep], distance * s["size_per_m"])
        vx, vy = velocities @ right, velocities @ up
        speed = np.sqrt(vx * vx + vy * vy) + 1e-9
        along = (right[None, :] * (vx / speed)[:, None] + up[None, :] * (vy / speed)[:, None])
        across = (right[None, :] * (-vy / speed)[:, None] + up[None, :] * (vx / speed)[:, None])
        length = np.clip(speed * s["streak"] * rate, half, np.minimum(s["length_max"], distance * s["length_per_m"]))
        a, b = along * length[:, None], across * half[:, None]
        quads = np.stack([centres - a - b, centres + a - b, centres + a + b, centres - a + b], axis=1)
        tail = colors * s["tail"]                                                # 筋の後ろは暗く
        return quads, np.stack([tail, colors, colors, tail], axis=1)

    def embers(self, tf, level, right, up, camera_at, rate):
        e = self.s["embers"]
        n = int(len(self.e_r) * min(max(level, 0.0), 1.0))
        if n == 0:
            return EMPTY
        angle = self.e_phi[:n] + self.e_omega[:n] * tf
        z = (self.e_z0[:n] + self.e_vz[:n] * tf) % e["height"]
        cx, cy = e["centre"]
        centres = np.stack([cx + self.e_r[:n] * np.cos(angle) + 0.15 * np.sin(tf * 1.3 + self.e_phase[:n]),
                            cy + self.e_r[:n] * np.sin(angle) + 0.15 * np.cos(tf * 1.1 + self.e_phase[:n]), z], axis=1)
        velocities = np.stack([-self.e_r[:n] * self.e_omega[:n] * np.sin(angle) + 0.2 * np.cos(tf * 1.3 + self.e_phase[:n]),
                               self.e_r[:n] * self.e_omega[:n] * np.cos(angle) - 0.17 * np.sin(tf * 1.1 + self.e_phase[:n]),
                               self.e_vz[:n]], axis=1)
        bright = ((0.6 + 0.4 * np.sin(tf * 6.0 + self.e_phase[:n] * 7)) * np.clip(z / 0.3, 0, 1)
                  * np.clip((e["height"] - z) / 0.8, 0, 1))
        colors = bright[:, None] * np.array(e["color"])
        return self.streaks(centres, velocities, self.e_size[:n], colors, right, up, camera_at, rate)

    def bursts(self, tf, points, right, up, camera_at, rate):
        b = self.s["bursts"]
        quads, colors = [], []
        for start, strength in b["times"]:
            age = tf - start
            if not 0 <= age < b["life"]:
                continue
            at = start if b["hold"] is None else min(start, b["hold"])
            origin = points.at(b["origin"], at)
            travel = (1 - math.exp(-b["drag"] * age)) / b["drag"]
            centres = origin + self.b_dir * (self.b_speed * strength * travel)[:, None] + UP * b["lift"] * age
            velocities = self.b_dir * (self.b_speed * strength * math.exp(-b["drag"] * age))[:, None] + UP * b["lift"]
            bright = math.exp(-age / b["fade"])
            q, c = self.streaks(centres, velocities, self.b_size * (1 - age / b["life"]),
                                np.tile(np.array(b["color"]) * bright, (len(self.b_dir), 1)), right, up, camera_at, rate)
            quads.append(q)
            colors.append(c)
        if not quads:
            return EMPTY
        return np.concatenate(quads), np.concatenate(colors)

    def sparks(self, dance_t, tf, level, points, right, up, camera_at, rate):
        p = self.s["sparks"]
        if level <= 0.01:
            return EMPTY
        quads, colors = [], []
        for index, hand in enumerate(p["hands"]):
            age = (tf + self.s_phase[index]) % p["life"]
            born = points.at(hand, dance_t - age)
            moving = np.linalg.norm(born - points.at(hand, dance_t - age - 1 / 30), axis=1) * 30
            centres = born + self.s_velocity[index] * age[:, None] - UP * (0.5 * p["gravity"] * age * age)[:, None]
            velocities = self.s_velocity[index] - UP * (p["gravity"] * age)[:, None]
            bright = level * (1 - age / p["life"]) ** 1.5 * np.clip(moving / 1.5, 0.15, 1.0)      # 手が速いほど多く散る
            q, c = self.streaks(centres, velocities, self.s_size[index], bright[:, None] * np.array(p["color"]), right, up,
                                camera_at, rate)
            quads.append(q)
            colors.append(c)
        return np.concatenate(quads), np.concatenate(colors)

    def trail(self, hand, phase, dance_t, tf, level, points, camera_at):
        t = self.s["trails"]
        if level <= 0.01:
            return EMPTY
        age = self.trail_age
        track = points.at(hand, dance_t - age * t["seconds"])
        speed = float(np.linalg.norm(points.at(hand, dance_t) - points.at(hand, dance_t - 1 / 30)) * 30)
        strength = level * float(np.clip(speed / 2.0, 0.3, 1.0))
        # 炎らしく、古い所ほど上へ立ちのぼり、揺らぐ
        track = track + UP * (0.10 * age ** 1.5)[:, None]
        track[:, 0] += 0.03 * age * np.sin(age * 9 + tf * 7 + phase)
        track[:, 1] += 0.03 * age * np.cos(age * 7 + tf * 6 + phase)
        tangent = np.gradient(track, axis=0)
        side = np.cross(tangent, track - camera_at)
        side /= np.linalg.norm(side, axis=1, keepdims=True) + 1e-9
        base = np.stack([np.interp(age, TRAIL_COLOR_AT, TRAIL_COLORS[:, k]) for k in range(3)], axis=1)
        quads, colors = [], []
        for half, tint, fade in ((0.07, 1.0, 0.6), (0.024, 1.4, 1.2)):          # 外側の炎と、白っぽい芯
            wide = half * (1 - age) ** fade * (0.6 + 0.4 * strength)
            left, right_ = track - side * wide[:, None], track + side * wide[:, None]
            quads.append(np.stack([left[:-1], right_[:-1], right_[1:], left[1:]], axis=1))
            tone = np.clip(base * tint * strength * 0.8, 0, 0.8)
            colors.append(np.stack([tone[:-1], tone[:-1], tone[1:], tone[1:]], axis=1))
        return np.concatenate(quads), np.concatenate(colors)

    def ring(self, tf, level, flare, camera_at):
        r = self.s["ring"]
        if level <= 0.01:
            return EMPTY
        n = len(self.f_angle)
        cx, cy = r["centre"]
        base = np.stack([cx + self.f_radius * np.cos(self.f_angle), cy + self.f_radius * np.sin(self.f_angle), np.full(n, 0.004)], axis=1)
        tall = level * flare * self.f_tall * (0.09 + 0.13 * np.abs(np.sin(self.f_w * tf * 1.7 + self.f_phase))
                                              + 0.05 * np.sin(5.3 * tf + self.f_phase2))
        # カメラのすぐ前の炎は描かない（輪の外から撮ると、手前の炎が踊り手を隠すため）。near_fade[0] m より近いと消え、合わせて
        # near_fade[0] + near_fade[1] m で元の明るさ
        near = np.clip((np.linalg.norm(base - camera_at, axis=1) - r["near_fade"][0]) / r["near_fade"][1], 0, 1)
        near = near * near * (3 - 2 * near)
        tall = tall * near
        view = base[:, :2] - camera_at[:2]
        view /= np.linalg.norm(view, axis=1, keepdims=True) + 1e-9
        side = np.stack([-view[:, 1], view[:, 0], np.zeros(n)], axis=1)
        s = self.flame_s
        # 舌の芯の位置（高さごと）。上へ行くほど、横へ S の字に揺れる
        sway = 0.35 * s[None, :] ** 1.5 * np.sin(tf * 3.1 + self.f_phase[:, None] + 2.6 * s[None, :]) * tall[:, None]
        spine = base[:, None, :] + UP[None, None, :] * (tall[:, None] * s[None, :])[..., None] + side[:, None, :] * sway[..., None]
        wide = self.f_half[:, None] * self.flame_width[None, :]
        left, right_ = spine - side[:, None, :] * wide[..., None], spine + side[:, None, :] * wide[..., None]
        quads = np.stack([left[:, :-1], right_[:, :-1], right_[:, 1:], left[:, 1:]], axis=2).reshape(-1, 4, 3)
        tone = self.flame_colors[None, :, :] * (0.8 * level * near)[:, None, None]
        colors = np.stack([tone[:, :-1], tone[:, :-1], tone[:, 1:], tone[:, 1:]], axis=2).reshape(-1, 4, 3)
        # 床の照り返しの輪
        segments = int(r["glow_segments"])
        a = np.linspace(0, 2 * math.pi, segments + 1)
        rings = [r["radius"] - 0.35, r["radius"], r["radius"] + 0.45]
        glow = np.array(r["glow_color"]) * r["glow"] * level * min(flare, r["flare_cap"])
        glow_q, glow_c = [], []
        for j in (0, 1):
            r0, r1 = rings[j], rings[j + 1]
            c0, c1 = (np.zeros(3), glow) if j == 0 else (glow, np.zeros(3))
            for i in range(segments):
                corners = [(cx + r0 * math.cos(a[i]), cy + r0 * math.sin(a[i]), 0.006), (cx + r1 * math.cos(a[i]), cy + r1 * math.sin(a[i]), 0.006),
                           (cx + r1 * math.cos(a[i + 1]), cy + r1 * math.sin(a[i + 1]), 0.006),
                           (cx + r0 * math.cos(a[i + 1]), cy + r0 * math.sin(a[i + 1]), 0.006)]
                away = min(max((math.dist(corners[0], camera_at) - r["near_fade"][0]) / r["near_fade"][1], 0.0), 1.0)   # カメラのすぐ前の照り返しも消す
                glow_q.append(corners)
                glow_c.append([c0 * away, c1 * away, c1 * away, c0 * away])
        return np.concatenate([quads, np.array(glow_q)]), np.concatenate([colors, np.array(glow_c)])

    def frame(self, state):
        """そのコマの (四角形 (N, 4, 3), 頂点の色 (N, 4, 3))。形は台本と同じ順に並べる（火の粉・はじけ・火花・帯 2 本・輪）。"""
        s = self.s
        matrix = np.asarray(state["camera"], float)
        right, up, camera_at = matrix[:3, 0], matrix[:3, 1], matrix[:3, 3]
        levels, tf, dance_t, rate, points = state["levels"], state["tf"], state["dance_t"], state["rate"], state["points"]
        parts = []
        if "embers" in s:
            parts.append(self.embers(tf, levels.get("embers", 0.0), right, up, camera_at, rate))
        if "bursts" in s:
            parts.append(self.bursts(tf, points, right, up, camera_at, rate))
        if "sparks" in s:
            parts.append(self.sparks(dance_t, tf, levels.get("sparks", 0.0), points, right, up, camera_at, rate))
        if "trails" in s:
            t = s["trails"]
            steep = smoothstep((abs(float(matrix[2, 2])) - t["steep"][0]) / t["steep"][1])  # 真上から見るときは帯が体に重なって明るい塊になる
            level = levels.get("trails", 0.0) * (1 - t["steep_weaken"] * steep)
            for hand, phase in zip(t["hands"], t["phase"]):
                parts.append(self.trail(hand, phase, dance_t, tf, level, points, camera_at))
        if "ring" in s:
            parts.append(self.ring(tf, levels.get("ring", 0.0), levels.get("flare", 1.0), camera_at))
        if not parts:
            return EMPTY
        return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


def keys_at(keys, t):
    """時刻のキー [[時刻, 値], [時刻, 値, つなぎ方], …] の t での値（数か、省いたときの既定は呼ぶ側）。mv_pipeline の cues.envelope と同じ
    （最初より前は最初の値、最後より後は最後の値。間は後のキーのつなぎ方: linear・smooth・step。同じ時刻のキーを並べるとそこで跳ぶ）。
    Blender の中でも使うので、mv_pipeline を読まずにここに書く。"""
    if isinstance(keys, (int, float)):
        return float(keys)
    times = [key[0] for key in keys]
    import bisect
    index = bisect.bisect_right(times, t) - 1
    if index < 0:
        return keys[0][1]
    if index == len(keys) - 1:
        return keys[-1][1]
    (t0, v0), (t1, v1, *rest) = keys[index][:2], keys[index + 1]
    ease = rest[0] if rest else "linear"
    if ease == "step":
        return v0
    if ease == "smooth":
        u = min(1.0, max(0.0, (t - t0) / (t1 - t0)))
        return v0 + (v1 - v0) * (u * u * (3.0 - 2.0 * u))
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
