"""Real OpenGL parity tests; CI must run with ModernGL + software EGL.

These explicitly skip when ModernGL/EGL is absent. A passing skip is NOT GPU
parity evidence; the dedicated gpu-mattes workflow forbids skip-only success.
"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from framerudiments.effect_graph_gpu import GLMatteBackend
from framerudiments.effect_graph_mattes import apply_matte, chroma_key


@pytest.fixture(scope='module')
def gl():
    pytest.importorskip('moderngl')
    try:
        engine = GLMatteBackend(backend='egl')
    except Exception as exc:
        pytest.skip(f'No EGL OpenGL 3.3 renderer: {exc}')
    yield engine
    engine.close()


def image_set():
    size = (96, 64)
    ink = Image.new('RGBA', size, (210, 60, 20, 250))
    ImageDraw.Draw(ink).ellipse((15, 7, 85, 58), fill=(0, 235, 0, 160))
    mat = Image.new('RGBA', size)
    ImageDraw.Draw(mat).rectangle((5, 8, 55, 51), fill=(200, 155, 95, 200))
    ImageDraw.Draw(mat).ellipse((30, 4, 90, 60), fill=(255, 255, 255, 100))
    extra = Image.new('RGBA',size)
    ImageDraw.Draw(extra).polygon([(4, 4), (80, 2), (64, 54)],fill=(140, 200, 40, 200))
    return ink, mat, extra


def difference(a, b):
    x = np.asarray(a, dtype=np.int16)
    y = np.asarray(b, dtype=np.int16)
    return np.max(np.abs(x-y),axis=(0,1)).tolist()


@pytest.mark.parametrize('channel,combine,threshold', [
    ('alpha','multiply',3),
    ('luminance','max',4),
    ('alpha','screen',4),
    ('alpha','subtract',4),
    ('alpha','difference',4),
])
def test_matte_rgb_and_alpha_parity(gl,channel,combine,threshold):
    images = image_set()
    config = {'matte_channel':channel,'mask_combine':combine,'invert':True,
              'mask_opacity':.72,'alpha_source':'original'}
    orig = images[0].getchannel('A').point(lambda a: round(a*.8))
    cpu = apply_matte(images,config,original_alpha=orig)
    gpu = gl.apply_matte(images,config,original_alpha=orig)
    error = difference(cpu,gpu)
    assert max(error[:3]) <= 1, error
    assert error[3] <= threshold, error
    assert images[0].getpixel((0,0))[3] == 250


@pytest.mark.parametrize('expand,feather,max_error', [(2,0,4),(-3,0,4),(.5,0,5), (0,2,20), (2,2,22)])
def test_morphology_blur_parity(gl,expand,feather,max_error):
    inputs = image_set()[:2]
    config = {'expand':expand,'feather':feather}
    error = difference(apply_matte(inputs,config),gl.apply_matte(inputs,config))
    assert max(error[:3]) <= 1, error
    assert error[3] <= max_error, error


@pytest.mark.parametrize('space,extra,threshold', [
    ('rgb',{},4),
    ('oklab',{},7),
    ('rgb',{'edge_expand':2},6),
    ('rgb',{'edge_feather':2},22),
    ('rgb',{'despill':.8},7),
    ('rgb',{'edge_expand':-1,'edge_feather':2,'despill':.6},24),
])
def test_keying_parity(gl,space,extra,threshold):
    image = image_set()[0]
    config = dict(key_space=space, key_colors=['#00ff00','#ffff00'],
                  tolerance=.08,softness=.16,**extra)
    cpu = chroma_key(image,config)
    gpu = gl.chroma_key(image,config)
    error = difference(cpu,gpu)
    assert max(error) <= threshold, error
    assert gpu.size == image.size


def test_original_alpha_and_repeated_frame_determinism(gl):
    image = image_set()[0]
    original = Image.new('L',image.size, 130)
    config = {'alpha_source':'original','tolerance':0.05,'softness':0.2,'key_space':'oklab'}
    first = gl.chroma_key(image,config,original_alpha=original)
    second = gl.chroma_key(image,config,original_alpha=original)
    assert first.tobytes() == second.tobytes()
    assert first.getpixel((0,0))[3] <= 130
    assert first.tobytes() == gl.chroma_key(image,config,original_alpha=original).tobytes()
