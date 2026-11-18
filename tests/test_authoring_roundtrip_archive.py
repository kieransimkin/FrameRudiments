"""Adversarial ZIP admission for the explicit M10.5 native qualification route."""
from hashlib import sha256
import importlib.util
import io
from pathlib import Path
import stat
import struct
import warnings
import zipfile

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'integration/qualify_authoring_roundtrip_m10_5.py'
SPEC = importlib.util.spec_from_file_location('authoring_roundtrip_qualification', SOURCE)
QUALIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALIFIER)


def archive(extra=(), *, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(stream, 'w', compression=compression) as bundle:
            bundle.writestr('AUTHORING-README.txt', 'Synthetic export\n')
            bundle.writestr('project.json', '{}\n')
            for name, content in extra:
                bundle.writestr(name, content)
    return stream.getvalue()


@pytest.mark.parametrize('name', ['authoring-lines-2.lrc', 'authoring-lines-10.lrc', 'authoring-lines-101.lrc'])
def test_extract_preserves_existing_relative_pins_and_checks_every_hash(tmp_path, name):
    source = tmp_path / 'audio'
    source.mkdir()
    pinned = source / 'source.wav'
    pinned.write_bytes(b'unchanged canonical source')
    contents = b'[00:00.250]Synthetic\n'
    data = archive([(name, contents)])
    receipt = QUALIFIER.safe_extract_archive(data, tmp_path,
        expected_names=['AUTHORING-README.txt', 'project.json', name],
        protected=['audio/source.wav'])
    assert pinned.read_bytes() == b'unchanged canonical source'
    assert (tmp_path / name).read_bytes() == contents
    assert receipt['archive_sha256'] == sha256(data).hexdigest()
    assert {row['name']: row['sha256'] for row in receipt['entries']} == {
        name: sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in ('AUTHORING-README.txt', 'project.json', name)}


@pytest.mark.parametrize('name', [
    '../outside.lrc', '/absolute.lrc', 'C:/drive.lrc', 'nested/sidecar.lrc',
    'nested\\sidecar.lrc', 'authoring-lines.lrc/child', 'unexpected.txt',
])
def test_unsafe_entry_names_fail_before_any_write(tmp_path, name):
    # ZipInfo normalizes native separators, including backslashes on Windows.
    # Use its stored name in the manifest so this fixture reaches filename
    # admission on every platform rather than an earlier manifest mismatch.
    entry = zipfile.ZipInfo(name)
    data = archive([(entry, b'hostile')])
    with pytest.raises(ValueError, match='archive_unsafe_entry'):
        QUALIFIER.safe_extract_archive(data, tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json', entry.filename])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('name', ['project.json', 'PROJECT.JSON'])
def test_duplicate_names_and_case_aliases_are_rejected(tmp_path, name):
    data = archive([(name, b'changed')])
    with pytest.raises(ValueError, match='archive_duplicate_entry'):
        QUALIFIER.safe_extract_archive(data, tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json', name])
    assert not list(tmp_path.iterdir())


def test_symbolic_link_entry_is_rejected_without_creating_targets(tmp_path):
    entry = zipfile.ZipInfo('authoring-lines.lrc')
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    data = archive([(entry, b'../outside.lrc')])
    with pytest.raises(ValueError, match='archive_unsafe_entry'):
        QUALIFIER.safe_extract_archive(data, tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json', entry.filename])
    assert not list(tmp_path.iterdir())


def test_compressed_entries_are_not_admitted_by_store_only_contract(tmp_path):
    with pytest.raises(ValueError, match='archive_unsafe_entry'):
        QUALIFIER.safe_extract_archive(archive(compression=zipfile.ZIP_DEFLATED), tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json'])
    assert not list(tmp_path.iterdir())


def test_crc_failure_after_valid_first_entry_still_writes_nothing(tmp_path):
    data = bytearray(archive([('authoring-lines.lrc', b'corrupt-this-unique-content')]))
    offset = data.index(b'corrupt-this-unique-content')
    data[offset] ^= 1
    with pytest.raises(zipfile.BadZipFile, match='CRC'):
        QUALIFIER.safe_extract_archive(bytes(data), tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json', 'authoring-lines.lrc'])
    assert not list(tmp_path.iterdir())


def test_oversized_advertised_entry_is_rejected_before_reading_or_writing(tmp_path):
    data = bytearray(archive())
    offset = data.index(b'PK\x01\x02')
    struct.pack_into('<I', data, offset + 24, QUALIFIER.MAX_ENTRY_BYTES + 1)
    with pytest.raises(ValueError, match='archive_unsafe_entry'):
        QUALIFIER.safe_extract_archive(bytes(data), tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json'])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('mode', ['protected', 'existing'])
def test_source_collisions_are_rejected_before_any_write(tmp_path, mode):
    name = 'authoring-lines.lrc'
    if mode == 'existing':
        (tmp_path / name).write_bytes(b'original bytes')
    data = archive([(name, b'exported bytes')])
    with pytest.raises(ValueError, match='archive_would_overwrite_input'):
        QUALIFIER.safe_extract_archive(data, tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json', name],
            protected=[name.upper()] if mode == 'protected' else [])
    assert not (tmp_path / 'project.json').exists()
    if mode == 'existing':
        assert (tmp_path / name).read_bytes() == b'original bytes'


def test_archive_byte_and_entry_counts_are_bounded(tmp_path, monkeypatch):
    data = archive()
    monkeypatch.setattr(QUALIFIER, 'MAX_ARCHIVE_BYTES', len(data) - 1)
    with pytest.raises(ValueError, match='archive_size_limit'):
        QUALIFIER.safe_extract_archive(data, tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json'])
    monkeypatch.setattr(QUALIFIER, 'MAX_ARCHIVE_BYTES', 10000)
    extras = [(f'authoring-lines-{number}.lrc', b'') for number in range(2, 6)]
    with pytest.raises(ValueError, match='archive_entry_count'):
        QUALIFIER.safe_extract_archive(archive(extras), tmp_path,
            expected_names=['AUTHORING-README.txt', 'project.json', *[name for name, _ in extras]])
    assert not list(tmp_path.iterdir())
