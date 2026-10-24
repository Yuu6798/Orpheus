"""fx のテスト用の合成素材と spec（numpy と Pillow だけで作る。素材は一切コミットしない）。"""
import copy
import json

import numpy as np
from PIL import Image, ImageDraw


def make_scene(directory, width=160, height=90):
    """暗い夜景: 空の勾配・地面・人物の台形シルエット。深度（下ほど手前）と人物/地面マスクも書く。"""
    sky = np.linspace(0, 1, height)[:, None]
    base = np.zeros((height, width, 3), dtype=np.float64)
    base[..., 0] = 12 + 18 * sky
    base[..., 1] = 16 + 24 * sky
    base[..., 2] = 34 + 30 * sky
    horizon = int(height * 0.62)
    base[horizon:] = (24, 22, 26)
    image = Image.fromarray(base.astype(np.uint8))
    draw = ImageDraw.Draw(image)
    cx = int(width * 0.4)
    top, bottom = int(height * 0.25), int(height * 0.8)
    draw.polygon([(cx - width // 14, top), (cx + width // 14, top), (cx + width // 9, bottom), (cx - width // 9, bottom)],
                 fill=(52, 48, 58))
    image.save(directory / "base.png")
    subject = Image.new("L", (width, height), 0)
    ImageDraw.Draw(subject).polygon(
        [(cx - width // 14, top), (cx + width // 14, top), (cx + width // 9, bottom), (cx - width // 9, bottom)], fill=255)
    subject.save(directory / "mask_subject.png")
    ground = Image.new("L", (width, height), 0)
    ImageDraw.Draw(ground).rectangle((0, horizon, width, height), fill=255)
    ground.save(directory / "mask_ground.png")
    depth = np.tile(np.linspace(0, 65535, height)[:, None], (1, width)).astype(np.uint16)
    Image.fromarray(depth).save(directory / "depth.png")
    return directory / "spec.json"


FULL = {
    "schema_version": "fx-shot/0.1",
    "output": {"width": 160, "height": 90, "fps": 12, "crf": 23},
    "duration_s": 2.0,
    "seed": 7,
    "assets": {
        "base": "base.png",
        "depth": {"path": "depth.png", "near": "white"},
        "masks": {"subject": "mask_subject.png", "ground": {"path": "mask_ground.png", "feather_px": 2}},
    },
    "camera": {
        "keys": [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5]},
                 {"t": 2.0, "zoom": 1.08, "center": [0.52, 0.48], "ease": "in_out"}],
        "parallax_px": 6, "parallax_dir": [1, 0],
    },
    "layers": {
        "background": {"parallax": 0.4, "inpaint_px": 3},
        "planes": [{"id": "subject", "mask": "subject", "z": 2, "parallax": 1.0}],
        "effects": [
            {"id": "fog", "type": "fog", "z": 1, "color": [170, 175, 200], "opacity": 0.35, "scale": 0.25,
             "drift": [0.01, 0.0], "parallax": 0.6},
            {"id": "embers", "type": "particles", "z": 3, "count": 40, "color": [255, 170, 80],
             "size_px": [1.5, 4.0], "velocity": [0.0, -0.06], "flicker": 0.4, "parallax": 1.3},
        ],
    },
    "lights": [{
        "id": "flame", "type": "point", "position": [0.64, 0.42], "color": [230, 236, 255], "radius": 0.45,
        "intensity": [{"t": 0.0, "v": 0.0}, {"t": 0.5, "v": 1.2}, {"t": 2.0, "v": 0.9}],
        "flicker": {"amount": 0.12, "hz": 5.0},
        "emitter": {"size": 0.03, "glow": 0.6},
        "receivers": [{"mask": "subject", "gain": 1.0, "rim": 0.5}, {"mask": "ground", "gain": 0.6, "reflection": 0.5}],
        "depth": 0.7, "depth_falloff": 0.25,
    }],
}


def spec_dict(**changes):
    """FULL の深いコピーに、トップレベルの鍵を差し替えて返す。値が None の鍵は取り除く。"""
    data = copy.deepcopy(FULL)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def write_spec(directory, data):
    path = directory / "spec.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_project(directory, width=160, height=90, **changes):
    """素材と spec.json を directory に作り、spec のパスを返す。"""
    make_scene(directory, width, height)
    data = spec_dict(**changes)
    data["output"]["width"], data["output"]["height"] = width, height
    return write_spec(directory, data)


def run_cli(*argv):
    """`mv ...` を同じプロセスで実行し、(終了コード, stdout の JSON または None, stderr の文字列) を返す。"""
    import contextlib
    import io

    from mv_pipeline import cli

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None), err.getvalue()
