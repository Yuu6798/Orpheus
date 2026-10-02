import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {MusicVideo} from './MusicVideo';
import type {Props} from './types';

const defaults: Props = {
  storyboard: {render: {fps: 30, width: 1920, height: 1080, duration_frames: 30, show_lyrics: false}, scenes: []},
  assets: {}, audio: 'audio.wav', captions: [],
};
const Root = () => <Composition id="Orpheus" component={MusicVideo} defaultProps={defaults}
  durationInFrames={30} fps={30} width={1920} height={1080}
  calculateMetadata={({props}) => ({durationInFrames: props.storyboard.render.duration_frames,
    fps: props.storyboard.render.fps, width: props.storyboard.render.width, height: props.storyboard.render.height})} />;
registerRoot(Root);
