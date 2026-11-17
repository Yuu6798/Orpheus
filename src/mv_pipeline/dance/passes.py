"""要素別の描画（model3d の render.passes）を重ねて、1 本の動画にする（`mv dance passes`。docs/dance.md の「要素別の描画を重ねる」）。

1 コマの重ね方（表示の値。sRGB に符号化した 0〜1 の float32。リポジトリ外の試作の重ね合わせの台本と同じ順）:

    部屋（room）→ ぼかし（room.blur。区間ごとの σ）→ 明るさ（room.level）→ 足もとの円（disc）の影（finish.halo）
    → 別の世界への切り替え（world。空・床の光のたまり pool・焼け抜けは fx.burn。space.world）
    → 床への映り込み（rchar・rph<i>・小さな絵。reflection。space.mirror・space.ripple）→ 遠い小さな絵（sprites。space.sprite_layers・
    over_glow）→ 奥の分身（ph<i>。phantoms。space.phantom）
    人物（char の色・アルファ）→ 灯りの色で染める（shade と head。finish.face_shade・finish.light_tint）→ 輪郭の光（finish.rim_light）
    重ね（finish.over）→ 手前の分身 → 近い小さな絵 → 衝撃（impact。space.impact）→ にじみ（finish.stage_bloom）→ 閃光（fx.fire.screen）→ 明るさ（level）→ look（周辺の減光・粒。fx の look）

既存の部品（dance の finish・cues、fx の fire と look、encode.write_video）を呼ぶ。新しく足したのは、区間ごとの部屋のぼかし
（finish.blur と、区間の値を引く segment_value）と、灯りの色で人物を染める部品（finish.light_tint・finish.face_shade）。
時刻はコマ T の (T − 1) ÷ fps 秒。長さ（画素）は幅 1080 の画面での値で、コマの幅に合わせて掛ける。

中身は継ぎ目ごとのモジュールにある（どれも dance の中で、dance の指紋に入る）: passes_spec（版・既定の値・区間とキーと色の
読み方・spec の読み込みと埋め）、passes_scene（カメラの控えの空間・場面（板・柱・ワイプ）・要素の一覧の読み込み）、passes_layers（コマ
ごとの重ね方と仕上げ）。run（audio_plan・default_out・render_run）はここに残す（テストが audio_plan を差し替える）。以前の名前は、
下線付きも、すべてこのモジュールから読める。
"""
# 以前どおり読めるように、元の import も残す（このモジュールでは使わないものもある）
import json  # noqa: F401
import re  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from pathlib import Path  # noqa: F401

import jsonschema  # noqa: F401
import numpy as np  # noqa: F401

from ..fx import spec as fxspec  # noqa: F401
from ..io import PipelineError, read_json, read_json_file, schema_lines, sha256  # noqa: F401
from . import cues as dancecues  # noqa: F401
from . import space  # noqa: F401

from .passes_spec import (  # noqa: F401（以前の名前。passes_spec のもの）
    SCHEMA_VERSION, SCHEMA_FILE, SPRITES_SCHEMA, KINDS, PHANTOM_KIND, FLOOR_SHADOW, FLASH_COLOR, schema, _check_keys, _segments,
    segment_value, level_at, clock_at, _level, _signed, _color, _color_cache, color_at, _phantoms, _world, _effects, CARD_KEYS,
    _cards, _wipes, _impact, _sprites, _reflection, normalize, parse_spec, load_spec)
from .passes_scene import (  # noqa: F401（以前の名前。passes_scene のもの）
    Space, load_space, Scene, load_scene, card_layer, Plan, load_passes, read_image)
from .passes_layers import (  # noqa: F401（以前の名前。passes_layers のもの）
    sprite_layers, reflection_layer, phantom_layer, layers, NEXT, wipe_at, wipe_layer, finish_frame, compose, _same_size)


def audio_plan(path, offset, seconds, warnings):
    """write_video の音（T = 1 が音の offset 秒）。音が動画より短ければ、足りない分を無音で足して動画の長さにそろえ、warnings に残す
    （そろえないと、重ね終えた後の技術検査で音の長さが合わずに止まる）。読み始めより音が短ければ止める。"""
    if path is None:
        return None
    from ..fx import probe
    plan = {"path": path, "start": offset, "duration": seconds}
    available = probe.media_seconds(path)
    if available is None:
        return plan
    remaining = available - offset
    if remaining <= 0:
        raise PipelineError(f"音の読み始め（{offset:g} 秒）が音の長さ（{available:g} 秒）より後です: {path}")
    if remaining < seconds:
        warnings.append(f"audio: 音が動画より {seconds - remaining:.3f} 秒短いので、後ろを無音で足します")
        plan.update(length=remaining, pad=True)
    return plan


def default_out(spec):
    return spec.base_dir / "dance_runs"


def render_run(spec_path, out=None, force=False, progress=None, quiet=False):
    """spec の要素を重ねて dance.mp4 を描き、run として記録する（run の形と版の名前は mv dance render と同じなので、mv dance accept が
    そのまま使える）。同じ入力で成功済みなら再利用する。"""
    import shutil
    import time

    from .. import dance
    from ..fx import look as fxlook
    from ..fx import probe
    from ..fx import render as fxrender
    from ..fx import runs as fxruns
    from . import VARIANT, fingerprint
    from .runs import _meter

    began = time.monotonic()
    say = progress or (lambda message: None)
    spec = load_spec(spec_path)
    data = spec.data
    passes_path = spec.resolve(data["passes"])
    plan = load_passes(passes_path)
    if data["figure"] is not None and "shade" not in plan.files:
        raise PipelineError("figure（灯りの色で染める）には、要素の一覧に shade（陰影）が要ります")
    if data["figure"] is not None and len(plan.files["shade"]) != len(plan.frames):
        raise PipelineError("figure（灯りの色で染める）には、人物を描いたどのコマにも shade（陰影）が要ります（一覧の outside にしない）")
    if data["reflection"] is not None and data["reflection"]["char"] is not None and "rchar" not in plan.files:
        raise PipelineError("reflection.char（人物の映り込み）には、要素の一覧に rchar（床で反転したカメラで描いた人物）が要ります")
    if data["effects"] is not None and "fx" not in plan.files:
        raise PipelineError("effects（効果）には、要素の一覧に fx（model3d の render.passes の fx で描いた効果）が要ります")
    if data["reflection"] is not None and data["reflection"]["fx"] is not None and "rfx" not in plan.files:
        raise PipelineError("reflection.fx（効果の映り込み）には、要素の一覧に rfx（床で反転したカメラで描いた効果）が要ります")
    if data["phantoms"] is not None and not plan.phantoms():
        raise PipelineError("phantoms（分身）には、要素の一覧に ph<i>（model3d の render.passes の phantoms で描いた分身）が要ります")
    where = load_space(spec, plan)
    if data["cards"] and where is None:
        raise PipelineError("cards（手前の板）には、spec の cameras（コマごとのカメラの控え）が要ります")
    scene = load_scene(spec, plan)
    notes = list(spec.warnings)
    if data["figure"] is not None and data["figure"]["face_smooth"] > 0 and "head" not in plan.files:
        notes.append("figure.face_smooth: 要素の一覧に head（顔の範囲）が無いので、顔の陰影はならしません")
    width, height = plan.size
    if width % 2 or height % 2:
        raise PipelineError(f"コマの大きさは偶数にしてください（H.264 の 4:2:0）: {width}×{height}")
    n_frames = len(plan.frames)
    audio_path = spec.resolve(data["audio"]["path"]) if data["audio"] is not None else None
    if audio_path is not None and not audio_path.is_file():
        raise PipelineError(f"音声が見つかりません: {data['audio']['path']}")
    assets = {"passes": {"path": data["passes"], "sha256": sha256(passes_path)}}
    if data["cameras"] is not None:
        assets["cameras"] = {"path": data["cameras"], "sha256": sha256(spec.resolve(data["cameras"]))}
    if data["sprites"] is not None:
        for n, name in enumerate([data["sprites"]["items"], *data["sprites"]["images"]]):
            assets[f"sprites_{n}"] = {"path": name, "sha256": sha256(spec.resolve(name))}
    names = [data["references"]] if data["references"] is not None else []
    names += [card["image"] for card in data["cards"] or []]
    for wipe in data["wipes"] or []:
        names += [wipe["passes"], wipe["pillar"]["image"]] + ([wipe["cameras"]] if wipe["cameras"] is not None else [])
    for n, name in enumerate(names):
        assets[f"scene_{n}"] = {"path": name, "sha256": sha256(spec.resolve(name))}
    if audio_path is not None:
        assets["audio"] = {"path": data["audio"]["path"], "sha256": sha256(audio_path)}
    libraries = fxrender.library_versions()
    key = fxruns.content_key_for("dance-passes", {"spec": data, "assets": {r: a["sha256"] for r, a in assets.items()},
                                                  "engine": fingerprint(), "libraries": libraries})
    out = (Path(out) if out else default_out(spec)).resolve()
    with fxruns.Session(out, key, "dance-passes", "render.log", say=say, tag="[dance]", force=force) as session:
        if session.reused:
            return fxruns.summarize(session.reused_dir, session.reused, reused=True)
        run = session.run
        shutil.copyfile(spec.path, run.dir / "spec.json")
        seconds = n_frames / plan.fps
        run.manifest.update(
            spec=fxruns.spec_record(spec), assets=assets, engine=fxruns.engine_record(dance, "dance"), libraries=libraries,
            # 最初のコマの時刻（T = 1 が 0 秒）。fx の比較（compare_run）などが、ここから基準の動画のコマを選ぶ
            interval={"start": (plan.frames[0] - 1) / plan.fps, "duration": seconds, "fps": plan.fps, "frames": n_frames,
                      "T": [plan.frames[0], plan.frames[-1]]},
            output={"size": [width, height], "scale": 1.0, "crf": data["output"]["crf"]}, passes=sorted(plan.files),
            variants={}, grid=None, warnings=notes, look=data["look"] is not None)
        run.save()
        run.log(f"passes start key={key} frames={n_frames} size={width}x{height} passes={sorted(plan.files)}")
        look = fxlook.prepare(data["look"], plan.size, plan.fps) if data["look"] is not None else None
        frames_dir = run.dir / "frames" if data["output"]["frames"] else None
        if frames_dir is not None:
            frames_dir.mkdir()
        meter = _meter(None if quiet else progress, n_frames)

        def frames():
            from ..fx import compare
            for i, T in enumerate(plan.frames):
                image = compose(data, plan, T, look, where, scene)
                if frames_dir is not None:
                    compare.save_png_atomic(image, frames_dir / f"f{T:04d}.png")
                yield image
                meter(i + 1)

        # 音の読み始め: T = 1 が offset_s 秒なので、一覧の最初の T のぶん先から読む（T が 1 から始まらない一覧でも絵と合う）
        start = (data["audio"]["offset_s"] + (plan.frames[0] - 1) / plan.fps) if audio_path is not None else 0.0
        audio = audio_plan(audio_path, start, seconds, run.manifest["warnings"])
        path = run.dir / f"{VARIANT}.mp4"
        result = fxrender.write_video(path, frames(), plan.size, plan.fps, data["output"]["crf"], n_frames, audio, run.log_path)
        checks = probe.check_video(path, width=width, height=height, fps=plan.fps, frames=n_frames, audio=audio is not None,
                                   audio_s=seconds if audio else None, bt709=True)
        run.manifest["variants"][VARIANT] = {"path": path.name, "frames": checks["frames"], "sha256": sha256(path),
                                              "technical_pass": checks["pass"], "checks": checks, "render_seconds": result["seconds"]}
        run.save()
        run.log(f"{VARIANT}: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
        say(f"[dance] {checks['frames']} フレーム（{result['seconds']:.1f} 秒）technical_pass={checks['pass']}")
        if not checks["pass"]:
            message = f"技術検査に失敗しました（{checks['failures']}）。詳細: {run.dir / 'manifest.json'}"
            run.finish(False, message)
            raise PipelineError(message)
        session.succeed()
    result = fxruns.summarize(run.dir, run.manifest, reused=False)
    result["frames_dir"] = str(frames_dir) if frames_dir is not None else None
    result["seconds"] = round(time.monotonic() - began, 1)
    return result
