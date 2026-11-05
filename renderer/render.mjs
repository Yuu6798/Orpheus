import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';

const root = path.dirname(fileURLToPath(import.meta.url));

function describeError(error, seen = new Set()) {
  if (error === null || typeof error !== 'object') return {message: String(error)};
  if (seen.has(error)) return {message: '[Circular cause]'};
  seen.add(error);
  return {name: error.name, message: error.message ?? String(error), code: error.code,
    stack: error.stack, cause: error.cause === undefined ? undefined : describeError(error.cause, seen)};
}

async function diskSpace(directory) {
  try {
    const stats = await fs.statfs(directory);
    return {path: directory, available_bytes: stats.bavail * stats.bsize, free_inodes: stats.ffree};
  } catch (error) {
    return {path: directory, error: describeError(error)};
  }
}

async function reportFailure(error, phase, tempDir, outputDir) {
  // Emit the original cause before diagnostics, which may themselves fail.
  console.error(`[orpheus-render-error] ${JSON.stringify({phase, error: describeError(error)})}`);
  const [temp, output] = await Promise.all([diskSpace(tempDir), diskSpace(outputDir)]);
  const memory = {};
  for (const [name, read] of [['system_free_bytes', () => os.freemem()],
    ['process_available_bytes', () => process.availableMemory()]]) {
    try { memory[name] = read(); }
    catch (error) { memory[`${name}_error`] = describeError(error); }
  }
  console.error(`[orpheus-render-resources] ${JSON.stringify({phase, temp, output, memory})}`);
}

export async function renderProject(propsPath, publicDir, output, dependencies = {}) {
  if (!propsPath || !publicDir || !output) throw new Error('Usage: node renderer/render.mjs props.json public-dir output.mp4');
  const makeBundle = dependencies.bundle ?? bundle;
  const select = dependencies.selectComposition ?? selectComposition;
  const render = dependencies.renderMedia ?? renderMedia;
  // Resolve paths before bundle(), which may change the working directory.
  const publicPath = path.resolve(publicDir);
  const outputLocation = path.resolve(output);
  const tempDir = path.resolve(os.tmpdir());
  const outputDir = path.dirname(outputLocation);
  const browserExecutable = process.env.ORPHEUS_BROWSER;
  let bundleDir;
  let composition;
  let phase = 'input';
  let failed = false;
  try {
    const inputProps = JSON.parse(await fs.readFile(propsPath, 'utf8'));
    phase = 'bundle';
    const serveUrl = await makeBundle({entryPoint: path.join(root, 'src/index.tsx'), publicDir: publicPath,
      // Capture ownership before webpack/publicDir copying can fail.
      onDirectoryCreated: (directory) => { bundleDir = path.resolve(directory); }});
    phase = 'selectComposition';
    composition = await select({serveUrl, id: 'Orpheus', inputProps, browserExecutable});
    phase = 'renderMedia';
    await render({serveUrl, composition, inputProps, codec: 'h264', audioCodec: 'aac',
      pixelFormat: 'yuv420p', crf: 18, outputLocation,
      concurrency: 2, browserExecutable, overwrite: true});
  } catch (error) {
    failed = true;
    // Diagnostics must not replace the render failure; collect before cleanup.
    await reportFailure(error, phase, tempDir, outputDir).catch(() => {});
    throw error;
  } finally {
    if (bundleDir) {
      try {
        await fs.rm(bundleDir, {recursive: true, force: true, maxRetries: 3});
      } catch (error) {
        await reportFailure(error, 'cleanup', tempDir, outputDir).catch(() => {});
        if (!failed) throw error;
      }
    }
  }
  console.log(`Rendered ${composition.durationInFrames} frames to ${output}`);
}

if (process.argv[1] && await fs.realpath(process.argv[1]).catch(() => null) === fileURLToPath(import.meta.url)) {
  await renderProject(...process.argv.slice(2));
}
