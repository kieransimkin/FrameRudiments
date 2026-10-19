"""Trusted Blender 5.x background bake: bounded gas domain to transparent PNGs.

Run with Blender --background --factory-startup --python this.py -- ... .
No downloaded scenes, add-ons, wall-clock playback or audio changes.
"""

import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resolution", type=int, default=64)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--burning-rate", type=float, default=0.85)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    if not 32 <= args.resolution <= 128 or not 12 <= args.frames <= 480 or not 24 <= args.fps <= 120:
        raise ValueError("Bound resolution32..128, frames12..480 and fps24..120")
    if not 64 <= args.width <= 1024 or not 64 <= args.height <= 1024 or not 0 <= args.warmup <= 240:
        raise ValueError("Bound image dimensions64..1024 and warmup0..240")
    if not 0.05 <= args.burning_rate <= 2:
        raise ValueError("Bound burning rate0.05..2")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    scene = bpy.context.scene
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.fps = args.fps
    scene.view_settings.exposure = -1.2
    configured_volume = {}
    for key, value in {
        "volumetric_tile_size": "2",
        "volumetric_samples": 128,
        "use_volumetric_shadows": True,
    }.items():
        if hasattr(scene.eevee, key):
            setattr(scene.eevee, key, value)
            configured_volume[key] = value
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "AgX"
    scene.frame_start = 1
    scene.frame_end = args.warmup + args.frames
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1.4))
    domain = bpy.context.object
    domain.name = "Fire domain"
    domain.scale = (1.35, 0.6, 1.6)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    fluid = domain.modifiers.new("Gas domain", "FLUID")
    fluid.fluid_type = "DOMAIN"
    ds = fluid.domain_settings
    ds.domain_type = "GAS"
    ds.resolution_max = args.resolution
    ds.cache_type = "ALL"
    ds.cache_frame_start = 1
    ds.cache_frame_end = scene.frame_end
    ds.cache_directory = str(out / "cache")
    ds.cache_data_format = "UNI"
    ds.use_noise = True
    ds.noise_scale = 2
    ds.vorticity = 0.35
    ds.burning_rate = args.burning_rate
    ds.flame_max_temp = 2.0
    ds.flame_ignition = 1.0
    ds.timesteps_min = 1
    ds.timesteps_max = 4
    mat = bpy.data.materials.new("Thermal emission and smoke")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    volume = nodes.new("ShaderNodeVolumePrincipled")
    volume.inputs["Density"].default_value = 1.2
    volume.inputs["Blackbody Intensity"].default_value = 0.75
    volume.inputs["Temperature"].default_value = 1200
    mat.node_tree.links.new(volume.outputs["Volume"], output.inputs["Volume"])
    domain.data.materials.append(mat)
    # Several adjacent finite fuel sources produce separate rising sheets and gaps.
    for i in range(5):
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=12, ring_count=8, radius=1, location=(-0.9 + i * 0.45, 0, 0.12)
        )
        flow = bpy.context.object
        flow.name = f"Fuel {i}"
        flow.scale = (0.22, 0.16, 0.10)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        fm = flow.modifiers.new("Fuel inflow", "FLUID")
        fm.fluid_type = "FLOW"
        fs = fm.flow_settings
        fs.flow_type = "BOTH"
        fs.flow_behavior = "INFLOW"
        fs.fuel_amount = 1.0 + i * 0.12
        fs.temperature = 1.2
        fs.density = 0.15
        if hasattr(fs, "use_texture"):
            fs.use_texture = True
            texture = bpy.data.textures.new(f"Fuel variation {i}", type="CLOUDS")
            texture.noise_scale = 0.18
            fs.noise_texture = texture
        fs.use_initial_velocity = True
        fs.velocity_normal = 0.4
        flow.hide_render = True
    bpy.ops.object.camera_add(location=(0, -7, 1.45))
    camera = bpy.context.object
    camera.rotation_euler = (Vector((0, 0, 1.45)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 3.0
    scene.camera = camera
    bpy.ops.object.light_add(type="AREA", location=(0, -3, 4))
    bpy.context.object.data.energy = 50
    scene.world.color = (0, 0, 0)
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "source.blend"))
    bpy.ops.object.select_all(action="DESELECT")
    domain.select_set(True)
    bpy.context.view_layer.objects.active = domain
    bpy.ops.fluid.bake_all()
    frames = out / "frames"
    frames.mkdir()
    for n in range(args.frames):
        scene.frame_set(args.warmup + n + 1)
        scene.render.filepath = str(frames / f"{n:05d}.png")
        bpy.ops.render.render(write_still=True)
    (out / "receipt.json").write_text(
        json.dumps(
            {
                "backend": "blender-mantaflow",
                "blender": bpy.app.version_string,
                "blender_build": bpy.app.build_hash.decode(),
                "settings": vars(args) | {"output": str(out)},
                "state": "baked and rendered",
                "frame_count": args.frames,
                "volume_render_settings": configured_volume,
                "scope": "Gas simulation and Principled Volume thermal emission; no scientific fire-model validation",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
