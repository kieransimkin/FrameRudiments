"""S5 conservative render-dependency closures, in absolute native frame space.

Only proved time-gated layer dependencies are localised. Unknown project fields,
shared graph settings and edited timing sidecars remain global. No caller-supplied
'invalidate these frames' hint can bypass dependency discovery. No pixels render
and no authored document or clock is changed here.
"""
from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass
from fractions import Fraction
from hashlib import sha256
from importlib import metadata
from math import ceil, floor, isfinite
from pathlib import Path

from .preview_native_renderer import _sidecar
from .preview_stream_contract import (
    MAX_FRAME_COUNT, Segment, _hex, _integer, canonical_digest, quality_profiles, section_segments,
)

SCHEMA = 'framerudiments.preview-dependencies.v1'
MAX_COMPONENTS = 8192
MAX_EDGES = 262144


def implementation_fingerprint():
    """Hash installed Python implementation, bundled resources and native versions.

    No paths or source text are exposed in the returned digest. Sources are
    inspected only at session admission, not per frame. The cache is not a
    hostile-filesystem security boundary.
    """
    package = Path(__file__).parent
    sources = []
    for path in sorted(package.rglob('*')):
        if (path.is_file() and '__pycache__' not in path.parts
                and path.suffix in {'.py', '.js', '.mjs', '.glsl', '.fs', '.png', '.json', '.svg'}):
            sources.append((path.relative_to(package).as_posix(), sha256(path.read_bytes()).hexdigest()))
    versions = []
    for name in ('Pillow', 'numpy', 'moderngl', 'dancerudiments'):
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = 'not_installed'
        versions.append((name, version))
    return canonical_digest({'schema': SCHEMA, 'sources': sources, 'versions': versions})


def cache_namespace(project_file):
    """Stable workspace identity, not a claim that the complete project is unchanged."""
    return canonical_digest({'schema': SCHEMA, 'workspace': str(Path(project_file).resolve())})


@dataclass(frozen=True)
class TemporalReach:
    """Engine-supplied read windows. Never accept these as browser edit hints.

    Feedback reset frames may be supplied only by a native engine adapter.
    M11.2 derives native resets from the exact source clock. External S6 engines
    retain programme-wide propagation because their intermediate resets are
    not verified by an exported checkpoint contract.
    """
    mode: str = 'independent'
    past_frames: int = 0
    future_frames: int = 0
    reset_frames: tuple[int, ...] = ()

    def __post_init__(self):
        if self.mode not in {'independent', 'bounded', 'feedback', 'unknown'}:
            raise ValueError('unsupported temporal dependency mode')
        _integer(self.past_frames, 'past frame reach', 0, MAX_FRAME_COUNT)
        _integer(self.future_frames, 'future frame reach', 0, MAX_FRAME_COUNT)
        if (not isinstance(self.reset_frames, tuple)
                or any(type(n) is not int or not 0 <= n < MAX_FRAME_COUNT for n in self.reset_frames)
                or tuple(sorted(set(self.reset_frames))) != self.reset_frames):
            raise ValueError('verified reset frames must be sorted and unique')
        if self.mode == 'independent' and (self.past_frames or self.future_frames or self.reset_frames):
            raise ValueError('independent frames cannot declare temporal dependencies')
        if self.reset_frames and self.mode != 'feedback':
            raise ValueError('only verified feedback engines may declare reset boundaries')

    def reads(self, section, total):
        if self.mode == 'unknown':
            return 0, total
        first = max(0, section.start_frame - self.past_frames)
        end = min(total, section.end_frame_exclusive + self.future_frames)
        if self.mode == 'feedback':
            first = max((n for n in self.reset_frames if n <= section.start_frame), default=0)
        return first, end


@dataclass(frozen=True)
class SectionClosure:
    id: str
    start_frame: int
    end_frame_exclusive: int
    fingerprint: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class DependencyIndex:
    namespace: str
    document_fingerprint: str
    global_fingerprint: str
    sections: tuple[SectionClosure, ...]
    components: tuple[tuple[str, str], ...]
    reverse: tuple[tuple[str, tuple[str, ...]], ...]
    temporal_edges: tuple[tuple[str, str], ...]
    component_edges: tuple[tuple[str, str], ...]
    temporal: TemporalReach
    scope: str = 'time_gated_layers_with_conservative_shared_dependencies'

    @property
    def digest(self):
        return canonical_digest(self.document())

    def document(self):
        return {'schema': SCHEMA, **asdict(self)}

    def segments(self):
        # S1's Segment is a bounded traversal descriptor. Pixel authority lives
        # in the separate persisted section closure, not this namespace value.
        return tuple(Segment(s.id, s.start_frame, s.end_frame_exclusive, self.namespace)
                     for s in self.sections)

    def affected_by(self, component):
        return dict(self.reverse).get(component, ())


def _component(kind, identity):
    return kind + ':' + sha256(identity.encode('utf-8')).hexdigest()


def _pins(value, depth=0):
    if depth > 64:
        raise ValueError('render dependencies exceed maximum nesting')
    if isinstance(value, dict):
        for path_key, hash_key in (('path', 'sha256'), ('shader_path', 'shader_sha256')):
            if path_key in value and hash_key in value:
                path = value[path_key]
                if not isinstance(path, str) or not path:
                    raise ValueError('dependency pin needs a path')
                yield _component('asset', path), _hex(value[hash_key], 'dependency source pin')
        for child in value.values():
            yield from _pins(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _pins(child, depth + 1)


def _seconds(value, label):
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError(f'{label} must be a finite nonnegative source time')
    return Fraction(str(value))


def layer_frame_ranges(project, layer):
    """Conservative intersections with native sample-window/repeat semantics.

    Time gates use source seconds, never output seconds. floor(start*fps) includes
    a frame whose native supersamples first enter a layer; ceil(end*fps) excludes
    an exact half-open end. The project FPS and sample clock are never rewritten.
    """
    fps = _integer(project.fps, 'project FPS', 1, 120)
    rate = _integer(project.sample_rate, 'sample rate', 1, 10**13)
    first = _integer(project.start_sample, 'first sample', 0, 10**13)
    end = _integer(project.end_sample, 'end sample', first + 1, 10**13)
    repeats = _integer(project.repeats, 'source repeats', 1, 1000)
    total = _integer(project.frame_count, 'frame count', 1, MAX_FRAME_COUNT)
    start, stop = Fraction(first, rate), Fraction(end, rate)
    authored_start = _seconds(layer.get('start_seconds', 0), 'layer start')
    authored_end = _seconds(layer['end_seconds'], 'layer end') if 'end_seconds' in layer else None
    if authored_end is not None and authored_end < authored_start:
        raise ValueError('layer end precedes source start')
    a = max(start, authored_start)
    b = min(stop, authored_end if authored_end is not None else stop)
    if b <= a:
        return ()
    duration = stop - start
    return tuple((max(0, floor((a - start + cycle * duration) * fps)),
                  min(total, ceil((b - start + cycle * duration) * fps))) for cycle in range(repeats))


def _intersects(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _native_sections(project, namespace, sidecars):
    view = copy(project)
    if 'cues' in sidecars:
        # The same native ledger used by the compositor owns source->frame
        # conversion. Do not infer new section boundaries from output seconds.
        from .timeline import Event
        if not callable(getattr(project, 'ledger', None)):
            raise ValueError('edited cues require the authoritative project ledger')
        rows = sorted(enumerate(sidecars['cues']), key=lambda p: (p[1]['start'], p[0]))
        view.cues = project.ledger([Event(round(row['start'] * 1000) / 1000,
                                         row['label'], i) for i, row in rows])
    return section_segments(view, project_hash=namespace)


def build_dependency_index(project, draft, sidecars, *, namespace, renderer_abi,
                           implementation, reach=None, capabilities=None):
    """Discover closures from actual render configuration; never trust client scope.

    Locality is proved for time-gated sprite/bars/rudiment layers and their
    post-effect, animation, matte and displacement targets. Sidecars, shared
    graph/node configuration and unknown project fields stay global.
    A global graph has no section envelope in the current project schema, so its
    parameter edits MUST invalidate all sections, not an invented local scope.
    """
    namespace = _hex(namespace, 'cache workspace')
    if not isinstance(draft, dict) or draft.get('schema') != 'framerudiments.project.v1':
        raise ValueError('dependency index requires project.v1')
    if not isinstance(renderer_abi, str) or not renderer_abi:
        raise ValueError('renderer ABI required')
    implementation = _hex(implementation, 'implementation fingerprint')
    if not isinstance(sidecars, dict) or set(sidecars) - {'lines', 'words', 'cues'}:
        raise ValueError('invalid dependency sidecars')
    sidecar_hashes = {}
    for kind, rows in sidecars.items():
        if {'lines': 'lyrics', 'words': 'word_lyrics', 'cues': 'cues'}[kind] not in draft:
            raise ValueError('new timing source needs explicit pinning')
        sidecar_hashes[kind] = sha256(_sidecar(rows, cues=kind == 'cues').encode()).hexdigest()
    sections = _native_sections(project, namespace, sidecars)
    post = draft.get('post_effects', {})
    if not isinstance(post, dict):
        raise ValueError('post_effects must be an object')
    if not isinstance(post.get('elements', {}), dict):
        raise ValueError('post_effects.elements must be an object')
    if reach is None:
        mode = ('feedback' if draft.get('effects') else
                'unknown' if 'temporal' in post else 'independent')
        reach = TemporalReach(mode)
    if not isinstance(reach, TemporalReach):
        raise ValueError('engine temporal dependency contract required')
    layers = draft.get('layers', [])
    if (not isinstance(layers, list) or len(layers) > 1024
            or any(not isinstance(layer, dict) or not isinstance(layer.get('id'), str)
                   or not layer['id'] or len(layer['id']) > 128 for layer in layers)):
        raise ValueError('invalid dependency layer list')
    ids = [layer['id'] for layer in layers]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate dependency layer ID')
    by_id = dict(zip(ids, layers))
    globals_ = {k: v for k, v in draft.items() if k != 'layers'}
    # Unknown drawing semantics cannot claim time-gated locality.
    local = all(layer.get('type', 'sprite') in {'sprite', 'bars', 'rudiment'} for layer in layers)
    local_pipelines = {name: {} for name in ids}
    dispatch = {field: False for field in ('layer_masks', 'layer_displacement', 'layer_animations')}
    if not local:
        globals_['layers'] = layers
    else:
        def split_targets(mapping, label):
            if not isinstance(mapping, dict) or len(mapping) > 256:
                raise ValueError('invalid bounded per-layer dependency map')
            shared_mapping = {}
            for target, value in mapping.items():
                if not isinstance(target, str):
                    raise ValueError('invalid dependency map target')
                if target.startswith('layer:'):
                    if target[6:] not in by_id:
                        raise ValueError('unknown time-gated layer dependency target')
                    local_pipelines[target[6:]][label] = value
                else:
                    shared_mapping[target] = value
            return shared_mapping
        for field in ('layer_masks', 'layer_displacement', 'layer_animations'):
            if field in draft:
                cleaned = split_targets(draft[field], field)
                dispatch[field] = bool(draft[field])
                if cleaned:
                    globals_[field] = cleaned
                else:
                    globals_.pop(field, None)
        if 'elements' in post:
            rest = {k: v for k, v in post.items() if k != 'elements'}
            cleaned = split_targets(post['elements'], 'post_effects.elements')
            if cleaned:
                rest['elements'] = cleaned
            if rest:
                globals_['post_effects'] = rest
            else:
                globals_.pop('post_effects', None)
        # Enabling the isolation pipeline can change final alpha/matte behaviour
        # even on inactive layers. Its dispatch flags are therefore GLOBAL.
        dispatch['post_effects'] = bool(post.get('all_elements') or post.get('frame') or
                                        any(post.get('elements', {}).values()) or 'temporal' in post)
    components = {}
    component_edges = set()
    def add(key, value):
        digest = canonical_digest(value)
        if key in components and components[key] != digest:
            raise ValueError('ambiguous shared render dependency')
        components[key] = digest
        if len(components) > MAX_COMPONENTS:
            raise ValueError('too many render dependency components')
        return key
    def register_pins(value):
        return {add(key, {'pin': digest}) for key, digest in _pins(value)}
    window = {key: getattr(project, key) for key in
              ('fps', 'frame_count', 'sample_rate', 'start_sample', 'end_sample', 'repeats')}
    quality_contract = [asdict(p) for p in quality_profiles(project)]
    # Capability eligibility is global, but a complete derived-draft hash would
    # falsely invalidate every section after any isolated layer edit.
    supported = None if capabilities is None else [(r['id'], r['supported']) for r in capabilities]
    shared = {add('shared-pipeline', {'project': globals_, 'sidecars': sidecar_hashes,
                                    'window': window, 'renderer': renderer_abi,
                                    'implementation': implementation, 'profiles': quality_contract,
                                    'supported_profiles': supported, 'isolation_dispatch': dispatch, 'temporal': asdict(reach)})}
    global_pins = register_pins(globals_)
    shared |= global_pins
    component_edges.update((pin, 'shared-pipeline') for pin in global_pins)
    graph = draft.get('effect_graph', {})
    if graph:
        if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list):
            raise ValueError('invalid graph dependency document')
        nodes = graph['nodes']
        if len(nodes) > 256 or any(not isinstance(n, dict) or not isinstance(n.get('id'), str) for n in nodes):
            raise ValueError('invalid graph dependency node list')
        names = [n['id'] for n in nodes]
        if len(set(names)) != len(names):
            raise ValueError('duplicate graph dependency node')
        for node in nodes:
            key = add(_component('graph-node', node['id']), node)
            pins = register_pins(node)
            shared.add(key)
            shared |= pins
            component_edges.update((pin, key) for pin in pins)
            inputs = node.get('inputs', [])
            if not isinstance(inputs, list) or any(not isinstance(n, str) or n not in names for n in inputs):
                raise ValueError('invalid graph dependency input')
            component_edges.update((_component('graph-node', source), key) for source in inputs)
            if node.get('type') == 'source' and node['id'].startswith('layer:'):
                if node['id'][6:] not in by_id:
                    raise ValueError('unknown graph layer dependency')
                component_edges.add((_component('layer', node['id'][6:]), key))
    ranges = {name: layer_frame_ranges(project, layer) if local else ((0, project.frame_count),)
              for name, layer in by_id.items()}
    layer_keys = {}
    for name, layer in by_id.items():
        effective = {'layer': layer, 'scoped_pipeline': local_pipelines[name]}
        key = add(_component('layer', name), effective)
        pins = register_pins(effective)
        layer_keys[name] = {key, *pins}
        component_edges.update((pin, key) for pin in pins)
    # Explicit isolated-layer references are followed transitively; an active
    # matte/displacement consumer depends on its source's complete pipeline.
    references = {name: set() for name in ids}
    for field in ('layer_masks', 'layer_displacement'):
        mapping = draft.get(field, {})
        if not isinstance(mapping, dict):
            raise ValueError('invalid layer dependency map')
        for target, config in mapping.items():
            target_name = target.removeprefix('layer:')
            if target_name in references and isinstance(config, dict):
                source = config.get('source')
                if isinstance(source, str) and source.startswith('layer:'):
                    source_name = source[6:]
                    if source_name not in by_id:
                        raise ValueError('unknown isolated-layer dependency')
                    references[target_name].add(source_name)
                    component_edges.add((_component('layer', source_name), _component('layer', target_name)))
    reverse = {}
    edges, closures = [], []
    for section in sections:
        read = reach.reads(section, project.frame_count)
        used = {name for name in ids if any(_intersects(read, r) for r in ranges[name])}
        pending = list(used)
        while pending:
            for source in references[pending.pop()] - used:
                used.add(source)
                pending.append(source)
        deps = set(shared)
        for name in used:
            deps |= layer_keys[name]
        deps.add(add('order:' + section.id, [name for name in ids if name in used]))
        for dependency in deps:
            reverse.setdefault(dependency, []).append(section.id)
        for upstream in sections:
            if upstream.id != section.id and _intersects(read, (upstream.start_frame, upstream.end_frame_exclusive)):
                edges.append((upstream.id, section.id))
        if sum(map(len, reverse.values())) + len(edges) > MAX_EDGES:
            raise ValueError('too many reverse render dependency edges')
        digest = canonical_digest({'schema': SCHEMA, 'range': [section.start_frame, section.end_frame_exclusive],
                                   'dependencies': [(k, components[k]) for k in sorted(deps)]})
        closures.append(SectionClosure(section.id, section.start_frame, section.end_frame_exclusive,
                                       digest, tuple(sorted(deps))))
    if len(component_edges) > MAX_EDGES:
        raise ValueError('too many component dependency edges')
    return DependencyIndex(namespace, canonical_digest({'project': draft, 'sidecars': sidecar_hashes}),
                           components['shared-pipeline'], tuple(closures), tuple(sorted(components.items())),
                           tuple((k, tuple(v)) for k, v in sorted(reverse.items())), tuple(edges), tuple(sorted(component_edges)), reach)
