"""Safety checks for the narrowly authorized historical processed-input extraction."""
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest


SCRIPT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/scripts/prepare_original_airway.py')
spec = importlib.util.spec_from_file_location('prepare_original_airway', SCRIPT)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def archive(path, members):
    with tarfile.open(path, 'w:gz') as handle:
        for name, payload, kind in members:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(payload)
            handle.addfile(member, io.BytesIO(payload) if kind == tarfile.REGTYPE else None)


def test_extracts_only_allowlisted_regular_members(tmp_path):
    source = tmp_path / 'input.tar.gz'
    archive(source, [('airway/DESCRIPTION', b'Version: 0.102.0', tarfile.REGTYPE),
                     ('airway/data/airway.RData', b'processed', tarfile.REGTYPE),
                     ('airway/inst/extdata/example.bam', b'not extracted', tarfile.REGTYPE),
                     ('airway/R/install.R', b'not executed', tarfile.REGTYPE)])
    destination = tmp_path / 'extracted'
    result = subject.extract_processed(source, destination)
    assert {p.name for p in destination.rglob('*') if p.is_file()} == {'DESCRIPTION', 'airway.RData'}
    assert len(result) == 4
    assert next(x for x in result if x['name'].endswith('.bam'))['extracted'] is False


@pytest.mark.parametrize('name,kind,size', [('../outside', tarfile.REGTYPE, 1),
    ('/airway/data/airway.RData', tarfile.REGTYPE, 1),
    ('airway/../outside', tarfile.REGTYPE, 1),
    ('airway/data/airway.RData', tarfile.SYMTYPE, 0),
    ('airway/data/airway.RData', tarfile.LNKTYPE, 0),
    ('airway/data/airway.RData', tarfile.REGTYPE, 100_000_001)])
def test_rejects_unsafe_archive_before_writing(tmp_path, name, kind, size):
    member = tarfile.TarInfo(name)
    member.type, member.size = kind, size
    with pytest.raises(ValueError):
        subject.validate_members([member])
    assert list(tmp_path.iterdir()) == []


def test_rejects_duplicate_member_and_missing_processed_object():
    member = tarfile.TarInfo('airway/data/airway.RData')
    with pytest.raises(ValueError, match='duplicate'):
        subject.validate_members([member, member])
    with pytest.raises(ValueError, match='required'):
        subject.validate_members([tarfile.TarInfo('airway/DESCRIPTION')])


@pytest.mark.parametrize('name', ['airway//data/airway.RData', 'airway/./data/airway.RData'])
def test_rejects_noncanonical_required_name(name):
    with pytest.raises(ValueError, match='path'):
        subject.validate_members([tarfile.TarInfo('airway/DESCRIPTION'),tarfile.TarInfo(name)])


def test_rejects_required_object_as_trailing_slash_directory():
    member = tarfile.TarInfo('airway/data/airway.RData/')
    member.type = tarfile.DIRTYPE
    with pytest.raises(ValueError, match='required'):
        subject.validate_members([tarfile.TarInfo('airway/DESCRIPTION'), member])


def test_refuses_existing_destination(tmp_path):
    source = tmp_path / 'input.tar.gz'
    archive(source, [('airway/DESCRIPTION', b'metadata', tarfile.REGTYPE),
                     ('airway/data/airway.RData', b'processed', tarfile.REGTYPE)])
    with pytest.raises(FileExistsError):
        subject.extract_processed(source, tmp_path)


def test_bounds_compressed_size_and_decompression(tmp_path, monkeypatch):
    source = tmp_path / 'input.tar.gz'
    archive(source, [('airway/DESCRIPTION', b'metadata', tarfile.REGTYPE),
                     ('airway/data/airway.RData', b'processed', tarfile.REGTYPE)])
    monkeypatch.setattr(subject, 'MAX_ARCHIVE_BYTES', 1)
    with pytest.raises(ValueError, match='compressed'):
        subject.extract_processed(source, tmp_path / 'output')
    monkeypatch.setattr(subject, 'MAX_ARCHIVE_BYTES', 100_000)
    monkeypatch.setattr(subject, 'MAX_UNCOMPRESSED_BYTES', 512)
    with pytest.raises(ValueError, match='uncompressed'):
        subject.extract_processed(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_rejects_oversized_total_or_many_members(monkeypatch):
    members = [tarfile.TarInfo(name) for name in subject.REQUIRED]
    for member in members:
        member.size = 8
    monkeypatch.setattr(subject, 'MAX_UNCOMPRESSED_BYTES', 15)
    with pytest.raises(ValueError, match='total'):
        subject.validate_members(members)
    with pytest.raises(ValueError, match='too many'):
        subject.validate_members([tarfile.TarInfo(f'airway/entry{i}') for i in range(513)])
