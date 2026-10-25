---
name: wrap-up
description: Persist a session-end reflection into .claude/memory for the Orpheus repo (handoff notes for the next session). Use when the user signals the session is ending — e.g. 「今日はここまで」「今日は終わり」「セッション終了」「また明日」「お疲れ様」「セッションの引き継ぎ」「引き継ぎをまとめて」「done for today」「that's all」 — or runs /wrap-up manually.
---

# wrap-up — セッション記憶の保存（Orpheus 版）

svp-video-pipeline / ugh-prompt-engine の `.claude/memory/` 方式を Orpheus 向けに最小化したもの。
discipline テストは移植していないので、手順 6 の点を目で確かめる。合図は文脈つきの語で判断し、「引き継ぎ」単体では発火しない（RunPod 用の引き継ぎ書は別スキル）。

**このリポジトリは public。** 記憶ファイルにも Git の禁止対象（素材・鍵・モデル・動画・作品名・Drive ID・費用）に加えて、個人のパスも書かない。未公開作品の中身が読み取れる具体例も避ける。作品は「第 N 作」、場所は「PC のダウンロード配下」のように一般化する。

## 手順（順番どおりに）

1. **振り返りを保存**: `.claude/memory/YYYY-MM-DD.md`（今日の日付）に書く。同じ日のファイルがあれば `## Session N` を追記する。
   見出しは最初のセッションが `# Session Summary — YYYY-MM-DD`、同日 2 回目以降は `## Session N`。節は `### Context` / `### Design Decisions` / `### Artifacts` / `### Handoff`。自分で実行した主な作業（任せた先があればそれも）を Artifacts に 1 行。ユーザーや他セッションの報告は出どころを書き、実測と区別する。
2. **索引に 1 行**: `.claude/memory/_index.md` に `- YYYY-MM-DD: <1 行成果>。[詳細](YYYY-MM-DD.md)` を足す（同日複数は `(Session N)`）。1 エントリ 500 字以内。
3. **30 日超の日付ファイルを移す**: `archive/YYYY-MM/` へ原文のまま移し、`_index.md` の行を 1 行要約＋移動先に縮め、`archive/INDEX.md` に記録する。
4. **Next-Issue Queue を掃除**（手順 5 より先）: `STATUS.md` の Queue から終わった項目を消し、`## Recently Merged` の行に変える。
5. **Recently Merged を 5 件に**: 溢れた古い行を `archive/STATUS_MERGED_LOG.md` の末尾へ原文のまま移す。
6. **目視チェック**（discipline テストの代わり）:
   - `STATUS.md ## Phase` が 1 段落だけ（古い段落を残さない）
   - Queue の Title に終わった印（merged・完了・done・closed・shipped・済）が無い
   - `_index.md` の各行が 500 字以内
   - Next-Issue Queue と Handoff が、このセッション限りの場所（作業用フォルダ等）を参照していない
   - CLAUDE.md が 40 行前後のまま
7. **方針文書の更新は提案だけ**: CLAUDE.md / AGENTS.md を変えたほうがよいと思ったら、ユーザーに提案する（黙って書き換えない）。
8. **保存**: CLAUDE.md の Git 節に従う（main へ直接 push しない。`.claude/memory/` だけの変更でもブランチでコミットして PR にする。直接 push の例外はユーザーが決めたら CLAUDE.md に書く）。このリポジトリは public なので、終了の合図だけでは公開の許可とみなさない（AGENTS.md「依頼のない送信・公開をしない」）。既定ではブランチへのコミットまでにとどめる。push と PR 作成は、ユーザーが明示的に頼んだとき（「保存して」「PR にして」など）か、push してよいか尋ねて了承を得たときだけ行う。

## 終わりに

変更した記憶ファイル、archive への移動の有無、目視チェックの結果、提案した方針変更をユーザーに短く伝える。
