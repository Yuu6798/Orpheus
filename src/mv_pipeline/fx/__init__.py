"""演出エンジン fx（実験的）: 1 枚絵 + 深度 + マスクから、カメラ・空間レイヤー・局所光で動画を作る。

設定スキーマ（fx-shot/0.1）は contracts/ の外にあり、昇格にはユーザー承認が要る。
決定論: 同じ spec・seed・環境（ライブラリ版）なら同じフレームを返す。LLM もネットワークも使わない。

このパッケージを import しても cv2 は読み込まない（既存コマンドは cv2 なしで動く）。
cv2 が要るのは layers / light / camera / assets / render。fx 系コマンドだけがそれらを import する。

使い方（CLI や run 管理の担当者向け）:

    from mv_pipeline.fx import spec as fxspec, render
    sp = fxspec.load_spec("path/to/spec.json")         # 検証・既定値補完済みの Spec
    ctx = render.prepare(sp, scale=1.0)                # 前処理は 1 回。ctx.warnings / ctx.size / ctx.variant_notes
    img = render.frame(ctx, t=2.5, variant="both")     # uint8 (H, W, 3) RGB。純関数
    render.render_variant(ctx, "both", "out.mp4", start=0, duration=10)   # ffmpeg へ書き出し

版（variants）は VARIANTS。fingerprint() は fx パッケージの全ファイル（.py と schema）から作る
エンジン指紋で、run の manifest とキャッシュ key に入れる。

run 管理・グリッド・legacy の担当者向けの部品表:
  - manifest の材料: spec.canonical_json(sp.data)（正規化 spec。`x_` の鍵も含むので、コメントだけの変更でも
    key が変わる＝安全側）、sp.sha256、assets.describe(sp)（全素材の sha256・寸法。無ければ PipelineError）、
    fingerprint()、render.library_versions()、ctx.size、ctx.warnings、ctx.variant_notes()（機能なしの版の記録）。
  - 描画サイズ: render.scaled_size(sp, scale)。prepare(sp, scale) の ctx.size と一致する。key には scale を入れる。
  - フレーム数: timeline.frame_count(duration, fps)（= round(duration×fps)）。legacy の検査もこれで数える。
  - グリッド: 4 版を frame() で描いてタイル状に並べ、render.write_video(out, frames, size, fps, crf, n_frames,
    audio=render.audio_for(ctx, start, n_frames), log_path=...) に渡す。静止画は render.save_png(img, path)。
  - 失敗しても最終名の MP4 は作らない（write_video は一時名に書いて成功時だけ置き換える）。
  - 例外はすべて io.PipelineError。メッセージは原因と直し方を含む。
"""
import hashlib
from pathlib import Path

SCHEMA_VERSION = "fx-shot/0.1"
VERSION = "0.1"
VARIANTS = ("base", "layers", "light", "both")

_PACKAGE = Path(__file__).resolve().parent


def fingerprint():
    """fx パッケージの .py と .schema.json の（相対パス, sha256）から作る 16 進 64 桁の指紋。

    描画に影響しうるコードを 1 文字でも変えると値が変わる。__pycache__ は含めない。
    """
    digest = hashlib.sha256()
    files = sorted(p for p in _PACKAGE.rglob("*") if p.is_file()
                   and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts)
    for path in files:
        digest.update(path.relative_to(_PACKAGE).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
