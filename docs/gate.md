# gate（書き出し前の検査と、歌詞の行・秒の確認表・実験的）

書き出しの前に、機械で分かる問題だけを見つける道具。好みの判断はしない。

- **採点器ではない**: 字の時刻・UI 枠・重なり・拍・書き出しの揃い方だけを見る。絵の良し悪しや作品としての採用は判定しない（採用は `accept` の人間の判定）。
- **決まりとの関係**: AGENTS.md の「生成物の開発中は測定器・検証器の機能を足さない」の例外として、2026-10-31 にユーザーが認めた。例外はこの `mv gate` の範囲だけ。
- **しきい値はどれも仮の値**: 最初の 1 作で確かめて直す。
- 動作確認は合成の timeline と `mv dance` のデモまで。行・秒の確認表だけ、第 1 作の分離ボーカルで試した（下記）。実作品の run ではまだ通していない。

## 1. 行・秒の確認表

```sh
uv run mv gate lines 歌詞.lrc 出力フォルダ --vocals vocals.flac
```

`--vocals` には `uv sync --locked --extra analysis` が要る。

- 入力は LRC か JSON（`[{"start": 秒, "line" か "text" か "display": 歌詞}, …]`）。`--vocals` にボーカルの Stem か分離音源を渡すと、推定の前後 `--window` 秒（既定 0.8）で歌い出しの立ち上がりを探す。
- 出力は `lines_table.csv`（試聴用。Excel で開ける UTF-8 BOM。確認の欄は空）と `lines_measured.json`（`mv gate check` に渡す）。
- `method`: `vocals_onset` = 実測 / `not_found` = 窓の中に候補が無く推定のまま / `estimate` = ボーカル無し。`mv gate check` は `not_found` と `estimate` の行を実測として照合しない（`method` が無い行は実測として扱う）。
- 行ごとの探索窓は、前後の行の推定時刻との中点で切る。前の行の結果より 0.08 秒以上後の候補だけを取る（短い行が続いても、同じ歌い出しを 2 行に当てない）。
- `ambiguous`: 2 番目の候補との上がり幅の差が 1.5 dB 未満で、どちらの立ち上がりか決め切れない行。確認表の `曖昧` の欄に `要確認` と出る。

**これは行頭の候補で、確定はユーザーの試聴。** 第 1 作の分離ボーカルで試した結果（2026-10-31）:

- 38 行のうち 31 行で実測でき、7 行は not_found。
- 人が詰めた行頭との差は中央値 0.07 秒、0.2 秒以内が 28 行、0.5 秒以内が 37 行。人の値の側がずれている行もある（1 行目は音量の形から見て実測の方が正しい）。
- 大きく外れた 1 行（0.74 秒）がある。語の途中の谷を拾うことがあるので、`上がり幅dB` が小さい行と not_found の行は特に聞いて確かめる。
- 実曲の行間は無音にならない（残響で -20dB 前後の谷）ので、無音からの立ち上がりではなく、谷から山への上がり幅で探す。

## 2. 検査

```sh
uv run mv gate check gate.json [--out DIR]
```

`gate.json` のパスは `gate.json` のあるフォルダ基準（絶対パスも可）で、`timeline` だけが必須。他は持っている分だけ書く。

```json
{
  "timeline": "dance_runs/<run_id>/timeline.json",
  "video": "dance_runs/<run_id>/dance.mp4",
  "master": "master.mp4",
  "expected_sha256": "…",
  "measured_lines": "lines_measured.json",
  "lyric_layers": ["band"],
  "confirmed": "confirmed.json",
  "confirmed_overrides_measured": true,
  "faces": "faces.json",
  "spec": "spec.json",
  "tracks": "tracks.json",
  "poses": "poses.json",
  "beats": "analysis.json",
  "thresholds": {"lead_max_s": 0.2}
}
```

| 検査 | 要るもの | 合格の条件（仮） |
|---|---|---|
| 歌詞の時刻 | `measured_lines`（`mv gate lines` の出力。層 `lyric_layer`（複数なら `lyric_layers`）の、`sung` が false でない行と順に照合） | 字が歌の 0〜0.2 秒前に出る。推定のままの行は見ず、一部なら要確認、全部なら未実施。`confirmed` に一致する行の外れは「要確認（試聴で確定済み）」で不合格にしない（`confirmed_overrides_measured`。既定 true） |
| 確認済みの時刻 | `confirmed`（`[{"text", "start"}]`） | 1 つも動いていない |
| 字と顔 | `faces`（`[{"start", "end", "box", "role"}]`。box は正規化座標）か `"faces": "auto"` と `"spec"` | 前の字が主役の顔に重ならない（出入りの間は可。z が behind の層は人物に隠れるので見ない）。顔の時刻はコマに丸める。推定の顔（`auto` の結果）との重なりは不合格にせず要確認にする |
| UI 枠 | timeline だけ | 0 コマ。箱に収まらない字（`overflow`）も不合格 |
| 揺れ | `tracks`（`{"名前": [[t, x, y], …]}`。画素） | 2 階差分の RMS 3 画素以下。3 点未満の要素は見ず、一部なら要確認、全部なら未実施 |
| つなぎ目 | `poses`（`[{"t", "points": [[x, y], …]}]`。正規化座標） | 切れ目の前後 2 コマ以内の骨格で姿勢の差 0.05 以下。超えたら要確認（切るコマの変更か `camera.seams` の提案）。時刻はコマに丸めてから前後を決める。1 コマ以内に `camera.seams` があれば、その設定を文言に添える。骨格が前後に無ければ要確認 |
| 字の重なり | timeline だけ | `exclusive` な層ごとに同時表示 0.1 秒以下（`exclusive` の無い 0.1 の timeline は back だけ） |
| 拍 | `beats`（`downbeats`・`beats`・`beats_s`（`mv analyze` の出力）のどれかの配列）。無ければ timeline の `beats` | フラッシュと切れ目が小節頭（無ければ拍）から 1 コマ以内。`beat_exempt` の切れ目・フラッシュは「対象外（意図）」として数えるが、不合格にしない。blend の切れ目は blend の開始時刻を見る |
| 書き出し | `video`・`master`（任意で `expected_sha256`） | 全部のコマを復号できる、コマ数・尺が描いた区間（timeline の `interval`）と揃う、SHA-256 が控えと同じ、SSIM 0.98 以上 |

一部の区間だけ描いた run（`mv dance render --start/--duration`）は、timeline の `interval` の中に出るものだけを見る。確認済みの時刻だけは曲全体で見る。

`timeline` は `dance-timeline/0.1` と `0.2` の両方を読む（`src/mv_pipeline/dance/dance_timeline.schema.json` で検証し、知らない欄は無視する。形が違えばエラーで止まる）。独自のレンダラで仕上げた場合も、この形式で timeline を書けば検査できる。

**顔の推定（`"faces": "auto"`）** — `"spec"` に dance の spec のパスを書くと、dance の api から 2 コマごとに人物のアルファを取り、頭の帯から顔の箱を推定する。頭の帯は、fit の体の高さ（無ければその時刻のアルファの高さ）× 寄りの倍率の、上端から 12%。報告には「推定」と書く。

結果は 合格 / 不合格 / 要確認 / 未実施。**未実施は「材料が無くて見ていない」で、合格ではない。** 全体（`overall`）は、不合格が 1 つでもあれば `fail`（終了コード 1）、未実施があれば `pass_with_skips`、要確認があれば `pass_with_warnings`、なければ `pass`。

出力先は既定で `gate.json` の隣の `gate_report/`: `gate_report.md`・`gate_report.json`・問題のコマの静止画（検査ごとに最大 12 枚）。stdout は短い JSON。

## 限界

- 揺れの位置の列・骨格は別途用意する（推定の道具は持っていない）。無ければその検査は未実施になる。顔は `"faces": "auto"` で粗く推定できるが、正面を向いた 1 人の全身を前提にした頭の帯で、顔の検出ではない。
- 入力の `timeline.json` は `mv dance render` の出力か、同じ形式で書いたもの。
