# Orpheus / オルフェウス

完成ミックス音源、歌詞時刻、画像、演出JSONから、ローカルで再生成できるMV制作パイプライン。
Pythonが入力検証と工程管理を行い、Remotionが静止画のパン・ズーム・クロスフェード、歌詞、音声をMP4へ描画します。

## 動かす

Python 3.11〜3.13、Node.js 22以上、uv、pnpmが必要です。リポジトリのルートで実行してください。
uvは0.13.5以上が必要です（古い場合は `uv self update` か `pip install -U "uv>=0.13.5"`）。

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

## AI に制作を任せるとき

最初に用途を選び、必要な入力・既存コマンド・次の工程への受け渡しを制作計画にします。
`workflow` は解析や描画を勝手に実行せず、現在のファイルと検証結果から工程の完了を確認します。

```sh
# 歌詞付き MV。対象ディレクトリは mv init の前でも指定できます。
uv run mv workflow plan projects/song-a --purpose lyric-mv
# 計画に示された既存コマンドで制作した後に確認します。
uv run mv workflow check projects/song-a/workflow.json --out projects/song-a/workflow-report

# 既存ダンス素材の音楽編集。歌詞を表示しない場合も拍の確認は必要です。
uv run mv workflow plan projects/dance/spec.json --purpose dance-edit --no-lyrics
uv run mv workflow check projects/dance/workflow.json --run projects/dance/dance_runs/RUN_ID --gate-config projects/dance/gate.json
```

必要な工程が未実施・未確認なら終了コードは 1。拍・歌詞同期を意図的に省く場合は
`mv workflow omit PLAN beats --reason "自由拍の区間として編集する"` のように理由を記録します。
省略は検査合格に変わらず `complete_with_omissions` として残り、失敗した検査は省略理由で隠せません。
作品の採用判断（`accepted`）は別です。

合成音源・架空歌詞から拍解析、歌詞時刻、演出、描画、完了確認まで追う実行例:

```sh
uv run python examples/workflow/run.py projects/workflow-example
```

入力素材は実行時に生成します。実曲の歌詞同期精度を検証する例ではありません。
[用途別の手順と判定](docs/workflow.md) / [制作例の入出力](examples/workflow/README.md)

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
- 用途別の制作計画、必須工程の完了確認、省略理由の記録（既存の入出力契約は変更しない）

## 演出エンジン fx（実験的）

1 枚絵 + 深度 + マスクから、カメラ・空間レイヤー・局所光で動画を作り、「現行版 / レイヤー / 局所光 / 両方」を同じ区間で比べます（決定論・API 不要）。
`uv sync --locked --extra analysis --extra fx` の後、`uv run mv fx demo projects/fxdemo && uv run mv fx render projects/fxdemo/spec.json` で合成素材の4版と2×2グリッドを出します。
設定は `contracts/` の外にあり、採点も自動合否も行いません。詳細と限界は [docs/fx.md](docs/fx.md)。

## 生成動画の検品 inspect（実験的）

生成した動画（i2v のカット・緑背景のダンス素材・3D の描画など）から、異常の候補をコマ番号と秒で見つけ、形状・動作・衣装・連続性に分けます。設定画と配色を照合し、見つけた所を直す場所（元動画のコマ・dance の spec の clip と出力の秒・生成の素性）に結び付けて、前後のコマの画像つきの修正計画（`fix_plan.json`）を書きます。
`uv sync --locked --extra analysis --extra fx` の後、`uv run mv inspect demo projects/inspectdemo && uv run mv inspect check projects/inspectdemo/inspect.json` で、異常を 7 か所に入れた合成素材を検品します。
数値と根拠を出すだけで、合否・採否は決めません（検出が無いことは合格ではない）。しきい値は合成素材で決めた仮の値で、実作品ではまだ通していません。VLM（画像を読めるモデル）は、依頼の束を書いてユーザーが明示した外部プログラムだけを呼び、素材を外へ送る設定は `--allow-send` が無いと動きません。自動の再生成はしません。詳細と限界は [docs/inspect.md](docs/inspect.md)。

## 自作の 3D モデリングツール model3d（外部ツール・実験的）

設定画や既存モデルから、部位・骨の対応（役割名）、加工レシピの実行、形状と動作の検査、書き出しをレシピと記録で進める道具。`tools/model3d/` にあり、Orpheus とは出力ファイルと制作記録だけで接続します（`mv_pipeline` とは互いに import しません）。
`uv sync --locked --extra analysis --extra fx --extra model3d` の後、`uv run model3d demo projects/model3d-demo && uv run model3d run projects/model3d-demo/recipe.json` で合成素材の一気通貫（押し出し → 結合 → 貫通の補正 → 検査 → OBJ）を通します。
設定画（RGBA 1 枚）から初期モデルを作る工程（前処理・別プロセスの生成器・検査・記録）もあり、`recipe_generate.json` で代役の生成器を使った流れを通せます（本物の生成器は別の仮想環境。`tools/model3d/backends/README.md`）。
`model3d dance-recipe` で、非公開の既存制作 worker を原料からの再構築 → 設定画の目元投影 → 質感適用 → Blender 描画 → 動画化の5工程に接続できます。実素材と既存動作の再現用で、新規生成メッシュの品質改善や汎用リギングとは別です。[接続手順と実行範囲](docs/model3d-dance.md)、全体の詳細と限界は [docs/model3d.md](docs/model3d.md)。

## 現在の範囲

実曲のMV制作には、音源・正しい歌詞・対象区間・画像が必要です。合成素材の描画確認は実曲の同期精度を保証しません。
Qwen/Demucsの接続スクリプトは同梱していますが、このPCではモデル実行・歌唱精度を未検証です。
画像は手動搬入、演出はファイル受け渡しです。画像API、Claudeへの送信、Drive公開、allin1、生成動画は初期依存に含めません。

## 手順・仕様

- [実行手順](docs/runbook.md)
- [用途別の制作計画と完了確認](docs/workflow.md)
- [音源・歌詞から描画までの実行例](examples/workflow/README.md)
- [検証結果](docs/implementation-validation.md)
- [採用ツールと利用条件](docs/dependencies.md)
- [元の設計](docs/design.md)（設計時の仮名 `ugh-mv-pipeline` は本リポジトリでは `Orpheus`）
- [同期アダプター](docs/adapters.md)
- [演出エンジン fx（実験的）](docs/fx.md)
- [ダンス合成 dance（実験的）](docs/dance.md)
- [書き出し前の検査 gate（実験的）](docs/gate.md)
- [生成動画の検品 inspect（実験的）](docs/inspect.md)
- [自作の 3D モデリングツール model3d（外部ツール・実験的）](docs/model3d.md)
- [ロードマップ](docs/roadmap.md)（実装前の計画を含む。実装済みの機能とは区別）

MV の原契約は `contracts/` が正典です。dance・fx の設定と用途別の制作計画は、その外で管理します。
`examples/` 直下は元の架空データであり、対応する音源・画像は付属しません。`examples/workflow/` は実行時に合成素材を生成します。
`projects/` の音源、画像、動画、ログや鍵はGitへ追加しません。

## 開発検証

```sh
uv sync --locked --extra analysis --extra fx --extra model3d   # fx / model3d のテストは各 extra が前提（無いと黙って skip される）
uv run pytest
pnpm typecheck
pnpm test
```

Remotionには独自の利用条件があります。導入版の記録と公式リンクは [dependencies.md](docs/dependencies.md) を参照してください。

## ライセンス

このリポジトリのコードと資料は [Apache License 2.0](LICENSE) です（2026-11-06）。

- 依存するライブラリと外部のツール（Remotion、Blender、生成モデル、推定モデルなど）は、それぞれのライセンスに従います。
- GPL のコードは入れません。
- 作品の素材・モデル・動画・鍵は、このリポジトリに含まれません。
