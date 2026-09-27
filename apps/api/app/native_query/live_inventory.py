"""Native instance inventory. AABBs select queries, never declare occupied ground."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, FiniteFloat, model_validator

from app.native_query.contracts import NativeDto, NativeTarget
from app.native_query.live_client import LiveSession
from app.native_query.face_contracts import NativeFace

Bounds = tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]


class InventoryObject(NativeDto):
    # Unqueryable MINSERT routes also stay in the ledger, with a native error.
    route: str = Field(min_length=1)
    layer: str
    entity_type: str
    context: bool
    bounds: Bounds | None
    curve: bool
    error: str


class InventoryGroup(NativeDto):
    routes: tuple[str, ...] = Field(min_length=1)
    error: str


class LiveInventory(NativeDto):
    schema_: Literal["green-atlas.live-inventory/1"] = Field(alias="schema")
    objects: tuple[InventoryObject, ...] = Field(max_length=1_000_000)
    groups: tuple[InventoryGroup, ...]
    face_policy: Literal["native-planar-faces/1"] | None = None
    face_preparation_available: bool = False
    faces: tuple[NativeFace, ...] = ()
    linear_routes: tuple[str, ...] = ()
    face_issues: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        indexed = {item.route: item for item in self.objects}
        if len(indexed) != len(self.objects):
            raise ValueError("Duplicate native inventory route")
        if (self.faces or self.linear_routes) and self.face_policy is None:
            raise ValueError("Native faces require an explicit preparation policy")
        if len({f.id for f in self.faces}) != len(self.faces):
            raise ValueError("Duplicate native face identity")
        for face in self.faces:
            if any(route not in indexed for route in face.routes) or any(
                indexed[route].layer != face.layer for route in face.physical_routes
            ):
                raise ValueError("Native face lost its source provenance")
        if any(route not in indexed or not indexed[route].curve or indexed[route].error
               for route in self.linear_routes):
            raise ValueError("Native linear remainder is not a readable curve")
        grouped = set()
        for group in self.groups:
            if len(set(group.routes)) != len(group.routes):
                raise ValueError("Duplicate native group member")
            if any(route not in indexed or route in grouped for route in group.routes):
                raise ValueError("Native group membership is inconsistent")
            if len({indexed[route].layer for route in group.routes}) != 1:
                raise ValueError("Native group crosses effective layers")
            grouped.update(group.routes)
        for item in self.objects:
            if item.bounds and (
                item.bounds[0] > item.bounds[2] or item.bounds[1] > item.bounds[3]
            ):
                raise ValueError("Invalid native extents")
        return self


def load_inventory(session: LiveSession) -> LiveInventory:
    if not session.inventory_path or not session.inventory_sha256:
        raise ValueError("Сеанс AutoCAD не содержит перечня препятствий")
    path = Path(session.inventory_path)
    expected = Path(session.snapshot_path + ".inventory.json")
    if path.resolve() != expected.resolve() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("Недопустимый перечень объектов AutoCAD")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != session.inventory_sha256:
        raise ValueError("Перечень объектов изменён после захвата")
    return LiveInventory.model_validate_json(content)


class QueryObject(NativeDto):
    routes: tuple[str, ...]
    layer: str
    bounds: Bounds | None
    curve: bool
    error: str
    # An area assembly failure does not make its readable native curves vanish.
    # These routes are measured separately, never joined/repaired by Python.
    curve_routes: tuple[str, ...] = ()
    reviewed_closure: bool = False
    reviewed_linear: bool = False
    native_linear: bool = False
    face_id: int = 0

    def target(self, *, area: bool) -> NativeTarget | None:
        if self.face_id:
            return NativeTarget(route=self.routes[0], capability="area", face_id=self.face_id)
        if self.error or len(self.routes) > 256:
            return None
        return NativeTarget(
            route=self.routes[0],
            capability=("closed_area" if self.reviewed_closure else "area")
                if area and not (self.reviewed_linear or self.native_linear) else "curve",
            additional_routes=self.routes[1:],
        )

    def distance_to_bounds(self, x: float, y: float) -> float:
        if self.bounds is None:
            return 0.0
        x0, y0, x1, y1 = self.bounds
        return (max(x0 - x, 0, x - x1) ** 2 + max(y0 - y, 0, y - y1) ** 2) ** 0.5


def query_objects(
    inventory: LiveInventory, group_layers: set[str] | None = None,
    reviewed_closures: frozenset[str] = frozenset(),
    linear_routes: frozenset[str] = frozenset(),
    ignored_routes: frozenset[str] = frozenset(),
    native_linear_routes: frozenset[str] = frozenset(),
) -> tuple[QueryObject, ...]:
    indexed = {item.route: item for item in inventory.objects}
    grouped = set()
    result = []
    overridden = reviewed_closures | linear_routes | ignored_routes | native_linear_routes
    for group in inventory.groups:
        # Only exact connected cycles are native area targets. Open/ambiguous
        # chains retain their joint envelope as LOCAL interior uncertainty.
        routes = tuple(route for route in group.routes
                       if route not in overridden)
        if not routes:
            continue
        members = [indexed[route] for route in routes]
        if group_layers is not None and members[0].layer not in group_layers:
            continue
        if len(members) == 1:
            continue
        # Preserve the remaining group's joint uncertainty envelope; splitting
        # every edge would accidentally lose an unresolved interior between them.
        changed = len(routes) != len(group.routes)
        error = "open native endpoint chain" if changed else group.error
        grouped.update(routes)
        bounds = [item.bounds for item in members]
        merged = (
            None
            if any(b is None for b in bounds)
            else (
                min(b[0] for b in bounds),
                min(b[1] for b in bounds),
                max(b[2] for b in bounds),
                max(b[3] for b in bounds),
            )
        )
        result.append(
            QueryObject(
                routes=routes,
                layer=members[0].layer,
                bounds=merged,
                curve=True,
                error=error,
                curve_routes=routes
                if (error in {"open native endpoint chain", "ambiguous native endpoint junction"}
                    or len(routes) > 256)
                and all(item.curve and not item.error for item in members)
                else (),
            )
        )
    for item in inventory.objects:
        if not item.context and item.route not in grouped and item.route not in ignored_routes:
            result.append(
                QueryObject(
                    routes=(item.route,),
                    layer=item.layer,
                    # Closing a polyline activates its final bulge. The open
                    # source extents need not bound that arc; until the native
                    # closure publishes new extents it must bypass pruning.
                    bounds=None if item.route in reviewed_closures else item.bounds,
                    curve=item.curve,
                    error=item.error,
                    reviewed_closure=item.route in reviewed_closures,
                    reviewed_linear=item.route in linear_routes,
                    native_linear=item.route in native_linear_routes,
                )
            )
    return tuple(result)
