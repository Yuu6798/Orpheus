# Remotion 用の props（`mv export remotion-props`）

Remotion を「透過レイヤーの部品」（歌詞テロップなどを透過 PNG 連番で描く側）として使うとき、Remotion 側に秒を扱わせず、
**コマ番号だけ**の props.json を渡すための書き出し。analysis.json（と任意で storyboard.json）から作る。

```sh
uv run mv export remotion-props projects/song-a --out projects/song-a/work/props.json
uv run mv export remotion-props projects/song-a --out props.json --beats-per-bar 4            # beats_s を 4 つずつ数えて小節頭を作る
uv run mv export remotion-props projects/song-a --out props.json --beats beats.json          # gate / dance と同じ {"beats", "downbeats"} を使う
```

- 形式は `remotion-props/0.1`（`contracts/` の外。正は `src/mv_pipeline/remotion_props.py`、TS の型は `renderer/src/lyric-props.ts`）。
- 行ごとに `id`・`text`・`display`（表示用の文字。今は `text` と同じ）・`sung`・`start_frame`（floor）・`end_frame`（ceil。含まない）・`timing_source`・`review_status`・`confidence`。丸めは `mv render` の字幕と同じ関数（`validate.frame_span`）を呼ぶので、2 つの描画で行の出入りのコマがずれない。
- 時刻の無い行（`start_s` が null）は `lines` に入れず `untimed` に残す。推定の行は `timing_source` / `review_status` をそのまま写し、確定扱いにしない。
- `beats.frames` / `beats.downbeat_frames` はいちばん近いコマ（`validate.frame_at`）。analysis.json には小節頭が無いので、`--beats`（ファイル。sha256 を記録）か `--beats-per-bar N [--downbeat-phase k]` で与える。どちらも無ければ空。
- `storyboard.json` があれば `scenes`（コマ番号の場面）と `show_lyrics` も写す（現在の analysis.json と合わないものは止まる。`--no-storyboard` で省ける）。
- `fps`・`width`・`height`・`duration_frames`（`mv render` と同じ式）と、元ファイルの sha256（`sources`）。

端数の例（30 fps）: 3.02 秒の始まりは 90 コマ、5.48 秒の始まりは 164 コマ、5.48 秒の終わりは 165 コマ。

書き出しはプロジェクトのファイルを読むだけで、`--out` 以外を書かない（`project_lock` も通らない）。Remotion で描いた連番を重ねる（`mv overlay`）、連番から timeline を起こす（`mv gate timeline`）、描画を台帳に載せる工程は別。
