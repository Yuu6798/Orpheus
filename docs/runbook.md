# 実行手順

すべてリポジトリのルートで実行します。Pythonパッケージ単独配布ではなく、renderer/contractsを含むチェックアウトを使用します。

## 1. 入力を用意

歌詞はUTF-8、一行を一つの表示単位にします。対象区間に含まれる歌詞だけを入力します。

```sh
uv run mv init projects/song-a --audio song.wav --lyrics lyrics.txt --start 60 --duration 45
uv run mv analyze projects/song-a
```

初期設定は1920×1080・30fps。`--width`、`--height`、`--fps`で変更可能です。
時刻の0秒は切り出し音源の先頭です。元曲の開始秒と元ファイルのハッシュは別に残ります。
analyzeは拍・BPM候補を計算し、RMSをwork/librosa.jsonに保存します。歌詞時刻やサビラベルを推測して確定扱いにはしません。

## 2. 歌詞時刻を確認

最短経路は手修正JSONの搬入です。analysis.jsonの音源ハッシュ・歌詞ID・表示テキストを転記します。

```json
{
  "audio_sha256": "analysis.jsonのaudio.sha256を転記",
  "lyrics": [
    {"id": "lyric-001", "text": "窓の向こうへ", "start_s": 0.75, "end_s": 3.4}
  ]
}
```

```sh
uv run mv apply-edits projects/song-a timings.json
```

このファイルは現在の手修正全体として置き換えます。前版はeditsに保存されます。
モデル同期の取り込みは `import-alignment`。形式と既存モデル接続は [adapters.md](adapters.md) を参照してください。
再解析してもeditsの値を最後に適用します。歌詞変更は `update-lyrics PROJECT lyrics.txt` を使います。
歌詞IDまたはテキストが変わった修正は適用せずissuesに残します。音源・切り出しを変える場合は新しいプロジェクトを初期化してください。

## 3. 演出を作る

```sh
uv run mv export-brief projects/song-a
```

work/briefにanalysis、ハッシュと描画設定、スキーマ、指示書を出力します。世界観の希望と一緒に演出担当へ渡し、返却されたstoryboardを取り込みます。

```sh
uv run mv import-storyboard projects/song-a storyboard.json
```

analysisを変更した後は新しいbriefを出し、演出を再確認して新しいanalysis_sha256を反映したstoryboardを取り込んでください。
シーンは0から総フレーム数まで連続させます。総フレーム数はceil(sample_count × fps / sample_rate)。
字幕は歌詞時刻に従い、lyric_idsやシーン境界では切りません。

## 4. 画像を取り込む

画像をプロジェクト内のimagesへコピーし、contracts/assets.schema.jsonに従ってassets.jsonを作ります。
相対パス、実寸、実ファイルのSHA-256、参照画像IDを記録します。

```sh
uv run mv import-assets projects/song-a assets.json
```

画像1枚を変えたら、そのハッシュ・寸法を更新して再登録します。参照画像が変わった場合は依存画像の連続性を確認し、`confirm-assets PROJECT`で確認完了を記録します。
画像生成APIは呼びません。既存画像も生成済み画像も同じ契約で扱います。

## 5. 確認と描画

### 一時領域と出力先を確認

描画前に、Node が実際に使う一時領域と、動画・ログを保存する `runs` の空き容量・書き込み権限を確認します。Linux/macOS では次のように確認できます。

```sh
orpheus_tmpdir="$(node -p 'require("node:os").tmpdir()')"
df -h "$orpheus_tmpdir" projects/song-a/runs
test -w "$orpheus_tmpdir"
test -w projects/song-a/runs
```

`/tmp` は環境によってディスク上にも tmpfs 上にもあります。小容量の tmpfs などで容量が足りない場合は、十分な空き容量と書き込み権限のあるディスク上のディレクトリを `TMPDIR` に指定します。以下の `/mnt/render-disk` は、実際のディスクのマウント先に置き換えてください。設定後は一時領域と出力先の容量を再確認します。

```sh
mkdir -p /mnt/render-disk/orpheus-tmp
export TMPDIR=/mnt/render-disk/orpheus-tmp
node -p 'require("node:os").tmpdir()'
df -h "$TMPDIR" projects/song-a/runs
test -w "$TMPDIR"
```

`TMPDIR` の変更は動画の出力先を変えません。bundle には素材のコピーも作られるため、1回の描画で必要な一時容量を確保してください。必要量は素材や描画条件で変わり、固定の容量閾値による自動停止は行いません。

### 描画を実行

```sh
uv run mv validate projects/song-a
uv run mv preview projects/song-a
uv run mv preview projects/song-a --studio
uv run mv render projects/song-a --profile final
uv run mv verify projects/song-a
uv run mv resume projects/song-a
```

previewは未確認時刻を警告付きで許可します。Studioは終了するまでコマンドを実行したままにします。
finalはunknown、未確認・修正要の歌詞、未解決issuesがあれば停止します。明示的にstoryboard.render.show_lyricsをfalseにすれば字幕なしで描画できます。
実際に歌唱が重なる行は自動で一方を隠しません。人が表示行を選び、契約の歌詞時刻を整理してください。
resumeは現在の契約を検査し、必要な場合のみ描画を再開します。分離・同期・API生成は勝手に再実行しません。

各runにJSON・画像・正規化音源・手修正・バージョン指紋・描画ログ・動画・検証結果が残ります。
一時動画は検証に通ってからvideo.mp4になります。失敗したrunも調査用に残り、過去の成功動画は上書きされません。

### 失敗時の診断と後始末

`runs/<run_id>/render.log` には renderer の標準出力・標準エラーの全文が残ります。失敗時は、失敗工程、元のエラーと取得できる原因コード・cause、一時領域と出力先の空き容量、メモリ状況を一時 bundle の削除前に記録します。CLI の原因要約と併せて確認し、`ENOSPC` などの原因コードをメモリ不足と取り違えないようにします。診断情報を取得できなくても、元のエラーを保持します。

通常終了・例外時には、その描画で作った一時 bundle の削除を試みます。過去に残ったディレクトリや、`SIGKILL` などの強制終了による残骸は自動削除しません。古い `remotion-webpack-bundle-*` を整理する場合は、対象のパスと、実行中の描画が使用していないことを確認してから個別に削除してください。`runs` は調査記録として残します。ログの保存先自体が容量不足の場合やプロセスの強制終了時には、診断を保存できない場合があります。

## 検証結果の読み方

- technical_passは映像・音声の機械検査の成功です。
- acceptedは常にfalseから始まります。視聴して採用した場合だけ手動で別の視聴記録に残してください。
- AACの許容差は1フレーム＋1024音声サンプルです。使用した値を検証JSONへ保存します。
- 元音源に対して250msごとの音量低下とゼロ遅延相関を調べます。元から静かな区間は無音異常にしません。
- verifyは記録されたrunを検査し、現在のJSONと一致するかも表示します。入力更新後の古い動画を現在の成果物と混同しないでください。

視聴時には行の境界、繰り返し、長い母音、英日混在、文字の収まり、画像の連続性を確認してください。
成果物の搬出はvideo.mp4を指定の保存先へコピーします。外部アップロードは独立の操作です。
