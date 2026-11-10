# gate（書き出し前の検査と、歌詞の行・秒の確認表・実験的）

書き出しの前に、機械で分かる問題だけを見つける道具。好みの判断はしない。

- **採点器ではない**: 字の時刻・UI 枠・重なり・拍・書き出しの揃い方だけを見る（明滅の回数は、頼んだときだけ測る。下記）。絵の良し悪しや作品としての採用は判定しない（採用は `accept` の人間の判定）。
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
  "flash": {"delta": 0.10, "max_per_s": 3},
  "thresholds": {"lead_max_s": 0.2, "overlap_mode": "time"}
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
| 字の重なり | timeline だけ | `exclusive` な層ごとに同時表示 0.1 秒以下（`exclusive` の無い 0.1 の timeline は back だけ）。見えている区間は、timeline の `lyric_fade` が `"after"`（無ければこれ。手書きの timeline）なら `[start, end + fade_out_s]`（フェードアウト込み）、`"inside"`（`mv dance` の出力。end の前でフェードアウトを終える）なら `[start, end]`。問題の行には 2 行の箱が交わるか（`box_intersects`）を添える。`thresholds.overlap_mode` が `"time_and_box"` なら箱が交わるときだけ数える（既定 `"time"`） |
| 拍 | `beats`（`downbeats`・`beats`・`beats_s`（`mv analyze` の出力）のどれかの配列）。無ければ timeline の `beats` | フラッシュと切れ目が**拍**（`beats` / `beats_s`。無ければ小節頭で代用）から 1 コマ以内。小節頭に合わせたいものは `flash_info` / `seam_info` の `align: "downbeat"`（dance の spec では `flashes[].align` / `clips[].align`。無ければ `"beat"`）で小節頭（無ければ拍で代用）と照合する。文面と集計は照合先ごと（「拍から」「小節頭から」、「N 件を拍と照合、M 件を小節頭と照合」）。`beat_exempt` の切れ目・フラッシュは「対象外（意図）」として数えるが、不合格にしない。blend の切れ目は blend の開始時刻を見る |
| 書き出し | `video`・`master`（任意で `expected_sha256`） | 全部のコマを復号できる、コマ数・尺が描いた区間（timeline の `interval`）と揃う、SHA-256 が控えと同じ、SSIM 0.98 以上 |
| 明滅（`flash` を書いたときだけ） | `flash`（`{"delta", "max_per_s"}`。既定値で測るなら `{}`）と、`master`（無ければ `video`） | どの 1 秒の窓でも、画面全体の明るさ（線形の相対輝度）の明滅が `max_per_s` 回以下（既定 3。変化は直前の山・谷から `delta` 以上、既定 0.10）。測り方と限界は下の「完成動画の明滅を測る」。**規格への適合を判定するものではない**。動画が無ければ未実施 |

`flash` を書かない gate.json では、明滅は測らず、検査は上の 9 つのまま（結果も合否も出力のファイルも、この検査を足す前と同じ）。

一部の区間だけ描いた run（`mv dance render --start/--duration`）は、timeline の `interval` の中に出るものだけを見る。確認済みの時刻だけは曲全体で見る。

`timeline` は `dance-timeline/0.1` と `0.2` の両方を読む（`src/mv_pipeline/dance/dance_timeline.schema.json` で検証し、知らない欄は無視する。形が違えばエラーで止まる）。独自のレンダラで仕上げた場合も、この形式で timeline を書けば検査できる。
`mv fx timeline render`（複数の fx ショットをつなぐ。docs/fx.md の timeline）の run の `timeline.json` もこの形。`0.1` に `beats`・`seam_info`（kind は `cut`・`xfade`・`black`・`flash`・`burn`）・`flash_info`（mode は `lerp_display`）を足したもので、字幕は無い。

**顔の推定（`"faces": "auto"`）** — `"spec"` に dance の spec のパスを書くと、dance の api から 2 コマごとに人物のアルファを取り、頭の帯から顔の箱を推定する。頭の帯は、fit の体の高さ（無ければその時刻のアルファの高さ）× 寄りの倍率の、上端から 12%。報告には「推定」と書く。

結果は 合格 / 不合格 / 要確認 / 未実施。**未実施は「材料が無くて見ていない」で、合格ではない。** 全体（`overall`）は、不合格が 1 つでもあれば `fail`（終了コード 1）、未実施があれば `pass_with_skips`、要確認があれば `pass_with_warnings`、なければ `pass`。

出力先は既定で `gate.json` の隣の `gate_report/`: `gate_report.md`・`gate_report.json`・問題のコマの静止画（検査ごとに最大 12 枚）。stdout は短い JSON。`flash` を書いて動画を測ったときは、測定の詳細 `flash_report.json` も同じフォルダに書く。

## 3. 正解の分かっている試験素材（`mv gate demo`）

```sh
uv run mv gate demo projects/gatedemo                 # 既定: 4 行（2 行目は 0.333 秒早く、4 行目は正解の 0.05 秒後）、拍に置いたフラッシュ 5 件
uv run mv gate lines projects/gatedemo/lyrics.lrc projects/gatedemo/lines --vocals projects/gatedemo/vocals.wav
uv run mv gate check projects/gatedemo/gate.json      # expected.json の checks と比べる。既定では歌詞の時刻が fail（終了コード 1）、拍が pass
```

書くもの: `vocals.wav`（声の代役。行ごとに正解の歌い出しから持続する倍音の音。立ち上がり `--attack-ms`、背景の雑音 `--floor-db`）、`lyrics.lrc`（宣言した行の時刻 = 正解の 0.1 秒前 + `--shifts`）、`timeline.json`（手書きの dance-timeline/0.1。層 band の行・フラッシュ・拍の格子）、`beats.json`（`--bpm`・`--beats-per-bar`。`--no-downbeats` で小節頭を省く）、`gate.json`、`expected.json`（行ごとの正解の歌い出し・宣言・期待する lead と合否、フラッシュごとの照合先からのずれと合否、検査ごとの期待する結果）。`--flashes` と `--flash-align beat|downbeat` でフラッシュの位置と照合先を変えられる。

正解はすべて素材の設計値で、推定ではない。音は倍音だけで子音も残響も無いので、実曲の歌い出しの推定精度の確かめにはならない（工程の接続と検査の論理を確かめる素材）。CI はこの素材で demo → lines → check → expected の照合を回す。`--vocals` の WAV は soundfile が無くても読める（他の形式は analysis extra）。

## 既定の合否が変わった変更

- 2026-11-08（字の重なり）: `lyric_fade` の無い timeline（手書き）は、見えている区間をフェードアウト込み `[start, end + fade_out_s]` で数える。前の行が `end` で消えたことにして「超過 0」だった、フェードアウト中に次の行が出る timeline は、重なりが 0.1 秒を超えれば不合格になる（例: `end=3.0`・`fade_out_s=0.3` の次に `start=3.0` の行 → 0.300 秒）。`mv dance` は行を `end` の前でフェードアウトし終えるので、この変更から timeline に `lyric_fade: "inside"` を書き、重なりは `[start, end]` で数える（**この変更より前に描いた dance の run は `lyric_fade` が無く after として読むので、フェードの分だけ重なりが増えて不合格になりうる。描き直すか timeline に `"lyric_fade": "inside"` を足す**）。別の列に置いた 2 行を重なりと数えたくないときは `thresholds.overlap_mode: "time_and_box"`。Remotion で描いたテロップのデモで見つかった。
- 2026-11-08（GATE-3）: 拍の検査の照合先を「小節頭があれば小節頭」から「既定は拍（`align: "downbeat"` のものだけ小節頭）」に変えた。小節頭の配列を持つ timeline で、拍に置いたフラッシュ・切れ目が不合格だったものは合格になる。小節頭に合わせたい出来事は `align: "downbeat"` を書く（書かないと拍と照合するので、小節頭からのずれは見つからない）。Remotion で描いたテロップのデモを `mv gate check` に掛けたとき、拍のフラッシュ 5 件が「小節頭と照合、ずれ 4」で止まったことから。

## 連番から timeline を起こす（`mv gate timeline`）

Remotion などで描いた透過の連番（PNG。straight α）から、字が**実際に見えているコマと範囲**を α で実測し、`mv gate check` が読む dance-timeline/0.1 を書く。宣言（props の時刻）と実測の差を行ごとに出す。

```sh
uv run mv export remotion-props projects/song-a --out props.json --beats-per-bar 4
uv run mv gate timeline --frames out/line1:lyric-001 --frames out/line2:lyric-002 --lyrics props.json --layer band --out timeline.json
uv run mv gate timeline --frames out/all --hints hints.json --lyrics props.json --layer band --out timeline.json   # 1 つの連番に全行
uv run mv gate check gate.json      # {"timeline": "timeline.json", "beats": …} 
```

- 行の切り分けは、行ごとに別の連番（`--frames DIR:行の id`）か、1 つの連番と行ごとの領域の手がかり（`--hints` = `{行の id: 正規化した箱}`。箱の中の不透明な画素をその行と見なす）。props の行が 1 つだけなら手がかりは要らない（あればその箱の中だけを見る）。
- 「見えている」は α > `--alpha-threshold`（既定 64）の画素があること。行の最初と最後に見えるコマ（end は最後のコマ + 1）と、見えている全コマの不透明な画素を覆う箱（正規化）を測る。
- 宣言と実測の差（例「開始が 1 コマ遅い」「途中で見えないコマ 2」）は `<out の stem>_measured.json` と stdout の要約に出す。どちらが正しいかは決めない。見えているコマが無い行は、timeline に宣言の時刻のまま残し（`measured: false`。行数を props と揃え、`mv gate check` の行の対応を崩さない）、要約の `unseen_lines` に出す。`mv gate check` はこの行を実測として扱わない（歌詞の時刻では「実測できず見ていない行」として要確認、字と顔・UI 枠・字の重なりには出さない）。
- 連番のコマ数は props の `duration_frames` と同じでなければならない（途中だけ描いた連番は測れない）。アルファの無い連番はエラー。並びは数字の大小（`frame2` < `frame10`）。
- 拍・小節頭は props からそのまま秒に直して写し（丸めない）、フラッシュは props に無いので `--flashes 5.5,6.0`（秒）で与える。書く timeline は実測なので `fade_in_s` / `fade_out_s` は 0、`lyric_fade` は inside、`exclusive` は true。連番の各コマ（読んだバイト列から 1 回で計算）と props・使った hints の sha256 は timeline の `measured.sources` に残る。
- 確かめたのは合成の連番（numpy で描いた箱）だけ。実際の Remotion の連番では未確認。

## 完成動画の明滅を測る（`mv gate flashes`）

hooks で足した光（拍に合わせた閃光など）は timeline に載らないので、描き終えた動画そのものから、画面全体の明るさの明滅を数える。timeline は要らず、完成した動画 1 本だけに掛けられる。

```sh
uv run mv gate flashes final.mp4 [--out DIR] [--delta 0.10] [--max-per-s 3]
```

- **機械で測った回数の報告で、規格への適合を判定するものではない。** 既定値は、広く使われている目安（1 秒間に 3 回を超える明滅を避ける。変化の大きさは最大の明るさの 10%）に合わせた仮の値。`pass` は「この測り方で上限を超えなかった」だけを意味し、放送・配信の基準への適合や、見る人への安全を示さない。光の強さや速さの好みも判定しない。
- 測り方:
  1. **明るさ = 線形の相対輝度**: コマを小さく読み（幅 320 画素までの面積平均。色の行列はタグによらず BT.709 として読む）、表示の値を sRGB の伝達特性で画素ごとに線形へ戻し（fx・dance の合成と同じ式）、`0.2126 R + 0.7152 G + 0.0722 B` を画面全体で平均する（0〜1）。`--delta` はこの値に対するしきい値で、ガンマのかかった表示の値の差ではない（灰色 128 は 0.216、188 が 0.503）。コマの間隔が一定でない動画（可変フレームレート。ffmpeg の情報の平均 `fps` と基準 `tbr` が違うもの）は、ffmpeg の `fps` フィルタで基準のフレームレートに揃えた一定の間隔のコマにしてから測る（1 秒の窓がコマ数と合うように。コマ番号と秒は揃えた後のもの）。平均のフレームレートに揃えると、コマの詰まった区間の光が落ちて数えられないので、基準に揃える。基準が 240 fps を超えるときは、測らずにエラーで止まる（`mv gate check` では「明滅」の行だけ不合格）。コマの間隔が一定の動画（平均と基準が同じ）は、揃えずにそのまま測る。
  2. **変化**: 明るさが、直前の極値（山か谷）から `--delta` 以上離れること（しきい値つきのジグザグ）。離れたコマで 1 つの変化とし、向きを反転して次の極値を追う。変化の時刻は、しきい値を越えたコマ。上がりと下がりは必ず交互になる。数コマかけた上がりも、ゆっくり消える光の戻りも、山・谷からの差が合計で `--delta` に達したコマで 1 つの変化になる。`--delta` 未満の揺れは変化にしない。最初の変化までは、それまでの最小と最大の両方を極値として見る。
  3. **明滅**: 隣り合う 2 つの変化（暗 → 明 → 暗、または明 → 暗 → 明）で、コマの差が 1 秒未満の組を 1 回と数える。前から順に、重ならないように組にする。片方向だけの変化（場面の切り替え・単調なフェード）と、1 秒以上離れた 2 つの変化は明滅にしない。
  4. **1 秒の窓の回数**: その 1 秒（あるコマへの変わり目から、fps コマ未満の変わり目まで）の明るさだけに 2 を掛け直し、隣り合う 2 つの変化を前から組にした数。窓の前の明るさは使わない。動画全体で取った変化から数えると、窓の前の変化が回数を減らすことがあるため（明るい場面への切り替えの後の最初の光は、上がりが切り替えの上がりに吸収される。暗くなる切り替えは、組を 1 つずつずらす。どちらも、1 秒に 4 回の光が 3 回と数えられる）。そのため、`changes`・`flashes`（動画全体）と、窓の回数・組が違うことがある。窓は 1 コマずつずらして全部見る。どの窓でも最大の回数（`max_per_window`）が `--max-per-s` を超えたら `status` は `fail` で、終了コード 1（gate のほかの検査の不合格と同じ扱い）。
- 出力は既定で動画の隣の `<動画の名前>_flash_report/flash_report.json`（`--out` でフォルダを変える）。stdout は短い JSON。
  - `video`（名前・sha256・平均のフレームレート `fps`・基準のフレームレート `base_fps`・測ったコマ数 `frames`・寸法・色の行列のタグ）、`thresholds`（`delta`・`max_per_s`・`window_s`）。
  - `read`: 測り方。読んだ大きさ `size`、測ったコマのフレームレート `fps`、一定の間隔のコマに揃えたか `resampled`（true なら `video.frames`・コマ番号・秒は揃えた後のもの）、色の行列 `matrix`（`bt709`）、線形に戻す式 `transfer`（`srgb`）、係数 `weights`、`brightness`（`relative_luminance` = 線形の相対輝度）、`change`（`delta_from_last_extremum` = 直前の極値から `delta` 以上）、`window`（`recounted_from_its_own_frames` = 窓の回数は、その窓の明るさだけで数え直す）。
  - `changes`: 変化の一覧（動画全体に 2 を掛けたもの）。`frame`（しきい値を越えたコマ）・`t`（秒 = コマ / fps）・`direction`（`up` / `down`）・`amount`（大きさ = 山から谷までの差）・`from` / `from_frame`（離れる前の極値とそのコマ）・`to` / `to_frame`（変化の後に着いた極値とそのコマ。最後の変化は動画の終わりまでの極値）。
  - `flashes`: 明滅の一覧（`changes` を 3 で組にしたもの）。`frame` / `t`（1 つ目の変化）・`return_frame` / `return_t`（2 つ目の変化）・`order`（`up_down` = 暗 → 明 → 暗、`down_up` = 明 → 暗 → 明）・`amount`（2 つの変化の小さい方）。
  - `counts`（動画全体の変化・上がり・下がり・明滅・組にならなかった変化の数）、`max_per_window`（どの 1 秒の窓でも最大何回か）、`busiest_window`（最も多い窓。始まりのコマと秒・その窓で数え直した変化の数と組のコマ。同じ回数なら早い窓で、始まりは最初の変化に寄せる。明滅が無ければ null）、`status`。
  - 同じ動画と同じしきい値なら同じ報告になる（時刻や絶対パスは書かない）。
- `mv gate check` は、gate.json に `"flash": {"delta": 0.10, "max_per_s": 3}` を書いたときだけ、`master`（無ければ `video`）に同じ測定を掛けて「明滅」の行を足す。行の文には、動画全体の変化・明滅の数と、1 秒の窓ごとに数え直した最大の回数を書く（合否は後者で決まる）。超えたら不合格で、問題の一覧に最も多い窓を出す（時刻はほかの検査と同じショットの時刻で、一部の区間だけ描いた run では `interval` の開始を足す。ミリ秒に切り捨てるので、静止画は窓の最初の変化のコマになる）。「明滅」の静止画は測った動画（`master` があれば `master`）から取り、ほかの検査の静止画は今までどおり `video` から取る。`master` も `video` も無ければ未実施、動画を読めなければ不合格（ほかの検査の結果は残る）。`flash` の知らない欄や範囲の外の値は、検査を始める前にエラーで止まる。
- 確かめたのは合成の動画（1 色で塗ったコマに、白い閃光・ゆっくり消える光・緩い明暗を置いたもの。コマの間隔が一定でない動画は、最初の 1 秒に 4 回の閃光を置いて残りのコマを 0.5 秒ずつにしたもので、平均 4.11 fps・基準 30 fps。揃えずに数えると 1 回、平均に揃えると 0 回、基準に揃えると 4 回）と、デモの描画 2 本（どちらも合成素材。2026-11-11、24 fps、既定のしきい値）:
  - `mv fx timeline demo --duration 3` のつなぎのフラッシュ（strength 0.75）: 上がり +0.387（コマ 48）と、次のコマの下がり（山 0.387 → 0.180）が組になり、明滅 1 回。
  - `mv dance demo --width 180 --height 320 --duration 2`: 1.0 秒のフラッシュは上がり +0.136（0.050 → 0.186。コマ 24）。戻りは 1 コマあたり 0.053 以下だが、3 コマ後（コマ 27）に山からの下がりが 0.108 になり、同じ光の上がりと下がりが組になって明滅 1 回。1.5 秒の光は +0.041（0.056 → 0.097）で `delta` に届かず、変化にならない。どの 1 秒でも最大 1 回。
- 実素材: 2026-11-10、リポジトリの外の試作で、73 秒・1080p の完成動画に上がり方向だけの数え方を掛け、11 回・どの 1 秒でも最大 2 回だった。組で数えるこの実装では実素材は未確認。

## 限界

- **明滅の測定（`mv gate flashes`）は、画面全体の平均の明るさだけを見る。**
  - 画面の一部だけの明滅は測れない（全体の平均に薄まる。目安にある「画面のどれだけの広さが光るか」は見ていない）。
  - 色（赤）の明滅は測らない。
  - 暗い側の明るさの条件は見ていない（目安には、暗い方の状態が一定より暗いときだけ数える条件がある。ここでは明るい場面どうしの変化も同じに数える）。
  - 規格への適合を判定するものではない。回数が上限以下でも、基準への適合や安全の確認にはならない（判定は別の道具と人の役目）。
  - 測り方の細部: コマを縮めてから線形に戻すので、画素ごとの細かい明暗が強い絵では明るさが少しずれる（1080p の合成の雑音の絵で、縮めずに測った値との差は最大 0.03）。コマの間隔が一定でない動画は、基準のフレームレート（ffmpeg の見積もり `tbr`）に揃えて測るので、その見積もりが実際のいちばん短い間隔より粗いと、落ちるコマがある（平均と基準が小数 2 桁まで同じ動画は、一定として扱う）。BT.709 でない行列で書いた動画は、色の濃い所の明るさがずれる（BT.601 として読むと、赤の 0.21 が 0.17）。
- **歌い出しの推定は正解より約 20 ms 早い**（2026-11-08 に `mv gate demo` の素材で測定。補正は入れていない。入れるかはユウの判断）。音量の包絡は 30 ms の窓を 10 ms 刻みで前向きに取るので、窓が歌い出しに掛かり始める 2 刻み前から上がり始め、谷から上がり始めた最初の点へ戻す式がそこを選ぶ。立ち上がりの長さにはほぼよらず、背景の床が高いときだけ上がり幅のしきい値を越える点が遅れて偏りが小さくなる。実曲（子音・残響つき）では未測定。

  | 立ち上がり | 背景の床（音の山に対して） | 行ごとの差（推定 − 正解, ms。4 行） | 平均 |
  |---|---|---|---|
  | 5 ms | −60 / −40 / −25 dB | −20, −20, −20, −20（どの床でも同じ） | −20 |
  | 30 ms | −60 / −40 dB | −20, −20, −20, −20 | −20 |
  | 30 ms | −25 dB | −10, −10, −10, −10 | −10 |
  | 80 ms | −60 / −40 dB | −20, −20, −20, −20 | −20 |
  | 80 ms | −25 dB | 0, 0, 0, 0 | 0 |

  4 行とも `vocals_onset` で見つかり、行ごとのばらつきは無い（素材が同じ形の音だから）。gate の許容は 0〜0.2 秒なので、宣言した時刻が正解のちょうど 0 秒前の行は、この偏りで不合格側に出る。
- 揺れの位置の列・骨格は別途用意する（推定の道具は持っていない）。無ければその検査は未実施になる。顔は `"faces": "auto"` で粗く推定できるが、正面を向いた 1 人の全身を前提にした頭の帯で、顔の検出ではない。
- 入力の `timeline.json` は `mv dance render` の出力か、同じ形式で書いたもの。
