"""M9.5 pinned host reference verification; no GPU or media dependencies."""
from hashlib import sha256
from pathlib import Path
import shutil

from PIL import Image
import pytest

from framerudiments.gl_transition_goldens import (
    CASES, MANIFEST, SIZE, reference_image, shader_config, synthetic_sources, verify_manifest,
)
from framerudiments.gl_transitions import shader_pass_plan, validate_gl_transition

ROOT = Path(__file__).resolve().parents[1]


def test_approved_reference_images_are_pinned_and_match_independent_integer_model():
    manifest = verify_manifest(ROOT)
    assert len(manifest['cases']) == len(CASES) == 5
    assert len({x['rgba_sha256'] for x in manifest['cases']}) == 5
    a, b = synthetic_sources()
    assert a.size == b.size == SIZE and a.mode == b.mode == 'RGBA'
    for case in manifest['cases']:
        expected = reference_image(case['id'])
        with Image.open(ROOT / case['golden']) as png:
            assert png.convert('RGBA').tobytes() == expected.tobytes()
        assert sha256(expected.tobytes()).hexdigest() == case['rgba_sha256']
        cfg = shader_config(case, ROOT)
        validate_gl_transition(cfg)
        plan = shader_pass_plan(cfg, SIZE)
        assert plan['passes'] == len(case['shaders'])
        assert plan['textures'] == plan['passes'] + 2
        assert plan['eligible']


def test_independent_time_and_coordinate_cases_have_no_history():
    a, b = synthetic_sources()
    previous = reference_image('timed-split-before')
    following = reference_image('timed-split-after')
    assert previous.tobytes() == a.tobytes()
    for y in (0, SIZE[1] - 1):
        for x in (0, SIZE[0] // 2 - 1):
            assert following.getpixel((x, y)) == a.getpixel((x, y))
        for x in (SIZE[0] // 2, SIZE[0] - 1):
            assert following.getpixel((x, y)) == b.getpixel((x, y))
    reinforced = reference_image('reinforce-two-pass')
    for xy in ((0, 0), (11, 17), (SIZE[0] - 1, SIZE[1] - 1)):
        rgba_a, rgba_b = a.getpixel(xy), b.getpixel(xy)
        assert reinforced.getpixel(xy) == tuple((ca + 3 * cb) // 4
                                               for ca, cb in zip(rgba_a, rgba_b, strict=True))


def test_reference_generator_never_overwrites_reviewed_files(tmp_path):
    from scripts.generate_gl_goldens import generate
    output = tmp_path / 'proposed'
    new = generate(output)
    approved = verify_manifest(ROOT)
    assert [c['id'] for c in new['cases']] == [c['id'] for c in approved['cases']]
    assert [c['rgba_sha256'] for c in new['cases']] == [c['rgba_sha256'] for c in approved['cases']]
    with pytest.raises(ValueError, match='already exists'):
        generate(output)


def test_manifest_rejects_tampered_reference_before_gpu(tmp_path):
    (tmp_path / 'examples').mkdir()
    (tmp_path / MANIFEST.parent).mkdir(parents=True)
    for shader in ('gl-transition-crossfade.glsl', 'gl-transition-reinforce.glsl', 'gl-transition-timed-split.glsl'):
        shutil.copyfile(ROOT / 'examples' / shader, tmp_path / 'examples' / shader)
    for f in (ROOT / MANIFEST.parent).iterdir():
        shutil.copyfile(f, tmp_path / MANIFEST.parent / f.name)
    assert verify_manifest(tmp_path)['schema'] == verify_manifest(ROOT)['schema']
    with (tmp_path / MANIFEST.parent / 'crossfade-half.png').open('ab') as stream:
        stream.write(b'corruption')
    with pytest.raises(ValueError, match='PNG SHA-256 mismatch'):
        verify_manifest(tmp_path)
