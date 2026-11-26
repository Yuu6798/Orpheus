"""SAM 3D Body を Colab で動かすテンプレート。ローカルではノートブックの JSON を組み立てるだけ。"""
import ast
from textwrap import dedent


def _cell(kind, source):
    source = dedent(source).strip() + "\n"
    cell = {"cell_type": kind, "metadata": {}, "source": source.splitlines(keepends=True)}
    if kind == "code":
        ast.parse(source)
        cell.update(execution_count=None, outputs=[])
    return cell


def notebook():
    cells = [
        _cell("markdown", """
            # SAM 3D Body で動画から Orpheus 用の 3D 点を抽出する

            主役が 1 人の固定カメラ・固定フレームレートの動画を使います。
            Google Drive に元動画を置き、次のセルで入力と出力の場所を設定します。
            [重みの利用条件](https://huggingface.co/facebook/sam-3d-body-dinov3) を確認してアクセスを有効にし、
            Colab のシークレットに `HF_TOKEN` を設定して、このノートブックからのアクセスを許可します。
            ランタイムを GPU に変更し、上から実行してください。試しの数コマで人物を正しく囲めているか確認してから全コマを処理します。
            無地の背景では `plain_background`、そうでなければ主役が画面全体に収まる素材で `full_image` を使えます。
        """),
        _cell("code", """
            from pathlib import Path
            import hashlib
            import json
            import math
            import os
            import subprocess
            import sys
            from google.colab import drive

            video_path = ""  # @param {type:"string"}
            output_folder = "/content/drive/MyDrive/orpheus-sam3d"  # @param {type:"string"}
            box_mode = "plain_background"  # @param ["plain_background", "full_image"]

            if not video_path.strip():
                raise ValueError("video_path に Drive の動画のパスを指定してください")
            if box_mode not in {"plain_background", "full_image"}:
                raise ValueError("box_mode は plain_background か full_image です")
            if subprocess.run(["nvidia-smi"], check=False).returncode:
                raise RuntimeError("Colab のランタイムで GPU を選択してください")
            drive.mount("/content/drive")
            video = Path(video_path).expanduser()
            output = Path(output_folder).expanduser()
            if not video.is_file():
                raise FileNotFoundError(f"動画が見つかりません: {video}")
            if not output_folder.strip() or not output.is_absolute():
                raise ValueError("output_folder に絶対パスを指定してください")
            output.mkdir(parents=True, exist_ok=True)
            scratch = Path("/content/orpheus-sam3d")
            scratch.mkdir(exist_ok=True)
        """),
        _cell("code", """
            from importlib import metadata
            from getpass import getpass
            from google.colab import userdata

            sources = {
                "sam-3d-body": ("https://github.com/facebookresearch/sam-3d-body.git",
                                "b5c765a0d89d789985e186d396315e7590887b94"),
                "dinov3": ("https://github.com/facebookresearch/dinov3.git",
                           "91ffb6803c74aeb6927209b82560142743763dd7"),
                "MoGe": ("https://github.com/microsoft/MoGe.git", "07444410f1e33f402353b99d6ccd26bd31e469e8"),
                "utils3d": ("https://github.com/EasternJournalist/utils3d.git",
                            "3fab839f0be9931dac7c8488eb0e1600c236e183"),
            }
            sam_weights = ("facebook/sam-3d-body-dinov3", "11aaa346c7204874a1cbafe3d39a979080b2c55a")
            fov_weights = ("Ruicheng/moge-2-vitl-normal", "b135031bae30b5ac2ae141a0e68717795ce38340")

            def checkout(name):
                url, sha = sources[name]
                folder = scratch / f"{name}-{sha}"
                folder.mkdir(exist_ok=True)
                current = subprocess.run(
                    ["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True
                ).stdout.strip()
                if current != sha:
                    subprocess.run(["git", "-C", str(folder), "init", "-q"], check=True)
                    subprocess.run(["git", "-C", str(folder), "fetch", "-q", "--depth=1", url, sha], check=True)
                    subprocess.run(["git", "-C", str(folder), "checkout", "-q", "--detach", sha], check=True)
                return folder

            sam_source = checkout("sam-3d-body")
            dino_source = checkout("dinov3")
            constraints = scratch / "torch-constraints.txt"
            installed = {}
            for package in ("torch", "torchvision", "torchaudio"):
                try:
                    installed[package] = metadata.version(package)
                except metadata.PackageNotFoundError:
                    pass
            constraints.write_text("".join(f"{name}=={version}\\n" for name, version in installed.items()))
            pip = [sys.executable, "-m", "pip", "install", "-q", "-c", str(constraints)]
            dependencies = [
                "huggingface_hub>=0.34,<2", "pytorch-lightning>=2.0,<3", "timm>=0.9,<2",
                "hydra-core>=1.3,<1.4", "omegaconf>=2.3,<2.4", "trimesh<5", "torchmetrics<2",
                "yacs", "einops", "dill", "rich", "pyrootutils", "webdataset", "roma", "joblib",
                "loguru", "optree", "fvcore", "pycocotools", "braceexpand", "iopath", "tabulate",
                "cloudpickle", "termcolor", "click", "ftfy", "regex",
            ]
            utils_url, utils_sha = sources["utils3d"]
            moge_url, moge_sha = sources["MoGe"]
            subprocess.run([*pip, *dependencies, f"utils3d @ git+{utils_url}@{utils_sha}"], check=True)
            subprocess.run([*pip, "--no-deps", f"git+{moge_url}@{moge_sha}"], check=True)

            from huggingface_hub import hf_hub_download, snapshot_download

            def model_token():
                try:
                    secret = userdata.get("HF_TOKEN")
                except (userdata.SecretNotFoundError, userdata.NotebookAccessError):
                    secret = None
                token = secret or os.environ.get("HF_TOKEN") or getpass("Hugging Face token: ").strip()
                if not token:
                    raise ValueError("Hugging Face のトークンが必要です")
                return token

            weights = output / ".weights"
            sam_dir = weights / sam_weights[1]
            if not all((sam_dir / file).is_file() for file in ("model.ckpt", "model_config.yaml", "assets/mhr_model.pt")):
                snapshot_download(sam_weights[0], revision=sam_weights[1], local_dir=sam_dir, token=model_token())
            fov_dir = weights / fov_weights[1]
            fov_file = fov_dir / "model.pt"
            if not fov_file.is_file():
                hf_hub_download(fov_weights[0], "model.pt", revision=fov_weights[1], local_dir=fov_dir)
        """),
        _cell("code", """
            from unittest.mock import patch
            import cv2
            import numpy as np
            import torch
            from IPython.display import display
            from PIL import Image

            if not torch.cuda.is_available():
                raise RuntimeError("PyTorch から Colab の GPU が見つかりません")
            os.environ["MOMENTUM_ENABLED"] = "0"
            if str(sam_source) not in sys.path:
                sys.path.insert(0, str(sam_source))

            from sam_3d_body import SAM3DBodyEstimator, load_sam_3d_body
            from tools.build_fov_estimator import FOVEstimator

            for name in ("estimator", "fov", "model"):
                globals().pop(name, None)
            torch.cuda.empty_cache()
            hub_load = torch.hub.load

            def local_dinov3(repository, model, *args, **kwargs):
                if str(repository).partition(":")[0] == "facebookresearch/dinov3":
                    for key in ("source", "trust_repo", "force_reload", "skip_validation"):
                        kwargs.pop(key, None)
                    return hub_load(str(dino_source), model, *args, source="local", **kwargs)
                return hub_load(repository, model, *args, **kwargs)

            device = torch.device("cuda")

            def video_info():
                capture = cv2.VideoCapture(str(video))
                try:
                    fps = capture.get(cv2.CAP_PROP_FPS)
                    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                    if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or count <= 0:
                        raise ValueError("動画を開けません。一定フレームレートの動画を指定してください")
                    previews = []
                    for index in np.unique(np.linspace(0, count - 1, min(count, 9)).round().astype(int)):
                        capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
                        ok, bgr = capture.read()
                        if not ok:
                            raise ValueError(f"試しのコマ {index} を読めません")
                        previews.append((int(index), cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
                    return fps, count, previews
                finally:
                    capture.release()

            fps, frame_count, previews = video_info()
            fov = FOVEstimator(name="moge2", device=device, path=str(fov_file))
            matrices = []
            for _, rgb in previews:
                matrix = fov.get_cam_intrinsics(rgb).detach().cpu().numpy().reshape(3, 3)
                if np.isfinite(matrix).all() and matrix[0, 0] > 0 and matrix[1, 1] > 0:
                    matrices.append(matrix)
            del fov
            torch.cuda.empty_cache()
            if not matrices:
                raise RuntimeError("焦点距離を推定できません")
            intrinsics = np.median(matrices, axis=0).astype(np.float32)
            camera = torch.from_numpy(intrinsics[None])

            # MoGe の GPU メモリを解放してから SAM 3D Body を読み込む。
            # モデル構築時だけ読み先を差し替え、セルを再実行しても前の関数を包み直さない。
            with patch.object(torch.hub, "load", local_dinov3):
                model, model_config = load_sam_3d_body(
                    str(sam_dir / "model.ckpt"), device=device, mhr_path=str(sam_dir / "assets/mhr_model.pt")
                )
            estimator = SAM3DBodyEstimator(model, model_config, human_detector=None, human_segmentor=None)

            def person_box(rgb):
                h, w = rgb.shape[:2]
                full = np.array([0, 0, w, h], dtype=np.float32)
                if box_mode == "full_image":
                    return full, True
                border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
                distance = np.linalg.norm(rgb.astype(np.float32) - np.median(border, axis=0), axis=2)
                mask = cv2.morphologyEx((distance > 18).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
                ys, xs = np.nonzero(mask)
                if len(xs) < max(16, h * w * 0.001):
                    return full, True
                x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
                dx, dy = (x1 - x0) * 0.08, (y1 - y0) * 0.06
                return np.array([max(0, x0-dx), max(0, y0-dy), min(w, x1+dx), min(h, y1+dy)], dtype=np.float32), False

            def frame_prediction(rgb):
                box, full = person_box(rgb)
                predictions = estimator.process_one_image(rgb, bboxes=box[None], cam_int=camera)
                if not predictions and not full:
                    h, w = rgb.shape[:2]
                    box, full = np.array([0, 0, w, h], dtype=np.float32), True
                    predictions = estimator.process_one_image(rgb, bboxes=box[None], cam_int=camera)
                if len(predictions) != 1:
                    raise ValueError("1 人分の点を推定できません")
                pred = predictions[0]
                shapes = {"pred_keypoints_3d": (70, 3), "pred_keypoints_2d": (70, 2), "pred_cam_t": (3,)}
                result = {key: np.asarray(pred[key], dtype=np.float32) for key in shapes}
                if any(result[key].shape != shape or not np.isfinite(result[key]).all()
                       for key, shape in shapes.items()):
                    raise ValueError("SAM 3D Body の推定結果が不正です")
                return {**result, "bbox": box, "used_full_image_box": np.bool_(full)}

            for position in sorted({0, len(previews) // 2, len(previews) - 1}):
                index, rgb = previews[position]
                sample = frame_prediction(rgb)
                check = rgb.copy()
                x0, y0, x1, y1 = sample["bbox"].astype(int)
                cv2.rectangle(check, (x0, y0), (x1, y1), (50, 255, 150), 2)
                for x, y in sample["pred_keypoints_2d"].astype(int):
                    cv2.circle(check, (x, y), 3, (255, 210, 50), -1)
                thumbnail = Image.fromarray(check)
                thumbnail.thumbnail((420, 420))
                print(f"確認コマ {index} / 画像全体の枠: {bool(sample['used_full_image_box'])}")
                display(thumbnail)
        """),
        _cell("markdown", """
            確認画像で枠と 2D の点が主役に合っていれば、次のセルで全コマを処理します。
            途中までの結果は入力動画と設定ごとに Drive に保存されます。
        """),
        _cell("code", """
            from tempfile import NamedTemporaryFile
            from zipfile import BadZipFile

            def atomic_npz(destination, **arrays):
                with NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
                    temp = Path(stream.name)
                    try:
                        np.savez_compressed(stream, **arrays)
                        stream.close()
                        temp.replace(destination)
                    finally:
                        temp.unlink(missing_ok=True)

            with video.open("rb") as stream:
                video_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            settings = {
                "template": 1, "video": video_hash, "box_mode": box_mode, "matrix": intrinsics.tolist(),
                "sources": sources, "weights": [sam_weights, fov_weights], "torch": torch.__version__,
                "opencv": cv2.__version__, "fps": fps, "frames": frame_count,
            }
            key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
            run_dir = output / key
            cache = run_dir / "frames"
            cache.mkdir(parents=True, exist_ok=True)
            shapes = {"pred_keypoints_3d": (70, 3), "pred_keypoints_2d": (70, 2), "pred_cam_t": (3,),
                      "bbox": (4,), "used_full_image_box": ()}

            def read_cached_frame(target):
                if not target.is_file():
                    return None
                try:
                    with np.load(target, allow_pickle=False) as stored:
                        arrays = {name: stored[name] for name in shapes}
                    for name, shape in shapes.items():
                        dtype = np.bool_ if name == "used_full_image_box" else np.float32
                        if arrays[name].shape != shape or arrays[name].dtype != dtype or not np.isfinite(arrays[name]).all():
                            return None
                    return arrays
                except (BadZipFile, EOFError, KeyError, TypeError, ValueError, OSError):
                    return None

            collected = {name: [] for name in shapes}
            capture = cv2.VideoCapture(str(video))
            try:
                for index in range(frame_count):
                    ok, bgr = capture.read()
                    if not ok:
                        raise ValueError(f"動画のコマ {index} を読めません。動画を事前に再エンコードしてください")
                    target = cache / f"{index:07d}.npz"
                    arrays = read_cached_frame(target)
                    if arrays is None:
                        arrays = frame_prediction(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                        atomic_npz(target, **arrays)
                    for name in shapes:
                        collected[name].append(arrays[name])
                    if (index + 1) % 30 == 0 or index + 1 == frame_count:
                        print(f"{index + 1}/{frame_count} コマ")
                if capture.read()[0]:
                    raise ValueError("実際のコマ数とコンテナのコマ数が違います。動画を事前に再エンコードしてください")
            finally:
                capture.release()

            arrays = {name: np.stack(rows) for name, rows in collected.items()}
            counts = np.arange(frame_count, dtype=np.int64)
            result = run_dir / "keypoints.npz"
            atomic_npz(result, **arrays, fps=np.float64(fps), frame_index=counts, t=counts / fps,
                       valid=np.ones(frame_count, dtype=bool), focal_length=np.full(frame_count, intrinsics[1, 1]),
                       cam_int_fixed=intrinsics, cam_int=np.broadcast_to(intrinsics[None, None], (frame_count, 1, 3, 3)))
            (run_dir / "source.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
            print(f"保存: {result}")
            print(f"画像全体の枠: {int(arrays['used_full_image_box'].sum())}/{frame_count}")
        """),
    ]
    return {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {"accelerator": "GPU", "kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "cells": cells,
    }
