# 分離・歌詞同期アダプター

Python本体に大型モデル依存を混ぜず、既存環境の実行ファイルを引数配列で呼びます。
JSON内のargvは**ローカルで実行するプログラムの設定**です。自分で内容を確認した設定だけをrun-adapterに渡してください。
storyboardや画像プロンプトからプログラムを起動することはありません。

## 共通形式

```json
{
  "tool": "existing-aligner",
  "version": "installed-fixed-version",
  "argv": ["C:/models/aligner/.venv/Scripts/python.exe", "C:/models/aligner/wrapper.py", "--audio", "{audio}", "--analysis", "{analysis}", "--output", "{output}"]
}
```

利用可能な置換はaudio、lyrics、analysis、output。シェルは使いません。
別のボーカル音源は `input_audio`（プロジェクト相対パス）と `input_sha256` を追加して指定します。

```sh
uv run mv run-adapter projects/song-a aligner-config.json --kind alignment
uv run mv run-adapter projects/song-a separator-config.json --kind separation
```

separationはwork/vocals.wavに出力します。設定に既知の `offset_s` を必ず記録します。
同期の出力は以下の共通JSONです。clip_time = model_time + offset_s。分離器の遅延もこの補正に含めます。

```json
{
  "audio_sha256": "完成ミックスの正規化音源ハッシュ",
  "offset_s": 0.0,
  "tool": {"name": "aligner", "version": "固定版", "model": "使用モデル"},
  "lyrics": [
    {"id": "lyric-001", "text": "窓の向こうへ", "start_s": 1.0, "end_s": 4.0, "timing_source": "model"}
  ]
}
```

confidenceはconfidence_definitionがある場合のみ搬入します。全モデル結果はunreviewedとして扱います。
補間した値はinterpolated、取得できない行はunknown・nullとします。
`uv run mv import-alignment PROJECT result.json` で既存JSONだけを搬入することもできます。

## 同梱の接続スクリプト

- `scripts/demucs_separate.py`: 既存のdemucs環境からHTDemucsのvocalsを取り出します。未キャッシュの重みはDemucsがダウンロードする可能性があります。
- `scripts/qwen_align.py`: 既存qwen-asr環境と事前取得済みのローカルモデルを使用します。モデルディレクトリ、言語、デバイス、offsetを明示します。ネットワーク経由の重み取得は無効です。
- `scripts/suno_align.py`: 保存済みのSunoの歌詞アラインメント（語ごとの秒）を共通形式へ変換します。音源もモデルも使わず、標準ライブラリだけで動きます。ネットワークには出ません（下の「Sunoの語タイミング」）。

Qwenのargv例：

```json
{
  "tool": "qwen-asr",
  "version": "使用環境の実際の版",
  "argv": ["C:/models/qwen/.venv/Scripts/python.exe", "C:/code/Orpheus/scripts/qwen_align.py", "--audio", "{audio}", "--analysis", "{analysis}", "--output", "{output}", "--model-dir", "C:/models/Qwen3-ForcedAligner-0.6B", "--offset-s", "0", "--language", "Japanese", "--device", "cpu"]
}
```

Qwenの文字・単語結果を歌詞行へ順に対応付けます。テキストが一致しない場合や一つのトークンが行境界をまたぐ場合は、時刻を推測せずunknownにします。
confidenceは生成しません。動作確認済みの分離・同期モデルはこの実装環境では見つかっておらず、実モデル推論は未検証です。
実曲の最初の比較は最大2経路に抑え、不十分な部分はapply-editsで修正して制作を続けます。

## Sunoの語タイミング

Sunoの歌詞アラインメントの応答（`{"status": "complete", "words": [{"word", "start_s", "end_s", "success", "p_align"}]}`）をJSONで保存し、プロジェクトのanalysis.jsonと合わせて共通形式にします。音源を使わないので、run-adapterを通さずに実行してimport-alignmentで取り込みます。

```sh
uv run python scripts/suno_align.py --suno suno-alignment.json --analysis projects/song-a/analysis.json --output projects/song-a/work/suno-alignment.json --source-offset-s 0
uv run mv import-alignment projects/song-a projects/song-a/work/suno-alignment.json
```

`--source-offset-s` はSunoの秒から `mv init` に渡した音源の秒へのずれです（mv initに渡した音源の秒 = Sunoの秒 + これ）。Sunoからダウンロードした音源をそのまま使ったなら0です。切り出しの頭（analysisの `audio.source_start_s`）は自動で引き、出力の `offset_s` はそれを合わせた値になります（切り出しの秒 = Sunoの秒 + offset_s）。

- **行の分け方**：語の文字列をつないだ全文を改行で切ります。改行はふつう次の行の頭の語に付いて来ますが（`"\nしろい"` のような語）、前の語の後ろにあっても同じに扱います。`[Verse]` `[Chorus]` などの角かっこの見出しと行の両端の空白は外し、行の中の空白（`" そら"` の頭の空白）は行の字に残します。最後の語の後ろの改行は数えません。丸かっこ（コーラス）は歌われる字として残します。
- **行の秒**：頭の語の `start_s` から終わりの語の `end_s` までです。頭か終わりの語が `success: false`（Sunoが置けず、隣の語から推した秒）なら interpolated、そうでなければ model です。行の途中の `success: false` は行の秒に使いません。
- **行の対応**：Sunoの行と歌詞の行を順に1対1で当てます。字は全角半角・大文字小文字・空白・記号の違いを無視して比べ、`text` か `alignment_text` と一致すれば同じ行とします。行数が違うときや、1行でも字が違うときは、行の対応を決められないので全部の行をunknown・nullにします。理由は各行の `reason` と標準エラーに出ます。
- **曲の一部を使うとき**（`mv init --start S --duration D`）：歌詞を切り出しの行だけにしたときは、Sunoの行のうち切り出しの中に収まる行（続いた並び）が歌詞と同じ数のときだけ、それを当てます。同じ字の行（くり返しのサビなど）は秒で見分けます。切り出しのすぐ外の行の秒が分からないときは当てません。
- **unknownになる行**：行の境目をまたぐ語（1つの語に前の行の終わりと次の行の頭が入っている）のある2行と、切り出しの範囲からはみ出す行です。
- **残すもの**：元の語の並びは `raw_words`、Sunoの `audio_id` は `suno` に残ります。confidenceは作りません（`p_align` の定義をSunoが示していないため）。`tool.version` はSunoが版を返さないので既定で `not reported by Suno` です。取得日などを残すなら `--tool-version` で書きます。
- **前提（未検証）**：Sunoの1回の生成で出る2曲は歌詞が同じなので、字の照合では見分けられません。ダウンロードした音源と同じ曲の `audio_id` の応答を使ってください。Sunoの0秒とダウンロードしたWAV・MP3の0秒がそろっていることも前提で、確かめていません（MP3はデコーダの遅れが出ることがあります）。

2026-11-06に、Sunoの実際の応答1曲分（7行）で、作品フォルダで使った使い捨ての変換と行・語の秒が一致することを確かめました。Sunoの秒が実際の歌い出しに合っているかは確かめていません。取り込んだ行はunreviewedのままなので、試聴で確かめるかapply-editsで直します。

### 語の秒（dance spec 用）

`--words-output words.json` で行ごとの語の秒も書き出します。`lines[]` の `text`・`start`・`end`・`words` はdance specの `lyrics.lines[]` にそのまま貼れます（`layer` は足します）。

```json
{"id": "lyric-001", "text": "窓の向こうへ", "timing_source": "model", "start": 1.0, "end": 3.0,
 "words": [{"text": "窓", "t": 1.0}, {"text": "の", "t": 1.4}, {"text": "向こう", "t": 1.6}, {"text": "へ", "t": 2.4}],
 "interpolated_words": []}
```

- 秒はプロジェクトの切り出し音源の秒（Sunoの秒 + offset_s）です。dance specの `audio` がその切り出し音源で `offset_s` が0なら、動画の秒と同じです。
- `words` をつなぐと `text` と一致します（dance specの決まり）。Sunoの字が歌詞の行と字面で違う行（空白・記号・全角半角だけの違い）は語に分けず、`words` をnullにします。
- `interpolated_words` は `success: false` の語の番号（0から）です。`start`・`end` は歌っている間なので、字を出す長さは作品ごとに決めます。

公式API参照：[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[Demucs](https://github.com/facebookresearch/demucs)。
