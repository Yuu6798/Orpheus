"""光の台本: 光の段取り表（model3d-light-plan/0.1）から、コマごとの光の値のトラック（model3d-light-track/0.1）を作る。
計算だけで、Blender は使わない（plan.lights が呼ぶ）。3 作目の VRM のダンスの演出版（リポジトリ外の台本）で、コマ T の関数として
光の強さと色・拍で打つ量・閃光・暗転を返した計算を、段取り表と小さな式で書ける形に移した。

コマ T（1 から）の時刻は t = (T - 1) / fps 秒。コマごとに:
1. values（名前 → 既定の値）を全部求める。値は数、3 つの並び（色や位置。要素は数か式）、色の名前、式のどれか。
2. t を含む区間（segments。頭は前の区間の until、最後の区間だけ until を省ける。時刻は秒・拍 {"beat": k}・marks の名前）の
   let（区間の中の名前。書いた順に求める）を求め、set の値で上書きする。
3. 区間の flashes（at・strength・color・decay・length）を並べた順に見て、0 ≤ t − at < length なら（境目は区間と同じく、
   1e-6 秒前のコマから届いたとみなす）strength × exp(−max(t − at, 0) / decay)（decay は 0 より大きい数）を求め、今の flash より強ければ flash と
   flash_color をそれにする。
flash（閃光の強さ。既定 0）・flash_color（既定 [1, 1, 1]）・fade（暗転で掛ける割合。既定 1）は、どの段取り表にもある値。

式（Python の式の書き方の一部だけ。ast で読み、決めた形のほかは読む前に止める。eval は使わない）:
- 名前: t（秒）、low・mid・high（光のキーのそのコマの帯の強さ。キーが無ければ 0）、hit（直前の拍からの減衰
  exp(−((t − k0_time) mod period) / hit.tau)。拍の 1e-6 秒前からのコマは、区間と同じく拍に届いたとみなして 1）、colors の名前、
  その区間の let の名前（前に書いたもの）。
- 関数: beat(k)（拍 k の時刻 k0_time + period × k）、ramp(x, a, b)（ease((x − a) / max(b − a, 1e-6))）、ease(u[, "smooth"|"linear"|"in"|"out"])
  （u を 0〜1 に切ってから。smooth は u²(3 − 2u)、in は u²、out は 1 − (1 − u)²）、mix(a, b, u)（a + (b − a) × u。a と b は数どうしか
  3 つの並びどうし）、max・min（2 つ以上の数）、exp。
- 演算: + − × ÷（3 つの並びは要素ごと。並びと数の掛け算・割り算も）、単項の −、比べる（< <= > >=。続けて書ける）、
  「a if 比べる else b」。数と、ease の 2 つ目の文字列のほかの定数は使えない。
計算は Python の浮動小数（倍精度）で、式に書いた順に行う（同じ式なら、元の台本と同じ丸めになる）。
"""
import ast
import math
import operator

from .io import ToolError
from .stage_plan import EPS, Clock, find, inside, spans

SIGNALS = ("t", "low", "mid", "high", "hit")
FUNCTIONS = {"beat": (1, 1), "ramp": (3, 3), "ease": (1, 2), "mix": (3, 3), "max": (2, None), "min": (2, None), "exp": (1, 1)}
EASE = ("smooth", "linear", "in", "out")
RESERVED = {"flash": 0.0, "flash_color": [1.0, 1.0, 1.0], "fade": 1.0}
FLASH = {"decay": 0.10, "length": 0.6}                  # 閃光の既定（3 作目の VRM のダンスの値）。色を省くと values の flash_color の既定
HIT_TAU = 0.13
ARITH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
COMPARE = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def ease(u, kind="smooth"):
    """u を 0〜1 に切ってから、なめらかに（3 作目の台本と同じ式）。"""
    u = min(1.0, max(0.0, u))
    if kind == "linear":
        return u
    if kind == "in":
        return u * u
    if kind == "out":
        return 1 - (1 - u) * (1 - u)
    return u * u * (3 - 2 * u)


def ramp(x, a, b):
    return ease((x - a) / max(b - a, 1e-6), "smooth")


def mix(a, b, u):
    if isinstance(a, tuple) and isinstance(b, tuple):
        return tuple(x + (y - x) * u for x, y in zip(a, b))
    if isinstance(a, tuple) or isinstance(b, tuple):
        raise ValueError("mix の a と b は、数どうしか 3 つの並びどうしにしてください")
    return a + (b - a) * u


class Expression:
    """式 1 つ。読むときに形を確かめ（決めた名前・関数・演算だけ）、コマごとに名前の値で求める。"""

    def __init__(self, text, where, names):
        self.text, self.where = text, where
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError as error:
            raise ToolError(f"{where} の式を読めません: {text!r}（{error.msg}）") from error
        self.body = tree.body
        self._check(self.body, set(names))

    def _fail(self, what):
        raise ToolError(f"{self.where} の式 {self.text!r} に、使えない{what}があります"
                        f"（使えるのは t・low・mid・high・hit・colors と let の名前、関数 {', '.join(sorted(FUNCTIONS))}、"
                        "+ - * /、if の条件の中の比べる式）")

    def _check(self, node, names, test=False):
        if isinstance(node, ast.Compare) and not test:
            self._fail("比べる式（if の条件の中だけで使えます）")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                self._fail(f"定数 {node.value!r} ")
        elif isinstance(node, ast.Name):
            if node.id not in names:
                self._fail(f"名前 {node.id!r} ")
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd)):
                self._fail("演算")
            self._check(node.operand, names)
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in ARITH:
                self._fail("演算")
            self._check(node.left, names)
            self._check(node.right, names)
        elif isinstance(node, ast.Compare):
            if not all(type(op) in COMPARE for op in node.ops):
                self._fail("比べ方")
            for part in [node.left, *node.comparators]:
                self._check(part, names)
        elif isinstance(node, ast.IfExp):
            if not isinstance(node.test, ast.Compare):
                self._fail("if の条件（比べる式だけ）")
            self._check(node.test, names, test=True)
            self._check(node.body, names)
            self._check(node.orelse, names)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                self._fail("呼び出し")
            low, high = FUNCTIONS[node.func.id]
            if len(node.args) < low or (high is not None and len(node.args) > high) or any(isinstance(a, ast.Starred) for a in node.args):
                self._fail(f"{node.func.id} の引数の数")
            for k, arg in enumerate(node.args):
                if node.func.id == "ease" and k == 1:
                    if not (isinstance(arg, ast.Constant) and arg.value in EASE):
                        self._fail(f"ease のつなぎ方（{', '.join(EASE)}）")
                else:
                    self._check(arg, names)
        else:
            self._fail(f"書き方（{type(node).__name__}）")

    def __call__(self, env):
        return self._eval(self.body, env)

    def _eval(self, node, env):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand, env)
            if isinstance(node.op, ast.UAdd):
                return value
            return tuple(-v for v in value) if isinstance(value, tuple) else -value
        if isinstance(node, ast.BinOp):
            return arith(type(node.op), self._eval(node.left, env), self._eval(node.right, env))
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, env)
            for op, part in zip(node.ops, node.comparators):
                right = self._eval(part, env)
                if isinstance(left, tuple) or isinstance(right, tuple):
                    raise ValueError("比べるのは数どうしだけです")
                if not COMPARE[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body, env) if self._eval(node.test, env) else self._eval(node.orelse, env)
        name = node.func.id                               # ast.Call（形は読むときに確かめてある）
        if name == "ease":
            u = self._eval(node.args[0], env)
            return ease(number(u, "ease"), node.args[1].value if len(node.args) > 1 else "smooth")
        args = [self._eval(a, env) for a in node.args]
        if name == "mix":
            return mix(args[0], args[1], number(args[2], "mix の u"))
        if name == "beat":
            return env["@k0"] + env["@period"] * number(args[0], "beat")
        if name == "ramp":
            return ramp(*(number(a, "ramp") for a in args))
        if name == "exp":
            return math.exp(number(args[0], "exp"))
        values = [number(a, name) for a in args]
        return max(values) if name == "max" else min(values)


def number(value, where):
    if isinstance(value, tuple):
        raise ValueError(f"{where} には数が要ります（3 つの並びが来ました）")
    return value


def arith(op, a, b):
    """数と 3 つの並びの + − × ÷（並びは要素ごと。並びと数は × と ÷ だけ）。"""
    f = ARITH[op]
    if isinstance(a, tuple) and isinstance(b, tuple):
        return tuple(f(x, y) for x, y in zip(a, b))
    if isinstance(a, tuple):
        if op not in (ast.Mult, ast.Div):
            raise ValueError("3 つの並びと数は、掛けるか割るだけです")
        return tuple(f(x, b) for x in a)
    if isinstance(b, tuple):
        if op is not ast.Mult:
            raise ValueError("数と 3 つの並びは、掛けるだけです")
        return tuple(f(a, y) for y in b)
    return f(a, b)


class Value:
    """値 1 つ: 数・3 つの並び（要素は数か式）・式（色の名前も式）。"""

    def __init__(self, raw, where, names):
        self.where = where
        if isinstance(raw, bool):
            raise ToolError(f"{where} は数・3 つの並び・式にしてください: {raw!r}")
        if isinstance(raw, (int, float)):
            self.kind, self.parts = "number", float(raw)
        elif isinstance(raw, str):
            self.kind, self.parts = "expression", Expression(raw, where, names)
        elif isinstance(raw, list) and len(raw) == 3:
            self.kind, self.parts = "vector", [Value(v, f"{where}[{k}]", names) for k, v in enumerate(raw)]
            if any(p.kind == "vector" for p in self.parts):
                raise ToolError(f"{where} の 3 つの並びの中に、並びは入れられません: {raw!r}")
        else:
            raise ToolError(f"{where} は数・3 つの並び・式にしてください: {raw!r}")

    def __call__(self, env):
        if self.kind == "number":
            return self.parts
        if self.kind == "vector":
            out = tuple(p(env) for p in self.parts)
            if any(isinstance(v, tuple) for v in out):
                raise ValueError("3 つの並びの要素には数が要ります")
            return out
        return self.parts(env)


def _names_ok(names, taken, where):
    for name in names:
        if not isinstance(name, str) or not name.isidentifier():
            raise ToolError(f"{where} の名前 {name!r} は、英字で始まる名前にしてください")
        if name in taken:
            raise ToolError(f"{where} の名前 {name!r} は、ほかの名前（t・low・mid・high・hit・関数・colors・前の let）と重なります")


class Plan:
    """読んで確かめた光の段取り表。"""

    def __init__(self, plan, keys=None):
        self.keys = keys
        for band in ("low", "mid", "high") if keys is not None else ():   # 数がそろわないと、後のコマの帯が黙って止まるか読めない
            if len(keys[band]) != keys["frames"]:                          # （plan.stage と同じ確かめ）
                raise ToolError(f"光のキーの {band} の数（{len(keys[band])}）が frames（{keys['frames']}）と違います")
        grid = (keys or {}).get("grid") or plan.get("grid")
        if grid is None:
            raise ToolError("光の段取り表の拍の格子（grid）が要ります（光のキー keys を渡すか、段取り表に grid を書く）")
        if keys is not None and plan.get("grid") is not None and plan["grid"] != keys["grid"]:
            raise ToolError(f"段取り表の grid（{plan['grid']}）が光のキーの grid（{keys['grid']}）と違います")
        self.grid = {"period": float(grid["period"]), "k0_time": float(grid["k0_time"])}
        fps = keys["fps"] if keys is not None else plan.get("fps")
        if keys is not None and plan.get("fps") is not None and float(plan["fps"]) != float(keys["fps"]):
            raise ToolError(f"段取り表の fps（{plan['fps']}）が光のキーの fps（{keys['fps']}）と違います")
        if fps is None:
            raise ToolError("光の段取り表の fps が要ります（光のキー keys を渡すか、段取り表に fps を書く）")
        self.fps = fps
        frames = plan.get("frames") if plan.get("frames") is not None else (keys or {}).get("frames")
        if frames is None:
            raise ToolError("光の段取り表の frames（コマの数）が要ります（光のキー keys を渡すか、段取り表に frames を書く）")
        self.frames = int(frames)
        self.tau = float(plan.get("hit", {}).get("tau", HIT_TAU))
        self.clock = Clock(plan.get("marks"), self.grid)
        colors = plan.get("colors", {})
        _names_ok(colors, set(SIGNALS) | set(FUNCTIONS), "colors")
        self.colors = {name: tuple(float(v) for v in value) for name, value in colors.items()}
        base = set(SIGNALS) | set(self.colors)
        self.values = {name: Value(raw, f"values.{name}", base) for name, raw in {**RESERVED, **plan["values"]}.items()}
        self.segments = plan.get("segments") or [{}]
        self.spans = spans(self.segments, self.clock, "segments")
        self.compiled = []
        for n, segment in enumerate(self.segments):
            where = f"segments[{n}]"
            names = set(base)
            lets = []
            for name, raw in (segment.get("let") or {}).items():
                _names_ok([name], names | set(FUNCTIONS), f"{where}.let")
                lets.append((name, Value(raw, f"{where}.let.{name}", names)))
                names.add(name)
            sets = []
            for name, raw in (segment.get("set") or {}).items():
                if name not in self.values:
                    raise ToolError(f"{where}.set の {name!r} が values にありません（あるのは {sorted(self.values)}）")
                sets.append((name, Value(raw, f"{where}.set.{name}", names)))
            flashes = []
            for k, flash in enumerate(segment.get("flashes") or []):
                f = {**FLASH, **flash}
                spot = f"{where}.flashes[{k}]"
                scalars = {key: Value(f[key], f"{spot}.{key}", names) for key in ("strength", "decay", "length")}
                for key, value in scalars.items():
                    if value.kind == "vector":
                        raise ToolError(f"{spot}.{key} は数か式にしてください（3 つの並びは使えません）: {f[key]!r}")
                if scalars["decay"].kind == "number" and scalars["decay"].parts <= 0:
                    raise ToolError(f"{spot}.decay（減り方の秒）は 0 より大きい数にしてください: {f['decay']!r}")
                flashes.append({"at": self.clock(f["at"], f"{spot}.at"), **scalars,
                                "color": Value(f["color"], f"{spot}.color", names) if "color" in f else None,
                                "where": spot})
            self.compiled.append((lets, sets, flashes))

    def signals(self, T):
        t = (T - 1) / self.fps
        if self.keys is not None:
            i = min(max(T - 1, 0), len(self.keys["low"]) - 1)
            low, mid, high = self.keys["low"][i], self.keys["mid"][i], self.keys["high"][i]
        else:
            low = mid = high = 0.0
        phase = (t - self.grid["k0_time"]) % self.grid["period"]
        if self.grid["period"] - phase <= EPS:           # 拍の EPS 秒前からのコマは拍に届いたとみなす（区間・閃光と同じゆとり。
            phase = 0.0                                  # ちょうど拍のコマで、引き算の丸めのために余りがほぼ 1 拍ぶんになり、hit が落ちないように）
        hit = math.exp(-phase / self.tau)
        return {"t": t, "low": low, "mid": mid, "high": high, "hit": hit, **self.colors,
                "@k0": self.grid["k0_time"], "@period": self.grid["period"]}

    def at(self, T):
        """コマ T の値 {名前: 数か 3 つの並び} と区間の番号。"""
        env = self.signals(T)
        t = env["t"]
        out = {name: self._value(value, env, T, name) for name, value in self.values.items()}
        default_color = out["flash_color"]
        n = find(self.spans, t)
        if n is None:
            raise ToolError(f"T {T}（{t:.4f} 秒）が、どの区間（segments）にも入りません（最後の区間の until を省くか延ばしてください）")
        lets, sets, flashes = self.compiled[n]
        local = dict(env)
        for name, value in lets:
            local[name] = self._value(value, local, T, name)
        for name, value in sets:
            got = self._value(value, local, T, name)
            if isinstance(got, tuple) != isinstance(out[name], tuple):
                raise ToolError(f"T {T} の segments[{n}].set.{name} は、values.{name} と同じ形（数か 3 つの並び）にしてください")
            out[name] = got
        for flash in flashes:
            d = t - flash["at"]
            length = self._number(flash["length"], local, T)
            if inside(d, 0.0, length):                   # 区間の境目と同じゆとり（ちょうど拍のコマを、丸めで外さない）
                strength = self._number(flash["strength"], local, T)
                decay = self._number(flash["decay"], local, T)
                if decay <= 0:                           # 0 で割る・強まっていく閃光にしない（式は、求めた値を確かめる）
                    raise ToolError(f"T {T} の {flash['decay'].where}（減り方の秒）は 0 より大きい数にしてください: {decay}")
                v = strength * math.exp(-max(d, 0.0) / decay)
                if v > out["flash"]:
                    color = default_color if flash["color"] is None else self._value(flash["color"], local, T, flash["where"])
                    out["flash"], out["flash_color"] = v, color
        for name in ("flash", "fade"):
            if isinstance(out[name], tuple) or out[name] < 0:
                raise ToolError(f"T {T} の {name} は 0 以上の数にしてください: {plain(out[name])}")
        if not isinstance(out["flash_color"], tuple):
            raise ToolError(f"T {T} の flash_color は 3 つの並びにしてください")
        return out, n

    @classmethod
    def _number(cls, value, env, T):
        """閃光の strength・decay・length。数でなければ（式が 3 つの並びになれば）止める。"""
        got = cls._value(value, env, T, value.where)
        if isinstance(got, tuple):
            raise ToolError(f"T {T} の {value.where} は数にしてください（3 つの並びになりました）: {list(got)}")
        return got

    @staticmethod
    def _value(value, env, T, name):
        try:
            got = value(env)
        except (ValueError, ZeroDivisionError, OverflowError, TypeError) as error:
            raise ToolError(f"T {T} の {value.where if hasattr(value, 'where') else name} を求められません（{error}）") from error
        if isinstance(got, bool):
            raise ToolError(f"T {T} の {value.where} は、比べる式でなく数にしてください")
        if isinstance(got, tuple):
            if len(got) != 3 or not all(math.isfinite(v) for v in got):
                raise ToolError(f"T {T} の {value.where} の 3 つの並びが有限の数ではありません: {got}")
            return tuple(float(v) for v in got)
        if not math.isfinite(got):
            raise ToolError(f"T {T} の {value.where} が有限の数ではありません: {got}")
        return float(got)


def plain(value):
    return list(value) if isinstance(value, tuple) else value


def build(plan, keys=None):
    """トラック（model3d-light-track/0.1）と報告。"""
    p = Plan(plan, keys)
    frames, hits = [], []
    ranges = {}
    for T in range(1, p.frames + 1):
        values, n = p.at(T)
        frames.append({"T": T, "segment": n, "values": {name: plain(v) for name, v in values.items()}})
        hits.append(n)
        for name, v in values.items():
            if not isinstance(v, tuple):
                lo, hi = ranges.get(name, (v, v))
                ranges[name] = (min(lo, v), max(hi, v))
    track = {"schema_version": "model3d-light-track/0.1", "fps": p.fps, "grid": p.grid, "frames": frames}
    segments = []
    for n, (start, end) in enumerate(p.spans):
        Ts = [T for T, k in zip(range(1, p.frames + 1), hits) if k == n]
        segments.append({"index": n, "from": start, "until": None if math.isinf(end) else end,
                         "T": [Ts[0], Ts[-1]] if Ts else None, "frames": len(Ts)})
    report = {"schema_version": "model3d-light-plan-report/0.1", "fps": p.fps, "frames": p.frames, "segments": segments,
              "names": sorted(p.values), "flash_frames": sum(1 for fr in frames if fr["values"]["flash"] > 0),
              "fade_frames": sum(1 for fr in frames if fr["values"]["fade"] < 1),
              "keys": None if keys is None else {"frames": keys["frames"], "beyond": max(0, p.frames - len(keys["low"]))},
              "ranges": {name: {"min": lo, "max": hi} for name, (lo, hi) in sorted(ranges.items())}}
    return track, report
