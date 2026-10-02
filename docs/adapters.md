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

公式API参照：[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[Demucs](https://github.com/facebookresearch/demucs)。
