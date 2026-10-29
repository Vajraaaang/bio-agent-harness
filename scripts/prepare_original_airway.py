"""Extract only the explicitly authorized airway 0.102.0 processed input.

No package code is installed or executed, and raw-read members are never extracted.
Acquisition and inspection do not authorize a scientific workflow.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


ROOT = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness')
REPORT = ROOT / 'reports/rna-airway-original-preparation/2026-10-21-v1'
ARCHIVE = REPORT / 'airway_0.102.0.tar.gz'
EXPECTED_MD5 = '8133c8de64ff701d1a2a9f2afcab60b6'
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 100_000_000
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
ALLOW = frozenset({'airway/DESCRIPTION', 'airway/data/airway.RData',
                   'airway/man/airway.Rd', 'airway/inst/extdata/sample_table.csv'})
REQUIRED = frozenset({'airway/DESCRIPTION', 'airway/data/airway.RData'})


class BoundedReader:
    def __init__(self, source):
        self.source = source
        self.read_bytes = 0

    def read(self, size=-1):
        if size < 0:
            raise ValueError('unbounded archive read')
        data = self.source.read(min(size, MAX_UNCOMPRESSED_BYTES - self.read_bytes + 1))
        self.read_bytes += len(data)
        if self.read_bytes > MAX_UNCOMPRESSED_BYTES:
            raise ValueError('uncompressed archive exceeds limit')
        return data


def validate_members(members):
    seen, total = set(), 0
    if len(members) > 512:
        raise ValueError('too many archive members')
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or '..' in path.parts or not path.parts
                or path.parts[0] != 'airway' or '\\' in member.name
                or str(path) != member.name.rstrip('/')
                or (not member.isdir() and member.name.endswith('/'))):
            raise ValueError('unsafe member path')
        if str(path) in seen:
            raise ValueError('duplicate archive member')
        seen.add(str(path))
        if not (member.isdir() or member.isfile()) or member.issparse():
            raise ValueError('non-regular archive member')
        if member.size < 0 or member.size > MAX_MEMBER_BYTES:
            raise ValueError('member exceeds size limit')
        if str(path) in REQUIRED and not member.isfile():
            raise ValueError('required member is not a regular file')
        total += member.size
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ValueError('archive exceeds total size limit')
    if not REQUIRED.issubset(seen):
        raise ValueError('required processed input missing')


def stream_archive(archive):
    with gzip.open(archive, 'rb') as compressed:
        with tarfile.open(fileobj=BoundedReader(compressed), mode='r|') as handle:
            yield handle


def extract_processed(archive: Path, destination: Path):
    if not archive.is_absolute() or not destination.is_absolute():
        raise ValueError('absolute filesystem paths required')
    if destination.exists():
        raise FileExistsError(destination)
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('compressed archive exceeds limit')
    for handle in stream_archive(archive):
        members = []
        for member in handle:
            members.append(member)
            if len(members) > 512:
                raise ValueError('too many archive members')
        validate_members(members)
    manifest, selected = [], {}
    for handle in stream_archive(archive):
        for member in handle:
            row = {'name': member.name, 'size_bytes': member.size,
                   'type': 'directory' if member.isdir() else 'regular', 'extracted': False}
            if member.name in ALLOW and member.isfile():
                payload = handle.extractfile(member).read(MAX_MEMBER_BYTES + 1)
                if len(payload) != member.size:
                    raise ValueError('member payload size mismatch')
                row.update(extracted=True, sha256=hashlib.sha256(payload).hexdigest())
                selected[member.name] = payload
            manifest.append(row)
    destination.mkdir(parents=True, exist_ok=False)
    for name, payload in selected.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(payload)
        target.chmod(0o444)
    return manifest


def main():
    if ARCHIVE.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('compressed archive exceeds limit')
    payload = ARCHIVE.read_bytes()
    if len(payload) != 11763178 or hashlib.md5(payload).hexdigest() != EXPECTED_MD5:
        raise ValueError('official historical archive identity mismatch')
    manifest = extract_processed(ARCHIVE, REPORT / 'inputs')
    result = {'format': 'devbench.airway-original-acquisition/1',
              'url': 'https://ftp.gwdg.de/pub/misc/bioconductor/packages/3.1/data/experiment/src/contrib/airway_0.102.0.tar.gz',
              'archive_bytes': len(payload), 'archive_sha256': hashlib.sha256(payload).hexdigest(),
              'archive_md5': EXPECTED_MD5, 'members': manifest,
              'package_scripts_executed': False, 'bam_members_extracted': False,
              'biological_execution_authorized': False, 'live_generation_authorized': False}
    with (REPORT / 'acquisition.json').open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    ARCHIVE.chmod(0o444)


if __name__ == '__main__':
    main()
