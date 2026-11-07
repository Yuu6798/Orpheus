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

export type PropsIndex = {
  /** start_frame で昇順に並んだ行。 */
  lines: LyricLine[];
  beats: Set<number>;
  downbeats: Set<number>;
};

const indexes = new WeakMap<LyricProps, PropsIndex>();

/** コマごとの参照に使う索引（props ごとに 1 回だけ作る）。 */
export const indexProps = (props: LyricProps): PropsIndex => {
  let index = indexes.get(props);
  if (!index) {
    index = {lines: [...props.lines].sort((a, b) => a.start_frame - b.start_frame),
      beats: new Set(props.beats.frames), downbeats: new Set(props.beats.downbeat_frames)};
    indexes.set(props, index);
  }
  return index;
};

/** コマ frame に見えている行（無ければ undefined）。行は重ならない前提（Python 側が切る）なので二分探索で引く。 */
export const lineAt = (props: LyricProps, frame: number): LyricLine | undefined => {
  const {lines} = indexProps(props);
  let low = 0;
  let high = lines.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    const line = lines[mid];
    if (frame < line.start_frame) high = mid - 1;
    else if (frame >= line.end_frame) low = mid + 1;
    else return line;
  }
  return undefined;
};

/** コマ frame が拍（downbeat なら小節頭）に当たるか。 */
export const onBeat = (props: LyricProps, frame: number, kind: 'beat' | 'downbeat' = 'beat'): boolean =>
  (kind === 'downbeat' ? indexProps(props).downbeats : indexProps(props).beats).has(frame);
