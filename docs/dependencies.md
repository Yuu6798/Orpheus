# 依存関係の記録

検証日：2026-10-02。Pythonはuv.lock、Nodeはpnpm-lock.yamlに解決済み依存を固定しています。

| 項目 | 採用版 / 環境 |
| --- | --- |
| Python | 3.12.14（対応範囲3.11〜3.13） |
| Node.js | 24.19.0 |
| uv | 0.13.5（`pyproject.toml` の `required-version = ">=0.13.5"` で強制） |
| pnpm | 11.19.0 |
| Remotion関連 | 4.0.532（全パッケージ同版） |
| React / React DOM | 19.2.0 |
| TypeScript | 5.9.3 |
| librosa | 0.11.0 |
| numba / llvmlite | 0.63.1 / 0.46.0（Windows実行確認済みの組合せ） |
| imageio-ffmpeg | 0.6.0 |
| Pillow | 13.0.0 |
| pytest（dev） | 9.0.3 |
| pip-audit（audit group） | 2.10.1 |
| JSON Schema | Draft 2020-12 / jsonschema 4.26.0 |
| GPUとして検出した機器 | AMD Radeon 840M Graphics |

## 2026-10-24 更新（安全網）

- **uv 0.13.5 以上**：0.13.4 未満はemSign ECC Root CA - G3を含むCA bundleを同梱します（GHSA-hq85-5cpj-mr62）。古いuvで `uv lock` / `uv sync` を回さないよう、`[tool.uv] required-version` で拒否します。`uv.lock` は0.13.5で再生成し、`revision = 5` のままです。更新は `uv self update` か `pip install -U "uv>=0.13.5"`。
- **Pillow 12.1.1 → 13.0.0**：12.1.1には `pip-audit` が報告する既知の脆弱性が複数あり、修正版は13.0.0です。更新後は `pip-audit` の指摘がゼロです。
- **pytest 9.0.2 → 9.0.3**（dev）。
- **pip-audit 2.10.1**：`[dependency-groups] audit` に置き、`uv.lock` で固定します。実行例：
  `uv sync --locked --only-group audit --no-install-project` →
  `uv export --frozen --all-extras --all-groups --no-emit-project --no-hashes -o req.txt` →
  `uv run --no-sync pip-audit --strict --no-deps --disable-pip -r req.txt`
- **fast-uri 3.1.8 → 3.1.9**（`pnpm update fast-uri --depth Infinity --lockfile-only`）：Remotion bundlerの推移依存で、`pnpm audit --prod` の5件（high 1・moderate 2・low 2）のうち、fast-uriの4件（moderate 2・low 2。修正版は3.1.9以上）が解消します。`package.json` とRemotionの版は変えていません。
- **zod は据え置き**：Remotionが4.5.4に固定しており、`pnpm audit --prod` に残る指摘は `zod`（<4.6.6）の1件のみです。Remotionが検証していない版の組合せになるため、zodだけを上書きせず、Remotion更新の機会に解消します。CIでは `pnpm audit` をreport-onlyにしています。

GPU情報はOS報告であり、利用可能VRAMやモデル性能を保証しません。描画はCPU経路です。
既存HTDemucs/Qwenの実行経路は未設定です。大型モデルを自動導入せず、別環境のCLIを接続できます。

## 利用条件

導入したRemotion 4.0.532の `node_modules/remotion/LICENSE.md` を確認しました。
この版は個人、従業員3名以下の営利組織、非営利組織、商用利用前の評価などにFree Licenseの区分があります。
適用外の組織はCompany Licenseの対象です。価格や資格の判断は公式条件を確認してください。
Editor Starterの購入・導入はしていません。

- [導入版のRemotionライセンス](https://github.com/remotion-dev/remotion/blob/v4.0.532/LICENSE.md)
- [公式ライセンス](https://www.remotion.dev/license)
- [公式描画API](https://www.remotion.dev/docs/renderer/render-media)
- [公式字幕データ](https://www.remotion.dev/docs/captions/)

FFmpegの版は各analysisのprovenanceへ記録します。Qwen/Demucsの版とモデルは利用する別環境の実値を設定し、モデルの利用条件も別途確認してください。
画像API・有料クラウド・有料テンプレートの契約や課金はこの実装では行いません。
