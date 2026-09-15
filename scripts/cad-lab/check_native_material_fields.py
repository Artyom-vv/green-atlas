"""Evidence-only comparison of LibreDWG JSON fields against ASCII DXF tags.

This is a test mapping, never a production material serializer.
"""
import json
import math
from pathlib import Path
import sys


def native_materials(path):
    result = {}
    with Path(path).open(encoding='utf8', errors='replace') as stream:
        active = False
        for line in stream:
            if line == '    {\n':
                active, buf = True, [line]
            elif active:
                buf.append(line)
                if line in ('    },\n', '    }\n'):
                    obj = json.loads(''.join(buf).rstrip().rstrip(','))
                    if obj.get('object') == 'MATERIAL':
                        result[format(obj['handle'][-1], 'X')] = obj
                        if len(result) == 3:
                            return result
                    active = False
    return result


def dxf_materials(path):
    result = {}
    current = None
    handle = None
    with Path(path).open(encoding='utf8', errors='replace') as stream:
        while True:
            line = stream.readline()
            if not line:
                break
            code, value = int(line.strip()), stream.readline().rstrip('\r\n')
            if code == 0:
                if current is not None:
                    result[handle] = current
                current = {} if value == 'MATERIAL' else None
                handle = None
            if current is not None:
                current.setdefault(code, []).append(value)
                if code == 5:
                    handle = value
    return result


if __name__ == '__main__':
    native, dxf = native_materials(sys.argv[1]), dxf_materials(sys.argv[2])
    fields = {'name': 1, 'description': 2, 'specular_gloss_factor': 44,
        'opacity_percent': 140, 'refraction_index': 145, 'translucence': 148,
        'self_illumination': 149, 'reflectivity': 468, 'illumination_model': 93,
        'channel_flags': 94, 'mode': 282}
    for prefix, codes in {'ambient_color':(70,40), 'diffuse_color':(71,41), 'specular_color':(76,45)}.items():
        fields.update({prefix+'.flag':codes[0], prefix+'.factor':codes[1]})
    for prefix, codes in {'diffusemap':(42,72,73,74,75,43), 'specularmap':(46,77,78,79,170,47),
        'reflectionmap':(48,171,172,173,174,49), 'opacitymap':(141,175,176,177,178,142),
        'bumpmap':(143,179,270,271,272,144), 'refractionmap':(146,273,274,275,276,147)}.items():
        fields.update({prefix+'.'+key:code for key,code in zip(
            ('blendfactor','source','projection','tiling','autotransform','transmatrix'),codes)})
    checks, failures, unchecked = [], [], {}
    structural = {'object','index','type','handle','size','bitsize','is_xdic_missing','has_ds_data',
        'ownerhandle','reactors','_subclass'}
    for handle, obj in native.items():
        tags = dxf.get(handle,{})
        for key, code in (('ownerhandle',330), ('xdicobjhandle',360)):
            if key in obj:
                expected = format(obj[key][-1], 'X')
                same = expected in tags.get(code,[])
                checks.append({'handle':handle,'field':key,'same':same})
                if not same:
                    failures.append({'handle':handle,'field':key,'expected':expected,'actual':tags.get(code,[])})
        for key, code in fields.items():
            if key not in obj:
                continue
            value = obj[key]
            actual = tags.get(code, [])
            # Writer explicitly omits numeric zero values for these optional tags.
            if not actual and code in (148,149,468,93,94,282) and value == 0:
                actual = ['0']
            expected = value if isinstance(value,list) else [value]
            same = len(actual)==len(expected) and all(
                a==e if isinstance(e,str) else math.isclose(float(a),e,rel_tol=1e-11,abs_tol=1e-12)
                for a,e in zip(actual,expected))
            item = {'handle':handle,'field':key,'same':same}
            checks.append(item)
            if not same:
                failures.append({**item,'expected':expected,'actual':actual})
        unchecked[handle] = sorted(set(obj)-set(fields)-structural-{'xdicobjhandle'})
    report = {'materials':len(native),'checks':len(checks),'failures':failures,'unchecked_source_fields':unchecked,
        'scope':'three initial system materials decoded in source JSON; other/custom material coverage not inferred'}
    Path(sys.argv[3]).write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report))
