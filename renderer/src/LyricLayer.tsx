import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';
import {lineAt, onBeat} from './lyric-props';
import type {LyricProps} from './lyric-props';

/**
 * 歌詞の透過レイヤー（最小の例）。`mv export remotion-props` の props（コマ番号だけ）を読み、背景を描かずに
 * 見えている行だけを置く。`mv layer render` が透過 PNG 連番に描き、`mv overlay` で土台に重ねる。
 * 拍のコマでは字を少し大きくする（コマ番号で引くだけで、秒は扱わない）。
 */
export const LyricLayer: React.FC<LyricProps> = (props) => {
  const frame = useCurrentFrame();
  const line = lineAt(props, frame);
  const pulse = onBeat(props, frame) ? 1.04 : 1;
  return <AbsoluteFill style={{backgroundColor: 'transparent', justifyContent: 'flex-end', alignItems: 'center', padding: '5% 6%'}}>
    {props.show_lyrics && line && <div style={{fontFamily: '"Yu Gothic", "Noto Sans CJK JP", "Meiryo", sans-serif',
      fontSize: props.height * 0.051, fontWeight: 700, lineHeight: 1.45, color: '#fff',
      textShadow: '0 2px 8px rgba(0,0,0,0.9)', transform: `scale(${pulse})`, textAlign: 'center',
      whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxWidth: '100%'}}>{line.display}</div>}
  </AbsoluteFill>;
};
