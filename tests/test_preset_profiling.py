import io
import json
import os
from pathlib import Path
import zipfile

from PIL import Image
import pytest

from framerudiments.demo import create_demo
from framerudiments.io import sha256, write_json
from framerudiments.preset_library import archive_members, install_library
from framerudiments.preset_profiling import capture_frames, make_cards, profile_presets


def test_interval_frames_are_causal_and_bounded():
    assert capture_frames([0, .051, .051, .1], 60, 10) == [0, 4, 6]
    for bad in ([], [float('nan')], [-1], [1]):
        with pytest.raises(ValueError):
            capture_frames(bad, 60, 10)


def test_archive_reader_does_not_traverse_or_expand_links():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('root/../outside.milk', 'not a preset')
    with pytest.raises(ValueError, match='Unsafe'):
        list(archive_members(data.getvalue()))


def test_library_checksum_prevents_unpinned_execution(tmp_path):
    (tmp_path/'butterchurn-presets-2.4.7.tgz').write_bytes(b'untrusted')
    with pytest.raises(ValueError, match='checksum'):
        install_library(tmp_path/'library', packs=['butterchurn'], cache=tmp_path)


def test_static_cards_have_distinct_pinned_pixels(tmp_path):
    paths = make_cards(tmp_path, (128, 96), os.environ['FRAMERUDIMENTS_TEST_FONT'])
    assert len({v['sha256'] for v in paths.values()}) == 3
    with Image.open(paths['grid-rings']['path']) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    assert make_cards(tmp_path, (128, 96), os.environ['FRAMERUDIMENTS_TEST_FONT']) == paths


def suite(tmp_path):
    path = create_demo(tmp_path/'sample', os.environ['FRAMERUDIMENTS_TEST_FONT'], width=128, height=128)
    cover = path.parent/'cover.png'
    Image.new('RGB', (128, 128), 'navy').save(cover)
    data = json.loads(path.read_text())
    data['background'] = dict(image=dict(path=str(cover), sha256=sha256(cover)))
    data['layers'] = []
    data['identity'] = []
    data['audio'].update(end_sample=48000, fade_seconds=0)
    write_json(path, data)
    config = tmp_path/'suite.json'
    write_json(config, dict(schema='framerudiments.preset-suite.v1', projects=[dict(path=str(path), sha256=sha256(path))]))
    preset = tmp_path/'example.json'
    preset.write_text('{}')
    catalogue = tmp_path/'catalogue.json'
    write_json(catalogue, dict(schema='framerudiments.preset-library.v1', presets=[dict(id='test', name='Test', engine='butterchurn', preset=dict(path=str(preset), sha256=sha256(preset)))]))
    return config, catalogue


def test_real_sample_decode_sequential_captures_silence_resume_and_hashes(tmp_path):
    config, catalogue = suite(tmp_path)
    calls, levels, instances = [], [], []
    class Engine:
        def __init__(self, size, fps, preset, mix, seed, work):
            self.evidence = dict(engine='synthetic protocol fixture')
            instances.append(self)
        def process(self, image, context, pcm):
            calls.append(context.frame)
            levels.append(max(abs(v) for v in pcm))
            return image.convert('RGBA')
        def close(self):
            self.evidence['cleanup'] = dict(closed=True)
    output = tmp_path/'run'
    kwargs = dict(intervals=[0, .1], images=['cover-lyrics'], factories={'butterchurn': Engine})
    jobs = profile_presets(config, catalogue, output, **kwargs)
    assert calls == list(range(7))*2
    assert max(levels[:7]) > .1 and set(levels[7:]) == {0}
    manifest = json.loads(Path(jobs[0]['manifest']).read_text())
    assert manifest['complete'] and manifest['state'] == 'captured'
    assert all(x['runtime']['cleanup']['closed'] for x in manifest['conditions'])
    assert all(len(x['captures']) == 2 for x in manifest['conditions'])
    profile_presets(config, catalogue, output, resume=True, **kwargs)
    assert len(instances) == 2
    capture = manifest['conditions'][0]['captures'][0]['output']['path']
    Path(capture).write_bytes(b'changed')
    with pytest.raises(ValueError, match='capture bytes'):
        profile_presets(config, catalogue, output, resume=True, **kwargs)


def test_bad_shader_is_a_retained_partial_profile_and_closes_engine(tmp_path):
    config, catalogue = suite(tmp_path)
    class Failing:
        def __init__(self, *args):
            self.evidence = {}
        def process(self, *args):
            raise RuntimeError('actual shader rejected')
        def close(self):
            self.evidence['cleanup'] = dict(closed=True)
    jobs = profile_presets(config, catalogue, tmp_path/'run', intervals=[0], images=['cover-lyrics'],
                           audio_modes=['programme'], factories={'butterchurn': Failing})
    data = json.loads(Path(jobs[0]['manifest']).read_text())
    assert data['complete'] and data['state'] == 'partial'
    assert data['conditions'][0]['captures'] == []
    assert data['conditions'][0]['runtime']['cleanup']['closed']


def test_receipt_replace_failure_preserves_existing_evidence(tmp_path, monkeypatch):
    import errno
    import framerudiments.io as io_module
    path=tmp_path/'receipt.json'
    write_json(path,dict(state='verified'))
    before=path.read_bytes()
    def disk_full(*args):
        raise OSError(errno.ENOSPC,'No space left on device')
    monkeypatch.setattr(io_module.os,'replace',disk_full)
    with pytest.raises(OSError,match='No space'):
        write_json(path,dict(state='new candidate'))
    assert path.read_bytes()==before
    assert list(tmp_path.iterdir())==[path]
