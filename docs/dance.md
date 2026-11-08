# dance（ダンス合成・実験的）

緑背景の人物動画をステージの画像に合成し、カメラと歌詞を載せた動画を作る**下書きエンジン**。下書きは構造の決定（時刻・人物の配置とつながり・カメラ・歌詞・切れ目）と、検査できる記録（`timeline.json`）のこと。CLI と設定ファイルだけで動く。縦・横どちらの出力でも動く（UI 枠の既定は縦だけ）。

- 決定論: 同じ spec・素材・環境（ライブラリ版）なら同じフレーム。LLM もネットワークも使わない。
- **実験的**: 設定スキーマ `dance-shot/0.3`（`dance-shot/0.2` と `0.1` も読める）は `contracts/` の外（`src/mv_pipeline/dance/`）にある。`contracts/` への昇格はユーザー承認が要る。
- **汎用の部品だけ**: 作品固有の値はコードに持たず、spec に書く。作品ごとの spec・素材・出力は Git に入れない。
- **採点しない**: `technical_pass` は構造検査、`accept` は人間の判定。絵の良し悪しや時刻の合否は `mv dance` では判定しない（書き出し前の機械的な検査は `mv gate check` で行う。run の `timeline.json` を読む。`docs/gate.md`）。
- 実素材での確認は**生成 AI の縦の緑背景動画 1 本（歌詞なし）**まで。緑抜き・カメラ・hooks・`mv gate` を通し、`technical_pass` と音のずれ（+0.0 ms）を確かめた。絵はコマを抜き出して見た範囲で、通しの再生と試聴はしていない。字の見え方と、他の素材での挙動は未確認なので、素材のある環境で実測すること。

## 流れ

1. 下書き: `mv dance render`
2. 検査: `mv gate check`
3. 仕上げ: `--hooks finish.py` で同じ run 形式に描き直す。または外部レンダラが timeline を書く（下の「仕上げの受け渡し口」）。
4. 同じ検査をもう一度。仕上げても時刻と構造は変わらない。
5. 人が採用を決める: `mv dance accept`

## 準備

fx と同じく opencv が要る（`uv sync --locked --extra fx`）。Windows でスマート アプリ コントロールがオンの PC は `docs/dependencies.md` の 2026-10-30 節。

## 5 分で試す（合成素材）

```sh
uv run mv dance demo projects/dancedemo                                  # 素材と spec.json（540x960・6 秒・24fps）
uv run mv dance plan projects/dancedemo/spec.json                        # 解決した時刻の一覧
uv run mv dance stills projects/dancedemo/spec.json --times 1 3 5 --guides   # 静止画シート（--guides で UI 枠と見えている字の箱を重ねる）
uv run mv dance render projects/dancedemo/spec.json                      # dance.mp4 と timeline.json を run として出す
uv run mv dance accept projects/dancedemo/dance_runs/<run_id> --decision accepted --by NAME
```

デモは動く夜のステージ（2 秒ループの動画。`parallax` 0.7）に、緑背景の棒人間のクリップ 3 本と静止画 1 枚をつなぐ。クリップは縮尺・左右の位置・足元の高さが本ごとに違い、足元はゆっくり流れ、途中で 1 回跳ぶので、`fit` の効きを確かめられる。最後の静止画は blend 3 コマでつなぐ。音声は描画より短く、`fit: "pad"` と `end_s` で足す。カメラは小節頭のアクセントと揺れつきの打ち込みが 1 回。クリップは腕の振りの位相が違うので切れ目で姿勢が飛び、そこにフラッシュと、非対称の seams とブレ（回転は切れ目を越えて続く）が入る。

歌詞は back が 3 row の互い違いの縦積み（reveal word・enter・exit・glow）、band が語ごとの色と行の上書き、title が sung false の層。look は vignette と grain。

0.3 のデモ（既定）は、元のコマの丸めを `nearest` にし、clip_c の最後の半拍をその始まりの姿勢で止め（`retime`。始まりはコマ番号の参照）、clip_b にわざと入れた壊れたコマ 1 つ（胴の高さの紫の帯）を `replace_frames` で前後のコマに差し替え、clip_c の拍ごとに 1 コマのストロボ（`flashes` の `at` と `curve_frames`）を光らせる。差し替えるので出力に紫の帯は出ない（spec から `replace_frames` を消すと 1 コマだけ出る）。

`mv dance demo --schema 0.2` は 0.2 の spec（壊れたコマの無い素材）、`--schema 0.1` は 0.1 の spec と素材を作る（互換の確認用）。

## spec（dance-shot/0.3）

素材パスは **spec ファイルのあるディレクトリ基準の相対パス**（`io.safe_path` で逸脱を拒否）。検証は JSON Schema 2020-12（`src/mv_pipeline/dance/dance_shot.schema.json`）。未知の鍵は拒否し、`x_` で始まる鍵はどの階層でも許す（描画には使われないが、キャッシュ key には入る）。

```json
{
  "schema_version": "dance-shot/0.3",
  "seed": 0,
  "output": {"width": 1080, "height": 1920, "fps": 24, "crf": 18},
  "duration_s": 30.0,
  "beats": {"bpm": 120.0, "offset_s": 0.0, "beats_per_bar": 4, "count": 80},
  "audio": {"path": "audio.wav", "offset_s": 0.0, "fit": "pad", "end_s": 29.0, "fade_in_s": 0.02, "fade_out_s": 0.25},
  "stage": {"path": "stage.mp4", "source_start": 0.0, "loop": true, "parallax": 0.7},
  "performer": {
    "clips": [{"path": "a.mp4", "start": 0.0, "end": {"beat": 20}, "source_start": -0.07, "speed": 1.0},
              {"path": "b.mp4", "start": {"beat": 20}, "end": 20.0, "placement": {"offset": [0.01, 0.0], "scale": 1.1, "flip_x": true}},
              {"path": "end.png", "start": 20.0, "end": 30.0, "transition_in": {"type": "blend", "frames": 3}, "beat_exempt": true}],
    "key": {"color": [0, 255, 0], "tolerance": 0.22, "softness": 0.12, "spill": 1.0, "choke_px": 1, "feather_px": 1},
    "placement": {"anchor": [0.5, 0.92], "pivot": [0.5, 1.0], "height": 0.72, "flip_x": false},
    "fit": {"mode": "body", "body_height": 0.62, "x": "median", "ground": "lock", "ground_window_s": 0.7, "ground_smooth_s": 0.3}
  },
  "camera": {
    "keys": [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5], "rotate_deg": 0},
             {"t": 30.0, "zoom": 1.08, "center": [0.5, 0.55], "ease": "in_out"}],
    "accents": [{"at": {"grid": "downbeats", "from": {"beat": 8}, "to": 30.0, "except": [{"beat": 20}]}, "zoom": 1.018, "decay_s": 0.12},
                {"at": [{"beat": 4}], "zoom": 1.06, "decay_s": 0.12, "shake_px": 14, "shake_decay_s": 0.18}],
    "seams": [{"t": 10.0, "span_s": 0.4, "zoom": 1.25, "rotate_deg": 6},
              {"t": {"beat": 40}, "pre_s": 0.3, "post_s": 0.5, "zoom": 1.55, "rotate_deg": 6, "rotate_post_deg": 9, "pivot": [0.5, 0.5],
               "blur": {"samples": 8, "shutter_frames": 1.0, "peak_shutter_frames": 1.6}}],
    "cover_rotation": true
  },
  "flashes": [{"t": 10.0, "strength": 0.6, "decay_s": 0.2, "color": [255, 255, 255], "mode": "lerp_linear"}],
  "look": {"vignette": {"amount": 0.3}, "grain": {"sigma": 1.5}},
  "lyrics": {
    "fonts": {"display": {"path": "fonts/display.ttf"}, "text": {"path": "fonts/text.ttc", "index": 0}},
    "font": "text",
    "ui_regions": [{"name": "right", "box": [0.86, 0.45, 1.0, 0.88]}],
    "layers": {
      "back": {"font": "display", "box": [0.03, 0.2, 0.97, 0.62], "size_px": 380, "reveal": "word",
               "rows": {"align": "center", "gap_em": 0.04},
               "enter": {"scale_from": 1.3, "rise_px": 26, "dur_s": 0.14}, "exit": {"scale_to": 1.16, "dur_s": 0.3},
               "glow": {"color": [255, 150, 70], "radius_px": 22, "strength": 0.8}},
      "band": {"size_px": 118, "stroke_px": 8, "fade_in_s": 0.1},
      "title": {"z": "front", "font": "display", "box": [0.06, 0.08, 0.94, 0.2], "size_px": 132, "sung": false}
    },
    "lines": [
      {"layer": "back", "text": "ONE TWO THREE", "start": {"beat": 3, "offset_s": -0.04}, "end": {"beat": 7},
       "words": [{"text": "ONE", "t": {"beat": 3, "offset_s": -0.04}, "row": 0},
                 {"text": " TWO", "t": {"beat": 3.5, "offset_s": -0.04}, "row": 1},
                 {"text": " THREE", "t": {"beat": 4, "offset_s": -0.04}, "row": 2}]},
      {"layer": "band", "text": "うたのことば", "start": {"beat": 7, "offset_s": -0.1}, "end": {"beat": 10},
       "words": [{"text": "うた", "t": {"beat": 7, "offset_s": -0.1}, "color": [205, 160, 255]}, {"text": "のことば", "accent": false}]},
      {"layer": "band", "text": "こたえるこえ", "start": {"beat": 40, "offset_s": -0.1}, "end": {"beat": 44},
       "style": {"color": [255, 222, 170], "glow": {"color": [255, 150, 60], "radius_px": 14, "strength": 0.55}}},
      {"layer": "title", "text": "TITLE", "start": 28.0, "end": 30.0}
    ]
  }
}
```

- 必須は `schema_version`・`output.width/height`・`duration_s`・`stage`・`performer.clips`。他は任意。出力寸法は偶数。
- 座標はすべて**出力画面の正規化座標** 0..1。時間は秒で、**ショット時刻**（`--start` で区間を切って描いても同じ時刻は同じ絵）。色は sRGB 0〜255。
- `seed`（既定 0）: 乱数の種。
- `performer.clips`: start の昇順で、区間は重ねない。元動画のフレームは `(source_start + (t − start) × speed) × 元動画の fps` を `performer.frame_rounding` で丸めた番号（既定は切り捨て。下の「元のコマの対応」）。`source_start` は負でもよく、0 より前は先頭のフレームを保つ。動画の末尾を越えたら最後のフレームを保つ。人物のいない区間は warnings に残る。同じ `path` を何度使ってもよい。`path` に静止画（png/jpg/webp）を書くと 1 枚を保つ（`speed` は無視。アルファは見ないので、緑抜きの背景にしておく）。
- `placement`: 元動画の点 `pivot`（元動画の幅・高さに対する割合）を画面の点 `anchor` に合わせる。`height` は元動画の高さが画面の高さの何倍か（全身が映る動画なら、人物の身長の割合ではない）。`flip_x` は全体の左右反転（元動画の点 `pivot` は反転しても `anchor` に来る）。
- `clips[].placement`（`fit` の後に掛ける）: `offset`（[dx, dy]。出力の正規化座標）、`scale`（倍率。`anchor` のまわり）、`flip_x`（全体の `flip_x` と重ねると元に戻る）。
- `clips[].transition_in`: `{"type": "cut"}`（既定）か `{"type": "blend", "frames": 3}`。blend は clip の start から frames コマの間、前のクリップの再生を続け（素材の終わりを越えたら最後のフレームを保つ）、両方をそれぞれの配置で置いて、前乗算の色とアルファを smoothstep で重ねる。前のクリップと隙間なく接していること。
- `clips[].transition_in` の `{"type": "dissolve", "frames": 4}`（0.3）: 切れ目（start）を中心に、前後 frames/2 コマずつ線形に重ねる。新しい側の重みは窓の頭で 0 から始まり、`(t − (start − frames/2/fps)) / (frames/fps)` で増える（非公開の制作のディゾルブと同じ式）。前のクリップは start の後も続き、このクリップは start の前から始まる（start より前の元のコマは `source_start + (t − start)·speed`。0 より前は先頭のコマ）。重ね方は blend と同じで、前乗算の色とアルファを重ねる（フレーム全体を重ねるのと同じ値）。前のクリップと隙間なく接し、1 つのクリップに入る窓（頭の後半と次の前半）の和がクリップの長さ以下であること。timeline の `seam_info[].kind` は `dissolve`。
- `clips[].shift`（0.3）: 切れ目の前後で人物をずらす（つなぎで人物の位置や大きさをそろえる）。`[{"t": 10.05, "ease": 9.25, "offset": [-0.035, 0], "scale": 0}]`。`t` で全部効き、`ease` で 0 になる（smoothstep）。`ease` が `t` より前なら、`ease` から効き始めて `t` 以降は全部（出ていくクリップに使う）。後なら、`t` までは全部で `ease` までに消える（入ってくるクリップに使う）。`offset` は出力の正規化座標、`scale` は倍率の差（0.1 で 1.1 倍）で、足元の点（`performer.rows` の `cx_med` と足元の列）を中心に掛けるので足は床に残る（`scale` を使うと、hooks が無くても足元の列を作る。人物をどのコマにも測れない動画に `scale` を書くと、中心が無いので止める。`offset` だけなら描ける）。同じクリップの shift は足し合わせる。カメラの前に掛け、`performer_info` の `placement`・`body` と timeline の配置の列にも入る。式は非公開の制作のずらしと同じ（制作の値の `dx` は元動画の画素なので、`offset` は `dx / 元動画の幅`）。
  - 確かめ（2026-11-09）: 非公開の制作の 32 秒のずらし 3 つを `shift` に写すと、960 コマ × 5 クリップで、画面で動かす量が制作と 7e-15 画素以内（浮動小数の丸めだけ）で同じだった。ディゾルブは制作の 2 つの設定ではどの切れ目も使っていないので、同じ切れ目に窓の幅 4 コマと 6 コマを入れて比べ、960 コマすべてで（クリップ, 重み）が制作の式と同じだった。
- `clips[].beat_exempt`: この切れ目は意図して拍から外している（`mv gate check` が対象外として数える）。
- `clips[].align`: この切れ目を `mv gate check` が照合する相手。`"beat"`（既定。拍）か `"downbeat"`（小節頭）。timeline の `seam_info[].align` に写す。
- `stage`: 画像（png/jpg/webp）か動画。出力の縦横比に cover で中央に合わせる（余る側を切る。縮尺の計算は 1 回だけ）。動画は `source_start`（秒）から再生し、`loop`（既定 true）で折り返す。false なら最後のフレームを保つ。読み出しは人物と同じく前方向だけ。`parallax`（既定 1.0）はステージだけの視差（下の「カメラ」）。
- `lyrics.fonts` = `{"名前": {"path": …, "index": 0}}`。`lyrics.font` は既定の書体名。`fonts` が無ければ `font` はパスで、`font_index` とあわせて `fonts["default"]` になる（0.1 の書き方）。ASCII 以外の歌詞には書体が要る（フォントファイルを spec の隣に置く。素材と同じく sha256 が記録される）。無ければ Pillow の既定の欧文フォントを使う。

### 拍と時刻の参照

`beats` は次のどちらかで書く。どちらも正規化で `beats`・`downbeats` の配列になる。

- `{"path": "beats.json"}`: `beats`（または `beats_s`）と任意の `downbeats` の配列。gate・`mv analyze` と同じ形。素材として sha256 を manifest に残す。
- `{"bpm": 120, "offset_s": 0.0, "beats_per_bar": 4, "count": 512, "downbeat_phase": 0}`

時刻の欄（clips の start/end、`camera.keys` の t、seams・accents・flashes の t、歌詞の start/end と語の t）は、秒の数値か次の参照で書ける。読み込み時に秒へ解決するので、`spec.data` には秒だけが入る。`beats` が無いのに参照があればエラー。解決した結果は `mv dance plan` で見られる。

- `{"beat": 12.5}`: 拍の番号（0 始まり）。小数は前後の拍の間を線形補間し、格子の外は端の間隔で外挿する。
- `{"bar": 3, "beat": 1.5}`: 小節頭の番号 + 拍数。
- どちらも `"offset_s": -0.1` を足せる（字を歌の少し前に出す、など）。解決した時刻が負ならエラー。
- `{"frame": 302}`（0.3）: 出力のコマ番号。`302 / output.fps` 秒になる。カットの切れ目を出力のコマで決めるとき（`beats` は要らない）。

### 元のコマの対応（0.3）

どれも 0.2 の spec には無い欄で、既定値なら 0.2 と同じ絵になる。解決した値は `mv dance plan` の `retime`・`replace_frames` の表で見られる。

- `performer.frame_rounding`: 元のコマ番号の丸め。`floor`（既定。0.2 と同じ切り捨て）か `nearest`（いちばん近いコマ。半コマは切り上げ）。秒とコマを四捨五入で対応させて作った編集を再現するときは `nearest` にする。
- `clips[].retime`: `[{"from": 時刻, "to": 時刻, "source": [a, b], "unit": "frame"}]`。区間 [from, to) の間は、元のコマを a から b へ直線で進める（`a + (b − a)·(t − from)/(to − from)` を `frame_rounding` で丸める）。a と b が同じなら止まる。`unit` は source の単位で、`frame`（既定。元のコマ番号）か `s`（元の秒）。区間の外は既定の対応。同じ clip の区間は重ねない。clip の区間と交わらない区間は効かないので warnings に残る。静止画の clip には使えない。
- `performer.replace_frames`: `[{"path": "b.mp4", "frames": [a, b], "with": "nearest"}]`。動画 `path` の壊れたコマ [a, b)（半開区間。`mv inspect` の提案と同じ）を見せずに、`with` のコマを見せる。
  - `with`: `nearest`（既定。区間の前 a − 1 と後 b のうち近い方。同じ距離なら前）、`before`、`after`、コマ番号、`{"image": "fix.png"}`（そのコマの代わりの画像。動画と同じ寸法で、動画と同じく緑抜きの背景）。
  - `path` は `performer.clips` の動画であること。同じ動画の区間は重ねない。接する 2 つの区間（[5, 7) と [7, 9) など）は、前後のコマがもう一方の壊れたコマになるので止める（1 つの区間にまとめる）。0 コマ目からの区間に `before` は使えない。
  - 描画の前に、差し替えのある動画だけコマを数えて突き合わせる。最後のコマまで届く区間の `after`・`nearest`、動画に無いコマ番号、寸法の違う画像は止める。動画の外の区間は warnings に残る。
  - 差し替えのある動画では、末尾を越えた元のコマの番号を最後のコマに止めてから差し替えを引く。最後のコマまで届く区間（`before`・コマ番号・画像）は、clip が動画の末尾を越えて最後のコマを保つ間も効く（壊れた最後のコマを見せない）。差し替えの無い動画の番号は 0.2 と同じ。
  - 差し替えたコマは `fit` の解析で欠け（前後の値で埋める）として扱う（解析の結果に `skipped` が入り、キャッシュも分かれる）。置き場所は見せるコマの値を使う。
  - timeline の `frames` の `source_frame` は見せるコマの番号（画像で差し替えたコマは元の番号）。
  - 前後のコマを保つだけで、混ぜたり補間したりはしない。速い動きの途中のコマを差し替えると止まって見える（`mv inspect` はその場合 `interpolate` を提案する）。

非公開の制作の 32 秒（30fps・5 クリップ・カット 4 つ・差し替え 14 区間・付け替え 2 区間）の対応表をこの書き方の spec に写し、960 コマすべてで、制作のスクリプトと同じ（クリップ, 元のコマ）を返すことを確かめた（素材を読まない比較。2026-11-09）。丸めを既定の `floor` のままにすると 678 コマずれる。

### 人物の合わせ（performer.fit）

生成した人物動画は、本ごとに縮尺・左右の位置・足元の高さが違い、1 本の中でも足元が流れる。`fit` はそれを輪郭から合わせる。無ければ 0.1 と同じ動き。

- `mode`: `"frame"`（`placement.height` のまま）か `"body"`（`body_height` = 人物の体の高さ（アルファの高さの p90）が出力の高さに占める比。既定 0.62）。
- `x`: `"median"`（クリップごとに、アルファの中心 x の中央値を anchor x に合わせる）/ `"track"`（毎フレームの中心 x を `x_smooth_s`（既定 0.5 秒）でならして anchor x に合わせる）/ `"none"`（既定）。
- `ground`: `"lock"`（毎フレームの足元を求め、`ground_window_s`（既定 0.7 秒）の窓で接地している足の位置を取り、`ground_smooth_s`（既定 0.3 秒）でならして anchor y に固定する）/ `"median"`（クリップの足元の中央値を anchor y に合わせる）/ `"none"`（既定）。
- 足元は、アルファ 0.5 超の画素が 3 個以上ある最下行。接地の位置は窓の最大値（低い方の足）を取った後に同じ窓の最小値を取る（最大値だけだと、足元の流れより窓の半分だけ先走るため）。
- `analysis_scale`（既定 0.5）: 解析の解像度の比。
- `clips[].fit: false` で、そのクリップだけ外して `placement` のまま置く。
- 解析はクリップを 1 回だけ読み、`<out>/analysis/<clip の sha256>-<key と fit の設定の sha256>.json` にキャッシュする（内容だけで決まる）。path と sha256 を manifest に残す。

注意:

- しゃがみの多いクリップは p90 が小さく出て、大きく置かれる。`clips[].placement.scale` で直すか、`clips[].fit: false` で外す。
- ジャンプが `ground_window_s` より長いと、接地として扱われて跳んだ分が消える。
- 元の動画の端で頭や手足が切れている素材は、`fit` で縮めると切れ目が画面の中に出る。出力と同じ縦横比の素材なら、`fit` を使わず `placement` を `{"height": 1.0, "anchor": [0.5, 1.0], "pivot": [0.5, 1.0]}` にすると元の端と出力の端がそろい、切れ目が隠れる。カメラを回すときは寄って、端を画面の外に出す。
- この解析は人物を置くための**合成の入力**で、評価にも `technical_pass` にも使わない。

### 音声

- `fit`: `"strict"`（既定。足りなければ `technical_pass` で止まる）/ `"pad"`（足りない分を無音で足して、描画区間ちょうどにする）。
- `end_s`（ショット時刻）: 曲の終わり。ここで `fade_out_s` を終え、以降は無音にする。
- `fade_in_s`（既定 0.02）・`fade_out_s`（既定 0.25）。

## 描画モデル

重ね順は **ステージ → behind の字 → 人物 → フラッシュ → front の字 → look**。合成は線形光の float32 で行い、最後に 1 回だけ sRGB の uint8 へ戻す。

**look** — fx と同じスキーマ・同じ関数（`fx.look.prepare`・`fx.look.apply`）で、全部の層と hooks の `final` の後に掛ける（`docs/fx.md` の look）。無ければ従来の経路を通る。

**緑抜き** — 透明度は色差（BT.709 の Cb・Cr）の距離で決める（明るさを見ないので、背景の濃淡に強い）。距離が `tolerance` 以下は透明、`tolerance + softness` 以上は不透明で、間はなだらかに変わる。`spill` はキー色の成分が他の成分を超える分を削る割合（緑かぶりの除去。緑の衣装には 1 未満を使う）。`choke_px` で縁を内側へ削り、`feather_px` でぼかす（どちらも元動画の画素）。

**カメラ** — ステージと人物に掛かり、字には掛からない。`keys` は zoom・center・rotate_deg を補間する（曲線は到達側のキーの `ease`）。`center` の点が画面の中央に来る。ステージの外が見えるときは端の画素を引き伸ばす（zoom は 1 以上にする）。変換の順番は 基本（keys + accents + 揺れ）→ seams（pivot のまわり）。

- `accents`: 拍に合わせたアクセント。拍の前後で形が違う。`at` は時刻の配列か、grid の指定 `{"grid": "beats"|"downbeats", "from": …, "to": …, "every": 1, "except": [時刻…], "except_tolerance_s": 0.05}`。`zoom`・`attack_s`（既定 0）・`decay_s`（既定 0.12）。包絡は dt = t − アクセントの時刻 として、−attack ≤ dt < 0 で smoothstep の 0→1、0 ≤ dt < 5·decay で exp(−dt/decay)、それ以外は 0。zoom に 1 + (zoom_a − 1)·包絡 を掛ける。
- 揺れ（任意）: `shake_px`（幅 1080 換算）・`shake_decay_s`（既定は decay_s）・`shake_hz`（既定 [15.1, 12.7]）。exp(−dt/shake_decay)·(sin 2πf₁dt, cos 2πf₂dt) の平行移動で、画面座標でズームの後に掛ける。乱数は使わない。
- `seams`: 切れ目の時刻 `t` で最大になる動き（姿勢の飛びを隠す「回転しながら寄って引く」）。`span_s` だけなら前後対称で 0.1 と同じ式。
  - `pre_s`・`post_s`: 寄りは前を u³（u: 0→1）、後を (1 − v)^2.5（v: 0→1）で描く。拍の前から寄り始めないよう、前の寄りは切れ目の直前に集まる。
  - `rotate_deg`: 切れ目で到達する角度。`rotate_post_deg` を書くと、切れ目の後も同じ向きに回り続け、この角度で頂点になって、`post_s` の終わりで 0（傾き 0）に戻る。`rotate_deg` と同じ符号で、絶対値が `rotate_deg` 以上でなければエラー。切れ目の前後の 1 コマの回転の変化量がそろうように解く（切れ目で回転が止まらない）。これを頂点の角度より優先し、頂点が切れ目に近すぎて 1 コマ目で越えるときは頂点を上げ、`post_s` が短くて届かないときは頂点を下げて、warnings に残す。`post_s` は 3 コマ以上要る。
  - `pivot`: 寄りと回転の中心（出力の正規化座標、既定 [0.5, 0.5]）。
  - `blur`: `{"samples": 8, "shutter_frames": 1.0, "peak_shutter_frames": 1.6}`。seams の窓（前後 + 0.05 秒）の中だけで効き、カメラに乗る層ごとに、カメラの変換を samples 回ずらして平均する。シャッター幅 = (shutter + (peak − shutter)·exp(−|t − T|/0.04)) / fps。
- `cover_rotation`（0.2 の既定は true、0.1 は false）: 回転で画面の角に縁が出ないよう、zoom を cos|θ| + (長辺/短辺)·sin|θ| 以上にする（画面の中央のまわりの回転を前提にした式）。
- `stage.parallax`: 式は fx の camera と同じ。zoom_p = z0·(zoom/z0)^p、center_p = c0 + (center − c0)·p、回転 × p（z0・c0 は最初のキー）。人物は p = 1。

**歌詞** — 字は既定で画面に固定で、カメラに乗らない（時刻によらず箱が 1 つに決まり、UI 枠を確かめやすい）。配置は行全体で 1 回だけ決め、時刻で動かさない。`enter`・`exit` の動きは配置の上に掛けるだけで、timeline の箱は動きの無い位置を指す。

- **層**: `lyrics.layers` = `{"名前": {…}}`。`back`（z: behind）と `band`（z: front）は既定値を持つ。他の層は `z` が必須で、`"behind"` は人物の後ろ、`"front"` はフラッシュの後に描く。残りの既定値は `band` と同じ。描く順は z ごとに書いた順（`back`・`band` を書かなければ先頭）。
- **字の大きさ**: `size_px`（幅 1080 の画面での値）から、箱に収まるまで 5% ずつ縮める。最小は幅 1080 で 8px（画面の幅に比例）で、それでも収まらない行は warnings に残し、`timeline.json` の `overflow` を true にする（`box_px` は画面内に切った値なので、欠けはこの印で分かる）。
- **層の設定**: `box`・`size_px`・`color`・`accent_color`・`opacity`・`stroke_px`・`stroke_color`・`fade_in_s`・`fade_out_s` に加えて:
  - `font`: 書体名。
  - `rows`: `{"align": "center"|"left"|"right"|"stagger", "gap_em": 0.05}`。stagger は row ごとに左・中央・右を繰り返す。
  - `reveal`: `"line"`（0.1 と同じ。行の start から全語が見え、語の t で色が変わる）/ `"word"`（語は t まで見えない）。
  - `enter`: `{"scale_from": 1.3, "rise_px": 26, "dur_s": 0.14}`。語の出現時刻から、語のインクの中心のまわりで動かす。
  - `exit`: `{"scale_to": 1.16, "dur_s": 0.30}`。行の end の前 dur_s の間で、行のインクの中心のまわりに広げる。書けば `fade_out_s` の代わりになる。
  - `glow`: `{"color": […], "radius_px": 16, "strength": 0.6}`。字の後ろに、ぼかした字を線形光で足す。
  - `follow_camera`（既定 false）: true ならカメラ（とブレ）に乗る。
  - `exclusive`（既定 true）: この層には同時に 1 行だけ。gate が重なりを検査する。
  - `sung`（既定 true）: false の層の行は、歌詞の時刻の照合から外す（題字・クレジット）。
- **行**: `style` で層の設定（上の全キー。`z` を除く）を行ごとに上書きできる。`sung` も行ごとに上書きできる。
- **語**: `t`・`accent` に加えて `color`（この語の強調の色。`accent_color` の代わりで、t から効く。t が無ければ最初から）と `row`（行番号。既定 0）。行の `text` は語の text をつないだものと一致させる。row の頭の空白は、配置のときに詰める。
- **rows の配置**: row ごとに 1 本の文字列として描く。全 row を同じ大きさにし、箱の幅と高さに収まる最大の大きさにする。語の境目は行全体を描いてから列で切る（字詰めを変えない）。
- **強調は色だけ**（大きさも位置も変えない）。`words` の `t` が来たら語が `accent_color` に変わる。`accent: false` なら強調しない。
- `start` の最初のフレームから見え、`fade_in_s` / `fade_out_s` で出入りする（既定 0.04 / 0.08 秒）。reveal word では語の出現時刻から `fade_in_s` を数える。
- 縦画面の `ui_regions` の既定値は上・右・下の操作部品のおおよその範囲（目安）。字のインクが重なると warnings に残す（描画は止めない）。横画面の既定は空。

**フラッシュ** — `t` から `strength × exp(−経過/decay_s)` だけ効く（5·decay_s まで）。front の字はその上に載るので白飛びしない。

- `mode: "lerp_linear"`（既定。0.1 と同じ）: 線形光で色へ寄せる。
- `mode: "add_display"`: 表示用の値に strength·exp(−経過/decay)·color を足す（線形光のままではなく、sRGB に符号化した値に足す）。
- **同じ値でも `lerp_linear` は暗部で強く見える**（線形光の補間は暗部の持ち上がりが表示空間で足す方式の 2〜3 倍になる。計算値）。旧来の実制作レンダラと同じ見え方が欲しければ `add_display` を使う。
- `beat_exempt`: 意図して拍から外している（`mv gate check` が対象外として数える）。
- `align`: `mv gate check` が照合する相手。`"beat"`（既定。拍）か `"downbeat"`（小節頭）。timeline の `flash_info[].align` に写す。

0.3 で足した欄（どれも任意。書かなければ 0.2 と同じ絵）:

- `at`（`t` の代わり。どちらか 1 つ）: 同じフラッシュを何度も出す。時刻の配列か、accents と同じ拍の格子（`{"grid": "beats", "from": …, "to": …, "every": 1, "except": […]}`）。読み込み時に時刻ごとの 1 つに展開するので、timeline の `flashes`・`flash_info` と `mv gate check` には 1 回ずつ載る。同じ欄の重なりはコマごとに強い方だけを使い、別の欄は順に重ねる。時刻が 1 つもない `at` は warnings に残る。
- `snap`: 時刻とコマの合わせ方。
  - `none`（既定）: `t` 以降の最初のコマから光る。
  - `time`: `t` をいちばん近い出力のコマの時刻に寄せる（減衰もそこから数える。timeline の `t` も寄せた時刻）。拍の参照で書いた切れ目のフラッシュを、切れ目のコマに合わせるとき。
  - `peak`: いちばん近いコマに山を出す。`t` の半コマ前から山の値で光り、減衰は `t` から数える。
- `curve_frames`: コマごとの強さの表 `{"offset": -1, "levels": [0.3, 1, 0.6, 0.3, 0.1]}`。`t` にいちばん近いコマから `offset` コマ目に `levels[0]` を置き、`strength` を掛ける（`decay_s` は使わない。`snap` とは一緒に使えない）。1 コマだけのストロボは `{"levels": [1]}`。
- `mv dance plan` の flashes の表（0.3 の spec）は、欄の番号・形（`decay 0.08` か `コマ -1: 0.3 1 0.6 …`）・`snap` も出す。

非公開の制作の 2 つの設定（指数の減衰のフラッシュ 5 つ、コマごとの表のフラッシュ 5 つと 1 コマのストロボ 2 つ）を、この書き方の spec に写した。どちらも 960 コマすべてで、コマごとに足す表示用の色が、制作の台本の関数と float32 のビットまで同じだった（素材を読まない比較。2026-11-09）。
- 切れ目のフラッシュは `snap: time`、決めの姿勢と最後の打ちは `snap: peak` にした。`snap` を書かないと 39 コマ、`peak` だけ書かないと 2 コマずれる。
- 制作の稲妻の層（切れ目に重ねる絵）はステージの絵なので移していない。

## 仕上げの受け渡し口

下書きの質感はエージェントがここから足す。仕上げても時刻と構造は変わらないので、同じ `mv gate check` を通せる。

### 層の公開 API（`mv_pipeline/dance/api.py`。0.x の間は互換を保つ）

```python
from mv_pipeline.dance import api
ctx = api.prepare("spec.json", scale=1.0, hooks=None)   # Spec も可。rows=True で hooks なしでも足元と中心の列を作る
L = api.layers(ctx, t)              # FrameLayers（hooks を掛ける前の層）
img = api.compose(ctx, L, t)        # uint8 RGB。api.frame(ctx, t) と同じバイト列
rng = api.rng(ctx, "名前")           # spec の seed と名前から決まる乱数
api.close(ctx)
```

FrameLayers の中身はすべて float32・線形・出力の大きさで、色は前乗算。合成の順は stage → behind → performer → flashes → front → look → uint8。

| 欄 | 内容 |
|---|---|
| `stage` | カメラ・視差・ブレを掛けた後のステージ。書き換えない |
| `behind` / `front` | `[(層名, 色, アルファ)]`。その時刻に見えている層だけ。全画面の色とアルファは展開したときに作る |
| `performer` | `(色, アルファ)`。人物のいない時刻は None |
| `performer_info` | `clip`・`source_frame`・`placement`（元→画面、カメラの前の 2×3）・`camera`（2×3）・`blend`（重み）・`body`（足元と中心。下の「足元と中心の列」。列を作らないときは None） |
| `flashes` | `[(強さ, 色, mode)]`。色は `lerp_linear` なら線形、`add_display` なら表示用の値 0..1 |
| `camera` | `zoom`・`center`・`rotate`・`shake`・`seams`・`matrix` |
| `t`・`frame_index` | 時刻とコマ番号 |

### hooks

`mv dance render spec.json --hooks finish.py`（`stills` も可）。spec からは読み込まない。明示したときだけ Python を実行する。関数はどれも任意で、入力を書き換えず、新しい配列を返す。

| 関数 | 掛かる位置 |
|---|---|
| `prepare(ctx)` | 描画の最初に 1 回（テクスチャの準備など） |
| `stage(img, t, ctx, L)` | stage の後 |
| `behind(img, t, ctx, L)` | 後ろの字の後、人物の前（人物の後ろの光・影・反射など） |
| `performer(color, alpha, t, ctx, L) -> (color, alpha)` | 人物そのもの（輪郭の光・色合わせなど） |
| `front(img, t, ctx, L)` | 人物とフラッシュの後、前の字の前 |
| `final(img, t, ctx, L)` | 前の字の後、look の前 |

```python
# finish.py の例: 人物の後ろに足元の影を足す
import cv2
import numpy as np


def behind(img, t, ctx, L):
    if L.performer is None:
        return img
    shadow = cv2.GaussianBlur(L.performer[1], (0, 0), 24)
    return img * (1.0 - 0.5 * shadow[..., None])
```

- **決定論**: hooks は入力・t・定数だけの関数にする。ファイル書き込みとネットワークは禁止。乱数は `api.rng` から取る。
- hooks ファイルの sha256 を run のキャッシュ key と manifest（`hooks: {path, sha256}`）に入れる。hooks が別のファイルを読むときは、その内容はキャッシュ key に入らない。
- timeline は spec だけから作る。hooks では時刻と構造が変わらない。timeline に `finished: {hooks: {path, sha256}}` を残す。
- hooks で例外が出たら run を失敗にし、render.log にトレースバックを残す。
- 例外は特定のカメラ位置でしか起きないことがある（寄りで足元が画面の外に出て、配列が空になるなど）。本番の前に `--scale 0.25 --out <別の場所>` で全コマを流すと、途中で止まる描き直しを避けられる。

### 足元と中心の列（performer.rows。0.3）

hooks を使うとき（`api.prepare(..., rows=True)` でも）、fit の有無にかかわらず全クリップで人物の足元と中心の列を作り、`L.performer_info["body"]` に出力の画素で渡す（DANCE-HOOK-1）。足元の影・床の反射・後光などを、hooks がアルファから探さずに置ける。列を作らないときの絵と timeline は変わらない。

- 測り方: 元のコマを `analysis_scale`（既定 0.5）に縮め、アルファ 0.5 超の画素の「最下の画素の行」（`bottom`）と「x の中央値」（`cx`）を元動画の画素で取る。写っていないコマと `replace_frames` で差し替えるコマは前後の値で埋める。
- 足元の列 `ground`: 使う範囲（spec のどこかで出る元のコマの最小〜最大）の外は端の値で止め、前後 `hold_frames`（既定 10）コマの最大で接地している足の位置を取り、`box_frames`（既定 9。奇数）コマの平均でならす。跳んでいる間も床に残る。`cx_med` は使う範囲の写ったコマの `cx` の中央値（使う範囲に写ったコマが 1 つも無い、たとえば全部を `replace_frames` の画像で差し替えたときは、前後の値で埋めた `cx` の中央値。NaN を hooks に渡さない）。
- `body`:
  - `feet`: 足元の列と `cx_med` の点。
  - `bottom`: このコマの最下の行と `cx` の点。
  - `scale`: 元動画の 1 画素が出力の何画素か（配置の縮尺 × カメラの倍率）。
  - `source`: 元動画の画素での `frame`・`ground`・`bottom`・`cx`・`cx_med`。
  - 点の座標はカメラの後の出力の画素の番号（画素の中心が整数。`img[y, x]` で塗れる）で、人物の描き方と同じ対応にしてある（合成の人物で、描いたアルファとの差は横 0.5・縦 1.2 画素以内）。blend の窓では新しい側。
- `ctx.rows`: 動画のパスごとの列（`bottom`・`cx`・`ground` の配列、`cx_med`、`used`、キャッシュの `file`）。測った値は `<out>/analysis/<clip の sha256 の頭 16 桁>-rows-<設定の sha256 の頭 16 桁>.json` にキャッシュする（素材の中身・key・縮尺・差し替えの区間で決まる。名前は Windows のパスの長さの上限に掛かりにくいよう縮め、全桁は中に書いて読むときに照合する）。
- どのコマにも人物を測れない動画（全部を `replace_frames` で差し替えた・key が合わない）は列を作らない。その clip の `body` は None で、warnings に残す（列は hooks の手がかりなので、作れなくても描画は止めない）。
- 測るのに各動画を 1 回読むので、hooks を使う描画はその分遅くなる。

非公開の制作の `stats()`（各クリップの足元と中心）と比べた（2026-11-09）。
- 同じ測定を入れたときの式: 制作の読み手とキーで測った毎コマの値を公開版の式に入れると、制作の結果と同じになった（制作が 0.1 画素に丸めた分の差だけ）。合成の配列でも、テストで写した制作の式と同じ。
- 通しの差: ユーザーの緑背景の実素材（1080×1920、960 コマ）を公開版の読み手とキー（spec の既定）で測ると、足元の列の差は平均 3.4・最大 6.0 画素、`cx_med` は 2 画素だった。キー（制作のキーは小さな塊を消す）と、BT.709 の読み方の違いによる。

### 層の静止画

`mv dance stills spec.json --times … --layers` は、時刻ごとに 1 列で「完成・stage・人物のアルファ・後ろの字・前の字」を並べる。

### 外部レンダラの約束

`src/mv_pipeline/dance/dance_timeline.schema.json` が `timeline.json` の形（0.1 と 0.2）。独自のレンダラで仕上げる場合も、この形式で timeline を書けば `mv gate check` で検査できる。知らない欄は無視する。

## run

```
<out>/                          既定は <spec のディレクトリ>/dance_runs/
├── <UTC run_id>/               spec.json manifest.json render.log dance.mp4 timeline.json
├── index.json                  key → run_id（成功した run だけ）
├── acceptance.jsonl            人間の判定（追記のみ）
└── stills-<key8>.png
```

run の形・manifest・キャッシュ・`technical_pass`・`accept` は fx と同じ（`docs/fx.md` の「run と再現性」）。キャッシュ key は正規化 spec・全素材の sha256（`beats` のファイルも含む）・エンジン指紋（dance と fx の全ファイル）・区間・出力寸法・ライブラリ版から作る。

**timeline.json**（`dance-timeline/0.2`。0.1 の spec からは `dance-timeline/0.1`）は、描いた区間（`interval`）、字の出入りと実際の箱（`box_px`・正規化した `box`）、clip の区間、切れ目（`seams`）、フラッシュの時刻、`ui_regions` の記録。描画の入力ではなく、`mv gate check` に渡すためのもの。0.2 は次を足す:

- `beats`: `{"beats": […], "downbeats": […]}`（spec にあれば）。
- `layers`: `[{name, z, exclusive, sung, follow_camera}]`。
- `lyrics[]`: `sung`・`reveal`・`exclusive`・`words`（`[{text, t, row, box}]`）。`fade_in_s`・`fade_out_s` は enter・exit を含めた出入りの長さ。
- `lyric_fade`: `"inside"`。行は `end` の直前まで見え、`fade_out_s` は `end` の前で終わる（`mv gate check` の字の重なりはこれを読んで `[start, end]` で数える。無い timeline は `after` = `[start, end + fade_out_s]`）。
- `clips[]`: `kind`（video/image）・`transition_in`・`beat_exempt`。
- `seam_info`: `[{t, from, to, kind: cut|blend|dissolve, beat_exempt, align}]`（from・to は clip の番号。align は次の clip の `align`）。
- `frames`: 描画区間の毎フレームの記録。`frames_fields` で列名を宣言した配列の配列。列は t・clip・source_frame・blend・zoom・cx・cy・rotate・shake_x・shake_y・blur で、fit を使うときは配置行列（元→画面、カメラの前の 2×3）p_a・p_b・p_tx・p_c・p_d・p_ty も。
- `analysis`: クリップごとの `{path, sha256, p90_height, median_cx, ground, file}`。
- `camera_seams`: seams の設定（pre_s・post_s・zoom・rotate・pivot・blur）。`accents`: 解決した時刻と設定。
- `flash_info`: `[{t, strength, decay_s, mode, beat_exempt, align}]`。
- `audio`: `{fit, end_s}`。
- `finished`: 仕上げの記録 `{hooks: {path, sha256}}` か null。

## コマンド

| コマンド | 内容 |
|---|---|
| `mv dance demo DIR [--width 540 --height 960 --duration 6 --fps 24] [--schema 0.3\|0.2\|0.1]` | 合成素材と spec.json を作る |
| `mv dance render SPEC [--start S] [--duration D] [--out DIR] [--scale 1.0] [--force] [--quiet] [--hooks FILE]` | dance.mp4 と timeline.json を run として出す。約 5% ごとに stderr へ進み具合（コマ数・経過時間・残りの見込み）を出す（`--quiet` で消す） |
| `mv dance plan SPEC` | 解決した時刻の一覧（clips・retime・replace_frames・つなぎ・accents・seams・フラッシュ・歌詞）を表で出す。判定はしない |
| `mv dance stills SPEC --times T… [--out DIR] [--scale 1.0] [--guides [all]] [--layers] [--hooks FILE]` | 指定時刻を 4 列に並べた静止画シート。`--guides` はその時刻に見えている行の箱（層名つき）と UI 枠、fit の地面の線、seams の pivot を重ねる。`--guides all` は全行の箱 |
| `mv dance accept RUN_DIR --decision accepted\|rejected\|hold --by NAME [--note TEXT]` | 人間の判定を追記する |

## 限界

- 緑抜きは 1 色のクロマキーだけ。髪の細い毛や半透明の布は `tolerance`・`softness` で調整する。縁の質は素材次第。アニメ調の生成動画 1 本では `tolerance` 0.22・`softness` 0.12・`spill` 1.0・`choke_px` 1.0・`feather_px` 1.0 で、抜き出したコマを見た範囲では使えた。実写は確かめていない。
- 人物の明るさや色をステージの光に合わせる処理は無い（人物が浮いて見えることがある）。
- 歌詞の折り返しは自動ではない（`row` で明示する）。横書きだけ。
- 動画の読み出しは前方向だけで、戻るときは先頭から数え直す。本番の描画は順に読むので速いが、`stills` で後ろの時刻から描くと遅い。
- 動画のフレームは OpenCV で復号するが、色の読み方は環境で違う。Windows の OpenCV 5.0.0 は色のタグを見ず、いつも BT.601 の行列で RGB に直す（合成の単色動画の実測で、BT.709 の緑 (30, 220, 60) が (44, 248, 64) になる）。Linux の CI の同じ版は (28, 218, 59) と正しく読んだ。どの環境でも同じ色で読めるよう、タグが `bt709` の動画は ffmpeg に行列を指定して読む（Windows の実測で誤差 1 以内。時間は OpenCV の 1.5 倍ほど）。タグが `smpte170m`・`bt470bg` の動画は OpenCV のままで正しい。タグが無い動画は行列を見分けられないので BT.601 として読み、`warnings` に残す。BT.709 で作ったタグなしの動画は色がずれるので、タグを付けてから渡す。色の範囲は `tv`・`pc` ともタグに従う。
- 生成動画は 24 コマの絵を 30fps に水増ししていることがある（5 コマに 1 コマが同じ絵）。`speed` を変えるときは先に重複を除く（ffmpeg の `decimate=cycle=5` など）。
