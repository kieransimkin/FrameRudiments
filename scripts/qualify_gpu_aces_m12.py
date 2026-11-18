"""CI-only M12.3 reference gallery: real float32 EGL MRT, EXR and pinned ACEScg.

Produces actual decoded renderer comparisons and content hashes. This is an
engineering acceptance fixture, NOT a physical-HDR display or device test.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from framerudiments.aces_reference_m12 import to_acescg_reference
from framerudiments.colour_aov import scene_linear_to_srgb8, write_exr
from framerudiments.gpu_spatial_aov import parity_report, render_solid_aov_gpu
from framerudiments.solid_spatial_aov import render_solid_aov
from framerudiments.solid_spatial_renderers import SolidSpatialRenderer

SCHEMA = "framerudiments.m12-3-gpu-aces-qualification.v1"
BUILTIN_ACES_CG = "cg-config-v2.2.0_aces-v1.3_ocio-v2.4"


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _frame(n):
    return SimpleNamespace(context=SimpleNamespace(frame=n, output_seconds=n / 60),
                           values=(0.4, -0.2))


def build(output):
    folder = Path(output)
    if folder.exists() or folder.is_symlink():
        raise ValueError("M12.3 output must be a new directory")
    folder.mkdir(parents=True)
    try:
        import PyOpenColorIO as ocio
        cfg = ocio.Config.CreateFromBuiltinConfig(BUILTIN_ACES_CG)
        if cfg.getColorSpace('ACEScg') is None or cfg.getColorSpace('Linear Rec.709 (sRGB)') is None:
            raise RuntimeError('Required pinned ACEScg conversion spaces unavailable')
        config = folder / 'aces-cg-reference.ocio'
        config.write_text(cfg.serialize(), encoding='utf-8')
        config_sha = _digest(config)
        receipts = []
        for geometry in ('torus', 'sphere', 'terrain'):
            renderer = SolidSpatialRenderer((160, 112), {
                'geometry': geometry, 'fog': 0, 'glow': 0, 'background': '#00000000',
                'metallic': .8, 'roughness': .15,
            })
            previous, current = _frame(19), _frame(20)
            native = render_solid_aov(renderer, current, previous_frame=previous, light_stops=3)
            gpu, gl_receipt = render_solid_aov_gpu(renderer, current, previous_frame=previous,
                                                  light_stops=3, backend='egl',
                                                  return_evidence=True)
            parity = parity_report(native, gpu)
            if not parity['passed']:
                raise RuntimeError(f"{geometry}: float32 GPU parity did not pass: {parity}")
            if gl_receipt['backend'] != 'egl' or gl_receipt['raster'] != 'hardware_depth_test_float32_mrt':
                raise RuntimeError(f"{geometry}: actual GPU rasterisation was not used")
            aces, aces_receipt = to_acescg_reference(gpu, config=config, config_sha256=config_sha)
            exr = folder / f'{geometry}-acescg.exr'
            export = write_exr(exr, aces, source_evidence={**gl_receipt, **aces_receipt})
            # Actual renderer output, a separate display preview from scene-linear data.
            cpu_view = Image.fromarray(scene_linear_to_srgb8(native.rgb, tone_map='reinhard'), 'RGB')
            gpu_view = Image.fromarray(scene_linear_to_srgb8(gpu.rgb, tone_map='reinhard'), 'RGB')
            pair = Image.new('RGB', (renderer.size[0] * 2, renderer.size[1]))
            pair.paste(cpu_view, (0, 0))
            pair.paste(gpu_view, (renderer.size[0], 0))
            png = folder / f'{geometry}-comparison.png'
            pair.save(png)
            receipts.append({
                'geometry': geometry,
                'parity': parity,
                'gpu': gl_receipt,
                'aces': aces_receipt,
                'comparison_png': png.name,
                'comparison_sha256': _digest(png),
                'exr': exr.name,
                'exr_sha256': _digest(exr),
                'exr_receipt': exr.name + '.receipt.json',
                'exr_readback': export,
            })
        report = {
            'schema': SCHEMA,
            'head_sha': os.environ.get('GITHUB_SHA', 'local_unpublished'),
            'shapes': receipts,
            'gpu_environment': 'runner_software_EGL_not_physical_GPU_certification',
            'aces_config': config.name,
            'aces_config_sha256': config_sha,
            'aces_builtin_name': BUILTIN_ACES_CG,
            'source_clock': 'real_FrameRudiments_native_frame_60fps_synthetic_values',
            'claimed': ['float32_surface_colour','camera_depth','normals','previous_frame_motion'],
            'not_claimed': ['physical_HDR_display','independent_GPU_GGX','volume_AOV',
                            'runtime_character_kinematics','external_visualiser_replay'],
        }
        (folder / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n',
                                            encoding='utf-8')
        return report
    except BaseException:
        # This is a private output directory owned by the caller for this run.
        # Keep failed evidence for diagnosis; the CI artifact uploads it.
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description='Qualify M12.3 exact float32 GPU colour/AOV export')
    p.add_argument('--output', required=True)
    args = p.parse_args(argv)
    result = build(args.output)
    print(json.dumps({"schema": result['schema'], "passed": True,
                      "geometries": [x['geometry'] for x in result['shapes']]}, sort_keys=True))


if __name__ == '__main__':
    main()
