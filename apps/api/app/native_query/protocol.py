"""Bounded local worker protocol; identity errors never fall back to GeoJSON."""

from hashlib import sha256
from pathlib import Path

from app.native_query.contracts import PROTOCOL, NativeObjectQuery, NativeObjectReply


def encode_request(query: NativeObjectQuery, output: Path) -> bytes:
    output_text = str(output)
    if not output.is_absolute() or any(c in output_text for c in "\r\n\x00"):
        raise ValueError("Native output must be an absolute single-line path")
    if len(output_text.encode("utf-8")) > 4096:
        raise ValueError("Native output path exceeds protocol limit")
    lines = [
        PROTOCOL,
        output_text,
        query.request_id,
        query.source_sha256,
        str(query.units_code),
        f"{len(query.targets)} {len(query.points)}",
        *[
            row
            for target in query.targets
            for row in (
                f"{target.route} {('face_' + str(target.face_id)) if target.face_id else target.capability} {len(target.additional_routes)}",
                *target.additional_routes,
            )
        ],
        *[" ".join(format(value, ".17g") for value in point) for point in query.points],
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def decode_reply(
    data: bytes, query: NativeObjectQuery, *, request_bytes: bytes
) -> NativeObjectReply:
    if len(data) > 96 * 1024 * 1024:
        raise ValueError("Native reply exceeds protocol limit")
    result = NativeObjectReply.model_validate_json(data)
    if (
        result.request_id != query.request_id
        or result.request_sha256 != sha256(request_bytes).hexdigest()
        or result.source_sha256 != query.source_sha256
        or result.units_code != query.units_code
        or result.point_count != len(query.points)
        or tuple(item.route for item in result.objects)
        != tuple(target.route for target in query.targets)
    ):
        raise ValueError("Native reply does not match requested source/batch")
    expected_indices = tuple(range(len(query.points)))
    for target, item in zip(query.targets, result.objects, strict=True):
        if item.face_id != target.face_id:
            raise ValueError("Native reply changed the derived face identity")
        if item.additional_routes != target.additional_routes:
            raise ValueError("Native reply changed the group member inventory")
        if target.additional_routes and item.capability == "curve":
            raise ValueError("Native group cannot be reduced to one member curve")
        if tuple(answer.point_index for answer in item.answers) != expected_indices:
            raise ValueError("Native reply lost, reordered or duplicated points")
        if target.capability == "curve" and item.capability == "area":
            raise ValueError("Native reply changed requested curve semantics")
        if target.capability in {"area", "closed_area"} and item.capability != "area" and not item.preparation_error:
            raise ValueError("Missing native interior requires an addressed explanation")
    return result
