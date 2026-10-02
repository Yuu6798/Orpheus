# Orpheus

- 動く既存ツールを優先する。
- contracts/ の入出力契約を無断変更しない。
- 推定を実測扱いしない。合成素材のテストと実曲の評価を区別する。
- 制作を採点器開発で止めない。
- 依頼のない送信・公開をしない。素材・鍵・モデル・動画をGitに入れない。
- Pythonの検証は `uv run pytest`、描画コードは `pnpm typecheck`。
- `uv run mv demo projects/demo` と `uv run mv render projects/demo` で実描画を検証する。
