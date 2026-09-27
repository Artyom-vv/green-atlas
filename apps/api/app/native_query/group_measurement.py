"""Measure readable group members without pretending their area was recovered."""

from uuid import uuid4

from app.native_query.contracts import (
    MAX_MEASUREMENTS,
    MAX_QUERY_MEMBERS,
    NativeObjectMeasurement,
    NativeObjectQuery,
    NativePointMeasurement,
    NativeTarget,
)


def measure_group_curves(client, session, item, points):
    answers = [[] for _ in points]
    batch_size = min(MAX_QUERY_MEMBERS, MAX_MEASUREMENTS // len(points))
    elapsed = 0
    for start in range(0, len(item.curve_routes), batch_size):
        routes = item.curve_routes[start : start + batch_size]
        reply = client.measure(
            session,
            NativeObjectQuery(
                request_id=uuid4().hex,
                source_sha256=session.source_sha256,
                units_code=session.units_code,
                targets=tuple(
                    NativeTarget(route=route, capability="curve") for route in routes
                ),
                points=points,
            ),
        )
        for route, measured in zip(routes, reply.objects, strict=True):
            if measured.route != route or len(measured.answers) != len(points):
                raise ValueError("Ответ AutoCAD не соответствует составу группы")
            elapsed += measured.prepare_ms
            for index, answer in enumerate(measured.answers):
                answers[index].append(answer)
    combined = []
    for index, members in enumerate(answers):
        failed = next(
            (a for a in members if a.status or a.error or a.distance_units is None),
            None,
        )
        combined.append(
            NativePointMeasurement(
                point_index=index,
                status=failed.status or 1 if failed else 0,
                membership="unknown",
                distance_units=None
                if failed
                else min(a.distance_units for a in members),
                error=(failed.error or "group member distance unavailable")
                if failed
                else "",
            )
        )
    return NativeObjectMeasurement(
        route=item.routes[0],
        additional_routes=item.routes[1:],
        entity_type="NativeCurveGroup",
        layer=item.layer,
        capability="curve",
        interior_known=False,
        preparation_error=item.error or "native area group exceeds member limit",
        prepare_ms=elapsed,
        answers=tuple(combined),
    )
