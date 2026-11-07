// Remotion のコンポジションを透過 PNG 連番に描く（`mv layer render` が呼ぶ）。
//   node renderer/render-frames.mjs --entry renderer/src/layer-index.tsx --composition LyricLayer --props props.json --out DIR [--public DIR] [--concurrency 2]
// 標準出力の最後の行に JSON（frames・fps・width・height・remotion の版・ブラウザーの実行ファイル・描画時間）を出す。
// 連番は <out>/element-<コマ番号>.png（Remotion の既定の名前）。失敗は render.mjs と同じ [orpheus-render-error] の行で原因を残す。
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {describeError} from './errors.mjs';
import {bundle} from '@remotion/bundler';
import {ensureBrowser, renderFrames, selectComposition} from '@remotion/renderer';

const root = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);


const KNOWN_FLAGS = new Set(['entry', 'composition', 'props', 'out', 'public', 'concurrency']);

export function parseArgs(argv) {
  const options = {concurrency: 2};
  const usage = 'Usage: --entry E --composition ID --props P --out DIR [--public DIR] [--concurrency N]';
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    const value = argv[i + 1];
    if (!key.startsWith('--') || value === undefined) throw new Error(`${usage} (bad: ${key})`);
    const name = key.slice(2);
    if (!KNOWN_FLAGS.has(name)) throw new Error(`${usage} (unknown flag: ${key})`);
    if (name === 'concurrency') {
      const count = Number(value);
      if (!Number.isInteger(count) || count < 1) throw new Error(`--concurrency must be a positive integer, got ${value}`);
      options.concurrency = count;
    } else {
      options[name] = value;
    }
  }
  for (const name of ['entry', 'composition', 'props', 'out']) {
    if (!options[name]) throw new Error(`--${name} is required`);
  }
  return options;
}

export async function renderLayer(options, dependencies = {}) {
  const makeBundle = dependencies.bundle ?? bundle;
  const select = dependencies.selectComposition ?? selectComposition;
  const render = dependencies.renderFrames ?? renderFrames;
  const browser = dependencies.ensureBrowser ?? ensureBrowser;
  const entryPoint = path.resolve(options.entry);
  const outputDir = path.resolve(options.out);
  const publicDir = options.public ? path.resolve(options.public) : undefined;
  const browserExecutable = process.env.ORPHEUS_BROWSER;
  let bundleDir;
  let phase = 'input';
  const started = Date.now();
  try {
    const inputProps = JSON.parse(await fs.readFile(options.props, 'utf8'));
    await fs.mkdir(outputDir, {recursive: true});
    phase = 'ensureBrowser';
    const status = await browser({browserExecutable});
    phase = 'bundle';
    const serveUrl = await makeBundle({entryPoint, publicDir,
      onDirectoryCreated: (directory) => { bundleDir = path.resolve(directory); }});
    phase = 'selectComposition';
    const composition = await select({serveUrl, id: options.composition, inputProps, browserExecutable});
    phase = 'renderFrames';
    let rendered = 0;
    await render({serveUrl, composition, inputProps, imageFormat: 'png', outputDir, browserExecutable,
      concurrency: options.concurrency, onStart: () => {}, onFrameUpdate: (count) => { rendered = count; }});
    const result = {frames: rendered, duration_frames: composition.durationInFrames, fps: composition.fps,
      width: composition.width, height: composition.height, composition: options.composition,
      remotion_version: require('remotion/package.json').version,
      browser: {status: status.type, path: status.path ?? browserExecutable ?? null},
      seconds: (Date.now() - started) / 1000};
    console.log(JSON.stringify(result));
    return result;
  } catch (error) {
    console.error(`[orpheus-render-error] ${JSON.stringify({phase, error: describeError(error)})}`);
    throw error;
  } finally {
    if (bundleDir) await fs.rm(bundleDir, {recursive: true, force: true, maxRetries: 3}).catch(() => {});
  }
}

if (process.argv[1] && await fs.realpath(process.argv[1]).catch(() => null) === fileURLToPath(import.meta.url)) {
  await renderLayer(parseArgs(process.argv.slice(2)));
}
