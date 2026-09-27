"""Isolated lossless transport experiment. NOT a product importer or CAD parser.

The source bytes and geometry are never edited. Each measured stage runs in a
fresh process; temporary compressed files are removed after verification.
Only the explicitly requested report persists.
"""

import argparse
import gzip
import hashlib
import json
import resource
import subprocess
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import ijson

CHUNK_BYTES = 1024 * 1024
PARSE_BUFFER_BYTES = 64 * 1024
STAGE_TIMEOUT_SECONDS = 180
GZIP_LEVELS = (1, 6)
RECORD_ARRAYS = frozenset({
    'coverage', 'regions', 'paths', 'points', 'area_proposals',
    'area_proposal_rejections', 'xref_dependencies',
})


class IntegrityError(ValueError):
    pass


class CheckedReader:
    """Bound decoded bytes before parsing; do not buffer the entire file."""

    def __init__(self, stream, expected_bytes, expected_sha256, cancelled=None):
        self.stream = stream
        self.expected_bytes = expected_bytes
        self.expected_sha256 = expected_sha256
        self.cancelled = cancelled or (lambda: False)
        self.count = 0
        self.digest = hashlib.sha256()

    def read(self, size):
        if size < 0:
            raise IntegrityError('Unbounded read is forbidden in this experiment')
        if self.cancelled():
            raise InterruptedError('Cancelled')
        # At most one byte beyond the admitted decoded size is needed to reject.
        size = min(size, CHUNK_BYTES, self.expected_bytes - self.count + 1)
        data = self.stream.read(size)
        self.count += len(data)
        if self.count > self.expected_bytes:
            raise IntegrityError('Decoded byte limit exceeded')
        self.digest.update(data)
        return data

    def finish(self):
        if self.count != self.expected_bytes:
            raise IntegrityError('Decoded size differs')
        if self.digest.hexdigest() != self.expected_sha256:
            raise IntegrityError('Source SHA256 differs')


def verify_stream(stream, expected_bytes, expected_sha256, cancelled=None):
    checked = CheckedReader(stream, expected_bytes, expected_sha256, cancelled)
    while checked.read(CHUNK_BYTES):
        pass
    checked.finish()
    return checked.count


def statistics(stream, expected_bytes, expected_sha256):
    checked = CheckedReader(stream, expected_bytes, expected_sha256)
    counts, points, statuses = Counter(), Counter(), Counter()
    summary = {}
    for prefix, event, value in ijson.parse(
        checked, use_float=True, buf_size=PARSE_BUFFER_BYTES,
    ):
        if event == 'start_map' and prefix.endswith('.item'):
            section = prefix[:-5]
            if section in RECORD_ARRAYS:
                counts[section] += 1
        elif event == 'start_array':
            if prefix == 'paths.item.coordinates.item':
                points['paths'] += 1
            elif prefix == 'regions.item.loops.item.coordinates.item':
                points['regions'] += 1
        elif prefix == 'coverage.item.status' and event == 'string':
            statuses[value] += 1
        elif prefix.startswith('summary.') and event == 'number':
            summary[prefix.split('.')[1]] = value
    checked.finish()
    for field in ('regions', 'paths', 'points', 'area_proposals'):
        if summary.get(field) != counts[field]:
            raise IntegrityError(f'{field} count differs from summary')
    if summary.get('source_instances') != counts['coverage']:
        raise IntegrityError('Coverage count differs')
    if summary.get('region_sampled_points') != points['regions']:
        raise IntegrityError('Region point count differs')
    return {'records': counts, 'coordinate_triples': points, 'statuses': statuses}


def pack(source, destination, level, expected_bytes, expected_sha256):
    # Exclusive creation, no source/output overwrite. Plain input is read-only.
    with source.open('rb') as raw, destination.open('xb') as packed:
        checked = CheckedReader(raw, expected_bytes, expected_sha256)
        with gzip.GzipFile(filename='', mode='wb', fileobj=packed,
                           compresslevel=level, mtime=0) as zipped:
            while data := checked.read(CHUNK_BYTES):
                zipped.write(data)
            checked.finish()
    return destination.stat().st_size


def peak_rss_bytes():
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maximum if sys.platform == 'darwin' else maximum * 1024


def run_stage(args):
    started = perf_counter()
    result = {}
    if args.stage == 'pack':
        result['packed_bytes'] = pack(args.source, args.destination, args.level,
                                      args.expected_bytes, args.expected_sha256)
    else:
        opener = gzip.open if args.compressed else open
        with opener(args.source, 'rb') as stream:
            if args.stage == 'verify':
                result['verified_bytes'] = verify_stream(
                    stream, args.expected_bytes, args.expected_sha256,
                )
            else:
                result['statistics'] = statistics(
                    stream, args.expected_bytes, args.expected_sha256,
                )
    result.update(seconds=round(perf_counter() - started, 3),
                  peak_rss_bytes=peak_rss_bytes())
    return result


def child(args, stage, source, destination=None, level=None, compressed=False):
    command = [sys.executable, __file__, str(source), '--stage', stage,
               '--expected-bytes', str(args.expected_bytes),
               '--expected-sha256', args.expected_sha256]
    if destination is not None:
        command += ['--destination', str(destination), '--level', str(level)]
    if compressed:
        command += ['--compressed']
    run = subprocess.run(command, capture_output=True, text=True, check=True,
                         timeout=STAGE_TIMEOUT_SECONDS)
    result = json.loads(run.stdout)
    print(json.dumps({'stage': stage, 'level': level, 'compressed': compressed,
                      **result}, ensure_ascii=False), flush=True)
    return result


def benchmark(args):
    if args.report is None:
        raise ValueError('--report is required')
    if args.report.exists():
        raise FileExistsError(args.report)
    report = {'scope': 'transport and streaming counters only; NOT full admission',
              'source': str(args.source), 'source_bytes': args.expected_bytes,
              'source_sha256': args.expected_sha256, 'gzip': {}}
    report['plain_verify'] = child(args, 'verify', args.source)
    report['plain_statistics'] = child(args, 'statistics', args.source)
    with TemporaryDirectory(prefix='ga-lossless-transport-', dir='/private/tmp') as scratch:
        for level in GZIP_LEVELS:
            packed = Path(scratch) / f'capture-level{level}.json.gz'
            entry = {'pack': child(args, 'pack', args.source, packed, level),
                     'verify': child(args, 'verify', packed, compressed=True),
                     'statistics': child(args, 'statistics', packed, compressed=True)}
            if entry['statistics']['statistics'] != report['plain_statistics']['statistics']:
                raise IntegrityError('Streaming counters differ')
            entry['statistics_match'] = True
            report['gzip'][str(level)] = entry
    # Receipt is generated only after every stage succeeds. Test archives have
    # been cleaned by their own exact TemporaryDirectory, never by a broad glob.
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x') as output:
        json.dump(report, output, indent=2, ensure_ascii=False)
        output.write('\n')
    return {'report': str(args.report), 'temporary_archives_removed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--expected-bytes', required=True, type=int)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--stage', choices=('pack', 'verify', 'statistics'))
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--level', type=int, choices=GZIP_LEVELS)
    parser.add_argument('--compressed', action='store_true')
    args = parser.parse_args()
    if args.expected_bytes <= 0 or len(args.expected_sha256) != 64:
        parser.error('Expected immutable source size and SHA256 are required')
    print(json.dumps(run_stage(args) if args.stage else benchmark(args)))


if __name__ == '__main__':
    main()
