"""音の道具（`mv audio`）。曲の音から作る記録（拍の格子など）を、音源や analysis.json の隣の別ファイルに書く。

記録の形は `contracts/` の外（analysis.json は additionalProperties が false なので、契約を変えずに隣に置く）。値は推定。
librosa が要る（`uv sync --locked --extra analysis`）。
"""
