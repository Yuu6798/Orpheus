# 自前の描画スクリプトから乗る

fx や dance に無い描き方を、自前の Python で書くときの手順。描画そのものは自分のスクリプトで行い、
**色・記録・つなぎ・検査**の決まりだけを借りる。描画スクリプトそのものは、リポジトリに入れない（作品の値を含むため）。

## 先に確かめること

欲しい演出が、もう部品として無いかを見る。初見だと見落としやすい。

| 欲しいもの | ある場所 |
|---|---|
| カメラ・空間レイヤー・局所光・調色（深度とマスクは任意） | fx-shot（[fx](fx.md)） |
| カット・クロスフェード・黒・フラッシュでつなぐ | [fx › timeline](fx.md#timeline複数のショットをつなぐfx-timeline01) |
| 炎・刃の火・火の粉・灯の点灯・稲光・雨・暖色の光・煙 | [fx › 演出の hooks](fx.md#演出の-hooks火空と天気明かり)（`fx.fire`・`fx.sky`・`fx.accent`） |
| 透過の連番（歌詞・題字）を重ねる | [fx › overlay](fx.md#overlay透過レイヤーの重ね合わせfx-lyric-1) |
| 緑背景の人物を舞台に合成する | [dance](dance.md) |

それでも無ければ自前で描く。そのとき借りるのは次の 5 つ。

| すること | 借りるもの |
|---|---|
| 光や炎を置く位置を読む | `mv fx guides`（[fx › 見比べの補助](fx.md#見比べの補助目盛り基準の列比較動画)） |
| コマを H.264 に書く | `mv_pipeline.encode.write_video`（[deliver › 共通の書き出し](deliver.md#共通の書き出しencodepy)） |
| 描画を run として記録する | `mv fx legacy --units frames`（[fx › legacy 接続](fx.md#legacy-接続)） |
| 区間をつないで音を付ける | `mv deliver join`（[deliver](deliver.md)） |
| コマを並べて見る | `mv sheet`（[sheet](sheet.md)） |

## 書き出しは `write_video` を使う

```python
from mv_pipeline import encode

def frames():                       # RGB の uint8、(高さ, 幅, 3)
    for n in range(first, last):
        yield draw(n)

encode.write_video(out, frames(), (1920, 1080), 30, 17, last - first)
```

`write_video` は、BT.709 の式で YUV にして BT.709 の印を付け、一時名に書いて、ffmpeg が成功し枚数が揃ったときだけ置き換える。
cv2 を使わないので、fx の extra が無くても動く。

**自分で ffmpeg の引数を書かない。** RGB のコマを ffmpeg に既定のまま渡し、出力に BT.709 の印だけを付けると、式は BT.601 のまま、
印だけが BT.709 の動画になる。再生機は印を信じて BT.709 で読むので、色がずれる（[roadmap](roadmap.md) の原則にも同じ注意がある）。

2026-11-10 に、ffmpeg 6.1.1 で、同じ色票（下の見本のコマ。320×180・CRF 18）を 2 通りに書いて測った値
（一方は `bgr24` のコマを libx264 に `yuv420p` で渡し、`-colorspace bt709` などの印だけを付けた。もう一方は `write_video`。どちらも、印に従う既定の読み方で RGB に戻した）:

| 元の色（RGB） | 印だけ付けた動画を読んだ色 | `write_video` で書いた動画を読んだ色 |
|---|---|---|
| 255, 0, 0 | 255, 23, 0 | 254, 0, 0 |
| 0, 255, 0 | 0, 214, 0 | 0, 254, 0 |
| 64, 160, 224 | 53, 154, 226 | 63, 158, 224 |
| 224, 192, 64 | 227, 186, 57 | 223, 190, 64 |

- 実際の絵（1080p の 4 コマ）の彩度の高い画素では、緑が平均で +3.4〜+9.9 ずれていた。書き出しを `write_video` に替えると −0.7〜−1.3 になった。
- 並べれば分かるが、1 本だけを見ても気づかない程度の差。だから測るまで見つからなかった。
- ffmpeg の既定の振る舞いは版で変わる。上の値は 6.1.1 のもので、ほかの版では測っていない。

## 見本（合成素材）

`examples/external/render_frames.py` は、色票と動く帯を描くだけの小さなレンダラ。引数は `CONFIG seg START END OUT` で、
`mv fx legacy --units frames` が呼ぶ形と同じ（START・END はコマ番号で、END のコマは含まない。END が START 以下なら、描かずに止まる）。

```sh
mkdir -p projects/external-demo
echo '{"width": 320, "height": 180, "fps": 24}' > projects/external-demo/config.json

# 区間を描く（名前は seg_<最初のコマ>_<終わりのコマ>.mp4）
uv run python examples/external/render_frames.py projects/external-demo/config.json seg 0 48 projects/external-demo/segs/seg_0000_0048.mp4
uv run python examples/external/render_frames.py projects/external-demo/config.json seg 48 96 projects/external-demo/segs/seg_0048_0096.mp4

# つなぐ（区間が続いていることとコマ数を確かめ、sha256 と ffmpeg の版を記録する）
uv run mv deliver join projects/external-demo/segs --out projects/external-demo/master.mp4

# 並べて見る
uv run mv sheet projects/external-demo/master.mp4 --out projects/external-demo/sheet.png --at 0.5 1.5 2.5 3.5

# 描画を run として記録したいとき（renderer と config の sha256、argv、終了コード、所要時間が残る）
uv run mv fx legacy --renderer examples/external/render_frames.py --config projects/external-demo/config.json \
    --start 0 --duration 2 --units frames --fps 24 --width 320 --height 180 --out projects/external-demo/fx_runs
```

リポジトリのルートで実行する。出力はどれも `projects/external-demo/` の下にできる（`projects/` は `.gitignore` 済みなので、Git に入らない）。

`tests/test_external_renderer.py` が、この見本で legacy・join・色の読み戻しと、空の区間で止まることを確かめる。

## 描画スクリプトの作り

- **コマ番号だけで絵が決まるようにする。** 前のコマの状態を持たない。区間を並べて描けて、直した区間だけを描き直せる。
  粒や炎も、時刻から位置を計算する（hooks の部品と同じ作り）。
- **区間の名前は `seg_<最初>_<終わり>.mp4`。** `mv deliver join` が、区間がすき間なく続くことと、区間ごとのコマ数を確かめる。
- **位置は素材の正規化座標で持つ。** カメラの変換で画面へ写す。カメラが動いても、光や炎が絵の同じ所に付く
  （hooks の `view.point` と同じ考え方。fx の光の座標の決まりとも同じ）。
- **本番の前に静止画で確かめる。** 時刻をいくつか選んで 1 枚に並べる。縮小した一覧だけでなく、**原寸の切り出し**も見る。

## 事例（2026-11-10）

初見のエージェントが、静止画 31 枚と 3 曲のつなぎから、73 秒・1080p の予告編を自前のスクリプトで描いた。合成素材ではない、実際の制作。

- 借りたもの: `mv fx guides`（4 枚で位置を読んだ）、`mv deliver join`（4 区間）、`mv sheet`（48 コマの確認）。
- 書き出しは自前の引数で行い、上の色の罠を踏んだ。`write_video` に替えて描き直した。差し替えは十数行だった。
- hooks の部品に気づかず、炎・刃の火・灯の点灯・稲光・雨を書き直した。README の入口の表に hooks の行を足したのは、このため。
- 半分の大きさの一覧では通った不具合（小さな灯が暗すぎる）が、原寸の切り出しで見つかり、本番の後に 1 区間を描き直した。
- 自前の描画の速さは 1 プロセスで毎秒 2〜3 コマ（2 コア）。fx のデモ（both 版・1080p）は同じ環境で毎秒 2.7 コマで、速さは自前で書く理由にならなかった。
