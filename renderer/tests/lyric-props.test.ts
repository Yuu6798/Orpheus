import assert from 'node:assert/strict';
import {test} from 'node:test';
import {lineAt, onBeat} from '../src/lyric-props.ts';
import type {LyricProps} from '../src/lyric-props.ts';

const props: LyricProps = {
  schema_version: 'remotion-props/0.1', fps: 30, width: 1080, height: 1920, duration_frames: 300,
  lines: [
    {id: 'a', text: 'A', display: 'A', sung: true, start_frame: 90, end_frame: 165, timing_source: 'manual', review_status: 'confirmed', confidence: null},
    {id: 'b', text: 'B', display: 'B', sung: true, start_frame: 165, end_frame: 240, timing_source: 'model', review_status: 'unreviewed', confidence: 0.8},
  ],
  untimed: [], beats: {frames: [0, 15, 30, 45], downbeat_frames: [0, 60], bpm: 120, source: {kind: 'beats_per_bar'}},
  scenes: null, show_lyrics: true, sources: {analysis_sha256: 'x', audio_sha256: 'y'},
};

test('a line is visible from start_frame up to but not including end_frame', () => {
  assert.equal(lineAt(props, 89), undefined);
  assert.equal(lineAt(props, 90)?.id, 'a');
  assert.equal(lineAt(props, 164)?.id, 'a');
  assert.equal(lineAt(props, 165)?.id, 'b');
  assert.equal(lineAt(props, 240), undefined);
});
test('beats and downbeats are frame numbers, never seconds', () => {
  assert.equal(onBeat(props, 15), true);
  assert.equal(onBeat(props, 16), false);
  assert.equal(onBeat(props, 15, 'downbeat'), false);
  assert.equal(onBeat(props, 60, 'downbeat'), true);
});
