import ast
import tomllib
from pathlib import Path


def _model3d_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("model3d"))
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("model3d"):
            found.append(node.module)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "import_module" and node.args
              and isinstance(node.args[0], ast.Constant) and str(node.args[0].value).startswith("model3d")):
            found.append(str(node.args[0].value))
    return found


def test_catalog_is_a_neutral_package_not_an_mv_execution_import():
    # 中立のcatalog packageだけが、登録表を読むためにmodel3d.opsを参照してよい。
    catalog_imports = []
    for path in Path("src/orpheus_catalog").rglob("*.py"):
        catalog_imports.extend(_model3d_imports(path))
    assert catalog_imports
    assert set(catalog_imports) == {"model3d.ops"}

    # 制作実行層どうしは従来どおり直接importしない。
    for path in Path("src/mv_pipeline").rglob("*.py"):
        assert _model3d_imports(path) == [], path


def test_orpheus_entrypoint_and_wheel_include_the_neutral_package():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["orpheus"] == "orpheus_catalog.capabilities_cli:main"
    assert "src/orpheus_catalog" in data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]


def test_previous_mv_pipeline_imports_remain_compatible():
    from mv_pipeline import capability_catalog as legacy_catalog
    from mv_pipeline import capability_validation as legacy_validation
    from orpheus_catalog import capability_catalog as catalog, capability_validation as validation

    assert legacy_catalog.build_catalog is catalog.build_catalog
    assert legacy_validation.validate_catalog is validation.validate_catalog
