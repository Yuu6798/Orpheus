"""Create two equivalent asymmetric Blender rigs at different world transforms."""
import math
import sys
from pathlib import Path

import bpy


def box(name, location, size, color):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    mesh = bpy.context.object
    mesh.name = name
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs[0].default_value = (*color, 1)
    tree.links.new(emission.outputs[0], output.inputs[0])
    mesh.data.materials.append(material)
    return mesh


folder = Path(sys.argv[-1])
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.frame_end = 4
data = bpy.data.armatures.new("review arm")
rig = bpy.data.objects.new("rig", data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
for name, x in [("left", .3), ("right", -.3), ("anchor", 0), ("focus", .12)]:
    bone = data.edit_bones.new(name)
    bone.head = (x, 0, 0)
    bone.tail = (x, 0, .3)
bpy.ops.object.mode_set(mode="OBJECT")
rig.pose.bones["focus"].location.x = .06

face = box("face", (0, 0, 0), .5, (.6, .2, .1))
face.parent = rig
pin = box("pin", (.27, -.22, .17), .19, (.01, .9, .02))
pin.parent = rig
pin.shape_key_add(name="Basis")
key = pin.shape_key_add(name="lift")
for point in key.data:
    point.co.z += .21
for frame, value in [(1, 0), (2, 1), (3, .5), (4, 0)]:
    key.value = value
    key.keyframe_insert("value", frame=frame)
key.slider_min = .25

limb = box("limb", (.4, 0, -.2), .25, (.05, .23, .4))
limb.parent = rig
group = limb.vertex_groups.new(name="left")
group.add([v.index for v in limb.data.vertices], 1.0, "REPLACE")
mod = limb.modifiers.new("Armature", "ARMATURE")
mod.object = rig
joint = rig.pose.bones["left"]
joint.rotation_mode = "XYZ"
joint.keyframe_insert("rotation_euler", frame=2)
joint.rotation_euler.x = .7
joint.keyframe_insert("rotation_euler", frame=3)

box("floor", (0, 0, -.29), 1.6, (.1, .1, .9))
scene.frame_set(2)
bpy.ops.wm.save_as_mainfile(filepath=str(folder / "before.blend"))
rig.location = (.8, -.4, .5)
rig.rotation_euler.z = math.pi / 2
# The original close-up landmark does not have to exist in the revised rig.
rig.data.bones["focus"].name = "revised_landmark"
bpy.ops.wm.save_as_mainfile(filepath=str(folder / "after.blend"))
