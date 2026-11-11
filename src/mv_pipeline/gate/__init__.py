"""書き出し前の機械的な検査（実験的）と、歌詞の行・秒の確認表。

機械で分かる問題（字の時刻・UI 枠・重なり・拍・書き出しの揃い方）だけを見る。絵の良し悪しや好みは判定しない。
2026-10-31 にユーザーが認めた例外で、AGENTS.md の「測定器・検証器を足さない」の対象外（docs/gate.md）。
しきい値は最初の 1 作で確かめて直す前提の仮の値。

  - checks: 9 つの検査（`mv gate check`）。入力は `mv dance render` の timeline.json と、持っている分の材料。
  - lines: 行・秒の確認表（`mv gate lines`）。行頭の候補を出すだけで、確定はユーザーの試聴。
  - demo: 正解の分かっている試験素材（`mv gate demo`）。声の代役・宣言した行の時刻・timeline・拍の格子・expected.json。
  - frames_timeline: 透過の連番から timeline を起こす（`mv gate timeline`）。α から見えているコマと箱を実測する。
  - flashes: 完成動画から明滅を測る（`mv gate flashes`）。画面全体の明るさ（線形の相対輝度）の変化の組を数える。規格への適合は判定しない。
    `mv gate check` は、gate.json に `flash` を書いたときだけこの測定を検査に足す（書かなければ 9 つのまま）。
  - vocals: 分離ボーカルの数字（`mv gate voice`・`notes`・`repeat`）。声の区間・音符・旋律の繰り返しのずれを出すだけで、合否は出さない。

cv2 は使わない。soundfile は `--vocals` を使うときだけ読み込む（analysis extra。PCM の WAV は無くても読める）。
voice・notes・repeat は librosa と soundfile が要る（analysis extra）。
"""
