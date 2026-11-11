"""光の強さのキー（`mv audio keys`）。打点の記録（mv audio hits）から fx-shot の光の強さ（lights[].intensity）のキーを作り、
その光のキーだけを置き換えた spec を、もとの spec の隣に書く（手で打っていた稲光の明滅を、打点から作る）。

    mv audio keys HITS.json --spec SPEC.json --light ID [--start S] [--from strong|onsets] [--min-strength X]
                  [--section A B]… [--min-gap-s 1.6] [--base 0] [--gain 1] [--by-strength] [--out FILE] [--force]

明滅の形は、実制作の本編の描画の台本の稲妻と同じ:
- 使う打点: 記録の一覧（--from。既定 strong）の順に、--section の区間（曲の時刻の [A, B) 秒。いくつでも。無ければ曲全体）に入り、
  直前に使った打点から --min-gap-s 秒（既定 1.6）より後のものだけを使う。--min-strength は、その前に弱い打点を外す（実制作には無い）。
- 打点 s の明滅: dt = 曲の時刻 − s が −0.01 以上 0.5 未満のとき exp(−14·dt) + 0.5·exp(−40·|dt − 0.12|)、それ以外は 0。
  量 = 打点の明滅の最大（1.2 で頭打ち）。--by-strength は、打点ごとに記録の強さを掛けてから最大を取る（実制作には無い）。
- キー: ショットのコマ i の時刻 t = i / fps（曲の時刻は start + t）で v = base + gain × 量 を出し、前後のコマと同じ値のコマは省く
  （fx はキーの間を直線で補うので、コマの時刻の値は変わらない）。
- start（ショットの 0 秒が曲の何秒か）は、spec の audio が打点の記録と同じ音源（sha256）なら audio.offset_s。違えば --start が要る。
出力の spec は、もとの spec と同じフォルダに置く（素材の相対パスは spec のフォルダから読むため）。既定は `<spec の名前>_keys.json`。
入力（spec・打点の記録と、spec の素材の base・depth・マスク・音・overlays）と同じファイルには、--force でも書かない。
光には、作り方の記録 x_audio_keys を付ける。
"""
import bisect
import copy
import math
from pathlib import Path

from ..io import PipelineError, read_json, sha256
from . import files
from .hits import SCHEMA as HITS_SCHEMA

LEAD_S, LENGTH_S, DECAY, ECHO_GAIN, ECHO_DELAY_S, ECHO_SHARP, CAP = 0.01, 0.5, 14, 0.5, 0.12, 40, 1.2
MIN_GAP_S = 1.6
SOURCES = ("strong", "onsets")
MAX_VALUE = 20.0                      # fx-shot の keyed_value の上限


def select(hits, sections=None, min_gap_s=MIN_GAP_S, min_strength=None):
    """明滅に使う打点（実制作の稲妻の選び方。sections は曲の時刻の [A, B) の列で、空なら曲全体）。"""
    kept = []
    for hit in hits:
        s = hit["t"]
        if min_strength is not None and hit["strength"] < min_strength:
            continue
        if sections and not any(a <= s < b for a, b in sections):
            continue
        if kept and not s - kept[-1]["t"] > min_gap_s:
            continue
        kept.append(hit)
    return kept


def amount(t, kept, by_strength=False, _times=None):
    """曲の時刻 t の明滅の量（実制作の稲妻の量と同じ式。kept は時刻順）。"""
    times = _times if _times is not None else [hit["t"] for hit in kept]
    v = 0.0
    # 効くのは t − 0.5 < s ≤ t + 0.01 の打点だけ。境目の丸めで漏らさないよう広めに取り、条件は下の式のまま確かめる
    for hit in kept[bisect.bisect_left(times, t - LENGTH_S - 0.1):bisect.bisect_right(times, t + LEAD_S + 0.1)]:
        dt = t - hit["t"]
        if -LEAD_S <= dt < LENGTH_S:
            flash = math.exp(-dt * DECAY) + ECHO_GAIN * math.exp(-abs(dt - ECHO_DELAY_S) * ECHO_SHARP)
            v = max(v, hit["strength"] * flash if by_strength else flash)
    return min(v, CAP)


def keys(kept, start, frames, fps, base=0.0, gain=1.0, by_strength=False):
    """ショットのコマごとの v = base + gain × 量 を、前後と同じ値のコマを省いたキー [{t, v}] にする。"""
    times = [hit["t"] for hit in kept]
    values = [base + gain * amount(start + i / fps, kept, by_strength, times) for i in range(frames)]
    return [{"t": i / fps, "v": v} for i, v in enumerate(values)
            if not (0 < i < frames - 1 and values[i - 1] == v == values[i + 1])]


def _number(value, name, low=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (low is not None and value < low):
        raise PipelineError(f"{name} は{'' if low is None else f' {low:g} 以上の'}数にしてください: {value!r}")
    return float(value)


def _sections(sections):
    out = []
    for pair in sections or []:
        a, b = (_number(v, "--section") for v in pair)
        if not a < b:
            raise PipelineError(f"--section は 始め < 終わり にしてください（{a:g}, {b:g}）")
        out.append((a, b))
    return out


def _hits(path, source):
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"打点の記録が見つかりません: {path}")
    record = read_json(path)
    if not isinstance(record, dict) or record.get("schema_version") != HITS_SCHEMA:
        raise PipelineError(f"mv audio hits の記録（{HITS_SCHEMA}）を渡してください: {path}")
    if source not in SOURCES:
        raise PipelineError(f"--from は {' か '.join(SOURCES)} にしてください: {source!r}")
    hits = record.get(source)
    if not isinstance(hits, list):
        raise PipelineError(f"打点の記録に {source} の一覧がありません: {path}")
    return record, sorted(hits, key=lambda hit: hit["t"])


def run(hits_path, spec_path, light, out=None, start=None, source="strong", min_strength=None, sections=None,
        min_gap_s=MIN_GAP_S, base=0.0, gain=1.0, by_strength=False, force=False):
    """打点から光の強さのキーを作り、その光のキーだけを置き換えた spec を書く。"""
    from ..fx import spec as fxspec
    from ..fx import timeline

    hits_path, spec_path = Path(hits_path), Path(spec_path)
    record, hits = _hits(hits_path, source)
    min_gap_s = _number(min_gap_s, "--min-gap-s", 0)
    base, gain = _number(base, "--base", 0), _number(gain, "--gain", 0)
    min_strength = None if min_strength is None else _number(min_strength, "--min-strength", 0)
    sections = _sections(sections)
    spec = fxspec.load_spec(spec_path)
    raw = read_json(spec_path)
    found = [i for i, entry in enumerate(raw.get("lights") or []) if entry.get("id") == light]
    if len(found) != 1:
        names = [entry.get("id") for entry in raw.get("lights") or []]
        raise PipelineError(f"spec に光 {light!r} がありません（ある光: {names}）" if not found else f"spec に光 {light!r} が 2 つ以上あります")
    if start is None:
        audio = spec.data["audio"]
        audio_path = spec.base_dir / audio["path"] if audio else None
        if audio_path is None or not audio_path.is_file() or sha256(audio_path) != (record.get("audio") or {}).get("sha256"):
            raise PipelineError("spec の audio が打点の記録と同じ音源ではないので、ショットの 0 秒が曲の何秒かを --start で渡してください")
        start = float(audio["offset_s"])
    start = _number(start, "--start")
    fps, duration = float(spec.data["output"]["fps"]), float(spec.data["duration_s"])
    frames = timeline.frame_count(duration, fps)
    kept = select(hits, sections, min_gap_s, min_strength)
    made = keys(kept, start, frames, fps, base, gain, by_strength)
    peak = max(key["v"] for key in made)
    if peak > MAX_VALUE:
        raise PipelineError(f"光の強さが {MAX_VALUE:g} を超えます（最大 {peak:.3f}）。--base か --gain を下げてください")
    used = [hit["t"] for hit in kept if start - LENGTH_S < hit["t"] <= start + (frames - 1) / fps + LEAD_S]
    patched = copy.deepcopy(raw)
    entry = patched["lights"][found[0]]
    entry["intensity"] = made
    entry["x_audio_keys"] = {"hits": hits_path.name, "hits_sha256": sha256(hits_path), "from": source, "start_s": start,
                             "sections": [list(pair) for pair in sections], "min_gap_s": min_gap_s, "min_strength": min_strength,
                             "base": base, "gain": gain, "by_strength": bool(by_strength), "hits_used_s": used}
    fxspec.parse_spec(patched, spec.base_dir)            # できた spec が読めることを確かめる（書く前に）
    out = Path(out) if out else spec_path.with_name(f"{spec_path.stem}_keys{spec_path.suffix}")
    if files.same_file(out.parent) != files.same_file(spec_path.parent):
        raise PipelineError(f"--out は spec と同じフォルダに置いてください（素材の相対パスは spec のフォルダから読むため）: {out}")
    inputs = {files.same_file(spec_path): "spec", files.same_file(hits_path): "打点の記録"}
    # spec の素材（base・depth・マスク・音・overlays）も入力。置き換えると、もとの spec も出力の spec も壊れる
    for role, rel in [*fxspec.asset_refs(spec.data).items(), *(("overlays", entry["source"]) for entry in spec.data["overlays"])]:
        inputs.setdefault(files.same_file(spec.resolve(rel)), f"spec の素材（{role}）")
    files.refuse_inputs(out, inputs)
    state = files.write_json(out, patched, force, indent=2)
    return {"spec": str(out), "state": state, "estimated": True, "light": light, "from": source, "start_s": start, "fps": fps,
            "frames": frames, "keys": len(made), "peak": peak, "hits_used_s": [round(t, 3) for t in used]}
