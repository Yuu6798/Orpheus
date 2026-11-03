"""REA（morluto/rea 6.4.0、commit a56f7bb…）の解析結果を、工程の根拠（evidence）と観測（Observation3D）に写す。

REA は JS/TS の静的解析で、既存 3D ツールの関数・呼出し・依存を観測する。3D 専用の provider は無いので、
メッシュや Blender の観測はこの側の Observation3D（rea-3d-observation/0.1）に置く。既存の JS グラフ型との互換は主張しない。
"""
REA_VERSION = "6.4.0"
REA_COMMIT = "a56f7bb668ab23fec8d33f7af353ac7ed2fe1895"
