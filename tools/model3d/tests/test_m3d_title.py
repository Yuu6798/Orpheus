"""render.title: 題字の絵（ぼかし・文字・影と光り・重ね方）と、コマへの重ね方（前のコマは写す・浮かび上がり・一覧と報告・
render.assemble へのつなぎ）。書体は Pillow の内蔵の書体（ファイル不要）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from model3d import title
from model3d.io import ToolError, atomic_json, sha256


def test_smoothstep_and_the_kernel_lengths_follow_opencv():
    assert [title.smoothstep(u) for u in (-1, 0, 0.5, 1, 2)] == [0.0, 0.0, 0.5, 1.0, 1.0]
    assert len(title.gaussian_kernel(9)) == 73 and len(title.gaussian_kernel(18)) == 145 and len(title.gaussian_kernel(1)) == 9
    k = title.gaussian_kernel(2.5)
    assert k.dtype == np.float32 and k.sum() == pytest.approx(1.0, abs=1e-6)
    assert np.array_equal(k, k[::-1]) and int(k.argmax()) == len(k) // 2
    with pytest.raises(ValueError):
        title.gaussian_kernel(0)


def _reflect101(i, n):
    while i < 0 or i >= n:
        i = -i if i < 0 else 2 * (n - 1) - i
    return i


def test_the_blur_is_a_separable_gaussian_with_reflect101_edges():
    assert list(title.reflect101(4, 5)) == [1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1, 0, 1, 2]     # 端を繰り返さず、何度でも折り返す
    assert list(title.reflect101(1, 2)) == [0, 0, 0, 0, 0]
    rng = np.random.default_rng(3)
    a = rng.random((7, 11)).astype(np.float32)
    sigma = 2.0                                            # 核（17）の半分（8）が縦（7）より長い: 端の折り返しが 2 度起きる
    k = title.gaussian_kernel(sigma).astype(np.float64)
    r = len(k) // 2
    expected = np.zeros(a.shape)
    for y in range(a.shape[0]):
        for x in range(a.shape[1]):
            expected[y, x] = sum(k[i] * k[j] * a[_reflect101(y + i - r, a.shape[0]), _reflect101(x + j - r, a.shape[1])]
                                 for i in range(len(k)) for j in range(len(k)))
    out = title.blur(a, sigma)
    assert out.dtype == np.float32 and np.allclose(out, expected, atol=1e-6)


def test_the_blur_matches_opencv_within_float32_rounding():
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(5)
    a = rng.random((40, 60)).astype(np.float32)
    for sigma in (2.5, 9.0):
        assert np.abs(title.blur(a, sigma) - cv2.GaussianBlur(a, (0, 0), sigma)).max() < 2e-6
        reference = cv2.getGaussianKernel(int(round(sigma * 8 + 1)) | 1, sigma, cv2.CV_32F).ravel()
        assert np.allclose(title.gaussian_kernel(sigma), reference, rtol=0, atol=1e-8)   # 同じ長さと形の核


def test_text_is_drawn_char_by_char_with_tracking_and_room_for_the_glow():
    font = title.load_font(None, 20)
    plain_color, plain = title.text_layer("AB", font, 20)
    _, wide = title.text_layer("AB", font, 20, tracking=0.5)
    widths = [font.getlength(c) for c in "AB"]
    pad = int(20 * 0.45)
    assert plain.shape[1] == int(sum(widths) + 2 * pad) and wide.shape[1] == int(sum(widths) + 0.5 * 20 + 2 * pad)
    assert plain.max() == pytest.approx(1.0) and plain[:pad].max() == 0 and plain[:, :pad - 1].max() == 0   # 余白は空
    assert np.allclose(plain_color[plain > 0.99], 1.0)                                                       # 既定は白
    _, stroked = title.text_layer("AB", font, 20, stroke=2)
    assert (stroked > 0.5).sum() > (plain > 0.5).sum()                                                      # 縁取りで覆いが太る
    with pytest.raises(ValueError, match="variation"):
        title.load_font(None, 20, "Black")


def _square(size=21, side=7, color=(1.0, 0.0, 0.0)):
    cover = np.zeros((size, size), np.float32)
    lo = (size - side) // 2
    cover[lo:lo + side, lo:lo + side] = 1.0
    return np.broadcast_to(np.array(color, np.float32), (size, size, 3)).copy(), cover, lo


def test_the_sprite_puts_a_shadow_and_a_glow_under_the_cover():
    color, cover, lo = _square()
    rgb, alpha, box = title.sprite(color, cover, shadow=(2.0, 0.7), glow=((0, 0, 255), 2.0, 0.8))
    assert box == (lo, lo + 7, lo, lo + 7)
    inside = (slice(lo, lo + 7), slice(lo, lo + 7))
    assert np.allclose(alpha[inside], 1.0) and np.allclose(rgb[inside], [1.0, 0.0, 0.0])
    ring = (lo - 1, lo + 3)                                       # 覆いのすぐ外: 光りと影で半ば不透明、色は光り（青）だけ
    assert 0.0 < alpha[ring] < 1.0 and rgb[ring][2] > 0 and rgb[ring][0] == 0 and rgb[ring][1] == 0
    assert alpha[0, 0] < 0.01                                     # 遠い所は透けたまま
    plain = title.sprite(color, cover, shadow=(2.0, 0.7))[1]
    assert plain[ring] < alpha[ring] and 0 < plain[ring] < 0.7    # 影だけ（濃さ 0.7 まで）より、光りを足した方が濃い
    _, bare, _ = title.sprite(color, cover)
    assert bare[0, 0] == 0 and np.array_equal(bare, cover)        # 影も光りも無ければ覆いのまま
    with pytest.raises(ValueError, match="覆い"):
        title.sprite(color, np.zeros_like(cover))


def test_blit_centres_the_cover_box_and_skips_faint_or_offscreen_layers():
    color, cover, lo = _square()
    spr = title.sprite(color, cover)
    dst = np.zeros((40, 60, 3), np.float32)
    assert title.blit(dst, spr, 30.0, 20.0, 1.0)
    ys, xs = np.nonzero(dst[..., 0] > 0.5)                        # 覆いの範囲の中心が (30, 20) に来る（画素の丸めで 0.5 まで）
    assert abs((xs.min() + xs.max() + 1) / 2 - 30) <= 0.5 and abs((ys.min() + ys.max() + 1) / 2 - 20) <= 0.5 and len(xs) == 49
    half = np.zeros_like(dst)
    title.blit(half, spr, 30.0, 20.0, 0.5)
    assert half[..., 0].max() == pytest.approx(0.5)
    untouched = np.zeros_like(dst)
    assert not title.blit(untouched, spr, 30.0, 20.0, 0.003) and not title.blit(untouched, spr, 500.0, 20.0, 1.0)
    assert untouched.max() == 0
    assert np.array_equal(title.to_uint8(np.array([[[0.999, 1.5, -1.0]]], np.float32)), [[[254, 255, 0]]])   # 切り捨て


def _frames(directory, count=10, size=(64, 96)):
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    for T in range(1, count + 1):
        Image.new("RGB", size, (20 * T, 90, 150)).save(directory / f"f{T:04d}.png")
    return directory


def _badge(path):
    from PIL import Image
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    image.paste((255, 40, 40, 255), (2, 2, 6, 6))
    image.save(path)
    return path


LAYERS = [{"text": "Qx", "size": 16, "stroke": 1, "tracking": 0.1, "center": [32, 30], "rise": 4,
           "glow": {"color": [190, 130, 255], "blur": 2, "amount": 0.8}},
          {"image": "badge.png", "center": [32, 70], "opacity": 0.95}]


def _parameters(**changes):
    parameters = {"frames_dir": "frames", "range": [1, 10], "fps": 10, "start": 0.5, "fade_in": 0.2, "reference_width": 64,
                  "shadow": {"blur": 2, "amount": 0.7}, "layers": LAYERS}
    parameters.update(changes)
    return parameters


def _recipe(tmp_path, parameters, assemble=False):
    steps = [{"id": "title", "operation": "render.title", "parameters": parameters,
              "outputs": {"frames": "frames.json", "report": "title.json"}}]
    if assemble:
        steps.append({"id": "assemble", "operation": "render.assemble", "inputs": {"frames": "$steps.title.frames"},
                      "parameters": {}, "outputs": {"video": "out.mp4"}})
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "title", "inputs": {}, "steps": steps})
    return tmp_path / "recipe.json"


def _rgb(path):
    from PIL import Image
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def test_the_operation_copies_the_frames_before_the_title_and_fades_it_in(tmp_path):
    from model3d import contracts, executor
    _frames(tmp_path / "frames")
    _badge(tmp_path / "badge.png")
    result = executor.run_recipe(_recipe(tmp_path, _parameters()), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["title"]
    listing = contracts.load("frames", step["artifacts"]["frames"])
    out = Path(listing["directory"])
    assert out == (Path(step["artifacts"]["frames"]).parent / "frames").resolve() and listing["resolution"] == [64, 96]
    assert [fr["T"] for fr in listing["frames"]] == list(range(1, 11)) and listing["missing"] == []
    assert all(fr["sha256"] == sha256(out / fr["file"]) for fr in listing["frames"])
    for T in range(1, 6):                                          # 0.5 秒より前はそのまま写す
        assert (out / f"f{T:04d}.png").read_bytes() == (tmp_path / "frames" / f"f{T:04d}.png").read_bytes()
    source = {T: _rgb(tmp_path / "frames" / f"f{T:04d}.png") for T in range(6, 11)}
    titled = {T: _rgb(out / f"f{T:04d}.png") for T in range(6, 11)}
    resaved = {T: title.to_uint8(source[T].astype(np.float32) / 255.0) for T in source}
    assert np.array_equal(titled[6], resaved[6])                   # 0.5 秒ちょうどは濃さ 0（描き直すだけ。試作と同じ切り捨て）
    corner = (slice(88, 96), slice(0, 6))                          # 層から遠い所は変わらない
    assert all(np.array_equal(titled[T][corner], resaved[T][corner]) for T in titled)
    glyphs = (slice(18, 42), slice(16, 48))
    change = [np.abs(titled[T][glyphs].astype(int) - resaved[T][glyphs]).sum() for T in range(6, 11)]
    assert change[0] == 0 < change[1] < change[2] and change[2] == pytest.approx(change[4], rel=0.2)   # 0.2 秒で浮かび上がる
    badge = titled[10][66:74, 28:36].astype(int)
    assert badge[..., 0].max() > 200 and badge[..., 1].min() < 80                                   # 画像の層（赤）
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-title/0.1" and report["titled"] == {"count": 5, "first": 6, "last": 10}
    assert report["copied"] == 5 and report["scale"] == 1.0 and report["source"]["count"] == 10
    text = report["layers"][0]
    assert text["kind"] == "text" and text["chars"] == 2 and text["size_px"] == 16 and text["stroke_px"] == 1
    assert "Qx" not in json.dumps(report)                          # 題字の文字そのものは報告に書かない（sha256 と字数だけ）
    assert report["layers"][1]["kind"] == "image" and report["layers"][1]["opacity"] == 0.95
    observation = step["observations"][0]
    assert observation["kind"] == "title" and observation["titled"] == 5 and observation["first_titled"] == 6


def test_sizes_follow_the_frame_width(tmp_path):
    from model3d import executor
    _frames(tmp_path / "frames", count=2, size=(128, 192))
    _badge(tmp_path / "badge.png")
    result = executor.run_recipe(_recipe(tmp_path, _parameters(range=[1, 2], start=0.0, fade_in=0.0)), tmp_path / "runs")
    report = json.loads(Path(result["steps"]["title"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["scale"] == 2.0 and report["layers"][0]["size_px"] == 32 and report["layers"][0]["stroke_px"] == 2
    assert report["shadow"]["blur_px"] == 4.0 and report["layers"][0]["glow"]["blur_px"] == 4.0
    assert report["titled"]["count"] == 2                          # fade_in 0 は、start から濃さ 1


def test_the_titled_frames_feed_render_assemble(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    from model3d import executor
    _frames(tmp_path / "frames")
    _badge(tmp_path / "badge.png")
    result = executor.run_recipe(_recipe(tmp_path, _parameters(), assemble=True), tmp_path / "runs")
    assert result["status"] == "succeeded"
    observation = result["steps"]["assemble"]["observations"][0]
    assert observation["frames"] == 10 and observation["first"] == 1 and observation["last"] == 10


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def call(tmp_path, parameters, inputs=None, outputs=None):
    from model3d.ops import get
    declared = {k: tmp_path / v for k, v in (outputs or {"frames": "out.json"}).items()}
    return get("render.title").function(inputs or {}, parameters, declared, Context(tmp_path))


@pytest.mark.parametrize("changes, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"layers": []}, "layers"),
    ({"layers": [{"text": "A", "image": "badge.png", "size": 9, "center": [1, 1]}]}, "どちらか 1 つ"),
    ({"layers": [{"center": [1, 1]}]}, "どちらか 1 つ"),
    ({"layers": [{"text": "A", "center": [1, 1]}]}, "size"),
    ({"layers": [{"text": "  ", "size": 9, "center": [1, 1]}]}, "空でない"),
    ({"layers": [{"text": "A", "size": 9, "center": [1]}]}, "center"),
    ({"layers": [{"text": "A", "size": 9, "center": [1, 1], "color": [300, 0, 0]}]}, "color"),
    ({"layers": [{"text": "A", "size": 9, "center": [1, 1], "opacity": 1.5}]}, "opacity"),
    ({"layers": [{"text": "A", "size": 9, "center": [1, 1], "glow": {"color": [1, 2, 3]}}]}, "glow"),
    ({"layers": [{"text": "A", "size": 9, "center": [1, 1], "font": "no/such.ttf"}]}, "書体のファイルがありません"),
    ({"layers": [{"text": "A", "size": 9, "center": [1, 1], "variation": "Black"}]}, "variation"),
    ({"layers": [{"image": "badge.png", "center": [1, 1], "font": "x.ttf"}]}, "文字の項目"),
    ({"layers": [{"image": "no-such.png", "center": [1, 1]}]}, "画像がありません"),
    ({"layers": [{"text": "A", "size": 9, "center": [1, 1], "shape": "round"}]}, "知らない項目"),
    ({"shadow": {"blur": 0, "amount": 0.7}}, "blur"),
    ({"start": None}, "start"),
    ({"range": [3, 1]}, "range"),
    ({"out_dir": "frames"}, "同じ"),
])
def test_the_operation_rejects_bad_settings(tmp_path, changes, message):
    _frames(tmp_path / "frames", count=3)
    _badge(tmp_path / "badge.png")
    parameters = _parameters(range=[1, 3], out_dir="titled")      # 既定の書き出し先（工程のフォルダ）はここでは元のコマと同じになる
    parameters.update({k: v for k, v in changes.items() if v is not None})
    for key in [k for k, v in changes.items() if v is None]:
        parameters.pop(key)
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters)


def test_the_operation_checks_the_rendered_frames_list(tmp_path):
    frames = _frames(tmp_path / "frames", count=3)
    listing = {"schema_version": "model3d-frames/0.1", "directory": str(frames.resolve()), "fps": 10, "resolution": [64, 96],
               "frames": [{"T": T, "f": T + 100, "file": f"f{T:04d}.png", "sha256": sha256(frames / f"f{T:04d}.png"),
                           "bytes": (frames / f"f{T:04d}.png").stat().st_size} for T in (1, 2, 3)], "missing": []}
    atomic_json(tmp_path / "frames.json", listing)
    base = {"start": 0.1, "reference_width": 64, "layers": [{"text": "A", "size": 20, "center": [32, 30]}]}
    call(tmp_path, dict(base, out_dir="titled"), {"frames": tmp_path / "frames.json"})
    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert [fr["f"] for fr in written["frames"]] == [101, 102, 103] and written["fps"] == 10    # 一覧の f と fps を引き継ぐ
    with pytest.raises(ToolError, match="一緒に使えません"):
        call(tmp_path, dict(base, range=[1, 3]), {"frames": tmp_path / "frames.json"})
    listing["frames"][1]["sha256"] = "0" * 64                     # 描いた後にコマが変わった
    atomic_json(tmp_path / "frames.json", listing)
    with pytest.raises(ToolError, match="違います"):
        call(tmp_path, dict(base, out_dir="titled2"), {"frames": tmp_path / "frames.json"})
    listing["missing"] = [4]
    atomic_json(tmp_path / "frames.json", listing)
    with pytest.raises(ToolError, match="描いていないコマ"):
        call(tmp_path, base, {"frames": tmp_path / "frames.json"})
