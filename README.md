# Orpheus / オルフェウス

[![CI](https://github.com/Yuu6798/orpheus/actions/workflows/ci.yml/badge.svg)](https://github.com/Yuu6798/orpheus/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

音源・歌詞・画像・設定ファイルから、**ローカルで再生成できる**ミュージックビデオ（MV）を組み立てる道具箱です。
Python が入力の検証と工程の記録を担い、描画は Remotion（2D の MV）、OpenCV（演出・ダンス合成）、Blender（3D。外部プロセス）に任せます。

> Orpheus is a local, reproducible toolkit for building music videos from audio, lyric timings, images and JSON specs.
> Python validates inputs and journals every step; rendering is delegated to Remotion, OpenCV and (optionally) Blender.
> Documentation is in Japanese.

## 特徴

- **再現できる**: 入力は SHA-256 で記録し、同じ入力なら再描画しない。出力ごとに入力の写しと検証結果を残す。
- **ネットワークを呼ばない**: 生成 AI の API、クラウドへの送信、公開は行わない。外部プログラムはユーザーが明示した設定だけを実行する。
- **採点しない**: 自動で出すのは構造の検査（`technical_pass`）だけ。作品としての採否（`accepted`）は人が記録する。
- **契約を守る**: MV の入出力の形は `contracts/` の JSON Schema が正典。実験的な機能の設定はその外に置く。

## 何ができるか

| 入口 | できること | 状態 | 詳細 |
|---|---|---|---|
| `mv init … render verify` | 音源・歌詞時刻・静止画・演出 JSON から、パン・ズーム・クロスフェードと歌詞テロップつきの MP4 を描く | 本体 | [runbook](docs/runbook.md) |
| `mv workflow` | 用途別（歌詞つき MV / ダンス編集）に必要な工程を並べ、現在のファイルから完了を確認する | 本体 | [workflow](docs/workflow.md) |
| `mv run-adapter` | 手元に入れてある歌詞同期器・音源分離器を argv 設定で呼び、結果を取り込む | 本体 | [adapters](docs/adapters.md) |
| `mv export remotion-props` | analysis / storyboard から、Remotion 側が秒を扱わずに済むコマ番号だけの props.json を書く | 実験的 | [remotion-props](docs/remotion-props.md) |
| `mv layer render` | Remotion の透過レイヤーの描画を工程として包み、版・sha256・ブラウザー・描画時間・決定論の確認を台帳に残す | 実験的 | [remotion-props › 台帳](docs/remotion-props.md#描画を台帳に載せるmv-layer-render) |
| `mv export subtitles` | 歌詞の行の時刻から、字幕ファイル（SRT・WebVTT・LRC）と動画の概要欄に貼る歌詞を書く | 実験的 | [subtitles](docs/subtitles.md) |
| `mv audio grid` | 曲の音の立ち上がりに一定のテンポの格子を当てはめ、拍と小節の頭の時刻を推定して、音源か analysis.json の隣に書く（dance・fx-timeline の `beats` がそのまま読める。値は推定） | 実験的 | [audio](docs/audio.md) |
| `mv fx` | 1 枚絵から、カメラ・空間レイヤー・局所光・調色で動画を作り、4 版を並べて比べる（深度とマスクは任意。無ければ、それを使う機能だけが使えない） | 実験的 | [fx](docs/fx.md) |
| `mv fx timeline` | いくつもの fx のショットを、カット・クロスフェード・黒・フラッシュ・焼け抜けでつないで 1 本にする。`--hooks` で、炎・刃の火・火の粉・灯の点灯・稲光・雨・暖色の光・形に集まる光の粒などの演出の部品（`fx.fire`・`fx.sky`・`fx.accent`・`fx.particles`）を掛ける | 実験的 | [fx › timeline](docs/fx.md#timeline複数のショットをつなぐfx-timeline02)、[演出の hooks](docs/fx.md#演出の-hooks火空と天気明かり) |
| `mv fx guides` | 素材の絵に座標の目盛りを重ねて、光や炎を置く位置を読む | 実験的 | [fx › 見比べの補助](docs/fx.md#見比べの補助目盛り基準の列比較動画) |
| `mv fx legacy` | 手元の描画スクリプトを、リポジトリに入れずに呼んで run として記録する。`--cuts` で区間を同時に描き、`mv deliver join` でつなぐ | 実験的 | [fx › legacy 接続](docs/fx.md#legacy-接続) |
| `mv overlay` | 透過の連番（PNG）やアルファつき動画を土台に重ねて、fx と同じ式と印で H.264 にする | 実験的 | [fx › overlay](docs/fx.md#overlay透過レイヤーの重ね合わせfx-lyric-1) |
| `mv dance` | 緑背景の人物動画をステージに合成し、カメラと縦の歌詞を載せる | 実験的 | [dance](docs/dance.md) |
| `mv gate` | 書き出し前の機械的な検査（歌詞時刻・UI 枠・拍・書き出し）、歌詞の行・秒の確認表、透過連番から起こす timeline、完成動画の明滅の回数（`mv gate flashes`。規格への適合は判定しない）、分離ボーカルの声の区間・音符・旋律の繰り返しのずれ（`mv gate voice`・`notes`・`repeat`。数字だけ） | 実験的 | [gate](docs/gate.md) |
| `mv inspect` | 生成動画の異常候補をコマと秒で見つけ、分類し、設定画と照合して修正計画を書く | 実験的 | [inspect](docs/inspect.md) |
| `mv sheet` | 動画や透過連番の指定時刻・出来事のコマを 1 枚に並べて見る（合否なしの閲覧用） | 実験的 | [sheet](docs/sheet.md) |
| `mv deliver` | 描いた区間の動画を再エンコードせずにつなぎ、音を付けて元データの MP4 にする。確認用の小さい版（2 パス）と、受け渡しの片（結合の bat と SHA256SUMS つき）も作る | 実験的 | [deliver](docs/deliver.md) |
| `model3d` | 設定画や既存モデルから、部位と骨の対応・加工レシピ・検査・書き出し・動きの移し替え・舞台描画を記録つきで進める | 実験的・外部ツール | [model3d](docs/model3d.md) |

fx や dance に無い描き方を自前のスクリプトで書くときは、色・記録・つなぎ・検査だけを借りられる（[自前の描画スクリプトから乗る](docs/external-renderer.md)）。

「本体」は `contracts/` の契約に沿って動く範囲（MV の描画と検証は CI が合成素材で確かめる。アダプターの接続は手元の同期器・分離器で確かめる）、「実験的」は設定スキーマが契約の外にあり、しきい値や既定値が合成素材で決めた仮の値の範囲です。
実験的な機能の実素材での確認状況は各ドキュメントの冒頭に書いてあります。

## 必要なもの

| ツール | 版 |
|---|---|
| Python | 3.11〜3.13 |
| [uv](https://docs.astral.sh/uv/) | 0.13.5 以上（`uv self update` か `pip install -U "uv>=0.13.5"`） |
| Node.js | 22 以上 |
| pnpm | 11（`package.json` の `packageManager` に固定） |
| FFmpeg | PATH にあればそれを使う。無ければ同梱の imageio-ffmpeg 版 |
| Blender | `model3d` の描画工程だけ。別プロセスで呼ぶ |

初回の `mv render` では Remotion が Chrome Headless Shell を取得します。既存のブラウザーを使うときは環境変数 `ORPHEUS_BROWSER` に実行ファイルのパスを入れてください。
ffprobe は PATH のもの、無ければ Remotion 同梱版を使います（`ORPHEUS_FFPROBE` で指定可）。

## 5 分で試す

リポジトリのルートで実行します。合成音・幾何学画像・架空の歌詞から 6 秒のテスト動画を作ります。

```sh
uv sync --locked --extra analysis
pnpm install --frozen-lockfile
uv run mv demo projects/demo
uv run mv render projects/demo
uv run mv verify projects/demo
```

動画は `projects/demo/runs/<run_id>/video.mp4`、検証結果は同じフォルダーの `verification.json` に出ます。
実験的な機能も同じ形で合成素材のデモを持っています（`--extra fx` が要ります）。

```sh
uv sync --locked --extra analysis --extra fx --extra model3d
uv run mv fx demo projects/fxdemo && uv run mv fx render projects/fxdemo/spec.json
uv run mv dance demo projects/dancedemo && uv run mv dance render projects/dancedemo/spec.json
uv run mv inspect demo projects/inspectdemo && uv run mv inspect check projects/inspectdemo/inspect.json
uv run model3d demo projects/model3d-demo && uv run model3d run projects/model3d-demo/recipe.json
```

`uv sync` は指定しなかった extra を外すので、使う extra は毎回まとめて書きます。

## 実曲で MV を作る流れ

```sh
# 1. 用途を選ぶと、必要な入力・使うコマンド・次の工程への受け渡しが workflow.json に並ぶ
uv run mv workflow plan projects/song-a --purpose lyric-mv

# 2. 計画に沿って既存コマンドで進める（抜粋。全体は docs/runbook.md）
uv run mv init projects/song-a --audio song.wav --lyrics lyrics.txt --start 60 --duration 45
uv run mv analyze projects/song-a                       # 拍・BPM 候補（librosa）
uv run mv apply-edits projects/song-a timings.json      # 歌詞の行時刻（手修正）。モデルの同期結果は import-alignment
uv run mv export-brief projects/song-a                  # 演出担当（人や AI）へ渡す資料
uv run mv import-storyboard projects/song-a storyboard.json
uv run mv import-assets projects/song-a assets.json
uv run mv preview projects/song-a --studio              # Remotion Studio で確認
uv run mv render projects/song-a --profile final
uv run mv verify projects/song-a

# 3. 必要な工程が済んでいるかを現在のファイルから確認する（未実施・未確認なら終了コード 1）
uv run mv workflow check projects/song-a/workflow.json --out projects/song-a/workflow-report
```

- 時刻の 0 秒は切り出した音源の先頭です。元曲の開始秒と元ファイルのハッシュは別に残ります。
- 歌詞時刻やサビの区間は推測して確定扱いにしません。モデルの推定は `unreviewed` のまま取り込み、確認した行だけが本番描画に使えます。
- 拍や歌詞同期を意図して省くときは `mv workflow omit PLAN beats --reason "…"` で理由を記録します。省略は検査の合格には変わりません。
- 合成素材で全工程を通す実行例: `uv run python examples/workflow/run.py projects/workflow-example`（[examples/workflow](examples/workflow/README.md)）。

## リポジトリの構成

```
contracts/            MV の入出力契約（analysis / storyboard / assets の JSON Schema）。変更には承認が要る
src/mv_pipeline/      CLI `mv` と工程管理。fx / dance / gate / inspection はサブパッケージ
renderer/             Remotion のコンポジションと描画スクリプト（TypeScript）
tools/model3d/        自作 3D ツール `model3d`（別パッケージ。mv_pipeline と互いに import しない）
scripts/              既存の同期器・分離器・VLM を呼ぶアダプターの例（ネットワークに出るものは明記）
prompts/              演出担当へ渡す指示文（storyboard / 画像の様式）
examples/             契約の例。examples/workflow/ は合成素材で全工程を通す実行例
docs/                 手順・仕様・検証記録・ロードマップ
tests/                pytest。fx / model3d のテストは各 extra が無いと黙って skip される
projects/             作業用（Git には入れない。音源・画像・動画・鍵を置く場所）
```

## 範囲と限界

- 実曲の MV には、音源・正しい歌詞・対象区間・画像が要ります。合成素材の描画が通ることは、実曲の同期精度を保証しません。
- 画像は手動で搬入し、演出はファイルで受け渡します。画像生成 API、LLM への送信、Drive への公開は含みません。
- 歌詞同期器（Qwen）と音源分離器（Demucs）の接続スクリプトは同梱していますが、モデルの実行環境は別に用意します。
- 実験的な機能のしきい値・既定値は合成素材で決めた仮の値で、実素材での確認範囲は各ドキュメントに書いてあります。
- 作品の素材・モデル・動画・鍵・費用はこのリポジトリに含めません。

## 開発

```sh
uv sync --locked --extra analysis --extra fx --extra model3d && pnpm install --frozen-lockfile
uv run pytest                  # 数分。fx / model3d のテストは各 extra がある環境でだけ走る
pnpm typecheck && pnpm test    # tsc --noEmit / node --test
```

- 開発の決まり（契約の不変、実測と推定の区別、依頼のない送信・公開の禁止、検証・依存のルール）は [AGENTS.md](AGENTS.md) が正です。Claude Code 向けの運用は [CLAUDE.md](CLAUDE.md)。
- 依存は `uv.lock` / `pnpm-lock.yaml` で固定し、直接依存は exact pin。依存を変えたら lock も同じ PR で更新します。
- CI（[.github/workflows/ci.yml](.github/workflows/ci.yml)）は Python 3.11 / 3.13 のテスト、Node の型検査とテスト、合成素材での描画（MV / fx / dance / inspect / model3d）、`pip-audit` を回します。成果物はアップロードしません。
- main へ直接 push せず、トピックブランチから PR にします。コミットは Conventional Commits（日本語可）。

## ドキュメント

| 文書 | 内容 |
|---|---|
| [runbook.md](docs/runbook.md) | MV 制作の実行手順（入力 → 歌詞時刻 → 演出 → 描画 → 検証） |
| [workflow.md](docs/workflow.md) | 用途別の制作計画と完了確認 |
| [adapters.md](docs/adapters.md) | 歌詞同期・音源分離アダプターの共通形式と接続例 |
| [remotion-props.md](docs/remotion-props.md) | Remotion を透過レイヤーの部品として使うための、コマ番号だけの props の書き出し |
| [sheet.md](docs/sheet.md) | 時刻や出来事のコマを 1 枚に並べる閲覧用のシート |
| [subtitles.md](docs/subtitles.md) | 歌詞の行の時刻からの字幕ファイル（SRT・WebVTT・LRC）と、概要欄に貼る歌詞 |
| [fx.md](docs/fx.md) / [dance.md](docs/dance.md) / [gate.md](docs/gate.md) / [inspect.md](docs/inspect.md) | 実験的な機能の使い方と限界 |
| [deliver.md](docs/deliver.md) | 区間の動画のつなぎ、音を付けた元データ、確認用の小さい版、受け渡しの片 |
| [model3d.md](docs/model3d.md) / [model3d-dance.md](docs/model3d-dance.md) / [model3d-review.md](docs/model3d-review.md) | 3D ツールの使い方、既存制作レシピの接続、実行確認の記録 |
| [dependencies.md](docs/dependencies.md) | 採用ツールの版と利用条件（Remotion の利用条件を含む） |
| [implementation-validation.md](docs/implementation-validation.md) / [validation.md](docs/validation.md) | 実装と納品物の検証記録 |
| [design.md](docs/design.md) | 元の設計（設計時の仮名 `ugh-mv-pipeline` は本リポジトリの `Orpheus`） |
| [roadmap.md](docs/roadmap.md) | ロードマップ（実装前の計画を含む。実装済みの機能とは区別） |

## ライセンス

コードと資料は [Apache License 2.0](LICENSE) です。
依存するライブラリと外部ツール（Remotion、Blender、生成モデル、推定モデルなど）はそれぞれのライセンスに従います。GPL のコードは入れません。
