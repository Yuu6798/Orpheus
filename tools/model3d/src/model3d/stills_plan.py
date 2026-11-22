"""見本のコマ（plan.stills）: 決めた踊りのコマを 1 つずつ、決めた位置から見るカメラ（model3d-cameras/0.1）と、コマの並び
（model3d-stage-track/0.1。T と動きのコマ f）と、そのコマの光の値（model3d-light-track/0.1）を作る。render.show でそのまま描き、
本番の前に、塗りと舞台の見本を数枚だけ見るため。計算だけで、Blender は使わない。

リポジトリ外の 4 作目の 3D のダンスで、見本のコマのカメラの控えと光のトラックを書いた台本の置き方を移した。plan.orbit は T から
時刻を決める（コマが等間隔に並ぶ）ので、ばらばらの踊りのコマを 1 枚ずつ見る見本には使えなかった。

- 並び: 見本（shots）を踊りのコマ f の小さい順に並べ、T = 1 から振る（render.show は、T の順に動きのコマが増える並びを描く）。
  同じ f の見本が 2 つあれば止める。
- 点: 骨の通り道の点を、動きのコマ f で読む（ならさない。通り道にそのコマがあればその値、無ければ前後の標本のコマで直線に補間、
  通り道の外は端の標本の値で、外として数える。plan.scout と同じ読み方）。
- 位置: 足もと（base の点。既定 hips）の真下の床 (x, y, 0) ＋ offset（m）。狙う点 = aim の点 ＋ (0, 0, dz)。
- 向き: 狙う点を向き、傾けない（右 = 前 × (0, 0, 1)、上 = 右 × 前。plan.scout・plan.orbit と同じ式）。真上か真下を向くと止める。
- レンズ: lens（mm。camera.sensor_height の縦の受光）。焦点距離の画素 = 画面の高さ × lens ÷ sensor_height、主点は画面の中央。
- 絞り: fstop があれば、cameras の fstop と focus_m（カメラの位置からピントの点までのまっすぐの距離。ピントの点は focus の点、
  省くと狙う点）。fstop が無ければ被写界深度なし。
- 光の値: flash 0・flash_color [1, 1, 1]・fade 1 に、lights（どの見本にも）を重ね、見本の lights（そのコマだけ）を重ねる。
"""
import numpy as np

from .io import ToolError
from .scout_plan import Reader, look_at

DEFAULTS = {"base": "hips", "fps": 30.0, "lights": {}, "camera": {"resolution": [1080, 1920], "sensor_height": 36.0}, "clip_m": None}
RESERVED = {"flash": 0.0, "flash_color": [1.0, 1.0, 1.0], "fade": 1.0}


def build(settings, path):
    """(cameras, track, lights, report)。settings は既定を足して確かめた設定（plan_ops._stills_settings）。"""
    shots = sorted(settings["shots"], key=lambda shot: shot["f"])
    camera = settings["camera"]
    width, height = (int(v) for v in camera["resolution"])
    sensor, fps = float(camera["sensor_height"]), float(settings["fps"])
    readers = {}

    def point(name, f):
        if name not in readers:
            readers[name] = Reader(path, name)
        return readers[name].at(f)

    entries, rows, lights, table, outside = [], [], [], [], 0
    for T, shot in enumerate(shots, start=1):
        f = shot["f"]
        base, out_base = point(settings["base"], f)
        aim_point, out_aim = point(shot["aim"], f)
        outside += out_base + out_aim
        position = np.array([base[0], base[1], 0.0]) + np.array(shot["offset"], float)
        aim = aim_point + np.array([0.0, 0.0, float(shot["dz"])])
        try:
            basis = look_at(position, aim)
        except ToolError as error:
            raise ToolError(f"plan.stills の見本 {shot['name']!r}: カメラが狙う点と同じ位置か、狙う点の真上か真下にあり、向きが決まりません"
                            "（offset と dz を確かめてください）") from error
        matrix = np.eye(4)
        matrix[:3, :3] = basis
        matrix[:3, 3] = position
        entry = {"T": T, "f": f, "matrix": [[float(v) for v in row] for row in matrix], "focal_px": height * float(shot["lens"]) / sensor,
                 "principal_px": [width / 2, height / 2], "lens_mm": float(shot["lens"]), "shift": [0.0, 0.0],
                 "dancer": [float(v) for v in base], "distance_m": float(np.linalg.norm(base - position))}
        if shot.get("fstop") is not None:
            if shot.get("focus"):
                focus, out_focus = point(shot["focus"], f)
                outside += out_focus
            else:
                focus = aim
            entry["fstop"], entry["focus_m"] = float(shot["fstop"]), float(np.linalg.norm(focus - position))
        entries.append(entry)
        rows.append({"T": T, "f": f})
        lights.append({"T": T, "values": {**RESERVED, **settings["lights"], **shot.get("lights", {})}})
        table.append({"T": T, "f": f, "name": shot["name"], "aim_distance_m": float(np.linalg.norm(aim - position)),
                      "dof": shot.get("fstop") is not None})
    cameras = {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [width, height], "fps": fps,
               "sensor_height_mm": sensor, "frames": entries}
    if settings["clip_m"] is not None:
        cameras["clip_m"] = [float(v) for v in settings["clip_m"]]
    track = {"schema_version": "model3d-stage-track/0.1", "fps": fps, "frames": rows}
    light_track = {"schema_version": "model3d-light-track/0.1", "fps": fps, "frames": lights}
    report = {"schema_version": "model3d-stills-report/0.1", "frames": len(entries), "shots": table, "base": settings["base"],
              "points": sorted(readers), "outside": int(outside), "lights": sorted({k for fr in lights for k in fr["values"]})}
    return cameras, track, light_track, report
