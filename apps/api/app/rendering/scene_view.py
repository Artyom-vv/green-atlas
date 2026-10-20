"""Fast display projection. No source asset hashing, conversion, render packet or network.

The accepted CAD geometry stays unchanged. Display areas retain shared boundaries
and holes; engineering linework remains available in 2D and source diagnostics.
"""

from __future__ import annotations

import json
import marshal
import sys
from collections import defaultdict
from collections.abc import Callable
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from threading import RLock
from types import CodeType
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from shapely import union_all
from shapely.geometry import Point, Polygon, mapping, shape

from app.projects.contracts import Project
from app.projects.resource_scope import (
    project_resource_scope,
    register_project_resource,
)
from app.rendering.assets import plant_asset
from app.rendering.building_heights import prepare_buildings
from app.rendering.contracts import (
    RenderCamera,
    RenderPreparationPreview,
    RenderPreparationSummary,
    SceneRenderPreparation,
    SceneView,
)
from app.rendering.environment import _neighbours
from app.rendering.environment_inputs import ROLES, _circle, _tree_layer
from app.rendering.facade_geometry import prepare_facades
from app.rendering.packet import compact_mesh
from app.rendering.preview_assets import _SURFACES, _model_assignment, _valid_manifest
from app.rendering.scene_curbs import native_scene_curbs
from app.scene.contracts import ScenePlantObject, SceneSnapshot
from app.scene.domain import build_scene, validate_horizon
from app.scene.plants import scene_origin, scene_plants
from app.species.catalog import get_species

_CAMERA = RenderCamera(
    position=(0, 500, 0),
    quaternion=(0, 0, 0, 1),
    fov_y_deg=42,
    aspect=1,
    near=0.1,
    far=10000,
)


def _canonical_module_code(code: CodeType) -> CodeType:
    """Retain executable bytes/constants, excluding installation/line metadata."""
    return code.replace(
        co_filename="",
        co_firstlineno=0,
        co_linetable=b"",
        co_consts=tuple(
            _canonical_module_code(value) if isinstance(value, CodeType) else value
            for value in code.co_consts
        ),
    )


def _curb_compiler_fingerprint() -> str:
    # The signed frozen runtime carries Python modules in its PYZ, not as
    # raw .py files. Its loader is the authority for the executable compiler.
    # Source and frozen loaders provide the same code contract; missing code
    # is an error, never a constant fallback that could reuse stale geometry.
    digest = sha256()
    digest.update(str(sys.implementation.cache_tag).encode())
    for name in (
        "scene_curbs",
        "curb_geometry",
        "curb_semantics",
        "environment_inputs",
        "environment",
        "georeference",
        "location_binding",
        "location_source",
    ):
        module_name = "app.rendering." + name
        module = import_module(module_name)
        loader = module.__spec__.loader if module.__spec__ is not None else None
        get_code = getattr(loader, "get_code", None)
        code = get_code(module_name) if callable(get_code) else None
        if not isinstance(code, CodeType):
            raise ValueError("Не найден исполняемый модуль просмотра: " + module_name)
        digest.update(module_name.encode())
        digest.update(marshal.dumps(_canonical_module_code(code)))
    return digest.hexdigest()


class SceneViewReader:
    def __init__(
        self,
        repository: Any,
        directory: Path,
        display_features: Callable[[Project], list[dict] | None] | None = None,
    ) -> None:
        self.repository, self.directory = repository, directory
        self.display_features = display_features
        self._lock = RLock()
        self._cache: dict[tuple, SceneView] = {}
        self._assets: dict[str, tuple[Path, dict]] = {}
        self._models: dict[str, dict] = {}
        self._materials: dict[str, dict] = {}
        self._indexed: set[str] = set()
        self._converter = sha256(
            Path(__file__).with_name("preview_asset_worker.py").read_bytes()
        ).hexdigest()
        self._style = sha256(
            Path(__file__).with_name("render_style.py").read_bytes()
        ).hexdigest()
        self._curbs = _curb_compiler_fingerprint()

    def _index_assets(self) -> None:
        # Verified browser derivatives are reused independently across bundles.
        # This checks small GLBs once per process, never the 409 MB source library.
        for path in (self.directory / "preview-cache").glob("*/manifest.json"):
            if path.parent.name in self._indexed:
                continue
            try:
                identity = json.loads(path.read_bytes()).get("identity", {})
                if (
                    identity.get("worker_sha256") != self._converter
                    or identity.get("style_sha256") != self._style
                ):
                    self._indexed.add(path.parent.name)
                    continue
                manifest = _valid_manifest(path.parent, path.parent.name)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            self._indexed.add(path.parent.name)
            for row in manifest["assets"]:
                self._assets[row["id"]] = (path.parent / row["filename"], row)
                if row["key"].startswith("botaniq/"):
                    self._models[row["key"]] = row
                elif row["kind"] == "texture":
                    self._materials[row["key"]] = row

    def asset(self, asset_id: str) -> tuple[Path, str, str]:
        with self._lock:
            self._index_assets()
            for cache_key in {path.parent.name for path, _ in self._assets.values()}:
                register_project_resource("preview-cache", cache_key)
            if asset_id not in self._assets:
                raise KeyError("Ресурс просмотра не найден")
            path, row = self._assets[asset_id]
            # An immutable admitted file must retain its size and stat identity.
            stat = path.stat()
            identity = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
            admitted = row.setdefault("_stat", identity)
            if admitted != identity or stat.st_size != row["size_bytes"]:
                raise ValueError("Ресурс просмотра изменился")
            return path, row["mime_type"], row["sha256"]

    def read(self, project_id: str, horizon: int) -> SceneView:
        with project_resource_scope(self.repository, project_id):
            return self._read(project_id, horizon)

    def _read(self, project_id: str, horizon: int) -> SceneView:
        validate_horizon(horizon)
        with self._lock:
            self._index_assets()
            # Geometry is independent of catalog admission. A newly prepared
            # model/PBR derivative only changes appearance, not the CAD view.
            appearance = sha256(
                json.dumps(
                    sorted(
                        (key, row["id"])
                        for key, row in {**self._models, **self._materials}.items()
                    )
                ).encode()
            ).hexdigest()
            metadata = self.repository.get(project_id, lightweight=True)
            frame = self.directory / "georeferences" / f"{project_id}.json"
            frame_sha = (
                sha256(frame.read_bytes()).hexdigest() if frame.exists() else None
            )
            origin = scene_origin(metadata.model_copy(update={"plan": None}))
            geometry_key = sha256(
                json.dumps(
                    [
                        "green-atlas.scene-view/5",
                        self._converter,
                        self._style,
                        self._curbs,
                        project_id,
                        metadata.geometry_version,
                        metadata.source_file.content_sha256
                        if metadata.source_file
                        else None,
                        [row.model_dump(mode="json") for row in metadata.layers],
                        origin,
                        metadata.coordinate_reference.model_dump(mode="json"),
                        frame_sha,
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            key = (
                geometry_key,
                metadata.plan.version if metadata.plan else 1,
                horizon,
                appearance,
            )
            register_project_resource("scene-view", geometry_key)
            if key in self._cache:
                return self._cache[key]
            for cached_key, cached in tuple(self._cache.items()):
                if cached_key[0] == geometry_key:
                    result = self._with_plan(cached, metadata, horizon, geometry_key)
                    if len(self._cache) > 5:
                        self._cache.clear()
                    self._cache[key] = result
                    return result
            disk = self.directory / "scene-views" / (geometry_key + ".json")
            if disk.exists():
                document = json.loads(disk.read_bytes())
                body = document["view"]
                if (
                    document["sha256"]
                    != sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                ):
                    raise ValueError(
                        "Нарушена целостность сохранённого представления 3D"
                    )
                result = self._with_plan(
                    SceneView.model_validate(body), metadata, horizon, geometry_key
                )
                self._cache[key] = result
                return result
            cached_features = (
                self.display_features(metadata) if self.display_features else None
            )
            if cached_features is not None:
                from app.geometry.contracts import GeometrySnapshot

                graph = metadata.geometry or GeometrySnapshot(
                    feature_collection={"type": "FeatureCollection", "features": []}
                )
                graph = graph.model_copy(
                    update={
                        "feature_collection": {
                            "type": "FeatureCollection",
                            "features": cached_features,
                        }
                    }
                )
                project = metadata.model_copy(
                    update={
                        "geometry": graph if metadata.geometry else None,
                        "source_geometry": graph if metadata.geometry is None else None,
                    }
                )
            elif hasattr(self.repository, "get_geometry_view"):
                geometry_view = self.repository.get_geometry_view(project_id)
                project = metadata.model_copy(
                    update={
                        "geometry": geometry_view.geometry,
                        "source_geometry": geometry_view.source_geometry,
                    }
                )
            else:
                project = self.repository.get(project_id)
            graph = project.geometry or project.source_geometry
            source_features = (
                graph.feature_collection.get("features", []) if graph else []
            )
            curbs = native_scene_curbs(project, source_features, origin)
            layers = {row.source_name: row for row in project.layers}
            selected: list[dict[str, Any]] = []
            markers: dict[Any, Any] = {}
            roles: dict[str, str] = {}
            for index, feature in enumerate(source_features):
                prop = feature.get("properties", {})
                layer = layers.get(prop.get("source_layer"))
                identifier = str(
                    feature.get("id") or prop.get("source_handle") or f"context-{index}"
                )
                geometry_type = feature.get("geometry", {}).get("type")
                if layer and _tree_layer(layer):
                    marker = _circle(feature)
                    if marker:
                        chain = prop.get("source_instance_chain")
                        if (
                            isinstance(chain, list)
                            and chain
                            and all(isinstance(value, str) for value in chain)
                        ):
                            anchors = markers.setdefault(tuple(chain), {})
                            anchors.setdefault(
                                tuple(round(value, 6) for value in marker),
                                prop.get("source_base_elevation_m"),
                            )
                if geometry_type not in {"Polygon", "MultiPolygon"}:
                    continue
                if identifier in curbs["curb_polygon_ids"]:
                    continue
                kind = prop.get("kind")
                if kind not in {"building", "road", "lawn", "water"}:
                    continue
                role = (
                    ROLES.get(str(layer.category))
                    if layer and layer.mapping_confirmed
                    else None
                )
                if kind in {"road", "lawn"} and role not in {
                    "road",
                    "sidewalk",
                    "lawn",
                    "curb",
                    "marking",
                }:
                    continue
                selected.append(feature)
                roles[identifier] = role or kind
            quick_graph = (
                graph.model_copy(
                    update={
                        "feature_collection": {
                            "type": "FeatureCollection",
                            "features": selected,
                        }
                    }
                )
                if graph
                else None
            )
            quick_project = project.model_copy(
                update={
                    "geometry": quick_graph if project.geometry else None,
                    "source_geometry": quick_graph if not project.geometry else None,
                }
            )
            snapshot = build_scene(
                quick_project, horizon, species=get_species, origin_xy=origin
            )
            grouped = defaultdict(list)
            retained = []
            for row in snapshot.context_features:
                polygon = shape(row.geometry)
                if not polygon.is_valid or polygon.is_empty:
                    raise ValueError(
                        "Невалидная поверхность просмотра: " + row.feature_id
                    )
                if row.kind == "building":
                    retained.append(row)
                else:
                    grouped[
                        (roles.get(row.feature_id, row.kind), row.base_elevation_m or 0)
                    ].append(polygon)
            surfaces = []
            for base in {key[1] for key in grouped}:
                polygons = {
                    role: union_all(rows)
                    for (role, elevation), rows in grouped.items()
                    if elevation == base
                }
                lawn = polygons.get("lawn", Polygon())
                sidewalk = polygons.get("sidewalk", Polygon()).difference(lawn)
                # Genuine curb footprints stay surfaces. Native projected
                # closed lines are compiled as perimeter volumes separately.
                curb = polygons.get("curb", Polygon()).difference(lawn)
                road = polygons.get("road", Polygon()).difference(
                    union_all([lawn, sidewalk, curb])
                )
                for role, polygon, lift in [
                    ("road", road, 0.055),
                    ("sidewalk", sidewalk, 0.08),
                    ("lawn", lawn, 0.012),
                    ("curb", curb, 0.18),
                    ("water", polygons.get("water", Polygon()), 0.008),
                ]:
                    if polygon.is_empty:
                        continue
                    # Surface shell has no vertical slab; shared boundaries and
                    # metre-based textures stay identical at all camera angles.
                    surfaces.append(
                        {
                            "id": f"view:{role}:{base}",
                            "kind": role,
                            "source": "cad_surface",
                            "vertices": [],
                            "faces": [],
                            "surface_geometry": mapping(polygon),
                            "surface_elevation_m": base + lift,
                        }
                    )
            body = snapshot.model_dump(mode="json", exclude={"context_features"})
            packet: dict[str, Any] = {
                "scene": body,
                "camera": _CAMERA.model_dump(mode="json"),
                "meshes": [],
                "building_inputs": [r.model_dump(mode="json") for r in retained],
            }
            packet = prepare_buildings(packet, self.directory, allow_network=False)
            mask = (
                union_all([shape(r.geometry) for r in snapshot.context_features])
                if snapshot.context_features
                else Polygon()
            )
            neighbours, map_info = _neighbours(
                packet, self.directory, mask, allow_network=False
            )
            cad, cad_details, _ = prepare_facades(
                packet["building_display_meshes"], "cad_building"
            )
            external, external_details, _ = prepare_facades(neighbours, "map_building")
            meshes = [
                {**compact_mesh(row), "source": source}
                for source, rows in [
                    ("cad_building", cad),
                    ("map_building", external),
                    ("cad_building", cad_details),
                    ("map_building", external_details),
                ]
                for row in rows
            ]
            meshes.extend(surfaces)
            meshes.extend(compact_mesh(row) for row in curbs["meshes"])
            self._index_assets()
            profile = json.loads(
                Path(__file__).with_name("native_style.json").read_bytes()
            )
            ox, oy = snapshot.coordinate_origin
            trees, planned = [], []
            required: set[str] = set()

            lawn_mask = union_all(
                [
                    shape(row.geometry)
                    for row in snapshot.context_features
                    if roles.get(row.feature_id) == "lawn"
                ]
            )
            for chain, anchors in sorted(markers.items()):
                if len(anchors) != 1:
                    continue
                (x, y, _), base = next(iter(anchors.items()))
                if not lawn_mask.covers(Point(x - ox, y - oy)):
                    continue
                identifier = "existing-tree/" + "/".join(chain)
                # Same explicit illustrative size used by the render pipeline.
                marker_plant = ScenePlantObject(
                    object_id=identifier,
                    kind="tree",
                    local_x=x - ox,
                    local_y=y - oy,
                    crown_shape="round",
                    canopy_radius_min_m=3.5,
                    canopy_radius_max_m=3.5,
                )
                _, relative, _ = plant_asset(marker_plant, profile)
                trees.append(
                    self._tree(
                        identifier,
                        x - ox,
                        y - oy,
                        base or 0,
                        12,
                        3.5,
                        "botaniq/" + relative,
                        required,
                    )
                )
            for plant in snapshot.objects:
                _, relative, _ = plant_asset(plant, profile)
                planned.append(
                    self._tree(
                        plant.object_id,
                        plant.local_x,
                        plant.local_y,
                        0,
                        max(0.4, plant.height_max_m or plant.canopy_radius_max_m * 2.4),
                        max(0.18, plant.canopy_radius_max_m),
                        "botaniq/" + relative,
                        required,
                    )
                )
            materials = []
            for role, settings in _SURFACES.items():
                maps = {
                    name: self._materials.get(role + ":" + name)
                    for name in ["diffuse", "roughness", "normal_gl"]
                }
                ready_maps = {
                    name: row for name, row in maps.items() if row is not None
                }
                if len(ready_maps) == 3:
                    materials.append(
                        {
                            "kind": role,
                            **settings,
                            "maps": {
                                name: row["id"] for name, row in ready_maps.items()
                            },
                        }
                    )
                    required.update(row["id"] for row in ready_maps.values())
            assets = [
                {
                    **{
                        k: v
                        for k, v in self._assets[id][1].items()
                        if k not in {"key", "filename", "_stat"}
                    },
                    "url": f"/api/projects/{project_id}/scene-view-assets/{id}",
                }
                for id in sorted(required)
            ]
            # The display snapshot carries only editable plants and terrain;
            # composition surfaces live once in the environment payload.
            snapshot.context_features = []
            snapshot.note += (
                " Инженерные линии доступны в 2D; 3D использует поверхности просмотра."
            )
            snapshot.content_sha256 = sha256(
                snapshot.model_dump_json(exclude={"content_sha256"}).encode()
                + geometry_key.encode()
            ).hexdigest()
            preview = {
                "meshes": meshes,
                "curbs": [],
                "context_trees": trees,
                "planned_plants": planned,
                "suppressed_source_ids": curbs["suppressed_source_ids"],
                "assets": assets,
                "materials": materials,
                "asset_version": 1 if assets else 0,
            }
            preview = RenderPreparationPreview.model_validate(preview).model_dump(
                mode="json"
            )
            digest = sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
            counts = packet["building_heights"]["counts"]
            environment = SceneRenderPreparation(
                id=uuid5(NAMESPACE_URL, snapshot.content_sha256 + digest),
                sha256=digest,
                project_id=project_id,
                scene_sha256=snapshot.content_sha256,
                horizon_year=horizon,
                geometry_version=snapshot.geometry_version,
                plan_version=snapshot.plan_version,
                camera=_CAMERA,
                preview=RenderPreparationPreview.model_validate(preview),
                summary=RenderPreparationSummary.model_validate(
                    {
                        "message": "Просмотр готов",
                        "context_tree_count": len(trees),
                        "map_building_count": len(external),
                        "map_status": map_info["status"],
                        "conditional_building_count": counts.get(
                            "render_class_proxy", 0
                        ),
                        "osm_building_count": sum(
                            n for name, n in counts.items() if name.startswith("osm_")
                        ),
                        "terrain_status": snapshot.terrain_status,
                    }
                ),
                provenance={
                    "scope": "display-only; full render preparation is explicit",
                    "source_sha256": snapshot.source_sha256,
                    "geometry_sha256": geometry_key,
                    "georeference_sha256": frame_sha,
                    "curb_display": curbs["evidence"],
                    "curb_volume_source_ids": sorted(
                        {
                            identifier
                            for mesh in curbs["meshes"]
                            for identifier in mesh["source_ids"]
                        }
                    ),
                },
            )
            result = SceneView(
                scene={
                    **snapshot.model_dump(mode="json"),
                    "display_geometry_sha256": geometry_key,
                },
                environment=environment,
            )
            if len(self._cache) > 5:
                self._cache.clear()
            if (
                self.repository.get(project_id, lightweight=True).state_version
                != metadata.state_version
            ):
                raise ValueError("Проект изменился во время загрузки 3D")
            if (
                sha256(frame.read_bytes()).hexdigest() if frame.exists() else None
            ) != frame_sha:
                raise ValueError("Привязка изменилась во время загрузки 3D")
            disk.parent.mkdir(parents=True, exist_ok=True)
            body = result.model_dump(mode="json")
            document = {
                "sha256": sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
                "view": body,
            }
            pending = disk.with_suffix(".pending")
            pending.write_text(
                json.dumps(document, separators=(",", ":")), encoding="utf-8"
            )
            pending.replace(disk)
            self._cache[key] = result
            return result

    def _with_plan(
        self, view: SceneView, project: Any, horizon: int, geometry_key: str
    ) -> SceneView:
        self._index_assets()
        snapshot = SceneSnapshot.model_validate(view.scene)
        snapshot.horizon_year = horizon
        snapshot.plan_version = project.plan.version if project.plan else 1
        snapshot.objects = scene_plants(
            project, horizon, *snapshot.coordinate_origin, species=get_species
        )
        encoded = (
            snapshot.model_dump_json(exclude={"content_sha256"}).encode()
            + geometry_key.encode()
        )
        snapshot.content_sha256 = sha256(encoded).hexdigest()
        profile = json.loads(Path(__file__).with_name("native_style.json").read_bytes())
        required: set[str] = set()
        planned, trees = [], []

        for tree in view.environment.preview.context_trees:
            placeholder = ScenePlantObject(
                object_id=tree.object_id,
                kind="tree",
                local_x=tree.local_x,
                local_y=tree.local_y,
                crown_shape="round",
                canopy_radius_min_m=tree.radius_m,
                canopy_radius_max_m=tree.radius_m,
            )
            _, relative, _ = plant_asset(placeholder, profile)
            trees.append(
                self._tree(
                    tree.object_id,
                    tree.local_x,
                    tree.local_y,
                    tree.base_elevation_m,
                    tree.height_m,
                    tree.radius_m,
                    "botaniq/" + relative,
                    required,
                )
            )
        for row in snapshot.objects:
            _, relative, _ = plant_asset(row, profile)
            planned.append(
                self._tree(
                    row.object_id,
                    row.local_x,
                    row.local_y,
                    0,
                    max(0.4, row.height_max_m or row.canopy_radius_max_m * 2.4),
                    max(0.18, row.canopy_radius_max_m),
                    "botaniq/" + relative,
                    required,
                )
            )
        materials = []
        for role, settings in _SURFACES.items():
            maps = {
                name: self._materials.get(role + ":" + name)
                for name in ["diffuse", "roughness", "normal_gl"]
            }
            ready_maps = {name: row for name, row in maps.items() if row is not None}
            if len(ready_maps) == 3:
                materials.append(
                    {
                        "kind": role,
                        **settings,
                        "maps": {name: row["id"] for name, row in ready_maps.items()},
                    }
                )
                required.update(row["id"] for row in ready_maps.values())
        assets = [
            {
                **{
                    k: v
                    for k, v in self._assets[id][1].items()
                    if k not in {"key", "filename", "_stat"}
                },
                "url": f"/api/projects/{project.id}/scene-view-assets/{id}",
            }
            for id in sorted(required)
        ]
        appearance = RenderPreparationPreview.model_validate(
            {
                "meshes": [],
                "curbs": [],
                "suppressed_source_ids": [],
                "planned_plants": planned,
                "context_trees": trees,
                "materials": materials,
                "assets": assets,
                "asset_version": 1 if assets else 0,
            }
        )
        # Share immutable mesh objects between year/catalog projections. A
        # scenario change must not duplicate the source-positioned volumes.
        preview = view.environment.preview.model_copy(
            update={
                "planned_plants": appearance.planned_plants,
                "context_trees": appearance.context_trees,
                "materials": appearance.materials,
                "assets": appearance.assets,
                "asset_version": appearance.asset_version,
            }
        )
        digest = sha256(preview.model_dump_json().encode()).hexdigest()
        environment = view.environment.model_copy(
            update={
                "id": uuid5(NAMESPACE_URL, snapshot.content_sha256 + digest),
                "sha256": digest,
                "scene_sha256": snapshot.content_sha256,
                "horizon_year": horizon,
                "plan_version": snapshot.plan_version,
                "preview": preview,
            }
        )
        return SceneView(
            scene={
                **snapshot.model_dump(mode="json"),
                "display_geometry_sha256": geometry_key,
            },
            environment=environment,
        )

    def _tree(
        self,
        identifier: str,
        x: float,
        y: float,
        base: float,
        height: float,
        radius: float,
        relative: str,
        required: set[str],
    ) -> dict[str, Any]:
        assignment = {}
        if relative + ":near" in self._models and relative + ":far" in self._models:
            assignment = _model_assignment(
                {"object_id": identifier, "relative_path": relative}, self._models
            )
            required.update(
                [assignment["model_asset_id"], assignment["far_model_asset_id"]]
            )
        return {
            "object_id": identifier,
            "local_x": x,
            "local_y": y,
            "base_elevation_m": base,
            "height_m": height,
            "radius_m": radius,
            "rotation_z": 0,
            **assignment,
        }
