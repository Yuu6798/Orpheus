"""ダンス合成（実験的）: 緑背景の人物動画をステージに合成し、カメラと縦の歌詞を載せた縦動画を作る。

設定スキーマ（dance-shot/0.2。0.1 も読める）は contracts/ の外にあり、昇格にはユーザー承認が要る。
決定論: 同じ spec・素材・環境（ライブラリ版）なら同じフレームを返す。LLM もネットワークも使わない。
作品固有の値はコードに持たず、すべて spec に書く（作品ごとの spec と素材は Git に入れない）。

このパッケージを import しても cv2 は読み込まない。cv2 が要るのは key / clips / compose / runs の描画 / demo で、
`mv dance` の import は CLI 側で遅延させる（既存コマンドは cv2 なしで動く）。

モジュールの構成（CLI は `mv dance demo|render|stills|plan|accept`。詳細は docs/dance.md）:
  - spec: 読み込み・検証・既定値補完。
  - key: 緑抜き（クロマキー、緑かぶりの除去、縁の調整）。
  - clips: 人物動画のタイムライン（ショット時刻 → 元動画のフレーム）。
  - lyrics: 歌詞の配置と描画（層・書体・row の積み方・語ごとの出現と色・動き・光彩、UI 枠の確認）。
  - camera: カメラの式（キー・アクセント・揺れ・seams・ブレ・視差）。
  - compose: 1 フレームの合成（ステージ → behind の字 → 人物 → フラッシュ → front の字）。
  - beats: 拍の格子と時刻の参照。
  - fit: 人物の輪郭から縮尺・左右・足元を求める（合成の入力。評価には使わない）。
  - plan: 解決した時刻の一覧（`mv dance plan`）。
  - runs: run の記録・キャッシュ・静止画シート。書き出しと構造検査は fx の部品を使う。
  - demo: 合成素材と spec.json。

run には timeline.json（字の出入りと箱、カットの切れ目、フラッシュ、UI 枠）を残す。`mv gate check` はこれを読む。
"""
import hashlib
from pathlib import Path

from .. import fx

SCHEMA_VERSION = "dance-shot/0.2"
SCHEMA_V01 = "dance-shot/0.1"
VERSION = "0.2"
VARIANT = "dance"

_PACKAGE = Path(__file__).resolve().parent


def fingerprint():
    """dance パッケージの .py と .schema.json に、使っている fx の指紋を足した 16 進 64 桁。"""
    digest = hashlib.sha256(fx.fingerprint().encode("ascii"))
    files = sorted(p for p in _PACKAGE.rglob("*") if p.is_file()
                   and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts)
    for path in files:
        digest.update(path.relative_to(_PACKAGE).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
