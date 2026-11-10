# 見るためのシート（`mv sheet`）

動画か透過の連番から、指定した時刻・等間隔の時刻・timeline の出来事（行の開始・フラッシュ・切れ目）のコマを 1 枚の画像（PNG か JPEG）に並べ、コマ番号・秒・出来事の名前を書き込む。**検査ではなく閲覧の道具**で、合否は出さない。AI が動画を再生せずに「どのコマに何が写っているか」を確かめる入口。

```sh
uv run mv sheet dance_runs/<run_id>/dance.mp4 --out sheet.png --at 1.0 2.5 4.0
uv run mv sheet dance_runs/<run_id>/dance.mp4 --out sheet.png --events dance_runs/<run_id>/timeline.json --kinds lyrics,flashes
uv run mv sheet out/lyrics_png --out sheet.png --at 3.0 5.5 --fps 30 --backgrounds white,black,gray     # 透過の連番
uv run mv sheet dance_runs/<run_id>/dance.mp4 --out every.jpg --every 5                                 # 0 秒から 5 秒おき。JPEG
uv run mv sheet dance_runs/<run_id>/dance.mp4 --out parts.jpg --count 12 --at 1.0 2.5                   # 12 等分した各区間の中央と、指定の時刻
uv run mv sheet dance_runs/<run_id>/dance.mp4 --out detail.png --every 2 --crop 0.55 0.30 0.75 0.50     # その範囲を縮めず原寸で並べる
```

- 時刻は `--at`（秒の並び）か `--events`（timeline.json）のどちらか一方に、`--every S`（0 秒から S 秒おき。動画・連番の長さ未満）と `--count N`（全体を N 等分した各区間の中央）を足せる。`--every`・`--count` だけでもよい（4 つのどれかは要る）。S が 0 以下・N が 0 以下は止める。
- `--every`・`--count` を使うと、全部の時刻を時刻順に並べる（使わなければ今までどおりで、`--at` は書いた順）。等間隔・等分の時刻は、同じコマがすでにあれば足さない（残るのは `--at`・`--events` のコマ、`--every`、`--count` の順）。`--at`・`--events` の中の同じコマは、今までどおり両方並べる。出来事の名前は「等間隔」「等分」（ASCII の略記は `every`・`count`）。
- 等間隔・等分の時刻は動画の中の時刻なので、いちばん近いコマが最後のコマの次になるとき（最後の半コマ）は最後のコマにし、`missing` には入れない。`--events` と一緒のときは、等間隔・等分の時刻も timeline の時刻（`interval.start` を足した値）で書く。
- `--crop X0 Y0 X1 Y1` は、各コマをその範囲で切り出し、**縮めずに原寸**でセルにする（縮めた一覧では見えない小さな不具合を見るため）。`--cell-width` を明示したときだけ、その幅に合わせる。単位は `--units norm`（既定。コマの 0..1）か `px`（画素）で、`mv fx guides` の `--crop` と同じ形・同じ切り方（norm は幅・高さを掛けて切り捨て。px は渡した画素をそのまま使い、小数は切り捨て）。範囲が逆・コマの外・面積 0（1 画素より小さい）は止める。
- `--crop` のセルは、字を帯の中に寄せて、絵には字を掛けない。PNG なら、セルの絵は切り出した範囲の画素そのまま（透過の連番は、背景に置いた後の画素）。`--crop` なしのセルは今までどおりの絵で、字の下の端が絵のいちばん上の数行に掛かることがある。原寸のセルが狭いと、字は右で切れる（JSON には全部残る）。
- `--out` の拡張子で形式が決まる: `.png`、または `.jpg`・`.jpeg`（品質 90）。ほかの拡張子は止める（以前は、どの拡張子でも PNG の中身を書いていた）。隣の JSON の名前は今までどおり（拡張子を `.json` に替えた名前）。
- 時刻 → コマ番号はいちばん近いコマ（`validate.frame_at`）。timeline の時刻はショットの時刻なので、一部の区間だけ描いた run（`interval.start` > 0）では、その分を引いてから動画のコマにする（JSON の `events.interval_start`）。透過は α のチャンネルか PNG の透明色（tRNS）で判定する。連番の fps は `--fps`（`--events` の timeline があればその fps）。動画の fps は動画から読み、`--fps` や timeline の fps と 1% 以上違えば止める。
- 透過の連番（PNG。straight α）は、白・黒・灰（`--backgrounds`）の上に置いた版を横に並べる（縁と読みやすさの確認用）。動画は不透明として扱い（アルファつきの動画は先に `mv overlay` などで平らにする）、`--columns` 列に折り返す。セルの幅は `--cell-width`（既定 320。`--crop` があれば切り出した範囲の幅）。連番の並びは数字の大小（`frame2` < `frame10`）。寸法や透過の有無のそろわない連番は止める。
- 字は `--font`（TTF / TTC）か、よくある場所の日本語の書体を探して使う。見つからなければ Pillow の既定の書体（日本語が無い）で、出来事の名前は ASCII の略記（`line 1` / `flash` / `seam` / `given` / `every` / `count`）にして画像に書く。正式な名前は JSON に残り、どちらで書いたかは JSON の `labels` / `label_font` で分かる。
- 隣に `<out>.json`（セルごとのコマ番号・秒・出来事・背景・切り出した画素の範囲 `crop`・そのコマの sha256（連番はファイル、動画は画素。切り出す前のコマ全体）、動画の外の時刻、出所、`format`・`every`・`count`・`crop`（単位・渡した値・画素・原寸かどうかの `native`））を書く。使わなかった項目は null。
- 動画の外の時刻は並べず `missing` に残す。
- 動画のコマの取り出しは `mv inspect` と同じ読み手（ffmpeg。復号順のコマ番号、時刻のシークは使わない）。
- cv2 は使わない（Pillow・numpy・ffmpeg）。実験的。
- `--every`・`--count`・`--crop`・JPEG は合成素材（コマ番号と場所を画素値で読める動画と、透過の連番）で確かめた。実素材では未確認。`--every`・`--count`・`--crop` を使わない呼び出しの PNG は、前の版とバイト単位で同じ。

AI が見るときの読み方: `sheet.json` の `cells[*].frame` / `t` / `event` で、画像のどの位置（上から `cell // columns` 行目、左から `cell % columns` 列目）がどのコマかが分かる。字の縁や読みやすさは白・黒・灰の 3 版を見比べる。`--crop` のシートでは、セルの絵（字の帯の下）がコマの `cells[*].crop` の範囲で、`crop.native` が true なら 1 画素がそのまま 1 画素（字の帯の高さは、セルの高さから範囲の高さを引いた値）。
