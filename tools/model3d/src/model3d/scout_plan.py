"""向きの下見（plan.scout）: 拍ごと（か時刻ごと）の姿勢を、踊り手のまわりのいくつかの方位から見るカメラ（model3d-cameras/0.1）と、
コマの並び（model3d-stage-track/0.1。T と動きのコマ f）を作る。render.passes でそのまま描き、mv sheet で拍（行）× 方位（列）に並べると、
動きごとに形がいちばん読める向きを選べる。計算だけで、Blender は使わない。

リポジトリ外の 3 作目の VRM のダンスで、止めの多い前半の振りを拍ごとに 7 つの方位から描いて並べ、カメラの向きを選んだ下見の台本の
置き方を移した（差し出す腕は正面からだと縮んで見える・かがんで手を回す所は真横のほうが体の折れが読める、などを決めた）。

- 並び: 行 r（拍か時刻。0 から）と列 c（方位。0 から）のコマは T = 1 + r × 列の数 + c。
- 時刻: beats（[最初の拍, 最後の拍]。拍 k の時刻は grid の k0_time + period × k）か times（秒の並び）。
- 動きのコマ: f = dance.frame0 + round(dance.fps × 時刻)（姿勢は整数のコマ。round は Python の丸め（ちょうど半分は偶数へ））。
- 狙う点: 骨の通り道の点 aim（points か bones。既定 hips）の、そのコマの値（ならさない。通り道にそのコマがあればその値、無ければ
  前後の標本の間）。高さは max(aim_floor, 点の z) + dz（しゃがんでも狙いが下がりすぎない。aim_floor が null なら点の z + dz）。
- カメラ: 位置 = 狙う点 + dist × (sin az cos el, −cos az cos el, sin el)（az は列の方位。0 で正面（−Y 側）、正で +X 側。el は正で上）。
  狙う点を向き、傾けない（右 = 前 × (0, 0, 1)、上 = 右 × 前）。レンズ lens（mm。camera.sensor_height の縦の受光）、主点は画面の中央。
"""
import math

import numpy as np

from .blender_adapter.point_track import PathError, Samples
from .io import ToolError

DEFAULTS = {"angles": [-90.0, -55.0, -25.0, 0.0, 25.0, 55.0, 90.0], "el": -4.0, "dist": 3.1, "lens": 35.0, "aim": "hips",
            "aim_floor": 0.75, "dz": 0.22, "dance": {"frame0": 1.0, "fps": 30.0},
            "camera": {"resolution": [1080, 1920], "sensor_height": 36.0}}


def times_of(settings):
    """行の時刻 [(拍か None, 秒)]。"""
    if "beats" in settings:
        grid = settings["grid"]
        first, last = settings["beats"]
        return [(k, grid["k0_time"] + grid["period"] * k) for k in range(first, last + 1)]
    return [(None, float(t)) for t in settings["times"]]


def look_at(position, aim):
    """位置から狙う点を向く、傾けない 3×3 の向き（列が右・上・後ろ。plan.orbit と同じ式）。"""
    forward = aim - position
    length = float(np.linalg.norm(forward))
    if length < 1e-9:
        raise ToolError("plan.scout: カメラの位置が狙う点と同じです（dist を確かめてください）")
    forward = forward / length
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    if float(np.linalg.norm(right)) < 1e-9:
        raise ToolError("plan.scout: 真上か真下から見るカメラは、右の向きが決まりません（el を確かめてください）")
    right = right / np.linalg.norm(right)
    return np.stack([right, np.cross(right, forward), -forward], axis=1)


class Reader:
    """通り道の点を、動きのコマ f で読む（通り道にそのコマがあればその値そのもの、無ければ前後の標本の間の直線補間）。"""

    def __init__(self, path, name, frame0, fps):
        try:
            self.samples = Samples(path, {name: 0.0}, frame0, fps)
        except PathError as error:
            raise ToolError(str(error)) from error
        self.name, self.frame0, self.fps = name, frame0, fps
        self.index = {float(f): k for k, f in enumerate(path["frames"])}

    def at(self, f):
        k = self.index.get(float(f))
        if k is not None:
            return np.array(self.samples.columns[self.name][k], float), False
        dance_t = (f - self.frame0) / self.fps
        return self.samples.at(self.name, dance_t), self.samples.outside(dance_t)


def build(settings, path):
    """(cameras, track, report)。settings は既定を足した設定。"""
    angles = [float(a) for a in settings["angles"]]
    dance, camera = settings["dance"], settings["camera"]
    width, height = (int(v) for v in camera["resolution"])
    sensor, lens = float(camera["sensor_height"]), float(settings["lens"])
    focal = height * lens / sensor
    reader = Reader(path, settings["aim"], float(dance["frame0"]), float(dance["fps"]))
    el = math.radians(float(settings["el"]))
    entries, rows, layout, outside = [], [], [], 0
    for r, (beat, t) in enumerate(times_of(settings)):
        f = float(dance["frame0"]) + round(float(dance["fps"]) * t)
        f = int(f) if f.is_integer() else f
        point, out = reader.at(f)
        outside += out
        floor = settings["aim_floor"]
        z = float(point[2]) if floor is None else max(float(floor), float(point[2]))
        aim = np.array([float(point[0]), float(point[1]), z + float(settings["dz"])])
        first = 1 + r * len(angles)
        layout.append({"row": r, "beat": beat, "time": t, "f": f, "T": [first, first + len(angles) - 1]})
        for c, az in enumerate(angles):
            a = math.radians(az)
            position = aim + np.array([math.sin(a) * math.cos(el), -math.cos(a) * math.cos(el), math.sin(el)]) * float(settings["dist"])
            matrix = np.eye(4)
            matrix[:3, :3] = look_at(position, aim)
            matrix[:3, 3] = position
            T = first + c
            entries.append({"T": T, "f": f, "matrix": [[float(v) for v in row] for row in matrix], "focal_px": float(focal),
                            "principal_px": [width / 2, height / 2], "lens_mm": lens, "shift": [0.0, 0.0],
                            "dancer": [float(v) for v in point], "distance_m": float(np.linalg.norm(point - position))})
            rows.append({"T": T, "f": f})
    fps = float(dance["fps"])
    cameras = {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [width, height], "fps": fps,
               "sensor_height_mm": sensor, "frames": entries}
    track = {"schema_version": "model3d-stage-track/0.1", "fps": fps, "frames": rows}
    report = {"schema_version": "model3d-scout-report/0.1", "frames": len(entries), "rows": layout, "angles": angles,
              "columns": len(angles), "aim": settings["aim"], "el": float(settings["el"]), "dist": float(settings["dist"]),
              "lens_mm": lens, "outside": outside, "T": "1 + 行 × 列の数 + 列（行も列も 0 から）"}
    return cameras, track, report
