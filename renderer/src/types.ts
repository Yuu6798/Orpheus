import type {Caption} from '@remotion/captions';

export type Scene = {
  id: string; start_frame: number; end_frame: number; asset_id: string;
  motion: 'still' | 'slow_push' | 'pan_left' | 'pan_right';
  transition_in: {type: 'cut' | 'crossfade'; duration_frames: number};
};
export type TimedCaption = Caption & {id: string; startFrame: number; endFrame: number};
export type Props = {
  storyboard: {
    render: {fps: number; width: number; height: number; duration_frames: number; show_lyrics: boolean};
    scenes: Scene[];
  };
  assets: Record<string, string>;
  audio: string;
  captions: TimedCaption[];
};
