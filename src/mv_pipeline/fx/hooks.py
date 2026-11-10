"""fx-timeline の hooks（`mv fx timeline render timeline.json --hooks effects.py`）。作品ごとの演出を Python で掛ける口。

timeline からは読み込まない。明示したときだけ Python を実行する。関数はどれも任意で、渡した画を書き換えてよく、同じ形の画を返す:

    prepare(ctx)              描画の最初に 1 回（作り置きなど）。ctx は sequence.Context
    shot(img, t, view)        ショットの画（つなぎの前）。t は timeline の時刻、view はそのショットの見え方（fx/view.py）
    final(img, t, ctx)        つないだ後、timeline の look の前。ctx.view_at(t) はその時刻のショットの見え方（深度は view.depth()）

img は float32 (H, W, 3) の表示の値（0..255、ガンマ符号化。まだ切らない）。演出の部品は fx/fire.py（火）・fx/sky.py（空と天気）・
fx/accent.py（明かりと質感）・fx/particles.py（形に集まる光の粒）。
決定論を保つため、hooks は入力・t・定数だけの関数にする（ファイル書き込みとネットワークは禁止。乱数は seed から作る）。
hooks ファイルの sha256 は run のキャッシュ key と manifest に入る。hooks で時刻とショットの区切りは変わらない。
"""
import hashlib
import sys
import traceback
import types
from pathlib import Path

import numpy as np

from ..io import PipelineError

NAMES = ("prepare", "shot", "final")


class Hooks:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise PipelineError(f"hooks のファイルが見つかりません: {path}")
        source = self.path.read_bytes()      # 記録する sha256 と実行するソースを同じバイト列にする
        self.record = {"path": str(path), "sha256": hashlib.sha256(source).hexdigest()}
        self.module = types.ModuleType(f"fx_hooks_{self.record['sha256'][:12]}")
        self.module.__file__ = str(self.path)
        sys.modules[self.module.__name__] = self.module
        self._guarded("読み込み", lambda: exec(compile(source, str(self.path), "exec"), self.module.__dict__))
        self.functions = {name: getattr(self.module, name) for name in NAMES if callable(getattr(self.module, name, None))}
        if not self.functions:
            raise PipelineError(f"hooks に {'・'.join(NAMES)} のどれもありません: {path}")

    def has(self, name):
        return name in self.functions

    def _guarded(self, name, action):
        try:
            return action()
        except PipelineError:
            raise
        except Exception as error:
            raise PipelineError(f"hooks の {name} が失敗しました: {error}\n{traceback.format_exc()}") from error

    def prepare(self, ctx):
        if self.has("prepare"):
            self._guarded("prepare", lambda: self.functions["prepare"](ctx))

    def shot(self, img, t, view):
        if not self.has("shot"):
            return img
        return _checked("shot", self._guarded("shot", lambda: self.functions["shot"](img, t, view)), img.shape)

    def final(self, img, t, ctx):
        if not self.has("final"):
            return img
        return _checked("final", self._guarded("final", lambda: self.functions["final"](img, t, ctx)), img.shape)


def _checked(name, array, shape):
    if not isinstance(array, np.ndarray) or array.shape != shape:
        got = array.shape if isinstance(array, np.ndarray) else type(array).__name__
        raise PipelineError(f"hooks の {name} は、渡した画と同じ形 {shape} の配列を返してください（返したもの: {got}）")
    return array.astype(np.float32, copy=False)
