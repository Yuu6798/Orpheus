"""能力カタログの実環境に対する厳密な参照検査。"""
from __future__ import annotations

from typing import Any

from .capability_catalog import build_catalog, validate_catalog as validate_shape


def validate_catalog(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """外付けコンポーネントが読めたのに存在しない能力参照も不合格にする。

    コンポーネント自体を導入していない環境では、その領域の能力は候補として残し、
    導入不足をカタログ全体の破損とは扱わない。
    """
    data = catalog or build_catalog()
    report = validate_shape(data)
    errors = list(report["errors"])
    seen = {(error.get("code"), error.get("id"), error.get("capability")) for error in errors}
    for item in data.get("capabilities", []):
        availability = item.get("availability", {})
        if availability.get("available") is False and availability.get("reason") == "現在の版に登録されていません":
            error = {"code": "UNKNOWN_CAPABILITY", "id": item.get("id")}
            key = (error["code"], error["id"], None)
            if key not in seen:
                errors.append(error)
                seen.add(key)
    return {**report, "valid": not errors, "errors": errors}
