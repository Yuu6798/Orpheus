"""model3d のテスト。`uv sync --locked --extra model3d` で同期していない環境では skip される（黙って緑になるので注意。CI は extra あり）。"""
import pytest

pytest.importorskip("model3d", reason="model3d extra が無い（uv sync --locked --extra model3d）")
