"""`mv inspect check --vlm` から呼ぶ Claude のアダプタ（例。**コマと設定画を Anthropic の API へ送る**）。

依頼の束（request.json と画像）を読み、設定画と動画のコマを Claude（Messages API の画像入力）に見せて、
不自然な所の指摘を inspect-vlm-response/0.1 の JSON で書く。返答の形は構造化出力（output_config.format）で縛る。

- **送信する**: 設定（--vlm の JSON）には必ず `"sends_data": true` を書く。Orpheus は `--allow-send` が無いと、このプログラムを呼ばない。
- **Orpheus の依存ではない**: anthropic の SDK を入れた別の Python で動かす（`pip install anthropic`）。鍵は ANTHROPIC_API_KEY など
  SDK の既定の探し方で読む。鍵をファイルや設定に書かない。
- **費用がかかる**: 1 回の依頼で、設定画とコマ（既定で最大 24 枚）を batch_size 枚ずつ送る。設定画は毎回送るが、キャッシュに載せる。
- **推定**: 返答は Claude の見立てで、Orpheus は推定（estimated）として扱い、合否に使わない。
- 拒否（stop_reason: refusal）・出力の打ち切り・API の失敗で見られなかったコマは、空の指摘にせず x_unreviewed に理由つきで残す。
  1 枚も見られなければ終了コード 1。
- **未検証**: このリポジトリの開発環境では API を呼んでいない（鍵が無く、送信もしない）。偽の client で依頼の組み立てと返答の整え方だけを
  テストした（tests/test_inspect.py）。

    python scripts/vlm_claude.py --request REQ --output OUT [--model ID] [--effort high] [--batch-size 6] [--dry-run]

--model を書かなければ DEFAULT_MODEL（下の定数）を使う。

設定の例（パスは置き換える）:
    {"tool": "claude", "version": "anthropic SDK の版", "sends_data": true, "timeout_s": 1800,
     "argv": ["/abs/venv/bin/python", "/abs/Orpheus/scripts/vlm_claude.py", "--request", "{request}", "--output", "{output}"]}
"""
import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
CATEGORIES = ("shape", "motion", "costume", "continuity", "other")


def api_schema(frame_ids):
    """構造化出力に渡す返答の形（API が受け付けない数や長さの制約は書かず、box はこちらで確かめる）。"""
    return {
        "type": "object",
        "properties": {"findings": {"type": "array", "items": {
            "type": "object",
            "properties": {"frame_id": {"type": "string", "enum": list(frame_ids)},
                           "category": {"type": "string", "enum": list(CATEGORIES)},
                           "description": {"type": "string"},
                           "box": {"anyOf": [{"type": "array", "items": {"type": "number"}}, {"type": "null"}]}},
            "required": ["frame_id", "category", "description", "box"],
            "additionalProperties": False}}},
        "required": ["findings"],
        "additionalProperties": False,
    }


def _image(path):
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


NOTES = {
    "ja": ("分類（category）", "box は画像の幅と高さを 1 とした [x0, y0, x1, y1]。場所が言えないときは null。説明は日本語で短く。見た目の事実と推測を分けて書く。"),
    "en": ("Categories", "box is [x0, y0, x1, y1] normalized to the image width and height, or null when no location can be given. "
                         "Keep descriptions short and in English, and separate what is visible from what is guessed."),
}


def system_prompt(request):
    heading, note = NOTES.get(request.get("language"), NOTES["en"])
    categories = "\n".join(f"- {name}: {text}" for name, text in request["categories"].items())
    return f"{request['instructions']}\n\n{heading}:\n{categories}\n\n{note}"


def batches(request, packet, size):
    """[(コマの id の列, user の content)]。設定画を先に置いてキャッシュの印を付け（全バッチで同じ前置き）、続けてコマを並べる。"""
    frames = request["frames"]
    head = []
    for index, ref in enumerate(request["reference"]):
        head.append({"type": "text", "text": f"設定画 {index + 1}（{ref.get('view') or '向き不明'}・{ref['path']}）:"})
        head.append(_image(packet / ref["file"]))
    if head:
        head[-1] = {**head[-1], "cache_control": {"type": "ephemeral"}}
    out = []
    for start in range(0, len(frames), size):
        chunk = frames[start:start + size]
        content = list(head)
        for item in chunk:
            seconds = f"{item['t']:.3f} 秒" if item.get("t") is not None else "静止画"
            content.append({"type": "text", "text": f"frame_id: {item['id']}（クリップ {item['clip']}・コマ {item['frame']}・{seconds}）"})
            content.append(_image(packet / item["file"]))
        content.append({"type": "text", "text": "上のコマのうち、不自然な所があるものだけを findings に書いてください。無ければ空の配列。"})
        out.append(([item["id"] for item in chunk], content))
    return out


def clean_box(box):
    """[x0, y0, x1, y1] を 0..1 に収め、幅か高さが無ければ None。"""
    if not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box):
        return None
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in box)
    return [x0, y0, x1, y1] if x0 < x1 and y0 < y1 else None


def normalize(findings, frame_ids):
    """返答の指摘を確かめて整える（依頼に無い id・知らない分類・空の説明は捨てる）。"""
    out = []
    for item in findings:
        if not isinstance(item, dict) or item.get("frame_id") not in frame_ids or item.get("category") not in CATEGORIES:
            continue
        text = str(item.get("description") or "").strip()[:800]
        if text:
            out.append({"frame_id": item["frame_id"], "category": item["category"], "description": text, "box": clean_box(item.get("box"))})
    return out


def _text(message):
    return next((block.text for block in message.content if getattr(block, "type", None) == "text"), None)


def _api_errors():
    """(状態の誤り, 接続の誤り) の例外の型。SDK が無い環境（テスト）では何にも当たらない型を返す。"""
    try:
        import anthropic
    except ModuleNotFoundError:
        class Never(Exception):
            pass
        return Never, Never
    return anthropic.APIStatusError, anthropic.APIConnectionError


def review(client, request, packet, model, effort, size, say=print, errors=None):
    """全バッチを送って (指摘, 見られなかったコマ, バッチの記録, 応えたモデル) を返す。client は anthropic.Anthropic と同じ形。"""
    status_error, connection_error = errors or _api_errors()
    findings, unreviewed, log, served = [], [], [], None
    system = system_prompt(request)
    for number, (ids, content) in enumerate(batches(request, packet, size), start=1):
        record = {"batch": number, "frames": ids}
        try:
            message = client.beta.messages.create(
                model=model, max_tokens=MAX_TOKENS, betas=[FALLBACK_BETA], fallbacks="default",
                system=system, messages=[{"role": "user", "content": content}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": api_schema(ids)}})
        except status_error as error:
            record.update(error=f"HTTP {getattr(error, 'status_code', '?')}: {getattr(error, 'message', error)}"[:400])
        except connection_error as error:
            record.update(error=f"接続できません: {error}"[:400])
        else:
            served = message.model
            record.update(model=message.model, stop_reason=message.stop_reason, request_id=getattr(message, "_request_id", None))
            text = _text(message)
            if message.stop_reason == "refusal":
                details = getattr(message, "stop_details", None)
                record.update(error=f"拒否されました（{getattr(details, 'category', None)}）")
            elif message.stop_reason == "max_tokens":
                record.update(error="出力が max_tokens で打ち切られました")
            elif text is None:
                record.update(error="返答に文章がありません")
            else:
                try:
                    findings += normalize(json.loads(text).get("findings", []), set(ids))
                except (ValueError, AttributeError) as error:
                    record.update(error=f"返答の JSON を読めません: {error}"[:400])
        if record.get("error"):
            unreviewed += [{"frame_id": frame, "reason": record["error"]} for frame in ids]
        say(f"[vlm_claude] バッチ {number}: {len(ids)} 枚 {record.get('error') or 'ok'}")
        log.append(record)
    return findings, unreviewed, log, served


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude で生成動画のコマを見て、inspect-vlm-response/0.1 を書く（送信する）")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="送らずに、バッチの数と画像の枚数だけを表示する")
    args = parser.parse_args(argv)
    raw = args.request.read_bytes()
    request = json.loads(raw.decode("utf-8"))
    packet = args.request.parent
    if args.batch_size < 1:
        parser.error("--batch-size は 1 以上にしてください")
    planned = batches(request, packet, args.batch_size)
    if args.dry_run:
        print(json.dumps({"batches": len(planned), "frames": len(request["frames"]), "references": len(request["reference"]),
                          "model": args.model, "effort": args.effort}, ensure_ascii=False))
        return 0
    try:
        import anthropic
    except ModuleNotFoundError:
        print("anthropic の SDK がありません。このプログラムを動かす Python に `pip install anthropic` してください", file=sys.stderr)
        return 2
    client = anthropic.Anthropic(max_retries=4)
    say = lambda message: print(message, file=sys.stderr)  # noqa: E731
    findings, unreviewed, log, served = review(client, request, packet, args.model, args.effort, args.batch_size, say)
    response = {"schema_version": "inspect-vlm-response/0.1",
                "tool": {"name": "claude", "version": f"anthropic {anthropic.__version__}", "model": served or args.model},
                "request_sha256": hashlib.sha256(raw).hexdigest(), "findings": findings,
                "x_unreviewed": unreviewed, "x_batches": log, "x_requested_model": args.model, "x_effort": args.effort}
    args.output.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if len(unreviewed) == len(request["frames"]) and request["frames"]:
        print("どのコマも見られませんでした（x_batches の error を見てください）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
