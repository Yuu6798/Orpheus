import ast
import json
import re

import numpy as np
import pytest

from model3d import executor
from model3d.io import ToolError, atomic_json
from model3d.ops import get
from model3d.sam3d_colab import notebook


def test_notebook_recipe_is_valid_and_contains_no_secrets(tmp_path):
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {
        "schema_version": "model3d-recipe/0.1",
        "name": "sam3d",
        "inputs": {},
        "steps": [{"id": "colab", "operation": "motion.extract_s3d_colab",
                   "outputs": {"notebook": "extract.ipynb"}}],
    })
    run = executor.run_recipe(recipe, tmp_path / "runs")
    assert run["status"] == "succeeded"
    artifact = run["steps"]["colab"]["artifacts"]["notebook"]
    with open(artifact, encoding="utf-8") as stream:
        written = json.load(stream)
    assert written == notebook()
    assert written["nbformat"] == 4
    assert written["metadata"]["accelerator"] == "GPU"
    code = [cell for cell in written["cells"] if cell["cell_type"] == "code"]
    assert len(code) == 4
    assert all(not cell["outputs"] and cell["execution_count"] is None for cell in code)
    source = "\n".join("".join(cell["source"]) for cell in code)
    assert 'video_path = ""' in source
    assert 'userdata.get("HF_TOKEN")' in source
    assert 'pred_keypoints_3d' in source and 'pred_cam_t' in source
    assert not re.search(r"hf_[A-Za-z0-9]{10,}", source)
    assert "file:///" not in source and "C:/Users" not in source
    for cell in code:
        ast.parse("".join(cell["source"]))


def test_unknown_settings_rejected(tmp_path):
    operation = get("motion.extract_s3d_colab").function
    with pytest.raises(ToolError, match="video_path"):
        operation({}, {"video_path": "private.mp4"}, {"notebook": tmp_path / "extract.ipynb"}, None)
    assert not (tmp_path / "extract.ipynb").exists()


def test_generated_foreground_and_full_image_boxes():
    cv2 = pytest.importorskip("cv2")
    code = next("".join(cell["source"]) for cell in notebook()["cells"] if "def person_box" in "".join(cell["source"]))
    box_function = next(node for node in ast.parse(code).body
                        if isinstance(node, ast.FunctionDef) and node.name == "person_box")
    sandbox = {"np": np, "cv2": cv2, "box_mode": "plain_background"}
    exec(compile(ast.Module(body=[box_function], type_ignores=[]), "colab_person_box", "exec"), sandbox)
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    box, full = sandbox["person_box"](image)
    assert full
    np.testing.assert_array_equal(box, [0, 0, 100, 100])
    image[25:75, 30:70] = [100, 110, 120]
    box, full = sandbox["person_box"](image)
    assert not full
    np.testing.assert_allclose(box, [26.8, 22, 73.2, 78])
    sandbox["box_mode"] = "full_image"
    box, full = sandbox["person_box"](image)
    assert full
    np.testing.assert_array_equal(box, [0, 0, 100, 100])


def test_generated_extraction_writes_reusable_mhr70_archive(tmp_path):
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace

    video = tmp_path / "clip"
    video.write_bytes(b"fixture video identity")
    rgb = np.zeros((10, 10, 3), dtype=np.uint8)
    frames = 4
    rgb_frames = (rgb + index for index in range(frames))

    def capture(path):
        stream = iter(rgb_frames)

        def read():
            image = next(stream, None)
            return image is not None, image

        return SimpleNamespace(read=read, release=lambda: None)

    def prediction(rgb):
        value = float(rgb[0, 0, 0])
        return {
            "pred_keypoints_3d": np.full((70, 3), value, dtype=np.float32),
            "pred_keypoints_2d": np.full((70, 2), value, dtype=np.float32),
            "pred_cam_t": np.full(3, value, dtype=np.float32),
            "bbox": np.array([1, 1, 9, 9], dtype=np.float32),
            "used_full_image_box": np.bool_(value == 3),
        }

    program = "".join(notebook()["cells"][-1]["source"])
    runtime = dict(
        Path=Path, video=video, output=tmp_path, hashlib=hashlib, json=json, np=np,
        box_mode="plain_background", intrinsics=np.eye(3, dtype=np.float32), sources={},
        sam_weights=("SAM", "test"), fov_weights=("FoV", "test"), torch=SimpleNamespace(__version__="test"),
        cv2=SimpleNamespace(__version__="test", VideoCapture=capture, COLOR_BGR2RGB=0,
                            cvtColor=lambda image, _: image), fps=24, frame_count=frames, frame_prediction=prediction,
    )
    exec(compile(program, "colab_full_extraction", "exec"), runtime)
    result = next(tmp_path.glob("*/keypoints.npz"))
    with np.load(result, allow_pickle=False) as arrays:
        np.testing.assert_array_equal(arrays["pred_keypoints_3d"][:, 0, 0], np.arange(frames))
        np.testing.assert_array_equal(arrays["pred_cam_t"][:, 0], np.arange(frames))
        np.testing.assert_array_equal(arrays["frame_index"], np.arange(frames))
        np.testing.assert_array_equal(arrays["t"], np.arange(frames) / 24)
        np.testing.assert_array_equal(arrays["valid"], [True] * frames)
        np.testing.assert_array_equal(arrays["used_full_image_box"], [False, False, False, True])
        assert arrays["cam_int"].shape == (frames, 1, 3, 3)

    rgb_frames = (rgb + index for index in range(frames))
    runtime["frame_prediction"] = lambda _: pytest.fail("cached rows should be reused")
    exec(compile(program, "colab_full_extraction", "exec"), runtime)

    cache = result.parent / "frames"
    (cache / "0000001.npz").write_bytes(b"corrupt")
    np.savez(cache / "0000002.npz", **{**prediction(rgb + 2), "pred_cam_t": np.zeros(2, dtype=np.float32)})
    recomputed = []

    def track_prediction(image):
        recomputed.append(int(image[0, 0, 0]))
        return prediction(image)

    rgb_frames = (rgb + index for index in range(frames))
    runtime["frame_prediction"] = track_prediction
    exec(compile(program, "colab_full_extraction", "exec"), runtime)
    assert recomputed == [1, 2]
