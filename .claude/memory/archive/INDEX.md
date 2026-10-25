# Archive Index

30 日以上経過したセッションログの格納先。元テキストをそのまま保持し、情報損失ゼロで移動する。

## 格納ルール

- ディレクトリ構造: `archive/YYYY-MM/YYYY-MM-DD.md`
- `_index.md` の該当エントリは 1 行要約 + アーカイブパスに短縮する
- STATUS.md の Recently Merged は最新 5 件のみ保持し、溢れ分は `STATUS_MERGED_LOG.md` の末尾へ移す

## 格納済みファイル

| 元パス | アーカイブ先 | 移動日 |
|---|---|---|
