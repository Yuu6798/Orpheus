from html.parser import HTMLParser
from urllib.parse import unquote

import pytest
from PIL import Image

from model3d.character_review_sheet import VIEW_LABELS, write_character_review_sheet
from model3d.io import ToolError


class SheetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.words = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.words.append(data)


def review_files(root):
    rows = []
    for angle in VIEW_LABELS:
        image = root / "比較 #1" / f"{angle}%.png"
        image.parent.mkdir(exist_ok=True)
        Image.new("RGB", (8, 6), "#7f5f64").save(image)
        rows.append({"event": "turn", "view": angle, "before": image, "after": image})
    return {
        "references": {"right": rows[4]["before"]},
        "renders": rows,
        "events": [{"id": "turn", "label": '<指先 & "肩">', "region": "手首",
                    "frame": 13, "observations": ["内側の面が交差 <候補>"]}],
        "motion_reference": None, "fps": 24, "first_frame": 1,
    }


def read_sheet(path):
    parser = SheetParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def test_matches_actual_angles_and_links_scoped_evidence(tmp_path):
    request = review_files(tmp_path)
    request["events"][0]["control_label"] = "輪郭をオフ"
    request["events"][0]["focus_view"] = "right"
    for row in request["renders"]:
        row["control_off"] = row["after"]
    page = read_sheet(write_character_review_sheet(tmp_path, **request))
    text = "".join(page.words)
    assert "0.500 秒 · フレーム 13 · 24 fps" in text
    assert request["events"][0]["label"] in text and "確認部位　手首" in text
    assert "内側の面が交差 <候補>" in text and "修正後・制御オフ（輪郭をオフ）" in text
    angle_link = "#event-1-right"
    assert sum(attrs.get("href") == angle_link for tag, attrs in page.tags if tag == "a") == 2
    assert any(tag == "tr" and attrs.get("id") == angle_link[1:] for tag, attrs in page.tags)
    assert "右真横 90°の比較へ" in text
    assert text.count("この向きの設定画なし") == 5
    images = [attrs for tag, attrs in page.tags if tag == "img"]
    assert len(images) == 6 * 3 + 1
    refs = [img for img in images if img["alt"].startswith("設定画")]
    assert [r["alt"] for r in refs] == ["設定画・右真横 90°"]
    image_links = {attrs["href"] for tag, attrs in page.tags if tag == "a"}
    for img in images:
        assert img["src"] in image_links and (tmp_path / unquote(img["src"])).is_file()
        assert "%23" in img["src"] and "%25" in img["src"]
    assert not any(tag == "script" for tag, _ in page.tags)


def test_local_video_link_and_movable_published_directory(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    request = review_files(staging)
    video = tmp_path / "振り付け & 参考.mp4"
    video.touch()
    request["motion_reference"] = video
    path = write_character_review_sheet(staging, **request)
    staging.rename(tmp_path / "published")
    page = read_sheet(tmp_path / "published" / path.name)
    links = [attrs["href"] for tag, attrs in page.tags if tag == "a"]
    assert video.as_uri() in links
    assert "振付のリファレンス動画を開く" in page.words
    assert all((tmp_path / "published" / unquote(url)).is_file()
               for url in links if url.startswith("./"))
    assert all(not url.startswith(("https:", "http:")) for url in links)


@pytest.mark.parametrize("bad", ["missing", "duplicate", "control", "outside"])
def test_incomplete_or_unsafe_inputs_do_not_publish_a_report(tmp_path, bad):
    root = tmp_path / "review"
    root.mkdir()
    request = review_files(root)
    rows = request["renders"]
    if bad == "missing":
        rows.pop()
    elif bad == "duplicate":
        rows.append(rows[0].copy())
    elif bad == "control":
        rows[0]["control_off"] = rows[0]["after"]
    else:
        outside = tmp_path / "outside.png"
        Image.new("RGB", (8, 6)).save(outside)
        rows[0]["before"] = outside
    with pytest.raises(ToolError):
        write_character_review_sheet(root, **request)
    assert not (root / "index.html").exists()
