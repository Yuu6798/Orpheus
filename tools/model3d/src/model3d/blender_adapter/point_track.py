"""骨の通り道（model3d-bone-path/0.1）の点を、踊りの時刻（秒）で読む。numpy だけを使う（Blender の中の台本からも、外の段取りからも
読む。plan.orbit のカメラと、render.passes の効果の形が使う）。

通り道の動きのコマ f を踊りの時刻 (f − frame0) / fps に直し、点ごとの σ（秒）のガウス（±ceil(reach × σ) の標本。端は端の値で
延ばす。σ 0 ならならさない）でならす。踊りの時刻での値は、前後の標本の直線補間（範囲の外は端の標本。リポジトリ外の試作と同じ
位置の取り方）。標本は時刻に等間隔であること（plan.retime のトラックで motion.bone_path を測る）。
"""
import math
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from . import stage_render
except ImportError:   # Blender が台本として動かすとき（ほかの部品と同じく、自分のフォルダを sys.path に足してから読む）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import stage_render

UNIFORM = 1e-6        # 標本の時刻の間隔のずれの許し（秒）


class PathError(ValueError):
    """通り道が読めない（呼ぶ側が ToolError か SystemExit に直す）。"""


class Samples:
    """sigmas: 点の名前 → ならしの σ（秒）。名前は通り道の points か bones の列。"""

    def __init__(self, path, sigmas, frame0, fps, reach=3.0):
        columns = {**path.get("bones", {}), **path.get("points", {})}
        missing = sorted(set(sigmas) - set(columns))
        if missing:
            raise PathError(f"骨の通り道に点がありません: {missing}（あるのは {sorted(columns)}。motion.bone_path の points か bones で"
                            "測ってください）")
        frames = [float(f) for f in path["frames"]]
        if len(frames) < 2:
            raise PathError("骨の通り道の標本が 2 つより少ないので、時刻で読めません")
        times = (np.asarray(frames) - frame0) / fps
        steps = np.diff(times)
        if steps.min() <= 0 or steps.max() - steps.min() > UNIFORM:
            raise PathError("骨の通り道の標本が、時刻に等間隔で増えていません（plan.retime のトラックで motion.bone_path を測ってください）")
        self.start = float(times[0])
        self.rate = (len(times) - 1) / float(times[-1] - times[0])      # 1 秒あたりの標本の数
        self.count = len(times)
        self.span = (float(times[0]), float(times[-1]))
        self.columns = {}
        for name, sigma_s in sigmas.items():
            values = np.asarray(columns[name], float)
            if values.shape != (self.count, 3):
                raise PathError(f"骨の通り道の {name} の数（{len(values)}）が frames（{self.count}）と違います")
            sigma = sigma_s * self.rate
            self.columns[name] = (stage_render.gauss_smooth(values, sigma, int(math.ceil(reach * sigma)))
                                  if sigma > 0 else values)

    def position(self, time):
        """標本の位置（0 〜 標本の数 − 1.001 に止める）。時刻は数か配列。"""
        return np.clip((np.asarray(time, dtype=float) - self.start) * self.rate, 0.0, self.count - 1.001)

    def at(self, name, time):
        """踊りの時刻（数か配列）での点（(3,) か (n, 3)）。"""
        values = self.columns[name]
        position = self.position(time)
        i = np.floor(position).astype(int)
        w = (position - i)[..., None]
        return values[i] * (1 - w) + values[i + 1] * w

    def outside(self, time):
        return not self.span[0] - 1e-9 <= time <= self.span[1] + 1e-9
