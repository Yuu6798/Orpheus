"""Keep the recipe's report file separate from its rendered views directory."""
from types import SimpleNamespace

import pytest

from model3d import character_review
from model3d.io import read_json
from model3d.ops.character_review_ops import character_views


@pytest.mark.parametrize(("filename", "directory"), [("review.json", "views"), ("views", "views.views")])
def test_character_views_report_and_render_directory_are_distinct(tmp_path, monkeypatch, filename, directory):
    def render(spec, out, blender, *, roundtrip_report):
        out.mkdir()
        index = out / "index.html"
        index.write_text("<title>Review</title>", encoding="utf-8")
        return {"index": str(index)}

    monkeypatch.setattr(character_review, "run", render)
    report = tmp_path / filename
    context = SimpleNamespace(step_dir=tmp_path, tools={"blender": "blender"})

    character_views({"spec": tmp_path / "spec.json"}, {}, {"report": report}, context)

    index = tmp_path / directory / "index.html"
    assert index.is_file()
    assert read_json(report) == {"index": str(index)}
