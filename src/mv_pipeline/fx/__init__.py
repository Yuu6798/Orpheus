"""演出エンジン fx（実験的）: 1 枚絵 + 深度 + マスクから、カメラ・空間レイヤー・局所光で動画を作る。

設定スキーマ（fx-shot/0.1）は contracts/ の外にあり、昇格にはユーザー承認が要る。
決定論: 同じ spec・seed・環境（ライブラリ版）なら同じフレームを返す。LLM もネットワークも使わない。

このパッケージを import しても cv2 は読み込まない（既存コマンドは cv2 なしで動く）。
cv2 が要るのは layers / light / camera / assets / render / compare / demo。fx 系コマンドだけがそれらを import する。

使い方（CLI や run 管理の担当者向け）:

    from mv_pipeline.fx import spec as fxspec, render
    sp = fxspec.load_spec("path/to/spec.json")         # 検証・既定値補完済みの Spec
    ctx = render.prepare(sp, scale=1.0)                # 前処理は 1 回。ctx.warnings / ctx.size / ctx.variant_notes
    img = render.frame(ctx, t=2.5, variant="both")     # uint8 (H, W, 3) RGB。純関数
    render.render_variant(ctx, "both", "out.mp4", start=0, duration=10)   # ffmpeg へ書き出し

版（variants）は VARIANTS。fingerprint() は fx パッケージの全ファイル（.py と schema）から作る
エンジン指紋で、run の manifest とキャッシュ key に入れる。

モジュールの構成（CLI は `mv fx demo|render|stills|legacy|accept`。詳細は docs/fx.md）:
  - spec / timeline / assets / camera / layers / light / color / render: 描画の中核（cv2 を使う。上の使い方）。
  - look: 最終フレームの色調整・周辺減光・粒状感（実制作レンダラと同じ式。spec に look があるときだけ、全版に同じものを掛ける）。
  - compare: 2×2 グリッド動画（書き出し済みの各版を復号して並べる）と、行=時刻・列=版の静止画シート。
  - probe: ffmpeg だけで行う構造検査 check_video（technical_pass。ffprobe 不要）。
  - runs: run ディレクトリ・manifest・キャッシュ（index.json）・stills・accept。key は描画に影響する入力すべて
    （正規化 spec・素材の sha256・fingerprint()・版の集合・区間・出力寸法と scale・ライブラリ版）から作る。入力を足したら key にも足す。
  - legacy: 外部の実制作レンダラを呼んで run として記録する（cv2 不要・キャッシュしない）。
  - demo: 合成デモ素材と spec.json（cv2 を使う）。
  runs / legacy / probe は cv2 を import しない（fx accept と fx legacy は fx extra なしで動く）。
  例外はすべて io.PipelineError。メッセージは原因と直し方を含む。
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
