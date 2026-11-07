import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {test} from 'node:test';
import {renderLayer} from '../render-frames.mjs';

async function fixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'orpheus-layer-test-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const props = path.join(directory, 'props.json');
  await fs.writeFile(props, JSON.stringify({duration_frames: 3}));
  const logs = t.mock.method(console, 'log', () => {});
  const errors = t.mock.method(console, 'error', () => {});
  let bundleDir;
  const dependencies = {
    ensureBrowser: async () => ({type: 'local-puppeteer-browser', path: '/browser/chrome'}),
    bundle: async ({entryPoint, onDirectoryCreated}) => {
      assert.ok(path.isAbsolute(entryPoint));
      bundleDir = await fs.mkdtemp(path.join(directory, 'remotion-webpack-bundle-'));
      onDirectoryCreated(bundleDir);
      return bundleDir;
    },
    selectComposition: async ({id, inputProps}) => ({id, durationInFrames: inputProps.duration_frames, fps: 12, width: 48, height: 64}),
    renderFrames: async ({outputDir, imageFormat, composition, onFrameUpdate}) => {
      assert.equal(imageFormat, 'png');
      for (let i = 0; i < composition.durationInFrames; i += 1) {
        await fs.writeFile(path.join(outputDir, `element-0${i}.png`), 'png');
        onFrameUpdate(i + 1, i, 1);
      }
    },
  };
  return {directory, props, logs, errors, dependencies, bundleDir: () => bundleDir};
}

test('renders a PNG sequence and reports frames, composition metadata, remotion version and browser', async (t) => {
  const f = await fixture(t);
  const out = path.join(f.directory, 'frames');
  const result = await renderLayer({entry: 'renderer/src/layer-index.tsx', composition: 'LyricLayer', props: f.props, out, concurrency: 2}, f.dependencies);
  assert.deepEqual((await fs.readdir(out)).sort(), ['element-00.png', 'element-01.png', 'element-02.png']);
  assert.equal(result.frames, 3);
  assert.equal(result.duration_frames, 3);
  assert.deepEqual([result.fps, result.width, result.height], [12, 48, 64]);
  assert.match(result.remotion_version, /^\d+\.\d+\.\d+/);
  assert.deepEqual(result.browser, {status: 'local-puppeteer-browser', path: '/browser/chrome'});
  assert.equal(JSON.parse(f.logs.mock.calls.at(-1).arguments[0]).frames, 3);
  await assert.rejects(fs.access(f.bundleDir()), {code: 'ENOENT'});
  assert.equal(f.errors.mock.calls.length, 0);
});

test('a failure is reported with its phase before the bundle is removed', async (t) => {
  const f = await fixture(t);
  f.dependencies.renderFrames = async () => { throw new Error('no browser'); };
  await assert.rejects(renderLayer({entry: 'e.tsx', composition: 'X', props: f.props, out: path.join(f.directory, 'frames')}, f.dependencies), /no browser/);
  const line = f.errors.mock.calls[0].arguments[0];
  assert.ok(line.startsWith('[orpheus-render-error] '));
  assert.equal(JSON.parse(line.slice('[orpheus-render-error] '.length)).phase, 'renderFrames');
  await assert.rejects(fs.access(f.bundleDir()), {code: 'ENOENT'});
});
