import copy
import json

import jsonschema
import pytest

from fx_synth import FULL, spec_dict
from mv_pipeline import fx
from mv_pipeline.fx import spec as fxspec
from mv_pipeline.io import PipelineError


def parse(tmp_path, data):
    return fxspec.parse_spec(data, tmp_path)


def test_schema_itself_is_valid_draft_2020_12():
    jsonschema.Draft202012Validator.check_schema(fxspec.schema())


def test_full_spec_loads_and_fills_defaults(tmp_path):
    minimal = {"schema_version": "fx-shot/0.1", "output": {"width": 320, "height": 180}, "duration_s": 3,
               "assets": {"base": "base.png"}}
    spec = parse(tmp_path, minimal)
    data = spec.data
    assert data["output"] == {"width": 320, "height": 180, "fps": 24.0, "crf": 18}
    assert data["seed"] == 0 and data["audio"] is None and data["layers"] is None and data["lights"] == []
    assert data["camera"]["keys"] == [{"t": 0.0, "ease": "linear", "zoom": 1.0, "center": [0.5, 0.5]}]
    assert data["assets"]["depth"] is None and data["assets"]["masks"] == {}
    full = parse(tmp_path, spec_dict()).data
    assert full["assets"]["depth"] == {"path": "depth.png", "near": "white"}
    assert full["assets"]["masks"]["subject"] == {"path": "mask_subject.png", "feather_px": 0.0}
    assert full["assets"]["masks"]["ground"]["feather_px"] == 2.0
    light = full["lights"][0]
    assert light["emitter"] == {"size": 0.03, "glow": 0.6, "attach": None}
    assert light["receivers"][1] == {"mask": "ground", "gain": 0.6, "rim": 0.0, "reflection": 0.5}
    assert full["layers"]["planes"][0]["drift_px"] == [0.0, 0.0]


def test_scalar_intensity_and_int_float_forms_normalize_identically(tmp_path):
    a = spec_dict()
    a["lights"][0]["intensity"] = 1
    b = copy.deepcopy(a)
    b["lights"][0]["intensity"] = 1.0
    b["duration_s"] = 2.0
    assert a["duration_s"] == 2.0
    assert fxspec.canonical_json(parse(tmp_path, a).data) == fxspec.canonical_json(parse(tmp_path, b).data)
    assert parse(tmp_path, a).data["lights"][0]["intensity"] == [{"t": 0.0, "v": 1.0, "ease": "linear"}]


def test_canonical_json_ignores_key_order():
    assert fxspec.canonical_json({"b": 1, "a": [2, {"d": 1, "c": 2}]}) == fxspec.canonical_json(
        {"a": [2, {"c": 2, "d": 1}], "b": 1})


@pytest.mark.parametrize("version", ["fx-shot/0.2", "fx-shot/0", None, 1])
def test_rejects_wrong_schema_version(tmp_path, version):
    data = spec_dict()
    if version is None:
        del data["schema_version"]
    else:
        data["schema_version"] = version
    with pytest.raises(PipelineError, match="schema_version"):
        parse(tmp_path, data)


@pytest.mark.parametrize("missing", ["output", "duration_s", "assets"])
def test_rejects_missing_required_top_level(tmp_path, missing):
    data = spec_dict()
    del data[missing]
    with pytest.raises(PipelineError, match=missing):
        parse(tmp_path, data)


def test_rejects_missing_base_and_output_size(tmp_path):
    data = spec_dict()
    del data["assets"]["base"]
    with pytest.raises(PipelineError, match="base"):
        parse(tmp_path, data)
    data = spec_dict()
    del data["output"]["height"]
    with pytest.raises(PipelineError, match="height"):
        parse(tmp_path, data)


def test_rejects_unknown_keys_at_any_depth(tmp_path):
    for path in [("foo",), ("output", "fpss"), ("lights", 0, "flickr"), ("layers", "planes", 0, "paralax"),
                 ("camera", "keys", 0, "zom"), ("assets", "masks", "ground", "feather")]:
        data = spec_dict()
        node = data
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = 1
        with pytest.raises(PipelineError, match="未知の鍵"):
            parse(tmp_path, data)


def test_allows_x_prefixed_keys_everywhere_and_keeps_them(tmp_path):
    data = spec_dict()
    data["x_note"] = "試行 A"
    data["output"]["x_probe"] = 1
    data["lights"][0]["x_old_radius"] = 0.3
    data["lights"][0]["receivers"][0]["x_why"] = "顔と袖"
    data["layers"]["effects"][0]["x_tuning"] = {"a": 1}
    data["assets"]["masks"]["ground"]["x_src"] = "hand"
    data["camera"]["keys"][1]["x_beat"] = 4
    spec = parse(tmp_path, data)
    assert spec.data["x_note"] == "試行 A" and spec.data["lights"][0]["x_old_radius"] == 0.3
    assert spec.data["lights"][0]["receivers"][0]["x_why"] == "顔と袖"
    assert spec.data["camera"]["keys"][1]["x_beat"] == 4


@pytest.mark.parametrize("bad", ["../evil.png", "/etc/passwd", "a\\b.png", "C:evil.png", "sub/../../x.png", "", "."])
def test_rejects_path_escape_in_every_asset_slot(tmp_path, bad):
    for mutate in [lambda d: d["assets"].__setitem__("base", bad),
                   lambda d: d["assets"]["depth"].__setitem__("path", bad),
                   lambda d: d["assets"]["masks"].__setitem__("subject", bad),
                   lambda d: d["assets"]["masks"]["ground"].__setitem__("path", bad),
                   lambda d: d.__setitem__("audio", {"path": bad})]:
        data = spec_dict()
        mutate(data)
        with pytest.raises(PipelineError):
            parse(tmp_path, data)


def test_relative_subdirectories_are_allowed(tmp_path):
    data = spec_dict()
    data["assets"]["base"] = "plates/night/base.png"
    spec = parse(tmp_path, data)
    assert spec.resolve("plates/night/base.png") == tmp_path.resolve() / "plates/night/base.png"


def test_rejects_dangling_references_and_bad_ordering(tmp_path):
    data = spec_dict()
    data["layers"]["planes"][0]["mask"] = "nope"
    with pytest.raises(PipelineError, match="nope"):
        parse(tmp_path, data)
    data = spec_dict()
    data["lights"][0]["receivers"][0]["mask"] = "nope"
    with pytest.raises(PipelineError, match="nope"):
        parse(tmp_path, data)
    data = spec_dict()
    data["camera"]["keys"][1]["t"] = 0.0
    with pytest.raises(PipelineError, match="camera.keys"):
        parse(tmp_path, data)
    data = spec_dict()
    data["lights"][0]["intensity"][1]["t"] = 0.0
    with pytest.raises(PipelineError, match="intensity"):
        parse(tmp_path, data)


def test_rejects_duplicate_ids(tmp_path):
    data = spec_dict()
    data["layers"]["effects"][0]["id"] = "subject"
    with pytest.raises(PipelineError, match="重複"):
        parse(tmp_path, data)
    data = spec_dict()
    data["lights"].append(copy.deepcopy(data["lights"][0]))
    with pytest.raises(PipelineError, match="重複"):
        parse(tmp_path, data)


def test_rejects_bad_values(tmp_path):
    cases = [
        lambda d: d["output"].__setitem__("width", 161),                      # 偶数のみ（yuv420p）
        lambda d: d.__setitem__("duration_s", 0),
        lambda d: d["lights"][0].__setitem__("color", [256, 0, 0]),
        lambda d: d["lights"][0].__setitem__("position", [1.5, 0.5]),
        lambda d: d["layers"]["effects"][0].__setitem__("type", "rain"),
        lambda d: d["layers"]["effects"][1].__setitem__("count", 0),
        lambda d: d["camera"]["keys"][1].__setitem__("zoom", 0.5),            # 1 未満は画像の外が見える
        lambda d: d["camera"]["keys"][1].__setitem__("ease", "bounce"),
        lambda d: d["assets"]["depth"].__setitem__("near", "grey"),
        lambda d: d["lights"][0]["flicker"].__setitem__("amount", 2),
    ]
    for mutate in cases:
        data = spec_dict()
        mutate(data)
        with pytest.raises(PipelineError, match="invalid"):
            parse(tmp_path, data)


def test_emitter_attach_accepts_a_plane_id_or_background(tmp_path):
    for attach in ("subject", "background"):
        data = spec_dict()
        data["lights"][0]["emitter"]["attach"] = attach
        assert parse(tmp_path, data).data["lights"][0]["emitter"]["attach"] == attach
    assert parse(tmp_path, spec_dict()).data["lights"][0]["emitter"]["attach"] is None      # 未指定 = 自動で決める
    data = spec_dict()
    data["lights"][0]["emitter"]["attach"] = "subject"
    key = fxspec.canonical_json(parse(tmp_path, data).data)
    assert key != fxspec.canonical_json(parse(tmp_path, spec_dict()).data)                  # attach はキャッシュ key に入る


def test_emitter_attach_must_name_an_existing_plane(tmp_path):
    data = spec_dict()
    data["lights"][0]["emitter"]["attach"] = "ghost"
    with pytest.raises(PipelineError, match="attach.*ghost|ghost.*attach"):
        parse(tmp_path, data)
    data = spec_dict(layers=None)                                                          # layers が無ければ plane も無い
    data["lights"][0]["emitter"]["attach"] = "subject"
    with pytest.raises(PipelineError, match="attach"):
        parse(tmp_path, data)
    data = spec_dict()
    data["lights"][0]["emitter"]["attach"] = "no good!"
    with pytest.raises(PipelineError, match="invalid"):
        parse(tmp_path, data)
    data = spec_dict()
    data["layers"]["planes"][0]["id"] = "background"                                       # 背景を指す予約語
    with pytest.raises(PipelineError, match="background"):
        parse(tmp_path, data)


def test_effect_type_errors_are_specific(tmp_path):
    data = spec_dict()
    data["layers"]["effects"][1]["opacity"] = 0.5     # particles に fog の鍵
    with pytest.raises(PipelineError) as error:
        parse(tmp_path, data)
    assert "opacity" in str(error.value) and "未知の鍵" in str(error.value)


def test_warnings_for_inert_settings(tmp_path):
    data = spec_dict()
    del data["assets"]["depth"]
    spec = parse(tmp_path, data)
    assert any("depth" in w and "flame" in w for w in spec.warnings)
    assert any("parallax_px" in w for w in spec.warnings)
    assert parse(tmp_path, spec_dict()).warnings == ()


def test_load_spec_reads_file_and_reports_problems(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(FULL), encoding="utf-8")
    spec = fxspec.load_spec(path)
    assert spec.base_dir == tmp_path.resolve() and len(spec.sha256) == 64
    with pytest.raises(PipelineError, match="not found"):
        fxspec.load_spec(tmp_path / "missing.json")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PipelineError, match="not valid JSON"):
        fxspec.load_spec(path)
    path.write_text('{"schema_version": "fx-shot/0.1", "duration_s": NaN}', encoding="utf-8")
    with pytest.raises(PipelineError, match="Non-finite"):
        fxspec.load_spec(path)


def test_asset_refs_lists_every_role(tmp_path):
    data = spec_dict()
    data["audio"] = {"path": "audio.wav", "offset_s": 1.5}
    refs = fxspec.asset_refs(parse(tmp_path, data).data)
    assert refs == {"base": "base.png", "depth": "depth.png", "mask:subject": "mask_subject.png",
                    "mask:ground": "mask_ground.png", "audio": "audio.wav"}


def test_engine_fingerprint_is_stable_hex_and_covers_schema():
    first = fx.fingerprint()
    assert first == fx.fingerprint() and len(first) == 64 and int(first, 16) >= 0
    assert (fx._PACKAGE / "fx_shot.schema.json").is_file()
