"""目の動きの台本（motion.eye_plan の計算。Python だけ。Blender の中でも外でも読む）。

台本（model3d-eye-plan/0.1。JSON）の演技の時間 τ（秒）で、視線（瞳が目の板の上を滑る量 m）と、目の形状キーの値を決める。画面の時間は、
区間（sections）の順に τ をたどる（同じ τ をゆっくり見せ直す区間もある）。リポジトリ外の 3 作目の VRM のダンスのあとの試しの台本と同じ式。
時刻・速さ・量は台本に書く（既定は無い。頭と演技ごとに決める）。

- 視線の飛び（gaze.events の [τ, 横 m, たて m]。横 + はキャラの左、たて + は上）: 前の位置から、かかる時間 base_s + per_m_s × 距離で、
  1 − (1 − u)³ に (1 + overshoot) を掛けて行きすぎ、settle_s ごとに exp(−settle_decay × v)・cos(π v) で戻って止まる。
- なめらかに回す（gaze.circle）: start〜end を 1 周、横 h_m・たて v_m。始めと終わりは ramp_s かけて出入りする（ゆるめ env を横にもたてにも
  1 回掛ける。vertical_ramp: "squared" なら、試しの式のとおりたてだけ 2 回掛ける。試しの式は誤りに見え、違うのはゆるめの間のたての値だけ。
  ユウの答え 2026-11-18）。
- 驚いた直後の小さな震え（gaze.tremor。驚きの始めから start_s〜end_s、だんだん小さく）: 横 h_m × sin(2π h_hz u)、たて v_m × sin(2π v_hz u + phase)。
- まばたき（blink.starts の τ から length_s の間）: close_s で閉じ、hold_s 閉じたまま、open_s で開く。瞳のつぶれは閉じと同じで、開くときは
  squash_open_s で戻り、stretch（start_s〜length_s）でたてに amount 伸びて戻る（taper で後ろほど弱く）。まぶたは overopen で少し開きすぎて戻る。
  瞳のつぶれの戻りは、まぶたの開き（open_s）と別に squash_open_s の間続く（試しは開き終わった所で打ち切っていた。squash_open_s が open_s
  以下なら同じ）。
- 驚き（surprise の start〜end。release から end でゆるめる）: squint（length_s の間、軽く閉じる）→ 見開き（wide）・瞳が縮む（small）は
  [始め, 立ち上がり, 行きすぎ, 戻り] で少し行きすぎてから止まる。眉（brow）は [始め, 立ち上がり] で上がる。
- 笑顔（smile.start から ramp_s で）: 笑顔の閉じ目と口の笑顔を上げ、まばたきと見開きを消し、眉を brow だけ上げる。
- 視線につられるまぶた（lids）: 上を見ると見開きを up_per_m × たて、下を見るとまばたきを down_per_m × たて。
- 横を見ると瞳が横へ伸びる（iris_wide）: amount × smoothstep(|横| ÷ full_m) × (1 − 縮む量)。
返すキーの名前は _L・_R を除いたもの（blink・blink_smile・mouth_smile・brow_up・eye_wide・iris_small・iris_wide・iris_squash）。
"""
import math

SCHEMA = "model3d-eye-plan/0.1"
KEY_NAMES = ("blink", "blink_smile", "mouth_smile", "brow_up", "eye_wide", "iris_small", "iris_wide", "iris_squash")
VERTICAL_RAMPS = ("once", "squared")   # gaze.circle のたてのゆるめ: 既定は once（横と同じ）、squared は試しの式


class PlanError(ValueError):
    """台本の誤り。"""


def smooth(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _number(value, where, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (positive and value <= 0):
        raise PlanError(f"{where} は{'正の' if positive else ''}数にしてください: {value!r}")
    return float(value)


def _table(value, where, keys, positive=(), optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, dict) or set(value) != set(keys):
        raise PlanError(f"{where} は {sorted(keys)} の表にしてください: {value!r}")
    return {key: _number(value[key], f"{where}.{key}", key in positive) for key in keys}


def _pops(value, where, length):
    if not isinstance(value, list) or len(value) != length:
        raise PlanError(f"{where} は数 {length} つの並びにしてください: {value!r}")
    return [_number(v, f"{where}[{k}]") for k, v in enumerate(value)]


def validate(plan):
    """台本を確かめて、数を float にした写しを返す（誤りは PlanError）。"""
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA:
        raise PlanError(f"台本は schema_version: {SCHEMA} の JSON にしてください")
    known = {"schema_version", "fps", "gaze", "blink", "surprise", "smile", "lids", "iris_wide", "sections"}
    if set(plan) - known:
        raise PlanError(f"台本の知らない項目: {sorted(set(plan) - known)}")
    out = {"schema_version": SCHEMA, "fps": _number(plan.get("fps"), "fps", positive=True)}
    gaze = plan.get("gaze")
    if not isinstance(gaze, dict) or set(gaze) - {"events", "saccade", "circle", "tremor"}:
        raise PlanError(f"gaze は {{events, saccade, circle, tremor}} にしてください: {gaze!r}")
    events = gaze.get("events")
    if not isinstance(events, list) or not all(isinstance(e, list) and len(e) == 3 for e in events):
        raise PlanError(f"gaze.events は [[τ, 横 m, たて m], …] にしてください: {events!r}")
    events = [[_number(v, f"gaze.events[{k}]") for v in e] for k, e in enumerate(events)]
    if any(b[0] < a[0] for a, b in zip(events, events[1:])):
        raise PlanError("gaze.events は τ の順に並べてください")
    raw_circle, vertical_ramp = gaze.get("circle"), "once"
    if isinstance(raw_circle, dict) and "vertical_ramp" in raw_circle:   # たてのゆるめの掛け方（ユウの答え 2026-11-18。既定は once）
        vertical_ramp = raw_circle["vertical_ramp"]
        if vertical_ramp not in VERTICAL_RAMPS:
            raise PlanError(f"gaze.circle.vertical_ramp は {list(VERTICAL_RAMPS)} のどれかにしてください: {vertical_ramp!r}")
        raw_circle = {key: value for key, value in raw_circle.items() if key != "vertical_ramp"}
    out["gaze"] = {"events": events,
                   "saccade": _table(gaze.get("saccade"), "gaze.saccade", ("base_s", "per_m_s", "overshoot", "settle_s", "settle_decay"),
                                     positive=("base_s", "settle_s")),
                   "circle": _table(raw_circle, "gaze.circle", ("start", "end", "h_m", "v_m", "ramp_s"), positive=("ramp_s",), optional=True),
                   "tremor": _table(gaze.get("tremor"), "gaze.tremor", ("start_s", "end_s", "h_m", "v_m", "h_hz", "v_hz", "phase"), optional=True)}
    circle = out["gaze"]["circle"]
    if circle and circle["end"] <= circle["start"]:
        raise PlanError("gaze.circle の end は start より後にしてください")
    if circle:
        circle["vertical_ramp"] = vertical_ramp
    tremor = out["gaze"]["tremor"]
    if tremor and tremor["end_s"] <= tremor["start_s"]:
        raise PlanError("gaze.tremor の end_s は start_s より後にしてください")
    blink = plan.get("blink")
    if not isinstance(blink, dict):
        raise PlanError(f"blink は表にしてください: {blink!r}")
    starts = blink.get("starts")
    if not isinstance(starts, list):
        raise PlanError(f"blink.starts はまばたきの始まりの τ の並びにしてください: {starts!r}")
    out["blink"] = {"starts": [_number(v, "blink.starts") for v in starts],
                    **_table({k: v for k, v in blink.items() if k not in ("starts", "stretch", "overopen")}, "blink",
                             ("close_s", "hold_s", "open_s", "squash_open_s", "length_s"),
                             positive=("close_s", "open_s", "squash_open_s", "length_s")),
                    "stretch": _table(blink.get("stretch"), "blink.stretch", ("start_s", "length_s", "amount", "taper"), positive=("length_s",)),
                    "overopen": _table(blink.get("overopen"), "blink.overopen", ("start_s", "length_s", "amount"), positive=("length_s",))}
    surprise = plan.get("surprise")
    if surprise is not None:
        if not isinstance(surprise, dict) or set(surprise) != {"start", "release", "end", "squint", "wide", "small", "brow"}:
            raise PlanError(f"surprise は {{start, release, end, squint, wide, small, brow}} にしてください: {surprise!r}")
        surprise = {"start": _number(surprise["start"], "surprise.start"), "release": _number(surprise["release"], "surprise.release"),
                    "end": _number(surprise["end"], "surprise.end"),
                    "squint": _table(surprise["squint"], "surprise.squint", ("amount", "length_s"), positive=("length_s",)),
                    "wide": _pops(surprise["wide"], "surprise.wide", 4), "small": _pops(surprise["small"], "surprise.small", 4),
                    "brow": _pops(surprise["brow"], "surprise.brow", 2)}
        if not surprise["start"] <= surprise["release"] < surprise["end"]:
            raise PlanError("surprise は start ≤ release < end にしてください")
        for name in ("wide", "small"):
            if surprise[name][1] <= 0 or surprise[name][3] <= 0:
                raise PlanError(f"surprise.{name} の立ち上がりと戻りは正の数にしてください")
        if surprise["brow"][1] <= 0:
            raise PlanError("surprise.brow の立ち上がりは正の数にしてください")
    out["surprise"] = surprise
    out["smile"] = _table(plan.get("smile"), "smile", ("start", "ramp_s", "brow"), positive=("ramp_s",), optional=True)
    out["lids"] = _table(plan.get("lids"), "lids", ("up_per_m", "down_per_m"))
    out["iris_wide"] = _table(plan.get("iris_wide"), "iris_wide", ("amount", "full_m"), positive=("full_m",))
    sections = plan.get("sections")
    if not isinstance(sections, list) or not sections:
        raise PlanError(f"sections は [[τ の始め, τ の終わり, 速さ, 向き, 説明], …] にしてください: {sections!r}")
    out["sections"] = []
    for k, section in enumerate(sections):
        if not isinstance(section, list) or len(section) != 5 or not isinstance(section[3], str) or not isinstance(section[4], str):
            raise PlanError(f"sections[{k}] は [τ の始め, τ の終わり, 速さ, 向き, 説明] にしてください: {section!r}")
        t0, t1, speed = _number(section[0], f"sections[{k}][0]"), _number(section[1], f"sections[{k}][1]"), _number(section[2], f"sections[{k}][2]", True)
        if t1 <= t0:
            raise PlanError(f"sections[{k}] の τ の終わりは始めより後にしてください")
        out["sections"].append([t0, t1, speed, section[3], section[4]])
    return out


def gaze(t, plan):
    """視線（横 m, たて m）。"""
    g = plan["gaze"]
    s_cfg = g["saccade"]
    prev, cur, tk = (0.0, 0.0), None, 0.0
    for k in g["events"]:
        if t < k[0]:
            break
        if cur is not None:
            prev = cur
        cur, tk = (k[1], k[2]), k[0]
    if cur is None:
        x, z = 0.0, 0.0
    else:
        dist = math.hypot(cur[0] - prev[0], cur[1] - prev[1])
        dur = s_cfg["base_s"] + s_cfg["per_m_s"] * dist
        u = (t - tk) / dur
        if u < 1.0:
            s = (1 + s_cfg["overshoot"]) * (1 - (1 - u) ** 3)
        else:
            v = (t - tk - dur) / s_cfg["settle_s"]
            s = 1 + s_cfg["overshoot"] * math.exp(-s_cfg["settle_decay"] * v) * math.cos(math.pi * v)
        x, z = prev[0] + (cur[0] - prev[0]) * s, prev[1] + (cur[1] - prev[1]) * s
    circle = g["circle"]
    if circle and circle["start"] <= t <= circle["end"]:
        a = 2 * math.pi * (t - circle["start"]) / (circle["end"] - circle["start"])
        env = smooth((t - circle["start"]) / circle["ramp_s"]) * smooth((circle["end"] - t) / circle["ramp_s"])
        if circle.get("vertical_ramp") == "squared":                   # 試しの式: たてだけ env を 2 回（誤りに見える。照合のときに選ぶ）
            dz = circle["v_m"] * env * math.sin(a + math.pi / 2) * env
        else:                                                           # 既定: 横と同じく 1 回
            dz = circle["v_m"] * env * math.sin(a + math.pi / 2)
        x, z = x + circle["h_m"] * env * math.sin(a), z + dz
    tremor, surprise = g["tremor"], plan["surprise"]
    if tremor and surprise:
        u = t - surprise["start"]
        if tremor["start_s"] <= u <= tremor["end_s"]:
            k = 1 - (u - tremor["start_s"]) / (tremor["end_s"] - tremor["start_s"])
            x += tremor["h_m"] * k * math.sin(2 * math.pi * tremor["h_hz"] * u)
            z += tremor["v_m"] * k * math.sin(2 * math.pi * tremor["v_hz"] * u + tremor["phase"])
    return x, z


def blink_parts(t, plan):
    """まばたき: まぶたの閉じ（0〜1）、瞳のつぶれ（+ でつぶれる、− で伸びる）、開いた直後のわずかな見開き。"""
    c = plan["blink"]
    b = sq = wide = 0.0
    closed, opened = c["close_s"] + c["hold_s"], c["close_s"] + c["hold_s"] + c["open_s"]
    for tb in c["starts"]:
        u = t - tb
        if u < 0 or u > c["length_s"]:
            continue
        if u < c["close_s"]:
            b = smooth(u / c["close_s"])
            sq = b
        elif u < closed:
            b, sq = 1.0, 1.0
        else:                                                         # まぶたの開きと瞳のつぶれの戻りは、それぞれの長さで別に
            if u < opened:
                b = 1 - smooth((u - closed) / c["open_s"])
            if u < closed + c["squash_open_s"]:                       # squash_open_s が open_s より長くても、途中で戻り切らない
                sq = 1 - smooth((u - closed) / c["squash_open_s"])
        stretch = c["stretch"]
        v = (u - stretch["start_s"]) / stretch["length_s"]
        if 0 <= v <= 1:
            sq -= stretch["amount"] * math.sin(math.pi * v) * (1 - stretch["taper"] * v)
        over = c["overopen"]
        w = (u - over["start_s"]) / over["length_s"]
        if 0 <= w <= 1:
            wide = over["amount"] * math.sin(math.pi * w) * (1 - w)
    return b, sq, wide


def surprise_parts(t, plan):
    """驚き: 軽く閉じる・見開き・瞳が縮む・眉が上がる（それぞれ少し行きすぎてから止まり、release から end でゆるめる）。"""
    c = plan["surprise"]
    if c is None:
        return 0.0, 0.0, 0.0, 0.0
    u = t - c["start"]
    if u < 0 or t > c["end"]:
        return 0.0, 0.0, 0.0, 0.0
    squint = c["squint"]["amount"] * math.sin(math.pi * u / c["squint"]["length_s"]) if u < c["squint"]["length_s"] else 0.0

    def pop(start, rise, over, decay):
        p = (u - start) / rise
        if p < 0:
            return 0.0
        if p < 1:
            return (1 + over) * (1 - (1 - p) ** 3)
        return 1 + over * math.exp(-(u - start - rise) / decay)

    wide, small, brow = pop(*c["wide"]), pop(*c["small"]), smooth((u - c["brow"][0]) / c["brow"][1])
    rel = 1 - smooth((t - c["release"]) / (c["end"] - c["release"]))
    return squint, wide * rel, small * rel, brow * rel


def state(t, plan):
    """τ での視線とキーの値。返す: {"gaze": (横 m, たて m), "keys": {名前: 値}}（キーの名前は _L・_R を除いたもの）。"""
    x, z = gaze(t, plan)
    b, sq, bw = blink_parts(t, plan)
    squint, wide, small, brow = surprise_parts(t, plan)
    smile_cfg = plan["smile"]
    smile = smooth((t - smile_cfg["start"]) / smile_cfg["ramp_s"]) if smile_cfg and t >= smile_cfg["start"] else 0.0
    smile_brow = smile_cfg["brow"] if smile_cfg else 0.0
    lid_up, lid_down = max(z, 0.0) * plan["lids"]["up_per_m"], max(-z, 0.0) * plan["lids"]["down_per_m"]
    iris = plan["iris_wide"]
    keys = {"blink": min(b + squint + lid_down * (1 - b), 1.0) * (1 - smile), "blink_smile": smile, "mouth_smile": smile,
            "brow_up": min(brow + smile_brow * smile, 1.0), "eye_wide": (wide + bw + lid_up) * (1 - smile),
            "iris_small": small, "iris_wide": iris["amount"] * smooth(abs(x) / iris["full_m"]) * (1 - small), "iris_squash": sq}
    return {"gaze": (x, z), "keys": keys}


def frames(plan):
    """画面のコマごとの (τ, 区間の番号)。区間の長さ ÷ 速さ × fps のコマ（四捨五入）で、区間の τ を等分する。"""
    out = []
    for i, (t0, t1, speed, _, _) in enumerate(plan["sections"]):
        n = int(round((t1 - t0) / speed * plan["fps"]))
        out += [(t0 + (t1 - t0) * k / n, i) for k in range(n)]
    return out


def timeline(plan, start_frame=1):
    """コマごとの τ・区間・視線・キーの値と、区間ごとのコマの範囲と説明（説明の字を重ねる範囲。render.title などで使う）。"""
    rows, sections = [], []
    for i, (tau, section) in enumerate(frames(plan)):
        st = state(tau, plan)
        rows.append({"frame": start_frame + i, "tau": tau, "section": section, "gaze": list(st["gaze"]), "keys": st["keys"]})
        if not sections or sections[-1]["section"] != section:
            t0, t1, speed, view, caption = plan["sections"][section]
            sections.append({"section": section, "frames": [start_frame + i, start_frame + i], "tau": [t0, t1], "speed": speed,
                             "view": view, "caption": caption})
        sections[-1]["frames"][1] = start_frame + i
    return {"fps": plan["fps"], "count": len(rows), "frames": rows, "sections": sections}
