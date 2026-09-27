"""Repath copies through AutoCAD, then verify in a separately relocated directory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import uuid
import zipfile

from capture_session_run import OUTPUT, build, core, digest, dump


def main() -> int:
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--main',required=True)
    args=parser.parse_args()
    root=OUTPUT/('desktop-kit-'+uuid.uuid4().hex[:10])
    root.mkdir()
    print(str(root),flush=True)
    if shutil.disk_usage(root).free < 2_000_000_000:
        raise RuntimeError('Insufficient free disk')
    archive=args.archive.resolve(strict=True)
    source_hash=digest(archive)
    (root/'input').mkdir()
    inputs=[]
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith('.dwg'):
                continue
            name=Path(info.filename).name
            target=root/'input'/name
            with z.open(info) as original,target.open('xb') as copy:
                shutil.copyfileobj(original,copy)
            inputs.append({'archive_entry':info.filename,'name':name,'sha256':digest(target),'bytes':target.stat().st_size})
    if not (root/'input'/args.main).is_file() or Path(args.main).name!=args.main:
        raise ValueError('Main drawing must be an exact archive filename')
    bundle=build(root,Path(__file__).with_name('flat_package_probe.cpp'))
    receipt={'archive':str(archive),'archive_sha256':source_hash,'entry':args.main,'input_files':inputs,
             'binary_sha256':digest(bundle/'Contents/MacOS/CaptureSession'),'engines':{}}
    try:
        receipt['engines']['prepare']=core(root,bundle,'01-prepare','flat-prepare',timeout_seconds=90)
        # Verify relocation, not accidental resolution via the preparation path.
        relocated=root/'desktop-kit-relocated'
        relocated.mkdir()
        shutil.copytree(root/'package',relocated/'package')
        receipt['engines']['verify']=core(relocated,bundle,'02-verify','flat-verify',variant=args.main,timeout_seconds=90)
        report=json.loads((relocated/'verified-xrefs.json').read_bytes())
        package=(relocated/'package').resolve()
        refs=report['nodes'][1:]
        loaded=[n for n in refs if n['status']==1 and n['database_present']]
        receipt['loaded_xrefs']=[{'name':n['name'],'resolved':n['resolved_path'],'sha256':n['resolved_disk_sha256']} for n in loaded]
        receipt['other_xrefs']=[{'name':n['name'],'status':n['status'],'authored_path':n['authored_path']} for n in refs if n not in loaded]
        receipt['all_loaded_paths_inside_relocated_package']=all(Path(n['resolved_path']).resolve().parent==package for n in loaded)
        receipt['all_input_xrefs_loaded']=set(n['name'] for n in loaded)=={Path(f['name']).stem for f in inputs if f['name']!=args.main}
        receipt['verified_model_instances']=len(json.loads((relocated/'verified-model.json').read_bytes())['entities'])
        receipt['input_bytes_unchanged']=all(digest(root/'input'/f['name'])==f['sha256'] for f in inputs)
        receipt['archive_unchanged']=digest(archive)==source_hash
        receipt['output_files']=[{'name':p.name,'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted((root/'package').glob('*.dwg'))]
        receipt['relocated_bytes_match']=all(digest(package/f['name'])==f['sha256'] for f in receipt['output_files'])
        receipt['passed']=all(receipt[k] for k in ('all_loaded_paths_inside_relocated_package','all_input_xrefs_loaded','input_bytes_unchanged','archive_unchanged','relocated_bytes_match')) and all(e['exit_code']==0 and not e['owned_process_terminated'] for e in receipt['engines'].values())
    except (OSError,ValueError,RuntimeError) as error:
        receipt['error']=str(error)
        receipt['passed']=False
    dump(root/'receipt.json',receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('input_files','output_files')},ensure_ascii=False,indent=2),flush=True)
    return int(not receipt['passed'])


if __name__=='__main__':
    raise SystemExit(main())
