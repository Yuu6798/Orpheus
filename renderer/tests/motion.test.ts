import assert from 'node:assert/strict';
import {test} from 'node:test';
import {motionTransform, incomingOpacity} from '../presets/motion.ts';
import type {Scene} from '../src/types.ts';

test('crossfade occupies the new scene without shifting its end', () => {
  const scene = {start_frame: 90, end_frame: 180, transition_in: {type: 'crossfade', duration_frames: 12}} as Scene;
  assert.equal(incomingOpacity(scene, 90), 0);
  assert.equal(incomingOpacity(scene, 101), 1);
  assert.equal(incomingOpacity(scene, 179), 1);
});
test('motion stays bounded and one-frame scene is finite', () => {
  assert.equal(motionTransform('slow_push', 99, 100), 'scale(1.08)');
  assert.equal(motionTransform('pan_right', 0, 1), 'translateX(-3%) scale(1.1)');
  assert.equal(motionTransform('still', 200, 1), 'none');
});
