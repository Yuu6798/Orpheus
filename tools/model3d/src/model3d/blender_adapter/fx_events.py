"""出来事の表と時計: 出来事（種類・始まり・場所・強さ・種）を読み、コマごとに、効いている出来事と、その年齢と場所を出す。
numpy だけを使う（render.show のフックが Blender の中で `import fx_events` で読む。CI でも試せる）。

リポジトリ外の 4 作目の 3D のダンスの演出の台本の、出来事の表の読み方（点の 3 通りの書き方・整数の種の乱数）を移し、
始まりの比べ方をコマの番号に直した。形は、コマと種だけで決まる（どのコマから描いても同じ）。長さは m。

    table = Table(spec, path)                    spec: {"fps", "frame0", "events": [出来事, …]}。path: 骨の通り道（任意）
    for event in table.events:
        age = table.age(event, f)                そのコマ（動きのコマ f）での年齢（秒。始まりの前は負）
        if table.within(event, f, 0.0, life):    始まりから life 秒までの間か（コマの番号で比べる）
            p = table.point(event["at"], f, camera)

**始まり**（どちらか 1 つ）: `frame`（動きのコマの番号。整数なら、そのコマから効く）か `t`（踊りの秒。コマ f の秒は
(f − frame0) ÷ fps）。秒は、表を読むときに一度だけコマの番号（frame0 + t × fps）に直し、それより後はコマの番号で比べる。
秒を小数で丸めて書くと、コマの秒より少し後になり、そのコマでは「まだ始まっていない」になる（1 コマだけの出来事が出ない）。
コマの頭の直後（0.05 コマ以内）に始まる秒は、notes に書く（そのコマから効かせるなら frame で書く）。

**点の書き方**（`point`）:
- `[x, y, z]`: 世界の座標。
- `{"point": 名前, "offset": [x, y, z], "frame" か "t": いつ}`: 骨の通り道（model3d-bone-path/0.1）の点（points か bones の列）の、
  いちばん近い標本に offset を足した所。いつを省くと、そのコマ。
- `{"camera": [右, 上, 前]}`: そのコマのカメラから見た位置（m）。カメラが替わると、点も動く（カットの替わり目をまたがせない）。

**そのコマだけに出る出来事**: `when`（値の名前）を書いた出来事は、そのコマの値（光のトラックの値など）の大きさが 0.5 以上のときだけ出す
（`gated`）。
"""
import math
import random

import numpy as np

EPS = 1e-6          # コマの番号で比べるときの許し（コマ）
NEAR_HEAD = 0.05    # コマの頭の直後とみなす幅（コマ）。秒を小数 3 桁に丸めた時刻は、30 fps でコマの頭から 0.015 コマ以内


class EventError(ValueError):
    """出来事の表が読めない（呼ぶ側が ToolError か SystemExit に直す）。"""


def rng(*keys):
    """整数の鍵だけで決まる乱数（random.Random）。文字列の種は使わない（実行ごとに変わることがあるため）。"""
    seed = 1469598103
    for k in keys:
        if isinstance(k, bool) or not isinstance(k, (int, np.integer)):
            raise EventError(f"乱数の鍵は整数にしてください: {k!r}")
        seed = (seed * 1000003 + int(k) + 0x9E3779B1) & 0x7FFFFFFF
    return random.Random(seed)


def need(event, key, where=""):
    """出来事の欄を読む（無ければ止める）。"""
    if key not in event:
        raise EventError(f"出来事{where}に {key} がありません: {event!r}")
    return event[key]


def _number(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EventError(f"{where} は数にしてください: {value!r}")
    return float(value)


def _triple(value, where):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise EventError(f"{where} は 3 つの数にしてください: {value!r}")
    return [_number(v, where) for v in value]


class Path:
    """骨の通り道の点を、動きのコマの番号で読む（いちばん近い標本。ならさない）。"""

    def __init__(self, path):
        frames = [float(f) for f in path.get("frames", [])]
        if len(frames) < 2:
            raise EventError("骨の通り道の標本が 2 つより少ないので、コマで読めません")
        steps = np.diff(frames)
        if steps.min() <= 0 or steps.max() - steps.min() > 1e-6:
            raise EventError("骨の通り道の標本が、コマに等間隔で増えていません")
        self.first, self.step, self.count = frames[0], float(steps[0]), len(frames)
        self.columns = {**path.get("bones", {}), **path.get("points", {})}

    def row(self, frame):
        return min(self.count - 1, max(0, int(round((frame - self.first) / self.step))))

    def at(self, name, frame):
        if name not in self.columns:
            raise EventError(f"骨の通り道に点がありません: {name}（あるのは {sorted(self.columns)}）")
        return np.asarray(self.columns[name][self.row(frame)], float)


class Table:
    def __init__(self, spec, path=None):
        if not isinstance(spec, dict) or not isinstance(spec.get("events"), list):
            raise EventError("出来事の表は {\"fps\", \"frame0\", \"events\": [出来事, …]} にしてください")
        self.fps = _number(need(spec, "fps", "の表"), "fps")
        if self.fps <= 0:
            raise EventError(f"fps は正の数にしてください: {self.fps}")
        self.frame0 = _number(spec.get("frame0", 1), "frame0")
        self.path = Path(path) if path is not None else None
        self.notes = []
        starts = []
        for n, event in enumerate(spec["events"]):
            if not isinstance(event, dict):
                raise EventError(f"events[{n}] は表（辞書）にしてください: {event!r}")
            starts.append(self._start(event, n))
        order = sorted(range(len(starts)), key=lambda i: starts[i])          # 始まりの順（同じなら表の順）
        self.events = [spec["events"][i] for i in order]
        self._starts = {id(spec["events"][i]): starts[i] for i in order}

    def _start(self, event, n):
        if ("frame" in event) == ("t" in event):
            raise EventError(f"events[{n}] には frame（コマ）か t（秒）のどちらか 1 つを書いてください: {event!r}")
        if "frame" in event:
            return _number(event["frame"], f"events[{n}].frame")
        start = self.frame0 + _number(event["t"], f"events[{n}].t") * self.fps
        late = start - math.floor(start)
        if EPS < late < NEAR_HEAD:
            self.notes.append(f"events[{n}]（t {event['t']}）は、コマ {math.floor(start):g} の頭の {late:.3f} コマ後に始まるので、"
                              f"コマ {math.floor(start) + 1:g} から効きます（コマ {math.floor(start):g} から効かせるなら frame で書く）")
        return start

    # ---- 時刻 ----
    def start(self, event):
        """始まりのコマの番号（小数のこともある）。"""
        return self._starts[id(event)]

    def seconds(self, f):
        """動きのコマ f の踊りの秒。"""
        return (f - self.frame0) / self.fps

    def age(self, event, f, offset=0.0):
        """コマ f での年齢（秒。形を決める数で、効くかどうかは within で決める）。offset 秒だけ遅れて始まる部分の年齢にもできる。
        秒で書いた出来事は (コマの秒 − t)、コマで書いた出来事は (f − frame) ÷ fps（同じ時刻なら同じ丸めになる）。"""
        if "t" in event:
            return self.seconds(f) - float(event["t"]) - offset
        return (f - self.start(event)) / self.fps - offset

    def within(self, event, f, before, after, offset=0.0):
        """コマ f が、始まり（+ offset 秒）の before 秒前から after 秒後までに入るか（コマの番号で比べる）。"""
        frames = f - self.start(event) - offset * self.fps
        return -before * self.fps - EPS <= frames <= after * self.fps + EPS

    def tick(self, f, every=1):
        """形の細かい揺れを替えるコマの番号（every コマごとに替わる整数）。"""
        return int(math.floor(f + EPS)) // max(1, int(every))

    @staticmethod
    def gated(event, values):
        """when のある出来事は、その値の大きさが 0.5 以上のコマだけ出す。"""
        name = event.get("when")
        return name is None or abs(float((values or {}).get(name, 0.0))) >= 0.5

    # ---- 点 ----
    def point(self, spec, f, camera=None):
        """点（世界の座標 (3,)）。f は今のコマ（骨の点でいつを省いたとき、その時刻で読む）。camera は {"matrix", "position"}。"""
        if isinstance(spec, (list, tuple)) and len(spec) == 3:
            return np.array([_number(v, "点") for v in spec])
        if not isinstance(spec, dict):
            raise EventError(f"点は [x, y, z]・{{\"point\": 名前}}・{{\"camera\": [右, 上, 前]}} のどれかにしてください: {spec!r}")
        if "camera" in spec:
            if camera is None:
                raise EventError(f"カメラから見た点を読むには、そのコマのカメラが要ります: {spec!r}")
            m = np.asarray(camera["matrix"], float)
            r, u, forward = _triple(spec["camera"], "camera の点")
            return np.asarray(camera["position"], float) + m[:3, 0] * r + m[:3, 1] * u - m[:3, 2] * forward
        if "point" in spec:
            if self.path is None:
                raise EventError(f"骨の点を読むには、骨の通り道が要ります: {spec!r}")
            if "frame" in spec and "t" in spec:
                raise EventError(f"骨の点のいつは frame か t のどちらか 1 つにしてください: {spec!r}")
            at = (_number(spec["frame"], "点の frame") if "frame" in spec else
                  self.frame0 + _number(spec["t"], "点の t") * self.fps if "t" in spec else f)
            offset = np.array(_triple(spec.get("offset", (0.0, 0.0, 0.0)), "点の offset"))
            return self.path.at(spec["point"], at) + offset
        raise EventError(f"点の書き方が分かりません: {spec!r}")

    def fixed(self, spec):
        """カメラによらない点か（カメラから見た点でない）。形を一度だけ作る出来事が使う。"""
        return not (isinstance(spec, dict) and "camera" in spec)
