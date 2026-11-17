"""Blender の中で動かす: 演出版を、EEVEE で 1 回でコマごとに PNG に描く（render.show の描く側。.blend は保存しない）。

    blender --background --factory-startup --python show_render.py -- --blend in.blend --settings settings.json \
        --cameras cameras.json --frames-dir frames --report report.json [--lights track.json] [--hooks stage.py]

3 作目の VRM のダンスの演出版（リポジトリ外の台本。Blender 5.2 の EEVEE で 1 回で描いた）の描き方を移した。舞台の形は作品ごとに
違うので、フック（--hooks。Python の台本）で作って動かす。この台本が持つのは、どの作品にも要る所:
- カメラ（cameras。model3d-cameras/0.1）: 全部のコマを、カメラの位置・向き（四元数。前のコマと符号をそろえる）・レンズ（とずらし）の
  キーにして置く（動きのぶれに、カメラの動きも入る）。キーは動きのコマ f（整数で、増えていく並び）に打つ。カットの最後のコマ
  （cuts の T の 1 つ前と、最後のコマ）のキーは「次のキーまで止める」にし、ほかは直線でつなぐ（ぶれがカットをまたがない）。
  受光は縦に合わせ、大きさは cameras の sensor_height_mm（無ければ camera.sensor_height）。レンズは lens_mm（無ければ焦点距離の画素から）、
  ずらしは shift（無ければ主点から）。被写界深度は、コマに fstop と focus_m があればその値（focus_m はシーンの単位に直す）、無ければ切る。
- 動きのぶれ（motion_blur）: シャッターの開き shutter（コマ。0 で無し）、位置は「コマから後ろ」（START。コマ T の絵は T から
  T + shutter まで）、分ける回数 steps。off の T（動きのつなぎ目の直前のコマなど）はぶれを切る。
- 光（lights）: 光の物体の名前 → {energy: 光のトラックの値の名前, base: W, color: 値の名前}。強さは base × 値（シーンの単位で直す）。
- 光を受ける塗り（look.lit）のまわりの明るさ（ambient: 光のトラックの値の名前）: 印のある材質ごとに、値 × model3d_lit_ambient_scale。
- 表情（expressions）: objects の物体の形状キーを、values（{形状キー: [[秒, 値], …]}）を直線でつないだ値にする（render.passes と同じ）。
- 空（world: [R, G, B] 線形。省くと .blend の空）、にじみ（bloom: Glare の Bloom を合成で。省くと切る）。
- フック（--hooks）: 舞台を作る build(ctx) をカメラを置く前に 1 回、コマごとに動かす frame(ctx) を描く直前に呼ぶ（どちらも任意）。
  ctx: bpy・scene・unit（scale_length）・settings・track（光の値 {T: {名前: 値}}）・cameras（{T: カメラのコマ}）・state（フックが
  使ってよい辞書）、コマごとに T・f・t（秒）・values（そのコマの光の値）・camera（そのコマのカメラ。position は m）。
描く設定: EEVEE（samples・raytracing・shadows）、色の変換は view_transform（既定 Standard。Look・露出・ガンマ・カーブは無し）、
PNG の RGB 8 ビット。.blend から引き継ぐと絵をすり替えうる設定（シーケンサー・切り取り・画素の縦横比・多視点・透ける背景・
表示の装置・出力の色の上書き・マーカーのカメラ）は決まった値にし、違っていたものは報告の render_changes に残す。画像に日時などの
控えを書かない。描くファイルは frames-dir の f<T>.png。同じ名前のファイルがあっても、いつも描き直す（とばさない）。
**検証**: 本物の Blender 5.2.4 で、3 作目の台本の絵と比べた（docs/model3d.md）。
"""
import argparse
import importlib.util
import json
import math
import os
import sys
import time

try:   # パッケージとして読むとき（テスト）
    from . import face_sheet, pass_render
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import face_sheet
    import pass_render

DEFAULTS = {
    "resolution": [1080, 1920], "samples": 64, "raytracing": True, "shadows": True, "view_transform": "Standard",
    "camera": {"sensor_height": 36.0, "clip": [0.1, 1000.0]},
    "motion_blur": {"shutter": 0.0, "steps": 1, "off": []},
    "cuts": [], "lights": {}, "ambient": None, "world": None, "bloom": None, "expressions": None, "only": None,
}
SECTIONS = ("camera", "motion_blur")
LIT_MARK, LIT_SCALE, LIT_AMBIENT = "model3d_lit", "model3d_lit_ambient_scale", "model3d_lit_ambient"   # look.lit の印
RENDER_FIXED = {"use_sequencer": False, "use_border": False, "pixel_aspect_x": 1.0, "pixel_aspect_y": 1.0, "use_multiview": False,
                "film_transparent": False}
VIEW_FIXED = {"look": "None", "exposure": 0.0, "gamma": 1.0, "use_curve_mapping": False}
DISPLAY_FIXED = {"display_device": "sRGB"}
IMAGE_FIXED = {"file_format": "PNG", "color_mode": "RGB", "color_depth": "8", "compression": 15, "color_management": "FOLLOW_SCENE"}
BLOOM = {"quality": "Quality", "threshold": "Threshold", "smoothness": "Smoothness", "clamp": "Clamp", "maximum": "Maximum",
         "strength": "Strength", "size": "Size"}              # 設定の鍵 → Glare の入力の口


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="show_render.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--cameras", required=True)
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--lights")
    ap.add_argument("--hooks")
    return ap.parse_args(argv)


def merged(settings):
    """既定の値に settings を重ねる（camera・motion_blur は中の鍵ごと）。"""
    out = {**DEFAULTS, **{k: v for k, v in settings.items() if k not in SECTIONS}}
    for key in SECTIONS:
        out[key] = {**DEFAULTS[key], **settings.get(key, {})}
    return out


def frame_name(T):
    return f"f{int(T):04d}.png"


def held_frames(Ts, cuts):
    """カメラのキーを「次のキーまで止める」にするコマ: cuts（新しいカットの最初の T）の 1 つ前のコマと、最後のコマ。Ts は T の並び。"""
    present = set(Ts)
    held = {T - 1 for T in cuts if T - 1 in present}
    if Ts:
        held.add(max(Ts))
    return held


def motion_frames_problem(rows):
    """カメラのキーは動きのコマ f に打つので、f は整数で、T の順に増えていく並びにする。そうでなければその説明。"""
    previous = None
    for T, f in rows:
        if float(f) != int(f):
            return f"cameras の T {T} の f（{f}）が整数ではありません（カメラのキーは動きのコマに打つので、小数のコマは描けません）"
        if previous is not None and f <= previous:
            return f"cameras の f が T の順に増えていません（T {T} の f {f}）。カメラのキーを打つ動きのコマが重なるか、戻ります"
        previous = f
    return None


def lens_and_shift(frame, resolution, sensor_height):
    """カメラのコマ → (レンズ mm, ずらし)。lens_mm と shift があればそれ、無ければ焦点距離と主点の画素（resolution の画面）から。"""
    width, height = resolution
    lens = float(frame["lens_mm"]) if "lens_mm" in frame else pass_render.lens_from_focal(float(frame["focal_px"]), height, sensor_height)
    shift = (tuple(float(v) for v in frame["shift"]) if "shift" in frame
             else pass_render.shift_from_principal([float(v) for v in frame["principal_px"]], width, height))
    return lens, shift


def depth_of_field(frame):
    """コマの被写界深度: (f 値, ピントまでの距離 m) か None（fstop の無いコマは切る）。fstop と focus_m は組で書く。"""
    if "fstop" not in frame and "focus_m" not in frame:
        return None
    if "fstop" not in frame or "focus_m" not in frame:
        raise SystemExit(f"cameras の T {frame['T']} の fstop と focus_m は組で書いてください")
    return float(frame["fstop"]), float(frame["focus_m"])


def same_sign(quaternion, previous):
    """前のコマの四元数と内積が負なら、符号を返す（間のコマで回り道をさせない）。数の並び (w, x, y, z)。"""
    if previous is not None and sum(a * b for a, b in zip(quaternion, previous)) < 0:
        return tuple(-v for v in quaternion)
    return tuple(quaternion)


def light_energy(watts, unit):
    """m で決めた強さ（W）を、1 Blender 単位が unit m のシーンで同じ明るさにする強さ（距離の 2 乗で弱まる）。"""
    return watts / unit ** 2


def load_hooks(path):
    """フックの台本（Python）を読む。build・frame があれば呼ぶ（無ければ何もしない）。"""
    spec = importlib.util.spec_from_file_location("model3d_show_hooks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    """フックに渡すもの。"""

    def __init__(self, **items):
        self.__dict__.update(items)
        self.state = {}


def action_curves(action):
    """アクションの F カーブ（Blender 5.x は層 → 帯 → チャンネルの袋、4.x は action.fcurves）。"""
    curves = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                curves.extend(bag.fcurves)
    if not curves and hasattr(action, "fcurves"):
        curves = list(action.fcurves)
    return curves


def version_problem(version):
    """この描き方が動かない Blender の版なら、その理由（5.x の EEVEE の名前と合成の API で書いた。5.2.4 で確かめた）。"""
    if tuple(version[:2]) < (5, 0):
        return (f"render.show は Blender 5.x の描き方で、Blender {'.'.join(str(v) for v in version[:3])} では動きません"
                "（4.x の描き方は render.stage）")
    return None


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Matrix, Vector

    problem = version_problem(bpy.app.version)
    if problem:
        raise SystemExit(problem)
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    with open(a.cameras, encoding="utf-8") as stream:
        record = json.load(stream)
    track = {}
    if a.lights:
        with open(a.lights, encoding="utf-8") as stream:
            track = {int(fr["T"]): fr["values"] for fr in json.load(stream)["frames"]}
    cameras = {int(fr["T"]): fr for fr in record["frames"]}
    rows = sorted((T, pass_render.stage_render.motion_frame(fr.get("f", T))) for T, fr in cameras.items())
    problem = motion_frames_problem(rows)
    if problem:
        raise SystemExit(problem)
    only = set(settings["only"]) if settings["only"] else None
    plan = [(T, int(f)) for T, f in rows if only is None or T in only]
    if not plan:
        raise SystemExit("描くコマがありません（cameras と only を確かめてください）")
    needs_track = bool(settings["lights"]) or settings["ambient"] is not None
    if needs_track:
        missing = [T for T, _ in plan if T not in track]
        if missing:
            raise SystemExit(f"光のトラックに、描くコマの値がありません: T {missing[:10]}")
    for name, spec in settings["lights"].items():
        for key in ("energy", "color"):
            if spec.get(key) is not None and any(spec[key] not in track[T] for T, _ in plan):
                raise SystemExit(f"lights.{name}.{key} の値 {spec[key]!r} が光のトラックにありません")
    if settings["ambient"] is not None and any(settings["ambient"] not in track[T] for T, _ in plan):
        raise SystemExit(f"ambient の値 {settings['ambient']!r} が光のトラックにありません")

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    sc = bpy.context.scene
    unit = float(sc.unit_settings.scale_length)
    width, height = settings["resolution"]
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = width, height, 100
    groups = (("display", sc.display_settings, DISPLAY_FIXED), ("render", sc.render, RENDER_FIXED),
              ("image", sc.render.image_settings, IMAGE_FIXED))
    render_changes = {key: face_sheet.setting_changes({name: getattr(target, name) for name in fixed}, fixed)
                      for key, target, fixed in groups}
    for key, target, _ in groups:
        for name, (_, value) in render_changes[key].items():
            setattr(target, name, value)
    sc.view_settings.view_transform = settings["view_transform"]
    render_changes["view"] = face_sheet.setting_changes({name: getattr(sc.view_settings, name) for name in VIEW_FIXED}, VIEW_FIXED)
    for name, (_, value) in render_changes["view"].items():
        setattr(sc.view_settings, name, value)
    render_changes["marker_cameras"] = [marker.name for marker in sc.timeline_markers if marker.camera is not None]
    for marker in sc.timeline_markers:                   # マーカーに結んだカメラは、描くときにカメラを差し替える
        marker.camera = None
    for name in dir(sc.render):                          # 画像に日時・ファイル名などの控えを書かない（画素は変わらない）
        if name.startswith("use_stamp") and isinstance(getattr(sc.render, name), bool):
            setattr(sc.render, name, False)
    sc.eevee.taa_render_samples = settings["samples"]
    sc.eevee.use_raytracing = bool(settings["raytracing"])
    sc.eevee.use_shadows = bool(settings["shadows"])
    blur = settings["motion_blur"]
    sc.render.motion_blur_shutter = blur["shutter"] if blur["shutter"] > 0 else 0.5
    sc.render.motion_blur_position = "START"             # コマ T の絵は、T から T + 開きまで（次のカットの姿を混ぜない）
    sc.eevee.motion_blur_steps = int(blur["steps"])
    if settings["world"] is not None:
        world = bpy.data.worlds.new("model3d_show_world")
        world.use_nodes = True
        world.node_tree.nodes["Background"].inputs[0].default_value = (*settings["world"], 1)
        sc.world = world
    lit = [(m.node_tree.nodes[LIT_AMBIENT], float(m[LIT_SCALE])) for m in bpy.data.materials
           if m.get(LIT_MARK) is not None and m.node_tree is not None and LIT_AMBIENT in m.node_tree.nodes]
    if settings["ambient"] is not None and not lit:
        raise SystemExit("ambient を渡しましたが、光を受ける塗り（look.lit）の材質がありません")
    lights = {}
    for name, spec in settings["lights"].items():
        ob = bpy.data.objects.get(name)
        if ob is None or ob.type != "LIGHT":
            lights[name] = None                          # フックが作るかもしれないので、build の後で確かめる
        else:
            lights[name] = ob
    hooks, hooks_sha = None, None
    ctx = Context(bpy=bpy, scene=sc, unit=unit, settings=settings, track=track, cameras=cameras)
    if a.hooks:
        import hashlib
        with open(a.hooks, "rb") as stream:
            hooks_sha = hashlib.sha256(stream.read()).hexdigest()
        hooks = load_hooks(a.hooks)
        if hasattr(hooks, "build"):
            hooks.build(ctx)
    for name in settings["lights"]:
        ob = bpy.data.objects.get(name)
        if ob is None or ob.type != "LIGHT":
            raise SystemExit(f"lights の光の物体がありません: {name}")
        lights[name] = ob
    expressions = settings["expressions"]
    shape_targets = []
    if expressions:
        for name in expressions["objects"]:
            ob = bpy.data.objects.get(name)
            if ob is None or ob.data is None or getattr(ob.data, "shape_keys", None) is None:
                raise SystemExit(f"expressions の物体に形状キーがありません: {name}")
            absent = sorted(set(expressions["values"]) - set(ob.data.shape_keys.key_blocks.keys()))
            if absent:
                raise SystemExit(f"{name} に無い形状キー: {absent}")
            shape_targets.append(ob.data.shape_keys.key_blocks)

    # ---- カメラ: 全部のコマのキー（動きのぶれにカメラの動きも入る）。カットの最後のキーは止める ----
    sensor = float(record.get("sensor_height_mm") or settings["camera"]["sensor_height"])
    clip = record.get("clip_m") or settings["camera"]["clip"]
    resolution = record.get("resolution") or settings["resolution"]
    cam_d = bpy.data.cameras.new("model3d_show_camera")
    cam_d.sensor_fit, cam_d.sensor_height = "VERTICAL", sensor
    cam_d.clip_start, cam_d.clip_end = clip[0] / unit, clip[1] / unit
    cam = bpy.data.objects.new("model3d_show_camera", cam_d)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.rotation_mode = "QUATERNION"
    previous = None
    shifts = False
    for T, f in rows:
        frame = cameras[T]
        M = Matrix(frame["matrix"])
        quaternion = same_sign(tuple(M.to_quaternion()), previous)
        previous = quaternion
        lens, shift = lens_and_shift(frame, resolution, sensor)
        cam.location = M.translation / unit
        cam.rotation_quaternion = quaternion
        cam_d.lens = lens
        cam.keyframe_insert("location", frame=int(f))
        cam.keyframe_insert("rotation_quaternion", frame=int(f))
        cam_d.keyframe_insert("lens", frame=int(f))
        if shift != (0.0, 0.0) or shifts:
            shifts = True
            cam_d.shift_x, cam_d.shift_y = shift
            cam_d.keyframe_insert("shift_x", frame=int(f))
            cam_d.keyframe_insert("shift_y", frame=int(f))
    held = held_frames([T for T, _ in rows], settings["cuts"])
    held_f = {int(f) for T, f in rows if T in held}
    curves = 0
    for block in (cam, cam_d):
        for curve in action_curves(block.animation_data.action):
            curves += 1
            for point in curve.keyframe_points:
                point.interpolation = "CONSTANT" if int(round(point.co[0])) in held_f else "LINEAR"

    # ---- にじみ（合成。Blender 5.x は scene.compositing_node_group） ----
    if settings["bloom"] is not None:
        comp = bpy.data.node_groups.new("model3d_show_bloom", "CompositorNodeTree")
        comp.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        layers = comp.nodes.new("CompositorNodeRLayers")
        glare = comp.nodes.new("CompositorNodeGlare")
        out = comp.nodes.new("NodeGroupOutput")
        glare.inputs["Type"].default_value = "Bloom"
        for key, socket in BLOOM.items():
            if key in settings["bloom"]:
                glare.inputs[socket].default_value = settings["bloom"][key]
        comp.links.new(layers.outputs["Image"], glare.inputs["Image"])
        comp.links.new(glare.outputs["Image"], out.inputs[0])
        sc.compositing_node_group = comp
        sc.render.use_compositing = True
    else:
        render_changes["render"].update(face_sheet.setting_changes({"use_compositing": sc.render.use_compositing},
                                                                    {"use_compositing": False}))
        sc.render.use_compositing = False

    os.makedirs(a.frames_dir, exist_ok=True)
    fps = record.get("fps") or sc.render.fps / sc.render.fps_base
    off = set(blur["off"])
    rendered, blurred, dof_frames = [], [], []
    began = time.time()
    for T, f in plan:
        sc.frame_set(f)
        sc.render.use_motion_blur = blur["shutter"] > 0 and T not in off
        if sc.render.use_motion_blur:
            blurred.append(T)
        values = track.get(T, {})
        for name, spec in settings["lights"].items():
            data = lights[name].data
            if spec.get("energy") is not None:
                data.energy = light_energy(spec.get("base", 1.0) * values[spec["energy"]], unit)
            if spec.get("color") is not None:
                data.color = values[spec["color"]]
        if settings["ambient"] is not None:
            amb = values[settings["ambient"]]
            for node, scale in lit:
                node.outputs[0].default_value = (*[v * scale for v in amb], 1)
        frame = cameras[T]
        dof = depth_of_field(frame)
        cam_d.dof.use_dof = dof is not None
        if dof is not None:
            cam_d.dof.aperture_fstop, cam_d.dof.focus_distance = dof[0], dof[1] / unit
            dof_frames.append(T)
        if expressions:
            for key, value in pass_render.expression_values(expressions["values"], (T - 1) / fps).items():
                for blocks in shape_targets:
                    blocks[key].value = value
        if hooks is not None and hasattr(hooks, "frame"):
            M = frame["matrix"]
            ctx.T, ctx.f, ctx.t, ctx.values = T, f, (T - 1) / fps, values
            ctx.camera = {**frame, "position": [M[0][3], M[1][3], M[2][3]]}
            hooks.frame(ctx)
        sc.render.filepath = os.path.join(a.frames_dir, frame_name(T))
        bpy.ops.render.render(write_still=True)
        rendered.append(T)
        print(f"T {T} f {f} {time.time() - began:.1f}s", flush=True)
    seconds = time.time() - began
    report = {"schema_version": "model3d-show-render/0.1", "blender": bpy.app.version_string,
              "planned": [{"T": T, "f": f} for T, f in plan], "rendered": rendered, "fps": fps, "resolution": [width, height],
              "scale_length": unit, "engine": sc.render.engine, "samples": settings["samples"],
              "camera": {"keys": len(rows), "curves": curves, "held": sorted(held), "sensor_height_mm": sensor, "shift_keys": shifts,
                         "dof_frames": len(dof_frames)},
              "motion_blur": {"shutter": blur["shutter"], "steps": blur["steps"], "position": "START", "frames": len(blurred),
                              "off": sorted(T for T in off if T in {p for p, _ in plan})},
              "lights": sorted(settings["lights"]), "lit_materials": len(lit),
              "hooks": {"path": os.path.abspath(a.hooks), "sha256": hooks_sha} if a.hooks else None,
              "render_changes": render_changes, "seconds": round(seconds, 1),
              "seconds_per_frame": round(seconds / len(rendered), 2) if rendered else None, "settings": settings}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"planned": len(plan), "rendered": len(rendered)}))


if __name__ == "__main__":
    main()
