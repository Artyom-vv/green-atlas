"""Пакетные native-измерения и пространственный отбор объектов независимо от предметных
правил посадки.
"""

from dataclasses import dataclass
from uuid import uuid4

from app.dxf_import.layers.contracts import Layer
from app.native_query.capture.capture_client import CaptureClient
from app.native_query.contracts import (
    MAX_MEASUREMENTS,
    MAX_QUERY_MEMBERS,
    NativeObjectQuery,
    NativeTarget,
)
from app.native_query.live.live_client import LiveSession
from app.native_query.live.live_inventory import QueryObject
from app.native_query.measurement.group_measurement import measure_group_curves
from app.native_query.policy.live_rules import MeasuredObstacle
from app.native_query.search.bounds_index import NativeBoundsIndex

OBJECT_BATCH = (
    1024  # Порция также ограничена количеством членов и размером матрицы протокола.
)


@dataclass(frozen=True)
class NativeMeasurementReader:
    session: LiveSession
    client: CaptureClient
    factor: float
    bounds_index: NativeBoundsIndex
    layers: dict[str, Layer]
    linear_layers: frozenset[str]

    def measure(
        self,
        points: list[tuple[float, float]],
        reach_m: float,
        *,
        sites_only: bool | None = None,
    ) -> dict[tuple[float, float], tuple[float, tuple[MeasuredObstacle, ...]]]:
        raw_points = tuple((x / self.factor, y / self.factor, 0.0) for x, y in points)
        reach = reach_m / self.factor
        rows: list[list[MeasuredObstacle]] = [[] for _ in points]
        targets: list[NativeTarget] = []
        items: list[QueryObject] = []

        def flush() -> None:
            if not targets:
                return
            reply = self.client.measure(
                self.session,
                NativeObjectQuery(
                    request_id=uuid4().hex,
                    source_sha256=self.session.source_sha256,
                    units_code=self.session.units_code,
                    targets=tuple(targets),
                    points=raw_points,
                ),
            )
            for item, measured in zip(items, reply.objects, strict=True):
                for index, answer in enumerate(measured.answers):
                    x, y, _ = raw_points[index]
                    if item.distance_to_bounds(x, y) <= reach:
                        rows[index].append(MeasuredObstacle(item, measured, answer))
            targets.clear()
            items.clear()

        members = 0
        for item in self.bounds_index.candidates(raw_points, reach):
            layer = self.layers.get(item.layer)
            site = layer is not None and layer.mapped_kind == "site_border"
            if sites_only is not None and site != sites_only:
                continue
            # Неизвестные назначения сохраняются для локального просмотра, а не исключаются автоматически.
            if (
                layer
                and layer.mapped_kind in {"ignore", "lawn"}
                and layer.mapping_confirmed
            ):
                continue
            nearby = [item.distance_to_bounds(x, y) <= reach for x, y, _ in raw_points]
            # Далёкие территории остаются явными свидетельствами только по габаритам.
            # Габариты могут опровергнуть принадлежность, но не подтверждают свободный грунт.
            if site:
                for index, near in enumerate(nearby):
                    if not near:
                        rows[index].append(MeasuredObstacle(item, None, None))
            if not any(nearby):
                continue
            target = item.target(
                area=item.layer not in self.linear_layers
                and (not layer or layer.mapped_kind != "utility")
            )
            if (
                layer
                and layer.mapping_confirmed
                and target is None
                and item.curve_routes
            ):
                measured = measure_group_curves(
                    self.client, self.session, item, raw_points
                )
                for index, near in enumerate(nearby):
                    if near:
                        rows[index].append(
                            MeasuredObstacle(item, measured, measured.answers[index])
                        )
                continue
            if not layer or not layer.mapping_confirmed or target is None:
                for index, near in enumerate(nearby):
                    if near:
                        rows[index].append(MeasuredObstacle(item, None, None))
                continue
            count = 1 if item.face_id else len(item.routes)
            if targets and (
                len(targets) >= OBJECT_BATCH
                or members + count > MAX_QUERY_MEMBERS
                or (len(targets) + 1) * len(points) > MAX_MEASUREMENTS
            ):
                flush()
                members = 0
            targets.append(target)
            items.append(item)
            members += count
        flush()
        # Неполный кеш не публикуется после сбоя порции или изменения исходника.
        return {
            point: (reach_m, tuple(measured))
            for point, measured in zip(points, rows, strict=True)
        }
