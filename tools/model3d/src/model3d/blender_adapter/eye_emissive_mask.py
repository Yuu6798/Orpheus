"""MToon の色アトラスから瞳孔を保護した発光 PNG を作って詰め込む。"""

import argparse
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

import numpy as np

if __package__:
    from .apply_texture import pack_verified, sha256
    from .vrm_materials_mtoon import mtoon_of
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from apply_texture import pack_verified, sha256
    from vrm_materials_mtoon import mtoon_of


def eye_mask(pixels, settings):
    """上から下の sRGB RGBA を受け、同じ並びのグレーの発光マスクを返す。"""
    h, w = pixels.shape[:2]
    x0, y0, x1, y1 = settings["iris_box"]
    x = (np.arange(w) + 0.5) / w
    y = (np.arange(h) + 0.5) / h
    iris = ((y0 <= y) & (y < y1))[:, None] & ((x0 <= x) & (x < x1))[None, :]
    if not iris.any():
        raise ValueError("iris_box に入る画素がありません")
    luma = pixels[..., :3] @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    low, high = settings["luminance"]
    ramp = np.clip((luma - low) / (high - low), 0, 1)
    value = np.where(iris, ramp * ramp * (3 - 2 * ramp), settings["sclera"] * luma)
    dark = luma <= low
    mask = np.where(dark, 0, value * pixels[..., 3])
    return mask, {"iris_pixels": int(iris.sum()), "protected_iris_pixels": int((iris & dark).sum()),
                  "iris_mask_max": float(mask[iris].max()), "sclera_mask_max": float(mask[~iris].max(initial=0))}


def _pixels(image):
    width, height = image.size
    if (image.source not in {"FILE", "GENERATED"} or image.colorspace_settings.name != "sRGB"
            or image.is_float or not width or not height or len(image.pixels) != width * height * 4):
        raise ValueError(f"色の画像 {image.name!r} は画素の読める単一の sRGB 画像にしてください")
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape(height, width, 4)[::-1]  # Blender の画素は左下から。


def _save_mask(bpy, mask, path):
    height, width = mask.shape
    staging = bpy.data.images.new("_EyeEmissiveMask", width=width, height=height, alpha=True, float_buffer=False)
    pixels = np.ones((height, width, 4), dtype=np.float32)
    pixels[..., :3] = mask[::-1, :, None]
    staging.pixels.foreach_set(pixels.ravel())
    try:
        staging.filepath_raw = str(path)
        staging.file_format = "PNG"
        staging.save()
    finally:
        bpy.data.images.remove(staging)


def _bind(material, mtoon, base, image, factor):
    emission = mtoon.emissive_texture
    texture = emission.index
    tree = material.node_tree
    node = tree.nodes.get(texture.get_image_texture_node_name()) if tree is not None else None
    if node is None or node.type != "TEX_IMAGE":
        raise ValueError(f"{material.name!r} に MToon の発光画像ノードがありません")
    texture.source = image
    node.image = image
    mtoon.emissive_factor = factor
    for key in ("mag_filter", "min_filter", "wrap_s", "wrap_t"):
        setattr(texture.sampler, key, getattr(base.index.sampler, key))
    for key in ("offset", "scale"):
        setattr(emission.extensions.khr_texture_transform, key, tuple(getattr(base.extensions.khr_texture_transform, key)))
    if texture.source != image or texture.get_connected_node_image() != image:
        raise ValueError("MToon とシェーダーの発光画像に同じマスクを設定できませんでした")
    return node.name


def main(argv=None):
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    for argument in ("blend", "settings", "out-blend", "report"):
        parser.add_argument(f"--{argument}", type=Path, required=True)
    parser.add_argument("--mask", type=Path)
    args = parser.parse_args(argv)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()), use_scripts=False)
    material = bpy.data.materials.get(settings["material"])
    if material is None:
        raise ValueError(f"材質がありません: {settings['material']!r}")
    mtoon = mtoon_of(material)
    if not mtoon.enabled:
        raise ValueError(f"{material.name!r} の MToon を有効にしてください")
    base = mtoon.pbr_metallic_roughness.base_color_texture
    source = base.index.get_connected_node_image()
    if source is None:
        raise ValueError(f"{material.name!r} に MToon の色の画像が必要です")
    mask, values = eye_mask(_pixels(source), settings)
    image_name = args.mask.name if args.mask else "EyeEmissiveMask.png"
    with TemporaryDirectory() as temp:
        path = Path(temp) / image_name
        _save_mask(bpy, mask, path)
        digest = sha256(path)
        image = bpy.data.images.load(str(path), check_existing=False)
        pack_verified(image, digest)
        node_name = _bind(material, mtoon, base, image, settings["emissive_factor"])
        image.filepath = f"//{image_name}"
        bpy.ops.wm.save_as_mainfile(filepath=str(args.out_blend.resolve()), copy=True)
        if args.mask:
            shutil.copyfile(path, args.mask)
    report = {"schema_version": "model3d-eye-emissive-mask/0.1", "settings": settings, "source_image": source.name,
              "emissive_image": image.name, "shader_node": node_name, "size": list(image.size), "packed_sha256": digest, **values}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
