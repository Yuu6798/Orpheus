"""出力の金型（リファクタリングの安全網）: テストの入力から作った出力のバイト列の sha256 を、記録と比べる（1 ビットの変化も拾う）。

- plan.orbit と plan.shots: 動く踊り手の点の通り道と、区間・キーのつなぎ方・手持ちの揺れと打点・踊りの止め・寄りと揺れ・手直し・
  回り込み・つなぎの相手・基準のカメラを使う表から、レシピで作ったファイル（cameras・track・alt・alt_track・report）。
- executor.run_recipe: model3d demo のレシピを、初めて・再利用・回し直し・失敗・要確認（対応表の役割が未確認）で回した記録
  （run_record.json）・run.log・進み具合の行と、工程の出力のファイル。記録は、時刻・秒・run の名前・一時フォルダのパス・Python の版と
  OS の名前と、.py を変えると変わる値（コードの指紋と、それを含む工程の key）を伏せて比べる。キーの順はそのまま比べる。
  demo の対応表は preset の絶対パスを持つので、比べる前に固定の名前に書き換える。設定画からの生成のレシピは、出力に時刻と実行ファイルの
  パスとハッシュが入るので比べない。

浮動小数の最後のビットは環境（OS・Python・numpy・Pillow。CPU の命令でも変わりうる）で変わりうるので、記録は環境ごとに持つ。記録の
無い環境（CI のランナーなど）では、比べずに飛ばす（理由に値を出す）。意図して出力を変えたときは ORPHEUS_UPDATE_GOLDEN=1 で走らせて、
この環境の記録を書き直す。"""
import hashlib
import json
import math
import os
import platform
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import PIL
import pytest

from model3d.io import atomic_json, read_json

RECORD = Path(__file__).parent / "data" / "output_golden_model3d.json"
# .py を変えると変わる値（engine の fingerprint と、それを含む工程の key）と、実行ごと・機械ごとに変わる値
HIDDEN = {"created_at", "finished_at", "started_at", "seconds", "run_id", "reused_from", "key", "fingerprint", "python", "platform"}
RUN_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}(?:-[0-9]+)?")     # run の名前（UTC の時刻 + recipe の sha256 の頭）


def environment():
    return (f"{platform.system()}-{platform.machine()}-py{sys.version_info[0]}.{sys.version_info[1]}"
            f"-np{np.__version__}-pil{PIL.__version__}")


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def check(name, values):
    """values（名前 → sha256）を、この環境の記録 name と比べる。"""
    records = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.exists() else {}
    env = environment()
    if os.environ.get("ORPHEUS_UPDATE_GOLDEN") == "1":
        records.setdefault(env, {})[name] = values
        RECORD.write_text(json.dumps(records, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return
    expected = records.get(env, {}).get(name)
    if expected is None:
        pytest.skip(f"出力の金型の記録が無い環境です（比べていない）: {json.dumps({env: {name: values}}, sort_keys=True)}")
    assert values == expected, f"{name} の出力が金型と違います（{env}）: {sorted(k for k in values if values[k] != expected.get(k))}"


def _bone_path(seconds=4.0, rate=60.0):
    """動く踊り手: 腰・胸・顔が横に揺れながら前へ進み、上下に弾む（踊りの時刻 0 秒からを rate Hz で測った、24 fps の動きのコマ）。"""
    times = np.arange(int(seconds * rate)) / rate

    def point(y, z, lean):
        return [[0.3 * math.sin(1.3 * t) + lean * math.sin(2.1 * t), y + 0.05 * t, z + 0.04 * math.sin(4.0 * t)] for t in times]
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": (1.0 + times * 24.0).tolist(), "bones": {},
            "points": {"hips": point(0.0, 0.9, 0.0), "chest": point(0.0, 1.25, 0.03), "face": point(-0.05, 1.55, 0.06)}}


ORBIT = {"schema_version": "model3d-orbit-plan/0.1", "fps": 30, "frames": 90, "time_offset": 0.5,
         "dance": {"frame0": 1, "fps": 24, "holds": [{"from": 2.0, "until": 2.4, "at": 1.9}]},
         "smooth": {"sigma_s": 0.1}, "grid": {"k0_time": 0.25, "period": 0.5}, "marks": {"drop": 2.6},
         "kicks": [{"at": 1.2, "deg": 0.8}, {"at": {"beat": 4}, "deg": 0.5}],
         "effects": [{"kind": "push", "at": [1.0], "amount": 0.08}, {"kind": "shake", "at": 1.5},
                     {"kind": "whip", "at": "drop"}, {"kind": "beat_push", "from": 0.5, "until": 1.5}],
         "shots": [{"name": "wide", "until": 1.4, "handheld": 0.3, "keys": [
                        {"az": -20.0, "dist": 3.0, "height": 1.2, "aim": "chest", "fov": 40.0, "ease": "out"},
                        {"u": 0.5, "az": 10.0, "dist": 2.5, "height": {"above": 0.3}, "aim": "face", "dz": 0.05, "fov": 35.0,
                         "roll": 4.0, "ease": "whip"},
                        {"az": 30.0, "dist": 2.2, "height": 1.0, "aim": "hips", "fov": 30.0, "ease": "in"}]},
                   {"name": "top", "until": "drop", "effects": False, "keys": [
                        {"az": 30.0, "dist": 0.3, "height": 3.0, "aim": "hips", "fov": 50.0, "ease": "linear"},
                        {"az": 60.0, "dist": 0.2, "height": 3.2, "aim": "hips", "fov": 55.0}]},
                   {"keys": [{"az": 90.0, "dist": 2.0, "height": 0.05, "aim": "face", "fov": 45.0}]}]}

SHOTS = {"schema_version": "model3d-shot-plan/0.1", "fps": 30, "frames": 75, "time_offset": 1.0,
         "room": {"focal_px": 2000.0, "center_px": [540.0, 800.0], "camera_height": 1.0, "origin_px": [600.0, 1800.0], "scale": 1.2},
         "dance": {"fps": 24.0, "holds": [{"from": 3.0, "until": 3.2, "at": 2.9}]}, "smooth": {"sigma_s": 0.05},
         "follow": {"samples": 9, "default": 0.6}, "handheld": {"seed": 5}, "kicks": [{"at": 1.4, "deg": 0.5}],
         "wipes": [{"at": 2.0, "seconds": 0.2}], "references": [{"name": "start", "shot": "A"}, {"name": "round", "shot": "B", "at": 2.4}],
         "shots": [{"name": "A", "until": 2.0, "handheld": 0.2, "follow": 0.9,
                    "fix": {"dx": [0.0, 0.05], "dyaw": 1.5, "zoom": [1.0, 1.1], "px": 4.0},
                    "keys": [{"move": [0.0, 0.2, 0.3], "yaw": -4.0, "pitch": 3.0, "roll": 2.0, "zoom": 1.5, "anchor": "chest",
                              "at": [540, 900], "ease": "linear"},
                             {"u": 0.6, "move": [0.1, 0.2, 0.4], "yaw": -1.0, "zoom": 1.6, "anchor": "face", "at": [530, 860], "ease": "whip"},
                             {"move": [0.2, 0.2, 0.5], "yaw": 2.0, "pitch": 1.0, "roll": -1.0, "zoom": 1.8, "anchor": [560, 1000],
                              "at": [520, 980]}]},
                   {"name": "B", "until": 3.0, "fix": {"radius": [1.0, 0.9], "aim_dz": 0.05, "dyaw": -2.0},
                    "orbit": {"keys": [[2.0, 30.0, 2.5, 2.0], [2.5, 0.0, 2.4, 1.5], [3.0, -20.0, 2.6, 0.5]], "aim_z": 1.0, "fov": 48.0}},
                   {"name": "C", "keys": [{"move": [0.0, 0.1, 0.2], "anchor": "hips", "at": [540, 1200]}]}]}


def _run_plans(tmp_path):
    from model3d import executor
    atomic_json(tmp_path / "orbit.json", ORBIT)
    atomic_json(tmp_path / "shots.json", SHOTS)
    atomic_json(tmp_path / "bones.json", _bone_path())
    steps = [{"id": "orbit", "operation": "plan.orbit", "inputs": {"plan": "$inputs.orbit", "bones": "$inputs.bones"},
              "outputs": {"cameras": "cameras.json", "track": "track.json", "report": "report.json"}},
             {"id": "shots", "operation": "plan.shots", "inputs": {"plan": "$inputs.shots", "bones": "$inputs.bones"},
              "outputs": {"cameras": "cameras.json", "track": "track.json", "alt": "alt.json", "alt_track": "alt_track.json",
                          "report": "report.json"}}]
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "plans", "steps": steps,
                                           "inputs": {name: {"path": f"{name}.json"} for name in ("orbit", "shots", "bones")}})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result["error"]
    return result


def test_plan_orbit_files_match_the_golden(tmp_path):
    step = _run_plans(tmp_path)["steps"]["orbit"]
    check("plan.orbit", {name: digest(Path(path).read_bytes()) for name, path in sorted(step["artifacts"].items())})


def test_plan_shots_files_match_the_golden(tmp_path):
    step = _run_plans(tmp_path)["steps"]["shots"]
    assert len(read_json(step["artifacts"]["alt"])["frames"]) > 0                       # つなぎの相手のカメラも作っている
    check("plan.shots", {name: digest(Path(path).read_bytes()) for name, path in sorted(step["artifacts"].items())})


def _hide(value, roots):
    """記録の伏せる値を <hidden> に、一時フォルダのパスを <tmp> にした写し（キーの順はそのまま）。"""
    if isinstance(value, dict):
        return {k: "<hidden>" if k in HIDDEN and v is not None else _hide(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [_hide(v, roots) for v in value]
    if isinstance(value, str):
        for root in roots:
            value = value.replace(root, "<tmp>")
        return RUN_NAME.sub("<run>", value)
    return value


def _run(recipe, out, roots, force=False):
    """レシピを回して、伏せた記録・run.log（時刻を除く）・進み具合の行と、工程の出力のファイルの sha256 を返す。"""
    from model3d import executor
    said = []
    result = executor.run_recipe(recipe, out, force=force, progress=said.append)
    run_dir = Path(result["run_dir"])
    record = _hide(read_json(run_dir / "run_record.json"), roots)
    log = "".join(line.split(" ", 1)[1] for line in (run_dir / "run.log").read_text(encoding="utf-8").splitlines(True))
    values = {"record": digest(json.dumps(record, ensure_ascii=False, indent=2)), "log": digest(_hide(log, roots)),
              "progress": digest(_hide("\n".join(said), roots)), "status": result["status"]}
    for step in record["steps"]:
        for name, item in step["artifacts"].items():
            values[f"{step['id']}/{name}"] = digest((run_dir / item["path"]).read_bytes())
    return values


def test_run_recipe_records_match_the_golden(tmp_path):
    from model3d import demo
    demo.create(tmp_path / "demo")
    semantic_map = read_json(tmp_path / "demo" / "semantic_map.json")
    semantic_map["preset"]["path"] = "preset.json"                                       # 一時フォルダの絶対パスを固定の名前に
    atomic_json(tmp_path / "demo" / "semantic_map.json", semantic_map)
    roots = [str(tmp_path), tmp_path.as_posix(), str(tmp_path).replace("\\", "\\\\")]
    recipe = tmp_path / "demo" / "recipe.json"
    first = _run(recipe, tmp_path / "runs", roots)
    again = _run(recipe, tmp_path / "runs", roots)                                       # 全部の工程を再利用する
    forced = _run(recipe, tmp_path / "runs", roots, force=True)                          # 再利用しないで回し直す
    assert (first["status"], again["status"], forced["status"]) == ("succeeded",) * 3
    shutil.copytree(tmp_path / "demo", tmp_path / "broken")
    broken = read_json(recipe)
    broken["steps"][5]["parameters"]["strict"] = True                                    # 補正前の帯を strict に検査 → 失敗で止まる
    broken["steps"][4]["parameters"]["clearance"] = 0.0
    broken["steps"][5]["inputs"]["mesh"] = "$steps.weld.mesh"
    atomic_json(tmp_path / "broken" / "recipe.json", broken)
    failed = _run(tmp_path / "broken" / "recipe.json", tmp_path / "broken_runs", roots)
    shutil.copytree(tmp_path / "demo", tmp_path / "review")
    semantic_map["parts"]["leg_upper_l_collider"]["state"] = "unconfirmed"               # leg の工程の役割が未確認 → 要確認で続ける
    atomic_json(tmp_path / "review" / "semantic_map.json", semantic_map)
    review = _run(tmp_path / "review" / "recipe.json", tmp_path / "review_runs", roots)
    assert (failed["status"], review["status"]) == ("failed", "needs_review")
    check("run_recipe", {f"{label}:{k}": v for label, values in (("first", first), ("again", again), ("forced", forced),
                                                                ("failed", failed), ("review", review)) for k, v in values.items()})
