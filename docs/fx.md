# fx（演出エンジン・実験的）

1 枚絵（と任意の深度・マスク）から、**カメラ・空間レイヤー・局所光**で動画を作る。同じ素材・同じ区間で
「現行版 / レイヤー / 局所光 / 両方」を並べて比べるための道具で、CLI と設定ファイルだけで動く。

- 決定論: 同じ spec・seed・環境（ライブラリ版）なら同じフレーム。LLM もネットワークも使わない。
- **実験的**: 設定スキーマ `fx-shot/0.1` は `contracts/` の外（`src/mv_pipeline/fx/`）にある。`contracts/` への昇格はユーザー承認が要る。
- **採点しない**: `technical_pass` は構造検査、`accept` は人間の判定。fx は絵の良し悪しを自動判定しない。
- 既存の `mv init / render / verify …`（Remotion 経路）は変えない。fx は別系統で、`project` 引数も `project_lock` も持たない。

## 準備

```sh
uv sync --locked --extra fx        # opencv-python-headless を追加（fx 系コマンドだけが使う）
uv sync --locked --extra analysis --extra fx    # analysis も使うなら両方（uv sync は指定外の extra を外す）
```

fx が要るもの: opencv（上記 extra）、ffmpeg（PATH のもの、無ければ imageio-ffmpeg 同梱版。**ffprobe は不要**）。
extra が無いと fx 系コマンドは「`uv sync --locked --extra fx` を実行してください」で止まる（`fx accept` と `fx legacy` は opencv 不要）。

## 5 分で試す（合成素材）

```sh
uv run mv fx demo projects/fxdemo                     # 素材と spec.json を作る（960x540・10 秒・24fps）
uv run mv fx stills projects/fxdemo/spec.json --times 0 5 9.5       # 行=時刻・列=版の静止画シート（速い）
uv run mv fx render projects/fxdemo/spec.json         # 4 版の MP4 + 2x2 グリッド。projects/fxdemo/fx_runs/<run_id>/
uv run mv fx accept projects/fxdemo/fx_runs/<run_id> --variant both --decision accepted --by NAME
```

デモは暗い夜景に白い炎を掲げた人物。炎の光が人物の顔・袖・地面に当たり、霧は人物の後ろ、火の粉は前、カメラはゆっくり寄る。
素材はすべてプログラムで描いた合成物（実作品の素材は使わない）。`projects/` は `.gitignore` 済みなので、素材も動画も Git に入らない。

## 版（variants）

| 版 | 内容 |
|---|---|
| `base` | カメラ + 深度視差のみ。実制作の z/c/par 型ショット（現行版）に相当 |
| `layers` | base + plane（マスクで切った人物など）と effects（霧・火の粉）。光なし |
| `light` | base + 局所光。plane / effects なし |
| `both` | 全部 |

spec に `layers`（plane も effects も空）や `lights` が無ければ、その機能はどの版でも無効。版名と MP4 は残り（グリッドの枠も固定）、
manifest に `"empty": true` と理由が記録され、グリッドのラベルに `[no-op]` が付く（base と同じ映像であることを読み違えないため）。

## spec（fx-shot/0.1）

素材パスは **spec ファイルのあるディレクトリ基準の相対パス**（`io.safe_path` で逸脱を拒否）。検証は JSON Schema 2020-12
（`src/mv_pipeline/fx/fx_shot.schema.json`）。トップレベルは未知の鍵を拒否するが、**`x_` で始まる鍵はどの階層でも許す**（作品ごとの試行用。描画には使われない）。

```json
{
  "schema_version": "fx-shot/0.1",
  "output": {"width": 1920, "height": 1080, "fps": 24, "crf": 18},
  "duration_s": 10.0, "seed": 7,
  "audio": {"path": "audio.wav", "offset_s": 0.0},
  "assets": {"base": "base.png", "depth": {"path": "depth.png", "near": "white"},
             "masks": {"subject": "mask_subject.png", "ground": {"path": "mask_ground.png", "feather_px": 6}}},
  "camera": {"keys": [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5]},
                      {"t": 10.0, "zoom": 1.08, "center": [0.52, 0.48], "ease": "in_out"}],
             "parallax_px": 12, "parallax_dir": [1, 0]},
  "layers": {"background": {"parallax": 0.4, "inpaint_px": 8},
             "planes": [{"id": "subject", "mask": "subject", "z": 2, "parallax": 1.0, "drift_px": [0, 0]}],
             "effects": [{"id": "fog", "type": "fog", "z": 1, "color": [170, 175, 200], "opacity": 0.35, "scale": 0.25, "drift": [0.01, 0.0], "parallax": 0.6},
                         {"id": "embers", "type": "particles", "z": 3, "count": 140, "color": [255, 170, 80], "size_px": [1.5, 4.0], "velocity": [0.0, -0.06], "flicker": 0.4, "parallax": 1.3}]},
  "lights": [{"id": "white_flame", "type": "point", "position": [0.64, 0.42], "color": [230, 236, 255], "radius": 0.45,
              "intensity": [{"t": 0.0, "v": 0.0}, {"t": 1.0, "v": 1.2}, {"t": 10.0, "v": 0.9}],
              "flicker": {"amount": 0.12, "hz": 5.0}, "emitter": {"size": 0.03, "glow": 0.6},
              "receivers": [{"mask": "subject", "gain": 1.0, "rim": 0.5}, {"mask": "ground", "gain": 0.6, "reflection": 0.5}],
              "depth": 0.7, "depth_falloff": 0.25}]
}
```

- 必須は `schema_version`・`output.width/height`・`duration_s`・`assets.base`。他は任意（未指定の機能は無効）。出力寸法は偶数。
- 座標はすべて**基準画像の正規化座標**（0..1）。カメラが動いても光と対象は一緒に動く。出力と縦横比が違うときは cover で中央に合わせて切った後の画面（zoom 1 で見える範囲）が 0..1 で、切った割合は warnings に残る（黙って切らない）。
- 時間は秒で、**ショット時刻**（spec の時間軸）。`--start` で区間を切って描いても、同じ時刻は同じ絵になる。色は sRGB 0〜255。`ease` は `linear | in | out | in_out`。
- マスクとデプスは基準画像と同じ縦横比（誤差 1%）。RGBA のマスクは alpha が一定でなければ alpha、一定なら輝度を使う（切り抜き PNG をそのまま使える）。深度は 2〜98 パーセンタイルで正規化（外れ値で潰れない）。`near` は白が手前か黒が手前か。
- 参照切れ・id 重複・キーの `t` が昇順でない・奇数の出力寸法は拒否。`depth` を指定したのに `assets.depth` が無い等は無視して warnings に記録する。

## 描画モデル

合成はすべて線形光の float32 で行い、最後に 1 回だけ sRGB の uint8 へ戻す。

**カメラ** — keys を補間して zoom・center を得る（区間の曲線は到達側キーの `ease`）。plane ごとに視差 p を取り、
`zoom_p = z0 + (zoom(t) − z0)·p`、`center_p = c0 + (center(t) − c0)·p`（z0・c0 は最初のキー）、さらに `drift_px × t`。最初のキーの状態では全 plane が重なる。
深度視差（`parallax_px > 0` かつ深度あり）は `parallax_px × (W/1920) × s(t) × (D − 0.5) × parallax_dir` の変位で、s(t) は −0.5→+0.5 を in_out で。

**レイヤー** — background は全 plane マスクの和集合（`inpaint_px` で膨張）を cv2.inpaint（TELEA）で埋めた板。plane は基準画像 × マスク。
z の小さい順に背面から合成し、effects も z で挟む（霧 z=1 は人物 z=2 の後ろ、火の粉 z=3 は前）。
霧は seed から作った周期ノイズを drift とカメラ移動でずらして重ねる。火の粉は位置を `(x0 + velocity·t + カメラ移動·parallax) mod 1` でフレームごとに状態なしで計算し、加算で描く。

**局所光**（基準画像空間・カメラの前） — 光の場 `L = color × I(t) × flicker(t) × falloff(r)`。`r` は光源からの距離を画像の幅で割ったもの、
`falloff = 1/(1+(r/(0.25R))²) × clamp(1−(r/R)⁴, 0, 1)²`（`r ≥ R` で厳密に 0）。受光マスク `M` の内側だけが照らされる。

| 項 | 内容 |
|---|---|
| diffuse | `基準画像 × L × gain × M × 4.0`（面の明るさに比例するので全体の明るさ変更にならない） |
| rim | マスクの内側の輪郭帯 × `L × rim` × 光源の向きの重み。輪郭に光が回る |
| reflection | 縦に潰した距離（dy×0.35）・半径 0.5R の鋭い falloff。地面の照り返し |
| emitter | 光源位置のガウスの光点と裾（半径 4×size で厳密に 0） |
| depth | `exp(−((D−depth)/depth_falloff)²)` を受光に掛ける（光と同じ奥行きの面だけ） |
| flicker | `1 + amount × (0.6 sin(2π·hz·t+φ1) + 0.4 sin(2π·hz·2.37·t+φ2))`、φ は seed から。負にしない |

最後に 0 未満を切り、0.8 以上を緩やかに圧縮する（ソフトニー `x − d²/(d+0.2)`、d = max(x−0.8, 0)）。
**局所性の保証**: 受光マスクが 0 で emitter の影響半径の外の画素は、光なしの版と**ビット単位で同じ**値になる。

**plane と背景への振り分け**（layers がある版） — plane は**すべての受光**を受ける。背景は plane のマスクを除いた受光だけを受ける
（人物の形の光が、視差でのぞいた背景に残らない）。このため、**受光マスクどうしが重なる画素は両方の受光が足される**
（例: 地面マスクが人物の足元に重なると、光の版では人物の足元が二重に照らされる）。マスクは重ねない作り方を勧める
（デモの地面マスクは人物の輪郭の内側で 1→0 に落としてある）。

## 限界

- 単一画像からの照明は**画像空間の近似**。面の向き・材質・影は推定しない（隠れた面は光らず、他の物が落とす影は出ない）。
  rim の向き重みと reflection は見た目のための近似で、物理的に正しい反射ではない。
- 背景の穴埋めは TELEA の単純な補間。人物の背後が複雑な絵だと、**大きな移動（視差が大きい・カメラを大きく動かす）で破綻が見える**。
  まず `stills` で端の時刻（移動が最大の時刻）を確認する。
- 光の影響範囲は半径 R で切れる。受光マスクの質（縁の粗さ・欠け）がそのまま絵に出る。
- 動作確認は合成素材のみ。実作品の素材（マスクの品質・深度の質）での挙動は、素材のある環境で実測すること。
- 性能の目安（4 コア・実測）: 960x540 の `both` は 1 枚約 0.1 秒、デモ（10 秒・24fps）の 4 版 + グリッドで約 1 分。1920x1080 の `both` は約 0.36 秒/枚。

## マスクの用意

fx はセグメンテーションモデルを持たない。マスクは次のどれかで用意する（基準画像と同じ縦横比の PNG）。

- 手描き（ペイントソフトで人物を塗る。縁は `feather_px` でぼかす）
- rembg など既存の切り抜きツールの出力（RGBA の alpha をそのまま使える）
- 既存の切り抜き素材（別レイヤーのアルファ）
- 深度のしきい値（手前の物体だけを白にする。縁が粗くなりやすいので `feather_px` を大きめに）

深度画像は 8/16bit の PNG。外部の深度推定ツールの出力をそのまま置ける。

## 実作品の素材での回し方

素材のある環境（Cowork や PC）で、**spec を素材の隣に置いて**実行する。素材・spec・出力は Git に入れない（このリポジトリは public）。

```sh
uv run mv fx stills  /path/to/shot/spec.json --times 0 5 9.5          # まず静止画で当たりを確認
uv run mv fx render  /path/to/shot/spec.json --start 0 --duration 10  # 4 版 + グリッド
uv run mv fx legacy  --renderer /path/to/render_v2.py --config /path/to/config.json --start 0 --duration 10   # 現行版
```

出力先は既定で spec のあるディレクトリの `fx_runs/`。`fx legacy` も同じ `--out` を指定すると、現行版と fx の run が同じ場所に並ぶ。

## コマンド

| コマンド | 内容 |
|---|---|
| `mv fx demo DIR [--width 960 --height 540 --duration 10 --fps 24]` | 合成素材と spec.json を作る |
| `mv fx render SPEC [--variants base,layers,light,both] [--start S] [--duration D] [--out DIR] [--scale 1.0] [--no-grid] [--force]` | 版ごとの MP4 と 2×2 グリッドを run として出す |
| `mv fx stills SPEC --times T… [--variants …] [--out DIR] [--scale 1.0]` | 行=時刻・列=版の静止画シート `stills-<key8>.png`（圧縮なし） |
| `mv fx legacy --renderer PATH --config CFG --duration D [--start S] [--python EXE] [--width W --height H --fps F] [--out DIR] [--timeout SEC] [--cwd DIR]` | 外部の実制作レンダラを呼んで run として記録する |
| `mv fx accept RUN_DIR --variant V --decision accepted\|rejected\|hold --by NAME [--note TEXT]` | 人間の判定を `acceptance.jsonl` に追記する |

stdout は短い JSON、進捗は stderr に 1 版 1 行、詳細は run の `render.log`。エラーは `Error: …`（原因と直し方）で終了コード 1。
`--scale` は出力寸法を偶数に丸めて縮尺する（下見用。key に入る）。`--start/--duration` はショットの長さに収まること。

## run と再現性

```
<out>/                          既定は <spec のディレクトリ>/fx_runs/
├── <UTC run_id>/               例: 20261024T120000Z-1a2b3c4d（末尾 8 桁は key の先頭）
│   ├── spec.json               spec の写し（素材パスは spec の元ディレクトリ基準。素材の記録は manifest の assets）
│   ├── manifest.json  render.log
│   ├── base.mp4  layers.mp4  light.mp4  both.mp4  grid.mp4
├── index.json                  key → run_id（成功した run だけ）
├── acceptance.jsonl            人間の判定（追記のみ）
└── stills-<key8>.png
```

**manifest**（`fx-run/0.1`）: spec の sha256（元ファイルと正規化形の両方）、全素材の sha256・寸法、engine（版 + **fx パッケージの全ファイル（.py と schema）の sha256 から作る指紋**）、
ライブラリ版（python・numpy・opencv・Pillow・ffmpeg）、seed、区間（start・duration・fps・frames）、出力寸法・scale、版ごとの
出力パス・frames・sha256・technical_pass・検査内容・有効な機能・`empty`、warnings、status。

**status** は `running → succeeded | failed`。失敗（例外・技術検査不合格・Ctrl-C）しても過去の成功 run と `index.json` は変わらず、
失敗した run は原因つきで残る。プロセスが強制終了して `running` のまま残った run は、次に同じ出力先を使うときに `failed`（interrupted）へ直す。
同じ出力先を同時に使うことはできない（2 つ目は「already running」で止まる）。

**キャッシュ**: key = sha256(正規化 spec + 全素材の sha256 + engine 指紋 + 版の集合 + 区間 + 出力寸法・scale + グリッド有無 + ライブラリ版)。
`index.json` にあり、出力が揃って sha256 が manifest と一致し technical_pass の run があれば、再描画せず `reused: true` を返す。`--force` で作り直す（新しい run になり、index は新しい方を指す）。
**描画に影響する入力は必ず key に入れる**（古い絵を返さないため）。`x_` の鍵も key に入る（コメントだけの変更でも作り直しになる、安全側）。

**technical_pass** — ffmpeg だけで行う構造検査（ffprobe 不要）: 全復号でエラーが無い・寸法・fps・フレーム数（= `round(duration×fps)`）が一致・
色タグが `bt709 / tv / yuv420p`・音声ありなら尺が許容内（1 フレーム + AAC 1 パケット）。**絵の良し悪し・同期の精度・作品としての採用は判定しない。**
不合格の run は `failed` になり、index に入らない。

## legacy 接続

実制作レンダラ（例: 手元の `render_v2.py CONFIG seg START END out.mp4`）を**リポジトリに入れずに**呼ぶ。
argv は `[python, renderer, config, "seg", start, end, out]`（`end = start + duration`、`out` は run 内の一時名）。

- 記録: renderer と config の sha256、argv、終了コード、所要時間、作業ディレクトリ（既定はレンダラのあるディレクトリ。`--cwd` で変更）。
- ログ: 子の標準出力・標準エラーを描画中に逐次 `legacy.log` へ書く（子の Python は `PYTHONUNBUFFERED=1`）。
- 検査: フレーム数は `round(D×fps)` から**最大 2 少ないまで**許容（実務の `-shortest`）、多いのは不合格。寸法・fps は `--width/--height/--fps` を指定したときだけ照合
  （`--fps` 未指定なら出力の fps から期待枚数を数える）。全復号でエラーが無いこと。音声は検査しない。
- `--timeout` 既定は `max(600, 120×duration)` 秒。超えたら子プロセスごと止めて `failed`。
- **キャッシュしない**: 設定が参照する素材などレンダラの隠れた入力を fx は知らないため、毎回実行する（`index.json` に入らない）。
- 失敗しても `legacy.mp4` は作らず、過去の run は変えない。run の版名は `legacy`（`fx accept --variant legacy` で判定できる）。

## accept

`acceptance.jsonl` の 1 行 = `{at(UTC), run_id, variant, decision, by, note, video_sha256, key}`。**追記のみ**で、既存の行も manifest も変えない。
判定は特定の出力ファイルへのもの（sha256 を記録し、出力が manifest と違えば拒否）。`accepted` は技術検査に通った版にだけ付けられる
（`rejected` / `hold` はいつでも付けられる）。`technical_pass` とは別物で、`accepted` は人間の判断。

## 出力の色

`libx264 -crf <crf> -preset medium`、`scale=out_color_matrix=bt709:out_range=tv,format=yuv420p`（`accurate_rnd+full_chroma_int`）、
タグは `bt709 / bt709 / bt709 / tv`。音声は AAC 192k・48kHz・ステレオ、頭 0.02 秒・終わり 0.25 秒のフェード、`offset_s + start` から区間の長さだけ切る。
フレーム数は `round(duration×fps)` ちょうど（`-shortest` は使わない）。グリッドは版の MP4 を同じ書式で復号して並べ直したもので、
タイルは W/2×H/2（版ごとに枠が固定: base=左上 / layers=右上 / light=左下 / both=右下）。

## CI

`uv sync --locked --extra fx` の後、pytest と、合成素材（320x180・2 秒）の `fx demo → fx render → technical_pass` を回す。
これは構造検査であり、見た目や実素材での挙動の保証ではない。
