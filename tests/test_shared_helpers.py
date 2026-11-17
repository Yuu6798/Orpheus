"""中身が同じだった小さな補助を 1 か所にまとめたもの（リファクタリング R1-2）が、どこから読んでも同じものであること。写しを
足し直すと落ちる。置き場所はどれも cv2 を読まないモジュール（パスの鍵 same_file は絵を変えないので io）。"""
import pytest

pytest.importorskip("cv2")


def test_each_helper_has_one_body():
    from mv_pipeline import deliver, inspection, io, sheet, subtitles
    from mv_pipeline import fx as fxpackage
    from mv_pipeline.audio import files
    from mv_pipeline.dance import clips, compose, cues
    from mv_pipeline.fx import compare, fire, layering, overlay, probe, render, sequence, spec, spec_layers
    from mv_pipeline.gate import frames_timeline
    assert clips.smoothstep is cues.smoothstep
    assert fire.ease is sequence.ease                                        # smoothstep とは別物（nan と整数の扱いが違う）
    assert overlay.natural_key is frames_timeline.natural_key is sheet.natural_key is spec_layers._natural is layering.natural_key
    assert files.same_file is deliver._same_file is subtitles._same_file is io.same_file
    assert issubclass(compare._Reader, probe.RawFrames) and issubclass(overlay._VideoReader, probe.RawFrames)
    assert render.scaled_size is compose.scaled_size is spec.scaled_size
    assert inspection.tree_digest is fxpackage.tree_digest


def test_the_two_eases_differ_at_the_edges():
    from mv_pipeline.dance import cues
    from mv_pipeline.fx import sequence
    assert sequence.ease(0) == 0 and type(sequence.ease(0)) is int and type(sequence.ease(1)) is int
    assert sequence.ease(float("nan")) != sequence.ease(float("nan"))         # nan のまま
    assert cues.smoothstep(float("nan")) == 0.0 and type(cues.smoothstep(0)) is float
