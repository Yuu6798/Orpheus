"""scripts/suno_align.py（Suno の語タイミング → 共通形式）。入力はすべて合成の JSON。"""
import importlib.util
import json
from pathlib import Path

import pytest

from mv_pipeline import cli
from mv_pipeline.demo import create
from mv_pipeline.io import read_json

spec = importlib.util.spec_from_file_location("suno_adapter", Path(__file__).resolve().parents[1] / "scripts/suno_align.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
convert = module.convert


def w(text, start, end, success=True):
    return {"word": text, "start_s": start, "end_s": end, "success": success, "p_align": 1.0 if success else 0.1}


def analysis(lines, duration=20.0, source_start=0.0):
    rate = 48000
    return {"audio": {"sha256": "a" * 64, "sample_rate": rate, "sample_count": int(duration * rate),
                      "duration_s": duration, "source_start_s": source_start},
            "lyrics": [{"id": f"lyric-{i:03d}", "text": text, "alignment_text": text} for i, text in enumerate(lines, 1)]}


def response(words, status="complete"):
    return {"audio_id": "synthetic-take", "status": status, "words": words}


def spans(rows):
    return [(r["timing_source"], r["start_s"], r["end_s"]) for r in rows]


TWO_LINES = [w("[Intro]\n\n\n[Verse]\nあ", 0.5, 0.8), w("おい", 0.8, 1.2), w(" そら", 1.3, 2.0),
             w("\n\n[Chorus]\nしろい", 2.5, 3.0), w(" くも", 3.0, 3.4), w(" なが", 3.5, 3.9), w("れる\n\n", 3.9, 4.6)]


def test_tags_and_newlines_split_lines_and_keep_inner_spaces():
    out, _ = convert(analysis(["あおい そら", "しろい くも ながれる"]), response(TWO_LINES), 0.0)
    assert spans(out["lyrics"]) == [("model", 0.5, 2.0), ("model", 2.5, 4.6)]
    assert [r["text"] for r in out["lyrics"]] == ["あおい そら", "しろい くも ながれる"]
    assert out["audio_sha256"] == "a" * 64 and out["offset_s"] == 0.0
    assert out["tool"]["name"] == "suno-lyrics-alignment" and out["tool"]["version"]
    assert out["suno"] == {"audio_id": "synthetic-take", "status": "complete"} and out["raw_words"] == TWO_LINES


def test_newline_at_the_end_of_a_word_also_ends_the_line():
    words = [w("あおい", 0.5, 1.0), w(" そら\n", 1.0, 2.0), w("しろい", 2.5, 3.0), w(" くも\n\n", 3.0, 3.5)]
    out, _ = convert(analysis(["あおい そら", "しろい くも"]), response(words), 0.0)
    assert spans(out["lyrics"]) == [("model", 0.5, 2.0), ("model", 2.5, 3.5)]


def test_unplaced_word_at_a_line_edge_is_interpolated_but_not_inside():
    words = [w("あ", 0.5, 0.8), w("おい", 0.8, 1.2, success=False), w(" そら", 1.3, 2.0),
             w("\nしろい", 2.4, 3.0, success=False), w(" くも", 3.0, 3.4)]
    out, timed = convert(analysis(["あおい そら", "しろい くも"]), response(words), 0.0)
    assert spans(out["lyrics"]) == [("model", 0.5, 2.0), ("interpolated", 2.4, 3.4)]
    assert "reason" not in out["lyrics"][0] and "推した" in out["lyrics"][1]["reason"]
    assert [line["interpolated_words"] for line in timed["lines"]] == [[1], [0]]


@pytest.mark.parametrize("lines", [["あおい そら"], ["あおい そら", "しろい くも ながれる", "あおい そら"]])
def test_line_count_mismatch_stays_unknown(lines):
    out, timed = convert(analysis(lines), response(TWO_LINES), 0.0)
    assert all(r["timing_source"] == "unknown" and r["start_s"] is None and r["end_s"] is None for r in out["lyrics"])
    assert all("行数が違う" in r["reason"] for r in out["lyrics"])
    assert all(line["words"] is None for line in timed["lines"])


def test_one_different_line_makes_every_line_unknown():
    out, _ = convert(analysis(["あおい そら", "くろい くも ながれる"]), response(TWO_LINES), 0.0)
    assert [r["timing_source"] for r in out["lyrics"]] == ["unknown", "unknown"]
    assert "Suno: しろい くも ながれる" in out["lyrics"][1]["reason"]
    assert "ほかの行" in out["lyrics"][0]["reason"]


def test_spacing_width_and_punctuation_differences_still_match():
    words = [w("Blue", 0.5, 0.9), w(" sky", 0.9, 1.5), w("\nあおい", 2.0, 2.5), w(" そら", 2.5, 3.0)]
    out, timed = convert(analysis(["ｂｌｕｅ、SKY!", "あおい　そら"]), response(words), 0.0)
    assert spans(out["lyrics"]) == [("model", 0.5, 1.5), ("model", 2.0, 3.0)]
    assert [r["text"] for r in out["lyrics"]] == ["ｂｌｕｅ、SKY!", "あおい　そら"]  # 歌詞の行の字のまま
    # 字面が違う行は、つないだ語が text と一致しないので語に分けない
    assert all(line["words"] is None and "字面" in line["reason"] for line in timed["lines"])


def test_word_crossing_a_line_boundary_does_not_guess_the_boundary():
    words = [w("あおい", 0.5, 1.0), w(" そ", 1.0, 1.4), w("ら\nしろ", 1.4, 2.6), w("い", 2.6, 3.0),
             w("\nくも", 3.5, 4.0)]
    out, _ = convert(analysis(["あおい そら", "しろい", "くも"]), response(words), 0.0)
    assert spans(out["lyrics"]) == [("unknown", None, None), ("unknown", None, None), ("model", 3.5, 4.0)]
    assert "またいで" in out["lyrics"][0]["reason"]


CHORUS_TWICE = [w("[Chorus]\nあおい", 1.0, 1.6), w(" そら", 1.6, 2.4), w("\nしろい", 4.0, 4.6), w(" くも", 4.6, 5.4),
                w("\n\n[Chorus]\nあおい", 11.0, 11.6), w(" そら", 11.6, 12.4), w("\nしろい", 14.0, 14.6), w(" くも\n", 14.6, 15.4)]


def test_clip_start_is_taken_from_the_analysis_and_picks_lines_by_time():
    # mv init --start 10 --duration 8 の切り出しに、2 回目のサビだけを歌詞として書いた（1 回目と同じ字）
    out, timed = convert(analysis(["あおい そら", "しろい くも"], duration=8.0, source_start=10.0), response(CHORUS_TWICE), 0.0)
    assert out["offset_s"] == -10.0
    assert spans(out["lyrics"]) == [("model", 11.0, 12.4), ("model", 14.0, 15.4)]  # Suno の秒のまま
    assert [(line["start"], line["end"]) for line in timed["lines"]] == [(1.0, 2.4), (4.0, 5.4)]  # 切り出しの秒
    assert [word["t"] for word in timed["lines"][0]["words"]] == [1.0, 1.6]


def test_source_offset_is_added_to_the_clip_start():
    out, timed = convert(analysis(["あおい そら", "しろい くも"], duration=8.0, source_start=10.0), response(CHORUS_TWICE), 0.5)
    assert out["offset_s"] == -9.5 and timed["lines"][0]["start"] == 1.5


def test_clip_needs_known_times_just_outside_it():
    words = [w("あおい", 0.5, 1.0), w(" そら", 1.0, 1.5), w("\nしろ", 2.0, 2.5), w("い\nくろ", 2.5, 3.5), w("い", 3.5, 4.0),
             w("\nあおい", 11.0, 11.6), w(" そら", 11.6, 12.4), w("\nしろい", 13.0, 13.6), w(" くも", 13.6, 14.4)]
    out, _ = convert(analysis(["あおい そら", "しろい くも"], duration=8.0, source_start=10.0), response(words), 0.0)
    # 切り出しのすぐ前の行（くろい）の秒が分からないので、切り出しに入る行を決められない
    assert all(r["timing_source"] == "unknown" and "すぐ外" in r["reason"] for r in out["lyrics"])


def test_same_count_but_outside_the_clip_is_unknown():
    words = [w("あおい", 0.5, 1.0), w(" そら", 1.0, 1.5), w("\nしろい", 4.5, 5.0), w(" くも", 5.0, 5.5),
             w("\nくろい", 6.0, 6.5), w(" くも", 6.5, 7.0)]
    out, timed = convert(analysis(["あおい そら", "しろい くも", "くろい くも"], duration=5.0), response(words), 0.0)
    assert spans(out["lyrics"]) == [("model", 0.5, 1.5), ("unknown", None, None), ("unknown", None, None)]
    assert all("範囲の外" in r["reason"] for r in out["lyrics"][1:])
    assert timed["lines"][1]["words"] is None


def test_words_join_to_the_line_text_for_dance_spec():
    out, timed = convert(analysis(["あおい そら", "しろい くも ながれる"]), response(TWO_LINES), 0.25)
    line = timed["lines"][1]
    assert line["words"] == [{"text": "しろい", "t": 2.75}, {"text": " くも", "t": 3.25}, {"text": " なが", "t": 3.75},
                             {"text": "れる", "t": 4.15}]
    assert all("".join(word["text"] for word in l["words"]) == l["text"] for l in timed["lines"])
    assert (line["start"], line["end"], line["interpolated_words"]) == (2.75, 4.85, [])
    assert timed["review_status"] == "unreviewed" and timed["offset_s"] == 0.25


@pytest.mark.parametrize("status", ["processing", "unavailable"])
def test_unfinished_alignment_is_an_error(status):
    with pytest.raises(ValueError, match=status):
        convert(analysis(["あおい そら"]), response(TWO_LINES, status), 0.0)


def test_output_passes_mv_import_alignment(tmp_path, capsys):
    project = create(tmp_path / "song", 320, 180)  # 合成の 6 秒の曲。歌詞は「窓の向こうへ」「声をつないで」
    words = [w("[Verse]\n窓", 1.0, 1.4), w("の", 1.4, 1.6), w("向こう", 1.6, 2.4), w("へ", 2.4, 3.0, success=False),
             w("\n\n[Chorus]\n声", 3.4, 3.8), w("を", 3.8, 4.0), w("つない", 4.0, 4.8), w("で\n", 4.8, 5.5)]
    suno = tmp_path / "suno.json"
    suno.write_text(json.dumps(response(words), ensure_ascii=False), encoding="utf-8")
    out, timed = tmp_path / "alignment.json", tmp_path / "words.json"
    module.main(["--suno", str(suno), "--analysis", str(project / "analysis.json"), "--output", str(out),
                 "--source-offset-s", "-0.25", "--words-output", str(timed)])
    assert "interpolated 1" in capsys.readouterr().err
    assert cli.main(["import-alignment", str(project), str(out)]) == 0
    raw = read_json(project / "work/analysis.raw.json")
    assert [(r["timing_source"], r["review_status"], r["start_s"], r["end_s"]) for r in raw["lyrics"]] == [
        ("interpolated", "unreviewed", 0.75, 2.75), ("model", "unreviewed", 3.15, 5.25)]
    assert raw["provenance"][-1] == {"stage": "alignment", "tool": "suno-lyrics-alignment",
                                      "version": "not reported by Suno", "model": None}
    assert read_json(timed)["lines"][1]["words"][0] == {"text": "声", "t": 3.15}


def test_cli_reports_an_unfinished_alignment(tmp_path):
    suno, a = tmp_path / "suno.json", tmp_path / "analysis.json"
    suno.write_text(json.dumps(response([], "processing")), encoding="utf-8")
    a.write_text(json.dumps(analysis(["あおい そら"]), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit, match="processing"):
        module.main(["--suno", str(suno), "--analysis", str(a), "--output", str(tmp_path / "out.json"), "--source-offset-s", "0"])
    assert not (tmp_path / "out.json").exists()
