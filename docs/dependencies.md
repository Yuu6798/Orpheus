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
| opencv-python-headless（fx extra） | 4.14.0.94（Apache-2.0。実験的な演出エンジン fx だけが使う。公開 2026-07-28、NumPy 2.3.5・Python 3.11〜3.13 で pytest 通過） |
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

## 2026-10-24 更新（fx）

- **opencv-python-headless 4.14.0.94**（`fx` extra・Apache-2.0）：実験的な演出エンジン `mv fx …` だけが使い、既存コマンドは入れなくても動きます。公開2026-07-28、cp37-abi3 の wheel、NumPy 2.3.5・Python 3.11〜3.13 で pytest 通過。導入は `uv sync --locked --extra fx`。
- **fx は ffmpeg だけを使い、ffprobe に依存しません**：構造検査（technical_pass）は `ffmpeg -i` と `ffmpeg -progress` で行います。ffmpeg は PATH のもの、なければ imageio-ffmpeg 同梱版を使います（PATH に ffprobe が無い環境でも動きます）。`ORPHEUS_FFMPEG` で差し替えられます。
- CI の python job は `--extra fx` で同期し、pytest の後に合成素材（320x180・2秒）で `mv fx demo → mv fx render` の technical_pass を確認します。

## 2026-10-30 更新（Windows のスマート アプリ コントロール）

Windows 11 のスマート アプリ コントロール（SAC）が「オン」の PC では、uv が入れる Python（python-build-standalone）に署名が無いため、`python313.dll` の読み込みが止められて Python 自体が起動しません（終了コード `-1058471934` = `0xC0E90002`、CodeIntegrity のイベント 3077・3033）。SAC は「評価中」の間は何も止めず、その後自動でオンになることがあります（Microsoft の説明）。実測した PC でも、以前は動いていた同じ Python が、ある日から止められていました。SAC は一度切ると Windows の版によっては再インストールしないと戻せないため、切らずに次の手順で対処します。手順と結果は 2026-10-30 に、SAC がオンの Windows 11 で実測したものです。

1. **状態を見ます**（読み取りだけ）
   - SAC の状態：`(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy').VerifiedAndReputablePolicyState`（0 = オフ、1 = オン、2 = 評価中）
   - 止められたファイル：`Get-WinEvent -LogName 'Microsoft-Windows-CodeIntegrity/Operational' -MaxEvents 3000 | Where-Object Id -in 3033,3077`。このログは上書き式で（実測した PC では 1MB）、古い記録は残りません。
2. **署名つき Python を置きます**（インストール不要。リポジトリの外で、AppData の外）
   - python.org の NuGet パッケージ（例：`https://api.nuget.org/v3-flatcontainer/python/3.13.17/python.3.13.17.nupkg`。3.13.17 の SHA256 は `DCE409744B088F94C6E37BA41F8100C384C27E029E843237364C602D30D475E5`）を取得し、拡張子を `.zip` にして `%USERPROFILE%\.local\share\orpheus-python\3.13.17` に展開します。
   - `Get-AuthenticodeSignature` で `tools\python.exe` と `tools\python313.dll` が `Valid`・`CN=Python Software Foundation` であることを確かめます。
   - **AppData の下には置きません**：Claude デスクトップアプリのようにパッケージ化されたアプリ（MSIX）の中から AppData に書いたファイルは、アプリ専用の場所（`%LOCALAPPDATA%\Packages\<アプリ>\LocalCache`）へ振り替えられます（実測）。振り替えられたファイルは他のアプリからは見えないため（Microsoft の説明）、そこに置いた Python で作った .venv は、そのアプリの外のターミナルでは基の Python を見失います。
3. **.venv をこの Python で作ります**
   - 同じ PowerShell で `$env:UV_PYTHON_DOWNLOADS = "never"` にしてから、`uv sync --locked --extra analysis --extra fx --python "$env:USERPROFILE\.local\share\orpheus-python\3.13.17\tools\python.exe"` を実行します。既存の .venv が別の Python で作られていれば、uv が消して作り直します。その .venv の python が実行中（別のターミナルやサーバーなど）だと消せずに失敗するので、先に止めます。
   - 確認：`.venv\Scripts\python.exe -c "import sys; print(sys.base_prefix)"` が上の `tools` を指し、`.venv\Scripts\python.exe` の署名が `Valid` であること。
4. **以後は通常どおりです**：署名つき Python の .venv ができれば、`--python` を付けない `uv sync --locked …` でも .venv はそのまま保たれ、`uv run pytest`・`uv run mv …`・`.venv\Scripts\mv.exe`（mv.exe 自体は署名なし）も動きます。
   - .venv を新しく作るとき（.venv を消した・新しく clone した・Python の版を変えた）は、手順 3 のとおり `UV_PYTHON_DOWNLOADS=never` と `--python` を付けます。付けないと、uv が自分で入れた署名なしの Python を選び、また起動しなくなるおそれがあります（未検証）。

- **通ったもの**：署名なしの拡張モジュール（numpy・OpenCV・Pillow・rpds・scipy・soundfile・soxr・scikit-learn・llvmlite・numba・librosa）の import、librosa のリサンプリング（44.1kHz の音声を 22050Hz で読む）、`uv run pytest`（375 passed・1 skipped）、`mv fx render`（1920x1080 の 4 版とグリッドが technical_pass）。uv.exe・Git for Windows・GitHub CLI は署名つきで、そのまま動きました。
- **一度だけ止められた例**：soxr の拡張モジュール（`soxr_ext.pyd`）が 1 回だけ止められ（イベント 3077）、その後の実行では通りました。原因は確かめていません。import が `DLL load failed` と「アプリケーション制御ポリシーによってこのファイルがブロックされました」で失敗したら、手順 1 のログでファイル名を控え、もう一度実行して再現するかを確かめます。
- **未確認**：Node.js・pnpm（Remotion の経路）の SAC 下での動作。

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
