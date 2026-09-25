"""Small, reversible operator decisions about derived native areas."""

from typing import Literal

from app.native_query.contracts import NativeDto, Sha256
from app.native_query.derived_faces import face_key


class NativeFaceReviewItem(NativeDto):
    key: Sha256
    anchor: str
    layer: str
    area_m2: float
    member_count: int
    repair_count: int
    status: Literal["active", "rejected", "overridden"]
    path: list[tuple[float, float]]
    repairs: list[list[tuple[float, float]]]


class NativeFaceReview(NativeDto):
    source_sha256: Sha256
    items: list[NativeFaceReviewItem]


class NativeFaceDecision(NativeDto):
    source_sha256: Sha256
    key: Sha256
    rejected: bool


def review_faces(project, faces, active, factor):
    active_keys = {face_key(face) for face in active}
    rejected = set(project.source_file.rejected_native_face_keys)
    return NativeFaceReview(
        source_sha256=project.source_file.content_sha256,
        items=[
            NativeFaceReviewItem(
                key=(key := face_key(face)),
                anchor=face.anchor,
                layer=face.layer,
                area_m2=face.area * factor**2,
                member_count=len(face.routes),
                repair_count=len(face.repairs),
                status="rejected"
                if key in rejected
                else "active"
                if key in active_keys
                else "overridden",
                path=[(p[0] * factor, p[1] * factor) for p in face.display],
                repairs=[
                    [(p[0] * factor, p[1] * factor) for p in (r.a, r.b)]
                    for r in face.repairs
                ],
            )
            for face in faces
        ],
    )


def change_face_decision(project, request, review):
    if project.import_status.editability == "read_only":
        raise ValueError("Источник доступен только для просмотра")
    if (
        request.source_sha256 != project.source_file.content_sha256
        or review.source_sha256 != request.source_sha256
    ):
        raise ValueError("Исходные данные изменились")
    item = next((item for item in review.items if item.key == request.key), None)
    if item is None or item.status == "overridden":
        raise ValueError("Область изменена другим решением или недоступна")
    rejected = set(project.source_file.rejected_native_face_keys)
    if request.rejected:
        rejected.add(request.key)
    else:
        rejected.discard(request.key)
    if rejected == set(project.source_file.rejected_native_face_keys):
        return project
    return project.model_copy(
        update={
            "source_file": project.source_file.model_copy(
                update={"rejected_native_face_keys": sorted(rejected)}
            ),
            "source_geometry": project.source_geometry or project.geometry,
            "geometry": None,
            "map_ready": False,
            "site_area_m2": None,
            "planning_area_m2": None,
            "allowed_area_m2": None,
        }
    )


def face_context(project, item, scale):
    """Native face overlay sets the view extent, not a new CAD calculation."""
    from app.dxf_import.object_context import object_context

    graph = project.source_geometry or project.geometry
    feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [item.path]},
        "properties": {
            "source_handle": item.anchor.split("/")[-1],
            "source_instance_chain": item.anchor.split("/")[:-1],
            "source_layer": item.layer,
            "entity_type": "REGION",
        },
    }
    view = project.model_copy(
        update={
            "source_geometry": graph.model_copy(
                update={
                    "feature_collection": {
                        "type": "FeatureCollection",
                        "features": [feature, *graph.feature_collection["features"]],
                    }
                }
            )
        }
    )
    return object_context(view, item.anchor, scale=scale)
