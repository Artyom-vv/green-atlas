"""An incomplete preview may omit only ACIS data already absent at source."""

from ezdxf.document import Drawing
from ezdxf.entities.acis import Body

from app.cad_import.aoi_contracts import AoiEntityLink, AoiExportOmission
from app.cad_import.contracts import CadConversionError


def expected_export_omissions(
    target: Drawing, sources: dict[str, Drawing], links: list[AoiEntityLink]
) -> list[AoiExportOmission]:
    omissions = []
    for link in links:
        entity = target.entitydb[link.output_handle]
        if not isinstance(entity, Body) or entity.acis_data:
            continue
        original = sources[link.source_sha256].entitydb[link.source_handle]
        if not isinstance(original, Body) or original.sat or original.sab:
            raise CadConversionError(
                f"ACIS #{link.source_handle}: данные исчезли при подготовке DXF"
            )
        link.exported_in_dxf = False
        omissions.append(AoiExportOmission(source=link))
    return omissions


def verify_exported_handles(
    links: list[AoiEntityLink],
    omissions: list[AoiExportOmission],
    exported_handles: set[str],
) -> None:
    expected_missing = {item.source.output_handle for item in omissions}
    actual_missing = {link.output_handle for link in links} - exported_handles
    if actual_missing != expected_missing:
        unexpected = actual_missing - expected_missing
        raise CadConversionError(
            "Фактическая запись DXF не совпала с проверкой полноты; "
            f"неожиданно отсутствуют handles {sorted(unexpected)[:10]}"
        )
