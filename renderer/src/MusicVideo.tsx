import React from 'react';
import {AbsoluteFill, Audio, Img, staticFile, useCurrentFrame} from 'remotion';
import {incomingOpacity, motionTransform} from '../presets/motion';
import type {Props, Scene} from './types';

function Picture({scene, assets, frame, opacity = 1}: {scene: Scene; assets: Record<string, string>; frame: number; opacity?: number}) {
  return <AbsoluteFill style={{overflow: 'hidden', opacity}}>
    <Img src={staticFile(assets[scene.asset_id])} style={{width: '100%', height: '100%', objectFit: 'cover',
      transform: motionTransform(scene.motion, frame, scene.end_frame - scene.start_frame)}} />
  </AbsoluteFill>;
}

export const MusicVideo: React.FC<Props> = ({storyboard, assets, audio, captions}) => {
  const frame = useCurrentFrame();
  const index = storyboard.scenes.findIndex(s => s.start_frame <= frame && frame < s.end_frame);
  const scene = storyboard.scenes[index];
  const previous = storyboard.scenes[index - 1];
  const caption = captions.find(c => c.startFrame <= frame && frame < c.endFrame);
  const opacity = scene ? incomingOpacity(scene, frame) : 1;
  return <AbsoluteFill style={{backgroundColor: '#090e1a'}}>
    {previous && opacity < 1 && <Picture scene={previous} assets={assets} frame={previous.end_frame - previous.start_frame - 1} />}
    {scene && <Picture scene={scene} assets={assets} frame={frame - scene.start_frame} opacity={opacity} />}
    <Audio src={staticFile(audio)} />
    {storyboard.render.show_lyrics && caption && <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', padding: '5% 6%'}}>
      <div style={{fontFamily: '"Yu Gothic", "Noto Sans CJK JP", "Meiryo", sans-serif',
        fontSize: storyboard.render.height * 0.051, fontWeight: 700, lineHeight: 1.45,
        color: '#fff', background: 'rgba(5, 10, 22, 0.72)', borderRadius: 12,
        padding: '0.25em 0.75em', textAlign: 'center', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
        textShadow: '0 2px 8px #000', maxWidth: '100%'}}>{caption.text}</div>
    </AbsoluteFill>}
  </AbsoluteFill>;
};
