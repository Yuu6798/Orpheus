"""仕上げの hooks（`mv dance render spec.json --hooks finish.py`）。

spec からは読み込まない。明示したときだけ Python を実行する。関数はどれも任意で、入力を書き換えず、新しい配列を返す:

    prepare(ctx)                            描画の最初に 1 回（テクスチャの準備など）
    stage(img, t, ctx, L)                   stage の後
    behind(img, t, ctx, L)                  後ろの字の後、人物の前（人物の後ろの光・影・反射など）
    performer(color, alpha, t, ctx, L)      人物そのもの。(color, alpha) を返す（輪郭の光・色合わせなど）
    front(img, t, ctx, L)                   人物とフラッシュの後、前の字の前
    final(img, t, ctx, L)                   前の字の後、look の前

img・color は float32 の線形で、color は前乗算。決定論を保つため、hooks は入力・t・定数だけの関数にする（ファイル書き込みと
ネットワークは禁止。乱数は api.rng から取る）。hooks ファイルの sha256 は run のキャッシュ key と manifest に入る。
hooks で時刻と構造は変わらない（timeline は spec だけから作る）。
"""
import hashlib
import sys
import traceback
import types
from pathlib import Path

import numpy as np

from ..io import PipelineError

NAMES = ("prepare", "stage", "behind", "performer", "front", "final")


class Hooks:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise PipelineError(f"hooks のファイルが見つかりません: {path}")
        source = self.path.read_bytes()      # 記録する sha256 と実行するソースを同じバイト列にする（.pyc のキャッシュを通さない）
        self.record = {"path": str(path), "sha256": hashlib.sha256(source).hexdigest()}
        self.module = types.ModuleType(f"dance_hooks_{self.record['sha256'][:12]}")
        self.module.__file__ = str(self.path)
        sys.modules[self.module.__name__] = self.module    # dataclass などが自分のモジュールを引けるように
        self._guarded("読み込み", lambda: exec(compile(source, str(self.path), "exec"), self.module.__dict__))
        self.functions = {name: getattr(self.module, name) for name in NAMES if callable(getattr(self.module, name, None))}

    def has(self, name):
        return name in self.functions

    def _guarded(self, name, action):
        try:
            return action()
        except Exception as error:
            raise PipelineError(f"hooks の {name} が失敗しました: {error}\n{traceback.format_exc()}") from error

    def prepare(self, ctx):
        if self.has("prepare"):
            self._guarded("prepare", lambda: self.functions["prepare"](ctx))

    def image(self, name, img, t, ctx, layers):
        """stage・behind・front・final を掛ける。無ければ img のまま。"""
        if not self.has(name):
            return img
        result = self._guarded(name, lambda: self.functions[name](img, t, ctx, layers))
        return _checked(name, result, img.shape)

    def performer(self, color, alpha, t, ctx, layers):
        if not self.has("performer"):
            return color, alpha
        result = self._guarded("performer", lambda: self.functions["performer"](color, alpha, t, ctx, layers))
        if not isinstance(result, tuple) or len(result) != 2:
            raise PipelineError("hooks の performer は (color, alpha) を返してください")
        return _checked("performer", result[0], color.shape), _checked("performer", result[1], alpha.shape)


def _checked(name, array, shape):
    if not isinstance(array, np.ndarray) or array.shape != shape:
        raise PipelineError(f"hooks の {name} は入力と同じ形の配列を返してください（期待 {shape}）")
    return array.astype(np.float32, copy=False)
