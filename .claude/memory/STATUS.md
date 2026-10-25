# STATUS.md — Orpheus プロジェクト状況

## Phase

ロードマップ（`docs/roadmap.md`）の段階 0（安全網と演出エンジン fx v0.1）を PR #2 で、Windows の ffmpeg 7.1 向けの色タグ修正を PR #6 で 2026-10-24 にマージし、ユーザーの Windows PC でも `mv fx demo → fx render` の全版 technical_pass を実測した段階にある。次は段階 1（制作中の作品の基準画で 10 秒の 4 版比較を作り、指摘を 1 回反映して採用を記録）で、素材のある PC で行う。並行トラック（工程の骨格の実害修正）は未着手で、段階 2（カメラ軌道・ピント送り・演出部品）は段階 1 の結果を見てから、判断ゲート（統合／併存／Remotion 進化、実制作ツールの取り込みの可否）は段階 1〜2 の後に置く。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| STAGE-1 | 実作品の基準画で 10 秒の 4 版比較と指摘 1 回の反映 | P1 | マシン依存（素材のある PC）。ユーザーの指定待ち: カット・曲の 10 秒・光源。手順は docs/fx.md |
| DEP-3 | Dependabot PR #3（opencv-python-headless 5.0、メジャー）の確認 | P2 | CI に加えて fx の決定論テストと demo の見た目を確認してから |
| DEP-4 | Dependabot PR #4（hatchling）・#5（pytest）の確認 | P3 | CI が green ならマージ判断のみ |
| STATE-1 | 失敗 render が成功記録を消す・preview/final のキャッシュ外れ（B01〜B04）| P2 | 並行トラック。契約不変。再現スクリプトあり（render 工程の stage を profile ごとに分ける） |
| LYRIC-1 | 歌詞時刻 1 行の修正で手書きハッシュが要る・update-lyrics の行き止まり（B18/B19/B23/B24） | P2 | 並行トラック。rebase の範囲（演出の再確認を省くか）はユーザー判断が先 |
| IMPORT-1 | 歌詞取り込みの行単位降格・表示用と歌唱用の歌詞の分離経路 | P2 | 並行トラック。契約は既に分離を持つ |
| CLI-1 | 型付き例外と終了コード・読み取り系はロックなし・描画中の逐次ログと timeout | P3 | 並行トラック |
| FX-BND-1 | fx の画面外警告の拡張（parallax≠1・drift・深度変位）と light 版の光点の深度追従 | P3 | 境界宣言済み。段階 1 の後の周回で |
| DEP-2 | uv の推移依存に exclude-newer（7 日）を入れるか | P3 | 境界宣言済み。導入するなら waiver の運用も同時に |
| LINT-1 | ruff の最小ルール（E9・F・I）導入 | P3 | 既存の指摘を自動修正してから CI に足す |
| DECIDE-1 | ユーザー判断: 実制作ツールの取り込み・出力の既定値（bt709・crf 16・24fps）・LICENSE | P2 | 取り込みは public/private の判断とセット |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #6 | fix(fx): ffmpeg 7.1 でも色タグを bt709 にして Windows で technical_pass に通す | 2026-10-24 | 段階 0 の Windows 追従 |
| #2 | feat: 安全網（依存・CI）と演出エンジン fx v0.1（空間レイヤー・局所照明・4 版比較） | 2026-10-24 | 段階 0 |
| #1 | fix(deps): emSign CA 鍵漏洩対応で certifi を 2026.10.19 へ更新 | 2026-10-23 | 依存の緊急更新 |
