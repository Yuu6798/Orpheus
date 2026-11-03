"""設定画（RGBA 1 枚）から初期モデルを作る工程のうち、自作の部分。

- 入力の前処理 `prepare_reference`: 前景の矩形で切り出し、正方形の中央に置き、前景比 foreground_ratio で余白を足し、
  アルファを灰色 0.5 に合成した RGB を作る。固定した TripoSR の入力の約束を独立に書いたもので、上流の関数は呼ばない
  （切り出しが最後の行と列を含まない癖も、互換のためにそのまま残す）。2026-11-03 の検証で、実設定画 1 件と合成 6 件が
  原本の関数と画素単位で一致した（合成 6 件の記録は tests/data/prepare_reference_cases.json で照合している）。
- 生成器は**別プロセス**（tools/model3d/backends/）。ニューラルネットと重みはこのパッケージに入れず、argv で呼んで
  版・重みのハッシュ・ライセンス・実行ファイルのハッシュを記録する。
- 生成物の検査 `inspect_mesh` と記録 `build_record`（model3d-initial-model/0.1）。**生成が終わっただけでは採用しない**:
  記録の review は常に needs_review で、正面・側面・背面を人が見てから、分離・形状修正・リトポロジーへ渡す。
- 座標の実寸は未校正（生成器の正規化した空間）。units の欄は仮で、checks.scale_calibrated が false のまま。
"""
import math
from pathlib import Path

import numpy as np

from . import contracts
from .io import ToolError, now, sha256
from .mesh import Mesh, canonical_signature

from . import PACKAGE_DIR

BACKENDS_DIR = PACKAGE_DIR.parents[1] / "backends"   # tools/model3d/backends（editable で入れた場所）
DEFAULT_FOREGROUND_RATIO = 0.85
BACKGROUND_RGB = (0.5, 0.5, 0.5)
REVIEW_NOTE = "初期モデルは生成が終わっただけでは採用しない。正面・側面・背面を人が見てから、分離・形状修正・リトポロジーへ渡す"


def load_rgba(path):
    """PNG などを RGBA の uint8 配列 (H, W, 4) で読む。"""
    from PIL import Image
    try:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except (OSError, ValueError) as error:
        raise ToolError(f"設定画を読めません: {path}（{error}）") from error


def prepare_reference(pixels, foreground_ratio=DEFAULT_FOREGROUND_RATIO):
    """RGBA (H, W, 4) uint8 から、生成器に渡す RGB (S, S, 3) uint8 と前処理の条件を作る。

    前景（アルファが 0 でない画素）の矩形で切り出し（互換のため最後の行と列は含まない）、正方形の中央に置き、
    foreground_ratio で余白を足し、アルファを灰色 0.5 に合成する。前景が無い・1 行か 1 列しか無いときは止める。
    """
    if isinstance(foreground_ratio, bool) or not isinstance(foreground_ratio, (int, float)) or not 0 < foreground_ratio <= 1:
        raise ToolError(f"foreground_ratio は 0 より大きく 1 以下にしてください: {foreground_ratio!r}")
    pixels = np.asarray(pixels)
    if pixels.ndim != 3 or pixels.shape[2] != 4 or pixels.dtype != np.uint8:
        raise ToolError("設定画は RGBA の uint8 配列（H, W, 4）にしてください")
    occupied = np.argwhere(pixels[:, :, 3] != 0)
    if not len(occupied):
        raise ToolError("設定画に前景（アルファが 0 でない画素）がありません")
    top, left = occupied.min(axis=0)
    bottom, right = occupied.max(axis=0)
    if bottom == top or right == left:
        raise ToolError("前景は 2 行 2 列以上にしてください（1 画素や 1 列だけの前景は受け付けない）")
    crop = pixels[top:bottom, left:right]
    side = int(max(crop.shape[:2]))
    final_side = int(side / foreground_ratio)
    square = np.zeros((side, side, 4), dtype=np.uint8)   # 中央寄せは 2 段階に分ける（整数の丸めが結果に効く）
    y, x = (side - crop.shape[0]) // 2, (side - crop.shape[1]) // 2
    square[y:y + crop.shape[0], x:x + crop.shape[1]] = crop
    canvas = np.zeros((final_side, final_side, 4), dtype=np.uint8)
    pad = (final_side - side) // 2
    canvas[pad:pad + side, pad:pad + side] = square
    normalized = canvas.astype(np.float32) / 255.0
    alpha = normalized[:, :, 3:4]
    rgb = normalized[:, :, :3] * alpha + (1 - alpha) * BACKGROUND_RGB[0]
    metadata = {"crop_xyxy_exclusive_compat": [int(left), int(top), int(right), int(bottom)], "square_side": side,
                "output_side": final_side, "foreground_ratio": float(foreground_ratio), "background_rgb_float": list(BACKGROUND_RGB)}
    return (rgb * 255.0).astype(np.uint8), metadata


def synthetic_sheet(width=240, height=360):
    """合成の設定画（RGBA）。頭の円・胴の四角・帯の横帯を透明な背景に描く。デモとテストの材料で、実キャラの素材ではない。"""
    yy, xx = np.mgrid[0:height, 0:width]
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    head = (xx - width * 0.5) ** 2 + (yy - height * 0.2) ** 2 <= (width * 0.16) ** 2
    body = (np.abs(xx - width * 0.5) <= width * 0.17) & (yy >= height * 0.33) & (yy <= height * 0.86)
    sash = body & (np.abs(yy - height * 0.55) <= height * 0.03)
    pixels[head] = (235, 200, 170, 255)
    pixels[body] = (70, 90, 200, 255)
    pixels[sash] = (200, 40, 60, 255)
    return pixels


def save_rgb(array, path):
    from PIL import Image
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(array, dtype=np.uint8), "RGB").save(path)
    return path


def save_rgba(array, path):
    from PIL import Image
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(array, dtype=np.uint8), "RGBA").save(path)
    return path


def standin_config(python=None):
    """生成器の代役（backends/standin_initial_model.py）を呼ぶ設定。デモとテスト用。"""
    import sys
    from . import VERSION
    script = BACKENDS_DIR / "standin_initial_model.py"
    if not script.is_file():
        raise ToolError(f"代役の生成器が見つかりません: {script}")
    return {"tool": "stand-in", "version": VERSION, "kind": "stand_in", "license": "this repository",
            # resolve() は使わない: venv の python は symlink で、解決すると numpy の無い元の Python になる
            "argv": [str(Path(python or sys.executable).absolute()), str(script), "{prepared}", "{out_dir}", "--resolution", "{resolution}"],
            "outputs": {"obj": "initial_model.obj", "result": "result.json"}}


def prepare_file(image_path, out_path, foreground_ratio=DEFAULT_FOREGROUND_RATIO):
    """設定画のファイルを前処理して PNG に書き、入力と出力のハッシュを含む条件を返す。"""
    image_path = Path(image_path)
    pixels = load_rgba(image_path)
    rgb, metadata = prepare_reference(pixels, foreground_ratio)
    out = save_rgb(rgb, out_path)
    return {**metadata, "input": {"path": str(image_path.resolve()), "sha256": sha256(image_path),
                                  "width": int(pixels.shape[1]), "height": int(pixels.shape[0])},
            "prepared": {"path": str(out.resolve()), "sha256": sha256(out), "bytes": out.stat().st_size}}


def inspect_mesh(mesh):
    """初期モデルの幾何と構造の検査（数値と真偽だけ。見た目の採否は人）。"""
    lo, hi = mesh.bounds()
    edges = mesh.edge_report()
    colors = mesh.extra.get("x_vertex_colors")
    tris, _ = mesh.triangles()
    return {"vertices": mesh.vertex_count, "triangles": int(len(tris)),
            "finite": bool(np.isfinite(mesh.vertices).all()) if mesh.vertex_count else True,
            "bounds": {"min": [float(v) for v in lo], "max": [float(v) for v in hi]},
            "extent": [float(b - a) for a, b in zip(lo, hi)], "components": mesh.components(),
            "closed": bool(edges["closed"]), "winding_consistent": bool(edges["consistent_winding"]),
            "vertex_colors": bool(isinstance(colors, list) and len(colors) == mesh.vertex_count and mesh.vertex_count),
            "scale_calibrated": bool(mesh.extra.get("x_scale_calibrated", False)),
            "topology_sha256": mesh.topology_hash(), "canonical_sha256": canonical_signature(mesh)["sha256"]}


def fileref(path):
    path = Path(path)
    return {"path": str(path.resolve()), "sha256": sha256(path), "bytes": path.stat().st_size}


def build_record(preparation, backend, parameters, outputs, checks, single_view_only=True, notes=()):
    """model3d-initial-model/0.1 の記録。preparation は prepare_file の戻り値。review は常に needs_review。"""
    data = {"schema_version": "model3d-initial-model/0.1", "created_at": now(),
            "input": dict(preparation["input"]),
            "preparation": {k: preparation[k] for k in ("foreground_ratio", "crop_xyxy_exclusive_compat", "square_side",
                                                        "output_side", "background_rgb_float", "prepared")},
            "backend": dict(backend), "parameters": dict(parameters), "outputs": dict(outputs), "checks": dict(checks),
            "review": "needs_review", "single_view_only": bool(single_view_only),
            "notes": [REVIEW_NOTE, *notes]}
    return contracts.validate("initial-model", data)


def backend_identity(config, record=None):
    """生成器の設定（adapters の形）から、記録に残す素性。"""
    identity = {"tool": str(config.get("tool")), "version": str(config.get("version")), "kind": config.get("kind", "single_view")}
    for key in ("license", "weights"):
        if key in config:
            identity[key] = config[key]
    if record:
        identity.update(executable=record.get("executable"), executable_sha256=record.get("executable_sha256"),
                        argv=record.get("argv"), exit_code=record.get("exit_code"), seconds=record.get("seconds"))
    return {k: v for k, v in identity.items() if v is not None}
