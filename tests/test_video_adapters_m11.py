"""M11.4 rational-PTS input/output: isolated and real FFmpeg regression tests."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from PIL import Image

from framerudiments.video_adapters import (
    MANIFEST_SCHEMA, VideoInput, _fraction, _probe, _rate,
    encode_manifest, extract_png,
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_manifest(tmp_path, *, fps="30000/1001", count=6):
    files = []
    for i in range(count):
        path = tmp_path / f"frame-{i:03d}.png"
        Image.new("RGB", (64, 48), (i * 35, i * 25, i * 12)).save(path)
        files.append({"path": path.name, "sha256": sha(path)})
    manifest = tmp_path / "frames.json"
    manifest.write_text(json.dumps({"schema": MANIFEST_SCHEMA, "width": 64, "height": 48,
                                    "fps": fps, "frames": files}), encoding="utf-8")
    return manifest


def fake_input():
    return VideoInput(Path('/unused'), 'a'*64, 0, 0, 64, 48,
                      Fraction(1,90000), (9000,12003,18009,27009), Fraction(30000,1001))


def test_exact_vfr_index_floor_ceil_nearest_and_tie():
    source=fake_input()
    assert source.is_vfr
    assert source.timestamps == (Fraction(0),Fraction(3003,90000),
                                 Fraction(9009,90000),Fraction(18009,90000))
    assert source.frame_at(Fraction(3003,90000))==1
    assert source.frame_at(Fraction(3002,90000),policy='floor')==0
    assert source.frame_at(Fraction(3002,90000),policy='ceil')==1
    assert source.frame_at(Fraction(3003,180000),policy='nearest')==1
    assert source.frame_at(100, policy='floor')==3
    assert source.frame_for_output(1,'30000/1001')==1
    with pytest.raises(ValueError, match='outside'):
        source.frame_at(Fraction(10),boundary='error')
    with pytest.raises(ValueError, match='outside'):
        source.frame_at(-1,boundary='error')
    assert source.document(include_frames=True)['frames'][1]['seconds']=='1001/30000'
    assert source.document()['vfr'] is True


@pytest.mark.parametrize('invalid', [1.2, True, None, [1,0], [1,2,3], '', 'inf', '1/0'])
def test_reject_inexact_or_malformed_time(invalid):
    with pytest.raises(ValueError):
        _fraction(invalid, 'test')


@pytest.mark.parametrize('rate', ['0', '-2', '121', '1/10000', 1.5, True])
def test_reject_invalid_output_rates(rate):
    with pytest.raises(ValueError):
        _rate(rate)


def test_probe_rejects_duplicate_pts_and_invalid_dimensions(monkeypatch, tmp_path):
    class Result:
        stdout = json.dumps({'streams':[{'index':0,'width':64,'height':48,
                                         'time_base':'1/90000','avg_frame_rate':'30/1'}],
                             'frames':[{'best_effort_timestamp':0},
                                       {'best_effort_timestamp':0}]}).encode()
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: Result())
    with pytest.raises(ValueError, match='timestamps'):
        _probe(tmp_path / 'x', 'ffprobe')


def test_manifest_is_strict_and_pins_every_image(tmp_path):
    manifest = make_manifest(tmp_path)
    output = tmp_path / 'video.mp4'
    jsondoc=json.loads(manifest.read_text())
    jsondoc['frames'][0]['sha256']='f'*64
    manifest.write_text(json.dumps(jsondoc))
    with pytest.raises(ValueError, match='SHA-256'):
        encode_manifest(manifest,output)
    manifest.write_text('{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError, match='Duplicate'):
        encode_manifest(manifest,output)
    assert not output.exists()


def test_invalid_decode_indices_are_checked_before_calling_decoder():
    source = fake_input()
    with pytest.raises(ValueError,match='index'):
        source.read(999)
    with pytest.raises(ValueError,match='index'):
        source.read(True)


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
                    reason='FFmpeg/ffprobe unavailable')
def test_ffmpeg_exact_ntsc_output_and_nonsequential_decode(tmp_path):
    manifest=make_manifest(tmp_path)
    output=tmp_path / 'rational.mp4'
    receipt=encode_manifest(manifest,output,preset='ultrafast',crf=18)
    assert output.exists()
    assert output.with_name(output.name+'.receipt.json').exists()
    assert receipt['verified']['fps']=='30000/1001'
    assert receipt['verified']['full_decode']=='passed'
    index=VideoInput.open(output,sha256=sha(output))
    assert index.frame_count==6
    assert index.nominal_rate==Fraction(30000,1001)
    assert index.is_vfr is False
    assert index.timestamps==tuple(Fraction(i*1001,30000) for i in range(6))
    assert index.frame_for_output(4,'30000/1001')==4
    for n in (5,1,4,0,2,3):
        pixel=index.read(n).getpixel((30,20))
        assert abs(pixel[0]-n*35)<=5
    exported=tmp_path / 'selected.png'
    record=extract_png(output,exported,sha256=sha(output),time='1001/30000')
    assert record['frame']==1
    assert Image.open(exported).mode=='RGB'
    with pytest.raises(ValueError,match='already exists'):
        extract_png(output,exported,sha256=sha(output),index=1)
    with pytest.raises(ValueError,match='already exists'):
        encode_manifest(manifest,output)
    with pytest.raises(ValueError,match='SHA-256'):
        VideoInput.open(output,sha256='0'*64)


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
                    reason='FFmpeg/ffprobe unavailable')
def test_decode_real_noninteger_output_fps_without_frame_drops(tmp_path):
    manifest=make_manifest(tmp_path,fps='24000/1001',count=7)
    output=tmp_path/'video.mp4'
    encode_manifest(manifest,output,preset='ultrafast')
    index=VideoInput.open(output,sha256=sha(output))
    assert index.frame_count==7
    assert index.timestamps[6]==Fraction(6006,24000)


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
                    reason='FFmpeg/ffprobe unavailable')
def test_invalid_image_rejected_and_temporary_output_discarded(tmp_path):
    manifest=make_manifest(tmp_path,count=2)
    original=json.loads(manifest.read_text())
    first=tmp_path/original['frames'][0]['path']
    Image.new('RGBA',(64,48),(0,0,0,200)).save(first)
    original['frames'][0]['sha256']=sha(first)
    manifest.write_text(json.dumps(original))
    output=tmp_path/'rejected.mp4'
    with pytest.raises(ValueError,match='RGB8'):
        encode_manifest(manifest,output)
    assert not output.exists()
    assert not list(tmp_path.glob('.fr-video-*'))


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')),
                    reason='FFmpeg/ffprobe unavailable')
def test_real_vfr_presentation_timestamps_and_bounded_out_of_order_read(tmp_path):
    paths = []
    for n in range(4):
        path = tmp_path / f"vfr-{n}.png"
        Image.new("RGB", (64, 48), (n * 55, n * 19, n * 8)).save(path)
        paths.append(path)
    concat = tmp_path / 'vfr.ffconcat'
    concat.write_text('ffconcat version 1.0\n' + ''.join(
        f"file '{p.name}'\nduration {duration}\n"
        for p, duration in zip(paths[:-1], ('0.08', '0.16', '0.04'))
    ) + f"file '{paths[-1].name}'\n", encoding='utf-8')
    output = tmp_path / 'vfr.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'concat', '-safe', '0',
                    '-i', str(concat), '-fps_mode', 'vfr', '-c:v', 'ffv1', str(output)],
                   cwd=tmp_path, capture_output=True, check=True)
    video = VideoInput.open(output, sha256=sha(output))
    assert video.is_vfr is True
    assert video.timestamps == (Fraction(0), Fraction(2, 25), Fraction(6, 25), Fraction(7, 25))
    assert video.frame_at('3/25', policy='floor') == 1
    assert video.frame_at('3/25', policy='ceil') == 2
    assert video.frame_at('4/25', policy='nearest') == 2  # exact midpoint ties later
    for n in (3, 1, 0, 2):
        assert video.read(n).getpixel((5, 5)) == (n * 55, n * 19, n * 8)
