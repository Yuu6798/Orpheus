"""演出の時刻（cues）: カメラのカット・カメラの速い動き・拍・照明の演出の時刻を読み、出来事が重なるかを記録する。

設定の `cues`（全体か clips[] ごと。時刻はそのクリップの頭が 0 秒。`offset_s` を書くと、cue の時刻からその秒を引く）:
- `cuts`: カットの秒の並び。`moves`: カメラが速く動く区間 [[始め, 終わり], …]。`lights`: 照明の演出の区間か秒。
- `beats`: 拍の格子 {"period", "k0_time"}（拍 k は k0_time + period × k 秒）か、拍の秒の並び。
- `track`: model3d の `render.stage` のトラック（model3d-stage-track/0.1。1 つか並び。並びはつないでから読むので、クリップの
  境目のカットも見る）。コマ T と T+1 の間でカメラが飛んだ所をカット、速く動くコマを動きの区間、光（壁・輪郭光・光の柱・塗りの
  縁の強さ）が相対で LIGHT_REL 以上跳ねた所と、色・模様が切り替わった所を照明の演出にする。camera を省いたコマ（描く側の
  既定のカメラ）を含む組は、カットと動きを見ない（照明は見る）。
  `light_keys`: model3d の光のキー（model3d-light-keys/0.1）の拍の格子。
- `tolerance_s`（既定 0.05 秒）: カット・区間との時刻の許し。`beat_tolerance`（既定 0.1 拍）: 拍との許し。

**重なっても出来事は消さない**: 演出と同じ時刻に本当の崩れが出ることもあるので、出来事は要確認のまま残し、重なった演出を
`cues` に書き添えるだけ（「演出との一致」と「異常の可能性」を分けて見られるように）。拍は 0.46 秒ごとのように細かく、どの時刻も
近くに拍があるので、拍と照明の演出は、明るさ・配色の信号（KIND_CHECKS）にだけ重ねる。
"""
from pathlib import Path

import numpy as np

from ..io import PipelineError, read_json

TOLERANCE_S = 0.05
BEAT_TOLERANCE = 0.1
CUT_M, CUT_DEG, CUT_LENS_MM = 0.5, 15.0, 2.0       # トラックのカット: コマ間のカメラの飛び（位置・向き・レンズ）
MOVE_M, MOVE_DEG = 0.05, 1.0                       # トラックの速い動き: コマあたりの移動・向きの変化
LIGHT_REL = 0.5                                    # トラックの照明の演出: 光の強さの、コマ間の相対の跳ね（|差| / 大きい方）
# 演出の種類ごとに、重なりを見る信号（None は全部）。カットは画面全体が変わるので、どの信号でも重なりを書く
KIND_CHECKS = {
    "cut": None,
    "move": {"motion_spike", "teleport", "jitter", "area_pop", "silhouette_pop", "jump"},
    "beat": {"flicker", "costume_pop"},
    "light": {"flicker", "costume_pop", "costume_drift", "jump"},
}
KIND_TEXT = {"cut": "カット", "move": "カメラの動き", "beat": "拍", "light": "照明"}


def _numbers(value, where):
    if not isinstance(value, list) or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        raise PipelineError(f"{where} は秒の並びにしてください: {value!r}")
    return [float(v) for v in value]


def _intervals(value, where):
    out = []
    for item in value if isinstance(value, list) else [None]:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            out.append([float(item), float(item)])
        elif isinstance(item, list) and len(item) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in item) \
                and item[0] <= item[1]:
            out.append([float(item[0]), float(item[1])])
        else:
            raise PipelineError(f"{where} は秒か [始め, 終わり]（始め ≤ 終わり）の並びにしてください: {value!r}")
    return out


def _light_levels(frame):
    """トラックの 1 コマの光の強さ（壁・輪郭光の和・光の柱・塗りの縁）と、色と模様。無い欄は None。"""
    wall, rims, beams, tone = frame.get("wall") or {}, frame.get("rims") or [], frame.get("beams") or {}, frame.get("tone") or {}
    levels = (wall.get("strength"), sum(r.get("energy", 0.0) for r in rims) if rims else None, beams.get("gain"), tone.get("gain"))
    look = (frame.get("stage"), tuple(wall.get("color") or ()), tuple(tuple(r.get("color") or ()) for r in rims))
    return levels, look


def _camera(frame):
    """トラックの 1 コマのカメラの位置・向き（単位ベクトル）・レンズ。camera を省いたコマ（描く側の、腰を追う既定のカメラ）は
    None。lens を省けば、レンズは None（描く側の既定のレンズ）。"""
    camera = frame.get("camera")
    if not camera:
        return None
    loc = np.asarray(camera["location"], float)
    look = np.asarray(camera["target"], float) - loc
    return loc, look / max(float(np.linalg.norm(look)), 1e-12), camera.get("lens")


def from_track(frames, fps):
    """model3d のトラックのコマ（T・camera・光）から、カットの秒・速い動きの区間・照明の演出の秒。→ (cuts, moves, lights)。
    コマ T と T + 1 の間の変化は、動画のコマ T（0 から）= T / fps 秒に見える（T は 1 から、動画のコマ T - 1 が (T - 1) / fps 秒）。
    camera はトラックで省ける。省いたコマ（描く側の既定のカメラで、位置はトラックから分からない）を含む組は、カットとカメラの
    動きを見ない（照明は見る）。レンズは両方のコマに書いてあるときだけ比べる（両方省けば同じ既定のレンズ）。"""
    frames = sorted(frames, key=lambda f: f["T"])
    T = np.array([f["T"] for f in frames])
    cameras = [_camera(f) for f in frames]
    cuts, moving, lights = [], [], []
    for k in range(len(frames) - 1):
        if T[k + 1] != T[k] + 1:                       # 並びにとびがあれば、その間は見ない
            continue
        (before, look_a), (after, look_b) = _light_levels(frames[k]), _light_levels(frames[k + 1])
        jumps = [abs(b - a) / max(abs(a), abs(b), 1e-6) for a, b in zip(before, after) if a is not None and b is not None]
        if look_a != look_b or (jumps and max(jumps) >= LIGHT_REL):
            lights.append(round(T[k] / fps, 4))
        a, b = cameras[k], cameras[k + 1]
        if a is None or b is None:                     # 既定のカメラのコマ: カットと動きは見ない
            continue
        step = float(np.linalg.norm(b[0] - a[0]))
        turn = float(np.degrees(np.arccos(np.clip(a[1] @ b[1], -1, 1))))
        zoom = abs(b[2] - a[2]) if a[2] is not None and b[2] is not None else 0.0
        if step > CUT_M or turn > CUT_DEG or zoom > CUT_LENS_MM:
            cuts.append(round(T[k] / fps, 4))
        elif step > MOVE_M or turn > MOVE_DEG:
            moving.append(int(T[k]))
    moves, start, last = [], None, None
    for value in moving + [None]:                      # 2 コマ以内のすき間はつなぐ
        if value is not None and last is not None and value - last <= 2:
            last = value
            continue
        if start is not None:
            moves.append([round((start - 1) / fps, 4), round(last / fps, 4)])
        start = last = value
    return cuts, moves, lights


def load(spec, base):
    """設定の cues を読む（パスは base 基準）。→ {"cuts", "moves", "lights", "beats"（秒の並び）か "grid", "tolerance_s",
    "beat_tolerance", "sources"}。無ければ None。"""
    if spec is None:
        return None
    offset = float(spec.get("offset_s", 0.0))
    cues = {"cuts": [], "moves": [], "lights": [], "beats": [], "grid": None, "sources": [],
            "tolerance_s": float(spec.get("tolerance_s", TOLERANCE_S)), "beat_tolerance": float(spec.get("beat_tolerance", BEAT_TOLERANCE))}
    if "cuts" in spec:
        cues["cuts"] += _numbers(spec["cuts"], "cues.cuts")
    if "moves" in spec:
        cues["moves"] += _intervals(spec["moves"], "cues.moves")
    if "lights" in spec:
        cues["lights"] += _intervals(spec["lights"], "cues.lights")
    beats = spec.get("beats")
    if isinstance(beats, dict):
        cues["grid"] = {"period": float(beats["period"]), "k0_time": float(beats["k0_time"])}
    elif beats is not None:
        cues["beats"] = _numbers(beats, "cues.beats")
    tracks = spec.get("track")
    tracks = [tracks] if isinstance(tracks, str) else list(tracks or [])
    if tracks:                                         # 並びはつないでから読む（クリップの境目のカットも見るため）
        frames, rates = [], set()
        for rel in tracks:
            path = (Path(base) / rel).resolve()
            if not path.is_file():
                raise PipelineError(f"cues.track が見つかりません: {rel}")
            data = read_json(path)
            if not isinstance(data, dict) or data.get("schema_version") != "model3d-stage-track/0.1" or not data.get("fps"):
                raise PipelineError(f"cues.track は model3d-stage-track/0.1（fps のあるもの）にしてください: {rel}")
            frames += data["frames"]
            rates.add(float(data["fps"]))
        if len(rates) != 1:
            raise PipelineError(f"cues.track の fps がそろっていません: {sorted(rates)}")
        if len({f["T"] for f in frames}) != len(frames):
            raise PipelineError("cues.track の並びで、同じコマ T が重なっています")
        cuts, moves, light_times = from_track(frames, rates.pop())
        cues["cuts"] += cuts
        cues["moves"] += moves
        cues["lights"] += [[t, t] for t in light_times]
        cues["sources"].append({"kind": "track", "paths": [str(rel) for rel in tracks], "cuts": len(cuts), "moves": len(moves),
                                "lights": len(light_times),
                                "frames_without_camera": sum(1 for f in frames if not f.get("camera"))})   # カットと動きを見ていないコマ
    if spec.get("light_keys"):
        path = (Path(base) / spec["light_keys"]).resolve()
        if not path.is_file():
            raise PipelineError(f"cues.light_keys が見つかりません: {spec['light_keys']}")
        data = read_json(path)
        if not isinstance(data, dict) or data.get("schema_version") != "model3d-light-keys/0.1":
            raise PipelineError(f"cues.light_keys は model3d-light-keys/0.1 にしてください: {spec['light_keys']}")
        cues["grid"] = {"period": float(data["grid"]["period"]), "k0_time": float(data["grid"]["k0_time"])}
        cues["sources"].append({"kind": "light_keys", "path": str(spec["light_keys"])})
    if cues["grid"] is not None and not cues["grid"]["period"] > 0:
        raise PipelineError("cues の拍の period は正の秒にしてください")
    if offset:                                         # cue の時刻 → クリップの時刻
        cues["cuts"] = [t - offset for t in cues["cuts"]]
        cues["moves"] = [[a - offset, b - offset] for a, b in cues["moves"]]
        cues["lights"] = [[a - offset, b - offset] for a, b in cues["lights"]]
        cues["beats"] = [t - offset for t in cues["beats"]]
        if cues["grid"]:
            cues["grid"]["k0_time"] -= offset
    cues["cuts"].sort()
    return cues


def _beat_near(t, cues):
    """t に近い拍（秒）と、拍の単位の差。無ければ None。"""
    grid = cues["grid"]
    if grid:
        k = round((t - grid["k0_time"]) / grid["period"])
        beat = grid["k0_time"] + k * grid["period"]
        return beat, abs(t - beat) / grid["period"]
    if cues["beats"]:
        beat = min(cues["beats"], key=lambda b: abs(b - t))
        return beat, None
    return None


def match(event, cues, fps):
    """出来事の信号に重なる演出 [{"kind", "t"（演出の秒か区間）, "offset_s"（山との差）, "checks"}]。重ならなければ空。"""
    if not cues or not fps:
        return []
    tol = cues["tolerance_s"]
    found = {}

    def note(kind, when, peak_t, check):
        key = (kind, tuple(when) if isinstance(when, list) else when)
        anchor = when if not isinstance(when, list) else min(max(peak_t, when[0]), when[1])
        item = found.setdefault(key, {"kind": kind, "t": when, "offset_s": round(peak_t - anchor, 4), "checks": []})
        if check not in item["checks"]:
            item["checks"].append(check)

    for signal in event["signals"]:
        check = signal["check"]
        a, b = signal["frames"][0] / fps, signal["frames"][1] / fps
        peak = signal["peak"] / fps
        for kind in ("cut", "move", "beat", "light"):
            wanted = KIND_CHECKS[kind]
            if wanted is not None and check not in wanted:
                continue
            if kind == "cut":
                for cut in cues["cuts"]:
                    if a - tol <= cut <= b + tol:
                        note("cut", cut, peak, check)
            elif kind in ("move", "light"):
                for start, end in cues["moves" if kind == "move" else "lights"]:
                    if start - tol <= b and a <= end + tol:
                        note(kind, [start, end], peak, check)
            else:
                near = _beat_near(peak, cues)
                if near is None:
                    continue
                beat, beats_off = near
                close = beats_off <= cues["beat_tolerance"] if beats_off is not None else abs(peak - beat) <= tol
                if close:
                    note("beat", round(beat, 4), peak, check)
    return sorted(found.values(), key=lambda m: (list(KIND_TEXT).index(m["kind"]), m["t"] if not isinstance(m["t"], list) else m["t"][0]))


def describe(cues):
    """報告用の要約（配列は数だけ）。"""
    if not cues:
        return None
    return {"cuts": len(cues["cuts"]), "moves": len(cues["moves"]), "lights": len(cues["lights"]),
            "beats": cues["grid"] or len(cues["beats"]), "tolerance_s": cues["tolerance_s"], "beat_tolerance": cues["beat_tolerance"],
            "sources": cues["sources"]}


def text(matches):
    """md に書く短い文。"""
    parts = []
    for m in matches:
        when = f"{m['t'][0]:g}〜{m['t'][1]:g} 秒" if isinstance(m["t"], list) else f"{m['t']:g} 秒"
        parts.append(f"{KIND_TEXT[m['kind']]} {when}（{m['offset_s']:+.2f} 秒）")
    return "・".join(parts)
