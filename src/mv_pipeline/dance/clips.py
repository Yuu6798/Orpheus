"""人物のタイムライン: ショット時刻 → どの素材のどのフレームか。動画の読み手はステージの動画にも使う。

読み出しは前方向だけに進め、戻るときは開き直して先頭から数える。コーデックのシークは環境によって隣のフレームを
返すことがあり、それだと同じ t で違う絵になるため（決定論を優先。静止画シートは遅いが本番の描画は順に読むので速い）。
"""
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

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

    def ended_at(self, index):
        """index が最後のフレームより後か（read(index) の後に呼ぶ）。"""
        return self.end_index is not None and index > self.end_index

    def close(self):
        self.cap.release()


class Still:
    """静止画の読み手。Source と同じ形で、どの番号でも同じ 1 枚を返す。"""
    fps = 0.0

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise PipelineError(f"人物の静止画が見つかりません: {self.path.name}")
        with Image.open(self.path) as image:
            self.image = np.asarray(image.convert("RGB"))
        self.height, self.width = self.image.shape[:2]

    def read(self, index):
        return self.image

    def ended_at(self, index):
        return index > 0

    def close(self):
        pass


def smoothstep(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


@dataclass
class Shown:
    """時刻 t に見せる 1 つの素材。frame は元のフレーム番号（負の開始は 0 に切った後の値）、weight は blend の重み。"""
    index: int
    image: np.ndarray
    frame: int
    weight: float


class Timeline:
    """spec の performer.clips（start 昇順・重なりなし）。shown(t) は [Shown]（人物のいない時刻は空）。"""

    def __init__(self, spec):
        self.clips = spec.data["performer"]["clips"]
        self.fps = spec.data["output"]["fps"]
        self.sources = {}
        self.outgoing = {}     # blend の前のクリップ用。同じ動画を切れ目の前後で使うと 1 本の読み手が行き来して遅い
        for index, clip in enumerate(self.clips):
            if clip["path"] not in self.sources:
                self.sources[clip["path"]] = self._source(spec, clip)
            if clip["transition_in"]["type"] == "blend" and self.clips[index - 1]["path"] not in self.outgoing:
                self.outgoing[self.clips[index - 1]["path"]] = self._source(spec, self.clips[index - 1])

    @staticmethod
    def _source(spec, clip):
        return (Still if clip["kind"] == "image" else Source)(spec.resolve(clip["path"]))

    def index_at(self, t):
        for index, clip in enumerate(self.clips):
            if clip["start"] <= t < clip["end"]:
                return index
        return None

    def source_frame(self, clip, t):
        """ショット時刻 t に見せる元のフレーム番号（フレームの切り捨て。誤差で 1 つ前に落ちないよう微小値を足す）。"""
        source = self.sources[clip["path"]]
        if clip["kind"] == "image":
            return 0
        return max(0, int(math.floor((clip["source_start"] + (t - clip["start"]) * clip["speed"]) * source.fps + 1e-6)))

    def blend_weight(self, clip, t):
        """t が clip の blend の窓の中なら新しい側の重み（0 より大きく 1 未満）、窓の外なら None。"""
        frames = clip["transition_in"]["frames"]
        if clip["transition_in"]["type"] != "blend":
            return None
        step = int(math.floor((t - clip["start"]) * self.fps + 1e-6))
        return smoothstep((step + 1) / (frames + 1)) if 0 <= step < frames else None

    def mapping(self, t):
        """時刻 t の [(clip 番号, 元のフレーム番号, 重み)]。画を読まずに決まる。blend の窓では前のクリップが先。"""
        index = self.index_at(t)
        if index is None:
            return []
        clip = self.clips[index]
        number = self.source_frame(clip, t)
        weight = self.blend_weight(clip, t)
        if weight is None:
            return [(index, number, 1.0)]
        return [(index - 1, self.source_frame(self.clips[index - 1], t), 1.0 - weight), (index, number, weight)]

    def shown(self, t):
        mapped = self.mapping(t)
        readers = [self.outgoing, self.sources] if len(mapped) == 2 else [self.sources]
        return [Shown(index, reader[self.clips[index]["path"]].read(number), number, weight)
                for reader, (index, number, weight) in zip(readers, mapped)]

    def seam_list(self):
        """[(切れ目の時刻, 前の clip 番号, 後の clip 番号)]。隣り合う clip の境目。"""
        return [(right["start"], index, index + 1) for index, (left, right) in enumerate(zip(self.clips, self.clips[1:]))
                if abs(right["start"] - left["end"]) < 1e-9]

    def seams(self):
        """人物の切り替わる時刻（隣り合う clip の境目）。"""
        return [seam[0] for seam in self.seam_list()]

    def close(self):
        for source in (*self.sources.values(), *self.outgoing.values()):
            source.close()
