"""RecipeGraph（model3d-recipe/0.1）の読み込み、参照の解決、依存順の決定、工程の key。"""
import hashlib

from . import contracts
from .io import ToolError, canonical_json, resolve_path, sha256

INPUT_PREFIX = "$inputs."
STEP_PREFIX = "$steps."


def parse_ref(value):
    """"$inputs.x" -> ("input", "x") / "$steps.s.o" -> ("step", "s", "o")。"""
    if value.startswith(INPUT_PREFIX):
        return ("input", value[len(INPUT_PREFIX):])
    if value.startswith(STEP_PREFIX):
        rest = value[len(STEP_PREFIX):].split(".")
        if len(rest) == 2 and all(rest):
            return ("step", rest[0], rest[1])
    raise ToolError(f"入力の参照は $inputs.<名前> か $steps.<id>.<出力名> です: {value!r}")


def load(path):
    """検証済みの recipe と、その入力のパス・ハッシュ（宣言された sha256 と違えば止める）。"""
    recipe = contracts.load("recipe", path)
    base = path.parent
    inputs = {}
    for name, spec in recipe["inputs"].items():
        file = resolve_path(base, spec["path"])
        if not file.is_file():
            raise ToolError(f"入力 {name} が見つかりません: {file}")
        digest = sha256(file)
        if spec.get("sha256") and spec["sha256"] != digest:
            raise ToolError(f"入力 {name} のハッシュが宣言と違います（{file}）。入力を確かめてから recipe の sha256 を直してください")
        inputs[name] = {"path": str(file), "sha256": digest, **({"kind": spec["kind"]} if spec.get("kind") else {})}
    return recipe, inputs


def order_steps(recipe):
    """参照と depends_on から依存順に並べた工程の列。重複 id・未知の参照・循環は ToolError。"""
    steps = {}
    for step in recipe["steps"]:
        if step["id"] in steps:
            raise ToolError(f"工程 id が重なっています: {step['id']}")
        steps[step["id"]] = step
    dependencies = {}
    for step in recipe["steps"]:
        needs = set(step.get("depends_on", []))
        for name, ref in step.get("inputs", {}).items():
            parsed = parse_ref(ref)
            if parsed[0] == "input":
                if parsed[1] not in recipe["inputs"]:
                    raise ToolError(f"工程 {step['id']} の入力 {name} が無い入力を指しています: {ref}")
            else:
                _, other, output = parsed
                if other not in steps:
                    raise ToolError(f"工程 {step['id']} の入力 {name} が無い工程を指しています: {ref}")
                if output not in steps[other].get("outputs", {}):
                    raise ToolError(f"工程 {step['id']} の入力 {name} が工程 {other} に無い出力を指しています: {output}")
                needs.add(other)
        for other in needs:
            if other not in steps:
                raise ToolError(f"工程 {step['id']} の depends_on に無い工程があります: {other}")
            if other == step["id"]:
                raise ToolError(f"工程 {step['id']} が自分に依存しています")
        dependencies[step["id"]] = needs
    ordered, done = [], set()
    remaining = [s["id"] for s in recipe["steps"]]
    while remaining:
        progress = False
        for step_id in list(remaining):
            if dependencies[step_id] <= done:
                ordered.append(steps[step_id])
                done.add(step_id)
                remaining.remove(step_id)
                progress = True
        if not progress:
            raise ToolError(f"工程の依存が循環しています: {', '.join(remaining)}")
    return ordered


def step_key(step, input_hashes, fingerprint, library_versions, context_hashes=None):
    """工程の再利用 key。操作名・パラメーター・入力のハッシュ・コードの指紋・ライブラリ版・文脈（preset と対応表の sha256）から作る。

    文脈は全工程の key に入れる（check.structure のように preset や対応表を読む操作が、変更後に古い報告を再利用しないため）。
    設定ファイルや実行ファイルを隠れた入力に持つ操作（external.run・blender.run_script）は key に関わらず再利用しない。
    """
    payload = {"operation": step["operation"], "parameters": step.get("parameters", {}),
               "inputs": dict(sorted(input_hashes.items())), "outputs": dict(sorted(step.get("outputs", {}).items())),
               "fingerprint": fingerprint, "libraries": library_versions, "context": dict(sorted((context_hashes or {}).items())),
               "applies_to": sorted(step.get("applies_to", [])), "preconditions": list(step.get("preconditions", [])),
               "space": step.get("space")}   # space は生成物（deltas）の記録に写るので key に入れる
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
