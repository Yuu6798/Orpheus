"""固定した公開 TripoSR の重み（MIT）を取得し、sha256 を確かめてから置く。違えば採用しない。

    python fetch_triposr_weights.py WEIGHTS_DIR     # WEIGHTS_DIR/model.ckpt（約 1.68 GB）。config.yaml は別途置く

ネットワークを使う唯一の script。Orpheus の CI では動かさない。
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

REVISION = "5b521936b01fbe1890f6f9baed0254ab6351c04a"
URL = f"https://huggingface.co/stabilityai/TripoSR/resolve/{REVISION}/model.ckpt"
EXPECTED = "429e2c6b22a0923967459de24d67f05962b235f79cde6b032aa7ed2ffcd970ee"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("weights_dir", type=Path)
    args = parser.parse_args(argv)
    target = args.weights_dir / "model.ckpt"
    if target.is_file() and sha256(target) == EXPECTED:
        print("pinned checkpoint already verified")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".download")
    with urllib.request.urlopen(URL, timeout=180) as response, partial.open("wb") as out:
        while block := response.read(4 * 1024 * 1024):
            out.write(block)
    actual = sha256(partial)
    if actual != EXPECTED:
        partial.unlink(missing_ok=True)
        print(f"checkpoint sha256 mismatch: {actual}; not adopted", file=sys.stderr)
        return 1
    partial.replace(target)
    print("pinned checkpoint downloaded and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
