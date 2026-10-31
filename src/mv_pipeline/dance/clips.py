"""人物動画のタイムライン: ショット時刻 → どの動画のどのフレームか。

読み出しは前方向だけに進め、戻るときは開き直して先頭から数える。コーデックのシークは環境によって隣のフレームを
返すことがあり、それだと同じ t で違う絵になるため（決定論を優先。静止画シートは遅いが本番の描画は順に読むので速い）。
"""
import math
from pathlib import Path

import cv2

from ..io import PipelineError


class Source:
    """1 本の動画の読み手。read(index) は uint8 RGB (H, W, 3)。末尾を越えたら最後のフレームを返す。"""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise PipelineError(f"人物動画が見つかりません: {self.path.name}")
        self._open()
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not self.fps or self.fps != self.fps or self.width <= 0 or self.height <= 0:
            raise PipelineError(f"人物動画を読めません（fps か寸法が取れない）: {self.path.name}")
        self.last_index, self.last, self.end_index = -1, None, None

    def _open(self):
        self.cap = cv2.VideoCapture(str(self.path))
        self.next_index = 0
        if not self.cap.isOpened():
            raise PipelineError(f"人物動画を開けません: {self.path.name}")

    def read(self, index):
        index = max(0, int(index))
        if self.end_index is not None:
            index = min(index, self.end_index)
        if index == self.last_index:
            return self.last
        if index < self.next_index:
            self.cap.release()
            self._open()
        while self.next_index < index:
            if not self.cap.grab():
                return self._tail()
            self.next_index += 1
        ok, bgr = self.cap.read()
        if not ok:
            return self._tail()
        self.next_index = index + 1
        self.last_index, self.last = index, cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return self.last

    def _tail(self):
        """末尾を越えたとき: 先頭から数え直して本当の最後のフレームを返す（読み方の順番で返す絵が変わらないように）。"""
        self.cap.release()
        self._open()
        final = None
        while True:
            ok, bgr = self.cap.read()
            if not ok:
                break
            final = bgr
            self.next_index += 1
        if final is None:
            raise PipelineError(f"人物動画からフレームを読めません: {self.path.name}")
        self.end_index = self.last_index = self.next_index - 1
        self.last = cv2.cvtColor(final, cv2.COLOR_BGR2RGB)
        return self.last

    def close(self):
        self.cap.release()


class Timeline:
    """spec の performer.clips（start 昇順・重なりなし）。frame_at(t) は (clip 番号, uint8 RGB) か None。"""

    def __init__(self, spec):
        self.clips = spec.data["performer"]["clips"]
        self.sources = {}
        for clip in self.clips:
            if clip["path"] not in self.sources:
                self.sources[clip["path"]] = Source(spec.resolve(clip["path"]))

    def index_at(self, t):
        for index, clip in enumerate(self.clips):
            if clip["start"] <= t < clip["end"]:
                return index
        return None

    def source_frame(self, clip, t):
        """ショット時刻 t に見せる元動画のフレーム番号（フレームの切り捨て。誤差で 1 つ前に落ちないよう微小値を足す）。"""
        source = self.sources[clip["path"]]
        return int(math.floor((clip["source_start"] + (t - clip["start"]) * clip["speed"]) * source.fps + 1e-6))

    def frame_at(self, t):
        index = self.index_at(t)
        if index is None:
            return None
        clip = self.clips[index]
        return index, self.sources[clip["path"]].read(self.source_frame(clip, t))

    def seams(self):
        """人物の切り替わる時刻（隣り合う clip の境目）。"""
        return [right["start"] for left, right in zip(self.clips, self.clips[1:])
                if abs(right["start"] - left["end"]) < 1e-9]

    def close(self):
        for source in self.sources.values():
            source.close()
