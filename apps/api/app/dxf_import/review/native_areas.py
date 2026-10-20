"""Просмотр подписанных native-областей по точному пути в неизменяемом захвате без
восстановления контуров в Python.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from hashlib import sha256
from typing import Literal

from shapely.geometry import mapping

from app.cad_bridge.contracts import CadSnapshot, NativeAreaProposal
from app.cad_bridge.provider import native_geometry_shape
from app.dxf_import.contracts import (
    ImportEditability,
    NativeAreaPreview,
    NativeAreaProposalReview,
)
from app.dxf_import.layers.contracts import LayerKind
from app.dxf_import.layers.suggestions import suggest_layer_kind
from app.dxf_import.review.evidence import area_evidence
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.geometry.contracts import GeometrySnapshot
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


def review_records(
    snapshot: CadSnapshot, source_features: list[dict]
) -> list[NativeAreaProposalReview]:
    """Сохранять компактный индекс просмотра; полные полигоны остаются в захвате."""

    units = DXF_UNIT_FACTORS.get(snapshot.source.units_code)
    if units is None:
        raise DomainFailure(FailureReason.NATIVE_AREA_UNITS_UNSUPPORTED)
    factor = units[1]
    proposals = snapshot.area_proposals or []
    if not proposals:
        return []
    requested_paths = {
        (
            proposal.source.handle,
            tuple(proposal.source.instance_chain),
            proposal.source_path_content_sha256,
        )
        for proposal in proposals
    }
    paths = {
        key: feature
        for feature in source_features
        if feature.get("properties", {}).get("source_layer")
        and (
            key := (
                feature.get("properties", {}).get("source_handle"),
                tuple(feature.get("properties", {}).get("source_instance_chain") or []),
                feature.get("properties", {}).get("source_geometry_content_sha256"),
            )
        )
        in requested_paths
    }
    records = []
    for proposal in proposals:
        key = (
            proposal.source.handle,
            tuple(proposal.source.instance_chain),
            proposal.source_path_content_sha256,
        )
        path = paths.get(key)
        counted_gap = (
            path is not None
            and path.get("geometry", {}).get("type") == "LineString"
            and suggest_layer_kind(proposal.layer) == LayerKind.BUILDING
        )
        records.append(
            NativeAreaProposalReview(
                id=proposal.id,
                source=proposal.source,
                layer=proposal.layer,
                source_path_content_sha256=proposal.source_path_content_sha256,
                proposal_sha256=proposal.content_sha256,
                closure_gap_m=proposal.closure_gap_wcs_xy_units * factor,
                area_m2=proposal.preview.native_area_units2 * factor * factor,
                area_gap_entity_type=(
                    str(path["properties"].get("entity_type"))
                    if counted_gap and path is not None
                    else None
                ),
            )
        )
    return records


def unconfirmed_building_paths(features: list[dict]) -> Counter[str]:
    """Считает контуры зданий, которые ещё не заменены подтверждённой поверхностью."""
    covered = {
        (str(member.get("handle")), tuple(member.get("instance_chain") or []))
        for feature in features
        if feature.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
        for member in feature.get("properties", {}).get("source_derived_from", [])
        if isinstance(member, dict)
    }
    remaining: Counter[str] = Counter()
    for feature in features:
        props = feature.get("properties", {})
        layer = str(props.get("source_layer", ""))
        identity = (
            str(props.get("source_handle", "")),
            tuple(props.get("source_instance_chain") or []),
        )
        if (
            feature.get("geometry", {}).get("type") == "LineString"
            and "source_closed_path" in props
            and suggest_layer_kind(layer) == LayerKind.BUILDING
            and identity not in covered
        ):
            remaining[layer] += 1
    return remaining


def append_accepted_areas(
    snapshot: CadSnapshot,
    records: list[NativeAreaProposalReview],
    source_features: list[dict],
) -> None:
    """Восстанавливать явно принятые native-поверхности после проверенного чтения
    исходника.
    """

    units = DXF_UNIT_FACTORS.get(snapshot.source.units_code)
    if units is None:
        raise DomainFailure(FailureReason.NATIVE_AREA_UNITS_UNSUPPORTED)
    factor = units[1]
    by_id = _verified_proposals(snapshot, records, factor)
    path_keys = {
        (
            props.get("source_handle"),
            tuple(props.get("source_instance_chain") or []),
            props.get("source_geometry_content_sha256"),
            props.get("source_layer"),
        )
        for feature in source_features
        if (props := feature.get("properties", {}))
    }
    for item in records:
        if item.decision != "accepted":
            continue
        key = (
            item.source.handle,
            tuple(item.source.instance_chain),
            item.source_path_content_sha256,
            item.layer,
        )
        if key not in path_keys:
            raise DomainFailure(FailureReason.NATIVE_AREA_CONFIRMED_PATH_MISSING)
        source_features.append(_accepted_feature(by_id[item.id], factor))


def decide_native_area(
    project: Project,
    source_content: bytes,
    *,
    source_sha256: str,
    proposal_id: str,
    proposal_sha256: str,
    decision: Literal["accepted", "rejected"],
) -> Project:
    """Пересобирать черновик по исходным объектам и явно принятым областям."""

    source_file = project.source_file
    provenance = source_file.cad_snapshot_provenance if source_file else None
    if (
        source_file is None
        or project.import_status.mode.value != "autocad_live"
        or provenance is None
        or provenance.live_capture is None
    ):
        raise DomainFailure(FailureReason.NATIVE_AREA_LIVE_SOURCE_REQUIRED)
    if project.import_status.editability == ImportEditability.READ_ONLY:
        raise DomainFailure(FailureReason.SOURCE_READ_ONLY)
    if (
        source_file.content_sha256 != source_sha256
        or sha256(source_content).hexdigest() != source_sha256
    ):
        raise DomainFailure(FailureReason.NATIVE_AREA_SNAPSHOT_CHANGED_OR_UNAVAILABLE)
    records = source_file.native_area_proposals
    record = next((item for item in records if item.id == proposal_id), None)
    if record is None or record.proposal_sha256 != proposal_sha256:
        raise DomainFailure(FailureReason.NATIVE_AREA_PROPOSAL_CHANGED)
    if decision == "accepted" and any(
        item.source == record.source for item in source_file.object_decisions
    ):
        raise DomainFailure(FailureReason.NATIVE_AREA_OBJECT_REVIEW_REQUIRED)
    if record.decision == decision:
        return project

    evidence = area_evidence(
        source_content,
        source_sha256=source_sha256,
        autocad_version=provenance.autocad_version,
        target=provenance.target,
    )
    base_features = _source_features(project)
    updated = [
        item.model_copy(update={"decision": decision})
        if item.id == proposal_id
        else item.model_copy(deep=True)
        for item in records
    ]
    by_id = _verify_records(evidence.proposals, updated, evidence.factor)
    path_keys = {
        (
            p.get("source_handle"),
            tuple(p.get("source_instance_chain") or []),
            p.get("source_geometry_content_sha256"),
            p.get("source_layer"),
        )
        for feature in base_features
        if (p := feature.get("properties", {}))
    }
    for item in updated:
        if item.decision == "accepted":
            if (
                item.source.handle,
                tuple(item.source.instance_chain),
                item.source_path_content_sha256,
                item.layer,
            ) not in path_keys:
                raise DomainFailure(FailureReason.NATIVE_AREA_CONFIRMED_PATH_MISSING)
            base_features.append(_accepted_feature(by_id[item.id], evidence.factor))

    next_project = project.model_copy(deep=False)
    next_project.layers = [layer.model_copy(deep=True) for layer in project.layers]
    if record.area_gap_entity_type is not None:
        layer = next(
            (item for item in next_project.layers if item.source_name == record.layer),
            None,
        )
        if layer is None:
            raise DomainFailure(FailureReason.NATIVE_AREA_LAYER_MISSING)
        unresolved = dict(layer.unsupported_geometry_types)
        kind = record.area_gap_entity_type
        current = unresolved.get(kind, 0)
        if decision == "accepted":
            if current < 1:
                raise DomainFailure(FailureReason.NATIVE_AREA_COVERAGE_CHANGED)
            if current == 1:
                unresolved.pop(kind)
            else:
                unresolved[kind] = current - 1
        elif record.decision == "accepted":
            unresolved[kind] = current + 1
        layer.unsupported_geometry_types = unresolved
        layer.geometry_complete = not unresolved

    resolved_routes = {
        (item.source.handle, tuple(item.source.instance_chain))
        for item in source_file.object_decisions
    }
    resolved_routes.update(
        (m.source.handle, tuple(m.source.instance_chain))
        for group in source_file.area_groups
        for m in group.members
    )
    remaining = unconfirmed_building_paths(
        [
            feature
            for feature in base_features
            if (
                feature.get("properties", {}).get("source_handle"),
                tuple(feature.get("properties", {}).get("source_instance_chain") or []),
            )
            not in resolved_routes
        ]
    )
    warnings = [
        warning
        for warning in source_file.warnings
        if not warning.startswith("Линии зданий без подтверждённой площади")
    ]
    if remaining:
        details = "; ".join(
            f"{layer}: {count}" for layer, count in remaining.most_common(3)
        )
        if len(remaining) > 3:
            details += f"; ещё {len(remaining) - 3} слоёв"
        warnings.append(
            f"Линии зданий без подтверждённой площади ({sum(remaining.values())}): "
            f"{details}. Остальная геометрия доступна для работы."
        )
    next_project.source_file = source_file.model_copy(
        update={
            "native_area_proposals": updated,
            "warnings": warnings,
        }
    )
    source_graph = project.source_geometry or project.geometry
    assert source_graph is not None
    next_project.source_geometry = GeometrySnapshot(
        feature_collection={"type": "FeatureCollection", "features": base_features},
        vertical_primitives=source_graph.vertical_primitives,
    )
    next_project.geometry = None
    next_project.map_ready = False
    next_project.site_area_m2 = None
    next_project.planning_area_m2 = None
    next_project.allowed_area_m2 = None
    return next_project


def preview_native_area(
    project: Project, source_content: bytes, proposal_id: str
) -> NativeAreaPreview:
    """Только отображение неизменяемого native-предложения без приёма клиентского
    полигона.
    """
    source = project.source_file
    provenance = source.cad_snapshot_provenance if source else None
    if source is None or provenance is None or provenance.live_capture is None:
        raise DomainFailure(FailureReason.NATIVE_AREA_SNAPSHOT_MISSING)
    if sha256(source_content).hexdigest() != source.content_sha256:
        raise DomainFailure(FailureReason.NATIVE_AREA_SNAPSHOT_CHANGED)
    evidence = area_evidence(
        source_content,
        source_sha256=source.content_sha256,
        autocad_version=provenance.autocad_version,
        target=provenance.target,
    )
    by_id = _verify_records(
        evidence.proposals, source.native_area_proposals, evidence.factor
    )
    native = by_id.get(proposal_id)
    if native is None:
        raise DomainFailure(FailureReason.NATIVE_AREA_PROPOSAL_MISMATCH)
    path = evidence.paths.get(native.source_path_content_sha256)
    if path is None or path.identity != native.source or path.closed:
        raise DomainFailure(FailureReason.NATIVE_AREA_SOURCE_PATH_MISSING)
    geometry = _accepted_feature(native, evidence.factor)["geometry"]
    polygons = (
        [geometry["coordinates"]]
        if geometry["type"] == "Polygon"
        else geometry["coordinates"]
    )
    return NativeAreaPreview(
        source_sha256=source.content_sha256,
        proposal_sha256=native.content_sha256,
        source_path=[
            (point[0] * evidence.factor, point[1] * evidence.factor)
            for point in path.coordinates
        ],
        proposed_rings=[list(ring) for polygon in polygons for ring in polygon],
    )


def _source_features(project: Project) -> list[dict]:
    graph = project.source_geometry or project.geometry
    if graph is None:
        raise DomainFailure(FailureReason.NATIVE_AREA_GEOMETRY_MISSING)
    return [
        feature
        for feature in graph.feature_collection.get("features", [])
        if feature.get("properties", {}).get("source_layer")
        and not feature.get("properties", {}).get("source_area_proposal_id")
    ]


def _accepted_feature(proposal: NativeAreaProposal, factor: float) -> dict:
    shape = native_geometry_shape(proposal.preview, factor)
    return {
        "type": "Feature",
        "id": f"cad-area-proposal-{proposal.content_sha256}",
        "properties": {
            "source_layer": proposal.layer,
            "kind": suggest_layer_kind(proposal.layer).value,
            "entity_type": "REGION",
            "source_handle": proposal.source.handle,
            "source_instance_chain": proposal.source.instance_chain,
            "source_geometry_provider": "autocad_snapshot_v1",
            "source_native_geometry": True,
            "source_geometry_content_sha256": proposal.preview.content_sha256,
            "source_native_area_units2": proposal.preview.native_area_units2,
            "source_native_perimeter_units": proposal.preview.native_perimeter_units,
            "source_sampling_tolerance_m": proposal.preview.achieved_tolerance_m,
            "source_derived_from": [proposal.source.model_dump(mode="json")],
            "source_area_proposal_id": proposal.id,
            "source_area_proposal_sha256": proposal.content_sha256,
            "source_area_proposal_method": proposal.method,
            "source_path_content_sha256": proposal.source_path_content_sha256,
        },
        "geometry": mapping(shape),
    }


def _verified_proposals(
    snapshot: CadSnapshot, records: list[NativeAreaProposalReview], factor: float
) -> dict[str, NativeAreaProposal]:
    return _verify_records(snapshot.area_proposals or [], records, factor)


def _verify_records(
    proposals: Sequence[NativeAreaProposal],
    records: Sequence[NativeAreaProposalReview],
    factor: float,
) -> dict[str, NativeAreaProposal]:
    by_id = {item.id: item for item in proposals}
    if set(by_id) != {item.id for item in records}:
        raise DomainFailure(FailureReason.NATIVE_AREA_LIST_MISMATCH)
    for item in records:
        native = by_id[item.id]
        if (
            native.content_sha256 != item.proposal_sha256
            or native.source != item.source
            or native.layer != item.layer
            or native.source_path_content_sha256 != item.source_path_content_sha256
            or abs(native.preview.native_area_units2 * factor * factor - item.area_m2)
            > max(1e-8, item.area_m2 * 1e-9)
            or abs(native.closure_gap_wcs_xy_units * factor - item.closure_gap_m)
            > max(1e-8, item.closure_gap_m * 1e-9)
        ):
            raise DomainFailure(FailureReason.NATIVE_AREA_PROVENANCE_MISMATCH)
    return by_id
