# Orpheus / オルフェウス

完成ミックス音源、歌詞時刻、画像、演出JSONから、ローカルで再生成できるMV制作パイプライン。
Pythonが入力検証と工程管理を行い、Remotionが静止画のパン・ズーム・クロスフェード、歌詞、音声をMP4へ描画します。

## 動かす

Python 3.11〜3.13、Node.js 22以上、uv、pnpmが必要です。リポジトリのルートで実行してください。

```sh
uv sync --locked --extra analysis
pnpm install --frozen-lockfile
uv run mv demo projects/demo
uv run mv render projects/demo
uv run mv verify projects/demo
```

6秒の合成音・幾何学画像・架空歌詞のテスト動画を `projects/demo/runs/<run_id>/video.mp4` に生成します。
初回の描画ではRemotionがChrome Headless Shellを取得します。FFmpegはPATH上のもの、なければ固定版imageio-ffmpeg、ffprobeはRemotion同梱版を使います。
既存ブラウザーを使う場合は `ORPHEUS_BROWSER` に実行ファイルのパスを設定できます。

## 実装済み

- 入力のSHA-256記録、区間切り出し、48kHz・ステレオPCMへの正規化
- 3つの原契約を変更せず、スキーマ・参照・時刻・ファイル・パス・ハッシュを検証
- librosaによる拍・BPM候補・RMS。構成ラベルは手動区間を使用
- 歌詞同期JSONの搬入、既存分離・同期環境へのCLI接続、時刻オフセット補正
- 元解析と別管理する手修正、歌詞変更時の未解決表示
- 演出用brief出力、storyboard/assets搬入、参照画像変更の再確認
- Remotion Studioと本番で共通のコンポジション、全体レイヤーの日本語歌詞
- プロジェクトロック、工程状態、ハッシュキャッシュ、描画からの再開、実行ごとの入力保存
- MP4全復号、解像度・fps・フレーム数・音声・元音源との比較

## 現在の範囲

実曲のMV制作には、音源・正しい歌詞・対象区間・画像が必要です。合成素材の描画確認は実曲の同期精度を保証しません。
Qwen/Demucsの接続スクリプトは同梱していますが、このPCではモデル実行・歌唱精度を未検証です。
画像は手動搬入、演出はファイル受け渡しです。画像API、Claudeへの送信、Drive公開、allin1、生成動画は初期依存に含めません。

## 手順・仕様

- [実行手順](docs/runbook.md)
- [検証結果](docs/implementation-validation.md)
- [採用ツールと利用条件](docs/dependencies.md)
- [元の設計](docs/design.md)（設計時の仮名 `ugh-mv-pipeline` は本リポジトリでは `Orpheus`）
- [同期アダプター](docs/adapters.md)

`contracts/` が唯一のJSON契約です。`examples/` は元の架空データであり、対応する音源・画像は付属しません。
`projects/` の音源、画像、動画、ログや鍵はGitへ追加しません。

## 開発検証

```sh
uv run pytest
pnpm typecheck
pnpm test
```

Remotionには独自の利用条件があります。導入版の記録と公式リンクは [dependencies.md](docs/dependencies.md) を参照してください。
