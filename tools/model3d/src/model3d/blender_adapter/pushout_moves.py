"""Blender の中で動かす: 押し出しの補正（Shrinkwrap の Outside など）が、頂点をおかしな所まで動かしていないかを、コマごとに調べる
（check.pushout_moves。測るだけで、.blend は変えない）。

    blender --background --factory-startup --python pushout_moves.py -- --blend dance.blend --settings settings.json --report raw.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: Shrinkwrap の Outside は、相手の形の「いちばん近い面の裏側」にある頂点を、距離に
関係なく面まで動かす。相手の形に鋭い角や穴のふちがあると、遠くの頂点が吸い寄せられて、とがった面が伸びる（袖の頂点が相手の手首まで
20〜30 cm 飛んだ。裾の頂点が体の表面まで 10〜15 cm 引き寄せられ、表の面が破れて裏地が見えた）。目で見るコマを選んで拡大するだけでは
見落としたので、全コマを数で調べる。

測り方（checks の 1 つごと）:
  - コマごとに、メッシュ mesh の、名前が prefix で始まるモディファイアを効かせたときと外したときの頂点の位置（メッシュの中の座標）を比べ、
    動いた量が limit を超えた頂点を数える。当たって押し出されただけなら、動く量は相手の形の太さより小さい。
  - ほかのメッシュ（調べるメッシュと、モディファイアの相手を除く）は評価しない（コマ送りを速くする）。

報告（raw）は model3d-pushout-moves/0.1: 調べたものごとの、モディファイア・しきい値・動いたコマの数・しきい値を超えたコマ（コマ・頂点の数・
いちばん大きい量）・いちばん大きい量とそのコマ・1 コマに動いた頂点のいちばん多い数。合否は check.pushout_moves がする。
**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ値になることを確かめた（docs/model3d.md）。
CI には Blender が無い（数え方はテストする）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .stage_render import sync_to_render
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from stage_render import sync_to_render
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"checks": [], "frames": None, "step": 1, "armature": None}
MOVED = 0.0005                                          # これより動いた頂点を「動いた」に数える（m。台本と同じ）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="pushout_moves.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def tally(record, frame, move, limit):
    """1 コマの動いた量 move (N,) を、調べたものの記録 record に足す（台本と同じ数え方）。"""
    moved = int((move > MOVED).sum())
    over = int((move > limit).sum())
    top = float(move.max()) * 1000 if len(move) else 0.0
    if moved:
        record["frames_moved"] += 1
    record["max_moved_verts"] = max(record["max_moved_verts"], moved)
    if over:
        record["frames_over_limit"].append([frame, over, round(top, 1)])
    if top > record["max_move_mm"]:
        record["max_move_mm"], record["max_move_frame"] = round(top, 1), frame


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        s = {**DEFAULTS, **json.load(stream)}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not s["armature"] or obj.name == s["armature"])]
    scene = use_scene_of(bpy, armatures[0]) if armatures else bpy.context.scene
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:          # 距離の設定（m）と Blender 単位を合わせる
        raise SystemExit(f"1 Blender 単位 = 1 m のシーンだけを扱います（scale_length {scene.unit_settings.scale_length}）")
    checks = []
    for item in s["checks"]:
        obj = bpy.data.objects.get(item["mesh"])
        if obj is None or obj.type != "MESH":
            raise SystemExit(f"メッシュがありません: {item['mesh']}")
        modifiers = [m for m in obj.modifiers if m.name.startswith(item["prefix"])]
        if not modifiers:
            raise SystemExit(f"{obj.name} に、名前が {item['prefix']} で始まるモディファイアがありません")
        checks.append({"object": obj, "modifiers": modifiers, "limit": item["limit_mm"] / 1000, "key": item["mesh"] + ":" + item["prefix"]})
    synced = []
    for c in checks:                                     # （描く形で測る: 表示と描画でモディファイアの効き方が違えば描画にそろえる。保存しない）
        synced += sync_to_render(c["object"])
    needed = {c["object"].name for c in checks}
    for c in checks:                                     # 相手の形（当たり用）は消さない
        for m in c["modifiers"]:
            target = getattr(m, "target", None)
            if target is not None:
                needed.add(target.name)
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.name not in needed:
            obj.hide_viewport = True

    def positions(obj):
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        evaluated.to_mesh_clear()
        return co.reshape(-1, 3)

    first, last = s["frames"] or (scene.frame_start, scene.frame_end)
    frames = list(range(int(first), int(last) + 1, int(s["step"])))
    result = {c["key"]: {"modifiers": [m.name for m in c["modifiers"]], "limit_mm": round(c["limit"] * 1000, 3), "frames_moved": 0,
                         "frames_over_limit": [], "max_move_mm": 0.0, "max_move_frame": None, "max_moved_verts": 0} for c in checks}
    for frame in frames:
        scene.frame_set(frame)
        on = {c["key"]: positions(c["object"]) for c in checks}
        for c in checks:
            for m in c["modifiers"]:
                m.show_viewport = False
        bpy.context.view_layer.update()
        for c in checks:
            off = positions(c["object"])
            if off.shape != on[c["key"]].shape:
                raise SystemExit(f"{c['object'].name}: モディファイアを外すと頂点の数が変わります（{len(on[c['key']])} → {len(off)}）")
            tally(result[c["key"]], frame, np.linalg.norm(on[c["key"]] - off, axis=1), c["limit"])
        for c in checks:
            for m in c["modifiers"]:
                m.show_viewport = m.show_render          # そろえた効き方に戻す
    report = {"schema_version": "model3d-pushout-moves/0.1", "blender": bpy.app.version_string, "scene": scene.name, "frames": frames, "modifiers_synced": synced,
              "checks": result}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"frames": len(frames), "checks": len(result)}))


if __name__ == "__main__":
    main()
