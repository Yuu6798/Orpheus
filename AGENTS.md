# Orpheus

- 依頼の目的から既存の道具を探すときは、ソースや自前スクリプトへ進む前に `uv run orpheus search "目的"` を実行する。候補の workflow / capability と `availability`・`maturity`・`parameters.status`・`requires.tool_requirements` を確認する。workflow に `starters` があれば自前スクリプトより先に既存例を使い、詳細が要る道具だけ `uv run orpheus show ID`、`model3d ops`、対応する `docs/` を読む（`docs/capabilities.md`）。
- 動く既存ツールを優先する。3D は段ごとに `model3d ops` の一覧を取り直して読み、使わない部品は理由を記録する。
- contracts/ の入出力契約を無断変更しない。
- 推定を実測扱いしない。合成素材のテストと実曲の評価を区別する。
- 歌詞付き MV / 音楽に合わせるダンス編集は `mv workflow plan` で用途と必要工程を確認する。制作後は同じ入力の `mv workflow check` を実行し、未実施・未確認を完了扱いにしない。意図した省略は理由付きで記録し、検査の失敗や作品採用と混同しない（`docs/workflow.md`、`examples/workflow/`）。
- fx / dance に無い描き方を自前のスクリプトで書く前に `docs/external-renderer.md` を読む（既にある部品の表と色の罠。書き出しは `mv_pipeline.encode.write_video`）。
- 作品を作り終えたら、知見の持ち帰りは `docs/after-production.md` の流れ（洗い出し → ユーザーの判断 → 引き継ぎ書と制作後記 → 凍結 → 小さな PR）で進める。
- 制作を採点器開発で止めない。
- 依頼のない送信・公開をしない。素材・鍵・モデル・動画をGitに入れない。
- Pythonの検証は `uv run pytest`、描画コードは `pnpm typecheck`。
- `uv run mv demo projects/demo` と `uv run mv render projects/demo` で実描画を検証する。

## 検証・依存

- 変更は PR 前に `uv run pytest` / `pnpm typecheck` / `pnpm test` を通す。fx のテストは `uv sync --locked --extra analysis --extra fx` の環境が前提（fx extra が無いと cv2 を使うテストは黙って skip されて緑になる）。描画に触れたら `mv demo → render → verify` も、fx に触れたら `mv fx demo → fx render`（`technical_pass` を確認）も実行する。CI も同じ 3 点 + 合成素材の実描画 + fx の smoke + `pip-audit` を回す。
- 依存は `uv.lock` / `pnpm-lock.yaml` で固定し、直接依存は exact pin。依存を変えたら lock も同じ PR で更新する。
- uv は 0.13.5 以上（GHSA-hq85-5cpj-mr62。`pyproject.toml` の `required-version` と CI の `UV_VERSION` を揃える）。
- GitHub Actions は commit SHA 固定＋版コメント。更新は Dependabot（cooldown 7 日）に任せる。
- CI の smoke は構造検査（technical_pass）であり、作品採用（accepted）や実曲の同期精度ではない。CI から成果物（MP4 等）をアップロードしない。

## 開発の原則

- 生成物の開発中は測定器・検証器の改善や機能追加をしない（2026-09-10 裁定）。例外は `mv gate`（書き出し前の機械的な検査と行・秒の確認表。好みは判定しない。2026-10-31 ユーザー承認、`docs/gate.md`）、`tools/model3d`（自作 3D ツール。設計が検査を実装単位に含む。構造と数値だけを報告し、好みは判定しない。2026-11-03 ユーザー承認、`docs/model3d.md`）、`mv inspect`（生成動画の検品。異常の位置・分類・設定画との照合・時系列の検査を、数値と根拠つきで直す場所に結び付ける。合否・採否・好みは判定しない。VLM は明示した外部プログラムだけで、送信は `--allow-send` のときだけ。2026-11-07 ユーザー依頼、`docs/inspect.md`）、`mv handoff`（別の担当から届いた絵の機械的な検査。決めた範囲の外が元と同じか・大きさ。好みは判定しない。2026-11-16 ユーザー承認、`docs/handoff.md`）だけ。
- 成熟度は操作ごとに `stable`（本体・安定契約）/ `verified`（記載範囲の実制作検証済み）/ `experimental` / `external` を記録する。根拠・限界・昇格条件は `docs/maturity.md` と能力カタログの `maturity_assessment` を読む。試作だけの実績・合成素材だけのテスト・部品の実績から、現行実装や全工程を確認済みとしない。
- fx（演出エンジン）の設定スキーマは `contracts/` の外（`src/mv_pipeline/fx/`）に置く。操作の実制作確認と契約の固定は別に判断する。`contracts/` への昇格はユーザー承認が必要。
