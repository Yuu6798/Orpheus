# Colab で SAM 3D Body の点を抽出する

`motion.extract_s3d_colab` は、Google Colab で動画を SAM 3D Body にかけるための `.ipynb` を書く工程。
ローカルのレシピ実行はファイルの生成まで。GPU での推定と重みのダウンロードは、利用者が Colab でノートブックを実行したときに行う。

```json
{
  "schema_version": "model3d-recipe/0.1",
  "name": "extract-keypoints",
  "inputs": {},
  "steps": [
    {
      "id": "notebook",
      "operation": "motion.extract_s3d_colab",
      "outputs": {"notebook": "extract-keypoints.ipynb"}
    }
  ]
}
```

1. このレシピを `model3d run` で実行し、出力したノートブックを Colab に開く。
2. [Hugging Face のモデルページ](https://huggingface.co/facebook/sam-3d-body-dinov3) で利用条件を確認し、重みへのアクセスを得る。Colab のシークレット `HF_TOKEN` にアクセストークンを登録し、このノートブックからのアクセスを許可する。未設定なら実行中に入力できる。
3. GPU を選び、Google Drive に置いた動画のパスと出力フォルダを設定する。固定カメラ・固定フレームレートで、画面に主役が 1 人いる素材を使う。背景が無地なら前景から枠を取り、難しい背景では画面全体を使う設定も選べる。
4. 前半のセルを実行し、試しのコマの枠と 2D の点を確認してから最後のセルを実行する。Drive に途中の推定が残る。入力と設定が同じなら途中から再開できる。
5. 出力の `keypoints.npz` を作業側に移し、[点の下ごしらえ](model3d.md#推定した-3d-の点を整える) の `keypoints` へ渡す。
   `pred_keypoints_3d` と `pred_cam_t` のほか、fps・時刻・枠・焦点距離・全画面の枠を使ったコマを記録する。

SAM 3D Body・DINOv3・MoGe・utils3d と重みのリビジョンを固定する。Colab が提供する PyTorch の GPU ビルドを保つため、Python ランタイムとその周辺ライブラリは Colab 環境の値を使う。
この工程のレシピ実行、セルの構文と枠の処理はローカルで確認した。生成されたノートブックでの Colab GPU の通し実行は未確認。
