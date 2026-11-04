# 音源・歌詞から描画までの実行例

8 秒・320×180・30fps の小さな MV を、**合成パルス音、架空の英語歌詞、幾何学画像**から作る。素材は実行時に生成する。外部 API、私物素材、同期モデルの重みは使わない。

これは工程の接続を確かめる例。音源に歌唱はなく、実曲の歌詞同期精度、作品の採用、AI の演出品質は評価しない。

## 実行

リポジトリのルートで実行する。

```sh
uv sync --locked --extra analysis
pnpm install --frozen-lockfile
uv run python examples/workflow/run.py projects/workflow-example
```

出力先は新規または空のディレクトリにする。既存データは置き換えない。描画環境は通常の `mv render` と同じで、初回は Remotion が Chrome Headless Shell を取得する場合がある。

成功時は stdout に動画、制作計画、工程検査レポートのパスを返す。動画は `projects/workflow-example/runs/<run_id>/video.mp4`、工程検査は `work/workflow-check/workflow_report.json` と `.md`。`workflow: complete` は必要工程の証拠が揃ったことを表し、`accepted` は false のまま。

Node を使わず、解析から演出の受け渡しだけを調べる場合：

```sh
uv run python examples/workflow/run.py projects/workflow-prepared --prepare-only
```

このコマンドの終了コード 0 は**準備の成功**。工程検査は `incomplete` になり、描画と検証が未完了として残る。続きは既存コマンドで実行する。

```sh
uv run mv render projects/workflow-prepared
uv run mv verify projects/workflow-prepared
uv run mv workflow check projects/workflow-prepared/workflow.json --out projects/workflow-prepared/work/workflow-check
```

## 何を受け渡しているか

`run.py` は既存の処理を順番に呼び、同じ操作に対応する CLI 引数を `work/example/commands.json` に残す。

| 工程 | 入力 | 出力・確認するもの |
| --- | --- | --- |
| 制作計画 | 用途 `lyric-mv`、出力先 | `workflow.json`、必要工程と次に使うコマンド |
| 初期化 | 合成音源、歌詞の各行 | `analysis.json`。歌詞時刻は `unknown` |
| 拍解析 | 正規化した音源 | 実際の `mv analyze` による `beats_s` と `work/librosa.json` |
| 推定時刻の取り込み | 合成テスト用の alignment JSON | `interpolated / unreviewed`。この段階では確認済みにしない |
| テスト用の確認 | 生成側が決めた字幕の表示区間 | `apply-edits` による `manual / confirmed`。実際の歌声の実測ではない |
| 演出の受け渡し | `export-brief` の解析・ハッシュ・描画設定 | 拍と歌詞IDを参照する storyboard、演出理由 |
| 素材登録 | 幾何学 PNG のパス・寸法・SHA-256 | `assets.json` |
| 描画・検証 | 現在の契約と素材 | 最終動画、現在入力との一致、工程の完了／不足 |

通常の `mv demo` と異なり、未知の時刻から推定・確認・演出へ進む途中を残す。

## 時刻と演出を読む

`work/example/` の次のファイルを順に見る。

- `01-unknown.json`：入力直後。歌詞時刻は null。
- `02-unreviewed.json`：テスト用の推定値を取り込んだ状態。未確認のまま。
- `03-fixture-confirmed.json`：既知の表示区間をテスト用に適用した状態。
- `fixture-review.json`：歌唱がなく、人による試聴を行っていないことを記録。
- `direction.json`：2 行目の開始を演出上の候補にし、その近くの**抽出された拍**を選んだ理由と、フレームへ丸めた差。
- `storyboard-proposal.json`：その拍でカットし、窓へ寄る動きから光を追うパンへ切り替える構成。表示区間がカットをまたぐ歌詞IDは両方のシーンに残す。

字幕の開始・終了は歌詞時刻から作り、カットに合わせて切り詰めない。拍が抽出できなければ例は停止する。既知の 120 BPM を解析結果の代わりに書き込む処理はない。

**実作品に使う際は、テスト用の自動確認を移植しない。** 歌詞時刻を実音源と照合してから手修正を取り込み、映像の採否は別に記録する。用途別の使い方と dance への受け渡しは [workflow.md](../../docs/workflow.md) を参照。
