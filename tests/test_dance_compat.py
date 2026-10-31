"""0.1 のデモ spec が、0.2 の実装でも変更前と全フレームがバイト単位で同じであること。

正解は実装を変える前に記録した全フレームの SHA-256（tests/data/dance_01_frames.json。素材は持たず、ハッシュだけ）。
素材の mp4 の符号化と線形の浮動小数の演算は環境で変わるので、記録は環境ごとに持ち、素材のハッシュも一致したときだけ比べる。
"""
import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import PIL
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import compose, demo  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import timeline  # noqa: E402
from mv_pipeline.io import sha256  # noqa: E402

RECORD = Path(__file__).parent / "data" / "dance_01_frames.json"


def environment():
    return f"{platform.system()}-{platform.machine()}-cv2{cv2.__version__}-np{np.__version__}-pil{PIL.__version__}"


def test_the_01_demo_keeps_every_frame_byte_for_byte(tmp_path):
    demo.create(tmp_path, schema="0.1")
    assets = {name: sha256(tmp_path / name) for name in ("stage.png", "clip_a.mp4", "clip_b.mp4")}
    ctx = compose.prepare(dancespec.load_spec(tmp_path / "spec.json"))
    try:
        frames = [hashlib.sha256(compose.frame(ctx, timeline.frame_time(i, ctx.fps)).tobytes()).hexdigest()
                  for i in range(timeline.frame_count(ctx.duration, ctx.fps))]
    finally:
        ctx.close()
    record = json.loads(RECORD.read_text(encoding="utf-8")).get(environment())
    if record is None or record["assets"] != assets:
        pytest.skip(f"記録の無い環境です: {environment()}")
    assert frames == record["frames"]
