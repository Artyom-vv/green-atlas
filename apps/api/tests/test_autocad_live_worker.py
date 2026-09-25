"""The large capture path is the same native importer in a bounded process."""

import json
from hashlib import sha256

import pytest
from test_cad_bridge_compiler import near_closed_building_proposal_probe

from app.composition import create_runtime
from app.dxf_import.capacity import SourceCapacityExceeded
from app.projects.concurrency import (
    reset_expected_project_version,
    set_expected_project_version,
)


@pytest.mark.parametrize('selection', ['size', 'capacity'])
def test_supervised_live_import_preserves_native_review_and_source(
    tmp_path, monkeypatch, selection
):
    probe = near_closed_building_proposal_probe()
    probe['capture_mode'] = 'live_document'
    probe['source'].update(
        database_modified_flags=32, live_database_matches_disk=False
    )
    content = json.dumps(probe).encode()
    runtime = create_runtime(tmp_path / 'live.sqlite3')
    try:
        project = runtime.application.create_project('Supervised live')
        if selection == 'size':
            monkeypatch.setattr('app.application.LIVE_PROCESS_THRESHOLD_BYTES', 1)
        else:
            monkeypatch.setattr('app.application.LIVE_PROCESS_THRESHOLD_BYTES', 10**9)
            def capacity_exceeded(*_args, **_kwargs):
                raise SourceCapacityExceeded('ordinary HTTP budget exceeded')
            monkeypatch.setattr(
                runtime.application._imports, 'import_autocad_live',
                capacity_exceeded,
            )
        token = set_expected_project_version(str(project.state_version))
        try:
            imported = runtime.application.import_autocad_live(
                project.id,
                'document.autocad.json',
                content,
                autocad_version='2027.0.1',
                target='macos-arm64',
            )
        finally:
            reset_expected_project_version(token)
        assert imported.import_status.mode == 'autocad_live'
        assert imported.source_file.content_sha256 == sha256(content).hexdigest()
        assert len(imported.source_file.native_area_proposals) == 1
        assert imported.source_file.native_area_proposals[0].decision == 'pending'
        assert imported.geometry.feature_collection['features'] == []
        assert runtime.project_repository.get_source(project.id) == content
        assert runtime.project_repository.get(project.id).geometry is not None
    finally:
        runtime.close()
