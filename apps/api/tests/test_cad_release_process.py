"""Exercise the real supervisor with a synthetic Core executable."""

import json
import os
import plistlib
import sys
from hashlib import sha256

import pytest

from app.exporting.cad_contracts import CadPlant
from app.exporting.cad_process import write_cad_release
from app.native_query.process import WORKER_BINARY, WORKER_ID
from app.native_query.process_contracts import (
    CadPackageFile,
    NativeInputPackage,
    NativeQueryProcessConfig,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX worker")

SCRIPT = """
import json, pathlib, hashlib, sys, time
root = pathlib.Path.cwd()
if MODE == 'timeout': time.sleep(10)
request = (root/'request.txt').read_bytes()
source = root/'package/host.dwg'
target = root/'result/planting-plan.dwg'
target.write_bytes(b'reopened-DWG')
receipt = dict(schema='green-atlas.cad-release/1', request_sha256=hashlib.sha256(request).hexdigest(),
 source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), result_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
 source_instances=10, result_instances=11, units_code=6, reopened=True,
 plantings=[dict(id='aa',kind='shrub',x=1,y=2,radius=.5,handle='AB')])
if MODE == 'wrong-coordinate': receipt['plantings'][0]['x'] = 99
if MODE == 'changed-xref': (root/'result/xref-1.dwg').write_bytes(b'changed')
if MODE == 'changed-staged-source': source.write_bytes(b'changed')
if MODE == 'changed-request': (root/'request.txt').write_bytes(b'changed')
if MODE != 'missing-receipt': (root/'release.json').write_text(json.dumps(receipt))
if MODE == 'unfinished': (root/'release.json.tmp').write_text('{}')
sys.exit(254 if MODE == 'exit-254' else 0)
"""


def setup(tmp_path, mode):
    root = tmp_path / "source"
    root.mkdir()
    files = []
    for name in ("host.dwg", "xref-1.dwg"):
        data = name.encode()
        (root / name).write_bytes(data)
        files.append(CadPackageFile(name, sha256(data).hexdigest(), len(data)))
    bundle = tmp_path / "worker.bundle"
    binary = bundle / WORKER_BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"trusted test worker")
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": WORKER_ID,
                "CFBundleExecutable": "GreenAtlasBridge",
                "CFBundleShortVersionString": "0.1.42",
            }
        )
    )
    core = tmp_path / "core"
    core.write_text(f"#!{sys.executable}\nMODE={mode!r}\n" + SCRIPT)
    core.chmod(0o700)
    template = tmp_path / "bootstrap.dwt"
    template.write_bytes(b"empty")
    config = NativeQueryProcessConfig(
        core,
        bundle,
        tmp_path / "jobs",
        sha256(binary.read_bytes()).hexdigest(),
        "0.1.42",
        template,
        timeout_seconds=0.5 if mode == "timeout" else 5,
        terminate_grace_seconds=0.1,
        poll_seconds=0.01,
        min_free_bytes=1,
    )
    return NativeInputPackage(root, "host.dwg", tuple(files)), config


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "wrong-coordinate",
        "changed-xref",
        "changed-staged-source",
        "changed-request",
        "missing-receipt",
        "unfinished",
        "exit-254",
        "timeout",
    ],
)
def test_cad_publication_requires_clean_exit_integrity_and_complete_receipt(
    tmp_path, mode
):
    package, config = setup(tmp_path, mode)
    plants = (CadPlant(id="aa", kind="shrub", x=1, y=2, radius=0.5),)
    if mode == "ok":
        result = write_cad_release(package, plants, 6, config)
        assert result.files["planting-plan.dwg"] == b"reopened-DWG"
        assert json.loads(result.files["cad-receipt.json"])["reopened"]
    else:
        with pytest.raises((ValueError, OSError)):
            write_cad_release(package, plants, 6, config)
    assert (package.root / "host.dwg").read_bytes() == b"host.dwg"
