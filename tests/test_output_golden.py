"""出力の金型（リファクタリングの安全網）: fx・fx timeline・dance のデモを小さく作って描いた出力を、記録と比べる（1 ビットの変化も拾う）。

比べるもの:
- デモと run が書く MP4 以外のファイル（画像・音・spec・timeline.json・faces.json）のバイト列の sha256。
- 書き出し（fx.render.write_video。fx の 4 版とグリッド・timeline・dance と、デモの素材の動画）に渡したコマ全部のバイト列の
  sha256 と、大きさ・fps・crf・コマ数・音の切り方・ffmpeg の引数（パスはファイル名に）。書き出した順に並べる。
MP4 のバイト列は比べない。同じコマと引数でも、ffmpeg（x264）の結果が変わることがある（Windows で fx のグリッドを 6 回描いて
2 回、13 コマ目あたりから違った。CPU が混んでいると起きやすく、-threads 1 でも起きる。rgb → yuv の色の変換までは変わらない）。
そのため、このテストの中だけ、書き出しを x264 の可逆（crf 0）で行う（本番の書き出しの設定は変えない）。記録する ffmpeg の引数は、
コードが渡した crf で本番に走る引数（encode.ffmpeg_command）のまま。可逆なら、x264 の結果が揺れても、復号したコマは色の変換の
結果そのものになるので、書いた MP4 を読み戻して描くもの（fx のグリッド、timeline の動画のショット、dance のステージと人物の
クリップ）に揺れが移らない（実測。本物の crf で書くと、混んだ PC で dance の最後のコマが 180 回のうち 52 回変わった。crf 0 なら、
ステージの動画を 30 回書いて復号したコマが 30 回とも同じで、色の変換だけの結果とも同じだった）。run の記録（manifest.json）と
render.log は時刻と秒を持つので比べない。

浮動小数の演算と画像の読み書きは環境（OS・Python・OpenCV・numpy・Pillow・同梱の ffmpeg。CPU の命令でも最後のビットが変わりうる）で
変わりうるので、記録は環境ごとに持つ。記録の無い環境（CI のランナーなど）では、比べずに飛ばす（理由に値を出す）。意図して出力を
変えたときは ORPHEUS_UPDATE_GOLDEN=1 で走らせて、この環境の記録を書き直す。"""
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import PIL
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline import encode  # noqa: E402
from mv_pipeline.dance import demo as dancedemo  # noqa: E402
from mv_pipeline.dance import runs as danceruns  # noqa: E402
from mv_pipeline.fx import demo as fxdemo  # noqa: E402
from mv_pipeline.fx import render  # noqa: E402
from mv_pipeline.fx import runs as fxruns  # noqa: E402
from mv_pipeline.fx import sequence  # noqa: E402

RECORD = Path(__file__).parent / "data" / "output_golden_mv.json"


def environment():
    return (f"{platform.system()}-{platform.machine()}-py{sys.version_info[0]}.{sys.version_info[1]}-cv2{cv2.__version__}"
            f"-np{np.__version__}-pil{PIL.__version__}-imageio-ffmpeg{imageio_ffmpeg.__version__}")


def check(name, values):
    """values（名前 → 値）を、この環境の記録 name と比べる。"""
    records = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.exists() else {}
    env = environment()
    if os.environ.get("ORPHEUS_UPDATE_GOLDEN") == "1":
        records.setdefault(env, {})[name] = values
        RECORD.write_text(json.dumps(records, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return
    expected = records.get(env, {}).get(name)
    if expected is None:
        pytest.skip(f"出力の金型の記録が無い環境です（比べていない）: {json.dumps({env: {name: values}}, sort_keys=True)}")
    assert values == expected, f"{name} の出力が金型と違います（{env}）: {sorted(k for k in values if values[k] != expected.get(k))}"


LOSSLESS_CRF = 0   # このテストの中で実際に書く crf（x264 の可逆。docstring を参照）


@pytest.fixture
def encoded(monkeypatch):
    """書き出しに渡したコマと引数を、書き出した順に集める（書き出し自体は本物を呼ぶ。crf だけ LOSSLESS_CRF で書く）。"""
    calls = []
    original = render.write_video

    def capture(out_path, frames, size, fps, crf, n_frames, audio=None, log_path=None):
        frame_hash, count = hashlib.sha256(), 0

        def tee():
            nonlocal count
            for image in frames:
                frame_hash.update(np.ascontiguousarray(image).tobytes())
                count += 1
                yield image
        result = original(out_path, tee(), size, fps, LOSSLESS_CRF, n_frames, audio, log_path)
        partial = encode.partial_path(out_path)
        assert result["command"] == encode.ffmpeg_command(partial, size, fps, LOSSLESS_CRF, n_frames, audio)
        command = encode.ffmpeg_command(partial, size, fps, crf, n_frames, audio)     # コードの crf で本番に走る引数
        paths = [command[-1], *([audio["path"]] if audio and "path" in audio else [])]
        names = {text: Path(path).name for path in paths for text in (str(path), str(Path(path)))}
        cut = None if audio is None else {k: (Path(v).name if k == "path" else v) for k, v in audio.items()}
        calls.append({"file": Path(out_path).name, "size": list(size), "fps": fps, "crf": crf, "frames": count, "n_frames": n_frames,
                      "frames_sha256": frame_hash.hexdigest(), "audio": cut,
                      "command": ["ffmpeg"] + [names.get(arg, arg) for arg in command[1:]]})
        return result
    monkeypatch.setattr(render, "write_video", capture)
    return calls


def _files(root, prefix):
    """MP4 以外のファイルのバイト列の sha256（run の記録 manifest.json と render.log は時刻を持つので除く）。"""
    return {prefix + p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix != ".mp4" and p.name not in ("manifest.json", "render.log")}


def _written(calls):
    """書き出しの記録（書き出した順。同じファイル名が続くときは番号を足す）。"""
    return {f"write/{n:02d}/{call['file']}": hashlib.sha256(json.dumps(call, sort_keys=True).encode()).hexdigest()
            for n, call in enumerate(calls)}


def test_fx_demo_and_render_match_the_golden(tmp_path, encoded):
    fxdemo.create(tmp_path / "demo", 320, 180, 2.0, 12, look=True)
    result = fxruns.render_run(tmp_path / "demo" / "spec.json", out=tmp_path / "runs")
    assert result["status"] == "succeeded" and result["technical_pass"] is True
    assert [c["file"] for c in encoded] == ["base.mp4", "layers.mp4", "light.mp4", "both.mp4", "grid.mp4"]
    check("fx", {**_files(tmp_path / "demo", "demo/"), **_files(Path(result["run_dir"]), "run/"), **_written(encoded)})


def test_fx_timeline_demo_and_render_match_the_golden(tmp_path, encoded):
    sequence.demo(tmp_path / "demo", 160, 90, 3.0, 12)
    result = sequence.render_run(tmp_path / "demo" / "timeline.json", tmp_path / "runs", quiet=True)
    assert result["status"] == "succeeded" and result["technical_pass"] is True
    assert [c["file"] for c in encoded][-1] == "timeline.mp4"
    check("fx timeline", {**_files(tmp_path / "demo", "demo/"), **_files(Path(result["run_dir"]), "run/"), **_written(encoded)})


def test_dance_demo_and_render_match_the_golden(tmp_path, encoded):
    dancedemo.create(tmp_path / "demo", 180, 320, 2.0, 24, "0.3")
    result = danceruns.render_run(tmp_path / "demo" / "spec.json", out=tmp_path / "runs", quiet=True)
    assert result["status"] == "succeeded" and result["technical_pass"] is True
    assert [c["file"] for c in encoded][-1] == "dance.mp4"
    check("dance", {**_files(tmp_path / "demo", "demo/"), **_files(Path(result["run_dir"]), "run/"), **_written(encoded)})
