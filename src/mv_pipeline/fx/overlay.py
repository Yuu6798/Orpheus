"""透過レイヤーの重ね合わせ（`mv overlay`。実験的。FX-LYRIC-1）。

    mv overlay --base 画像か動画 --layer 連番のフォルダか透過動画 [--layer …] [--audio 音] [--out DIR] [--fps 30] [--crf 18]

土台（1 枚の画像か動画）の上に、透過レイヤー（PNG 連番のフォルダか、アルファつきの動画）を指定した順に重ね、
fx の共通の書き出し（render.write_video。BT.709・tv・yuv420p の式と印）で H.264 にする。ffmpeg の引数は写さない。

- 重ね方: straight α（半透明の画素は光の色、アルファが 0 の画素は色を持たない）を前提に、sRGB の 8 bit の値のまま
  out = layer × α + under × (1 − α) で重ねる（線形化しない。Remotion の PNG 連番と同じ前提）。
- コマ数・fps・寸法が合わないものはエラー（勝手に合わせない）。土台が画像なら --fps が要り、コマ数はレイヤーから決まる。
- run（<out>/<run_id>/）に overlay.mp4・manifest.json・render.log を残し、index.json（台帳）に key → run_id を登録する。
  key は土台・各レイヤーの全コマ・音声の sha256 と設定から作る。同じ入力で成功済みなら再利用する（--force で作り直す）。
  manifest の overlay の欄は fx_overlay.schema.json（fx-overlay/0.1。contracts/ の外）で検証してから書く。
- 最後に probe.check_video（technical_pass）を通す。通らなければ run は failed。

cv2 は使わない（Pillow・numpy・ffmpeg だけ）。
"""
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

import jsonschema
import numpy as np
from PIL import Image

from .. import media
from ..io import PipelineError, atomic_json, sha256
from . import probe, runs
from . import spec as fxspec

SCHEMA_VERSION = "fx-overlay/0.1"
SCHEMA_PATH = Path(__file__).with_name("fx_overlay.schema.json")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
FRAME_SUFFIXES = (".png", ".webp", ".tif", ".tiff")
DEFAULT_CRF = 18


# ---- 入力 -----------------------------------------------------------------------

def _is_image(path):
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


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
            "fps": float(info["fps"]), "frames": decoded["frames"]}


def describe_layer(path):
    """レイヤーの {kind, path, size, frames, sha256 | files}。連番は各コマの名前と sha256、動画は全体の sha256。"""
    path = Path(path)
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in FRAME_SUFFIXES)
        if not files:
            raise PipelineError(f"--layer のフォルダに連番（{'/'.join(FRAME_SUFFIXES)}）がありません: {path}")
        width, height = _image_size(files[0])
        return {"kind": "frames", "path": str(path), "size": [width, height], "frames": len(files),
                "files": [{"name": p.name, "sha256": sha256(p)} for p in files]}
    if not path.is_file():
        raise PipelineError(f"--layer が見つかりません: {path}")
    if _is_image(path):
        raise PipelineError(f"--layer に 1 枚の画像は渡せません（連番のフォルダか透過動画にしてください）: {path.name}")
    info = probe.inspect(path)["video"]
    decoded = probe.decode(path, "v")
    if not decoded["ok"]:
        raise PipelineError(f"--layer の動画を復号できません: {decoded['error']}")
    if "a" not in (info["pix_fmt"] or ""):
        raise PipelineError(f"--layer の動画にアルファがありません（pix_fmt {info['pix_fmt']}）: {path.name}")
    return {"kind": "video", "path": str(path), "sha256": sha256(path), "size": [info["width"], info["height"]],
            "frames": decoded["frames"], "fps": info["fps"]}


def describe_audio(path):
    if path is None:
        return None
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"--audio が見つかりません: {path}")
    return {"path": str(path), "sha256": sha256(path)}


def resolve(base, layers, fps):
    """コマ数・fps・寸法の食い違いを止め、(size, fps, n_frames) を返す。"""
    size = base["size"]
    counts = {f"layer[{i}]": layer["frames"] for i, layer in enumerate(layers)}
    if base["frames"] is not None:
        counts["base"] = base["frames"]
    if len(set(counts.values())) != 1:
        raise PipelineError("コマ数が合いません: " + ", ".join(f"{name} {count}" for name, count in counts.items()))
    for i, layer in enumerate(layers):
        if layer["size"] != size:
            raise PipelineError(f"寸法が合いません: base {size[0]}x{size[1]} / layer[{i}] {layer['size'][0]}x{layer['size'][1]}")
        if layer.get("fps") is not None and abs(layer["fps"] - fps) > 1e-3:
            raise PipelineError(f"fps が合いません: base {fps:g} / layer[{i}] {layer['fps']:g}")
    return size, fps, next(iter(counts.values()))


# ---- 読み出し ---------------------------------------------------------------------

class _VideoReader:
    """動画を rgb24（土台）か rgba（レイヤー）で 1 コマずつ読む。寸法は変えない。"""

    def __init__(self, path, size, pix_fmt):
        width, height = size
        channels = 4 if pix_fmt == "rgba" else 3
        self.path, self.bytes_per_frame, self.shape = Path(path), width * height * channels, (height, width, channels)
        self.errors = tempfile.TemporaryFile()
        self.proc = subprocess.Popen([media.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-an",
                                      "-pix_fmt", pix_fmt, "-f", "rawvideo", "pipe:1"], stdout=subprocess.PIPE, stderr=self.errors)

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
    reader = _VideoReader(base["path"], base["size"], "rgb24")
    try:
        for _ in range(n_frames):
            yield reader.read()
    finally:
        reader.close()


def _layer_frames(layer, n_frames):
    if layer["kind"] == "frames":
        folder = Path(layer["path"])
        for item in layer["files"]:
            with Image.open(folder / item["name"]) as image:
                yield np.asarray(image.convert("RGBA"), dtype=np.uint8)
        return
    reader = _VideoReader(layer["path"], layer["size"], "rgba")
    try:
        for _ in range(n_frames):
            yield reader.read()
    finally:
        reader.close()


def composite(under, rgba):
    """straight α で rgba を under（uint8 RGB）に重ねる。sRGB の値のまま混ぜ、四捨五入して uint8。"""
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    mixed = rgba[..., :3].astype(np.float32) * alpha + under.astype(np.float32) * (1.0 - alpha)
    return np.clip(mixed + 0.5, 0, 255).astype(np.uint8)


def frames(base, layers, n_frames, progress=None):
    """合成したコマを順に返す。"""
    sources = [_layer_frames(layer, n_frames) for layer in layers]
    for index, under in enumerate(_base_frames(base, n_frames)):
        image = under
        for source in sources:
            rgba = next(source)
            if rgba.shape[:2] != image.shape[:2]:
                raise PipelineError(f"コマ {index} の寸法が違います: {rgba.shape[1]}x{rgba.shape[0]}（期待 {image.shape[1]}x{image.shape[0]}）")
            image = composite(image, rgba)
        yield image
        if progress:
            progress(index + 1)


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


def run_overlay(base_path, layer_paths, audio_path=None, out=None, fps=None, crf=DEFAULT_CRF, force=False, progress=None, quiet=False):
    """土台にレイヤーを重ねて overlay.mp4 を書き、run と index に記録する。CLI が stdout に出す要約を返す。"""
    began = time.monotonic()
    say = progress or (lambda message: None)
    if not layer_paths:
        raise PipelineError("--layer を 1 つ以上指定してください")
    base = describe_base(base_path, fps)
    layers = [describe_layer(p) for p in layer_paths]
    audio = describe_audio(audio_path)
    size, fps, n_frames = resolve(base, layers, base["fps"])
    crf = int(crf)
    if not 0 <= crf <= 51:
        raise PipelineError(f"--crf は 0〜51 にしてください: {crf}")
    record = {"schema_version": SCHEMA_VERSION, "base": base, "layers": layers, "audio": audio,
              "settings": {"fps": fps, "crf": crf, "size": list(size), "frames": n_frames, "alpha": "straight", "blend": "srgb"}}
    _validate(record)
    key = runs.content_key_for("overlay", {"record": record, "engine": fxspec.canonical_json({"version": SCHEMA_VERSION}), "libraries": _libraries()})
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


def content_sha256(record):
    """記録の正規形の sha256（テストと台帳の照合用）。"""
    return hashlib.sha256(fxspec.canonical_json(record).encode("utf-8")).hexdigest()
