"""人物のタイムライン: ショット時刻 → どの素材のどのフレームか。動画の読み手はステージの動画にも使う。

読み出しは前方向だけに進め、戻るときは開き直して先頭から数える。コーデックのシークは環境によって隣のフレームを
返すことがあり、それだと同じ t で違う絵になるため（決定論を優先。静止画シートは遅いが本番の描画は順に読むので速い）。

OpenCV の色の読み方は環境で違う。Windows の OpenCV 5.0.0 はタグを見ず、いつも BT.601 の行列で RGB に直す（BT.709 の緑
(30, 220, 60) が (44, 248, 64) になり、飽和した色は 255 で切れるので後から戻せない）。Linux の CI の同じ版は正しく読んだ。
どの環境でも同じ色で読めるよう、タグが bt709 の動画は ffmpeg に行列を指定して読む。タグの無い動画は行列を見分けられないので
OpenCV のまま BT.601 として読む（compose.prepare が warnings に残す）。
"""
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .. import media
from ..fx import probe
from ..io import PipelineError

KNOWN_MATRICES = ("bt709", "smpte170m", "bt470bg")   # 正しく読める行列。bt709 以外の 2 つは BT.601 と同じ行列


def _matrix(path):
    """動画のタグの色の行列（bt709 など）。タグが無い・読めないときは None。"""
    try:
        matrix = probe.inspect(path)["video"]["matrix"]
    except PipelineError:
        return None
    return None if matrix == "unknown" else matrix


class _Bt709Capture:
    """タグが bt709 の動画を ffmpeg で先頭から順に読む。cv2.VideoCapture と同じ grab・read・release を持つ。"""

    def __init__(self, path, width, height):
        self.shape = (height, width, 3)
        # 可変フレームレートでも OpenCV と同じ枚数になるよう、コマを足し引きしない（rawvideo の既定は固定レート）
        self.process = subprocess.Popen(
            [media.ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
             "-fps_mode", "passthrough", "-vf", "scale=in_color_matrix=bt709:flags=bicubic+accurate_rnd+full_chroma_int",
             "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def isOpened(self):
        return True     # 開けなければ最初の読み出しが空になり、Source が止める

    def _next(self):
        size = self.shape[0] * self.shape[1] * 3
        data = self.process.stdout.read(size)
        return data if len(data) == size else None

    def grab(self):
        return self._next() is not None

    def read(self):
        data = self._next()
        return (False, None) if data is None else (True, np.frombuffer(data, np.uint8).reshape(self.shape))

    def release(self):
        self.process.kill()
        self.process.stdout.close()
        self.process.wait()


class Source:
    """1 本の動画の読み手。read(index) は uint8 RGB (H, W, 3)。末尾を越えたら最後のフレームを返す。"""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise PipelineError(f"人物動画が見つかりません: {self.path.name}")
        self.matrix = None
        self._open()
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not self.fps or self.fps != self.fps or self.width <= 0 or self.height <= 0:
            raise PipelineError(f"人物動画を読めません（fps か寸法が取れない）: {self.path.name}")
        self.last_index, self.last, self.end_index = -1, None, None
        self.matrix = _matrix(self.path)
        if self.matrix == "bt709":
            self.cap.release()
            self._open()

    def _open(self):
        self.cap = _Bt709Capture(self.path, self.width, self.height) if self.matrix == "bt709" else cv2.VideoCapture(str(self.path))
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

    def count(self):
        """全フレーム数。CAP_PROP_FRAME_COUNT は環境で変わるので数える。読み位置は変えない。"""
        cap, frames = cv2.VideoCapture(str(self.path)), 0
        while cap.grab():
            frames += 1
        cap.release()
        if not frames:
            raise PipelineError(f"動画からフレームを読めません: {self.path.name}")
        return frames

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


def round_frame(value, rounding):
    """元のコマ番号の丸め。floor は切り捨て（誤差で 1 つ前に落ちないよう微小値を足す。0.2 と同じ）、nearest はいちばん近いコマ（半コマは切り上げ）。"""
    return int(math.floor(value + (0.5 if rounding == "nearest" else 1e-6)))


class Replacements:
    """performer.replace_frames。shown(path, n) は (見せる元のコマ, 画像の読み手か None)。区間 [a, b) の外のコマはそのまま。"""

    def __init__(self, spec):
        self.runs = {}
        self.images = {}
        for run in spec.data["performer"]["replace_frames"]:
            self.runs.setdefault(run["path"], []).append(run)
            if isinstance(run["with"], dict) and run["with"]["image"] not in self.images:
                self.images[run["with"]["image"]] = Still(spec.resolve(run["with"]["image"]))

    def run_of(self, path, number):
        for run in self.runs.get(path, ()):
            if run["frames"][0] <= number < run["frames"][1]:
                return run
        return None

    def shown(self, path, number):
        run = self.run_of(path, number)
        if run is None:
            return number, None
        a, b = run["frames"]
        target = run["with"]
        if isinstance(target, dict):
            return number, self.images[target["image"]]             # 画像はそのコマの代わり（番号は元のまま）
        if isinstance(target, int):
            return target, None
        before, after = a - 1, b
        if target == "before" or (target == "nearest" and before >= 0 and number - before <= after - number):
            return before, None                                   # 同じ距離なら前（実制作と同じ）
        return after, None

    def skipped(self, path):
        """fit の解析で欠けとして扱うコマの区間 [[a, b), …]（差し替えたコマの輪郭で置き場所を決めない）。"""
        return sorted(run["frames"] for run in self.runs.get(path, ()))

    def close(self):
        for image in self.images.values():
            image.close()


def check_replacements(timeline):
    """replace_frames を素材と突き合わせる（描画の前に 1 回。差し替えのある動画だけを数える）。差し替えに使うコマが動画に無い・
    画像の寸法が動画と違うなら止め、動画の外の区間は warnings で知らせる。動画の末尾を越えた番号は最後のコマを見せるので、
    最後のコマまで届く区間の after・nearest は、壊れた最後のコマを見せてしまう。"""
    warnings = []
    for path, runs in timeline.replacements.runs.items():
        source = timeline.sources[path]
        frames = timeline.last_frame(path) + 1          # 数えるのは 1 回（Timeline が覚えて、末尾の差し替えにも使う）
        for run in runs:
            a, b = run["frames"]
            target, label = run["with"], f"replace_frames {path!r} [{a}, {b})"
            if a >= frames:
                warnings.append(f"performer: {label} は動画（{frames} コマ）の外なので効きません")
            elif isinstance(target, dict):
                image = timeline.replacements.images[target["image"]]
                if (image.width, image.height) != (source.width, source.height):
                    raise PipelineError(f"{label} の画像 {target['image']!r} の寸法 {image.width}x{image.height} が動画の "
                                        f"{source.width}x{source.height} と違います（置き場所は動画の寸法で決めるので、同じ寸法にしてください）")
            elif isinstance(target, int):
                if target >= frames:
                    raise PipelineError(f"{label} の差し替え先 {target} は動画（{frames} コマ。0〜{frames - 1}）にありません")
            elif target in ("after", "nearest") and b >= frames:
                raise PipelineError(f"{label} は動画の最後のコマ（{frames - 1}）まで届くので、後ろにきれいなコマがありません"
                                    "（before かコマ番号にしてください）")
    return warnings


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
        self.rounding = spec.data["performer"].get("frame_rounding", "floor")
        self.replacements = Replacements(spec)
        self.sources = {}
        self.frame_counts = {}     # replace_frames のある動画のコマ数（末尾を越えた番号の差し替えに使う。数えるのは 1 回）
        self.outgoing = {}     # blend の前のクリップ用。同じ動画を切れ目の前後で使うと 1 本の読み手が行き来して遅い
        for index, clip in enumerate(self.clips):
            if clip["path"] not in self.sources:
                self.sources[clip["path"]] = self._source(spec, clip)
            if clip["transition_in"]["type"] in ("blend", "dissolve") and self.clips[index - 1]["path"] not in self.outgoing:
                self.outgoing[self.clips[index - 1]["path"]] = self._source(spec, self.clips[index - 1])

    @staticmethod
    def _source(spec, clip):
        return (Still if clip["kind"] == "image" else Source)(spec.resolve(clip["path"]))

    def index_at(self, t):
        for index, clip in enumerate(self.clips):
            if clip["start"] <= t < clip["end"]:
                return index
        return None

    def raw_frame(self, clip, t):
        """ショット時刻 t に対応する元のフレーム番号（replace_frames の前）。retime の区間の中はその区間の付け替え、
        外は source_start + (t − start)·speed。丸めは performer.frame_rounding（既定の floor は 0.2 と同じ）。"""
        source = self.sources[clip["path"]]
        if clip["kind"] == "image":
            return 0
        for segment in clip.get("retime", ()):
            if segment["from"] <= t < segment["to"]:
                a, b = segment["source"]
                value = a + (b - a) * (t - segment["from"]) / (segment["to"] - segment["from"])   # 実制作の付け替えと同じ式の順
                return max(0, round_frame(value if segment["unit"] == "frame" else value * source.fps, self.rounding))
        return max(0, round_frame((clip["source_start"] + (t - clip["start"]) * clip["speed"]) * source.fps, self.rounding))

    def last_frame(self, path):
        """動画の最後のコマ番号（Source.count で 1 回だけ数えて覚える。check_replacements と共有）。"""
        if path not in self.frame_counts:
            self.frame_counts[path] = self.sources[path].count()
        return self.frame_counts[path] - 1

    def source_frame(self, clip, t):
        """ショット時刻 t に見せる元のフレーム番号（retime と丸めの後、replace_frames で差し替えた番号）。

        replace_frames のある動画は、末尾を越えた番号を最後のコマに止めてから差し替えを引く（読み手は最後のコマで止めて見せるので、
        最後のコマまで届く差し替えは、末尾を越えて止まる間も効く）。差し替えの無い動画の番号は、0.2 と同じくそのまま。"""
        number = self.raw_frame(clip, t)
        if clip["kind"] == "image":
            return number
        if clip["path"] in self.replacements.runs:
            number = min(number, self.last_frame(clip["path"]))
        return self.replacements.shown(clip["path"], number)[0]

    def blend_weight(self, clip, t):
        """t が clip の blend の窓の中なら新しい側の重み（0 より大きく 1 未満）、窓の外なら None。"""
        frames = clip["transition_in"]["frames"]
        if clip["transition_in"]["type"] != "blend":
            return None
        step = int(math.floor((t - clip["start"]) * self.fps + 1e-6))
        return smoothstep((step + 1) / (frames + 1)) if 0 <= step < frames else None

    def dissolve_weight(self, clip, t):
        """t が clip の dissolve の窓（start の前後 frames/2 コマ）の中なら新しい側の重み（窓の頭で 0、線形）、外なら None。
        式の順は非公開の制作のディゾルブと同じ: w = (t − (start − half)) / (2·half)。"""
        if clip["transition_in"]["type"] != "dissolve":
            return None
        half = clip["transition_in"]["frames"] / (2 * self.fps)
        low = clip["start"] - half
        return (t - low) / (2 * half) if low <= t < clip["start"] + half else None

    def mapping(self, t):
        """時刻 t の [(clip 番号, 元のフレーム番号, 重み)]。画を読まずに決まる（replace_frames のある動画だけ、コマ数を 1 回数える）。
        blend と dissolve の窓では前のクリップが先。dissolve は start の前から次のクリップを重ねる。"""
        index = self.index_at(t)
        if index is None:
            return []
        clip = self.clips[index]
        if index + 1 < len(self.clips) and t < self.clips[index + 1]["start"]:
            following = self.clips[index + 1]
            weight = self.dissolve_weight(following, t)
            if weight is not None:
                return [(index, self.source_frame(clip, t), 1.0 - weight), (index + 1, self.source_frame(following, t), weight)]
        number = self.source_frame(clip, t)
        weight = self.blend_weight(clip, t)
        if weight is None:
            weight = self.dissolve_weight(clip, t)
        if weight is None:
            return [(index, number, 1.0)]
        return [(index - 1, self.source_frame(self.clips[index - 1], t), 1.0 - weight), (index, number, weight)]

    def image_for(self, clip, number):
        """number のコマの代わりに見せる画像の読み手（replace_frames の image）。無ければ None。"""
        return None if clip["kind"] == "image" else self.replacements.shown(clip["path"], number)[1]

    def shown(self, t):
        mapped = self.mapping(t)
        readers = [self.outgoing, self.sources] if len(mapped) == 2 else [self.sources]
        out = []
        for reader, (index, number, weight) in zip(readers, mapped):
            clip = self.clips[index]
            still = self.image_for(clip, number)
            out.append(Shown(index, (reader[clip["path"]] if still is None else still).read(number), number, weight))
        return out

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
        self.replacements.close()
