import copy
import hashlib
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline.fx import assets as fxassets  # noqa: E402
from mv_pipeline.fx import camera as fxcamera  # noqa: E402
from mv_pipeline.fx import demo, render  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

FILES = ["base.png", "depth.png", "mask_subject.png", "mask_ground.png", "audio.wav", "spec.json"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_demo_writes_all_assets_and_a_valid_spec(tmp_path):
    code, result, _ = run_cli("fx", "demo", tmp_path / "d", "--width", "320", "--height", "180", "--duration", "2", "--fps", "12")
    assert code == 0 and result["files"] == FILES and result["size"] == [320, 180]
    assert all((tmp_path / "d" / name).is_file() for name in FILES)
    spec = fxspec.load_spec(tmp_path / "d" / "spec.json")
    assert spec.data["output"]["fps"] == 12.0 and spec.data["duration_s"] == 2.0 and not spec.warnings
    info = fxassets.describe(spec)
    assert info["base"]["width"] == 640 and info["depth"]["height"] == 360        # 基準画像と同じ縦横比で、出力の 2 倍
    assert set(info) == {"base", "depth", "mask:subject", "mask:ground", "audio"}


def test_demo_is_deterministic_and_makes_no_other_files(tmp_path):
    for name in ("a", "b"):
        demo.create(tmp_path / name, 320, 180, 1.0, 12)
    assert {n: sha(tmp_path / "a" / n) for n in FILES} == {n: sha(tmp_path / "b" / n) for n in FILES}
    assert sorted(p.name for p in (tmp_path / "a").iterdir()) == sorted(FILES)       # 一時ファイルを残さない


def test_demo_rejects_bad_sizes(tmp_path):
    for width, height, duration, fps in ((161, 90, 2, 12), (320, 91, 2, 12), (100, 90, 2, 12), (320, 180, 0.1, 12), (320, 180, 2, 0)):
        with pytest.raises(PipelineError):
            demo.create(tmp_path / "x", width, height, duration, fps)


def prepared(directory, keep):
    """デモの spec から、effects を id が keep のものだけにした ctx。"""
    import json
    raw = json.loads((directory / "spec.json").read_text(encoding="utf-8"))
    raw["layers"]["effects"] = [e for e in raw["layers"]["effects"] if e["id"] in keep]
    return render.prepare(fxspec.parse_spec(raw, directory))


def test_demo_light_hits_face_sleeve_and_ground_but_stays_local(tmp_path):
    demo.create(tmp_path, 320, 180, 4.0, 12)
    ctx = render.prepare(fxspec.load_spec(tmp_path / "spec.json"))
    t = 3.0
    base, light = (render.frame(ctx, t, v).astype(int) for v in ("base", "light"))
    gain = light.mean(axis=2) - base.mean(axis=2)
    subject = ctx.planes[0].alpha > 0.99
    height, width = base.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width]
    flame_x, flame_y = demo.FLAME[0] * width, demo.FLAME[1] * height
    near = subject & (np.hypot(xs - flame_x, ys - flame_y) < 0.25 * width)          # 炎に近い人物の部分（袖・手・顔の側）
    assert near.sum() > 100 and gain[near].mean() > 8
    ground = (ys > demo.HORIZON * height + 4) & (np.abs(xs - flame_x) < 0.15 * width) & ~subject
    assert gain[ground].mean() > 5                                                   # 地面に照り返す
    far_sky = (ys < 0.15 * height) & (xs < 0.2 * width)
    assert np.abs(light - base)[far_sky].max() == 0                                  # 遠い空には届かない（局所性）


def test_demo_fog_is_behind_the_figure_and_embers_in_front(tmp_path):
    demo.create(tmp_path, 320, 180, 4.0, 12)
    plain = prepared(tmp_path, keep=())
    fog, embers = prepared(tmp_path, keep=("fog",)), prepared(tmp_path, keep=("embers",))
    times = (0.5, 1.5, 2.5, 3.5)

    def core_at(t):  # カメラが動くので、人物の不透明な芯は時刻ごとに画面上の位置が違う
        moved = fxcamera.warp(plain.planes[0].alpha, plain.path.matrix(t, 1.0), plain.size, cv2.BORDER_CONSTANT)
        return moved >= 0.999

    assert all(core_at(t).sum() > 100 for t in times)
    for t in times:
        core = core_at(t)
        flat = render.frame(plain, t, "layers").astype(int)
        misty = render.frame(fog, t, "layers").astype(int)
        assert (misty != flat).any(axis=2)[~(plain.planes[0].alpha > 0)].mean() > 0.9   # 霧は背景に出る
        assert not (misty != flat).any(axis=2)[core].any()                                # 人物の芯には出ない（後ろ）
    over = [(render.frame(embers, t, "layers").astype(int) != render.frame(plain, t, "layers").astype(int)).any(axis=2)[core_at(t)].any()
            for t in times]
    assert any(over)                                                                  # 火の粉は人物の上にも描かれる（前）
    assert fog.stack[0].z < fog.planes[0].z < embers.stack[-1].z


def test_demo_versions_get_brighter_step_by_step(tmp_path):
    demo.create(tmp_path, 320, 180, 4.0, 12)
    ctx = render.prepare(fxspec.load_spec(tmp_path / "spec.json"))
    mean = {v: render.frame(ctx, 3.0, v).astype(float).mean() for v in ("base", "layers", "light", "both")}
    assert mean["light"] > mean["base"] and mean["layers"] > mean["base"] and mean["both"] > mean["layers"]


def test_demo_flame_stays_on_the_hand_in_both_and_is_not_hidden_by_the_sleeve(tmp_path):
    demo.create(tmp_path, 320, 180, 4.0, 12)
    raw = json.loads((tmp_path / "spec.json").read_text(encoding="utf-8"))
    lit = render.prepare(fxspec.parse_spec(raw, tmp_path))
    bare_raw = copy.deepcopy(raw)
    del bare_raw["lights"][0]["emitter"]                                           # 受光は同じで、光点だけ無い版
    bare = render.prepare(fxspec.parse_spec(bare_raw, tmp_path))
    assert lit.lights[0].host == "subject"                                         # 炎は手（subject plane）に載る
    source = np.array([demo.FLAME[0] * 320 - 0.5, demo.FLAME[1] * 180 - 0.5, 1.0])
    peaks = {}

    def core_of_spot(spot):                                                         # 芯（最大の 9 割以上）の中心
        ys, xs = np.nonzero(spot >= 0.9 * spot.max())
        return xs.mean(), ys.mean()

    for t in (0.5, 2.0, 3.5):                                                      # 開始・中盤・終盤（カメラが寄る）
        spots = {v: (render.frame(lit, t, v).astype(int) - render.frame(bare, t, v).astype(int)).sum(axis=2)
                 for v in ("both", "light")}
        both_x, both_y = core_of_spot(spots["both"])
        on_hand = (lit.path.matrix(t, 1.0) @ source)[:2]
        assert np.hypot(both_x - on_hand[0], both_y - on_hand[1]) < 1.0            # 手に対する位置が時間で変わらない
        light_x, light_y = core_of_spot(spots["light"])
        assert np.hypot(both_x - light_x, both_y - light_y) < 1.0                  # light 版の光点と中心が一致
        peaks[t] = spots["both"].max() / spots["light"].max()
    assert min(peaks.values()) > 0.8                                               # 袖の plane に隠れず、芯も light 版と同じ
