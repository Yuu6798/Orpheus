# workflow：制作目的に沿った工程案内と完了確認

`mv workflow` は制作目的から必要な工程を並べ、現在の入力・出力・検証結果を照合する。動画を書き出しただけで、拍や歌詞の工程まで完了したことにしないための入口。

計画は `contracts/` の既存データと別の JSON に保存する。解析・同期・演出・描画は既存コマンドで進める。`workflow check` の結果は工程の充足状況であり、作品の採用や実曲での同期精度の保証ではない。

## 1. 目的を選び、必要な入力と順番を読む

```sh
uv run mv workflow plan projects/song-a --purpose lyric-mv
uv run mv workflow plan projects/dance-a/spec.json --purpose dance-edit --lyrics
```

標準 MV はプロジェクトのディレクトリ、dance は spec のパスを渡す。入力を作る前にも計画できる。計画に並ぶ各工程には、必要な入力、期待する出力、次に使うコマンドがある。計画先を変える場合は `--out PATH` を付ける。

| 目的 | 必要な材料 | 演出担当が決めること |
| --- | --- | --- |
| `lyric-mv` | 音源、歌詞、拍候補、確認した歌詞時刻、storyboard、画像 | 歌詞の意味と時刻を見てカットと動きを選び、歌詞IDをシーンへ対応させる |
| `dance-edit --lyrics` | 音源、人物・背景素材、拍、歌詞時刻、dance spec、描画 run、gate の入力 | フレーズごとのクリップ、カメラ、字幕位置・表示時刻を同じ時間軸で割り当てる |
| `dance-edit --no-lyrics` | 音源、人物・背景素材、拍、dance spec、描画 run、gate の入力 | 歌詞なしの編集。題字・クレジットは歌唱字幕と区別して指定する |

歌詞の有無が分かっている dance では `--lyrics` / `--no-lyrics` を明示する。拍解析は歌詞なしでも実行できる。歌詞文字列だけから実際の歌唱時刻が確定するわけではない。

## 2. 標準 MV を進める

```sh
uv run mv init projects/song-a --audio song.wav --lyrics lyrics.txt --start 60 --duration 30
uv run mv analyze projects/song-a
uv run mv import-alignment projects/song-a alignment.json
uv run mv apply-edits projects/song-a reviewed-timings.json
uv run mv export-brief projects/song-a
uv run mv import-storyboard projects/song-a storyboard.json
uv run mv import-assets projects/song-a assets.json
uv run mv render projects/song-a
uv run mv verify projects/song-a
uv run mv workflow check projects/song-a/workflow.json --out projects/song-a/work/workflow-check
```

`alignment.json` は同期モデルなどの結果、`reviewed-timings.json` は音源と照合した修正。既存の確認済み時刻を搬入する場合は `apply-edits` から進められる。外部モデルの実行は [adapters.md](adapters.md)、各 JSON の形式は [runbook.md](runbook.md) を参照。

`export-brief` の解析と世界観を演出担当AIへ渡す。拍候補、歌詞の区切り、意味をもとに storyboard を作り、字幕の時刻は analysis から描く。解析を更新したら新しい brief を出して構成を再確認する。標準 MV の完了確認は工程の接続を検査するもので、全カットの拍同期を自動採点するものではない。

実行可能な全工程の例は [examples/workflow](../examples/workflow/README.md)。合成音源・架空歌詞から始め、推定／確認済みの途中状態も保存する。

## 3. 解析から dance へ渡す

例えば元曲 60〜90 秒を編集する場合、解析と描画の双方を切り出した音源の 0 秒へ揃える。

```sh
uv run mv init projects/dance-a/analysis --audio song.wav --lyrics lyrics.txt --start 60 --duration 30
uv run mv analyze projects/dance-a/analysis
uv run mv import-alignment projects/dance-a/analysis alignment.json
uv run mv apply-edits projects/dance-a/analysis reviewed-timings.json
```

`projects/dance-a/spec.json` に、次のように解析結果と同じ音源を接続する（spec の一部）。

```json
{
  "duration_s": 30.0,
  "audio": {"path": "analysis/work/audio/clip.wav", "offset_s": 0.0},
  "beats": {"path": "analysis/analysis.json"}
}
```

この `beats.path` は解析の `beats_s` を読む。音源は既に切り出しているので、元曲の開始秒 60 を再び足さない。ボーカルを分離した場合も、その遅延を補正して同じ原点へ戻す。

演出担当は確認した `analysis.lyrics[].start_s/end_s` を、dance の `lyrics.lines[].start/end` へ渡す。字幕の画面位置は `lyrics.layers.band.box` のように層の名前ごとに指定する。どのフレーズにどのクリップを置くか決め、必要なカット・カメラアクセントに `{ "beat": 8 }` のような拍参照を使う（0 始まり）。歌詞時刻を機械的に最寄りの拍へ動かさない。詳細は [dance.md](dance.md)。

```sh
uv run mv dance plan projects/dance-a/spec.json
uv run mv dance stills projects/dance-a/spec.json --times 1 10 20 --guides
uv run mv dance render projects/dance-a/spec.json
```

描画結果の run を `gate.json` に指定する。以下のパスは `projects/dance-a/gate.json` 基準で、`RUN_ID` は描画結果の ID に置き換える。

```json
{
  "timeline": "dance_runs/RUN_ID/timeline.json",
  "video": "dance_runs/RUN_ID/dance.mp4",
  "beats": "analysis/analysis.json",
  "measured_lines": "timing/lines_measured.json",
  "confirmed": "timing/confirmed.json",
  "lyric_layers": ["band"],
  "faces": "auto",
  "spec": "spec.json"
}
```

`measured_lines` は `mv gate lines` の出力、`confirmed` は試聴で確かめた時刻。時刻の元になる推定値とボーカルが必要で、形式は [gate.md](gate.md) を参照。`estimate` / `not_found` を実測へ書き換えて検査を通さない。`faces: auto` は顔領域の推定として扱う。

```sh
uv run mv gate check projects/dance-a/gate.json
uv run mv workflow check projects/dance-a/workflow.json --run projects/dance-a/dance_runs/RUN_ID --gate-config projects/dance-a/gate.json --out projects/dance-a/work/workflow-check
```

歌詞なしの計画では歌詞検査用の入力を外せる。必要な検査が `skip` や未解決のままなら、その事実が工程検査にも残る。

## 4. 完了・不足・意図的な省略を分ける

```sh
uv run mv workflow check projects/song-a/workflow.json --out projects/song-a/work/workflow-check
```

標準 MV は現在の run を使い、dance は `--run` で対象の run を選ぶ。過去の成功記録があっても、現在の入力と対応しなければ完了にしない。`--out DIR` を指定すると `workflow_report.json` と `workflow_report.md` を保存する。

| 結果 | 意味 | 終了コード |
| --- | --- | --- |
| `complete` | 必要工程の証拠が揃った | 0 |
| `complete_with_omissions` | 意図的な省略を理由付きで残し、その他の必要工程が揃った | 0 |
| `incomplete` | 必要工程が未実施、未確認、または材料不足 | 1 |
| `failed` | 入力の不整合や検査失敗がある | 1 |

例えば拍に合わせない演出を意図して選んだ場合は、工程 ID と理由を明示する。

```sh
uv run mv workflow omit projects/song-a/workflow.json beats --reason "この作品は自由拍で編集する"
uv run mv workflow check projects/song-a/workflow.json
uv run mv workflow restore projects/song-a/workflow.json beats
```

省略できるのは、未完了の `beats` と `lyrics` の工程。省略理由は計画と結果に残り、検査の成功へ変換されない。既に観測した失敗も省略理由だけでは合格にならない。`restore` はその工程を再び完了条件に戻す。`accepted` は常に別の判断で、工程検査から自動で true にしない。

### 自前の描画で作った制作は `lyric-mv` の工程に載らない

リポジトリの外で自前の描画スクリプトを使って作った制作は、いまの `lyric-mv` の工程に載らない。2026-11-12、完成していた制作（自前の描画・外部のツールの歌詞の連番・手書きの ffmpeg）に、制作のあとで `mv workflow plan --purpose lyric-mv` と `mv workflow check` を掛けると、全体は `incomplete`・終了コード 1、必要な 7 工程（`inputs`・`beats`・`lyrics`・`direction`・`assets`・`render`・`verification`）はすべて `pending` だった。検査はプロジェクトのフォルダの中だけを見るので、外で描いた動画・外で推定した歌詞の時刻・外の拍の格子を登録する口が無い。`workflow omit` は未完了の `beats` と `lyrics` を「意図して使わない」と記録するもので、「外で済ませた」とは違う。

- いまの扱い: `incomplete` の結果をそのまま報告し、外で行ったこと・確かめていないことは言葉で添える。`workflow omit` で埋めない（上の規則は「省略できるのは、未完了の `beats` と `lyrics` の工程」で、意図して使わない工程のためのもの）。
- これは既知の足りない点で、機能を足すと決めたものではない（判断はユーザー。`.claude/memory/STATUS.md` の WORKFLOW-EXT-1）。
- 事例は [external-renderer › 事例（2026-11-11）](external-renderer.md#事例2026-11-11)。

## 5. 検品（mv inspect）の記録をつなぐ

`mv inspect`（`docs/inspect.md`）は独立した検品のコマンドで、既定の工程には入らない（前からの計画と結果はそのまま）。検品をしたかを制作の記録に残すときは、計画で検品の行を足すか、確認のときに検品の報告を渡す。

```sh
uv run mv workflow plan projects/dance-a/spec.json --purpose dance-edit --inspection required   # 検品を必須にする（optional なら記録だけ）
uv run mv inspect check projects/dance-a/inspect.json                                            # → inspect_report/inspect_report.json
uv run mv workflow check projects/dance-a/workflow.json --run … --gate-config … --inspect-report projects/dance-a/inspect_report/inspect_report.json
```

- **検品の行**（`inspection`）は、計画の `inspection` が `optional` か `required` のとき、または `--inspect-report` を渡したとき（`optional` として）だけ出る。必須にするかは制作ごとに決める（`required` なら、合う検品の記録が無いと `incomplete`）。
- **照合**: 渡した報告（`inspect-report/0.1`。何度でも渡せる）が、今の何を見たものかを sha256 で確かめる。
  - 最終動画（標準 MV の `runs/<run_id>/video.mp4`、dance の run の `dance.mp4`）を見た報告 → `final_video`
  - dance で、今の spec の素材を見た報告（報告の `run.inputs` の spec の sha256 が今の spec と同じで、見た動画がどれも今の素材の sha256 に入る）→ `spec_sources`。spec の JSON は素材をパスで指すだけなので、同じパスで差し替えた素材は spec の sha256 では見分けられず、見た動画の sha256 も照らす
  - 見た動画がどれも今の素材の sha256 に入る報告 → `assets`
  - どれにも合わない報告は、古いか別の動画の検品として `stale`（`pending`）。
- **状態**（`inspection_state`）: `not_run`（未実施）・`review`（検品済み・要確認の出来事あり）・`clean_with_skips`（見つけた所は無いが、見ていない検査あり）・`clean`・`stale`。要約に、検品の run_id と時刻、要確認の数（演出の時刻と重ならない数）、見ていない検査の数、VLM に見せなかった出来事の数を書く（`inspections` に報告ごとの値）。
- **採否と混ぜない**: 検品済みは「検品をした記録がある」だけで、異常かどうか・作品の採否は人が決める（`accepted` は常に false のまま）。形の違う報告を渡したときは `fail`。
