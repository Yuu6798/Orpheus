# CLAUDE.md — Orpheus

@AGENTS.md

AGENTS.md（契約不変・実測/推定の区別・採点器で止めない・依頼なき送信/公開禁止・検証/依存ルール）が正。
Claude Code 固有の運用だけをここに置く（詳細は docs/ へ）。

## Commands（リポジトリルートで実行。uv は 0.13.5 以上）

```sh
uv sync --locked --extra analysis --extra fx && pnpm install --frozen-lockfile   # fx の E2E は --extra fx 必須
uv run pytest                  # 約 50 秒。fx のテストは --extra fx で同期した環境でだけ走る（無いと cv2 を使うテストは黙って skip される）
pnpm typecheck && pnpm test    # tsc --noEmit / node --test
uv run mv demo projects/demo && uv run mv render projects/demo && uv run mv verify projects/demo
uv run mv fx demo projects/fxdemo && uv run mv fx render projects/fxdemo/spec.json   # fx（実験的。docs/fx.md。--extra fx が前提）
```

## Session Memory（引き継ぎ）

- セッション開始時に `.claude/memory/_index.md` と `.claude/memory/STATUS.md` を読み、直近の日付ファイルで詳細を確かめる。
- 終了の合図（「今日はここまで」「セッションの引き継ぎ」など。「引き継ぎ」単体では発火しない）や `/wrap-up` で `.claude/skills/wrap-up/SKILL.md` の手順を実行する（手順はスキルが正）。
- 記憶ファイルも public。下の Git 節の禁止対象に加えて、個人のパスも書かない。

## Git

- main へ直接 push しない。`claude/<topic>` ブランチで変更し PR にする。コミットは Conventional Commits（日本語可）。
- このリポジトリは public。素材・鍵・モデル・動画・作品名・Drive ID・費用は Git に入れない（.gitignore 済みでも書かない）。
- `contracts/` は変更しない。依存を変えたら lock も同じ PR で更新する。
