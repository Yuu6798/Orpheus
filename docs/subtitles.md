# 字幕ファイル（`mv export subtitles`）

歌詞の行の時刻から、動画の字幕ファイル（SRT・WebVTT・LRC）と、動画の概要欄に貼る歌詞を書く（実験的）。
読むのは入力だけで、書くのは `--out` のフォルダだけ。描画・送信・公開はしない。

```sh
uv run mv export subtitles projects/song-a --out projects/song-a/work/subs              # analysis.json の lyrics から SRT・VTT・LRC
uv run mv export subtitles lyrics.json --out subs --name song_en --formats srt,vtt,description \
    --vtt-settings "line:8% position:50% align:center"                                 # 画面の上に出す WebVTT と、概要欄の歌詞
uv run mv export subtitles lyrics.json --out subs --name song_ja --field ja --formats srt   # 訳の欄から
uv run mv export subtitles song.lrc --out subs --duration 231.583                      # LRC から（最後の行に終わりの印が無ければ動画の長さまで）
```

## 入力

| 入力 | 読むもの |
|---|---|
| プロジェクトのフォルダか `analysis.json` | `lyrics` の `text`（`--field alignment_text` も可）・`start_s`・`end_s`、時刻の出所（`timing_source`・`review_status`） |
| 行の JSON | `[{"start", "end", "text", "section", "id", "confidence"}, …]`。`start_s`・`end_s` でもよい。本文は `--field` の欄（既定は `text`、無ければ `display` か `line`。`mv gate lines` の JSON と同じ）。`section` で概要欄の歌詞の段落を分ける |
| LRC | `[mm:ss.xx]本文`。行の終わりは、空の行（終わりの印）か次の行の頭。最後の行に終わりの印が無ければ `--duration` まで |

- 時刻の無い行（`analysis.json` の `start_s` が null）は字幕に入れず、結果と記録の `untimed` に id を残す（`mv export remotion-props` と同じ）。
  概要欄の歌詞には入れる（時刻を使わないため）。
- 本文の空白は行ごとに除き、空の行は飛ばす。本文に `-->` があれば止める（SRT・WebVTT の時刻の区切りと同じ字）。

## 字幕の出入り

本編のテロップと同じ出入りにする（実制作の公開準備の字幕と同じ式）。

```text
出る   = 歌い出し − lead            （既定 0.15 秒。0 秒より前にはしない）
消える = min(歌い終わり + tail, 次の字幕の出る時刻)   （tail の既定 0.40 秒。字幕は重ねない）
```

- 行は歌い出しの順に並べる。歌が重なる行も、前の字幕は次の字幕が出る時刻で消える。
- `--duration`（動画の長さ）があれば、消える時刻をそこで切る。その後に出る行があれば止める。
- 時刻はミリ秒（LRC は 1/100 秒）に丸める（Python の `round`）。1 時間を越えても `HH:MM:SS,mmm` で書く。

## 書き出すもの

UTF-8（BOM なし）、改行は LF。名前の頭は `--name`（既定は、プロジェクトならフォルダの名前、ファイルならその名前）。

| 形式（`--formats`） | ファイル | 中身 |
|---|---|---|
| `srt`（既定） | `<名前>.srt` | 番号・`HH:MM:SS,mmm --> HH:MM:SS,mmm`・本文を空行で区切る |
| `vtt`（既定） | `<名前>.vtt` | `WEBVTT` と同じ字幕（時刻の小数点は `.`）。`--vtt-settings` を時刻の後ろに付ける。例えば `line:8% position:50% align:center` で画面の上 8% に置く（下の焼き込みの歌詞と重ねない） |
| `lrc`（既定） | `<名前>.lrc` | `[mm:ss.xx]本文`（歌い出しの時刻。lead を引かない）。次の行まで間があれば、歌い終わりに空の行（終わりの印）。`mv gate lines` でそのまま読める |
| `description` | `<名前>_description.txt` | `--description-heading`（既定 `■ Lyrics`。空にすると無し）と、`section` ごとに空行で分けた本文。動画の概要欄に貼る形 |
| （いつも） | `<名前>_subtitles.json` | 記録 `subtitles-export/0.1`（`contracts/` の外）。入力のパス・種類・sha256、設定、行と字幕の数、`untimed`、時刻の出所の数、最初と最後の時刻、書いたファイルの sha256、注意 |

- 既にあるファイルと中身が違えば、`--force` を付けない限り何も書かない（手で直した字幕を消さない）。同じ中身なら書かずに `unchanged` と記録する。
- 概要欄の歌詞に `<` か `>` があれば止める（YouTube の概要欄は受け付けない）。`--description-body 本文.txt` を渡すと、本文と歌詞を
  合わせた大きさ（本文 + 改行 + 歌詞）を数え、5,000 バイト（YouTube Data API の上限）を超えれば止める。

## 推定と確認

字幕の時刻は、入力の行の時刻をそのまま使う。行の時刻が推定か確認済みかは、結果と記録の `timing` に数で残す（確定扱いにしない）。

- `analysis.json` は `timing_source/review_status`（例 `model/unreviewed`・`manual/confirmed`）ごと。
- 行の JSON は `confidence` の文字（例 `推定`）ごと。無ければ `unknown`。
- LRC は `lrc`（出所は LRC からはわからない）。

公開の前に、試聴で行の時刻を確かめる（`mv gate lines` の確認表）。

## 実制作との比較

- 実制作の公開準備のスクリプト（英語の字幕と、概要欄の歌詞）を写した関数と比べた（`tests/test_subtitles.py`）。合成の 7 行で、SRT・画面の上の WebVTT・概要欄の歌詞の文字列が同じだった。
  - 7 行には、間の空く行・重なる行・区間の変わり目・1 時間を越える時刻を入れた。
- 実際の 1 曲（35 行。時刻は推定）でも同じ入力から書き出した。実制作が書き出した SRT・画面の上に出す WebVTT・概要欄の歌詞の 3 つが、
  バイト単位で同じだった。
- 実制作のスクリプトにある最短の表示時間（0.12 秒）は、式の上で結果を変えていなかったので入れていない
  （次の字幕と重なるときは、どの値でも次の字幕の出る時刻で消える）。LRC と、`analysis.json`・LRC の読み込みは公開で足したもの。
