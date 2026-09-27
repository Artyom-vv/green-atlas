"""Check the UTF-8 chunk patch against every ASCII DXF record.

Only group 1/3 segmentation is normalized; concatenated bytes and all other
ordered code/value pairs must match. This is a patch regression check, not a
general CAD semantic equivalence checker.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


def records(path):
    fingerprints = Counter()
    pairs, text_parts = [], []
    invalid_lines = 0

    def record():
        if pairs:
            fingerprints[hashlib.sha256(repr((pairs, b''.join(text_parts))).encode()).hexdigest()] += 1

    with path.open('rb') as file:
        while code := file.readline():
            value = file.readline().rstrip(b'\r\n')
            number = int(code)
            try:
                value.decode('utf8')
            except UnicodeDecodeError:
                invalid_lines += 1
            if number == 0:
                record()
                pairs, text_parts = [], []
            if number in (1, 3):
                text_parts.append(value)
            else:
                pairs.append((number, value))
        record()
    return fingerprints, invalid_lines


if __name__ == '__main__':
    before, invalid_before = records(Path(sys.argv[1]))
    after, invalid_after = records(Path(sys.argv[2]))
    result = dict(before_records=before.total(), after_records=after.total(),
                  text_content_and_other_tags_preserved=before == after,
                  invalid_utf8_values_before=invalid_before,
                  invalid_utf8_values_after=invalid_after,
                  normalized_removed=(before-after).total(), normalized_added=(after-before).total())
    Path(sys.argv[3]).write_text(json.dumps(result, indent=2)+'\n', encoding='utf8')
    print(json.dumps(result))
    sys.exit(0 if before == after and invalid_after == 0 else 1)
