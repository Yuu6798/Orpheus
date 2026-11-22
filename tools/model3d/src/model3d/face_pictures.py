"""絵の差し替えの表情の、出来事の表 → コマごとの絵の名前（render.show の expressions.events。計算だけ）。

顔が 1 枚の色の絵で描かれたモデル（目も口も絵。形状キーが無い）は、表情を「色の絵をコマごとに差し替える」で作る。4 作目の 3D の
ダンス（リポジトリ外の制作）の、表情の出来事の表からコマごとの絵を決める台本を、作品の名前と値を外して移した。

出来事の表（events）:
- mouth: [[秒, 口の絵], …]。その秒から次の出来事までの口の絵（"" はもとの口）。
- steps: {口の絵: [はさむ絵, …]}。その口の絵に入るとき、はじめのコマから 1 コマずつはさむ（開ける口の、小 → 中 など）。
  出るとき（次の口の出来事の頭）は、逆の順に 1 コマずつはさむ（次の出来事も steps の口なら、そちらの入りを使う）。
- eyes: [[始まりの秒, 終わりの秒, 目の絵], …]。その区間だけ目の絵を変える（[始まり, 終わり)）。
- blinks: [秒, …] と blink: [目の絵, …]。まばたきの頭の秒から、blink の目の絵を 1 コマずつ（半目 → 閉じ目 → 半目 など）。
- join: 目と口を重ねた絵の名前のつなぎ（既定 "+"。目の絵 + join + 口の絵）。
秒は、近いコマの番号（round(秒 × fps)）に直してから比べる（秒の小数のまま比べると、丸めで 1 コマずれることがある）。
コマの絵: 目も口も "" ならもとの絵（""）、片方だけならその絵、両方なら重ねた絵。使える絵（names）に無い絵の組み合わせが
1 コマでもあれば、秒と目と口を並べて止める（重ねた絵を作るか、表を直す）。
"""
from .io import ToolError

EVENT_KEYS = {"mouth", "steps", "eyes", "blinks", "blink", "join"}


def _seconds(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        raise ToolError(f"{where} の秒は 0 以上の数にしてください: {value!r}")
    return float(value)


def _name(value, where, empty=True):
    if not isinstance(value, str) or (not empty and not value):
        raise ToolError(f"{where} の絵の名前は{'' if empty else '空でない'}文字列にしてください: {value!r}")
    return value


def check_events(events):
    """出来事の表の形を確かめて、整えた写しを返す（絵の名前があるかは、frames で確かめる）。"""
    if not isinstance(events, dict) or set(events) - EVENT_KEYS:
        raise ToolError(f"expressions.events は {{{', '.join(sorted(EVENT_KEYS))}}} の辞書にしてください: {events!r}")
    out = {"mouth": [], "steps": {}, "eyes": [], "blinks": [], "blink": [], "join": events.get("join", "+")}
    _name(out["join"], "expressions.events.join", empty=False)
    for n, item in enumerate(events.get("mouth", [])):
        if not isinstance(item, list) or len(item) != 2:
            raise ToolError(f"expressions.events.mouth[{n}] は [秒, 口の絵] にしてください: {item!r}")
        out["mouth"].append([_seconds(item[0], f"expressions.events.mouth[{n}]"), _name(item[1], f"expressions.events.mouth[{n}]")])
    starts = [m[0] for m in out["mouth"]]
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        raise ToolError("expressions.events.mouth は秒の小さい順にし、同じ秒を重ねないでください")
    steps = events.get("steps", {})
    if not isinstance(steps, dict):
        raise ToolError(f"expressions.events.steps は {{口の絵: [はさむ絵, …]}} にしてください: {steps!r}")
    for key, names in steps.items():
        if not isinstance(names, list) or not names:
            raise ToolError(f"expressions.events.steps.{key} は、はさむ絵の並び（1 つ以上）にしてください: {names!r}")
        out["steps"][_name(key, "expressions.events.steps", empty=False)] = [_name(v, f"expressions.events.steps.{key}", empty=False)
                                                                            for v in names]
    for n, item in enumerate(events.get("eyes", [])):
        if not isinstance(item, list) or len(item) != 3:
            raise ToolError(f"expressions.events.eyes[{n}] は [始まりの秒, 終わりの秒, 目の絵] にしてください: {item!r}")
        start, end = _seconds(item[0], f"expressions.events.eyes[{n}]"), _seconds(item[1], f"expressions.events.eyes[{n}]")
        if end <= start:
            raise ToolError(f"expressions.events.eyes[{n}] の終わりの秒は、始まりより後にしてください: {item!r}")
        out["eyes"].append([start, end, _name(item[2], f"expressions.events.eyes[{n}]", empty=False)])
    out["blinks"] = [_seconds(t, f"expressions.events.blinks[{n}]") for n, t in enumerate(events.get("blinks", []))]
    out["blink"] = [_name(v, f"expressions.events.blink[{n}]", empty=False) for n, v in enumerate(events.get("blink", []))]
    if out["blinks"] and not out["blink"]:
        raise ToolError("expressions.events.blinks を書くときは、blink（まばたきのコマごとの目の絵の並び）も書いてください")
    return out


def frames(events, fps, count, names):
    """出来事の表 → コマごとの絵の名前の並び（長さ count。コマ i の時刻は i / fps 秒）。names は使える絵の名前（"" はいつも使える）。"""
    if count < 1:
        raise ToolError("expressions.events: 描くコマがありません")
    index = lambda t: int(round(t * fps))                # 秒 → 近いコマの番号（秒の小数のままは比べない）
    mouth = [""] * count
    events_m = events["mouth"]
    for i, (start, name) in enumerate(events_m):
        first = index(start)
        last = min(index(events_m[i + 1][0]) if i + 1 < len(events_m) else count, count)
        for f in range(max(first, 0), last):
            mouth[f] = name
        previous = events_m[i - 1][1] if i else ""
        if name in events["steps"]:                      # 入る: はさむ絵を 1 コマずつ
            for k, step in enumerate(events["steps"][name]):
                if 0 <= first + k < last:
                    mouth[first + k] = step
        elif previous in events["steps"]:                # 出る: 前の口のはさむ絵を、逆の順に 1 コマずつ
            for k, step in enumerate(reversed(events["steps"][previous])):
                if 0 <= first + k < count:
                    mouth[first + k] = step
    eyes = [""] * count
    for start, end, name in events["eyes"]:
        for f in range(max(index(start), 0), min(index(end), count)):
            eyes[f] = name
    for t in events["blinks"]:
        for k, name in enumerate(events["blink"]):
            if 0 <= index(t) + k < count:
                eyes[index(t) + k] = name
    available, out, problems = set(names) | {""}, [], []
    for f in range(count):
        e, m = eyes[f], mouth[f]
        name = e + events["join"] + m if e and m else (e or m)
        if name not in available:
            problems.append((round(f / fps, 3), e, m))
        out.append(name)
    if problems:
        shown = ", ".join(f"{t} 秒（目 {e or 'もとの目'}・口 {m or 'もとの口'}）" for t, e, m in problems[:8])
        raise ToolError(f"expressions.events: 使える絵（images）に無い絵の組み合わせが {len(problems)} コマあります: {shown}"
                        f"{' …' if len(problems) > 8 else ''}。重ねた絵を足すか、表を直してください")
    return out


def times(names, fps):
    """コマごとの絵の名前 → [[秒, 絵の名前], …]（変わるコマだけ。コマ i の秒は i / fps）。"""
    out = [[0.0, names[0]]]
    for f in range(1, len(names)):
        if names[f] != names[f - 1]:
            out.append([f / fps, names[f]])
    return out
