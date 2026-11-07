"""VLM（画像を読めるモデル）への依頼の束・外部プログラムの呼び出し・返答の取り込み。

Orpheus はネットワークを呼ばない。依頼の束（request.json と画像）を書き、ユーザーが CLI で明示した設定（--vlm）の argv だけを実行する。
設定は `docs/adapters.md` と同じ形（tool・version・argv）に `sends_data`（素材を外へ送るか）を必ず書く。sends_data が true の設定は
`--allow-send` が無いと動かさない（AGENTS.md「依頼のない送信をしない」）。束だけを書く（--vlm-packet）、手で書いた返答を取り込む
（--vlm-response）こともできる。

返答の指摘は推定（evidence: estimated）で、合否にも数値の検査にも使わない。同じクリップの近いコマ（1 コマ以内）に数値の出来事があれば
その出来事に添え、無ければ VLM だけの出来事にする。
"""
import os
import subprocess
from pathlib import Path

import jsonschema
from PIL import Image

from ..io import PipelineError, atomic_json, read_json, sha256
from . import CATEGORIES, video
from .detect import SHORT   # この長さ以下の出来事は、分類が違っても同じ時の指摘として添える

REQUEST_SCHEMA = "inspect-vlm-request/0.1"
RESPONSE_SCHEMA = "inspect-vlm-response/0.1"
VLM_CATEGORIES = CATEGORIES + ("other",)
CATEGORY_TEXT = {
    "shape": "形状: 手足・指・顔・体の形の崩れ、部位の増減や溶け、ありえない曲がり",
    "motion": "動作: 前後のコマと見比べて分かる不自然な動き（急な位置の飛び・ありえない姿勢の変化）",
    "costume": "衣装: 設定画と違う服・色・模様・小物、途中で変わる衣装",
    "continuity": "連続性: 前後のコマとつながらない画面、背景や人物の入れ替わり、明滅、乱れ",
    "other": "その他: 文字化け・透かし・余計な人物など、上の 4 つに入らないもの",
}
INSTRUCTIONS = {
    "ja": ("あなたは生成動画の検品を手伝います。画像は動画のコマで、frames の id・クリップ・コマ番号・秒が付いています。"
           "reference（設定画）がある場合は、人物の形と衣装の色を設定画と比べてください。"
           "見て分かる不自然な点だけを、分類（categories のどれか）・場所（画像の幅と高さを 1 とした [x0, y0, x1, y1]）・短い説明で返してください。"
           "好みや演出の良し悪しは書かないでください。見つからない画像は書かないでください（何も無ければ findings を空にします）。"
           "推測の入る点は、説明にそう書いてください。返答は response_schema に合う JSON だけにしてください。"),
    "en": ("You help inspect generated video. Each image is a frame with an id, clip, frame number and time. "
           "If reference images (character design sheets) are given, compare the character's shape and costume colors with them. "
           "Report only visible anomalies, each with a category (one of categories), a location box [x0, y0, x1, y1] normalized to the image size, "
           "and a short description. Do not judge taste or artistic choices. Skip frames without anomalies (empty findings if none). "
           "Say so when a point is a guess. Reply with JSON matching response_schema only."),
}
RESPONSE_JSON_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "tool", "findings"],
    "properties": {
        "schema_version": {"const": RESPONSE_SCHEMA},
        "tool": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "minLength": 1},
                                                                        "version": {"type": "string"}, "model": {"type": "string"}},
                 "patternProperties": {"^x_": {}}, "additionalProperties": False},
        "request_sha256": {"type": "string"},
        "findings": {"type": "array", "items": {
            "type": "object", "required": ["frame_id", "category", "description"],
            "properties": {"frame_id": {"type": "string"}, "category": {"enum": list(VLM_CATEGORIES)},
                           "description": {"type": "string", "minLength": 1, "maxLength": 800},
                           "box": {"type": ["array", "null"], "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 4, "maxItems": 4}},
            "patternProperties": {"^x_": {}}, "additionalProperties": False}},
    },
    "patternProperties": {"^x_": {}},
    "additionalProperties": False,
}


def frame_id(clip, frame):
    return f"{clip}@{frame}"


def select(clips, events, settings):
    """VLM に見せるコマ [{"id", "clip", "frame", "t", "why"}]。出来事の山のコマを先に、残りは every_s ごとの見本。max_frames まで。"""
    chosen, seen = [], set()

    def add(clip, frame, why):
        key = (clip["id"], frame)
        if key in seen:
            return
        seen.add(key)
        fps = clip["fps"]
        chosen.append({"id": frame_id(clip["id"], frame), "clip": clip["id"], "frame": frame,
                       "t": round(frame / fps, 4) if fps else None, "why": why})

    by_id = {clip["id"]: clip for clip in clips}
    for event in events:
        add(by_id[event["clip"]], event["peak"], f"event:{event['id']}")
    samples = []
    for clip in clips:
        first, last = clip["inspected"]
        step = max(1, int(round(settings["every_s"] * clip["fps"]))) if clip["fps"] else 1
        samples.append([(clip, frame) for frame in range(first, last, step)])
    budget = settings["max_frames"]
    if len(chosen) > budget:
        return chosen[:budget]
    while len(chosen) < budget and any(samples):    # クリップを順に 1 つずつ（多いクリップに偏らない）
        for queue in samples:
            if queue and len(chosen) < budget:
                clip, frame = queue.pop(0)
                add(clip, frame, "sample")
    return sorted(chosen, key=lambda item: (item["clip"], item["frame"]))


def _save_jpeg(image, path, max_side):
    scale = min(1.0, max_side / max(image.shape[:2]))
    picture = Image.fromarray(image)
    if scale < 1.0:
        picture = picture.resize((max(1, int(round(image.shape[1] * scale))), max(1, int(round(image.shape[0] * scale)))), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    picture.save(path, format="JPEG", quality=90)


def write_packet(out, selection, grabbed, references, settings):
    """<out>/vlm/ に request.json・frames/・reference/ を書く。→ (request のパス, request)。

    分類は数値の検査の checks によらず、いつも 4 つと「その他」を示す（VLM だけの検品でも分けられるように）。
    画像ごとの sha256 を依頼に入れるので、素材を同じ名前で差し替えると依頼の sha256 も変わる（古い返答を取り込まない）。"""
    packet = Path(out) / "vlm"
    frames = []
    for item in selection:
        image = grabbed.get(item["clip"], {}).get(item["frame"])
        if image is None:
            continue
        name = f"frames/{item['clip']}_f{item['frame']:06d}.jpg"
        _save_jpeg(image, packet / name, settings["max_side"])
        frames.append({**item, "file": name, "sha256": sha256(packet / name)})    # 依頼の sha256 を画の中身に結び付ける
    refs = []
    for index, entry in enumerate(references):
        name = f"reference/{index:02d}.png"
        image = video.load_rgba(entry["path"])
        scale = min(1.0, settings["max_side"] / max(image.shape[:2]))
        picture = Image.fromarray(image, "RGBA")
        if scale < 1.0:
            picture = picture.resize((max(1, int(round(image.shape[1] * scale))), max(1, int(round(image.shape[0] * scale)))), Image.Resampling.LANCZOS)
        (packet / "reference").mkdir(parents=True, exist_ok=True)
        picture.save(packet / name, format="PNG")
        refs.append({"file": name, "path": entry["rel"], "view": entry.get("view"), "sha256": sha256(packet / name)})
    language = settings.get("language", "ja")
    request = {"schema_version": REQUEST_SCHEMA, "language": language,
               "instructions": INSTRUCTIONS.get(language, INSTRUCTIONS["en"]),
               "categories": {name: CATEGORY_TEXT[name] for name in VLM_CATEGORIES},
               "reference": refs, "frames": frames, "response_schema": RESPONSE_JSON_SCHEMA}
    path = packet / "request.json"
    atomic_json(path, request)
    return path, request


def load_adapter(path):
    """--vlm の設定。{"tool", "version", "argv", "sends_data"} は必須。argv[0] は存在する絶対パス（シェルは使わない）。"""
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"VLM の設定が見つかりません: {path}")
    config = read_json(path)
    if not isinstance(config, dict):
        raise PipelineError("VLM の設定は JSON のオブジェクトにしてください")
    for key in ("tool", "version"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise PipelineError(f"VLM の設定に {key} がありません（docs/inspect.md の VLM 節）")
    argv = config.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
        raise PipelineError("VLM の設定の argv は文字列の配列にしてください")
    executable = Path(argv[0])
    if not executable.is_absolute() or not executable.is_file():
        raise PipelineError(f"VLM の設定の argv[0] は存在する実行ファイルの絶対パスにしてください: {argv[0]}")
    if not isinstance(config.get("sends_data"), bool):
        raise PipelineError("VLM の設定に sends_data（素材を外部へ送るなら true、手元で動くなら false）を書いてください")
    timeout = config.get("timeout_s", 900)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not timeout > 0:
        raise PipelineError(f"VLM の設定の timeout_s は正の秒にしてください: {timeout!r}")
    return {**config, "path": str(path.resolve()), "sha256": sha256(path), "timeout_s": float(timeout)}


def run_adapter(adapter, request_path, output_path, log_path, allow_send):
    """設定の argv を実行する（{request}・{output}・{packet} を置き換える）。sends_data なのに allow_send が無ければ実行しない。"""
    if adapter["sends_data"] and not allow_send:
        raise PipelineError(f"VLM の設定 {Path(adapter['path']).name} は素材（コマと設定画）を外部へ送ります（sends_data: true）。"
                            "送ってよいときだけ --allow-send を付けてください。送らずに確かめるなら --vlm-packet で依頼の束だけを書けます")
    values = {"{request}": str(request_path), "{output}": str(output_path), "{packet}": str(Path(request_path).parent)}
    args = []
    for part in adapter["argv"]:
        for key, value in values.items():
            part = part.replace(key, value)
        args.append(part)
    Path(output_path).unlink(missing_ok=True)      # 古い返答を新しい結果として読まない
    env = {**os.environ}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        result = subprocess.run(args, capture_output=True, env=env, text=True, encoding="utf-8", errors="replace",
                                timeout=adapter["timeout_s"], shell=False)
    except subprocess.TimeoutExpired as error:
        text = lambda value: value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")  # noqa: E731  POSIX では bytes
        Path(log_path).write_text(f"timeout after {adapter['timeout_s']} s\n{text(error.stdout)}{text(error.stderr)}", encoding="utf-8")
        raise PipelineError(f"VLM のプログラムが {adapter['timeout_s']:g} 秒で終わりませんでした（ログ: {log_path}）") from error
    Path(log_path).write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise PipelineError(f"VLM のプログラムが終了コード {result.returncode} で失敗しました（ログ: {log_path}）: {result.stderr[-600:]}")
    if not Path(output_path).is_file():
        raise PipelineError(f"VLM のプログラムが返答 {output_path} を書きませんでした（ログ: {log_path}）")


def read_response(path, request, request_sha):
    """返答を読んで確かめる。→ (返答, [警告])。依頼に無いコマの id は止める。"""
    response = read_json(path)
    errors = sorted(jsonschema.Draft202012Validator(RESPONSE_JSON_SCHEMA).iter_errors(response), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        raise PipelineError("VLM の返答の形が違います:\n" + "\n".join(lines))
    known = {item["id"] for item in request["frames"]}
    unknown = sorted({f["frame_id"] for f in response["findings"]} - known)
    if unknown:
        raise PipelineError(f"VLM の返答に、依頼に無いコマの id があります: {unknown[:5]}")
    warnings = []
    if "request_sha256" not in response:
        warnings.append("返答に request_sha256 が無く、どの依頼への返答かを確かめていません")
    elif response["request_sha256"] != request_sha:
        raise PipelineError("VLM の返答の request_sha256 が、今回の依頼（vlm/request.json）と違います。古い依頼への返答の可能性があります")
    for finding in response["findings"]:
        box = finding.get("box")
        if box and not (box[0] < box[2] and box[1] < box[3]):
            raise PipelineError(f"VLM の返答の box は [x0, y0, x1, y1] で x0 < x1・y0 < y1 にしてください: {box}")
    unreviewed = response.get("x_unreviewed")
    if isinstance(unreviewed, list) and unreviewed:      # 拒否・打ち切り・失敗で見られなかったコマ（指摘が無いのとは違う）
        reasons = sorted({str(item.get("reason")) for item in unreviewed if isinstance(item, dict)})
        warnings.append(f"VLM が見られなかったコマが {len(unreviewed)} 枚あります（{'・'.join(reasons)[:300]}）。指摘が無いこととは違います")
    return response, warnings


def _hosts(event, item, category):
    """VLM の指摘を添えてよい出来事か: 同じクリップで、指摘のコマが区間か前後 1 コマにあり、重複コマの周期ではなく、
    短い（SHORT コマ以下）か分類が同じもの。長い出来事に分類の違う一点の指摘を混ぜると、その指摘の位置と手当てが埋もれるため。"""
    a, b = event["frames"]
    if event["clip"] != item["clip"] or not a - 1 <= item["frame"] <= b:
        return False
    if any(s["check"] == "duplicate_cadence" for s in event["signals"]):
        return False
    return b - a <= SHORT or category in event["categories"]


def attach(events, response, request, clips):
    """返答の指摘を出来事に添える（添えてよい出来事が無ければ VLM だけの出来事を作る。_hosts）。→ 新しい出来事の列。

    添えた出来事の区間は、指摘のコマを含むように広げる（秒・直す場所・手当ての候補は呼んだ側で作り直す）。"""
    frames = {item["id"]: item for item in request["frames"]}
    fps = {clip["id"]: clip["fps"] for clip in clips}
    tool = response["tool"]
    added = []
    for finding in response["findings"]:
        item = frames[finding["frame_id"]]
        signal = {"check": "vlm", "category": finding["category"], "label": "VLM の指摘", "kind": "point",
                  "frames": [item["frame"], item["frame"] + 1], "peak": item["frame"], "value": None, "baseline": None, "threshold": None,
                  "unit": None, "z": None, "region": finding.get("box"), "detail": finding["description"],
                  "tool": tool.get("name"), "model": tool.get("model"), "frame_id": finding["frame_id"]}
        host = next((e for e in events + added if _hosts(e, item, finding["category"])), None)
        if host is not None:
            host["signals"].append(signal)
            if finding["category"] not in host["categories"]:
                host["categories"].append(finding["category"])
            if host["evidence"] == "measured":
                host["evidence"] = "measured+estimated"
            # 隣のコマの指摘でも直す場所から漏らさない（区間を広げる。直す場所と手当ては run が作り直す）
            host["frames"] = [min(host["frames"][0], item["frame"]), max(host["frames"][1], item["frame"] + 1)]
            continue
        rate = fps.get(item["clip"])
        added.append({"clip": item["clip"], "frames": list(signal["frames"]), "peak": item["frame"],
                      "t": [round(item["frame"] / rate, 4), round((item["frame"] + 1) / rate, 4)] if rate else None,
                      "category": finding["category"], "categories": [finding["category"]], "label": "VLM の指摘",
                      "summary": finding["description"], "region": finding.get("box"), "evidence": "estimated", "status": "review",
                      "signals": [signal]})
    return added


def describe_selection(selection):
    """報告用: コマの数と、出来事の山・見本の内訳。"""
    events = sum(1 for item in selection if item["why"].startswith("event:"))
    return {"frames": len(selection), "event_peaks": events, "samples": len(selection) - events}
