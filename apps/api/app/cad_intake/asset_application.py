"""Read-only render assets use the operation journal, not the Project payload."""

from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import quote

from app.cad_intake.asset_contracts import CadAssetUnavailable, CadSourceAsset
from app.cad_intake.asset_files import open_asset
from app.cad_intake.asset_sources import resolve_asset_source
from app.cad_intake.config import CadIntakeConfig
from app.dxf_import.units import meters_per_dxf_unit
from app.operations.ports import OperationRepository


@dataclass(frozen=True)
class OpenCadAsset:
    metadata: CadSourceAsset
    stream: BinaryIO


class CadAssetApplication:
    def __init__(
        self, config: CadIntakeConfig, operations: OperationRepository
    ) -> None:
        self.config = config
        self.operations = operations

    def metadata(self, project_id: str, operation_id: str) -> CadSourceAsset:
        asset = self.open(project_id, operation_id)
        asset.stream.close()
        return asset.metadata

    def open(self, project_id: str, operation_id: str) -> OpenCadAsset:
        operation = self.operations.get(operation_id)
        try:
            source = resolve_asset_source(self.config, operation, project_id)
            drawing = source.drawing
            assert drawing.inspection is not None
            assert drawing.source_sha256 is not None
            assert drawing.normalized_sha256 is not None
            metadata = CadSourceAsset(
                project_id=project_id,
                operation_id=operation_id,
                manifest_sha256=source.passport.manifest_sha256,
                source_name=source.original.name,
                source_sha256=drawing.source_sha256,
                source_bytes=drawing.source_bytes,
                source_units=drawing.inspection.units,
                unit_scale_to_m=meters_per_dxf_unit(drawing.inspection.units),
                asset_sha256=drawing.normalized_sha256,
                asset_bytes=source.asset_bytes,
                file_url=f"/api/projects/{quote(project_id, safe='')}/operations/"
                f"{quote(operation_id, safe='')}/cad-asset/file",
                fidelity=source.conversion.integrity
                if source.conversion
                else "unverified",
            )
            return OpenCadAsset(metadata, open_asset(source))
        except (OSError, ValueError) as error:
            raise CadAssetUnavailable() from error
