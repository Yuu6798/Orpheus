"""cv2 なしの import の道（リファクタリングの安全網）: cv2 を読めなくした子プロセスで、いま cv2 なしで読めるモジュールを全部読む。

fx extra（opencv-python-headless）の無い環境でも、mv の CLI は起動し、cv2 を使わない命令（fx accept・fx legacy・fx timeline plan・
dance plan・dance accept など）は動く（docs/fx.md。cv2 が要る命令は cli._require_fx で止まる）。そのために、cv2 は使う関数の中で
読み、import の道に置かない。CI は fx extra をいつも入れるので、ここが壊れても普通のテストでは気づけない。
並びは、2026-11-17 の main（bece1ad）で cv2 なしで読めたモジュール全部（94 のうち 63）と、そのあとリファクタリングで分けた先の
モジュール（fx の sequence_*）。cv2 を import の道に置くモジュールを意図して増やすときは、ここから外す理由を PR に書く。"""
import json
import subprocess
import sys

WITHOUT_CV2 = (
    "mv_pipeline.adapters", "mv_pipeline.adapters.external", "mv_pipeline.adapters.librosa_adapter",
    "mv_pipeline.audio", "mv_pipeline.audio.end", "mv_pipeline.audio.files", "mv_pipeline.audio.grid", "mv_pipeline.audio.hits",
    "mv_pipeline.audio.keys",
    "mv_pipeline.cli",
    "mv_pipeline.dance", "mv_pipeline.dance.beats", "mv_pipeline.dance.camera", "mv_pipeline.dance.cues", "mv_pipeline.dance.hooks",
    "mv_pipeline.dance.plan", "mv_pipeline.dance.runs", "mv_pipeline.dance.spec",
    "mv_pipeline.deliver", "mv_pipeline.demo", "mv_pipeline.encode",
    "mv_pipeline.fx", "mv_pipeline.fx.burn", "mv_pipeline.fx.clipprep", "mv_pipeline.fx.color", "mv_pipeline.fx.hooks",
    "mv_pipeline.fx.layering", "mv_pipeline.fx.legacy", "mv_pipeline.fx.look", "mv_pipeline.fx.overlay", "mv_pipeline.fx.probe",
    "mv_pipeline.fx.runs", "mv_pipeline.fx.sequence", "mv_pipeline.fx.sequence_demo", "mv_pipeline.fx.sequence_plan",
    "mv_pipeline.fx.sequence_render", "mv_pipeline.fx.sequence_spec", "mv_pipeline.fx.spec", "mv_pipeline.fx.spec_layers",
    "mv_pipeline.fx.timeline",
    "mv_pipeline.gate", "mv_pipeline.gate.checks", "mv_pipeline.gate.demo", "mv_pipeline.gate.flashes", "mv_pipeline.gate.frames_timeline",
    "mv_pipeline.gate.lines", "mv_pipeline.gate.tail", "mv_pipeline.gate.vocals",
    "mv_pipeline.handoff",
    "mv_pipeline.inspection", "mv_pipeline.inspection.config", "mv_pipeline.inspection.cues", "mv_pipeline.inspection.detect",
    "mv_pipeline.inspection.targets", "mv_pipeline.inspection.video", "mv_pipeline.inspection.vlm",
    "mv_pipeline.io", "mv_pipeline.layer", "mv_pipeline.media", "mv_pipeline.normalize", "mv_pipeline.pipeline",
    "mv_pipeline.remotion_props", "mv_pipeline.runner", "mv_pipeline.sheet", "mv_pipeline.subtitles", "mv_pipeline.validate",
    "mv_pipeline.workflow",
)

# 名前を 1 つずつ読み、読めなかったものと理由を JSON で返す（cv2 は sys.modules の None で読めなくする）
LOAD = r'''
import importlib, json, sys
sys.modules["cv2"] = None
failed = {}
for name in json.loads(sys.argv[1]):
    try:
        importlib.import_module(name)
    except ImportError as error:
        failed[name] = repr(error)
print(json.dumps(failed))
'''


def test_these_modules_import_without_cv2(tmp_path):
    completed = subprocess.run([sys.executable, "-c", LOAD, json.dumps(WITHOUT_CV2)], cwd=tmp_path, capture_output=True, text=True,
                               encoding="utf-8", check=True)
    assert json.loads(completed.stdout) == {}


def test_the_list_holds_the_entry_points_used_without_cv2():
    """cv2 なしで使う命令の入口（fx timeline plan・fx accept・dance plan・gate・sheet）と、書き出しの共通の部品 encode。"""
    promised = {"mv_pipeline.cli", "mv_pipeline.fx.sequence", "mv_pipeline.fx.runs", "mv_pipeline.fx.probe", "mv_pipeline.dance.spec",
                "mv_pipeline.dance.plan", "mv_pipeline.dance.cues", "mv_pipeline.gate.checks", "mv_pipeline.sheet", "mv_pipeline.encode"}
    assert promised <= set(WITHOUT_CV2) and len(set(WITHOUT_CV2)) == len(WITHOUT_CV2) == 67
