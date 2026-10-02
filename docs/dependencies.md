# 依存関係の記録

検証日：2026-10-02。Pythonはuv.lock、Nodeはpnpm-lock.yamlに解決済み依存を固定しています。

| 項目 | 採用版 / 環境 |
| --- | --- |
| Python | 3.12.14（対応範囲3.11〜3.13） |
| Node.js | 24.19.0 |
| uv | 0.12.22 |
| pnpm | 11.19.0 |
| Remotion関連 | 4.0.532（全パッケージ同版） |
| React / React DOM | 19.2.0 |
| TypeScript | 5.9.3 |
| librosa | 0.11.0 |
| numba / llvmlite | 0.63.1 / 0.46.0（Windows実行確認済みの組合せ） |
| imageio-ffmpeg | 0.6.0 |
| JSON Schema | Draft 2020-12 / jsonschema 4.26.0 |
| GPUとして検出した機器 | AMD Radeon 840M Graphics |

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
