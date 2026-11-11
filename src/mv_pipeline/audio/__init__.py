"""音の道具（`mv audio`）。曲の音から作る記録（拍の格子・打点）を、音源や analysis.json の隣の別ファイルに書く。

記録の形は `contracts/` の外（analysis.json は additionalProperties が false なので、契約を変えずに隣に置く）。値は推定。
grid（拍の格子）と hits（打点）には librosa が要る（`uv sync --locked --extra analysis`）。keys（打点から fx-shot の光の強さの
キー）は記録と spec だけを読む。files は記録の置き場所と書き出し（3 つで共通）。
"""
