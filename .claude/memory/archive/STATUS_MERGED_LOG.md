# STATUS_MERGED_LOG — Recently Merged の溢れ分アーカイブ

`STATUS.md ## Recently Merged` の直近 5 件 cap を超えた行を、古い順に末尾へ追記する（原文保存）。

| PR | Title | Date | Phase |
|---|---|---|---|
| #1 | fix(deps): emSign CA 鍵漏洩対応で certifi を 2026.10.19 へ更新 | 2026-10-23 | 依存の緊急更新 |
| #2 | feat: 安全網（依存・CI）と演出エンジン fx v0.1（空間レイヤー・局所照明・4 版比較） | 2026-10-24 | 段階 0 |
| #6 | Claude/fx windows color tags（内容: ffmpeg 7.1 でも色タグを bt709 にして Windows で technical_pass に通す） | 2026-10-24 | 段階 0 の Windows 追従 |
| #7 | docs(memory): 引き継ぎ用のセッション記憶（.claude/memory）と wrap-up 手順を追加 | 2026-10-25 | 引き継ぎの仕組み |
| （PR なし） | 段階 1: 公開済みの第 1 作の 1 カットで 4 版比較、ユーザーが both を採用（PC 側で実施、記録はこのセッションの docs 更新） | 2026-10-25 | 段階 1 |
| #9 | feat(fx): look（色調整・周辺減光・粒状感）を実制作と同じ式で追加（FX-LOOK-1） | 2026-10-25 | 段階 1 の課題 |
| #10 | docs: Windows のスマート アプリ コントロール下での準備手順と 10-30 の作業記録（WIN-SAC-1） | 2026-10-30 | 環境 |
| #3・#4・#5 | Dependabot（OpenCV 5.0・hatchling・pytest）。PC で OpenCV 5.0 の pytest と technical_pass を確認、fx の見た目の目視比較は未実施 | 2026-10-31 | 依存 |
| #11 | feat(dance): 緑背景の人物動画をステージに合成するダンス合成を追加 | 2026-10-31 | ダンス合成 |
| #13 | docs(memory): 10-31 の作業を PR #12 のマージまで記録する | 2026-10-31 | 引き継ぎ |
| #12 | feat(gate): 書き出し前の機械的な検査と行・秒の確認表をリポジトリに入れる | 2026-10-31 | 検査 |
| #74 | `mv sheet`: 動画か透過の連番から、指定した時刻か timeline の出来事のコマを 1 枚に並べ、コマ番号・秒・出来事を書き込む（透過は白・黒・灰の上に置いた版。合否なしの閲覧用。隣に sheet.json）。AI が動画を見ずに確かめる入口 | 2026-11-08 | 閲覧 |
| #73 | `mv gate timeline`: 透過の連番から、字が見えているコマと箱を α で実測して dance-timeline/0.1 を書き、宣言（props）との差を行ごとに出す（行ごとの連番か hints の箱で切り分け。Remotion のテロップのデモで timeline を手で書くしかなかったことから） | 2026-11-08 | 検査 |
| #72 | `mv overlay`: 透過の連番・アルファつき動画を土台に重ねて fx の共通の書き出しで H.264 にする（straight α・sRGB で混ぜる。コマ数・fps・寸法の食い違いはエラー。各コマの sha256 と設定を run と台帳に記録、fx-overlay/0.1 で検証、technical_pass）。FX-LYRIC-1 の入口（Remotion のテロップのデモで ffmpeg の引数を fx から写すしかなかったことから） | 2026-11-08 | fx |
| #71 | `mv export remotion-props`: analysis / storyboard からコマ番号だけの props.json（remotion-props/0.1）を書く。丸めは `validate.frame_span` / `frame_at` に 1 か所化し、`mv render` の字幕と共有。TS の型 `renderer/src/lyric-props.ts`（Remotion のテロップのデモで秒→コマの丸めを TS に書き直す必要があったことから） | 2026-11-08 | Remotion 連携 |
| #70 | `mv gate demo`: 正解の分かっている試験素材（声の代役・宣言した行の時刻・timeline・拍の格子・expected.json）と CI の smoke（demo → lines → check → 照合）。`gate lines` の歌い出しの推定の偏り（約 −20 ms。窓の掛かり始め）を docs/gate.md に表で記録（補正は入れず、ユウ判断）。WAV は soundfile 無しで読める（Remotion のテロップのデモから） | 2026-11-08 | 検査 |
| #69 | 字の重なりの検査がフェードアウトを数える（手書きの timeline は `[start, end + fade_out_s]`。dance の timeline は `lyric_fade: inside` を書き `[start, end]`。`box_intersects` を添え、`overlap_mode: time_and_box` で箱が交わるときだけ数える。Remotion のテロップのデモから） | 2026-11-08 | 検査 |
| #68 | GATE-3: 拍の検査の照合先を既定で拍にし、`align: "downbeat"` のものだけ小節頭と照合。文面と集計も照合先ごとに分けた（Remotion のテロップのデモで拍のフラッシュが不合格になった問題から） | 2026-11-08 | 検査 |
| （この PR） | INSPECT-6: Windows と Blender の描画で `mv inspect` を確かめた（合成素材の 7 つ・本番の 32 秒の描画・緑背景で描いた描画にわざと入れた 7 つ）。`render.stage` に `background`（緑背景・透け）を足し、離れた塊の見落とし（2% → 1%）と人物が戻ったコマの動きの誤検出を直した | 2026-11-07 | 検品 |
| #55 | `mv inspect` の 2 回目のドッグフーディング: 手当ての規則（速い動きの崩れに補間）・静止画のメモリ（60 秒で 2.1→0.6 GB）・VLM のトークン数とキャッシュ・緑抜きの注意・表に設定画の色を出す | 2026-11-07 | 検品 |
| #54 | `mv inspect` を実素材で確かめ、設定画の向き違いでの衣装の誤検出を直す（そのコマをいちばんよく説明する設定画と比べる）。設定画の名前と切り抜きの画像、数値の出来事に添えた VLM の指摘の文を報告に出す | 2026-11-07 | 検品 |
| #51 | 生成動画の検品 `mv inspect`（異常の位置・分類・設定画との照合・時系列の検査・VLM の受け渡し・修正計画。AGENTS.md の例外としてユーザーが依頼） | 2026-11-07 | 検品 |
| （この PR） | DECIDE-1 を閉じる: LICENSE を Apache-2.0 にした（ユーザー決定）。取り込みと出力は #33 で決めた | 2026-11-06 | 方針 |
| #33〜#37 | ツールキットの方向と判断の記録、Windows の子プロセスの文字化け、dance-recipe の固有名を preset へ、Blender の --factory-startup、fx legacy のフレーム番号（TOOLKIT-0） | 2026-11-06 | ツールキット段 0 |
| （PR なし） | DANCE-3D-3: 3D モデルで 32 秒を描き終えた（リポジトリの外）。本番を描いて保存した。書き出しで色の式が BT.601 のまま印なしだった誤りを見つけ、直した版も保存した | 2026-11-06 | 3D 試作 |
| #31・#32 | 制作の計画と証跡（`mv workflow`）／描画の片付けと失敗時の診断 | 2026-11-05 | 骨格 |
| #22〜#30 | model3d: 断面と単位の修正、検査を開発原則の例外として記録、Windows での動画化、面領域、テクスチャの投影、dance-recipe の顔の投影 | 2026-11-03 | 3D ツール |
| （PR なし） | DANCE-3D-1: 動画から推定した動きを 3D キャラに載せる試作（リポジトリの外）。三面図で作り直したモデルの第 6 版をユーザーが採用し、動画・記録・レシピをクラウドに保存 | 2026-11-02 | 3D 試作 |
| #21 | fix(dance): タグが bt709 の動画を正しい行列で読む | 2026-11-01 | ダンス合成 |
| #19・#20 | Dependabot（actions/setup-python 7.1.0・astral-sh/setup-uv 10.3.2） | 2026-11-01 | 依存 |
| （PR なし） | DANCE-1: ダンス合成を実素材の縦動画で試した（ユーザーの素材で 3 本、コードの変更なし、歌詞なし） | 2026-11-01 | ダンス合成 |
| #14〜#18 | feat(dance): dance-shot/0.2（基盤・カメラ・歌詞・仕上げの受け渡し口）と gate の timeline 0.2 対応 | 2026-10-31 | ダンス合成 |
