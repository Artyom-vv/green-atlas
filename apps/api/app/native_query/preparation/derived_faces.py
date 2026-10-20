"""Единый допуск native-областей для расчёта и карты, включая читаемые линейные остатки
исходных объектов.
"""

import json
from collections.abc import Iterable, Iterator, Mapping
from hashlib import sha256
from typing import Any

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer
from app.dxf_import.layers.recognition import NameLayerRecognition
from app.native_query.live.live_inventory import LiveInventory, QueryObject
from app.native_query.preparation.face_contracts import NativeFace

AREA_ROLES = frozenset({"building", "road", "water", "restricted", "existing_green"})
LINE_CATEGORIES = frozenset(
    {
        "fence",
        "retaining_wall",
        "curb",
        "open_drain",
        "road_marking",
        "terrain_slope",
        "surface_boundary",
        "interior_detail",
        "mixed_source",
        "unspecified_topography",
    }
)
SYMBOL_CATEGORIES = frozenset({"pole", "geodetic_marker", "tree", "shrub", "manhole"})
# Явная категория футляра сохраняет native REGION/HATCH и отдельные линии.
# Имя прежнего слоя без этой категории не меняет подготовительную семантику.
NATIVE_AREA_ONLY_CATEGORIES = frozenset({"protective_casing"})


def content_category(layer: Layer) -> LayerCategory | None:
    # Старые назначения не содержат категорий. Имя помогает не выдумывать
    # заполненную поверхность для явной линии или символа, не переназначая роль
    # и не удаляя исходные native-объекты.
    return layer.category or NameLayerRecognition().propose([layer])[0].category


def area_layers(
    layers: Mapping[str, Layer], linear_layers: frozenset[str] = frozenset()
) -> frozenset[str]:
    return frozenset(
        name
        for name, layer in layers.items()
        if layer.mapping_confirmed
        and layer.mapped_kind in AREA_ROLES
        and content_category(layer) not in LINE_CATEGORIES | SYMBOL_CATEGORIES
        and layer.category not in NATIVE_AREA_ONLY_CATEGORIES
        and name not in linear_layers
    )


def face_key(face: NativeFace) -> str:
    # Идентификаторы принадлежат текущему захвату; решения переживают пересборку кеша.
    value = face.model_dump(by_alias=True, exclude={"id", "sampling_tolerance_units"})
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def active_faces(
    inventory: LiveInventory,
    layers: Mapping[str, Layer],
    excluded_routes: frozenset[str] = frozenset(),
    linear_layers: frozenset[str] = frozenset(),
    rejected: frozenset[str] = frozenset(),
) -> Iterator[NativeFace]:
    eligible = area_layers(layers, linear_layers)
    for face in inventory.faces:
        if (
            face.layer not in eligible
            or excluded_routes.intersection(face.physical_routes)
            or face_key(face) in rejected
        ):
            continue
        yield face


def face_targets(faces: Iterable[NativeFace]) -> tuple[QueryObject, ...]:
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


def linear_remainders(
    inventory: LiveInventory,
    layers: Mapping[str, Layer],
    overridden: frozenset[str] = frozenset(),
) -> frozenset[str]:
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


def face_features(
    faces: Iterable[NativeFace], factor: float
) -> Iterator[dict[str, Any]]:
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
                "source_sampling_tolerance_m": (
                    face.sampling_tolerance_units * factor
                    if face.sampling_tolerance_units is not None
                    else None
                ),
                "source_derived_from": members,
                "source_native_repairs": [
                    r.model_dump(by_alias=True) for r in face.repairs
                ],
            },
        }
