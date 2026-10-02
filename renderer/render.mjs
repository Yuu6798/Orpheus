import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';

const [propsPath, publicDir, output] = process.argv.slice(2);
if (!propsPath || !publicDir || !output) throw new Error('Usage: node renderer/render.mjs props.json public-dir output.mp4');
const inputProps = JSON.parse(await fs.readFile(propsPath, 'utf8'));
const root = path.dirname(fileURLToPath(import.meta.url));
const serveUrl = await bundle({entryPoint: path.join(root, 'src/index.tsx'), publicDir: path.resolve(publicDir)});
const browserExecutable = process.env.ORPHEUS_BROWSER;
const composition = await selectComposition({serveUrl, id: 'Orpheus', inputProps, browserExecutable});
await renderMedia({serveUrl, composition, inputProps, codec: 'h264', audioCodec: 'aac',
  pixelFormat: 'yuv420p', crf: 18, outputLocation: path.resolve(output),
  concurrency: 2, browserExecutable, overwrite: true});
console.log(`Rendered ${composition.durationInFrames} frames to ${output}`);
