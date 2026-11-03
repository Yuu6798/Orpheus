# backends — 初期モデルの生成器（別プロセス）

`generate.initial_model` が argv で呼ぶ script。model3d のパッケージには torch もニューラルの重みも入れない。
設定（`docs/adapters.md` と同じ形。`tool`・`version`・`argv`・`outputs.obj`、任意で `license`・`weights`・`kind`）の argv は
**ローカルで実行するプログラム**なので、自分で確かめたものだけを使う。置き換え: `{prepared}`（前処理済み PNG）、`{out_dir}`、
`{resolution}`、`{threshold}`、`{seed}`、`{work}`。

| script | 中身 | 動く場所 |
|---|---|---|
| `standin_initial_model.py` | 生成器の代役。前処理済み PNG の前景の輪郭の箱を頂点色付きの OBJ に書く（生成ではない） | model3d の venv（numpy・Pillow）。デモ・テスト・CI |
| `triposr_initial_model.py` | 固定した公開 TripoSR（MIT）のネットワークと重みで、設定画 1 枚から初期モデルを作る。密度場 → marching cubes → 頂点色は自作の順で呼ぶ。`--verify-original` で元の抽出と座標・向き付き三角形・RGBA を許容差 0 で比べる | **別の仮想環境**（torch CPU・trimesh・torchmcubes MPL-2.0）。CI では動かない |
| `fetch_triposr_weights.py` | 固定 revision の `model.ckpt`（約 1.68 GB）を取得し sha256 を照合する。違えば採用しない | ネットワークのある環境 |
| `triposr_example.json` | 設定の例（パスは置き換える） | — |

## TripoSR の環境（2026-11-03 のユーザーの検証と同じ固定）

Linux・Python 3.12・gcc/g++。GPU も有料 API も要らない。重みは別途取得する。

```sh
python3.12 -m venv /path/triposr_env
/path/triposr_env/bin/python -m pip install torch==2.5.1+cpu --index-url https://download.pytorch.org/whl/cpu
/path/triposr_env/bin/python -m pip install numpy==1.26.4 Pillow==10.1.0 trimesh==4.0.5 omegaconf==2.3.0 transformers==4.35.0 huggingface-hub==0.17.3 einops==0.7.0 safetensors==0.8.0
CC=gcc CXX=g++ CMAKE_BUILD_PARALLEL_LEVEL=1 /path/triposr_env/bin/python -m pip install --no-build-isolation --no-deps /path/torchmcubes-a9ac95aa329957ba646e7bb057f825f5dfa77f38
python3 tools/model3d/backends/fetch_triposr_weights.py /path/triposr_weights   # config.yaml は TripoSR の HF 配布の固定 revision から置く
```

固定: TripoSR `107cefdc244c39106fa830359024f6a2f1c78871`、HF `stabilityai/TripoSR` revision `5b521936…`（model.ckpt sha256 `429e2c6b…`）、
torchmcubes `a9ac95aa…`。DINO の補助 config は固定 revision `f205d5d8…` を `HF_HUB_CACHE` に置き、`HF_HUB_OFFLINE=1` で動かす。
依存全体の版はユーザーの記録（requirements_cpu.txt）にある。ここに書いた最小の組は未検証なので、動かないときは記録の版に揃える。

## ライセンスと範囲

- TripoSR のコードと公開の重みは MIT、torchmcubes は MPL-2.0。依存全体を MIT として一括表示しない。
- Hunyuan3D-2mv（正面・左・背面・右の同時入力）は Tencent Hunyuan 3D 2.0 Community License で、MIT と同じ条件の OSS として扱わず、
  任意の追加の生成器にする（この repo には入れない。同じ設定・検査の契約で `kind: multi_view` として呼ぶ）。
- 今の実証は正面 1 枚（`single_view_only: true`）。背面・側面は入力から拘束していない。
