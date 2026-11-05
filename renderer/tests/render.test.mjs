import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';
import {renderProject} from '../render.mjs';

async function fixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'orpheus-render-test-'));
  const remove = fs.rm.bind(fs);
  t.after(() => remove(directory, {recursive: true, force: true}));
  const publicDir = path.join(directory, 'public');
  const outputDir = path.join(directory, 'output');
  await fs.mkdir(publicDir);
  await fs.mkdir(outputDir);
  await fs.writeFile(path.join(publicDir, 'source.txt'), 'keep source');
  const props = path.join(directory, 'props.json');
  await fs.writeFile(props, '{}');
  const unrelated = await fs.mkdtemp(path.join(directory, 'remotion-webpack-bundle-'));
  t.mock.method(os, 'tmpdir', () => directory);
  const errors = t.mock.method(console, 'error', () => {});
  const logs = t.mock.method(console, 'log', () => {});
  let bundleDir;
  const dependencies = {
    bundle: async ({onDirectoryCreated}) => {
      bundleDir = await fs.mkdtemp(path.join(directory, 'remotion-webpack-bundle-'));
      onDirectoryCreated(bundleDir);
      await fs.writeFile(path.join(bundleDir, 'copied-asset.txt'), 'partial or complete bundle');
      return bundleDir;
    },
    selectComposition: async ({serveUrl}) => {
      await fs.access(serveUrl);
      return {durationInFrames: 30};
    },
    renderMedia: async ({serveUrl, outputLocation, concurrency}) => {
      await fs.access(serveUrl);
      assert.equal(concurrency, 2);
      await fs.writeFile(outputLocation, 'test output');
    },
  };
  const records = (prefix) => errors.mock.calls
    .map(({arguments: args}) => args[0])
    .filter((line) => line.startsWith(prefix))
    .map((line) => JSON.parse(line.slice(prefix.length)));
  return {directory, publicDir, outputDir, unrelated, dependencies, errors, logs,
    bundleDir: () => bundleDir,
    errorsRecorded: () => records('[orpheus-render-error] '),
    resourcesRecorded: () => records('[orpheus-render-resources] '),
    run: () => renderProject(props, publicDir, path.join(outputDir, 'video.mp4'), dependencies)};
}

async function assertOnlyOwnedBundleRemoved(f) {
  await assert.rejects(fs.access(f.bundleDir()), {code: 'ENOENT'});
  assert.equal(await fs.readFile(path.join(f.publicDir, 'source.txt'), 'utf8'), 'keep source');
  await fs.access(f.unrelated);
}

test('successful render removes its bundle after rendering and preserves sources/other bundles', async (t) => {
  const f = await fixture(t);
  await f.run();
  await assertOnlyOwnedBundleRemoved(f);
  assert.equal(await fs.readFile(path.join(f.outputDir, 'video.mp4'), 'utf8'), 'test output');
  assert.equal(f.logs.mock.calls.length, 1);
  assert.equal(f.errors.mock.calls.length, 0);
});

for (const phase of ['bundle', 'selectComposition', 'renderMedia']) {
  test(`${phase} failure preserves cause and records resources before removing its bundle`, async (t) => {
    const f = await fixture(t);
    const original = new Error('Could not complete render', {
      cause: Object.assign(new Error('no space left on device'), {code: 'ENOSPC'}),
    });
    const operation = f.dependencies[phase];
    f.dependencies[phase] = async (options) => {
      if (phase === 'bundle') await operation(options);
      throw original;
    };
    t.mock.method(fs, 'statfs', async () => {
      await fs.access(f.bundleDir());
      return {bavail: 5, bsize: 4096, ffree: 12};
    });
    await assert.rejects(f.run(), (error) => error === original);
    await assertOnlyOwnedBundleRemoved(f);
    const failure = f.errorsRecorded()[0];
    assert.equal(failure.phase, phase);
    assert.equal(failure.error.cause.code, 'ENOSPC');
    assert.equal(failure.error.stack, original.stack);
    const resources = f.resourcesRecorded()[0];
    assert.deepEqual(resources.temp, {path: f.directory, available_bytes: 20480, free_inodes: 12});
    assert.deepEqual(resources.output, {path: f.outputDir, available_bytes: 20480, free_inodes: 12});
    assert.equal(typeof resources.memory.system_free_bytes, 'number');
    assert.equal(f.logs.mock.calls.length, 0);
  });
}

test('failure before bundle directory creation does not delete unrelated directories', async (t) => {
  const f = await fixture(t);
  const original = Object.assign(new Error('cannot create bundle'), {code: 'ENOSPC'});
  f.dependencies.bundle = async () => { throw original; };
  await assert.rejects(f.run(), (error) => error === original);
  assert.equal(f.bundleDir(), undefined);
  await fs.access(f.unrelated);
  await fs.access(f.publicDir);
  assert.equal(f.errorsRecorded()[0].error.code, 'ENOSPC');
});

test('partial resource diagnostic failure preserves the render error and other measurements', async (t) => {
  const f = await fixture(t);
  const original = new Error('render failed');
  f.dependencies.renderMedia = async () => { throw original; };
  t.mock.method(fs, 'statfs', async (directory) => {
    if (directory === f.directory) throw Object.assign(new Error('statfs denied'), {code: 'EACCES'});
    return {bavail: 3, bsize: 4096, ffree: 7};
  });
  t.mock.method(process, 'availableMemory', () => { throw new Error('memory unavailable'); });
  await assert.rejects(f.run(), (error) => error === original);
  await assertOnlyOwnedBundleRemoved(f);
  const resources = f.resourcesRecorded()[0];
  assert.equal(resources.temp.error.code, 'EACCES');
  assert.equal(resources.output.available_bytes, 12288);
  assert.equal(typeof resources.memory.system_free_bytes, 'number');
  assert.equal(resources.memory.process_available_bytes_error.message, 'memory unavailable');
});

for (const renderFails of [false, true]) {
  test(`cleanup failure ${renderFails ? 'does not mask render failure' : 'fails without a success message'}`, async (t) => {
    const f = await fixture(t);
    const original = Object.assign(new Error('render failed'), {code: 'ENOSPC'});
    const cleanupError = Object.assign(new Error('cannot remove bundle'), {code: 'EACCES'});
    if (renderFails) f.dependencies.renderMedia = async () => { throw original; };
    t.mock.method(fs, 'rm', async () => { throw cleanupError; });
    await assert.rejects(f.run(), (error) => error === (renderFails ? original : cleanupError));
    const failures = f.errorsRecorded();
    assert.equal(failures[0].phase, renderFails ? 'renderMedia' : 'cleanup');
    assert.equal(failures.at(-1).phase, 'cleanup');
    assert.equal(failures.at(-1).error.code, 'EACCES');
    assert.equal(f.logs.mock.calls.length, 0);
  });
}

test('circular causes remain diagnosable without interfering with cleanup', async (t) => {
  const f = await fixture(t);
  const original = new Error('cyclic cause');
  original.cause = original;
  f.dependencies.renderMedia = async () => { throw original; };
  await assert.rejects(f.run(), (error) => error === original);
  await assertOnlyOwnedBundleRemoved(f);
  assert.equal(f.errorsRecorded()[0].error.cause.message, '[Circular cause]');
});

test('failure reporting cannot replace the original render error or prevent cleanup', async (t) => {
  const f = await fixture(t);
  const original = new Error('render failed');
  f.dependencies.renderMedia = async () => { throw original; };
  f.errors.mock.mockImplementation(() => { throw new Error('diagnostic output unavailable'); });
  await assert.rejects(f.run(), (error) => error === original);
  await assertOnlyOwnedBundleRemoved(f);
});

test('CLI invoked through a symlink still runs and reports missing arguments', async (t) => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'orpheus-render-cli-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const link = path.join(directory, 'render.mjs');
  try {
    await fs.symlink(fileURLToPath(new URL('../render.mjs', import.meta.url)), link);
  } catch (error) {
    if (error.code === 'EPERM') return t.skip('File symlinks unavailable');
    throw error;
  }
  const result = spawnSync(process.execPath, [link], {encoding: 'utf8'});
  assert.equal(result.error, undefined);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Usage: node renderer\/render\.mjs/);
});
