"""Compare every ASCII DXF record, retaining only hashes and identities in RAM."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


def records(path):
    result = Counter()
    identities = {}
    digest = None
    kind = handle = ''
    with Path(path).open('rb') as stream:
        while True:
            code = stream.readline()
            if not code:
                break
            value = stream.readline().rstrip(b'\r\n')
            code = int(code.strip())
            if code == 0:
                if digest:
                    key = digest.hexdigest()
                    result[key] += 1
                    identities[key] = (kind, handle)
                digest = hashlib.sha256()
                kind, handle = value.decode('ascii', errors='replace'), ''
            if digest:
                digest.update(str(code).encode() + b'\n' + value + b'\n')
            if code in (5, 105):
                handle = value.decode('ascii', errors='replace')
        if digest:
            key = digest.hexdigest()
            result[key] += 1
            identities[key] = (kind, handle)
    return result, identities


if __name__ == '__main__':
    before, names_before = records(sys.argv[1])
    after, names_after = records(sys.argv[2])
    removed, added = before-after, after-before
    report = dict(before_records=before.total(), after_records=after.total(),
        removed=[dict(type=names_before[k][0], handle=names_before[k][1], count=v) for k,v in removed.items()],
        added=[dict(type=names_after[k][0], handle=names_after[k][1], count=v) for k,v in added.items()],
        method='SHA256 of every ordered code/value pair per group-0 record, multiplicities retained')
    report['only_materials_added'] = not removed and bool(added) and all(names_after[k][0]=='MATERIAL' for k in added)
    Path(sys.argv[3]).write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report))
