"""`mv layer render --renderer` の代役。Remotion を呼ばず、props の duration_frames・寸法で透過の PNG 連番を書き、最後の行に JSON を出す。

    python tests/data/layer_standin.py --entry E --composition ID --props P --out DIR [--public DIR] [--concurrency N]

環境変数 LAYER_STANDIN_NOISE=1 なら、コマごとに乱数の画素を入れる（決定論の確認が「一致しない」になる代役）。
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
props = json.loads(Path(args["--props"]).read_text(encoding="utf-8"))
out = Path(args["--out"])
out.mkdir(parents=True, exist_ok=True)
width, height, count = int(props["width"]), int(props["height"]), int(props["duration_frames"])
rng = np.random.default_rng()
for index in range(count):
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[height // 4:height // 2, width // 4:width // 4 + 2 + index] = (255, 255, 255, 220)
    if os.environ.get("LAYER_STANDIN_NOISE") == "1":
        rgba[0, 0] = (*rng.integers(0, 255, 3), 255)
    Image.fromarray(rgba, "RGBA").save(out / f"element-{index:02d}.png")
print("standin render log line")
print(json.dumps({"frames": count, "duration_frames": count, "fps": props["fps"], "width": width, "height": height,
                  "composition": args["--composition"], "remotion_version": "standin", "browser": {"status": "no-browser", "path": None}}))
