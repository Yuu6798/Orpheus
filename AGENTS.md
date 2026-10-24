# Orpheus

- 動く既存ツールを優先する。
- contracts/ の入出力契約を無断変更しない。
- 推定を実測扱いしない。合成素材のテストと実曲の評価を区別する。
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

- 生成物の開発中は測定器・検証器の改善や機能追加をしない（2026-09-10 裁定）。
- fx（演出エンジン）は実験的機能。設定スキーマは `contracts/` の外（`src/mv_pipeline/fx/`）に置く。`contracts/` への昇格はユーザー承認が必要。
