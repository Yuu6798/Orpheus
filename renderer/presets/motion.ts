import type {Scene} from '../src/types.ts';

export function motionTransform(motion: Scene['motion'], frame: number, duration: number): string {
  const progress = Math.max(0, Math.min(1, frame / Math.max(1, duration - 1)));
  if (motion === 'slow_push') return `scale(${1 + 0.08 * progress})`;
  if (motion === 'pan_left') return `translateX(${3 - 6 * progress}%) scale(1.1)`;
  if (motion === 'pan_right') return `translateX(${-3 + 6 * progress}%) scale(1.1)`;
  return 'none';
}

export function incomingOpacity(scene: Scene, frame: number): number {
  if (scene.transition_in.type === 'cut') return 1;
  return Math.min(1, Math.max(0, (frame - scene.start_frame) / Math.max(1, scene.transition_in.duration_frames - 1)));
}
