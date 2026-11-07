// 描画スクリプト（render.mjs / render-frames.mjs）が共有する、原因の連鎖を残す例外の記録。
export function describeError(error, seen = new Set()) {
  if (error === null || typeof error !== 'object') return {message: String(error)};
  if (seen.has(error)) return {message: '[Circular cause]'};
  seen.add(error);
  return {name: error.name, message: error.message ?? String(error), code: error.code,
    stack: error.stack, cause: error.cause === undefined ? undefined : describeError(error.cause, seen)};
}
