"""fx-shot の `overlays`: fx の描画の中で重ねる透過レイヤー（歌詞の連番・題字のカード。FX-LYRIC-1）。

    entries = normalize(raw_overlays)        # spec の正規化（spec.py が呼ぶ）。各要素は fx_overlay_layers.schema.json の layer と同じ形
    records = describe(sp)                   # 使う素材の sha256（run の key と manifest 用）
    layers = prepare(sp, size)               # 描画サイズごとに 1 回
    visible = at(layers, t)                  # 時刻 t に見えるもの [(rgba, 設定, 不透明度)]

時間はショット時刻。連番のコマは grain と同じ丸め（いちばん近いコマ、半コマは切り上げ）で、n = round(t·fps)。
  - first_frame の無い連番は、ショット全体のコマ数（round(duration_s·fps)）とちょうど同じ枚数で、n 枚目を使う。
  - first_frame のある連番は、番号 first_frame + n のファイル（ファイル名の最後の数字）を使う。無いコマは missing に従う。
  - 1 枚の画像（image）は start_s ≤ t < end_s の間だけ。出入りは smoothstep。
大きさは spec の出力（output.width × height）と同じであること（resize: true なら縦横比 1% 以内で縮尺する）。`--scale` の下見では一緒に縮める。
動画のレイヤーは spec では受けない（`mv overlay` で重ねる）。
"""
from pathlib import Path

from PIL import Image

from ..io import PipelineError, sha256
from . import layering, timeline

FRAME_SUFFIXES = (".png", ".webp", ".tif", ".tiff")


def normalize(items):
    """spec の overlays（生）を検証して正規化する。各要素 {kind, source, first_frame, missing, alpha, resize, opacity, style, (時刻), x_…}。"""
    if not items:
        return []
    layering.validate_entries(items, "overlays")
    out = []
    for index, item in enumerate(items):
        options = layering.entry_options(item, f"overlays[{index}]")
        entry = {"kind": "image" if "image" in item else "frames", "source": item.get("path") or item["image"],
                 **{k: options[k] for k in ("first_frame", "missing", "alpha", "resize", "resize_space", "opacity", "style")}}
        if entry["kind"] == "image":
            entry.update({k: options[k] for k in layering.TIMED})
        entry.update({k: v for k, v in item.items() if k.startswith("x_")})
        out.append(entry)
    return out


def _natural(path):
    import re
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", Path(path).name)]


def _frames_of(folder, entry, total):
    """連番の [(コマの番号 n, パス | None)]（n = 0..total-1）。first_frame の無い連番は total とちょうど同じ枚数であること。"""
    if not folder.is_dir():
        raise PipelineError(f"overlays の連番のフォルダが見つかりません: {entry['source']}"
                            + ("（spec の overlays は連番のフォルダか 1 枚の画像。動画は mv overlay で重ねてください）" if folder.is_file() else ""))
    files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in FRAME_SUFFIXES), key=_natural)
    if not files:
        raise PipelineError(f"overlays の連番のフォルダに PNG などがありません: {entry['source']}")
    if entry["first_frame"] is None:
        if len(files) != total:
            raise PipelineError(f"overlays の連番 {entry['source']} は {len(files)} 枚で、ショットの {total} コマと違います"
                                f"（曲全体の番号の連番なら first_frame で区間を指定してください）")
        return list(enumerate(files))
    numbers = {}
    for path in files:
        number = layering.frame_number(path)
        if number in numbers:
            raise PipelineError(f"overlays の連番の番号が重なっています: {numbers[number].name} と {path.name}（{number}）")
        numbers[number] = path
    first = entry["first_frame"]
    pairs = [(n, numbers.get(first + n)) for n in range(total)]
    missing = [first + n for n, path in pairs if path is None]
    if missing and entry["missing"] == "error":
        raise PipelineError(f"overlays の連番 {entry['source']} に、番号 {missing[0]}〜{missing[-1]} のうち {len(missing)} コマがありません"
                            f"（first_frame {first}。無いコマを透明にするなら missing: \"transparent\"）")
    return pairs


def _check_size(entry, size, output):
    width, height = output
    if tuple(size) == (width, height):
        return
    if not entry["resize"]:
        raise PipelineError(f"overlays の {entry['source']} は {size[0]}x{size[1]} で、spec の出力 {width}x{height} と違います"
                            f"（縮尺して重ねるなら resize: true）")
    if abs(size[0] * height - width * size[1]) > 0.01 * width * size[1]:
        raise PipelineError(f"overlays の {entry['source']} の縦横比が、spec の出力 {width}x{height} と違います（{size[0]}x{size[1]}）")


def _image_size(path):
    with Image.open(path) as image:
        if not layering.has_alpha(image):
            raise PipelineError(f"overlays の {Path(path).name} にアルファがありません（{image.mode}）。透過の PNG にしてください")
        return image.size


def describe(sp):
    """overlays が使う素材の記録 [{kind, source, size, sha256 | files, missing_frames}]（run の key と manifest 用）。
    連番はショット全体で使うコマの名前と sha256（first_frame なら番号つき）。"""
    data = sp.data
    total = timeline.frame_count(data["duration_s"], data["output"]["fps"])
    output = (data["output"]["width"], data["output"]["height"])
    records = []
    for entry in data["overlays"]:
        path = sp.resolve(entry["source"])
        if entry["kind"] == "image":
            if not path.is_file():
                raise PipelineError(f"overlays の画像が見つかりません: {entry['source']}")
            size = _image_size(path)
            _check_size(entry, size, output)
            records.append({"kind": "image", "source": entry["source"], "size": list(size), "sha256": sha256(path)})
            continue
        pairs = _frames_of(path, entry, total)
        present = [(n, p) for n, p in pairs if p is not None]
        size = _image_size(present[0][1]) if present else output
        _check_size(entry, size, output)
        files = [{"name": p.name, "sha256": sha256(p), **({"number": entry["first_frame"] + n} if entry["first_frame"] is not None else {})}
                 for n, p in present]
        records.append({"kind": "frames", "source": entry["source"], "size": list(size), "files": files,
                        "missing_frames": len(pairs) - len(present)})
    return records


class Layer:
    """描画サイズへ合わせたレイヤー 1 つ。連番は時刻ごとに読む（直前の 1 枚だけ覚えておく）。"""

    def __init__(self, entry, size, paths=None, still=None, fps=24.0, output=None):
        self.entry, self.size, self.paths, self.still, self.fps = entry, tuple(size), paths, still, fps
        self.output = tuple(output) if output else self.size
        self._cached = (None, None)

    def rgba_at(self, t):
        """時刻 t の uint8 RGBA（描画サイズ）。無いコマは None。"""
        if self.still is not None:
            return self.still
        from .look import grain_index
        n = min(max(grain_index(t, self.fps), 0), len(self.paths) - 1)
        path = self.paths[n]
        if path is None:
            return None
        if self._cached[0] != path:
            self._cached = (path, layering.read_rgba(path, self.size, self.entry, f"連番のコマ {path.name}", self.output,
                                                     "spec の出力"))   # 各コマの寸法も確かめる
        return self._cached[1]


def prepare(sp, size):
    """描画サイズ size の Layer の列（spec に overlays が無ければ空）。素材の検査は describe と同じ。"""
    data = sp.data
    if not data["overlays"]:
        return []
    fps = data["output"]["fps"]
    total = timeline.frame_count(data["duration_s"], fps)
    output = (data["output"]["width"], data["output"]["height"])
    layers = []
    for entry in data["overlays"]:
        path = sp.resolve(entry["source"])
        if entry["kind"] == "image":
            if not path.is_file():
                raise PipelineError(f"overlays の画像が見つかりません: {entry['source']}")
            _check_size(entry, _image_size(path), output)
            layers.append(Layer(entry, size, still=layering.read_rgba(path, size, entry), fps=fps))
            continue
        pairs = _frames_of(path, entry, total)
        present = [p for _, p in pairs if p is not None]
        if present:
            _check_size(entry, _image_size(present[0]), output)
        layers.append(Layer(entry, size, paths=[p for _, p in pairs], fps=fps, output=output))
    return layers


def at(layers, t):
    """時刻 t に見えるレイヤー [(rgba, 設定, 不透明度)]（不透明度 0・無いコマ・どこも透明のコマは除く）。"""
    visible = []
    for layer in layers:
        opacity = layering.opacity(layer.entry["kind"], layer.entry, t)
        if opacity <= 0:
            continue
        rgba = layer.rgba_at(t)
        if rgba is None or not rgba[..., 3].any():
            continue
        visible.append((rgba, layer.entry, opacity))
    return visible


def composite(display, visible, scale):
    """表示用の値（float32 の 0..255）に、見えるレイヤーを下から順に重ねる。"""
    for rgba, entry, opacity in visible:
        display = layering.apply_layer(display, rgba, entry, opacity, scale)
    return display


def without(sp):
    """overlays を外した有効 spec（`--no-overlays`）。正規化 spec が変わるのでキャッシュ key が分かれる。"""
    from dataclasses import replace
    return replace(sp, data={**sp.data, "overlays": []})

