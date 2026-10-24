import copy
import hashlib
import json

import numpy as np
import pytest
from PIL import Image

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


def lit_demo(tmp_path, t=3.0, rim=None, receivers=None):
    """デモ（320x180・4 秒）の ctx と、時刻 t の base / light の差（3 チャンネル平均の増分）。"""
    demo.create(tmp_path, 320, 180, 4.0, 12)
    raw = json.loads((tmp_path / "spec.json").read_text(encoding="utf-8"))
    if rim is not None:
        raw["lights"][0]["receivers"][0]["rim"] = rim
    ctx = render.prepare(fxspec.parse_spec(raw, tmp_path))
    base, light = (render.frame(ctx, t, v).astype(int) for v in ("base", "light"))
    height, width = base.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width]
    fx, fy = (ctx.path.matrix(t, 1.0) @ np.array([demo.FLAME[0] * width - 0.5, demo.FLAME[1] * height - 0.5, 1.0]))[:2]
    flame = np.hypot(xs - fx, ys - fy) / width                                  # カメラで動いた炎からの距離（画像の幅の割合）
    return ctx, (light - base).mean(axis=2), (xs, ys, flame, fx)


def test_demo_ground_mask_rises_smoothly_inside_the_figure(tmp_path):
    demo.create(tmp_path, 320, 180, 2.0, 12)
    ground = np.asarray(Image.open(tmp_path / "mask_ground.png")).astype(float) / 255
    subject = np.asarray(Image.open(tmp_path / "mask_subject.png")) > 0
    horizon = int(demo.HORIZON * ground.shape[0])
    assert ground[:horizon].max() == 0                                          # 地平線より上は地面ではない
    assert ground[horizon, subject[horizon]].max() == 0                         # 人物の内側は地平線で 0 から立ち上がる
    inside = subject[:-1] & subject[1:]
    assert np.abs(np.diff(ground, axis=0))[inside].max() < 0.2                  # 縦方向に段差がない（以前は帯の上端が 1 の段差）
    deep = cv2.erode(subject.astype(np.uint8), np.ones((3 * int(demo.REVEAL * ground.shape[0]),) * 2, np.uint8)) > 0
    assert deep.sum() > 1000 and ground[deep].max() == 0                        # 人物の体の中央には載せない（二重に照らさない）
    assert ground[~subject & (np.arange(ground.shape[0])[:, None] > horizon + 2)].min() == 1.0   # 外の地面は全面


def test_demo_depth_edge_follows_the_subject_mask_edge_and_is_no_softer_than_its_feather(tmp_path):
    demo.create(tmp_path, 640, 360, 1.0, 12)
    ctx = render.prepare(fxspec.load_spec(tmp_path / "spec.json"))
    depth, mask = ctx.assets.depth, ctx.assets.masks["subject"] > 0.5
    height = depth.shape[0]
    rows = slice(int(0.2 * height), int(demo.HORIZON * height) - 2)                 # 地平線より上: 外側は空（遠い）
    inside = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)[rows]
    outside = cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 5)[rows]
    sigma = demo.SUBJECT_FEATHER_PX * 640 / 1920 / 2                                  # マスクの羽根 σ（出力画素）
    reach = 3 * sigma + 1                                                            # 境界からこの距離の外は、羽根の影響が及ばない
    core, away = inside > reach, outside > reach
    assert core.sum() > 500 and away.sum() > 5000
    assert depth[rows][core].min() > 0.55                                            # 人物の内側は手前の深度
    assert depth[rows][away].max() < 0.25                                            # 外側は背景の深度（人物の深度が染み出さない）


def test_demo_light_differs_by_object_and_the_far_side_stays_dark(tmp_path):
    ctx, gain, (xs, ys, flame, _) = lit_demo(tmp_path)
    subject = ctx.planes[0].alpha > 0.99
    hand, face_and_shoulder = subject & (flame < 0.08), subject & (flame > 0.12) & (flame < 0.22) & (ys < 0.4 * 180)
    far = subject & (flame > 0.30)
    assert hand.sum() > 100 and face_and_shoulder.sum() > 300 and far.sum() > 50
    assert gain[hand].mean() > 60                                                # 炎の真下の手・袖は強く照らされる
    assert 8 < gain[face_and_shoulder].mean() < 0.5 * gain[hand].mean()          # 顔・肩の炎側は明るいが、手ほどではない
    assert gain[far].max() <= 1                                                  # 人物の反対側・遠い裾は暗いまま（半径 0.30 のほぼ外）
    assert gain[ctx.assets.masks["ground"] > 0.99].max() > 20                    # 地面は地面で別の当たり方（照り返し）をする


def test_demo_ground_reflection_is_a_narrow_vertical_streak_under_the_flame(tmp_path):
    ctx, gain, (xs, ys, flame, flame_x) = lit_demo(tmp_path)
    height, width = gain.shape
    near_figure = cv2.dilate((ctx.planes[0].alpha > 0).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    ground = (ys > demo.HORIZON * height + 2) & ~near_figure
    lit = ground & (gain > 8)
    ly, lx = np.nonzero(lit)
    assert lit.sum() > 500
    assert abs(lx.mean() - flame_x) < 0.03 * width                               # 炎の真下
    assert lx.max() - lx.min() < 0.16 * width                                    # 霧の塊のように横へ広がらない
    assert ly.max() - ly.min() > 0.8 * (height - demo.HORIZON * height)          # 縦に長い（地面の奥行き方向）
    away = ground & (np.abs(xs - flame_x) > 0.12 * width)
    assert gain[away].mean() < 1 and gain[away].max() <= 8                       # 筋の外の地面はほとんど変わらない


def test_demo_rim_light_sits_on_the_edges_facing_the_flame(tmp_path):
    ctx, with_rim, (xs, ys, flame, _) = lit_demo(tmp_path, rim=4.0)
    _, without, _ = lit_demo(tmp_path / "again", rim=0.0)
    rim = np.abs(with_rim - without)
    centre = demo.FIGURE_X * 320
    assert rim[xs > centre + 10].sum() > 10 * rim[xs < centre - 10].sum()      # 光源側の縁に出て、反対側の縁には出ない
    assert rim[flame > 0.34].max() == 0                                         # 半径の外には出ない（カメラの寄りぶん余裕を見る）
