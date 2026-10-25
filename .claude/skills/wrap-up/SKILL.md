---
name: wrap-up
description: Persist a session-end reflection into .claude/memory for the Orpheus repo (handoff notes for the next session). Use when the user signals the session is ending — e.g. 「今日はここまで」「今日は終わり」「セッション終了」「また明日」「お疲れ様」「引き継ぎ」「done for today」「that's all」 — or runs /wrap-up manually.
---

# wrap-up — セッション記憶の保存（Orpheus 版）

svp-video-pipeline / ugh-prompt-engine の `.claude/memory/` 方式を Orpheus 向けに最小化したもの。
discipline テストは移植していないので、手順 6 の 3 点を目で確かめる。

**このリポジトリは public。** 記憶ファイルに作品名・素材・費用・Drive ID・個人のパス・鍵を書かない。作品は「第 N 作」、場所は「PC のダウンロード配下」のように一般化する。

## 手順（順番どおりに）

1. **振り返りを保存**: `.claude/memory/YYYY-MM-DD.md`（今日の日付）に書く。同じ日のファイルがあれば `## Session N` を追記する。
   節は `### Context` / `### Design Decisions` / `### Artifacts` / `### Handoff`。メインが直接手を動かした作業（委譲しなかった実装・実行・検証）があれば Artifacts に 1 行。
2. **索引に 1 行**: `.claude/memory/_index.md` に `- YYYY-MM-DD: <1 行成果>。[詳細](YYYY-MM-DD.md)` を足す（同日複数は `(Session N)`）。1 エントリ 500 字以内。
3. **30 日超の日付ファイルを移す**: `archive/YYYY-MM/` へ原文のまま移し、`_index.md` の行を 1 行要約＋移動先に縮め、`archive/INDEX.md` に記録する。
4. **Next-Issue Queue を掃除**（手順 5 より先）: `STATUS.md` の Queue から終わった項目を消し、`## Recently Merged` の行に変える。
5. **Recently Merged を 5 件に**: 溢れた古い行を `archive/STATUS_MERGED_LOG.md` の末尾へ原文のまま移す。
6. **目視チェック**（discipline テストの代わり）:
   - `STATUS.md ## Phase` が 1 段落だけ（古い段落を残さない）
   - Queue の Title に「完了」「済」「merged」など終わった印が無い
   - `_index.md` の各行が 500 字以内
7. **方針文書の更新は提案だけ**: CLAUDE.md / AGENTS.md を変えたほうがよいと思ったら、ユーザーに提案する（黙って書き換えない）。
8. **保存**: Orpheus は main へ直接 push しない。ブランチでコミットして PR にする（`.claude/memory/` だけの変更でも同じ。直接 push の例外はユーザーが決めたら CLAUDE.md に書く）。

## 終わりに

変更した記憶ファイル、archive への移動の有無、目視チェックの結果、提案した方針変更をユーザーに短く伝える。
