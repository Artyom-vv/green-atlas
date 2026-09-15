from pathlib import PurePosixPath, PureWindowsPath

from app.cad_import.package_contracts import SourcePackage
from app.cad_intake.contracts import (
    CadDrawingPassport,
    CadPackagePassport,
    CadReferencePassport,
)


def public_reference(value: str) -> str:
    """Retain CAD-relative names, redact legacy absolute/outside XREF locations."""
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
        return path.name
    return path.as_posix()


def make_passport(
    package: SourcePackage, root_id: str, digest: str, hashes: dict[str, str]
) -> CadPackagePassport:
    drawings: list[CadDrawingPassport] = []
    for item in package.drawings:
        inspection = item.inspection.model_copy(deep=True) if item.inspection else None
        if inspection is not None:
            inspection.xrefs = {
                block: public_reference(path)
                for block, path in inspection.xrefs.items()
            }
        drawings.append(
            CadDrawingPassport(
                path=item.path,
                source_sha256=item.source_sha256,
                source_bytes=item.source_bytes,
                normalized_sha256=hashes.get(item.path),
                inspection=inspection,
                status=item.status,
                message="Чертёж не прочитан; подробности сохранены в серверном журнале"
                if item.message
                else None,
            )
        )
    return CadPackagePassport(
        root_id=root_id,
        entry=package.entry,
        manifest_sha256=digest,
        drawings=drawings,
        references=[
            CadReferencePassport(
                owner=item.owner,
                block=item.block,
                requested_path=public_reference(item.requested_path),
                target=item.target,
                status=item.status,
                resolution=item.resolution,
                expected_sha256=item.expected_sha256,
                resolution_reason=item.resolution_reason,
            )
            for item in package.references
        ],
        status=package.status,
        blockers=package.blockers,
    )
