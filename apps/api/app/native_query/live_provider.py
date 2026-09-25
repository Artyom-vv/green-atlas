"""One-source live native geometry adapter for the existing planning tools.

AutoCAD owns final point checks. Same-capture projections may now supply a
Shapely candidate mask, never an authoritative placement verdict.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict, defaultdict
from math import floor
from threading import RLock

from app.dxf_import.units import meters_per_dxf_unit
from app.geometry.domain import PositionAdvisory, PositionViolation
from app.native_query.bounds_index import NativeBoundsIndex, nearby_measurements
from app.native_query.calculation_rules import calculation_rule
from app.native_query.derived_faces import (
    active_faces,
    area_layers,
    face_features,
    face_targets,
    linear_remainders,
)
from app.native_query.display_snapshot import display_snapshot
from app.native_query.domain_checkpoint import DomainCheckpointStore
from app.native_query.face_preparation import NativeFacePreparation
from app.native_query.hybrid_domain import prepare_hybrid_domain
from app.native_query.live_client import LiveQueryClient, LiveSession
from app.native_query.live_inventory import LiveInventory, load_inventory, query_objects
from app.native_query.live_rules import violation
from app.native_query.measurement_reader import NativeMeasurementReader
from app.native_query.query_policy import (
    BASE_QUERY_REACH_M,
    query_reach_m,
    review_reach_m,
)
from app.regulations.placement_config import PLACEMENT_RULES_REVISION
from app.regulations.profiles import LCT_REQUIREMENT_PROFILE
from app.regulations.registry import REGISTRY_REVISION
from app.regulations.trace_contracts import (
    PlantingRuleTrace,
    RuleTraceBasis,
    RuleTraceEntry,
)

MAX_CACHED_POINTS = 20_000
POINT_BATCH = 256
QUERY_WINDOW_M = 32.0  # Transport locality, not a geometry tolerance
SINGLE_WINDOW_POINT_LIMIT = 16
WINDOW_REACH_MULTIPLIER = 4


class LiveNativeGeometryEngine:
    def __init__(
        self,
        session: LiveSession,
        client: LiveQueryClient,
        *,
        inventory: LiveInventory | None = None,
        linear_layers: frozenset[str] = frozenset(),
    ):
        self.session, self.client = session, client
        self.inventory = inventory if inventory is not None else load_inventory(session)
        # Explicit legacy line semantics; new captures also preserve readable
        # remainders of native face assembly as distance constraints.
        self.linear_layers = linear_layers
        self.factor = meters_per_dxf_unit(session.units_code)
        if self.factor is None:
            raise ValueError("Единицы чертежа не подтверждены")
        self._lock = RLock()
        self._basis_key = None
        self._objects = ()
        self._layers = {}
        self._faces = ()
        self._face_preparation = NativeFacePreparation()
        self._cache = OrderedDict()
        self._domain_cache = OrderedDict()
        self._hybrid = None
        self._coverage = None
        queue = getattr(client, "queue", None)
        self._domain_checkpoints = DomainCheckpointStore(queue / "search-domains") if queue else None

    def _basis(self, project):
        if (
            project.source_file is None
            or project.source_file.content_sha256 != self.session.snapshot_sha256
        ):
            raise ValueError("Карта проекта и native-сеанс относятся к разным захватам")
        layers = "\n".join(layer.model_dump_json() for layer in project.layers)
        reviewed_closures = frozenset(
            "/".join((*item.source.instance_chain, item.source.handle))
            for item in project.source_file.native_area_proposals
            if item.decision == "accepted"
        )
        object_decisions = project.source_file.object_decisions
        area_groups = project.source_file.area_groups
        if any(group.source_sha256 != project.source_file.content_sha256 for group in area_groups):
            raise ValueError('Подтверждённая область относится к другому захвату')
        linear_routes = frozenset('/'.join((*item.source.instance_chain, item.source.handle))
                                  for item in object_decisions if item.interpretation == 'linear')
        ignored_routes = frozenset('/'.join((*item.source.instance_chain, item.source.handle))
                                   for item in object_decisions if item.interpretation == 'reference')
        key = (
            project.id,
            project.geometry_version,
            PLACEMENT_RULES_REVISION,
            hashlib.sha256(layers.encode()).hexdigest(),
            tuple(sorted(reviewed_closures)),
            tuple(item.model_dump_json() for item in object_decisions),
            tuple(item.model_dump_json() for item in area_groups),
            tuple(sorted(project.source_file.rejected_native_face_keys)),
        )
        if key != self._basis_key:
            indexed = {item.route: item for item in self.inventory.objects}
            if any(route not in indexed or not indexed[route].curve or indexed[route].error
                   or route in reviewed_closures for route in linear_routes | ignored_routes):
                raise ValueError('Решение об объекте не соответствует текущему захвату AutoCAD')
            self._layers = {layer.source_name: layer for layer in project.layers}
            requested_layers = tuple(sorted(area_layers(self._layers, self.linear_layers)))
            self.inventory = self._face_preparation.prepare(self.inventory, self.session, self.client, requested_layers)
            from app.native_query.reviewed_groups import group_targets
            groups, group_routes = group_targets(area_groups, self.inventory, self._layers)
            if group_routes & (reviewed_closures | linear_routes | ignored_routes):
                raise ValueError('Для объекта сохранены противоречащие решения')
            rejected_closures = frozenset(
                '/'.join((*item.source.instance_chain, item.source.handle))
                for item in project.source_file.native_area_proposals if item.decision == 'rejected'
            )
            self._faces = tuple(active_faces(
                self.inventory, self._layers,
                linear_routes | ignored_routes | group_routes | rejected_closures,
                self.linear_layers,
                frozenset(project.source_file.rejected_native_face_keys),
            ))
            native_linear = linear_remainders(
                self.inventory, self._layers, reviewed_closures | linear_routes | ignored_routes | group_routes,
            )
            for name in self.linear_layers:
                layer = self._layers.get(name)
                if (
                    not layer
                    or not layer.mapping_confirmed
                    or layer.mapped_kind not in {"road", "restricted"}
                ):
                    raise ValueError("Назначение линейного препятствия изменилось")
                if any(
                    not item.curve
                    for item in self.inventory.objects
                    if item.layer == name and not item.context
                ):
                    raise ValueError(
                        "Линейный слой содержит площадные или неподтверждённые объекты"
                    )
            self._objects = query_objects(
                self.inventory,
                {
                    name
                    for name, layer in self._layers.items()
                    if layer.mapped_kind
                    in {
                        "building",
                        "road",
                        "site_border",
                        "water",
                        "restricted",
                        "existing_green",
                    }
                    and name not in self.linear_layers
                },
                reviewed_closures=reviewed_closures,
                linear_routes=linear_routes,
                ignored_routes=ignored_routes | group_routes,
                native_linear_routes=native_linear,
            ) + groups + face_targets(self._faces)
            self._cache.clear()
            self._domain_cache.clear()
            self._hybrid = None
            self._coverage = None
            self._bounds_index = NativeBoundsIndex(self._objects, self._layers)
            self._basis_key = key

    def review_area_group(self, project, request):
        from app.native_query.reviewed_groups import verify_group
        return verify_group(self, project, request)

    def source_coverage(self, project):
        from app.native_query.coverage import source_coverage
        return source_coverage(self, project)

    def native_face_review(self, project):
        from app.native_query.face_review import review_faces
        with self._lock:
            self.assert_current(project)
            return review_faces(project, self.inventory.faces, self._faces, self.factor)

    def assert_current(self, project):
        """Mandatory before committing a cached preview against a mutable CAD doc."""
        with self._lock:
            self._basis(project)
            try:
                self.client.inspect(self.session)
            except Exception:
                self._cache.clear()
                self._domain_cache.clear()
                self._hybrid = None
                self._coverage = None
                raise

    def prepare_positions(self, project, points, *, reach_m=BASE_QUERY_REACH_M):
        with self._lock:
            self.assert_current(project)
            if len(set(points)) > MAX_CACHED_POINTS:
                raise ValueError("Разделите размещение на меньшие участки")
            for point in points:
                if point in self._cache:
                    self._cache.move_to_end(point)
            missing = list(
                dict.fromkeys(
                    point
                    for point in points
                    if point not in self._cache or self._cache[point][0] < reach_m
                )
            )
            windows = defaultdict(list)
            window_m = max(QUERY_WINDOW_M, reach_m * WINDOW_REACH_MULTIPLIER)
            for point in missing:
                key = (
                    (floor(point[0] / window_m), floor(point[1] / window_m))
                    if len(missing) > SINGLE_WINDOW_POINT_LIMIT
                    else (0, 0)
                )
                windows[key].append(point)
            measured = {}
            sites = {}
            reader = NativeMeasurementReader(
                self.session, self.client, self.factor,
                self._bounds_index, self._layers, self.linear_layers,
            )
            if len(windows) > 1:
                # Site contours span the whole street. Measure them once per
                # point batch, not once for every local obstacle window.
                for start in range(0, len(missing), POINT_BATCH):
                    sites.update(
                        reader.measure(
                            missing[start : start + POINT_BATCH],
                            reach_m,
                            sites_only=True,
                        )
                    )
            for window in windows.values():
                for start in range(0, len(window), POINT_BATCH):
                    batch = window[start : start + POINT_BATCH]
                    measured.update(
                        reader.measure(
                            batch, reach_m, sites_only=False if sites else None
                        )
                    )
            if sites:
                measured = {
                    point: (reach_m, rows + sites[point][1])
                    for point, (_, rows) in measured.items()
                }
            if measured:
                # Publish only when every spatial packet shares the same live basis.
                self.client.inspect(self.session)
                self._cache.update(measured)
            while len(self._cache) > MAX_CACHED_POINTS:
                self._cache.popitem(last=False)

    def _rows(self, project, x, y, radius):
        with self._lock:
            self._basis(project)
            point = (x, y)
            reach = query_reach_m(radius)
            if point not in self._cache or self._cache[point][0] < reach:
                self.prepare_positions(project, [point], reach_m=reach)
            # A domain certificate may have cached a wider neighbourhood.
            # Reusing it must not turn distant unknown objects into local ones.
            return nearby_measurements(
                self._cache[point][1],
                self._layers,
                x / self.factor,
                y / self.factor,
                reach / self.factor,
            )

    def calculate(self, project, progress=None):
        self.assert_current(project)
        return display_snapshot(project, self._layers, face_features(self._faces, self.factor))

    def position_violation(self, project, x, y, radius, plant_kind="tree"):
        rows = self._rows(project, x, y, radius)
        sites = []
        for row in rows:
            layer = self._layers.get(row.item.layer)
            if not layer or not layer.mapping_confirmed:
                continue
            if layer.mapped_kind == "site_border":
                sites.append(row)
                continue
            result = violation(row, layer, plant_kind, radius, self.factor)
            if result:
                return result
        if sites and all(
            (row.answer and row.answer.membership == "outside")
            or (
                (
                    row.answer is None
                    or row.answer.membership not in {"occupied", "edge"}
                )
                and row.item.bounds is not None
                and row.item.distance_to_bounds(x / self.factor, y / self.factor)
                > radius / self.factor
            )
            for row in sites
        ):
            # Extents may disprove membership, never establish it. A remote
            # broken contour must not turn a known outside point into unknown.
            return PositionViolation(
                "NATIVE_OUTSIDE_SITE",
                "Граница территории",
                "Позиция находится за границами территории",
                "site_border",
                None,
                None,
                "Выбрать позицию внутри территории",
            )
        return None

    def validate_position(self, project, x, y, radius, plant_kind="tree"):
        result = self.position_violation(project, x, y, radius, plant_kind)
        if result:
            raise ValueError(result.description)

    def automatic_safe_geometry(
        self,
        project,
        geometry,
        radius,
        plant_kind="tree",
        growth_canopy_radius=None,
        growth_root_radius=None,
    ):
        return prepare_hybrid_domain(
            self,
            project,
            geometry,
            radius,
            plant_kind,
            growth_canopy_radius,
            growth_root_radius,
        )

    def explain_position(self, project, x, y, radius, kind, canopy=0, roots=0):
        from app.native_query.position_evidence import explain_position

        return explain_position(self, project, x, y, radius, kind, canopy, roots)

    def placement_advisory_detail(self, project, x, y, radius, plant_kind="tree"):
        rows = self._rows(project, x, y, radius)
        sites = [
            row
            for row in rows
            if self._layers.get(row.item.layer)
            and self._layers[row.item.layer].mapped_kind == "site_border"
        ]
        if not any(
            row.answer and row.answer.membership in {"occupied", "edge"}
            for row in sites
        ):
            unresolved = [
                row
                for row in sites
                if (
                    row.answer is None
                    or row.answer.membership not in {"outside", "occupied", "edge"}
                )
                and row.item.distance_to_bounds(x / self.factor, y / self.factor) == 0
            ]
            return PositionAdvisory(
                "NATIVE_LOCAL_UNKNOWN",
                "Граница территории",
                "Принадлежность этой позиции территории не подтверждена",
                "Проверить границу территории",
                source_layer=unresolved[0].item.layer if unresolved else None,
                source_feature_ids=tuple(
                    route for row in unresolved for route in row.item.routes
                ),
            )
        for row in rows:
            layer = self._layers.get(row.item.layer)
            if row.item.bounds is None:
                continue  # Addressless source loss: global partial-data decision below.
            if layer and layer.mapped_kind == "site_border":
                continue
            if row.item.distance_to_bounds(x / self.factor, y / self.factor) * self.factor > review_reach_m(radius):
                # The broad query window includes 5 m building rules. It must
                # not silently reinstate the old 5 m missing-data horizon in
                # final point checks after the domain used the configured 2 m.
                continue
            reason = None
            if not layer or not layer.mapping_confirmed:
                reason = "Не подтверждено назначение ближайшего слоя"
            elif row.answer is None or row.answer.status or row.answer.error:
                reason = "Не удалось проверить ближайший объект"
            elif layer.mapped_kind == "utility":
                # Geometry is checked with the executable rule. Missing network
                # metadata is a recorded assumption, not an unknown-position gate.
                if row.answer.distance_units is None:
                    reason = "Расстояние до линии сети не получено"
            elif row.item.layer in self.linear_layers or row.item.reviewed_linear or row.item.native_linear:
                if row.answer.distance_units is None:
                    reason = (
                        "Расстояние до ближайшего линейного препятствия не определено"
                    )
            elif (
                not row.measurement.interior_known
                and row.item.distance_to_bounds(x / self.factor, y / self.factor)
                <= radius / self.factor
            ):
                reason = "Внутренняя область ближайшего объекта не подтверждена"
            if reason:
                return PositionAdvisory(
                    "NATIVE_LOCAL_UNKNOWN",
                    "Нужна проверка позиции",
                    f"{reason}, слой «{row.item.layer}»",
                    "Проверить объект исходника",
                    source_layer=row.item.layer,
                    source_feature_ids=row.item.routes,
                )
        # Full normative/source completeness is deliberately not certified by a
        # geometric query. Explicit partial-source consent permits bulk drafts.
        return PositionAdvisory(
            "SOURCE_GEOMETRY_PARTIAL",
            "Расчёт по доступным данным",
            "Полнота исходника не подтверждена",
            "Проверить исходные данные",
        )

    def placement_advisory(self, project, x, y, radius, plant_kind="tree"):
        return self.placement_advisory_detail(
            project, x, y, radius, plant_kind
        ).description

    def future_growth_advisory_detail(
        self, project, x, y, canopy_radius, root_radius, plant_kind="tree"
    ):
        rows = self._rows(project, x, y, max(canopy_radius, root_radius))
        for row in rows:
            layer = self._layers.get(row.item.layer)
            if (
                not layer
                or layer.mapped_kind == "site_border"
                or not row.answer
                or row.answer.status
                or row.answer.error
            ):
                continue
            distance = row.answer.distance_units
            radius = root_radius if layer.mapped_kind == "utility" else canopy_radius
            if distance is not None and distance * self.factor < radius:
                return PositionAdvisory(
                    "GROWTH_NATIVE_CLEARANCE",
                    "Рост растения",
                    f"Прогноз роста пересекает объект слоя «{layer.source_name}»",
                    "Выбрать меньший размер или другую позицию",
                    source_layer=layer.source_name,
                    source_feature_ids=row.item.routes,
                )
        advisory = self.placement_advisory_detail(
            project, x, y, max(canopy_radius, root_radius), plant_kind
        )
        # Source completeness is already reported for the present position.
        # Do not turn the same global gap into a second, fictitious growth issue.
        return advisory if advisory.code != "SOURCE_GEOMETRY_PARTIAL" else None

    def future_growth_advisory(
        self, project, x, y, canopy_radius, root_radius, plant_kind="tree"
    ):
        advisory = self.future_growth_advisory_detail(
            project, x, y, canopy_radius, root_radius, plant_kind
        )
        return advisory.description if advisory else None

    def position_rule_trace(
        self, project, x, y, plant_kind="tree", mature_crown_diameter_m=None
    ):
        result = self.position_violation(project, x, y, 0, plant_kind)
        advice = self.placement_advisory_detail(project, x, y, 0, plant_kind)
        assumptions = []
        seen = set()
        for row in self._rows(project, x, y, 0):
            layer = self._layers.get(row.item.layer)
            if not layer or not layer.mapping_confirmed or layer.source_name in seen:
                continue
            rule = calculation_rule(layer, plant_kind, 0)
            if rule and rule.assumptions:
                seen.add(layer.source_name)
                assumptions.append(RuleTraceEntry(
                    obstacle_kind=layer.mapped_kind,
                    status="not_checked", code="GEOMETRY_RULE_ASSUMPTION",
                    rule_id=rule.id, required_distance_m=rule.distance_m,
                    note=f"{layer.source_name}: расчёт по геометрии; " + "; ".join(rule.assumptions),
                ))
        return PlantingRuleTrace(
            basis=RuleTraceBasis(
                project_id=project.id,
                state_version=project.state_version,
                geometry_version=project.geometry_version,
                plan_version=project.plan.version if project.plan else None,
                requirement_profile=LCT_REQUIREMENT_PROFILE,
                registry_revision=REGISTRY_REVISION,
                source_content_sha256=self.session.snapshot_sha256,
            ),
            x=x,
            y=y,
            plant_kind=plant_kind,
            mature_crown_diameter_m=mature_crown_diameter_m,
            entries=[
                RuleTraceEntry(
                    obstacle_kind="native_geometry",
                    status="failed" if result else "not_checked",
                    code=result.code if result else advice.code,
                    rule_id=result.rule_id if result else None,
                    actual_distance_m=result.actual if result else None,
                    required_distance_m=result.required if result else None,
                    note=result.description if result else advice.description,
                )
            ] + assumptions,
        )
