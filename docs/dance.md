# dance（ダンス合成・実験的）

緑背景の人物動画をステージの画像に合成し、カメラと歌詞を載せた動画を作る**下書きエンジン**。下書きは構造の決定（時刻・人物の配置とつながり・カメラ・歌詞・切れ目）と、検査できる記録（`timeline.json`）のこと。CLI と設定ファイルだけで動く。縦・横どちらの出力でも動く（UI 枠の既定は縦だけ）。

- 決定論: 同じ spec・素材・環境（ライブラリ版）なら同じフレーム。LLM もネットワークも使わない。
- **実験的**: 設定スキーマ `dance-shot/0.2`（`dance-shot/0.1` も読める）は `contracts/` の外（`src/mv_pipeline/dance/`）にある。`contracts/` への昇格はユーザー承認が要る。
- **汎用の部品だけ**: 作品固有の値はコードに持たず、spec に書く。作品ごとの spec・素材・出力は Git に入れない。
- **採点しない**: `technical_pass` は構造検査、`accept` は人間の判定。絵の良し悪しや時刻の合否は `mv dance` では判定しない（書き出し前の機械的な検査は `mv gate check` で行う。run の `timeline.json` を読む。`docs/gate.md`）。
- 動作確認は**プログラムで描いた合成素材のみ**。実素材（緑抜きの質・字の見え方・動画のコーデック）での挙動は、素材のある環境で実測すること。

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

`mv dance demo --schema 0.1` は 0.1 の spec と素材を作る（互換の確認用）。

## spec（dance-shot/0.2）

素材パスは **spec ファイルのあるディレクトリ基準の相対パス**（`io.safe_path` で逸脱を拒否）。検証は JSON Schema 2020-12（`src/mv_pipeline/dance/dance_shot.schema.json`）。未知の鍵は拒否し、`x_` で始まる鍵はどの階層でも許す（描画には使われないが、キャッシュ key には入る）。

```json
{
  "schema_version": "dance-shot/0.2",
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
  "lyrics": {
    "font": "font.ttf", "font_index": 0,
    "ui_regions": [{"name": "right", "box": [0.86, 0.45, 1.0, 0.88]}],
    "back": {"box": [0.04, 0.18, 0.96, 0.44], "size_px": 180, "color": [255, 255, 255], "accent_color": [255, 90, 170], "opacity": 0.92},
    "band": {"box": [0.04, 0.07, 0.96, 0.15], "size_px": 64, "stroke_px": 6, "stroke_color": [12, 10, 24]},
    "lines": [
      {"layer": "back", "text": "LIGHT", "start": 5.0, "end": 8.0, "words": [{"text": "LI", "t": 5.5}, {"text": "GHT", "t": 6.0}]},
      {"layer": "band", "text": "上の帯の字", "start": 5.0, "end": 8.0}
    ]
  }
}
```

- 必須は `schema_version`・`output.width/height`・`duration_s`・`stage`・`performer.clips`。他は任意。出力寸法は偶数。
- 座標はすべて**出力画面の正規化座標** 0..1。時間は秒で、**ショット時刻**（`--start` で区間を切って描いても同じ時刻は同じ絵）。色は sRGB 0〜255。
- `seed`（既定 0）: 乱数の種。
- `performer.clips`: start の昇順で、区間は重ねない。元動画のフレームは `(source_start + (t − start) × speed) × 元動画の fps` の切り捨て。`source_start` は負でもよく、0 より前は先頭のフレームを保つ。動画の末尾を越えたら最後のフレームを保つ。人物のいない区間は warnings に残る。同じ `path` を何度使ってもよい。`path` に静止画（png/jpg/webp）を書くと 1 枚を保つ（`speed` は無視。アルファは見ないので、緑抜きの背景にしておく）。
- `placement`: 元動画の点 `pivot`（元動画の幅・高さに対する割合）を画面の点 `anchor` に合わせる。`height` は元動画の高さが画面の高さの何倍か（全身が映る動画なら、人物の身長の割合ではない）。`flip_x` は全体の左右反転（元動画の点 `pivot` は反転しても `anchor` に来る）。
- `clips[].placement`（`fit` の後に掛ける）: `offset`（[dx, dy]。出力の正規化座標）、`scale`（倍率。`anchor` のまわり）、`flip_x`（全体の `flip_x` と重ねると元に戻る）。
- `clips[].transition_in`: `{"type": "cut"}`（既定）か `{"type": "blend", "frames": 3}`。blend は clip の start から frames コマの間、前のクリップの再生を続け（素材の終わりを越えたら最後のフレームを保つ）、両方をそれぞれの配置で置いて、前乗算の色とアルファを smoothstep で重ねる。前のクリップと隙間なく接していること。
- `clips[].beat_exempt`: この切れ目は意図して拍から外している（`mv gate check` が対象外として数える）。
- `stage`: 画像（png/jpg/webp）か動画。出力の縦横比に cover で中央に合わせる（余る側を切る。縮尺の計算は 1 回だけ）。動画は `source_start`（秒）から再生し、`loop`（既定 true）で折り返す。false なら最後のフレームを保つ。読み出しは人物と同じく前方向だけ。`parallax`（既定 1.0）はステージだけの視差（下の「カメラ」）。
- ASCII 以外の歌詞には `lyrics.font` が要る（フォントファイルを spec の隣に置く。素材と同じく sha256 が記録される）。無ければ Pillow の既定の欧文フォントを使う。

### 拍と時刻の参照

`beats` は次のどちらかで書く。どちらも正規化で `beats`・`downbeats` の配列になる。

- `{"path": "beats.json"}`: `beats`（または `beats_s`）と任意の `downbeats` の配列。gate・`mv analyze` と同じ形。素材として sha256 を manifest に残す。
- `{"bpm": 120, "offset_s": 0.0, "beats_per_bar": 4, "count": 512, "downbeat_phase": 0}`

時刻の欄（clips の start/end、`camera.keys` の t、seams・accents・flashes の t、歌詞の start/end と語の t）は、秒の数値か次の参照で書ける。読み込み時に秒へ解決するので、`spec.data` には秒だけが入る。`beats` が無いのに参照があればエラー。解決した結果は `mv dance plan` で見られる。

- `{"beat": 12.5}`: 拍の番号（0 始まり）。小数は前後の拍の間を線形補間し、格子の外は端の間隔で外挿する。
- `{"bar": 3, "beat": 1.5}`: 小節頭の番号 + 拍数。
- どちらも `"offset_s": -0.1` を足せる（字を歌の少し前に出す、など）。解決した時刻が負ならエラー。

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
- この解析は人物を置くための**合成の入力**で、評価にも `technical_pass` にも使わない。

### 音声

- `fit`: `"strict"`（既定。足りなければ `technical_pass` で止まる）/ `"pad"`（足りない分を無音で足して、描画区間ちょうどにする）。
- `end_s`（ショット時刻）: 曲の終わり。ここで `fade_out_s` を終え、以降は無音にする。
- `fade_in_s`（既定 0.02）・`fade_out_s`（既定 0.25）。

## 描画モデル

重ね順は **ステージ → 後ろの字（back）→ 人物 → フラッシュ → 帯の字（band）**。合成は線形光の float32 で行い、最後に 1 回だけ sRGB の uint8 へ戻す。

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

**縦の歌詞** — 字は画面に固定で、カメラに乗らない（時刻によらず箱が 1 つに決まり、UI 枠を確かめやすい）。

- `back` は人物の後ろの大きな字、`band` は上の帯の字。
- 字は `size_px`（幅 1080 の画面での値）から、箱に収まるまで 5% ずつ縮め、箱の中央に置く。最小は幅 1080 で 8px（画面の幅に比例）で、それでも収まらない行は warnings に残し、`timeline.json` の `overflow` を true にする（`box_px` は画面内に切った値なので、欠けはこの印で分かる）。
- 強調は**色だけ**（大きさも位置も変えない）。`words` の `t` が来たら語が `accent_color` に変わる。`t` が無ければ最初から、`accent: false` なら強調しない。`words` をつなげた文字列は `text` と一致させる。
- `start` の最初のフレームから見え、`fade_in_s` / `fade_out_s` で出入りする（既定 0.04 / 0.08 秒）。
- 縦画面の `ui_regions` の既定値は上・右・下の操作部品のおおよその範囲（目安）。字のインクが重なると warnings に残す（描画は止めない）。横画面の既定は空。

**フラッシュ** — `t` から `strength × exp(−経過/decay_s)` だけ効く。帯の字はその上に載るので白飛びしない。

- `mode: "lerp_linear"`（既定。0.1 と同じ）: 線形光で色へ寄せる。
- `mode: "add_display"`: 表示用の値に strength·exp(−経過/decay)·color を足す（線形光のままではなく、sRGB に符号化した値に足す）。
- **同じ値でも `lerp_linear` は暗部で強く見える**（線形光の補間は暗部の持ち上がりが表示空間で足す方式の 2〜3 倍になる。計算値）。旧来の実制作レンダラと同じ見え方が欲しければ `add_display` を使う。
- `beat_exempt`: 意図して拍から外している（`mv gate check` が対象外として数える）。

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
- `clips[]`: `kind`（video/image）・`transition_in`・`beat_exempt`。
- `seam_info`: `[{t, from, to, kind: cut|blend, beat_exempt}]`（from・to は clip の番号）。
- `frames`: 描画区間の毎フレームの記録。`frames_fields` で列名を宣言した配列の配列。列は t・clip・source_frame・blend・zoom・cx・cy・rotate・shake_x・shake_y・blur で、fit を使うときは配置行列（元→画面、カメラの前の 2×3）p_a・p_b・p_tx・p_c・p_d・p_ty も。
- `analysis`: クリップごとの `{path, sha256, p90_height, median_cx, ground, file}`。
- `camera_seams`: seams の設定（pre_s・post_s・zoom・rotate・pivot・blur）。`accents`: 解決した時刻と設定。
- `flash_info`: `[{t, strength, decay_s, mode, beat_exempt}]`。
- `audio`: `{fit, end_s}`。
- `finished`: 仕上げの記録か null。

## コマンド

| コマンド | 内容 |
|---|---|
| `mv dance demo DIR [--width 540 --height 960 --duration 6 --fps 24] [--schema 0.2\|0.1]` | 合成素材と spec.json を作る |
| `mv dance render SPEC [--start S] [--duration D] [--out DIR] [--scale 1.0] [--force] [--quiet]` | dance.mp4 と timeline.json を run として出す。約 5% ごとに stderr へ進み具合（コマ数・経過時間・残りの見込み）を出す（`--quiet` で消す） |
| `mv dance plan SPEC` | 解決した時刻の一覧（clips・つなぎ・accents・seams・フラッシュ・歌詞）を表で出す。判定はしない |
| `mv dance stills SPEC --times T… [--out DIR] [--scale 1.0] [--guides [all]]` | 指定時刻を 4 列に並べた静止画シート。`--guides` はその時刻に見えている行の箱（層名つき）と UI 枠、fit の地面の線、seams の pivot を重ねる。`--guides all` は全行の箱 |
| `mv dance accept RUN_DIR --decision accepted\|rejected\|hold --by NAME [--note TEXT]` | 人間の判定を追記する |

## 限界

- 緑抜きは 1 色のクロマキーだけ。髪の細い毛や半透明の布は `tolerance`・`softness` で調整する。縁の質は素材次第で、合成素材では実写やアニメ調の動画の難しさは確かめていない。
- 人物の明るさや色をステージの光に合わせる処理は無い（人物が浮いて見えることがある）。
- 歌詞は 1 行ずつ（折り返しなし）で、横書きだけ。
- 動画の読み出しは前方向だけで、戻るときは先頭から数え直す。本番の描画は順に読むので速いが、`stills` で後ろの時刻から描くと遅い。
- 動画のフレームは OpenCV で復号する。色の範囲や行列の扱いはコーデックのタグに依存するので、実素材でキー色の値を確かめる。
