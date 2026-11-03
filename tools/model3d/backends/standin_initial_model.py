"""生成器の代役（stand-in）。設定画の前処理済み PNG から、前景の輪郭の箱を頂点色付きの OBJ に書く。

生成ではない。接続（前処理 → 別プロセス → OBJ → 検査 → 記録）をニューラルネット無しで通すための部品で、
デモ・テスト・CI が使う。本物の生成器は triposr_initial_model.py（別の仮想環境で動かす）。

    python standin_initial_model.py PREPARED.png OUT_DIR [--resolution N] [--threshold T] [--depth-ratio 0.4]

OUT_DIR に initial_model.obj（v x y z r g b）と result.json を書く。座標は背の高さを 1 にした正規化の空間。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

BACKGROUND = 127   # prepare_reference の灰色 0.5 は uint8 で 127


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("prepared", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--resolution", type=int, default=128)
    parser.add_argument("--threshold", type=float, default=25.0)
    parser.add_argument("--depth-ratio", type=float, default=0.4)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    with Image.open(args.prepared) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.int64)
    foreground = np.any(rgb != BACKGROUND, axis=2)
    occupied = np.argwhere(foreground)
    if not len(occupied):
        print("no foreground in the prepared image", file=sys.stderr)
        return 2
    top, left = occupied.min(axis=0)
    bottom, right = occupied.max(axis=0)
    height = float(bottom - top + 1)
    width = float(right - left + 1) / height
    depth = width * args.depth_ratio
    color = (rgb[foreground].mean(axis=0) / 255.0).tolist()
    hx, hy, hz = width / 2.0, depth / 2.0, 0.5
    vertices = [[-hx, -hy, -hz], [hx, -hy, -hz], [hx, hy, -hz], [-hx, hy, -hz], [-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz]]
    faces = [[0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    obj = args.out_dir / "initial_model.obj"
    lines = ["# stand-in initial model (box around the foreground silhouette; not a generator)"]
    lines.extend(f"v {x!r} {y!r} {z!r} {color[0]!r} {color[1]!r} {color[2]!r}" for x, y, z in vertices)
    lines.extend("f " + " ".join(str(i + 1) for i in face) for face in faces)
    obj.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = {"backend": "stand-in box from the prepared silhouette (no neural network)", "single_view_only": True,
              "input_sha256": hashlib.sha256(args.prepared.read_bytes()).hexdigest(),
              "foreground_bbox_xyxy": [int(left), int(top), int(right), int(bottom)], "resolution": args.resolution,
              "density_threshold": args.threshold, "vertices": len(vertices), "triangles": len(faces),
              "mean_color": color, "output_files": {"initial_model.obj": {"bytes": obj.stat().st_size,
                                                                          "sha256": hashlib.sha256(obj.read_bytes()).hexdigest()}}}
    (args.out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"obj": str(obj), "vertices": len(vertices), "triangles": len(faces)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
