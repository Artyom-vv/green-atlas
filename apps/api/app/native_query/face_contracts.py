"""AutoCAD area evidence; display coordinates may filter search, not validate positions."""

from typing import Annotated

from pydantic import Field, FiniteFloat, model_validator

from app.native_query.contracts import Hex32, NativeDto, Route, Sha256

Point = tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class FaceRepair(NativeDto):
    from_: Route = Field(alias="from")
    to: Route
    a: Point
    b: Point


class NativeFace(NativeDto):
    id: int = Field(gt=0)
    anchor: Route
    layer: str
    routes: tuple[Route, ...] = Field(min_length=1)
    physical_routes: tuple[Route, ...] = Field(min_length=1)
    area: Annotated[FiniteFloat, Field(gt=0)]
    bounds: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    display: tuple[Point, ...] = Field(min_length=4)
    repairs: tuple[FaceRepair, ...] = ()

    @model_validator(mode="after")
    def coherent(self):
        if (
            len(set(self.routes)) != len(self.routes)
            or not set(self.physical_routes) <= set(self.routes)
            or self.anchor not in self.physical_routes
            or len({route.rpartition("/")[0] for route in self.routes}) != 1
            or self.bounds[0] > self.bounds[2]
            or self.bounds[1] > self.bounds[3]
            or self.display[0] != self.display[-1]
        ):
            raise ValueError("Inconsistent native face evidence")
        if any(
            r.from_ not in self.routes or r.to not in self.routes for r in self.repairs
        ):
            raise ValueError("Repair is not tied to source members")
        return self


class FacePreparation(NativeDto):
    session_id: Hex32
    request_sha256: Sha256
    layers: tuple[str, ...]
    face_policy: str
    faces: tuple[NativeFace, ...]
    linear_routes: tuple[Route, ...]
    face_issues: tuple[str, ...]
