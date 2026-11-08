# fx（演出エンジン・実験的）

1 枚絵（と任意の深度・マスク）から、**カメラ・空間レイヤー・局所光**で動画を作る。同じ素材・同じ区間で
「現行版 / レイヤー / 局所光 / 両方」を並べて比べるための道具で、CLI と設定ファイルだけで動く。
仕上げに、実制作と同じ式の**色調整・周辺減光・粒状感（look）**を全版に掛けられる（任意。[look](#look色調整周辺減光粒状感)）。

- 決定論: 同じ spec・seed・環境（ライブラリ版）なら同じフレーム。LLM もネットワークも使わない。
- **実験的**: 設定スキーマ `fx-shot/0.2`（`fx-shot/0.1` の spec もそのまま読める）は `contracts/` の外（`src/mv_pipeline/fx/`）にある。`contracts/` への昇格はユーザー承認が要る。
- **採点しない**: `technical_pass` は構造検査、`accept` は人間の判定。fx は絵の良し悪しを自動判定しない。
- 既存の `mv init / render / verify …`（Remotion 経路）は変えない。fx は別系統で、`project` 引数も `project_lock` も持たない。

## 準備

```sh
uv sync --locked --extra fx        # opencv-python-headless を追加（fx 系コマンドだけが使う）
uv sync --locked --extra analysis --extra fx    # analysis も使うなら両方（uv sync は指定外の extra を外す）
```

**開発者へ**: fx のテスト（render / light / layers / runs / compare / demo ほか）は opencv が前提で、`--extra fx` で同期していない環境では
`pytest.importorskip("cv2")` で**黙って skip され、緑のまま終わる**。fx に触れたら必ず `uv sync --locked --extra analysis --extra fx` の環境で `uv run pytest` を回す（CI は `--extra fx`）。

fx が要るもの: opencv（上記 extra）、ffmpeg（PATH のもの、無ければ imageio-ffmpeg 同梱版。**ffprobe は不要**）。
extra が無いと fx 系コマンドは「`uv sync --locked --extra fx` を実行してください」で止まる（`fx accept` と `fx legacy` は opencv 不要）。

## 5 分で試す（合成素材）

```sh
uv run mv fx demo projects/fxdemo                     # 素材と spec.json を作る（960x540・10 秒・24fps）
uv run mv fx stills projects/fxdemo/spec.json --times 0 5 9.5       # 行=時刻・列=版の静止画シート（速い）
uv run mv fx render projects/fxdemo/spec.json         # 4 版の MP4 + 2x2 グリッド。projects/fxdemo/fx_runs/<run_id>/
uv run mv fx accept projects/fxdemo/fx_runs/<run_id> --variant both --decision accepted --by NAME
```

デモは暗い夜景に白い炎を掲げた人物。炎は手の上にあり（`emitter.attach = subject`）、光は対象物ごとに違う当たり方をする: 手・袖・顔・肩の炎側が明るく、人物の反対側や遠い裾は暗いまま、輪郭光が炎側の縁に出て、地面には炎の真下に縦長の反射が出る。霧は人物の後ろ、火の粉は前、カメラはゆっくり寄る。深度の人物の境界は人物マスクの羽根と同じなだらかさ（それより広げない。硬い段差だと深度視差で手や袖の輪郭が二重に見える）。
素材はすべてプログラムで描いた合成物（実作品の素材は使わない）。`projects/` は `.gitignore` 済みなので、素材も動画も Git に入らない。
既定のデモ spec には look を入れない（光とレイヤーの見え方をそのまま確かめるため）。`mv fx demo DIR --look` で夜向けの look の例を足した spec を作れる（素材は同じ）。

## 版（variants）

| 版 | 内容 |
|---|---|
| `base` | カメラ + 深度視差のみ。実制作の z/c/par 型ショット（現行版）に相当 |
| `layers` | base + plane（マスクで切った人物など）と effects（霧・火の粉）。光なし |
| `light` | base + 局所光。plane / effects なし |
| `both` | 全部 |

spec に `layers`（plane も effects も空）や `lights` が無ければ、その機能はどの版でも無効。版名と MP4 は残り（グリッドの枠も固定）、
manifest に `"empty": true` と理由が記録され、グリッドのラベルに `[no-op]` が付く（base と同じ映像であることを読み違えないため）。
spec に `look` があれば**全 4 版に同じ look が掛かる**（版は増えない）。base は「カメラ + 実制作の調色」に近づき、`features` に `look` が載る。

## spec（fx-shot/0.2）

素材パスは **spec ファイルのあるディレクトリ基準の相対パス**（`io.safe_path` で逸脱を拒否）。検証は JSON Schema 2020-12
（`src/mv_pipeline/fx/fx_shot.schema.json`）。トップレベルは未知の鍵を拒否するが、**`x_` で始まる鍵はどの階層でも許す**（作品ごとの試行用。描画には使われない）。

0.2 は 0.1 に深度視差の項目（[深度視差](#深度視差fx-shot02)）を足した版。`"schema_version": "fx-shot/0.1"` の spec は 0.1 の schema（`fx_shot_0_1.schema.json`）で検証してから
0.2 の正規形にそろえ、新しい項目は既定値（0.1 と同じ絵になる値）で埋める。0.1 の spec の絵は 0.2 の実装でもバイト単位で同じ（合成素材と段階 1 の実素材の 80 コマで確認）。
0.2 の項目を 0.1 の spec に書くと、未知の鍵として拒否する。

```json
{
  "schema_version": "fx-shot/0.2",
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
              "flicker": {"amount": 0.12, "hz": 5.0}, "emitter": {"size": 0.03, "glow": 0.6, "attach": "subject"},
              "receivers": [{"mask": "subject", "gain": 1.0, "rim": 0.5}, {"mask": "ground", "gain": 0.6, "reflection": 0.5}],
              "depth": 0.7, "depth_falloff": 0.25}]
}
```

- 必須は `schema_version`・`output.width/height`・`duration_s`・`assets.base`。他は任意（未指定の機能は無効）。出力寸法は偶数。
- `look`（任意）: 最終フレームの色調整・周辺減光・粒状感。0.1 のときに版番号を上げずに足したので、0.1 の spec でも使える。座標規約は上の元画像基準ではなく**出力画面基準**（下の look 節）。無ければ従来とバイト単位で同じ出力。
- 座標（`light.position`・`camera.center`）は**元画像（基準画像）の正規化座標**（0..1）、長さ（`light.radius`・`emitter.size`）は**元画像の幅**が基準。
  出力と縦横比が違うときは cover で中央に合わせて切るが（切った割合は warnings に残る。黙って切らない）、座標は切った後の画面ではなく元画像で解釈して出力画素へ写す。
  そのため同じ spec を 16:9 と 9:16 で描いても、光やカメラの中心は元画像の同じ画素・同じマスクに対する同じ位置に乗る（半径も元画像の幅の同じ割合）。カメラが動いても光と対象は一緒に動く。
- `emitter.attach`（任意）: 光点が載る層。`layers.planes` の id か `"background"`。未指定なら**光点の影響半径（`size`×4）内に不透明部分（alpha > 0.5）を持つ plane のうち z が最も前のもの**
  （z が同じなら宣言の後ろ）、なければ背景。載った層と同じカメラ変換で動き、その層の画素の上に描かれる（自分の層には隠れず、その層より後ろの霧や火の粉にも覆われない。
  手前の plane や z が前の effects には覆われる）。plane に載った光点も `light` 版と同じソフトニーを通すので、`light` 版と `both` 版で芯の見え方がそろう（受光と光点が重なる画素も、受光と合わせて 1 回のソフトニーで作る。二重に掛けない）。`"background"` は予約語で plane の id に使えない。
- 時間は秒で、**ショット時刻**（spec の時間軸）。`--start` で区間を切って描いても、同じ時刻は同じ絵になる。色は sRGB 0〜255。`ease` は `linear | in | out | in_out`。
- マスクとデプスは基準画像と同じ縦横比（誤差 1%）。RGBA のマスクは alpha が一定でなければ alpha、一定なら輝度を使う（切り抜き PNG をそのまま使える）。深度は 2〜98 パーセンタイルで正規化（外れ値で潰れない）。`near` は白が手前か黒が手前か。**ほぼ一定の深度画像は情報が無いので使わない**（warnings に記録し、深度視差も `light` の `depth` ゲートも無効になる。警告と描画が一致する）。
- 整数の項目（`output.width/height/crf`・`seed`・particles の `count`・look.grain の `size_px/frames/seed`）は `120.0` のように小数部が 0 の数も受け付けて整数にそろえる（`120` と同じ正規形・同じキャッシュ key）。小数部があれば拒否。
- 参照切れ・id 重複・キーの `t` が昇順でない・奇数の出力寸法は拒否。`depth` を指定したのに `assets.depth` が無い等は無視して warnings に記録する。

## 描画モデル

合成はすべて線形光の float32 で行い、最後に 1 回だけ sRGB の uint8 へ戻す（spec に look があるときだけ、その直前で float の sRGB に符号化して [look](#look色調整周辺減光粒状感) を掛ける）。

**カメラ** — keys を補間して zoom・center（元画像の座標）を得る（区間の曲線は到達側キーの `ease`）。plane ごとに視差 p を取り、
`zoom_p = z0·(zoom(t)/z0)^p`（対数空間。常に正）、`center_p = c0 + (center(t) − c0)·p`（z0・c0 は最初のキー）、さらに `drift_px × t`。最初のキーの状態では全 plane が重なる。
p=1 はカメラそのもの、p=0 は z0 のまま。zoom を線形に p 倍する式（`z0 + (zoom − z0)·p`）はズームアウトで p > 1 の plane のスケールが 0 や負になり、plane が潰れて反転するため使わない（両者の差が小さいのは zoom の変化が小さいときだけ: デモの 1.0→1.08 で最大約 1.2px。1→2 で p=0.4 だと背景の倍率は 1.40 から 1.32 に変わるので、ズームの大きい spec は見た目が変わる）。
深度視差（`parallax_px > 0` かつ深度あり）は `parallax_px × (W/1920) × s(t) × (D − pivot) × parallax_dir` の変位で、s(t) は **0→1 を in_out で**（t=0 で恒等。ショット全体の振れ幅は `parallax_px × (D − pivot)`。pivot の既定は 0.5）。このため t=0 では、`parallax_px > 0` でも base 版は元画像どおりで、layers 版（光なし）とほぼ一致する（人物の背後の穴埋めの帯を除く）。
0.2 では keys の `depth_shift`・`dolly` で、始まりから動いている視差や寄り・引きも書ける（[深度視差](#深度視差fx-shot02)）。

**レイヤー** — background は全 plane マスクの和集合（`inpaint_px` で膨張）を cv2.inpaint（TELEA）で埋めた板。plane は基準画像 × マスク。
z の小さい順に背面から合成し、effects も z で挟む（霧 z=1 は人物 z=2 の後ろ、火の粉 z=3 は前）。
霧は seed から作った周期ノイズを drift とカメラ移動でずらして重ねる。火の粉は位置を `(x0 + velocity·t + カメラ移動·parallax) mod 1` でフレームごとに状態なしで計算し、加算で描く。

**局所光**（基準画像空間・カメラの前） — 光の場 `L = color × I(t) × flicker(t) × falloff(r)`。`r` は光源からの距離を元画像の幅で割ったもの、
`falloff = 1/(1+(r/(0.25R))²) × clamp(1−(r/R)⁴, 0, 1)²`（`r ≥ R` で厳密に 0）。受光マスク `M` の内側だけが照らされる。

| 項 | 内容 |
|---|---|
| diffuse | `基準画像 × L × gain × M × 4.0`（面の明るさに比例するので全体の明るさ変更にならない） |
| rim | マスクの内側の輪郭帯 × `L × rim` × 光源の向きの重み。輪郭に光が回る |
| reflection | 縦に潰した距離（dy×0.10）・半径 0.18R の鋭い falloff。光源の真下に出る**縦長の筋**の照り返し（霧の塊のように広がらない） |
| emitter | 光源位置のガウスの光点と裾（半径 4×size で厳密に 0）。layers のある版では `attach` の層に載る（上の `emitter.attach`） |
| depth | `exp(−((D−depth)/depth_falloff)²)` を受光に掛ける（光と同じ奥行きの面だけ） |
| flicker | `1 + amount × (0.6 sin(2π·hz·t+φ1) + 0.4 sin(2π·hz·2.37·t+φ2))`、φ は seed から。負にしない |

最後に 0 未満を切り、0.8 以上を緩やかに圧縮する（ソフトニー `x − d²/(d+0.2)`、d = max(x−0.8, 0)）。
**局所性の保証**: 受光マスクが 0 で emitter の影響半径の外の画素は、光なしの版と**ビット単位で同じ**値になる。

**plane と背景への振り分け**（layers がある版） — plane は**すべての受光**を受ける。背景は plane のマスクを除いた受光だけを受ける
（人物の形の光が、視差でのぞいた背景に残らない）。このため、**受光マスクどうしが重なる画素は両方の受光が足される**
（例: 地面マスクが人物の足元に重なると、光の版では人物の足元が二重に照らされる）。マスクは重ねない作り方を勧める
（デモの地面マスクは人物の輪郭の内側で 1→0 に落としてある）。

## 深度視差（fx-shot/0.2）

段階 1 の課題（FX-PIVOT-1: 公開版は動かない深さ 0.35 相当、fx は 0.5 固定）に応える項目。実制作の 2.5D の式（動かない深さ・始まりと終わりのずらし・寄りと引き・平らな領域・深度の下ごしらえ）を、そのまま spec に書ける。

```json
"assets": {"base": "base.png",
           "depth": {"path": "depth.png", "near": "white", "normalize": "none", "resolution": "base", "blur_px": 3,
                     "regions": [{"quad": [[0.42, 0.31], [0.54, 0.32], [0.53, 0.53], [0.41, 0.52]]}]}},
"camera": {"keys": [{"t": 0.0, "zoom": 1.12, "depth_shift": [10, 0]},
                    {"t": 5.375, "zoom": 1.0, "depth_shift": [-10, 0], "ease": "in_out"}],
           "pivot": 0.35}
```

出力画素ごとに `k = D − pivot` とし、`変位 = (shift + (画素 − 画面の中心) × dolly) × k`（出力画素。内容がこの向きへ動く）で入力をずらして引く。

- `shift` は keys の `depth_shift`（1920 幅換算の px）を補間して W/1920 を掛けたもの。+x で手前が右、+y で手前が下へ動き、奥は逆へ動く。
- `dolly` は深さの差 1 あたりの拡大率。正で寄り（手前ほど大きく）、負で引き。画面の中心は (W/2, H/2)（画素の番号で数える。実制作と同じ）。
- D は出力側の深度（元の深度を camera と同じ変換で写したもの）。pivot の深さの面は動かない。
- `depth_shift` と `dolly` は zoom・center と同じ keys に書き、同じ ease で補間する（区間の曲線は到達側のキーの ease）。keys の各キーは完全な状態で、省いた項目は既定値（`depth_shift` [0, 0]、`dolly` 0）。前のキーの値は引き継がない。
- `parallax_px` は「0 から `parallax_px × parallax_dir` へ in_out で動く `depth_shift`」の省略形で、0.1 の式のまま計算する。keys の `depth_shift` と一緒には書けない（エラー）。`dolly` とは一緒に使える。

| 項目 | 既定 | 内容 |
|---|---|---|
| `camera.pivot` | 0.5 | 動かない深さ 0..1（1 = 手前）。数か、`{"mask": id}`（マスクの 0.5 を超える画素の深度の中央値）か、`{"box": [x0, y0, x1, y1]}`（元画像の正規化座標の箱の中の深度の中央値）。マスクと箱は、下ごしらえと切り出しの後の描画サイズの深度で数える。決まった値は manifest と `stills` の結果の `depth`（`pivot`・`pivot_from`・`motion`）に残る |
| `camera.keys[].depth_shift` | [0, 0] | 深さが pivot から 1 違う面の変位（1920 幅換算の px） |
| `camera.keys[].dolly` | 0 | 深さが pivot から 1 違う面の拡大率（−1〜1） |
| `assets.depth.normalize` | percentile | `percentile` は 2〜98 パーセンタイルを 0..1 に伸ばす（0.1 と同じ）。`none` は伸ばさず、16 bit なら 65535、8 bit なら 255 で割るだけ（float の画像はそのままで、0..1 に収まっていること）。実制作の pivot の値は `none` の尺度 |
| `assets.depth.resolution` | depth | 下ごしらえ（`regions`・`dilate_px`・`blur_px`）を掛ける解像度で、px もこの画素で数える。`depth` は深度画像のまま、`base` は先に基準画像の大きさへ線形補間で合わせる |
| `assets.depth.regions` | [] | 深度を平面（`mode: "plane"`）か一定の値（`mode: "value"` と `value`）に置きかえる凸の四角形。`quad`（元画像の正規化座標の四隅。画像の外にはみ出してよい）か `box`。plane は四隅を重心へ `inner`（0.92）倍に縮めた範囲の深度に最小二乗の平面を当て、`grow`（1.03）倍に広げた範囲へ書く。縁は `blur_px`（σ、既定 4）でなじませる。画面に合成するモニターの文字を歪ませない、遠景を止める（value 0 と pivot 0）などに使う。並びの順に掛ける |
| `assets.depth.dilate_px` | 0 | 手前を太らせる楕円の直径（奇数。0 で無効）。手前の物の縁が背景ごと動き、輪郭が裂けにくくなる |
| `assets.depth.blur_px` | 0 | 太らせた後に掛けるガウスぼかしの σ（0 で無効） |

**下ごしらえの順番**: 割る（`none`）→ 基準画像の大きさへ（`base`）→ 伸ばす（`percentile`。統計は cover で切る範囲の中で取る）→ 向き（`near`）→ `regions`（並びの順）→ 太らせ → ぼかし → cover で切って描画サイズへ。
下ごしらえを 1 つも使わなければ 0.1 と同じ経路（切り出してから正規化）で、値もビット単位で同じ。下ごしらえした深度は、light の `depth`（受光を同じ深さの面に絞る）にも使う。

**画面の端の検査**（FX-BND-1 の深度の部分）: 深度視差の変位で画面の端が画像の外を参照する時刻があれば、camera の視野のはみ出しを超えた分の最大を warnings に残す（1920 幅換算の px。0.5 出力画素までは残さない）。
平らな版（camera と同じ変換）と、layers のある版の背景（`background.parallax`）の両方で数える。変位の上限より内側の画素は端より外を引けないので、端の帯だけを調べる（画面全体で数えた値と一致することを試験で確かめた）。
0.1 の spec でも、zoom 1 のまま `parallax_px` を掛けると、この警告が出るようになった（絵は変わらない）。

**実制作の 2.5D の設定の移し方**
1. ショットの par（始め → 終わり。1920 幅換算の px）は、最初と最後のキーの `depth_shift` に書き、最後のキーの ease を `in_out` にする（実制作は smoothstep で補間する）。寄りと引き（dolly）も同じ。
2. pivot はそのまま `camera.pivot` に書く（実制作の既定は 0.35 で、0.1 の 0.5 とは違う）。
3. 深度は `normalize: "none"` にする（実制作の pivot は /65535 の尺度）。深度を基準画像の大きさに合わせてからぼかす描画なら `resolution: "base"` と `blur_px: 3`、深度画像のままで平面・太らせ・ぼかしを掛ける描画なら `resolution: "depth"` と `regions`・`dilate_px: 7`・`blur_px: 2.5`。四隅の画素は、深度画像の幅と高さで割って正規化座標にする。

**実制作との違い**（段階 1 の 1 カットで確かめた）
- 深度の下ごしらえは同じ値になる。段階 1 の深度で、基準画像の大きさで σ3 を掛ける経路と、平面・太らせ・σ2.5 の経路の両方がビット単位で一致した。変位の式も、同じ深度を渡せばビット単位で同じ（試験で確認）。
- ショット全体の変位の差は、平均 0.006px 以下、最大 0.36px（深度の段差の所）。fx は深度を描画サイズへ縮めてから camera で写し、実制作は基準画像の大きさの深度を直接写すため。
- 実制作は camera の変換をした後の画像を、変位でもう一度引く（2 回の補間）。境界は折り返す（BORDER_REFLECT）。fx は 1 回の補間で、境界は端の画素を引き伸ばす。画像の外を参照する所（段階 1 の 1 カットでは最大 6.3px）は見え方が違う。
- 実制作は画素の角、fx は画素の中心で座標を数えるので、camera の位置が出力の半画素ずれる。
- 実制作は 2 倍の静止画から直接引くが、fx は先に描画サイズへ縮めた絵を引くので、寄った所は少し柔らかい（0.1 からの違いで、この版では変えていない）。

## look（色調整・周辺減光・粒状感）

段階 1 の課題「公開版の見た目に揃えるには色調整・周辺減光・粒状感が必須」に応える仕上げの層。**実制作レンダラと同じ式・同じ順番・同じ既定値**で実装してあり、
実制作の設定値（`grade_keys`）をそのまま移せることを最優先にした。線形光での「物理的に正しい」調色ではない（やっていない）。

```json
"look": {
  "grade": {
    "keys": [[0.0, 0.85, [0.95, 1.0, 1.07], 0.12],
             {"t": 3.0, "sat": 1.12, "gain": [1.06, 0.94, 1.08], "con": 0.18}],
    "blend_s": 1.2,
    "time_offset_s": 0.0
  },
  "vignette": {"amount": 0.38, "start": 0.55, "power": 1.6, "floor": 0.55},
  "grain": {"sigma": 2.0, "size_px": 2, "frames": 6, "seed": 7}
}
```

`look` も、その子（`grade` / `vignette` / `grain`）も任意。無い子は無効で、`look` 自体が無ければ従来の経路（`to_srgb8`）を通り**出力は従来とバイト単位で同じ**。
`x_` の鍵はどの階層でも許す。

**子が全部無効の look は「look なし」と同じ出力にならない**: `look` を書いたのにどの子も無いとき（`look: {}`）や、`grade` が無く `vignette.amount` 0・`grain.sigma` 0 のように
効く段が無いときも、look の経路（float の sRGB 符号化 → 切り捨て）を通る。色・減光・粒の調整は何も変わらないが、look なしの `to_srgb8`（丸め）に対して切り捨てになるので、
**値の一部が 1 段階暗くなる**（明るくなる値は無い。線形値を一様にばらまいた 960x540 で値の約半分、8bit から戻した値では 1 割ほど）。キャッシュ key も look なしとは別になる。
look なしと同じ出力にしたいときは `look` を外す（比較には `--no-look`）。`look: {}`（子が 1 つも無い）のときだけ、この旨が warnings に残る。

| 項目 | 既定 | 範囲 | 内容 |
|---|---|---|---|
| `grade.keys` | （必須・1 個以上） | `t` は昇順（厳密） | 区間 `[t, 次のキーの t)` の色。下の配列形かオブジェクト形 |
| `grade.keys[].sat` | 1 | 0〜3 | 彩度（各画素自身の輝度が基準） |
| `grade.keys[].gain` | [1,1,1] | 各 0〜4 | RGB ゲイン |
| `grade.keys[].con` | 0 | 0〜1 | S 字コントラスト。0 で恒等、1 で完全な smoothstep |
| `grade.blend_s` | 1.2 | 0〜10 | 次のキーの時刻の何秒前から次の色へ移るか。0 なら段差 |
| `grade.time_offset_s` | 0 | −1e6〜1e6（秒） | 評価時刻 τ = 時刻 + この値 |
| `vignette.amount` / `start` / `power` / `floor` | 0.38 / 0.55 / 1.6 / 0.55 | 0〜2 / 0〜2 / 0.1〜8 / 0〜1 | 周辺減光 |
| `grain.sigma` | 2.0 | 0〜50 | 標準偏差（0..255 の単位）。0 で無効 |
| `grain.size_px` / `frames` / `seed` | 2 / 6 / 7 | 整数 1〜8 / 1〜64 / 0 以上 | 粒の一辺（出力画素）/ パターンの枚数 / 乱数の seed |

**計算順**（光・レイヤーの**後**、文字の前。fx には文字が無い）: 光とレイヤーを合成した線形 RGB（ソフトニーまで済み）を、正確な sRGB 伝達関数で float の 0..1 に符号化（0..1 に切る）→
**grade** → ×255 → **vignette**（乗算）→ **grain**（加算）→ 0..255 に切って uint8 に**切り捨て**（丸めない。実制作と同じ）。
どの段も画素ごとの演算で、近傍も画面統計も見ない。全 4 版に同じ関数を掛けるので、「受光マスクの外の画素は光なしの版とビット一致」の保証は look があっても保たれる。
粒状感は vignette の後に足す（減光の影響を受けない）。

**表示用の値の上で計算する**: grade・vignette・grain はすべてガンマ符号化した 0..1（表示用の値）に対する演算で、線形光への乗算ではない。実制作と同じ数値で同じ見え方になることを優先した結果で、
同じ `amount` でも線形光に掛けた周辺減光とは強さが違う（表示用の値に 0.5 を掛けると、線形光に 0.5 を掛けるより強く暗くなる）。

**grade**
- キーは実制作と同じ配列形 `[t, sat, [r, g, b], con]` か、オブジェクト形 `{"t", "sat", "gain", "con"}`（混在可。正規化でオブジェクト形に揃うので同じ key になる）。
  オブジェクト形で省いた項目は中立（sat 1・gain [1,1,1]・con 0）。
- 区間 `[a, b)` は a の色を保持し、b の時刻の `blend_s` 秒前から b へ移る: `f = smoothstep(clamp((τ − (b.t − blend_s)) / blend_s, 0, 1))`、sat・gain の各 ch・con を同じ f で線形補間する。b の時刻ちょうどに b の色になる。
  b が 62.4 秒なら τ = 61.2 → f 0、61.5 → 0.156、61.8 → 0.5、62.1 → 0.844、62.4 → 1。
  キー間隔が `blend_s` より短いと区間の頭で色が段差になるので、警告を出す。
- 最後のキー以降は最後のキーのまま（外挿しない）。**最初のキーより前は最初のキー**（実制作はこの場合に最後のキーになる癖があるが、ここは直した）。
- 式: `lum = 0.299R + 0.587G + 0.114B`（Rec.601）、`x = lum + (x − lum)·sat`、`x = clip(x·gain, 0, 1)`、`x = x·x·(3 − 2x)·con + x·(1 − con)`。
  sat > 1 で 0..1 の外に出た値は、ゲインの直後のクリップで初めて切る。

**vignette** — 出力画面の座標で `r = √(((x − W/2)/(W/2))² + ((y − H/2)/(H/2))²)`（各軸を自軸の半幅で割った楕円距離。辺の中点が 1、角が √2。画素は整数座標のまま）、
ゲイン `= clip(1 − amount·max(r − start, 0)^power, floor, 1)` を 3ch に同じ値で乗算する。既定では r=1 で 0.894、角（√2）で 0.699。
下限 0.55 に届くのは r ≈ 1.66 以降で、角でも r = √2（縦横比によらない）なので、既定値では下限は実質効かない。**出力画面基準**（cover の切り出しやカメラ・元画像の座標に引きずられない）で、形は準備時に 1 回だけ作る。

**grain** — `numpy.random.default_rng(seed)` から、`(H//size_px, W//size_px)` の正規乱数（平均 0・標準偏差 `sigma`）を `frames` 枚順に引き、各軸 `size_px` 倍の最近傍反復で H×W に広げる
（割り切れない端数は端の行・列を反復して埋める）。コマ番号 `n = floor(τ·fps + 0.5)`（四捨五入。ちょうど半コマは切り上げ）で `n % frames` 枚目を 0..255 の値にそのまま加算する。
モノクロ（R・G・B に同じ値）・輝度依存なし。暗部は 0 で切れるので負の半分が消える。パターンは準備時に 1 回だけ作り、フレームごとに乱数を引かない
（順方向・逆方向・`--start` で切っても同じ時刻は同じ粒）。`--start` や `time_offset_s` で `τ·fps` がちょうど半コマに当たっても、
出力フレームが 1 つ進むごとに粒が 1 枚ずつ進む（半コマを偶数へ寄せる `round()` だと、連続する 2 フレームが同じ粒になり別の粒が飛ぶので、切り上げに固定している）。**seed は `look.grain.seed` だけで決まり、spec の `seed` にも版にも依らない**（版が違っても受光マスク外の画素がビット一致するため）。
粒の大きさは出力画素で数え、解像度に比例しない（`--scale` で縮小した下見は粒の見かけが変わる）。`stills` に非整数のコマ位置の時刻を渡したときは最寄りのコマの粒になる。

**time_offset_s** — 評価時刻は `τ = t + time_offset_s`（t はショット時刻）。キーの `t` と grain のコマ番号（`τ·fps` の四捨五入）は τ で数える。実制作の `grade_keys` は曲の絶対秒なので、
**区間（ショット）の曲上の開始秒を `time_offset_s` に入れる**と、キーを書き換えずに移せる（例: ショットが曲の 62.4 秒から始まるなら `time_offset_s: 62.4`、spec の 0 秒が grade の 62.4 秒に当たる）。`time_offset_s` は `grade` の中にあるので、`grade` が無いとき grain のコマ番号は τ = t で数える。

**実制作の調色設定の移し方**
1. 実制作の `grade_keys` をそのまま `look.grade.keys` に貼る（配列形のまま使える）。
2. `look.grade.time_offset_s` にショットの曲上の開始秒を入れる。
3. `blend_s`・`vignette`・`grain` は既定のままでよい（実制作もコード直書きの同じ値: blend 1.2 秒・減光 0.38/0.55/1.6/0.55・粒 σ2.0/2px/6 枚/seed 7）。
4. 縦画面向けの実制作の値は `"vignette": {"amount": 0.34, "floor": 0.58}`、`"grain": {"sigma": 1.8}`（他は既定）。

**実制作との違い**
- 最初のキーより前は最初のキー（実制作は最後のキー）。ブレンド秒を設定できる（実制作は 1.2 秒固定）。
- 全 4 版に同じ look を掛ける（版を seed に混ぜない）。
- fx には、ショットごとの brighten・ビート脈動・最後の暗転・歌詞や題名の文字が**無い**（歌詞の合成は別課題）。光と火の粉が grade より前に合成される（演出の色も調色を通る）点は同じ。
- 実制作は 0..255 の float で合成するが、fx は線形光で合成してから符号化する。look の式・順番・既定値が同じというだけで、同じ設定から同じ絵が出るわけではない。
  式は実制作レンダラの読み取りと同じにしたが、実制作のフレームとの画像比較はまだしていない（テストは、式を独立に書き写した参照実装と合成フレームで一致することまで）。

**`--no-look` と `demo --look`**
- `mv fx render` / `mv fx stills` に `--no-look` を付けると、spec から look を外して描く（look 有り無しの比較用）。外した有効 spec でキャッシュ key を作るので、look 有りの run とは key が分かれ（`stills` のファイル名も分かれる）、
  spec に look が無いときは付けても付けなくても同じ key になる。manifest の `look` に `applied`（描いた）/ `disabled`（`--no-look` で外した）/ `absent`（spec に無い）を記録する。
- `mv fx demo DIR --look` は夜向けの look の例を足した spec を書く。`--look` を付けない既定のデモ spec は変わらない。
  上の JSON と同じ色の組だが、デモは 2 つ目のキーを長さの 60%（10 秒なら 6.0 秒）に置き、`blend_s` は `min(1.2, その秒)` にする。上の JSON の `t: 3.0` は一般的な書き方の例で、デモが書く値そのものではない。

## 限界

- 単一画像からの照明は**画像空間の近似**。面の向き・材質・影は推定しない（隠れた面は光らず、他の物が落とす影は出ない）。
  rim の向き重みと reflection は見た目のための近似で、物理的に正しい反射ではない。
- 背景の穴埋めは TELEA の単純な補間。人物の背後が複雑な絵だと、**大きな移動（視差が大きい・カメラを大きく動かす）で破綻が見える**。
  まず `stills` で端の時刻（移動が最大の時刻）を確認する。
- 光の影響範囲は半径 R で切れる。受光マスクの質（縁の粗さ・欠け）がそのまま絵に出る。
- `parallax_px > 0` でカメラが zoom 1 のままだと、ショットの終わりで画像の端が最大 `parallax_px/2`（1920 幅換算）引き伸ばされる（端の画素の複製）。0.2 からは、深度視差の変位で端が画像の外を参照すれば warnings に残る（[深度視差](#深度視差fx-shot02)）。plane の parallax と drift_px によるはみ出しは、まだ警告しない。
- 派生ズームは `z0·(zoom/z0)^p` なので、zoom の変化が大きいときに p を大きくすると指数的に増減する（例: zoom 1→8 で p=4 は 4096 倍、zoom 2→1 で p=4 は z0 の 1/16、つまり派生ズーム 0.125）。壊れはしないが、plane が画面外へ飛ぶ・点になる絵になりやすい。まず `stills` で端の時刻を確認する。
- light 版（レイヤーなし）では光点が**光点の位置の深度**で深度視差を受けるため、手など別の深度の対象から少しずれることがある（both 版の光点は所属する層に付く）。
- **look は見た目の仕上げで、光やレイヤーの計算には影響しない**（光を足し終えた結果にだけ掛かる。look の有無で光の加算量や局所性は変わらない）。表示用の値の上で計算するので、線形光での調色ではない。
  look の実制作との突き合わせ（フレーム画像の比較）と、実素材での見え方は未確認。
- 動作確認は合成素材のみ。実作品の素材（マスクの品質・深度の質）での挙動は、素材のある環境で実測すること。
- 性能の目安（4 コア・実測）: 960x540 の `both` は 1 枚約 0.1 秒、デモ（10 秒・24fps）の 4 版 + グリッドで約 1 分。1920x1080 の `both` は約 0.36 秒/枚。look を掛けると 1920x1080 で 1 枚あたり約 +0.05 秒（`to_srgb8` 約 0.035 秒 → look 約 0.085 秒、4 コア・numpy 単独の実測）、準備に 0.15〜0.35 秒（2 回の実測の幅）。粒があると MP4 は同じ crf で 3〜6 倍ほど大きくなり、書き出しもその分遅くなる（デモの 4 版 + グリッドで約 +40%）。

## マスクの用意

fx はセグメンテーションモデルを持たない。マスクは次のどれかで用意する（基準画像と同じ縦横比の PNG）。

- 手描き（ペイントソフトで人物を塗る。縁は `feather_px` でぼかす）
- rembg など既存の切り抜きツールの出力（RGBA の alpha をそのまま使える）
- 既存の切り抜き素材（別レイヤーのアルファ）
- 深度のしきい値（手前の物体だけを白にする。縁が粗くなりやすいので `feather_px` を大きめに）
- 受光マスクと plane マスクが重なると light 版（レイヤーなし）では光が漏れて見える（layers のある版は plane ごとに振り分ける。デモの地面マスクは人物の内側を帯に絞り、地平線付近もなだらかに立ち上げてある）。

深度画像は 8/16bit の PNG。外部の深度推定ツールの出力をそのまま置ける。

## 実作品の素材での回し方

素材のある環境（Cowork や PC）で、**spec を素材の隣に置いて**実行する。素材・spec・出力は Git に入れない（このリポジトリは public）。

```sh
uv run mv fx stills  /path/to/shot/spec.json --times 0 5 9.5          # まず静止画で当たりを確認
uv run mv fx render  /path/to/shot/spec.json --start 0 --duration 10  # 4 版 + グリッド
uv run mv fx legacy  --renderer /path/to/renderer.py --config /path/to/config.json --start 0 --duration 10   # 現行版
```

出力先は既定で spec のあるディレクトリの `fx_runs/`。`fx legacy` も同じ `--out` を指定すると、現行版と fx の run が同じ場所に並ぶ。

## コマンド

| コマンド | 内容 |
|---|---|
| `mv fx demo DIR [--width 960 --height 540 --duration 10 --fps 24] [--look]` | 合成素材と spec.json を作る（`--look` で夜向けの look の例を足す） |
| `mv fx render SPEC [--variants base,layers,light,both] [--start S] [--duration D] [--out DIR] [--scale 1.0] [--no-grid] [--force] [--no-look]` | 版ごとの MP4 と 2×2 グリッドを run として出す |
| `mv fx stills SPEC --times T… [--variants …] [--out DIR] [--scale 1.0] [--no-look] [--guides [grid,lights,masks]] [--grid-step S] [--reference VIDEO] [--reference-offset S] [--reference-label TEXT]` | 行=時刻・列=版の静止画シート `stills-<key8>.png`（圧縮なし）。目盛りと基準の列は[見比べの補助](#見比べの補助目盛り基準の列比較動画) |
| `mv fx guides IMAGE --out OUT.png [--crop X0 Y0 X1 Y1] [--units norm\|px] [--step S] [--width 1600]` | 元画像に座標の目盛りを重ねる（光の位置・pivot の箱・regions の四隅を読む） |
| `mv fx compare RUN_DIR --reference VIDEO [--reference-offset S] [--reference-label TEXT] [--out OUT.mp4]` | 成功した render の run の各版と基準の動画を 1 本に並べる（再描画しない） |
| `mv fx legacy --renderer PATH --config CFG --duration D [--start S] [--python EXE] [--width W --height H --fps F] [--out DIR] [--timeout SEC] [--cwd DIR]` | 外部の実制作レンダラを呼んで run として記録する |
| `mv fx accept RUN_DIR --variant V --decision accepted\|rejected\|hold --by NAME [--note TEXT]` | 人間の判定を `acceptance.jsonl` に追記する |

stdout は短い JSON、進捗は stderr に 1 版 1 行、詳細は run の `render.log`。エラーは `Error: …`（原因と直し方）で終了コード 1。
`--scale` は出力寸法を偶数に丸めて縮尺する（下見用。key に入る）。`--start/--duration` はショットの長さに収まること。

## 見比べの補助（目盛り・基準の列・比較動画）

どれも**見るための道具**で、合否も、絵の差の数（PSNR・SSIM など）も出さない。エンジンをまたいで画素を比べない決まりは、ここでも同じ。

**元画像の目盛り（`mv fx guides`）**

```sh
uv run mv fx guides base.png --out grid_full.png                              # 正規化座標（0..1）。0.05 ごと
uv run mv fx guides base.png --out grid_sword.png --crop 0.45 0.1 0.75 0.4    # 範囲を幅 1600 に拡大。範囲の幅で 0.02 / 0.01 ごと
uv run mv fx guides depth.png --out grid_depth.png --units px --step 100      # 画素の目盛り（深度は灰色で見せる）
```

- spec を書く前に、光の位置・`camera.pivot` の箱・`assets.depth.regions` の四隅を元画像から読むための絵。縦線と x の値が緑、横線と y の値が黄。
- `--crop` の範囲を幅 `--width`（既定 1600）に縮尺してから線と値を描く。`--step` を省くと、正規化座標では範囲の幅で 0.05（半分より広い）・0.02・0.01（0.15 以下）、画素では 100。
- 正規化座標の目盛りは、実制作の格子の台本と同じ式で、同じ画像と範囲なら画素まで同じ絵になる（段階 1 の静止画で、全体・中くらい・狭い範囲の 3 通りを確かめた）。
- 16 bit の画像（深度）は 257 で割った灰色、float の画像は 0..1 を 255 倍した灰色で見せる。アルファは捨てる（cv2 で読むのと同じ）。

**静止画シートの目盛り（`mv fx stills --guides`）**
- 各版のマスに、元画像の正規化座標の格子を重ねる。cover の切り出しと、その時刻の camera（視差 1）で写すので、絵と一緒に動く。深度視差の変位は含めない。
- `lights` は spec の光の位置（十字）・影響半径（円）・id を桃色で、`masks` は各マスクの 0.5 の輪郭と id を水色で描く。`--guides` だけなら `grid,lights`。
- `--grid-step` で格子の間隔を決める（省けば、見えている幅から 0.05 / 0.02 / 0.01）。目盛りの有無と間隔は key に入るので、ファイル名が分かれる。

**静止画シートの基準の列（`mv fx stills --reference`）**
- 基準の動画（公開版の切り出しや `fx legacy` の `legacy.mp4`）の、時刻 `--reference-offset + t` に最も近いコマを、先頭の列に置く（`mv inspect`・`mv sheet` と同じ読み手。復号順のコマ番号）。
  `--reference-offset` は「基準の動画の何秒目がショットの 0 秒か」。基準の列には目盛りを重ねない。
- 動画の外の時刻は止める。縦横比が出力と違えば、引き伸ばして並べ、warnings に残す。基準の動画の sha256・offset・ラベル・使ったコマ番号は key と結果の JSON に入る。
- ラベル（`--reference-label`）は ASCII だけにする（ラベルの字体に日本語が無いため）。

**比較動画（`mv fx compare`）**

```sh
uv run mv fx compare fx_runs/<run_id> --reference reference.mp4 --reference-label published
```

- 上の段に基準の動画と `both`（W/2×H/2）、下の段に `base`・`layers`・`light`（W/3×H/3）、最下段にコマ番号・ショットの時刻・基準の動画の時刻を置く。
  これは実制作の比較の台本と同じ並びで、段階 1 の run で作り直すと、5 つの枠がずれ 0 で重なった（下の段の y は、実制作の台本の 585 を ffmpeg が 4:2:0 の都合で置いた 584 と同じ偶数）。
- 描き直さない。run の版の MP4（manifest の sha256 と同じであること。違えば止める）を復号して並べる。描いていない版の枠は背景のまま。
- run の i コマ目の隣に、基準の動画のコマ `round((offset + 区間の開始 + i/fps)·fps)` を置く。コマを 1 対 1 に並べるので、基準の動画の fps は run と同じであること（ffmpeg の fps の表示は小数 2 桁なので、0.005 までの差だけを同じとみなす。24 と 23.976 のような違いは、並べるとだんだんずれるので止める）。区間の途中で基準の動画が終われば止める（途中までのファイルは残さない）。
- 音声は run の版の音声（spec の音声の区間）。書式は版の MP4 と同じ（BT.709）で、書いた後に technical_pass を通す。
- 出力の既定は `<run の親>/compare-<run_id>-<key8>.mp4`。隣に同じ名前の `.json`（`fx-compare/0.1`。run と各版の sha256、基準の動画の sha256・offset・最初のコマ、並び、技術検査）を書く。

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
出力パス・frames・sha256・technical_pass・検査内容・有効な機能（look があれば `look` も）・`empty`、`look`（`applied` / `disabled` / `absent`）、`depth`（深度視差の記録 `pivot`・`pivot_from`・`motion`。深度が無ければ null）、warnings、status。

**status** は `running → succeeded | failed`。失敗（例外・技術検査不合格・Ctrl-C）しても過去の成功 run と `index.json` は変わらず、
失敗した run は原因つきで残る。プロセスが強制終了して `running` のまま残った run は、次に同じ出力先を使うときに `failed`（interrupted）へ直す。
同じ出力先を同時に使うことはできない（2 つ目は「already running」で止まる）。

**キャッシュ**: key = sha256(正規化 spec（look を含む。`--no-look` なら look を外したもの）+ 全素材の sha256 + engine 指紋 + 版の集合 + 区間 + 出力寸法・scale + グリッド有無 + ライブラリ版)。
`index.json` にあり、出力が揃って sha256 が manifest と一致し technical_pass の run があれば、再描画せず `reused: true` を返す。`--force` で作り直す（新しい run になり、index は新しい方を指す）。
**描画に影響する入力は必ず key に入れる**（古い絵を返さないため）。`x_` の鍵も key に入る（コメントだけの変更でも作り直しになる、安全側）。

**technical_pass** — ffmpeg だけで行う構造検査（ffprobe 不要）: 全復号でエラーが無い・寸法・fps・フレーム数（= `round(duration×fps)`）が一致・
色タグが `bt709 / tv / yuv420p`・音声ありなら尺が許容内（1 フレーム + AAC 1 パケット）。**絵の良し悪し・同期の精度・作品としての採用は判定しない。**
不合格の run は `failed` になり、index に入らない。

## legacy 接続

実制作レンダラ（例: 手元の `renderer.py CONFIG seg START END out.mp4` の形のスクリプト）を**リポジトリに入れずに**呼ぶ。
argv は `[python, renderer, config, "seg", start, end, out]`（`end = start + duration`、`out` は run 内の一時名）。

- **区間の単位**（`--units`）:
  - 既定の `seconds` は、秒で渡す。
  - `--units frames --fps N` は、フレーム番号の整数で渡す。`start = round(start秒×N)`、`end = start + round(duration×N)` で、
    `end` のフレームは含まない（`range(start, end)` を描く形）。
  - 実制作レンダラの多くはこちらの形で受け取る。`--fps` が無いと拒否する。
  - manifest の `interval` に `units` と、フレームのときは `frame_range` が残る。

- 記録: renderer と config の sha256、argv、終了コード、所要時間、作業ディレクトリ（既定はレンダラのあるディレクトリ。`--cwd` で変更）。
- ログ: 子の標準出力・標準エラーを描画中に逐次 `legacy.log` へ書く（子の Python は `PYTHONUNBUFFERED=1`）。
- 検査: フレーム数は `round(D×fps)` から**最大 2 少ないまで**許容（実務の `-shortest`）、多いのは不合格。寸法・fps は `--width/--height/--fps` を指定したときだけ照合
  （`--fps` 未指定なら出力の fps から期待枚数を数える。**出力の fps も読めず `--fps` も無く期待枚数が決まらないときは `technical_pass=false`**（理由を `checks.failures` と manifest の `error` に記録。黙って合格にしない）。
  **出力の fps が読めなくても `--fps` を指定すれば、fps の照合は省き（読めないので比べられない）、指定した fps で期待枚数だけを照合する**（省いたことは manifest の `warnings` に残る。読めた fps は従来どおり指定値と照合）。全復号でエラーが無いこと。音声は検査しない。
- `--timeout` 既定は `max(600, 120×duration)` 秒。超えたら子プロセスごと止めて `failed`。
- **キャッシュしない**: 設定が参照する素材などレンダラの隠れた入力を fx は知らないため、毎回実行する（`index.json` に入らない）。
- 失敗しても `legacy.mp4` は作らず、過去の run は変えない。run の版名は `legacy`（`fx accept --variant legacy` で判定できる）。

## overlay（透過レイヤーの重ね合わせ・FX-LYRIC-1）

Remotion などで描いた**透過の連番（PNG。straight α）**やアルファつきの動画を、土台（1 枚の画像か動画）に重ねて、fx の共通の書き出し（BT.709・tv・yuv420p）で H.264 にする。歌詞テロップを fx の出力に合成する入口。

```sh
uv run mv overlay --base base.png --layer lyrics_png/ --audio song.wav --fps 30 --out runs/overlay
uv run mv overlay --base fx_runs/<run_id>/both.mp4 --layer lyrics_png/ --layer sparks.mov    # 複数は下から順に重ねる
```

- 重ね方は straight α を前提に、sRGB の 8 bit の値のまま `layer × α + under × (1 − α)`（線形化しない）。アルファ 0 の画素は色を持たない前提。
- コマ数・fps・寸法が合わないものはエラー（勝手に合わせない）。土台が画像なら `--fps` が要り、コマ数はレイヤーから決まる。土台が動画なら fps とコマ数は動画から。
- run（既定 `<土台のフォルダ>/overlay_runs/<run_id>/`）に `overlay.mp4`・`manifest.json`・`render.log` を残し、`index.json` に key → run_id を登録する。key は土台・各レイヤーの全コマ・音声の sha256 と設定から作り、同じ入力の成功 run は再利用する（`--force` で作り直す）。manifest の `overlay` の欄（土台・レイヤーごとの各コマの sha256・音声・設定）は `fx_overlay.schema.json`（fx-overlay/0.1。`contracts/` の外）で検証してから書く。
- 書き出しの後に `probe.check_video`（technical_pass。寸法・fps・コマ数・色タグ・音声の尺）を通す。通らなければ run は failed。
- 重ね合わせ自体は Pillow・numpy・ffmpeg だけで行うが、書き出しは fx の render（cv2 を import する）なので fx extra が要る。動画の土台・レイヤーはコマを復号順にそのまま読み（`-fps_mode passthrough`）、タグが bt709 なら行列を指定して読む（dance / inspection と同じ）。連番の並びは数字の大小（`frame2` < `frame10`）。アルファの無い連番・動画はエラー。実験的。確かめたのは合成素材（numpy で描いた半透明の箱）だけで、Remotion の実際の連番ではまだ通していない。

## accept

`acceptance.jsonl` の 1 行 = `{at(UTC), run_id, variant, decision, by, note, video_sha256, key}`。**追記のみ**で、既存の行も manifest も変えない。
判定は特定の出力ファイルへのもの（sha256 を記録し、出力が manifest と違えば拒否）。`accepted` は技術検査に通った版にだけ付けられる
（`rejected` / `hold` はいつでも付けられる）。`technical_pass` とは別物で、`accepted` は人間の判断。

## 出力の色

`libx264 -crf <crf> -preset medium`、`scale=out_color_matrix=bt709:out_range=tv,format=yuv420p`（`accurate_rnd+full_chroma_int`）、
タグは `bt709 / bt709 / bt709 / tv`。タグはフィルタの `setparams` と出力オプションの両方で付ける（ffmpeg 7.1 などフレームの色属性が
出力オプションより優先される版では、`-color_primaries` / `-color_trc` だけだと `bt709/unknown/unknown` になり technical_pass に落ちる。
Windows で PATH に ffmpeg が無いと使われる imageio-ffmpeg 同梱版がこれに当たる）。音声は AAC 192k・48kHz・ステレオ、頭 0.02 秒・終わり 0.25 秒のフェード、`offset_s + start` から区間の長さだけ切る。
フレーム数は `round(duration×fps)` ちょうど（`-shortest` は使わない）。グリッドは版の MP4 を同じ書式で復号して並べ直したもので、
タイルは W/2×H/2（版ごとに枠が固定: base=左上 / layers=右上 / light=左下 / both=右下）。

## CI

`uv sync --locked --extra fx` の後、pytest と、合成素材（320x180・2 秒）の `fx demo → fx render → technical_pass` を回す。
これは構造検査であり、見た目や実素材での挙動の保証ではない。
