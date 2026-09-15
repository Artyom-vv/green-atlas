import asyncio
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

from app.cad_import.boundary_contracts import DrawingBoundaryCatalog
from app.cad_intake.asset_contracts import CadAssetUnavailable
from app.cad_intake.asset_files import ASSET_CHUNK_BYTES, asset_chunks
from app.cad_intake.asset_response import CadAssetResponse
from app.cad_intake.config import AllowedCadRoot
from app.operations.contracts import OperationKind, OperationStatus
from tests.cad_asset_fixtures import fixture_asset


@pytest.fixture
def service(tmp_path):
    fixture = fixture_asset(tmp_path)
    try:
        yield fixture
    finally:
        fixture.runtime.close()


def test_completed_full_asset_is_bound_to_source_and_passport(service):
    before = service.runtime.project_repository.get(
        service.operation.project_id
    ).model_dump_json()
    metadata = service.application.metadata(
        service.operation.project_id, service.operation.id
    )
    assert metadata.file_url == service.url + "/file"
    assert metadata.source_sha256 == sha256(service.original.read_bytes()).hexdigest()
    assert metadata.asset_sha256 == sha256(service.asset.read_bytes()).hexdigest()
    assert metadata.asset_bytes == service.asset.stat().st_size
    assert metadata.unit_scale_to_m == 1 and metadata.source_units == 6
    assert metadata.calculation_ready is False
    assert metadata.external_references_loaded is False
    assert metadata.scope == "entry_drawing" and metadata.fidelity == "unverified"
    assert str(service.asset.parent) not in metadata.model_dump_json()
    assert str(service.original.parent) not in metadata.model_dump_json()
    assert (
        service.runtime.project_repository.get(
            service.operation.project_id
        ).model_dump_json()
        == before
    )


@pytest.mark.parametrize("units,scale", [(0, None), (4, 0.001), (6, 1.0), (99, None)])
def test_native_dxf_units_never_guess_unknown_scale(tmp_path, units, scale):
    service = fixture_asset(tmp_path, units=units, native=True)
    try:
        metadata = service.application.metadata(
            service.operation.project_id, service.operation.id
        )
        assert metadata.unit_scale_to_m == scale
        assert metadata.asset_sha256 == metadata.source_sha256
    finally:
        service.runtime.close()


@pytest.mark.parametrize(
    "status", [item for item in OperationStatus if item != OperationStatus.COMPLETED]
)
def test_only_completed_intake_can_expose_asset(service, status):
    service.operation.status = status
    service.runtime.operation_repository.save(service.operation)
    with pytest.raises(CadAssetUnavailable):
        service.application.metadata(service.operation.project_id, service.operation.id)


@pytest.mark.parametrize("change", ["kind", "passport", "drawing", "entry_hash"])
def test_completed_receipt_requires_matching_readable_entry(service, change):
    if change == "kind":
        service.operation.kind = OperationKind.PREPARE_CAD_PREVIEW
    elif change == "passport":
        service.operation.cad_intake.passport = None
    elif change == "drawing":
        service.operation.cad_intake.passport.drawings[0].status = "rejected"
    else:
        service.operation.cad_intake.request.entry_sha256 = "a" * 64
    service.runtime.operation_repository.save(service.operation)
    with pytest.raises(CadAssetUnavailable):
        service.application.open(service.operation.project_id, service.operation.id)


@pytest.mark.parametrize("target", ["original", "asset", "manifest", "evidence"])
def test_tampered_bytes_fail_before_any_file_is_exposed(service, target):
    path = getattr(service, target)
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    with pytest.raises(CadAssetUnavailable):
        service.application.open(service.operation.project_id, service.operation.id)


@pytest.mark.parametrize("target", ["normalized_path", "evidence_path"])
def test_private_manifest_cannot_expose_files_outside_cache(service, tmp_path, target):
    outside = tmp_path / "unrelated.dxf"
    outside.write_bytes(service.asset.read_bytes())
    setattr(service.package.drawings[0], target, str(outside))
    service.save_package()
    with pytest.raises(CadAssetUnavailable):
        service.application.open(service.operation.project_id, service.operation.id)


def test_revoked_or_repointed_root_prevents_asset_access(service, tmp_path):
    config = service.application.config
    service.application.config = replace(config, roots=())
    with pytest.raises(CadAssetUnavailable):
        service.application.metadata(service.operation.project_id, service.operation.id)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / service.original.name).write_bytes(service.original.read_bytes())
    service.application.config = replace(
        config, roots=(AllowedCadRoot("official", "Moved", elsewhere),)
    )
    with pytest.raises(CadAssetUnavailable):
        service.application.metadata(service.operation.project_id, service.operation.id)


def test_binary_dxf_is_not_advertised_as_ascii(tmp_path):
    service = fixture_asset(tmp_path, native=True)
    try:
        content = b"AutoCAD Binary DXF\r\n\x1a\x00rest"
        service.original.write_bytes(content)
        digest = sha256(content).hexdigest()
        private = service.package.drawings[0]
        public = service.operation.cad_intake.passport.drawings[0]
        private.source_sha256 = public.source_sha256 = public.normalized_sha256 = digest
        private.source_bytes = public.source_bytes = len(content)
        service.operation.cad_intake.request.entry_sha256 = digest
        service.save_package()
        with pytest.raises(CadAssetUnavailable):
            service.application.open(service.operation.project_id, service.operation.id)
    finally:
        service.runtime.close()


def test_streaming_is_chunked_and_closes_on_completion_and_abandonment(service):
    opened = service.application.open(
        service.operation.project_id, service.operation.id
    )
    chunks = list(asset_chunks(opened.stream))
    assert len(chunks) > 2
    assert max(map(len, chunks)) == ASSET_CHUNK_BYTES
    assert sha256(b"".join(chunks)).hexdigest() == opened.metadata.asset_sha256
    assert opened.stream.closed
    abandoned = BytesIO(b"x" * ASSET_CHUNK_BYTES * 2)
    iterator = asset_chunks(abandoned)
    next(iterator)
    iterator.close()
    assert abandoned.closed


def test_wrong_project_never_retrieves_another_projects_asset(service):
    with pytest.raises(KeyError):
        service.application.open("another-project", service.operation.id)


def test_refreshed_package_inspection_keeps_immutable_conversion_asset_usable(service):
    catalog = DrawingBoundaryCatalog(total_candidates=0)
    service.package.drawings[0].inspection.boundary_catalog = catalog
    service.operation.cad_intake.passport.drawings[
        0
    ].inspection.boundary_catalog = catalog
    evidence_before = service.evidence.read_bytes()
    service.save_package()
    metadata = service.application.metadata(
        service.operation.project_id, service.operation.id
    )
    assert metadata.asset_bytes == service.asset.stat().st_size
    assert service.evidence.read_bytes() == evidence_before


def test_asset_read_uses_bounded_streams_and_missing_file_fails(service, monkeypatch):
    def prohibit_whole_file_read(_):
        pytest.fail("Use bounded streams instead of materializing CAD files")

    monkeypatch.setattr(Path, "read_bytes", prohibit_whole_file_read)
    opened = service.application.open(
        service.operation.project_id, service.operation.id
    )
    assert sum(map(len, asset_chunks(opened.stream))) == opened.metadata.asset_bytes
    service.asset.unlink()
    with pytest.raises(CadAssetUnavailable):
        service.application.open(service.operation.project_id, service.operation.id)


def test_stream_retains_verified_descriptor_when_cache_path_is_replaced(service):
    opened = service.application.open(
        service.operation.project_id, service.operation.id
    )
    replacement = service.asset.with_suffix(".replacement")
    replacement.write_bytes(b"new content")
    try:
        replacement.replace(service.asset)
    except PermissionError:
        # Windows may protect the open file from replacement; either behavior
        # must preserve the bytes verified by this request's descriptor.
        pass
    digest = sha256()
    for chunk in asset_chunks(opened.stream):
        digest.update(chunk)
    assert digest.hexdigest() == opened.metadata.asset_sha256


def test_cache_symlink_cannot_escape_allowed_storage(service, tmp_path):
    outside = tmp_path / "outside.dxf"
    outside.write_bytes(service.asset.read_bytes())
    link = service.asset.with_name("linked.dxf")
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("File symlinks are unavailable on this host")
    service.package.drawings[0].normalized_path = str(link)
    service.save_package()
    with pytest.raises(CadAssetUnavailable):
        service.application.open(service.operation.project_id, service.operation.id)


@pytest.mark.parametrize("abort_at", ["http.response.start", "http.response.body"])
@pytest.mark.parametrize("error", [OSError, asyncio.CancelledError])
def test_response_closes_descriptor_even_before_iterator_starts(
    service, abort_at, error
):
    opened = service.application.open(
        service.operation.project_id, service.operation.id
    )
    response = CadAssetResponse(opened)

    async def send(message):
        if message["type"] == abort_at:
            raise error("client left")

    async def receive():
        return {"type": "http.disconnect"}

    async def execute():
        with pytest.raises(
            asyncio.CancelledError if error is asyncio.CancelledError else Exception
        ):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        # Assert while the response/iterator are still retained: GC is not cleanup.
        assert opened.stream.closed

    asyncio.run(execute())
