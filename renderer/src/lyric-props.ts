/**
 * `mv export remotion-props` が書く props.json の形（remotion-props/0.1。正は src/mv_pipeline/remotion_props.py）。
 * 時刻はすべてコマ番号。Remotion 側は秒を扱わない（start_frame は floor、end_frame は ceil で Python 側が丸める）。
 * contracts/ の外の形式。
 */
export type LyricLine = {
  id: string;
  text: string;
  /** 表示用の文字（今は text と同じ）。 */
  display: string;
  sung: boolean;
  /** 表示を始めるコマ（この番号を含む）。 */
  start_frame: number;
  /** 表示を終えるコマ（この番号を含まない）。 */
  end_frame: number;
  timing_source: 'model' | 'interpolated' | 'manual' | 'unknown';
  review_status: 'unreviewed' | 'confirmed' | 'needs_fix';
  confidence: number | null;
};

export type UntimedLine = {id: string; text: string; timing_source: LyricLine['timing_source']};

export type BeatFrames = {
  frames: number[];
  downbeat_frames: number[];
  bpm: number | null;
  source: {kind: 'analysis' | 'file' | 'beats_per_bar'; [key: string]: unknown};
};

export type PropsScene = {
  id: string;
  start_frame: number;
  end_frame: number;
  asset_id: string;
  motion: 'still' | 'slow_push' | 'pan_left' | 'pan_right';
  transition_in: {type: 'cut' | 'crossfade'; duration_frames: number};
};

export type LyricProps = {
  schema_version: 'remotion-props/0.1';
  fps: number;
  width: number;
  height: number;
  duration_frames: number;
  lines: LyricLine[];
  untimed: UntimedLine[];
  beats: BeatFrames;
  scenes: PropsScene[] | null;
  show_lyrics: boolean;
  sources: {analysis_sha256: string; audio_sha256: string; storyboard_sha256?: string};
};

/** コマ frame に見えている行（無ければ undefined）。 */
export const lineAt = (props: LyricProps, frame: number): LyricLine | undefined =>
  props.lines.find(line => line.start_frame <= frame && frame < line.end_frame);

/** コマ frame が拍（downbeat なら小節頭）に当たるか。 */
export const onBeat = (props: LyricProps, frame: number, kind: 'beat' | 'downbeat' = 'beat'): boolean =>
  (kind === 'downbeat' ? props.beats.downbeat_frames : props.beats.frames).includes(frame);
