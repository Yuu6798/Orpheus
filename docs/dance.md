# dance（ダンス合成・実験的）

緑背景の人物動画をステージの画像に合成し、カメラと縦の歌詞を載せた縦動画を作る。CLI と設定ファイルだけで動く。

- 決定論: 同じ spec・素材・環境（ライブラリ版）なら同じフレーム。LLM もネットワークも使わない。
- **実験的**: 設定スキーマ `dance-shot/0.1` は `contracts/` の外（`src/mv_pipeline/dance/`）にある。`contracts/` への昇格はユーザー承認が要る。
- **汎用の部品だけ**: 作品固有の値はコードに持たず、spec に書く。作品ごとの spec・素材・出力は Git に入れない。
- **採点しない**: `technical_pass` は構造検査、`accept` は人間の判定。絵の良し悪しや時刻の合否は判定しない（書き出し前の検査はリポジトリの外の道具で行い、run の `timeline.json` を読ませる）。
- 動作確認は**プログラムで描いた合成素材のみ**。実素材（緑抜きの質・字の見え方・動画のコーデック）での挙動は、素材のある環境で実測すること。

## 準備

fx と同じく opencv が要る（`uv sync --locked --extra fx`）。Windows でスマート アプリ コントロールがオンの PC は `docs/dependencies.md` の 2026-10-30 節。

## 5 分で試す（合成素材）

```sh
uv run mv dance demo projects/dancedemo                                  # 素材と spec.json（540x960・6 秒・24fps）
uv run mv dance stills projects/dancedemo/spec.json --times 1 3 5 --guides   # 静止画シート（--guides で UI 枠と字の箱を重ねる）
uv run mv dance render projects/dancedemo/spec.json                      # dance.mp4 と timeline.json を run として出す
uv run mv dance accept projects/dancedemo/dance_runs/<run_id> --decision accepted --by NAME
```

デモは夜のステージに、緑背景の棒人間 2 本を切り替える。2 本は腕の振りの位相が違うので切れ目で姿勢が飛び、そこにフラッシュと「回転しながら寄って引く」カメラが入る。

## spec（dance-shot/0.1）

素材パスは **spec ファイルのあるディレクトリ基準の相対パス**（`io.safe_path` で逸脱を拒否）。検証は JSON Schema 2020-12（`src/mv_pipeline/dance/dance_shot.schema.json`）。未知の鍵は拒否し、`x_` で始まる鍵はどの階層でも許す（描画には使われないが、キャッシュ key には入る）。

```json
{
  "schema_version": "dance-shot/0.1",
  "output": {"width": 1080, "height": 1920, "fps": 24, "crf": 18},
  "duration_s": 30.0,
  "audio": {"path": "audio.wav", "offset_s": 0.0},
  "stage": {"path": "stage.png"},
  "performer": {
    "clips": [{"path": "a.mp4", "start": 0.0, "end": 10.0, "source_start": 0.0, "speed": 1.0},
              {"path": "b.mp4", "start": 10.0, "end": 20.0}],
    "key": {"color": [0, 255, 0], "tolerance": 0.22, "softness": 0.12, "spill": 1.0, "choke_px": 1, "feather_px": 1},
    "placement": {"anchor": [0.5, 0.92], "pivot": [0.5, 1.0], "height": 0.72}
  },
  "camera": {
    "keys": [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5], "rotate_deg": 0},
             {"t": 30.0, "zoom": 1.08, "center": [0.5, 0.55], "ease": "in_out"}],
    "seams": [{"t": 10.0, "span_s": 0.4, "zoom": 1.25, "rotate_deg": 6}]
  },
  "flashes": [{"t": 10.0, "strength": 0.6, "decay_s": 0.2, "color": [255, 255, 255]}],
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
- `performer.clips`: start の昇順で、区間は重ねない。元動画のフレームは `(source_start + (t − start) × speed) × 元動画の fps` の切り捨て。動画の末尾を越えたら最後のフレームを保つ。人物のいない区間は warnings に残る。同じ `path` を何度使ってもよい。
- `placement`: 元動画の点 `pivot`（元動画の幅・高さに対する割合）を画面の点 `anchor` に合わせる。`height` は元動画の高さが画面の高さの何倍か（全身が映る動画なら、人物の身長の割合ではない）。
- ステージの画像は出力の縦横比に cover で中央に合わせる（余る側を切る）。
- ASCII 以外の歌詞には `lyrics.font` が要る（フォントファイルを spec の隣に置く。素材と同じく sha256 が記録される）。無ければ Pillow の既定の欧文フォントを使う。

## 描画モデル

重ね順は **ステージ → 後ろの字（back）→ 人物 → フラッシュ → 帯の字（band）**。合成は線形光の float32 で行い、最後に 1 回だけ sRGB の uint8 へ戻す。

**緑抜き** — 透明度は色差（BT.709 の Cb・Cr）の距離で決める（明るさを見ないので、背景の濃淡に強い）。距離が `tolerance` 以下は透明、`tolerance + softness` 以上は不透明で、間はなだらかに変わる。`spill` はキー色の成分が他の成分を超える分を削る割合（緑かぶりの除去。緑の衣装には 1 未満を使う）。`choke_px` で縁を内側へ削り、`feather_px` でぼかす（どちらも元動画の画素）。

**カメラ** — ステージと人物に掛かり、字には掛からない。`keys` は zoom・center・rotate_deg を補間する（曲線は到達側のキーの `ease`）。`center` の点が画面の中央に来る。`seams` は切れ目の時刻 `t` で最大になり、前後 `span_s` 秒で戻る動き（姿勢の飛びを隠す「回転しながら寄って引く」）で、`keys` の値に掛け合わされる。ステージの外が見えるときは端の画素を引き伸ばす（zoom は 1 以上にする）。

**縦の歌詞** — 字は画面に固定で、カメラに乗らない（時刻によらず箱が 1 つに決まり、UI 枠を確かめやすい）。

- `back` は人物の後ろの大きな字、`band` は上の帯の字。
- 字は `size_px`（幅 1080 の画面での値）から、箱に収まるまで 5% ずつ縮め、箱の中央に置く。最小は幅 1080 で 8px（画面の幅に比例）で、それでも収まらない行は warnings に残し、`timeline.json` の `overflow` を true にする（`box_px` は画面内に切った値なので、欠けはこの印で分かる）。
- 強調は**色だけ**（大きさも位置も変えない）。`words` の `t` が来たら語が `accent_color` に変わる。`t` が無ければ最初から、`accent: false` なら強調しない。`words` をつなげた文字列は `text` と一致させる。
- `start` の最初のフレームから見え、`fade_in_s` / `fade_out_s` で出入りする（既定 0.04 / 0.08 秒）。
- 縦画面の `ui_regions` の既定値は上・右・下の操作部品のおおよその範囲（目安）。字のインクが重なると warnings に残す（描画は止めない）。横画面の既定は空。

**フラッシュ** — `t` から `strength × exp(−経過/decay_s)` の割合で色へ寄せる。帯の字はその上に載るので白飛びしない。

## run

```
<out>/                          既定は <spec のディレクトリ>/dance_runs/
├── <UTC run_id>/               spec.json manifest.json render.log dance.mp4 timeline.json
├── index.json                  key → run_id（成功した run だけ）
├── acceptance.jsonl            人間の判定（追記のみ）
└── stills-<key8>.png
```

run の形・manifest・キャッシュ・`technical_pass`・`accept` は fx と同じ（`docs/fx.md` の「run と再現性」）。キャッシュ key は正規化 spec・全素材の sha256・エンジン指紋（dance と fx の全ファイル）・区間・出力寸法・ライブラリ版から作る。

**timeline.json**（`dance-timeline/0.1`）は、字の出入りと実際の箱（`box_px`・正規化した `box`）、clip の区間、切れ目（`seams`）、フラッシュの時刻、`ui_regions` の記録。描画の入力ではなく、外部の検査道具に渡すためのもの。

## コマンド

| コマンド | 内容 |
|---|---|
| `mv dance demo DIR [--width 540 --height 960 --duration 6 --fps 24]` | 合成素材と spec.json を作る |
| `mv dance render SPEC [--start S] [--duration D] [--out DIR] [--scale 1.0] [--force]` | dance.mp4 と timeline.json を run として出す |
| `mv dance stills SPEC --times T… [--out DIR] [--scale 1.0] [--guides]` | 指定時刻を 4 列に並べた静止画シート |
| `mv dance accept RUN_DIR --decision accepted\|rejected\|hold --by NAME [--note TEXT]` | 人間の判定を追記する |

## 限界

- 緑抜きは 1 色のクロマキーだけ。髪の細い毛や半透明の布は `tolerance`・`softness` で調整する。縁の質は素材次第で、合成素材では実写やアニメ調の動画の難しさは確かめていない。
- 人物の明るさや色をステージの光に合わせる処理は無い（人物が浮いて見えることがある）。
- 歌詞は 1 行ずつ（折り返しなし）で、横書きだけ。
- 動画の読み出しは前方向だけで、戻るときは先頭から数え直す。本番の描画は順に読むので速いが、`stills` で後ろの時刻から描くと遅い。
- 動画のフレームは OpenCV で復号する。色の範囲や行列の扱いはコーデックのタグに依存するので、実素材でキー色の値を確かめる。
