"""fx-timeline の版と既定の値、読み込みと確かめ（parse_timeline・_validate）、拍の脈（pulse_at・shot_pulse）と拍への寄せ（snap）。
mv_pipeline.fx.sequence から以前の名前で読める。cv2 は読まない。"""
import bisect
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import jsonschema
import numpy as np

from ..io import PipelineError, read_json_file, safe_path, schema_lines, sha256
from . import spec as fxspec


SCHEMA_VERSION = "fx-timeline/0.2"


SCHEMA_V01 = "fx-timeline/0.1"         # 0.2 の新しい項目（つなぎの burn）を使わない timeline。そのまま読めて同じ絵になる


RECORD_SCHEMA = "dance-timeline/0.1"   # 0.2 は dance の描画の記録（frames・layers）が要る形


_SCHEMAS = {SCHEMA_VERSION: Path(__file__).with_name("fx_timeline.schema.json"),
            SCHEMA_V01: Path(__file__).with_name("fx_timeline_0_1.schema.json")}


FLASH = {"strength": 0.75, "color": [235.0, 210.0, 255.0], "window_s": 0.30, "rate": 13.0}


BURN = {"seed": 0, "scale": 0.08, "soft": 0.035, "char": 0.0, "from": [0.5, 0.5], "bias": 0.0}     # 縁（edge）は書いたときだけ


BURN_EDGE = {"color": [255.0, 125.0, 35.0], "width": 0.05, "gain": 0.9}     # 色は fx.fire の小さい炎の光と同じ橙


OVERLAPS = ("xfade", "burn")           # 前のショットを描き続けて重ねるつなぎ（窓は start から duration_s）


LOOP = {"crossfade_frames": 6.0, "start_s": 0.0, "mix": 0.0, "mix_speed": 0.77, "mix_offset_s": 0.41}


KEEP_SHOTS = 2          # 前処理済みのショットを持つ数（xfade・burn は前と今の 2 つ）。順に描くので、古いものから捨てる


KEEP_FRAMES = 8         # 動画のショットが持つ復号済みのコマの数（loop はループの区間 + 2 コマ）


PULSE_DECAY = 9.0       # 拍の脈の減り方（1/秒。実制作の 2 つのレンダラと同じ）


def ease(x):
    """つなぎの割合（0〜1 に切ってから 3x² − 2x³）。fire（accent）も同じものを読む。dance.cues.smoothstep とは別物（nan は nan のまま、
    整数の 0・1 は整数のまま返す）。"""
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def schema(version=SCHEMA_VERSION):
    """JSON Schema（version は fx-timeline/0.2 か 0.1）。look は fx-shot と同じ定義を使う。"""
    data = json.loads(_SCHEMAS[version].read_text(encoding="utf-8"))
    look = fxspec.look_schema()
    data["properties"]["look"] = look["look"]
    data["$defs"].update(look["defs"])
    return data


@dataclass(frozen=True)
class Timeline:
    data: dict
    base_dir: Path
    path: Path | None
    sha256: str
    warnings: tuple
    specs: tuple           # ショットごとの fx の Spec（data["shots"] と同じ順。動画のショットは None）
    source_schema: str = SCHEMA_VERSION    # ファイルに書いてあった版（data の schema_version は読んだ後の正規形の版）

    def resolve(self, rel):
        return safe_path(self.base_dir, rel)


def load_timeline(path):
    path = Path(path)
    raw = read_json_file(path, f"fx timeline not found: {path}", f"fx timeline is not valid JSON ({path.name})")
    return parse_timeline(raw, path.resolve().parent, path.resolve(), sha256(path))


def _check_kinds(raw):
    """spec と video の取り違えを、スキーマの oneOf より分かる文で止める。"""
    shots = raw.get("shots")
    for index, shot in enumerate(shots if isinstance(shots, list) else []):
        if not isinstance(shot, dict):
            continue
        if ("spec" in shot) == ("video" in shot):
            raise PipelineError(f"fx timeline: shots[{index}] は spec（fx のショット）か video（動画のショット）のどちらか一方を書いてください")
        wrong = [key for key in (("offset_s", "variant") if "video" in shot else ("camera", "faces")) if key in shot]
        if wrong:
            hint = "動画の中の秒は video.offset_s に書きます" if "video" in shot else "カメラと顔の枠は spec に書きます"
            raise PipelineError(f"fx timeline: shots[{index}] の {'・'.join(wrong)} は{'動画' if 'video' in shot else ' spec '}のショットには使えません（{hint}）")


def _check_version(raw):
    """版ごとのつなぎの取り違えを、スキーマの oneOf より分かる文で止める（0.1 の timeline に 0.2 の burn）。"""
    shots = raw.get("shots")
    for index, shot in enumerate(shots if isinstance(shots, list) else []):
        transition = shot.get("transition_in") if isinstance(shot, dict) else None
        if raw["schema_version"] == SCHEMA_V01 and isinstance(transition, dict) and transition.get("type") == "burn":
            raise PipelineError(f"fx timeline: shots[{index}] の burn（焼け抜け）は {SCHEMA_VERSION} のつなぎです。{SCHEMA_V01} の timeline には"
                                f"書けません（schema_version を {SCHEMA_VERSION!r} にしてください。ほかの項目はそのまま読めます）")


def parse_timeline(raw, base_dir, path=None, digest=""):
    from ..dance import beats as dancebeats            # 拍の格子と時刻の参照は dance と同じ書き方

    version = raw.get("schema_version") if isinstance(raw, dict) else None
    if not isinstance(version, str) or version not in _SCHEMAS:
        raise PipelineError(f"fx timeline schema_version must be {SCHEMA_VERSION!r} or {SCHEMA_V01!r}, got {version!r}")
    _check_kinds(raw)
    _check_version(raw)
    errors = sorted(jsonschema.Draft202012Validator(schema(version)).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = schema_lines(errors)
        raise PipelineError("fx timeline is invalid:\n" + "\n".join(lines))
    base_dir = Path(base_dir).resolve()
    out = raw["output"]
    fps = float(out.get("fps", 24))
    duration = float(raw["duration_s"])
    grid = dancebeats.load_grid(raw.get("beats"), lambda rel: safe_path(base_dir, rel))
    shots = []
    for index, shot in enumerate(raw["shots"]):
        start = dancebeats.resolve_time(shot["start"], grid, f"shots[{index}] の start", fps)
        snapped = bool(shot.get("snap", False))
        if snapped and grid is None:
            raise PipelineError(f"fx timeline: shots[{index}] の snap には beats が必要です")
        common = {"start": snap(start, grid.beats, duration) if snapped else start, "requested": start, "snap": snapped,
                  "transition_in": _transition(shot.get("transition_in", {"type": "cut"})),
                  "beat_exempt": bool(shot.get("beat_exempt", False)), "align": shot.get("align", "beat")}
        if "spec" in shot:
            shots.append({"kind": "spec", "spec": shot["spec"], "offset_s": float(shot.get("offset_s", 0.0)),
                          "variant": shot.get("variant", "both"), **common})
        else:
            video = _video(shot["video"], index)
            if not safe_path(base_dir, video["path"]).is_file():
                raise PipelineError(f"fx timeline: shots[{index}] の動画が見つかりません: {video['path']}")
            faces = fxspec.normalize_faces(shot.get("faces"))
            for number, face in enumerate(faces):
                if not (face["box"][0] < face["box"][2] and face["box"][1] < face["box"][3]):
                    raise PipelineError(f"fx timeline: shots[{index}].faces[{number}].box は [x0, y0, x1, y1] で x0 < x1、y0 < y1 にしてください")
            shots.append({"kind": "video", "video": video, "camera": _camera(shot.get("camera"), index), "faces": faces, "offset_s": 0.0,
                          **common})
    for index, shot in enumerate(shots):
        shot["end"] = shots[index + 1]["start"] if index + 1 < len(shots) else duration
    specs = tuple(_without_audio(fxspec.load_spec(safe_path(base_dir, shot["spec"]))) if shot["kind"] == "spec" else None for shot in shots)
    look = None if raw.get("look") is None else fxspec.normalize_look(raw["look"])
    data = {"schema_version": SCHEMA_VERSION,
            "output": {"width": int(out["width"]), "height": int(out["height"]), "fps": fps, "crf": int(out.get("crf", 18))},
            "duration_s": duration, "beats": None if grid is None else {"path": grid.path, **grid.record()},
            "audio": None if "audio" not in raw else {"path": raw["audio"]["path"], "offset_s": float(raw["audio"].get("offset_s", 0.0))},
            "look": look, "pulse": _pulse(raw.get("pulse"), grid, fps), "shots": shots}
    warnings = _validate(data, specs) + fxspec.look_warnings(look)
    if data["audio"]:
        safe_path(base_dir, data["audio"]["path"])
    return Timeline(data=data, base_dir=base_dir, path=path, sha256=digest, warnings=tuple(warnings), specs=specs, source_schema=version)


def _without_audio(sp):
    """ショットの spec の audio は使わない（音は timeline の audio）。外しておけば、素材の確かめ・記録・key にも入らない
    （ファイルが無くても止まらず、中身を変えても描き直さない）。path・sha256 は元のファイルのまま。"""
    return replace(sp, data={**sp.data, "audio": None}) if sp.data["audio"] else sp


def _transition(raw):
    kind = raw["type"]
    if kind == "flash":
        return {"type": "flash", "strength": float(raw.get("strength", FLASH["strength"])),
                "color": [float(v) for v in raw.get("color", FLASH["color"])],
                "window_s": float(raw.get("window_s", FLASH["window_s"])), "rate": float(raw.get("rate", FLASH["rate"]))}
    if kind in ("xfade", "black"):
        return {"type": kind, "duration_s": float(raw["duration_s"])}
    if kind == "burn":
        edge = raw.get("edge")
        if edge is not None:
            edge = {"color": [float(v) for v in edge.get("color", BURN_EDGE["color"])],
                    **{key: float(edge.get(key, BURN_EDGE[key])) for key in ("width", "gain")}}
        return {"type": "burn", "duration_s": float(raw["duration_s"]), "seed": int(raw.get("seed", BURN["seed"])),
                **{key: float(raw.get(key, BURN[key])) for key in ("scale", "soft")},
                "edge": edge if edge and edge["gain"] > 0 else None,          # gain 0 は縁なしと同じ絵（しきい値の余白も縁なしと同じ）
                "char": float(raw.get("char", BURN["char"])), "from": [float(v) for v in raw.get("from", BURN["from"])],
                "bias": float(raw.get("bias", BURN["bias"]))}
    return {"type": "cut"}


def _video(raw, index):
    loop = raw.get("loop")
    if loop is not None:
        if "offset_s" in raw:
            raise PipelineError(f"fx timeline: shots[{index}].video の loop と offset_s は一緒に使えません（ループの中の位置は loop.start_s）")
        first, last = int(loop["frames"][0]), int(loop["frames"][1])
        crossfade = float(loop.get("crossfade_frames", LOOP["crossfade_frames"]))
        if last - first - crossfade <= 0:
            raise PipelineError(f"fx timeline: shots[{index}].video.loop の区間 {first}〜{last} コマが、"
                                f"つなぎ目の crossfade_frames {crossfade:g} コマより長くありません")
        if crossfade > last - first - crossfade:   # 周期（区間 − 重ね）より長い重ねは、終わる前に折り返して境目で絵が飛ぶ
            raise PipelineError(f"fx timeline: shots[{index}].video.loop の crossfade_frames {crossfade:g} コマが、区間 {first}〜{last}"
                                f"（{last - first} コマ）の半分より長いです。重ねが終わる前に折り返し、ループの境目で絵が飛びます"
                                f"（crossfade_frames は {(last - first) / 2:g} コマ以下にしてください）")
        loop = {"frames": [first, last], "crossfade_frames": crossfade,
                **{key: float(loop.get(key, LOOP[key])) for key in ("start_s", "mix", "mix_speed", "mix_offset_s")}}
    return {"path": raw["path"], "fps": None if raw.get("fps") is None else float(raw["fps"]), "speed": float(raw.get("speed", 1.0)),
            "offset_s": float(raw.get("offset_s", 0.0)), "loop": loop}


def _camera(raw, index):
    raw = raw or {}
    keys = fxspec._keys(raw.get("keys") or [{"t": 0.0}], {"zoom": 1.0, "center": [0.5, 0.5]})
    times = [key["t"] for key in keys]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise PipelineError(f"fx timeline: shots[{index}].camera.keys の t は厳密な昇順にしてください: {times}")
    punch = raw.get("punch")
    return {"keys": keys, "parallax_px": 0.0, "parallax_dir": [1.0, 0.0], "frame": raw.get("frame", "cover"), "clamp": bool(raw.get("clamp", False)),
            "punch": None if punch is None else {"amount": float(punch["amount"]), "duration_s": float(punch["duration_s"])}}


def _pulse(raw, grid, fps):
    """pulse（拍の脈）を {amount, decay, sections}（sections は秒の [始め, 終わり) の並びか None = 全体）にする。"""
    from ..dance import beats as dancebeats
    if raw is None:
        return None
    if grid is None:
        raise PipelineError("fx timeline: pulse には beats が必要です")
    sections = None
    if "sections" in raw:
        sections = []
        for number, (a, b) in enumerate(raw["sections"]):
            start = dancebeats.resolve_time(a, grid, f"pulse.sections[{number}] の始め", fps)
            end = dancebeats.resolve_time(b, grid, f"pulse.sections[{number}] の終わり", fps)
            if end <= start:
                raise PipelineError(f"fx timeline: pulse.sections[{number}] は始め < 終わりにしてください（{start:g} → {end:g} 秒）")
            sections.append([start, end])
    return {"amount": float(raw["amount"]), "decay": float(raw.get("decay", PULSE_DECAY)), "sections": sections}


def pulse_at(pulse, beats, t):
    """timeline の時刻 t の拍の脈（zoom に足す割合）。実制作のレンダラの beat_pulse と同じ式:
    amount·exp(−(t − 直前の拍)·decay)。直前の拍は t より前の最後の拍（t がちょうど拍なら 1 つ前の拍）。区間の外と最初の拍の前は 0。"""
    if pulse["sections"] is not None and not any(a <= t < b for a, b in pulse["sections"]):
        return 0.0
    i = bisect.bisect_left(beats, t) - 1
    return 0.0 if i < 0 else pulse["amount"] * math.exp(-(t - beats[i]) * pulse["decay"])


def shot_pulse(data, index):
    """ショット index の camera に付ける拍の脈（ショットの中の時刻 → 割合）。timeline に pulse が無ければ None。"""
    if data["pulse"] is None:
        return None
    pulse, beats, shot = data["pulse"], data["beats"]["beats"], data["shots"][index]
    start, offset = shot["start"], shot["offset_s"]
    return lambda local: pulse_at(pulse, beats, start + (local - offset))


def snap(t, beats, duration):
    """いちばん近い拍（実制作の snap と同じ: 0 以下は 0、終わりの 0.01 秒以内は終わり。同じ距離なら前の拍）。"""
    if t <= 0:
        return 0.0
    if t >= duration - 0.01:
        return duration
    values = np.asarray(beats, dtype=np.float64)
    return float(values[np.argmin(np.abs(values - t))])


def shot_name(shot):
    """表や注意に出すショットの名前（spec のパスか video のパス）。"""
    return shot["spec"] if shot["kind"] == "spec" else shot["video"]["path"]


def _overlap_tail(shots, index):
    """次のショットが xfade か burn なら (その長さ, つなぎの種類)（その間もショット index を描き続ける）。それ以外は (0, None)。"""
    following = shots[index + 1]["transition_in"] if index + 1 < len(shots) else {"type": "cut"}
    return (following["duration_s"], following["type"]) if following["type"] in OVERLAPS else (0.0, None)


def _validate(data, specs):
    shots, duration, fps = data["shots"], data["duration_s"], data["output"]["fps"]
    if shots[0]["start"] > 1e-9:
        raise PipelineError(f"fx timeline: 最初のショットは 0 秒から始めてください（{shots[0]['start']:g} 秒）")
    warnings = []
    for index, (shot, sp) in enumerate(zip(shots, specs)):
        where = f"shots[{index}]（{shot_name(shot)}）"
        if shot["end"] <= shot["start"] + 1e-9:
            moved = "（snap で寄せた後）" if shot["snap"] or (index + 1 < len(shots) and shots[index + 1]["snap"]) else ""
            raise PipelineError(f"fx timeline: {where} の長さが 0 以下です{moved}（start {shot['start']:g} 秒、終わり {shot['end']:g} 秒）。"
                                "start は昇順にし、最後のショットは duration_s より前から始めてください")
        transition, length = shot["transition_in"], shot["end"] - shot["start"]
        if index == 0 and transition["type"] in OVERLAPS:
            raise PipelineError(f"fx timeline: 最初のショットは {transition['type']} できません（前のショットがありません。黒から上げるなら black）")
        if transition["type"] in (*OVERLAPS, "black") and transition["duration_s"] > length + 1e-9:
            raise PipelineError(f"fx timeline: {where} の {transition['type']} の duration_s {transition['duration_s']:g} 秒が"
                                f"ショットの長さ {length:g} 秒より長いです")
        if shot["snap"] and abs(shot["start"] - shot["requested"]) > 0.25:
            warnings.append(f"shots[{index}]: snap で start が {shot['requested']:g} → {shot['start']:g} 秒に動きました")
        if sp is None:
            continue
        tail, kind = _overlap_tail(shots, index)    # 次の xfade・burn の間も、このショットを描き続ける
        local_end = shot["offset_s"] + length + tail
        if local_end > sp.data["duration_s"] + 1e-6:
            warnings.append(f"{where}: spec の長さ {sp.data['duration_s']:g} 秒を超えて {local_end:g} 秒まで描きます"
                            f"（{f'次のショットの {kind} の間も描くため。' if tail else ''}カメラのキーなどは spec の終わりの値のまま）")
        if abs(sp.data["output"]["fps"] - fps) > 1e-9:
            warnings.append(f"{where}: spec の fps {sp.data['output']['fps']:g} が timeline の fps {fps:g} と違います"
                            "（spec の look の粒状感のコマなど、spec の fps で数えるものがあります）")
    return warnings
