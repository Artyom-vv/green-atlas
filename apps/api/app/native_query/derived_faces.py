"""One admission policy for native face calculation and map presentation."""

import json
from hashlib import sha256

from app.dxf_import.layer_recognition import NameLayerRecognition
from app.native_query.live_inventory import QueryObject

AREA_ROLES = frozenset({"building", "road", "water", "restricted", "existing_green"})
LINE_CATEGORIES = frozenset(
    {"fence", "retaining_wall", "curb", "open_drain", "road_marking",
     "terrain_slope", "surface_boundary", "interior_detail", "mixed_source",
     "unspecified_topography"}
)
SYMBOL_CATEGORIES = frozenset({"pole", "geodetic_marker", "tree", "shrub", "manhole"})


def content_category(layer):
    # Older persisted mappings predate categories. Use name evidence only to
    # avoid inventing filled faces for explicit line/symbol subjects; this
    # neither remaps their role nor removes their original native objects.
    return layer.category or NameLayerRecognition().propose([layer])[0].category


def area_layers(layers, linear_layers=frozenset()):
    return frozenset(
        name
        for name, layer in layers.items()
        if layer.mapping_confirmed
        and layer.mapped_kind in AREA_ROLES
        and content_category(layer) not in LINE_CATEGORIES | SYMBOL_CATEGORIES
        and name not in linear_layers
    )


def face_key(face):
    # IDs belong to a running capture; decisions survive a cache rebuild.
    value = face.model_dump(by_alias=True, exclude={"id"})
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def active_faces(
    inventory,
    layers,
    excluded_routes=frozenset(),
    linear_layers=frozenset(),
    rejected=frozenset(),
):
    eligible = area_layers(layers, linear_layers)
    for face in inventory.faces:
        if (
            face.layer not in eligible
            or excluded_routes.intersection(face.physical_routes)
            or face_key(face) in rejected
        ):
            continue
        yield face


def face_targets(faces):
    return tuple(
        QueryObject(
            routes=(face.anchor, *sorted(set(face.routes) - {face.anchor})),
            layer=face.layer,
            bounds=face.bounds,
            curve=False,
            error="",
            face_id=face.id,
        )
        for face in faces
    )


def linear_remainders(inventory, layers, overridden=frozenset()):
    indexed = {item.route: item for item in inventory.objects}
    line_layers = {
        name
        for name, layer in layers.items()
        if content_category(layer) in LINE_CATEGORIES
    }
    routes = set(inventory.linear_routes) | {
        row.route
        for row in inventory.objects
        if row.curve and not row.error and not row.context and row.layer in line_layers
    }
    return frozenset(
        route
        for route in routes
        if route not in overridden
        and (layer := layers.get(indexed[route].layer)) is not None
        and layer.mapping_confirmed
        and layer.mapped_kind != "site_border"
    )


def face_features(faces, factor):
    for face in faces:
        members = [
            {"handle": route.split("/")[-1], "instance_chain": route.split("/")[:-1]}
            for route in face.routes
        ]
        yield {
            "type": "Feature",
            "id": f"native-face-{face.id}",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[p[0] * factor, p[1] * factor] for p in face.display]],
            },
            "properties": {
                "source_layer": face.layer,
                "entity_type": "REGION",
                "source_handle": face.anchor.split("/")[-1],
                "source_instance_chain": face.anchor.split("/")[:-1],
                "source_native_face_id": face.id,
                "source_native_geometry": True,
                "source_native_area_units2": face.area,
                "source_derived_from": members,
                "source_native_repairs": [
                    r.model_dump(by_alias=True) for r in face.repairs
                ],
            },
        }
