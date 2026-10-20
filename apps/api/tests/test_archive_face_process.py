"""Run owned subprocesses to exercise failure, cleanup and retry boundaries."""

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_native_query_process import setup_job

from app.native_query.archive.archive_face_process import prepare_archive_faces
from app.operations.progress import OperationCancelled, operation_cancellation

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX Core supervisor fixture"
)

REQUEST = b"checked fixture request\n"


def face_core(path: Path, mode: str) -> None:
    path.write_text(
        f"#!{sys.executable}\nMODE={mode!r}\n"
        + """
import hashlib,json,sys,time
from pathlib import Path
job=Path.cwd()
request=(job/'request.txt').read_bytes()
source=job/'package/entry.dwg'
reply={'session_id':'a'*32,'request_sha256':hashlib.sha256(request).hexdigest(),
       'layers':['SITE'],'face_policy':'native-planar-faces/1',
       'faces':[],'linear_routes':[],'face_issues':[]}
output=job/'native.json'
output.write_text(json.dumps(reply))
lifecycle={'schema':'green-atlas.native-query-lifecycle/1',
           'request_sha256':hashlib.sha256(request).hexdigest(),
           'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
           'reply_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
           'database_destroyed':True,'working_database_restored':True,
           'field_evaluation_restored':True}
if MODE=='numeric_cleanup': lifecycle['database_destroyed']=1
if MODE=='wrong_cleanup': lifecycle['reply_sha256']='f'*64
if MODE=='changed_request': (job/'request.txt').write_bytes(b'changed')
if MODE=='changed_input': source.write_bytes(b'changed')
if MODE=='temporary_reply': (job/'native.json.tmp').write_bytes(b'partial')
if MODE=='malformed': output.write_bytes(b'{broken')
if MODE!='missing_cleanup': (job/'lifecycle.json').write_text(json.dumps(lifecycle))
if MODE=='exit7': sys.exit(7)
if MODE=='hang': time.sleep(10)
"""
    )


def test_restart_uses_fresh_job_and_preserves_archive(tmp_path):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "hang")
    config = replace(config, timeout_seconds=0.2)
    before = {
        item.path: (package.root / item.path).read_bytes() for item in package.files
    }
    with pytest.raises(ValueError, match="не завершена"):
        prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    failed_job = next(config.job_root.iterdir())
    assert (failed_job / "core.log").exists()
    face_core(config.core_executable, "ok")
    reply = prepare_archive_faces(
        package, REQUEST, "a" * 32, ("SITE",), replace(config, timeout_seconds=5)
    )
    assert reply.layers == ("SITE",)
    assert len(list(config.job_root.iterdir())) == 2
    assert before == {
        item.path: (package.root / item.path).read_bytes() for item in package.files
    }


@pytest.mark.parametrize(
    "mode",
    [
        "numeric_cleanup",
        "wrong_cleanup",
        "changed_request",
        "changed_input",
        "temporary_reply",
        "malformed",
        "missing_cleanup",
        "exit7",
    ],
)
def test_incomplete_or_unbound_job_never_returns_prepared_faces(tmp_path, mode):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, mode)
    with pytest.raises(ValueError):
        prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    # A damaged staged copy does not change the retained input package.
    assert (package.root / "entry.dwg").read_bytes().startswith(b"synthetic CAD")


def test_capture_and_layer_identity_are_checked(tmp_path):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "ok")
    with pytest.raises(ValueError, match="другому заданию"):
        prepare_archive_faces(package, REQUEST, "b" * 32, ("SITE",), config)
    with pytest.raises(ValueError, match="другому заданию"):
        prepare_archive_faces(package, REQUEST, "a" * 32, ("OTHER",), config)


def test_cancel_running_preparation_allows_a_fresh_retry(tmp_path):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "hang")

    def cancel_after_child_started():
        if any(config.job_root.glob("*/native.json")):
            raise OperationCancelled("Cancelled by the project owner")

    with operation_cancellation(cancel_after_child_started):
        with pytest.raises(OperationCancelled):
            prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    assert (next(config.job_root.iterdir()) / "core.log").exists()
    face_core(config.core_executable, "ok")
    assert prepare_archive_faces(
        package, REQUEST, "a" * 32, ("SITE",), config
    ).layers == ("SITE",)
    assert len(list(config.job_root.iterdir())) == 2
