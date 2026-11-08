"""透過レイヤーの重ね合わせ（`mv overlay`。実験的。FX-LYRIC-1）。

    mv overlay --base 画像か動画 --layer 連番のフォルダか透過動画 [--layer …] [--audio 音] [--out DIR] [--fps 30] [--crf 18]
    mv overlay --base 画像か動画 --layers layers.json [--frames N] …     # レイヤーごとの設定（fx-overlay-layers/0.1）

layers.json（fx_overlay_layers.schema.json）では、レイヤーごとに次を決められる:
- first_frame / missing: 曲全体の番号で書いた連番から区間を取り出す（出力の 0 コマ目 = 番号 first_frame）。無いコマは止めるか透明。
- alpha（straight / premultiplied）・resize・opacity。
- image + start_s / end_s / fade_in_s / fade_out_s: 1 枚の透過画像を時刻を決めて重ねる（題字のカード。smoothstep で出入り）。
- style: 色づけ（tint × 輝度）・影・光彩・後光。表示用の値（sRGB の 0..255）の float の上で 後光 → 影 → 光彩 → 本体 の順に掛け、
  最後に 1 回だけ四捨五入する（実制作の重ねの式と同じ式・同じ順。長さは出力の短い辺を 1080 としたときの画素）。
  style・opacity・premultiplied・image の無いレイヤーだけのコマは、0.1 と同じ式（レイヤーごとに uint8 へ丸める）でバイト単位で同じ。

土台（1 枚の画像か動画）の上に、透過レイヤー（PNG 連番のフォルダか、アルファつきの動画）を指定した順に重ね、
fx の共通の書き出し（render.write_video。BT.709・tv・yuv420p の式と印）で H.264 にする。ffmpeg の引数は写さない。

- 重ね方: straight α（半透明の画素は光の色、アルファが 0 の画素は色を持たない）を前提に、sRGB の 8 bit の値のまま
  out = layer × α + under × (1 − α) で重ねる（線形化しない。Remotion の PNG 連番と同じ前提）。
- コマ数・fps・寸法が合わないものはエラー（勝手に合わせない）。土台が画像なら --fps が要り、コマ数はレイヤーから決まる。
- run（<out>/<run_id>/）に overlay.mp4・manifest.json・render.log を残し、index.json（台帳）に key → run_id を登録する。
  key は土台・各レイヤーの全コマ・音声の sha256 と設定から作る。同じ入力で成功済みなら再利用する（--force で作り直す）。
  manifest の overlay の欄は fx_overlay.schema.json（fx-overlay/0.2。contracts/ の外）で検証してから書く。
- 最後に probe.check_video（technical_pass）を通す。通らなければ run は failed。

書き出しは fx の render（cv2 を import する）にあるので fx extra が要る。style と resize も cv2 を使う。
"""
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import jsonschema
import numpy as np
from PIL import Image

from .. import media
from ..io import PipelineError, atomic_json, sha256
from . import fingerprint, probe, runs

SCHEMA_VERSION = "fx-overlay/0.2"
SCHEMA_PATH = Path(__file__).with_name("fx_overlay.schema.json")
LAYERS_VERSION = "fx-overlay-layers/0.1"
LAYERS_SCHEMA_PATH = Path(__file__).with_name("fx_overlay_layers.schema.json")
PLAIN = {"first_frame": None, "missing": "error", "alpha": "straight", "resize": False, "opacity": 1.0, "style": None}
STYLE_DEFAULTS = {"shadow": {"amount": 0.45, "sigma_px": 4.0, "gain": 1.5}, "glow": {"amount": 0.9, "sigma_px": 14.0},
                  "backdrop": {"amount": 0.55, "dilate_px": 5, "iterations": 3, "sigma_px": 26.0, "gain": 1.6}}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
FRAME_SUFFIXES = (".png", ".webp", ".tif", ".tiff")
DEFAULT_CRF = 18


# ---- 入力 -----------------------------------------------------------------------

def _is_image(path):
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def natural_key(path):
    """frame2 < frame10 になる並び（数字の桁をそろえていない連番でも順序を間違えない）。"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", Path(path).name)]


def _image_size(path):
    with Image.open(path) as image:
        return image.size


def describe_base(path, fps=None):
    """土台の {kind, path, sha256, size, fps, frames}。動画なら fps とコマ数は動画から、画像なら fps は引数（必須）。"""
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"--base が見つかりません: {path}")
    if _is_image(path):
        if fps is None:
            raise PipelineError("--base が画像のときは --fps を指定してください（コマ数はレイヤーから決まります）")
        width, height = _image_size(path)
        return {"kind": "image", "path": str(path), "sha256": sha256(path), "size": [width, height], "fps": float(fps), "frames": None}
    info = probe.inspect(path)["video"]
    decoded = probe.decode(path, "v")
    if not decoded["ok"]:
        raise PipelineError(f"--base の動画を復号できません: {decoded['error']}")
    if info["fps"] is None:
        raise PipelineError(f"--base の動画から fps を読めません: {path.name}")
    if fps is not None and abs(float(fps) - info["fps"]) > 1e-3:
        raise PipelineError(f"--fps {fps:g} が --base の fps {info['fps']:g} と違います（土台が動画のときは動画の fps を使います）")
    return {"kind": "video", "path": str(path), "sha256": sha256(path), "size": [info["width"], info["height"]],
            "fps": float(info["fps"]), "frames": decoded["frames"], "matrix": info["matrix"]}


ALPHA_PIX_FMT_PREFIXES = ("rgba", "bgra", "argb", "abgr", "yuva", "gbrap", "ya")   # ffmpeg のアルファつき pix_fmt（gray など名前に a を含むだけのものは除く）


def has_alpha_pix_fmt(pix_fmt):
    return bool(pix_fmt) and pix_fmt.startswith(ALPHA_PIX_FMT_PREFIXES)


def _has_alpha(image):
    return "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)


def _frame_number(path):
    digits = re.findall(r"\d+", path.stem)
    if not digits:
        raise PipelineError(f"first_frame を使う連番は、ファイル名の最後に番号が要ります: {path.name}")
    return int(digits[-1])


def describe_layer(path, options=None):
    """レイヤーの {kind, path, size, frames, sha256 | files, options}。

    連番: options の first_frame が無ければ並びの順の全コマ（各コマの名前と sha256。0.1 と同じ）。first_frame があれば
    ファイル名の最後の番号で引くので、使うコマ（出力のコマ数が決まった後に select_frames で選ぶ）だけを記録する。
    動画は全体の sha256。1 枚の画像（options の image）は、その sha256。"""
    options = {**PLAIN, **(options or {})}
    path = Path(path)
    if options.get("image"):
        if not path.is_file():
            raise PipelineError(f"レイヤーの画像が見つかりません: {path}")
        with Image.open(path) as image:
            if not _has_alpha(image):
                raise PipelineError(f"レイヤーの画像にアルファがありません（{image.mode}）: {path.name}。透過の PNG にしてください")
            size = list(image.size)
        return {"kind": "image", "path": str(path), "sha256": sha256(path), "size": size, "frames": None, "options": _record_options(options)}
    if path.is_dir():
        files = sorted((p for p in path.iterdir() if p.is_file() and p.suffix.lower() in FRAME_SUFFIXES), key=natural_key)
        if not files:
            raise PipelineError(f"--layer のフォルダに連番（{'/'.join(FRAME_SUFFIXES)}）がありません: {path}")
        with Image.open(files[0]) as image:
            width, height = image.size
            if not _has_alpha(image):
                raise PipelineError(f"--layer の連番にアルファがありません（{image.mode}）: {files[0].name}。透過の PNG にしてください")
        layer = {"kind": "frames", "path": str(path), "size": [width, height], "frames": len(files), "options": _record_options(options)}
        if options["first_frame"] is None:
            layer["files"] = [{"name": p.name, "sha256": sha256(p)} for p in files]
        else:
            numbers = {}
            for p in files:
                number = _frame_number(p)
                if number in numbers:
                    raise PipelineError(f"連番の番号が重なっています: {numbers[number].name} と {p.name}（{number}）")
                numbers[number] = p
            layer["numbered"], layer["frames"] = numbers, None
        return layer
    if not path.is_file():
        raise PipelineError(f"--layer が見つかりません: {path}")
    if _is_image(path):
        raise PipelineError(f"--layer に 1 枚の画像は渡せません（連番のフォルダか透過動画にしてください。"
                            f"時刻を決めて重ねるなら --layers の image）: {path.name}")
    info = probe.inspect(path)["video"]
    decoded = probe.decode(path, "v")
    if not decoded["ok"]:
        raise PipelineError(f"--layer の動画を復号できません: {decoded['error']}")
    if not has_alpha_pix_fmt(info["pix_fmt"]):
        raise PipelineError(f"--layer の動画にアルファがありません（pix_fmt {info['pix_fmt']}）: {path.name}")
    layer = {"kind": "video", "path": str(path), "sha256": sha256(path), "size": [info["width"], info["height"]],
             "frames": decoded["frames"], "fps": info["fps"], "matrix": info["matrix"], "options": _record_options(options)}
    if options["first_frame"] is not None:
        layer["available"], layer["frames"] = decoded["frames"], None
    return layer


def _record_options(options):
    """記録に残すレイヤーの設定（style は既定値を埋めた形）。"""
    keep = {k: options[k] for k in ("first_frame", "missing", "alpha", "resize", "opacity") if k in options}
    for key in ("start_s", "end_s", "fade_in_s", "fade_out_s"):
        if options.get("image"):
            keep[key] = float(options.get(key, 0.0))
    keep["style"] = _normalize_style(options.get("style"))
    return keep


def _normalize_style(style):
    if not style:
        return None
    out = {"tint": None if style.get("tint") is None else [float(v) for v in style["tint"]]}
    for name, defaults in STYLE_DEFAULTS.items():
        given = style.get(name)
        out[name] = None if given is None else {**defaults, **{k: v for k, v in given.items() if not k.startswith("x_")}}
    return out


def select_frames(layer, n_frames):
    """first_frame の連番・動画で、出力の n_frames コマに使うコマを決める（無いコマは missing に従う）。"""
    options = layer["options"]
    first = options["first_frame"]
    if first is None or layer["kind"] == "image":
        return layer
    wanted = range(first, first + n_frames)
    if layer["kind"] == "frames":
        numbers = layer.pop("numbered")
        missing = [n for n in wanted if n not in numbers]
        layer["files"] = [{"name": numbers[n].name, "sha256": sha256(numbers[n]), "number": n} for n in wanted if n in numbers]
    else:
        missing = [n for n in wanted if n >= layer["available"]]
    if missing and options["missing"] == "error":
        raise PipelineError(f"{Path(layer['path']).name} に、番号 {missing[0]}〜{missing[-1]} のうち {len(missing)} コマがありません"
                            f"（first_frame {first}。無いコマを透明にするなら missing: \"transparent\"）")
    layer["frames"], layer["missing_frames"] = n_frames, len(missing)
    return layer


def load_layers(path):
    """`--layers` の設定を読んで検証し、[(パス, 設定)] を返す（素材パスは設定ファイルのあるディレクトリ基準）。"""
    from ..io import read_json, safe_path
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"--layers が見つかりません: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"--layers が JSON として読めません ({path.name}): {error}") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != LAYERS_VERSION:
        raise PipelineError(f"--layers の schema_version は {LAYERS_VERSION!r} にしてください")
    schema = json.loads(LAYERS_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        raise PipelineError("--layers の設定が正しくありません:\n" + "\n".join(
            f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]))
    base_dir = path.resolve().parent
    out = []
    for index, item in enumerate(raw["layers"]):
        item = {k: v for k, v in item.items() if not k.startswith("x_")}
        label = f"layers[{index}]"
        if "image" in item:
            if item["end_s"] <= item["start_s"]:
                raise PipelineError(f"{label}: end_s は start_s より後にしてください（{item['start_s']} → {item['end_s']}）")
            bad = [k for k in ("first_frame", "missing") if k in item]
        else:
            bad = [k for k in ("start_s", "end_s", "fade_in_s", "fade_out_s") if k in item]
        if bad:
            raise PipelineError(f"{label}: {bad} は {'path（連番・動画）' if 'image' in item else 'image（1 枚の画像）'} のレイヤーでは使えません")
        if "missing" in item and "first_frame" not in item:
            raise PipelineError(f"{label}: missing は first_frame と一緒に使います")
        options = {**PLAIN, **item, "opacity": float(item.get("opacity", 1.0))}
        if "image" in item:
            options.update(image=True, fade_in_s=float(item.get("fade_in_s", 0.0)), fade_out_s=float(item.get("fade_out_s", 0.0)),
                           start_s=float(item["start_s"]), end_s=float(item["end_s"]))
        out.append((safe_path(base_dir, item.get("path") or item["image"]), options))
    return out


def describe_audio(path):
    if path is None:
        return None
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"--audio が見つかりません: {path}")
    return {"path": str(path), "sha256": sha256(path)}


def resolve(base, layers, fps, frames=None):
    """コマ数・fps・寸法の食い違いを止め、(size, fps, n_frames) を返す。

    コマ数は土台の動画か、並びの順で読むレイヤー（first_frame の無い連番・動画）から決まり、すべて一致すること。
    どちらも無ければ frames（`--frames`）が要る。first_frame のレイヤーと 1 枚の画像はコマ数を決めない。"""
    size = base["size"]
    counts = {f"layer[{i}]": layer["frames"] for i, layer in enumerate(layers)
              if layer["kind"] != "image" and layer["options"]["first_frame"] is None}
    if base["frames"] is not None:
        counts["base"] = base["frames"]
    if frames is not None:
        if base["frames"] is not None:
            raise PipelineError("--frames は土台が画像のときだけ使えます（動画の土台はそのコマ数）")
        counts["--frames"] = int(frames)
    if not counts:
        raise PipelineError("コマ数が決まりません: 土台が画像で、並びの順で読むレイヤーも無いときは --frames を指定してください")
    if len(set(counts.values())) != 1:
        raise PipelineError("コマ数が合いません: " + ", ".join(f"{name} {count}" for name, count in counts.items()))
    for i, layer in enumerate(layers):
        _check_fit(layer["size"], size, layer["options"], f"layer[{i}]")
        if layer.get("fps") is not None and abs(layer["fps"] - fps) > 1e-3:
            raise PipelineError(f"fps が合いません: base {fps:g} / layer[{i}] {layer['fps']:g}")
    return size, fps, next(iter(counts.values()))


def _check_fit(found, size, options, where):
    """寸法 found のレイヤー（か連番のコマ）を、土台の寸法 size に重ねられるか。違う寸法は、resize: true で縦横比が 1% 以内の
    ときだけ縮尺する（それ以外は止める）。resolve はレイヤーの寸法（連番は 1 枚目）で、読み出しは連番の各コマで確かめる。"""
    if list(found) == list(size):
        return
    if not options["resize"]:
        raise PipelineError(f"寸法が合いません: base {size[0]}x{size[1]} / {where} {found[0]}x{found[1]}"
                            f"（縮尺して重ねるなら --layers で resize: true）")
    if abs(found[0] * size[1] - size[0] * found[1]) > 0.01 * size[0] * found[1]:
        raise PipelineError(f"縦横比が合いません: base {size[0]}x{size[1]} / {where} {found[0]}x{found[1]}")


# ---- 読み出し ---------------------------------------------------------------------

class _VideoReader:
    """動画を rgb24（土台）か rgba（レイヤー）で 1 コマずつ読む。寸法は変えない。

    コマは復号順にそのまま（-fps_mode passthrough。probe.decode の数え方と同じ）。タグが bt709 の動画は行列を指定して読む
    （dance / inspection と同じ。指定しないと swscale の既定の BT.601 で色が変わる）。"""

    def __init__(self, path, size, pix_fmt, matrix=None, start=0):
        width, height = size
        channels = 4 if pix_fmt == "rgba" else 3
        self.path, self.bytes_per_frame, self.shape = Path(path), width * height * channels, (height, width, channels)
        self.errors = tempfile.TemporaryFile()
        scale = ((f"trim=start_frame={int(start)}," if start else "")      # first_frame: 復号順の start コマ目から
                 + "scale=iw:ih:flags=bicubic+accurate_rnd+full_chroma_int" + (":in_color_matrix=bt709" if matrix == "bt709" else ""))
        self.proc = subprocess.Popen([media.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-an",
                                      "-fps_mode", "passthrough", "-vf", scale, "-pix_fmt", pix_fmt, "-f", "rawvideo", "pipe:1"],
                                     stdout=subprocess.PIPE, stderr=self.errors)

    def read(self):
        data = self.proc.stdout.read(self.bytes_per_frame)
        while data is not None and len(data) < self.bytes_per_frame:
            more = self.proc.stdout.read(self.bytes_per_frame - len(data))
            if not more:
                break
            data += more
        if data is None or len(data) != self.bytes_per_frame:
            self.errors.seek(0)
            detail = self.errors.read()[-400:].decode("utf-8", "replace")
            raise PipelineError(f"{self.path.name} を復号できませんでした: {detail or '途中で終わりました'}")
        return np.frombuffer(data, dtype=np.uint8).reshape(self.shape)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.stdout.close()
        self.proc.wait()
        self.errors.close()


def _base_frames(base, n_frames):
    if base["kind"] == "image":
        with Image.open(base["path"]) as image:
            still = np.asarray(image.convert("RGB"), dtype=np.uint8)
        for _ in range(n_frames):
            yield still
        return
    reader = _VideoReader(base["path"], base["size"], "rgb24", base.get("matrix"))
    try:
        for _ in range(n_frames):
            yield reader.read()
    finally:
        reader.close()


def _fit(rgba, size):
    """resize: true のレイヤーを土台の大きさへ（縮めるなら INTER_AREA、広げるなら INTER_LINEAR。実制作の重ねと同じ）。"""
    if (rgba.shape[1], rgba.shape[0]) == tuple(size):
        return rgba
    import cv2
    return cv2.resize(rgba, tuple(size), interpolation=cv2.INTER_AREA if rgba.shape[1] > size[0] else cv2.INTER_LINEAR)


def _rgba(path, size, options=None):
    """画像を uint8 RGBA で読み、土台の大きさへ合わせる。options（連番のレイヤーの設定）を渡すと、縮尺する前にこのコマの寸法を
    確かめる（resolve は連番の 1 枚目の寸法しか見ないので、途中のコマの寸法の違いを黙って縮尺しない）。"""
    with Image.open(path) as image:
        if options is not None:
            _check_fit(image.size, size, options, f"連番のコマ {Path(path).name}")
        return _fit(np.asarray(image.convert("RGBA"), dtype=np.uint8), size)


def _layer_frames(layer, n_frames, size=None):
    """レイヤーのコマ（uint8 RGBA）を n_frames 枚返す。first_frame で無いコマ（missing: transparent）は None。"""
    size = size or layer["size"]
    first = layer["options"]["first_frame"]
    if layer["kind"] == "image":
        still = _rgba(layer["path"], size)
        for _ in range(n_frames):
            yield still
        return
    if layer["kind"] == "frames":
        folder = Path(layer["path"])
        if first is None:
            for item in layer["files"]:
                yield _rgba(folder / item["name"], size, layer["options"])
            return
        names = {item["number"]: item["name"] for item in layer["files"]}
        for number in range(first, first + n_frames):
            yield _rgba(folder / names[number], size, layer["options"]) if number in names else None
        return
    available = layer["frames"] if first is None else layer["available"] - first
    reader = _VideoReader(layer["path"], layer["size"], "rgba", layer.get("matrix"), start=first or 0)
    try:
        for index in range(n_frames):
            yield _fit(reader.read(), size) if index < available else None
    finally:
        reader.close()


def composite(under, rgba):
    """straight α で rgba を under（uint8 RGB）に重ねる。sRGB の値のまま混ぜ、四捨五入して uint8。"""
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    mixed = rgba[..., :3].astype(np.float32) * alpha + under.astype(np.float32) * (1.0 - alpha)
    return np.clip(mixed + 0.5, 0, 255).astype(np.uint8)


def plain(layer):
    """0.1 と同じ式（straight α・不透明度 1・仕上げなし。レイヤーごとに uint8 へ丸める）で重ねるレイヤーか。"""
    options = layer["options"]
    return layer["kind"] != "image" and options["style"] is None and options["alpha"] == "straight" and options["opacity"] == 1.0


def opacity_at(layer, index, fps):
    """index コマ目（時刻 index/fps）の不透明度。1 枚の画像は start_s ≤ t < end_s の間だけで、出入りは smoothstep。"""
    options = layer["options"]
    if layer["kind"] != "image":
        return options["opacity"]
    t = index / fps
    if not options["start_s"] <= t < options["end_s"]:
        return 0.0
    from .timeline import ease
    value = 1.0
    if options["fade_in_s"] > 0:
        value = min(value, float(ease("in_out", (t - options["start_s"]) / options["fade_in_s"])))
    if options["fade_out_s"] > 0:
        value = min(value, float(ease("in_out", (options["end_s"] - t) / options["fade_out_s"])))
    return options["opacity"] * value


def apply_layer(f, rgba, options, opacity=1.0, scale=1.0):
    """表示用の値（float32 の 0..255）の f に、rgba（uint8）を style つきで重ねた f を返す（実制作の重ねの式と同じ式・同じ順）。

    後光（アルファを dilate_px の正方形で iterations 回太らせて σ でぼかし、gain 倍して 0..1 に切ったものの分だけ暗くする）→
    影（σ でぼかしたアルファ × gain の分だけ暗くする）→ 光彩（color × ぼかしたアルファ × amount を足す）→
    本体（f × (1 − α) + 色 × α。色は tint × 輝度、tint が無ければレイヤーの色）。どの項にも opacity を掛ける。scale は長さの倍率。"""
    import cv2
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    if alpha.max() <= 0:
        return f
    style = options["style"] or {}
    if options["alpha"] == "premultiplied":
        rgb = np.clip(rgba[..., :3].astype(np.float32) / np.maximum(alpha, np.float32(1e-6)), 0, 255).astype(np.float32)
        bgr = rgb[..., ::-1]
    else:
        rgb, bgr = rgba[..., :3].astype(np.float32), rgba[..., 2::-1].astype(np.float32)
    if style.get("tint") is not None:
        lum = (bgr / 255.0).mean(-1, keepdims=True)       # 実制作は BGR の順に平均していた（float32 の丸めまで同じにする）
        color = np.array(style["tint"], np.float32) * lum
    else:
        color = rgb
    plane = alpha[..., 0]
    backdrop, shadow, glow = style.get("backdrop"), style.get("shadow"), style.get("glow")
    if backdrop:
        size = max(1, int(round(backdrop["dilate_px"] * scale)))
        grown = cv2.dilate(plane, np.ones((size, size), np.uint8), iterations=int(backdrop["iterations"]))
        halo = np.clip(cv2.GaussianBlur(grown, (0, 0), backdrop["sigma_px"] * scale) * backdrop["gain"], 0, 1)
        f = f * (1 - backdrop["amount"] * halo[..., None] * opacity)
    if shadow:
        soft = cv2.GaussianBlur(plane, (0, 0), shadow["sigma_px"] * scale)[..., None]
        f = f * (1 - shadow["amount"] * np.clip(soft * shadow["gain"], 0, 1) * opacity)
    if glow:
        wide = cv2.GaussianBlur(plane, (0, 0), glow["sigma_px"] * scale)[..., None]
        f = f + np.array(glow["color"], np.float32) * wide * glow["amount"] * opacity
    return f * (1 - alpha * opacity) + color * alpha * opacity


def frames(base, layers, n_frames, progress=None):
    """合成したコマを順に返す。仕上げ・不透明度・premultiplied・画像のレイヤーが無ければ 0.1 と同じ式で、
    あれば表示用の値の float で全部のレイヤーを重ねてから 1 回だけ四捨五入する。"""
    size, fps = base["size"], base.get("fps")         # fps は 1 枚の画像のレイヤーの時刻にだけ使う
    sources = [_layer_frames(layer, n_frames, size) for layer in layers]
    simple = all(plain(layer) for layer in layers)
    scale = min(size) / 1080.0                       # 長さは出力の短い辺を 1080 としたときの画素
    under_frames = _base_frames(base, n_frames)
    try:
        for index, under in enumerate(under_frames):
            image = under if simple else under.astype(np.float32)
            for layer, source in zip(layers, sources):
                rgba = next(source)
                if rgba is None:
                    continue
                if rgba.shape[:2] != under.shape[:2]:
                    raise PipelineError(f"コマ {index} の寸法が違います: {rgba.shape[1]}x{rgba.shape[0]}（期待 {under.shape[1]}x{under.shape[0]}）")
                if simple:
                    image = composite(image, rgba)
                    continue
                opacity = opacity_at(layer, index, fps)
                if opacity > 0:
                    image = apply_layer(image, rgba, layer["options"], opacity, scale)
            yield image if simple else np.clip(image + 0.5, 0, 255).astype(np.uint8)
            if progress:
                progress(index + 1)
    finally:   # 途中で止まっても、各レイヤーの ffmpeg を閉じる
        for source in sources + [under_frames]:
            source.close()


# ---- run --------------------------------------------------------------------------

def _validate(record):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(record), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        raise PipelineError("overlay の記録の形式が違います:\n" + "\n".join(
            f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]))


def _meter(say, total):
    if say is None:
        return None
    began, every = time.monotonic(), max(1, round(total / 20))

    def report(done):
        if done % every == 0 or done == total:
            spent = time.monotonic() - began
            say(f"[overlay] {done}/{total} コマ 経過 {spent:.1f} 秒 残り約 {spent / done * (total - done):.1f} 秒")
    return report


def run_overlay(base_path, layer_paths, audio_path=None, out=None, fps=None, crf=DEFAULT_CRF, force=False, progress=None, quiet=False,
                layers_file=None, frames_count=None):
    """土台にレイヤーを重ねて overlay.mp4 を書き、run と index に記録する。CLI が stdout に出す要約を返す。

    layer_paths（`--layer`。設定なし）か layers_file（`--layers`。fx-overlay-layers/0.1）のどちらか。frames_count は `--frames`。"""
    began = time.monotonic()
    say = (lambda message: None) if quiet or progress is None else progress
    if layers_file is not None and layer_paths:
        raise PipelineError("--layer と --layers は一緒に使えません（設定をつけるなら --layers にまとめてください）")
    entries = load_layers(layers_file) if layers_file is not None else [(p, None) for p in layer_paths]
    if not entries:
        raise PipelineError("--layer か --layers を指定してください")
    base = describe_base(base_path, fps)
    layers = [describe_layer(p, options) for p, options in entries]
    audio = describe_audio(audio_path)
    size, fps, n_frames = resolve(base, layers, base["fps"], frames_count)
    layers = [select_frames(layer, n_frames) for layer in layers]
    crf = int(crf)
    if not 0 <= crf <= 51:
        raise PipelineError(f"--crf は 0〜51 にしてください: {crf}")
    record = {"schema_version": SCHEMA_VERSION, "base": base, "layers": layers, "audio": audio,
              "settings": {"fps": fps, "crf": crf, "size": list(size), "frames": n_frames, "blend": "srgb"}}
    _validate(record)
    # 重ねる式や読み方（この package のコード）が変われば key も変わる（古い絵を再利用しない）
    key = runs.content_key_for("overlay", {"record": record, "engine": {"version": SCHEMA_VERSION, "fingerprint": fingerprint()},
                                           "libraries": _libraries()})
    out = (Path(out) if out else Path(base_path).resolve().parent / "overlay_runs").resolve()
    with runs.Session(out, key, "overlay", "render.log", say=say, tag="[overlay]", force=force) as session:
        if session.reused:
            return _summarize(session.reused_dir, session.reused, reused=True, began=began)
        run = session.run
        run.manifest.update(overlay=record, libraries=_libraries(), variants={}, grid=None, warnings=[],
                            interval={"start": 0.0, "duration": n_frames / fps, "fps": fps, "frames": n_frames},
                            output={"size": list(size), "scale": 1.0, "crf": crf})
        run.save()
        run.log(f"overlay start key={key} base={Path(base['path']).name} layers={len(layers)} frames={n_frames} size={size[0]}x{size[1]} fps={fps:g}")
        sound = None if audio is None else {"path": Path(audio["path"]), "start": 0.0, "duration": n_frames / fps}
        path = run.dir / "overlay.mp4"
        from . import render
        result = render.write_video(path, frames(base, layers, n_frames, _meter(None if quiet else progress, n_frames)),
                                    size, fps, crf, n_frames, sound, run.log_path)
        checks = probe.check_video(path, width=size[0], height=size[1], fps=fps, frames=n_frames, audio=audio is not None,
                                   audio_s=n_frames / fps if audio else None, bt709=True)
        run.manifest["variants"]["overlay"] = {"path": path.name, "frames": checks["frames"], "sha256": sha256(path),
                                               "technical_pass": checks["pass"], "checks": checks, "render_seconds": result["seconds"]}
        run.save()
        run.log(f"overlay: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
        say(f"[overlay] {checks['frames']} フレーム（{result['seconds']:.1f} 秒）technical_pass={checks['pass']}")
        if not checks["pass"]:
            message = f"技術検査に失敗しました（{checks['failures']}）。詳細: {run.dir / 'manifest.json'}"
            run.finish(False, message)
            raise PipelineError(message)
        session.succeed()
    return _summarize(run.dir, run.manifest, reused=False, began=began)


def _libraries():
    from . import render
    return render.library_versions()


def _summarize(run_dir, manifest, reused, began):
    item = manifest["variants"]["overlay"]
    return {"run_id": manifest["run_id"], "run_dir": str(run_dir), "reused": reused, "status": manifest["status"],
            "technical_pass": manifest["technical_pass"], "output": str(Path(run_dir) / item["path"]), "frames": item["frames"],
            "size": manifest["output"]["size"], "fps": manifest["interval"]["fps"], "layers": len(manifest["overlay"]["layers"]),
            "audio": manifest["overlay"]["audio"] is not None, "seconds": round(time.monotonic() - began, 1)}
