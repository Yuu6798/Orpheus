"""設定（inspect/0.1）の読み込み・検証・既定値。

    {"schema_version": "inspect/0.1",
     "clips": [{"id": "cut07", "path": "clips/cut07.mp4", "frames": [0, 120], "origin": {"tool": "…", "job": "…"}}],
     "reference": [{"path": "sheets/front.png", "view": "front"}],
     "foreground": {"key": {"color": [0, 255, 0], "tolerance": 0.22, "softness": 0.12}},
     "thresholds": {"jump_min": 0.08}}

clips の代わりに "dance_spec": "spec.json" を書くと、dance の spec の performer.clips から、使っている区間だけを見る
（人物の切り抜きは spec の key を使い、見つけた所を performer.clips[i] と出力の秒に結び付ける）。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from ..io import PipelineError, read_json, sha256
from . import SCHEMA_VERSION

SCHEMA_PATH = Path(__file__).with_name("inspect.schema.json")

# しきい値はどれも仮の値（合成素材で決めた。最初の実作品で確かめて直す）。単位は docs/inspect.md の表。
THRESHOLDS = {
    "window_s": 0.5,           # 局所の基準（中央値）を取る前後の幅（秒）
    "spike_z": 6.0,            # 急変: 局所の中央値から MAD（×1.4826）の何倍離れたか
    "spike_ratio": 3.0,        # 急変: 局所の中央値の何倍か
    "pair_max_s": 2.0,         # 変わった絵が元に戻るのを探す長さ（秒）
    "return_ratio": 0.5,       # 戻ったとみなす差（入りの変化の何倍以下か）
    "min_segment_s": 0.25,     # 区間で見る信号（配色のずれなど）の最短（秒）
    "jump_min": 0.06,          # 連続性: 画の構造の差（各コマの平均を引いた明るさの差の平均。0..1）
    "flicker_min": 0.04,       # 連続性: 画面の明るさの平均の差（0..1）
    "dup_max": 0.0005,         # 連続性: 同じ絵とみなす前のコマとの差（0..1。圧縮した同じ絵は 0.00002 ほど）
    "dup_context": 4.0,        # 連続性: 周りの差が dup_max の何倍以上（動いている所）のときだけ「止まった」と読む
    "freeze_frames": 3,        # 連続性: 同じ絵がこのコマ数以上続いた所を「止まったコマ」にする（1 つだけの重複は周期の判定にだけ使う）
    "vanish_max_s": 0.5,       # 連続性: これより短く人物が消えた所（前後に人物がいる）
    "motion_min": 0.05,        # 動作: 動きの大きさの p95（体の高さ / コマ）
    "teleport_min": 0.06,      # 動作: 人物の重心の移動（体の高さ / コマ）
    "jitter_min": 0.006,       # 動作: 重心の 2 階差の RMS（体の高さ）
    "jitter_ratio": 1.3,       # 動作: 2 階差 / 1 階差 の中央値の比（なめらかな動きは 1 より小さい）
    "jitter_reversals": 0.5,   # 動作: 前のコマと逆向きに動いたコマの割合（毎コマ向きの入れ替わる揺れは 1 に近い。跳ねる動きは小さい）
    "shape_min": 0.12,         # 形状: 1 − IoU（輪郭を囲みの大きさと位置にそろえて比べる）
    "area_min": 0.18,          # 形状: |ln(面積の比)|（0.18 で約 1.2 倍）
    "fragment_max_s": 0.5,     # 形状: 人物の塊の数がこれより短く変わった所
    "palette_min": 0.15,       # 衣装: 人物の配色の前のコマとの距離（Bhattacharyya）
    "drift_min": 0.35,         # 衣装: 冒頭の配色からの距離（Bhattacharyya）
    "anchor_s": 0.5,           # 衣装: 冒頭の配色を取る長さ（秒）
    "ref_match_de": 18.0,      # 設定画: 設定画の色に入る色の差（ΔE。明るさの差は半分に数える）
    "ref_extra_de": 30.0,      # 設定画: どの色からもこれより遠い色を「設定画に無い色」と数える
    "ref_min_weight": 0.05,    # 設定画: 欠けを見る色の、設定画の中の割合の下限
    "ref_missing_ratio": 0.25, # 設定画: 割合が設定画のこの倍より少なければ欠け
    "ref_extra_min": 0.12,     # 設定画: 設定画に無い色がこの割合以上
    "plan_merge_s": 0.15,      # 修正計画: すき間がこれ以下の所は 1 つの区間にまとめる（間の短い良いコマを前後の生成し直しに挟まない）
    "plan_pad_frames": 2,      # 修正計画: 生成し直す区間を前後に広げるコマ数
}
VLM_DEFAULTS = {"every_s": 1.0, "max_frames": 24, "max_side": 768, "language": "ja"}
DEFAULT_KEY = {"tolerance": 0.22, "softness": 0.12}    # dance の key と同じ既定値
ANALYSIS_WIDTH = 320


@dataclass
class Config:
    path: Path
    base: Path
    sha256: str
    raw: dict
    thresholds: dict
    checks: tuple
    analysis_width: int
    references: list = field(default_factory=list)
    clips: list = field(default_factory=list)       # clips の書き方のとき: [{id, path, rel, frames, origin, foreground}]
    dance_spec: Path | None = None
    origins: dict = field(default_factory=dict)
    foreground: object = None                       # 全体の既定（None = 書いていない）
    vlm: dict = field(default_factory=dict)
    cues: dict | None = None                        # 全体の演出の時刻（cues.load の形。None = 書いていない）
    notes: list = field(default_factory=list)       # 実行中に気づいた注意（報告に残す）

    def resolve(self, value):
        path = Path(value)
        return (path if path.is_absolute() else self.base / path).resolve()


def _validate(raw):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        if any(e.validator == "oneOf" and not e.absolute_path for e in errors):
            lines.insert(0, "  clips と dance_spec のどちらか 1 つだけを書いてください")
        raise PipelineError("inspect の設定の形が違います:\n" + "\n".join(lines))


def normalize_foreground(value):
    """None / "none" / {"key": {...}} / {"box": [...]} → None / {"kind": "none"} / {"kind": "key", "key": …} / {"kind": "box", "box": …}。"""
    if value is None:
        return None
    if value == "none":
        return {"kind": "none"}
    if "key" in value:
        key = value["key"]
        return {"kind": "key", "key": {"color": [float(c) for c in key["color"]],
                                       "tolerance": float(key.get("tolerance", DEFAULT_KEY["tolerance"])),
                                       "softness": float(key.get("softness", DEFAULT_KEY["softness"]))}}
    return {"kind": "box", "box": [float(v) for v in value["box"]]}


def _box_ok(box):
    return box[0] < box[2] and box[1] < box[3]


def load(path):
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"inspect の設定が見つかりません: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"inspect の設定が JSON として読めません（{path.name}）: {error}") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"inspect の設定の schema_version は {SCHEMA_VERSION!r} にしてください")
    _validate(raw)
    thresholds = dict(THRESHOLDS)
    unknown = sorted(set(raw.get("thresholds", {})) - set(THRESHOLDS))
    if unknown:
        raise PipelineError(f"知らないしきい値です: {unknown}（使える名前は docs/inspect.md の表）")
    for name, value in raw.get("thresholds", {}).items():
        if not value > 0:
            raise PipelineError(f"しきい値 {name} は 0 より大きくしてください: {value}")
        thresholds[name] = float(value)
    for name in ("plan_pad_frames", "freeze_frames"):
        thresholds[name] = int(round(thresholds[name]))
    if thresholds["freeze_frames"] < 2:
        raise PipelineError("しきい値 freeze_frames は 2 以上にしてください（同じ絵が 2 コマ = 1 コマ止まる）")
    if thresholds["jitter_reversals"] > 1:
        raise PipelineError("しきい値 jitter_reversals は 0 より大きく 1 以下にしてください")
    from . import cues as cue_times
    resolved = path.resolve()
    config = Config(path=resolved, base=resolved.parent, sha256=sha256(resolved), raw=raw, thresholds=thresholds,
                    checks=tuple(raw["checks"]) if "checks" in raw else ("continuity", "motion", "shape", "costume"),
                    analysis_width=int(raw.get("analysis_width", ANALYSIS_WIDTH)),
                    origins=dict(raw.get("origins", {})), foreground=normalize_foreground(raw.get("foreground")),
                    vlm={**VLM_DEFAULTS, **{k: v for k, v in raw.get("vlm", {}).items() if not k.startswith("x_")}},
                    cues=cue_times.load(raw.get("cues"), resolved.parent))
    for box_owner in [config.foreground] + [normalize_foreground(c.get("foreground")) for c in raw.get("clips", [])]:
        if box_owner and box_owner["kind"] == "box" and not _box_ok(box_owner["box"]):
            raise PipelineError(f"foreground の box は [x0, y0, x1, y1] で x0 < x1・y0 < y1 にしてください: {box_owner['box']}")
    for item in raw.get("reference", []):
        if "box" in item and not _box_ok(item["box"]):
            raise PipelineError(f"reference の box は [x0, y0, x1, y1] で x0 < x1・y0 < y1 にしてください: {item['box']}")
        file = config.resolve(item["path"])
        if not file.is_file():
            raise PipelineError(f"設定画が見つかりません: {item['path']}")
        config.references.append({"path": file, "rel": item["path"], "view": item.get("view"), "box": item.get("box"),
                                  "background": item.get("background", "auto"), "key": normalize_foreground({"key": item["key"]})["key"]
                                  if "key" in item else None})
    if "dance_spec" in raw:
        config.dance_spec = config.resolve(raw["dance_spec"])
        if not config.dance_spec.is_file():
            raise PipelineError(f"dance の spec が見つかりません: {raw['dance_spec']}")
        if config.cues is not None:
            raise PipelineError("cues は clips の書き方で使います（dance_spec で見る素材のコマの時刻は、出力の時刻と違うため）")
        return config
    if config.origins:
        raise PipelineError("origins は dance_spec と使います（clips では clips[].origin に書いてください）")
    seen = set()
    for item in raw["clips"]:
        file = config.resolve(item["path"])
        if not file.is_file():
            raise PipelineError(f"動画が見つかりません: {item['path']}")
        clip_id = item.get("id") or file.stem
        if clip_id in seen:
            raise PipelineError(f"clips の id が重なっています: {clip_id}（id を書き分けてください）")
        seen.add(clip_id)
        frames = item.get("frames")
        if frames is not None and not frames[0] < frames[1]:
            raise PipelineError(f"clips[].frames は [開始, 終了) で開始 < 終了にしてください: {frames}")
        config.clips.append({"id": clip_id, "path": file, "rel": item["path"], "frames": frames, "origin": item.get("origin"),
                             "foreground": normalize_foreground(item.get("foreground")),
                             "cues": cue_times.load(item["cues"], config.base) if "cues" in item else config.cues})
    return config
