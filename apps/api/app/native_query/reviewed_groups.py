"""Same-capture native verification and calculation targets for reviewed groups."""

from uuid import uuid4

from app.dxf_import.layer_contracts import Layer
from app.dxf_import.object_review_contracts import SourceAreaGroupCheck
from app.native_query.contracts import NativeObjectQuery, NativeTarget
from app.native_query.live_inventory import QueryObject


def verify_group(engine, project, request):
    routes = [
        "/".join((*m.source.instance_chain, m.source.handle)) for m in request.members
    ]
    with engine._lock:
        engine.assert_current(project)
        indexed = {item.route: item for item in engine.inventory.objects}
        if any(
            route not in indexed
            or not indexed[route].curve
            or indexed[route].error
            or indexed[route].context
            for route in routes
        ):
            raise ValueError("AutoCAD не может проверить одну из выбранных кривых")
        reply = engine.client.measure(
            engine.session,
            NativeObjectQuery(
                request_id=uuid4().hex,
                source_sha256=engine.session.source_sha256,
                units_code=engine.session.units_code,
                targets=(
                    NativeTarget(
                        route=routes[0],
                        additional_routes=tuple(routes[1:]),
                        capability="area",
                    ),
                ),
                points=((0, 0, 0),),
            ),
        )
        row = reply.objects[0]
        valid = (
            row.capability == "area"
            and row.interior_known
            and not row.preparation_error
        )
        detail = row.preparation_error
        if valid:
            reason = "AutoCAD подтвердил замкнутую область"
        elif "open or ambiguous" in detail:
            reason = "Концы выбранных линий не соединены либо в контуре есть развилка"
        elif "disconnected" in detail:
            reason = "Выбрано несколько отдельных контуров"
        elif "createFromCurves" in detail:
            reason = "AutoCAD не создал единую область из выбранных кривых"
        elif "definition/layer" in detail:
            reason = "Кривые относятся к разным слоям или определениям блока"
        else:
            reason = "Выбранные кривые не образуют поддерживаемую область"
        return SourceAreaGroupCheck(valid=valid, reason=reason, detail=detail)


def group_targets(groups, inventory, layers):
    indexed = {item.route: item for item in inventory.objects}
    result, used = [], set()
    for group in groups:
        routes = tuple(
            "/".join((*m.source.instance_chain, m.source.handle)) for m in group.members
        )
        if any(
            route in used
            or route not in indexed
            or not indexed[route].curve
            or indexed[route].error
            for route in routes
        ):
            raise ValueError("Состав подтверждённой области изменился")
        NativeTarget(route=routes[0], additional_routes=routes[1:], capability="area")
        used.update(routes)
        layer_name = f"Подтверждённая область {group.id}"
        layers[layer_name] = Layer(
            id=group.id,
            source_name=layer_name,
            suggested_kind=group.kind,
            mapped_kind=group.kind,
            mapping_confirmed=True,
            color="#315bdf",
            object_count=len(routes),
        )
        bounds = [indexed[r].bounds for r in routes]
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
                routes=routes, layer=layer_name, bounds=merged, curve=True, error=""
            )
        )
    return tuple(result), frozenset(used)
