"""M8.1 backend discovery and allocation-free effect-graph preflight.

This is a contract/planner, not a second executor. It describes the existing
host RGBA8 graph and per-node GL bridge. It never imports ModernGL, creates a
context, executes shaders, allocates image buffers or advances a motion clock.
Payload accounting is not an RSS/VRAM measurement or a complete memory bound.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from importlib import metadata, util

from .effect_graph_surface_transport import bridge_contract, transport_capabilities

GPU_KINDS = frozenset({'matte', 'chroma_key', 'spectral_displacement',
                       'transition', 'chromatic_displacement', 'filter'})
CPU_EFFECTS = frozenset({'layer_style', 'spatial_warp', 'raster_effect', 'procedural_field'})
HOST_KINDS = frozenset({'source', 'copy', 'opacity', 'composite'})
KNOWN_KINDS = GPU_KINDS | CPU_EFFECTS | HOST_KINDS
MAX_GPU_MORPH_RADIUS = 16
MAX_GPU_GAUSSIAN_RADIUS = 12
MAX_GPU_KEY_COLOURS = 8
MAX_GPU_INPUT_MATTES = 8
DEFAULT_GPU_BUDGET = 768 * 1024**2
MAX_DIMENSION = 16384
MAX_PIXELS = 67_108_864


def _integer(value, label, low=1, high=None):
    if type(value) is not int or value < low or high is not None and value > high:
        raise ValueError(f'{label} must be an integer >= {low}' + (f' and <= {high}' if high else ''))
    return value


@dataclass(frozen=True)
class SurfaceContract:
    """Current supported surface ABI. Future float/HDR formats are not implied.

    row_order describes the consumer's logical rows, not OpenGL coordinates.
    R8 is alpha-lineage metadata; all executable graph image ports use RGBA8.
    """
    width: int
    height: int
    format: str = 'rgba8'
    alpha: str = 'straight'
    colour_space: str = 'display-referred'
    row_order: str = 'top-first'
    residency: str = 'host'

    def __post_init__(self):
        _integer(self.width, 'surface width', high=MAX_DIMENSION)
        _integer(self.height, 'surface height', high=MAX_DIMENSION)
        if self.width * self.height > MAX_PIXELS:
            raise ValueError('surface exceeds pixel contract')
        if (not isinstance(self.format, str) or self.format not in {'rgba8', 'r8'}
                or not isinstance(self.residency, str) or self.residency not in {'host', 'device'}):
            raise ValueError('unsupported surface format or residency')
        expected = ('straight', 'display-referred') if self.format == 'rgba8' else ('coverage', 'data')
        if (self.alpha, self.colour_space) != expected or self.row_order != 'top-first':
            raise ValueError('unsupported surface alpha, colour space or row order')

    @property
    def size(self):
        return self.width, self.height

    @property
    def nbytes(self):
        return self.width * self.height * (4 if self.format == 'rgba8' else 1)

    def describe(self):
        return {**asdict(self), 'bytes': self.nbytes,
                'row_stride_bytes': self.width * (4 if self.format == 'rgba8' else 1)}

    def lineage(self):
        return SurfaceContract(self.width, self.height, 'r8', 'coverage', 'data')


def rgba_surface(value):
    """Coerce a dimension pair or explicit descriptor without touching pixels."""
    if isinstance(value, SurfaceContract):
        surface = value
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        surface = SurfaceContract(*value)
    else:
        raise ValueError('source surface must be a SurfaceContract or [width, height]')
    if surface.format != 'rgba8' or surface.residency != 'host':
        raise ValueError('current graph ports require host straight RGBA8')
    return surface


@dataclass(frozen=True)
class PlanLimits:
    """Opt-in checks on precisely named payloads, not total process memory."""
    max_retained_host_bytes: int | None = None
    max_transfer_bytes: int | None = None

    def __post_init__(self):
        for name in ('max_retained_host_bytes', 'max_transfer_bytes'):
            value = getattr(self, name)
            if value is not None:
                _integer(value, name)


@dataclass(frozen=True)
class GPURequirements:
    supported: bool
    reason: str | None = None
    texture_count: int = 0
    upload_count: int = 0
    readback_count: int = 0
    draw_passes: int = 0


def gpu_requirements(kind, config, input_count, *, original_alpha=True):
    """Shared GL bridge predicate and exact live texture-payload accounting.

    The matte/key bridge retains every pass target until finally. A morphology
    pass and blur each create TWO textures. Alpha metadata is uploaded as RGBA8
    even when the authored node selects current alpha. Spectral targets are reused
    within the call, but are not shared across graph nodes.
    """
    if not isinstance(config, Mapping) or type(original_alpha) is not bool:
        raise ValueError('invalid GPU requirement request')
    _integer(input_count, 'input count', high=32)
    if not isinstance(kind, str):
        raise ValueError('GPU node kind must be a string')
    if kind not in GPU_KINDS:
        return GPURequirements(False, 'kernel_not_implemented')
    if kind == 'filter':
        from .effect_graph_filter import validate_filter
        validate_filter(dict(config), input_count)
        style = config.get('filter_type', 'blur')
        if (config.get('filter_mix', 1) == 0
                or style == 'blur' and config.get('filter_radius', 1.5) == 0
                or style == 'bloom' and config.get('filter_strength', 1) == 0):
            return GPURequirements(True, 'exact_host_identity')
        passes = 4 if style == 'bloom' else 3
        return GPURequirements(True, texture_count=passes + 1, upload_count=1,
                               readback_count=1, draw_passes=passes)
    if kind == 'transition':
        if input_count not in (2, 3):
            raise ValueError('transition must receive two or three inputs')
        if config.get('transition_style', 'dissolve') not in {'dissolve', 'wipe', 'radial'}:
            return GPURequirements(False, 'transition_style_not_implemented')
        if input_count != 2:
            raise ValueError('GPU transition styles require two inputs')
        if config.get('progress', 0) in (0, 1):
            return GPURequirements(True, 'exact_host_identity')
        return GPURequirements(True, texture_count=3, upload_count=2,
                               readback_count=1, draw_passes=1)
    if kind == 'chromatic_displacement':
        if input_count != 1:
            raise ValueError('chromatic displacement needs one input')
        if not any(any(config.get(f'{c}_offset', (0, 0))) for c in ('red','green','blue')):
            return GPURequirements(True, 'exact_host_identity')
        return GPURequirements(True, texture_count=2, upload_count=1,
                               readback_count=1, draw_passes=1)
    if kind == 'spectral_displacement':
        from .effect_graph_spectral import validate_spectral
        validate_spectral(dict(config))
        if input_count != 1:
            raise ValueError('spectral displacement needs one input')
        if config.get('mix', 1) == 0:
            return GPURequirements(True, 'exact_host_identity')
        return GPURequirements(True, texture_count=4, upload_count=1,
                               readback_count=1, draw_passes=len(config['bands']) + 2)
    from .effect_graph_mattes import validate_key, validate_matte
    if kind == 'matte':
        validate_matte(dict(config), input_count)
        morph, blur = config.get('expand', 0), config.get('feather', 0)
    else:
        validate_key(dict(config))
        if input_count != 1:
            raise ValueError('chroma key needs one input')
        morph, blur = config.get('edge_expand', 0), config.get('edge_feather', 0)
        if (config.get('edge_decontaminate', 0)
                and config.get('edge_radius', 3) > MAX_GPU_GAUSSIAN_RADIUS):
            return GPURequirements(False, 'edge_decontamination_radius_limit_exceeded')
    if abs(morph) > MAX_GPU_MORPH_RADIUS or blur > MAX_GPU_GAUSSIAN_RADIUS:
        return GPURequirements(False, 'radius_limit_exceeded')
    uploads = input_count + int(original_alpha)
    passes = 2 + 2 * bool(morph) + 2 * bool(blur)
    if kind == 'chroma_key' and config.get('edge_decontaminate', 0):
        passes += 4
    return GPURequirements(True, texture_count=uploads + passes, upload_count=uploads,
                           readback_count=1, draw_passes=passes)


def _package(distribution, module):
    try:
        version = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        version = None
    try:
        found = util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    return {'version': version, 'module_discoverable': found, 'binary_import_tested': False}


def _device(backend):
    if backend is None:
        return {'state': 'not_attached', 'available': False, 'shader_validation': 'not_performed'}
    # Accept only the executor whose predicates this module describes. A declared
    # foreign plugin must not be silently treated as a tested ModernGL backend.
    from .effect_graph_gpu import GLMatteBackend
    if not isinstance(backend, GLMatteBackend):
        raise ValueError('unsupported GPU backend implementation')
    if getattr(backend, 'closed', False):
        return {'state': 'closed', 'available': False, 'shader_validation': 'not_performed'}
    version = getattr(backend.ctx, 'version_code', None)
    info = backend.ctx.info
    if not isinstance(info, Mapping):
        raise ValueError('invalid GPU context info')
    limit = info.get('GL_MAX_TEXTURE_SIZE', 16384)
    budget = backend.max_working_set_bytes
    _integer(limit, 'device texture limit')
    _integer(budget, 'device payload budget')
    _integer(version, 'OpenGL version')
    return {'state': 'attached', 'available': version >= 330,
            'context_version': version, 'renderer': str(info.get('GL_RENDERER', 'unknown')),
            'vendor': str(info.get('GL_VENDOR', 'unknown')),
            'max_texture_size': limit, 'texture_limit_reported': 'GL_MAX_TEXTURE_SIZE' in info,
            'max_texture_payload_bytes': budget, 'shader_validation': 'not_performed',
            'context_ownership': 'caller; discovery does not create, close or mutate it'}


def backend_registry(gpu_backend=None):
    """Detached, JSON-safe discovery. Installed is NOT the same as executable."""
    device = _device(gpu_backend)
    return {
        'schema': 'framerudiments.backend-capabilities.v1',
        'packages': {'pillow': _package('Pillow', 'PIL'), 'numpy': _package('numpy', 'numpy'),
                     'moderngl': _package('moderngl', 'moderngl')},
        'backends': {
            'pillow': {'role': 'host_surfaces_and_compositing', 'gpu_native': False},
            'numpy': {'role': 'cpu_effect_kernels', 'gpu_native': False},
            'moderngl': {'role': 'resident_graph_islands_and_per_node_bridge', 'device': device,
                         'kernel_kinds': sorted(GPU_KINDS), 'gpu_resident_graph': True,
                         'mixed_cpu_gpu_islands': True, 'cross_node_texture_pool': True,
                         'limits': {'morph_radius': MAX_GPU_MORPH_RADIUS,
                                    'gaussian_radius': MAX_GPU_GAUSSIAN_RADIUS,
                                    'edge_decontamination_radius': MAX_GPU_GAUSSIAN_RADIUS,
                                    'key_colours': MAX_GPU_KEY_COLOURS,
                                    'input_mattes': MAX_GPU_INPUT_MATTES}},
        },
        'surface_transport': transport_capabilities(),
        'node_kinds': sorted(KNOWN_KINDS),
        'external_backends': [],
        'probe_policy': 'no context creation, shader compilation, rendering or model downloads',
    }


def _numpy_needed(node):
    if node.kind in {'source', 'copy', 'opacity', 'matte'}:
        return False
    if node.kind == 'composite' and node.config.get('mode', 'over') == 'over':
        return False
    if node.kind == 'spectral_displacement' and node.config.get('mix', 1) == 0:
        return False
    # Conservative package-level requirement: some identity/style variants can
    # avoid numpy internally, but are not advertised as a numpy-free effect ABI.
    return True


def _gpu_legacy_rejects(node):
    if node.kind in CPU_EFFECTS:
        return True
    if node.kind == 'transition':
        return node.config.get('transition_style', 'dissolve') not in {'dissolve', 'wipe', 'radial'}
    return False


def _diagnostic(code, message, node=None):
    return {'code': code, 'message': message, 'node': node}


class BackendPlanError(ValueError):
    """A rejected explicit preflight, with its detached diagnostic report."""
    def __init__(self, report):
        self.report = deepcopy(report)
        text = '; '.join(f'{r["node"] or "graph"}: {r["code"]}' for r in report['errors'])
        super().__init__('Effect graph backend preflight rejected: ' + text)


def plan_effect_graph(graph, source_surfaces, *, mode='cpu', gpu_backend=None,
                      parameter_values=None, context=None, limits=None, require=False):
    """Plan one resolved sample without rendering it or changing the executor.

    Native bindings can supply parameter_values/context through their existing
    adapter. Recompute after any control or source-size change. No prior-frame
    state, implicit device creation or persistent plan cache is used.
    """
    from .effect_graph import EffectGraph

    if not isinstance(graph, EffectGraph):
        raise ValueError('plan needs a validated EffectGraph')
    if not isinstance(mode, str) or mode not in {'cpu', 'auto', 'gpu'} or type(require) is not bool:
        raise ValueError('mode must be cpu, auto or gpu; require must be boolean')
    if not isinstance(source_surfaces, Mapping):
        raise ValueError('source_surfaces must be a mapping')
    if limits is None:
        limits = PlanLimits()
    if not isinstance(limits, PlanLimits):
        raise ValueError('limits must be PlanLimits')
    if mode == 'cpu' and gpu_backend is not None:
        raise ValueError('CPU plan must not attach a GPU backend')
    # Same resolver as render, followed by full semantic validation before any
    # resource decision. Never copy or replace the native modulation engine.
    resolved = graph._resolved_configs(parameter_values, context)
    spec = {'nodes': [deepcopy(resolved.get(k, n.config)) for k, n in graph.nodes.items()],
            'output': graph.output}
    effective = EffectGraph(spec)
    registry = backend_registry(gpu_backend)
    device = registry['backends']['moderngl']['device']
    errors, warnings, rows = [], [], {}
    if mode == 'gpu' and not device['available']:
        errors.append(_diagnostic('gpu_unavailable', 'Strict GPU mode needs an attached OpenGL 3.3+ context'))
    if mode == 'auto' and not device['available']:
        warnings.append(_diagnostic('gpu_not_attached', 'CPU plan; a package install is not a device probe'))
    surfaces, owners, alpha_bytes, consumers = {}, {}, {}, {}
    index = {key: i for i, key in enumerate(effective.order)}
    for key in effective.order:
        consumers[key] = []
    for key in effective.order:
        for upstream in effective.nodes[key].inputs:
            consumers[upstream].append(key)
    uploads = readbacks = peak_device = 0
    for key in effective.order:
        node = effective.nodes[key]
        if node.kind not in KNOWN_KINDS:
            raise ValueError(f'unregistered effect node kind: {node.kind}')
        if node.kind == 'source':
            if key not in source_surfaces:
                raise ValueError(f'missing source surface contract: {key}')
            surface = rgba_surface(source_surfaces[key])
        else:
            surface = surfaces[node.inputs[0]]
            # Live texture overlays are the only current multi-resolution port.
            if node.kind != 'layer_style' and any(surfaces[i].size != surface.size for i in node.inputs):
                raise ValueError(f'{key}: graph input dimensions must match')
        surfaces[key] = surface
        if node.kind in {'source', 'transition', 'composite', 'procedural_field', 'spatial_warp', 'raster_effect'}:
            owners[key] = key
            alpha_bytes[key] = surface.width * surface.height
        else:
            owners[key] = owners[node.inputs[0]]
        req = (gpu_requirements(node.kind, node.config, len(node.inputs))
               if node.kind in GPU_KINDS else GPURequirements(False, 'kernel_not_implemented'))
        selected, reason = 'cpu', 'cpu_requested'
        texture_bytes = surface.nbytes * req.texture_count
        if mode != 'cpu':
            reason = 'gpu_not_attached'
            if device['available']:
                reason = req.reason
                if node.kind in GPU_KINDS and req.supported:
                    if req.reason == 'exact_host_identity':
                        selected, reason = 'cpu', 'exact_host_identity'
                    elif max(surface.size) > device['max_texture_size']:
                        reason = 'texture_dimension_limit'
                    elif texture_bytes > device['max_texture_payload_bytes']:
                        reason = 'texture_payload_budget'
                    else:
                        selected, reason = 'gpu_bridge', None
                if selected == 'cpu' and reason is None:
                    reason = 'kernel_not_implemented'
                if mode == 'gpu' and selected == 'cpu' and (
                        _gpu_legacy_rejects(node) or node.kind in GPU_KINDS and not req.reason == 'exact_host_identity'):
                    errors.append(_diagnostic(reason, 'Existing strict GPU executor rejects this node', key))
            if selected == 'cpu' and not any(r['node'] == key for r in errors):
                warnings.append(_diagnostic(reason, 'Host execution; this is not a GPU-native operation', key))
        cpu_impl = 'numpy' if _numpy_needed(node) else 'pillow'
        if selected == 'cpu' and not registry['packages'][cpu_impl]['module_discoverable']:
            errors.append(_diagnostic('cpu_dependency_missing', f'{cpu_impl} is required by this CPU contract', key))
        if node.kind == 'filter' and surface.width * surface.height > 16_777_216:
            errors.append(_diagnostic('filter_pixel_limit', 'Filter canvas exceeds 16M pixel budget', key))
        if node.kind in {'spatial_warp', 'raster_effect', 'procedural_field'}:
            # Their public kernels all enforce 8K pixel/16K dimension ceilings.
            if surface.width * surface.height > 33_177_600:
                errors.append(_diagnostic('effect_pixel_limit', 'Effect canvas exceeds 8K pixel budget', key))
        moved_up = surface.nbytes * req.upload_count if selected == 'gpu_bridge' else 0
        moved_down = surface.nbytes * req.readback_count if selected == 'gpu_bridge' else 0
        uploads += moved_up
        readbacks += moved_down
        peak_device = max(peak_device, texture_bytes if selected == 'gpu_bridge' else 0)
        last = max([index[key], *(index[c] for c in consumers[key])])
        if key == effective.output:
            last = len(effective.order)  # final host copy consumes this result
        rows[key] = {
            'kind': node.kind, 'inputs': list(node.inputs), 'surface': surface.describe(),
            'alpha_owner': owners[key], 'cpu_implementation': cpu_impl,
            'selected_backend': selected, 'decision_reason': reason,
            'gpu_kernel': asdict(req), 'gpu_texture_payload_bytes': texture_bytes if selected == 'gpu_bridge' else 0,
            'upload_bytes': moved_up, 'readback_bytes': moved_down,
            'host_output_opacity': node.config.get('opacity', 1) != 1,
            'lifetime': {'birth_step': index[key], 'last_consumer_step': last,
                         'consumer_edges': len(consumers[key]), 'consumers': list(consumers[key])},
            'parameters': deepcopy(node.config),
            'transport': {**bridge_contract(surface.size).describe(),
                          'uploads': req.upload_count if selected == 'gpu_bridge' else 0,
                          'readbacks': req.readback_count if selected == 'gpu_bridge' else 0,
                          'contract': 'per-node RGBA8 bridge; not a resident device texture'},
        }
    # The current executor keeps ALL results and lineages until return. Report
    # that reality, not the smaller memory usage a future last-use release could achieve.
    source_bytes = sum(surfaces[k].nbytes for k in effective.source_ids)
    result_bytes = sum(s.nbytes for s in surfaces.values())
    lineage_bytes = sum(alpha_bytes.values())
    final_bytes = surfaces[effective.output].nbytes
    retained_bytes = source_bytes + result_bytes + lineage_bytes + final_bytes
    lineage_rows = {}
    for owner, nbytes in alpha_bytes.items():
        members = [k for k in effective.order if owners[k] == owner]
        uses = [index[k] for k in effective.order
                if k in members or any(owners[i] == owner for i in effective.nodes[k].inputs)]
        lineage_rows[owner] = {'surface': surfaces[owner].lineage().describe(), 'bytes': nbytes,
                               'aliases': members, 'last_consumer_step': max(uses)}
    if limits.max_retained_host_bytes is not None and retained_bytes > limits.max_retained_host_bytes:
        errors.append(_diagnostic('retained_host_payload_budget', 'Retained logical image payload exceeds limit'))
    if limits.max_transfer_bytes is not None and uploads + readbacks > limits.max_transfer_bytes:
        errors.append(_diagnostic('transfer_payload_budget', 'Planned per-sample upload/readback payload exceeds limit'))
    report = {
        'schema': 'framerudiments.backend-plan.v1', 'mode': mode,
        'eligible': not errors, 'scope': 'single resolved graph sample; not shader/runtime certification',
        'gpu_resident_graph': False, 'new_kernels': True,
        'parameters_resolved': parameter_values is not None,
        'context_supplied': context is not None,
        'output': effective.output, 'order': list(effective.order),
        'unreachable_nodes': sorted(set(effective.nodes) - set(effective.order)),
        'registry': registry, 'nodes': rows, 'alpha_lineages': lineage_rows,
        'resources': {
            'caller_source_bytes': source_bytes, 'retained_result_bytes': result_bytes,
            'retained_lineage_bytes': lineage_bytes, 'final_copy_bytes': final_bytes,
            'retained_host_payload_bytes': retained_bytes,
            'peak_device_texture_payload_bytes': peak_device,
            'upload_bytes': uploads, 'readback_bytes': readbacks,
            'texture_pooling': False, 'last_use_release': False,
            'excluded': ['CPU effect scratch buffers', 'Pillow/NumPy/Python object overhead',
                         'driver/program/framebuffer metadata', 'allocator alignment',
                         'shader compilation', 'compositor/decoder/encoder buffers'],
            'measurement': False,
        },
        'limits': asdict(limits), 'errors': errors, 'warnings': warnings,
        'legacy_gpu_mode': 'host copies, opacity, compositing and basic transitions can remain on CPU',
    }
    if require and errors:
        raise BackendPlanError(report)
    return report
