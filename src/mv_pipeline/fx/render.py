"""フレーム合成（純関数）と ffmpeg への書き出し。

    ctx = prepare(spec, scale=1.0)                  # 素材の前処理・層・光の形。描画サイズごとに 1 回
    img = frame(ctx, t, "both")                     # uint8 (H, W, 3) RGB。同じ ctx・t・版なら同じバイト列
    render_variant(ctx, "both", "out.mp4", start=0, duration=10)

t は**ショット時刻**（spec の時間軸。start 付きで描いても同じ t は同じ絵）。
版: base = カメラ + 深度視差のみ / layers = + plane と effects / light = + 局所光 / both = 全部。
spec に layers や lights が無ければその機能はどの版でも無効（版は残り、variant_notes に記録される）。
spec に look（色調整・周辺減光・粒状感）があれば、光とレイヤーの後の最終段で**全 4 版に同じものを**掛ける
（look.py）。look が無ければ従来の to_srgb8 の経路で、出力は従来とバイト単位で同じ。

CLI や run 管理の担当者向け:
  - write_video(out, frames, size, fps, crf, n_frames, audio=None, log_path=None) は任意のフレーム列
    （グリッド合成など）を同じ書式（libx264 / bt709 / tv / yuv420p / AAC）で書く。失敗時に最終名の
    ファイルを作らない（一時名に書いて成功時だけ置き換える）。
  - ctx.warnings は spec と素材の注意（黙って切った割合など）。manifest に入れる。
  - library_versions() は manifest 用のライブラリ版。fx.fingerprint() とあわせて再現性の鍵にする。
"""
import os
import platform
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import PIL
from PIL import Image

from .. import media
from ..io import PipelineError
from . import VARIANTS, camera, layers, light, timeline
from . import look as fxlook
from .assets import Assets, load_assets
from .color import soft_add, to_srgb8

FADE_IN_S = 0.02
FADE_OUT_S = 0.25


@dataclass
class Context:
    spec: object
    size: tuple
    assets: Assets
    path: camera.CameraPath
    background: np.ndarray
    planes: list
    stack: list                      # z 昇順の Plane / Fog / Particles
    lights: list
    warnings: list = field(default_factory=list)
    hole: object = None              # 背景の穴埋め重み float32 (H, W)。plane が無ければ None
    look: object = None              # look.Look（周辺減光・粒状感は prepare で 1 回だけ作る）。spec に look が無ければ None

    @property
    def fps(self):
        return self.spec.data["output"]["fps"]

    @property
    def crf(self):
        return self.spec.data["output"]["crf"]

    @property
    def duration(self):
        return self.spec.data["duration_s"]

    @property
    def has_layers(self):
        return bool(self.stack)

    @property
    def has_lights(self):
        return bool(self.lights)

    def n_frames(self, duration=None, start=0.0):
        return timeline.frame_count(self.duration - start if duration is None else duration, self.fps)

    def audio_path(self):
        audio = self.spec.data["audio"]
        return self.spec.resolve(audio["path"]) if audio else None

    def variant_notes(self):
        """版ごとの {features: 有効な機能, empty: その機能が spec に無く base と同じ映像か}（manifest 用）。"""
        data = self.spec.data
        base = ["camera"] + (["depth_parallax"] if self.assets.depth is not None and data["camera"]["parallax_px"] > 0 else [])
        base += ["look"] if self.look is not None else []     # look は全 4 版に同じものが掛かる
        notes = {"base": {"features": base, "empty": False}}
        notes["layers"] = {"features": base + ["layers"], "empty": False} if self.has_layers \
            else {"features": base, "empty": True, "note": "spec に layers が無い（または plane/effects が空）ため base と同じ映像"}
        notes["light"] = {"features": base + ["light"], "empty": False} if self.has_lights \
            else {"features": base, "empty": True, "note": "spec に lights が無いため base と同じ映像"}
        both = base + (["layers"] if self.has_layers else []) + (["light"] if self.has_lights else [])
        notes["both"] = {"features": both, "empty": not (self.has_layers or self.has_lights)}
        return notes


def scaled_size(spec, scale=1.0):
    """出力寸法 × scale を偶数に丸めた (W, H)。scale=1.0 なら spec の output どおり。"""
    out = spec.data["output"]
    if not 0 < scale <= 4:
        raise PipelineError(f"scale は 0 より大きく 4 以下にしてください: {scale}")
    even = lambda v: max(16, 2 * int(round(v * scale / 2)))  # noqa: E731
    return even(out["width"]), even(out["height"])


def prepare(spec, scale=1.0, size=None):
    """spec から描画に必要な静的データをすべて作る（素材読み込み・背景の穴埋め・霧/火の粉・光の形）。"""
    size = tuple(size) if size else scaled_size(spec, scale)
    data = spec.data
    assets = load_assets(spec, size)
    path = camera.CameraPath(data["camera"], data["duration_s"], size, assets.box)
    warnings = list(spec.warnings) + list(assets.warnings)
    bounds = path.out_of_bounds()
    if bounds:
        warnings.append(bounds)
    items, planes, background, hole = [], [], assets.plate, None
    layer_spec = data["layers"]
    if layer_spec and (layer_spec["planes"] or layer_spec["effects"]):
        for plane_spec in layer_spec["planes"]:
            alpha = assets.masks[plane_spec["mask"]]
            if float(alpha.max()) < 0.01:
                warnings.append(f"plane {plane_spec['id']!r} のマスク {plane_spec['mask']!r} がほぼ空です")
            planes.append(layers.make_plane(plane_spec, alpha, assets.plate))
        bg = layer_spec["background"]
        background, hole = layers.build_background(assets.plate_u8, assets.plate, [p.alpha for p in planes],
                                                bg["inpaint_px"], size[0] / 1920.0)
        items = list(planes)
        for effect in layer_spec["effects"]:
            maker = layers.make_fog if effect["type"] == "fog" else layers.make_particles
            items.append(maker(effect, data["seed"], size))
        items.sort(key=lambda item: item.z)  # 安定ソート: 同じ z は plane → effects の宣言順
    lights = light.prepare(data, assets, size, planes) if data["lights"] else []
    for lt in lights:
        if not lt.receivers and lt.glow_w is None:
            warnings.append(f"light {lt.id!r} は receivers も emitter も無く、何も描きません")
    look = fxlook.prepare(data["look"], size, data["output"]["fps"]) if data["look"] is not None else None
    return Context(spec, size, assets, path, background, planes, items, lights, warnings, hole, look)


def frame(ctx, t, variant):
    """時刻 t・版 variant の 1 フレームを uint8 (H, W, 3) RGB で返す。ctx は変更しない。"""
    if variant not in VARIANTS:
        raise PipelineError(f"unknown variant {variant!r}; choose from {', '.join(VARIANTS)}")
    use_layers = ctx.has_layers and variant in ("layers", "both")
    active = light.levels(ctx.lights, t) if ctx.has_lights and variant in ("light", "both") else []
    linear = _layered(ctx, t, active) if use_layers else _flat(ctx, t, active)
    if ctx.look is not None:      # linear は ctx.assets.plate そのものでありうる（静止カメラ）。look は新しい配列を返す
        return fxlook.apply(ctx.look, linear, t)
    return to_srgb8(linear)


def _flat(ctx, t, active):
    image = ctx.assets.plate
    if active:
        add, roi = light.compose(active, image, ctx.size, None, "all")
        if add is not None:
            image = soft_add(image, add, roi)
    return camera.warp_with_depth(image, ctx.path.matrix(t, 1.0), ctx.size, ctx.assets.depth,
                                  ctx.path.depth_amplitude(t), ctx.path.direction)


def _layered(ctx, t, active):
    size, path, plate = ctx.size, ctx.path, ctx.assets.plate
    background, plate_lit, hosted, lit_add, lit_roi = ctx.background, None, {}, None, None
    if active:
        plane_masks = {p.mask_id for p in ctx.planes}
        for lt, _ in active:
            hosted.setdefault(lt.host, set()).add(lt.id)
        add, roi = light.compose(active, background, size, set(ctx.assets.masks) - plane_masks, hosted.get(None, set()))
        if add is not None:
            background = soft_add(background, add, roi)
        lit_add, lit_roi = light.compose(active, plate, size, None, None)
        if lit_add is not None:
            plate_lit = soft_add(plate, lit_add, lit_roi)
    spec_bg = ctx.spec.data["layers"]["background"]
    out = camera.warp_with_depth(background, path.matrix(t, spec_bg["parallax"]), size, ctx.assets.depth,
                                 path.depth_amplitude(t), path.direction)
    for item in ctx.stack:
        if isinstance(item, layers.Plane):
            glow = light.hosted_glow(active, plate, size, hosted[item.id], lit_add, lit_roi, plate_lit) \
                if item.id in hosted else None
            color, alpha = layers.plane_layer(item, path, t, size, plate_lit, glow)
            out = layers.over(out, color, alpha)
        elif isinstance(item, layers.Fog):
            color, alpha = layers.fog_layer(item, path, t, size)
            out = layers.over(out, color, alpha)
        else:
            out = layers.draw_particles(out, item, path, t, size)
    return out


# ---- 書き出し ---------------------------------------------------------------

def audio_for(ctx, start, n_frames):
    """write_video の audio 引数。offset_s + start から n_frames 分（= n/fps 秒）を切る。音声なしなら None。"""
    path = ctx.audio_path()
    if path is None:
        return None
    if not path.is_file():
        raise PipelineError(f"音声が見つかりません: {ctx.spec.data['audio']['path']}")
    return {"path": path, "start": ctx.spec.data["audio"]["offset_s"] + start, "duration": n_frames / ctx.fps}


def ffmpeg_command(out_path, size, fps, crf, n_frames, audio=None):
    width, height = size
    command = [media.ffmpeg(), "-y", "-hide_banner", "-nostdin", "-loglevel", "warning",
               "-thread_queue_size", "512", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
               "-framerate", f"{fps:g}", "-i", "pipe:0"]
    if audio:
        command += ["-ss", f"{audio['start']:.6f}", "-t", f"{audio['duration']:.6f}", "-i", str(audio["path"])]
    command += ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio else [])
    # ffmpeg 7.1（imageio-ffmpeg 同梱の Windows 版など）はフレームの色属性が出力オプションより優先されるため、setparams でも付ける
    command += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                "-sws_flags", "accurate_rnd+full_chroma_int",
                "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    if audio:
        fade_in = min(FADE_IN_S, audio["duration"] / 2)
        fade_out = min(FADE_OUT_S, audio["duration"] / 2)
        command += ["-af", f"afade=t=in:st=0:d={fade_in:.4f},afade=t=out:st={audio['duration'] - fade_out:.4f}:d={fade_out:.4f}",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    command += ["-frames:v", str(n_frames), "-movflags", "+faststart", str(out_path)]
    return command


def _close(stream):
    try:
        stream.close()
    except OSError:  # ffmpeg が先に終了していると、残りの書き込みの flush が BrokenPipeError になる
        pass


def write_video(out_path, frames, size, fps, crf, n_frames, audio=None, log_path=None):
    """フレーム列 (uint8 H×W×3) を n_frames ちょうど書く。-shortest は使わない。

    audio: None または {"path", "start", "duration"}（audio_for が作る）。
    一時名 (<stem>.part<suffix>) に書き、ffmpeg が成功し枚数が揃ったときだけ out_path へ置き換える。
    戻り値: {"frames", "command", "seconds"}。ffmpeg の警告と実行行は log_path に追記する。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_name(f"{out_path.stem}.part{out_path.suffix}")
    command = ffmpeg_command(partial, size, fps, crf, n_frames, audio)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a+b")
    else:
        log = tempfile.TemporaryFile()
    started = time.monotonic()
    written = 0
    error = None
    try:
        log.write(f"$ {shlex.join(command)}\n".encode())
        log.flush()
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        try:
            for image in frames:
                if image.shape != (size[1], size[0], 3) or image.dtype != np.uint8:
                    raise PipelineError(f"frame {written}: expected uint8 {size[1]}x{size[0]}x3, got {image.dtype} {image.shape}")
                proc.stdin.write(np.ascontiguousarray(image).data)
                written += 1
        except BrokenPipeError:
            error = "ffmpeg が途中で終了しました"
        except BaseException:
            proc.kill()
            _close(proc.stdin)
            proc.wait()
            raise
        _close(proc.stdin)
        code = proc.wait()
        if error is None and code != 0:
            error = f"ffmpeg が終了コード {code} で失敗しました"
        if error is None and written != n_frames:
            error = f"フレーム数が合いません（{written} 枚 / 期待 {n_frames} 枚）"
        if error:
            log.flush()
            log.seek(0)
            tail = log.read()[-2500:].decode("utf-8", "replace")
            raise PipelineError(f"{error}。ffmpeg の出力:\n{tail}")
        os.replace(partial, out_path)
    finally:
        log.close()
        if partial.exists():
            partial.unlink()
    return {"frames": written, "command": command, "seconds": round(time.monotonic() - started, 3)}


def render_variant(ctx, variant, out_path, start=0.0, duration=None, log_path=None, with_audio=True, progress=None):
    """1 つの版を区間 [start, start+duration) で MP4 に書く。progress(i, n) は 1 フレームごとに呼ばれる。"""
    n = ctx.n_frames(duration, start)

    def frames():
        for i in range(n):
            yield frame(ctx, timeline.frame_time(i, ctx.fps, start), variant)
            if progress:
                progress(i + 1, n)

    audio = audio_for(ctx, start, n) if with_audio else None
    result = write_video(out_path, frames(), ctx.size, ctx.fps, ctx.crf, n, audio, log_path)
    return {**result, "variant": variant, "start": start, "fps": ctx.fps, "size": list(ctx.size), "audio": audio is not None}


def save_png(image, path):
    """frame() の結果を PNG に保存する（確認用）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).save(path)


def library_versions():
    """manifest 用のライブラリ版（python・numpy・opencv・Pillow・ffmpeg）。"""
    try:
        first = subprocess.run([media.ffmpeg(), "-version"], capture_output=True, text=True, timeout=30).stdout.splitlines()[0]
    except (OSError, IndexError, subprocess.SubprocessError):
        first = "unknown"
    return {"python": platform.python_version(), "numpy": np.__version__, "opencv": cv2.__version__,
            "pillow": PIL.__version__, "ffmpeg": first}
