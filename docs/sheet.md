# 見るためのシート（`mv sheet`）

動画か透過の連番から、指定した時刻か timeline の出来事（行の開始・フラッシュ・切れ目）のコマを 1 枚の PNG に並べ、コマ番号・秒・出来事の名前を書き込む。**検査ではなく閲覧の道具**で、合否は出さない。AI が動画を再生せずに「どのコマに何が写っているか」を確かめる入口。

```sh
uv run mv sheet dance_runs/<run_id>/dance.mp4 --out sheet.png --at 1.0 2.5 4.0
uv run mv sheet dance_runs/<run_id>/dance.mp4 --out sheet.png --events dance_runs/<run_id>/timeline.json --kinds lyrics,flashes
uv run mv sheet out/lyrics_png --out sheet.png --at 3.0 5.5 --fps 30 --backgrounds white,black,gray     # 透過の連番
```

- 時刻 → コマ番号はいちばん近いコマ（`validate.frame_at`）。連番の fps は `--fps`（`--events` の timeline があればその fps）。動画の fps は動画から読み、`--fps` や timeline の fps と 1% 以上違えば止める。
- 透過の連番（PNG。straight α）は、白・黒・灰（`--backgrounds`）の上に置いた版を横に並べる（縁と読みやすさの確認用）。動画は不透明として扱い（アルファつきの動画は先に `mv overlay` などで平らにする）、`--columns` 列に折り返す。セルの幅は `--cell-width`（既定 320）。連番の並びは数字の大小（`frame2` < `frame10`）。寸法のそろわない連番は止める。
- 字は `--font`（TTF / TTC）か、よくある場所の日本語の書体を探して使う。見つからなければ Pillow の既定の書体（日本語が無い）で、出来事の名前は ASCII の略記（`line 1` / `flash` / `seam` / `given`）にして画像に書く。正式な名前は JSON に残り、どちらで書いたかは JSON の `labels` / `label_font` で分かる。
- 隣に `<out>.json`（セルごとのコマ番号・秒・出来事・背景・そのコマの sha256（連番はファイル、動画は画素）、動画の外の時刻、出所）を書く。
- 動画の外の時刻は並べず `missing` に残す。
- 動画のコマの取り出しは `mv inspect` と同じ読み手（ffmpeg。復号順のコマ番号、時刻のシークは使わない）。
- cv2 は使わない（Pillow・numpy・ffmpeg）。実験的。

AI が見るときの読み方: `sheet.json` の `cells[*].frame` / `t` / `event` で、画像のどの位置（上から `cell // columns` 行目、左から `cell % columns` 列目）がどのコマかが分かる。字の縁や読みやすさは白・黒・灰の 3 版を見比べる。
