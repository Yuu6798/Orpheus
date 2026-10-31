"""拍の格子と時刻の参照。

spec の時刻の欄は秒の数値か、次の参照で書ける（読み込み時に秒へ解決する。spec.data には秒だけが入る）:

    {"beat": 12.5}              拍の番号。小数は前後の拍の間を線形補間し、格子の外は端の間隔で外挿する
    {"bar": 3, "beat": 1.5}     小節頭の番号 + 拍数
    どちらも "offset_s": -0.1 を足せる

格子は beats の 2 つの書き方のどちらかから作る:

    {"path": "beats.json"}      beats（または beats_s）と任意の downbeats の配列。gate・`mv analyze` と同じ形
    {"bpm": 120, "offset_s": 0, "beats_per_bar": 4, "count": 512, "downbeat_phase": 0}
"""
import json
from dataclasses import dataclass

from ..io import PipelineError, read_json


def is_ref(value):
    return isinstance(value, dict)


def _at(times, position):
    """配列 times の小数の番号 position の値。格子の外は端の間隔で外挿する。"""
    last = len(times) - 1
    if last == 0:
        return times[0]
    index = max(0, min(last - 1, int(position // 1)))
    return times[index] + (times[index + 1] - times[index]) * (position - index)


def _position(times, t):
    """_at の逆: 時刻 t の小数の番号。"""
    last = len(times) - 1
    if last == 0:
        return 0.0
    index = 0
    while index < last - 1 and times[index + 1] <= t:
        index += 1
    return index + (t - times[index]) / (times[index + 1] - times[index])


@dataclass(frozen=True)
class Grid:
    beats: list
    downbeats: list
    path: str | None = None

    def at_beat(self, position):
        return _at(self.beats, float(position))

    def at_bar(self, bar, beat=0.0):
        if not self.downbeats:
            raise PipelineError("dance spec: {\"bar\": …} には小節頭（downbeats）のある beats が必要です")
        head = _at(self.downbeats, float(bar))
        return self.at_beat(_position(self.beats, head) + float(beat)) if beat else head

    def record(self):
        return {"beats": self.beats, "downbeats": self.downbeats}


def _strict(times, label):
    if any(b <= a for a, b in zip(times, times[1:])):
        raise PipelineError(f"dance spec: {label} は厳密な昇順にしてください")
    return times


def load_grid(raw, resolve):
    """spec の beats から Grid を作る。resolve は spec 相対のパスを絶対パスにする関数。"""
    if raw is None:
        return None
    if "path" in raw:
        path = resolve(raw["path"])
        if not path.is_file():
            raise PipelineError(f"beats のファイルが見つかりません: {raw['path']}")
        try:
            data = read_json(path)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise PipelineError(f"beats のファイルが JSON ではありません（{raw['path']}）: {error}") from error
        beats = data.get("beats") or data.get("beats_s") if isinstance(data, dict) else None
        if not beats:
            raise PipelineError(f"beats のファイルに beats / beats_s の配列がありません: {raw['path']}")
        downbeats = data.get("downbeats") or []
        return Grid(_strict([float(v) for v in beats], "beats"), _strict([float(v) for v in downbeats], "downbeats"), raw["path"])
    step = 60.0 / float(raw["bpm"])
    offset = float(raw.get("offset_s", 0))
    beats = [offset + index * step for index in range(int(raw.get("count", 512)))]
    return Grid(beats, beats[int(raw.get("downbeat_phase", 0))::int(raw.get("beats_per_bar", 4))])


def resolve_time(value, grid, where):
    """秒の数値か参照を秒にする。where はエラーの文言に使う欄の名前。"""
    if not is_ref(value):
        seconds = float(value)
    else:
        if grid is None:
            raise PipelineError(f"dance spec: {where} の拍の参照には beats が必要です")
        seconds = (grid.at_bar(value["bar"], value.get("beat", 0)) if "bar" in value else grid.at_beat(value["beat"]))
        seconds += float(value.get("offset_s", 0))
    if seconds < 0:
        raise PipelineError(f"dance spec: {where} の時刻が負です（{seconds:g} 秒）")
    return seconds
