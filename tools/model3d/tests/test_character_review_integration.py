"""Exercise the public CLI, rendered image evidence and local report as one workflow."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

import numpy as np
from PIL import Image
import pytest

from model3d.character_review_sheet import VIEW_LABELS


@pytest.mark.skipif(not os.getenv("MODEL3D_BLENDER"), reason="set MODEL3D_BLENDER to the Blender executable")
def test_character_review_cli_six_views_at_expression_peak_and_neighboring_pose(tmp_path):
    blender = os.environ["MODEL3D_BLENDER"]
    scene = Path(__file__).with_name("data") / "character_review_scene.py"
    subprocess.run([blender, "--background", "--factory-startup", "--python-exit-code", "1",
                    "--python", str(scene), "--", str(tmp_path)], check=True, capture_output=True)
    guide = tmp_path / "guide #1.png"
    Image.new("RGB", (32, 40), "#b66d42").save(guide)
    model = {"armature": "rig", "left": "left", "right": "right", "anchor": "anchor"}
    spec = {
        "before": dict(model, path="before.blend"), "after": dict(model, path="after.blend"),
        "references": {"left": guide.name}, "scale": 1.4, "size": 64,
        "events": [{"id": "blink", "label": "Blink", "region": "Eye",
                    "peak": {"object": "pin", "shape_key": "lift", "range": [1, 4]},
                    "framing": {"anchor": "focus", "scale": 1.2, "offset_z": 0.08},
                    "disable": {"object": "pin", "shape_key": "lift"}},
                   {"id": "turn", "label": "Turn", "frame": 3}],
    }
    spec_path = tmp_path / "study.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    inputs = [spec_path, guide, tmp_path / "before.blend", tmp_path / "after.blend"]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    output = tmp_path / "reviews"
    result = subprocess.run([sys.executable, "-m", "model3d.cli", "character-review", str(spec_path),
                             "--out", str(output), "--blender", blender], capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    index = Path(json.loads(result.stdout)["index"])
    package = index.parent
    assert package.parent == output and package.name.startswith("review-")
    html = index.read_text(encoding="utf-8")
    images = re.findall(r'<img src="([^"]+)"', html)
    assert len(images) == 6 * 5 + 2  # Two events, one left-side reference in each.
    for url in images:
        assert url.startswith("./") and f'href="{url}"' in html and (package / unquote(url)).is_file()
    assert (package / "references" / "left.png").read_bytes() == guide.read_bytes()
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == hashes[p] for p in inputs)
    events = json.loads((package / "events.json").read_text(encoding="utf-8"))
    assert [(e["id"], e["frame"]) for e in events] == [("blink", 2), ("turn", 3)]

    views = package / "views"
    cameras = json.loads((views / "cameras.json").read_text(encoding="utf-8"))["cameras"]
    assert len(cameras) == 6 * 2 * 2
    records = {(c["event"], c["view"], c["variant"]): c for c in cameras}

    def pixels(event, view, variant):
        with Image.open(views / event / view / f"{variant}.png") as im:
            return np.asarray(im.convert("RGBA")).astype(int)

    for event, frame in (("blink", 2), ("turn", 3)):
        for view in VIEW_LABELS:
            original = records[event, view, "before"]
            revised = records[event, view, "after"]
            assert original["frame"] == revised["frame"] == frame
            assert original["seconds"] == revised["seconds"] == pytest.approx((frame - 1) / 24)
            close = event == "blink"
            assert original["ortho_scale_m"] == revised["ortho_scale_m"] == (1.2 if close else 1.4)
            assert original["anchor_before"] == revised["anchor_before"] == ("focus" if close else "anchor")
            center = [.18, 0, .08] if close else [0, 0, 0]
            np.testing.assert_allclose(original["target_m"], center, atol=1e-5)
            np.testing.assert_allclose(revised["target_m"], [.8 - center[1], -.4 + center[0], .5 + center[2]], atol=1e-5)
            # The revised rig is yawed 90 degrees and translated in world space.
            # The same anatomical camera should project its unchanged local pose identically.
            x, y, z = original["direction"]
            np.testing.assert_allclose(revised["direction"], [-y, x, z], atol=1e-5)
            a, b = pixels(event, view, "before"), pixels(event, view, "after")
            assert np.abs(a - b).mean() < 2
            assert 0 < (b[:, :, 3] > 0).sum() < 0.55 * 64**2  # excludes the large stage prop
    assert not np.array_equal(pixels("blink", "left", "after"), pixels("blink", "right", "after"))
    assert not np.array_equal(pixels("blink", "left", "after"), pixels("blink", "left", "disabled"))
    assert not np.array_equal(pixels("blink", "left", "after"), pixels("turn", "left", "after"))
    def green_pin_row(event, variant):
        image = pixels(event, "left", variant)
        mask = (image[:, :, 1] > image[:, :, 0] + 70) & (image[:, :, 3] > 128)
        assert mask.any()
        return np.where(mask)[0].mean()

    # Turning the key off lowers the pin in the close-up. The next pose must
    # retain its partially raised pin under the original, wider framing.
    assert green_pin_row("blink", "after") + 5 < green_pin_row("blink", "disabled")
    assert green_pin_row("turn", "after") == pytest.approx(green_pin_row("turn", "before"), abs=.5)
