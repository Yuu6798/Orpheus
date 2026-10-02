"""Optional adapter, run with the Python of an existing qwen-asr environment.

Local weights only. This wrapper has contract tests, but real singing quality
must be reviewed on the user's audio. No network download is initiated.
"""
import argparse
import json
import os
from importlib.metadata import version
from pathlib import Path


def normalized(text):
    return ''.join(c for c in text.casefold() if c.isalnum())


def map_lines(lyrics, tokens):
    output = [{"id": row["id"], "text": row["text"], "start_s": None, "end_s": None,
               "timing_source": "unknown"} for row in lyrics]
    expected = ''.join(normalized(row["alignment_text"]) for row in lyrics)
    actual = ''.join(normalized(token["text"]) for token in tokens)
    if not expected or actual != expected:
        return output  # Do not invent timings when token-to-line mapping is ambiguous.
    spans = []
    cursor = 0
    for token in tokens:
        size = len(normalized(token["text"]))
        if size:
            spans.append((cursor, cursor + size, token))
        cursor += size
    cursor = 0
    for row, result in zip(lyrics, output):
        end = cursor + len(normalized(row["alignment_text"]))
        matches = [token for start, stop, token in spans if start < end and stop > cursor]
        # A token spanning two lines cannot provide the internal boundary.
        exact = any(start == cursor for start, _, _ in spans) and any(stop == end for _, stop, _ in spans)
        if matches and exact and matches[0]["start_s"] < matches[-1]["end_s"]:
            result.update(start_s=matches[0]["start_s"], end_s=matches[-1]["end_s"], timing_source="model")
        cursor = end
    return output


def main():
    p = argparse.ArgumentParser()
    for arg in ['audio', 'analysis', 'output', 'model-dir']:
        p.add_argument('--' + arg, required=True)
    p.add_argument('--offset-s', type=float, required=True)
    p.add_argument('--language', default='Japanese')
    p.add_argument('--device', default='cpu')
    args = p.parse_args()
    if not Path(args.model_dir).is_dir():
        p.error('--model-dir must be a previously downloaded local model directory')
    os.environ['HF_HUB_OFFLINE'] = '1'
    import torch
    from qwen_asr import Qwen3ForcedAligner
    a = json.loads(Path(args.analysis).read_text(encoding='utf-8-sig'))
    model = Qwen3ForcedAligner.from_pretrained(args.model_dir,
        dtype=torch.float32 if args.device == 'cpu' else torch.bfloat16, device_map=args.device)
    results = model.align(audio=args.audio, text='\n'.join(row['alignment_text'] for row in a['lyrics']), language=args.language)
    tokens = [{"text": item.text, "start_s": float(item.start_time), "end_s": float(item.end_time)} for item in results[0]]
    result = {"audio_sha256": a['audio']['sha256'], "offset_s": args.offset_s,
              "tool": {"name": "qwen-asr", "version": version('qwen-asr'), "model": Path(args.model_dir).name},
              "lyrics": map_lines(a['lyrics'], tokens), "raw_tokens": tokens}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
