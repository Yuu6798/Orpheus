"""目の動きの区間の説明の字（render.eye_captions）: 帯・説明・題の入れ方と大きさの合わせ方、コマの読み書き（Pillow だけ）、工程の設定と悪い設定を
止めること。本物のコマでの照合は docs/model3d.md。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import eye_captions
from model3d.io import atomic_json

TIMELINE = {"schema_version": "model3d-eye-timeline/0.1", "fps": 24, "count": 4,
            "frames": [{"frame": f, "tau": 0.1 * f, "section": 0 if f < 3 else 1, "gaze": [0.0, 0.0], "keys": {}} for f in (1, 2, 3, 4)],
            "sections": [{"section": 0, "frames": [1, 2], "tau": [0, 1], "speed": 1.0, "view": "front", "caption": "LOOK"},
                         {"section": 1, "frames": [3, 4], "tau": [1, 2], "speed": 0.5, "view": "front", "caption": "BLINK SLOW"}]}


def _frames(folder, size=(192, 108)):
    folder.mkdir(parents=True, exist_ok=True)
    for f in (1, 2, 3, 4):
        Image.new("RGB", size, (120, 120, 120)).save(folder / f"f{f:04d}.png")


def test_bar_caption_and_title_scale_with_the_frame():
    style = dict(eye_captions.STYLE)
    image = Image.new("RGB", (960, 540), (120, 120, 120))                    # 基準の幅 1920 の半分
    out = np.asarray(eye_captions.draw(image, "LOOK", "TITLE", style, *eye_captions.fonts(style, width=960)))
    bar = round(84 * 0.5)
    assert abs(int(out[-1, 0, 0]) - round(120 * (1 - 150 / 255))) <= 1        # 帯で暗くなる
    assert out[540 - bar - 1, 0].tolist() == [120, 120, 120]                  # 帯の上はそのまま
    assert (out[540 - bar:, :, 0] > 200).any()                                # 説明の白い字
    assert (out[:bar, :200, 0] > 200).any()                                   # 左上の題
    plain = np.asarray(eye_captions.draw(image, "", None, style, *eye_captions.fonts(style, width=960)))
    assert not (plain[:bar, :200, 0] > 200).any() and not (plain[540 - bar:, :, 0] > 200).any()


def test_caption_frames_writes_every_frame_with_its_section(tmp_path):
    _frames(tmp_path / "raw")
    written = eye_captions.caption_frames(TIMELINE, tmp_path / "raw", tmp_path / "out", "f{frame:04d}.png", eye_captions.STYLE)
    assert written == ["f0001.png", "f0002.png", "f0003.png", "f0004.png"]
    a, b = (np.asarray(Image.open(tmp_path / "out" / name)) for name in ("f0002.png", "f0003.png"))
    assert (a != b).any()                                                     # 区間が違えば説明も違う
    (tmp_path / "raw" / "f0004.png").unlink()
    with pytest.raises(ValueError, match="f0004.png"):
        eye_captions.caption_frames(TIMELINE, tmp_path / "raw", tmp_path / "out2", "f{frame:04d}.png", eye_captions.STYLE)


def test_captions_refuse_frames_too_short_for_the_bar_and_text(tmp_path):
    style = dict(eye_captions.STYLE)
    assert eye_captions.layout(style, 960, 540) == (0.5, 42, 540 - 34, 12)                 # 大きさはコマの幅に合わせる
    with pytest.raises(ValueError, match="入りません"):                                     # とても横長: 帯（高さ 168）がコマより高い
        eye_captions.layout(style, 3840, 100)
    _frames(tmp_path / "raw", size=(3840, 100))
    with pytest.raises(ValueError, match="入りません"):                                     # 書く前に止める（切れた絵を書かない）
        eye_captions.caption_frames(TIMELINE, tmp_path / "raw", tmp_path / "out", "f{frame:04d}.png", style)
    assert not (tmp_path / "out").exists()


def _recipe(tmp_path, parameters):
    atomic_json(tmp_path / "timeline.json", TIMELINE)
    step = {"id": "cap", "operation": "render.eye_captions", "inputs": {"timeline": "$inputs.timeline"}, "parameters": parameters,
            "outputs": {"report": "captions.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "cap", "inputs": {"timeline": {"path": "timeline.json"}},
                                           "steps": [step]})
    return tmp_path / "recipe.json"


def test_eye_captions_step_writes_to_another_folder(tmp_path):
    from model3d import executor
    _frames(tmp_path / "raw")
    result = executor.run_recipe(_recipe(tmp_path, {"frames_dir": str(tmp_path / "raw"), "title": "EYES", "bar_alpha": 200}), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["cap"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["written"] == 4 and report["style"]["bar_alpha"] == 200 and report["sections"][1] == {"frames": [3, 4], "caption": "BLINK SLOW"}
    assert len(list(Path(report["out_dir"]).glob("f*.png"))) == 4 and np.asarray(Image.open(tmp_path / "raw" / "f0001.png")).max() == 120


@pytest.mark.parametrize("change, message", [
    ({"frames_dir": "nowhere"}, "frames_dir"), ({"out_dir": "RAW"}, "別のフォルダ"), ({"name": "frame.png"}, "name"),
    ({"font": "missing.ttf"}, "font"), ({"bar_alpha": 300}, "bar_alpha"), ({"caption_at": [1]}, "caption_at"), ({"spin": 1}, "知らないパラメーター")])
def test_eye_captions_refuses_bad_settings(tmp_path, change, message):
    from model3d import executor
    _frames(tmp_path / "raw")
    parameters = {"frames_dir": str(tmp_path / "raw"), **change}
    if parameters.get("out_dir") == "RAW":
        parameters["out_dir"] = str(tmp_path / "raw")
    if change.get("frames_dir") == "nowhere":
        parameters["frames_dir"] = str(tmp_path / "nowhere")
    result = executor.run_recipe(_recipe(tmp_path, parameters), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["cap"]["error"]


def test_eye_captions_needs_a_timeline(tmp_path):
    from model3d import executor
    _frames(tmp_path / "raw")
    recipe = _recipe(tmp_path, {"frames_dir": str(tmp_path / "raw")})
    atomic_json(tmp_path / "timeline.json", {"schema_version": "other"})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "failed" and "model3d-eye-timeline/0.1" in result["steps"]["cap"]["error"]
