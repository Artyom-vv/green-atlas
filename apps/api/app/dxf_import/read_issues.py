"""Expose the exact native admission failures separately from semantic review."""

from app.cad_bridge.provider import DXF_TYPE_BY_AUTOCAD_CLASS
from app.dxf_import.native_area_evidence import area_evidence
from app.dxf_import.object_review_contracts import SourceReadIssue, SourceReadIssues


def source_read_issues(project, content):
    source = project.source_file
    provenance = source.cad_snapshot_provenance if source else None
    if not provenance or not provenance.live_capture or content is None:
        raise ValueError("Отчёт доступен для захвата AutoCAD")
    evidence = area_evidence(
        content,
        source_sha256=source.content_sha256,
        autocad_version=provenance.autocad_version,
        target=provenance.target,
    )
    items = []
    for item in evidence.unresolved:
        entity_type = DXF_TYPE_BY_AUTOCAD_CLASS.get(item.entity_type, item.entity_type)
        detail = item.reason or item.method
        if item.unresolved_reference:
            reason = "Не подключена внешняя ссылка"
        elif entity_type == "HATCH" and "getRegionArea_returned_null" in detail:
            reason = "AutoCAD не вернул область штриховки, нет связанного объекта контура"
        elif entity_type == "HATCH":
            reason = "Не получена расчётная область штриховки"
        elif entity_type == "LWPOLYLINE" and "valid closed path" in detail:
            reason = "Полилиния помечена замкнутой, но её контур не прошёл проверку"
        elif entity_type == "LWPOLYLINE":
            reason = "Не получена расчётная геометрия полилинии"
        else:
            reason = "Не получена расчётная геометрия объекта"
        items.append(
            SourceReadIssue(
                route="/".join((*item.identity.instance_chain, item.identity.handle)),
                layer=item.layer,
                entity_type=entity_type,
                reason=reason,
                detail=detail,
            )
        )
    return SourceReadIssues(source_sha256=source.content_sha256, items=items)
