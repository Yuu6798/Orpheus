from model3d import ops


def test_registered_operation_can_publish_optional_capability_metadata(monkeypatch):
    monkeypatch.setattr(ops, "load_all", lambda: ops.OPERATIONS)
    name = "test.capability_metadata"
    previous = ops.OPERATIONS.get(name)
    metadata = {
        "aliases": ["試験用の道具"],
        "tags": ["test"],
        "maturity": "experimental",
        "parameters": {"status": "schema", "schema": {"type": "object"}},
    }
    try:
        @ops.register(name, inputs=("source",), outputs=("report",), summary="試験", capability=metadata)
        def operation(inputs, parameters, outputs, context):
            return ops.StepResult()

        metadata["aliases"].append("登録後に元を変更")
        row = next(item for item in ops.describe() if item["operation"] == name)
        assert row["capability"]["aliases"] == ["試験用の道具"]
        row["capability"]["aliases"].append("変更")
        assert ops.OPERATIONS[name].capability["aliases"] == ["試験用の道具"]
    finally:
        if previous is None:
            ops.OPERATIONS.pop(name, None)
        else:
            ops.OPERATIONS[name] = previous


def test_capability_metadata_must_be_a_dictionary():
    try:
        ops.register("test.bad_capability", capability=["not", "a", "dict"])
    except TypeError as error:
        assert str(error) == "capability は辞書か None にしてください"
    else:
        raise AssertionError("辞書でない capability を受け付けました")
