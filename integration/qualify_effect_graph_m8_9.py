"""Run the M8.9 resource and precision checks on an explicitly selected renderer."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import sys
import tomllib

from benchmark_effect_graph_m8_9_gpu import SIZES, benchmark, fixture, graph, mapped_fixture, raindrop_graph
from benchmark_effect_graph_m8_9_precision import precision_cases, precision_graph
from benchmark_effect_graph_m8_9_resources import compare_resources, comparison_image, device_info
from framerudiments import __version__
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.effect_graph_gpu_memory import is_software_renderer
from framerudiments.effect_graph_spatial import PRECISE_SPATIAL_EXTENSION, spatial_requires_precise_gpu


def source_identity():
    here = Path(__file__).resolve().parent
    source_version = tomllib.loads((here.parent / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    scripts = (Path(__file__).name, 'benchmark_effect_graph_m8_9_gpu.py',
               'benchmark_effect_graph_m8_9_precision.py', 'benchmark_effect_graph_m8_9_resources.py')
    return {
        'version': source_version,
        'script_sha256': {name: sha256((here / name).read_bytes()).hexdigest() for name in scripts},
    }


def renderer_vendor(device):
    """Classify the active renderer's strings; physical host identity is separately reviewed."""
    renderer = device['renderer'].lower()
    identity = f"{device['vendor'].lower()} {renderer}"
    if is_software_renderer(renderer) or re.search(r'\b(?:software rasterizer|lavapipe|swrast)\b', renderer):
        return 'software'
    if re.search(r'\b(?:zink|angle|virgl|d3d12|virtio|svga3d|vmware|virtualbox|parallels|vbox)\b', identity):
        return 'translated_or_virtual'
    patterns = {
        'amd': r'\b(?:amd|radeon|ati)\b',
        'intel': r'\bintel\b',
        'nvidia': r'\bnvidia\b',
    }
    vendors = [vendor for vendor, pattern in patterns.items() if re.search(pattern, identity)]
    return vendors[0] if len(vendors) == 1 else 'unknown'


def require_device(device, *, vendor, renderer=None, precision=None):
    observed = renderer_vendor(device)
    if observed != vendor:
        raise RuntimeError(f"Expected renderer vendor {vendor}, observed {observed}: {device['renderer']}")
    if renderer is not None and renderer.casefold() not in device['renderer'].casefold():
        raise RuntimeError(f"Expected renderer containing {renderer!r}, observed {device['renderer']!r}")
    if precision is not None and device['precise_spatial_warps'] != (precision == 'available'):
        raise RuntimeError(f'Expected {PRECISE_SPATIAL_EXTENSION} to be {precision}')


def qualify_warp(gpu, graph, sources):
    precise = spatial_requires_precise_gpu(graph.nodes[graph.output].config)
    if not precise or gpu.supports_precise_spatial_warps:
        return {'route': 'gpu_resident', 'precision_required': precise, **benchmark(gpu, graph, sources, 1)}

    reason = 'warp_precision_limit_exceeded'
    try:
        graph.render(sources, gpu_backend=gpu, gpu_strict=True)
    except GPUUnsupported as exc:
        if reason not in str(exc):
            raise
    else:
        raise RuntimeError('Strict GPU accepted a case without the required precision extension')
    strict_transfers = {key: gpu.last_transport[key] for key in ('uploads', 'readbacks')}
    if any(strict_transfers.values()):
        raise RuntimeError('Strict rejection transferred pixels before rejecting the case')

    reference = graph.render(sources).tobytes()
    automatic = graph.render(sources, gpu_backend=gpu).tobytes()
    transport = dict(gpu.last_transport)
    if automatic != reference or transport['uploads'] or transport['readbacks']:
        raise RuntimeError('Automatic precision fallback must match the CPU and make no GPU transfers')
    if gpu.last_dispatch != {'gpu_nodes': 0, 'cpu_fallbacks': 1}:
        raise RuntimeError(f'Unexpected fallback dispatch: {gpu.last_dispatch}')
    return {'route': 'cpu_fallback', 'precision_required': True, 'reason': reason,
            'strict_rejection_verified': True, 'exact_cpu_match': True,
            'frame_sha256': sha256(reference).hexdigest(), 'strict_transfers': strict_transfers,
            'transport': transport}


def basic_cases():
    size = SIZES['small']
    images = fixture(size)
    for kind in ('wave', 'turbulence', 'swirl', 'radial', 'mirror', 'kaleidoscope', 'polar_unwrap'):
        yield kind, graph(kind), images
    mapped, inputs = mapped_fixture(size)
    yield 'displacement', mapped, inputs
    yield 'polar_wrap', precision_graph('polar_wrap_repeat', images, {'warp_radius': 94}), images
    for variant in ('rings', 'droplets', 'storm'):
        yield f'raindrop_{variant}', raindrop_graph(variant, size), images


def qualify(args, report):
    report['active_check'] = 'source_identity'
    report['source'] = source_identity()
    if report['source']['version'] != __version__:
        raise RuntimeError(f"Use matching qualification scripts and renderer: source {report['source']['version']}, "
                           f"imported FrameRudiments {__version__}")
    report['active_check'] = 'open_context_and_check_renderer'
    size = SIZES[args.size]
    backend = None if args.backend == 'auto' else args.backend
    with GLMatteBackend(backend=backend) as gpu:
        observed = device_info(gpu)
        report['device'] = {**observed, 'renderer_vendor': renderer_vendor(observed)}
        require_device(observed, vendor=args.expect_vendor, renderer=args.expect_renderer,
                       precision=args.expect_precision)
        report['driver_memory'] = gpu.sample_driver_memory()
        for name, effect, images in basic_cases():
            report['active_check'] = f'basic:{name}'
            row = qualify_warp(gpu, effect, images)
            if row['route'] != 'gpu_resident' or row['precision_required']:
                raise RuntimeError('The basic warp fixture must use the baseline shader')
            report['basic_cases'][name] = row
        for name, images, settings in precision_cases(size):
            report['active_check'] = f'precision:{name}'
            report['precision_cases'][name] = qualify_warp(gpu, precision_graph(name, images, settings), images)

    def same_device(device):
        if device != observed:
            raise RuntimeError(f'OpenGL identity changed between benchmark contexts: {device}')

    report['active_check'] = 'resource_reuse'
    resources, without_pool, with_pool = compare_resources(size, args.repeats, backend, check_device=same_device)
    report['resources'] = resources
    if args.image is not None:
        report['active_check'] = 'write_comparison'
        args.image.parent.mkdir(parents=True, exist_ok=True)
        with args.image.open('xb') as handle:
            comparison_image(resources, without_pool, with_pool).save(handle, format='PNG', optimize=True)
    report['state'] = 'checks_passed'
    report.pop('active_check')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=SIZES, default='hd')
    parser.add_argument('--backend', choices=('auto', 'egl'), default='auto')
    parser.add_argument('--expect-vendor', choices=('amd', 'intel', 'nvidia', 'software'), required=True)
    parser.add_argument('--expect-renderer', help='case-insensitive literal substring of the active renderer')
    parser.add_argument('--expect-precision', choices=('available', 'unavailable'))
    parser.add_argument('--repeats', type=int, default=6)
    parser.add_argument('--output', type=Path, required=True, help='new JSON receipt path')
    parser.add_argument('--image', type=Path, help='optional comparison PNG at a new path')
    args = parser.parse_args()
    if not 2 <= args.repeats <= 20:
        parser.error('repeats must be 2..20')
    if args.expect_vendor != 'software' and not (args.expect_renderer or '').strip():
        parser.error('hardware runs require an expected renderer substring')
    if args.output.exists() or args.image and (args.image.exists() or args.image.resolve() == args.output.resolve()):
        parser.error('use new, distinct output paths')

    report = {
        'schema': 'framerudiments.m8-9-qualification.v1', 'version': __version__,
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'platform': sys.platform, 'python': sys.version.split()[0],
        'backend_requested': args.backend, 'resolution': SIZES[args.size],
        'expected': {'vendor': args.expect_vendor, 'renderer': args.expect_renderer, 'precision': args.expect_precision},
        'precision_extension': PRECISE_SPATIAL_EXTENSION,
        'driver_overrides': {name: os.environ[name] for name in (
            'LIBGL_ALWAYS_SOFTWARE', 'LIBGL_ALWAYS_INDIRECT', 'MESA_GL_VERSION_OVERRIDE',
            'MESA_EXTENSION_OVERRIDE', 'GALLIUM_DRIVER', 'MESA_LOADER_DRIVER_OVERRIDE', 'DRI_PRIME',
        ) if name in os.environ},
        'basic_cases': {},
        'basic_resolution': SIZES['small'], 'precision_cases': {},
        'physical_adapter_attestation': 'requires_separate_host_evidence',
    }
    status = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        try:
            report['active_check'] = 'dependencies'
            report['dependencies'] = {name: version(name) for name in ('moderngl', 'glcontext', 'numpy', 'Pillow')}
            qualify(args, report)
        except Exception as exc:
            report.update(state='failed', error={'type': type(exc).__name__, 'message': str(exc)})
            print(f"M8.9 {report['active_check']}: {exc}", file=sys.stderr)
            status = 1
        handle.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    if status == 0:
        routes = [case['route'] for case in report['precision_cases'].values()]
        print(f"{report['device']['renderer']}: {len(report['basic_cases'])} basic GPU cases, "
              f"{routes.count('gpu_resident')} GPU stress cases, {routes.count('cpu_fallback')} verified CPU "
              'fallbacks, exact pooling parity')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
