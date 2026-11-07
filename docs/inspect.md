# inspect（生成動画の検品・実験的）

生成した動画（i2v のカット・緑背景のダンス素材・3D の描画など）から、異常の候補を**コマ番号と秒で**見つけて形状・動作・衣装・連続性に分ける道具。設定画と配色を照合し、見つけた所を**直す場所**（元動画のコマ・dance の spec の clip と出力の秒・生成の素性）に結び付ける。

- **採点器ではない**: 数値と根拠を出すだけで、合否・採否・好みは決めない。**検出が無いことは合格ではない**（採用は人。`mv dance accept` などの人の判定）。
- **決まりとの関係**: AGENTS.md の「生成物の開発中は測定器・検証器の機能を足さない」の例外として、2026-11-07 にユーザーが依頼した（BytePlus の生成動画の品質評価から取り入れる機能の一覧）。例外はこの `mv inspect` の範囲だけ。
- **推定を実測扱いしない**: 数値は動画から測った値（`evidence: measured`）。それを異常と読むかは人が見て決めるので、出来事はどれも `status: review`（要確認）。VLM の指摘は推定（`estimated`）として分けて残す。
- **しきい値はどれも仮の値**: 合成素材（`mv inspect demo`）で決めた。実作品の動画ではまだ通していない。最初の 1 作で `metrics/<クリップ>.csv` を見て直す。
- **送信しない**: Orpheus はネットワークを呼ばない。VLM はユーザーが CLI で明示した外部プログラムだけを呼び、素材を外へ送る設定は `--allow-send` が無いと動かさない。
- **自動の再生成はしない**: 修正計画（`fix_plan.json`）に、生成し直す区間・前後のコマの画像・生成の素性・手当ての候補を書くだけ。

## 取り入れた機能

| 機能（ユーザーの表） | Orpheus での形 | 優先度 | 状態 |
|---|---|---|---|
| 異常箇所の特定 | 出来事ごとに元動画のコマ番号 `[開始, 終了)`・秒・山のコマ・画面の中の場所（正規化の箱）と、前・山・後のコマを並べた静止画 | 高 | 実装 |
| 異常の分類 | 形状・動作・衣装・連続性。規則で主な分類を 1 つ決め、同じ所のほかの分類も残す | 高 | 実装 |
| 参照画像との照合 | 設定画の主な色と、コマの人物の色の重なり・欠けた色・設定画に無い色 | 高 | 実装（配色だけ。形・顔の照合は VLM に任せる） |
| 時系列の一貫性検査 | 前のコマとの差の急変と戻り、明滅、止まったコマ、水増しの周期、人物の消失 | 中 | 実装 |
| ニューラル品質評価 | VLM への依頼の束と外部プログラムの呼び出し。返答は推定として出来事に添える | 中 | 実装（代役で確かめた。Claude のアダプタは API を呼んでいない＝未検証） |
| 自動再生成 | 修正計画: 直す区間・前後のコマ（始点・終点の画）・素性・手当ての候補 | 将来 | 計画を書くだけ（実行しない） |

**検査の結果を直す場所に結び付ける**のが中心の設計。出来事には必ず `target`（直す場所）と `actions`（手当ての候補）が付き、修正計画は出来事をクリップごとの「生成し直す区間」にまとめる。

## 5 分で試す（合成素材）

```sh
uv sync --locked --extra analysis --extra fx                  # cv2 が要る（fx extra）
uv run mv inspect demo projects/inspectdemo                   # sheet.png（設定画）・clip.mp4（異常を 7 か所に入れた緑背景の人物）・inspect.json・expected.json
uv run mv inspect check projects/inspectdemo/inspect.json     # → projects/inspectdemo/inspect_report/
```

デモの人物は頭・髪・深紅の上着・スカート・脚の図形で、左右に揺れて腕を振る。入れた異常（24 fps）は、小刻みな揺れ（コマ 8〜19・動作）、1 コマの明るさの明滅（24・連続性）、1 コマだけ別の画（30・連続性）、同じ絵が 4 コマ（40〜43・連続性）、腕から離れた塊（50〜52・形状）、上着が青になる（60〜74・衣装）、横へ飛んで戻る（84〜85・動作）。どれも入れたコマと分類で見つかり、ほかの所では何も出ない（`tests/test_inspect.py`）。異常の無い素材・dance のデモの棒人間・fx のデモでも何も出ないことを確かめた。**合成素材で働くことは、実作品で見落としや誤検出が少ないことを保証しない。**

## 設定（`inspect/0.1`）

パスは設定ファイルのあるフォルダ基準（絶対パスも可）。`clips` か `dance_spec` のどちらか 1 つを書く。未知の鍵は拒否し、`x_` で始まる鍵はどの階層でも許す（スキーマは `src/mv_pipeline/inspection/inspect.schema.json`。`contracts/` の外）。

```json
{
  "schema_version": "inspect/0.1",
  "clips": [
    {"id": "cut07", "path": "clips/cut07.mp4", "frames": [0, 120],
     "origin": {"tool": "…", "model": "…", "job": "jobs/cut07.json", "seed": 42, "start_image": "stills/cut07.png", "prompt_file": "prompts/cut07.txt"}}
  ],
  "reference": [{"path": "sheets/front.png", "view": "front"}, {"path": "sheets/back.png", "view": "back"}],
  "foreground": {"key": {"color": [0, 255, 0], "tolerance": 0.22, "softness": 0.12}},
  "checks": ["continuity", "motion", "shape", "costume"],
  "thresholds": {"jump_min": 0.08},
  "vlm": {"every_s": 1.0, "max_frames": 24, "max_side": 768, "language": "ja"}
}
```

- `clips[]`: `path`（動画か静止画）、`id`（既定はファイル名。英数と `_.-`）、`frames`（見るコマ `[開始, 終了)`。既定は全部）、`origin`（生成の素性。検査には使わず、直す場所と修正計画にそのまま写す。将来の再生成に要る値を書いておく）、`foreground`（このクリップだけの切り抜き）。
- `dance_spec`: dance の spec のパス。`performer.clips` の素材を、spec の区間で実際に出る元のコマの範囲だけ見る（同じ素材を何度使っても 1 回だけ見る）。切り抜きは spec の `performer.key`。素性は `"origins": {"spec に書いた素材のパス": {…}}`。
- `reference[]`: 設定画。`background` は人物の切り抜き方で、`auto`（既定。透明があれば `alpha`、無ければ `border`）・`alpha`・`border`（画像の縁の色を背景とみなす。**推定**で、報告にそう書く）・`none`。`key`（緑背景の設定画）、`box`（正規化の切り出し。三面図の 1 体だけを使うときなど）。複数の設定画はコマごとにいちばん重なるものと比べる（正面と背面を渡すと、振り向いたコマで背面と比べる）。
- `foreground`: 人物の切り抜き。`{"key": {...}}`（緑抜き。dance と同じ式）・`{"box": [x0, y0, x1, y1]}`（固定の箱）・`"none"`。**無いと形状・衣装・設定画との照合・位置の飛び・揺れは見ない**（未実施として残る）。
- `checks`: 見る分類（書かなければ 4 つ全部。`[]` なら数値の検査をしない＝VLM だけの検品）。外した分類は未実施として残る。設定画との照合は `costume` に入る。
- `analysis_width`: 解析の幅（既定 320。元より大きくしない）。

## 測るものと分類

数値は解析の大きさに縮めたコマから測る。「急変」は前のコマとの差の列で、前後 `window_s`（0.5 秒）の中央値から MAD×1.4826 の `spike_z`（6）倍以上、中央値の `spike_ratio`（3）倍以上、かつ下の最小値以上の所。変わった絵が `pair_max_s`（2 秒）以内に元へ戻れば（入りの前のコマと戻りのコマの差が入りの変化の `return_ratio`（0.5）倍以下）その間 `[入り, 戻り)` が候補（pair）、戻らなければ変わり目の 1 コマ（step。以降は変わったまま）。

| 分類 | 信号 | 測るもの | 最小値（仮） | 要るもの |
|---|---|---|---|---|
| 連続性 | `glitch` 数コマだけの乱れ／`jump` 画の切り替わり | 人物の外（無ければ画面全体）の構造の差（各コマの平均を引いた明るさの差。明るさだけの変化は除く） | `jump_min` 0.06 | — |
| 連続性 | `flicker` 明るさの明滅 | 画面の明るさの平均の差（構造の急変と同じコマは除く） | `flicker_min` 0.04 | — |
| 連続性 | `freeze` 止まったコマ | 前のコマとの差が `dup_max`（0.0005）以下のコマが、動いている所（周りの差が `dup_context`（4）倍以上）で `freeze_frames`（3）コマ以上続く | — | — |
| 連続性 | `duplicate_cadence` 重複コマの周期 | 1 つだけの重複が一定の間隔で 3 回以上（24 コマの絵を 30fps に水増しした素材など）。クリップ全体で 1 つ | — | — |
| 連続性 | `vanish` 人物が消えたコマ | 前後に人物がいて、`vanish_max_s`（0.5 秒）以下だけ人物がいない（端の出入りと長い不在は見ない） | — | 前景 |
| 動作 | `motion_spike` 動きの急変 | オプティカルフロー（Farneback、解析の半分の大きさ）の大きさの p95（体の高さ／コマ。前景が無ければ画面の高さ）。逆向きに戻れば pair | `motion_min` 0.05 | — |
| 動作 | `teleport` 位置の飛び | 人物の重心の移動（体の高さ／コマ） | `teleport_min` 0.06 | 前景 |
| 動作 | `jitter` 小刻みな揺れ | 重心の 2 階差の中央値（`jitter_min` 0.006 以上）、1 階差に対する比（`jitter_ratio` 1.3 以上）、前のコマと逆向きに動いたコマの割合（`jitter_reversals` 0.5 以上） | — | 前景 |
| 形状 | `silhouette_pop` 輪郭の急変 | 人物の囲みの大きさと位置にそろえた輪郭（32×32）の 1 − IoU | `shape_min` 0.12 | 前景 |
| 形状 | `area_pop` 大きさの急変 | 人物の面積の比 \|ln\| | `area_min` 0.18 | 前景 |
| 形状 | `fragment` 部分の分離・増減 | 人物の塊（画面の 0.15% 以上で、いちばん大きい塊の 2% 以上）の数が、いつもの数と `fragment_max_s`（0.5 秒）以下だけ違う | — | 前景 |
| 衣装 | `costume_pop` 配色の急変 | 人物の内側の画素の配色（彩度の低い画素は明るさ 8 段、ほかは色相 16 × 彩度 3 × 明るさ 3）の、前のコマとの Bhattacharyya 距離 | `palette_min` 0.15 | 前景 |
| 衣装 | `costume_drift` 冒頭からの配色のずれ | 冒頭 `anchor_s`（0.5 秒）の配色からの距離が `drift_min`（0.35）以上の続き（`min_segment_s` 0.25 秒以上）。いちばん違う帯（頭・上半身・下半身）も書く | — | 前景 |
| 衣装 | `reference_missing` 設定画の色の欠け | 設定画の主な色（割合 `ref_min_weight` 5% 以上）が、コマの人物で設定画の割合の `ref_missing_ratio`（0.25）倍より少ない続き。色の差 ΔE `ref_match_de`（18。明るさの差は半分に数える）以内を同じ色と数える | — | 前景・設定画 |
| 衣装 | `reference_extra` 設定画に無い色 | どの設定画の色からも ΔE `ref_extra_de`（30）より遠い画素が `ref_extra_min`（12%）以上の続き。その色も書く | — | 前景・設定画 |
| （その他） | `vlm` VLM の指摘 | VLM の返答（推定）。分類は VLM が選ぶ（その他を含む） | — | `--vlm` か `--vlm-response` |

**まとめ方と分類の規則**（決め方の約束で、推定ではない）:

- 同じ分類で区間が半分以上重なる（または短い方がほぼ長い方の中にある）信号と、どちらも 3 コマ以下で 1 コマ以内に接する信号を、1 つの出来事にまとめる。水増しの周期はほかとまとめない（中の乱れを飲み込まない）。
- 主な分類は、含まれる信号の分類のうち **連続性 → 形状 → 衣装 → 動作** の順で先のもの。連続性は人物の外（背景）まで変わった・明滅・止まった所なので、そのとき人物の輪郭や配色の変化は結果として扱う。ほかの分類は `categories` に残す。
- 出来事の区間は、組になった信号と区間の信号から取る（変わり目だけの信号は戻りのコマを区間に足すことがあるため）。

## 直す場所と手当ての候補

出来事の `target`:

| 書き方 | `target` |
|---|---|
| `clips` | `kind: "clip"`・`path`（設定に書いたパス）・`sha256`・`source_frames`（元のコマ `[開始, 終了)`）・`source_t`（秒）・`origin` |
| `dance_spec` | `kind: "dance_clip"`・上と同じ欄・`spec`・`uses`（`[{pointer: "/performer/clips/2", clip_index, shot_frames: [[開始, 終了)…], shot_t}]`。その元のコマが spec のどの clip で、出力のどのコマ・秒に出るか。blend の窓で前の clip が続く所も数える） |

dance で、spec の区間で一度も出ない元のコマ（`speed` で飛ばすコマなど）で見つけたものは `unused` に分け、数だけ報告する。

`actions` は規則で出す提案で、何も実行しない:

| `kind` | 出す条件 | 中身 |
|---|---|---|
| `regenerate_segment` | 重複の周期以外 | 前後のコマ（`anchors`）を始点・終点にして、その区間だけを生成し直す |
| `replace_frames` | 2 コマ以下の連続性・形状・動作 | 前後のコマで置き換える（保持か混合）。Orpheus にはまだ無い（dance-shot/0.3 の「壊れたコマの差し替え」で予定） |
| `interpolate` | 止まったコマ | 補間（外部の道具）で埋めるか、生成し直す |
| `decimate` | 重複の周期 | `ffmpeg` の `decimate=cycle=N` などで除いてから使う（`speed` を変えるときは先に） |
| `regenerate_with_reference`・`color_match` | 衣装（主な分類か、設定画の信号・VLM の衣装の指摘がある） | 設定画を参照に入れて（強めて）生成し直す。尺を短く切る。配色の差だけなら仕上げ（dance の hooks の `performer` など）で合わせる |
| `trim` | 見た範囲の端に近い（範囲の 20% か 0.5 秒以内） | 使う区間を縮める（dance は `clips[].source_start`・`start`・`end` を直す） |

## 修正計画（`fix_plan.json`）— 自動再生成の入口

`inspect-fix-plan/0.1`。`status: "proposed"`、`executor: null`（自動の再生成は未実装）。クリップごとに、すき間が `plan_merge_s`（0.15 秒）以下の出来事を 1 つの区間にまとめ（間の短い良いコマを、前後の生成し直しに挟まないため）、前後に `plan_pad_frames`（2）コマ広げる。区間ごとに:

- `source_frames`・`source_t`（生成し直す元のコマ）、`event_frames`、`events`（出来事の ID）、`categories`、`evidence`
- `anchors`: 前後のコマ `{frame, image}`。画像は**元の大きさの PNG**（`anchors/<クリップ>_f<コマ>.png`）で、始点と終点を指定できる生成器にそのまま渡せる
- `origin`（素性）、`actions`（区間の `regenerate_segment` を先頭に、出来事の手当ての候補を重ねずに並べる）、dance なら `spec` と `uses`

将来の自動再生成は、この区間・前後の画・素性から生成器のジョブを作り、差し替えた素材で同じ検品をもう一度通す形を想定している（起動は毎回ユーザーの承認。GPU や有料 API の費用がかかるため）。

## VLM（ニューラル品質評価）

数値の検査は形や配色の統計しか見ない。指が 6 本・顔が別人・物理的にありえない姿勢のような意味の分かる不自然さは、画像を読めるモデル（VLM）に見せる。Orpheus は**依頼の束を書いて、ユーザーが明示した外部プログラムを呼ぶだけ**。

```sh
uv run mv inspect check inspect.json --vlm-packet                     # 依頼の束だけを書く（何も送らない）
uv run mv inspect check inspect.json --vlm vlm.json                   # 手元で動く VLM（sends_data: false）
uv run mv inspect check inspect.json --vlm vlm_claude.json --allow-send   # 素材を外へ送る VLM（sends_data: true）
uv run mv inspect check inspect.json --vlm-response response.json     # 手で書いた／別の所で作った返答を取り込む
```

- `--vlm-packet` は束を書くだけの指定で、`--vlm` とは同時に使えない（送らないつもりの指定で送信が起きないように）。`--vlm` と `--vlm-response` も同時に使えない。
- **見せるコマ**: 出来事の山のコマを先に、残りは `vlm.every_s`（1 秒）ごとの見本。合わせて `vlm.max_frames`（24）まで。長い辺 `vlm.max_side`（768）の JPEG。設定画も同じ大きさの PNG で入れる。
- **依頼の束**（`vlm/request.json`、`inspect-vlm-request/0.1`）: `instructions`（`vlm.language` が `ja` か `en`）、`categories`（分類の定義）、`reference`、`frames`（`id` = `クリップ@コマ`・クリップ・コマ・秒・`why`・`file`）、`response_schema`。
- **返答**（`inspect-vlm-response/0.1`）: `tool`（`name`・`version`・`model`）、`request_sha256`（依頼の束の sha256。違えば止める。無ければ警告）、`findings`（`frame_id`・`category`（4 つか `other`）・`description`・`box`（正規化。任意））。依頼に無いコマの id は止める。拒否・打ち切り・失敗で見られなかったコマは `x_unreviewed`（`[{frame_id, reason}]`）に書くと、警告として報告に残る（指摘が無いこととは違う）。
- **取り込み**: 同じクリップの近いコマ（1 コマ以内）に数値の出来事があればそれに添え（`evidence: measured+estimated`。主な分類は数値の規則のまま、VLM の分類は `categories` に足す）、無ければ VLM だけの出来事（`estimated`）を作る。どちらも直す場所と修正計画に入り、VLM が衣装と言った出来事には衣装の手当ての候補も付く。合否には使わない。
- **設定**（`--vlm` の JSON。`docs/adapters.md` と同じ約束）: `tool`・`version`・`argv`（`argv[0]` は存在する絶対パス。シェルは使わない。`{request}`・`{output}`・`{packet}` を置き換える）・**`sends_data`（必須）**・`timeout_s`（既定 900）。**argv はローカルで実行するプログラム**なので、中身を確かめた設定だけを使う。`sends_data: true` の設定は `--allow-send` が無いと、重い処理の前に止まる。
- **代役**（`scripts/vlm_standin.py`）: 画像を読まず、ネットワークにも出ない。`--flag 'id:分類:説明'`・`--echo-events 分類`・`--unreviewed id` で返答の形を確かめる（テストと CI 用）。
- **Claude のアダプタの例**（`scripts/vlm_claude.py`）: 設定画とコマを Messages API の画像入力で送り、構造化出力（`output_config.format`）で返答の形を縛る。既定のモデルはスクリプトの `DEFAULT_MODEL`（`--model` で変える）・effort `high`・6 枚ずつ。設定画はどのバッチでも同じ前置きにしてキャッシュに載せる。拒否されたときの別モデルでの再実行（`fallbacks: "default"`）を有効にしている。拒否・打ち切り・失敗のバッチのコマは `x_unreviewed` に残す。anthropic の SDK を入れた**別の Python**で動かし、鍵は SDK の既定の探し方（`ANTHROPIC_API_KEY` など）で読む。**送信と課金が起きる**ので、設定には `"sends_data": true` を書く。`--dry-run` で送らずにバッチの数を見られる。**このリポジトリの開発環境では API を呼んでいない（未検証）**。偽の client で依頼の組み立てと返答の整え方だけをテストした。

```json
{"tool": "claude", "version": "anthropic SDK の版", "sends_data": true, "timeout_s": 1800,
 "argv": ["/abs/venv/bin/python", "/abs/Orpheus/scripts/vlm_claude.py", "--request", "{request}", "--output", "{output}"]}
```

## 出力

既定は設定の隣の `inspect_report/`（`--out` で変える）。出力先には印の `inspect_output.txt` を置き、印のある出力先だけ、前の実行の `stills/`・`anchors/`・`metrics/`・`vlm/` を消してから書く。印が無いのにそれらがある出力先には書かない（ほかの作業を消さない）。

| ファイル | 中身 |
|---|---|
| `inspect_report.md` | 一覧（クリップ × 分類の数・設定画との重なり・見ていない検査）、クリップごとの出来事の表（ID・コマ・秒・分類・内容・根拠・直す場所・静止画）、修正計画、見ていない検査、VLM、設定画の主な色 |
| `inspect_report.json` | `inspect-report/0.1`。`overall`・設定の sha256・エンジンの版と指紋・ライブラリの版・しきい値・設定画の要約・クリップ（復号の結果・見た範囲・切り抜き・人物のいたコマの数・設定画との重なりの中央値と最小）・`events`・`unused`・`skipped`・`notes`・`vlm` |
| `fix_plan.json` | 修正計画（上） |
| `stills/<出来事>.jpg` | 前のコマ・山のコマ（赤い箱 = 数値の場所、黄色の箱 = VLM の場所）・後のコマ。設定画の信号があれば設定画も並べる（字は ASCII だけ） |
| `anchors/<クリップ>_f<コマ>.png` | 修正計画の前後のコマ（元の大きさ） |
| `metrics/<クリップ>.csv` | コマごとの数値（明るさ・差・構造の差・人物の面積と重心と高さ・塊の数・輪郭と面積と重心と配色の変化・動きの p95・設定画との重なり）。しきい値を自作に合わせて直す材料 |
| `vlm/` | 依頼の束（`request.json`・`frames/`・`reference/`）、返答（`response.json`）、`adapter.log` |

`overall` は `review`（要確認の出来事か復号のエラーがある）／`clean_with_skips`（見つけた所は無いが、見ていない検査がある）／`clean`。どれも合否ではない。終了コードは設定や入力の誤りのときだけ 1。stdout は短い JSON（`overall`・出来事の数・分類ごとの数・見ていない検査・報告と修正計画のパス）。

## 限界

- **実作品の動画では未確認**。合成素材は平らな塗りの図形で、生成動画の質感・ぶれ・圧縮の乱れ・照明の変化とは違う。しきい値は最初の実作品で直す前提。
- 動画の読み方: ffmpeg で、タグが `bt709` の動画は BT.709、ほかは BT.601 として読む（タグの無い BT.709 の動画は色がずれる。`docs/dance.md` の限界と同じ）。コマ番号は復号の順で、可変フレームレートでもコマを足し引きしない。秒はコマ番号 ÷ 表示の fps。
- 形状・衣装・設定画との照合・位置の飛び・揺れは**人物の切り抜きが要る**。緑背景か固定の箱だけで、背景のある場面の人物は切り抜けない（場面のカットは連続性と動きの急変だけを見る。意味の分かる検査は VLM）。
- 頭・上半身・下半身の帯は、立った全身の人物の囲みの高さの割合（0.2・0.55）で、体の部位の検出ではない。
- 設定画との照合は配色だけ。向き・陰影・照明で色の割合は変わる（正面の設定画だけだと、振り向いたコマで欠けが出やすい。背面の設定画も渡す）。形・顔・小物の照合はしない。
- オプティカルフローは平らな塗りの所で不安定。動きの急変は補助の信号で、ほかの信号と同じ所に出ることが多い。
- VLM の指摘は推定で、見せたコマ（既定で最大 24 枚）しか見ていない。
- 自動の再生成・コマの差し替え・補間はしない（提案だけ）。
