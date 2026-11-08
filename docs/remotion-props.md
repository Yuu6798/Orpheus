# Remotion 用の props（`mv export remotion-props`）

Remotion を「透過レイヤーの部品」（歌詞テロップなどを透過 PNG 連番で描く側）として使うとき、Remotion 側に秒を扱わせず、
**コマ番号だけ**の props.json を渡すための書き出し。analysis.json（と任意で storyboard.json）から作る。

```sh
uv run mv export remotion-props projects/song-a --out projects/song-a/work/props.json
uv run mv export remotion-props projects/song-a --out props.json --beats-per-bar 4            # beats_s を 4 つずつ数えて小節頭を作る
uv run mv export remotion-props projects/song-a --out props.json --beats beats.json          # gate / dance と同じ {"beats", "downbeats"} を使う
```

- 形式は `remotion-props/0.1`（`contracts/` の外。正は `src/mv_pipeline/remotion_props.py`、TS の型は `renderer/src/lyric-props.ts`）。
- 行ごとに `id`・`text`・`display`（表示用の文字。今は `text` と同じ）・`sung`・`start_frame`（floor）・`end_frame`（ceil。含まない）・`timing_source`・`review_status`・`confidence`。行は `mv render` の字幕と同じ関数（`validate.captions` → `frame_span`）で作る（開始順、前の行の終わりは次の始まりで切る、歌が重なる行は止める）ので、2 つの描画で行の出入りのコマがずれない。
- 描画の設定（`fps`・`width`・`height`・`duration_frames`）は `storyboard.json` があればその `render`（`mv render` が使うもの）、無ければ `project.json`（`render_source` に書く）。
- 時刻の無い行（`start_s` が null）は `lines` に入れず `untimed` に残す。推定の行は `timing_source` / `review_status` をそのまま写し、確定扱いにしない。
- `beats.frames` / `beats.downbeat_frames` はいちばん近いコマ（`validate.frame_at`。`duration_frames` の中だけ）。analysis.json には小節頭が無いので、`--beats`（`{"beats", "downbeats"[, "bpm"]}` のファイル。拍も bpm もこれに置き換え、名前と sha256 を記録する）か `--beats-per-bar N [--downbeat-phase k]` で与える。どちらも無ければ空。
- `storyboard.json` があれば `scenes`（コマ番号の場面）と `show_lyrics` も写す（現在の analysis.json と合わないものは止まる。`--no-storyboard` で省ける）。
- 元ファイルの sha256（`sources`）。

端数の例（30 fps）: 3.02 秒の始まりは 90 コマ、5.48 秒の始まりは 164 コマ、5.48 秒の終わりは 165 コマ。

書き出しはプロジェクトのファイルを読むだけで、`--out` 以外を書かない（`project_lock` も通らない）。Remotion で描いた連番を重ねる（`mv overlay`）、連番から timeline を起こす（`mv gate timeline`）は別のコマンド。

## 描画を台帳に載せる（`mv layer render`）

Remotion の連番の描画を工程として包み、run と台帳に残す。

```sh
uv run mv layer render --props props.json --out runs/layer                      # 既定: renderer/src/layer-index.tsx の LyricLayer
uv run mv layer render --props props.json --entry my/lyrics.tsx --composition Lyrics --public fonts/ --twice
```

- 既定のエントリ `renderer/src/layer-index.tsx` は、props（remotion-props/0.1）を読んで見えている行だけを透過で置く最小のコンポジション `LyricLayer`（`renderer/src/LyricLayer.tsx`。拍のコマで字を少し大きくする）。自分のコンポジションは `--entry` / `--composition` で差し替える。
- 描画は `renderer/render-frames.mjs`（`renderFrames`、`imageFormat: png`）。連番は `<run>/frames/element-<コマ>.png`。
- run の `manifest.json` に記録: Remotion の版、エントリ・props・public の各ファイルの sha256、コンポジション名、描画スクリプトの sha256、ブラウザーの実行ファイル（Remotion が用意したもの、または `ORPHEUS_BROWSER`）とその版、連番の manifest（`frames.json`。コマごとの sha256）の sha256、描画時間。エントリと描画スクリプトの `path` は、リポジトリの中ならリポジトリからの相対パス（OS によらず `/` 区切り）、外ならファイル名だけ。
- `--twice` は同じ入力で 2 回描き、コマごとの sha256 が全部一致するかを `deterministic` に残す（2 回目の連番は消す）。一致しないことは記録であって合否ではない。
- 台帳の鍵は入力（エントリ・props・public・コンポジション・Remotion の版・描画スクリプト）から作り、同じ入力の成功 run は再利用する（`--force`。`--twice` は常に描く）。
- `--renderer` で描画スクリプトを差し替えられる（同じ引数と、標準出力の最後の行の JSON の決まり）。テストは Remotion を呼ばない代役で確かめる。描画スクリプトの標準出力と標準エラーは `render.log`（UTF-8）に残す。Python の描画スクリプトには `PYTHONIOENCODING=utf-8` を渡す（利用者が決めていればそれを使う）。
- 連番の中身の良し悪しは判定しない。見るのは `mv sheet`、重ねるのは `mv overlay`、timeline を起こすのは `mv gate timeline`。
- 2026-11-08 にこの環境（Linux、Remotion 4.0.532、Remotion が用意した Chromium）で、192×320・12 コマの `LyricLayer` を `--twice` で描き、全コマの sha256 が一致した。実作品の連番では未確認。
