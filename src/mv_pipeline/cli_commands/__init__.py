"""mv の命令のまとまりごとの、組み立て（_X_parser）と処理（X_command）。入口は mv_pipeline.cli（parser・main。古い名前も
そこから読める）。どの処理も「引数 → 結果（dict / list / str / Path / None）」の関数で、重い import（cv2 を読むものなど）は関数の
中で遅らせる（mv の CLI は fx extra なしでも起動する）。"""
import sys

from ..io import PipelineError


def _progress(message):
    print(message, file=sys.stderr, flush=True)


def _require_fx():
    try:
        import cv2  # noqa: F401
    except ModuleNotFoundError as error:
        if error.name != "cv2":
            raise
        raise PipelineError("fx には opencv-python-headless が必要です。`uv sync --locked --extra fx` を実行してください") from error
