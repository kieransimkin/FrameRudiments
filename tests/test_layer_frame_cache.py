"""Per-layer cache admission, actual native output and edit-local reuse."""
from copy import deepcopy
from hashlib import sha256
import json

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.layer_frame_cache import LayerFrameCache
from framerudiments.preview_frame_cache import CacheIntegrityError
from framerudiments.preview_native_renderer import NativePreviewRenderer
from framerudiments.project import Project
from test_preview_native_project import fixture

IMPLEMENTATION = 'a'*64


@pytest.mark.parametrize('persistent', [False, True])
def test_detached_pixels_typed_metadata_and_per_layer_clear(tmp_path, persistent):
    cache = LayerFrameCache(tmp_path/'cache' if persistent else None, implementation=IMPLEMENTATION)
    image = Image.new('RGBA', (8, 8), (120, 80, 4, 127))
    meta = {'bounds': (1, 2, 4, 6), 'report': {'rows': [('native', [1, 2])], 'empty': None}}
    key = cache.key({'input': 'unchanged'})
    for layer in ('layer:a', 'layer:b'):
        assert cache.put(layer, key, image, meta)
    pixels, restored = cache.get('layer:a', key)
    assert restored == meta and isinstance(restored['bounds'], tuple)
    pixels.putpixel((0, 0), (0, 0, 0, 0))
    restored['report']['rows'].clear()
    assert cache.get('layer:a', key)[0].tobytes() == image.tobytes()
    assert cache.get('layer:a', key)[1] == meta
    cache.clear_layer('layer:a')
    assert cache.get('layer:a', key) is None
    assert cache.get('layer:b', key)[0].tobytes() == image.tobytes()
    cache.close()


def test_disk_reopen_corruption_and_immutable_input_identity(tmp_path):
    root = tmp_path/'cache'
    first = LayerFrameCache(root, implementation=IMPLEMENTATION, ram_bytes=0)
    key = first.key({'layer': {'opacity': .5}})
    next_key = first.key({'layer': {'opacity': .9}})
    image = Image.new('RGBA', (8, 8), 'red')
    first.put('a', key, image, {})
    first.close()
    second = LayerFrameCache(root, implementation=IMPLEMENTATION)
    assert second.get('a', key)[0].tobytes() == image.tobytes()
    assert second.get('a', next_key) is None
    with pytest.raises(CacheIntegrityError):
        second.put('a', key, Image.new('RGBA', (8, 8), 'blue'), {})
    blob = next((root/'blobs').glob('*/*.png'))
    blob.write_bytes(b'corrupt')
    assert second.get('a', key) is None
    assert second.stats()['layers']['a']['corruptions'] == 1
    second.put('a', next_key, Image.new('RGBA', (8, 8), 'blue'), {})
    first = LayerFrameCache(root, implementation=IMPLEMENTATION)
    first.put('a', key, image, {})  # Late old input can never replace next_key.
    assert second.get('a', next_key)[0].getpixel((0, 0)) == (0, 0, 255, 255)
    first.close()
    second.close()


@pytest.mark.parametrize('persistent', [False, True])
def test_aborted_native_admission_publishes_no_layer_pixels(tmp_path, persistent):
    cache = LayerFrameCache(tmp_path/'cache' if persistent else None, implementation=IMPLEMENTATION)
    key = cache.key({'frame': 3})
    cache._begin()
    cache.put('layer:a', key, Image.new('RGBA', (8, 8), 'red'), {})
    assert cache.get('layer:a', key) is not None
    cache._finish(False)
    assert cache.get('layer:a', key) is None
    cache._begin()
    cache.put('layer:a', key, Image.new('RGBA', (8, 8), 'red'), {})
    cache._finish(True)
    assert cache.get('layer:a', key) is not None
    cache.close()


def test_quotas_are_shared_across_layer_namespaces(tmp_path):
    cache = LayerFrameCache(tmp_path/'cache', max_bytes=650, ram_bytes=64, implementation=IMPLEMENTATION)
    key = cache.key('frame')
    for index in range(20):
        cache.put('layer:'+str(index), key, Image.new('RGBA', (8, 8), (index, 40, 60, 255)), {})
    stats = cache.stats()
    assert sum(row['bytes'] for row in stats['layers'].values()) <= 650
    assert stats['ram_bytes'] <= 64
    assert sum(row['evictions'] for row in stats['layers'].values()) > 0
    assert cache.get('layer:19', key) is not None
    assert cache.get('layer:0', key) is None
    cache.close()


def project(tmp_path, *, graph, temporal=1):
    path, data = fixture(tmp_path, graph=graph)
    data['render_quality'] = dict(enabled=True, spatial_scale=1, temporal_samples=temporal, antialias=0)
    second = deepcopy(data['layers'][0])
    second.update(id='other', center=[36, 24], opacity=.65)
    data['layers'].append(second)
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


@pytest.mark.parametrize('graph', [False, True])
@pytest.mark.parametrize('temporal', [1, 4])
def test_exact_native_pixels_across_seek_layer_edit_and_new_owner(tmp_path, graph, temporal):
    path, data = project(tmp_path, graph=graph, temporal=temporal)
    cache_root = tmp_path/'pixels'
    cache = LayerFrameCache(cache_root)
    first = Compositor(Project.load(path), layer_cache=cache)
    reference = Compositor(Project.load(path), layer_cache=False)
    try:
        for n in (3, 69, 3):
            actual, context = first.frame(n)
            expected, expected_context = reference.frame(n)
            assert actual.tobytes() == expected.tobytes() and context == expected_context
        before = cache.stats()['layers']
        assert before['layer:other']['hits'] > 0
    finally:
        first.close()
        reference.close()
        cache.close()
    edited = deepcopy(data)
    edited['layers'][0]['opacity'] = .2
    owner = LayerFrameCache(cache_root)
    changed = Compositor(Project(edited, path.parent), layer_cache=owner)
    expected = Compositor(Project(edited, path.parent), layer_cache=False)
    try:
        assert changed.frame(3)[0].tobytes() == expected.frame(3)[0].tobytes()
        stats = owner.stats()['layers']
        assert stats['layer:other']['hits'] > 0 and stats['layer:other']['writes'] == 0
        assert stats['layer:square']['writes'] > 0
    finally:
        changed.close()
        expected.close()
        owner.close()


def test_graph_and_mask_edits_recompose_without_redrawing_raw_layers(tmp_path):
    path, data = project(tmp_path, graph=True)
    cache = LayerFrameCache(tmp_path/'pixels')
    first = Compositor(Project.load(path), layer_cache=cache)
    baseline = first.frame(7)[0].tobytes()
    first.close()
    before = cache.stats()['layers']
    edited = deepcopy(data)
    edited['effect_graph']['nodes'][1]['filter_radius'] = 4
    changed = Compositor(Project(edited, path.parent), layer_cache=cache)
    reference = Compositor(Project(edited, path.parent), layer_cache=False)
    try:
        actual = changed.frame(7)[0].tobytes()
        assert actual == reference.frame(7)[0].tobytes() and actual != baseline
        after = cache.stats()['layers']
        for key in ('layer:square', 'layer:other'):
            assert after[key]['writes'] == before[key]['writes']
            assert after[key]['hits'] > before[key]['hits']
    finally:
        changed.close()
        reference.close()
        cache.close()


def test_real_preview_restart_and_quality_change_reuses_unchanged_layer(tmp_path):
    path, data = project(tmp_path, graph=True)
    cache_root = tmp_path/'persisted-layers'
    def renderer(draft):
        return NativePreviewRenderer(path, draft, {}, validate_draft=lambda a,b: None,
                                     temp_parent=tmp_path/'sessions', layer_cache_root=cache_root)
    first = renderer(data)
    try:
        first(4, 'Q5')
        first(4, 'Q3')
        first(4, 'Q5')
        assert first.last_layer_cache['layers']['layer:other']['hits'] > 0
    finally:
        first.close()
    edited = deepcopy(data)
    edited['layers'][0]['opacity'] = .2
    second = renderer(edited)
    try:
        png = second(4, 'Q5')
        assert png.startswith(b'\x89PNG\r\n\x1a\n')
        stats = second.last_layer_cache['layers']
        assert stats['layer:other']['hits'] > 0 and stats['layer:other']['writes'] == 0
        assert stats['layer:square']['writes'] > 0
    finally:
        second.close()


def test_cache_rejects_changed_pinned_input_before_reuse(tmp_path):
    path, _ = project(tmp_path, graph=True)
    owner = LayerFrameCache(tmp_path/'pixels')
    compositor = Compositor(Project.load(path), layer_cache=owner)
    try:
        compositor.frame(4)
        (tmp_path/'source.png').write_bytes(b'changed pin')
        with pytest.raises(ValueError, match='changed'):
            compositor.frame(4)
    finally:
        compositor.close()
        owner.close()

def test_asset_pin_change_rebuilds_only_its_layer(tmp_path):
    path, data = project(tmp_path, graph=True)
    second_asset = tmp_path/'other.png'
    second_asset.write_bytes((tmp_path/'source.png').read_bytes())
    data['layers'][1]['asset']['path'] = second_asset.name
    cache = LayerFrameCache(tmp_path/'pixels')
    first = Compositor(Project(data, path.parent), layer_cache=cache)
    first.frame(5)
    first.close()
    before = cache.stats()['layers']
    Image.new('RGBA', (8, 8), (20, 240, 20, 200)).save(tmp_path/'source.png')
    edited = deepcopy(data)
    edited['layers'][0]['asset']['sha256'] = sha256((tmp_path/'source.png').read_bytes()).hexdigest()
    changed = Compositor(Project(edited, path.parent), layer_cache=cache)
    reference = Compositor(Project(edited, path.parent), layer_cache=False)
    try:
        assert changed.frame(5)[0].tobytes() == reference.frame(5)[0].tobytes()
        after = cache.stats()['layers']
        assert after['layer:other']['hits'] > before['layer:other']['hits']
        assert after['layer:other']['writes'] == before['layer:other']['writes']
        assert after['layer:square']['writes'] > before['layer:square']['writes']
    finally:
        changed.close()
        reference.close()
        cache.close()


def test_mask_source_edit_updates_result_with_cached_target_pixels(tmp_path):
    path, data = project(tmp_path, graph=True)
    data['layer_masks'] = {'layer:other': {'source': 'layer:square'}}
    cache = LayerFrameCache(tmp_path/'pixels')
    original = Compositor(Project(data, path.parent), layer_cache=cache)
    before_pixels = original.frame(7)[0].tobytes()
    original.close()
    before = cache.stats()['layers']
    edited = deepcopy(data)
    edited['layers'][0]['center'][0] -= 10
    actual = Compositor(Project(edited, path.parent), layer_cache=cache)
    expected = Compositor(Project(edited, path.parent), layer_cache=False)
    try:
        pixels = actual.frame(7)[0].tobytes()
        assert pixels == expected.frame(7)[0].tobytes() and pixels != before_pixels
        after = cache.stats()['layers']
        assert after['layer:other']['writes'] == before['layer:other']['writes']
        assert after['layer:other']['hits'] > before['layer:other']['hits']
    finally:
        actual.close()
        expected.close()
        cache.close()


@pytest.mark.parametrize('graph', [False, True])
def test_native_particle_reports_survive_cached_seek_and_new_owner(tmp_path, graph):
    path, data = project(tmp_path, graph=graph)
    data['layers'].append({'id': 'mist', 'type': 'emitter', 'kind': 'particles',
                          'bounds': [0, 0, 64, 48], 'emission': {'mode': 'continuous'},
                          'style': {'seed': 42, 'rate': 17, 'size': 3, 'lifetime': .8}})
    cache = LayerFrameCache(tmp_path/'pixels')
    native = Compositor(Project(data, path.parent), layer_cache=cache)
    reference = Compositor(Project(data, path.parent), layer_cache=False)
    try:
        for n in (21, 48, 21):
            assert native.frame(n)[0].tobytes() == reference.frame(n)[0].tobytes()
            assert native.frame_reports() == reference.frame_reports()
        assert native.frame_reports()['mist']['frame'] == 21
        assert cache.stats()['layers']['layer:mist']['hits'] > 0
    finally:
        native.close()
        reference.close()
    reopened = Compositor(Project(data, path.parent), layer_cache=cache)
    try:
        reopened.frame(21)
        assert reopened.frame_reports()['mist']['frame'] == 21
    finally:
        reopened.close()
        cache.close()


@pytest.mark.parametrize('graph', [False, True])
def test_native_rudiment_history_and_reorder_keep_exact_pixels(tmp_path, graph):
    path, data = project(tmp_path, graph=graph)
    data['layers'].append({'id': 'trace', 'type': 'rudiment', 'renderer': 'path',
                          'motion': {'kind': 'named', 'pattern': 'circle'},
                          'bounds': [0, 0, 64, 48], 'history_frames': 8,
                          'style': {'padding': 4, 'line_width': 1, 'radius': 2}})
    cache = LayerFrameCache(tmp_path/'pixels')
    native = Compositor(Project(data, path.parent), layer_cache=cache)
    reference = Compositor(Project(data, path.parent), layer_cache=False)
    try:
        for n in (21, 48, 21):
            assert native.frame(n)[0].tobytes() == reference.frame(n)[0].tobytes()
            assert native.frame_reports() == reference.frame_reports()
    finally:
        native.close()
        reference.close()
    before = cache.stats()['layers']
    reordered = deepcopy(data)
    reordered['layers'].reverse()
    native = Compositor(Project(reordered, path.parent), layer_cache=cache)
    reference = Compositor(Project(reordered, path.parent), layer_cache=False)
    try:
        assert native.frame(21)[0].tobytes() == reference.frame(21)[0].tobytes()
        assert cache.stats()['layers']['layer:trace']['hits'] > before['layer:trace']['hits']
        assert cache.stats()['layers']['layer:trace']['writes'] == before['layer:trace']['writes']
    finally:
        native.close()
        reference.close()
        cache.close()


def test_source_changed_during_render_cannot_publish_partial_layers(tmp_path, monkeypatch):
    path, _ = project(tmp_path, graph=True)
    cache = LayerFrameCache(tmp_path/'pixels')
    compositor = Compositor(Project.load(path), layer_cache=cache)
    old_draw = compositor.layers[1].draw
    def corrupt_source(image, context):
        (tmp_path/'source.png').write_bytes(b'changed during rendering')
        return old_draw(image, context)
    monkeypatch.setattr(compositor.layers[1], 'draw', corrupt_source)
    try:
        with pytest.raises(ValueError, match='changed'):
            compositor.frame(7)
        assert not any(row['entries'] for row in cache.stats()['layers'].values())
        assert not any(row['writes'] for row in cache.stats()['layers'].values())
    finally:
        compositor.close()
        cache.close()


@pytest.mark.parametrize('graph', [False, True])
def test_caption_style_edit_does_not_invalidate_other_layers(tmp_path, graph):
    import os
    from pathlib import Path
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        pytest.skip('Set FRAMERUDIMENTS_TEST_FONT for native text parity')
    path, data = project(tmp_path, graph=graph)
    lyrics = tmp_path/'words.lrc'
    lyrics.write_text('[00:00.000]One\n[00:00.500]Two\n', encoding='utf-8')
    data['font'] = {'path': font, 'sha256': sha256(Path(font).read_bytes()).hexdigest()}
    data['lyrics'] = {'path': lyrics.name, 'sha256': sha256(lyrics.read_bytes()).hexdigest()}
    data['captions'] = {'box': [0, 28, 64, 48], 'font_size': 10, 'stroke': 0,
                        'colour': '#ffffff', 'panel': '#12203080'}
    cache = LayerFrameCache(tmp_path/'pixels')
    native = Compositor(Project(data, path.parent), layer_cache=cache)
    reference = Compositor(Project(data, path.parent), layer_cache=False)
    try:
        assert native.frame(4)[0].tobytes() == reference.frame(4)[0].tobytes()
        assert native.frame(4)[0].tobytes() == reference.frame(4)[0].tobytes()
    finally:
        native.close()
        reference.close()
    before = cache.stats()['layers']
    edited = deepcopy(data)
    edited['captions']['colour'] = '#ff7a38'
    native = Compositor(Project(edited, path.parent), layer_cache=cache)
    reference = Compositor(Project(edited, path.parent), layer_cache=False)
    try:
        assert native.frame(4)[0].tobytes() == reference.frame(4)[0].tobytes()
        after = cache.stats()['layers']
        for key in ('layer:square', 'layer:other', 'background'):
            assert after[key]['writes'] == before[key]['writes']
            assert after[key]['hits'] > before[key]['hits']
        assert after['caption']['writes'] > before['caption']['writes']
    finally:
        native.close()
        reference.close()
        cache.close()


def test_preflight_and_cli_share_persistent_layers_across_edits(tmp_path, monkeypatch):
    from framerudiments.cli import main
    path, data = project(tmp_path, graph=True)
    cache = tmp_path/'reusable'
    first_out = tmp_path/'first-preflight'
    args = ['preflight', str(path), '--output', str(first_out), '--max-frames', '8',
            '--layer-cache', str(cache)]
    assert main(args) == 0
    first = json.loads((first_out/'manifest.json').read_text(encoding='utf-8'))
    assert first['layer_cache']['persistent']
    data['layers'][0]['opacity'] = .25
    path.write_text(json.dumps(data), encoding='utf-8')
    next_out = tmp_path/'edited-preflight'
    assert main(['preflight', str(path), '--output', str(next_out), '--max-frames', '8',
                 '--layer-cache', str(cache)]) == 0
    changed = json.loads((next_out/'manifest.json').read_text(encoding='utf-8'))['layer_cache']['layers']
    assert changed['layer:other']['hits'] > 0 and changed['layer:other']['writes'] == 0
    assert changed['layer:square']['writes'] > 0
    assert main(['preflight', str(path), '--output', str(tmp_path/'disabled'), '--max-frames', '1',
                 '--no-layer-cache']) == 0
    assert json.loads((tmp_path/'disabled/manifest.json').read_text(encoding='utf-8'))['layer_cache'] is None


@pytest.mark.parametrize('chunked', [False, True])
def test_real_encoded_render_reuses_layers_and_matches_fresh_output(tmp_path, chunked):
    import subprocess
    from framerudiments.encoding import binaries
    from framerudiments.render import render
    path, data = project(tmp_path, graph=True)
    if chunked:
        data['audio']['repeats'] = 20
        path.write_text(json.dumps(data), encoding='utf-8')
    cache = tmp_path/'reusable'
    first_out = tmp_path/'first'
    render(path, first_out, layer_cache=cache, preset='veryfast', crf=18)
    data['layers'][0]['opacity'] = .25
    path.write_text(json.dumps(data), encoding='utf-8')
    edited_out, fresh_out = tmp_path/'edited', tmp_path/'fresh'
    result = render(path, edited_out, layer_cache=cache, preset='veryfast', crf=18,
                    chunked=chunked, chunk_seconds=10, min_free_bytes=0)
    render(path, fresh_out, layer_cache=False, preset='veryfast', crf=18,
           chunked=chunked, chunk_seconds=10, min_free_bytes=0)
    assert result['layer_cache']['layers']['layer:other']['writes'] == 0
    assert result['layer_cache']['layers']['layer:other']['hits'] > 0
    assert result['layer_cache']['layers']['layer:square']['writes'] > 0
    ffmpeg, _ = binaries()
    def decoded(out):
        return subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(out/'video.mp4'),
                                        '-map', '0:v:0', '-f', 'framehash', '-hash', 'sha256', '-'])
    assert decoded(edited_out) == decoded(fresh_out)
    if chunked:
        assert len(json.loads((edited_out/'chunk-plan.json').read_text())['chunks']) >= 2
        assert render(path, edited_out, layer_cache=cache, chunked=True, chunk_seconds=10,
                      preset='veryfast', crf=18, resume=True, min_free_bytes=0)['state'] == result['state']


def test_render_cache_cannot_be_created_inside_output(tmp_path):
    from framerudiments.render import preflight
    path, _ = project(tmp_path, graph=False)
    output = tmp_path/'public-output'
    with pytest.raises(ValueError, match='separate'):
        preflight(path, output, max_frames=1, layer_cache=output/'private-cache')
    assert not output.exists()


def test_cropped_storage_keeps_small_sprite_tiles_not_entire_canvas(tmp_path):
    path, _ = project(tmp_path, graph=True)
    cache = LayerFrameCache(tmp_path/'pixels')
    native = Compositor(Project.load(path), layer_cache=cache)
    try:
        native.frame(8)
        with cache._database() as db:
            rows = db.execute("SELECT width,height FROM frames WHERE layer LIKE 'layer:%'").fetchall()
        assert rows and all(row['width'] <= 20 and row['height'] <= 20 for row in rows)
    finally:
        native.close()
        cache.close()


def test_legacy_identity_adapter_keeps_original_canvas_alpha(tmp_path):
    # The actual font/caption test above covers this path with and without a DAG.
    # Here verify transparent RGB survives cropping before downstream effects.
    from framerudiments.compositor_layer_cache import LayerSurfaces
    path, _ = project(tmp_path, graph=True)
    cache = LayerFrameCache(tmp_path/'pixels')
    native = Compositor(Project.load(path), layer_cache=cache)
    try:
        def paint(canvas):
            canvas.putpixel((1, 1), (40, 20, 80, 0))
            canvas.putpixel((30, 20), (20, 90, 50, 180))
            return (1, 1, 31, 21)
        surfaces = native.layer_surfaces
        assert isinstance(surfaces, LayerSurfaces)
        a, _ = surfaces.draw('layer:square', native.context(8), paint)
        b, _ = surfaces.draw('layer:square', native.context(8), paint)
        assert a.tobytes() == b.tobytes()
        assert b.getpixel((1, 1)) == (40, 20, 80, 0)
    finally:
        native.close()
        cache.close()


@pytest.mark.parametrize('index', [False, True])
def test_unowned_directory_never_collects_existing_pixels(tmp_path, index):
    import sqlite3
    root = tmp_path/'foreign'
    folder = root/'blobs/ab'
    folder.mkdir(parents=True)
    pixel = folder/('a'*64+'.png')
    pixel.write_bytes(b'keep this existing asset')
    if index:
        connection = sqlite3.connect(root/'index.sqlite3')
        connection.close()
    with pytest.raises(ValueError, match='owned'):
        LayerFrameCache(root)
    assert pixel.read_bytes() == b'keep this existing asset'


def test_cache_directory_must_be_dedicated(tmp_path):
    (tmp_path/'README.md').write_text('existing project', encoding='utf-8')
    with pytest.raises(ValueError, match='dedicated'):
        LayerFrameCache(tmp_path)
    assert (tmp_path/'README.md').read_text() == 'existing project'
    assert not (tmp_path/'blobs').exists()


def _crash_cache_writer(root, key):
    import os
    cache = LayerFrameCache(root, implementation=IMPLEMENTATION)
    cache.put('layer:crash', key, Image.new('RGBA', (8, 8), 'red'), {'report': ('native', 4)})
    os._exit(0)  # Exercise WAL recovery without the ordinary close/checkpoint.


def test_application_crash_recovers_only_verified_working_pixels(tmp_path):
    import multiprocessing
    context = multiprocessing.get_context('spawn')
    key = LayerFrameCache(implementation=IMPLEMENTATION).key('application crash')
    root = tmp_path/'cache'
    worker = context.Process(target=_crash_cache_writer, args=(root, key))
    try:
        worker.start()
        worker.join(15)
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
            pytest.fail('Owned cache writer failed to exit')
        assert worker.exitcode == 0
    finally:
        worker.close()
    recovered = LayerFrameCache(root, implementation=IMPLEMENTATION)
    try:
        image, metadata = recovered.get('layer:crash', key)
        assert image.getpixel((0, 0)) == (255, 0, 0, 255)
        assert metadata == {'report': ('native', 4)}
    finally:
        recovered.close()


def test_verified_ram_hit_needs_no_database_write(tmp_path, monkeypatch):
    cache = LayerFrameCache(tmp_path/'cache', implementation=IMPLEMENTATION)
    key = cache.key('resident')
    image = Image.new('RGBA', (8, 8), 'red')
    cache.put('layer:resident', key, image, {})
    original = cache._database
    def forbidden():
        raise AssertionError('a verified RAM hit tried to use SQLite')
    monkeypatch.setattr(cache, '_database', forbidden)
    try:
        assert cache.get('layer:resident', key)[0].tobytes() == image.tobytes()
    finally:
        monkeypatch.setattr(cache, '_database', original)
        cache.close()


def test_oversize_ram_payload_is_bypassed_before_copy(tmp_path, monkeypatch):
    cache = LayerFrameCache(ram_bytes=1024)
    image = Image.new('RGBA', (64, 64), 'red')
    def forbidden(*a, **k):
        raise AssertionError('oversized raster was copied before admission')
    monkeypatch.setattr(image, 'tobytes', forbidden)
    assert not cache.put('large', cache.key('oversize'), image, {})
    assert cache.stats()['layers']['large']['bypasses'] == 1
    cache.close()


def test_final_output_cannot_be_nested_in_a_layer_cache(tmp_path):
    from framerudiments.render import preflight, render
    path, _ = project(tmp_path, graph=False)
    cache = tmp_path/'cache'
    output = cache/'public-output'
    with pytest.raises(ValueError, match='separate'):
        preflight(path, output, max_frames=1, layer_cache=cache)
    assert not cache.exists()
    with pytest.raises(ValueError, match='separate'):
        render(path, output, layer_cache=cache)
    assert not cache.exists()


def test_pending_input_identity_cannot_be_overwritten():
    cache = LayerFrameCache(implementation=IMPLEMENTATION)
    key = cache.key('same staged input')
    cache._begin()
    try:
        cache.put('layer:a', key, Image.new('RGBA', (8, 8), 'red'), {})
        with pytest.raises(CacheIntegrityError, match='pending'):
            cache.put('layer:a', key, Image.new('RGBA', (8, 8), 'blue'), {})
    finally:
        cache._finish(False)
    assert cache.get('layer:a', key) is None
    cache.close()
