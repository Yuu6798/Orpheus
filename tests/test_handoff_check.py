"""届いた絵の機械的な検査（mv handoff check）: 範囲の外の差・大きさ・範囲の中の変化・範囲の外を戻した絵・終了コード。合成の絵だけ。"""
import json

import numpy as np
import pytest
from PIL import Image

from fx_synth import run_cli
from mv_pipeline import handoff
from mv_pipeline.io import PipelineError


def write(path, array):
    Image.fromarray(np.clip(array, 0, 255).astype(np.uint8)).save(path)
    return path


@pytest.fixture
def pair(tmp_path):
    rng = np.random.default_rng(7)
    base = rng.integers(0, 256, (60, 80, 3)).astype(np.float32)
    delivered = base.copy()
    delivered[20:30, 30:50] = 255 - delivered[20:30, 30:50]           # 範囲の中を描き替えた
    return write(tmp_path / "base.png", base), write(tmp_path / "delivered.png", delivered), base, delivered


def test_changes_inside_the_regions_pass(pair, tmp_path):
    base_path, delivered_path, base, delivered = pair
    report, out = handoff.run(base_path, delivered_path, ["25,15,55,35"], restore=tmp_path / "restored.png", feather=2)
    m = report["measured"]
    assert report["status"] == "pass" and m["outside"]["changed_over_tolerance"] == 0 and m["inside"]["changed_over_tolerance"] > 0
    assert json.loads((out / "handoff_report.json").read_text(encoding="utf-8"))["status"] == "pass"
    restored = np.asarray(Image.open(tmp_path / "restored.png"), dtype=np.float32)
    assert (restored[:15] == base[:15]).all() and (restored[:, :25] == base[:, :25]).all()   # 範囲の外は元の絵
    assert (restored[24:26, 38:42] == delivered[24:26, 38:42]).all()                        # 範囲の芯は届いた絵


def test_changes_outside_fail_with_a_box(pair, tmp_path):
    base_path, _, base, delivered = pair
    delivered = delivered.copy()
    delivered[50:55, 5:9] = 0                                            # 範囲の外も変わった
    stray = write(tmp_path / "stray.png", delivered)
    report, _ = handoff.run(base_path, stray, ["25,15,55,35"])
    outside = report["measured"]["outside"]
    assert report["status"] == "fail" and outside["changed_over_tolerance"] > 0
    assert outside["big_change_box"][0] >= 5 and outside["big_change_box"][2] <= 8 and outside["big_change_box"][1] >= 50
    loose, _ = handoff.run(base_path, stray, ["25,15,55,35"], max_changed=100)
    assert loose["status"] == "pass"                                     # 上限までは許す


def test_regions_width_scales_the_regions(pair):
    base_path, delivered_path, _, _ = pair
    report, _ = handoff.run(base_path, delivered_path, ["50,30,110,70"], regions_width=160)    # 160 画素の絵で書いた範囲（半分にする）
    assert report["status"] == "pass" and report["settings"]["scale"] == 0.5


def test_size_and_unchanged_regions(pair, tmp_path):
    base_path, delivered_path, base, _ = pair
    bigger = write(tmp_path / "bigger.png", np.repeat(np.repeat(base, 2, axis=0), 2, axis=1))
    report, _ = handoff.run(base_path, bigger, ["25,15,55,35"])
    assert report["status"] == "fail" and report["size"]["same"] is False and "measured" not in report
    with pytest.raises(PipelineError, match="大きさ"):
        handoff.run(base_path, bigger, ["25,15,55,35"], restore=tmp_path / "x.png")
    for lossy in ("x.jpg", "x.JPEG", "x.webp"):                                        # 可逆の PNG だけ（検査の前に止める）
        with pytest.raises(PipelineError, match="PNG"):
            handoff.run(base_path, delivered_path, ["25,15,55,35"], restore=tmp_path / lossy)
        assert not (tmp_path / lossy).exists()
    shrunk, _ = handoff.run(base_path, bigger, ["25,15,55,35"], allow_resize=True)
    assert shrunk["size"]["resized"] is True and shrunk["status"] == "warn"           # 縮めて比べた（元に戻る）
    same, _ = handoff.run(base_path, base_path, ["25,15,55,35"])
    assert same["status"] == "warn" and "変わっていない" in same["reasons"][0]          # 頼んだ所が変わっていない


def test_helpers_match_the_definitions():
    mask = np.zeros((9, 9), np.float32)
    mask[2:7, 2:7] = 1
    eroded = handoff.erode(mask, 3)
    assert eroded.sum() == 9 and eroded[4, 4] == 1                       # 3×3 の最小で 1 画素ずつ縮む
    edge = np.ones((5, 5), np.float32)
    assert handoff.erode(edge, 3).sum() == 25                            # 画像の外は 1（縁は縮まない）
    blurred = handoff.gaussian_blur(mask, 1.0)
    assert blurred[4, 4] < 1 and blurred[0, 0] > 0                    # ぼけて、縁の外へも広がる
    cv2 = pytest.importorskip("cv2")
    assert (handoff.erode(mask, 3) == cv2.erode(mask, np.ones((3, 3), np.uint8))).all()
    assert np.abs(handoff.gaussian_blur(mask, 1.5) - cv2.GaussianBlur(mask, (0, 0), 1.5)).max() < 1e-6


def test_erode_is_the_window_minimum():
    rng = np.random.default_rng(11)
    mask = (rng.random((23, 31)) > 0.2).astype(np.float32)
    for size in (1, 3, 7):
        half = size // 2
        padded = np.pad(mask, half, constant_values=1.0)
        expected = np.array([[padded[y:y + size, x:x + size].min() for x in range(31)] for y in range(23)])
        assert (handoff.erode(mask, size) == expected).all()


def test_bad_regions_stop():
    for text in ("1,2,3", "5,5,1,9", "a,b,c,d"):
        with pytest.raises(PipelineError, match="--region"):
            handoff.parse_region(text)


def test_cli_exit_codes(pair, tmp_path):
    base_path, delivered_path, _, delivered = pair
    code, result, err = run_cli("handoff", "check", base_path, delivered_path, "--region", "25,15,55,35", "--out", tmp_path / "ok")
    assert code == 0 and result["status"] == "pass", err
    delivered = delivered.copy()
    delivered[0:3, 0:3] = 0
    stray = write(tmp_path / "stray.png", delivered)
    code, result, err = run_cli("handoff", "check", base_path, stray, "--region", "25,15,55,35", "--out", tmp_path / "ng")
    assert code == 1 and result["status"] == "fail" and result["outside_changed"] > 0
    code, _, err = run_cli("handoff", "check", base_path, delivered_path, "--out", tmp_path / "none")
    assert code == 1 and "--region" in err
