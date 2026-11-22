"""render.panels: コマ割りの絵（折れ線・境から下・上下のずらし・境の線）と、コマへの掛け方（段の色・同じ種なら同じ境・別のカメラのコマが
無い T はそのまま写す・高さの比・一覧と報告・render.assemble へのつなぎ）。合成の単色のコマで確かめる（Blender なし）。"""
import json
import random
from pathlib import Path

import numpy as np
import pytest

from model3d import panels as P
from model3d.io import ToolError, atomic_json, sha256

RED, GREEN, BLUE = (200, 30, 30), (30, 200, 30), (30, 30, 200)


def _prototype_jagged(w, y_left, y_right, seed, s, steps, jag, jag_x):
    """リポジトリ外の台本の折れ線の式（基準の高さの画素で書き、s 倍する。端は基準で 4 画素だけ画面の外へ）。"""
    rng = random.Random(seed)
    points = []
    for i in range(steps + 1):
        k = i / steps
        inner = i not in (0, steps)
        dy = rng.uniform(-jag, jag) * (1 if i % 2 else -1) * rng.uniform(0.5, 1.0) if inner else 0.0
        dx = rng.uniform(-jag_x, jag_x) if inner else 0.0
        points.append(((-4 + (w / s + 8) * k + dx) * s, (y_left + (y_right - y_left) * k + dy) * s))
    return [(int(x), int(y)) for x, y in np.int32(points)]


def test_the_border_is_the_prototype_polyline_and_the_same_seed_gives_the_same_line():
    a = P.jagged(720, 1280, 250.0, 410.0, 11, 7, 30.0, 12.0, 960.0)
    assert a == _prototype_jagged(720, 250, 410, 11, 1280 / 960, 7, 30, 12)
    assert a == P.jagged(720, 1280, 250.0, 410.0, 11, 7, 30.0, 12.0, 960.0)            # 同じ種なら同じ折れ線
    assert a != P.jagged(720, 1280, 250.0, 410.0, 12, 7, 30.0, 12.0, 960.0)
    assert a[0] == (-5, 333) and a[-1][1] == 546 and len(a) == 8                         # 両端は揺らさない（画面の外へ高さの 1/240）
    flat = P.jagged(100, 200, 0.5, 0.5, 1, 4, 0.0, 0.0, 1.0)
    assert {y for _x, y in flat} == {100}                                                # 折れの量 0 なら真横の線
    ratio = P.jagged(720, 1280, 250 / 960, 410 / 960, 11, 7, 30 / 960, 12 / 960, 1.0)   # 高さの比で書いても、ほぼ同じ点
    assert max(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(a, ratio)) <= 2


def test_below_is_the_area_under_the_line_rounded_per_column():
    mask = P.below([(0, 2), (4, 6)], 10, 5)
    assert [int(np.argmax(mask[:, x])) for x in range(5)] == [2, 3, 4, 5, 6]
    assert mask[9].all() and not mask[1].any()


def test_shift_rows_moves_whole_pixels_exactly_and_follows_opencv_in_between():
    rng = np.random.default_rng(1)
    image = rng.integers(0, 256, (40, 7, 3), dtype=np.uint8)
    up = P.shift_rows(image, -5)
    assert np.array_equal(up[:35], image[5:]) and np.array_equal(up[35:], np.repeat(image[-1:], 5, axis=0))   # 端の画素を延ばす
    assert np.array_equal(P.shift_rows(image, 0), image)
    cv2 = pytest.importorskip("cv2")
    big = rng.integers(0, 256, (400, 300, 3), dtype=np.uint8)
    for dy in (-12.0, 3.0, -300.0, -7.3, 2.55, 11.0 / 3, -0.1, 0.7):
        expected = cv2.warpAffine(big, np.float32([[1, 0, 0], [0, 1, dy]]), (300, 400), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE).astype(int)
        diff = np.abs(P.shift_rows(big, dy).astype(int) - expected)
        if float(dy).is_integer():
            assert not diff.any(), dy                                                    # 整数のずらしは画素まで同じ
        else:
            assert diff.max() <= 1 and (diff > 0).sum() <= 5, dy                         # 小数は、まれに 1 違う（float の和の順）


def test_the_glow_and_the_core_stay_near_the_line():
    image = np.zeros((120, 80, 3), np.uint8)
    line = [(-1, 60), (81, 60)]
    out = P.gutter(image, [line], (100, 50, 200), 8, 3.0, 1.0, (255, 255, 255), 4)
    assert (out[59:62] == 255).all()                                                     # 芯は白
    assert out[70, 40, 2] > out[70, 40, 0] > 0 and out[70, 40, 2] > out[90, 40, 2]       # にじみの色が、線から離れるほど薄く
    assert not out[:20].any() and not out[100:].any()                                    # 遠い所は変わらない


def _solid(directory, Ts, color, size=(48, 96)):
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    for T in Ts:
        Image.new("RGB", size, color).save(directory / f"f{T:04d}.png")
    return directory


def _rgb(path):
    from PIL import Image
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


THREE = {"range": [3, 5],
         "bands": [{"source": "c", "shift": -0.05}, {"source": "main"}, {"source": "b", "shift": 0.05}],
         "borders": [{"left": 0.3, "right": 0.35, "seed": 3, "steps": 5, "jag": 0.03, "jag_x": 0.01},
                     {"left": 0.65, "right": 0.7, "seed": 8, "steps": 5, "jag": 0.03}]}
TWO = {"range": [6, 7], "bands": [{"source": "b"}, {"source": "main"}],
       "borders": [{"left": 0.5, "right": 0.5, "seed": 5, "steps": 4, "jag": 0.02}]}
LINE = {"glow_color": [120, 200, 255], "glow_width": [0.04, 0.04], "glow_blur": [0.02, 0.02], "glow_gain": [0.5, 0.5],
        "core_color": [255, 255, 255], "core_width": [0.02, 0.04], "settle": 2}


def _parameters(**changes):
    parameters = {"frames_dir": "main", "range": [1, 8], "fps": 10, "frames_b_dir": "b", "frames_c_dir": "c", "panels": [THREE, TWO]}
    parameters.update(changes)
    return parameters


def _scene(tmp_path, size=(48, 96), b_frames=range(1, 9)):
    _solid(tmp_path / "main", range(1, 9), RED, size)
    _solid(tmp_path / "b", b_frames, GREEN, size)
    _solid(tmp_path / "c", range(1, 9), BLUE, size)


def _recipe(tmp_path, parameters, assemble=False):
    steps = [{"id": "panels", "operation": "render.panels", "parameters": parameters,
              "outputs": {"frames": "frames.json", "report": "panels.json"}}]
    if assemble:
        steps.append({"id": "assemble", "operation": "render.assemble", "inputs": {"frames": "$steps.panels.frames"},
                      "parameters": {}, "outputs": {"video": "out.mp4"}})
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "panels", "inputs": {}, "steps": steps})
    return tmp_path / "recipe.json"


def _run(tmp_path, parameters, assemble=False):
    from model3d import contracts, executor
    result = executor.run_recipe(_recipe(tmp_path, parameters, assemble), tmp_path / "runs")
    step = result["steps"]["panels"]
    listing = contracts.load("frames", step["artifacts"]["frames"])
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    return result, step, Path(listing["directory"]), listing, report


def test_each_band_shows_its_camera_and_the_frames_outside_are_copied(tmp_path):
    _scene(tmp_path)
    result, step, out, listing, report = _run(tmp_path, _parameters())
    assert result["status"] == "succeeded"
    assert [fr["T"] for fr in listing["frames"]] == list(range(1, 9)) and listing["missing"] == []
    assert all(fr["sha256"] == sha256(out / fr["file"]) for fr in listing["frames"])
    for T in (1, 2, 8):                                                                   # 区間の外は、ファイルまで同じ
        assert (out / f"f{T:04d}.png").read_bytes() == (tmp_path / "main" / f"f{T:04d}.png").read_bytes()
    three = _rgb(out / "f0004.png")
    assert tuple(three[5, 24]) == BLUE and tuple(three[48, 24]) == RED and tuple(three[90, 24]) == GREEN   # 上 c・真ん中 主・下 b
    two = _rgb(out / "f0007.png")
    assert tuple(two[10, 24]) == GREEN and tuple(two[85, 24]) == RED
    assert report["schema_version"] == "model3d-panels/0.1" and report["split"] == 5 and report["copied"] == 3
    assert report["panels"][0]["split"] == [3, 4, 5] and report["panels"][1]["split"] == [6, 7] and report["skipped"] == []
    assert report["scale"] == 96 and report["panels"][0]["bands"][0]["shift_px"] == pytest.approx(-4.8)
    assert step["observations"][0] == {"kind": "panels", "frames": 8, "split": 5, "skipped": 0, "copied": 3,
                                       "seconds": step["observations"][0]["seconds"]}


def test_the_same_seed_gives_the_same_frames(tmp_path):
    _scene(tmp_path)
    first = _run(tmp_path, _parameters(line=LINE, out_dir="one"))
    second = _run(tmp_path, _parameters(line=LINE, out_dir="two"))
    assert [fr["sha256"] for fr in first[3]["frames"]] == [fr["sha256"] for fr in second[3]["frames"]]
    other = json.loads(json.dumps(THREE))
    other["borders"][0]["seed"] = 4
    third = _run(tmp_path, _parameters(line=LINE, out_dir="three", panels=[other, TWO]))
    assert third[4]["panels"][0]["borders"][0]["points"] != first[4]["panels"][0]["borders"][0]["points"]
    assert third[4]["panels"][0]["borders"][1]["points"] == first[4]["panels"][0]["borders"][1]["points"]


def test_a_missing_frame_of_another_camera_keeps_the_main_frame_and_is_reported(tmp_path):
    _scene(tmp_path, b_frames=[3, 5, 6, 7])                                               # b の T 4 が無い
    result, step, out, _listing, report = _run(tmp_path, _parameters())
    assert (out / "f0004.png").read_bytes() == (tmp_path / "main" / "f0004.png").read_bytes()   # 主のコマと同じバイト
    assert report["skipped"] == [{"T": 4, "panel": 0, "missing": ["b"]}] and report["panels"][0]["split"] == [3, 5]
    assert result["status"] == "needs_review" and "4" in step["unresolved"][0]           # 黙って成功にしない


def test_border_positions_follow_the_frame_height(tmp_path):
    reports = {}
    for height in (960, 1920):
        root = tmp_path / str(height)
        _scene(root, size=(height // 2, height))
        parameters = _parameters(range=[3, 3], panels=[dict(THREE, range=[3, 3])])     # 線なし（段の境がそのまま見える）
        reports[height] = _run(root, parameters)
    small, large = (reports[h][4]["panels"][0]["borders"] for h in (960, 1920))
    for a, b in zip(small, large):
        assert max(abs(2 * p[1] - q[1]) + abs(2 * p[0] - q[0]) for p, q in zip(a["points"], b["points"])) <= 4   # 境は画面の高さの比
    pictures = {h: _rgb(reports[h][2] / "f0003.png") for h in (960, 1920)}
    for fraction in (0.1, 0.5, 0.9):                                                     # 列ごとの段の切り替わりの高さも比で合う
        changes = {}
        for h, picture in pictures.items():
            column = picture[:, int(fraction * h / 2)]
            changes[h] = [int(y) + 1 for y in np.nonzero(np.any(column[1:] != column[:-1], axis=1))[0]]
        assert len(changes[960]) == len(changes[1920]) == 2
        assert all(abs(2 * a - b) <= 3 for a, b in zip(changes[960], changes[1920]))
        assert 0.25 < changes[960][0] / 960 < 0.4 and 0.6 < changes[960][1] / 960 < 0.75


def test_the_line_is_thick_and_bright_on_the_first_split_frame_and_settles(tmp_path):
    _scene(tmp_path)
    _result, _step, out, _listing, report = _run(tmp_path, _parameters(line=LINE))
    bright = {T: int((_rgb(out / f"f{T:04d}.png").astype(int).sum(axis=2) > 600).sum()) for T in (3, 4, 5)}
    assert bright[3] > bright[4] > bright[5]                                              # 割れた最初のコマで太く明るい
    assert [row["T"] for row in report["fresh"]] == [3, 4, 6, 7] and report["fresh"][0]["fresh"] == 1.0
    assert report["fresh"][1]["fresh"] == 0.5                                             # settle 2 コマで落ち着く
    settled = _rgb(out / "f0005.png")
    assert tuple(settled[2, 24]) == BLUE                                                  # 線から遠い所は段の色のまま


def test_the_frames_feed_render_assemble(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    _scene(tmp_path)
    result, *_ = _run(tmp_path, _parameters(line=LINE), assemble=True)
    observation = result["steps"]["assemble"]["observations"][0]
    assert observation["frames"] == 8 and observation["first"] == 1 and observation["last"] == 8


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def call(tmp_path, parameters, inputs=None, outputs=None):
    from model3d.ops import get
    declared = {k: tmp_path / v for k, v in (outputs or {"frames": "out.json"}).items()}
    return get("render.panels").function(inputs or {}, parameters, declared, Context(tmp_path))


def _listing(directory, Ts, missing=()):
    return {"schema_version": "model3d-frames/0.1", "directory": str(directory.resolve()), "fps": 10, "resolution": [48, 96],
            "frames": [{"T": T, "f": T, "file": f"f{T:04d}.png", "sha256": sha256(directory / f"f{T:04d}.png"),
                        "bytes": (directory / f"f{T:04d}.png").stat().st_size} for T in Ts], "missing": list(missing)}


def test_frames_lists_are_read_and_an_unrendered_t_of_another_camera_is_skipped(tmp_path):
    _scene(tmp_path)
    atomic_json(tmp_path / "main.json", _listing(tmp_path / "main", range(1, 9)))
    atomic_json(tmp_path / "b.json", _listing(tmp_path / "b", [6], missing=[7]))         # render.show の only で 1 コマだけ描いた
    parameters = {"panels": [TWO], "out_dir": "out"}
    result = call(tmp_path, parameters, {"frames": tmp_path / "main.json", "frames_b": tmp_path / "b.json"},
                  {"frames": "out.json", "report": "report.json"})
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["panels"][0]["split"] == [6] and report["skipped"] == [{"T": 7, "panel": 0, "missing": ["b"]}]
    assert result.unresolved and json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["fps"] == 10
    changed = _listing(tmp_path / "b", [6])
    changed["frames"][0]["sha256"] = "0" * 64                                             # 描いた後にコマが変わった
    atomic_json(tmp_path / "b.json", changed)
    with pytest.raises(ToolError, match="違います"):
        call(tmp_path, dict(parameters, out_dir="out2"), {"frames": tmp_path / "main.json", "frames_b": tmp_path / "b.json"})


@pytest.mark.parametrize("changes, message", [
    ({"panels": []}, "panels"),
    ({"panels": [dict(TWO, bands=[{"source": "b"}])]}, "2 つ以上"),
    ({"panels": [dict(TWO, bands=[{"source": "d"}, {"source": "main"}])]}, "source"),
    ({"panels": [dict(TWO, borders=[])]}, "borders"),
    ({"panels": [dict(TWO, borders=[{"left": 0.5, "right": 0.5, "seed": 1, "steps": 4}])]}, "足りない項目"),
    ({"panels": [dict(TWO, borders=[{"left": 0.5, "right": 0.5, "seed": "a", "steps": 4, "jag": 0.0}])]}, "seed"),
    ({"panels": [dict(TWO, borders=[{"left": 0.5, "right": 0.5, "seed": 1, "steps": 4, "jag": 0.0, "jag_x": 0.4}])]}, "左から右"),
    ({"panels": [dict(TWO, range=[7, 9])]}, "主のコマにありません"),
    ({"panels": [THREE, dict(TWO, range=[5, 6])]}, "重なっています"),
    ({"panels": [dict(TWO, shape="x")]}, "知らない項目"),
    ({"frames_c_dir": None}, "frames_c"),
    ({"line": dict(LINE, settle=0)}, "settle"),
    ({"line": dict(LINE, glow_width=[0, 0.1])}, "glow_width"),
    ({"line": {"glow_color": [1, 2, 3]}}, "全部"),
    ({"line": dict(LINE, core_color=[256, 0, 0])}, "core_color"),
    ({"reference_height": 0}, "reference_height"),
    ({"out_dir": "b"}, "同じ"),
    ({"blend": "x"}, "知らないパラメーター"),
])
def test_the_operation_rejects_bad_settings(tmp_path, changes, message):
    _scene(tmp_path)
    parameters = _parameters(out_dir="out")
    parameters.update({k: v for k, v in changes.items() if v is not None})
    for key in [k for k, v in changes.items() if v is None]:
        parameters.pop(key)
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters)


def test_frames_of_different_sizes_and_transparent_frames_are_refused(tmp_path):
    from PIL import Image
    _scene(tmp_path)
    Image.new("RGB", (40, 80), GREEN).save(tmp_path / "b" / "f0006.png")
    with pytest.raises(ToolError, match="大きさ"):
        call(tmp_path, _parameters(out_dir="out", panels=[TWO]))
    Image.new("RGBA", (48, 96), (0, 0, 0, 0)).save(tmp_path / "b" / "f0006.png")
    with pytest.raises(ToolError, match="透けた"):
        call(tmp_path, _parameters(out_dir="out2", panels=[TWO]))
