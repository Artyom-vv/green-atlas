"""Exercise a patched ezdxf module in this process only; installed files stay intact."""
import hashlib
import json
from pathlib import Path
import sys

from ezdxf.acis import entities, sab
from ezdxf.acis.const import Tags, ParsingError


def verify(patched: Path, payload_dir: Path) -> dict:
    original_method = sab.SabDataLoader.read_transform
    original_source = Path(sab.__file__).read_bytes()
    # Execute the patched upstream module with its real package identity. This
    # changes only this disposable process and tests the exact retained source.
    exec(compile(patched.read_text(encoding='utf8'), str(patched), 'exec'), sab.__dict__)
    literal = '2 0 0 0 3 0 0 0 -1 10 20 30 1 rotate reflect shear'
    tokens = [sab.Token(Tags.LITERAL_STR, literal)]
    old_loader = sab.SabDataLoader(tokens, 22300)
    expected = original_method(old_loader)
    assert sab.SabDataLoader(tokens, 22300).read_transform() == expected
    vector_tokens = [sab.Token(Tags.DIRECTION_VEC, tuple(expected[n:n+3])) for n in range(0, 12, 3)]
    assert sab.SabDataLoader(vector_tokens, 22300).read_transform() == expected
    for bad in [vector_tokens[:2], vector_tokens[:2] + [sab.Token(Tags.STR, 'bad')]]:
        try:
            sab.SabDataLoader(bad, 22300).read_transform()
        except (ParsingError, IndexError):
            pass
        else:
            raise AssertionError('invalid vector matrix accepted')
    results = []
    for path in sorted(payload_dir.glob('*.sab')):
        data = path.read_bytes()
        raw = sab.parse_sab(data)
        bodies = entities.load(data)
        assert len(bodies) == 1
        matrix = list(map(float, bodies[0].transform.matrix))
        vector = next(e for e in raw.entities if e.name == 'transform')
        assert matrix[12:15] == list(vector.data[3].value)
        assert len(bodies[0].lumps()) == 1
        results.append(dict(handle=path.stem, bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest(), matrix=matrix,
                            raw_entities=len(raw.entities),
                            unsupported_types=sorted({e.name for e in raw.entities if e.name not in entities.ENTITY_TYPES})))
    assert results
    assert original_source == Path(sab.__file__).read_bytes()
    return dict(status='passed', literal_matrix_preserved=True, typed_matrix_passed=True,
                malformed_matrices_rejected=2, source_files_unchanged=True,
                bodies=results, curved_face_extraction='not implemented or claimed')


if __name__ == '__main__':
    result = verify(Path(sys.argv[1]), Path(sys.argv[2]))
    Path(sys.argv[3]).write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result))
