"""登録済みの操作。工程は `execute(operation, inputs, parameters, context)` の形で呼ばれ、生成物・観測値・未解決事項を返す。"""
import importlib
from dataclasses import dataclass, field

from ..io import ToolError

OPERATIONS = {}
MODULES = ("mesh_ops", "collision_ops", "check_ops", "external_ops", "blender_ops")


@dataclass
class StepResult:
    """操作の結果。生成物は outputs に宣言したファイル（実行器が存在とハッシュを確かめる）。"""
    observations: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    review: bool = False


@dataclass
class Operation:
    name: str
    function: object
    outputs: tuple
    summary: str
    inputs: tuple = ()


def register(name, *, outputs=(), inputs=(), summary=""):
    def wrap(function):
        OPERATIONS[name] = Operation(name, function, tuple(outputs), summary, tuple(inputs))
        return function
    return wrap


def load_all():
    for module in MODULES:
        importlib.import_module(f"{__name__}.{module}")
    return OPERATIONS


def get(name):
    load_all()
    if name not in OPERATIONS:
        raise ToolError(f"知らない操作です: {name}（使える操作: {', '.join(sorted(OPERATIONS))}）")
    return OPERATIONS[name]


def describe():
    load_all()
    return [{"operation": op.name, "inputs": list(op.inputs), "outputs": list(op.outputs), "summary": op.summary}
            for op in sorted(OPERATIONS.values(), key=lambda o: o.name)]


def require(inputs, names, operation):
    missing = [n for n in names if n not in inputs]
    if missing:
        raise ToolError(f"{operation} に入力 {', '.join(missing)} が要ります")


def number(parameters, key, default=None, minimum=None, exclusive=False, integer=False):
    value = parameters.get(key, default)
    if value is None:
        raise ToolError(f"パラメーター {key} が要ります")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"パラメーター {key} は数にしてください: {value!r}")
    if integer and not isinstance(value, int):
        raise ToolError(f"パラメーター {key} は整数にしてください: {value!r}")
    if minimum is not None and ((value <= minimum) if exclusive else (value < minimum)):
        raise ToolError(f"パラメーター {key} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    return value
