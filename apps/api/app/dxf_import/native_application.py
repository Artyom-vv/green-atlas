"""Read native CAD bytes without materializing Project geometry or parsing CAD."""

from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import quote

from app.dxf_import.contracts import ImportMode, SourceFile
from app.dxf_import.encoding import (
    DXF_HEADER_PROBE_BYTES,
    DeclaredDxfEncoding,
    declared_dxf_version,
    declared_text_encoding,
    validate_text_encoding,
)
from app.dxf_import.native_contracts import (
    NativeDxfAssetUnavailable,
    NativeDxfSourceAsset,
)
from app.dxf_import.native_ports import NativeDxfRepository
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.projects.contracts import Project

BINARY_DXF_SIGNATURE = b"AutoCAD Binary DXF\r\n\x1a\x00"


def _unit_scale(source: SourceFile) -> float:
    # Old imports persisted the canonical label rather than the numeric factor.
    # Match that existing contract exactly; do not parse arbitrary user labels.
    if source.units_assumed and source.units == "м (принято)":
        return 1.0
    if not source.units_assumed:
        for label, factor in DXF_UNIT_FACTORS.values():
            if source.units == label:
                return factor
    raise NativeDxfAssetUnavailable()


def _native_source(project: Project) -> SourceFile:
    source = project.source_file
    if (
        source is None
        or not source.content_sha256
        or project.import_status.mode != ImportMode.SOURCE_DXF
        or source.preview_provenance is not None
    ):
        raise NativeDxfAssetUnavailable()
    return source


def _encoding(content: bytes, source: SourceFile) -> DeclaredDxfEncoding:
    expected_version = source.dxf_version
    provenance = source.cad_snapshot_provenance
    # Bridge <=0.1.28 stored the application version in the file-format field.
    # Read compatibility only: retain source SHA/size/download verification and
    # do not weaken real ACxxxx mismatch detection or rewrite user projects.
    if (
        provenance is not None
        and provenance.source_sha256 == source.content_sha256
        and expected_version == f"AutoCAD {provenance.autocad_version}"
    ):
        expected_version = declared_dxf_version(content) or expected_version
    encoding = declared_text_encoding(content, expected_version=expected_version)
    if encoding is None:
        raise NativeDxfAssetUnavailable()
    return encoding


@dataclass(frozen=True)
class NativeSourceDownload:
    name: str
    content: bytes
    sha256: str


class NativeDxfSourceApplication:
    def __init__(self, repository: NativeDxfRepository) -> None:
        self.repository = repository

    def metadata(self, project_id: str) -> NativeDxfSourceAsset:
        project = self.repository.get(project_id, lightweight=True)
        source = _native_source(project)
        info = self.repository.get_source_info(
            project_id, prefix_bytes=DXF_HEADER_PROBE_BYTES
        )
        if (
            info is None
            or info.size != source.size
            or not info.size
            or info.prefix.startswith(BINARY_DXF_SIGNATURE)
        ):
            raise NativeDxfAssetUnavailable()
        assert source.content_sha256 is not None
        return NativeDxfSourceAsset(
            project_id=project.id,
            source_name=source.name,
            file_encoding=_encoding(info.prefix, source).browser_codec,
            source_sha256=source.content_sha256,
            source_bytes=info.size,
            unit_scale_to_m=_unit_scale(source),
            units_assumed=source.units_assumed,
            scale_basis="import_assumption"
            if source.units_assumed
            else "imported_units",
            bounds_m=(
                source.bounds[0],
                source.bounds[1],
                source.bounds[2],
                source.bounds[3],
            )
            if source.bounds and len(source.bounds) == 4
            else None,
            file_url=f"/api/projects/{quote(project.id, safe='')}/source-dxf/download"
            f"?expected_source_sha256={source.content_sha256}",
        )

    def download(
        self, project_id: str, expected_source_sha256: str | None = None
    ) -> NativeSourceDownload:
        project = self.repository.get(project_id, lightweight=True)
        if project.import_status.mode == ImportMode.AUTOCAD_LIVE:
            raise NativeDxfAssetUnavailable()
        source = project.source_file
        if expected_source_sha256 is not None:
            source = _native_source(project)
            if source.content_sha256 != expected_source_sha256:
                raise NativeDxfAssetUnavailable()
        content = self.repository.get_source(project.id)
        if content is None or source is None:
            if expected_source_sha256 is not None:
                raise NativeDxfAssetUnavailable()
            raise ValueError("Исходный DXF недоступен для скачивания")
        digest = sha256(content).hexdigest()
        if expected_source_sha256 is not None and (
            digest != expected_source_sha256
            or len(content) != source.size
            or content.startswith(BINARY_DXF_SIGNATURE)
            or not content
        ):
            raise NativeDxfAssetUnavailable()
        if expected_source_sha256 is not None:
            try:
                validate_text_encoding(content, _encoding(content, source))
            except UnicodeError as error:
                raise NativeDxfAssetUnavailable() from error
        return NativeSourceDownload(source.name, content, digest)
