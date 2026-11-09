"""`mv export subtitles`: 歌詞の行の時刻から SRT・WebVTT・LRC・概要欄の歌詞を書く。

字幕の出入りと書き方は、実制作の公開準備のスクリプトを写した関数（ref_*）と、同じ入力で文字列が同じかを比べる。
"""
import json

import pytest
from fx_synth import run_cli

from mv_pipeline import subtitles
from mv_pipeline.demo import create
from mv_pipeline.gate.lines import read_lines
from mv_pipeline.io import PipelineError, atomic_json, read_json, sha256

# ---- 実制作の公開準備のスクリプト（写し） -----------------------------------------------------------
REF_LEAD, REF_FADE_OUT, REF_MIN_HOLD = 0.15, 0.40, 0.12


def ref_ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def ref_files(L, settings="line:8% position:50% align:center"):
    cues = []
    for i, x in enumerate(L):
        a = x['start'] - REF_LEAD
        e = x['end'] + REF_FADE_OUT
        if i + 1 < len(L):
            na = L[i + 1]['start'] - REF_LEAD
            if e > na:
                e = max(na, x['end'] + REF_MIN_HOLD)
            e = min(e, na)
        cues.append((a, e, x['text']))
    srt = '\n'.join(f'{k}\n{ref_ts(a)} --> {ref_ts(e)}\n{t}\n' for k, (a, e, t) in enumerate(cues, 1))
    vtt = 'WEBVTT\n\n' + '\n'.join(f'{k}\n{ref_ts(a).replace(",", ".")} --> {ref_ts(e).replace(",", ".")} {settings}\n{t}\n'
                                   for k, (a, e, t) in enumerate(cues, 1))
    blocks, cur = [], None
    for x in L:
        if x['section'] != cur:
            blocks.append([])
            cur = x['section']
        blocks[-1].append(x['text'])
    txt = '■ Lyrics\n' + '\n\n'.join('\n'.join(b) for b in blocks) + '\n'
    return srt, vtt, txt


LINES = [
    {"id": "01", "section": "Verse 1", "text": "The first line comes in.", "ja": "最初の行", "start": 13.87, "end": 16.76, "confidence": "推定"},
    {"id": "02", "section": "Verse 1", "text": "A second line, close behind.", "ja": "二番目の行", "start": 17.0, "end": 18.9, "confidence": "推定"},
    {"id": "03", "section": "Verse 1", "text": "Then a long gap.", "ja": "長い間", "start": 19.004, "end": 20.0, "confidence": "推定"},
    {"id": "04", "section": "Chorus", "text": "Chorus begins here.", "ja": "サビ", "start": 31.2345, "end": 34.5, "confidence": "推定"},
    {"id": "05", "section": "Chorus", "text": "Sung over the next one.", "ja": "重なる", "start": 34.6, "end": 36.9, "confidence": "推定"},
    {"id": "06", "section": "Chorus", "text": "Next one.", "ja": "次", "start": 36.75, "end": 38.0, "confidence": "推定"},
    {"id": "07", "section": "Tag", "text": "Over an hour in.", "ja": "一時間後", "start": 3605.5, "end": 3609.9994, "confidence": "推定"},
]


def write_lines(tmp_path, lines=LINES, name="lines.json"):
    path = tmp_path / name
    path.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    return path


# ---- 実制作と同じか ----------------------------------------------------------------------------------

def test_srt_vtt_and_description_equal_the_private_script(tmp_path):
    source = write_lines(tmp_path)
    result = subtitles.export(source, tmp_path / "out", "song", ("srt", "vtt", "lrc", "description"),
                              vtt_settings="line:8% position:50% align:center")
    srt, vtt, txt = ref_files(LINES)
    out = tmp_path / "out"
    assert (out / "song.srt").read_bytes() == srt.encode("utf-8")
    assert (out / "song.vtt").read_bytes() == vtt.encode("utf-8")
    assert (out / "song_description.txt").read_bytes() == txt.encode("utf-8")
    assert b"\r" not in (out / "song.srt").read_bytes()                     # LF だけ（Windows でも）
    assert result["cues"] == 7 and result["timing"] == {"推定": 7} and result["untimed"] == 0
    assert "01:00:05,350 --> 01:00:10,399" in srt                           # 1 時間を越える時刻と丸め
    record = read_json(out / "song_subtitles.json")
    assert record["schema_version"] == "subtitles-export/0.1" and record["source"]["sha256"] == sha256(source)
    assert record["outputs"]["srt"]["sha256"] == sha256(out / "song.srt") and record["settings"]["lead_s"] == 0.15


def test_cues_never_overlap_and_end_at_the_next_cue():
    rows, _ = subtitles.timed(subtitles._from_lines(LINES, "text"))
    cue_list, warnings = subtitles.cues(rows)
    assert not warnings
    for (a0, e0, _), (a1, _, _) in zip(cue_list, cue_list[1:]):
        assert a0 < e0 <= a1
    assert cue_list[0][1] == pytest.approx(17.0 - 0.15)                     # 歌い終わり + 0.4 が次の字幕を越えるので、次の出る時刻まで
    assert cue_list[2][1] == pytest.approx(20.4)                           # 間があれば歌い終わり + 0.4


# ---- 入力の形 ---------------------------------------------------------------------------------------

def test_project_lyrics_keep_untimed_lines_apart_and_count_the_timing_sources(tmp_path):
    project = create(tmp_path / "song", 320, 180)
    a = read_json(project / "analysis.json")
    a["lyrics"].append({"id": "lyric-003", "text": "まだ時刻の無い行", "alignment_text": "まだ時刻の無い行", "start_s": None, "end_s": None,
                        "timing_source": "unknown", "review_status": "unreviewed", "confidence": None})
    atomic_json(project / "analysis.json", a)
    result = subtitles.export(project, tmp_path / "subs", formats=("srt", "description"))
    timed = [x for x in a["lyrics"] if x["start_s"] is not None]
    assert result["cues"] == len(timed) and result["untimed"] == 1 and "lyric-003" in result["warnings"][-1]
    assert sum(result["timing"].values()) == len(timed) and all("/" in key for key in result["timing"])
    assert result["outputs"]["srt"].endswith("song.srt")
    text = (tmp_path / "subs" / "song_description.txt").read_text(encoding="utf-8")
    assert "まだ時刻の無い行" in text                                        # 概要欄の歌詞には時刻の無い行も入る
    srt = (tmp_path / "subs" / "song.srt").read_text(encoding="utf-8")
    assert "まだ時刻の無い行" not in srt and srt.count(" --> ") == len(timed)


def test_lrc_input_reads_end_marks_and_round_trips_through_gate_lines(tmp_path):
    lrc = tmp_path / "song.lrc"
    lrc.write_text("[ti:test]\n[00:01.50]はじめの行\n[00:03.20]\n[00:04.00]次の行\n[00:05.10]最後の行\n[00:07.00]\n", encoding="utf-8")
    result = subtitles.export(lrc, tmp_path / "out", formats=("srt", "lrc"))
    srt = (tmp_path / "out" / "song.srt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:01,350 --> 00:00:03,600\nはじめの行\n") and result["timing"] == {"lrc": 3}
    assert "00:00:03,850 --> 00:00:04,950\n次の行" in srt                     # 終わりの印が無い行は次の行の頭まで（次の字幕の出る時刻で切る）
    written = (tmp_path / "out" / "song.lrc").read_text(encoding="utf-8")
    assert written == "[00:01.50]はじめの行\n[00:03.20]\n[00:04.00]次の行\n[00:05.10]最後の行\n[00:07.00]\n"
    assert [(x["text"], x["start"]) for x in read_lines(tmp_path / "out" / "song.lrc")] == [("はじめの行", 1.5), ("次の行", 4.0), ("最後の行", 5.1)]
    lrc.write_text("[00:01.50]一行だけ\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="--duration"):
        subtitles.export(lrc, tmp_path / "again")
    assert subtitles.export(lrc, tmp_path / "again", formats=("srt",), duration=4.0)["last"] == "00:00:04,000"


def test_field_picks_the_translation_and_text_falls_back_to_display(tmp_path):
    source = write_lines(tmp_path)
    subtitles.export(source, tmp_path / "ja", "ja", ("srt",), field="ja")
    assert "最初の行" in (tmp_path / "ja" / "ja.srt").read_text(encoding="utf-8")
    rows = [{"start": 1.0, "end": 2.0, "display": "表示の行"}, {"start": 3.0, "end": 4.0, "line": "line の行"}]
    subtitles.export(write_lines(tmp_path, rows, "display.json"), tmp_path / "d", formats=("srt",))
    assert "表示の行" in (tmp_path / "d" / "display.srt").read_text(encoding="utf-8")
    with pytest.raises(PipelineError, match="--field"):
        subtitles.export(source, tmp_path / "x", formats=("srt",), field="romaji")


# ---- 止めること・注意 ---------------------------------------------------------------------------------

@pytest.mark.parametrize("rows, message", [
    ([{"start": 1.0, "end": 2.0, "text": "a"}, {"start": 1.0, "end": 2.5, "text": "b"}], "同じ"),
    ([{"start": 0.0, "end": 2.0, "text": "a"}, {"start": 0.1, "end": 2.5, "text": "b"}], "近すぎ"),     # 0 秒に寄せた字幕が出る前に次が出る
    ([{"start": 2.0, "end": 1.0, "text": "a"}], "前です"),
    ([{"start": 1.0, "end": 2.0, "text": "a --> b"}], "-->"),
    ([{"start": -1.0, "end": 2.0, "text": "a"}], "0 以上"),
    ([{"end": 2.0, "text": "a"}], "start"),
])
def test_bad_lines_stop_with_the_cause(tmp_path, rows, message):
    with pytest.raises(PipelineError, match=message):
        subtitles.export(write_lines(tmp_path, rows), tmp_path / "out", formats=("srt",))


@pytest.mark.parametrize("name", [pytest.param("../escaped", id="parent"), pytest.param("sub/escaped", id="subfolder"),
                                  pytest.param("C:escaped", id="drive"), pytest.param("/escaped", id="absolute"),
                                  pytest.param("..", id="dotdot"), pytest.param("a\\b", id="backslash")])
def test_name_must_be_a_plain_file_stem(tmp_path, monkeypatch, name):
    # --name に .. やフォルダがあると、--out の外に書いてしまう（../escaped なら --out の 1 つ上に escaped.srt と記録）
    monkeypatch.chdir(tmp_path)                    # ドライブだけの名前（C:escaped）は、今いるフォルダからの位置になる
    source = write_lines(tmp_path)
    (tmp_path / "out").mkdir()
    with pytest.raises(PipelineError, match="--name"):
        subtitles.export(source, tmp_path / "out", name=name)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lines.json", "out"] and not any((tmp_path / "out").iterdir())


def test_a_project_given_as_dot_is_named_after_its_folder(tmp_path, monkeypatch):
    # "." のままでは親の名前が空になり、".srt" と "_subtitles.json" ができていた
    project = tmp_path / "song-a"
    project.mkdir()
    atomic_json(project / "analysis.json", {"lyrics": [{"id": "L1", "text": "a", "start_s": 1.0, "end_s": 2.0}]})
    monkeypatch.chdir(project)
    result = subtitles.export(".", "subs", formats=("srt",))
    assert sorted(p.name for p in (project / "subs").iterdir()) == ["song-a.srt", "song-a_subtitles.json"]
    assert result["outputs"]["srt"].endswith("song-a.srt")


def test_lines_that_collapse_at_the_written_precision_stop(tmp_path):
    # 歌い出しが違っても、書く時刻（SRT・WebVTT はミリ秒、LRC は 1/100 秒）に丸めて潰れる行は止める
    def rows(second):
        return [{"start": 1.0, "end": 1.5, "text": "a"}, {"start": second, "end": 3.0, "text": "b"}]
    for formats in (("srt",), ("vtt",)):           # 1 行目が 00:00:00.850 --> 00:00:00.850（出ている時間が 0）になっていた
        with pytest.raises(PipelineError, match="近すぎ"):
            subtitles.export(write_lines(tmp_path, rows(1.0001)), tmp_path / "out", formats=formats)
    with pytest.raises(PipelineError, match="1/100"):        # LRC では 2 行とも [00:01.00] になっていた
        subtitles.export(write_lines(tmp_path, rows(1.003)), tmp_path / "out", formats=("lrc",))
    assert not (tmp_path / "out").exists()
    subtitles.export(write_lines(tmp_path, rows(1.003)), tmp_path / "ok", formats=("srt",))   # ミリ秒なら 3 ミリ秒の長さが残る
    assert "1\n00:00:00,850 --> 00:00:00,853\na\n" in (tmp_path / "ok" / "lines.srt").read_text(encoding="utf-8")


def test_outputs_never_overwrite_the_inputs_even_with_force(tmp_path):
    # LRC をそのフォルダへ書き出すと、出力の .lrc が入力そのものになる（--force で入力が書き換わって消えていた）
    original = "[ar:Someone]\n[00:01.00]first line\n[00:03.50]second line\n[00:05.00]\n"
    (tmp_path / "song.lrc").write_text(original, encoding="utf-8")
    for force in (True, False):
        with pytest.raises(PipelineError, match="入力"):
            subtitles.export(tmp_path / "song.lrc", tmp_path, force=force)
    assert (tmp_path / "song.lrc").read_text(encoding="utf-8") == original and sorted(p.name for p in tmp_path.iterdir()) == ["song.lrc"]
    body = tmp_path / "out" / "lines_description.txt"          # 概要欄の本文を、書き出す歌詞と同じ名前で置いた
    body.parent.mkdir()
    body.write_text("概要", encoding="utf-8")
    with pytest.raises(PipelineError, match="入力"):
        subtitles.export(write_lines(tmp_path), tmp_path / "out", formats=("description",), description_body=body, force=True)
    assert body.read_text(encoding="utf-8") == "概要"


def test_cues_are_kept_inside_the_video_and_after_zero(tmp_path):
    rows = [{"start": 0.05, "end": 1.0, "text": "a"}, {"start": 9.0, "end": 11.0, "text": "b"}]
    result = subtitles.export(write_lines(tmp_path, rows), tmp_path / "out", formats=("srt",), duration=10.0)
    srt = (tmp_path / "out" / "lines.srt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,400\n") and "00:00:08,850 --> 00:00:10,000" in srt
    assert len(result["warnings"]) == 2
    with pytest.raises(PipelineError, match="--duration"):
        subtitles.export(write_lines(tmp_path, rows, "late.json"), tmp_path / "late", formats=("srt",), duration=5.0)


def test_description_rejects_angle_brackets_and_checks_the_total_size(tmp_path):
    rows = [{"start": 1.0, "end": 2.0, "text": "a <b>"}]
    with pytest.raises(PipelineError, match="<"):
        subtitles.export(write_lines(tmp_path, rows), tmp_path / "out", formats=("description",))
    body = tmp_path / "body.txt"
    body.write_text("x" * 4990, encoding="utf-8")
    with pytest.raises(PipelineError, match="5000"):
        subtitles.export(write_lines(tmp_path), tmp_path / "out", formats=("description",), description_body=body)
    body.write_text("概要", encoding="utf-8")
    fit = subtitles.export(write_lines(tmp_path), tmp_path / "ok", formats=("description",), description_body=body)["description"]
    assert fit["total_bytes"] == len(("概要\n" + (tmp_path / "ok" / "lines_description.txt").read_text(encoding="utf-8")).encode())
    plain = subtitles.export(write_lines(tmp_path), tmp_path / "plain", formats=("description",), description_heading="")
    assert (tmp_path / "plain" / "lines_description.txt").read_text(encoding="utf-8").startswith("The first line") and plain["cues"] == 7


def test_existing_files_are_not_overwritten_without_force(tmp_path):
    source = write_lines(tmp_path)
    first = subtitles.export(source, tmp_path / "out", formats=("srt",))
    record = read_json(tmp_path / "out" / "lines_subtitles.json")
    assert record["outputs"]["srt"]["state"] == "written" and first["cues"] == 7
    subtitles.export(source, tmp_path / "out", formats=("srt",))           # 同じ中身なら書かずに済む
    assert read_json(tmp_path / "out" / "lines_subtitles.json")["outputs"]["srt"]["state"] == "unchanged"
    (tmp_path / "out" / "lines.srt").write_text("手で直した字幕\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="--force"):
        subtitles.export(source, tmp_path / "out", formats=("srt",))
    assert (tmp_path / "out" / "lines.srt").read_text(encoding="utf-8") == "手で直した字幕\n"
    subtitles.export(source, tmp_path / "out", formats=("srt",), force=True)
    assert (tmp_path / "out" / "lines.srt").read_text(encoding="utf-8").startswith("1\n")


def test_cli_writes_the_files_and_a_summary(tmp_path):
    source = write_lines(tmp_path)
    code, result, err = run_cli("export", "subtitles", source, "--out", tmp_path / "cli", "--formats", "srt,vtt",
                                "--vtt-settings", "line:8% position:50% align:center")
    assert code == 0, err
    assert result["cues"] == 7 and set(result["outputs"]) == {"srt", "vtt"}
    assert "line:8% position:50% align:center" in (tmp_path / "cli" / "lines.vtt").read_text(encoding="utf-8")
    code, _, err = run_cli("export", "subtitles", source, "--out", tmp_path / "cli", "--formats", "srt,ass")
    assert code == 1 and "--formats" in err
