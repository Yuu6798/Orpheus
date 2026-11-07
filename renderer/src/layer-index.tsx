import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {LyricLayer} from './LyricLayer';
import type {LyricProps} from './lyric-props';

/** `mv layer render` の既定のエントリ。寸法・fps・コマ数は props（remotion-props/0.1）から決める。 */
const defaults: LyricProps = {
  schema_version: 'remotion-props/0.1', fps: 30, width: 1080, height: 1920, duration_frames: 30,
  lines: [], untimed: [], beats: {frames: [], downbeat_frames: [], bpm: null, source: {kind: 'analysis'}},
  scenes: null, show_lyrics: true, sources: {analysis_sha256: '', audio_sha256: ''},
};
const Root = () => <Composition id="LyricLayer" component={LyricLayer} defaultProps={defaults}
  durationInFrames={30} fps={30} width={1080} height={1920}
  calculateMetadata={({props}) => ({durationInFrames: props.duration_frames, fps: props.fps, width: props.width, height: props.height})} />;
registerRoot(Root);
