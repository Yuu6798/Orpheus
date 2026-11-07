"""`mv inspect check --vlm` の代役（テストと手元の確かめ用）。画像を読まず、ネットワークにも出ない。

依頼の束（request.json）を読み、指定した指摘だけを inspect-vlm-response/0.1 の形で返す。標準ライブラリだけで動く。

    python scripts/vlm_standin.py --request REQ --output OUT [--flag FRAME_ID:CATEGORY:説明 ...] [--echo-events CATEGORY]

- --flag: その id のコマに、その分類の指摘を 1 つ返す（例: demo@50:shape:腕の先に塊）。依頼に無い id は止める。
- --echo-events: 数値の出来事の山として依頼に入ったコマ（why が event: で始まる）に、その分類の指摘を返す。
- --unreviewed FRAME_ID: 見られなかったコマとして x_unreviewed に入れる（実物の VLM が拒否・失敗したときの形の確かめ）。

設定（--vlm に渡す JSON）の例:
    {"tool": "vlm-standin", "version": "0.1", "sends_data": false,
     "argv": ["/abs/python", "/abs/Orpheus/scripts/vlm_standin.py", "--request", "{request}", "--output", "{output}", "--echo-events", "other"]}
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

CATEGORIES = ("shape", "motion", "costume", "continuity", "other")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--flag", action="append", default=[], help="FRAME_ID:CATEGORY:TEXT")
    parser.add_argument("--echo-events", choices=CATEGORIES)
    parser.add_argument("--unreviewed", action="append", default=[], help="FRAME_ID")
    args = parser.parse_args(argv)
    raw = args.request.read_bytes()
    request = json.loads(raw.decode("utf-8"))
    frames = {item["id"]: item for item in request["frames"]}
    findings = []
    for flag in args.flag:
        frame, category, text = flag.split(":", 2)
        if frame not in frames:
            print(f"依頼に無いコマの id です: {frame}", file=sys.stderr)
            return 2
        if category not in CATEGORIES:
            print(f"分類は {', '.join(CATEGORIES)} のどれかにしてください: {category}", file=sys.stderr)
            return 2
        findings.append({"frame_id": frame, "category": category, "description": text, "box": None})
    if args.echo_events:
        for item in request["frames"]:
            if item["why"].startswith("event:"):
                findings.append({"frame_id": item["id"], "category": args.echo_events,
                                 "description": f"代役の指摘（{item['why']} のコマ。画像は見ていない）", "box": None})
    response = {"schema_version": "inspect-vlm-response/0.1", "tool": {"name": "vlm-standin", "version": "0.1"},
                "request_sha256": hashlib.sha256(raw).hexdigest(), "findings": findings}
    if args.unreviewed:
        response["x_unreviewed"] = [{"frame_id": frame, "reason": "代役が見ていないことにしたコマ"} for frame in args.unreviewed]
    args.output.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
