"""fx-timeline/0.2: 複数のショットを時刻順に並べ、つなぎで 1 本の動画にする（実験的。docs/fx.md）。

0.2 は 0.1 につなぎの焼け抜け（burn）を足した版。0.1 の timeline は 0.1 の schema（fx_timeline_0_1.schema.json）で検証して
そのまま読む（正規形は 0.2 と同じ形で、絵は同じ）。0.1 の timeline に burn を書くと拒否する。

ショットは start の昇順で、次のショットの start（最後は duration_s）まで。ショットは 2 種類:

- spec のショット: fx render の 1 コマ（出力の大きさで描く。spec の look と overlays 込みの 8 ビットの RGB）を、
  ショットの中の時刻 offset_s + (t − start) で描く。spec の audio は使わない（音は timeline の audio。読み込みで外すので、
  素材の確かめ・記録・key にも入らない）。
- 動画のショット（video）: 動画のコマを、fx の静止画と同じ道筋（cover で中央を切り出して出力の大きさに → 線形 → camera →
  sRGB）で描く。camera は fx-shot の keys と同じ書き方で、時刻はショットの中の秒 t − start。コマ位置は実制作のレンダラと同じ式:

      位置 = (offset_s + (t − start)·speed)·fps                     （fps は既定でファイルの fps）
      loop: τ = (t − start) + start_s、p = (τ·fps·speed) mod (区間 − crossfade_frames)。p < crossfade_frames の間は
            区間の頭（重み p/crossfade_frames）と尻尾を重ねる。mix > 0 なら τ·mix_speed + mix_offset_s のもう 1 本を重み mix で足す

  小数の位置は前後のコマを cv2.addWeighted で混ぜ（端は最後から 0.001 コマ手前で止める）、loop の重ねは float で足して切り捨てる。

つなぎは表示の値で、xfade・black・flash の式と順番は実制作のレンダラと同じ（ease は smoothstep）:

    xfade   t < start + duration_s の間、前のショットを描き続けて 前·(1 − a) + 今·a（a = ease((t − start)/duration_s)）
    black   t < start + duration_s の間、今·ease((t − start)/duration_s)（黒から上げる）
    flash   t − start < window_s の間、今 + (色 − 今)·strength·exp(−(t − start)·rate)（既定 0.75・(235, 210, 255)・0.30 秒・13）
    burn    t < start + duration_s の間、前のショットを描き続けて、ノイズの形に焼け抜ける（0.2。進み p = (t − start)/duration_s は直線。
            窓の置き方と色空間は xfade と同じ。式は fx/burn.py）

つないだ後に timeline の look（fx-shot と同じ grade・vignette・grain。時刻は timeline の t）を掛け、0〜255 に切って切り捨てる
（実制作と同じく、色調整・周辺減光・粒状感はつなぎの後）。snap は start をいちばん近い拍に寄せる（0 以下は 0、終わりの 0.01 秒以内は終わり。
同じ距離なら前の拍）。run には timeline.mp4・timeline.json（dance-timeline/0.1 の形に seam_info・flash_info・beats を足したもの。
`mv gate check` が切れ目とフラッシュを拍と照合できる）・manifest.json を残す。

中身は継ぎ目ごとのモジュールにある（どれも fx の中で、fx の指紋に入る。どれも cv2 を関数の中でだけ読む）:
sequence_spec（版・既定の値・読み込みと確かめ・拍の脈）、sequence_plan（1 コマの計画と重ね方・仕上げ）、sequence_render（描画の
文脈・記録・plan の表・run）、sequence_demo（デモ）。以前の名前は、下線付きも、すべてこのモジュールから読める。
"""
# 以前どおり読めるように、元の import も残す（このモジュールでは使わないものもある）
import bisect  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import math  # noqa: F401
import shutil  # noqa: F401
import time  # noqa: F401
from collections import OrderedDict  # noqa: F401
from dataclasses import dataclass, replace  # noqa: F401
from pathlib import Path  # noqa: F401

import jsonschema  # noqa: F401
import numpy as np  # noqa: F401

from ..io import PipelineError, read_json, read_json_file, safe_path, schema_lines, sha256  # noqa: F401
from . import spec as fxspec  # noqa: F401
from . import timeline as fxtimeline  # noqa: F401

from .sequence_spec import (  # noqa: F401（以前の名前。sequence_spec のもの）
    SCHEMA_VERSION, SCHEMA_V01, RECORD_SCHEMA, _SCHEMAS, FLASH, BURN, BURN_EDGE, OVERLAPS, LOOP, KEEP_SHOTS, KEEP_FRAMES,
    PULSE_DECAY, ease, schema, Timeline, load_timeline, _check_kinds, _check_version, parse_timeline, _without_audio,
    _transition, _video, _camera, _pulse, pulse_at, shot_pulse, snap, shot_name, _overlap_tail, _validate)
from .sequence_plan import (  # noqa: F401（以前の名前。sequence_plan のもの）
    shot_index, plan_frame, burn_at, video_positions, _loop_once, blend, finish)
from .sequence_render import (  # noqa: F401（以前の名前。sequence_render のもの）
    output_size, VideoFrames, VideoShot, Context, timeline_record, _describe_video, plan_text, _meter, _shot_records, render_run,
    _summary)
from .sequence_demo import (  # noqa: F401（以前の名前。sequence_demo のもの）
    _demo_clip, HEAD, demo)
