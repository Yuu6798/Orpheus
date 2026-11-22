"""Build an original synthetic two-joint skin and export a single animation for a replay test."""
import math
import sys
from pathlib import Path

import bpy

dst = Path(sys.argv[-1])
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 13
scene.render.fps = 24
arm_data = bpy.data.armatures.new("Tube rig")
rig = bpy.data.objects.new("Rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
root = arm_data.edit_bones.new("Forearm")
root.head, root.tail = (0, 0, 0), (0, 1, 0)
end = arm_data.edit_bones.new("Wrist")
end.head, end.tail = root.tail, (0, 1.3, 0)
end.parent, end.use_connect = root, True
bpy.ops.object.mode_set(mode="OBJECT")
count = 16
points = [(radius*math.cos(i*math.tau/count), y, radius*math.sin(i*math.tau/count))
          for y, radius in ((0, .14), (.5, .1), (1, .14)) for i in range(count)]
faces = [(j*count+i, j*count+(i+1)%count, (j+1)*count+(i+1)%count, (j+1)*count+i)
         for j in range(2) for i in range(count)]
mesh = bpy.data.meshes.new("Tube")
mesh.from_pydata(points, [], faces)
ob = bpy.data.objects.new("Sleeve", mesh)
scene.collection.objects.link(ob)
ob.parent = rig
mod = ob.modifiers.new("Skin", "ARMATURE")
mod.object, mod.use_deform_preserve_volume = rig, True
for name in ("Forearm", "Wrist"):
    group = ob.vertex_groups.new(name=name)
    group.add(list(range(len(points))), .5, "REPLACE")
ob.shape_key_add(name="Basis")
blink = ob.shape_key_add(name="Blink")
blink.data[0].co.z += .02
wrist = rig.pose.bones["Wrist"]
wrist.rotation_mode = "XYZ"


def animate(first, step):
    mesh.shape_keys.animation_data_clear()
    rig.animation_data_clear()
    for offset, value in [(0, .25), (6, 1), (12, .5)]:
        blink.value = value
        blink.keyframe_insert("value", frame=first + offset * step)
    for offset, angle in [(0, 0), (12, 120)]:
        wrist.rotation_euler.y = math.radians(angle)
        wrist.keyframe_insert("rotation_euler", frame=first + offset * step)
    scene.frame_start, scene.frame_end = first, first + 12 * step
    scene.frame_set(first)


# Both deliveries are twice the native size. The second has its first key at
# 9/48 seconds, equivalent to frame 4.5 at the native scene's import rate.
rig.scale = (2, 2, 2)
for filename, first, step, fps, slide in [("delivered.glb", 1, 1, 24, True), ("late.glb", 9, 2, 48, False)]:
    scene.render.fps = fps
    animate(first, step)
    bpy.ops.export_scene.gltf(filepath=str(dst/filename), export_format="GLB",
                              export_animation_mode="SCENE", export_anim_scene_split_object=False,
                              export_anim_slide_to_zero=slide)

rig.scale = (1, 1, 1)
scene.render.fps = 24
scene.name = "Performance"
animate(1, 1)
# The saved active scene is an unrelated edit stage with a different clock.
stage = bpy.data.scenes.new("Stage")
stage.render.fps, stage.frame_end = 60, 3
bpy.context.window.scene = stage
bpy.ops.wm.save_as_mainfile(filepath=str(dst/"native.blend"))
# Match the GLB's linear skinning so any difference here comes from the saved pose display.
mod.use_deform_preserve_volume = False
arm_data.pose_position = "REST"
bpy.ops.wm.save_as_mainfile(filepath=str(dst/"rest.blend"))
arm_data.pose_position = "POSE"
mod.use_deform_preserve_volume = True
blink.mute = True
bpy.ops.wm.save_as_mainfile(filepath=str(dst/"muted.blend"))
blink.mute = False
mesh.shape_keys.use_relative = False
bpy.ops.wm.save_as_mainfile(filepath=str(dst/"absolute.blend"))
mesh.shape_keys.use_relative = True

# Order points across all three rings by their posed X coordinate while keeping their number.
tree = bpy.data.node_groups.new("Sort sleeve points", "GeometryNodeTree")
tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
incoming = tree.nodes.new("NodeGroupInput")
outgoing = tree.nodes.new("NodeGroupOutput")
position = tree.nodes.new("GeometryNodeInputPosition")
components = tree.nodes.new("ShaderNodeSeparateXYZ")
sort = tree.nodes.new("GeometryNodeSortElements")
sort.domain = "POINT"
tree.links.new(incoming.outputs["Geometry"], sort.inputs["Geometry"])
tree.links.new(position.outputs["Position"], components.inputs["Vector"])
tree.links.new(components.outputs["X"], sort.inputs["Sort Weight"])
tree.links.new(sort.outputs["Geometry"], outgoing.inputs["Geometry"])
reorder = ob.modifiers.new("Reorder vertices", "NODES")
reorder.node_group = tree
bpy.ops.wm.save_as_mainfile(filepath=str(dst/"reordered.blend"))
ob.modifiers.remove(reorder)

alternate = bpy.data.scenes.new("Second performance")
alternate.collection.objects.link(ob)
alternate.collection.objects.link(rig)
bpy.ops.wm.save_as_mainfile(filepath=str(dst/"ambiguous.blend"))
