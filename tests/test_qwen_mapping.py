import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("qwen_adapter", Path(__file__).resolve().parents[1] / "scripts/qwen_align.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
map_lines = module.map_lines


def test_repeated_lines_map_sequentially():
    lyrics = [{"id": str(i), "text": "声", "alignment_text": "こえ"} for i in range(2)]
    tokens = [{"text": "こ", "start_s": 1, "end_s": 1.3}, {"text": "え", "start_s": 1.3, "end_s": 2},
              {"text": "こえ", "start_s": 3, "end_s": 4}]
    rows = map_lines(lyrics, tokens)
    assert [r["start_s"] for r in rows] == [1, 3]


def test_token_mismatch_stays_unknown():
    assert map_lines([{"id": "1", "text": "声", "alignment_text": "こえ"}],
                     [{"text": "違う", "start_s": 1, "end_s": 2}])[0]["timing_source"] == "unknown"


def test_token_crossing_line_does_not_guess_boundary():
    rows = map_lines([{"id": str(i), "text": "a", "alignment_text": "a"} for i in range(2)],
                     [{"text": "aa", "start_s": 1, "end_s": 4}])
    assert all(r["timing_source"] == "unknown" for r in rows)
