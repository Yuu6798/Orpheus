"""生成動画の検品（実験的。`mv inspect`、docs/inspect.md）。

生成した動画（i2v のカット・緑背景のダンス素材・3D の描画）から、異常の候補を**コマ番号と秒で**見つけ、
形状・動作・衣装・連続性に分け、設定画と配色を照合し、直す場所（元動画のコマ・dance の spec の clip・生成の素性）に結び付ける。

- **採点器ではない**: 数値と根拠を出すだけで、合否・採否・好みは決めない。検出が無いことは合格ではない（採用は人）。
- **決まりとの関係**: AGENTS.md の「生成物の開発中は測定器・検証器の機能を足さない」の例外として、2026-11-07 にユーザーが依頼した。
- **推定を実測扱いしない**: 数値は動画から測った値（measured）。それを異常と読むかは人が見て決める（status は常に review = 要確認）。
  VLM の指摘は推定（estimated）として別に残す。
- **しきい値はどれも仮の値**: 合成素材で決めた値で、最初の実作品で確かめて直す。
- **送信しない**: VLM はユーザーが明示した外部のプログラム（argv）だけを呼ぶ。素材を外へ送る設定は `--allow-send` が無いと動かさない。
- **自動の再生成はしない**: 修正計画（fix_plan.json）に、直す区間・前後のコマ・素性・手当ての候補を書くだけ（将来の再生成の入口）。

モジュールの構成:
  - config: 設定（inspect/0.1）の読み込み・検証・既定値。
  - video: ffmpeg だけで読む（色の式を固定。OpenCV の環境ごとの読み違いを避ける）。
  - features: コマごとの特徴（明るさ・差分・人物の切り抜き・輪郭・配色・動き）。
  - reference: 設定画の切り抜きと主な色、コマごとの色の重なり。
  - detect: 特徴から信号を作り、同じ出来事の信号をまとめ、規則で分類する。
  - targets: 直す場所（元動画のコマ・dance の clip と出力の秒）と手当ての候補、修正計画。
  - vlm: VLM への依頼の束、外部プログラムの呼び出し、返答の取り込み。
  - report: 静止画・前後のコマ・コマごとの数値・報告（JSON と Markdown）。
  - run: 全体の流れ（`mv inspect check`）。demo: 合成素材（`mv inspect demo`）。

cv2 を使う（fx extra）。このパッケージを import しても cv2 は読み込まない（CLI 側で遅延させる）。
"""
import hashlib
from pathlib import Path

SCHEMA_VERSION = "inspect/0.1"
REPORT_SCHEMA = "inspect-report/0.1"
PLAN_SCHEMA = "inspect-fix-plan/0.1"
VERSION = "0.1"
CATEGORIES = ("shape", "motion", "costume", "continuity")
CATEGORY_LABELS = {"shape": "形状", "motion": "動作", "costume": "衣装", "continuity": "連続性", "other": "その他"}

_PACKAGE = Path(__file__).resolve().parent


def fingerprint():
    """inspection パッケージの .py と .json の（相対パス, sha256）から作る 16 進 64 桁。"""
    digest = hashlib.sha256()
    files = sorted(p for p in _PACKAGE.rglob("*") if p.is_file()
                   and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts)
    for path in files:
        digest.update(path.relative_to(_PACKAGE).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
