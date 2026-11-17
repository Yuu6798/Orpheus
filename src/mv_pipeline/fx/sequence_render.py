"""fx-timeline の描画: 書き出しの大きさ、動画のショットの読み手、描画の文脈（Context）、記録（timeline.json）、plan の表、run。
mv_pipeline.fx.sequence から以前の名前で読める。cv2 は関数の中でだけ読む。"""
import hashlib
import json
import shutil
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..io import PipelineError, sha256
from . import spec as fxspec, timeline as fxtimeline
from .sequence_spec import KEEP_FRAMES, KEEP_SHOTS, RECORD_SCHEMA, _overlap_tail, load_timeline, shot_name, shot_pulse
from .sequence_plan import blend, burn_at, finish, plan_frame, shot_index, video_positions


def output_size(data, scale=1.0):
    """出力の (W, H) × scale を偶数に丸めたもの（fx render と同じ丸め）。"""
    if not 0 < scale <= 4:
        raise PipelineError(f"scale は 0 より大きく 4 以下にしてください: {scale}")
    even = lambda v: max(16, 2 * int(round(v * scale / 2)))  # noqa: E731
    return even(data["output"]["width"]), even(data["output"]["height"])


class VideoFrames:
    """1 本の動画の読み手（dance の Source。タグが bt709 なら ffmpeg で行列を指定して読む）と、復号したコマの LRU。"""

    def __init__(self, path, keep=KEEP_FRAMES):
        from ..dance.clips import Source
        try:
            self.source = Source(path)
            self.count = self.source.count()
        except PipelineError as error:
            raise PipelineError(f"動画のショットの動画を読めません（{Path(path).name}）: {error}") from error
        self.fps, self.width, self.height = self.source.fps, self.source.width, self.source.height
        self.keep, self._frames = keep, OrderedDict()

    def get(self, index):
        if index in self._frames:
            self._frames.move_to_end(index)
            return self._frames[index]
        frame = self.source.read(index)
        self._frames[index] = frame
        while len(self._frames) > self.keep:
            self._frames.popitem(last=False)
        return frame

    def at(self, pos):
        """位置 pos（小数のコマ番号）の uint8 RGB。実制作の video_frame と同じ: 0〜コマ数 − 1.001 に止め、
        小数の部分が 0.001 以上なら前後のコマを cv2.addWeighted で混ぜる。"""
        import cv2
        pos = min(max(pos, 0), self.count - 1.001)
        first = int(pos)
        weight = pos - first
        if weight < 1e-3:
            return self.get(first)
        return cv2.addWeighted(self.get(first), 1 - weight, self.get(min(first + 1, self.count - 1)), weight, 0)

    def close(self):
        self._frames.clear()
        self.source.close()


class VideoShot:
    """動画のショットの描き手。frame(local_t) は出力の大きさの uint8 RGB（fx の静止画と同じ道筋）。"""

    def __init__(self, timeline, index, size):
        from . import camera
        from .assets import CROP_WARN_FRACTION, _pixel_box, cover_box

        shot = timeline.data["shots"][index]
        video, loop = shot["video"], shot["video"]["loop"]
        keep = KEEP_FRAMES if loop is None else max(KEEP_FRAMES, loop["frames"][1] - loop["frames"][0] + 2)
        self.frames = VideoFrames(timeline.resolve(video["path"]), keep)
        self.video, self.size = video, size
        self.fps = video["fps"] or self.frames.fps
        width, height = self.frames.width, self.frames.height
        length = shot["end"] - shot["start"]
        tail, kind = _overlap_tail(timeline.data["shots"], index)        # 次の xfade・burn の間も、このショットを描き続ける
        self.warnings, self.faces = [], shot["faces"]
        if shot["camera"]["frame"] == "source":     # 動画の全体が板（fx-shot の camera.frame: source と同じ）
            self.box = (0.0, 0.0, 1.0, 1.0)
            cover = max(size[0] / width, size[1] / height)
            self.plate_size = (width, height) if cover >= 1 else (max(1, int(round(width * cover))), max(1, int(round(height * cover))))
            if cover > 1:
                self.warnings.append(f"動画 {width}x{height} は、zoom 1 の視野（出力 {size[0]}x{size[1]} の縦横比）でも出力より小さく、"
                                     f"{cover:.2f} 倍に拡大されます")
        else:
            self.box, self.plate_size = _pixel_box(cover_box(width, height, *size), (width, height)), None
            cut = 1.0 - (self.box[2] - self.box[0]) * (self.box[3] - self.box[1])
            if cut > CROP_WARN_FRACTION:
                axis = "左右" if self.box[0] > 0 else "上下"
                self.warnings.append(f"動画 {width}x{height} は出力 {size[0]}x{size[1]} と縦横比が違うため、cover で中央に合わせて{axis}を計 {cut * 100:.1f}% 切りました")
        self.path = camera.CameraPath(shot["camera"], length + tail, size, self.box, plate=self.plate_size,   # 視野の確かめも、次の xfade・burn の間まで見る
                                      pulse=shot_pulse(timeline.data, index))
        last = self.frames.count - 1
        if loop is not None and loop["frames"][1] > last:
            self.close()
            raise PipelineError(f"fx timeline: shots[{index}].video.loop の終わり {loop['frames'][1]} が、動画の最後のコマ {last} を越えています")
        if loop is None:
            end = (video["offset_s"] + (length + tail) * video["speed"]) * self.fps
            if end > last:
                held = (last / self.fps - video["offset_s"]) / video["speed"]
                self.warnings.append(f"動画の最後のコマ {last} を越えて描くため、ショットの {max(held, 0.0):.2f} 秒から後は最後のコマのままです"
                                     + (f"（次のショットの {kind} の間も描く分を含む）" if tail else ""))
        bounds = self.path.out_of_bounds()
        if bounds:
            self.warnings.append(bounds)

    def source_frame(self, local_t):
        """カメラの前の動画の 1 コマ（uint8 RGB、動画の大きさ）。"""
        positions = video_positions(self.video, local_t, self.fps)
        if self.video["loop"] is None:
            return self.frames.at(positions[0][0])
        total = None
        for pos, weight in positions:
            part = self.frames.at(pos).astype(np.float32)
            total = part * weight if total is None else total + part * weight
        return np.clip(total, 0, 255).astype(np.uint8)

    def frame(self, local_t):
        from . import camera
        from .assets import _crop_resize
        from .color import to_linear, to_srgb8
        plate = to_linear(_crop_resize(self.source_frame(local_t), self.box, self.plate_size or self.size))
        return to_srgb8(camera.warp_with_depth(plate, self.path.matrix(local_t, 1.0), self.size, None, None))

    def close(self):
        self.frames.close()


class Context:
    """timeline を描く状態。ショットの描き手（fx の Context か VideoShot）は初めて使うときに作り、新しい順に KEEP_SHOTS 個まで持つ
    （spec が同じ spec のショットは共有する）。描き終えたら close() で動画の読み手を閉じる。"""

    def __init__(self, timeline, scale=1.0, hooks=None):
        from . import look as fxlook
        from . import render
        self.timeline, self.data = timeline, timeline.data
        self.size = output_size(self.data, scale)
        self.fps = self.data["output"]["fps"]
        self.look = None if self.data["look"] is None else fxlook.prepare(self.data["look"], self.size, self.fps)
        self.warnings = list(timeline.warnings)
        self._render, self._contexts, self._warned = render, OrderedDict(), set()
        self._pulsed = OrderedDict()           # 拍の脈があるとき、spec のショットごとの写し（camera だけ違い、素材は共有）
        self._burns = OrderedDict()            # burn の場（ショットの番号 → 出力の大きさの N）。設定と大きさだけで決まる作り置き
        self.hooks = hooks                     # fx/hooks.py の Hooks か None（--hooks を渡したときだけ）
        if hooks is not None:
            hooks.prepare(self)

    def shot(self, index):
        shot = self.data["shots"][index]
        if shot["kind"] == "spec" and self.data["pulse"] is not None:
            # 脈はショットの時刻で決まるので、同じ spec のショットも camera だけショットごとに分ける
            if index in self._pulsed:
                self._pulsed.move_to_end(index)
                return self._pulsed[index]
            ctx = self._shared(index)
            self._pulsed[index] = replace(ctx, path=ctx.path.with_pulse(shot_pulse(self.data, index)))
            while len(self._pulsed) > KEEP_SHOTS:
                self._pulsed.popitem(last=False)
            return self._pulsed[index]
        return self._shared(index)

    def _shared(self, index):
        shot = self.data["shots"][index]
        name = shot["spec"] if shot["kind"] == "spec" else f"video:{index}"
        if name in self._contexts:
            self._contexts.move_to_end(name)
            return self._contexts[name]
        if shot["kind"] == "spec":
            ctx = self._render.prepare(self.timeline.specs[index], size=self.size)
        else:
            ctx = VideoShot(self.timeline, index, self.size)
        if name not in self._warned:
            self._warned.add(name)
            self.warnings += [f"{shot_name(shot)}: {w}" for w in ctx.warnings]
        self._contexts[name] = ctx
        while len(self._contexts) > KEEP_SHOTS:
            _, old = self._contexts.popitem(last=False)
            if isinstance(old, VideoShot):
                old.close()
        return ctx

    def image(self, index, local, t=None):
        """ショット index の、ショットの中の時刻 local の画（出力の大きさ）。hooks の shot が無ければ uint8 RGB、あれば
        それを掛けた float32 の表示の値（t は timeline の時刻。hooks に渡す）。"""
        shot = self.data["shots"][index]
        if shot["kind"] == "video":
            image = self.shot(index).frame(local)
        else:
            image = self._render.frame(self.shot(index), local, shot["variant"])
        if self.hooks is not None and self.hooks.has("shot"):
            image = self.hooks.shot(image.astype(np.float32), t, self.view(index, local))
        return image

    def view(self, index, local):
        """ショット index の、ショットの中の時刻 local の見え方（hooks の shot に渡す fx/view.py の View）。"""
        from .view import View
        shot, ctx = self.data["shots"][index], self.shot(index)
        if shot["kind"] == "video":
            return View(ctx.path, ctx.box, ctx.plate_size, self.size, local, index, "video", shot["video"]["path"])
        return View(ctx.path, ctx.assets.box, ctx.assets.plate_size, self.size, local, index, "spec", shot["spec"], ctx.assets.depth)

    def burn_field(self, index):
        """ショット index の burn の場（float32 (H, W)、0..1）。初めて使うときに作り、新しい順に KEEP_SHOTS 個まで持つ。"""
        if index in self._burns:
            self._burns.move_to_end(index)
            return self._burns[index]
        from . import burn as fxburn
        self._burns[index] = fxburn.field(self.data["shots"][index]["transition_in"], self.size)
        while len(self._burns) > KEEP_SHOTS:
            self._burns.popitem(last=False)
        return self._burns[index]

    def view_at(self, t):
        """timeline の時刻 t のショット（xfade・burn の間は入ってくるショット）の見え方。hooks の final で、つないだ後の画に掛ける
        演出（空の奥の色の深度など）に使う。"""
        index = shot_index(self.data, t)
        shot = self.data["shots"][index]
        return self.view(index, shot["offset_s"] + (t - shot["start"]))

    def frame(self, t):
        layers, dim, flash = plan_frame(self.data, t)
        burning = burn_at(self.data, t)
        burn = None if burning is None else (burning[1], burning[2], self.burn_field(burning[0]))
        image = blend([(self.image(i, local, t), weight) for i, local, weight in layers], dim, flash, burn)
        if self.hooks is not None and self.hooks.has("final"):
            image = self.hooks.final(image, t, self)
        return finish(image, self.look, t)

    def faces_at(self, t):
        """時刻 t のショット（xfade・burn の間は入ってくるショット）の顔の枠を、camera で画面の正規化座標へ写したもの。
        [{box, role, estimated}]。画面の外に出た所は切り、全部外なら入れない。深度視差は入れない（実制作と同じ）。"""
        from . import camera
        index = shot_index(self.data, t)
        shot = self.data["shots"][index]
        faces = self.timeline.specs[index].data["faces"] if shot["kind"] == "spec" else shot["faces"]
        if not faces:
            return []
        local, path, out = shot["offset_s"] + (t - shot["start"]), self.shot(index).path, []
        for face in faces:
            box = [min(max(v, 0.0), 1.0) for v in camera.screen_box(path, local, face["box"])]
            if box[2] > box[0] and box[3] > box[1]:
                out.append({"box": [round(v, 5) for v in box], "role": face["role"], "estimated": face["estimated"]})
        return out

    def close(self):
        for ctx in self._contexts.values():
            if isinstance(ctx, VideoShot):
                ctx.close()
        self._contexts.clear()
        self._pulsed.clear()
        self._burns.clear()


def timeline_record(timeline, size, start=0.0, duration=None, frames=None):
    """mv gate check が読む timeline.json（dance-timeline/0.1 の形 + seam_info・flash_info・beats。字幕は無い）。切れ目はショットの頭、フラッシュは flash のつなぎ。
    interval は描いた区間（dance の記録と同じく、長さは頼まれた長さ）。"""
    data = timeline.data
    fps, total, shots = data["output"]["fps"], data["duration_s"], data["shots"]
    length = total - start if duration is None else duration
    count = fxtimeline.frame_count(length, fps) if frames is None else frames
    flashes = [shot for shot in shots if shot["transition_in"]["type"] == "flash"]
    beats = data["beats"]
    return {"schema_version": RECORD_SCHEMA, "size": list(size), "fps": fps, "duration_s": total,
            "interval": {"start": start, "duration": length, "frames": count}, "lyric_fade": "inside", "lyrics": [],
            "ui_regions": [], "beats": None if beats is None else {"beats": beats["beats"], "downbeats": beats["downbeats"]},
            "clips": [{"path": shot_name(shot), "start": shot["start"], "end": shot["end"], "kind": "fx-shot" if shot["kind"] == "spec" else "video",
                       "transition_in": shot["transition_in"], "beat_exempt": shot["beat_exempt"]} for shot in shots],
            "seams": [shot["start"] for shot in shots[1:]], "flashes": [shot["start"] for shot in flashes],
            "seam_info": [{"t": shot["start"], "from": index, "to": index + 1, "kind": shot["transition_in"]["type"],
                           "beat_exempt": shot["beat_exempt"], "align": shot["align"]} for index, shot in enumerate(shots[1:])],
            "flash_info": [{"t": shot["start"], "strength": shot["transition_in"]["strength"], "decay_s": 1 / shot["transition_in"]["rate"],
                            "window_s": shot["transition_in"]["window_s"], "mode": "lerp_display", "beat_exempt": shot["beat_exempt"],
                            "align": shot["align"]} for shot in flashes]}


def _describe_video(video):
    loop = video["loop"]
    text = f"video ×{video['speed']:g}" + (f" {video['fps']:g}fps" if video["fps"] else "")
    if loop is None:
        return text + (f" +{video['offset_s']:g}s" if video["offset_s"] else "")
    # 「〜」は cp932 でも書ける（Windows でパイプやファイルへ出すときの既定の符号化。「–」は書けずに止まる）
    return text + f" loop {loop['frames'][0]}〜{loop['frames'][1]}" + (f" mix {loop['mix']:g}" if loop["mix"] > 0 else "")


def plan_text(path):
    """`mv fx timeline plan`: ショットとつなぎの表（秒・いちばん近い拍）。描かない。"""
    timeline = load_timeline(path)
    data = timeline.data
    beats = data["beats"]["beats"] if data["beats"] else None

    def beat_of(t):
        if beats is None:
            return "-"
        index = int(np.argmin(np.abs(np.asarray(beats) - t)))
        return f"{index}{t - beats[index]:+.3f}"

    out, pulse = data["output"], data["pulse"]
    lines = [f"# {timeline.source_schema}  {out['width']}x{out['height']} {out['fps']:g}fps {data['duration_s']:g} 秒  ショット {len(data['shots'])}"
             f"  look {'あり' if data['look'] else 'なし'}  音声 {data['audio']['path'] if data['audio'] else 'なし'}"]
    if pulse is not None:
        where = "全体" if pulse["sections"] is None else "、".join(f"{a:.3f}-{b:.3f}" for a, b in pulse["sections"])
        lines.append(f"拍の脈: zoom +{pulse['amount'] * 100:g}%（減り方 {pulse['decay']:g}/秒）  区間 {where}")
    lines += ["", f"{'#':<3} {'start':>8} {'end':>8} {'拍':>10}  {'つなぎ':<18} {'offset':>7}  ショット"]
    for index, shot in enumerate(data["shots"]):
        transition = shot["transition_in"]
        text = transition["type"] + (f" {transition['duration_s']:g}s" if "duration_s" in transition else "") \
            + (f" {transition['strength']:g}" if transition["type"] == "flash" else "")
        moved = f"  (snap {shot['requested']:.3f}→)" if shot["snap"] and shot["start"] != shot["requested"] else ""
        name = shot["spec"] if shot["kind"] == "spec" else f"{shot['video']['path']}（{_describe_video(shot['video'])}）"
        lines.append(f"{index:<3} {shot['start']:>8.3f} {shot['end']:>8.3f} {beat_of(shot['start']):>10}  {text:<18} "
                     f"{shot['offset_s']:>7.3f}  {name}{moved}")
    lines += [f"注意: {w}" for w in timeline.warnings]
    return "\n".join(lines)


def _meter(say, total):
    began, every = time.monotonic(), max(1, round(total / 20))

    def report(done):
        if say is not None and (done % every == 0 or done == total):
            spent = time.monotonic() - began
            say(f"[fx timeline] {done}/{total} コマ 経過 {spent:.1f} 秒 残り約 {spent / done * (total - done):.1f} 秒")
    return report


def _shot_records(timeline):
    from . import assets as fxassets
    from . import spec_layers
    records = []
    for shot, sp in zip(timeline.data["shots"], timeline.specs):
        if sp is None:
            records.append({"video": shot["video"]["path"], "sha256": sha256(timeline.resolve(shot["video"]["path"]))})
            continue
        record = {"spec": shot["spec"], "sha256": sp.sha256, "assets": {role: item["sha256"] for role, item in fxassets.describe(sp).items()}}
        if sp.data["overlays"]:
            record["overlays"] = spec_layers.describe(sp)
        records.append(record)
    return records


def render_run(path, out=None, start=0.0, duration=None, scale=1.0, force=False, progress=None, quiet=False, hooks=None):
    """timeline.mp4 と timeline.json を run として出す（fx の run と同じ記録・再利用・構造検査）。hooks は演出の hooks のパス
    （fx/hooks.py。明示したときだけ実行する。sha256 が key と manifest に入る）。"""
    from .. import fx
    from . import probe, render, runs
    from .hooks import Hooks

    began = time.monotonic()
    say = progress or (lambda message: None)
    timeline = load_timeline(path)
    data = timeline.data
    fps, total = data["output"]["fps"], data["duration_s"]
    start = float(start)
    length = total - start if duration is None else float(duration)
    if not start >= 0:
        raise PipelineError(f"--start は 0 以上にしてください: {start}")
    if not length > 0 or start + length > total + 1e-6:
        raise PipelineError(f"区間 {start:g}〜{start + length:g} 秒が timeline の長さ 0〜{total:g} 秒に収まりません")
    n_frames = fxtimeline.frame_count(length, fps)
    size = output_size(data, scale)
    shots = _shot_records(timeline)
    audio_path = timeline.resolve(data["audio"]["path"]) if data["audio"] else None
    if audio_path is not None and not audio_path.is_file():
        raise PipelineError(f"音声が見つかりません: {data['audio']['path']}")
    libraries = render.library_versions()
    effects = None if hooks is None else Hooks(hooks)
    payload = {"timeline": data, "shots": shots, "engine": fx.fingerprint(), "audio": sha256(audio_path) if audio_path else None,
               "start": start, "duration": length, "frames": n_frames, "size": list(size), "scale": float(scale), "libraries": libraries}
    if effects is not None:
        payload["hooks"] = effects.record["sha256"]
    key = runs.content_key_for("timeline", payload)
    out = (Path(out) if out else timeline.base_dir / "fx_runs").resolve()
    has_faces = any((sp.data["faces"] if sp is not None else shot["faces"]) for shot, sp in zip(data["shots"], timeline.specs))

    def has_record(run_dir, manifest):
        """再利用は、timeline.json（と、顔の枠を書いた run なら faces.json）が残っているときだけ。"""
        return (Path(run_dir) / "timeline.json").is_file() and (not manifest.get("faces") or (Path(run_dir) / manifest["faces"]).is_file())

    with runs.Session(out, key, "timeline", "render.log", say=say, tag="[fx timeline]", force=force, accept_reuse=has_record) as session:
        if session.reused:
            return _summary(session.reused_dir, session.reused, reused=True)
        run = session.run
        shutil.copyfile(timeline.path, run.dir / "fx_timeline.json")
        run.manifest.update(
            timeline={"file": "fx_timeline.json", "sha256": timeline.sha256,
                      "normalized_sha256": hashlib.sha256(fxspec.canonical_json(data).encode("utf-8")).hexdigest()},
            shots=shots, engine=runs.engine_record(fx, "fx"), libraries=libraries,
            interval={"start": start, "duration": length, "fps": fps, "frames": n_frames},
            output={"size": list(size), "scale": float(scale), "crf": data["output"]["crf"]},
            look="applied" if data["look"] else "absent", faces=None, hooks=None if effects is None else effects.record,
            variants={}, grid=None, warnings=[])
        run.save()
        run.log(f"timeline start key={key} shots={len(data['shots'])} start={start:g} duration={length:g} frames={n_frames} "
                f"size={size[0]}x{size[1]}" + (f" hooks={effects.record['path']}" if effects else ""))
        ctx = Context(timeline, scale, effects)
        audio = None if audio_path is None else {"path": audio_path, "start": data["audio"]["offset_s"] + start, "duration": n_frames / fps}
        video = run.dir / "timeline.mp4"
        meter = _meter(None if quiet else say, n_frames)

        faces = [] if has_faces else None

        def frames():
            for i in range(n_frames):
                t = fxtimeline.frame_time(i, fps, start)
                yield ctx.frame(t)
                if faces is not None:          # mv gate check の faces（[{start, end, box, role, estimated}]、box は画面の正規化座標）
                    faces.extend({"start": round(t, 6), "end": round(t + 1 / fps, 6), **face} for face in ctx.faces_at(t))
                meter(i + 1)

        try:
            result = render.write_video(video, frames(), size, fps, data["output"]["crf"], n_frames, audio, run.log_path)
        finally:
            ctx.close()
        record = timeline_record(timeline, size, start, length, n_frames)
        if faces is not None:
            (run.dir / "faces.json").write_text(json.dumps(faces, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            run.manifest["faces"] = "faces.json"
        (run.dir / "timeline.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        checks = probe.check_video(video, width=size[0], height=size[1], fps=fps, frames=n_frames, audio=audio is not None,
                                   audio_s=n_frames / fps if audio else None, bt709=True)
        run.manifest["warnings"] = list(ctx.warnings)
        for warning in ctx.warnings:
            run.log(f"warning: {warning}")
        run.manifest["variants"]["timeline"] = {"path": video.name, "frames": checks["frames"], "sha256": sha256(video),
                                                "technical_pass": checks["pass"], "checks": checks, "render_seconds": result["seconds"],
                                                "timeline_json": "timeline.json"}
        run.save()
        run.log(f"timeline: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
        say(f"[fx timeline] {checks['frames']} フレーム（{result['seconds']:.1f} 秒）technical_pass={checks['pass']}")
        if not checks["pass"]:
            message = f"技術検査に失敗しました（{json.dumps(checks['failures'], ensure_ascii=False)[:800]}）。詳細: {run.dir / 'manifest.json'}"
            run.finish(False, message)
            raise PipelineError(message)
        session.succeed()
        run.log(f"timeline done in {time.monotonic() - began:.1f}s")
    summary = _summary(run.dir, run.manifest, reused=False)
    summary["seconds"] = round(time.monotonic() - began, 1)
    return summary


def _summary(run_dir, manifest, reused):
    run_dir = Path(run_dir)
    item = manifest["variants"]["timeline"]
    return {"run_id": manifest["run_id"], "run_dir": str(run_dir), "reused": reused, "status": manifest["status"],
            "technical_pass": manifest["technical_pass"], "interval": manifest["interval"], "size": manifest["output"]["size"],
            "look": manifest.get("look"), "video": str(run_dir / item["path"]), "timeline": str(run_dir / item["timeline_json"]),
            "faces": str(run_dir / manifest["faces"]) if manifest.get("faces") else None, "hooks": manifest.get("hooks"),
            "warnings": manifest["warnings"]}
